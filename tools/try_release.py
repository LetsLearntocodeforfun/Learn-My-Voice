#!/usr/bin/env python3
"""Try a release package the way a person would: unpack it, start it with its launcher, use it, stop it.

    python3 tools/try_release.py dist/learn-my-voice-<version>.zip [--sha256 HEX] [--result FILE] [--label TEXT]
        (each may also be given as TRY_PACKAGE, TRY_SHA256, TRY_RESULT and TRY_LABEL)

What it does, on whatever system it runs on:

1. checks the file against the checksum, when one is given;
2. unpacks it into a folder of its own;
3. starts the app with the launcher meant for this system (the .command file on macOS, the .bat on Windows, the
   .sh on Linux), and once more with the Python that runs this script, so a second Python version is tried too;
4. asks the running app for its page and its scripts, checks that it says which build it is, writes a script
   with Chinese text, has it checked, exports it, and deletes it;
5. stops the app and removes the folder.

It needs no network: the app only listens on this computer and no AI service is asked. Nothing outside the
unpacked folder is written. ``--result`` writes what happened to a file, for the release notes.

Standard library only. Used by .github/workflows/release-candidate.yml; it runs the same on a desk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

START_TIMEOUT_SEC = 90
LAUNCHERS = {"Darwin": ["bash", "Start Learn My Voice.command"], "Windows": ["cmd", "/c", "Start Learn My Voice.bat"],
             "Linux": ["sh", "start-learn-my-voice.sh"]}
SPOKEN = "这周又去超市补货啦 / 然后拿了两盒水饺"
# A direct connection: a proxy set for this computer must never stand between the check and the app.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Failed(Exception):
    pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def unpack(package: Path, into: Path) -> Tuple[Path, Dict[str, Any]]:
    """Unpack like an archive tool does, file modes included; returns the app's folder and its build file."""
    with zipfile.ZipFile(str(package)) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise Failed("unsafe name in the package: %r" % name)
            target = into / name
            archive.extract(info, str(into))
            mode = (info.external_attr >> 16) & 0o777
            if mode and not name.endswith("/") and os.name != "nt":
                os.chmod(str(target), mode)
    folders = [p for p in into.iterdir() if p.is_dir()]
    if len(folders) != 1:
        raise Failed("the package should unpack to one folder, and gave %s" % sorted(p.name for p in into.iterdir()))
    build = json.loads((folders[0] / "studio" / "build.json").read_text(encoding="utf-8"))
    return folders[0], build


_TOKEN = ""


def set_token(token: str) -> None:
    global _TOKEN
    _TOKEN = token


def read_token(data_dir: Path) -> str:
    try:
        return (data_dir / "server-token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ask(base: str, method: str, path: str, body: Any = None, raw: bool = False,
        token: Optional[str] = None) -> Tuple[int, Any, Dict[str, str]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers: Dict[str, str] = {}
    if data is not None:
        req_headers["Content-Type"] = "application/json"
    active = _TOKEN if token is None else token
    if active:
        req_headers["X-Studio-Token"] = active
    request = urllib.request.Request(base + path, data=data, method=method, headers=req_headers)
    try:
        with _DIRECT.open(request, timeout=30) as response:
            payload, status, headers = response.read(), response.status, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        payload, status, headers = exc.read(), exc.code, dict(exc.headers.items())
    if raw:
        return status, payload, headers
    try:
        return status, json.loads(payload.decode("utf-8")), headers
    except ValueError:
        return status, payload[:300], headers


def stop(process: "subprocess.Popen[bytes]") -> None:
    """End the app and whatever its launcher started. A launcher may be a shell with Python under it."""
    if process.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=30)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def use(base: str, version: str, checks: List[str]) -> None:
    """Use the running app as a first visit does. Raises ``Failed`` at the first thing that is not as it should be."""
    def expect(what: str, ok: Any, seen: Any = "") -> None:
        if not ok:
            raise Failed("%s (got %s)" % (what, str(seen)[:300]))
        checks.append(what)

    status, page, headers = ask(base, "GET", "/", raw=True)
    expect("the page loads", status == 200 and b'<main id="view">' in page, status)
    expect("every answer carries the content security policy", "default-src" in headers.get("Content-Security-Policy", ""), headers)
    for name in ("app.js", "setup.js", "discover.js", "craft.js", "prompter.js", "kwimport.js", "posted.js", "wordlist.js", "refs.js", "pages.js",
                 "app.css"):
        status, body, headers = ask(base, "GET", "/" + name, raw=True)
        kind = "text/css" if name.endswith(".css") else "text/javascript"
        expect("/%s is served as %s" % (name, kind), status == 200 and len(body) > 500 and headers.get("Content-Type", "").startswith(kind),
               (status, headers.get("Content-Type")))
    status, state, _ = ask(base, "GET", "/api/state")
    expect("the app answers with its state", status == 200 and isinstance(state, dict) and "settings" in state, state)
    expect("it says which build it is: %s" % version, state.get("build", {}).get("version") == version and not state["build"]["from_source"],
           state.get("build"))
    status, made, _ = ask(base, "POST", "/api/scripts", {"title": "试一下", "lines": [{"spoken_script_zh": SPOKEN}]})
    expect("a script with Chinese text is saved", status == 201 and made.get("lines", [{}])[0].get("spoken_script_zh") == SPOKEN, made)
    script_id = made["id"]
    status, lint, _ = ask(base, "POST", "/api/lint", {"lines": made["lines"], "target_sec": 30})
    expect("the script is checked", status == 200 and lint.get("stats", {}).get("units") == 17 and "voice_match" in lint, lint)
    status, read, _ = ask(base, "GET", "/api/scripts/" + script_id)
    expect("it is read back as written", status == 200 and read["lines"][0]["spoken_script_zh"] == SPOKEN, read)
    status, text, headers = ask(base, "GET", "/api/scripts/%s/export/voiceover" % script_id, raw=True)
    expect("it exports as text", status == 200 and "这周又去超市补货啦" in text.decode("utf-8"), status)
    status, page, _ = ask(base, "GET", "/api/scripts/%s/export/prompter" % script_id, raw=True)
    expect("it exports as a teleprompter file", status == 200 and b"tpMount" in page, status)
    status, hooks, _ = ask(base, "GET", "/api/hooks")
    expect("the library answers", status == 200 and hooks.get("openers") == [], hooks)
    status, _, _ = ask(base, "DELETE", "/api/scripts/" + script_id)
    expect("the script is deleted", status == 200 and ask(base, "GET", "/api/scripts/" + script_id)[0] == 404, status)
    status, _, _ = ask(base, "GET", "/../../etc/passwd", raw=True)
    expect("a path out of the app's folder is refused", status >= 400, status)


