"""Tests for looking for a newer release (studio/updater.py), and for what a trial of a package or an installer starts.

Each class covers one thing the creator, or whoever runs a trial, can rely on:

- nothing is asked of GitHub unless the app was started for a person: a server a test makes, a trial of a
  package and a trial of an installer never reach out, whatever is requested of them and also when a stand-in
  for GitHub was handed in; the answer says that looking is off, and the page then offers no look and says why;
- only ``server.main`` turns looking on, and not when the app was started with LEARN_MY_VOICE_NO_UPDATE_CHECK=1
  or for a check of the desktop app's window;
- both trials hand that variable to every app they start, and the desktop shell hands its environment on.

No request leaves this computer in any of these tests. GitHub is stood in for by a function made here; where a
test has to show that nothing was asked, that function writes down that it was called, and the test fails.
"""
import contextlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import buildinfo, outside, server, updater
from studio.store import ROOT, Store
from tests import browser

WEB = Path(server.WEB_DIR)
REPO = "LetsLearntocodeforfun/Learn-My-Voice"


def load(folder, name):
    """A script of this repository that is not part of a package (``tools/``, ``desktop/``), loaded by its path."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / folder / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try_release = load("tools", "try_release")
try_installer = load("desktop", "try_installer")

_NO_ICON = re.compile(r"browser: Failed to load resource: the server responded with a status of 404 \(Not Found\) "
                      r"http://127\.0\.0\.1:[0-9]+/favicon\.ico")


def reported(page):
    """What the page reported as wrong, without the one thing that is there for every page of the app.

    Chrome asks for ``/favicon.ico`` by itself. The app has no such file and answers 404, and ``tests/browser.py``
    keeps that as a problem: measured on 21 November 2026 on a fresh store, before any page was opened. It says
    nothing about the page under test, so exactly that entry is left out here. Anything else still counts.
    """
    return [problem for problem in page.problems if not _NO_ICON.fullmatch(problem)]


def found(version="9.0.0"):
    """What a look that found ``version`` keeps in the settings: a release with a package and its checksum."""
    name = "learn-my-voice-%s.zip" % version
    address = "https://github.com/%s/releases/download/v%s/%s" % (REPO, version, name)
    return {"version": version, "tag": "v" + version, "prerelease": False, "published_at": "2026-11-21T00:00:00Z",
            "html_url": "https://github.com/%s/releases/tag/v%s" % (REPO, version), "notes": "",
            "asset": {"name": name, "url": address, "size": 10},
            "checksum_asset": {"name": name + ".sha256", "url": address + ".sha256", "size": 90}}


class World:
    """Stands in for studio.outside in the server: a Downloads folder of its own, and nothing is opened."""

    def __init__(self, downloads):
        self.downloads = downloads
        self.opened, self.shown = [], []

    def downloads_folder(self):
        return self.downloads

    def save(self, name, data, folders):
        return outside.save(name, data, folders)

    def link_problem(self, url):
        return outside.link_problem(url)

    def open_link(self, url):
        self.opened.append(url)
        return True

    def reveal(self, path):
        self.shown.append(str(path))
        return True


class Case(unittest.TestCase):
    """A folder the app is in, with its data folder inside, and nothing beside it but what a test puts there."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-updater-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "learn-my-voice-0.2.0"
        self.store = Store(self.home / "data")
        self.asked = []

    def never(self, method, url, headers):
        """Stands in for GitHub where nothing may be asked: it writes down that it was."""
        self.asked.append(url)
        raise AssertionError("GitHub was asked for %s" % url)

    def packaged(self, version="0.2.0"):
        """The app runs as a release package of ``version`` for as long as the test lasts."""
        path = self.tmp / ("build-%s.json" % version)
        path.write_text(json.dumps({"version": version, "commit": "a" * 40, "built_on": "2026-11-21"}), encoding="utf-8")
        patch = mock.patch.object(buildinfo, "BUILD_FILE", path)
        patch.start()
        self.addCleanup(patch.stop)

    def keep_found(self, version="9.0.0"):
        """As if a look, less than an hour ago, had found ``version``."""
        self.store.update_config(lambda config: config.update(
            updates={"auto_check": True, "last_checked_at": time.time() - 60, "cached": found(version)}))


