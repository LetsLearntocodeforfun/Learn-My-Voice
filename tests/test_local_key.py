"""Regression tests for local capability token auth, linear Cookie parsing, and Chrome UX.

Covers reviewer findings in `auth` (e1_headers, e2_routes, e3_browser, token file lifecycle).
"""
from __future__ import annotations

import base64
import contextlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from studio import server
from studio.store import Store
from tests.test_audit_remediation import CHROME_BIN, _CdpSession


def _raw_http(port: int, lines: list[bytes], body: bytes = b"") -> tuple[int, bytes, bytes]:
    host = b"Host: 127.0.0.1:%d" % port
    req = b"\r\n".join([lines[0], host] + lines[1:] + [b"Connection: close", b"", b""]) + body
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(req)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    head, _, payload = raw.partition(b"\r\n\r\n")
    status_line = head.splitlines()[0].decode("iso-8859-1")
    status = int(status_line.split()[1])
    return status, head, payload


class LocalKeyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-auth-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self._env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "STUDIO_DATA_DIR": str(self.tmp / "data"),
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.store = Store(self.tmp / "data")


class HostileHeadersAndCookieParserTest(LocalKeyCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = server.make_server(0, self.store, require_token=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_e1_non_ascii_headers_and_query_tokens_return_401_never_500(self) -> None:
        token_b = self.httpd.app.token.encode("ascii")
        cookie_name_b = ("studio_session_%d" % self.port).encode("ascii")
        good_cookie = b"Cookie: " + cookie_name_b + b"=" + self.httpd.app.session_cookie.encode("ascii")

        caught = io.StringIO()
        with contextlib.redirect_stderr(caught):
            for label, lines, body in (
                ("utf8 header", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: \xc3\xa9"], b""),
                ("latin1 header", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: \xe9"], b""),
                ("root utf8 header", [b"GET / HTTP/1.1", b"X-Studio-Token: \xc3\xa9"], b""),
                ("post utf8 header", [b"POST /api/scripts HTTP/1.1", b"X-Studio-Token: \xc3\xa9",
                                      b"Content-Type: application/json", b"Content-Length: 13"], b'{"title":"x"}'),
                ("token+latin1", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: " + token_b + b"\xe9"], b""),
                ("good cookie + bad header", [b"GET /api/state HTTP/1.1", good_cookie, b"X-Studio-Token: \xe9"], b""),
                ("duplicate header 1", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: " + token_b, b"X-Studio-Token: wrong"], b""),
                ("duplicate header 2", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: wrong", b"X-Studio-Token: " + token_b], b""),
                ("empty header + good cookie", [b"GET /api/state HTTP/1.1", good_cookie, b"X-Studio-Token:"], b""),
                ("60kb header", [b"GET /api/state HTTP/1.1", b"X-Studio-Token: " + b"A" * 60000], b""),
                ("query utf8 pct", [b"GET /?token=%C3%A9 HTTP/1.1"], b""),
                ("query latin1 pct", [b"GET /?token=%E9 HTTP/1.1"], b""),
                ("query raw utf8", [b"GET /?token=\xc3\xa9 HTTP/1.1"], b""),
                ("static with bad query token", [b"GET /app.js?token=%E4%B8%AD HTTP/1.1"], b""),
                ("duplicate query token", [b"GET /?token=" + token_b + b"&token=wrong HTTP/1.1"], b""),
                ("POST /?token=valid", [b"POST /?token=" + token_b + b" HTTP/1.1",
                                        b"Content-Type: application/json", b"Content-Length: 2"], b"{}"),
            ):
                status, _, _ = _raw_http(self.port, lines, body)
                self.assertEqual(status, 401, label)

        # Also check require_token=False with non-ASCII X-Studio-Token: still 401, never 500!
        off = server.make_server(0, self.store, require_token=False)
        off_port = off.server_address[1]
        threading.Thread(target=off.serve_forever, daemon=True).start()
        self.addCleanup(off.server_close)
        self.addCleanup(off.shutdown)
        with contextlib.redirect_stderr(caught):
            status, _, _ = _raw_http(off_port, [b"GET /api/state HTTP/1.1", b"X-Studio-Token: \xc3\xa9"])
            self.assertEqual(status, 401)
        self.assertEqual(caught.getvalue().strip(), "")

    def test_e1_neighbor_cookies_and_linear_time_backslash_bomb(self) -> None:
        cname = ("studio_session_%d" % self.port).encode("ascii")
        cval = self.httpd.app.session_cookie.encode("ascii")
        valid_pair = cname + b"=" + cval

        for neighbor in (
            valid_pair,
            b"a[b]=1; " + valid_pair,
            valid_pair + b"; a[b]=1",
            b"weird(name=1; " + valid_pair,
            b"a/b=1; a@b=1; nameless; " + valid_pair,
            b'json={"a":1}; prefs={"theme":"dark"}; ' + valid_pair,
            b"greeting=hello world; " + valid_pair,
            b"other=\xc3\xa9; " + valid_pair,
            cname + b"=wrong; " + valid_pair,
        ):
            status, _, payload = _raw_http(self.port, [b"GET /api/state HTTP/1.1", b"Cookie: " + neighbor])
            self.assertEqual(status, 200, neighbor)
            self.assertIn(b'"settings"', payload)

        # Hostile 64 KB backslash-quote / octal cookie value completes in milliseconds, not seconds.
        for pattern in (b'\\"', b"\\1"):
            hostile = b'Cookie: ' + cname + b'="' + (pattern * 16000) + b'"'
            t0 = time.perf_counter()
            status, _, _ = _raw_http(self.port, [b"GET /api/state HTTP/1.1", hostile])
            elapsed = time.perf_counter() - t0
            self.assertEqual(status, 401)
            self.assertLess(elapsed, 0.4)


class StaticRoutesPortScopedCookiesAndLifecycleTest(LocalKeyCase):
    def test_static_assets_need_no_key_while_html_and_api_require_key(self) -> None:
        httpd = server.make_server(0, self.store, require_token=True)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        # Static CSS and JS load without token/cookie and carry Referrer-Policy: no-referrer.
        for asset in ("/app.css", "/app.js", "/pages.js", "/setup.js"):
            status, head, body = _raw_http(port, [b"GET " + asset.encode("ascii") + b" HTTP/1.1"])
            self.assertEqual(status, 200, asset)
            self.assertIn(b"Referrer-Policy: no-referrer", head)
            self.assertGreater(len(body), 200)

        # GET / and GET /index.html without key return 401 styled bilingual HTML without data-token or script.
        for html_path in (b"/", b"/index.html"):
            status, head, body = _raw_http(port, [b"GET " + html_path + b" HTTP/1.1"])
            self.assertEqual(status, 401)
            self.assertIn(b"text/html", head)
            self.assertIn(b"Referrer-Policy: no-referrer", head)
            text = body.decode("utf-8")
            self.assertIn("访问钥匙", text)
            self.assertIn("launch key", text)
            self.assertIn('/app.css', text)
            self.assertNotIn("<script", text)
            self.assertNotIn("data-token", text)

        # GET /api/state without key still returns 401 JSON.
        status, head, body = _raw_http(port, [b"GET /api/state HTTP/1.1"])
        self.assertEqual(status, 401)
        self.assertIn(b"application/json", head)
        self.assertEqual(json.loads(body.decode("utf-8"))["error"], "unauthorized")

    def test_two_instances_use_distinct_port_scoped_cookies_and_preserve_newer_token_file(self) -> None:
        store_a = Store(self.tmp / "shared_data")
        srv_a = server.make_server(0, store_a, require_token=True)
        port_a = srv_a.server_address[1]
        threading.Thread(target=srv_a.serve_forever, daemon=True).start()
        self.addCleanup(srv_a.server_close)
        self.addCleanup(srv_a.shutdown)

        srv_b = server.make_server(0, store_a, require_token=True)
        port_b = srv_b.server_address[1]
        threading.Thread(target=srv_b.serve_forever, daemon=True).start()
        self.addCleanup(srv_b.server_close)
        self.addCleanup(srv_b.shutdown)

        # Token file now holds srv_b's token; closing srv_a MUST NOT delete srv_b's server-token!
        token_file = store_a.data_dir / "server-token"
        self.assertEqual(token_file.read_text(encoding="utf-8").strip(), srv_b.app.token)

        _, head_a, _ = _raw_http(port_a, [("GET /?token=%s HTTP/1.1" % srv_a.app.token).encode("ascii")])
        _, head_b, _ = _raw_http(port_b, [("GET /?token=%s HTTP/1.1" % srv_b.app.token).encode("ascii")])
        cookie_a = re.search(rb"Set-Cookie: (studio_session_\d+=[^;\r\n]+)", head_a).group(1)
        cookie_b = re.search(rb"Set-Cookie: (studio_session_\d+=[^;\r\n]+)", head_b).group(1)
        self.assertNotEqual(cookie_a.split(b"=", 1)[0], cookie_b.split(b"=", 1)[0])

        # Browser sending both cookies at once authenticates cleanly to both servers!
        both = b"Cookie: " + cookie_a + b"; " + cookie_b
        self.assertEqual(_raw_http(port_a, [b"GET /api/state HTTP/1.1", both])[0], 200)
        self.assertEqual(_raw_http(port_b, [b"GET /api/state HTTP/1.1", both])[0], 200)
        # Cookie A alone does NOT authenticate to server B.
        self.assertEqual(_raw_http(port_b, [b"GET /api/state HTTP/1.1", b"Cookie: " + cookie_a])[0], 401)

        srv_a.shutdown()
        srv_a.server_close()
        self.assertTrue(token_file.exists())
        self.assertEqual(token_file.read_text(encoding="utf-8").strip(), srv_b.app.token)

    def test_main_reports_token_write_oserror_distinctly_from_ports_in_use(self) -> None:
        err = io.StringIO()
        with mock.patch.object(server.App, "_write_token_file", side_effect=PermissionError("read-only data dir")), \
                contextlib.redirect_stderr(err):
            rc = server.main(["--port", "0", "--no-browser"])
        self.assertEqual(rc, 1)
        self.assertNotIn("ports", err.getvalue().lower())
        self.assertIn("read-only data dir", err.getvalue())


@unittest.skipUnless(CHROME_BIN.exists(), "Google Chrome is not installed on this machine")
class ChromeAuthAndErrorUxTest(LocalKeyCase):
    def test_401_html_page_launch_link_strip_and_non_destructive_401_and_413_notices(self) -> None:
        script = self.store.create_script(title="测试草稿", lines=[{"spoken_script_zh": "今天去超市买菜嘛"}])
        httpd = server.make_server(0, self.store, require_token=True)
        port = httpd.server_address[1]
        token = httpd.app.token
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        udd = Path(tempfile.mkdtemp(prefix="lmv-chrome-auth-"))
        self.addCleanup(shutil.rmtree, str(udd), True)
        proc = subprocess.Popen(
            [
                str(CHROME_BIN), "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--user-data-dir=" + str(udd),
                "--remote-debugging-port=0", "about:blank",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def _stop() -> None:
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

        self.addCleanup(_stop)
        cdp_port = None
        for _ in range(80):
            m = re.search(r"ws://127\.0\.0\.1:(\d+)/devtools/browser/\S+", proc.stderr.readline())
            if m:
                cdp_port = int(m.group(1))
                break
        self.assertIsNotNone(cdp_port)
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % cdp_port, timeout=10) as resp:
            page_ws = next(t["webSocketDebuggerUrl"] for t in json.loads(resp.read().decode("utf-8")) if t.get("type") == "page")
        cdp = _CdpSession(page_ws)
        self.addCleanup(cdp.close)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # 1. Opening plain address without launch link renders styled bilingual 401 HTML page.
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % port})
        cdp.wait_for("document.body && document.body.innerText.includes('访问钥匙') && document.body.innerText.includes('launch key')")

        # 2. Launch link from a local HTML file forwards cleanly to 200 page, strips ?token=, and survives reload.
        forwarder = self.tmp / "launcher.html"
        launch_url = "http://127.0.0.1:%d/?token=%s#/s/%s" % (port, token, script["id"])
        forwarder.write_text('<meta http-equiv="refresh" content="0;url=%s">' % launch_url, encoding="utf-8")
        cdp.call("Page.navigate", {"url": forwarder.as_uri()})
        cdp.wait_for("Boolean(document.getElementById('title')) && document.getElementById('title').value === '测试草稿'")
        self.assertEqual(cdp.eval_js("location.search"), "")

        # 3. 413 TextTooLong during saveNow surfaces the server's bilingual sentence in #toast.
        cdp.eval_js("""(async () => {
            cur.script.title = '题'.repeat(505);
            await saveNow();
            return true;
        })()""")
        toast_413 = cdp.wait_for("document.getElementById('toast').classList.contains('show') && document.getElementById('toast').textContent")
        self.assertIn("505", toast_413)
        cdp.eval_js("cur.script.title = '测试草稿';")

        # 4. Simulate server restart (new token + session cookie): autosave shows #auth-expired WITHOUT wiping editor text!
        httpd.app.token = "restarted-new-token-value"
        httpd.app.session_cookie = "restarted-new-session-cookie"
        cdp.eval_js("""(async () => {
            const box = document.querySelector('.spoken');
            box.value = '我刚写的还没保存的重要台词';
            box.dispatchEvent(new Event('input', { bubbles: true }));
            await saveNow();
            return true;
        })()""")
        cdp.wait_for("Boolean(document.getElementById('auth-expired')) && Boolean(document.getElementById('auth-reload-btn'))")
        kept_text = cdp.eval_js("document.querySelector('.spoken').value")
        self.assertEqual(kept_text, "我刚写的还没保存的重要台词")


if __name__ == "__main__":
    unittest.main()
