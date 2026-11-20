"""Tests for the desktop app: what it relies on in the core, what goes into it, and what its files must agree on.

The desktop app is a small shell (desktop/src-tauri, in Rust): it starts the core as its child, shows the page
the core serves, and goes away. Covered here, on any system and without building anything:

- the core's side: it takes any free port when asked, says where it listens, and stops when the one who
  started it is gone;
- desktop/build.py: what is put inside the app (the release package and a Python), and that a download is
  used only when it is the file whose SHA-256 is written down;
- the shell's files: that they agree with each other and with the scripts, and that the page is granted nothing.

The shell itself cannot be compiled or run by these tests. Whether it works is seen where it is built and
tried, on GitHub's machines (desktop/try_installer.py).
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
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from studio import buildinfo, server, transcribe
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
SHELL = DESKTOP / "src-tauri"


def load(name):
    spec = importlib.util.spec_from_file_location("desktop_" + name, str(DESKTOP / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load("build")
try_installer = load("try_installer")


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


def answers(port, data_dir=None):
    try:
        headers = {}
        if data_dir is not None:
            token_path = Path(data_dir) / "server-token"
            if token_path.is_file():
                headers["X-Studio-Token"] = token_path.read_text(encoding="utf-8").strip()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/state", headers=headers)
        status = conn.getresponse().status
        conn.close()
        return status == 200
    except OSError:
        return False


class StopsWithItsParentTest(TempCase):
    def test_the_core_stops_once_its_input_has_ended_and_not_before(self):
        httpd = server.make_server(0, Store(self.tmp / "data"))
        self.addCleanup(httpd.server_close)
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        read_end, write_end = os.pipe()
        stream = os.fdopen(read_end, "rb")
        self.addCleanup(stream.close)
        watcher = threading.Thread(target=server._stop_when_input_ends, args=(httpd, stream), daemon=True)
        watcher.start()
        port = httpd.server_address[1]
        time.sleep(0.3)
        self.assertTrue(serving.is_alive())                                      # the input is open: nothing happens
        self.assertTrue(answers(port))
        os.write(write_end, b"anything")                                         # what is written to it is not a command
        time.sleep(0.2)
        self.assertTrue(serving.is_alive())
        os.close(write_end)                                                      # the one who held it is gone
        serving.join(10)
        self.assertFalse(serving.is_alive())
        watcher.join(5)
        self.assertFalse(watcher.is_alive())

    def test_no_input_at_all_counts_as_its_end(self):
        for stream in (None, open(os.devnull, "rb")):
            httpd = server.make_server(0, Store(self.tmp / ("data-%s" % (stream is None))))
            serving = threading.Thread(target=httpd.serve_forever, daemon=True)
            serving.start()
            server._stop_when_input_ends(httpd, stream)
            serving.join(10)
            self.assertFalse(serving.is_alive())
            httpd.server_close()
            if stream is not None:
                stream.close()

    def start(self, *options, stdin):
        """The core as the shell starts it: its own process, any free port, its output read line by line."""
        env = dict(os.environ, STUDIO_DATA_DIR=str(self.tmp / "data"), PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-m", "studio.server", "--no-browser", "--port", "0", *options],
                                   cwd=str(ROOT), env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(process.stdout.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def address(self, process):
        """The address in the first line the core prints, as the shell reads it."""
        line = process.stdout.readline().decode("utf-8", "replace")
        self.assertIn("http://127.0.0.1:", line)
        text = line[line.index("http://127.0.0.1:"):].split()[0]
        self.assertTrue(text.endswith("/"), text)
        port = int(text[len("http://127.0.0.1:"):-1])
        self.assertGreater(port, 1023)                                           # port 0 was asked for: any free one was taken
        return port

    def test_started_as_the_shell_starts_it_the_core_ends_when_the_shell_is_gone(self):
        process = self.start("--exit-with-parent", stdin=subprocess.PIPE)
        port = self.address(process)
        self.assertFalse(answers(port))                                          # unauthenticated local probe is refused
        self.assertTrue(answers(port, self.tmp / "data"))
        self.assertIsNone(process.poll())
        process.stdin.close()                                                    # what happens when the shell ends, however it ends
        self.assertEqual(process.wait(15), 0)
        self.assertFalse(answers(port, self.tmp / "data"))
        self.assertTrue((self.tmp / "data").is_dir())                            # its data is where it was told to keep it

    def test_without_that_option_closed_input_does_not_stop_it(self):
        process = self.start(stdin=subprocess.DEVNULL)                           # how a launcher or a terminal starts it
        port = self.address(process)
        time.sleep(0.5)
        self.assertIsNone(process.poll())
        self.assertTrue(answers(port, self.tmp / "data"))
        process.terminate()
        process.wait(15)


class ShellFilesTest(unittest.TestCase):
    """What the shell's files must agree on. None of this shows that the shell works; it shows that the parts fit."""

    def setUp(self):
        self.config = json.loads((SHELL / "tauri.conf.json").read_text(encoding="utf-8"))
        self.rust = (SHELL / "src" / "main.rs").read_text(encoding="utf-8")
        self.cargo = (SHELL / "Cargo.toml").read_text(encoding="utf-8")

    def test_the_names_are_the_same_everywhere(self):
        self.assertEqual(self.config["identifier"], try_installer.IDENTIFIER)
        self.assertEqual(self.config["productName"], try_installer.PRODUCT)
        self.assertEqual(self.config["mainBinaryName"], try_installer.BINARY)
        self.assertIn('name = "%s"' % try_installer.BINARY, self.cargo)
        self.assertIn('const RUNNING_FILE: &str = "%s";' % try_installer.RUNNING_FILE, self.rust)
        self.assertIn('const PROBLEM_FILE: &str = "%s";' % try_installer.PROBLEM_FILE, self.rust)
        self.assertRegex(self.config["identifier"], r"^[a-z0-9.-]+$")
        self.assertFalse(self.config["identifier"].endswith(".app"))            # macOS takes that ending for something else

    def test_the_page_is_granted_nothing(self):
        # The window shows a page served on this computer. It must get no way into the shell: no command is
        # offered, no permission file exists, and the bridge to the shell is not put into the page.
        self.assertNotIn("invoke_handler", self.rust)
        self.assertNotIn("tauri::command", self.rust)
        self.assertFalse((SHELL / "capabilities").exists())
        self.assertIs(self.config["app"]["withGlobalTauri"], False)
        self.assertEqual(self.config["app"]["windows"], [])                     # the shell opens its one window itself, on the core's address
        self.assertEqual(
            self.config["app"]["security"]["csp"],
            "default-src 'none'; script-src 'self'; style-src 'self'; base-uri 'none'; form-action 'none'",
        )
        self.assertNotIn('"data"', self.rust.split(".on_navigation")[1].split(".on_download")[0])
        self.assertNotIn('"blob"', self.rust.split(".on_navigation")[1].split(".on_download")[0])
        self.assertNotIn("plugins", self.config)
        wanted = re.findall(r"^([a-z][a-z0-9_-]*) = ", self.cargo.split("[dependencies]")[1].split("[profile")[0], re.M)
        self.assertEqual(wanted, ["tauri", "tauri-plugin-single-instance", "open", "serde_json"])       # a new one is a decision, not a habit
        self.assertIn('tauri = { version = "2.12", features = [] }', self.cargo)

    def test_the_core_is_started_closed_off_and_tied_to_the_shell(self):
        arguments = re.search(r"const CORE_ARGUMENTS: \[&str; (\d+)\] = \[(.*?)\];", self.rust, re.S)
        given = re.findall(r'"([^"]+)"', arguments.group(2))
        self.assertEqual(len(given), int(arguments.group(1)))
        self.assertEqual(given, ["-X", "utf8", "-B", "-u", "-E", "-s", "-m", "studio.server", "--no-browser", "--port", "0", "--exit-with-parent"])
        core = (ROOT / "studio" / "server.py").read_text(encoding="utf-8")
        for option in ("--no-browser", "--port", "--exit-with-parent"):
            self.assertIn('"%s"' % option, core)                                # every option it passes is one the core has
        self.assertIn('.env("STUDIO_DATA_DIR", data)', self.rust)
        self.assertIn(".stdin(Stdio::piped())", self.rust)                      # held open: what --exit-with-parent watches
        self.assertIn('url.host_str() == Some("127.0.0.1")', self.rust)         # no other address is ever shown in the window
        self.assertIn('"LEARN_MY_VOICE_DATA_DIR"', self.rust)
        self.assertIn("app_data_dir()", self.rust)                              # data is kept outside the app, so an update keeps it

    def test_what_is_bundled_is_the_core_folder_and_the_icons(self):
        bundle = self.config["bundle"]
        self.assertEqual(bundle["resources"], ["core/"])
        self.assertEqual(bundle["macOS"]["signingIdentity"], "-")               # signed with no certificate: there is none
        self.assertEqual(bundle["windows"]["nsis"]["installMode"], "currentUser")       # installs without administrator rights
        starts = {".png": b"\x89PNG\r\n\x1a\n", ".icns": b"icns", ".ico": b"\x00\x00\x01\x00"}
        for name in bundle["icon"]:
            data = (SHELL / name).read_bytes()
            self.assertTrue(data.startswith(starts[Path(name).suffix]), name)
        self.assertEqual((SHELL / "icons" / "icon.ico").read_bytes()[4:6], b"\x06\x00")       # six sizes
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for made in ("desktop/src-tauri/core/", "desktop/src-tauri/target/"):
            self.assertIn(made, ignored)

    def test_the_rust_packages_are_the_ones_on_the_list_in_the_source(self):
        # Cargo.lock is the list of every Rust package and version the shell is compiled from. It is part of the
        # source, and the build tells Cargo to use it as it is.
        lock = (SHELL / "Cargo.lock").read_text(encoding="utf-8")
        self.assertNotIn("\r", lock)
        listed = dict(re.findall(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock))
        self.assertEqual(listed[try_installer.BINARY], re.search(r'^version = "([^"]+)"', self.cargo, re.M).group(1))
        wanted = re.findall(r"^([a-z][a-z0-9_-]*) = ", self.cargo.split("[dependencies]")[1].split("[profile")[0], re.M)
        for name in wanted + ["tauri-build"]:
            self.assertIn(name, listed)
        self.assertTrue(listed["tauri"].startswith("2.12."), listed["tauri"])
        self.assertIn("*.lock      text eol=lf\n", (ROOT / ".gitattributes").read_text(encoding="utf-8"))    # the same bytes on Windows
        self.assertNotIn("Cargo.lock", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        override = SHELL / "release.conf.json"
        plain = build.tauri_command("npx", "dmg", override, locked=False)
        self.assertEqual(plain, ["npx", "--yes", "--ignore-scripts", build.TAURI_CLI, "build", "--ci", "--bundles", "dmg", "--config", str(override)])
        self.assertEqual(build.tauri_command("npx", "dmg", override, locked=True), plain + ["--", "--locked"])
        self.assertRegex(build.TAURI_CLI, r"^@tauri-apps/cli@\d+\.\d+\.\d+$")     # the tool that builds is one exact version too

    def test_the_page_inside_the_shell_loads_nothing_from_elsewhere(self):
        for path in sorted((DESKTOP / "ui").iterdir()):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"https?://|//[a-z]", text), path.name)
            self.assertNotIn("innerHTML", text, path.name)                      # the reason for a failed start is shown as text