class ServerCase(Case):
    def setUp(self):
        super().setUp()
        self.world = World(self.tmp / "Downloads")
        self.httpd = server.make_server(0, self.store, None, None, self.world)
        self.app = self.httpd.app
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None):
        """One request; returns ``(status, the answer)``."""
        head, body = {}, None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, json.loads(raw.decode("utf-8"))

    def said(self, answer):
        """A refusal has a code and a sentence in each language, and is never the last-resort answer."""
        self.assertIn("error", answer)
        self.assertNotEqual(answer["error"], "internal")
        self.assertRegex(answer.get("message_zh", ""), "[\u4e00-\u9fff]")
        self.assertRegex(answer["message"], "[A-Za-z]{4}")
        return answer["error"]


# ---------------------------------------------------------------------------------------------- 1. nothing is asked unless a person started the app
class NothingLeavesTest(ServerCase):
    """U1 of the roadmap: opening a page asked api.github.com for releases, also from the tests that drive a browser."""

    EVERY_REQUEST = [
        ("GET", "/api/update", None),
        ("GET", "/api/update?check=1", None),
        ("GET", "/api/update?force=1", None),
        ("GET", "/api/update?check=1&force=1", None),
        ("POST", "/api/update/settings", {}),
        ("POST", "/api/update/settings", {"auto_check": True}),
        ("POST", "/api/update/settings", {"include_prereleases": True}),
        ("POST", "/api/update/settings", {"dismissed_version": "8.0.0"}),
        ("POST", "/api/update/download", {}),
        ("POST", "/api/update/migrate", {"source_path": "/nowhere/learn-my-voice-0.1.0"}),
        ("POST", "/api/update/apply-zip", {"zip_path": "/nowhere/learn-my-voice-9.0.0.zip"}),
        ("GET", "/api/update/nothing-here", None),
        ("POST", "/api/update/nothing-here", {}),
    ]

    def test_a_server_a_test_makes_asks_nothing_whatever_is_requested_also_with_a_stand_in_handed_in(self):
        # Everything that would make the app ask is in place: it runs as a package, a newer release with a file and
        # a checksum is known, the creator wants to be told at the start, and a stand-in for GitHub was handed in.
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        self.app.update_transport = self.never
        self.assertIs(self.app.update_checks, False)                            # what make_server leaves it as
        for method, path, payload in self.EVERY_REQUEST:
            status, answer = self.send(method, path, payload)
            self.assertNotEqual(status, 500, (method, path, answer))
            self.assertEqual(self.asked, [], (method, path))
            if (method, path.split("?")[0]) in (("GET", "/api/update"), ("POST", "/api/update/settings")):
                self.assertEqual((status, answer["checks"]), (200, {"on": False}), path)
                # Known to be newer, and still not put in front of the creator: what is stored may be old.
                self.assertEqual((answer["update_available"], answer["prompt_on_startup"]), (True, False), path)
        status, answer = self.send("POST", "/api/update/download", {})
        self.assertEqual((status, self.said(answer)), (409, "updates_off"))
        self.assertIn("LEARN_MY_VOICE_NO_UPDATE_CHECK=1", answer["message"])
        self.assertIn("LEARN_MY_VOICE_NO_UPDATE_CHECK=1", answer["message_zh"])
        self.assertEqual(list(self.world.downloads.iterdir()) if self.world.downloads.exists() else [], [])
        self.assertEqual(self.asked, [])

    def test_the_same_requests_do_ask_once_looking_is_on(self):
        """Without this the test above would also pass if the stand-in were never reached at all."""
        self.packaged("0.2.0")
        asked = []

        def github(method, url, headers):
            asked.append(url)
            return b"[]"

        self.app.update_transport = github
        self.app.update_checks = True
        status, answer = self.send("GET", "/api/update?check=1")
        self.assertEqual((status, answer["checks"]), (200, {"on": True}))
        self.assertEqual(asked, ["https://api.github.com/repos/%s/releases?per_page=15" % REPO])
        self.assertEqual(self.send("GET", "/api/update")[1]["checks"], {"on": True})
        self.assertEqual(len(asked), 1)                                         # the plain state asks nothing, on or off

    def test_the_module_itself_asks_nothing_while_looking_is_off(self):
        """The switch is in ``updater``, not only in the routes: no caller can forget it."""
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        for force in (False, True):
            state = updater.check_for_updates(self.store, checks_on=False, force=force, transport=self.never)
            self.assertEqual(state["checks"], {"on": False})
        self.assertEqual(updater.check_for_updates(self.store, force=True, transport=self.never)["checks"], {"on": False})   # off unless said
        self.assertEqual(updater.status_payload(self.store)["checks"], {"on": False})
        self.assertEqual(updater.save_update_settings(self.store, {"auto_check": False})["checks"], {"on": False})
        for arguments in ({"checks_on": False}, {}):
            with self.assertRaises(updater.UpdateError) as refused:
                updater.download_release_asset(self.store, self.world, transport=self.never, **arguments)
            self.assertEqual((refused.exception.code, refused.exception.status), ("updates_off", 409))
            self.assertRegex(refused.exception.message_zh, "[\u4e00-\u9fff]")
        self.assertEqual(self.asked, [])


