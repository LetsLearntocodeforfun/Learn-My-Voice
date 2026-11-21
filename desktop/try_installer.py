#!/usr/bin/env python3
"""Try the desktop app's installer the way a person would: install it, open the app, use it, close it.

    python3 desktop/try_installer.py dist/learn-my-voice-<version>-<system>.dmg|-setup.exe [--result FILE] [--shots DIR]
    (the installer, the file and the folder may also be given as INSTALLER, INSTALLER_RESULT and INSTALLER_SHOTS)

On whatever system it runs on (macOS or Windows):

1. installs: on macOS the .dmg is opened and the app copied out of it, as dragging it to Applications does; on
   Windows the setup program is run without questions, for this user only;
2. starts the installed app with a data folder of its own, and waits until it says where its core listens;
3. uses the core exactly as tools/try_release.py uses a package (page, scripts, a script in Chinese saved,
   checked, exported, deleted), asks it what only the desktop app has to get right (that it knows it is the
   desktop app, what it says about transcription, and that an AI app started as its settings page says reaches
   the same data), and waits until the window has finished loading the app's page;
4. has the page in the app's own window use itself the way a person would (open a script, export it through the
   menu, follow a link to another site, delete the script through the question the page asks) and reads what
   the page reports: this is the one part that shows what the system's web view does with the real page;
   then takes a picture of the screen, so that a person can look at what was on it;
5. ends the app abruptly and checks that its core stops by itself, then starts it once more without naming a
   data folder and checks that it keeps its data in the system's folder for application data;
6. on Windows, uninstalls it.

This is meant for GitHub's machines. It installs a program for the user it runs as and writes to that user's
application-data folder; on Windows it uninstalls again, on macOS nothing was installed outside its own folder.
The one file the window check saves into the Downloads folder is removed again.
Beside the picture it leaves desktop-<label>-trial.txt: every check in words, and what the window reported.
Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
IDENTIFIER = "io.github.letslearntocodeforfun.learn-my-voice"     # as in src-tauri/tauri.conf.json
PRODUCT = "Learn My Voice"
BINARY = "learn-my-voice"
RUNNING_FILE = "desktop-running.json"                              # as in src-tauri/src/main.rs
PROBLEM_FILE = "desktop-problem.txt"                               # as in src-tauri/src/main.rs
START_TIMEOUT_SEC = 180
WINDOW_CHECK_ENV = "LEARN_MY_VOICE_WINDOW_CHECK"                   # as in studio/server.py
NO_UPDATE_CHECK_ENV = "LEARN_MY_VOICE_NO_UPDATE_CHECK"             # as in studio/server.py and tools/try_release.py
# How long the page in the window has for its check. Each of its steps gives up after 30 seconds
# (STEP_MS in studio/web/windowcheck.js), so within this time even a check in which every step waited in vain
# has reported.
WINDOW_CHECK_SEC = 240
# What the page in the window has to have done, under the names studio/web/windowcheck.js gives its steps.
WINDOW_STEPS = (
    "a script opens in the editor",
    "an export through the menu is saved and the window still shows the editor",
    "the saved file can be shown in its folder",
    "a link to another site goes to the system browser and the window stays",
    "a script is deleted through the question the page asks",
)
MICROPHONE_RIGHT = "com.apple.security.device.audio-input"


def load_trial() -> Any:
    spec = importlib.util.spec_from_file_location("try_release", str(HERE.parent / "tools" / "try_release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trial = load_trial()
Failed = trial.Failed


def run(command: List[str], timeout: int = 300) -> str:
    done = subprocess.run(command, capture_output=True, timeout=timeout)
    said = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
    if done.returncode != 0:
        raise Failed("%s ended with code %d: %s" % (" ".join(command[:3]), done.returncode, said[-600:]))
    return said


def install_macos(installer: Path, work: Path, checks: List[str]) -> Tuple[Path, Path]:
    """Open the .dmg and copy the app out of it. Returns the program inside the app and the app's resources."""
    mount = work / "mount"
    mount.mkdir()
    run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(installer)])
    try:
        apps = sorted(mount.glob("*.app"))
        if len(apps) != 1:
            raise Failed("the .dmg should hold one app, and holds %s" % sorted(path.name for path in mount.iterdir()))
        checks.append("the .dmg opens and holds %s" % apps[0].name)
        if os.path.islink(str(mount / "Applications")):
            checks.append("it offers the Applications folder to drag the app to")
        target = work / "Applications" / apps[0].name
        target.parent.mkdir()
        run(["ditto", str(apps[0]), str(target)])
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True, timeout=120)
    program = target / "Contents" / "MacOS" / BINARY
    if not program.is_file():
        raise Failed("the app has no program at Contents/MacOS/%s" % BINARY)
    checks.append("the app is copied out of it")
    signed = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(target)], capture_output=True, timeout=120)
    if signed.returncode != 0:
        raise Failed("the app's signature does not hold together: %s" % signed.stderr.decode("utf-8", "replace")[-400:])
    checks.append("its own (unpaid, ad-hoc) signature holds together")
    return program, target / "Contents" / "Resources"


