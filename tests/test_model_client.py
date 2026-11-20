"""Regression tests for studio/llm.py model names, redirects, secret redaction, local URLs, and Rust download names.

Covers reviewer findings llm-A through llm-E (t01_names, t02_redirects) plus main.rs sanitize_download_name.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import llm, server
from studio.store import ROOT, Store
from tests.test_hardening import loopback_only


def _start_stub(handler_fn):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            seen.append({
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "goog": self.headers.get("x-goog-api-key"),
                "body": raw.decode("utf-8", "replace"),
            })
            handler_fn(self)

        do_GET = do_POST = _handle

        def log_message(self, *args):
            pass

    srv = server.StudioServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], seen


class ModelNameValidationTest(unittest.TestCase):
    """llm-A: provider-appropriate model name validation."""

    def test_t01_local_model_names_accept_gguf_paths_spaces_brackets_plus_and_cjk(self) -> None:
        real_local_names = (
            "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
            "/Users/jane/models/qwen2.5-7b-instruct-q4_k_m.gguf",
            "./models/qwen2.5-7b-instruct-q4_k_m.gguf",
            r"C:\models\qwen2.5-7b-instruct-q4_k_m.gguf",
            "/Users/jane/Library/Application Support/nomic.ai/GPT4All/Meta-Llama-3-8B.gguf",
            "Llama 3 8B Instruct",
            "gemma-3-27b-it(q4)",
            "llama3.2-vision+tools",
            "通义千问-7b",
            "_internal-model",
            ".hidden-model",
            "a" * 250,
        )
        for name in real_local_names:
            self.assertEqual(llm.validate_model_name(name, "local"), name)
            captured = {}

            def fake_transport(method, url, headers, body, captured=captured):
                captured["model"] = body["model"]
                return {"choices": [{"message": {"content": '{"ok": true}'}}]}

            out = llm.complete_json("local", "", name, "sys", "usr", fake_transport, "http://127.0.0.1:8080/v1")
            self.assertEqual((out, captured["model"]), ({"ok": True}, name))

    def test_openai_and_gemini_model_validation_and_bilingual_errors(self) -> None:
        for ok_openai in ("gpt-4o", "ft:gpt-4o-mini-2024-07-18:my-org::AbC123", "claude-sonnet-4-5@20250929", "_custom"):
            self.assertEqual(llm.validate_model_name(ok_openai, "openai"), ok_openai)
        for bad_openai in ("", "bad name", "gpt-4o?x=1", "gpt-4o#frag", "../other", "a\nb", "x" * 201):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.validate_model_name(bad_openai, "openai")
            self.assertEqual(ctx.exception.code, "bad_model")
            self.assertTrue(ctx.exception.message_zh)

        self.assertEqual(llm._gemini_model_segment("models/gemini-2.5-pro"), "gemini-2.5-pro")
        self.assertEqual(llm._gemini_model_segment("gemini-2.5-pro"), "gemini-2.5-pro")
        for bad_gemini in ("", "models/", "models/a/b", "../evil", "gemini?key=x", "gemini#f", r"a\b", "bad model"):
            with self.assertRaises(llm.LLMError) as ctx:
                llm._gemini_model_segment(bad_gemini)
            self.assertEqual(ctx.exception.code, "bad_model")
            self.assertTrue(ctx.exception.message_zh)

    def test_settings_route_accepts_local_gguf_path_and_returns_bilingual_400_on_bad_gemini_model(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lmv-llm-srv-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        with mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(tmp / "k.key")}):
            store = Store(tmp / "data")
            httpd = server.make_server(0, store)
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                gguf = "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
                conn.request(
                    "POST", "/api/settings",
                    body=json.dumps({"provider": "local", "base_url": "http://127.0.0.1:11434/v1", "model": gguf}),
                    headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"},
                )
                res = conn.getresponse()
                data = json.loads(res.read().decode("utf-8"))
                self.assertEqual(res.status, 200, data)
                self.assertEqual(data["settings"]["model"], gguf)

                conn.request(
                    "POST", "/api/settings",
                    body=json.dumps({"provider": "gemini", "key": "FAKE-KEY", "model": "../evil?x=1"}),
                    headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"},
                )
                res2 = conn.getresponse()
                err = json.loads(res2.read().decode("utf-8"))
                self.assertEqual((res2.status, err["error"]), (400, "bad_model"))
                self.assertTrue(err.get("message_zh"))
            finally:
                conn.close()


class RedirectPolicyTest(unittest.TestCase):
    """llm-B & llm-C: local loopback redirects up to 3 hops; cloud/cross-host redirects refused with clean bilingual message."""

    def test_t02_local_trailing_slash_and_3_hop_loopback_redirects_succeed_while_4th_is_refused(self) -> None:
        def slash_handler(h):
            if h.path == "/v1/models":
                h.send_response(301)
                h.send_header("Location", "/v1/models/")
                h.end_headers()
                h.wfile.write(b'<a href="/v1/models/">Moved Permanently</a>')
                return
            if h.path == "/v1/models/":
                body = b'{"data":[{"id":"llama-local"}]}'
                h.send_response(200)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                h.wfile.write(body)
                return

        srv, base, _ = _start_stub(slash_handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with loopback_only():
            self.assertEqual(llm.list_models("local", base_url=base + "/v1"), ["llama-local"])

        # 3 loopback hops succeed; 4 loopback hops refuse cleanly.
        def hop_handler(max_hops):
            def handle(h):
                step = int(h.path.rsplit("/", 1)[-1])
                if step < max_hops:
                    h.send_response(307)
                    h.send_header("Location", "/hop/%d" % (step + 1))
                    h.end_headers()
                else:
                    out = b'{"ok":true}'
                    h.send_response(200)
                    h.send_header("Content-Type", "application/json")
                    h.send_header("Content-Length", str(len(out)))
                    h.end_headers()
                    h.wfile.write(out)
            return handle

        srv3, base3, _ = _start_stub(hop_handler(3))
        self.addCleanup(srv3.server_close)
        self.addCleanup(srv3.shutdown)
        srv4, base4, _ = _start_stub(hop_handler(4))
        self.addCleanup(srv4.server_close)
        self.addCleanup(srv4.shutdown)

        with loopback_only():
            self.assertEqual(llm._http("POST", base3 + "/hop/0", {}, {"x": 1}), {"ok": True})
            with self.assertRaises(llm.LLMError) as ctx:
                llm._http("POST", base4 + "/hop/0", {}, {"x": 1})
            self.assertEqual(ctx.exception.code, "http")
            self.assertTrue(ctx.exception.message_zh)

    def test_t02_external_redirect_and_cloud_redirects_are_refused_without_raw_html_or_leaking_keys(self) -> None:
        nginx_html = b"<html><head><title>301 Moved</title></head><body><h1>301 Moved</h1></body></html>"

        def ext_redir(h):
            h.send_response(301)
            h.send_header("Location", "http://192.168.1.50:11434/v1/models")
            h.send_header("Content-Length", str(len(nginx_html)))
            h.end_headers()
            h.wfile.write(nginx_html)

        srv, base, _ = _start_stub(ext_redir)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        with loopback_only():
            with self.assertRaises(llm.LLMError) as ctx:
                llm.list_models("local", base_url=base + "/v1")
            self.assertEqual(ctx.exception.code, "http")
            self.assertIn("http://192.168.1.50:11434/v1/models", str(ctx.exception))
            self.assertNotIn("<html>", str(ctx.exception))
            self.assertIn("192.168.1.50", ctx.exception.message_zh)

        # Cloud providers NEVER follow redirects on any 3xx code (301, 302, 303, 307, 308) and never leak keys.
        canary_srv, canary_url, canary_seen = _start_stub(lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b"{}")))
        self.addCleanup(canary_srv.server_close)
        self.addCleanup(canary_srv.shutdown)

        for code in (301, 302, 303, 307, 308):
            r_srv, r_url, _ = _start_stub(lambda h, c=code: (
                h.send_response(c), h.send_header("Location", canary_url + "/steal"), h.end_headers()
            ))
            self.addCleanup(r_srv.server_close)
            self.addCleanup(r_srv.shutdown)
            with loopback_only(), mock.patch.object(llm, "_LOOPBACK_HOSTS", ()), \
                    mock.patch.object(llm, "OPENAI_BASE", r_url + "/v1"), \
                    mock.patch.object(llm, "GEMINI_BASE", r_url + "/v1beta"):
                for fn in (
                    lambda: llm.list_models("openai", "sk-FAKE-0000000000"),
                    lambda: llm.list_models("gemini", "AIzaSyFake0000000000"),
                    lambda: llm.complete_json("openai", "sk-FAKE-0000000000", "gpt-4o", "s", "u"),
                    lambda: llm.search_json("gemini", "AIzaSyFake0000000000", "gemini-2.5-pro", "p"),
                ):
                    with self.assertRaises(llm.LLMError) as c_err:
                        fn()
                    self.assertEqual(c_err.exception.code, "http")
                    self.assertNotIn("sk-FAKE", str(c_err.exception))
        self.assertEqual(canary_seen, [])


class RedactionLocalBaseAndRustSanitizerTest(unittest.TestCase):
    """llm-D, llm-E, and desktop/src-tauri/src/main.rs download filename sanitization."""

    def test_t01_redact_secrets_preserves_ordinary_text_and_scrubs_cjk_adjacent_keys(self) -> None:
        # Non-secret headers (Api-Revision, X-Mode) and English words ending in 'sk-' stay untouched.
        for text, hdrs in (
            ("revision 2026-05-20 is not supported", {"x-goog-api-key": "KEY12345", "Api-Revision": "2026-05-20"}),
            ("please finish the task-list-items-today and desk-12345678 work", {}),
            ("the risk-assessment-report and mask-wearing-guidelines", {}),
            ("model 'whisk-large-v3-turbo' not found", {"X-Mode": "local"}),
        ):
            self.assertEqual(llm._redact_secrets(text, hdrs), text)

        # Keys adjacent to CJK, underscore, hyphen, or ASCII letters (sk-proj- / AIza) are scrubbed.
        for raw_msg, hdrs, secret in (
            ("密钥sk-1234567890abcdef无效", {}, "sk-1234567890abcdef"),
            ("中文sk-proj-abcdefghijklmnop1234", {}, "sk-proj-abcdefghijklmnop1234"),
            ("_sk-proj-abcdefghijklmnop1234", {}, "sk-proj-abcdefghijklmnop1234"),
            ("Xsk-proj-abcdefghijklmnop1234", {}, "sk-proj-abcdefghijklmnop1234"),
            ("密钥AIzaSyA1234567890abcdefghijklmnopqrstu", {}, "AIzaSyA1234567890abcdefghijklmnopqrstu"),
            ("echoed custom-key-val", {"x-api-key": "custom-key-val"}, "custom-key-val"),
            ("echoed proxy-secret", {"Proxy-Authorization": "Bearer proxy-secret"}, "proxy-secret"),
        ):
            out = llm._redact_secrets(raw_msg, hdrs)
            self.assertNotIn(secret, out, raw_msg)
            self.assertIn("[REDACTED]", out)

    def test_llm_e_normalize_local_base_reports_specific_bilingual_reasons(self) -> None:
        cases = (
            ("ftp://127.0.0.1:11434", "http://", "http://"),
            ("http://user:pass@127.0.0.1:11434", "password", "密码"),
            ("http://127.0.0.1:99999", "port", "端口"),
            ("http://127.0.0.1:abc/v1", "port", "端口"),
            ("http://192.168.1.5:11434", "127.0.0.1", "127.0.0.1"),
            ("http://127.0.0.1:11434/v1?foo=bar", "?", "?"),
            ("http://127.0.0.1:11434/v1?", "?", "?"),
            ("http://127.0.0.1:11434/v1#", "#", "#"),
            ("http://127.0.0.1:11434/v1/../x", "..", ".."),
        )
        for bad_url, en_word, zh_word in cases:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.normalize_local_base(bad_url)
            self.assertEqual(ctx.exception.code, "local_address", bad_url)
            self.assertIn(en_word, str(ctx.exception).lower(), bad_url)
            self.assertIn(zh_word, ctx.exception.message_zh, bad_url)

    def test_rust_sanitize_download_name_handles_multi_dot_reserved_names_and_keeps_extension(self) -> None:
        rust = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        fn_block = rust[rust.index("fn sanitize_download_name"):rust.index("fn free_name")]
        self.assertIn(".split('.')", fn_block)
        self.assertIn("trimmed.rfind('.')", fn_block)
        self.assertIn("format!(\"{base_clean}{ext}\")", fn_block)


if __name__ == "__main__":
    unittest.main()