class SwitchTest(Case):
    """Only ``server.main`` turns looking on, and only for a run a person started."""

    def started(self, **variables):
        """What ``main`` leaves the switch as, when the app is started with these variables set and no others of ours."""
        seen = {}

        def serve(httpd, *args, **kwargs):                  # in place of serving: look, and return as if the app was closed
            seen["checks"] = httpd.app.update_checks
            seen["window_check"] = httpd.app.window_check is not None

        environment = dict(variables, STUDIO_DATA_DIR=str(self.tmp / ("started-%d" % len(os.listdir(str(self.tmp)))) / "data"))
        with mock.patch.dict(os.environ, environment), mock.patch.object(server.StudioServer, "serve_forever", serve), \
                contextlib.redirect_stdout(io.StringIO()):
            for name in (server.NO_UPDATE_CHECK_ENV, server.WINDOW_CHECK_ENV):
                if name not in variables:
                    os.environ.pop(name, None)
            self.assertEqual(server.main(["--port", "0", "--no-browser"]), 0)
        return seen

    def test_started_by_a_person_the_app_may_look(self):
        self.assertEqual(self.started(), {"checks": True, "window_check": False})

    def test_started_with_the_variable_it_may_not(self):
        self.assertEqual(server.NO_UPDATE_CHECK_ENV, "LEARN_MY_VOICE_NO_UPDATE_CHECK")
        self.assertEqual(self.started(LEARN_MY_VOICE_NO_UPDATE_CHECK="1")["checks"], False)
        for other in ("0", "", "yes", "true"):                                  # only 1 switches it off, as for the window check
            self.assertEqual(self.started(LEARN_MY_VOICE_NO_UPDATE_CHECK=other)["checks"], True, other)

    def test_started_for_a_check_of_the_window_it_may_not(self):
        self.assertEqual(self.started(LEARN_MY_VOICE_WINDOW_CHECK="1"), {"checks": False, "window_check": True})

    def test_a_server_made_any_other_way_may_not(self):
        httpd = server.make_server(0, self.store)
        self.addCleanup(httpd.server_close)
        self.assertIs(httpd.app.update_checks, False)
        self.assertIsNone(httpd.app.update_transport)
        source = (ROOT / "studio" / "server.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\.update_checks = ", source)), 2)     # where it is made (off), and in main


# ---------------------------------------------------------------------------------------------- 2. the trials
class Ended:
    """Stands in for a started app that ended at once."""
    pid, returncode = 4242, 1

    def poll(self):
        return 1

    def wait(self, timeout=None):
        return 1

    def kill(self):
        pass


