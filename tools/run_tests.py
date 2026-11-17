#!/usr/bin/env python3
"""Run the test suite, and say what did not pass where it can be read.

    python3 tools/run_tests.py [--label TEXT] [--result FILE]

Runs what ``python3 -m unittest discover -t . -s tests`` runs and prints what that prints. Afterwards:

- with ``--result``, what happened is written to a small file: how many tests ran and, for those that did not
  pass, which and why. A release candidate's notes are written from these files.
- on GitHub Actions, the tests that did not pass are also printed as error annotations, grouped by what went
  wrong. Annotations stand at the top of a run's page and can be read without signing in; the log cannot.

``--label`` and ``--result`` default to the environment variables TESTS_LABEL and TESTS_RESULT, so the command
is the same line in every shell.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
# GitHub shows at most ten error annotations for one step: one says how many did not pass, nine say why.
MAX_ANNOTATIONS = 10
MAX_MESSAGE = 6000
MAX_DETAIL = 2000
MAX_CAUSE = 140                         # of a cause, in a list of causes; the annotation for that cause holds it whole


def cause_of(trace: str) -> str:
    """The line of a traceback that names what went wrong: ``AssertionError: 1 != 2``."""
    lines = trace.rstrip().split("\n")
    start = 0
    for at, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last):"):
            start = at + 1                                   # of several chained tracebacks, the last one
    for line in lines[start:]:
        if line and not line[0].isspace():
            return line.strip()[:300]
    return (lines[-1].strip() if lines and lines[-1].strip() else "no reason was given")[:300]


def cause_key(cause: str) -> str:
    """What two causes have in common when they differ only in a path, an address or a number.

    A hundred tests that fail because a hundred different temporary files could not be removed are one
    problem, and are shown as one.
    """
    text = re.sub(r"0x[0-9A-Fa-f]+", "0x…", cause)
    text = re.sub(r"[A-Za-z]:\\\\?[^\s'\"]+|(?<![\w.])/[^\s'\"]+", "<path>", text)
    text = re.sub(r"\d+", "N", text)
    return text[:160]


def collect(result: unittest.TestResult) -> List[Dict[str, str]]:
    """Every test that did not pass: ``{"test", "kind", "cause", "trace"}``."""
    out = []
    for kind, pairs in (("error", result.errors), ("failure", result.failures)):
        for test, trace in pairs:
            name = test.id() if hasattr(test, "id") else str(test)
            out.append({"test": name, "kind": kind, "cause": cause_of(trace), "trace": trace})
    for test in getattr(result, "unexpectedSuccesses", []):
        out.append({"test": test.id(), "kind": "unexpected success", "trace": "",
                    "cause": "passed, although it is marked as expected to fail"})
    return out


def grouped(problems: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """The problems by cause, the most frequent first: ``{"cause", "count", "tests", "trace"}`` (the first one's)."""
    groups: Dict[str, Dict[str, Any]] = {}
    for problem in problems:
        group = groups.setdefault(cause_key(problem["cause"]), {"cause": problem["cause"], "count": 0, "tests": [], "trace": problem["trace"]})
        group["count"] += 1
        group["tests"].append(problem["test"])
    return sorted(groups.values(), key=lambda group: (-group["count"], group["cause"]))


def detail(ran: int, groups: List[Dict[str, Any]]) -> str:
    """A few lines of plain text: how many did not pass and, per cause, how many and one test as an example."""
    if not ran:
        return "No test ran."
    failed = sum(group["count"] for group in groups)
    if not failed:
        return ""
    lines = ["%d of %d tests did not pass." % (failed, ran)]
    for group in groups:
        cause = group["cause"] if len(group["cause"]) <= MAX_CAUSE else group["cause"][:MAX_CAUSE] + "..."
        lines.append("%d x %s (for one: %s)" % (group["count"], cause, _short(group["tests"][0])))
    text = "\n".join(lines)
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 20].rstrip() + "\n(and more)"


def _short(test: str) -> str:
    """The name of a test without the ``tests.`` every one of them starts with."""
    return test[len("tests."):] if test.startswith("tests.") else test


def _data(text: str) -> str:
    """Text as the value of a GitHub workflow command: it has to stay on one line."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(title: str, message: str) -> str:
    return "::error title=%s::%s" % (_property(title[:200]), _data(message[:MAX_MESSAGE]))


def annotations(label: str, ran: int, groups: List[Dict[str, Any]]) -> List[str]:
    """The lines to print on GitHub Actions: none when everything passed."""
    if ran and not groups:
        return []
    where = " on %s" % label if label else ""
    if not ran:
        return [annotation("Tests%s: none ran" % where, "No test ran. Nothing was checked.")]
    failed = sum(group["count"] for group in groups)
    # Every test by name comes first: when the message is cut, it is the list of causes that loses its end,
    # and each of the first causes has an annotation of its own below.
    names = ", ".join(sorted(_short(name) for group in groups for name in group["tests"]))
    head, _, causes = detail(ran, groups).partition("\n")
    out = [annotation("Tests%s: %d of %d did not pass" % (where, failed, ran), "%s\n\nAll of them: %s\n\nBy cause:\n%s" % (head, names, causes))]
    for group in groups[:MAX_ANNOTATIONS - 1]:
        tail = "\n".join(group["trace"].rstrip().split("\n")[-30:])
        out.append(annotation("%d x %s" % (group["count"], group["cause"][:150]), "For one: %s\n\n%s" % (group["tests"][0], tail)))
    return out


def record(label: str, result: unittest.TestResult, groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the result file holds. ``kind``, ``label``, ``ok`` and ``detail`` are what the release notes read."""
    ran = result.testsRun
    failed = sum(group["count"] for group in groups)
    ok = bool(ran) and not failed
    return {"kind": "tests", "label": " ".join(label.split())[:120], "ok": ok, "outcome": "success" if ok else "failure",
            "ran": ran, "not_passed": failed, "skipped": len(result.skipped), "detail": detail(ran, groups),
            "causes": [{"count": group["count"], "cause": group["cause"], "tests": group["tests"][:50]} for group in groups[:50]]}


def run() -> unittest.TestResult:
    """Find and run the tests as ``python3 -m unittest discover -t . -s tests`` does, from the project's folder."""
    os.chdir(str(ROOT))
    sys.path[0] = str(ROOT)                                  # in place of this script's folder: tests import `studio`
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=str(ROOT))
    return unittest.TextTestRunner(verbosity=1, warnings=None if sys.warnoptions else "default").run(suite)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the test suite and say what did not pass where it can be read.")
    parser.add_argument("--label", default=os.environ.get("TESTS_LABEL", ""))
    parser.add_argument("--result", default=os.environ.get("TESTS_RESULT", ""))
    args = parser.parse_args(argv)

    result = run()
    groups = grouped(collect(result))
    found = record(args.label, result, groups)
    if args.result:
        out = Path(args.result)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(found, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            sys.stdout.reconfigure(errors="backslashreplace")   # a traceback may hold text this output cannot show
        except (AttributeError, ValueError):
            pass
        for line in annotations(found["label"], result.testsRun, groups):
            print(line, flush=True)
    return 0 if found["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
