"""Which build of the app this is.

A release package carries ``studio/build.json``, written when the package was made (``tools/build_release.py``):
the version, the commit it was built from and the day of that commit. Run from a copy of the source there is
no such file, and the app says so instead of guessing a version.

The file is only ever shown. Nothing is decided by it, so a file that does not look like one this project
wrote is treated as absent.

Inside the desktop app there is a second small file, written by the desktop build next to the app folder
(``desktop/build.py``: ``core/runtime.json``): which Python was put into the app. ``desktop()`` reads it. It is
how the app knows that it runs inside the desktop app, where the Python is the app's own and the app folder is
not a place to keep anything.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

BUILD_FILE = Path(__file__).with_name("build.json")
# Next to the app folder: <core>/runtime.json beside <core>/app/studio/buildinfo.py. Only the desktop build makes it.
RUNTIME_FILE = Path(__file__).resolve().parent.parent.parent / "runtime.json"
MAX_BYTES = 2000

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z]+){0,8}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PYTHON = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")

FROM_SOURCE: Dict[str, Any] = {"from_source": True, "version": None, "commit": None, "built_on": None}


def read(path: Optional[Path] = None) -> Dict[str, Any]:
    """``{"from_source", "version", "commit", "built_on"}`` for the build file at ``path`` (the app's own by default).

    ``from_source`` is true, and the rest None, when there is no file or it does not hold a version, a commit
    and a day in the forms the build script writes.
    """
    path = BUILD_FILE if path is None else path
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        found = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        return dict(FROM_SOURCE)
    if not isinstance(found, dict):
        return dict(FROM_SOURCE)
    version, commit, day = found.get("version"), found.get("commit"), found.get("built_on")
    if not (isinstance(version, str) and _VERSION.fullmatch(version) and isinstance(commit, str) and _COMMIT.fullmatch(commit)
            and isinstance(day, str) and _DAY.fullmatch(day)):
        return dict(FROM_SOURCE)
    return {"from_source": False, "version": version, "commit": commit, "built_on": day}


def current() -> Dict[str, Any]:
    """The build this app is running as. Read each time: it is one small file, and a test can put one in place."""
    return read()


def desktop(path: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """``{"python": "3.14.10"}`` when this app runs inside the desktop app, else None.

    Read off the file the desktop build writes next to the app folder (``{"python", "from", "sha256"}``: the
    version of the Python inside the app, where it was downloaded from, and its SHA-256). An app started from
    the zip or from the source has no such file. One that does not hold those three in the forms the build
    writes is treated as absent, so a file of that name that happens to lie beside a copy of the source is not
    taken for it.
    """
    path = RUNTIME_FILE if path is None else path
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        found = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict):
        return None
    python, source, digest = found.get("python"), found.get("from"), found.get("sha256")
    if not (isinstance(python, str) and _PYTHON.fullmatch(python) and isinstance(source, str) and source.startswith("https://")
            and isinstance(digest, str) and _SHA256.fullmatch(digest)):
        return None
    return {"python": python}
