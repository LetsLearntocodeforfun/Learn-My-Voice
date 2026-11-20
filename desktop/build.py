#!/usr/bin/env python3
"""Build the desktop app's installer, on the system this runs on.

    python3 desktop/build.py --package dist/learn-my-voice-<version>.zip [--out dist] [--result FILE] [--only-core]
    (the package, the folder and the file may also be given as DESKTOP_PACKAGE, DESKTOP_OUT and DESKTOP_RESULT)

What it does:

1. The core. The release package (the same zip people can download) is unpacked into
   desktop/src-tauri/core/app, without its launchers. A Python for this system is downloaded, checked against
   the SHA-256 written in this file, and put into desktop/src-tauri/core/python. So the installed app needs no
   Python on the computer, and what it runs is exactly the package that was tested.
2. The installer. The Tauri command-line tool compiles the shell (desktop/src-tauri) and makes a .dmg on macOS
   or a setup program on Windows.
3. The installer is copied to --out as learn-my-voice-<version>-<system>.<ending>, with its SHA-256 beside it.

Where the Python comes from. Windows: the "embeddable package" published by python.org. macOS: python.org
publishes no such package for macOS, so it is the build of the same Python version from the project
python-build-standalone (github.com/astral-sh/python-build-standalone), which exists to be put inside other
programs. Both are pinned by SHA-256 below; the sums were compared with the lists the two publishers give.
A download that does not match is refused.

This needs Rust and Node on the machine (GitHub's machines have both). Standard library only on the Python side.
When a step fails on GitHub Actions, the end of what it printed is shown as an annotation on the run's page.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
SHELL = HERE / "src-tauri"
CORE = SHELL / "core"
TAURI_CLI = "@tauri-apps/cli@2.12.5"
PYTHON = "3.14.10"
STANDALONE = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.14.10%2B20261110-"
SYSTEMS: Dict[str, Dict[str, str]] = {
    "windows-x64": {
        "python": "https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip",
        "sha256": "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159",
        "bundle": "nsis", "found_in": "nsis", "pattern": "*-setup.exe", "ending": "-setup.exe",
    },
    "macos-arm64": {
        "python": STANDALONE + "aarch64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02",
        "bundle": "dmg", "found_in": "dmg", "pattern": "*.dmg", "ending": ".dmg",
    },
    "macos-x64": {
        "python": STANDALONE + "x86_64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "85f8e54798e8e6aa58d2253f013d815c5616343cc0d15cbea882fca78bdfeae2",
        "bundle": "dmg", "found_in": "dmg", "pattern": "*.dmg", "ending": ".dmg",
    },
}
# The package's own ways of starting it. The installed app is started by its shell instead.
LAUNCHERS = ("Start Learn My Voice.command", "启动工作台.command", "Start Learn My Voice.bat", "start-learn-my-voice.sh")
# Parts of the macOS Python that this app never uses: its tests, the Tk window toolkit and what needs it, headers,
# the package installer (nothing is installed into the app after it is built), and the library for programs that
# embed Python (the Python program itself does not use it: `otool -L` shows it linked against system libraries only).
NOT_NEEDED = ("lib/python3.14/test/", "lib/python3.14/idlelib/", "lib/python3.14/tkinter/", "lib/python3.14/turtledemo/",
              "lib/python3.14/lib-dynload/_tkinter", "lib/python3.14/config-", "lib/python3.14/site-packages/",
              "lib/python3.14/ensurepip/", "lib/python3.14/pydoc_data/", "lib/libpython3.14.dylib", "lib/tcl", "lib/tk", "lib/itcl",
              "lib/thread", "lib/libtcl", "lib/libtk", "lib/pkgconfig/", "include/", "share/", "bin/idle", "bin/pydoc", "bin/pip",
              "bin/python3-config", "bin/python3.14-config")
MAX_DOWNLOAD = 80 * 1024 * 1024


class Failed(Exception):
    pass


def this_system() -> str:
    kind, machine = platform.system(), platform.machine().lower()
    if kind == "Windows" and machine in ("amd64", "x86_64"):
        return "windows-x64"
    if kind == "Darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    raise Failed("there is no desktop build for %s on %s yet" % (kind, machine))


def safe(name: str) -> bool:
    return not (name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/") or ":" in name.split("/")[0])


def fetch(url: str, sha256: str) -> bytes:
    """Download ``url`` and return it only if it is the file whose SHA-256 is written in this script."""
    if not url.startswith("https://"):
        raise Failed("refusing to download from %s" % url)
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise Failed("%s is larger than expected" % url)
    found = hashlib.sha256(data).hexdigest()
    if found != sha256:
        raise Failed("%s has SHA-256 %s, and %s was expected. It is not used." % (url, found, sha256))
    return data


def put_app(package: Path, target: Path) -> Dict[str, Any]:
    """Unpack the release package into ``target`` without its top folder and its launchers; returns its build file."""
    with zipfile.ZipFile(str(package)) as archive:
        for info in archive.infolist():
            if not safe(info.filename) or "/" not in info.filename:
                raise Failed("unexpected name in the package: %r" % info.filename)
            inside = info.filename.split("/", 1)[1]
            if not inside or inside.endswith("/") or inside in LAUNCHERS:
                continue
            out = target / inside
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode and os.name != "nt":
                os.chmod(str(out), mode)
    build_file = target / "studio" / "build.json"
    if not (target / "studio" / "server.py").is_file() or not build_file.is_file():
        raise Failed("the package does not hold the app (studio/server.py and studio/build.json)")
    return json.loads(build_file.read_text(encoding="utf-8"))


def put_python_windows(data: bytes, target: Path) -> None:
    """The embeddable package as it is, plus one change: the app's folder is added to where it looks for modules.

    That package looks for modules only where its ``._pth`` file says. It ignores the folder it is started in
    and every setting from outside, which is what is wanted here; so the app's folder has to be named there.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        paths = [name for name in names if name.endswith("._pth")]
        if len(paths) != 1 or "python.exe" not in names:
            raise Failed("the Python for Windows is not laid out as expected (python.exe and one ._pth file)")
        for info in archive.infolist():
            if not safe(info.filename):
                raise Failed("unexpected name in the Python for Windows: %r" % info.filename)
            if info.filename.endswith("/"):
                continue
            out = target / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(info))
        listed = archive.read(paths[0]).decode("utf-8").split()
    standard = [line for line in listed if line.endswith(".zip")]
    # Written as bytes: on Windows a text file would get that system's line ends, and the file is the same everywhere.
    (target / paths[0]).write_bytes(("\n".join(standard + [".", "..\\app"]) + "\n").encode("utf-8"))


