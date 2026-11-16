"""Behaviour that must not depend on which system the app runs on.

The app was built on macOS. These tests stand in, on any system, for two things Windows does differently:

- Before Python 3.13, Windows has no ``os.fchmod``.
- On Windows, Python reads the type of a file from the registry, where ``.js`` is sometimes ``text/plain``.

They show that the code no longer depends on either. They do not show that the app works on Windows:
only running it there does.
"""
import http.client
import mimetypes
import os
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import server
from studio.store import Store


class MissingFchmodTest(unittest.TestCase):
    """Saving this app's JSON files on a system that cannot set a mode on an open file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        # Take os.fchmod away for the length of the test, as on Windows before Python 3.13.
        if hasattr(os, "fchmod"):
            original = os.fchmod
            del os.fchmod
            self.addCleanup(setattr, os, "fchmod", original)

    def test_settings_profile_and_voice_are_saved(self):
        self.assertFalse(hasattr(os, "fchmod"))
        store = Store(self.tmp)

        store.update_config(lambda config: config.update(provider="manual"))
        self.assertEqual(store.load_config()["provider"], "manual")

        store.save_creator({"name": "小满"})
        self.assertEqual(store.load_creator(), {"name": "小满"})

        store.save_dna({"profile_version": "1.0"})
        store.save_dna({"profile_version": "1.0", "second": True})       # the first is copied to the history folder
        self.assertEqual(store.load_dna(), ({"profile_version": "1.0", "second": True}, False))
        self.assertEqual(len(store.list_dna_history()), 1)

        store.save_pending({"dna": {"profile_version": "1.0"}, "created_at": 1.0})
        self.assertEqual(store.load_pending()["created_at"], 1.0)

        store.save_recommendations({"ideas": []})
        self.assertEqual(store.load_recommendations(), {"ideas": []})

    @unittest.skipUnless(os.name == "posix", "file modes only say who may read a file on POSIX systems")
    def test_a_saved_file_is_still_closed_to_other_accounts(self):
        store = Store(self.tmp)
        store.update_config(lambda config: config["keys"].update(openai="sk-test"))
        self.assertEqual(stat.S_IMODE(os.stat(str(store.config_path)).st_mode) & 0o077, 0)


class FileTypeTest(unittest.TestCase):
    """The pages' scripts and style sheet are sent as what they are, whatever the system's own table says."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.httpd = server.make_server(0, Store(self.tmp))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def content_type(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        res = conn.getresponse()
        res.read()
        conn.close()
        return res.status, res.getheader("Content-Type")

    def test_types_do_not_come_from_the_systems_table(self):
        # What a Windows registry can say of ".js" and, here, of everything else too.
        with mock.patch.object(mimetypes, "guess_type", return_value=("text/plain", None)):
            for script in ("/app.js", "/setup.js", "/pages.js"):
                self.assertEqual(self.content_type(script), (200, "text/javascript; charset=utf-8"), script)
            self.assertEqual(self.content_type("/app.css"), (200, "text/css; charset=utf-8"))
            self.assertEqual(self.content_type("/"), (200, "text/html; charset=utf-8"))

    def test_every_file_of_the_interface_has_a_named_type(self):
        # A new kind of file in studio/web/ has to be added to server.STATIC_TYPES, or it is back to the system's table.
        for path in sorted(server.WEB_DIR.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                self.assertIn(path.suffix.lower(), server.STATIC_TYPES, path.name)


if __name__ == "__main__":
    unittest.main()
