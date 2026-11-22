"""Tests for looking for a newer release, checking a download, and moving portable data safely (studio/updater.py).

Each class covers one thing the creator, or whoever runs a trial, can rely on:

- nothing is asked of GitHub unless a person started the app (and left startup checks on): a server a test makes,
  a trial of a package and a trial of an installer never reach out, whatever is requested of them; the answer says
  looking is off, and the page then offers no look and says why;
- version ordering agrees with ``tools/build_release.py`` and ``studio/buildinfo.py``, and hostile GitHub JSON,
  release links, asset lists and redirects are refused or cleaned;
- only an exact asset name with a published ``.sha256`` sidecar is ever downloaded, streamed chunk by chunk to a
  ``.part`` file in Bounded memory and renamed into place only when the SHA-256 matches;
- neighbouring portable folders are scanned only when the current data folder is empty or when explicitly asked,
  migration fills only an empty folder (otherwise pointing to Backup and restore in Settings), refuses while a job
  runs, rebuilds ``studio.db`` through ``studio.backup``, advances ``X-Data-Epoch`` against pages left open before,
  and never copies ``local_tools/whisper``;
- unpacking a release ``.zip`` works in a sibling temporary directory under strict entry, size and path bounds and
  refuses when the destination folder already exists;
- every connection on the database in the unpacked folder has been closed, not merely dropped, when the folder is
  renamed into place (Windows refuses the rename otherwise), and a rename that is refused is tried again only
  where and as long as ``updater._PLACE_TRIES`` says, then refused in two languages;
- the page works in Chinese and English, in light and dark appearances, with zero browser console or 404 problems.

No request leaves this computer in any of these tests.
"""
import contextlib
import errno
import hashlib
import http.client
import importlib.util
import io
import itertools
import json
import os
import re
import shutil
import socket
import sqlite3
import stat
import tempfile
import threading
import time
import tracemalloc
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from studio import backup, buildinfo, creator, mcp_server, outside, planner, server, updater
from studio.store import ROOT, Store
from tests import browser

WEB = Path(server.WEB_DIR)
REPO = "LetsLearntocodeforfun/Learn-My-Voice"
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
ASSUMES = re.compile(r"\b(she|her|hers|herself|husband|wife|couple)\b", re.I)


