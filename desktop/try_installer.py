#!/usr/bin/env python3
"""Try the desktop app's installer the way a person would: install it, open the app, use it, close it.

    python3 desktop/try_installer.py dist/learn-my-voice-<version>-<system>.dmg|-setup.exe [--result FILE] [--shots DIR]
    (the installer, the file and the folder may also be given as INSTALLER, INSTALLER_RESULT and INSTALLER_SHOTS)

On whatever system it runs on (macOS or Windows):

1. installs: on macOS the .dmg is opened and the app copied out of it, as dragging it to Applications does; on
   Windows the setup program is run without questions, for this user only;
2. starts the installed app with a data folder of its own, and waits until it says where its core listens;
3. uses the core exactly as tools/try_release.py uses a package (page, scripts, a script in Chinese saved,
   checked, exported, deleted), and waits until the window has finished loading the app's page;
4. takes a picture of the screen, so that a person can look at what was on it;
5. ends the app abruptly and checks that its core stops by itself, then starts it once more without naming a
   data folder and checks that it keeps its data in the system's folder for application data;
6. on Windows, uninstalls it.

This is meant for GitHub's machines. It installs a program for the user it runs as and writes to that user's
application-data folder; on Windows it uninstalls again, on macOS nothing was installed outside its own folder.
Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
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


def start(program: Path, data: Optional[Path], log: Path) -> "subprocess.Popen[bytes]":
    env = dict(os.environ)
    env.pop("LEARN_MY_VOICE_DATA_DIR", None)
    if data is not None:
        env["LEARN_MY_VOICE_DATA_DIR"] = str(data)
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
    work = Path(tempfile.mkdtemp(prefix="try-installer-"))
    process = None
    folder = None
    log = work / "app-output.txt"
    try:
        kind = platform.system()
        if kind == "Darwin":
            program, resources = install_macos(installer, work, checks)
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
        process = start(program, data, log)
        note = wait_for(data / RUNNING_FILE, process, False, "the installed app")
        base = str(note["url"]).rstrip("/")
        checks.append("the installed app starts and its core listens on this computer only")
        if not base.startswith("http://127.0.0.1:"):
            raise Failed("the core listens at %s, not on 127.0.0.1" % base)
        trial.use(base, version, checks)
        wait_for(data / RUNNING_FILE, process, True, "the installed app")
        checks.append("its window finished loading the app's page")
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
        if not answers(str(note["url"]).rstrip("/")):
            raise Failed("started a second time, the app's core did not answer")
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
    for check in record["checks"]:
        print("  ok    " + check)
    if not record["ok"]:
        print("  FAIL  " + record["error"])
    print("%s: %s, %d checks, %.1f s" % (label, "passed" if record["ok"] else "FAILED", len(record["checks"]), record["seconds"]), flush=True)
    if not record["ok"] and os.environ.get("GITHUB_ACTIONS") == "true":
        done = "After %d checks that passed (the last: %s).\n\n" % (len(record["checks"]), record["checks"][-1]) if record["checks"] else ""
        print(trial.annotation("Trying the desktop app on %s failed" % label, done + record["error"]), flush=True)
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
