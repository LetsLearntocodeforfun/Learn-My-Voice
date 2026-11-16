#!/usr/bin/env python3
"""Publish a beta release on GitHub for a tag that has already been pushed.

    python3 tools/publish_release.py v0.1.0-beta

Creates (or updates) a GitHub pre-release for the tag. The release notes are that version's section
of CHANGELOG.md, so write the changelog entry first.

Signing in: this uses the GitHub login that git has saved in the macOS Keychain, read through
git's own credential helper. The token stays in memory, is sent only to api.github.com, and is
never printed or written anywhere. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


def repository() -> str:
    """``owner/name`` from the origin remote."""
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(ROOT), capture_output=True, text=True,
                         check=True).stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise SystemExit("origin is not a GitHub repository: %s" % url)
    return match.group(1)


def saved_token() -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    done = subprocess.run(["git", "-c", "credential.helper=osxkeychain", "credential", "fill"], cwd=str(ROOT),
                          input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, env=env, timeout=60)
    for line in done.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit("No GitHub login is saved for git in the Keychain. Push once from Terminal first.")


def notes_for(tag: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^## %s\b[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(tag), text, re.S | re.M)
    if not match or not match.group(1).strip():
        raise SystemExit("CHANGELOG.md has no section for %s" % tag)
    return match.group(1).strip()


def call(token: str, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    request = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode("utf-8") if body else None, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "learn-my-voice-release", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        return exc.code, payload if isinstance(payload, dict) else {}


def main(argv: list) -> int:
    if len(argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", argv[1]):
        print(__doc__)
        return 2
    tag = argv[1]
    if subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/tags/" + tag], cwd=str(ROOT), capture_output=True).returncode:
        raise SystemExit("There is no local tag %s" % tag)
    repo, body, token = repository(), notes_for(tag), saved_token()

    status, on_github = call(token, "GET", "/repos/%s/git/ref/tags/%s" % (repo, tag))
    if status != 200:
        raise SystemExit("GitHub does not have the tag %s yet (HTTP %s). Push it first: git push origin %s" % (tag, status, tag))
    release = {"tag_name": tag, "name": tag.lstrip("v").replace("-beta", " beta"), "body": body, "prerelease": "-" in tag, "draft": False}
    status, existing = call(token, "GET", "/repos/%s/releases/tags/%s" % (repo, tag))
    if status == 200:
        status, result = call(token, "PATCH", "/repos/%s/releases/%s" % (repo, existing["id"]), release)
        action = "updated"
    else:
        status, result = call(token, "POST", "/repos/%s/releases" % repo, release)
        action = "created"
    if status not in (200, 201):
        print("GitHub refused (HTTP %s): %s" % (status, result.get("message", "no message")))
        return 1
    print("Release %s: %s (pre-release: %s)" % (action, result.get("html_url"), result.get("prerelease")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