class KnowsItIsTheDesktopAppTest(TempCase):
    """Inside the desktop app the core knows where it is, from the file the desktop build writes next to the app
    folder. Two things depend on it: what is said about transcription, and what an AI app is told to start."""

    RUNTIME = {"python": "3.14.10", "from": "https://example.test/python.tar.gz", "sha256": "a" * 64}

    def runtime(self, content=None):
        path = self.tmp / "runtime.json"
        path.write_text(json.dumps(self.RUNTIME if content is None else content), encoding="utf-8")
        return path

    def test_the_file_the_build_writes_is_the_file_the_core_reads(self):
        self.assertEqual(buildinfo.RUNTIME_FILE, ROOT.parent / "runtime.json")   # beside the app folder: core/runtime.json, core/app
        text = (DESKTOP / "build.py").read_text(encoding="utf-8")
        self.assertIn('build = put_app(package, CORE / "app")', text)
        self.assertIn('(CORE / "runtime.json").write_bytes((json.dumps({"python": PYTHON, "from": runtime["python"], "sha256": runtime["sha256"]}', text)
        for system, runtime in build.SYSTEMS.items():
            written = {"python": build.PYTHON, "from": runtime["python"], "sha256": runtime["sha256"]}
            self.assertEqual(buildinfo.desktop(self.runtime(written)), {"python": build.PYTHON}, system)

    def test_a_file_that_is_not_that_one_is_not_taken_for_it(self):
        self.assertIsNone(buildinfo.desktop(self.tmp / "none.json"))             # from the zip or the source there is none
        self.assertIsNone(buildinfo.desktop(self.tmp))                           # a folder where the file would be
        for wrong in ([], "text", {}, {"python": "3.14.10"}, dict(self.RUNTIME, python="3.14"), dict(self.RUNTIME, python=314),
                      dict(self.RUNTIME, sha256="a" * 63), dict(self.RUNTIME, **{"from": "http://example.test/python"}),
                      dict(self.RUNTIME, more="x" * 3000)):
            self.assertIsNone(buildinfo.desktop(self.runtime(wrong)), str(wrong)[:80])
        self.runtime().write_bytes(b"{not json")
        self.assertIsNone(buildinfo.desktop(self.tmp / "runtime.json"))

    def test_what_the_trial_asks_only_of_the_desktop_app(self):
        store = Store(self.tmp / "data")
        httpd = server.make_server(0, store)
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), serving.join(5)))
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        data = self.tmp / "data"

        def state():
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            conn.request("GET", "/api/state")
            out = json.loads(conn.getresponse().read().decode("utf-8"))
            conn.close()
            return out

        # Started from the source the core does not claim to be the desktop app, and the trial fails on exactly that.
        self.assertIsNone(state()["desktop"])
        with self.assertRaises(try_installer.Failed) as refused:
            try_installer.as_the_desktop_app(base, data, "3.14.10", [])
        self.assertIn("does not say that it is the desktop app", str(refused.exception))

        with mock.patch.object(buildinfo, "RUNTIME_FILE", self.runtime()):
            self.assertEqual(state()["desktop"], {"python": "3.14.10"})
            # An AI app is told the data folder in use, so that it does not open one inside the app.
            mcp = state()["mcp"]
            self.assertEqual(os.path.realpath(mcp["data_dir"]), os.path.realpath(str(data)))
            self.assertEqual(Path(mcp["script"]), ROOT / "run_mcp.py")
            # A core that still sends people to the launcher fails the trial.
            with mock.patch.object(transcribe, "_platform_problem", lambda: "python_version"):
                with self.assertRaises(try_installer.Failed) as refused:
                    try_installer.as_the_desktop_app(base, data, "3.14.10", [])
            self.assertIn("without naming a launcher", str(refused.exception))
            # So does one that would set transcription up inside the app (from the source that is where it goes).
            with mock.patch.object(transcribe, "_platform_problem", lambda: "desktop_app"):
                with self.assertRaises(try_installer.Failed) as refused:
                    try_installer.as_the_desktop_app(base, data, "3.14.10", [])
            self.assertIn("not beside the data folder", str(refused.exception))
            with mock.patch.object(transcribe, "_platform_problem", lambda: "desktop_app"), \
                    mock.patch.object(transcribe, "HOME", self.tmp / "whisper"):
                # Another data folder than the one the window uses is noticed.
                with self.assertRaises(try_installer.Failed) as refused:
                    try_installer.as_the_desktop_app(base, self.tmp / "elsewhere", "3.14.10", [])
                self.assertIn("data folder it gives an AI app", str(refused.exception))
                checks = []
                try_installer.as_the_desktop_app(base, data, "3.14.10", checks)
        self.assertEqual(checks, ["the app knows it is the desktop app, with its own Python 3.14.10",
                                  "about transcription it says: desktop_app; what it sets up goes beside the data folder, not into the app",
                                  "an AI app started as the settings page says reaches the same data, through the Python inside the app"])
        self.assertEqual(store.list_scripts(), [])                               # the script it saved to show that is gone again
        # The settings page says it is the desktop app, and gives the AI app the data folder.
        page = (ROOT / "studio" / "web" / "pages.js").read_text(encoding="utf-8")
        self.assertEqual(page.count("build_desktop: '"), 2)
        self.assertIn("appState.desktop.python", page)
        self.assertIn("['--data-dir', mcp.data_dir]", page)


