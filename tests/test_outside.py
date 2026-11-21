"""Tests for what the app does outside its own window and data folder (studio/outside.py), and the page's side of it.

Inside the desktop app the page is shown by the system's web view, and what such a view does with a download, a
link to another site or the browser's own yes/no question differs from system to system. So there the page asks
the core, and asks its own questions. Each class covers one thing that has to hold:

- a file name made from a script's title is one every system takes, and still says what the file is;
- a saved file never replaces another, lands in the folder it was meant for or the next one, and is whole;
- the Downloads folder is asked of the system, with the usual place as the answer when that fails;
- only an ordinary web address is handed to the system to open;
- the three requests: what they do, what they refuse, and that another site cannot send them;
- the window check, by which the installer trial sees the real window, exists only when the app was started
  for it, and then opens nothing on the machine;
- the pages ask their own questions and never the browser's.

Nothing here opens a browser or a file window, and nothing is written into the real Downloads folder: the
openers are replaced, and the folder is a throwaway one. That the system's own openers do their work is not
shown by these tests.
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import outside, server
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "studio" / "web"
FORBIDDEN = set('<>:"/\\|?*')


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


class NameTest(unittest.TestCase):
    def test_what_a_title_can_hold_and_what_becomes_of_it(self):
        for given, wanted in [
            ("周末 Costco 补货-口播文稿.txt", "周末 Costco 补货-口播文稿.txt"),          # an ordinary name is left as it is
            ("Costco/Trader Joe's: 补货?-口播文稿.txt", "Costco Trader Joe's 补货 -口播文稿.txt"),
            ("../../etc/passwd-字幕.srt", "etc passwd-字幕.srt"),                        # no way out of the folder
            ("..\\..\\windows\\system32-kit.zip", "windows system32-kit.zip"),
            ("a\tb\nc\rd\x00e-voiceover.txt", "a b c d e-voiceover.txt"),
            ("   spaces   and . dots .  -shot_list.txt", "spaces and . dots . -shot_list.txt"),
            (".hidden-subtitles.srt", "hidden-subtitles.srt"),                          # a leading dot hides a file on a Mac
            ("x\u202egnp.exe-teleprompter.html", "xgnp.exe-teleprompter.html"),         # the right-to-left mark is gone
            ("zero\u200bwidth\ufeff-kit.zip", "zerowidth-kit.zip"),
            ("-字幕.srt", "-字幕.srt"),                                                  # a script without a title
            ("", "export"), (None, "export"), ("...", "export"), ("///", "export"), (".txt", "export.txt"),
            ("no ending at all", "no ending at all"),
            ("ending.that is not one", "ending.that is not one"),
            ("CON.txt", "_CON.txt"), ("nul", "_nul"), ("Com1.srt", "_Com1.srt"), ("LPT9.zip", "_LPT9.zip"),
            ("con.tar.gz", "_con.tar.gz"),                                              # Windows keeps the name whatever follows
            ("CONSOLE.txt", "CONSOLE.txt"), ("COM10.txt", "COM10.txt"),
        ]:
            self.assertEqual(outside.safe_name(given), wanted, repr(given))

    def test_a_long_title_loses_its_middle_and_keeps_what_says_which_export_it_is(self):
        for title in ("标题" * 200, "a" * 500, "long title " * 60, "😀" * 200, "ｗｉｄｅ" * 100):
            for kind in ("-口播文稿.txt", "-剪映素材包.zip", "-teleprompter.html", "-onscreen_text.srt"):
                name = outside.safe_name(title + kind)
                self.assertTrue(name.endswith(kind), name)                              # the kind and the ending are whole
                self.assertTrue(name.startswith(title[:10]), name)                      # and so is its start
                self.assertIn("…", name)
                self.assertLessEqual(len(name.encode("utf-8")), outside.MAX_NAME_BYTES + 12)
                self.assertLessEqual(len(name.encode("utf-8")) + len(" (999)"), 255)    # room for the number that keeps two apart
                self.assertLessEqual(len(name), outside.MAX_NAME_CHARS + 12)
        short = "一" * 60 + "-口播文稿.txt"
        self.assertEqual(outside.safe_name(short), short)                               # under the limit nothing is cut

    def test_no_name_ever_holds_what_a_system_refuses(self):
        pieces = ["标题", "a b", "/", "\\", ":", "*", "?", '"', "<", ">", "|", ".", "..", " ", "\n", "\x7f", "\u202e", "\ud800",
                  "CON", "aux", "…", "-", "(2)", "😀", "\u3000"]
        seen = set()
        for first in pieces:
            for second in pieces:
                for ending in ("", ".txt", ".srt", ".", ".toolongtobeanending"):
                    name = outside.safe_name(first + second + ending)
                    seen.add(name)
                    self.assertTrue(name)
                    self.assertFalse(FORBIDDEN & set(name), (first, second, ending, name))
                    self.assertFalse(any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in name), repr(name))
                    self.assertEqual(name, name.strip(" ."), repr(name))                # Windows drops a dot or a space at the end
                    self.assertNotIn(name.split(".")[0].strip().upper(), outside._DEVICES, repr(name))
                    self.assertNotIn(name, (".", ".."))
                    name.encode("utf-8")                                                # half a character would fail here
                    self.assertEqual(outside.safe_name(name), name, repr(name))         # made safe once, it stays as it is
        self.assertGreater(len(seen), 200)


class SaveTest(TempCase):
    def test_a_file_is_written_whole_under_its_name_and_a_second_never_replaces_it(self):
        folder = self.tmp / "Downloads"
        first = outside.save("面霜-口播文稿.txt", "第一次".encode("utf-8"), [folder])
        second = outside.save("面霜-口播文稿.txt", "第二次".encode("utf-8"), [folder])
        third = outside.save("面霜-口播文稿.txt", b"", [folder])
        self.assertEqual([path.name for path in (first, second, third)], ["面霜-口播文稿.txt", "面霜-口播文稿 (2).txt", "面霜-口播文稿 (3).txt"])
        self.assertEqual([path.read_bytes().decode("utf-8") for path in (first, second, third)], ["第一次", "第二次", ""])
        self.assertEqual({path.parent for path in (first, second, third)}, {folder})
        second.unlink()                                                                 # a gap is filled, nothing is renumbered
        self.assertEqual(outside.save("面霜-口播文稿.txt", b"x", [folder]).name, "面霜-口播文稿 (2).txt")
        self.assertEqual(first.read_bytes().decode("utf-8"), "第一次")
        bare = [outside.save("no ending", b"1", [folder]).name, outside.save("no ending", b"2", [folder]).name]
        self.assertEqual(bare, ["no ending", "no ending (2)"])
        (folder / "taken.zip").mkdir()                                                  # a folder of that name is in the way too
        self.assertEqual(outside.save("taken.zip", b"z", [folder]).name, "taken (2).zip")

    def test_the_name_is_made_safe_so_the_file_cannot_land_anywhere_else(self):
        folder = self.tmp / "Downloads"
        for title in ("../../outside", "/etc/passwd", "..\\up", "a/b/c", "C:\\Windows\\x"):
            saved = outside.save(title + "-字幕.srt", b"1", [folder])
            self.assertEqual(saved.parent, folder, title)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["Downloads"])
        self.assertTrue(all(path.is_file() for path in folder.iterdir()))

    def test_a_folder_that_cannot_be_used_is_passed_over_for_the_next(self):
        in_the_way = self.tmp / "not-a-folder"
        in_the_way.write_bytes(b"a file where the folder should be")
        second = self.tmp / "deeper" / "exports"                                        # not there yet: it is made
        saved = outside.save("kit.zip", b"zip", [in_the_way, in_the_way / "below", second])
        self.assertEqual((saved, saved.read_bytes()), (second / "kit.zip", b"zip"))
        self.assertEqual(in_the_way.read_bytes(), b"a file where the folder should be")
        with self.assertRaises(outside.NotSaved) as refused:
            outside.save("kit.zip", b"zip", [in_the_way, in_the_way / "below"])
        self.assertIsInstance(refused.exception, OSError)                               # callers that catch OSError catch it
        self.assertTrue(str(refused.exception))
        with self.assertRaises(outside.NotSaved):
            outside.save("kit.zip", b"zip", [])

    def test_a_file_that_could_only_be_written_in_part_is_removed_and_the_next_folder_gets_it(self):
        full, roomy = self.tmp / "full", self.tmp / "roomy"
        real_open = open

        class Fails:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *problem):
                self.handle.close()

            def write(self, data):
                self.handle.write(data[:3])
                self.handle.flush()
                raise OSError(28, "No space left on device")

        def opens(path, mode):
            handle = real_open(path, mode)
            return Fails(handle) if Path(path).parent == full else handle

        with mock.patch.object(outside, "open", opens, create=True):
            saved = outside.save("big.zip", b"0123456789", [full, roomy])
        self.assertEqual((saved, saved.read_bytes()), (roomy / "big.zip", b"0123456789"))
        self.assertEqual(list(full.iterdir()), [])                                      # no half file is left behind
        with mock.patch.object(outside, "open", opens, create=True), self.assertRaises(outside.NotSaved) as refused:
            outside.save("big.zip", b"0123456789", [full])
        self.assertIn("No space left", str(refused.exception))
        self.assertEqual(list(full.iterdir()), [])

    def test_saves_made_at_the_same_moment_each_get_a_file_of_their_own(self):
        folder = self.tmp / "Downloads"
        results, problems = [], []

        def one(number):
            try:
                results.append((number, outside.save("same.txt", ("save %d" % number).encode("ascii") * 2000, [folder])))
            except Exception as exc:                                                    # noqa: BLE001 - reported below
                problems.append(exc)

        threads = [threading.Thread(target=one, args=(number,)) for number in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(problems, [])
        self.assertEqual(len({path for _, path in results}), 12)
        for number, path in results:
            self.assertEqual(path.read_bytes(), ("save %d" % number).encode("ascii") * 2000)
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         sorted(["same.txt"] + ["same (%d).txt" % n for n in range(2, 13)]))


class FolderTest(TempCase):
    def test_the_real_answer_on_this_system_is_a_whole_path(self):
        found = outside.downloads_folder()
        self.assertTrue(found.is_absolute(), found)
        self.assertTrue(found.name)
        if os.name == "nt":                                                             # the question is really put to Windows here
            asked = outside._windows_downloads()
            self.assertTrue(asked is None or asked.is_absolute(), asked)
            self.assertEqual(found, asked or Path.home() / "Downloads")
        elif sys.platform == "darwin":
            self.assertEqual(found, Path.home() / "Downloads")

    def test_when_asking_the_system_fails_the_usual_place_is_the_answer(self):
        def fails():
            raise OSError("the system did not answer")
        with mock.patch.object(outside, "_windows_downloads", fails), mock.patch.object(outside, "_xdg_downloads", fails):
            self.assertEqual(outside.downloads_folder(), Path.home() / "Downloads")
        with mock.patch.object(outside, "_windows_downloads", lambda: None), mock.patch.object(outside, "_xdg_downloads", lambda: None):
            self.assertEqual(outside.downloads_folder(), Path.home() / "Downloads")

    def test_the_folder_a_linux_desktop_wrote_down(self):
        home, config = self.tmp / "home", self.tmp / "config"
        config.mkdir()
        elsewhere = self.tmp / "elsewhere" / "下载"

        def asked(text):
            (config / "user-dirs.dirs").write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}), mock.patch.object(outside.Path, "home", return_value=home):
                return outside._xdg_downloads()

        self.assertEqual(asked('XDG_DESKTOP_DIR="$HOME/桌面"\nXDG_DOWNLOAD_DIR="$HOME/下载"\n'), home / "下载")       # a translated name
        self.assertEqual(asked('# a comment\n  XDG_DOWNLOAD_DIR = "%s"\n' % elsewhere.as_posix()), Path(elsewhere.as_posix()))
        self.assertIsNone(asked('XDG_DOWNLOAD_DIR="$HOME/"\n'))                         # how a desktop says "there is none"
        self.assertIsNone(asked('XDG_DOWNLOAD_DIR="relative/place"\n'))
        self.assertIsNone(asked('XDG_DESKTOP_DIR="$HOME/Desktop"\n'))
        self.assertIsNone(asked("XDG_DOWNLOAD_DIR=\n"))
        (config / "user-dirs.dirs").unlink()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}):
            self.assertIsNone(outside._xdg_downloads())                                 # no such file: not a desktop that writes one


class LinkTest(unittest.TestCase):
    GOOD = ["https://chatgpt.com/", "http://example.com/a?b=c&d=%E4%B8%AD#e", "HTTPS://Example.COM", "https://例子.测试/路径",
            "https://user@example.com:8443/x", "http://127.0.0.1:8765/", "https://example.com/" + "a" * 1900]
    BAD = [("file:///etc/passwd", "not_web"), ("javascript:alert(1)", "not_web"), ("data:text/html,x", "not_web"),
           ("mailto:a@example.com", "not_web"), ("ms-settings:privacy", "not_web"), ("smb://server/share", "not_web"),
           ("example.com", "not_web"), ("//example.com/x", "not_web"), ("-https://example.com", "not_web"), ("/Applications/Calculator.app", "not_web"),
           ("https://", "no_host"), ("https:///path", "no_host"), ("http://:80/", "no_host"),
           ("https://exa mple.com", "not_plain"), ("https://example.com/\n", "not_plain"), ("https://example.com/\u202ex", "not_plain"),
           ("https://example.com\\@evil.example", "not_plain"), ("https://example.com/\x00", "not_plain"), ("https://[::1", "not_plain"),
           ("https://example.com/" + "a" * 2000, "too_long"), ("", "missing"), (None, "missing"), (5, "missing"), (["https://x"], "missing")]

    def openers(self):
        """Every way this module could start something, replaced: what was asked for is written down, nothing starts."""
        started = []
        done = subprocess.CompletedProcess([], 0)
        patches = [
            mock.patch.object(outside.subprocess, "run", lambda command, **more: started.append(("run", command)) or done),
            mock.patch.object(outside.subprocess, "Popen", lambda command, **more: started.append(("start", command))),
            mock.patch.object(outside.os, "startfile", lambda what: started.append(("startfile", what)), create=True),
            mock.patch.object(outside.webbrowser, "open", lambda what: started.append(("browser", what)) or True),
            mock.patch.object(outside.shutil, "which", lambda name: "/usr/bin/" + name),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return started

    def test_only_an_ordinary_web_address_may_be_opened(self):
        for url in self.GOOD:
            self.assertEqual(outside.link_problem(url), "", url)
        for url, why in self.BAD:
            self.assertEqual(outside.link_problem(url), why, repr(url)[:60])

    def test_a_refused_address_starts_nothing(self):
        started = self.openers()
        for url, _ in self.BAD:
            self.assertIs(outside.open_link(url), False, repr(url)[:60])
        self.assertEqual(started, [])

    def test_an_address_is_handed_over_as_one_word_never_as_a_command_line(self):
        started = self.openers()
        url = "https://example.com/a b".replace(" ", "%20") + "?q=1&r=`id`;$(id)|x"
        self.assertEqual(outside.link_problem(url), "")
        self.assertIs(outside.open_link(url), True)
        self.assertEqual(len(started), 1)
        how, what = started[0]
        if sys.platform == "darwin":
            self.assertEqual((how, what), ("run", ["/usr/bin/open", url]))
        elif os.name == "nt":
            self.assertEqual((how, what), ("startfile", url))
        else:
            self.assertEqual((how, what), ("browser", url))

    def test_when_nothing_can_be_started_the_answer_is_no_and_not_an_error(self):
        def fails(*args, **more):
            raise OSError("nothing to start it with")
        with mock.patch.object(outside.subprocess, "run", fails), mock.patch.object(outside.subprocess, "Popen", fails), \
                mock.patch.object(outside.os, "startfile", fails, create=True), mock.patch.object(outside.webbrowser, "open", fails):
            self.assertIs(outside.open_link("https://example.com/"), False)
            self.assertIs(outside.reveal(Path(tempfile.gettempdir()) / "x.txt"), False)

    def test_showing_a_file_names_the_file_to_the_file_manager(self):
        started = self.openers()
        path = Path(tempfile.gettempdir()) / "下载" / "面霜 口播文稿 (2).txt"
        self.assertIs(outside.reveal(path), True)
        self.assertEqual(len(started), 1)
        how, what = started[0]
        if sys.platform == "darwin":
            self.assertEqual((how, what), ("run", ["/usr/bin/open", "-R", str(path)]))
        elif os.name == "nt":
            self.assertEqual((how, what), ("start", 'explorer /select,"%s"' % path))
            self.assertNotIn('"', str(path))                                            # what the quoting relies on
        else:
            self.assertEqual((how, what), ("start", ["/usr/bin/xdg-open", str(path.parent)]))


class World:
    """Stands in for studio.outside in the server: a Downloads folder of its own, and nothing is opened."""

    def __init__(self, downloads):
        self.downloads = downloads
        self.opened, self.shown = [], []
        self.opens, self.shows = True, True

    def downloads_folder(self):
        return self.downloads

    def save(self, name, data, folders):
        return outside.save(name, data, folders)

    def link_problem(self, url):
        return outside.link_problem(url)

    def open_link(self, url):
        self.opened.append(url)
        return self.opens

    def reveal(self, path):
        self.shown.append(str(path))
        return self.shows


class ServerCase(TempCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.tmp / "home" / "data")
        self.downloads = self.tmp / "Downloads"
        self.world = World(self.downloads)
        self.httpd = server.make_server(0, self.store, lambda *args: {}, None, self.world)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))
        self.script = self.store.create_script(title="面霜/分享: 第 1 篇?", target_sec=30,
                                               lines=[{"spoken_script_zh": "今天去超市啦 / 买了好多东西", "visual_broll_en_zh": "超市门口"}])

    def send(self, method, path, payload=None, headers=None, body=None):
        head = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, (json.loads(raw.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else raw)

    def save_address(self, kind="voiceover", script_id=None):
        return "/api/scripts/%s/export/%s/save" % (script_id or self.script["id"], kind)


class RequestTest(ServerCase):
    def test_by_default_the_app_uses_the_real_module(self):
        httpd = server.make_server(0, Store(self.tmp / "other"))
        self.addCleanup(httpd.server_close)
        self.assertIs(httpd.app.outside, outside)
        self.assertIsNone(httpd.app.window_check)

    def test_an_export_is_saved_into_downloads_as_the_file_a_download_would_be(self):
        for kind in ("voiceover", "shots", "flower", "srt", "flower_srt", "jianying", "prompter"):
            for lang in ("zh", "en"):
                status, sent = self.send("GET", "/api/scripts/%s/export/%s?lang=%s" % (self.script["id"], kind, lang))
                self.assertEqual(status, 200, kind)
                status, saved = self.send("POST", self.save_address(kind), {"lang": lang})
                self.assertEqual(status, 201, saved)
                path = Path(saved["path"])
                self.assertEqual((path.parent, saved["folder"], saved["name"], saved["in_downloads"]), (self.downloads, str(self.downloads), path.name, True))
                if kind != "jianying":                                                  # a zip holds the time it was made
                    self.assertEqual(path.read_bytes(), sent, (kind, lang))
                else:
                    self.assertEqual(path.read_bytes()[:2], b"PK")
                self.assertTrue(path.name.startswith("面霜 分享 第 1 篇 -"), path.name)    # the title, made safe
        names = sorted(path.name for path in self.downloads.iterdir())
        self.assertEqual(len(names), 14)
        self.assertIn("面霜 分享 第 1 篇 -口播文稿.txt", names)
        self.assertIn("面霜 分享 第 1 篇 -voiceover.txt", names)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["Downloads", "home"])        # nothing landed anywhere else
        # The language of the name can also come with the address, as it does for a download; without either it is Chinese.
        self.assertEqual(self.send("POST", self.save_address() + "?lang=en", {})[1]["name"], "面霜 分享 第 1 篇 -voiceover (2).txt")
        self.assertEqual(self.send("POST", self.save_address(), {})[1]["name"], "面霜 分享 第 1 篇 -口播文稿 (2).txt")

    def test_the_same_export_twice_is_two_files(self):
        first = self.send("POST", self.save_address(), {"lang": "zh"})[1]
        self.store.update_script(self.script["id"], {"lines": [{"spoken_script_zh": "改过的一句"}]})
        second = self.send("POST", self.save_address(), {"lang": "zh"})[1]
        self.assertEqual((first["name"], second["name"]), ("面霜 分享 第 1 篇 -口播文稿.txt", "面霜 分享 第 1 篇 -口播文稿 (2).txt"))
        self.assertTrue(Path(first["path"]).read_text(encoding="utf-8").startswith("今天去超市啦"))        # the first is as it was
        self.assertTrue(Path(second["path"]).read_text(encoding="utf-8").startswith("改过的一句"))

    def test_a_title_that_tries_to_leave_the_folder_stays_in_it(self):
        for title in ("../../../outside", "/etc/cron.d/x", "..\\..\\up", "CON", "\u202egpj.exe"):
            script = self.store.create_script(title=title, target_sec=30, lines=[{"spoken_script_zh": "一句"}])
            status, saved = self.send("POST", self.save_address(script_id=script["id"]), {})
            self.assertEqual((status, Path(saved["path"]).parent), (201, self.downloads), title)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["Downloads", "home"])

    def test_when_downloads_cannot_be_written_to_the_file_goes_beside_the_data_and_the_answer_says_so(self):
        self.downloads.write_bytes(b"a file where the folder should be")
        status, saved = self.send("POST", self.save_address(), {"lang": "en"})
        exports = (self.tmp / "home" / "exports").resolve()
        self.assertEqual((status, Path(saved["path"]).parent, saved["in_downloads"], saved["folder"]), (201, exports, False, str(exports)))
        self.assertTrue(Path(saved["path"]).read_text(encoding="utf-8").startswith("今天去超市啦"))
        # Neither place can be written to: refused with a code, and a sentence in each language that names the cause.
        shutil.rmtree(str(exports))
        exports.write_bytes(b"in the way as well")
        status, refused = self.send("POST", self.save_address(), {"lang": "en"})
        self.assertEqual((status, refused["error"]), (507, "export_not_saved"))
        self.assertIn("could not be saved", refused["message"])
        self.assertIn("没能存到", refused["message_zh"])
        self.assertEqual(self.httpd.app.saved_files, [saved["path"]])                   # only what was really saved is remembered

    def test_what_a_save_refuses(self):
        self.assertEqual(self.send("POST", self.save_address("nonsense"), {})[1]["error"], "unknown_export")
        self.assertEqual(self.send("POST", self.save_address(script_id="0" * 12), {})[0], 404)
        self.assertEqual(self.send("POST", self.save_address(script_id="not-an-id"), {})[0], 404)
        for method in ("GET", "PUT", "DELETE"):                                         # saving is a POST and nothing else
            status, _ = self.send(method, self.save_address(), {} if method != "GET" else None)
            self.assertIn(status, (404, 405), method)
        status, refused = self.send("POST", self.save_address(), body=b'{"lang": "en"}', headers={"Content-Type": "text/plain"})
        self.assertEqual((status, refused["error"]), (415, "json_required"))            # what a page on another site could send
        status, refused = self.send("POST", self.save_address(), {"lang": "en"}, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, refused["error"]), (403, "bad_origin"))
        status, refused = self.send("POST", self.save_address(), {"lang": "en"}, headers={"Host": "evil.example"})
        self.assertEqual((status, refused["error"]), (403, "bad_host"))
        self.assertFalse(self.downloads.exists())                                       # none of these wrote anything
        self.assertEqual(self.httpd.app.saved_files, [])

    def test_only_a_file_this_run_saved_can_be_shown(self):
        saved = self.send("POST", self.save_address(), {})[1]
        other = self.downloads / "someone-else.txt"
        other.write_bytes(b"not saved by the app")
        for wanted in (str(other), "/etc/passwd", str(self.downloads), "", None, 5, [saved["path"]], saved["path"] + "/..", saved["name"]):
            status, refused = self.send("POST", "/api/outside/show", {"path": wanted})
            self.assertEqual((status, refused["error"]), (404, "not_found"), wanted)
        self.assertEqual(self.send("POST", "/api/outside/show", {})[0], 404)
        self.assertEqual(self.world.shown, [])
        self.assertEqual(self.send("POST", "/api/outside/show", {"path": saved["path"]}), (200, {"shown": True}))
        self.assertEqual(self.world.shown, [saved["path"]])
        self.world.shows = False                                                        # the file manager could not be started
        self.assertEqual(self.send("POST", "/api/outside/show", {"path": saved["path"]}), (200, {"shown": False}))
        status, refused = self.send("POST", "/api/outside/show", {"path": saved["path"]}, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, refused["error"]), (403, "bad_origin"))
        self.assertEqual(self.send("GET", "/api/outside/show")[0], 404)

    def test_how_many_saved_files_are_remembered_is_bounded(self):
        app = self.httpd.app
        for number in range(server.MAX_REMEMBERED_FILES + 30):
            app.remember_saved(Path("/somewhere/%d.txt" % number))
        self.assertEqual(len(app.saved_files), server.MAX_REMEMBERED_FILES)
        self.assertEqual(app.saved_files[-1], str(Path("/somewhere/%d.txt" % (server.MAX_REMEMBERED_FILES + 29))))     # the newest are kept

    def test_a_web_address_is_opened_and_nothing_else(self):
        self.assertEqual(self.send("POST", "/api/outside/open", {"url": "https://chatgpt.com/"}), (200, {"opened": True}))
        self.assertEqual(self.world.opened, ["https://chatgpt.com/"])
        for url, _ in LinkTest.BAD:
            status, refused = self.send("POST", "/api/outside/open", {"url": url})
            self.assertEqual((status, refused["error"]), (400, "bad_link"), repr(url)[:60])
            self.assertTrue(refused["message"] and refused["message_zh"])
        self.assertEqual(self.send("POST", "/api/outside/open", {})[0], 400)
        self.assertEqual(self.world.opened, ["https://chatgpt.com/"])                   # no refused address reached the opener
        self.world.opens = False                                                        # no browser could be started
        self.assertEqual(self.send("POST", "/api/outside/open", {"url": "https://example.com/"}), (200, {"opened": False}))
        status, refused = self.send("POST", "/api/outside/open", {"url": "https://example.com/"}, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, refused["error"]), (403, "bad_origin"))
        status, refused = self.send("POST", "/api/outside/open", body=b'{"url": "https://example.com/"}', headers={"Content-Type": "text/plain"})
        self.assertEqual((status, refused["error"]), (415, "json_required"))
        self.assertEqual(self.world.opened, ["https://chatgpt.com/", "https://example.com/"])
        self.assertEqual(self.send("GET", "/api/outside/open")[0], 404)


class WindowCheckTest(ServerCase):
    def test_it_is_not_there_unless_the_app_was_started_for_it(self):
        self.assertNotIn("window_check", self.send("GET", "/api/state")[1])
        for method, path in (("GET", "/api/window-check"), ("POST", "/api/window-check"), ("POST", "/api/window-check/start"),
                             ("GET", "/api/window-check/start")):
            status, refused = self.send(method, path, {} if method == "POST" else None)
            self.assertEqual((status, refused["error"]), (404, "not_found"), (method, path))
        index = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("windowcheck", index)                                          # the page people get does not load it
        status, script = self.send("GET", "/windowcheck.js")
        self.assertEqual(status, 200)                                                   # it is served, for the trial to load
        self.assertIn(b"/api/window-check", script)

    def test_started_for_it_the_page_is_told_and_the_trial_reads_what_the_page_reports(self):
        self.httpd.app.start_window_check()
        self.assertIs(self.send("GET", "/api/state")[1]["window_check"], True)
        self.assertEqual(self.send("GET", "/api/window-check")[1],
                         {"start": False, "report": None, "so_far": None, "opened": [], "shown": []})
        self.assertEqual(self.send("POST", "/api/window-check/start", {}), (200, {"start": True}))
        self.assertIs(self.send("GET", "/api/window-check")[1]["start"], True)
        report = {"agent": "a web view", "steps": [{"name": "一步", "ok": True, "said": "好"}], "errors": [], "facts": {"secure_context": True}}
        # While at work the page says how far it has come. That is not the report, and does not become one.
        begun = dict(report, unfinished=True, steps=[])
        self.assertEqual(self.send("POST", "/api/window-check", begun), (200, {"received": True}))
        check = self.send("GET", "/api/window-check")[1]
        self.assertEqual((check["report"], check["so_far"]), (None, begun))
        self.assertEqual(self.send("POST", "/api/window-check", dict(report, unfinished=True)), (200, {"received": True}))
        self.assertEqual(self.send("GET", "/api/window-check")[1]["so_far"]["steps"], report["steps"])
        for not_true in ("yes", 1, None, False):                                         # only the plain word counts
            self.send("POST", "/api/window-check/start", {})
            self.httpd.app.window_check["report"] = None
            self.send("POST", "/api/window-check", dict(report, unfinished=not_true))
            self.assertEqual(self.send("GET", "/api/window-check")[1]["report"]["unfinished"], not_true)
        self.assertEqual(self.send("POST", "/api/window-check", report), (200, {"received": True}))
        self.assertEqual(self.send("GET", "/api/window-check")[1]["report"], report)
        status, refused = self.send("POST", "/api/window-check", {"steps": ["x" * 1000] * 300})
        self.assertEqual((status, refused["error"]), (413, "too_large"))                # what it reports is bounded
        self.assertEqual(self.send("GET", "/api/window-check")[1]["report"], report)
        self.assertEqual(self.send("PUT", "/api/window-check", {})[0], 405)
        status, refused = self.send("POST", "/api/window-check/start", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, refused["error"]), (403, "bad_origin"))

    def test_during_it_files_are_saved_for_real_and_links_and_folders_are_only_written_down(self):
        self.httpd.app.start_window_check()
        saved = self.send("POST", self.save_address(), {"lang": "en"})[1]
        self.assertTrue(Path(saved["path"]).read_text(encoding="utf-8").startswith("今天去超市啦"))
        self.assertEqual(self.send("POST", "/api/outside/open", {"url": "https://example.com/x"}), (200, {"opened": True}))
        self.assertEqual(self.send("POST", "/api/outside/show", {"path": saved["path"]}), (200, {"shown": True}))
        check = self.send("GET", "/api/window-check")[1]
        self.assertEqual((check["opened"], check["shown"]), (["https://example.com/x"], [saved["path"]]))
        self.assertEqual((self.world.opened, self.world.shown), ([], []))               # the stand-in for the system was never asked
        status, refused = self.send("POST", "/api/outside/open", {"url": "file:///etc/passwd"})
        self.assertEqual((status, refused["error"]), (400, "bad_link"))                 # what is refused stays refused
        self.assertEqual(self.send("GET", "/api/window-check")[1]["opened"], ["https://example.com/x"])

    def test_the_stand_in_keeps_the_real_modules_answers_for_everything_else(self):
        record = {}
        stand_in = outside.Recorded(record)
        self.assertIs(stand_in.real, outside)
        self.assertEqual(stand_in.downloads_folder(), outside.downloads_folder())
        self.assertEqual(stand_in.link_problem("file:///x"), "not_web")
        self.assertEqual(stand_in.save("a.txt", b"1", [self.tmp / "d"]).read_bytes(), b"1")
        with mock.patch.object(outside, "open_link", side_effect=AssertionError("nothing is opened during a trial")), \
                mock.patch.object(outside, "reveal", side_effect=AssertionError("nothing is shown during a trial")):
            self.assertIs(stand_in.open_link("https://example.com/"), True)
            self.assertIs(stand_in.reveal(self.tmp / "d" / "a.txt"), True)
        self.assertEqual(record, {"opened": ["https://example.com/"], "shown": [str(self.tmp / "d" / "a.txt")]})


class StartedForItTest(TempCase):
    """The switch itself: an environment variable given to the app when it is started, read by the core."""

    def start(self, **more):
        env = dict(os.environ, STUDIO_DATA_DIR=str(self.tmp / "data"), PYTHONDONTWRITEBYTECODE="1")
        env.pop(server.WINDOW_CHECK_ENV, None)
        env.update(more)
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-m", "studio.server", "--no-browser", "--port", "0", "--exit-with-parent"],
                                   cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(process.stdout.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        line = process.stdout.readline().decode("utf-8", "replace")
        self.assertIn("http://127.0.0.1:", line)
        return process, int(line[line.index("http://127.0.0.1:") + len("http://127.0.0.1:"):].split()[0].rstrip("/"))

    def ask(self, port, path):
        token_file = self.tmp / "data" / "server-token"
        headers = {"X-Studio-Token": token_file.read_text(encoding="utf-8").strip()} if token_file.exists() else {}
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            conn.request("GET", path, headers=headers)
            res = conn.getresponse()
            return res.status, json.loads(res.read().decode("utf-8"))
        finally:
            conn.close()

    def test_the_variable_switches_it_on_and_only_the_value_one_does(self):
        self.assertEqual(server.WINDOW_CHECK_ENV, "LEARN_MY_VOICE_WINDOW_CHECK")
        for value, on in (("1", True), (None, False), ("0", False), ("true", False), ("", False)):
            process, port = self.start(**({server.WINDOW_CHECK_ENV: value} if value is not None else {}))
            status, state = self.ask(port, "/api/state")
            self.assertEqual((status, state.get("window_check", False)), (200, on), value)
            self.assertEqual(self.ask(port, "/api/window-check")[0], 200 if on else 404, value)
            process.stdin.close()
            self.assertEqual(process.wait(15), 0)


class PageTest(unittest.TestCase):
    def setUp(self):
        self.sources = {path.name: path.read_text(encoding="utf-8") for path in WEB.glob("*.js")}

    def test_no_page_asks_through_the_browsers_own_boxes(self):
        # In the desktop app's window the browser's confirm() may answer "no" without showing anything, alert() may
        # show nothing, and prompt() may return nothing. The pages ask through askConfirm (app.js) and say through toast.
        for name, source in self.sources.items():
            code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)                         # what a comment says about them is not a use
            for box in ("confirm", "alert", "prompt"):
                self.assertEqual(re.findall(r"(?<![\w.$])%s\s*\(" % box, code), [], "%s uses %s()" % (name, box))
                self.assertEqual(re.findall(r"window\.%s\s*\(" % box, code), [], "%s uses window.%s()" % (name, box))
        asked = sum(len(re.findall(r"await askConfirm\(", source)) for source in self.sources.values())
        self.assertEqual(asked, 9)                                                      # the seven deletion/refresh prompts plus voice history and backup restore
        for name in ("app.js", "pages.js", "setup.js", "backup.js", "planner.js"):      # every deletion/overwrite starts with the focus on Cancel
            for call in re.findall(r"await askConfirm\((.*?)\)\)\)", self.sources[name]):
                self.assertIn("danger: true", call, name)

    def test_an_export_is_a_download_in_a_browser_and_a_save_by_the_core_in_the_desktop_app(self):
        app = self.sources["app.js"]
        self.assertEqual(app.count("location.href ="), 1)                               # the one place a page leaves for a file
        export = app[app.index("async function exportScript"):app.index("async function openOutside")]
        self.assertIn("if (!appState.desktop) { location.href =", export)               # only outside the desktop app
        self.assertIn("api('POST', path + '/save', { lang })", export)
        self.assertIn("exportScript(s.id, a.dataset.export)", app)
        self.assertIn("'/api/outside/show'", export)
        links = app[app.index("async function openOutside"):app.index("/* Must match studio/textstats.py")]
        self.assertIn("if (!appState.desktop || e.defaultPrevented || e.button) return;", links)       # a browser keeps its links
        self.assertIn("url.origin === location.origin", links)                          # the app's own pages are not sent out
        self.assertIn("'/api/outside/open'", links)

    def test_every_new_text_is_there_in_both_languages(self):
        app = self.sources["app.js"]
        # The English texts end where the list of texts ends: the first "};" after they begin (other lists stand before them).
        chinese, english = app[app.index("  zh: {"):app.index("  en: {")], app[app.index("  en: {"):app.index("\n};", app.index("  en: {"))]
        self.assertGreater(len(chinese), 2000)
        self.assertGreater(len(english), 2000)
        for key in ("ask_ok", "ask_cancel", "exp_saved", "exp_saved_else", "exp_show", "link_not_opened", "link_copy"):
            self.assertRegex(chinese, r"\b%s: '[^']*[\u4e00-\u9fff]" % key, key)
            self.assertRegex(english, r"\b%s: '[A-Z][^'\u4e00-\u9fff]*'" % key, key)
        self.assertIn("{name}", re.search(r"exp_saved: '([^']*)'", chinese).group(1))
        self.assertIn("{path}", re.search(r"exp_saved_else: '([^']*)'", english).group(1))

    def test_the_window_check_goes_through_the_buttons_a_person_uses(self):
        check = self.sources["windowcheck.js"]
        for used in ("getElementById('export-btn').click()", "[data-export=\"voiceover\"]", "getElementById('delete-btn')", "getElementById('ask-yes')",
                     "a.click()", "$toast.querySelector('button')"):
            self.assertIn(used, check, used)
        app = self.sources["app.js"]
        for there in ('id="ask-yes"', 'id="ask-no"', 'id="ask-text"', "let lastExport = null;", "check.src = '/windowcheck.js';"):
            self.assertIn(there, app, there)
        self.assertIn("if (appState.window_check && !router.checking)", app)            # loaded only when the core asks, and once
        self.assertEqual(app.count("windowcheck.js"), 2)                                # the comment and the one place it is loaded


if __name__ == "__main__":
    unittest.main()