def put_python_macos(data: bytes, target: Path) -> None:
    """The standalone Python without the parts this app never uses. Links inside it become plain copies."""
    links = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not safe(member.name) or not member.name.startswith("python/"):
                raise Failed("unexpected name in the Python for macOS: %r" % member.name)
            inside = member.name[len("python/"):]
            if not inside or inside.startswith(NOT_NEEDED):
                continue
            out = target / inside
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(archive.extractfile(member).read())
                os.chmod(str(out), member.mode & 0o755 | 0o644)
            elif member.issym() or member.islnk():
                links.append((out, member.linkname, member.issym()))
            else:
                raise Failed("unexpected kind of file in the Python for macOS: %r" % member.name)
    for out, to, relative in links:
        source = (out.parent / to) if relative else (target / to[len("python/"):] if to.startswith("python/") else target / to)
        source = Path(os.path.normpath(str(source)))
        if target not in source.parents or not source.is_file():
            continue                                        # it pointed at something that was left out
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(out))
        os.chmod(str(out), stat.S_IMODE(os.stat(str(source)).st_mode))
    if not (target / "bin" / "python3").is_file():
        raise Failed("the Python for macOS has no bin/python3")
    for same in ("python", "python3.14"):                   # the same program under other names: one copy is enough
        if (target / "bin" / same).is_file():
            (target / "bin" / same).unlink()


def make_core(package: Path, system: str) -> Dict[str, Any]:
    """Fill desktop/src-tauri/core for ``system``; returns the package's build file."""
    if CORE.exists():
        shutil.rmtree(str(CORE))
    build = put_app(package, CORE / "app")
    runtime = SYSTEMS[system]
    data = fetch(runtime["python"], runtime["sha256"])
    (put_python_windows if system.startswith("windows") else put_python_macos)(data, CORE / "python")
    (CORE / "runtime.json").write_bytes((json.dumps({"python": PYTHON, "from": runtime["python"], "sha256": runtime["sha256"]},
                                                    indent=1) + "\n").encode("utf-8"))
    return build


def run(command: List[str], cwd: Path, what: str) -> None:
    """Run a build command, showing what it prints as it comes. If it fails, the end of that is the error."""
    print("\n$ %s" % " ".join(command), flush=True)
    last: List[str] = []
    env = dict(os.environ, npm_config_ignore_scripts="true")
    process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        print(line, flush=True)
        last.append(line)
        del last[:-80]
    if process.wait() != 0:
        raise Failed("%s failed with code %d. The end of what it printed:\n%s" % (what, process.returncode, "\n".join(last)))


