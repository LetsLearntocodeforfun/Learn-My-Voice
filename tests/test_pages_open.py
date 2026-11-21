"""What a browser asks the app for by itself, and what the app's first page reports as wrong.

A browser asks every site for ``/favicon.ico`` without being told to. The app had none and answered 404,
which Chrome reports as a file that failed to load: twice on every start, before any page of the app was
opened. ``tests/browser.py`` says that a page that works leaves ``page.problems`` empty, and on a fresh start
it never was (found by the agent working on updates, 21 November 2026, on a new folder).

So the app now has the icon: ``studio/web/favicon.ico``, a copy of the one the desktop app wears
(``desktop/src-tauri/icons/icon.ico``), written by the same script (``desktop/make_icons.py``). No code of the
app was changed for it. The server already hands out the files in
``studio/web/`` and already counts ``.ico`` among the page's own files, which are given without the token
(a browser asks for the icon on its own and sends no token with the request).

What these tests show: the icon is there, is an icon, is the desktop app's, and is given with and without
the token rule; and, where Google Chrome is installed, that the app's first page reports nothing wrong.

What they do not show: how the icon looks in a browser's tab (nobody looked at it for these tests), anything
about the other pages (each area's own tests open those), or any browser other than Chrome.
"""
from __future__ import annotations

import http.client
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import server
from studio.store import Store
from tests import browser

ROOT = Path(__file__).resolve().parent.parent
WEB_ICON = ROOT / "studio" / "web" / "favicon.ico"
DESKTOP_ICON = ROOT / "desktop" / "src-tauri" / "icons" / "icon.ico"


class _Folder(unittest.TestCase):
    """A folder of its own for each test, and a key file that is not the system's."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-open-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "os-keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")

    def serve(self, **options: object) -> int:
        httpd = server.make_server(0, self.store, **options)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def get(self, port: int, path: str) -> tuple:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": "127.0.0.1:%d" % port})
            answer = conn.getresponse()
            return answer.status, answer.getheader("Content-Type"), answer.read()
        finally:
            conn.close()


class TheIconTest(_Folder):
    def test_what_a_browser_asks_for_by_itself_is_there(self) -> None:
        status, kind, body = self.get(self.serve(), "/favicon.ico")
        self.assertEqual((status, kind), (200, "image/x-icon"))
        self.assertEqual(body, WEB_ICON.read_bytes())
        # An .ico file starts with: two zero bytes, the number 1 (an icon, not a cursor), how many pictures.
        self.assertEqual(body[:4], b"\x00\x00\x01\x00")
        self.assertGreaterEqual(int.from_bytes(body[4:6], "little"), 1)

    def test_it_is_given_where_the_page_itself_needs_the_token(self) -> None:
        port = self.serve(require_token=True)
        self.assertEqual(self.get(port, "/")[0], 401)                   # the page: not without the token
        status, kind, body = self.get(port, "/favicon.ico")             # the icon: a browser sends none with it
        self.assertEqual((status, kind), (200, "image/x-icon"))
        self.assertEqual(body, WEB_ICON.read_bytes())

    @unittest.skipUnless(DESKTOP_ICON.exists(), "the desktop app's files are not in this folder")
    def test_it_is_the_icon_the_desktop_app_wears(self) -> None:
        self.assertEqual(WEB_ICON.read_bytes(), DESKTOP_ICON.read_bytes())


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class FirstPageTest(_Folder):
    def test_a_new_folder_opens_with_nothing_reported_as_wrong(self) -> None:
        with browser.open_app(self.store) as page:
            self.assertEqual(page.problems, [])                         # before any page is asked for
            page.go("#/")
            self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
