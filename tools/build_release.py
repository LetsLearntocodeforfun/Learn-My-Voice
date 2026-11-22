#!/usr/bin/env python3
"""Build the package people download, and write what a release says about it.

    python3 tools/build_release.py build [--rc N] [--attempt N] [--out dist]
    python3 tools/build_release.py verify <zip> --sha256 <hex>
    python3 tools/build_release.py result --kind tests --label "macos-latest / Python 3.9" --outcome success --out FILE [--if-missing]
        (label, outcome and file may also be given as RESULT_LABEL, RESULT_OUTCOME and RESULT_FILE)
    python3 tools/build_release.py notes --package <zip> --results DIR [--require macos] [--require linux:tests] [--legs tests=3] [--run-url URL]

``build`` makes ``learn-my-voice-<version>.zip`` and ``<zip>.sha256``. The package is the files of the commit
that is checked out (``HEAD``), read from git and not from the folder, so what is in it does not depend on
the system it is built on or on changes that were never committed. Left out: the tests, these tools and the
GitHub workflows. Added: ``studio/build.json``, which lets the app say which build it is.

The same commit always gives the same bytes: names are sorted, every file carries the time of the commit, file
modes come from git, and nothing is compressed (compressed bytes can differ between systems). So anyone can
build the package again from the source and compare its SHA-256 with the published one.

The version is the first ``## v...`` heading of CHANGELOG.md. With ``--rc N`` it becomes a release candidate,
``<version>.rc.N``; without, a local build, ``<version>.dev``.

Standard library only. This file is used by .github/workflows/release-candidate.yml and runs the same here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
NAME = "learn-my-voice"
# What a person who only wants to use the app has no use for. Everything else that is committed goes in.
LEFT_OUT = (".github/", "tests/", "tools/", "desktop/", ".gitattributes", ".gitignore", "AUDIT_AND_REMEDIATION_LOG.md",
            "docs/AGENT_TEAM_ROADMAP.md")
BUILD_FILE = "studio/build.json"
LAUNCHERS = {"macos": "Start Learn My Voice.command", "windows": "Start Learn My Voice.bat", "linux": "start-learn-my-voice.sh"}
SYSTEMS = (("macos", "macOS"), ("windows", "Windows"), ("linux", "Linux"))
OUTCOMES = ("success", "failure", "cancelled", "skipped")
# What a job can report on: the test suite, a trial of the package, a trial of the desktop app's installer.
KINDS = ("tests", "try", "desktop")
INSTALLERS = (("macos-arm64", ".dmg", "macOS, Apple chip"), ("macos-x64", ".dmg", "macOS, Intel chip"),
              ("windows-x64", "-setup.exe", "Windows, 64-bit"))
_VERSION_HEADING = re.compile(r"^## v([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)\b", re.M)


def git(*args: str, data: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", *args], cwd=str(ROOT), input=data, capture_output=True, check=True).stdout


def base_version(changelog: Optional[str] = None) -> str:
    """The version being worked towards: the first ``## v...`` heading of the changelog, without the ``v``."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") if changelog is None else changelog
    found = _VERSION_HEADING.search(text)
    if not found:
        raise SystemExit("CHANGELOG.md has no '## v<version>' heading to take the version from")
    return found.group(1)


def version_for(base: str, rc: Optional[int], attempt: int = 1, *, release: bool = False) -> str:
    """``<base>`` for a tagged release, ``<base>.rc.N`` for a release candidate, ``<base>.dev`` otherwise."""
    if release:
        if rc is not None:
            raise SystemExit("--release and --rc cannot be used together")
        return base
    if rc is None:
        return base + ".dev"
    if rc < 1 or attempt < 1:
        raise SystemExit("--rc and --attempt have to be 1 or more")
    return "%s.rc.%d%s" % (base, rc, "" if attempt == 1 else ".%d" % attempt)


