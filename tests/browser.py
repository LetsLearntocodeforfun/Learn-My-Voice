"""A real browser for the tests that need one: headless Google Chrome, driven over its DevTools port.

    from tests import browser

    @unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
    def test_the_plan_page_lists_what_is_planned(self):
        with browser.open_app(self.store) as page:
            page.go("#/plan")
            page.wait_for("Boolean(document.getElementById('pl-list'))")
            self.assertEqual(page.eval_js("document.querySelectorAll('[data-plan-card]').length"), 1)
            self.assertEqual(page.problems, [])

``open_app`` starts the app on the given store (``server.make_server``, so nothing a test starts may ask GitHub
or a model for anything unless the test hands it a stand-in), starts Chrome with a profile of its own, opens
the app's first page and waits until the page has drawn itself. On the way out both are stopped.

``page.problems`` is everything the page itself reported as wrong while a call was waiting for its answer: an
exception nobody caught, a ``console.error``, and what the browser logs as an error (a file that did not load,
something the content security policy refused). A page that works leaves it empty; assert that.

``page.shot(path)`` writes a picture of the page, to be looked at. It shows nothing by itself.

What this does not show: how the page behaves in the desktop app's own window (WebKit on macOS, WebView2 on
Windows), which ``desktop/try_installer.py`` tries, or in any browser other than Chrome.

Standard library only. Not a test file: it has no tests of its own.
"""
from __future__ import annotations

import base64
import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from studio import server
from tests.test_audit_remediation import CHROME_BIN, _CdpSession

AVAILABLE = CHROME_BIN.exists()
WHY_NOT = "Google Chrome is not installed on this machine"
WIDTH, HEIGHT = 1280, 900
_BOOTED = ("typeof appState !== 'undefined' && Boolean(appState && appState.settings)"
           " && Boolean(document.getElementById('view')) && document.getElementById('view').innerHTML.length > 20")


class Page(_CdpSession):
    """One page of Chrome. Besides what ``_CdpSession`` does, it keeps what the page reported as wrong."""

    def __init__(self, ws_url: str) -> None:
        super().__init__(ws_url)
        self.problems: List[str] = []
        self.httpd: Any = None
        self.port = 0

    def _note(self, frame: Dict[str, Any]) -> None:
        method, params = frame.get("method"), frame.get("params") or {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            thrown = (details.get("exception") or {}).get("description") or details.get("text") or "exception"
            self.problems.append("exception: %s" % str(thrown)[:400])
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            said = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in params.get("args") or [])
            self.problems.append("console.error: %s" % said[:400])
        elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") == "error":
            entry = params["entry"]
            self.problems.append("browser: %s %s" % (str(entry.get("text"))[:300], str(entry.get("url") or "")[:200]))

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        self._next_id += 1
        call_id = self._next_id
        self._send_frame(json.dumps({"id": call_id, "method": method, "params": params or {}}).encode("utf-8"))
        while True:
            frame = json.loads(self._recv_frame().decode("utf-8"))
            if frame.get("id") == call_id:
                if "error" in frame:
                    raise RuntimeError("CDP error on %s: %s" % (method, frame["error"]))
                return frame.get("result", {})
            self._note(frame)

    def go(self, hash_route: str, settle: str = "true") -> None:
        """Open a page of the app by its ``#/...`` address and wait until the app has drawn it (and ``settle`` holds)."""
        self.eval_js("(() => { window.__drawn = false; const done = () => { window.__drawn = true; };"
                     " if (location.hash === %s) { router().then(done, done); }"
                     " else { location.hash = %s; setTimeout(function wait() {"
                     "   if (document.getElementById('view').innerHTML.length > 20) done(); else setTimeout(wait, 50); }, 150); }"
                     " return true; })()" % (json.dumps(hash_route), json.dumps(hash_route)))
        self.wait_for("window.__drawn === true && (%s)" % settle)
        time.sleep(0.25)                    # what a page fetches after it drew (a card, a list) has time to arrive
        self.eval_js("true")                # one more exchange, so that what the page reported meanwhile is read

    def shot(self, path: Path) -> Path:
        """Write a picture of the whole page to ``path`` (PNG)."""
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})["data"]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return path


@contextlib.contextmanager
def open_app(store: Any, lang: str = "", theme: str = "", **server_options: Any) -> Iterator[Page]:
    """The app on ``store`` in headless Chrome: yields the page once it has drawn itself.

    ``server_options`` go to ``server.make_server`` (``transport``, ``transcriber``, ``outside_world``).
    ``lang`` (``zh`` or ``en``) and ``theme`` (``light`` or ``dark``) are kept for the page before it is opened,
    the way a choice made in the app is kept.
    """
    kept = {name: value for name, value in (("lang", lang), ("theme", theme)) if value}
    if kept:
        store.keep_for_page(kept)
    httpd = server.make_server(0, store, **server_options)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    profile = Path(tempfile.mkdtemp(prefix="lmv-chrome-"))
    chrome = subprocess.Popen(
        [str(CHROME_BIN), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--user-data-dir=" + str(profile), "--remote-debugging-port=0", "--window-size=%d,%d" % (WIDTH, HEIGHT), "about:blank"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    page: Optional[Page] = None
    try:
        port = None
        for _ in range(200):
            line = chrome.stderr.readline()
            found = re.search(r"ws://127\.0\.0\.1:(\d+)/devtools/browser/\S+", line)
            if found:
                port = int(found.group(1))
                break
        if port is None:
            raise RuntimeError("Chrome did not open its DevTools port")
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=10) as answer:
            targets = json.loads(answer.read().decode("utf-8"))
        page = Page(next(target["webSocketDebuggerUrl"] for target in targets if target.get("type") == "page"))
        page.httpd, page.port = httpd, httpd.server_address[1]
        for domain in ("Page.enable", "Runtime.enable", "Log.enable"):
            page.call(domain)
        page.call("Emulation.setDeviceMetricsOverride", {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False})
        page.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % page.port})
        page.wait_for(_BOOTED, timeout=20.0)
        yield page
    finally:
        if page is not None:
            page.close()
        if chrome.poll() is None:
            chrome.terminate()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome.kill()
                chrome.wait(timeout=5)
        for pipe in (chrome.stdout, chrome.stderr):
            if pipe:
                pipe.close()
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(str(profile), ignore_errors=True)
