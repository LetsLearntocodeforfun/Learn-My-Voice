"""Tests for AI provider clients, Anthropic Messages hardening, settings routes, and per-stage temperatures.

Every call uses a stand-in ``transport`` or a loopback HTTP stub on ``127.0.0.1`` under ``loopback_only()``:
no real API key is used and no network connection leaves this computer.
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

from studio import llm, pipeline, server, video_ingest
from studio.jobs import JobError
from studio.store import CLOUD_PROVIDERS, PROVIDERS, Store
from tests import browser
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
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": raw.decode("utf-8", "replace"),
            })
            handler_fn(self)

        do_GET = do_POST = _handle

        def log_message(self, *args):
            pass

    srv = server.StudioServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], seen


class AnthropicClientHardeningTest(unittest.TestCase):
    """L1: Anthropic docstring honesty, model validation, stop_reason handling, max_tokens fallback, redirects, secrets."""

    def test_docstring_states_anthropic_was_written_from_memory_and_never_run_live(self) -> None:
        doc = llm.__doc__ or ""
        self.assertIn("anthropic", doc)
        self.assertIn("21 November 2026", doc)
        self.assertIn("NEVER run against the live", doc)
        self.assertNotIn("docs.anthropic.com", doc)

    def test_anthropic_model_validation_enforces_cloud_rules_with_bilingual_errors(self) -> None:
        for valid in ("claude-sonnet-4-5", "claude-3-5-haiku-20241022", "claude-sonnet-4-5@20250929"):
            self.assertEqual(llm.validate_model_name(valid, "anthropic"), valid)
        for bad in ("", "claude sonnet", "claude?x=1", "claude#frag", r"claude\x", "../claude", "a\nb", "x" * 201):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.validate_model_name(bad, "anthropic")
            self.assertEqual(ctx.exception.code, "bad_model", bad)
            self.assertTrue(ctx.exception.message_zh, bad)

    def test_list_models_sends_headers_filters_invalid_entries_and_rejects_bad_shape(self) -> None:
        captured = {}

        def fake_transport(method, url, headers, body):
            captured.update(method=method, url=url, headers=headers, body=body)
            return {
                "data": [
                    {"id": "claude-sonnet-4-5"},
                    {"id": "claude-3-5-haiku-20241022"},
                    {"id": "claude-sonnet-4-5"},
                    {"id": "bad model with spaces"},
                    {"id": ""},
                    "not-a-dict",
                ]
            }

        models = llm.list_models("anthropic", "sk-ant-fake-key-0001", transport=fake_transport)
        self.assertEqual(models, ["claude-3-5-haiku-20241022", "claude-sonnet-4-5"])
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], llm.ANTHROPIC_BASE + "/models?limit=100")
        self.assertEqual(captured["headers"], {"x-api-key": "sk-ant-fake-key-0001", "anthropic-version": llm.ANTHROPIC_VERSION})
        self.assertIsNone(captured["body"])

        for bad_shape in ("oops", {"data": None}, {"data": "not-a-list"}):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.list_models("anthropic", "sk-ant-fake-key-0001", transport=lambda *a, s=bad_shape: s)
            self.assertEqual(ctx.exception.code, "bad_response")
            self.assertTrue(ctx.exception.message_zh)

    def test_stop_reason_truncation_and_refusal_raise_clear_bilingual_llm_error(self) -> None:
        # Truncated mid-JSON due to max_tokens: caught via stop_reason instead of cryptic JSON parse failure.
        def truncated_transport(method, url, headers, body):
            return {
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": '{"script_lines": [{"spoken_script_zh": "半句'}],
            }

        with self.assertRaises(llm.LLMError) as trunc:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", truncated_transport)
        self.assertEqual(trunc.exception.code, "bad_response")
        self.assertIn("max_tokens", str(trunc.exception))
        self.assertIn("max_tokens", trunc.exception.message_zh)

        # Explicit stop_reason == "refusal"
        def refusal_transport(method, url, headers, body):
            return {"stop_reason": "refusal", "content": []}

        with self.assertRaises(llm.LLMError) as ref:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", refusal_transport)
        self.assertEqual(ref.exception.code, "bad_response")
        self.assertIn("refus", str(ref.exception).lower())
        self.assertIn("拒绝", ref.exception.message_zh)

        # Content block of type "refusal" or empty text content
        for reply in (
            {"stop_reason": "end_turn", "content": [{"type": "refusal", "refusal": "Cannot comply"}]},
            {"stop_reason": "end_turn", "content": []},
            {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "..."}]},
        ):
            with self.assertRaises(llm.LLMError) as empty_err:
                llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", lambda *a, r=reply: r)
            self.assertEqual(empty_err.exception.code, "bad_response")
            self.assertTrue(empty_err.exception.message_zh)

    def test_max_tokens_400_retries_once_at_4096_and_never_loops(self) -> None:
        seen_max_tokens = []

        def fallback_ok_transport(method, url, headers, body):
            seen_max_tokens.append(body["max_tokens"])
            if body["max_tokens"] > 4096:
                raise llm.LLMError(
                    "HTTP 400: max_tokens: 8192 > 4096, which is the maximum allowed number of output tokens for claude-3-haiku",
                    "http",
                )
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"ok": 1}'}]}

        out = llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku-20240307", "sys", "usr", fallback_ok_transport)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(seen_max_tokens, [8192, 4096])

        # If 4096 also fails with 400 max_tokens, it stops after the single retry and raises.
        attempts = []

        def always_400_transport(method, url, headers, body):
            attempts.append(body["max_tokens"])
            raise llm.LLMError("HTTP 400: max_tokens is still invalid", "http")

        with self.assertRaises(llm.LLMError) as ctx:
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-3-haiku-20240307", "sys", "usr", always_400_transport)
        self.assertEqual(ctx.exception.code, "http")
        self.assertEqual(attempts, [8192, 4096])

        # Non-max_tokens 400 errors do NOT retry at all.
        non_token_attempts = []

        def bad_req_transport(method, url, headers, body):
            non_token_attempts.append(body["max_tokens"])
            raise llm.LLMError("HTTP 400: messages: roles must alternate", "http")

        with self.assertRaises(llm.LLMError):
            llm.complete_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "sys", "usr", bad_req_transport)
        self.assertEqual(non_token_attempts, [8192])

    def test_wire_http_400_scrubs_sk_ant_keys_and_refuses_redirects(self) -> None:
        secret = "sk-ant-api03-FakeSecretKey987654321"

        def error_echo_handler(h):
            err_body = json.dumps({
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key %s provided" % secret,
                },
            }).encode("utf-8")
            h.send_response(401)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(err_body)))
            h.end_headers()
            h.wfile.write(err_body)

        err_srv, err_url, _ = _start_stub(error_echo_handler)
        self.addCleanup(err_srv.server_close)
        self.addCleanup(err_srv.shutdown)

        canary_srv, canary_url, canary_seen = _start_stub(
            lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b"{}"))
        )
        self.addCleanup(canary_srv.server_close)
        self.addCleanup(canary_srv.shutdown)

        with loopback_only(), mock.patch.object(llm, "ANTHROPIC_BASE", err_url + "/v1"):
            with self.assertRaises(llm.LLMError) as auth_err:
                llm.complete_json("anthropic", secret, "claude-sonnet-4-5", "sys", "usr")
            self.assertEqual(auth_err.exception.code, "http")
            self.assertNotIn(secret, str(auth_err.exception))
            self.assertIn("[REDACTED]", str(auth_err.exception))

        # Pattern-only scrubbing when headers are absent
        self.assertEqual(llm._redact_secrets("bad key sk-ant-short1 in log"), "bad key [REDACTED] in log")

        # Redirects on Anthropic endpoints are refused on every 3xx status and never leak the key
        for code in (301, 302, 303, 307, 308):
            r_srv, r_url, _ = _start_stub(
                lambda h, c=code: (
                    h.send_response(c),
                    h.send_header("Location", "%s/steal?k=%s" % (canary_url, secret)),
                    h.end_headers(),
                )
            )
            self.addCleanup(r_srv.server_close)
            self.addCleanup(r_srv.shutdown)
            with loopback_only(), mock.patch.object(llm, "ANTHROPIC_BASE", r_url + "/v1"):
                for call_fn in (
                    lambda: llm.list_models("anthropic", secret),
                    lambda: llm.complete_json("anthropic", secret, "claude-sonnet-4-5", "sys", "usr"),
                ):
                    with self.assertRaises(llm.LLMError) as redir_err:
                        call_fn()
                    self.assertEqual(redir_err.exception.code, "http")
                    self.assertNotIn(secret, str(redir_err.exception))
                    self.assertTrue(redir_err.exception.message_zh)
        self.assertEqual(canary_seen, [])

    def test_anthropic_web_search_stays_false_and_discover_skips_web_lookup_cleanly(self) -> None:
        self.assertFalse(llm.can_search("anthropic"))
        with self.assertRaises(llm.LLMError) as ctx:
            llm.search_json("anthropic", "sk-ant-fake-0001", "claude-sonnet-4-5", "prompt", transport=lambda *a: {})
        self.assertEqual(ctx.exception.code, "cannot_browse")
        self.assertTrue(ctx.exception.message_zh)


class ProviderIntegrationRoutesAndUiTest(unittest.TestCase):
    """Audit of CLOUD_PROVIDERS, public_settings, server settings routes, _require_ai, video_ingest, and Settings UI."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-providers-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.env = mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.tmp / "data")
        self.calls = []

        def stand_in(method, url, headers, body):
            self.calls.append((method, url, headers, body))
            if method == "GET" and url.endswith("/models?limit=100"):
                return {"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}]}
            if method == "POST" and url.endswith("/messages"):
                return {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": json.dumps({
                        "ideas": [{"title_zh": "周末采购实话", "evidence_titles": ["样本一"]}],
                        "script_lines": [{"spoken_script_zh": "今天去超市买菜啦 / 顺便带了两盒草莓嘛"}],
                    }, ensure_ascii=False)}],
                }
            raise AssertionError("Unexpected call: %s %s" % (method, url))

        self.httpd = server.make_server(0, self.store, transport=stand_in)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def api(self, method: str, path: str, body=None, raw_body: bytes = None, content_type: str = "application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": "127.0.0.1:%d" % self.port}
            payload = raw_body
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif raw_body is not None:
                headers["Content-Type"] = content_type
            elif method == "POST":
                payload = b"{}"
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            res = conn.getresponse()
            data = json.loads(res.read().decode("utf-8"))
            return res.status, data
        finally:
            conn.close()

    def test_store_settings_routes_require_ai_discover_and_video_ingest_messaging(self) -> None:
        self.assertEqual(CLOUD_PROVIDERS, ("gemini", "openai", "anthropic"))
        self.assertEqual(PROVIDERS, ("gemini", "openai", "anthropic", "local"))

        # Before connecting, _require_ai refuses AI generation
        st0, err0 = self.api("POST", "/api/generate", {"notes": "买草莓"})
        self.assertEqual((st0, err0["error"]), (400, "ai_not_configured"))

        # Connect Anthropic models and verify public_settings never exposes raw key
        st_m, mod_out = self.api("POST", "/api/settings/models", {"provider": "anthropic", "key": "sk-ant-fake-key-7777"})
        self.assertEqual(st_m, 200, mod_out)
        self.assertEqual(mod_out["models"], ["claude-haiku-4-5", "claude-sonnet-4-5"])
        self.assertTrue(mod_out["settings"]["keys"]["anthropic"])
        self.assertFalse(mod_out["settings"]["configured"])  # model not saved yet
        self.assertNotIn("sk-ant-fake-key-7777", json.dumps(mod_out))

        # Invalid Anthropic model name in POST /api/settings returns bilingual 400 bad_model
        st_bad, bad_out = self.api("POST", "/api/settings", {"provider": "anthropic", "model": "bad model name"})
        self.assertEqual((st_bad, bad_out["error"]), (400, "bad_model"))
        self.assertTrue(bad_out.get("message_zh"))

        # Valid save configures Anthropic without needing to resend key
        st_ok, ok_out = self.api("POST", "/api/settings", {"provider": "anthropic", "model": "claude-sonnet-4-5"})
        self.assertEqual(st_ok, 200, ok_out)
        self.assertTrue(ok_out["settings"]["configured"])
        self.assertEqual(ok_out["settings"]["provider"], "anthropic")

        # GET /api/state reports lookup_available == False for Anthropic
        _, state = self.api("GET", "/api/state")
        self.assertFalse(state["lookup_available"])

        # _require_ai passes and POST /api/generate drafts via Anthropic
        self.store.create_sample(title="样本一", text="今天去超市买菜啦 / 买了两盒草莓嘛")
        st_gen, script = self.api("POST", "/api/generate", {"notes": "买草莓"})
        self.assertEqual(st_gen, 201, script)
        self.assertEqual(script["lines"][0]["spoken_script_zh"], "今天去超市买菜啦 / 顺便带了两盒草莓嘛")

        # pipeline.discover skips web lookup with no_web_search and still completes ideas via Anthropic
        disc = pipeline.discover(self.store, focus="超市日常", transport=self.httpd.app.transport)
        self.assertEqual(disc["lookup"]["status"], pipeline.LOOKUP_SKIPPED)
        self.assertEqual(disc["lookup"]["reason"], "no_web_search")
        self.assertEqual(disc["ideas"]["status"], "done")

        # video_ingest explains plainly why Anthropic (and local) do not take direct video/audio uploads
        info = video_ingest.ai_video_info(self.store)
        self.assertEqual(info, {"ai_video_capable": False, "ai_video_provider": ""})
        st_up, up = self.api("PUT", "/api/uploads?name=clip.mp4", raw_body=b"fakevideo", content_type="application/octet-stream")
        self.assertEqual(st_up, 201)
        st_vi, vi_err = self.api("POST", "/api/videos/ai", {"upload_id": up["upload_id"]})
        self.assertEqual((st_vi, vi_err["error"]), (400, "ai_video_not_configured"))
        self.assertIn("Anthropic Claude", vi_err["message"])
        self.assertIn("Anthropic Claude", vi_err["message_zh"])

        # Direct call to transcribe_via_ai also cleans up temp file and reports Anthropic-specific message
        tmp_clip = self.tmp / "temp_clip.mp4"
        tmp_clip.write_bytes(b"fakevideo")
        with self.assertRaises(JobError) as j_err:
            video_ingest.transcribe_via_ai(self.store, {"path": str(tmp_clip), "name": "clip.mp4"})
        self.assertIn("Anthropic Claude", str(j_err.exception))
        self.assertIn("Anthropic Claude", j_err.exception.message_zh)
        self.assertFalse(tmp_clip.exists())

    @unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
    def test_settings_ui_renders_anthropic_honesty_hint_in_zh_en_and_light_dark(self) -> None:
        self.store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5"),
            c["keys"].update(anthropic="sk-ant-fake-key-7777"),
        ))
        for lang_code, theme, expected_phrase in (
            ("zh", "light", "尚未连接真实 Anthropic 服务实测"),
            ("en", "dark", "not yet verified against the live Anthropic service"),
        ):
            with browser.open_app(self.store, lang=lang_code, theme=theme) as page:
                page.go("#/settings", "Boolean(document.getElementById('anthropic-hint'))")
                hint = page.eval_js("document.getElementById('anthropic-hint').textContent")
                self.assertIn(expected_phrase, hint)
                # Switching to OpenAI and back to Anthropic keeps the saved Anthropic model selected
                page.eval_js("document.querySelector('[data-provider=\"openai\"]').click()")
                page.eval_js("document.querySelector('[data-provider=\"anthropic\"]').click()")
                selected = page.eval_js("document.getElementById('model').value")
                self.assertEqual(selected, "claude-sonnet-4-5")
                self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