def run_once(app: Path, command: List[str], version: str, how: str, checks: List[str], chinese: bool) -> None:
    """Start the app with ``command`` in its folder, use it, stop it.

    ``chinese``: what it prints must show its Chinese text as written. That is the launcher's doing (on Windows
    it sets up the window for it); Python started by hand on an older Windows encoding may print it replaced.
    """
    port = free_port()
    log_path = app.parent / ("log-%d.txt" % port)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("STUDIO_DATA_DIR", None)                         # the app keeps its data where a person's copy would
    env.pop("LEARN_MY_VOICE_WINDOW_CHECK", None)             # and is started the way a person starts it
    options: Dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with open(str(log_path), "wb") as log:
        process = subprocess.Popen(command + ["--no-browser", "--port", str(port)], cwd=str(app), stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, env=env, **options)
    base = "http://127.0.0.1:%d" % port
    try:
        deadline = time.time() + START_TIMEOUT_SEC
        token = ""
        while True:
            if process.poll() is not None:
                raise Failed("%s ended at once with code %s" % (how, process.returncode))
            token = read_token(app / "data")
            if token:
                try:
                    if ask(base, "GET", "/", raw=True, token=token)[0] == 200:
                        break
                except OSError:
                    pass
            if time.time() > deadline:
                raise Failed("%s did not answer within %d seconds" % (how, START_TIMEOUT_SEC))
            time.sleep(0.3)
        checks.append("%s starts the app" % how)
        if ask(base, "GET", "/api/state", token="")[0] != 401:
            raise Failed("unauthenticated local request to /api/state was not refused with 401")
        unauth_status, unauth_page, unauth_hdrs = ask(base, "GET", "/", raw=True, token="")
        if unauth_status != 401 or not unauth_hdrs.get("Content-Type", "").startswith("text/html"):
            raise Failed("GET / without launch link did not return 401 HTML explanation page (got %s, %r)"
                         % (unauth_status, unauth_hdrs.get("Content-Type")))
        checks.append("unauthenticated local requests are refused")
        boot_status, boot_page, boot_hdrs = ask(base, "GET", "/?token=" + token, raw=True, token="")
        if (boot_status != 200 or b'<main id="view">' not in boot_page
                or ("studio_session_%d=" % port) not in boot_hdrs.get("Set-Cookie", "")
                or boot_hdrs.get("Referrer-Policy") != "no-referrer"):
            raise Failed("launch link /?token=... did not bootstrap page + port-scoped cookie (got %s, %r)"
                         % (boot_status, boot_hdrs))
        checks.append("the launch link serves the page directly and sets the port-scoped session cookie")
        set_token(token)
        use(base, version, checks)
        # The check of the desktop app's window is a way for a script to steer the page. Started by a person the
        # app must not offer it, and the page must not be told to load it.
        if ask(base, "GET", "/api/window-check")[0] != 404 or "window_check" in ask(base, "GET", "/api/state")[1]:
            raise Failed("started the way a person starts it, the app offers the window check that is only for trials of the desktop app")
        checks.append("started the way a person starts it, it offers no window check")
        said = log_path.read_bytes().decode("utf-8", "replace")
        if "http://127.0.0.1:%d/" % port not in said:
            raise Failed("%s did not print the address to open (it printed %r)" % (how, said[:300]))
        checks.append("it prints the address to open")
        if chinese:
            if "工作台已启动" not in said:
                raise Failed("%s printed its Chinese text wrongly: %r" % (how, said[:300]))
            checks.append("and its Chinese text as written")
    except Failed as exc:
        tail = log_path.read_bytes().decode("utf-8", "replace")[-1500:] if log_path.exists() else ""
        raise Failed("%s\n--- what the app printed ---\n%s" % (exc, tail))
    finally:
        set_token("")
        stop(process)
    if not (app / "data").is_dir():
        raise Failed("the app did not keep its data in its own folder")
    checks.append("its data stays in its own folder")
    shutil.rmtree(str(app / "data"), ignore_errors=True)     # the second start begins as empty as the first