class CoreForTheAppTest(TempCase):
    """desktop/build.py: what goes inside the app."""

    def package(self, extra=None, without=()):
        files = {"studio/server.py": "print('core')", "studio/build.json": json.dumps({"version": "0.2.0-beta.rc.7", "commit": "a" * 40, "built_on": "2026-11-17"}),
                 "studio/web/index.html": "<main>", "README.md": "read me", "run_mcp.py": "#!/usr/bin/env python3\n"}
        files.update({name: "launcher" for name in build.LAUNCHERS})
        files.update(extra or {})
        path = self.tmp / "learn-my-voice-0.2.0-beta.rc.7.zip"
        with zipfile.ZipFile(str(path), "w") as archive:
            for name, text in files.items():
                if name not in without:
                    info = zipfile.ZipInfo("learn-my-voice-0.2.0-beta.rc.7/" + name)
                    info.create_system = 3
                    info.external_attr = (0o100755 if name == "run_mcp.py" else 0o100644) << 16
                    archive.writestr(info, text)
        return path

    def test_every_download_is_pinned_and_one_that_does_not_match_is_refused(self):
        self.assertEqual(sorted(build.SYSTEMS), ["macos-arm64", "macos-x64", "windows-x64"])
        for system, runtime in build.SYSTEMS.items():
            self.assertTrue(runtime["python"].startswith("https://"), system)
            self.assertRegex(runtime["sha256"], r"^[0-9a-f]{64}$", system)
            self.assertIn(build.PYTHON, runtime["python"], system)              # the same Python version on every system
        self.assertIn("www.python.org/ftp/python/", build.SYSTEMS["windows-x64"]["python"])
        self.assertRegex(build.TAURI_CLI, r"^@tauri-apps/cli@\d+\.\d+\.\d+$")       # an exact version, not "the newest"
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn('"%s"' % build.PYTHON.rsplit(".", 1)[0], workflow)        # the tests run with the version the app carries

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        data = b"the real file"
        with mock.patch.object(build.urllib.request, "urlopen", return_value=Answer(data)) as asked:
            self.assertEqual(build.fetch("https://example.test/python.zip", hashlib.sha256(data).hexdigest()), data)
            asked.return_value = Answer(b"another file under the same address")
            with self.assertRaises(build.Failed):
                build.fetch("https://example.test/python.zip", hashlib.sha256(data).hexdigest())
            asked.reset_mock()
            with self.assertRaises(build.Failed):
                build.fetch("http://example.test/python.zip", hashlib.sha256(data).hexdigest())
            asked.assert_not_called()                                           # not even asked for over plain http

    def test_the_package_goes_in_as_it_is_without_its_launchers(self):
        target = self.tmp / "app"
        found = build.put_app(self.package(), target)
        self.assertEqual(found["version"], "0.2.0-beta.rc.7")
        names = sorted(str(path.relative_to(target)).replace(os.sep, "/") for path in target.rglob("*") if path.is_file())
        self.assertEqual(names, ["README.md", "run_mcp.py", "studio/build.json", "studio/server.py", "studio/web/index.html"])
        self.assertEqual((target / "studio" / "server.py").read_text(encoding="utf-8"), "print('core')")
        if os.name != "nt":
            self.assertTrue(os.access(str(target / "run_mcp.py"), os.X_OK))
        for wrong in (self.package(without=("studio/server.py",)), self.package(without=("studio/build.json",)),
                      self.package(extra={"../outside.txt": "x"})):
            with self.assertRaises(build.Failed):
                build.put_app(wrong, self.tmp / "other")
        self.assertFalse((self.tmp / "outside.txt").exists())

    def test_the_python_for_windows_is_told_where_the_app_is_and_nothing_else(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, text in (("python.exe", "MZ"), ("python314.zip", "PK"), ("python314._pth", "python314.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"),
                               ("_sqlite3.pyd", "MZ")):
                archive.writestr(name, text)
        target = self.tmp / "python"
        build.put_python_windows(data.getvalue(), target)
        self.assertEqual((target / "python314._pth").read_bytes(), b"python314.zip\n.\n..\\app\n")
        self.assertEqual(sorted(path.name for path in target.iterdir()), ["_sqlite3.pyd", "python.exe", "python314._pth", "python314.zip"])
        for names in (("python314._pth",), ("python.exe",), ("python.exe", "python314._pth", "../evil.txt")):
            wrong = io.BytesIO()
            with zipfile.ZipFile(wrong, "w") as archive:
                for name in names:
                    archive.writestr(name, "x")
            with self.assertRaises(build.Failed, msg=names):
                build.put_python_windows(wrong.getvalue(), self.tmp / ("wrong-%d" % len(names)))

    def python_for_macos(self, extra=()):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            def add(name, text=None, link=None, kind=tarfile.REGTYPE, mode=0o644):
                info = tarfile.TarInfo("python/" + name if not name.startswith("/") and not name.startswith("..") else name)
                info.type, info.mode = kind, mode
                if link is not None:
                    info.linkname = link
                    archive.addfile(info)
                else:
                    payload = (text or "").encode("utf-8")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            add("bin/python3.14", "the program", mode=0o755)
            add("bin/python3", link="python3.14", kind=tarfile.SYMTYPE)
            add("bin/python", link="python3.14", kind=tarfile.SYMTYPE)
            add("bin/pip3", "#!/somewhere/else/python3")
            add("lib/libpython3.14.dylib", "for programs that embed Python")
            add("lib/python3.14/os.py", "import sys")
            add("lib/python3.14/again.py", link="python/lib/python3.14/os.py", kind=tarfile.LNKTYPE)
            add("lib/python3.14/to-tests.py", link="test/x.py", kind=tarfile.SYMTYPE)
            for unused in ("lib/python3.14/test/x.py", "lib/python3.14/tkinter/__init__.py", "lib/python3.14/site-packages/pip/__init__.py",
                           "lib/python3.14/ensurepip/__init__.py", "include/python3.14/Python.h", "share/man/man1/python3.1", "lib/tcl9.0/init.tcl"):
                add(unused, "not needed")
            for name in extra:
                add(name, "x")
        return data.getvalue()

    def test_the_python_for_macos_loses_what_is_not_used_and_keeps_no_links(self):
        target = self.tmp / "python"
        build.put_python_macos(self.python_for_macos(), target)
        names = sorted(str(path.relative_to(target)).replace(os.sep, "/") for path in target.rglob("*") if path.is_file())
        self.assertEqual(names, ["bin/python3", "lib/python3.14/again.py", "lib/python3.14/os.py"])       # one program, under the name the shell starts
        self.assertEqual([path for path in target.rglob("*") if path.is_symlink()], [])
        self.assertEqual((target / "bin" / "python3").read_text(encoding="utf-8"), "the program")
        self.assertEqual((target / "lib" / "python3.14" / "again.py").read_text(encoding="utf-8"), "import sys")
        if os.name != "nt":
            self.assertTrue(os.access(str(target / "bin" / "python3"), os.X_OK))
            self.assertFalse(os.access(str(target / "lib" / "python3.14" / "os.py"), os.X_OK))
        for wrong in ("../outside.py", "/absolute.py"):
            with self.assertRaises(build.Failed, msg=wrong):
                build.put_python_macos(self.python_for_macos(extra=(wrong,)), self.tmp / "wrong")
        self.assertFalse((self.tmp / "outside.py").exists())

    def test_each_system_the_build_knows_has_an_installer_the_notes_know(self):
        release = load_release()
        self.assertEqual({system: ending for system, ending, _ in release.INSTALLERS}, {system: runtime["ending"] for system, runtime in build.SYSTEMS.items()})
        workflow = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")
        for system, ending in re.findall(r"- system: (\S+)\n\s+os: \S+\n\s+ending: (\S+)", workflow):
            self.assertEqual(build.SYSTEMS[system]["ending"], ending)
        self.assertIn("desktop/", release.LEFT_OUT)                             # the shell's sources are not in the package people download


class WindowTrialTest(TempCase):
    """The part of the installer trial that has the page in the app's own window use itself (through_the_window).

    Here the app's window is played by a thread that does what studio/web/windowcheck.js does through the page:
    it waits to be told to start, saves an export through the core, and reports. What the real page does in a
    real web view is seen only where the installers are tried.
    """

    def setUp(self):
        super().setUp()
        from studio import outside

        downloads = self.tmp / "Downloads"

        class Elsewhere:
            def downloads_folder(self):
                return downloads

            def __getattr__(self, name):
                return getattr(outside, name)

        self.store = Store(self.tmp / "data")
        self.httpd = server.make_server(0, self.store, None, None, Elsewhere())
        serving = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        serving.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), serving.join(5)))
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.script = self.store.create_script(title="窗口", target_sec=30, lines=[{"spoken_script_zh": "今天去超市啦 / 买了好多东西"}])

    def ask(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
            res = conn.getresponse()
            return res.status, json.loads(res.read().decode("utf-8"))
        finally:
            conn.close()

    def page(self, change=None):
        """Play the page: wait for "start", save an export through the core, report every step as done."""
        def play():
            deadline = time.time() + 20
            while time.time() < deadline and not self.ask("GET", "/api/window-check")[1].get("start"):
                time.sleep(0.02)
            saved = self.ask("POST", "/api/scripts/%s/export/voiceover/save" % self.script["id"], {"lang": "zh"})[1]
            report = {"agent": "a web view", "facts": {"secure_context": True, "clipboard": False}, "errors": [], "saved": saved,
                      "steps": [{"name": name, "ok": True, "said": "done"} for name in try_installer.WINDOW_STEPS]}
            if change:
                change(report)
            self.ask("POST", "/api/window-check", report)
        thread = threading.Thread(target=play, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)

    def test_an_app_not_started_for_it_fails_the_trial_at_once(self):
        with self.assertRaises(try_installer.Failed) as refused:
            try_installer.through_the_window(self.base, [], {})
        self.assertIn("was not started for a check of its window", str(refused.exception))

    def test_a_window_that_did_everything_passes_and_the_file_it_saved_is_removed_again(self):
        self.httpd.app.start_window_check()
        self.page()
        checks, record = [], {}
        try_installer.through_the_window(self.base, checks, record)
        self.assertEqual(checks, ["in its own window: " + name for name in try_installer.WINDOW_STEPS]
                         + ["in its own window the page logged no error",
                            "the file exported in the window is on the disk and holds the export: in the Downloads folder"])
        self.assertEqual(record["window"]["agent"], "a web view")
        self.assertEqual(list((self.tmp / "Downloads").iterdir()), [])          # nothing is left in the Downloads folder
        words = try_installer.in_words("macos-arm64", dict(record, ok=True, checks=checks, system="Darwin", version="0.2.0", python="3.14.10",
                                                           seconds=12.5, signature="flags=0x10002(adhoc,runtime)"))
        for said in ("Trial of the desktop app on macos-arm64", "Result: passed after 12.5 seconds", "  ok    in its own window: a script opens in the editor",
                     "web view: a web view", "  clipboard: False", "  secure_context: True",
                     "  ok    a script is deleted through the question the page asks: done",
                     "Signature: flags=0x10002(adhoc,runtime)", "the export was saved as: "):
            self.assertIn(said, words)

    def test_what_fails_it(self):
        def step_failed(report):
            report["steps"][1].update(ok=False, said="waited in vain for the core saying where the file went")
            report["errors"].append("TypeError: undefined is not an object")

        def step_missing(report):
            del report["steps"][3]

        def error_logged(report):
            report["errors"].append("SyntaxError: Unexpected token")

        def file_gone(report):
            Path(report["saved"]["path"]).unlink()

        def other_content(report):
            Path(report["saved"]["path"]).write_text("<html>the app's page</html>", encoding="utf-8")

        def nothing_saved(report):
            report["saved"] = None

        for change, said in ((step_failed, ["an export through the menu is saved and the window still shows the editor: waited in vain",
                                            "Errors the page logged: TypeError"]),
                             (step_missing, ["a link to another site goes to the system browser and the window stays: this step was not reached"]),
                             (error_logged, ["the page logged errors in the app's own window: SyntaxError: Unexpected token"]),
                             (file_gone, ["it cannot be read there"]), (other_content, ["does not hold the export"]),
                             (nothing_saved, ["it cannot be read there"])):
            self.httpd.app.window_check = None
            self.httpd.app.start_window_check()
            self.page(change)
            checks = []
            with self.assertRaises(try_installer.Failed, msg=change.__name__) as refused:
                try_installer.through_the_window(self.base, checks, {})
            for part in said:
                self.assertIn(part, str(refused.exception), change.__name__)
            self.assertNotIn("the file exported in the window is on the disk", " ".join(checks))

    def test_a_window_that_never_reports_fails_it_after_the_wait(self):
        self.httpd.app.start_window_check()
        with mock.patch.object(try_installer, "WINDOW_CHECK_SEC", 1):
            with self.assertRaises(try_installer.Failed) as refused:
                try_installer.through_the_window(self.base, [], {})
        self.assertIn("reported nothing within 1 seconds", str(refused.exception))
        self.assertIn("It never said that it had begun", str(refused.exception))
        self.assertIs(self.httpd.app.window_check["start"], True)               # it was asked

    def test_a_window_that_stops_on_the_way_fails_it_and_the_trial_says_how_far_it_came(self):
        """What happened on GitHub's Windows machine on 18 November 2026 said nothing about where. Now it would."""
        names = list(try_installer.WINDOW_STEPS)
        begun = {"unfinished": True, "agent": "a web view", "facts": {"secure_context": True}, "steps": [], "errors": []}
        two = dict(begun, steps=[{"name": names[0], "ok": True, "said": "窗口"}, {"name": names[1], "ok": False, "said": "no answer within 30 s: " + names[1]}],
                   errors=["TypeError: x is not a function"])
        for so_far, said in ((begun, ["It had begun, so the page's scripts run in this web view, and it did not get through its first step"]),
                             (two, ["It had begun and got through 2 of 5 steps; the last was: " + names[1] + " (FAILED: no answer within 30 s",
                                    "Errors the page logged: TypeError: x is not a function"]),
                             (dict(begun, steps=[{"name": names[0], "ok": True, "said": ""}]), ["got through 1 of 5 steps; the last was: " + names[0] + " (done)"])):
            self.httpd.app.window_check = None
            self.httpd.app.start_window_check()
            self.assertEqual(self.ask("POST", "/api/window-check", so_far)[0], 200)
            record = {}
            with mock.patch.object(try_installer, "WINDOW_CHECK_SEC", 1):
                with self.assertRaises(try_installer.Failed) as refused:
                    try_installer.through_the_window(self.base, [], record)
            for part in ["reported nothing within 1 seconds"] + said:
                self.assertIn(part, str(refused.exception))
            self.assertNotIn("never said that it had begun", str(refused.exception))
            self.assertEqual(record["window"], so_far)                           # and it goes into the trial's text
            words = try_installer.in_words("windows-x64", dict(record, ok=False, checks=[], system="Windows", error=str(refused.exception)))
            self.assertIn("What the page reported from the app's own window (while at work; it never finished)", words)
        self.assertEqual(try_installer.how_far(None), try_installer.how_far("x"))
        self.assertEqual(try_installer.how_far({}), try_installer.how_far(begun))

    def test_what_the_page_says_while_at_work_is_never_taken_for_its_report(self):
        self.httpd.app.start_window_check()
        done = {"unfinished": True, "agent": "a web view", "facts": {}, "errors": [], "saved": None,
                "steps": [{"name": name, "ok": True, "said": "done"} for name in try_installer.WINDOW_STEPS]}
        self.ask("POST", "/api/window-check", done)
        with mock.patch.object(try_installer, "WINDOW_CHECK_SEC", 1):
            with self.assertRaises(try_installer.Failed) as refused:
                try_installer.through_the_window(self.base, [], {})
        self.assertIn("reported nothing within 1 seconds", str(refused.exception))
        self.assertIn("got through 5 of 5 steps", str(refused.exception))

    def test_on_github_what_the_window_reported_is_put_where_anybody_can_read_it(self):
        """The trial's text is only attached to a release candidate that is published; a run's annotations are public."""
        installer = self.tmp / "Learn.My.Voice_0.2.0_x64-setup.exe"
        installer.write_bytes(b"not run by this test")
        window = {"agent": "a web view", "facts": {"clipboard_write": "prompt", "desktop": True}, "errors": [],
                  "steps": [{"name": try_installer.WINDOW_STEPS[0], "ok": True, "said": "窗口, 100% of it"}],
                  "saved": {"path": "C:\\Users\\x\\Downloads\\a.txt"}}
        passed = {"ok": True, "checks": ["one", "two"], "seconds": 3.0, "system": "Windows", "error": "", "window": window}
        failed = dict(passed, ok=False, error="in the app's own window: it stopped", window=dict(window, unfinished=True))

        def run(record, on_github):
            said = io.StringIO()
            with mock.patch.object(try_installer, "try_installer", lambda *args: record), \
                    mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true" if on_github else "false"}), contextlib.redirect_stdout(said):
                code = try_installer.main([str(installer), "--label", "windows-x64", "--shots", str(self.tmp / "shots")])
            return code, said.getvalue().splitlines()

        code, lines = run(passed, True)
        notices = [line for line in lines if line.startswith("::notice ")]
        self.assertEqual((code, len(notices), [line for line in lines if line.startswith("::error ")]), (0, 1, []))
        self.assertTrue(notices[0].startswith("::notice title=The desktop app's own window on windows-x64::What the page reported from the app's own window%0A"))
        for part in ("  web view: a web view", "  clipboard_write: prompt", "  ok    " + try_installer.WINDOW_STEPS[0] + ": 窗口, 100%25 of it",
                     "the export was saved as: C:\\Users\\x\\Downloads\\a.txt"):
            self.assertIn(part, notices[0])
        self.assertNotIn("\n", notices[0])                                        # one line, or GitHub reads the rest as output
        self.assertIn("clipboard_write: prompt", (self.tmp / "shots" / "desktop-windows-x64-trial.txt").read_text(encoding="utf-8"))

        code, lines = run(failed, True)                                           # a trial that failed says both: why, and what the window said
        self.assertEqual(code, 1)
        self.assertEqual(len([line for line in lines if line.startswith("::error title=Trying the desktop app on windows-x64 failed::")]), 1)
        notices = [line for line in lines if line.startswith("::notice ")]
        self.assertEqual(len(notices), 1)
        self.assertIn("(while at work; it never finished)", notices[0])

        for record in (passed, failed):                                           # on somebody's own computer nothing of the kind is printed
            self.assertEqual([line for line in run(record, False)[1] if line.startswith("::")], [])
        self.assertEqual([line for line in run(dict(passed, window=None), True)[1] if line.startswith("::")], [])

    def test_nothing_in_the_page_s_check_waits_for_ever_or_asks_the_system_a_question(self):
        page = (ROOT / "studio" / "web" / "windowcheck.js").read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)                     # what is done, without what is said about it
        code = re.sub(r"//[^\n]*", "", code)
        # A page may ask whether it may write to the clipboard; trying it can put a question on the screen.
        # (That a thing is there may be looked at: `navigator.clipboard.writeText` without brackets calls nothing.)
        for asks_somebody in ("writeText(", "clipboard.write(", "clipboard.read", "getUserMedia(", "requestPermission", "wakeLock.request",
                              "confirm(", "alert(", "prompt(", "window.open("):
            self.assertNotIn(asks_somebody, code, asks_somebody)
        self.assertIn("navigator.permissions.query({ name: 'clipboard-write' })", code)
        # Every step runs inside a limit, and so does everything else that waits for an answer from outside the page.
        step = code[code.index("async function step("):]
        step = step[:step.index("\n  }") + 4]
        self.assertIn("await within(STEP_MS, name, run())", step)
        self.assertIn("await soFar();", step)
        limit = int(re.search(r"const STEP_MS = (\d+);", code).group(1))
        self.assertLessEqual(len(try_installer.WINDOW_STEPS) * limit / 1000 + 30, try_installer.WINDOW_CHECK_SEC)
        for line in code.splitlines():
            if "await " in line and ("navigator." in line or "api('POST', '/api/window-check'" in line):
                self.assertIn("within(", line, line.strip())
        self.assertEqual(code.count("for (;;)"), 2)                               # the two loops: one ends at a deadline, one asks the core
        self.assertIn("if (Date.now() > end) throw new Error('waited in vain for ' + what);", code)

    def test_the_steps_it_waits_for_are_the_steps_the_page_reports(self):
        page = (ROOT / "studio" / "web" / "windowcheck.js").read_text(encoding="utf-8")
        reported = re.findall(r"await step\('([^']+)'", page)
        self.assertEqual(reported, list(try_installer.WINDOW_STEPS))
        self.assertEqual(try_installer.WINDOW_CHECK_ENV, server.WINDOW_CHECK_ENV)
        rust = (SHELL / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotIn("env_clear", rust)                                     # the shell hands its environment on to the core

    def test_the_app_is_started_for_the_check_once_and_like_a_person_starts_it_the_second_time(self):
        started = []
        with mock.patch.object(try_installer.subprocess, "Popen", lambda command, **more: started.append(more["env"])), \
                mock.patch.dict(os.environ, {try_installer.WINDOW_CHECK_ENV: "1", "LEARN_MY_VOICE_DATA_DIR": "/left/over"}):
            try_installer.start(self.tmp / "app", self.tmp / "data", self.tmp / "log.txt", window_check=True)
            try_installer.start(self.tmp / "app", None, self.tmp / "log.txt")
        self.assertEqual((started[0][try_installer.WINDOW_CHECK_ENV], started[0]["LEARN_MY_VOICE_DATA_DIR"]), ("1", str(self.tmp / "data")))
        self.assertNotIn(try_installer.WINDOW_CHECK_ENV, started[1])             # also when the trial itself was started with it
        self.assertNotIn("LEARN_MY_VOICE_DATA_DIR", started[1])
        source = (DESKTOP / "try_installer.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("window_check=True"), 1)

    def mac_app(self, about):
        import plistlib
        app = self.tmp / "Learn My Voice.app"
        (app / "Contents").mkdir(parents=True, exist_ok=True)
        with open(str(app / "Contents" / "Info.plist"), "wb") as handle:
            plistlib.dump(about, handle)
        return app

    def test_what_the_mac_app_has_to_say_about_the_microphone(self):
        def codesign(rights, flags="flags=0x10002(adhoc,runtime)"):
            def run(command, **more):
                said = rights if "--entitlements" in command else b"Executable=/x\nCodeDirectory v=20500 size=1 " + flags.encode("ascii") + b" hashes=1\n"
                return subprocess.CompletedProcess(command, 0, said if "--entitlements" in command else b"", b"" if "--entitlements" in command else said)
            return mock.patch.object(try_installer.subprocess, "run", run)

        right = b"<key>com.apple.security.device.audio-input</key><true/>"
        said = {"NSMicrophoneUsageDescription": "to speak an idea"}
        with codesign(right):
            checks, record = [], {}
            try_installer.microphone_is_declared(self.mac_app(said), checks, record)
            self.assertEqual(checks, ["on the Mac it says what it wants the microphone for, and is signed with the right to it"])
            self.assertEqual(record["signature"], "CodeDirectory v=20500 size=1 flags=0x10002(adhoc,runtime) hashes=1")
            for wrong in ({}, {"NSMicrophoneUsageDescription": "  "}, {"NSCameraUsageDescription": "x"}):
                with self.assertRaises(try_installer.Failed) as refused:
                    try_installer.microphone_is_declared(self.mac_app(wrong), [], {})
                self.assertIn("does not say what it wants the microphone for", str(refused.exception))
        with codesign(b"<dict/>"), self.assertRaises(try_installer.Failed) as refused:
            try_installer.microphone_is_declared(self.mac_app(said), [], {})
        self.assertIn("not signed with the right to the microphone", str(refused.exception))
        (self.tmp / "Learn My Voice.app" / "Contents" / "Info.plist").write_bytes(b"not a plist")
        with codesign(right), self.assertRaises(try_installer.Failed) as refused:
            try_installer.microphone_is_declared(self.tmp / "Learn My Voice.app", [], {})
        self.assertIn("Info.plist cannot be read", str(refused.exception))

    def test_the_shells_files_declare_the_microphone_and_nothing_more(self):
        import plistlib
        with open(str(SHELL / "Info.plist"), "rb") as handle:
            about = plistlib.load(handle)
        self.assertEqual(sorted(about), ["NSMicrophoneUsageDescription"])
        why = about["NSMicrophoneUsageDescription"]
        self.assertRegex(why, r"[A-Za-z]{4}.*[\u4e00-\u9fff]{4}")               # said in English and in Chinese
        page = (ROOT / "studio" / "web" / "discover.js").read_text(encoding="utf-8")
        for label in re.findall(r"dv_mic: '🎙 ([^']+)'", page):                  # it names the button as the page does
            self.assertIn(label, why)
        with open(str(SHELL / "Entitlements.plist"), "rb") as handle:
            rights = plistlib.load(handle)
        self.assertEqual(rights, {try_installer.MICROPHONE_RIGHT: True})        # a new right is a decision, not a habit
        config = json.loads((SHELL / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(config["bundle"]["macOS"]["entitlements"], "Entitlements.plist")


def load_release():
    spec = importlib.util.spec_from_file_location("desktop_build_release", str(ROOT / "tools" / "build_release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
