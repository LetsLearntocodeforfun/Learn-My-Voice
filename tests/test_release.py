"""Tests for what makes a release: the build file the app reads, the package, its notes, the launchers, the workflows,
and the runner that says which tests did not pass.

The package is built from the commit that is checked out, so the tests that build one need git and a copy
of the repository; they are skipped where there is neither (an unpacked package has no tests at all).
What GitHub itself does with the workflow files cannot be run here: those tests read the files and check
what must hold of them (every action pinned to a commit, one job allowed to write, no text from a push
written into a command).
"""
import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from studio import buildinfo, server
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "tools" / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_release = load_tool("build_release")
try_release = load_tool("try_release")
run_tests = load_tool("run_tests")


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, check=True).stdout


def in_a_repository():
    try:
        return git("rev-parse", "--is-inside-work-tree").strip() == b"true"
    except (OSError, subprocess.CalledProcessError):
        return False


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


class BuildInfoTest(TempCase):
    def write(self, value):
        path = self.tmp / "build.json"
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def test_a_package_says_which_build_it_is(self):
        path = self.write({"version": "0.2.0-beta.rc.7", "commit": "a" * 40, "built_on": "2026-11-16"})
        self.assertEqual(buildinfo.read(path), {"from_source": False, "version": "0.2.0-beta.rc.7", "commit": "a" * 40, "built_on": "2026-11-16"})
        for version in ("0.2.0", "0.2.0-beta", "0.2.0-beta.dev", "0.2.0-beta.rc.12.2", "1.10.3+local"):
            self.assertEqual(buildinfo.read(self.write({"version": version, "commit": "abc1234", "built_on": "2026-11-16"}))["version"], version)

    def test_without_a_build_file_it_says_from_source_and_guesses_nothing(self):
        self.assertEqual(buildinfo.read(self.tmp / "none.json"), {"from_source": True, "version": None, "commit": None, "built_on": None})
        self.assertFalse((ROOT / "studio" / "build.json").exists())          # the source never carries one
        self.assertTrue(buildinfo.current()["from_source"])

    def test_a_file_that_is_not_one_the_build_wrote_counts_as_absent(self):
        good = {"version": "0.2.0-beta.rc.7", "commit": "a" * 40, "built_on": "2026-11-16"}
        bad = ["", "{", "[]", "null", '"text"', json.dumps(dict(good, version="<script>alert(1)</script>")),
               json.dumps(dict(good, version="v0.2.0")), json.dumps(dict(good, version=5)), json.dumps(dict(good, commit="not hex")),
               json.dumps(dict(good, commit="abc")), json.dumps(dict(good, built_on="yesterday")), json.dumps({"version": "0.2.0"}),
               json.dumps(dict(good, note="x" * 5000))]
        for text in bad:
            self.assertTrue(buildinfo.read(self.write(text))["from_source"], text[:60])
        self.assertTrue(buildinfo.read(self.tmp)["from_source"])                 # a folder where the file should be

    def test_the_app_tells_the_page(self):
        store = Store(self.tmp / "data")
        httpd = server.make_server(0, store)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def state():
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            conn.request("GET", "/api/state")
            out = json.loads(conn.getresponse().read().decode("utf-8"))
            conn.close()
            return out["build"]

        self.assertEqual(state(), {"from_source": True, "version": None, "commit": None, "built_on": None})
        packaged = self.write({"version": "0.2.0-beta.rc.7", "commit": "b" * 40, "built_on": "2026-11-16"})
        with mock.patch.object(buildinfo, "BUILD_FILE", packaged):
            self.assertEqual(state()["version"], "0.2.0-beta.rc.7")
        page = (ROOT / "studio" / "web" / "pages.js").read_text(encoding="utf-8")
        self.assertIn("function buildLine()", page)
        self.assertEqual(len(re.findall(r"\bbuild_version: '", page)), 2)        # said in both languages
        self.assertEqual(len(re.findall(r"\bbuild_source: '", page)), 2)


class VersionTest(unittest.TestCase):
    def test_the_version_is_the_first_heading_of_the_changelog(self):
        self.assertEqual(build_release.base_version("# Changelog\n\n## v0.2.0-beta (not released yet)\n\n## v0.1.1-beta\n"), "0.2.0-beta")
        self.assertEqual(build_release.base_version("## v1.4.0 (2027-01-01)\n"), "1.4.0")
        with self.assertRaises(SystemExit):
            build_release.base_version("# Changelog\n\nNothing yet.\n")
        self.assertRegex(build_release.base_version(), r"^\d+\.\d+\.\d+")       # the real changelog has one

    def test_a_release_candidate_is_numbered_and_a_local_build_is_marked(self):
        self.assertEqual(build_release.version_for("0.2.0-beta", 7), "0.2.0-beta.rc.7")
        self.assertEqual(build_release.version_for("0.2.0-beta", 7, 2), "0.2.0-beta.rc.7.2")      # the same run started again
        self.assertEqual(build_release.version_for("0.2.0-beta", None), "0.2.0-beta.dev")
        for rc, attempt in ((0, 1), (-1, 1), (3, 0)):
            with self.assertRaises(SystemExit):
                build_release.version_for("0.2.0-beta", rc, attempt)
        # Every version the build can make is one the app accepts from its build file.
        for version in ("0.2.0-beta.rc.7", "0.2.0-beta.rc.7.2", "0.2.0-beta.dev"):
            self.assertIsNotNone(buildinfo._VERSION.fullmatch(version), version)