def committed_files() -> List[Tuple[str, int, str]]:
    """``(path, mode, blob id)`` for every file of HEAD that belongs in the package, sorted by path."""
    out = []
    for entry in git("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = meta.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit("%s is not an ordinary file (%s %s); a package holds ordinary files only" % (path, mode, kind))
        if any(path == rule or path.startswith(rule) for rule in LEFT_OUT) or path == BUILD_FILE:
            continue
        out.append((path, 0o755 if mode == "100755" else 0o644, blob))
    return sorted(out)


def build_package(out_dir: Path, rc: Optional[int] = None, attempt: int = 1, *, release: bool = False) -> Dict[str, Any]:
    """Write the zip and its checksum file into ``out_dir``; returns ``{"version", "tag", "file", "path", "sha256", "files", "commit"}``."""
    version = version_for(base_version(git("show", "HEAD:CHANGELOG.md").decode("utf-8")), rc, attempt, release=release)
    commit = git("rev-parse", "HEAD").decode("ascii").strip()
    committed_at = int(git("show", "-s", "--format=%ct", "HEAD").decode("ascii").strip())
    stamp = time.gmtime(committed_at)[:6]                    # the time of the commit, in UTC: the same on every system
    day = time.strftime("%Y-%m-%d", time.gmtime(committed_at))
    folder = "%s-%s" % (NAME, version)
    files = committed_files()
    for needed in list(LAUNCHERS.values()) + ["studio/server.py", "studio/web/index.html", "README.md", "LICENSE"]:
        if needed not in {path for path, _, _ in files}:
            raise SystemExit("the commit has no %s; the package would not start" % needed)
    build_info = json.dumps({"version": version, "commit": commit, "built_on": day}, sort_keys=True).encode("utf-8") + b"\n"
    members = [(path, mode, git("cat-file", "blob", blob)) for path, mode, blob in files] + [(BUILD_FILE, 0o644, build_info)]

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (folder + ".zip")
    with zipfile.ZipFile(str(target), "w", zipfile.ZIP_STORED) as package:
        for path, mode, data in sorted(members):
            info = zipfile.ZipInfo("%s/%s" % (folder, path), date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3                           # Unix, so that the file modes below mean something when unpacked
            info.external_attr = (0o100000 | mode) << 16
            package.writestr(info, data)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out_dir / (target.name + ".sha256")).write_text("%s  %s\n" % (digest, target.name), encoding="utf-8")
    return {"version": version, "tag": "v" + version, "file": target.name, "path": str(target), "sha256": digest,
            "files": len(members), "commit": commit}


def verify_package(path: Path, sha256: str) -> Dict[str, Any]:
    """Check a package against its checksum and against what a package must be; returns what its build file says."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256.strip().lower():
        raise SystemExit("%s has SHA-256 %s, not %s" % (path.name, digest, sha256))
    with zipfile.ZipFile(str(path)) as package:
        names = package.namelist()
        tops = {name.split("/", 1)[0] for name in names}
        if len(tops) != 1:
            raise SystemExit("the package should hold one folder, and holds %s" % sorted(tops))
        folder = tops.pop()
        for name in names:
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise SystemExit("unsafe name in the package: %r" % name)
        inside = {name[len(folder) + 1:] for name in names}
        for kept_out in ("data/", "local_tools/", "tests/", ".git/", ".github/"):
            if any(name.startswith(kept_out) for name in inside):
                raise SystemExit("the package holds %s, which must never be in it" % kept_out)
        build = json.loads(package.read("%s/%s" % (folder, BUILD_FILE)).decode("utf-8"))
    if folder != "%s-%s" % (NAME, build.get("version")):
        raise SystemExit("the folder %s does not match the version in its build file (%s)" % (folder, build.get("version")))
    return build


# ---- what each system found, and the notes of a release ---------------------------------------------------
def system_of(label: str) -> Optional[str]:
    """Which of macos, windows, linux a job label such as ``windows-latest / Python 3.13`` is about."""
    lowered = label.lower()
    if lowered.startswith("macos"):
        return "macos"
    if lowered.startswith("windows"):
        return "windows"
    if lowered.startswith("ubuntu") or lowered.startswith("linux"):
        return "linux"
    return None


def write_result(out: Path, kind: str, label: str, outcome: str, detail: str = "") -> Dict[str, Any]:
    """Write down what one job found, for the job that publishes to read. ``outcome`` is the step's own."""
    if kind not in KINDS:
        raise SystemExit("--kind is one of: %s" % ", ".join(KINDS))
    record = {"kind": kind, "label": " ".join(label.split())[:120], "ok": outcome == "success",
              "outcome": outcome if outcome in OUTCOMES else "failure", "detail": detail[:2000]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def _one_result(path: Path) -> Optional[Dict[str, Any]]:
    """The result in the file at ``path``, or None when there is no file or it does not hold one."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("kind") not in KINDS or not isinstance(record.get("label"), str):
        return None
    return record


def result_is_written(path: Path) -> bool:
    return _one_result(path) is not None


def read_results(folder: Path) -> List[Dict[str, Any]]:
    """Every result file under ``folder``. One that cannot be read counts as a failure of whatever it was for."""
    found = []
    for path in sorted(folder.rglob("*.json")) if folder.is_dir() else []:
        record = _one_result(path)
        if record is None:
            record = {"kind": "try", "label": path.stem, "ok": False, "outcome": "failure", "detail": "the result file could not be read"}
        found.append({"kind": record["kind"], "label": " ".join(record["label"].replace("`", "'").split())[:120],
                      "ok": record.get("ok") is True, "detail": str(record.get("detail") or "")})
    return found


def summarize(results: List[Dict[str, Any]], legs: Optional[Dict[str, int]] = None) -> Dict[str, Dict[str, Any]]:
    """Per system and kind: ``{"passed", "failed", "failed_labels", "told"}``.

    ``told`` is, for each job that failed and said why, ``(label, what it said)``.

    ``legs`` says how many jobs of a kind run on each system (``{"tests": 3, "try": 2}``). A job that left no
    result at all (it never got as far as writing one) then counts as failed, not as absent: three jobs of
    which two reported "passed" is not "passed". Without ``legs`` only what was reported is counted.
    """
    table: Dict[str, Dict[str, Any]] = {}
    for system, shown in SYSTEMS:
        for kind in KINDS:
            rows = [r for r in results if r["kind"] == kind and system_of(r["label"]) == system]
            passed = sum(1 for r in rows if r["ok"])
            failed_labels = [r["label"] for r in rows if not r["ok"]]
            told = [(r["label"], r["detail"].strip()) for r in rows if not r["ok"] and r["detail"].strip()]
            silent = max(0, (legs or {}).get(kind, 0) - len(rows))
            if silent:
                failed_labels.append("%s: %d %s job%s left no result" % (shown, silent, kind, "" if silent == 1 else "s"))
            table["%s/%s" % (system, kind)] = {"passed": passed, "failed": len(rows) - passed + silent, "failed_labels": failed_labels,
                                               "told": told}
    return table


def required(require: List[str]) -> List[Tuple[str, str]]:
    """``["macos", "linux:tests"]`` as ``(system, kind)`` pairs: a system alone stands for both checks of the
    package, its tests and its trial. The desktop app is asked for by name (``windows:desktop``)."""
    out: List[Tuple[str, str]] = []
    for given in require:
        system, _, kind = given.partition(":")
        if system not in dict(SYSTEMS) or kind not in ("", "tests", "try", "desktop"):
            raise SystemExit("--require takes macos, windows or linux, alone or followed by :tests, :try or :desktop")
        out += [(system, one) for one in (("tests", "try") if not kind else (kind,)) if (system, one) not in out]
    return out


def _cell(found: Dict[str, Any]) -> str:
    if not found["passed"] and not found["failed"]:
        return "not run"
    if not found["failed"]:
        return "passed (%d of %d)" % (found["passed"], found["passed"])
    return "FAILED (%d of %d passed)" % (found["passed"], found["passed"] + found["failed"])


def changelog_section(version_heading: str) -> str:
    text = git("show", "HEAD:CHANGELOG.md").decode("utf-8")
    found = re.search(r"^## v%s\b[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(version_heading), text, re.S | re.M)
    return found.group(1).strip() if found else ""


def release_notes(build: Dict[str, Any], sha256: str, file_name: str, results: List[Dict[str, Any]], require: List[str],
                  run_url: str = "", branch: str = "", legs: Optional[Dict[str, int]] = None,
                  installers: Optional[List[Dict[str, str]]] = None,
                  sbom_sha256: str = "", sbom_name: str = "") -> Tuple[str, List[str]]:
    """``(notes, problems)``. ``problems`` is not empty when something required did not pass: then nothing should
    be published.

    A system required for ``tests`` or ``try`` needs every one of those jobs to have passed. A system required for
    ``desktop`` needs one installer of the desktop app that passed its own trial and is among ``installers``, so
    that a person on that system has something to install. Where a system has two (macOS: Apple chip and Intel
    chip), one that failed does not stop the release while the other passed: it is not attached, and the notes
    say that it failed.
    """
    version = build["version"]
    base = version.split(".rc.")[0].split(".dev")[0]
    table = summarize(results, legs)
    passed_trial = {r["label"] for r in results if r["kind"] == "desktop" and r["ok"]}
    installable = {system_of(entry["system"]) for entry in (installers or []) if entry["system"] in passed_trial}
    problems = []
    for system, kind in required(require):
        found = table["%s/%s" % (system, kind)]
        if kind == "desktop":
            if system not in installable:
                problems.append("%s: no installer of the desktop app that passed its trial is there to attach" % system)
        elif found["failed"] or not found["passed"]:
            problems.append("%s: %s %s" % (system, "the tests" if kind == "tests" else "starting the package",
                                           "failed" if found["failed"] else "did not run"))
    is_final_release = (".rc." not in version) and (not version.endswith(".dev"))
    if is_final_release:
        lines = [
            "**Release %s.** Built by GitHub Actions from commit `%s`%s."
            % (base, build["commit"][:12], " on branch `%s`" % branch if branch else ""),
        ]
    else:
        lines = [
            "**A release candidate of %s.** Built by GitHub Actions from commit `%s`%s. It is for trying out, and is not the finished %s."
            % (base, build["commit"][:12], " on branch `%s`" % branch if branch else "", base),
        ]
    # The desktop app comes first when there is one: it is what a person who only wants to use the app installs.
    desktop = desktop_section(results, installers or [])
    lines += desktop + [
        "",
        "### The zip, for a computer that has Python" if desktop else "### Get it",
        "",
        "Download **%s** below and unpack it. You need Python 3.9 or newer and nothing else." % file_name,
        "",
        "| System | Start it with | If Python is missing |",
        "| :--- | :--- | :--- |",
        "| macOS | double-click `%s` (or `启动工作台.command`) | the launcher offers Apple's free Command Line Tools |" % LAUNCHERS["macos"],
        "| Windows | double-click `%s` | install Python from the Microsoft Store |" % LAUNCHERS["windows"],
        "| Linux | `sh %s` in a terminal | install `python3` with the system's package manager |" % LAUNCHERS["linux"],
        "",
        "macOS refuses to open the launcher the first time, because this project has no paid signing certificate. "
        "On macOS 15 or newer: open System Settings, go to Privacy & Security, scroll down and click Open Anyway. "
        "On older versions: right-click the file, choose Open, and confirm. This is needed once.",
        "",
        "### What was checked for this build",
        "",
        "Each line is what GitHub's machines found for this exact file. No person has used this build on any system.",
        "",
        "| System | Test suite | Unpacked, started with its launcher, and used |",
        "| :--- | :--- | :--- |",
    ]
    for system, shown in SYSTEMS:
        lines.append("| %s | %s | %s |" % (shown, _cell(table[system + "/tests"]), _cell(table[system + "/try"])))
    failed = sorted({label for key, found in table.items() if not key.endswith("/desktop") for label in found["failed_labels"]})
    if failed:
        lines += ["", "Failed: " + "; ".join(failed) + ". A system with a failure here should be taken as not working yet."]
    # What the failed jobs said about it, once for each different account (three Python versions often say the same).
    accounts: Dict[Tuple[str, str], List[str]] = {}
    for system, _ in SYSTEMS:
        for kind in KINDS:
            for label, said in table["%s/%s" % (system, kind)]["told"]:
                accounts.setdefault((kind, said), []).append(label)
    if accounts:
        lines += ["", "What those jobs reported:"]
        for (kind, said), labels in accounts.items():
            lines += ["", "%s, %s:" % ("; ".join(labels), {"tests": "the test suite", "try": "starting the package"}.get(kind, "the desktop app")),
                      "", "~~~~text", _fenced(said), "~~~~"]
    if run_url:
        lines += ["", "Every step of these checks: %s" % run_url]
    lines += [
        "",
        "### Checking the download",
        "",
        "SHA-256 of `%s`:" % file_name,
        "",
        "```",
        sha256,
        "```",
        "",
        "The package is built so that the same commit always gives the same bytes. From a copy of the source at "
        "commit `%s`, `python3 tools/build_release.py build%s` makes the same file."
        % (build["commit"][:12], _rc_arguments(version)),
    ]
    if sbom_sha256 and sbom_name:
        lines += [
            "",
            "CycloneDX 1.5 SBOM (`%s`):" % sbom_name,
            "",
            "```",
            "%s  %s" % (sbom_sha256, sbom_name),
            "```",
        ]
    body = changelog_section(base)
    if body:
        lines += ["", "### What is in %s" % base, "", body]
    return "\n".join(lines) + "\n", problems


def find_installers(folder: Optional[Path], version: str) -> List[Dict[str, str]]:
    """The installers in ``folder`` that belong to ``version``: ``{"system", "shown", "name", "sha256", "path"}``."""
    found = []
    for system, ending, shown in INSTALLERS:
        path = (folder / ("%s-%s-%s%s" % (NAME, version, system, ending))) if folder else None
        if path is not None and path.is_file():
            found.append({"system": system, "shown": shown, "name": path.name, "path": str(path),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return found


def desktop_section(results: List[Dict[str, Any]], installers: List[Dict[str, str]]) -> List[str]:
    """What the notes say about the desktop app: which installers there are, and what a trial of each found.

    An installer is offered only when its trial passed. One that was built and failed its trial is named as
    not attached, with what the trial reported further up. Nothing at all is said when no desktop job ran.
    """
    reports = {r["label"]: r for r in results if r["kind"] == "desktop"}
    built = {entry["system"]: entry for entry in installers}
    if not reports and not built:
        return []
    lines = ["", "### Desktop app", "",
             "New, and no person has used it yet. It is the same app in a window of its own, installed like any other "
             "program, and it needs no Python on the computer: Python 3.14 is inside it.", "",
             "On GitHub's machines each installer below was installed and the app opened. Then the page in the app's own "
             "window used itself through its own buttons: it opened a script, exported it (the file was then read back "
             "from the disk), followed a link to another site, and deleted the script through the question the page asks. "
             "What each trial did and found, in words, is in the file `desktop-<system>-trial.txt` attached below.", "",
             "| System | File | Installed, opened and used on GitHub's machine |", "| :--- | :--- | :--- |"]
    offered = []
    for system, _ending, shown in INSTALLERS:
        report, entry = reports.get(system), built.get(system)
        if report is None and entry is None:
            continue
        if report is not None and report["ok"] and entry is not None:
            offered.append(entry)
            lines.append("| %s | `%s` | passed |" % (shown, entry["name"]))
        elif entry is not None:
            lines.append("| %s | not attached | FAILED: it was built, and the trial of it did not pass |" % shown)
        else:
            lines.append("| %s | none | FAILED: it could not be built |" % shown)
    if offered:
        lines += ["", "SHA-256:", "", "```"] + ["%s  %s" % (entry["sha256"], entry["name"]) for entry in offered] + ["```", "",
                  "The installers are not signed, because this project has no paid certificates. So the system stops them the "
                  "first time, and has to be told once that you want this:", "",
                  "- **macOS:** open the `.dmg` and drag Learn My Voice to Applications. Open it; macOS refuses. Then open "
                  "System Settings, go to Privacy & Security, scroll down and click Open Anyway. "
                  "(打开 `.dmg`，把 Learn My Voice 拖到「应用程序」。第一次打开会被拦住：请到「系统设置」→「隐私与安全性」，往下找到「仍要打开」。)",
                  "- **Windows:** run the setup program. When \"Windows protected your PC\" appears, click More info, then "
                  "Run anyway. (运行安装程序。出现「Windows 已保护你的电脑」时，点「更多信息」，再点「仍要运行」。)", "",
                  "On a computer managed by an employer or a school this may not be allowed at all. "
                  "In the desktop app an export is not a download: the app puts the file into the Downloads folder itself "
                  "and says where it went. A link to another site opens in the system's browser. "
                  "Speaking an idea on a Mac: the app now says what it wants the microphone for and is signed with the right "
                  "to it, which the trial reads back; a recording made in the desktop app has not been tried by anyone. "
                  "To transcribe videos, the desktop app needs a Mac with an Apple chip and Apple's free Command Line Tools; it "
                  "says how to get them. That was tried with the app started from its source, not inside the desktop "
                  "app. "
                  "What the desktop app keeps (scripts, library, settings) is in the system's folder for application data, "
                  "not in the app, so installing a newer version keeps it."]
    return lines


def _fenced(text: str, limit: int = 1500) -> str:
    """Reported text for the inside of a ``~~~~`` block: it cannot end the block, and it is not endless."""
    text = text.replace("\r", "")
    while "~~~~" in text:
        text = text.replace("~~~~", "~~~ ~")
    return text if len(text) <= limit else text[:limit].rstrip() + "\n(cut here; the run has the rest)"


def _rc_arguments(version: str) -> str:
    found = re.search(r"\.rc\.(\d+)(?:\.(\d+))?$", version)
    if found:
        return " --rc %s%s" % (found.group(1), " --attempt %s" % found.group(2) if found.group(2) else "")
    if not version.endswith(".dev"):
        return " --release"
    return ""


def _github_output(values: Dict[str, Any]) -> None:
    """Hand values to later steps of a GitHub job. Each is one line of plain characters made here."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text or "\r" in text:
                raise SystemExit("refusing to write a value with a line break to GITHUB_OUTPUT")
            handle.write("%s=%s\n" % (key, text))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the release package and write what a release says about it.")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--rc", type=int)
    build.add_argument("--attempt", type=int, default=1)
    build.add_argument("--release", action="store_true", help="stamp the exact base version from CHANGELOG.md without .rc or .dev")
    build.add_argument("--expect-tag", default="", help="when non-empty, fail unless the built tag matches this exact git tag")
    build.add_argument("--out", default="dist")
    verify = commands.add_parser("verify")
    verify.add_argument("package")
    verify.add_argument("--sha256", required=True)
    result = commands.add_parser("result")
    # Label, outcome and file may come from the environment instead, so that a workflow's command line holds no
    # variable and reads the same in every shell.
    result.add_argument("--kind", required=True)
    result.add_argument("--label", default=os.environ.get("RESULT_LABEL", ""))
    result.add_argument("--outcome", default=os.environ.get("RESULT_OUTCOME", ""))
    result.add_argument("--out", default=os.environ.get("RESULT_FILE", ""))
    result.add_argument("--if-missing", action="store_true",
                        help="write only when there is no result yet, and then as a failure: the step that should have written it did not")
    notes = commands.add_parser("notes")
    notes.add_argument("--package", required=True)
    notes.add_argument("--results", required=True)
    notes.add_argument("--require", action="append", default=[], metavar="SYSTEM[:KIND]",
                       help="what has to have passed for the notes to be usable, e.g. macos (its tests and its trial), linux:tests, or "
                            "windows:desktop (an installer for it that passed its own trial)")
    notes.add_argument("--legs", action="append", default=[], metavar="KIND=N",
                       help="how many jobs of a kind run on each system, e.g. tests=3; a job without a result then counts as failed")
    notes.add_argument("--run-url", default="")
    notes.add_argument("--installers", default="", metavar="DIR", help="where the desktop app's installers and pictures are, if any were built")
    notes.add_argument("--attach", default="", metavar="FILE",
                       help="write the files a release should carry to FILE, one on a line: the package, its checksum, and each installer whose trial passed")
    notes.add_argument("--sbom", default="", metavar="FILE", help="optional CycloneDX SBOM JSON file to checksum, cite, and attach")
    notes.add_argument("--branch", default="")
    notes.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.command == "build":
        if git("status", "--porcelain", "--untracked-files=no").strip():
            print("Note: there are changes that are not committed. They are NOT in the package, which is built from HEAD.", file=sys.stderr)
        made = build_package(Path(args.out), args.rc, args.attempt, release=bool(args.release))
        if args.expect_tag and made["tag"] != args.expect_tag.strip():
            raise SystemExit("pushed tag %s does not match CHANGELOG.md tag %s" % (args.expect_tag.strip(), made["tag"]))
        verify_package(Path(made["path"]), made["sha256"])
        print(json.dumps(made, indent=1))
        _github_output({key: made[key] for key in ("version", "tag", "file", "sha256")})
        return 0
    if args.command == "verify":
        print(json.dumps(verify_package(Path(args.package), args.sha256), indent=1))
        return 0
    if args.command == "result":
        if not args.label or not args.out:
            raise SystemExit("result needs --label and --out (or RESULT_LABEL and RESULT_FILE)")
        out = Path(args.out)
        if args.if_missing and result_is_written(out):
            print("%s is there already and is left as it is" % out)
            return 0
        if args.if_missing:
            # The step that writes the result did not get that far. Whatever colour it had, nothing reported a pass.
            written = write_result(out, args.kind, args.label, args.outcome if args.outcome in OUTCOMES[1:] else "failure",
                                   "The step left no result (GitHub called its outcome: %s)." % (args.outcome or "none"))
        else:
            written = write_result(out, args.kind, args.label, args.outcome)
        print(json.dumps(written))
        return 0
    package = Path(args.package)
    sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
    legs: Dict[str, int] = {}
    for given in args.legs:
        kind, _, count = given.partition("=")
        if kind not in ("tests", "try") or not count.isdigit():
            raise SystemExit("--legs takes tests=N or try=N")
        legs[kind] = int(count)
    required(args.require)                                   # a mistyped --require stops here, before anything is written
    build_file, results = verify_package(package, sha256), read_results(Path(args.results))
    folder = Path(args.installers) if args.installers else None
    installers = find_installers(folder, build_file["version"])
    sbom_path = Path(args.sbom) if args.sbom else None
    sbom_sha256, sbom_name = "", ""
    if sbom_path is not None:
        if not sbom_path.is_file():
            raise SystemExit("SBOM file %s does not exist" % sbom_path)
        sbom_sha256 = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
        sbom_name = sbom_path.name
    text, problems = release_notes(build_file, sha256, package.name, results, args.require, args.run_url,
                                   " ".join(args.branch.split())[:100], legs, installers, sbom_sha256, sbom_name)
    if args.attach:
        passed = {r["label"] for r in results if r["kind"] == "desktop" and r["ok"]}
        files = [str(package), str(package) + ".sha256"]
        if sbom_path is not None:
            sbom_sum_path = Path(str(sbom_path) + ".sha256")
            sbom_sum_path.write_text("%s  %s\n" % (sbom_sha256, sbom_name), encoding="utf-8")
            files += [str(sbom_path), str(sbom_sum_path)]
        for entry in installers:
            if entry["system"] in passed:
                Path(entry["path"] + ".sha256").write_text("%s  %s\n" % (entry["sha256"], entry["name"]), encoding="utf-8")
                files += [entry["path"], entry["path"] + ".sha256"]
        # Pictures of the screen from the trials, each trial in words (every check, and what the page reported from
        # the app's own window), and the list of Rust packages each build used: small, and worth keeping.
        files += sorted(str(path) for path in (folder.glob("desktop-*.png") if folder else [])) \
            + sorted(str(path) for path in (folder.glob("desktop-*-trial.txt") if folder else [])) \
            + sorted(str(path) for path in (folder.glob("Cargo-*.lock") if folder else []))
        Path(args.attach).write_text("".join(name + "\n" for name in files if Path(name).is_file()), encoding="utf-8")
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    for problem in problems:
        print("NOT PUBLISHING: " + problem, file=sys.stderr)
    if problems and os.environ.get("GITHUB_ACTIONS") == "true":
        # Shown at the top of the run's page, where it can be read without signing in. One line, made of words from here.
        print("::error title=Not published::" + "; ".join(problems).replace("%", "%25").replace("\n", " ").replace("\r", " "), flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