def install_windows(installer: Path, work: Path, checks: List[str]) -> Tuple[Path, Path]:
    """Run the setup program without questions. Returns the installed program and the folder it is in."""
    folder = Path(os.environ["LOCALAPPDATA"]) / PRODUCT
    if folder.exists():
        raise Failed("%s is there before installing; this trial needs a machine without the app" % folder)
    run([str(installer), "/S"], timeout=600)
    program = folder / (BINARY + ".exe")
    deadline = time.time() + 120
    while not program.is_file():
        if time.time() > deadline:
            raise Failed("the setup program ended, and %s is not there" % program)
        time.sleep(1)
    checks.append("the setup program installs the app for this user, without questions")
    menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if list(menu.rglob(PRODUCT + ".lnk")):
        checks.append("it is in the Start menu")
    else:
        raise Failed("no %s.lnk was put into the Start menu" % PRODUCT)
    return program, folder


def uninstall_windows(folder: Path, checks: List[str]) -> None:
    remover = folder / "uninstall.exe"
    if not remover.is_file():
        raise Failed("the installed app has no uninstall.exe")
    subprocess.run([str(remover), "/S"], capture_output=True, timeout=300)
    deadline = time.time() + 120                             # the remover hands over to a copy of itself and returns at once
    while (folder / (BINARY + ".exe")).exists():
        if time.time() > deadline:
            raise Failed("after uninstalling, %s is still there" % (folder / (BINARY + ".exe")))
        time.sleep(1)
    checks.append("it uninstalls")


def python_inside(core: Path, checks: List[str]) -> None:
    """Run the Python that ships inside the app by itself, before the app is trusted to start it."""
    python = core / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    about = "%s (mode %s)" % (python, oct(python.stat().st_mode & 0o777) if python.is_file() else "missing")
    try:
        done = subprocess.run([str(python), "-X", "utf8", "-E", "-s", "-c",
                               "import sys, ssl, sqlite3, zlib, json, http.server; print(sys.version.split()[0], '|', ssl.OPENSSL_VERSION, '| sqlite', sqlite3.sqlite_version)"],
                              capture_output=True, timeout=120, cwd=str(core / "app"))
    except (OSError, subprocess.SubprocessError) as problem:
        raise Failed("the Python inside the app could not be started: %s: %s\n%s" % (type(problem).__name__, problem, about))
    said = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
    if done.returncode != 0:
        raise Failed("the Python inside the app ended with code %s: %s\n%s" % (done.returncode, said[-800:], about))
    checks.append("the Python inside the app runs by itself: %s" % said[:120])


def same_place(one: Any, other: Any) -> bool:
    """Whether two paths name the same place, however each is spelled (links on the way, letter case on Windows)."""
    return os.path.normcase(os.path.realpath(str(one))) == os.path.normcase(os.path.realpath(str(other)))