class TrialsTest(ServerCase):
    def test_the_three_places_name_the_same_variable(self):
        self.assertEqual({server.NO_UPDATE_CHECK_ENV, try_release.NO_UPDATE_CHECK_ENV, try_installer.NO_UPDATE_CHECK_ENV},
                         {"LEARN_MY_VOICE_NO_UPDATE_CHECK"})

    def test_the_trial_of_a_package_hands_it_to_every_app_it_starts(self):
        for given in ({}, {"LEARN_MY_VOICE_NO_UPDATE_CHECK": "0"}, {"LEARN_MY_VOICE_NO_UPDATE_CHECK": "1"}):
            started = []

            def start(command, **more):
                started.append((command, more["env"]))
                return Ended()

            app = self.tmp / "unpacked" / "learn-my-voice-0.2.0"
            app.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(os.environ, given), mock.patch.object(try_release.subprocess, "Popen", start):
                if not given:
                    os.environ.pop("LEARN_MY_VOICE_NO_UPDATE_CHECK", None)
                with self.assertRaises(try_release.Failed) as stopped:
                    try_release.run_once(app, ["sh", "start-learn-my-voice.sh"], "0.2.0", "the launcher", [], chinese=True)
            self.assertIn("ended at once", str(stopped.exception))
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0][1]["LEARN_MY_VOICE_NO_UPDATE_CHECK"], "1", given)
            self.assertNotIn("LEARN_MY_VOICE_WINDOW_CHECK", started[0][1])
        source = (ROOT / "tools" / "try_release.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("subprocess.Popen("), 1)                  # the one place an app is started, which is run_once

    def test_the_trial_of_an_installer_hands_it_to_both_starts_of_the_app(self):
        started = []
        with mock.patch.object(try_installer.subprocess, "Popen", lambda command, **more: started.append(more["env"])), \
                mock.patch.dict(os.environ, {"LEARN_MY_VOICE_NO_UPDATE_CHECK": "0"}):
            try_installer.start(self.tmp / "app", self.tmp / "data", self.tmp / "log.txt", window_check=True)
            try_installer.start(self.tmp / "app", None, self.tmp / "log.txt")       # "the way a person starts it"
        self.assertEqual([env["LEARN_MY_VOICE_NO_UPDATE_CHECK"] for env in started], ["1", "1"])
        source = (ROOT / "desktop" / "try_installer.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("subprocess.Popen("), 1)                  # the one place the app is started, which is start

    def test_the_desktop_shell_hands_its_environment_on_to_the_core(self):
        """Read off the shell's source, which is not changed here: it adds one variable and takes none away."""
        rust = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotIn("env_clear", rust)
        self.assertNotIn("env_remove", rust)
        self.assertEqual(re.findall(r"\.envs?\(([^)]*)\)", rust), ['"STUDIO_DATA_DIR", data'])
        self.assertEqual(rust.count("Command::new("), 1)                        # one program is started: the core

    def test_the_trial_asks_the_app_whether_it_knows_and_fails_when_it_would_look(self):
        """``use`` is what both trials do with a started app. Here it runs against a server made in this test."""
        self.packaged("0.2.0-beta.rc.99")
        base = "http://127.0.0.1:%d" % self.port
        self.app.update_transport = self.never
        checks = []
        try_release.use(base, "0.2.0-beta.rc.99", checks)
        self.assertIn("started by a trial, it does not look for updates and says so", checks)
        self.assertEqual(self.asked, [])
        # An app that would look is a failed trial. And the trial finds that out without asking for a look itself:
        # were the variable lost on the way to a real app, the trial must not be what makes it ask GitHub.
        self.app.update_checks = True
        with self.assertRaises(try_release.Failed) as stopped:
            try_release.use(base, "0.2.0-beta.rc.99", [])
        self.assertIn("it does not look for updates", str(stopped.exception))
        self.assertEqual(self.asked, [])
        for name, folder in (("try_release", "tools"), ("try_installer", "desktop")):
            source = (ROOT / folder / (name + ".py")).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r'"/api/update[^"]*"', source), ['"/api/update"'], name)


# ---------------------------------------------------------------------------------------------- 3. the page
@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class PageOffTest(Case):
    def test_with_looking_off_the_page_offers_no_look_and_no_download_and_says_why(self):
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        texts = {"zh": "这次运行没有开启检查更新", "en": "Looking for updates is switched off for this run"}
        # ``open_app`` makes its server itself, so the stand-in is put where the app would otherwise reach out from.
        with mock.patch.object(updater, "_default_transport", self.never):
            for lang, begins in texts.items():
                with browser.open_app(self.store, lang=lang) as page:
                    self.assertIs(page.httpd.app.update_checks, False)
                    page.go("#/", "true")
                    self.assertFalse(page.eval_js("Boolean(document.getElementById('up-startup-banner'))"), lang)
                    page.go("#/settings", "Boolean(document.getElementById('up-settings-card'))")
                    said = page.eval_js("document.getElementById('up-off').textContent")
                    self.assertTrue(said.startswith(begins), said)
                    self.assertIn("LEARN_MY_VOICE_NO_UPDATE_CHECK=1", said)
                    for gone in ("up-force-btn", "up-auto-chk", "up-pre-chk", "up-dl-now"):
                        self.assertFalse(page.eval_js("Boolean(document.getElementById(%s))" % json.dumps(gone)), (lang, gone))
                    self.assertEqual(reported(page), [], lang)
        self.assertEqual(self.asked, [])

    def test_the_sentence_is_there_in_both_languages(self):
        script = (WEB / "updater.js").read_text(encoding="utf-8")
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        zh = dict(re.findall(r"\b(up_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(up_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertEqual(sorted(zh), sorted(en))
        self.assertRegex(zh["up_off"], "[\u4e00-\u9fff]")
        self.assertNotRegex(en["up_off"], "[\u4e00-\u9fff]")


if __name__ == "__main__":
    unittest.main()