def try_package(package: Path, sha256: str = "") -> Dict[str, Any]:
    started = time.time()
    checks: List[str] = []
    record: Dict[str, Any] = {"ok": False, "python": platform.python_version(), "system": "%s %s" % (platform.system(), platform.release()),
                              "checks": checks, "error": ""}
    work = Path(tempfile.mkdtemp(prefix="try-release-"))
    try:
        if sha256:
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            if digest != sha256.strip().lower():
                raise Failed("the file has SHA-256 %s, not %s" % (digest, sha256))
            checks.append("the file matches its checksum")
        app, build = unpack(package, work)
        version = str(build.get("version"))
        record["version"] = version
        checks.append("it unpacks to one folder")
        launcher = LAUNCHERS.get(platform.system())
        if launcher is None:
            raise Failed("there is no launcher for %s" % platform.system())
        if not (app / launcher[-1]).is_file():
            raise Failed("the package has no %s" % launcher[-1])
        if os.name != "nt" and not os.access(str(app / launcher[-1]), os.X_OK):
            raise Failed("%s is not marked as a program, so double-clicking it would not start it" % launcher[-1])
        run_once(app, launcher, version, "the launcher (%s)" % launcher[-1], checks, chinese=True)
        run_once(app, [sys.executable, "-m", "studio.server"], version, "Python %s" % platform.python_version(), checks, chinese=False)
        record["ok"] = True
    except Failed as exc:
        record["error"] = str(exc)
    except Exception as exc:                                 # noqa: BLE001 - anything else is also "it did not work", with its name
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(str(work), ignore_errors=True)
    record["seconds"] = round(time.time() - started, 1)
    return record


def annotation(title: str, message: str, level: str = "error") -> str:
    """A GitHub workflow command that shows ``message`` at the top of the run's page. It has to stay on one line.

    ``level`` is "error", or "notice" for something worth reading that is not a failure.
    """
    def data(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return "::%s title=%s::%s" % ("notice" if level == "notice" else "error",
                                  data(title[:200]).replace(":", "%3A").replace(",", "%2C"), data(message[:3000]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Unpack a release package, start it with its launcher, use it, stop it.")
    # Each may come from the environment instead, so that a workflow's command line holds no variable and reads
    # the same in every shell.
    parser.add_argument("package", nargs="?", default=os.environ.get("TRY_PACKAGE", ""))
    parser.add_argument("--sha256", default=os.environ.get("TRY_SHA256", ""))
    parser.add_argument("--result", default=os.environ.get("TRY_RESULT", ""))
    parser.add_argument("--label", default=os.environ.get("TRY_LABEL", ""))
    args = parser.parse_args(argv)
    if not args.package:
        parser.error("which package? Give its path, or set TRY_PACKAGE")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")   # what the app printed is repeated below, Chinese included
        except (AttributeError, ValueError):
            pass
    record = try_package(Path(args.package), args.sha256)
    label = " ".join(args.label.split())[:120] or "%s / Python %s" % (platform.system(), platform.python_version())
    if args.result:                                          # first of all: whatever happens below, this is on record
        out = Path(args.result)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"kind": "try", "label": label, "ok": record["ok"], "outcome": "success" if record["ok"] else "failure",
                                   "detail": record["error"][:2000], "checks": len(record["checks"]), "python": record["python"],
                                   "system": record["system"]}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for check in record["checks"]:
        print("  ok    " + check)
    if not record["ok"]:
        print("  FAIL  " + record["error"])
    print("%s: %s, %d checks, %.1f s" % (label, "passed" if record["ok"] else "FAILED", len(record["checks"]), record["seconds"]), flush=True)
    if not record["ok"] and os.environ.get("GITHUB_ACTIONS") == "true":
        done = "After %d checks that passed (the last: %s).\n\n" % (len(record["checks"]), record["checks"][-1]) if record["checks"] else ""
        print(annotation("Trying the package on %s failed" % label, done + record["error"]), flush=True)
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