@unittest.skipUnless(in_a_repository(), "needs git and a copy of the repository")
class PackageTest(TempCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        try:
            cls.made = build_release.build_package(cls.out, 7)
        except SystemExit as stopped:
            # The build script stops with a message. Here that has to be a failed test, not the end of the whole run.
            shutil.rmtree(str(cls.out), ignore_errors=True)
            raise AssertionError("the package could not be built: %s" % stopped)
        cls.package = Path(cls.made["path"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.out), ignore_errors=True)

    def members(self):
        with zipfile.ZipFile(str(self.package)) as archive:
            return {info.filename: (info, archive.read(info)) for info in archive.infolist()}

    def test_the_same_commit_gives_the_same_bytes(self):
        again = build_release.build_package(self.tmp, 7)
        self.assertEqual(again["sha256"], self.made["sha256"])
        self.assertEqual(Path(again["path"]).read_bytes(), self.package.read_bytes())
        self.assertNotEqual(build_release.build_package(self.tmp / "other", 8)["sha256"], self.made["sha256"])   # another build, another file
        self.assertEqual((self.out / (self.package.name + ".sha256")).read_text(encoding="utf-8"),
                         "%s  %s\n" % (self.made["sha256"], self.package.name))

    def test_it_is_named_after_its_version_and_holds_one_folder(self):
        base = build_release.base_version(git("show", "HEAD:CHANGELOG.md").decode("utf-8"))
        self.assertEqual((self.made["version"], self.made["tag"], self.made["file"]),
                         (base + ".rc.7", "v%s.rc.7" % base, "learn-my-voice-%s.rc.7.zip" % base))
        self.assertEqual({name.split("/")[0] for name in self.members()}, {"learn-my-voice-%s.rc.7" % base})

    def test_every_file_is_the_committed_one_and_nothing_is_compressed(self):
        folder = self.package.name[:-len(".zip")] + "/"
        committed = dict((path, (mode, blob)) for path, mode, blob in build_release.committed_files())
        members = self.members()
        self.assertEqual(sorted(members), sorted([folder + path for path in committed] + [folder + "studio/build.json"]))
        times = set()
        for path, (mode, blob) in committed.items():
            info, data = members[folder + path]
            self.assertEqual(data, git("cat-file", "blob", blob), path)
            self.assertEqual(((info.external_attr >> 16) & 0o777, info.compress_type), (mode, zipfile.ZIP_STORED), path)
            times.add(info.date_time)
        self.assertEqual(len(times), 1)                                         # every file carries the time of the commit

    def test_what_a_user_needs_is_in_it_and_what_they_must_never_get_is_not(self):
        folder = self.package.name[:-len(".zip")] + "/"
        names = {name[len(folder):] for name in self.members()}
        for needed in ("Start Learn My Voice.command", "启动工作台.command", "Start Learn My Voice.bat", "start-learn-my-voice.sh",
                       "studio/server.py", "studio/web/index.html", "studio/web/prompter.js", "studio/lexicon.json",
                       "prompts/04_script_generator_normal_and_sponsored.md", "schemas/voice_dna.example.json", "run_mcp.py",
                       "README.md", "README.zh-CN.md", "LICENSE", "CHANGELOG.md"):
            self.assertIn(needed, names)
        for name in names:
            for never in ("data/", "local_tools/", "tests/", "tools/", "desktop/", ".git", "__pycache__", "dist/"):
                self.assertFalse(name.startswith(never), name)
            self.assertFalse(name.endswith((".pyc", ".db", ".sqlite", "config.json", ".DS_Store")), name)

    def test_the_launchers_keep_what_makes_them_start(self):
        folder = self.package.name[:-len(".zip")] + "/"
        members = self.members()
        for name in ("Start Learn My Voice.command", "启动工作台.command", "start-learn-my-voice.sh", "run_mcp.py"):
            info, data = members[folder + name]
            self.assertEqual((info.external_attr >> 16) & 0o777, 0o755, name)   # marked as a program
            self.assertNotIn(b"\r", data, name)
        for name in ("Start Learn My Voice.command", "启动工作台.command", "start-learn-my-voice.sh"):
            self.assertIn(b'-m studio.server "$@"', members[folder + name][1], name)     # what is typed after it reaches the app
        bat = members[folder + "Start Learn My Voice.bat"][1]
        self.assertEqual(bat.count(b"\n"), bat.count(b"\r\n"))                  # a batch file wants CRLF on every line
        self.assertGreater(bat.count(b"\r\n"), 10)
        for needed in (b"chcp 65001", b"set PYTHONUTF8=1", b'cd /d "%~dp0"', b"-m studio.server %*", b"py -3", b"pause"):
            self.assertIn(needed, bat)

    def test_its_build_file_names_this_commit(self):
        folder = self.package.name[:-len(".zip")] + "/"
        build = json.loads(self.members()[folder + "studio/build.json"][1].decode("utf-8"))
        self.assertEqual((build["version"], build["commit"]), (self.made["version"], git("rev-parse", "HEAD").decode().strip()))
        self.assertRegex(build["built_on"], r"^\d{4}-\d{2}-\d{2}$")
        unpacked = self.tmp / "build.json"
        unpacked.write_bytes(self.members()[folder + "studio/build.json"][1])
        self.assertEqual(buildinfo.read(unpacked)["version"], self.made["version"])      # and the app reads it

    def test_a_package_is_checked_before_it_is_published(self):
        self.assertEqual(build_release.verify_package(self.package, self.made["sha256"])["version"], self.made["version"])
        self.assertEqual(build_release.verify_package(self.package, "  " + self.made["sha256"].upper() + "\n")["version"], self.made["version"])
        with self.assertRaises(SystemExit):
            build_release.verify_package(self.package, "0" * 64)
        for bad_name in ("learn-my-voice-x/../../evil.txt", "/abs.txt", "learn-my-voice-x/data/studio.db", "learn-my-voice-x/tests/t.py",
                         "one/a.txt"):
            forged = self.tmp / ("forged-%d.zip" % len(bad_name))
            with zipfile.ZipFile(str(forged), "w") as archive:
                archive.writestr("learn-my-voice-x/studio/build.json", json.dumps({"version": "x"}))
                archive.writestr(bad_name, "x")
            with self.assertRaises(SystemExit, msg=bad_name):
                build_release.verify_package(forged, hashlib.sha256(forged.read_bytes()).hexdigest())

    def test_the_trial_unpacks_it_with_its_modes_and_refuses_an_unsafe_archive(self):
        app, build = try_release.unpack(self.package, self.tmp)
        self.assertEqual((app.name, build["version"]), (self.package.name[:-len(".zip")], self.made["version"]))
        self.assertTrue((app / "studio" / "server.py").is_file())
        if os.name != "nt":
            self.assertTrue(os.access(str(app / "Start Learn My Voice.command"), os.X_OK))
            self.assertFalse(os.access(str(app / "README.md"), os.X_OK))
        unsafe = self.tmp / "unsafe.zip"
        with zipfile.ZipFile(str(unsafe), "w") as archive:
            archive.writestr("app/../../outside.txt", "x")
        with self.assertRaises(try_release.Failed):
            try_release.unpack(unsafe, self.tmp / "elsewhere")
        self.assertFalse((self.tmp.parent / "outside.txt").exists())


class NotesTest(TempCase):
    BUILD = {"version": "0.2.0-beta.rc.7", "commit": "c" * 40, "built_on": "2026-11-16"}
    LEGS = {"tests": 3, "try": 2}

    def results(self, **failing):
        """Results as the jobs write them: three test jobs and two trials per system; ``failing`` names what failed,
        ``said`` what a failed job reported about it."""
        out = []
        for runner in ("macos-latest", "windows-latest", "ubuntu-24.04"):
            for kind, pythons in (("tests", ("3.9", "3.14", "3.15")), ("try", ("3.9", "3.15"))):
                for python in pythons:
                    label = "%s / Python %s" % (runner, python)
                    ok = (kind, label) not in failing.get("failed", ())
                    out.append({"kind": kind, "label": label, "ok": ok, "detail": "" if ok else failing.get("said", {}).get((kind, label), "")})
        return [r for r in out if (r["kind"], r["label"]) not in failing.get("silent", ())]

    def notes(self, results, require=("macos", "linux:tests")):
        with mock.patch.object(build_release, "changelog_section", return_value="**New**\n\n- Something new."):
            return build_release.release_notes(self.BUILD, "f" * 64, "learn-my-voice-0.2.0-beta.rc.7.zip", results, list(require),
                                               "https://example.test/run/1", "beta/0.2.0", self.LEGS)

    def test_the_notes_say_what_it_is_how_to_start_it_and_how_to_check_it(self):
        text, problems = self.notes(self.results())
        self.assertEqual(problems, [])
        for said in ("A release candidate of 0.2.0-beta", "commit `cccccccccccc`", "on branch `beta/0.2.0`", "not the finished 0.2.0-beta",
                     "learn-my-voice-0.2.0-beta.rc.7.zip", "Start Learn My Voice.command", "Start Learn My Voice.bat",
                     "sh start-learn-my-voice.sh", "Privacy & Security", "Open Anyway", "No person has used this build on any system",
                     "f" * 64, "tools/build_release.py build --rc 7`", "https://example.test/run/1", "### What is in 0.2.0-beta",
                     "- Something new."):
            self.assertIn(said, text)
        self.assertIn("| macOS | passed (3 of 3) | passed (2 of 2) |", text)
        self.assertIn("| Windows | passed (3 of 3) | passed (2 of 2) |", text)
        self.assertIn("| Linux | passed (3 of 3) | passed (2 of 2) |", text)
        self.assertNotIn("FAILED", text)

    def test_a_failure_on_a_system_that_may_fail_is_said_in_words_and_does_not_stop_the_release(self):
        text, problems = self.notes(self.results(failed=[("tests", "windows-latest / Python 3.9"), ("try", "windows-latest / Python 3.9"),
                                                         ("try", "windows-latest / Python 3.15")]))
        self.assertEqual(problems, [])
        self.assertIn("| Windows | FAILED (2 of 3 passed) | FAILED (0 of 2 passed) |", text)
        self.assertIn("| macOS | passed (3 of 3) | passed (2 of 2) |", text)
        self.assertIn("Failed: windows-latest / Python 3.15; windows-latest / Python 3.9.", text)
        self.assertIn("should be taken as not working yet", text)

    def test_a_failure_on_a_required_system_stops_the_release(self):
        for failed in ([("tests", "macos-latest / Python 3.14")], [("try", "macos-latest / Python 3.9")]):
            text, problems = self.notes(self.results(failed=failed))
            self.assertEqual(len(problems), 1, failed)
            self.assertIn("macos", problems[0])
        text, problems = self.notes(self.results(failed=[("tests", "ubuntu-24.04 / Python 3.9")]), require=("macos", "linux"))
        self.assertEqual(problems, ["linux: the tests failed"])

    def test_a_system_can_be_required_for_one_of_its_two_checks(self):
        # As the workflow asks: both checks on macOS, the test suite on Linux. The Linux trial may still fail.
        self.assertEqual(build_release.required(["macos", "linux:tests"]), [("macos", "tests"), ("macos", "try"), ("linux", "tests")])
        self.assertEqual(build_release.required(["macos", "macos:try"]), [("macos", "tests"), ("macos", "try")])
        self.assertEqual(build_release.required([]), [])
        for wrong in ("linux:all", "freebsd", "macos:", ":tests", "Linux"):
            if wrong == "macos:":
                continue                                                    # the same as macos alone
            with self.assertRaises(SystemExit, msg=wrong):
                build_release.required([wrong])
        text, problems = self.notes(self.results(failed=[("try", "ubuntu-24.04 / Python 3.9")]))
        self.assertEqual(problems, [])
        self.assertIn("| Linux | passed (3 of 3) | FAILED (1 of 2 passed) |", text)
        text, problems = self.notes(self.results(failed=[("tests", "ubuntu-24.04 / Python 3.15")]))
        self.assertEqual(problems, ["linux: the tests failed"])
        text, problems = self.notes(self.results(failed=[("tests", "windows-latest / Python 3.9"), ("try", "windows-latest / Python 3.9")]))
        self.assertEqual(problems, [])

    def test_the_notes_repeat_what_a_failed_job_reported_once_and_inside_a_block(self):
        same = "12 of 560 tests did not pass.\n12 x PermissionError: [WinError 32] in use: '<path>' (for one: tests.test_x.A.test_b)"
        nasty = "ended at once\n~~~~~~~~\n# not a heading\n<script>alert(1)</script> | a | b |\n" + "x" * 5000
        text, problems = self.notes(self.results(
            failed=[("tests", "windows-latest / Python 3.9"), ("tests", "windows-latest / Python 3.14"), ("try", "windows-latest / Python 3.9"),
                    ("try", "ubuntu-24.04 / Python 3.15")],
            said={("tests", "windows-latest / Python 3.9"): same, ("tests", "windows-latest / Python 3.14"): same,
                  ("try", "windows-latest / Python 3.9"): nasty}))
        self.assertEqual(problems, [])
        self.assertIn("What those jobs reported:", text)
        self.assertEqual(text.count("12 of 560 tests did not pass."), 1)        # two jobs said the same: shown once, under both names
        self.assertIn("windows-latest / Python 3.9; windows-latest / Python 3.14, the test suite:", text)
        self.assertIn("windows-latest / Python 3.9, starting the package:", text)
        self.assertNotIn("ubuntu-24.04 / Python 3.15, starting", text)          # it failed and said nothing: listed, not quoted
        self.assertIn("ubuntu-24.04 / Python 3.15", text.split("What those jobs reported:")[0])
        # What a job reported is test output. It stays inside its block, and the notes stay a readable length.
        fences = [line for line in text.split("\n") if line.startswith("~~~~")]
        self.assertEqual(fences, ["~~~~text", "~~~~"] * 2)
        self.assertIn("(cut here; the run has the rest)", text)
        self.assertLess(len(text), 6000)
        self.assertNotIn("What those jobs reported", self.notes(self.results())[0])
        self.assertEqual(build_release._fenced("a\n~~~~~~~~~\nb").count("~~~~"), 0)

    def test_a_job_that_left_no_result_counts_as_failed_not_as_absent(self):
        text, problems = self.notes(self.results(silent=[("tests", "windows-latest / Python 3.15")]))
        self.assertEqual(problems, [])
        self.assertIn("| Windows | FAILED (2 of 3 passed) | passed (2 of 2) |", text)
        self.assertIn("Windows: 1 tests job left no result", text)
        text, problems = self.notes(self.results(silent=[("tests", "ubuntu-24.04 / Python 3.15")]))
        self.assertEqual(problems, ["linux: the tests failed"])                 # required, and two of three is not "passed"
        self.assertIn("| Linux | FAILED (2 of 3 passed) | passed (2 of 2) |", text)
        text, problems = self.notes(self.results(silent=[("try", "macos-latest / Python 3.9")]))
        self.assertEqual(problems, ["macos: starting the package failed"])
        text, problems = self.notes([])                                         # nothing reported at all
        self.assertEqual(sorted(problems), ["linux: the tests failed", "macos: starting the package failed", "macos: the tests failed"])
        self.assertIn("| Windows | FAILED (0 of 3 passed) | FAILED (0 of 2 passed) |", text)

    def desktop(self, **outcomes):
        """Results of the desktop trials as the jobs write them: ``macos_arm64=True`` passed, ``False`` failed."""
        return [{"kind": "desktop", "label": system.replace("_", "-"), "ok": ok, "detail": "" if ok else "the window had not finished loading the page"}
                for system, ok in outcomes.items()]

    def installers(self, *systems):
        folder = self.tmp / "installers"
        folder.mkdir(exist_ok=True)
        for system in systems:
            ending = ".dmg" if system.startswith("macos") else "-setup.exe"
            (folder / ("learn-my-voice-0.2.0-beta.rc.7-%s%s" % (system, ending))).write_bytes(b"installer for " + system.encode())
        (folder / "learn-my-voice-0.2.0-beta.rc.6-macos-arm64.dmg").write_bytes(b"of another version")
        (folder / "desktop-macos-arm64.png").write_bytes(b"picture")
        (folder / "desktop-macos-arm64-trial.txt").write_bytes(b"the trial in words")
        (folder / "desktop-notes.txt").write_bytes(b"some other text file: not attached")
        return folder

    def test_nothing_is_said_about_the_desktop_app_when_no_desktop_job_ran(self):
        text, problems = self.notes(self.results())
        self.assertEqual(problems, [])
        self.assertNotIn("Desktop app", text)
        self.assertIn("### Get it", text)                                       # the zip is then the one way to get it

    def test_an_installer_is_offered_only_when_its_trial_passed(self):
        folder = self.installers("macos-arm64", "windows-x64")
        found = build_release.find_installers(folder, "0.2.0-beta.rc.7")
        self.assertEqual([entry["system"] for entry in found], ["macos-arm64", "windows-x64"])           # the other version's file is not taken
        self.assertEqual(found[0]["sha256"], hashlib.sha256(b"installer for macos-arm64").hexdigest())
        results = self.results() + self.desktop(macos_arm64=True, windows_x64=False)
        with mock.patch.object(build_release, "changelog_section", return_value=""):
            text, problems = build_release.release_notes(self.BUILD, "f" * 64, "learn-my-voice-0.2.0-beta.rc.7.zip", results,
                                                         ["macos", "linux:tests"], "", "", self.LEGS, found)
        self.assertEqual(problems, [])                                          # no installer is asked for in this call
        section = text[text.index("### Desktop app"):]
        # The installers come first: they are what a person who only wants to use the app takes. The zip follows.
        self.assertLess(text.index("### Desktop app"), text.index("### The zip, for a computer that has Python"))
        self.assertNotIn("### Get it", text)
        self.assertIn("| macOS, Apple chip | `learn-my-voice-0.2.0-beta.rc.7-macos-arm64.dmg` | passed |", section)
        self.assertIn("| Windows, 64-bit | not attached | FAILED: it was built, and the trial of it did not pass |", section)
        self.assertNotIn("windows-x64-setup.exe", section)                      # a file that failed its trial is not named as a download
        self.assertIn(found[0]["sha256"] + "  learn-my-voice-0.2.0-beta.rc.7-macos-arm64.dmg", section)
        self.assertNotIn(found[1]["sha256"], text)
        for said in ("no person has used it yet", "not signed", "Open Anyway", "仍要打开", "Run anyway", "仍要运行", "managed by an employer",
                     "To transcribe videos, the desktop app needs a Mac with an Apple chip", "not inside the desktop app",
                     "the page in the app's own window used itself through its own buttons", "`desktop-<system>-trial.txt`",
                     "an export is not a download: the app puts the file into the Downloads folder itself",
                     "a recording made in the desktop app has not been tried by anyone"):
            self.assertIn(said, section)
        self.assertIn("windows-x64, the desktop app:", text)                    # what the failed trial reported is quoted
        self.assertIn("the window had not finished loading the page", text)
        self.assertIn("| Windows | passed (3 of 3) | passed (2 of 2) |", text)   # the package's own table is not touched by it

    def test_a_release_can_need_an_installer_that_passed_for_a_system(self):
        folder = self.installers("macos-arm64", "macos-x64", "windows-x64")
        found = build_release.find_installers(folder, "0.2.0-beta.rc.7")
        none = "%s: no installer of the desktop app that passed its trial is there to attach"

        def problems(installers, **outcomes):
            with mock.patch.object(build_release, "changelog_section", return_value=""):
                return build_release.release_notes(self.BUILD, "f" * 64, "x.zip", self.results() + self.desktop(**outcomes),
                                                   ["macos", "linux:tests", "macos:desktop", "windows:desktop"], "", "", self.LEGS,
                                                   installers)[1]

        self.assertEqual(problems(found, macos_arm64=True, macos_x64=True, windows_x64=True), [])
        # Of the two for macOS one is enough: the other failed, or GitHub had no such Mac and its job never ran.
        self.assertEqual(problems(found, macos_arm64=False, macos_x64=True, windows_x64=True), [])
        self.assertEqual(problems(found, macos_x64=True, windows_x64=True), [])
        # A system with none that passed stops the release.
        self.assertEqual(problems(found, macos_arm64=True, macos_x64=True, windows_x64=False), [none % "windows"])
        self.assertEqual(problems(found, macos_arm64=False, macos_x64=False, windows_x64=True), [none % "macos"])
        self.assertEqual(problems(found), [none % "macos", none % "windows"])     # no desktop job reported anything
        # A trial that reported a pass is not enough when its file is not there to attach.
        without_windows = [entry for entry in found if entry["system"] != "windows-x64"]
        self.assertEqual(problems(without_windows, macos_arm64=True, macos_x64=True, windows_x64=True), [none % "windows"])
        self.assertEqual(problems([], macos_arm64=True, macos_x64=True, windows_x64=True), [none % "macos", none % "windows"])
        # A system named alone still stands for its tests and its trial of the package, not for the desktop app.
        self.assertEqual(build_release.required(["windows"]), [("windows", "tests"), ("windows", "try")])
        self.assertEqual(build_release.required(["windows:desktop"]), [("windows", "desktop")])

    def test_a_desktop_build_that_failed_is_said_and_no_first_open_steps_are_given_for_nothing(self):
        results = self.results() + [{"kind": "desktop", "label": "windows-x64", "ok": False, "detail": "The installer could not be built.\nerror[E0432]"}]
        with mock.patch.object(build_release, "changelog_section", return_value=""):
            text, _ = build_release.release_notes(self.BUILD, "f" * 64, "x.zip", results, ["macos"], "", "", self.LEGS, [])
        self.assertIn("| Windows, 64-bit | none | FAILED: it could not be built |", text)
        self.assertNotIn("Open Anyway. (", text)                                # nothing is offered, so nothing is explained
        self.assertIn("error[E0432]", text)

    def test_the_list_of_files_to_attach_holds_only_what_passed(self):
        folder = self.installers("macos-arm64", "windows-x64")
        results_dir = self.tmp / "results"
        results_dir.mkdir()
        for at, record in enumerate(self.results() + self.desktop(macos_arm64=True, windows_x64=False)):
            (results_dir / ("r%d.json" % at)).write_text(json.dumps(dict(record, outcome="success" if record["ok"] else "failure")), encoding="utf-8")
        package = self.tmp / "learn-my-voice-0.2.0-beta.rc.7.zip"
        with zipfile.ZipFile(str(package), "w") as archive:
            archive.writestr("learn-my-voice-0.2.0-beta.rc.7/studio/build.json", json.dumps(self.BUILD))
        Path(str(package) + ".sha256").write_text("x", encoding="utf-8")
        attach = self.tmp / "attach.txt"
        with mock.patch.object(build_release, "changelog_section", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            code = build_release.main(["notes", "--package", str(package), "--results", str(results_dir), "--require", "macos", "--require", "linux:tests",
                                       "--legs", "tests=3", "--legs", "try=2", "--installers", str(folder), "--attach", str(attach),
                                       "--out", str(self.tmp / "notes.md")])
        self.assertEqual(code, 0)
        names = [Path(line).name for line in attach.read_text(encoding="utf-8").split("\n") if line]
        self.assertEqual(names, ["learn-my-voice-0.2.0-beta.rc.7.zip", "learn-my-voice-0.2.0-beta.rc.7.zip.sha256",
                                 "learn-my-voice-0.2.0-beta.rc.7-macos-arm64.dmg", "learn-my-voice-0.2.0-beta.rc.7-macos-arm64.dmg.sha256",
                                 "desktop-macos-arm64.png", "desktop-macos-arm64-trial.txt"])
        self.assertTrue((folder / "learn-my-voice-0.2.0-beta.rc.7-macos-arm64.dmg.sha256").read_text(encoding="utf-8").startswith(
            hashlib.sha256(b"installer for macos-arm64").hexdigest()))

    def test_results_are_written_and_read_back_and_a_broken_file_is_a_failure(self):
        folder = self.tmp / "results"
        build_release.write_result(folder / "tests-a.json", "tests", "macos-latest / Python 3.9", "success")
        build_release.write_result(folder / "tests-b.json", "tests", "windows-latest  /  Python 3.9\n", "failure")
        build_release.write_result(folder / "tests-c.json", "tests", "ubuntu-24.04 / Python 3.9", "")           # the step never ran
        build_release.write_result(folder / "tests-d.json", "tests", "ubuntu-24.04 / Python 3.14", "cancelled")
        (folder / "try-broken.json").write_text("{not json", encoding="utf-8")
        (folder / "try-other.json").write_text(json.dumps({"kind": "something else", "label": "x"}), encoding="utf-8")
        found = {r["label"]: r["ok"] for r in build_release.read_results(folder)}
        self.assertEqual(found, {"macos-latest / Python 3.9": True, "windows-latest / Python 3.9": False, "ubuntu-24.04 / Python 3.9": False,
                                 "ubuntu-24.04 / Python 3.14": False, "try-broken": False, "try-other": False})
        self.assertEqual(build_release.read_results(self.tmp / "nothing-here"), [])
        with self.assertRaises(SystemExit):
            build_release.write_result(folder / "x.json", "deploy", "macos-latest", "success")
        # A label is repeated in the notes inside backticks, so it cannot hold one.
        (folder / "try-tick.json").write_text(json.dumps({"kind": "try", "label": "ubuntu `x` /  y", "ok": True}), encoding="utf-8")
        self.assertIn("ubuntu 'x' / y", [r["label"] for r in build_release.read_results(folder)])

    def test_a_job_that_left_no_result_is_written_down_as_a_failure_and_a_result_is_never_replaced(self):
        def result(*arguments):
            with contextlib.redirect_stdout(io.StringIO()):
                return build_release.main(["result", *arguments])

        there = self.tmp / "results" / "tests-a.json"
        there.parent.mkdir()
        mine = {"kind": "tests", "label": "windows-latest / Python 3.9", "ok": False, "detail": "3 of 560 tests did not pass.", "ran": 560}
        there.write_text(json.dumps(mine), encoding="utf-8")
        self.assertEqual(result("--kind", "tests", "--label", "windows-latest / Python 3.9", "--outcome", "failure", "--out", str(there), "--if-missing"), 0)
        self.assertEqual(json.loads(there.read_text(encoding="utf-8")), mine)                # what the tests wrote stays
        # No file: the step never got that far. Whatever GitHub called its outcome, nothing reported a pass.
        for outcome, written in (("success", "failure"), ("failure", "failure"), ("skipped", "skipped"), ("cancelled", "cancelled"), ("", "failure")):
            missing = self.tmp / "results" / ("none-%s.json" % (outcome or "empty"))
            self.assertEqual(result("--kind", "try", "--label", "macos-latest / Python 3.9", "--outcome", outcome, "--out", str(missing), "--if-missing"), 0)
            record = json.loads(missing.read_text(encoding="utf-8"))
            self.assertEqual((record["ok"], record["outcome"], record["kind"]), (False, written, "try"), outcome)
            self.assertIn("left no result", record["detail"])
        # A file that is there but is not a result is not kept.
        broken = self.tmp / "results" / "broken.json"
        broken.write_text("{cut", encoding="utf-8")
        result("--kind", "tests", "--label", "ubuntu-24.04 / Python 3.9", "--outcome", "failure", "--out", str(broken), "--if-missing")
        self.assertFalse(json.loads(broken.read_text(encoding="utf-8"))["ok"])
        # Without --if-missing it writes what it is told, as before.
        result("--kind", "tests", "--label", "macos-latest / Python 3.9", "--outcome", "success", "--out", str(there))
        self.assertTrue(json.loads(there.read_text(encoding="utf-8"))["ok"])
        # A workflow gives label, outcome and file through the environment; the tools are loaded anew to read it.
        from_env = self.tmp / "results" / "from-env.json"
        with mock.patch.dict(os.environ, {"RESULT_LABEL": "windows-latest / Python 3.15", "RESULT_OUTCOME": "cancelled", "RESULT_FILE": str(from_env)}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(load_tool("build_release").main(["result", "--kind", "try", "--if-missing"]), 0)
        self.assertEqual([json.loads(from_env.read_text(encoding="utf-8"))[key] for key in ("kind", "label", "ok", "outcome")],
                         ["try", "windows-latest / Python 3.15", False, "cancelled"])
        with mock.patch.dict(os.environ, {"RESULT_LABEL": "", "RESULT_FILE": ""}):
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                load_tool("build_release").main(["result", "--kind", "try", "--if-missing"])
        self.assertEqual([build_release.system_of(label) for label in ("macos-latest / Python 3.9", "windows-latest", "ubuntu-24.04 / x",
                                                                       "Linux / Python 3.9", "freebsd")],
                         ["macos", "windows", "linux", "linux", None])


class LaunchersTest(unittest.TestCase):
    def test_line_ends_are_fixed_for_the_files_that_depend_on_them(self):
        rules = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for rule in ("*.sh        text eol=lf", "*.command   text eol=lf", "*.bat       -text"):
            self.assertIn(rule, rules)
        bat = (ROOT / "Start Learn My Voice.bat").read_bytes()
        self.assertEqual(bat.count(b"\n"), bat.count(b"\r\n"))
        for name in ("Start Learn My Voice.command", "启动工作台.command", "start-learn-my-voice.sh"):
            self.assertNotIn(b"\r", (ROOT / name).read_bytes(), name)

    def test_each_launcher_says_what_to_do_when_python_is_missing_in_both_languages(self):
        for name in ("Start Learn My Voice.bat", "start-learn-my-voice.sh"):
            text = (ROOT / name).read_bytes().decode("utf-8")
            self.assertIn("Python 3.9 or newer is needed", text, name)
            self.assertIn("需要 Python 3.9 或更新的版本", text, name)
            self.assertIn("sys.version_info >= (3, 9)", text, name)             # a Python that is too old is not used

    def test_the_trial_knows_a_launcher_for_each_system_the_notes_name(self):
        self.assertEqual({command[-1] for command in try_release.LAUNCHERS.values()}, set(build_release.LAUNCHERS.values()))
        for name in build_release.LAUNCHERS.values():
            self.assertTrue((ROOT / name).is_file(), name)


class WorkflowsTest(unittest.TestCase):
    """What must hold of the workflow files, read as text. Whether GitHub runs them as meant is seen on GitHub only."""

    def setUp(self):
        self.files = {path.name: path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))}

    def test_there_are_the_two_workflows(self):
        self.assertEqual(sorted(self.files), ["release-candidate.yml", "tests.yml"])

    def test_every_action_is_pinned_to_a_commit_and_comes_from_github_itself(self):
        for name, text in self.files.items():
            used = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", text, re.M)
            self.assertTrue(used, name)
            for action in used:
                if action.startswith("./"):
                    continue                                                    # a workflow of this repository
                self.assertRegex(action, r"^actions/[a-z-]+@[0-9a-f]{40}$", "%s: %s" % (name, action))

    def test_only_the_publishing_job_may_write(self):
        for name, text in self.files.items():
            self.assertRegex(text, r"(?m)^permissions:\n  contents: read\n", name)       # the default for every job
            self.assertNotIn("pull_request_target", text, name)
            self.assertNotIn("secrets.", text, name)                            # nothing but the run's own token
        release = self.files["release-candidate.yml"]
        self.assertEqual(len(re.findall(r"contents: write", release)), 1)
        publish = release[release.index("\n  publish:"):]
        self.assertIn("contents: write", publish)
        self.assertEqual(re.findall(r"^\s+(\w[\w-]*): write", release, re.M), ["contents"])
        self.assertEqual(re.findall(r"^\s+(\w[\w-]*): write", self.files["tests.yml"], re.M), [])
        self.assertEqual(re.findall(r"^[ \t]*permissions:", self.files["tests.yml"], re.M), ["permissions:"])     # only the one at the top

    def test_no_text_from_a_push_is_written_into_a_command(self):
        # A branch name is chosen by whoever pushes. In a `run:` line it would be read as part of the command;
        # through `env:` it is only ever a value.
        for name, text in self.files.items():
            lines = text.split("\n")
            for at, line in enumerate(lines):
                found = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
                if not found:
                    continue
                body = [found.group(2)]
                indent = len(found.group(1))
                for later in lines[at + 1:]:
                    if later.strip() and len(later) - len(later.lstrip()) <= indent:
                        break
                    body.append(later)
                self.assertNotIn("${{", "\n".join(body), "%s line %d" % (name, at + 1))

    def test_a_beta_branch_builds_a_release_candidate_and_runs_the_tests_once(self):
        release, tests = self.files["release-candidate.yml"], self.files["tests.yml"]
        self.assertIn('branches: ["beta/**"]', release)
        self.assertIn("uses: ./.github/workflows/tests.yml", release)
        self.assertIn("workflow_call:", tests)
        self.assertIn('branches-ignore: ["beta/**"]', tests)
        self.assertIn("needs: [tests, build, try, desktop]", release)           # nothing is published before all of them
        self.assertIn("--require macos --require linux:tests --require windows:tests", release)
        self.assertIn("--require macos:desktop --require windows:desktop", release)    # something to install, for each
        self.assertIn("--prerelease", release)
        # A later push does not stop a release-candidate run, nor the tests inside it.
        self.assertNotIn("cancel-in-progress", release)
        self.assertIn("startsWith(github.ref, 'refs/heads/beta/') && github.run_id || 'latest'", tests)
        self.assertIn("persist-credentials: false", release)
        self.assertEqual(release.count("actions/checkout@"), release.count("persist-credentials: false"))
        # The numbers of jobs the notes are told to expect are the numbers the two matrices make.
        self.assertIn("--legs tests=3 --legs try=2", release)
        self.assertIn('python: ["3.9", "3.14", "3.15"]', tests)
        self.assertIn('python: ["3.9", "3.15"]', release)

    def test_a_failure_counts_where_the_suite_is_known_to_pass(self):
        # The suite has passed on all three on GitHub's machines. `experimental` is what would let a job fail.
        tests = self.files["tests.yml"]
        allowed = dict(re.findall(r"- os: (\S+)\n\s+experimental: (true|false)", tests))
        self.assertEqual(allowed, {"macos-15-intel": "false", "ubuntu-24.04": "false", "windows-latest": "false"})
        self.assertIn("continue-on-error: ${{ matrix.experimental }}", tests)
        trial = dict(re.findall(r"- os: (\S+)\n\s+experimental: (true|false)", self.files["release-candidate.yml"]))
        self.assertEqual(trial["macos-15-intel"], "false")
        # Whatever GitHub calls its Macs, the notes file a job under macOS by how its name starts.
        for runner in list(allowed) + ["macos-latest", "macos-26", "macos-15"]:
            self.assertEqual(build_release.system_of(runner + " / Python 3.9"), {"m": "macos", "w": "windows", "u": "linux"}[runner[0]])

    def test_every_job_that_checks_something_leaves_a_result_whatever_happens(self):
        for name, kind in (("tests.yml", "tests"), ("release-candidate.yml", "try")):
            text = self.files[name]
            fallback = "run: python tools/build_release.py result --kind %s --if-missing\n" % kind      # never replaces what the job wrote
            self.assertEqual(text.count(fallback), 1, name)
            step = text[:text.index(fallback)].split("- name:")[-1]
            self.assertIn("if: ${{ always() }}", step, name)
            for given in ("RESULT_LABEL: ", "RESULT_OUTCOME: ${{ steps.", "RESULT_FILE: results/%s-" % kind):
                self.assertIn(given, step, name)
        # Where a step runs on all three systems its command holds no variable: PowerShell and bash read those differently.
        for name in self.files:
            for line in self.files[name].split("\n"):
                if re.match(r"\s*run: python tools/(run_tests|try_release)\.py", line) or "result --kind" in line:
                    self.assertNotIn("$", line, name)
        self.assertIn("run: python tools/try_release.py\n", self.files["release-candidate.yml"])
        self.assertNotIn("defaults:", self.files["release-candidate.yml"])       # no shell is forced on the jobs that run everywhere
        uploads = re.findall(r"uses: actions/upload-artifact@.*\n((?:\s+.*\n)+?)(?=\s*- |\n|\Z)", "".join(self.files.values()))
        self.assertEqual(len(uploads), 5)
        for block in uploads:
            self.assertIn("overwrite: true", block)
            # Every upload has to find its files, but one: a desktop build that failed has made no installer.
            self.assertIn("if-no-files-found: ignore" if "name: installer-" in block else "if-no-files-found: error", block)

    def test_the_commands_the_workflows_run_exist(self):
        both = self.files["release-candidate.yml"] + self.files["tests.yml"]
        for command in ("build", "verify", "result", "notes"):
            self.assertIn("python tools/build_release.py %s" % command, both)
            with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stdout(io.StringIO()):
                build_release.main([command, "--help"])
            self.assertEqual(stopped.exception.code, 0)
        self.assertIn("python tools/try_release.py", self.files["release-candidate.yml"])
        self.assertIn("run: python tools/run_tests.py\n", self.files["tests.yml"])
        self.assertNotIn("unittest discover", "\n".join(line for line in both.split("\n") if not line.lstrip().startswith("#")))
        for tool in ("build_release", "try_release", "run_tests"):
            self.assertTrue((ROOT / "tools" / (tool + ".py")).is_file())
        for script in ("build", "try_installer"):
            self.assertIn("run: python desktop/%s.py\n" % script, self.files["release-candidate.yml"])
            self.assertTrue((ROOT / "desktop" / (script + ".py")).is_file())

    def test_a_release_needs_an_installer_for_macos_and_windows_and_attaches_only_what_passed_its_trial(self):
        release = self.files["release-candidate.yml"]
        desktop = release[release.index("\n  desktop:"):release.index("\n  publish:")]
        self.assertIn("continue-on-error: true", desktop)                       # the publish job decides, from what was written down
        self.assertIn("needs: build", desktop)                                  # built around the very package that is published
        self.assertIn("DESKTOP_PACKAGE: dist/${{ needs.build.outputs.file }}", desktop)
        self.assertEqual(desktop.count("run: python tools/build_release.py result --kind desktop --if-missing\n"), 1)
        self.assertNotIn("contents: write", desktop)
        publish = release[release.index("\n  publish:"):]
        self.assertIn("--installers installers --attach attach.txt", publish)
        self.assertIn('gh release create "$TAG" "${FILES[@]}"', publish)        # only what the notes step listed is attached
        self.assertIn("--require macos:desktop --require windows:desktop", publish)
        self.assertNotIn("linux:desktop", release)                              # there is no desktop app for Linux yet
        # A desktop job that GitHub cancelled for want of a machine does not stop the publish job from running:
        # it waits for all four, and runs when the three that every candidate needs succeeded.
        self.assertIn("    needs: [tests, build, try, desktop]\n", publish)
        self.assertIn("    if: ${{ always() && needs.tests.result == 'success' && needs.build.result == 'success' "
                      "&& needs.try.result == 'success' }}\n", publish)
        self.assertEqual(release.count("always() && needs."), 1)                # only the publish job is given that


class RunTestsTest(TempCase):
    """The runner that says which tests did not pass. A run's log on GitHub cannot be read without signing in;
    what this prints as annotations, and writes to the result file, can."""

    @staticmethod
    def sample():
        # Made here, inside a function, so that looking for tests never finds it: three of these are meant to fail.
        class Sample(unittest.TestCase):
            def test_passes(self):
                pass

            def test_fails(self):
                self.assertEqual("这周又去超市", "这周去超市", "the two lines differ")

            def test_cannot_remove_a(self):
                raise PermissionError(13, "The process cannot access the file", "C:\\Users\\runner\\Temp\\tmpab12\\studio.db")

            def test_cannot_remove_b(self):
                raise PermissionError(13, "The process cannot access the file", "C:\\Users\\runner\\Temp\\tmpzz99\\studio.db")

            @unittest.skip("not on this system")
            def test_skipped(self):
                pass

            @unittest.expectedFailure
            def test_was_expected_to_fail(self):
                pass

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Sample).run(result)
        return result

    def test_what_went_wrong_is_the_line_that_names_it(self):
        self.assertEqual(run_tests.cause_of('Traceback (most recent call last):\n  File "x.py", line 3, in test\n    self.assertEqual(1, 2)\n'
                                            '    ~~~~~~~~~~~~~~~~^^^^^^\nAssertionError: 1 != 2\n'), "AssertionError: 1 != 2")
        chained = ('Traceback (most recent call last):\n  File "a.py", line 1, in f\n    open(p)\nFileNotFoundError: no such file\n\n'
                   'During handling of the above exception, another exception occurred:\n\n'
                   'Traceback (most recent call last):\n  File "b.py", line 9, in g\n    raise ApiError(500)\nstudio.server.ApiError: could not read\n')
        self.assertEqual(run_tests.cause_of(chained), "studio.server.ApiError: could not read")
        several = 'Traceback (most recent call last):\n  File "x.py", line 3, in t\n    self.assertEqual(a, b)\nAssertionError: Lists differ\n\nFirst differing element 0:\n1\n2\n'
        self.assertEqual(run_tests.cause_of(several), "AssertionError: Lists differ")
        self.assertEqual(run_tests.cause_of(""), "no reason was given")
        self.assertLessEqual(len(run_tests.cause_of("Traceback (most recent call last):\nValueError: " + "x" * 900)), 300)

    def test_the_same_trouble_with_another_file_or_number_is_one_cause(self):
        key = run_tests.cause_key
        self.assertEqual(key("PermissionError: [WinError 32] in use: 'C:\\Users\\r\\Temp\\tmpab12\\studio.db'"),
                         key("PermissionError: [WinError 32] in use: 'C:\\Users\\r\\Temp\\tmpzz99\\other.db'"))
        self.assertEqual(key("OSError: [Errno 39] Directory not empty: '/tmp/tmp1/data'"), key("OSError: [Errno 39] Directory not empty: '/var/x/tmp2/data'"))
        self.assertEqual(key("AssertionError: 438 != 384"), key("AssertionError: 420 != 384"))
        self.assertEqual(key("<studio.store.Store object at 0x10fa3b2e0>"), key("<studio.store.Store object at 0x7ffee1c0>"))
        self.assertNotEqual(key("AssertionError: 1 != 2"), key("AssertionError: 'a' != 'b'"))
        self.assertNotEqual(key("PermissionError: in use"), key("FileNotFoundError: in use"))
        self.assertEqual(key("AssertionError: macos-latest / Python 3.9 failed"), "AssertionError: macos-latest / Python N.N failed")   # a lone slash is no path

    def test_what_did_not_pass_is_collected_and_put_by_cause_the_most_frequent_first(self):
        result = self.sample()
        self.assertEqual(result.testsRun, 6)
        problems = run_tests.collect(result)
        self.assertEqual(sorted((p["test"].rsplit(".", 1)[1], p["kind"]) for p in problems),
                         [("test_cannot_remove_a", "error"), ("test_cannot_remove_b", "error"), ("test_fails", "failure"),
                          ("test_was_expected_to_fail", "unexpected success")])
        groups = run_tests.grouped(problems)
        self.assertEqual([group["count"] for group in groups], [2, 1, 1])
        self.assertTrue(groups[0]["cause"].startswith("PermissionError: [Errno 13] The process cannot access the file"), groups[0]["cause"])
        self.assertEqual(len(groups[0]["tests"]), 2)
        self.assertIn("Traceback", groups[0]["trace"])
        text = run_tests.detail(result.testsRun, groups)
        self.assertTrue(text.startswith("4 of 6 tests did not pass.\n2 x PermissionError: "), text)
        self.assertIn("(for one: test_release.", text)                          # "tests." is left off: every name starts with it
        self.assertEqual(run_tests.detail(6, []), "")
        self.assertEqual(run_tests.detail(0, []), "No test ran.")
        long = run_tests.detail(900, [{"cause": "ValueError: %d %s" % (n, "y" * 200), "count": 1, "tests": ["tests.t.T.test_%d" % n], "trace": ""} for n in range(60)])
        self.assertLessEqual(len(long), run_tests.MAX_DETAIL)
        self.assertTrue(long.endswith("(and more)"))

    def test_the_result_file_is_one_the_release_notes_can_read(self):
        result = self.sample()
        found = run_tests.record("windows-latest  /  Python 3.9\n", result, run_tests.grouped(run_tests.collect(result)))
        self.assertEqual((found["kind"], found["label"], found["ok"], found["outcome"], found["ran"], found["not_passed"], found["skipped"]),
                         ("tests", "windows-latest / Python 3.9", False, "failure", 6, 4, 1))
        self.assertEqual(found["causes"][0]["count"], 2)
        path = self.tmp / "results" / "tests-w.json"
        path.parent.mkdir()
        path.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
        read = build_release.read_results(path.parent)
        self.assertEqual([(r["kind"], r["label"], r["ok"]) for r in read], [("tests", "windows-latest / Python 3.9", False)])
        self.assertTrue(read[0]["detail"].startswith("4 of 6 tests did not pass."))
        self.assertTrue(build_release.result_is_written(path))
        # Everything passed: a pass, with nothing to add. Nothing ran: not a pass.
        passed = unittest.TestResult()
        passed.testsRun = 560
        self.assertEqual([run_tests.record("macos-latest / Python 3.9", passed, [])[key] for key in ("ok", "outcome", "detail", "not_passed")],
                         [True, "success", "", 0])
        self.assertEqual([run_tests.record("x", unittest.TestResult(), [])[key] for key in ("ok", "detail")], [False, "No test ran."])

    def test_on_github_what_failed_is_printed_where_it_can_be_read_without_signing_in(self):
        result = self.sample()
        groups = run_tests.grouped(run_tests.collect(result))
        lines = run_tests.annotations("windows-latest / Python 3.9", result.testsRun, groups)
        self.assertEqual(len(lines), 1 + 3)                                     # how many, then one for each cause
        for line in lines:
            self.assertTrue(line.startswith("::error title="), line[:40])
            self.assertNotIn("\n", line)                                        # a command is one line, or GitHub reads the rest as text
            self.assertNotIn("\r", line)
            self.assertLessEqual(len(line), 200 * 3 + run_tests.MAX_MESSAGE * 3 + 20)
        self.assertTrue(lines[0].startswith("::error title=Tests on windows-latest / Python 3.9%3A 4 of 6 did not pass::4 of 6 tests did not pass.%0A%0AAll of them: "),
                        lines[0][:160])                                         # in a message a colon stays a colon
        names, _, causes = lines[0].partition("%0A%0ABy cause:%0A")
        for test in ("test_cannot_remove_a", "test_cannot_remove_b", "test_fails", "test_was_expected_to_fail"):
            self.assertIn(test, names)                                          # every test by name, before the causes
        self.assertTrue(causes.startswith("2 x PermissionError: "), causes[:60])
        long = [{"cause": "ValueError: %s" % ("y" * 900), "count": 1, "tests": ["tests.test_x.T.test_%d" % n],
                 "trace": "Traceback (most recent call last):\n  File \"x.py\", line 1, in t\nValueError: %s\n" % ("y" * 900)} for n in range(3)]
        cut = run_tests.annotations("x", 9, long)[0]
        self.assertIn("test_x.T.test_0, test_x.T.test_1, test_x.T.test_2", cut)
        self.assertNotIn("tests.test_x", cut.split("%0A%0ABy cause")[0])        # without the "tests." each name starts with
        self.assertIn(long[0]["cause"][:run_tests.MAX_CAUSE] + "... (for one", cut)     # a long cause is cut in the list; its own annotation has more
        self.assertIn("y" * 150, run_tests.annotations("x", 9, long)[1])
        self.assertTrue(lines[1].startswith("::error title=2 x PermissionError%3A "), lines[1][:80])
        self.assertIn("%0ATraceback (most recent call last):%0A", lines[1])
        self.assertIn("这周又去超市", "".join(lines))                              # text in a message is kept as written
        # Never more than GitHub shows for one step; nothing at all when everything passed; one line when nothing ran.
        many = [{"cause": "ValueError: cause %s" % chr(65 + n), "count": 30 - n, "tests": ["tests.t.T.test_%d" % n], "trace": "Traceback\nValueError"} for n in range(26)]
        self.assertEqual(len(run_tests.annotations("x", 500, many)), run_tests.MAX_ANNOTATIONS)
        self.assertEqual(run_tests.annotations("x", 560, []), [])
        self.assertEqual(len(run_tests.annotations("x", 0, [])), 1)
        self.assertIn("none ran", run_tests.annotations("x", 0, [])[0])
        # Signs that mean something to GitHub in a command are written as their codes.
        self.assertEqual(run_tests.annotation("a: b, c", "100% sure\r\nnext ::warning::x"), "::error title=a%3A b%2C c::100%25 sure%0D%0Anext ::warning::x")
        self.assertEqual(try_release.annotation("a: b, c", "100% sure\r\nnext"), "::error title=a%3A b%2C c::100%25 sure%0D%0Anext")


if __name__ == "__main__":
    unittest.main()
