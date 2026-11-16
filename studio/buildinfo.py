"""Which build of the app this is.

A release package carries ``studio/build.json``, written when the package was made (``tools/build_release.py``):
the version, the commit it was built from and the day of that commit. Run from a copy of the source there is
no such file, and the app says so instead of guessing a version.

The file is only ever shown. Nothing is decided by it, so a file that does not look like one this project
wrote is treated as absent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

BUILD_FILE = Path(__file__).with_name("build.json")
MAX_BYTES = 2000

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z]+){0,8}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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