def as_the_desktop_app(base: str, data: Path, python: Any, checks: List[str]) -> None:
    """What only the desktop app has to get right, asked of the running app.

    It knows that it is the desktop app. About transcription it either can set it up, or says why not without
    sending people to a launcher they do not have, and what it would set up goes beside the data folder, never
    into the app. And what its settings page gives an AI app to start (the Python inside the app, the script,
    the data folder) starts, from another folder, a server that works on the data the window is showing: a
    script saved through the window is listed by it.
    """
    status, state, _ = trial.ask(base, "GET", "/api/state")
    state = state if status == 200 and isinstance(state, dict) else {}
    inside = state.get("desktop")
    if not (isinstance(inside, dict) and inside.get("python") == python):
        raise Failed("the app does not say that it is the desktop app with Python %s (it says %r)" % (python, inside))
    checks.append("the app knows it is the desktop app, with its own Python %s" % python)

    status, ears, _ = trial.ask(base, "GET", "/api/transcriber")
    ears = ears if status == 200 and isinstance(ears, dict) else {}
    reason = ears.get("reason")
    if reason == "python_version" or (ears.get("supported") is not True and not reason):
        raise Failed("about transcription the desktop app has to say that it can be set up, or why not without naming "
                     "a launcher (it says %r)" % ({key: ears.get(key) for key in ("supported", "reason", "reason_en")},))
    beside = os.path.join(os.path.dirname(os.path.realpath(str(data))), "whisper")
    if not same_place(ears.get("location"), beside):
        raise Failed("what transcription sets up would go to %s, not beside the data folder (%s)" % (ears.get("location"), beside))
    checks.append("about transcription it says: %s; what it sets up goes beside the data folder, not into the app"
                  % (reason or "it can be set up on this Mac"))

    mcp = state.get("mcp") if isinstance(state.get("mcp"), dict) else {}
    command = [str(mcp.get("python")), str(mcp.get("script")), "--data-dir", str(mcp.get("data_dir"))]
    if not same_place(command[3], data):
        raise Failed("the data folder it gives an AI app is %s, and its data is in %s" % (command[3], data))
    title = "written in the window"
    status, made, _ = trial.ask(base, "POST", "/api/scripts", {"title": title, "lines": [{"spoken_script_zh": "今天去超市啦"}]})
    if status != 201 or not isinstance(made, dict):
        raise Failed("a script could not be saved before the AI app's way in was tried (status %s)" % status)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "trial", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_scripts", "arguments": {}}},
    ]
    try:
        done = subprocess.run(command, input="".join(json.dumps(message) + "\n" for message in messages).encode("ascii"),
                              capture_output=True, timeout=120, cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError) as problem:
        raise Failed("the MCP server could not be started as the settings page says: %s: %s\n%s"
                     % (type(problem).__name__, problem, " ".join(command)))
    said = done.stdout.decode("utf-8", "replace")
    try:
        replies = [json.loads(line) for line in said.splitlines() if line.strip()]
        listed = json.loads(replies[1]["result"]["content"][0]["text"])["scripts"]
        titles = [script.get("title") for script in listed]
    except (ValueError, LookupError, TypeError, AttributeError):
        raise Failed("started as the settings page says, the MCP server did not answer as expected (code %s): %s\n%s"
                     % (done.returncode, said[-500:], done.stderr.decode("utf-8", "replace")[-500:]))
    if title not in titles:
        raise Failed("the MCP server does not list the script just saved through the window (it lists %r): it is not "
                     "working on the same data" % (titles[:5],))
    trial.ask(base, "DELETE", "/api/scripts/" + str(made.get("id")))
    checks.append("an AI app started as the settings page says reaches the same data, through the Python inside the app")


def how_far(so_far: Any) -> str:
    """In words, how far a check of the window came that never reported, from what the page said while at work."""
    if not isinstance(so_far, dict):
        return ("It never said that it had begun: the page's scripts do not run in this web view, or the page did "
                "not learn that a check was wanted")
    steps = [step for step in so_far.get("steps") or [] if isinstance(step, dict)]
    if steps:
        last = steps[-1]
        text = "It had begun and got through %d of %d steps; the last was: %s (%s%s)" % (
            len(steps), len(WINDOW_STEPS), last.get("name"), "done" if last.get("ok") is True else "FAILED",
            ": " + str(last.get("said"))[:300] if last.get("said") else "")
    else:
        text = "It had begun, so the page's scripts run in this web view, and it did not get through its first step"
    errors = "; ".join(str(error) for error in so_far.get("errors") or [])[:1000]
    return text + (". Errors the page logged: " + errors if errors else "")