def load(folder, name):
    """A script of this repository that is not part of a package (``tools/``, ``desktop/``), loaded by its path."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / folder / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_release = load("tools", "build_release")
try_release = load("tools", "try_release")
try_installer = load("desktop", "try_installer")


def traced_peak(call):
    """``(what call() returned or raised, peak traced memory bytes added while it ran)``."""
    was_tracing = tracemalloc.is_tracing()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        try:
            outcome = call()
        except Exception as exc:
            outcome = exc
        return outcome, tracemalloc.get_traced_memory()[1] - before
    finally:
        if not was_tracing:
            tracemalloc.stop()


@contextlib.contextmanager
def loopback_only():
    """Block any socket connection or DNS lookup outside 127.0.0.1 / localhost."""
    blocked = []
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def is_local(host):
        host = str(host)
        return host in ("localhost", "::1") or host.startswith("127.")

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if not is_local(host):
            blocked.append(("connect", address))
            raise OSError("test: external network blocked")
        return real_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if host is not None and not is_local(host):
            blocked.append(("lookup", host))
            raise socket.gaierror("test: external DNS blocked")
        return real_lookup(host, *args, **kwargs)

    with mock.patch.object(socket.socket, "connect", connect), mock.patch.object(socket, "getaddrinfo", lookup):
        yield blocked


def database_path(database):
    """Where a database handed to ``sqlite3.connect`` is, also when it is named the ``file:`` way."""
    name = os.fsdecode(os.fspath(database))
    if name.startswith("file:"):
        name = urllib.request.url2pathname(urllib.parse.urlsplit(name).path)
    return os.path.normcase(os.path.realpath(name))


@contextlib.contextmanager
def watched_databases():
    """Every database opened while this lasts, as ``[{"path", "closed"}]``: ``closed`` says ``close()`` was called.

    A connection that is only dropped is closed by Python when it gets round to it: as the call returns before
    Python 3.11, at the next collection of cycles since. Until then the file is open, and Windows refuses to
    rename the folder it is in. So "dropped" is not "closed", and only ``close()`` counts here, on every system
    and every Python. Nothing here keeps a connection alive: the connection holds its note, not the note the
    connection.
    """
    notes = []
    real_connect = sqlite3.connect

    class Watched(sqlite3.Connection):
        def close(self):
            self.note["closed"] = True
            super().close()

    def connect(database, *args, **more):
        more.setdefault("factory", Watched)
        db = real_connect(database, *args, **more)
        if isinstance(db, Watched):
            db.note = {"path": database_path(database), "closed": False}
            notes.append(db.note)
        return db

    with mock.patch.object(sqlite3, "connect", connect):
        yield notes


@contextlib.contextmanager
def at_the_rename(notes):
    """What ``notes`` say about ``data/studio.db`` in an unpacked folder, each time it is to be renamed into place."""
    looks = []
    real_rename = os.rename

    def rename(source, target, *args, **more):
        if ".unpacking-" in os.path.basename(os.fspath(source)):
            unpacked = database_path(os.path.join(os.fspath(source), "data", "studio.db"))
            looks.append([dict(note) for note in notes if note["path"] == unpacked])
        return real_rename(source, target, *args, **more)

    with mock.patch.object(os, "rename", rename):
        yield looks


def found(version="9.0.0", *, notes="Faster exports and cleaner checks.", suffix=".zip", with_checksum=True):
    """What a look that found ``version`` keeps in settings: a release with an exact package and optional checksum."""
    name = "learn-my-voice-%s%s" % (version, suffix)
    address = "https://github.com/%s/releases/download/v%s/%s" % (REPO, version, name)
    entry = {
        "version": version,
        "tag": "v" + version,
        "prerelease": updater.is_prerelease_version(version),
        "published_at": "2026-11-21T00:00:00Z",
        "html_url": "https://github.com/%s/releases/tag/v%s" % (REPO, version),
        "notes": notes,
        "asset": {"name": name, "url": address, "size": 10},
        "checksum_asset": {"name": name + ".sha256", "url": address + ".sha256", "size": 90} if with_checksum else None,
    }
    return entry


def make_release_zip_bytes(version="9.0.0", extra_files=None):
    """Build a valid synthetic release ``.zip`` carrying ``studio/build.json`` and launcher modes."""
    top = "learn-my-voice-%s" % version
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        build_payload = json.dumps({"version": version, "commit": "a" * 40, "built_on": "2026-11-21"}).encode("utf-8")
        info_build = zipfile.ZipInfo("%s/studio/build.json" % top, date_time=(2026, 11, 21, 0, 0, 0))
        info_build.external_attr = (0o100644) << 16
        zf.writestr(info_build, build_payload)

        info_sh = zipfile.ZipInfo("%s/start-learn-my-voice.sh" % top, date_time=(2026, 11, 21, 0, 0, 0))
        info_sh.external_attr = (0o100755) << 16
        zf.writestr(info_sh, b"#!/bin/sh\nexit 0\n")

        for rel_path, content in (extra_files or {}).items():
            zi = zipfile.ZipInfo("%s/%s" % (top, rel_path), date_time=(2026, 11, 21, 0, 0, 0))
            zi.external_attr = (0o100644) << 16
            zf.writestr(zi, content if isinstance(content, bytes) else content.encode("utf-8"))
    return buf.getvalue()


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
        self.key_patch = mock.patch.dict(os.environ, {"STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key")})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        os.environ.pop("LEARN_MY_VOICE_NO_SIBLING_SCAN", None)
        self.home = self.tmp / "learn-my-voice-0.2.0"
        self.store = Store(self.home / "data")
        self.asked = []

    def never(self, method, url, headers):
        """Stands in for GitHub where nothing may be asked: it writes down that it was."""
        self.asked.append(url)
        raise AssertionError("GitHub was asked for %s" % url)

    def packaged(self, version="0.2.0", desktop=False):
        """The app runs as a release package of ``version`` for as long as the test lasts."""
        path = self.tmp / ("build-%s.json" % version)
        path.write_text(json.dumps({"version": version, "commit": "a" * 40, "built_on": "2026-11-21"}), encoding="utf-8")
        patch_b = mock.patch.object(buildinfo, "BUILD_FILE", path)
        patch_d = mock.patch.object(buildinfo, "desktop", return_value={"python": "3.14.10"} if desktop else None)
        patch_b.start()
        patch_d.start()
        self.addCleanup(patch_b.stop)
        self.addCleanup(patch_d.stop)

    def keep_found(self, version="9.0.0", **more):
        """As if a look, less than an hour ago, had found ``version``."""
        rel = found(version, **more)
        self.store.update_config(lambda config: config.update(
            updates={"auto_check": True, "include_prereleases": True, "last_checked_at": time.time() - 60, "cached": rel}
        ))


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

    def send_full(self, method, path, payload=None, headers=None):
        """One request; returns ``(status, answer_dict, response_headers_dict)``."""
        head = dict(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
            resp_headers = {k: v for k, v in res.getheaders()}
        finally:
            conn.close()
        return res.status, json.loads(raw.decode("utf-8")), resp_headers

    def send(self, method, path, payload=None, headers=None):
        status, data, _ = self.send_full(method, path, payload=payload, headers=headers)
        return status, data

    def said(self, answer):
        """A refusal has a code and a sentence in each language, and is never the last-resort answer."""
        self.assertIn("error", answer)
        self.assertNotEqual(answer["error"], "internal")
        self.assertRegex(answer.get("message_zh", ""), "[\u4e00-\u9fff]")
        self.assertRegex(answer["message"], "[A-Za-z]{4}")
        return answer["error"]

    def save_zip(self, zip_bytes, name="learn-my-voice-0.3.0-beta.zip"):
        """A package in the Downloads folder, as if the app had just downloaded it."""
        self.world.downloads.mkdir(parents=True, exist_ok=True)
        path = self.world.downloads / name
        path.write_bytes(zip_bytes)
        self.app.remember_saved(path)
        return path


# ---------------------------------------------------------------------------------------------- 1. U1: nothing leaves unless a person started the app
class NothingLeavesTest(ServerCase):
    """U1 of the roadmap: opening a page asked api.github.com for releases, also from the tests that drive a browser."""

    EVERY_REQUEST = [
        ("GET", "/api/update", None),
        ("GET", "/api/update?check=1", None),
        ("GET", "/api/update?force=1", None),
        ("GET", "/api/update?check=1&force=1", None),
        ("GET", "/api/update?scan=1", None),
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
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        self.app.update_transport = self.never
        self.assertIs(self.app.update_checks, False)
        for method, path, payload in self.EVERY_REQUEST:
            status, answer = self.send(method, path, payload)
            self.assertNotEqual(status, 500, (method, path, answer))
            self.assertEqual(self.asked, [], (method, path))
            if (method, path.split("?")[0]) in (("GET", "/api/update"), ("POST", "/api/update/settings")):
                self.assertEqual((status, answer["checks"]), (200, {"on": False}), path)
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
        self.assertEqual(len(asked), 1)

    def test_turning_off_startup_checks_stops_startup_requests_until_forced(self):
        """When the creator switches auto_check off in Settings, startup ?check=1 must not ask GitHub."""
        self.packaged("0.2.0")
        asked = []
        self.app.update_checks = True
        self.app.update_transport = lambda method, url, headers: (asked.append(url), b"[]")[1]
        status, saved = self.send("POST", "/api/update/settings", {"auto_check": False})
        self.assertEqual((status, saved["auto_check"]), (200, False))
        self.assertEqual(asked, [])
        status, startup = self.send("GET", "/api/update?check=1")
        self.assertEqual((status, startup["auto_check"]), (200, False))
        self.assertEqual(asked, [])
        # Explicit button click in Settings (?check=1&force=1) still checks.
        status, forced = self.send("GET", "/api/update?check=1&force=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(asked), 1)

    def test_the_module_itself_asks_nothing_while_looking_is_off(self):
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        for force in (False, True):
            state = updater.check_for_updates(self.store, checks_on=False, force=force, transport=self.never)
            self.assertEqual(state["checks"], {"on": False})
        self.assertEqual(updater.check_for_updates(self.store, force=True, transport=self.never)["checks"], {"on": False})
        self.assertEqual(updater.status_payload(self.store)["checks"], {"on": False})
        self.assertEqual(updater.save_update_settings(self.store, {"auto_check": False})["checks"], {"on": False})
        for arguments in ({"checks_on": False}, {}):
            with self.assertRaises(updater.UpdateError) as refused:
                updater.download_release_asset(self.store, self.world, transport=self.never, **arguments)
            self.assertEqual((refused.exception.code, refused.exception.status), ("updates_off", 409))
            self.assertRegex(refused.exception.message_zh, "[\u4e00-\u9fff]")
        self.assertEqual(self.asked, [])

    def test_no_mcp_tool_checks_downloads_or_migrates_updates(self):
        mcp_src = Path(mcp_server.__file__).read_text(encoding="utf-8")
        self.assertNotIn("updater", mcp_src)
        for name in mcp_server.TOOLS:
            self.assertIsNone(re.search(r"release|migrat|apply_zip", name), name)


class SwitchTest(Case):
    """Only ``server.main`` turns looking on, and only for a run a person started."""

    def started(self, **variables):
        seen = {}

        def serve(httpd, *args, **kwargs):
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
        for other in ("0", "", "yes", "true"):
            self.assertEqual(self.started(LEARN_MY_VOICE_NO_UPDATE_CHECK=other)["checks"], True, other)

    def test_started_for_a_check_of_the_window_it_may_not(self):
        self.assertEqual(self.started(LEARN_MY_VOICE_WINDOW_CHECK="1"), {"checks": False, "window_check": True})

    def test_a_server_made_any_other_way_may_not(self):
        httpd = server.make_server(0, self.store)
        self.addCleanup(httpd.server_close)
        self.assertIs(httpd.app.update_checks, False)
        self.assertIsNone(httpd.app.update_transport)
        source = (ROOT / "studio" / "server.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\.update_checks = ", source)), 2)


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
        self.assertEqual(source.count("subprocess.Popen("), 1)

    def test_the_trial_of_an_installer_hands_it_to_both_starts_of_the_app(self):
        started = []
        with mock.patch.object(try_installer.subprocess, "Popen", lambda command, **more: started.append(more["env"])), \
                mock.patch.dict(os.environ, {"LEARN_MY_VOICE_NO_UPDATE_CHECK": "0"}):
            try_installer.start(self.tmp / "app", self.tmp / "data", self.tmp / "log.txt", window_check=True)
            try_installer.start(self.tmp / "app", None, self.tmp / "log.txt")
        self.assertEqual([env["LEARN_MY_VOICE_NO_UPDATE_CHECK"] for env in started], ["1", "1"])
        source = (ROOT / "desktop" / "try_installer.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("subprocess.Popen("), 1)

    def test_the_desktop_shell_hands_its_environment_on_to_the_core(self):
        rust = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotIn("env_clear", rust)
        self.assertNotIn("env_remove", rust)
        self.assertEqual(re.findall(r"\.envs?\(([^)]*)\)", rust), ['"STUDIO_DATA_DIR", data'])
        self.assertEqual(rust.count("Command::new("), 1)

    def test_the_trial_asks_the_app_whether_it_knows_and_fails_when_it_would_look(self):
        self.packaged("0.2.0-beta.rc.99")
        base = "http://127.0.0.1:%d" % self.port
        self.app.update_transport = self.never
        checks = []
        try_release.use(base, "0.2.0-beta.rc.99", checks)
        self.assertIn("started by a trial, it does not look for updates and says so", checks)
        self.assertEqual(self.asked, [])
        self.app.update_checks = True
        with self.assertRaises(try_release.Failed) as stopped:
            try_release.use(base, "0.2.0-beta.rc.99", [])
        self.assertIn("it does not look for updates", str(stopped.exception))
        self.assertEqual(self.asked, [])
        for name, folder in (("try_release", "tools"), ("try_installer", "desktop")):
            source = (ROOT / folder / (name + ".py")).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r'"/api/update[^"]*"', source), ['"/api/update"'], name)


# ---------------------------------------------------------------------------------------------- 3. versions & hostile GitHub inputs
class VersionAndHardeningTest(ServerCase):
    # What tools/build_release.py makes from the version a changelog heading names.
    @staticmethod
    def builds_of(base):
        """Oldest first: a local build, candidate 1, its second attempt, candidate 2, the release."""
        return [build_release.version_for(base, None), build_release.version_for(base, 1, 1),
                build_release.version_for(base, 1, 2), build_release.version_for(base, 2, 1),
                build_release.version_for(base, None, release=True)]

    def test_versions_come_in_the_order_they_are_made_in(self):
        ordered = [
            "0.2.0-alpha",
            "0.2.0-beta.dev",
            "0.2.0-beta.rc.1",
            "0.2.0-beta.rc.1.2",
            "0.2.0-beta.rc.9",
            "0.2.0-beta",
            "0.2.0-beta.1.dev",
            "0.2.0-beta.1.rc.1",
            "0.2.0-beta.1",
            "0.2.0-beta.2",
            "0.2.0-beta.10",
            "0.2.0-rc.1.dev",           # a pre-release that is itself named rc: the name, then the build marks
            "0.2.0-rc.1.rc.1",
            "0.2.0-rc.1.rc.1.2",
            "0.2.0-rc.1",
            "0.2.0-rc.2",
            "0.2.0.dev",
            "0.2.0.rc.1",
            "0.2.0.rc.1.2",
            "0.2.0",
            "0.3.0-beta.rc.1",
            "0.3.0-beta",
            "0.3.0",
            "1.0.0-RC.2.dev",
            "1.0.0-RC.2.rc.3",
            "1.0.0-RC.2",
            "1.0.0",
        ]
        for i, ver in enumerate(ordered):
            self.assertIsNotNone(buildinfo._VERSION.fullmatch(ver), ver)
            self.assertIsNotNone(updater.parse_version(ver), ver)
            self.assertEqual(updater.parse_version("v" + ver), updater.parse_version(ver))
            self.assertFalse(updater.is_newer(ver, ver))
            for later in ordered[i + 1:]:
                self.assertTrue(updater.is_newer(later, ver), (ver, later))
                self.assertFalse(updater.is_newer(ver, later), (ver, later))
        # The same version written another way is neither newer nor older.
        for one, other in (("1.0.0-RC.2", "1.0.0-rc.2"), ("1.10.3+local", "1.10.3"), ("0.2.0-beta.RC.1", "0.2.0-beta.rc.1"),
                           ("01.2.3", "1.2.3"), (" v0.2.0 ", "0.2.0")):
            self.assertEqual(updater.parse_version(one), updater.parse_version(other), (one, other))
            self.assertFalse(updater.is_newer(one, other) or updater.is_newer(other, one), (one, other))
        # Only a plain X.Y.Z is a release; what is no version is treated as not one.
        self.assertEqual([updater.is_prerelease_version(ver) for ver in
                          ("0.2.0", "0.2.0+local", "0.2.0-beta", "0.2.0.dev", "0.2.0.rc.1", "0.3.0-rc.1", "0.2.0.1", "", None)],
                         [False, False, True, True, True, True, True, True, True])

    def test_every_version_a_changelog_heading_leads_to_is_read_as_the_build_file_reads_it_and_in_order(self):
        """``tools/build_release.py`` makes the versions, ``studio/buildinfo.py`` says which a build file may hold.

        For every heading tried, each of the five builds is a version here exactly when a build file may hold
        it, and where all five are, they come in the order they are made in. Before, a heading such as
        ``## v0.3.0-rc.1`` gave versions the build file held and the updater did not know: a copy with such a
        version was never offered anything newer.
        """
        names = ("alpha", "beta", "rc", "RC", "dev", "x", "0", "1", "2", "10")
        after = [""] + ["-" + ".".join(parts) for count in (1, 2, 3) for parts in itertools.product(names, repeat=count)]
        after += [
            "-rc1", "-Beta.RC.3", "-007", "-a.b.c.d.e", "-a.b.c.d.e.f",        # five parts leave room for .rc.N.M, six do not
            "-a.b.c.d.e.f.g.h", "-a.b.c.d.e.f.g.h.i",                         # eight are a release and nothing else; nine nothing
            "-a..b", "-.a", "-beta.", "-beta-2", "-bet\u00e9", "-" + "a" * 300,
        ]
        tried = held = whole = 0
        for tail in after:
            for core in ("1.2.3", "0.3.0"):
                base = build_release.base_version("# Changes\n\n## v%s%s (not released yet)\n\n## v0.1.0\n" % (core, tail))
                builds = self.builds_of(base)
                keys = [updater.parse_version(version) for version in builds]
                for version, key in zip(builds, keys):
                    tried += 1
                    held += key is not None
                    self.assertEqual(key is not None, buildinfo._VERSION.fullmatch(version) is not None, version)
                if None not in keys:
                    whole += 1
                    for earlier, later in zip(builds, builds[1:]):
                        self.assertTrue(updater.is_newer(later, earlier), (earlier, later))
                        self.assertFalse(updater.is_newer(earlier, later), (earlier, later))
        # The headings tried are of both kinds: most lead to versions, some to none or to a release only.
        self.assertGreater(whole, 2000)
        self.assertGreater(tried - held, 20)
        for base in ("0.3.0-rc.1", "1.0.0-RC.2", "0.3.0-beta.rc.1", "0.3.0-dev", "0.3.0-rc"):
            self.assertNotIn(None, [updater.parse_version(version) for version in self.builds_of(base)], base)

    def test_what_is_no_version_is_none_and_nothing_is_raised(self):
        long_one = "1.2.3-" + "a" * updater.MAX_VERSION_CHARS
        for bad in (None, 5, 1.5, [], b"1.2.3", "", " ", "v", "vv1.2.3", "0.2", "1.2.3.", "1.2.3-", "1.2.3+", "0.2.0+<script>",
                    "0.2.0-a..b", "0.2.0 beta", "0.2.0-b\u00e9ta", "\uff10.\uff12.\uff10", "1.2.3" + ".x" * 9,
                    "9" * 6000 + ".0.0", long_one, "1.2.3\x00", "<script>alert(1)</script>"):
            self.assertIsNone(updater.parse_version(bad), repr(bad)[:60])
            self.assertFalse(updater.is_newer(str(bad), "0.2.0"))
            self.assertFalse(updater.is_newer("9.9.9", str(bad)))
            self.assertTrue(updater.is_prerelease_version(bad if isinstance(bad, str) else None))
            if isinstance(bad, str) and bad not in (long_one, "vv1.2.3") and len(bad) < 100:
                self.assertIsNone(buildinfo._VERSION.fullmatch(bad.strip()), repr(bad))
        # The one thing a build file could hold in theory and this does not take: a version longer than a
        # build file may be (buildinfo.MAX_BYTES), which therefore no build file holds.
        self.assertEqual(updater.MAX_VERSION_CHARS, buildinfo.MAX_BYTES)
        self.assertIsNotNone(buildinfo._VERSION.fullmatch(long_one))
        self.assertIsNotNone(updater.parse_version("1.2.3-" + "a" * (updater.MAX_VERSION_CHARS - 6)))

    def test_a_run_from_the_source_reads_the_heading_as_the_build_script_does(self):
        self.assertEqual((updater._CHANGELOG_HEADING_RE.pattern, updater._CHANGELOG_HEADING_RE.flags),
                         (build_release._VERSION_HEADING.pattern, build_release._VERSION_HEADING.flags))
        for heading in ("## v0.3.0-rc.1 (not released yet)", "## v1.0.0-RC.2", "## v0.4.0 (1 December 2026)",
                        "## v0.3.0-beta-2", "## v0.3.0-bet\u00e9", "## v0.3.0-beta.rc.1"):
            changelog = "# Changes\n\nWhat changed.\n\n%s\n\n- one\n\n## v0.1.0 (earlier)\n" % heading
            (self.tmp / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            with mock.patch.object(updater, "ROOT", self.tmp), \
                    mock.patch.object(buildinfo, "BUILD_FILE", self.tmp / "no-build.json"):
                running = updater.current_version_info()
            base = build_release.base_version(changelog)
            self.assertEqual((running["from_source"], running["base_version"], running["version"]),
                             (True, base, build_release.version_for(base, None)), heading)

    def test_a_copy_whose_version_is_named_rc_is_offered_what_is_newer(self):
        def release(tag, early=True):
            return {"tag_name": tag, "draft": False, "prerelease": early, "assets": []}

        self.packaged("0.3.0-rc.1")
        self.app.update_checks = True
        # Only older ones are out: the best of them is named, and nothing is offered.
        older = [release("v0.3.0-rc.1.rc.4"), release("v0.3.0-beta"), release("v0.2.0", early=False)]
        self.app.update_transport = lambda method, url, headers: json.dumps(older).encode("utf-8")
        status, answer = self.send("GET", "/api/update?check=1&force=1")
        self.assertEqual((status, answer["current"]["version"], answer["include_prereleases"]), (200, "0.3.0-rc.1", True))
        self.assertEqual((answer["latest"]["version"], answer["update_available"]), ("0.3.0-rc.1.rc.4", False))
        # A later pre-release of the same name, and a release, are offered.
        for tags, best in (([release("v0.3.0-rc.2"), release("v0.3.0-rc.1")], "0.3.0-rc.2"),
                           ([release("v1.0.0-RC.2"), release("v0.3.0", early=False)], "1.0.0-RC.2"),
                           ([release("v0.3.0", early=False), release("v0.3.0-rc.1")], "0.3.0")):
            self.app.update_transport = lambda method, url, headers, tags=tags: json.dumps(tags).encode("utf-8")
            status, answer = self.send("GET", "/api/update?check=1&force=1")
            self.assertEqual((status, answer["latest"]["version"], answer["update_available"]), (200, best, True), tags)

    def test_hostile_github_releases_json_and_urls_are_neutralized_or_refused(self):
        self.packaged("0.2.0")
        self.app.update_checks = True

        # Non-list JSON (e.g. GitHub rate-limit object) or deeply nested JSON fails cleanly without 500 or poisoning cache.
        for payload in (b'{"message":"API rate limit exceeded"}', b'"plain"', b"123", b"[" * 4000 + b"]" * 4000, b"{bad"):
            self.app.update_transport = lambda m, u, h, p=payload: p
            st_force, ans_force = self.send("GET", "/api/update?check=1&force=1")
            self.assertEqual((st_force, self.said(ans_force)), (502, "bad_release_json"))
            st_bg, ans_bg = self.send("GET", "/api/update?check=1")
            self.assertEqual(st_bg, 200)
            self.assertIn("check_error", ans_bg)
            self.assertIsNone(ans_bg["latest"])

        # Hostile release fields: drafts ignored, bad html_url rewritten to repo tag, bidi/null stripped from notes,
        # wrong-host or http:// or boolean-size assets rejected.
        hostile_releases = [
            {"tag_name": "v99.0.0", "draft": True, "prerelease": False, "assets": []},
            {"tag_name": 12345, "draft": False, "prerelease": False},
            {"tag_name": "v9.0.0+bad", "draft": False, "prerelease": False},
            {
                "tag_name": "v1.0.0",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/evil-attacker/malware/releases/tag/v1.0.0",
                "body": "Clean line\x00\u202eevil-rtl\r\nSecond line",
                "assets": [
                    {"name": "learn-my-voice-1.0.0.zip", "browser_download_url": "http://github.com/a.zip", "size": 10},
                    {"name": "learn-my-voice-1.0.0.zip", "browser_download_url": "https://evil.example/a.zip", "size": 10},
                    {"name": "learn-my-voice-1.0.0.zip", "browser_download_url": "https://github.com/a.zip", "size": True},
                ],
            },
        ]
        self.app.update_transport = lambda m, u, h: json.dumps(hostile_releases).encode("utf-8")
        st, ans = self.send("GET", "/api/update?check=1&force=1")
        self.assertEqual(st, 200)
        latest = ans["latest"]
        self.assertEqual(latest["version"], "1.0.0")
        self.assertEqual(latest["html_url"], "https://github.com/%s/releases/tag/v1.0.0" % REPO)
        self.assertEqual(latest["notes"], "Clean lineevil-rtl\nSecond line")
        self.assertIsNone(latest["asset"])
        self.assertIsNone(latest["checksum_asset"])

        # Tampered cached object in config.json is discarded on read.
        self.store.update_config(lambda c: c.update(updates={
            "auto_check": True,
            "last_checked_at": time.time(),
            "cached": {"version": "<script>alert(1)</script>", "html_url": "javascript:alert(1)"},
        }))
        st_clean, ans_clean = self.send("GET", "/api/update")
        self.assertEqual(st_clean, 200)
        self.assertIsNone(ans_clean["latest"])

    def test_safe_redirect_and_opener_allow_only_exact_github_hosts_over_https_without_proxies(self):
        redirect = updater._SafeRedirect()
        origin = urllib.request.Request(
            "https://github.com/%s/releases/download/v1.0.0/a.zip" % REPO,
            headers={"Authorization": "Bearer secret", "Cookie": "a=b", "User-Agent": "lmv"},
            method="GET",
        )
        for good in (
            "https://objects.githubusercontent.com/github-production-release-asset/123?sig=abc",
            "https://release-assets.githubusercontent.com/123",
        ):
            hop = redirect.redirect_request(origin, None, 302, "Found", {}, good)
            self.assertEqual(hop.full_url, good)
            lowered = {k.lower() for k, _ in hop.header_items()}
            self.assertNotIn("authorization", lowered)
            self.assertNotIn("cookie", lowered)

        for bad in (
            "http://objects.githubusercontent.com/123",
            "https://evil.example/a.zip",
            "https://objects.githubusercontent.com.evil.example/a.zip",
            "https://sub.objects.githubusercontent.com/a.zip",
            "https://user:pass@objects.githubusercontent.com/a.zip",
            "https://objects.githubusercontent.com:8443/a.zip",
            "file:///etc/passwd",
            "javascript:alert(1)",
        ):
            with self.assertRaises(urllib.error.HTTPError, msg=bad):
                redirect.redirect_request(origin, None, 302, "Found", {}, bad)

        # Direct transport refuses non-HTTPS or disallowed hosts before touching the network, and ignores proxies.
        with loopback_only() as blocked:
            for bad_url in ("http://api.github.com/repos/x/y/releases", "https://evil.example/a.zip"):
                with self.assertRaises(updater.UpdateError) as ctx:
                    updater._default_transport("GET", bad_url, {})
                self.assertEqual(ctx.exception.code, "bad_url")
            self.assertEqual(blocked, [])

            proxy_hits = []
            proxy_srv = http.server.HTTPServer(("127.0.0.1", 0), type(
                "Trap", (http.server.BaseHTTPRequestHandler,),
                {"do_GET": lambda s: proxy_hits.append(s.path), "do_CONNECT": lambda s: proxy_hits.append(s.path)},
            ))
            self.addCleanup(proxy_srv.server_close)
            proxy_url = "http://127.0.0.1:%d" % proxy_srv.server_address[1]
            with mock.patch.dict(os.environ, {"HTTPS_PROXY": proxy_url, "HTTP_PROXY": proxy_url}):
                with self.assertRaises(updater.UpdateError) as net_err:
                    updater._default_transport("GET", "https://api.github.com/repos/%s/releases" % REPO, {})
                self.assertEqual(net_err.exception.code, "update_network_error")
            self.assertEqual(proxy_hits, [])
            self.assertEqual(blocked, [("lookup", "api.github.com")])


# ---------------------------------------------------------------------------------------------- 4. U4: exact asset, required .sha256, streaming download
class DownloadIntegrityTest(ServerCase):
    def test_exact_asset_name_only_no_suffix_fallback_and_platform_targets(self):
        self.assertEqual(updater.current_platform_target(is_desktop=True, sys_name="Darwin", machine="arm64")["suffix"], "-macos-arm64.dmg")
        self.assertEqual(updater.current_platform_target(is_desktop=True, sys_name="Darwin", machine="x86_64")["suffix"], "-macos-x64.dmg")
        self.assertEqual(updater.current_platform_target(is_desktop=True, sys_name="Windows", machine="AMD64")["suffix"], "-windows-x64-setup.exe")
        self.assertEqual(updater.current_platform_target(is_desktop=False, sys_name="Darwin", machine="arm64")["suffix"], ".zip")

        # Suffix matches with wrong version or extra words are NOT selected.
        wrong_assets = [
            {"name": "learn-my-voice-9.0.0-other-macos-arm64.dmg", "browser_download_url": "https://github.com/a.dmg", "size": 50},
            {"name": "learn-my-voice-9.0.0-other.zip", "browser_download_url": "https://github.com/a.zip", "size": 50},
            {"name": "learn-my-voice-8.9.9.zip", "browser_download_url": "https://github.com/b.zip", "size": 50},
        ]
        for desk in (False, True):
            tgt = updater.current_platform_target(is_desktop=desk, sys_name="Darwin", machine="arm64")
            self.assertEqual(updater._select_asset(wrong_assets, "9.0.0", tgt), (None, None))

        # Desktop mode does NOT silently fall back to .zip when the desktop installer is absent.
        only_zip = [
            {"name": "learn-my-voice-9.0.0.zip", "browser_download_url": "https://github.com/z.zip", "size": 50},
            {"name": "learn-my-voice-9.0.0.zip.sha256", "browser_download_url": "https://github.com/z.zip.sha256", "size": 90},
        ]
        desk_tgt = updater.current_platform_target(is_desktop=True, sys_name="Darwin", machine="arm64")
        self.assertEqual(updater._select_asset(only_zip, "9.0.0", desk_tgt), (None, None))

    def test_release_without_published_sha256_or_with_unreadable_sha256_is_never_downloaded(self):
        self.packaged("0.2.0")
        self.app.update_checks = True

        # Case 1: release published no .sha256 sidecar -> asset URL is never called; refusal names the release page.
        self.keep_found("9.0.0", with_checksum=False)
        self.app.update_transport = self.never
        st, ans = self.send("POST", "/api/update/download", {})
        self.assertEqual((st, self.said(ans)), (502, "missing_checksum"))
        self.assertIn("https://github.com/%s/releases/tag/v9.0.0" % REPO, ans["message"])
        self.assertIn("https://github.com/%s/releases/tag/v9.0.0" % REPO, ans["message_zh"])
        self.assertEqual(ans.get("release_url"), "https://github.com/%s/releases/tag/v9.0.0" % REPO)
        self.assertEqual(self.asked, [])

        # Case 2: .sha256 sidecar exists, but is empty, malformed, or names a DIFFERENT file -> asset URL is never called.
        self.keep_found("9.0.0", with_checksum=True)
        for bad_sidecar in (
            b"",
            b"not-a-sha256-digest\n",
            ("a" * 64 + "  learn-my-voice-9.0.0-macos-arm64.dmg\n").encode("utf-8"),
        ):
            requested = []

            def only_sidecar(method, url, headers, raw=bad_sidecar):
                requested.append(url)
                if url.endswith(".sha256"):
                    return raw
                raise AssertionError("Asset must NOT be downloaded when .sha256 is bad: " + url)

            self.app.update_transport = only_sidecar
            st_bad, ans_bad = self.send("POST", "/api/update/download", {})
            self.assertEqual((st_bad, self.said(ans_bad)), (502, "bad_checksum"))
            self.assertEqual(len(requested), 1)
            self.assertTrue(requested[0].endswith(".sha256"))

    def test_streaming_download_hashes_to_part_caps_size_and_renames_only_when_sha256_matches(self):
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        self.app.update_checks = True

        # Stream 3 MB in 64 KB chunks; verify .part exists mid-stream while final file does not, and memory stays small.
        chunk_block = b"LearnMyVoiceStreamChunk!" * (64 * 1024 // 24)
        num_chunks = 48
        expected_hasher = hashlib.sha256()
        for _ in range(num_chunks):
            expected_hasher.update(chunk_block)
        good_sha = expected_hasher.hexdigest()
        saw_mid_stream = {}

        def stream_chunks():
            for idx in range(num_chunks):
                if idx == 10 and self.world.downloads.is_dir():
                    names = [p.name for p in self.world.downloads.iterdir()]
                    saw_mid_stream["parts"] = [n for n in names if n.endswith(".part")]
                    saw_mid_stream["finals"] = [n for n in names if not n.endswith(".part")]
                yield chunk_block

        checksum_holder = {"sha": good_sha}

        def transport(method, url, headers):
            if url.endswith(".sha256"):
                return ("%s  learn-my-voice-9.0.0.zip\n" % checksum_holder["sha"]).encode("utf-8")
            if url.endswith(".zip"):
                return stream_chunks()
            raise AssertionError("Unexpected URL: " + url)

        self.app.update_transport = transport
        outcome, peak = traced_peak(lambda: self.send("POST", "/api/update/download", {}))
        st, res = outcome
        self.assertEqual(st, 201, res)
        self.assertLess(peak, 2 * 1024 * 1024)      # 3 MB download streamed in < 2 MB peak traced memory
        self.assertEqual(len(saw_mid_stream.get("parts", [])), 1)
        self.assertEqual(saw_mid_stream.get("finals"), [])
        saved = Path(res["saved"]["path"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.stat().st_size, len(chunk_block) * num_chunks)
        self.assertEqual([p.name for p in self.world.downloads.iterdir()], ["learn-my-voice-9.0.0.zip"])
        self.assertEqual(res["sha256"], good_sha)
        self.assertIn("not that the release is signed", res["integrity_note"])
        self.assertIn("未做代码签名", res["integrity_note_zh"])

        # Downloading again creates "learn-my-voice-9.0.0 (2).zip" without overwriting the first file.
        saved.write_bytes(b"keep-existing")
        st2, res2 = self.send("POST", "/api/update/download", {})
        self.assertEqual(st2, 201)
        self.assertEqual(res2["saved"]["name"], "learn-my-voice-9.0.0 (2).zip")
        self.assertEqual(saved.read_bytes(), b"keep-existing")

        # Checksum mismatch discards .part and writes no new file.
        before_files = sorted(p.name for p in self.world.downloads.iterdir())
        checksum_holder["sha"] = "0" * 64
        st_mis, ans_mis = self.send("POST", "/api/update/download", {})
        self.assertEqual((st_mis, self.said(ans_mis)), (502, "checksum_mismatch"))
        self.assertEqual(sorted(p.name for p in self.world.downloads.iterdir()), before_files)

        # Oversized stream stops as soon as MAX_ASSET_BYTES is exceeded, discarding .part.
        checksum_holder["sha"] = good_sha
        yielded = {"count": 0}

        def endless():
            for _ in range(200):
                yielded["count"] += 1
                yield chunk_block

        self.app.update_transport = lambda m, u, h: (
            ("%s  learn-my-voice-9.0.0.zip\n" % good_sha).encode("utf-8") if u.endswith(".sha256") else endless()
        )
        with mock.patch.object(updater, "MAX_ASSET_BYTES", 256 * 1024):
            st_big, ans_big = self.send("POST", "/api/update/download", {})
        self.assertEqual((st_big, self.said(ans_big)), (502, "too_large"))
        self.assertLess(yielded["count"], 20)
        self.assertEqual(sorted(p.name for p in self.world.downloads.iterdir()), before_files)


# ---------------------------------------------------------------------------------------------- 5. U2, U5, U6: sibling scan & safe empty-only migration
class SiblingScanAndMigrationTest(ServerCase):
    def make_sibling(self, version="0.1.9", *, with_whisper=True):
        sib = self.tmp / ("learn-my-voice-%s" % version)
        sib_store = Store(sib / "data")
        sib_store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5", trend_weight="heat"),
            c["keys"].update(anthropic="sk-ant-migrated-secret"),
        ))
        sib_store.keep_for_page({"lang": "en", "theme": "dark", "accent": "green"})
        sib_store.save_creator({"name": "小林", "pronoun": "they"})
        sib_store.save_dna(dict(EXAMPLE_DNA, persona_summary=dict(EXAMPLE_DNA["persona_summary"], one_line_essence_en="Earlier A")))
        sib_store.save_dna(dict(EXAMPLE_DNA, persona_summary=dict(EXAMPLE_DNA["persona_summary"], one_line_essence_en="Current B")))
        sib_store.create_script(title="旧版脚本", lines=[{"spoken_script_zh": "这周去超市买菜啦 / 拿了两盒水饺"}])
        sib_store.create_sample(title="旧版素材", text="周末在家煮咖啡嘛 / 顺便收拾一下厨房啦")
        sib_store.upsert_trends([{"keyword": "秋冬护肤", "heat": 12000}], source="manual")
        planner.create_plan_item(sib_store, {"title": "下周拍早餐", "scheduled_for": "2026-11-28"})
        if with_whisper:
            fake_venv = sib / "local_tools" / "whisper" / "venv" / "bin"
            fake_venv.mkdir(parents=True, exist_ok=True)
            (fake_venv / "python3").write_bytes(b"fake-2gb-whisper-runtime")
        return sib, sib_store

    def test_routine_update_status_and_checks_scan_siblings_only_when_current_folder_is_empty(self):
        self.packaged("0.2.0")
        self.make_sibling("0.1.9")

        # Empty portable folder -> scanned so a newly unpacked .zip offers migration.
        st0, ans0 = self.send("GET", "/api/update")
        self.assertEqual((st0, ans0["data_empty"], ans0["previous_scanned"]), (200, True, True))
        self.assertEqual([p["folder_name"] for p in ans0["previous_versions"]], ["learn-my-voice-0.1.9"])

        # Desktop mode -> never scans sibling portable folders on routine status.
        self.packaged("0.2.0", desktop=True)
        st_desk, ans_desk = self.send("GET", "/api/update")
        self.assertEqual((st_desk, ans_desk["previous_scanned"], ans_desk["previous_versions"]), (200, False, []))

        # Non-empty portable folder -> routine GET /api/update and ?check=1 never touch neighbouring databases.
        self.packaged("0.2.0", desktop=False)
        self.store.create_script(title="已有脚本")
        self.app.update_checks = True
        self.app.update_transport = lambda m, u, h: b"[]"
        with mock.patch.object(updater, "_safe_count_tables", wraps=updater._safe_count_tables) as spy:
            for path in ("/api/update", "/api/update?check=1", "/api/update?check=1&force=1"):
                st, ans = self.send("GET", path)
                self.assertEqual((st, ans["data_empty"], ans["previous_scanned"], ans["previous_versions"]), (200, False, False, []), path)
            self.assertEqual(spy.call_count, 0)
            # Explicit ?scan=1 scans on demand and still reports data_empty=False.
            st_scan, ans_scan = self.send("GET", "/api/update?scan=1")
            self.assertEqual((st_scan, ans_scan["data_empty"], ans_scan["previous_scanned"]), (200, False, True))
            self.assertEqual(len(ans_scan["previous_versions"]), 1)
            self.assertEqual(spy.call_count, 1)

    def test_migrating_into_empty_folder_carries_data_keys_older_schema_and_never_copies_whisper(self):
        self.packaged("0.2.0")
        sib, _ = self.make_sibling("0.1.9", with_whisper=True)

        st, res = self.send("POST", "/api/update/migrate", {"source_path": str(sib)})
        self.assertEqual(st, 200, res)
        self.assertEqual((res["scripts"], res["samples"], res["whisper_copied"], res["had_whisper"]), (1, 1, False, True))
        self.assertIn("local_tools/whisper", res["not_copied"])
        self.assertIn("not copied", res["transcription_note"])
        self.assertIn("不会在压缩包版本之间复制", res["transcription_note_zh"])
        self.assertFalse((self.home / "local_tools" / "whisper").exists())

        cfg = self.store.load_config()
        self.assertEqual((cfg["provider"], cfg["model"], cfg["keys"]["anthropic"], cfg["trend_weight"]),
                         ("anthropic", "claude-sonnet-4-5", "sk-ant-migrated-secret", "heat"))
        self.assertEqual(self.store.load_page_keeps(), {"lang": "en", "theme": "dark", "accent": "green"})
        self.assertEqual(self.store.load_creator()["name"], "小林")
        dna, is_ex = self.store.load_dna()
        self.assertFalse(is_ex)
        self.assertEqual(dna["persona_summary"]["one_line_essence_en"], "Current B")
        self.assertEqual(len(backup._history_names(self.store.dna_history_dir)), 1)
        self.assertEqual(self.store.list_scripts()[0]["title"], "旧版脚本")
        self.assertEqual(len(self.store.list_trends()), 1)
        self.assertEqual(len(self.store.list_video_plans()), 1)

        # Older SQLite schema (before keywords, posted_on, video_plans, etc.) also upgrades cleanly through _rebuild + Store.
        older_sib = self.tmp / "learn-my-voice-0.1.0"
        (older_sib / "data").mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(str(older_sib / "data" / "studio.db"))) as db:
            db.executescript("""
                CREATE TABLE scripts (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',
                    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER', target_sec INTEGER NOT NULL DEFAULT 60,
                    notes TEXT NOT NULL DEFAULT '', lines_json TEXT NOT NULL DEFAULT '[]',
                    post_json TEXT NOT NULL DEFAULT '{}', ai_lines_json TEXT,
                    status TEXT NOT NULL DEFAULT 'DRAFT', created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE samples (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                    is_sponsored INTEGER NOT NULL DEFAULT 0, views INTEGER, likes INTEGER, collects INTEGER, comments INTEGER,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'script',
                    timing_json TEXT, source_name TEXT NOT NULL DEFAULT ''
                );
            """)
            db.execute(
                "INSERT INTO scripts VALUES ('000000000001', '早期脚本', 'NORMAL_ORGANIC', 'VLOG_VOICEOVER', 60, '', '[]', '{}', NULL, 'DRAFT', 1.0, 1.0)"
            )
            db.commit()
        fresh_target = Store(self.tmp / "learn-my-voice-0.2.1" / "data")
        mig_old = updater.migrate_from_previous_folder(fresh_target, older_sib)
        self.assertEqual(mig_old["scripts"], 1)
        self.assertEqual(fresh_target.list_scripts()[0]["title"], "早期脚本")

    def test_migration_refuses_whenever_current_folder_holds_any_work_and_names_backup_and_restore(self):
        self.packaged("0.2.0")
        sib, _ = self.make_sibling("0.1.9", with_whisper=False)

        def seed_sponsor_brief(st):
            with contextlib.closing(st._db()) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS sponsor_briefs ("
                    "script_id TEXT PRIMARY KEY, brief TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)"
                )
                db.execute("INSERT INTO sponsor_briefs (script_id, brief, created_at, updated_at) VALUES ('aabbccddeeff', 'brand brief', 1.0, 1.0)")
                db.commit()

        cases = [
            ("script", lambda st: st.create_script(title="我的脚本")),
            ("library_item", lambda st: st.create_sample(title="我的素材", text="今天天气真好啦")),
            ("creator_profile", lambda st: st.save_creator({"name": "阿明", "pronoun": "he"})),
            ("voice_profile", lambda st: st.save_dna(EXAMPLE_DNA)),
            ("tracked_keyword", lambda st: st.upsert_trends([{"keyword": "咖啡", "heat": 100}], source="manual")),
            ("plan", lambda st: planner.create_plan_item(st, {"title": "周六拍探店"})),
            ("sponsor_brief", seed_sponsor_brief),
        ]
        for idx, (label, populate) in enumerate(cases):
            target_dir = self.tmp / ("learn-my-voice-0.2.%d" % (idx + 1))
            target_store = Store(target_dir / "data")
            populate(target_store)
            self.assertFalse(updater.is_data_empty(target_store), label)
            httpd = server.make_server(0, target_store, None, None, self.world)
            t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
                body = json.dumps({"source_path": str(sib)}).encode("utf-8")
                conn.request("POST", "/api/update/migrate", body=body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                ans = json.loads(resp.read().decode("utf-8"))
                conn.close()
                self.assertEqual((resp.status, self.said(ans)), (409, "data_not_empty"), label)
                self.assertIn("Backup and restore", ans["message"])
                self.assertIn("Settings", ans["message"])
                self.assertIn("备份和恢复", ans["message_zh"])
                self.assertIn("设置", ans["message_zh"])
                # Nothing from sib ("旧版脚本") entered target_store.
                self.assertNotIn("旧版脚本", [s["title"] for s in target_store.list_scripts()])
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(5)

    def test_migration_refuses_while_a_job_runs_and_advances_data_epoch_against_open_pages(self):
        self.packaged("0.2.0")
        sib, _ = self.make_sibling("0.1.9", with_whisper=False)

        # 1. Refused while any background job is running.
        release_job = threading.Event()
        started_job = threading.Event()
        self.app.jobs.start("transcribe", lambda progress: (started_job.set(), release_job.wait(10)))
        self.assertTrue(started_job.wait(5))
        try:
            st_busy, ans_busy = self.send("POST", "/api/update/migrate", {"source_path": str(sib)})
            self.assertEqual((st_busy, self.said(ans_busy)), (409, "migrate_busy"))
            self.assertTrue(updater.is_data_empty(self.store))
        finally:
            release_job.set()
        deadline = time.time() + 5
        while self.app.jobs.unfinished() and time.time() < deadline:
            time.sleep(0.02)

        # 2. An open page from before migration holds the old X-Data-Epoch; after migration its writes are refused.
        st_init, _, init_headers = self.send_full("GET", "/api/state")
        self.assertEqual(st_init, 200)
        old_epoch = init_headers[backup.EPOCH_HEADER]
        self.assertEqual(old_epoch, backup.FIRST_EPOCH)

        st_mig, res_mig, mig_headers = self.send_full("POST", "/api/update/migrate", {"source_path": str(sib)})
        self.assertEqual(st_mig, 200, res_mig)
        new_epoch = mig_headers[backup.EPOCH_HEADER]
        self.assertNotEqual(new_epoch, old_epoch)

        migrated_script = self.store.list_scripts()[0]
        for method, path, payload in (
            ("POST", "/api/scripts", {"title": "旧标签页写进来的"}),
            ("PUT", "/api/scripts/" + migrated_script["id"], {"title": "覆盖迁移后的脚本"}),
        ):
            st_stale, ans_stale = self.send(method, path, payload, headers={backup.EPOCH_HEADER: old_epoch})
            self.assertEqual((st_stale, self.said(ans_stale)), (409, "stale_data"))
        self.assertEqual([s["title"] for s in self.store.list_scripts()], ["旧版脚本"])

    def test_migration_rejects_hostile_or_corrupt_source_databases_and_symlinks(self):
        self.packaged("0.2.0")

        # 1. Corrupt bytes in studio.db
        bad_bytes = self.tmp / "learn-my-voice-0.1.1"
        (bad_bytes / "data").mkdir(parents=True)
        (bad_bytes / "data" / "studio.db").write_bytes(b"not sqlite at all")
        st1, ans1 = self.send("POST", "/api/update/migrate", {"source_path": str(bad_bytes)})
        self.assertEqual((st1, self.said(ans1)), (400, "bad_source_data"))

        # 2. SQLite database containing a hostile trigger or view or unknown table
        for extra_sql, ver in (
            ("CREATE TRIGGER t AFTER INSERT ON scripts BEGIN SELECT 1; END;", "0.1.2"),
            ("CREATE VIEW v AS SELECT * FROM scripts;", "0.1.3"),
            ("CREATE TABLE evil (id TEXT PRIMARY KEY);", "0.1.4"),
        ):
            bad_sql_dir = self.tmp / ("learn-my-voice-%s" % ver)
            Store(bad_sql_dir / "data")
            with contextlib.closing(sqlite3.connect(str(bad_sql_dir / "data" / "studio.db"))) as db:
                db.executescript(extra_sql)
                db.commit()
            st_s, ans_s = self.send("POST", "/api/update/migrate", {"source_path": str(bad_sql_dir)})
            self.assertEqual((st_s, self.said(ans_s)), (400, "bad_source_data"), ver)

        # 3. Symlinked config.json inside source data/
        outside_secret = self.tmp / "secret.json"
        outside_secret.write_text('{"provider": "manual"}', encoding="utf-8")
        sym_dir = self.tmp / "learn-my-voice-0.1.5"
        Store(sym_dir / "data")
        os.symlink(str(outside_secret), str(sym_dir / "data" / "config.json"))
        st_sym, ans_sym = self.send("POST", "/api/update/migrate", {"source_path": str(sym_dir)})
        self.assertEqual((st_sym, self.said(ans_sym)), (400, "bad_source_data"))

        # Current store remained completely untouched and empty throughout.
        self.assertTrue(updater.is_data_empty(self.store))


# ---------------------------------------------------------------------------------------------- 6. U3 & U5: bounded apply-zip, never into an existing folder
class ApplyZipTest(ServerCase):
    def test_apply_zip_unpacks_via_temp_folder_migrates_data_and_never_copies_whisper(self):
        self.packaged("0.2.0-beta")
        self.store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5"),
            c["keys"].update(anthropic="sk-ant-zip-key"),
        ))
        self.store.keep_for_page({"lang": "en", "theme": "dark", "accent": "purple"})
        self.store.save_dna(EXAMPLE_DNA)
        self.store.create_script(title="Carry Me Over", notes="Important script")
        whisper_bin = self.home / "local_tools" / "whisper" / "venv" / "bin"
        whisper_bin.mkdir(parents=True, exist_ok=True)
        (whisper_bin / "python3").write_bytes(b"fake-whisper")

        pkg = self.save_zip(make_release_zip_bytes("0.3.0-beta", {"README.md": "# 0.3.0-beta\n"}))
        st, res = self.send("POST", "/api/update/apply-zip", {"zip_path": str(pkg)})
        self.assertEqual(st, 201, res)
        unpacked = Path(res["unpacked_to"])
        self.assertEqual(unpacked.name, "learn-my-voice-0.3.0-beta")
        self.assertFalse((unpacked / "local_tools" / "whisper").exists())
        self.assertEqual(res["migration"]["whisper_copied"], False)
        self.assertEqual(res["migration"]["had_whisper"], True)
        if os.name != "nt":
            self.assertTrue(os.access(str(unpacked / "start-learn-my-voice.sh"), os.X_OK))
        # No leftover .unpacking- directories beside the folders.
        leftovers = [p.name for p in self.tmp.iterdir() if ".unpacking-" in p.name]
        self.assertEqual(leftovers, [])

        new_store = Store(unpacked / "data")
        new_cfg = new_store.load_config()
        self.assertEqual((new_cfg["provider"], new_cfg["model"], new_cfg["keys"]["anthropic"]),
                         ("anthropic", "claude-sonnet-4-5", "sk-ant-zip-key"))
        self.assertEqual(new_store.load_page_keeps(), {"lang": "en", "theme": "dark", "accent": "purple"})
        self.assertFalse(new_store.load_dna()[1])
        self.assertEqual(new_store.list_scripts()[0]["title"], "Carry Me Over")

    def test_apply_zip_refuses_when_destination_already_exists_without_touching_it(self):
        self.packaged("0.2.0-beta")
        pkg = self.save_zip(make_release_zip_bytes("0.3.0-beta"))
        target = self.tmp / "learn-my-voice-0.3.0-beta"

        # 1. Existing directory with creator's file inside -> refused, file untouched.
        (target / "data").mkdir(parents=True)
        marker = target / "data" / "keep.txt"
        marker.write_text("do not touch", encoding="utf-8")
        st1, ans1 = self.send("POST", "/api/update/apply-zip", {"zip_path": str(pkg)})
        self.assertEqual((st1, self.said(ans1)), (409, "destination_exists"))
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not touch")
        shutil.rmtree(str(target))

        # 2. Existing plain file or symlink at destination -> refused.
        target.write_text("file in the way", encoding="utf-8")
        st2, ans2 = self.send("POST", "/api/update/apply-zip", {"zip_path": str(pkg)})
        self.assertEqual((st2, self.said(ans2)), (409, "destination_exists"))
        target.unlink()

        other = self.tmp / "other-place"
        other.mkdir()
        os.symlink(str(other), str(target))
        st3, ans3 = self.send("POST", "/api/update/apply-zip", {"zip_path": str(pkg)})
        self.assertEqual((st3, self.said(ans3)), (409, "destination_exists"))
        target.unlink()
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".unpacking-" in p.name], [])

    def test_apply_zip_refuses_traversal_symlinks_windows_unsafe_names_forbidden_dirs_and_zip_bombs(self):
        self.packaged("0.2.0-beta")

        def build_custom_zip(entries, version="0.4.0"):
            top = "learn-my-voice-%s" % version
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "%s/studio/build.json" % top,
                    json.dumps({"version": version, "commit": "a" * 40, "built_on": "2026-11-21"}),
                )
                for info_or_name, data in entries:
                    zf.writestr(info_or_name, data)
            return buf.getvalue()

        symlink_info = zipfile.ZipInfo("learn-my-voice-0.4.0/evil_link", date_time=(2026, 11, 21, 0, 0, 0))
        symlink_info.create_system = 3
        symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16

        backslash_info = zipfile.ZipInfo("learn-my-voice-0.4.0/evil.txt", date_time=(2026, 11, 21, 0, 0, 0))
        backslash_info.filename = "learn-my-voice-0.4.0\\evil.txt"

        bad_archives = [
            ("traversal", [("learn-my-voice-0.4.0/../../escaped.txt", b"x")]),
            ("backslash", [(backslash_info, b"x")]),
            ("symlink", [(symlink_info, b"/etc/passwd")]),
            ("windows_device", [("learn-my-voice-0.4.0/CON.py", b"x")]),
            ("windows_colon", [("learn-my-voice-0.4.0/a:b.txt", b"x")]),
            ("trailing_dot", [("learn-my-voice-0.4.0/bad./a.txt", b"x")]),
            ("data_inside", [("learn-my-voice-0.4.0/data/studio.db", b"x")]),
            ("whisper_inside", [("learn-my-voice-0.4.0/local_tools/whisper/x", b"x")]),
        ]
        for label, entries in bad_archives:
            pkg = self.save_zip(build_custom_zip(entries), name="bad-%s.zip" % label)
            st, ans = self.send("POST", "/api/update/apply-zip", {"zip_path": str(pkg)})
            self.assertEqual((st, self.said(ans)), (400, "bad_zip"), label)
            self.assertFalse((self.tmp / "learn-my-voice-0.4.0").exists(), label)
            self.assertFalse((self.tmp.parent / "escaped.txt").exists(), label)

        # Entry count limit and uncompressed byte cap.
        many_pkg = self.save_zip(
            build_custom_zip([("learn-my-voice-0.4.0/f%d.txt" % i, b"x") for i in range(12)]),
            name="too-many.zip",
        )
        with mock.patch.object(updater, "MAX_ZIP_ENTRIES", 5):
            st_cnt, ans_cnt = self.send("POST", "/api/update/apply-zip", {"zip_path": str(many_pkg)})
        self.assertEqual((st_cnt, self.said(ans_cnt)), (413, "zip_too_large"))

        big_pkg = self.save_zip(
            build_custom_zip([("learn-my-voice-0.4.0/big.txt", b"A" * 32768)]),
            name="too-big.zip",
        )
        with mock.patch.object(updater, "MAX_ZIP_UNPACKED_BYTES", 4096):
            st_sz, ans_sz = self.send("POST", "/api/update/apply-zip", {"zip_path": str(big_pkg)})
        self.assertEqual((st_sz, self.said(ans_sz)), (413, "zip_too_large"))
        self.assertFalse((self.tmp / "learn-my-voice-0.4.0").exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".unpacking-" in p.name], [])


# ---------------------------------------------------------------------------------------------- 6b. the unpacked database is closed before the rename
class HandlesTest(ServerCase):
    """An unpacked folder is renamed into place with no connection left open on the database inside it.

    On the Windows runners with Python 3.14 and 3.15 that rename was refused once, while 3.9 passed. Nothing
    here runs on Windows; what is checked is what Windows needs, in a way every system can check.
    """

    def unpack(self, version):
        package = self.save_zip(make_release_zip_bytes(version), name="learn-my-voice-%s.zip" % version)
        return self.send("POST", "/api/update/apply-zip", {"zip_path": str(package)})

    def leftovers(self):
        return [path.name for path in self.tmp.iterdir() if ".unpacking-" in path.name]

    def test_every_connection_on_the_unpacked_database_is_closed_before_the_folder_is_put_in_place(self):
        self.packaged("0.2.0-beta")
        self.store.create_script(title="Carry Me Over")
        self.store.create_sample(title="素材", text="今天去买咖啡啦")
        with watched_databases() as notes, at_the_rename(notes) as looks:
            status, answer = self.unpack("0.3.0-beta")
        self.assertEqual(status, 201, answer)
        self.assertEqual(len(looks), 1)
        # It was made, looked at to see that it is empty, filled, and counted: the watch saw that.
        self.assertGreaterEqual(len(looks[0]), 3, looks[0])
        self.assertEqual([note for note in looks[0] if not note["closed"]], [])
        self.assertEqual((answer["migration"]["scripts"], answer["migration"]["samples"]), (1, 1))

    def test_the_same_watch_notices_a_connection_that_is_only_dropped(self):
        """Without this the test above would also pass if the watch saw nothing, or took dropped for closed."""
        self.packaged("0.2.0-beta")
        really_empty = updater.is_data_empty

        def counts_first(store):
            store.samples_count()           # ``with self._db() as db:`` commits and does not close
            return really_empty(store)

        with watched_databases() as notes, at_the_rename(notes) as looks, \
                mock.patch.object(updater, "is_data_empty", counts_first):
            status, answer = self.unpack("0.3.0-beta")
        self.assertEqual(status, 201, answer)
        self.assertEqual(len([note for note in looks[0] if not note["closed"]]), 1)

    def test_a_refusal_that_ends_is_waited_out_and_one_that_lasts_is_said_in_two_languages(self):
        self.packaged("0.2.0-beta")
        real_rename = os.rename
        tried = []

        def refused(times, error, first=None):
            """Stands in for ``os.rename``: the first ``times`` tries to place an unpacked folder raise ``error``."""
            def rename(source, target, *args, **more):
                if ".unpacking-" not in os.path.basename(os.fspath(source)):
                    return real_rename(source, target, *args, **more)
                tried.append(os.fspath(target))
                if len(tried) <= times:
                    if first is not None and len(tried) == 1:
                        first(Path(os.fspath(target)))
                    raise error
                return real_rename(source, target, *args, **more)
            return rename

        held = PermissionError(errno.EACCES, "held by another program")

        # Held for two tries, as by a program that is only looking: waited out, where waiting is on.
        with mock.patch.object(updater, "_PLACE_TRIES", 4), mock.patch.object(os, "rename", refused(2, held)):
            status, answer = self.unpack("0.3.1")
        self.assertEqual((status, len(tried)), (201, 3), answer)
        self.assertTrue((self.tmp / "learn-my-voice-0.3.1" / "data" / "studio.db").is_file())

        # Held for good: every try is used, then it is said, and nothing is left behind.
        del tried[:]
        with mock.patch.object(updater, "_PLACE_TRIES", 4), mock.patch.object(os, "rename", refused(99, held)):
            status, answer = self.unpack("0.3.2")
        self.assertEqual((status, self.said(answer), len(tried)), (507, "save_failed", 4))
        self.assertIn("held by another program", answer["message"])
        self.assertFalse((self.tmp / "learn-my-voice-0.3.2").exists())
        self.assertEqual(self.leftovers(), [])

        # Where a refusal is a real one (every system but Windows) it is tried once.
        del tried[:]
        self.assertEqual(updater._PLACE_TRIES, 12 if os.name == "nt" else 1)
        with mock.patch.object(updater, "_PLACE_TRIES", 1), mock.patch.object(os, "rename", refused(99, held)):
            status, answer = self.unpack("0.3.3")
        self.assertEqual((status, self.said(answer), len(tried)), (507, "save_failed", 1))
        self.assertEqual(self.leftovers(), [])

        # A folder of that name made by somebody else in the meantime is noticed, and left as it is.
        del tried[:]
        with mock.patch.object(updater, "_PLACE_TRIES", 4), \
                mock.patch.object(os, "rename", refused(99, held, first=lambda target: target.mkdir())):
            status, answer = self.unpack("0.3.4")
        self.assertEqual((status, self.said(answer), len(tried)), (409, "destination_exists", 1))
        self.assertEqual(list((self.tmp / "learn-my-voice-0.3.4").iterdir()), [])
        self.assertEqual(self.leftovers(), [])

        # Any other failure is not one that ends by itself: not tried again.
        del tried[:]
        elsewhere = OSError(errno.EXDEV, "another disk")
        with mock.patch.object(updater, "_PLACE_TRIES", 4), mock.patch.object(os, "rename", refused(99, elsewhere)):
            status, answer = self.unpack("0.3.5")
        self.assertEqual((status, self.said(answer), len(tried)), (507, "save_failed", 1))
        self.assertEqual(self.leftovers(), [])


# ---------------------------------------------------------------------------------------------- 7. the page (Chrome E2E in zh/en, light/dark)
class UpdaterCopyTest(unittest.TestCase):
    def test_both_languages_are_complete_and_assume_nothing_about_the_creator(self):
        script = (WEB / "updater.js").read_text(encoding="utf-8")
        self.assertTrue(script.startswith("'use strict';"))
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        zh = dict(re.findall(r"\b(up_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(up_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertEqual(sorted(zh), sorted(en))
        self.assertGreaterEqual(len(zh), 30)
        for key in zh:
            self.assertRegex(zh[key], "[\u4e00-\u9fff]", key)
            self.assertNotRegex(en[key], "[\u4e00-\u9fff]", key)
            self.assertIsNone(ASSUMES.search(en[key]), (key, en[key]))
        self.assertIn("LEARN_MY_VOICE_NO_UPDATE_CHECK=1", zh["up_off"])
        self.assertIn("LEARN_MY_VOICE_NO_UPDATE_CHECK=1", en["up_off"])
        self.assertIn("api.github.com", zh["up_privacy_note"])
        self.assertIn("api.github.com", en["up_privacy_note"])
        self.assertIn("IP", zh["up_privacy_note"])
        self.assertIn("IP", en["up_privacy_note"])
        self.assertIn(".sha256", zh["up_sha_note"])
        self.assertIn(".sha256", en["up_sha_note"])
        self.assertIn("local_tools/whisper", zh["up_whisper_note"])
        self.assertIn("local_tools/whisper", en["up_whisper_note"])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class PageOffTest(Case):
    def test_with_looking_off_the_page_offers_no_look_and_no_download_and_says_why(self):
        self.packaged("0.2.0")
        self.keep_found("9.0.0")
        texts = {"zh": "这次运行没有开启检查更新", "en": "Looking for updates is switched off for this run"}
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
                    self.assertEqual(page.problems, [], lang)
        self.assertEqual(self.asked, [])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class PageInteractiveTest(Case):
    def test_startup_banner_download_apply_zip_settings_and_migration_in_zh_en_light_and_dark(self):
        self.packaged("0.2.0")
        zip_bytes = make_release_zip_bytes("9.0.0", {"README.md": "# v9.0.0\n"})
        good_sha = hashlib.sha256(zip_bytes).hexdigest()
        releases = [{
            "tag_name": "v9.0.0",
            "prerelease": False,
            "draft": False,
            "published_at": "2026-11-21T00:00:00Z",
            "html_url": "https://github.com/%s/releases/tag/v9.0.0" % REPO,
            "body": "Faster exports\nCleaner backup checks",
            "assets": [
                {
                    "name": "learn-my-voice-9.0.0.zip",
                    "browser_download_url": "https://github.com/%s/releases/download/v9.0.0/learn-my-voice-9.0.0.zip" % REPO,
                    "size": len(zip_bytes),
                },
                {
                    "name": "learn-my-voice-9.0.0.zip.sha256",
                    "browser_download_url": "https://github.com/%s/releases/download/v9.0.0/learn-my-voice-9.0.0.zip.sha256" % REPO,
                    "size": 90,
                },
            ],
        }]

        def stand_in(method, url, headers):
            if url.endswith("/releases?per_page=15"):
                return json.dumps(releases).encode("utf-8")
            if url.endswith(".zip.sha256"):
                return ("%s  learn-my-voice-9.0.0.zip\n" % good_sha).encode("utf-8")
            if url.endswith(".zip"):
                return zip_bytes
            raise AssertionError("Unexpected URL: " + url)

        # Create an earlier sibling folder so the empty-folder migration banner and Settings section can be exercised.
        sib = self.tmp / "learn-my-voice-0.1.9"
        sib_store = Store(sib / "data")
        sib_store.create_script(title="从旧版带来的脚本", lines=[{"spoken_script_zh": "今天去买咖啡啦"}])

        with loopback_only() as blocked:
            # 1. Chinese + dark mode: startup banner (What's new, Later), Settings privacy/SHA/persistence notes, migration.
            self.store.keep_for_page({"lang": "zh", "theme": "dark"})
            with browser.open_app(self.store, lang="zh") as page:
                world = World(self.tmp / "Downloads")
                page.httpd.app.outside = world
                page.httpd.app.update_transport = stand_in
                page.httpd.app.update_checks = True

                page.eval_js("sessionStorage.removeItem('up-checked'); upState.status = null;")
                page.go("#/trends", "Boolean(document.getElementById('up-startup-banner')) && Boolean(document.getElementById('up-prev-banner'))")
                self.assertEqual(page.eval_js("document.documentElement.getAttribute('data-theme')"), "dark")

                # Toggle What's new and follow release link through openOutside.
                page.eval_js("document.getElementById('up-notes-now').click()")
                page.wait_for("Boolean(document.getElementById('up-notes-box'))")
                self.assertIn("Faster exports", page.eval_js("document.getElementById('up-notes-box').textContent"))
                page.eval_js("document.getElementById('up-release-link').click()")
                deadline = time.time() + 5
                while not world.opened and time.time() < deadline:
                    time.sleep(0.02)
                self.assertEqual(world.opened, ["https://github.com/%s/releases/tag/v9.0.0" % REPO])

                # Click Later -> startup banner hides for this session.
                page.eval_js("document.getElementById('up-later-now').click()")
                page.wait_for("!document.getElementById('up-startup-banner')")

                # Migrate from the earlier sibling folder via the startup banner.
                page.eval_js("document.querySelector('#up-prev-banner [data-up-migrate]').click()")
                page.wait_for("!document.getElementById('up-prev-banner')")
                self.assertEqual([s["title"] for s in self.store.list_scripts()], ["从旧版带来的脚本"])

                # Settings card in Chinese + dark: check data path, privacy note, SHA-256 note, persistence note, scan button.
                page.go("#/settings", "Boolean(document.getElementById('up-settings-card'))")
                card_zh = page.eval_js("document.getElementById('up-settings-card').textContent")
                for expected in ("api.github.com", "IP", ".sha256", "local_tools/whisper", str(self.store.data_dir.resolve())):
                    self.assertIn(expected, card_zh)
                page.eval_js("document.getElementById('up-scan-btn').click()")
                page.wait_for("Boolean(document.getElementById('up-prev-nonempty'))")
                self.assertIn("备份和恢复", page.eval_js("document.getElementById('up-prev-nonempty').textContent"))
                self.assertEqual(page.problems, [])

            # 2. English + light mode: Download, Unpack to new folder, startup toggle, Skip this version, Desktop persistence copy.
            self.store.keep_for_page({"lang": "en", "theme": "light"})
            with browser.open_app(self.store, lang="en") as page:
                world = World(self.tmp / "Downloads-en")
                page.httpd.app.outside = world
                page.httpd.app.update_transport = stand_in
                page.httpd.app.update_checks = True

                page.eval_js("sessionStorage.removeItem('up-checked'); upState.status = null; upState.startupDismissed = false;")
                page.go("#/trends", "Boolean(document.getElementById('up-startup-banner'))")
                self.assertEqual(page.eval_js("document.documentElement.getAttribute('data-theme')"), "light")
                # Folder is no longer empty after migration above, so #up-prev-banner is NOT shown.
                self.assertFalse(page.eval_js("Boolean(document.getElementById('up-prev-banner'))"))

                # Download from startup banner and unpack to new sibling folder.
                page.eval_js("document.getElementById('up-dl-now').click()")
                page.wait_for("Boolean(document.getElementById('up-dl-result'))")
                self.assertIn("not signed", page.eval_js("document.getElementById('up-dl-sha-note').textContent"))
                page.eval_js("document.querySelector('[data-up-apply-zip]').click()")
                page.wait_for("Boolean(document.getElementById('up-applied-msg'))")
                self.assertTrue((self.tmp / "learn-my-voice-9.0.0" / "data" / "studio.db").is_file())
                self.assertEqual(Store(self.tmp / "learn-my-voice-9.0.0" / "data").list_scripts()[0]["title"], "从旧版带来的脚本")

                # Skip this version -> banner closes and dismissed_version is saved.
                page.eval_js("document.getElementById('up-skip-now').click()")
                page.wait_for("!document.getElementById('up-startup-banner')")
                self.assertEqual(updater._stored_settings(self.store)["dismissed_version"], "9.0.0")

                # Settings card in English + light: toggle auto_check off, verify desktop persistence wording too.
                page.go("#/settings", "Boolean(document.getElementById('up-settings-card'))")
                card_en = page.eval_js("document.getElementById('up-settings-card').textContent")
                for expected in ("api.github.com", "IP address", ".sha256", "local_tools/whisper"):
                    self.assertIn(expected, card_en)
                page.eval_js("document.getElementById('up-auto-chk').click()")
                page.wait_for("upState.status && upState.status.auto_check === false")
                self.assertFalse(updater._stored_settings(self.store)["auto_check"])

                # Switch runtime to desktop mode and verify Settings shows OS application-data persistence wording.
                with mock.patch.object(buildinfo, "desktop", return_value={"python": "3.14.10"}):
                    page.eval_js("upState.status = null;")
                    page.go("#/trends", "true")
                    page.go("#/settings", "Boolean(document.getElementById('up-persistence-note'))")
                    self.assertIn("application-data folder", page.eval_js("document.getElementById('up-persistence-note').textContent"))
                    self.assertFalse(page.eval_js("Boolean(document.getElementById('up-prev-section'))"))
                self.assertEqual(page.problems, [])

        self.assertEqual(blocked, [])


if __name__ == "__main__":
    unittest.main()
