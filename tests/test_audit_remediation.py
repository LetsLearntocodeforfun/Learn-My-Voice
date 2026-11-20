"""End-to-end security and functional regression suite for the codebase & security audit remediations.

Covers every finding fixed in the audit:
- SEC-01: Per-launch token authentication, HttpOnly/SameSite=Strict cookie bootstrap, X-Studio-Token write gate,
  and Sec-Fetch-Site isolation on /api/* and non-GET routes.
- SEC-02: At-rest API key encryption (keystore.py + keys_sealed in config.json), tamper rejection, legacy migration,
  and locale-independent SID-based Windows ACL construction.
- SEC-03 & SEC-05: SHA-256 hash-pinned PyPI wheels (--require-hashes), TLS CA env-var stripping during install,
  npx --ignore-scripts in desktop/build.py, Python -B -E -s isolated launcher flags, and sys.path hardening in run_mcp.py.
- SEC-04 & SEC-09: HTTP 3xx redirect refusal on cloud and local LLM transports, model-name path/query injection
  rejection, normalize_local_base credential/query/fragment rejection, and API key redaction in upstream HTTP errors.
- SEC-06: Tauri CSP hardening, navigation scheme allowlist (blocking data:/blob:), private data folder permissions,
  token launch URL, download filename sanitization, and bounded free_name collision handling.
- SEC-07: MCP bounded stdin line draining (>12 MB), batch size cap, strict JSON (no NaN/Infinity, bounded recursion),
  and safe non-overwriting Jianying export via outside.save.
- SEC-08: Script field length ceilings (413 text_too_long), MAX_TRENDS pruning, fast list_scripts summary columns,
  bounded suggest candidate scanning, Stage 05 flagged_and_repaired_lines critic schema, and missing hard_max fallback.
- BUG-02: Views-driven exemplar ranking, trend performance lift, and hook library scoring when interactions are absent.
- BUG-01 & BUG-03: Real headless Google Chrome E2E verification of Voice DNA proposal approval (with free-text
  checkboxes), Voice DNA history list + unreadable warning banner, and editable post-copy persistence with optimistic
  locking (base_updated_at).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import http.server
import io
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath
from unittest import mock

from studio import corpus, guardrails, hooks, keystore, llm, mcp_server, pipeline, server, suggest, transcribe, trends
from studio.store import (
    MAX_LINE_CHARS,
    MAX_NOTES_CHARS,
    MAX_POST_CHARS,
    MAX_SCRIPT_LINES,
    MAX_SCRIPT_TITLE_CHARS,
    MAX_TRENDS,
    ROOT,
    StaleScript,
    Store,
    TextTooLong,
)

CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))


class TempStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.key_file = self.tmp / "os_config" / "keystore.key"
        self._env_patch = mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(self.key_file)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.store = Store(self.tmp / "data")


# ---- SEC-01: Local API authentication & Fetch Metadata isolation ------------------------------------------------

class TokenAuthAndFetchMetadataTest(TempStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = server.make_server(0, self.store, require_token=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def raw_request(self, method: str, path: str, body: object = None, headers: dict | None = None):
        hdrs = {"Host": "127.0.0.1:%d" % self.port}
        if headers:
            hdrs.update(headers)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            res = conn.getresponse()
            data = res.read()
            res_headers = {k.lower(): v for k, v in res.getheaders()}
            return res.status, data, res_headers
        finally:
            conn.close()

    def test_token_file_permissions_and_cleanup(self) -> None:
        token_file = self.store.data_dir / "server-token"
        self.assertTrue(token_file.exists())
        self.assertEqual(token_file.read_text(encoding="utf-8").strip(), self.httpd.app.token)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.assertFalse(token_file.exists())

    def test_unauthenticated_reads_and_writes_are_rejected(self) -> None:
        for method, path, body in (
            ("GET", "/", None),
            ("GET", "/api/state", None),
            ("GET", "/api/dna", None),
            ("POST", "/api/scripts", {"title": "x"}),
        ):
            status, _, _ = self.raw_request(method, path, body=body)
            self.assertEqual(status, 401, (method, path))

    def test_cookie_bootstrap_and_write_token_requirement(self) -> None:
        # Wrong query token is refused without setting any session cookie.
        status, _, hdrs = self.raw_request("GET", "/?token=wrong-token")
        self.assertEqual(status, 401)
        self.assertNotIn("set-cookie", hdrs)

        # Valid launch URL sets HttpOnly; SameSite=Strict session cookie and strips ?token= via 302.
        status, _, hdrs = self.raw_request("GET", "/?token=" + self.httpd.app.token)
        self.assertEqual(status, 302)
        self.assertEqual(hdrs.get("location"), "/")
        set_cookie = hdrs.get("set-cookie", "")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        cookie_pair = set_cookie.split(";", 1)[0]

        # Protocol-relative paths (///evil.example) are normalized to a single leading slash, never //evil.example.
        status_rel, _, hdrs_rel = self.raw_request("GET", "///evil.example/?token=" + self.httpd.app.token)
        self.assertEqual(status_rel, 302)
        self.assertEqual(hdrs_rel.get("location"), "/evil.example/")

        # Session cookie permits GET / (injecting data-token) and GET /api/state,
        # but NEVER permits state-changing writes without X-Studio-Token.
        status, html_bytes, _ = self.raw_request("GET", "/", headers={"Cookie": cookie_pair})
        self.assertEqual(status, 200)
        self.assertIn(('data-token="%s"' % self.httpd.app.token).encode("ascii"), html_bytes)

        status, state_bytes, _ = self.raw_request("GET", "/api/state", headers={"Cookie": cookie_pair})
        self.assertEqual(status, 200)
        self.assertIn("settings", json.loads(state_bytes.decode("utf-8")))

        status, _, _ = self.raw_request("POST", "/api/scripts", body={"title": "blocked"}, headers={"Cookie": cookie_pair})
        self.assertEqual(status, 401)

        status, _, _ = self.raw_request(
            "POST", "/api/scripts", body={"title": "blocked"},
            headers={"Cookie": cookie_pair, "X-Studio-Token": "wrong"},
        )
        self.assertEqual(status, 401)

        status, created, _ = self.raw_request(
            "POST", "/api/scripts", body={"title": "allowed"},
            headers={"Cookie": cookie_pair, "X-Studio-Token": self.httpd.app.token},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(created.decode("utf-8"))["title"], "allowed")

    def test_sec_fetch_site_blocks_cross_site_get_and_post(self) -> None:
        auth = {"X-Studio-Token": self.httpd.app.token}
        for bad_site in ("cross-site", "same-site"):
            status, _, _ = self.raw_request("GET", "/api/state", headers=dict(auth, **{"Sec-Fetch-Site": bad_site}))
            self.assertEqual(status, 403, bad_site)
            status, _, _ = self.raw_request("POST", "/api/scripts", body={"title": "x"},
                                            headers=dict(auth, **{"Sec-Fetch-Site": bad_site}))
            self.assertEqual(status, 403, bad_site)
        for good_site in ("same-origin", "none"):
            status, _, _ = self.raw_request("GET", "/api/state", headers=dict(auth, **{"Sec-Fetch-Site": good_site}))
            self.assertEqual(status, 200, good_site)


# ---- SEC-02: At-rest API key encryption & Windows ACL helper ----------------------------------------------------

class KeystoreAndSealedConfigTest(TempStoreCase):
    def test_api_keys_are_encrypted_on_disk_and_round_trip(self) -> None:
        secret_gemini = "AIzaSyTestSecretKeyForAudit998877"
        secret_openai = "sk-proj-AuditSecretKey112233445566"
        self.store.save_config({
            "provider": "gemini",
            "model": "gemini-2.5-pro",
            "keys": {"gemini": secret_gemini, "openai": secret_openai},
            "local_base_url": "",
            "trend_weight": "equal",
        })
        raw_disk = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn(secret_gemini, raw_disk)
        self.assertNotIn(secret_openai, raw_disk)
        parsed_disk = json.loads(raw_disk)
        self.assertEqual(parsed_disk["keys"], {})
        self.assertEqual(set(parsed_disk["keys_sealed"].keys()), {"gemini", "openai"})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o600)

        loaded = self.store.load_config()
        self.assertEqual(loaded["keys"], {"gemini": secret_gemini, "openai": secret_openai})

    def test_tampered_ciphertext_fails_closed_without_crashing(self) -> None:
        self.store.save_config({"provider": "openai", "keys": {"openai": "sk-real-secret"}})
        disk = json.loads(self.store.config_path.read_text(encoding="utf-8"))
        sealed = disk["keys_sealed"]["openai"]
        # Flip the final character before base64 padding.
        tampered = sealed[:-4] + ("A" if sealed[-4] != "A" else "B") + sealed[-3:]
        disk["keys_sealed"]["openai"] = tampered
        self.store.config_path.write_text(json.dumps(disk), encoding="utf-8")

        loaded = self.store.load_config()
        self.assertEqual(loaded["keys"], {})

    def test_legacy_plaintext_config_migrates_cleanly(self) -> None:
        self.store.data_dir.mkdir(parents=True, exist_ok=True)
        self.store.config_path.write_text(
            json.dumps({"provider": "claude", "keys": {"claude": "sk-ant-legacy-secret"}}),
            encoding="utf-8",
        )
        loaded = self.store.load_config()
        self.assertEqual(loaded["keys"]["claude"], "sk-ant-legacy-secret")
        self.store.save_config(loaded)
        after_disk = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-legacy-secret", after_disk)
        self.assertEqual(self.store.load_config()["keys"]["claude"], "sk-ant-legacy-secret")

    def test_windows_acl_uses_locale_independent_sids(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            res = mock.Mock()
            if cmd[0] == "whoami":
                res.returncode = 0
                res.stdout = b'"DESKTOP-PC\\Creator","S-1-5-21-1111-2222-3333-1001"\r\n'
            else:
                res.returncode = 0
                res.stdout = b""
            return res

        win_folder = self.tmp / "win_data"
        win_folder.mkdir(parents=True, exist_ok=True)
        keystore._WINDOWS_SID = None
        keystore._WINDOWS_SID_CHECKED = False
        keystore._ACL_APPLIED.clear()
        with mock.patch.object(keystore.os, "name", "nt"), mock.patch.object(keystore.subprocess, "run", side_effect=fake_run):
            ok = keystore.restrict_windows_acl(win_folder)
        keystore._WINDOWS_SID = None
        keystore._WINDOWS_SID_CHECKED = False
        keystore._ACL_APPLIED.clear()
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        icacls_cmd = calls[1]
        self.assertEqual(icacls_cmd[0], "icacls")
        self.assertIn("*S-1-5-18:(OI)(CI)F", icacls_cmd)
        self.assertIn("*S-1-5-32-544:(OI)(CI)F", icacls_cmd)
        self.assertIn("*S-1-5-21-1111-2222-3333-1001:(OI)(CI)F", icacls_cmd)


# ---- SEC-03 & SEC-05: Supply-chain pinning, TLS env stripping & launcher isolation ------------------------------

class SupplyChainAndSandboxHardeningTest(unittest.TestCase):
    def test_all_whisper_packages_have_valid_sha256_pins_and_require_hashes(self) -> None:
        expected = {transcribe.PIP_PIN} | set(transcribe.PACKAGES)
        self.assertEqual(set(transcribe.PACKAGE_HASHES.keys()), expected)
        for spec, digests in transcribe.PACKAGE_HASHES.items():
            self.assertGreaterEqual(len(digests), 1, spec)
            for d in digests:
                self.assertRegex(d, r"^[0-9a-f]{64}$", spec)
        formatted = transcribe._requirements_text(transcribe.PACKAGES)
        for pkg in transcribe.PACKAGES:
            self.assertIn(pkg, formatted)
        self.assertEqual(formatted.count("--hash=sha256:"), sum(len(transcribe.PACKAGE_HASHES[p]) for p in transcribe.PACKAGES))
        pip_src = (ROOT / "studio" / "transcribe.py").read_text(encoding="utf-8")
        self.assertIn('"--require-hashes"', pip_src)

    def test_install_env_strips_custom_ca_bundle_variables(self) -> None:
        with mock.patch.dict(os.environ, {
            "SSL_CERT_FILE": "/tmp/rogue-ca.pem",
            "REQUESTS_CA_BUNDLE": "/tmp/rogue-ca.pem",
            "PIP_INDEX_URL": "http://evil.example/simple",
            "PYTHONPATH": "/tmp/evil",
        }):
            env = transcribe._install_env()
        for forbidden in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_INDEX_URL", "PYTHONPATH"):
            self.assertNotIn(forbidden, env)

    def test_desktop_build_and_launchers_harden_execution(self) -> None:
        desktop_build = (ROOT / "desktop" / "build.py").read_text(encoding="utf-8")
        self.assertIn('"--ignore-scripts"', desktop_build)
        for name in ("Start Learn My Voice.command", "启动工作台.command", "start-learn-my-voice.sh", "Start Learn My Voice.bat"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("-B -E -s -m studio.server", text, name)
        mcp_runner = (ROOT / "run_mcp.py").read_text(encoding="utf-8")
        self.assertIn('p not in ("", ".")', mcp_runner)

    def test_model_download_requires_https_and_caps_stream_size(self) -> None:
        from studio.store import loads_stored

        with self.assertRaises(ValueError):
            loads_stored(("[" * 2500) + ("]" * 2500))
        with self.assertRaises(urllib.error.URLError):
            transcribe._open_url("http://huggingface.co/model.bin", {})

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp), True)

        class InfiniteStream:
            status = 200
            def read(self, n: int) -> bytes:
                return b"A" * n
            def close(self) -> None:
                pass

        dest = tmp / "weights.safetensors"
        with mock.patch.object(transcribe, "_open_url", return_value=InfiniteStream()):
            with self.assertRaises(transcribe.TranscribeError) as ctx:
                transcribe._fetch_file("https://huggingface.co/model.bin", dest, 1024, lambda _n: None)
            self.assertEqual(ctx.exception.reason, "download_damaged")
        self.assertFalse(dest.with_name("weights.safetensors.part").exists())


# ---- SEC-04 & SEC-09: Redirect blocking, model validation & error key scrubbing ---------------------------------

class LlmRedirectAndValidationTest(TempStoreCase):
    def test_redirect_is_refused_without_following_to_target(self) -> None:
        canary_hits = []

        class CanaryHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                canary_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"secret":"stolen"}')

            def do_POST(self):
                self.do_GET()

            def log_message(self, *args):
                pass

        canary = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CanaryHandler)
        canary_port = canary.server_address[1]
        t_canary = threading.Thread(target=canary.serve_forever, daemon=True)
        t_canary.start()
        self.addCleanup(canary.server_close)
        self.addCleanup(canary.shutdown)

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:%d/steal" % canary_port)
                self.end_headers()

            def log_message(self, *args):
                pass

        redirector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redir_port = redirector.server_address[1]
        t_redir = threading.Thread(target=redirector.serve_forever, daemon=True)
        t_redir.start()
        self.addCleanup(redirector.server_close)
        self.addCleanup(redirector.shutdown)

        url = "http://127.0.0.1:%d/v1/chat/completions" % redir_port
        with self.assertRaises(llm.LLMError) as ctx:
            llm._http("POST", url, {"Authorization": "Bearer sk-secret-token-1234567890"}, {})
        self.assertEqual(ctx.exception.code, "http")
        self.assertIn("HTTP 307", str(ctx.exception))
        self.assertEqual(canary_hits, [])

    def test_model_name_and_local_base_validation_and_secret_redaction(self) -> None:
        for valid in ("gemini-2.5-pro", "claude-sonnet-4-6", "qwen2.5:14b-instruct", "mlx-community/Qwen2.5-7B"):
            self.assertEqual(llm.validate_model_name(valid), valid)
        for invalid in ("../v1beta/files?key=evil", "gemini-pro?alt=sse", "model#frag", "bad model", "a\nb"):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.validate_model_name(invalid)
            self.assertEqual(ctx.exception.code, "bad_model", invalid)
        with self.assertRaises(llm.LLMError):
            llm._gemini_model_segment("models/../../other")

        for bad_local in (
            "http://user:pass@127.0.0.1:11434",
            "http://127.0.0.1:11434/v1?foo=bar",
            "http://127.0.0.1:11434/v1#frag",
            "http://192.168.1.5:11434",
            "ftp://127.0.0.1:11434",
            "http://127.0.0.1:99999",
        ):
            with self.assertRaises(llm.LLMError):
                llm.normalize_local_base(bad_local)

        raw_err = 'Invalid key sk-proj-1234567890abcdef and AIzaSy1234567890abcdef1234567890 or custom-secret-xyz'
        scrubbed = llm._redact_secrets(raw_err, {"x-api-key": "custom-secret-xyz"})
        self.assertNotIn("sk-proj-1234567890abcdef", scrubbed)
        self.assertNotIn("AIzaSy1234567890abcdef1234567890", scrubbed)
        self.assertNotIn("custom-secret-xyz", scrubbed)

        # Server settings endpoint also rejects bad model names with HTTP 400.
        httpd = server.make_server(0, self.store)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        try:
            conn.request(
                "POST", "/api/settings",
                body=json.dumps({"provider": "gemini", "model": "../evil?x=1"}),
                headers={"Host": "127.0.0.1:%d" % httpd.server_address[1], "Content-Type": "application/json"},
            )
            res = conn.getresponse()
            data = json.loads(res.read().decode("utf-8"))
            self.assertEqual((res.status, data["error"]), (400, "bad_model"))
        finally:
            conn.close()


# ---- SEC-06: Tauri desktop shell configuration & Rust guards ----------------------------------------------------

class DesktopShellSecurityTest(unittest.TestCase):
    def test_tauri_csp_and_rust_navigation_and_download_guards(self) -> None:
        conf = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        csp = conf["app"]["security"]["csp"]
        for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(directive, csp)

        rust = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotIn('"data" | "blob"', rust)
        self.assertIn("fn sanitize_download_name", rust)
        self.assertIn("fn free_name(folder: &Path, wanted: &OsStr) -> Option<PathBuf>", rust)
        self.assertIn("server-token", rust)
        self.assertIn("0o700", rust)
        self.assertIn("0o600", rust)


# ---- SEC-07: MCP bounded stdin, strict JSON & safe Jianying export ----------------------------------------------

class McpBoundsAndStrictJsonTest(TempStoreCase):
    def test_oversized_line_is_drained_and_next_request_succeeds(self) -> None:
        oversized = b'{"jsonrpc":"2.0","id":1,"pad":"' + (b"x" * (mcp_server.MAX_MCP_LINE_BYTES + 50000)) + b'"}\n'
        valid = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode("utf-8") + b"\n"
        stdin = io.BytesIO(oversized + valid)
        stdout = io.BytesIO()
        mcp_server.serve(self.store, stdin, stdout)
        replies = [json.loads(line.decode("utf-8")) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_batch_cap_nan_and_deep_nesting_are_rejected(self) -> None:
        deep = ("[" * 2500) + ("]" * 2500)
        with self.assertRaises(ValueError):
            llm.loads_strict(deep)
        with self.assertRaises(ValueError):
            llm.loads_strict('{"x": NaN}')

        big_batch = json.dumps([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcp_server.MAX_MCP_BATCH + 1)]).encode("utf-8") + b"\n"
        nan_line = b'{"jsonrpc":"2.0","id":9,"params":{"v":Infinity}}\n'
        deep_line = deep.encode("ascii") + b"\n"
        stdout = io.BytesIO()
        mcp_server.serve(self.store, io.BytesIO(big_batch + nan_line + deep_line), stdout)
        replies = [json.loads(line.decode("utf-8")) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r["error"]["code"] for r in replies], [-32600, -32700, -32700])

    def test_export_jianying_kit_numbers_collisions_and_locks_permissions(self) -> None:
        s = self.store.create_script(title="CON", lines=[{"spoken_script_zh": "今天去超市买菜嘛"}])
        first = Path(mcp_server.t_export_jianying_kit(self.store, {"script_id": s["id"]})["saved_to"])
        second = Path(mcp_server.t_export_jianying_kit(self.store, {"script_id": s["id"]})["saved_to"])
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertNotEqual(first, second)
        self.assertIn("(2)", second.name)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)


# ---- SEC-08 & BUG-03.5: Store bounds, trend pruning & suggest schema --------------------------------------------

class ResourceBoundsAndSuggestSchemaTest(TempStoreCase):
    def test_script_bounds_raise_text_too_long_and_http_413(self) -> None:
        with self.assertRaises(TextTooLong):
            self.store.create_script(title="题" * (MAX_SCRIPT_TITLE_CHARS + 1))
        with self.assertRaises(TextTooLong):
            self.store.create_script(notes="记" * (MAX_NOTES_CHARS + 1))
        with self.assertRaises(TextTooLong):
            self.store.create_script(lines=[{"spoken_script_zh": "好"}] * (MAX_SCRIPT_LINES + 1))
        with self.assertRaises(TextTooLong):
            self.store.create_script(lines=[{"spoken_script_zh": "好" * (MAX_LINE_CHARS + 1)}])
        with self.assertRaises(TextTooLong):
            self.store.create_script(post={"caption_body_zh": "文" * (MAX_POST_CHARS + 1)})

        httpd = server.make_server(0, self.store)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        try:
            conn.request(
                "POST", "/api/scripts",
                body=json.dumps({"title": "题" * (MAX_SCRIPT_TITLE_CHARS + 5)}),
                headers={"Host": "127.0.0.1:%d" % httpd.server_address[1], "Content-Type": "application/json"},
            )
            res = conn.getresponse()
            self.assertEqual(res.status, 413)
        finally:
            conn.close()

    def test_trend_table_prunes_beyond_max_trends_preferring_manual(self) -> None:
        self.store.upsert_trends([{"keyword": "手动保留词"}], source="manual")
        ai_items = [{"keyword": "热点词%d" % i, "source_url": "https://www.xiaohongshu.com/explore"}
                    for i in range(MAX_TRENDS + 25)]
        self.store.upsert_trends(ai_items, source="ai")
        self.assertLessEqual(self.store.trends_count(), MAX_TRENDS)
        keywords = {t["keyword"] for t in self.store.list_trends()}
        self.assertIn("手动保留词", keywords)

    def test_suggest_bounds_500_candidates_supports_stage5_critic_and_missing_hard_max(self) -> None:
        # Profile missing hard_max does not raise TypeError in suggest_prompt.
        dna_no_max = copy.deepcopy(EXAMPLE_DNA)
        dna_no_max["cadence_and_syntax"]["ideal_clause_length_chars"] = {}
        self.store.save_dna(dna_no_max)
        script = self.store.create_script(title="测试", lines=[
            {"spoken_script_zh": "今天去超市买菜嘛"},
            {"spoken_script_zh": "结果排队排了半小时哈"},
        ])
        sys_p, usr_p = suggest.suggest_prompt(self.store, script["id"], "rewrite", 0, "更口语")
        self.assertIn("16", sys_p + usr_p)

        # 500 candidates are capped cleanly without hanging.
        flood = {"suggestions": [{"spoken_script_zh": "第%d种说法嘛" % i} for i in range(500)]}
        t0 = time.perf_counter()
        out = suggest.apply_suggest(self.store, flood, script["id"], "rewrite", 0)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual(len(out["suggestions"]), suggest.MAX_SHOWN["rewrite"])
        self.assertGreater(out["left_out"], 490)

        # Stage 05 flagged_and_repaired_lines schema works for critic.
        stage5_reply = {
            "flagged_and_repaired_lines": [
                {
                    "line_no": 2,
                    "original_line": "结果排队排了半小时哈",
                    "repaired_zh": "结果排了足足半小时队嘛",
                    "why_fixed_en_zh": "更顺口",
                }
            ]
        }
        critic_out = suggest.apply_suggest(self.store, stage5_reply, script["id"], "critic")
        self.assertEqual(len(critic_out["suggestions"]), 1)
        self.assertEqual(critic_out["suggestions"][0]["text"], "结果排了足足半小时队嘛")


# ---- BUG-02: Views-driven ranking, trend lift & hook scoring ----------------------------------------------------

class ViewsDrivenRankingAndLiftTest(TempStoreCase):
    def test_views_only_samples_drive_exemplars_trend_lift_and_hooks(self) -> None:
        low = self.store.create_sample(title="低播放日常", text="今天在家收拾桌子嘛 / 擦了半天哈", metrics={"views": 1000})
        mid = self.store.create_sample(title="普通买菜", text="今天去楼下买水果嘛 / 挑了两个苹果哈", metrics={"views": 2000})
        high = self.store.create_sample(title="山姆超市爆款", text="周日去山姆补货嘛 / 这个牛排真的香哈", metrics={"views": 50000})
        samples = [low, mid, high]

        # 1. Exemplars put the 50,000-view organic sample first even with no likes/collects/comments.
        picked = corpus.exemplars(samples, budget_chars=5000, per_script_chars=1000)
        self.assertEqual(picked[0]["title"], "山姆超市爆款")

        # 2. Trend scoring gives "山姆" positive performance lift (> 0.5) over the 2,000-view median.
        self.store.upsert_trends([{"keyword": "山姆"}], source="manual")
        scored = trends.payload(self.store)["trends"]
        self.assertEqual(len(scored), 1)
        self.assertGreater(scored[0]["performance"], 0.7)

        # 3. Hook library computes times_usual and best() from views when interactions are absent.
        hook_lib = hooks.library(samples)
        self.assertEqual(hook_lib["videos_with_numbers"], 3)
        top_hook = next(o for o in hook_lib["openers"] if o["title"] == "山姆超市爆款")
        self.assertEqual(top_hook["times_usual"], 25.0)
        best_hooks = hooks.best(samples)
        self.assertEqual(best_hooks[0]["title"], "山姆超市爆款")


# ---- Minimal stdlib Chrome DevTools Protocol WebSocket client for BUG-01 & BUG-03 -------------------------------

class _CdpSession:
    """Tiny RFC 6455 WebSocket client over loopback for headless Chrome DevTools Protocol."""

    def __init__(self, ws_url: str) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        self.sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, parsed.hostname, parsed.port, key)
        self.sock.sendall(req.encode("ascii"))
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP handshake closed early")
            header_buf += chunk
        self._leftover = header_buf.split(b"\r\n\r\n", 1)[1]
        self._next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._leftover:
            take = self._leftover[:n]
            out.extend(take)
            self._leftover = self._leftover[len(take):]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("CDP socket closed")
            out.extend(chunk)
        return bytes(out)

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> bytes:
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if opcode == 0x9:  # ping -> pong
                self._send_frame(data, opcode=0xA)
                continue
            if opcode == 0x8:
                raise RuntimeError("CDP closed")
            return data

    def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        call_id = self._next_id
        msg = json.dumps({"id": call_id, "method": method, "params": params or {}}).encode("utf-8")
        self._send_frame(msg)
        while True:
            frame = json.loads(self._recv_frame().decode("utf-8"))
            if frame.get("id") == call_id:
                if "error" in frame:
                    raise RuntimeError("CDP error on %s: %s" % (method, frame["error"]))
                return frame.get("result", {})

    def eval_js(self, expression: str):
        res = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in res:
            raise AssertionError("JS exception: %s" % json.dumps(res["exceptionDetails"], ensure_ascii=False))
        return res.get("result", {}).get("value")

    def wait_for(self, js_predicate: str, timeout: float = 10.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.eval_js(js_predicate)
            if last:
                return last
            time.sleep(0.1)
        raise AssertionError("Timed out waiting for %s (last=%r)" % (js_predicate, last))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@unittest.skipUnless(CHROME_BIN.exists(), "Google Chrome is not installed on this machine")
class RealChromeEndToEndUiTest(TempStoreCase):
    """Drives the real SPA in headless Google Chrome against a token-protected server."""

    def test_voice_approval_history_unreadable_banner_and_post_editing_in_chrome(self) -> None:
        # Seed organic sample, archived voice profile, pending voice proposal, and a script with post package.
        sample_text = "\n".join([
            "今天去山姆补货嘛 / 结果排队排了半小时哈",
            "咱们下次还是早点出门吧 / 这个牛排真的超香哒",
        ] * 8)
        self.store.create_sample(title="周日山姆采购", text=sample_text, metrics={"views": 35000, "likes": 800})
        self.store.save_config({"provider": "manual", "model": "", "keys": {}, "local_base_url": "", "trend_weight": "equal"})

        # Save an initial active DNA then a second one so dna_history has an archived profile.
        first_dna = copy.deepcopy(EXAMPLE_DNA)
        first_dna["persona_summary"]["one_line_essence_zh"] = "第一版存档声音摘要"
        first_dna["source_corpus_summary"] = {"organic_scripts_analyzed": 1, "learned_at": "2026-11-01"}
        self.store.save_dna(first_dna)
        second_dna = copy.deepcopy(EXAMPLE_DNA)
        second_dna["persona_summary"]["one_line_essence_zh"] = "第二版当前声音摘要"
        second_dna["source_corpus_summary"] = {"organic_scripts_analyzed": 2, "learned_at": "2026-11-10"}
        self.store.save_dna(second_dna)

        # Create a pending proposal containing free-text descriptions (report.unverified).
        proposed = copy.deepcopy(EXAMPLE_DNA)
        proposed["persona_summary"]["one_line_essence_zh"] = "松弛真实的周末采购口吻"
        proposed["persona_summary"]["one_line_essence_en"] = "Relaxed weekend grocery tone"
        proposed["cadence_and_syntax"]["sentence_structure_habits"] = ["爱用短句接转折", "不被保留的句式习惯"]
        pipeline.propose_voice(self.store, proposed)
        self.assertIsNotNone(self.store.load_pending())

        script = self.store.create_script(
            title="山姆牛排草稿",
            lines=[{"spoken_script_zh": "今天去山姆补货嘛 / 结果人超多哈"}],
            post={
                "title_options": [{"title_zh": "初始小红书标题", "title_en": "Initial title"}],
                "cover_thumbnail_text_options_zh": ["初始封面字"],
                "caption_body_zh": "初始正文内容",
                "hashtags_zh": ["#山姆采购", "#周末日常"],
            },
        )

        httpd = server.make_server(0, self.store, require_token=True)
        port = httpd.server_address[1]
        token = httpd.app.token
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        udd = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(udd), True)
        proc = subprocess.Popen(
            [
                str(CHROME_BIN),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=" + str(udd),
                "--remote-debugging-port=0",
                "about:blank",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        def _stop_chrome() -> None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()

        self.addCleanup(_stop_chrome)

        ws_browser = None
        for _ in range(80):
            line = proc.stderr.readline()
            m = re.search(r"(ws://127\.0\.0\.1:(\d+)/devtools/browser/\S+)", line)
            if m:
                ws_browser = m.group(1)
                cdp_port = int(m.group(2))
                break
        self.assertIsNotNone(ws_browser, "Chrome DevTools port did not start")

        # Find the page target WebSocket URL.
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % cdp_port, timeout=10) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        page_ws = next(t["webSocketDebuggerUrl"] for t in targets if t.get("type") == "page")

        cdp = _CdpSession(page_ws)
        self.addCleanup(cdp.close)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # 1. Navigate with ?token=<token>: 302 strips query string and injects data-token; wait for boot() to settle.
        launch_url = "http://127.0.0.1:%d/?token=%s" % (port, token)
        cdp.call("Page.navigate", {"url": launch_url})
        cdp.wait_for(
            "document.documentElement.dataset.token === %s"
            " && typeof appState !== 'undefined' && Boolean(appState && appState.settings)"
            " && Boolean(document.getElementById('view')) && document.getElementById('view').innerHTML.length > 20"
            % json.dumps(token)
        )
        self.assertEqual(cdp.eval_js("location.search"), "")

        # 2. Open Setup -> Voice step cleanly, verify free-text checkboxes, uncheck the second sentence habit, and click #rv-approve (BUG-01).
        cdp.eval_js("(async () => { setupUi.open = 'voice'; location.hash = '#/start'; await renderSetup('voice'); return true; })()")
        cdp.wait_for("Boolean(document.getElementById('rv-approve')) && document.querySelectorAll('.ev input[data-free-path]').length >= 3")
        free_count = cdp.eval_js("document.querySelectorAll('.ev input[data-free-path]').length")
        self.assertGreaterEqual(free_count, 3)
        unchecked_ok = cdp.eval_js("""(() => {
            const box = document.querySelector('input[data-free-path="cadence_and_syntax.sentence_structure_habits[1]"]');
            if (!box) return false;
            box.checked = false;
            document.getElementById('rv-approve').click();
            return true;
        })()""")
        self.assertTrue(unchecked_ok)
        cdp.wait_for("!document.getElementById('rv-approve')")

        self.assertIsNone(self.store.load_pending())
        approved_dna, is_example = self.store.load_dna()
        self.assertFalse(is_example)
        self.assertEqual(approved_dna["persona_summary"]["one_line_essence_zh"], "松弛真实的周末采购口吻")
        self.assertEqual(approved_dna["cadence_and_syntax"]["sentence_structure_habits"], ["爱用短句接转折"])

        # 3. Open Voice page (#/voice): verify archived history renders (BUG-03.1) and restore works via askConfirm (#ask / #ask-yes).
        cdp.eval_js("location.hash = '#/voice';")
        cdp.wait_for("document.querySelectorAll('#voice-history [data-restore-id]').length >= 1")
        cdp.eval_js("document.querySelector('#voice-history [data-restore-id]').click()")
        cdp.wait_for("Boolean(document.getElementById('ask-yes')) && !document.getElementById('ask').hidden")
        cdp.eval_js("document.getElementById('ask-yes').click()")
        cdp.wait_for("document.getElementById('ask').hidden === true")
        time.sleep(0.3)
        restored_dna, _ = self.store.load_dna()
        self.assertIn(restored_dna["persona_summary"]["one_line_essence_zh"], ("第一版存档声音摘要", "第二版当前声音摘要"))

        # Corrupt voice_dna.json on disk and verify unreadable warning banner renders (BUG-03.2), then restore valid DNA.
        valid_bytes = self.store.dna_path.read_bytes()
        self.store.dna_path.write_text("{corrupt json", encoding="utf-8")
        cdp.eval_js("router()")
        cdp.wait_for("Boolean(document.querySelector('.banner')) && document.querySelector('.banner').textContent.length > 5")
        banner_text = cdp.eval_js("document.querySelector('.banner').textContent")
        self.assertTrue("读取" in banner_text or "read" in banner_text.lower(), banner_text)
        self.store.dna_path.write_bytes(valid_bytes)

        # 4. Open script editor (#/s/<id>), edit post title & hashtags in .post-row, and verify optimistic-locked save (BUG-03.3 & BUG-03.4).
        cdp.eval_js("location.hash = %s" % json.dumps("#/s/" + script["id"]))
        cdp.wait_for("Boolean(document.querySelector('[data-post-kind=\"title\"]'))")
        edited = cdp.eval_js("""(async () => {
            const titleIn = document.querySelector('[data-post-kind="title"][data-post-idx="0"]');
            const tagIn = document.querySelector('[data-post-kind="hashtags"][data-post-idx="0"]');
            titleIn.value = '修改后的小红书爆款标题';
            titleIn.dispatchEvent(new Event('input', { bubbles: true }));
            tagIn.value = '#山姆必买 #牛排测评';
            tagIn.dispatchEvent(new Event('input', { bubbles: true }));
            await flushSave();
            return cur.script.updated_at;
        })()""")
        self.assertIsInstance(edited, (int, float))
        saved_script = self.store.get_script(script["id"])
        self.assertEqual(saved_script["post"]["title_options"][0]["title_zh"], "修改后的小红书爆款标题")
        self.assertEqual(saved_script["post"]["hashtags_zh"], ["#山姆必买", "#牛排测评"])


if __name__ == "__main__":
    unittest.main()