def through_the_window(base: str, checks: List[str], record: Dict[str, Any]) -> None:
    """Have the page in the app's own window use itself, and read what it reports (studio/web/windowcheck.js).

    Everything before this spoke to the core. This is the page, in the web view the system gives the app
    (WebKit on macOS, WebView2 on Windows), clicking its own buttons: it opens a script in the editor, exports
    it through the menu, asks for the saved file to be shown, follows a link to another site, and deletes the
    script through the question the page asks. Each step has to have happened, the window has to show the app
    afterwards, the page must have logged no error, and the exported file has to be on the disk with the
    export in it. During this the core writes links and folders down and opens nothing.
    """
    status, _, _ = trial.ask(base, "POST", "/api/window-check/start", {})
    if status != 200:
        raise Failed("the app was not started for a check of its window (asked to start one, it answered %s)" % status)
    deadline = time.time() + WINDOW_CHECK_SEC
    report = None
    while not isinstance(report, dict):
        status, check, _ = trial.ask(base, "GET", "/api/window-check")
        report = check.get("report") if status == 200 and isinstance(check, dict) else None
        if not isinstance(report, dict):
            if time.time() > deadline:
                so_far = check.get("so_far") if status == 200 and isinstance(check, dict) else None
                if isinstance(so_far, dict):
                    record["window"] = so_far                # what it said while at work goes into the trial's text
                raise Failed("the page in the app's window reported nothing within %d seconds of being asked to check "
                             "itself. %s" % (WINDOW_CHECK_SEC, how_far(so_far)))
            time.sleep(1)
    record["window"] = report
    errors = "; ".join(str(error) for error in report.get("errors") or [])[:1500]
    steps = {str(step.get("name")): step for step in report.get("steps") or [] if isinstance(step, dict)}
    for name in WINDOW_STEPS:
        step = steps.get(name)
        if not step or step.get("ok") is not True:
            raise Failed("in the app's own window: %s: %s%s" % (name, (step or {}).get("said") or "this step was not reached",
                                                              "\nErrors the page logged: " + errors if errors else ""))
        checks.append("in its own window: " + name)
    if errors:
        raise Failed("the page logged errors in the app's own window: " + errors)
    checks.append("in its own window the page logged no error")
    saved = report.get("saved") if isinstance(report.get("saved"), dict) else {}
    path = Path(str(saved.get("path") or "nowhere"))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as problem:
        raise Failed("the page said the export was saved as %s, and it cannot be read there: %s" % (path, problem))
    if not text.startswith("今天去超市啦"):
        raise Failed("the file the window exported does not hold the export: %r" % text[:80])
    checks.append("the file exported in the window is on the disk and holds the export: %s"
                  % ("in the Downloads folder" if saved.get("in_downloads") is True
                     else "beside the app's data, because the Downloads folder could not be written to"))
    try:
        path.unlink()
    except OSError:
        pass


def microphone_is_declared(app: Path, checks: List[str], record: Dict[str, Any]) -> None:
    """On a Mac, speaking a prompt needs two things from the app itself, and both are read back here.

    macOS asks a person whether an app may use the microphone only when the app says what for
    (NSMicrophoneUsageDescription in its Info.plist); without that it refuses without asking. And an app
    signed for the system's stricter rules (the "hardened runtime") gets the microphone only when it was
    signed with the right to it. Whether a recording then works is not shown here: no person is there to
    answer the system's question.
    """
    try:
        with open(str(app / "Contents" / "Info.plist"), "rb") as handle:
            about = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException) as problem:
        raise Failed("the app's Info.plist cannot be read: %s" % problem)
    why = about.get("NSMicrophoneUsageDescription")
    if not (isinstance(why, str) and why.strip()):
        raise Failed("the app does not say what it wants the microphone for (NSMicrophoneUsageDescription): "
                     "macOS would refuse it the microphone without asking the person")
    said = b""
    for form in (["--entitlements", ":-"], ["--entitlements", "-", "--xml"]):       # how older and newer systems want it asked
        done = subprocess.run(["codesign", "-d"] + form + [str(app)], capture_output=True, timeout=120)
        said += done.stdout + done.stderr
    signature = subprocess.run(["codesign", "-dv", str(app)], capture_output=True, timeout=120)
    flags = [line for line in (signature.stdout + signature.stderr).decode("utf-8", "replace").splitlines() if "flags=" in line]
    record["signature"] = flags[0].strip() if flags else "no flags line"
    if MICROPHONE_RIGHT.encode("ascii") not in said:
        raise Failed("the app is not signed with the right to the microphone (%s); its signature says: %s"
                     % (MICROPHONE_RIGHT, record["signature"]))
    checks.append("on the Mac it says what it wants the microphone for, and is signed with the right to it")


