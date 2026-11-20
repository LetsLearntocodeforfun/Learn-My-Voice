"""Behaviour that must not depend on which system the app runs on.

The app was built on macOS. These tests stand in, on any system, for things Windows does differently:

- Before Python 3.13, Windows has no ``os.fchmod``.
- On Windows, Python reads the type of a file from the registry, where ``.js`` is sometimes ``text/plain``.
- Windows refuses to replace a file while anyone has it open. GitHub's Windows machines showed that: of eight
  saves at the same moment, six failed with "Access is denied".
- On Windows "reuse the address" lets a second program listen on a port that is taken. GitHub's Windows
  machines showed that too: starting on a taken port did not fail.

They show that the code no longer depends on these. They do not show that the app works on Windows:
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
from studio import store as store_module
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


class ReplaceRefusedForAMomentTest(unittest.TestCase):
    """Saving while Windows will not let the old file be replaced yet."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp)
        self.real = os.replace
        self.calls = 0

    def refusing(self, times):
        def replace(src, dst):
            self.calls += 1
            if self.calls <= times:
                raise PermissionError(13, "Access is denied")
            return self.real(src, dst)
        return replace

    def leftovers(self):
        return sorted(path.name for path in self.tmp.iterdir() if path.name.endswith(".tmp"))

    def test_where_a_refusal_may_pass_the_save_is_tried_again_and_goes_through(self):
        self.store.save_recommendations({"n": 1})
        with mock.patch.object(store_module, "_REPLACE_MAY_BE_REFUSED", True), mock.patch.object(store_module.time, "sleep") as waited, \
                mock.patch.object(store_module.os, "replace", self.refusing(3)):
            self.store.save_recommendations({"n": 2})
        self.assertEqual((self.calls, waited.call_count), (4, 3))
        self.assertEqual(self.store.load_recommendations(), {"n": 2})
        self.assertEqual(self.leftovers(), [])
        waits = [call.args[0] for call in waited.call_args_list]
        self.assertEqual(waits, sorted(waits))                                   # it waits a little longer each time
        self.assertLess(sum(waits), 0.1)

    def test_a_refusal_that_does_not_pass_is_reported_and_leaves_the_old_file(self):
        self.store.save_recommendations({"n": 1})
        with mock.patch.object(store_module, "_REPLACE_MAY_BE_REFUSED", True), mock.patch.object(store_module.time, "sleep") as waited, \
                mock.patch.object(store_module.os, "replace", self.refusing(10 ** 6)):
            with self.assertRaises(PermissionError):
                self.store.save_recommendations({"n": 2})
        self.assertEqual(self.calls, store_module._REPLACE_TRIES)                # a fixed number of tries, then it gives up
        self.assertLess(sum(call.args[0] for call in waited.call_args_list), 1.0)        # in under a second
        self.assertEqual(self.store.load_recommendations(), {"n": 1})
        self.assertEqual(self.leftovers(), [])

    def test_where_a_file_can_always_be_replaced_a_refusal_is_raised_at_once(self):
        with mock.patch.object(store_module, "_REPLACE_MAY_BE_REFUSED", False), mock.patch.object(store_module.time, "sleep") as waited, \
                mock.patch.object(store_module.os, "replace", self.refusing(1)):
            with self.assertRaises(PermissionError):
                self.store.save_recommendations({"n": 2})
        self.assertEqual((self.calls, waited.call_count), (1, 0))
        self.assertEqual(store_module._REPLACE_MAY_BE_REFUSED, os.name == "nt")  # tried again on Windows, and only there


class PortTakenTest(unittest.TestCase):
    """Starting on a port another program is listening on has to fail, so that the next port is tried."""

    def test_a_taken_port_is_refused_on_every_system(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp), True)
        first = server.make_server(0, Store(tmp / "one"))
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            server.make_server(first.server_address[1], Store(tmp / "two"))

    def test_only_windows_asks_for_the_port_exclusively(self):
        # Elsewhere "reuse the address" is kept: it lets the app start again right after it was closed.
        self.assertEqual(server.StudioServer.allow_reuse_address, os.name != "nt")
        asked = []

        class Socket:
            def setsockopt(self, *args):
                asked.append(args)

        with mock.patch.object(server.ThreadingHTTPServer, "server_bind", lambda self: None):
            stand_in = server.StudioServer.__new__(server.StudioServer)
            stand_in.socket = Socket()
            with mock.patch.object(server.os, "name", "nt"), mock.patch.object(server.socket, "SO_EXCLUSIVEADDRUSE", 0xFFFB, create=True):
                stand_in.server_bind()
            self.assertEqual(asked, [(server.socket.SOL_SOCKET, 0xFFFB, 1)])
            del asked[:]
            with mock.patch.object(server.os, "name", "posix"):
                stand_in.server_bind()
            self.assertEqual(asked, [])

    def test_server_bind_never_calls_getfqdn(self):
        # On macOS VMs with Python 3.14+, socket.getfqdn("127.0.0.1") can block for 30+ seconds in mDNS.
        with mock.patch.object(server.socket, "getfqdn", side_effect=AssertionError("getfqdn should not be called")):
            srv = server.StudioServer(("127.0.0.1", 0), server.Handler)
            try:
                self.assertEqual(srv.server_name, "127.0.0.1")
                self.assertGreater(srv.server_port, 0)
            finally:
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