def tauri_command(npx: str, bundle: str, override: Path, locked: bool) -> List[str]:
    """The command that compiles the shell and makes the installer.

    ``locked`` says that the source holds the list of Rust packages to compile (``Cargo.lock``, taken from the
    first build that worked). What follows ``--`` goes to Cargo: with ``--locked`` it uses exactly the versions on
    that list, and fails instead of choosing others if the list no longer fits ``Cargo.toml``. Without the list
    Cargo picks the newest versions that fit, which is how the list was first made.
    """
    command = [npx, "--yes", TAURI_CLI, "build", "--ci", "--bundles", bundle, "--config", str(override)]
    return command + ["--", "--locked"] if locked else command


def make_installer(system: str, version: str, out: Path) -> Path:
    """Compile the shell, make the installer, and copy it to ``out`` under its release name."""
    npx = shutil.which("npx")
    if not npx:
        raise Failed("npx (it comes with Node) was not found; the Tauri command-line tool is started with it")
    # The installer formats want a version made of numbers only. The exact version is in the app itself
    # (studio/build.json, shown on the settings page) and in the installer's file name.
    numbers = re.match(r"\d+\.\d+\.\d+", version)
    if not numbers:
        raise Failed("the package's version does not start with three numbers: %r" % version)
    override = SHELL / "release.conf.json"
    override.write_text(json.dumps({"version": numbers.group(0)}), encoding="utf-8")
    runtime = SYSTEMS[system]
    try:
        run(tauri_command(npx, runtime["bundle"], override, (SHELL / "Cargo.lock").is_file()), HERE, "building the desktop app")
    finally:
        override.unlink()
    made = sorted((SHELL / "target" / "release" / "bundle" / runtime["found_in"]).glob(runtime["pattern"]))
    if len(made) != 1:
        raise Failed("expected one installer, found %s" % [path.name for path in made])
    out.mkdir(parents=True, exist_ok=True)
    target = out / ("learn-my-voice-%s-%s%s" % (version, system, runtime["ending"]))
    shutil.copyfile(str(made[0]), str(target))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / (target.name + ".sha256")).write_text("%s  %s\n" % (digest, target.name), encoding="utf-8")
    lock = SHELL / "Cargo.lock"
    if lock.is_file():                                      # which versions of the Rust packages this build used
        shutil.copyfile(str(lock), str(out / ("Cargo-%s.lock" % system)))
    print("\n%s  %s  (%.1f MB)" % (digest, target.name, target.stat().st_size / 1e6), flush=True)
    return target


def annotation(title: str, message: str) -> str:
    """A GitHub workflow command that shows ``message`` at the top of the run's page. It has to stay on one line."""
    def data(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return "::error title=%s::%s" % (data(title[:200]).replace(":", "%3A").replace(",", "%2C"), data(message[-6000:]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the desktop app's installer on this system.")
    parser.add_argument("--package", default=os.environ.get("DESKTOP_PACKAGE", ""))
    parser.add_argument("--out", default=os.environ.get("DESKTOP_OUT", "dist"))
    parser.add_argument("--result", default=os.environ.get("DESKTOP_RESULT", ""))
    parser.add_argument("--system", default="", choices=[""] + sorted(SYSTEMS))
    parser.add_argument("--only-core", action="store_true", help="stop after filling desktop/src-tauri/core")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    system = args.system
    try:
        if not args.package:
            raise Failed("which package? Give --package, or set DESKTOP_PACKAGE")
        system = system or this_system()
        build = make_core(Path(args.package), system)
        files = sum(1 for path in CORE.rglob("*") if path.is_file())
        print("core for %s: package %s, Python %s, %d files" % (system, build.get("version"), PYTHON, files), flush=True)
        if not args.only_core:
            make_installer(system, str(build.get("version")), Path(args.out))
    except Failed as problem:
        print("FAILED: %s" % problem, flush=True)
        if args.result:                                     # the trial will not run: this is what is on record for the system
            out = Path(args.result)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"kind": "desktop", "label": system or "unknown", "ok": False, "outcome": "failure",
                                       "detail": ("The installer could not be built.\n" + str(problem))[-2000:]},
                                      ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(annotation("Building the desktop app on %s failed" % (system or "this system"), str(problem)), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