def picture(target: Path) -> bool:
    """A picture of the whole screen, for a person to look at. Not having one fails nothing."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            subprocess.run(["screencapture", "-x", str(target)], capture_output=True, timeout=60)
        else:
            script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                      "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $i=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
                      "$g=[System.Drawing.Graphics]::FromImage($i); $g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
                      "$i.Save($env:SHOT_FILE)")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=120,
                           env=dict(os.environ, SHOT_FILE=str(target)))
        return target.is_file() and target.stat().st_size > 1000
    except (OSError, subprocess.SubprocessError):
        return False


def end_abruptly(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=60)      # this one program, not what it started
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def answers(base: str) -> bool:
    try:
        return trial.ask(base, "GET", "/api/state")[0] == 200
    except OSError:
        return False


def start(program: Path, data: Optional[Path], log: Path, window_check: bool = False) -> "subprocess.Popen[bytes]":
    env = dict(os.environ)
    env.pop("LEARN_MY_VOICE_DATA_DIR", None)
    env.pop(WINDOW_CHECK_ENV, None)
    # Every start of the installed app, also the one "as a person starts it": the page in the window would
    # otherwise ask GitHub for the list of releases as soon as it has loaded. The shell hands its environment on
    # to the core (src-tauri/src/main.rs adds one variable and clears none), which reads this.
    env[NO_UPDATE_CHECK_ENV] = "1"
    if data is not None:
        env["LEARN_MY_VOICE_DATA_DIR"] = str(data)
    if window_check:
        env[WINDOW_CHECK_ENV] = "1"             # the shell hands its environment on to the core, which reads this
    with open(str(log), "wb") as out:
        return subprocess.Popen([str(program)], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env)


def wait_for(running: Path, process: "subprocess.Popen[bytes]", want_page: bool, how: str) -> Dict[str, Any]:
    """Wait for the file in which the running app says where its core listens (and, if asked, that its page loaded)."""
    deadline = time.time() + START_TIMEOUT_SEC
    note: Dict[str, Any] = {}
    while True:
        try:
            note = json.loads(running.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            note = {}
        if note.get("url") and (note.get("page_loaded") or not want_page):
            return note
        problem = running.with_name(PROBLEM_FILE)
        if problem.is_file():                                # the app's own account of why it could not start
            raise Failed("%s could not start, and says why:\n%s" % (how, problem.read_text(encoding="utf-8", errors="replace")[-1500:]))
        if process.poll() is not None:
            raise Failed("%s ended by itself with code %s" % (how, process.returncode))
        if time.time() > deadline:
            raise Failed("%s: after %d seconds %s" % (how, START_TIMEOUT_SEC, "the window had not finished loading the page (the core was running)"
                                                      if note.get("url") else "the app had not said where its core listens"))
        time.sleep(0.5)


def default_data_folder() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / IDENTIFIER / "data"
    return Path(os.environ["APPDATA"]) / IDENTIFIER / "data"


def try_installer(installer: Path, shots: Optional[Path], label: str) -> Dict[str, Any]:
    started = time.time()
    checks: List[str] = []
    record: Dict[str, Any] = {"ok": False, "checks": checks, "error": "", "system": "%s %s %s" % (platform.system(), platform.release(), platform.machine())}
    # Its real place, with no link on the way to it: on macOS the temporary folder is reached through one
    # (/var is a link to /private/var), and the app refuses to start from a place that is reached through a
    # link. The Applications folder, where a person puts it, is not.
    work = Path(os.path.realpath(tempfile.mkdtemp(prefix="try-installer-")))
    process = None
    folder = None
    log = work / "app-output.txt"
    try:
        kind = platform.system()
        if kind == "Darwin":
            program, resources = install_macos(installer, work, checks)
            microphone_is_declared(resources.parent.parent, checks, record)
        elif kind == "Windows":
            program, resources = install_windows(installer, work, checks)
            folder = resources
        else:
            raise Failed("there is no installer for %s" % kind)
        core = resources / "core"
        build = json.loads((core / "app" / "studio" / "build.json").read_text(encoding="utf-8"))
        runtime = json.loads((core / "runtime.json").read_text(encoding="utf-8"))
        version = str(build.get("version"))
        record["version"], record["python"] = version, runtime.get("python")
        checks.append("it carries the package %s and its own Python %s" % (version, runtime.get("python")))
        python_inside(core, checks)

        data = work / "data"
        process = start(program, data, log, window_check=True)
        note = wait_for(data / RUNNING_FILE, process, False, "the installed app")
        base = str(note["url"]).rstrip("/")
        checks.append("the installed app starts and its core listens on this computer only")
        if not base.startswith("http://127.0.0.1:"):
            raise Failed("the core listens at %s, not on 127.0.0.1" % base)
        if trial.ask(base, "GET", "/api/state", token="")[0] != 401:
            raise Failed("unauthenticated local request to /api/state was not refused with 401")
        checks.append("unauthenticated local requests are refused")
        trial.set_token(trial.read_token(data))
        trial.use(base, version, checks)
        as_the_desktop_app(base, data, runtime.get("python"), checks)
        wait_for(data / RUNNING_FILE, process, True, "the installed app")
        checks.append("its window finished loading the app's page")
        through_the_window(base, checks, record)
        time.sleep(3)                                        # let the page draw before the picture is taken
        if shots is not None and picture(shots / ("desktop-%s.png" % label)):
            checks.append("a picture of the screen was taken")
        if not (data / "studio.db").is_file():
            raise Failed("the app did not keep its data in the folder it was given")
        checks.append("its data is in the folder it was given")

        end_abruptly(int(note["shell_pid"]))
        process.wait(timeout=30)
        deadline = time.time() + 20
        while answers(base):
            if time.time() > deadline:
                raise Failed("the app was ended and its core was still answering 20 seconds later")
            time.sleep(0.5)
        checks.append("when the app is ended abruptly, its core stops by itself")

        usual = default_data_folder()
        process = start(program, None, log)
        note = wait_for(usual / RUNNING_FILE, process, False, "the app, started a second time")
        trial.set_token(trial.read_token(usual))
        if not answers(str(note["url"]).rstrip("/")):
            raise Failed("started a second time, the app's core did not answer")
        if trial.ask(str(note["url"]).rstrip("/"), "GET", "/api/window-check")[0] != 404:
            raise Failed("started the way a person starts it, the app still offers the window check")
        checks.append("started the way a person starts it, there is no window check")
        status, updates, _ = trial.ask(str(note["url"]).rstrip("/"), "GET", "/api/update")      # the plain state: it asks nothing
        if status != 200 or not isinstance(updates, dict) or updates.get("checks") != {"on": False}:
            raise Failed("started by this trial, the app should not look for updates and should say so (it answered %s: %r)"
                         % (status, updates if not isinstance(updates, dict) else updates.get("checks")))
        checks.append("started by this trial, it does not look for updates and says so")
        checks.append("started again without a folder named, it keeps its data in the system's folder for application data")
        end_abruptly(int(note["shell_pid"]))
        process.wait(timeout=30)
        process = None
        if folder is not None:
            time.sleep(3)
            uninstall_windows(folder, checks)
        record["ok"] = True
    except Failed as problem:
        said = log.read_bytes().decode("utf-8", "replace")[-800:] if log.is_file() else ""
        record["error"] = "%s%s" % (problem, "\n--- what the app printed ---\n" + said if said.strip() else "")
        if shots is not None:
            picture(shots / ("desktop-%s-at-failure.png" % label))
    except Exception as problem:                             # noqa: BLE001 - anything else is also "it did not work", with its name
        record["error"] = "%s: %s" % (type(problem).__name__, problem)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        shutil.rmtree(str(work), ignore_errors=True)
    record["seconds"] = round(time.time() - started, 1)
    return record


def in_words(label: str, record: Dict[str, Any]) -> str:
    """The trial as text, for a person: every check that passed, why it stopped if it did, and what the page in
    the app's own window reported about that window."""
    lines = ["Trial of the desktop app on %s (%s)" % (label, record.get("system", "")),
             "Version %s, with its own Python %s" % (record.get("version", "?"), record.get("python", "?")),
             "Result: %s after %s seconds" % ("passed" if record.get("ok") else "FAILED", record.get("seconds", "?")), ""]
    lines += ["  ok    " + check for check in record.get("checks", [])]
    if record.get("error"):
        lines += ["  FAIL  " + str(record["error"])]
    if record.get("signature"):
        lines += ["", "Signature: " + str(record["signature"])]
    if isinstance(record.get("window"), dict):
        lines += [""] + window_in_words(record["window"])
    return "\n".join(lines) + "\n"


def window_in_words(window: Dict[str, Any]) -> List[str]:
    """What the page reported from the app's own window, as lines of text: the web view, what it offers, each
    step, the errors the page logged, and where the export went."""
    lines = ["What the page reported from the app's own window" + (
        " (while at work; it never finished)" if window.get("unfinished") is True else ""),
             "  web view: " + str(window.get("agent", ""))]
    facts = window.get("facts") if isinstance(window.get("facts"), dict) else {}
    lines += ["  %s: %s" % (key, facts[key]) for key in sorted(facts)]
    for step in window.get("steps") or []:
        if isinstance(step, dict):
            lines.append("  %s  %s: %s" % ("ok  " if step.get("ok") is True else "FAIL", step.get("name"), step.get("said")))
    lines += ["  error logged by the page: " + str(error) for error in window.get("errors") or []]
    if isinstance(window.get("saved"), dict):
        lines.append("  the export was saved as: " + str(window["saved"].get("path")))
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Install the desktop app, open it, use it, close it.")
    parser.add_argument("installer", nargs="?", default=os.environ.get("INSTALLER", ""))
    parser.add_argument("--result", default=os.environ.get("INSTALLER_RESULT", ""))
    parser.add_argument("--shots", default=os.environ.get("INSTALLER_SHOTS", ""))
    parser.add_argument("--label", default=os.environ.get("INSTALLER_LABEL", ""))
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    label = " ".join(args.label.split())[:60] or ("macos" if platform.system() == "Darwin" else platform.system().lower())
    found = sorted(Path().glob(args.installer)) if any(sign in args.installer for sign in "*?[") else [Path(args.installer)]
    if not args.installer or len(found) != 1 or not found[0].is_file():
        record: Dict[str, Any] = {"ok": False, "checks": [], "seconds": 0.0, "system": platform.system(),
                                  "error": "there should be one installer at %r, and there %s" % (args.installer, "is none" if not found else "are %d" % len(found))}
        installer = None
    else:
        installer = found[0]
        record = try_installer(installer, Path(args.shots) if args.shots else None, label)
    if args.result:
        out = Path(args.result)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"kind": "desktop", "label": label, "ok": record["ok"], "outcome": "success" if record["ok"] else "failure",
                                   "detail": record["error"][:2000], "checks": len(record["checks"]), "system": record["system"],
                                   "installer": installer.name if installer else "",
                                   "sha256": hashlib.sha256(installer.read_bytes()).hexdigest() if installer else ""},
                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.shots and installer is not None:
        try:
            Path(args.shots).mkdir(parents=True, exist_ok=True)
            (Path(args.shots) / ("desktop-%s-trial.txt" % label)).write_text(in_words(label, record), encoding="utf-8")
        except OSError:
            pass                                             # the text is for people; not having it fails nothing
    for check in record["checks"]:
        print("  ok    " + check)
    if not record["ok"]:
        print("  FAIL  " + record["error"])
    print("%s: %s, %d checks, %.1f s" % (label, "passed" if record["ok"] else "FAILED", len(record["checks"]), record["seconds"]), flush=True)
    if not record["ok"] and os.environ.get("GITHUB_ACTIONS") == "true":
        done = "After %d checks that passed (the last: %s).\n\n" % (len(record["checks"]), record["checks"][-1]) if record["checks"] else ""
        print(trial.annotation("Trying the desktop app on %s failed" % label, done + record["error"]), flush=True)
    if isinstance(record.get("window"), dict) and os.environ.get("GITHUB_ACTIONS") == "true":
        # Also when the trial passed. What the page found in this system's web view is then on the run's page, which
        # anybody can read; the trial's text is only attached to a release candidate, and only when one is published.
        print(trial.annotation("The desktop app's own window on %s" % label, "\n".join(window_in_words(record["window"])), level="notice"),
              flush=True)
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
