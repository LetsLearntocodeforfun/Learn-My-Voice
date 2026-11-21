"""Look for a newer release on GitHub, check a downloaded package against its published SHA-256, and bring an earlier portable folder's data into an empty new one.

What asks GitHub, and when:

- Nothing leaves this computer while ``checks_on`` is false (``server.App.update_checks``). Only ``server.main``
  turns it on, and not when started with ``LEARN_MY_VOICE_NO_UPDATE_CHECK=1`` or for a window check. Every server
  a test or a release trial starts therefore never reaches out, even when asked with ``force=True``.
- When ``checks_on`` is true, a startup check (``force=False``) asks GitHub only when ``auto_check`` is on in
  ``data/config.json["updates"]`` and at least ``CACHE_TTL_SEC`` (12 hours) has passed since the last check.
  Clicking "Check for updates" in Settings passes ``force=True``.
- What is sent is one ``GET`` request to ``https://api.github.com/repos/<repo>/releases?per_page=15`` with no
  proxy, no cookies and no credentials. Nothing of the creator's scripts, library, voice profile or settings is in
  that request; GitHub sees this computer's IP address and when it asked.

What a download does, and what SHA-256 proves and does NOT prove:

- Only the exact expected asset name for this build (``learn-my-voice-<version><suffix>``) together with its exact
  published checksum file (``<asset>.sha256``) is accepted. Suffix-only fallbacks are not used. If a release
  publishes no ``.sha256`` for that exact file, or the ``.sha256`` file cannot be read, nothing is downloaded and
  the refusal points to the release page on GitHub.
- The asset is streamed chunk by chunk to a ``.part`` temporary file in the destination folder while its SHA-256
  is computed on the fly, bounded by ``MAX_ASSET_BYTES`` and never held whole in memory. Only when the finished
  digest matches the published ``.sha256`` is the ``.part`` file put under its final name; otherwise it is deleted.
- Matching SHA-256 proves download integrity: the bytes written to disk are complete and match the ``.sha256``
  file published alongside that release on GitHub. It does NOT prove release authenticity: both the package and
  its ``.sha256`` come from the same GitHub repository, and the desktop installers are not signed with a code-signing
  certificate. (For the ``.zip`` package, anyone with git and the source commit can rebuild the exact bytes with
  ``python3 tools/build_release.py`` and compare.)

How data stays across versions:

- In the desktop app, ``data/`` lives in the system's folder for application data outside the app bundle, so
  installing a newer version leaves scripts, library, profiles and settings where they are.
- In a portable ``.zip`` folder (``learn-my-voice-<version>/``), ``data/`` sits inside that version's folder.
  - ``migrate_from_previous_folder`` copies an earlier sibling folder's ``data/`` into the current folder ONLY
    when the current data folder is empty (no script, no library item, no creator profile, no custom voice profile,
    no tracked keyword, no plan, no sponsor brief, no remembered edit or topic ideas), and never while a background
    job or another write runs. Incoming JSON files are checked as strict JSON, ``studio.db`` is rebuilt and checked
    through ``studio.backup`` (plain tables only, integrity check, column types, script shapes, ``_try_out``), and
    ``Backups``'s data epoch advances so any page opened before migration is refused if it tries to save over what
    arrived. When the current folder already holds work, migration is refused and points to Backup and restore in
    Settings.
  - ``unpack_and_migrate_zip`` unpacks a downloaded release ``.zip`` into a sibling temporary directory (bounded in
    entry count and uncompressed bytes, refusing symlinks, traversal, ``data/``, ``local_tools/`` and Windows-unsafe
    names), copies ``data/`` into that temporary directory, and renames it into place ONLY when the destination
    folder does not already exist.
  - On-device transcription (``local_tools/whisper/``, a ~2 GB Python virtual environment tied to the folder it was
    installed in) is NEVER copied across ``.zip`` folders; local transcription must be set up again in a new ``.zip``
    folder if used.
  - Neighbouring ``learn-my-voice-<version>`` folders are scanned only when the current data folder is empty (and
    not in desktop mode) or when explicitly requested (``scan_previous=True``), never on routine startup checks
    once the folder holds work.

Standard library only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from . import backup, buildinfo, creator, llm, outside
from . import store as store_module
from .store import ROOT, Store, _owner_only

DEFAULT_REPO = "LetsLearntocodeforfun/Learn-My-Voice"
GITHUB_API_BASE = "https://api.github.com"
ALLOWED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
CACHE_TTL_SEC = 12 * 3600
MAX_RELEASES_JSON_BYTES = 1024 * 1024
MAX_RELEASES_INSPECTED = 50
MAX_ASSETS_PER_RELEASE = 100
MAX_NOTES_CHARS = 4000
MAX_CHECKSUM_BYTES = 8192
MAX_ASSET_BYTES = 512 * 1024 * 1024     # 512 MB cap for installer / zip packages
MAX_ZIP_ENTRIES = 4000
MAX_ZIP_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 128 * 1024 * 1024
MAX_ZIP_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
_CHUNK = 256 * 1024

_CORE_VER_RE = re.compile(
    r"^(?P<major>[0-9]{1,9})\.(?P<minor>[0-9]{1,9})\.(?P<patch>[0-9]{1,9})(?P<rest>.*)$",
    re.ASCII,
)
_RC_TAIL_RE = re.compile(r"^\.rc\.(?P<rc>[0-9]{1,9})(?:\.(?P<attempt>[0-9]{1,9}))?$", re.ASCII)
_PRE_PART_RE = re.compile(r"^[0-9A-Za-z-]{1,32}$", re.ASCII)
_BUILD_META_RE = re.compile(r"^[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+){0,7}$", re.ASCII)
_CHANGELOG_HEADING_RE = re.compile(r"^## v([0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:-[0-9A-Za-z.-]{1,40})?)\b", re.M | re.ASCII)
_SHA256_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})(?:[ \t]+\*?(\S.*))?$", re.ASCII)
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$", re.ASCII)
_SIBLING_DIR_RE = re.compile(r"^learn-my-voice-([0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:[-.][0-9A-Za-z]+){0,8})$", re.ASCII)
_WINDOWS_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + ["COM%d" % n for n in range(1, 10)] + ["LPT%d" % n for n in range(1, 10)]
)
_FORBIDDEN_ZIP_PREFIXES = ("data/", "local_tools/", ".git/", ".github/", "tests/")
_WORK_TABLES = (
    "scripts", "samples", "trends", "video_plans", "sponsor_briefs",
    "lessons", "sample_beats", "sample_comments", "sample_sentiments", "metric_snapshots",
)

VersionPrePart = Union[Tuple[int, int], Tuple[int, str]]
VersionKey = Tuple[int, int, int, int, Tuple[VersionPrePart, ...], int, int, int]
UpdateStream = Union[bytes, bytearray, Iterator[bytes], Any]
UpdateTransport = Callable[[str, str, Dict[str, str]], UpdateStream]


class UpdateError(Exception):
    """An update check, download, unpack, or migration step was refused. Nothing was half-written."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = status
        self.extra = dict(extra or {})


def _allowed_https_url(url: Any, *, hosts: Sequence[str]) -> bool:
    """True only for plain HTTPS URLs on port 443 without credentials whose hostname is in ``hosts``."""
    if not isinstance(url, str) or outside.link_problem(url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return False
    return host in hosts


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Allow HTTPS redirects strictly to exact GitHub release asset hosts, stripping credentials."""

    max_repeats = 2
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not isinstance(newurl, str) or len(newurl) > outside.MAX_LINK_CHARS:
            raise urllib.error.HTTPError(req.full_url, code, "Disallowed redirect target", headers, fp)
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if not _allowed_https_url(resolved, hosts=ALLOWED_DOWNLOAD_HOSTS):
            raise urllib.error.HTTPError(req.full_url, code, "Disallowed redirect target", headers, fp)
        new_headers = {
            k: v for k, v in req.header_items()
            if k.lower() not in ("authorization", "cookie", "proxy-authorization")
        }
        return urllib.request.Request(resolved, headers=new_headers, method="GET")


_API_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), llm._NoRedirect())
_ASSET_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeRedirect())


def repo_name() -> str:
    env = (os.environ.get("LEARN_MY_VOICE_REPO") or "").strip()
    if env and _REPO_RE.fullmatch(env) and ".." not in env.split("/"):
        return env
    return DEFAULT_REPO


# ---- version parsing & ordering (agrees with tools/build_release.py & studio/buildinfo.py) ----

def parse_version(raw: Any) -> Optional[VersionKey]:
    """Parse a release or build version into a strictly comparable tuple, or None when invalid.

    Accepts every version ``tools/build_release.py`` builds and ``studio/buildinfo.py`` reads:
    ``X.Y.Z``, ``X.Y.Z-pre``, ``X.Y.Z-pre.N``, ``<base>.dev``, ``<base>.rc.N``, ``<base>.rc.N.M``,
    optional leading ``v``, and optional ``+build`` metadata.

    Ordering within the same ``X.Y.Z``:
    ``0.2.0-alpha < 0.2.0-beta.dev < 0.2.0-beta.rc.1 < 0.2.0-beta.rc.1.2 < 0.2.0-beta
      < 0.2.0-beta.1 < 0.2.0-beta.2 < 0.2.0-beta.10 < 0.2.0.dev < 0.2.0.rc.1 < 0.2.0``
    """
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned or len(cleaned) > 80 or not cleaned.isascii():
        return None
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    if "+" in cleaned:
        core, meta = cleaned.split("+", 1)
        if not _BUILD_META_RE.fullmatch(meta):
            return None
    else:
        core = cleaned
    m = _CORE_VER_RE.fullmatch(core)
    if not m:
        return None
    major = int(m.group("major"))
    minor = int(m.group("minor"))
    patch = int(m.group("patch"))
    rest = m.group("rest")

    # Determine build substage suffix (.dev vs .rc.N[.M] vs final of base).
    substage = 2
    rc_num = 0
    rc_att = 0
    if rest.endswith(".dev"):
        substage = 0
        rest = rest[:-4]
    else:
        rc_pos = rest.rfind(".rc.")
        if rc_pos != -1:
            rc_match = _RC_TAIL_RE.fullmatch(rest[rc_pos:])
            if not rc_match:
                return None
            substage = 1
            rc_num = int(rc_match.group("rc"))
            rc_att = int(rc_match.group("attempt")) if rc_match.group("attempt") is not None else 1
            if rc_num < 1 or rc_att < 1:
                return None
            rest = rest[:rc_pos]

    if not rest:
        has_pre = 1
        pre_key: Tuple[VersionPrePart, ...] = ()
    elif rest.startswith("-") and len(rest) > 1:
        parts = rest[1:].split(".")
        if len(parts) > 8 or not all(_PRE_PART_RE.fullmatch(p) and p.lower() != "rc" for p in parts):
            return None
        has_pre = 0
        parsed_parts: List[VersionPrePart] = []
        for p in parts:
            if p.isdigit():
                if len(p) > 9:
                    return None
                parsed_parts.append((0, int(p)))
            else:
                parsed_parts.append((1, p.lower()))
        pre_key = tuple(parsed_parts)
    else:
        return None

    return (major, minor, patch, has_pre, pre_key, substage, rc_num, rc_att)


def is_newer(candidate: str, current: str) -> bool:
    cand_t = parse_version(candidate)
    curr_t = parse_version(current)
    if cand_t is None or curr_t is None:
        return False
    return cand_t > curr_t


def is_prerelease_version(ver: Optional[str]) -> bool:
    parsed = parse_version(ver or "")
    if parsed is None:
        return True
    return not (parsed[3] == 1 and parsed[5] == 2)


def current_version_info() -> Dict[str, Any]:
    """Current running build version, falling back to CHANGELOG.md base+.dev when running from source."""
    build = buildinfo.current()
    desk = buildinfo.desktop()
    if not build.get("from_source") and build.get("version"):
        return {
            "version": str(build["version"]),
            "from_source": False,
            "desktop": bool(desk),
            "commit": build.get("commit"),
            "built_on": build.get("built_on"),
        }
    base = "0.2.0-beta"
    changelog = ROOT / "CHANGELOG.md"
    if changelog.is_file():
        try:
            with changelog.open("rb") as fh:
                head = fh.read(65536).decode("utf-8", "replace")
            m = _CHANGELOG_HEADING_RE.search(head)
            if m and parse_version(m.group(1)) is not None:
                base = m.group(1)
        except OSError:
            pass
    return {
        "version": base + ".dev",
        "base_version": base,
        "from_source": True,
        "desktop": bool(desk),
        "commit": None,
        "built_on": None,
    }


def current_platform_target(is_desktop: Optional[bool] = None,
                            sys_name: Optional[str] = None,
                            machine: Optional[str] = None) -> Dict[str, str]:
    """Expected release asset suffix for this OS, CPU architecture, and runtime mode."""
    desk = bool(buildinfo.desktop()) if is_desktop is None else bool(is_desktop)
    os_name = (sys_name or platform.system() or "").lower()
    arch = (machine or platform.machine() or "").lower()
    if desk:
        if os_name == "darwin":
            if arch in ("arm64", "aarch64"):
                return {"mode": "desktop", "system": "macos-arm64", "suffix": "-macos-arm64.dmg"}
            return {"mode": "desktop", "system": "macos-x64", "suffix": "-macos-x64.dmg"}
        if os_name == "windows":
            return {"mode": "desktop", "system": "windows-x64", "suffix": "-windows-x64-setup.exe"}
    return {"mode": "zip", "system": os_name or "portable", "suffix": ".zip"}


def expected_asset_name(version: str, target: Dict[str, str]) -> str:
    clean_ver = version[1:] if version.startswith("v") else version
    return "learn-my-voice-%s%s" % (clean_ver, target["suffix"])


# ---- HTTP transport & bounded chunk streaming --------------------------------

def _validate_request_url(url: str) -> bool:
    """Validate ``url`` before any opener or test stand-in is called. Returns True for GitHub API, False for asset host."""
    if not isinstance(url, str) or outside.link_problem(url):
        raise UpdateError("bad_url", "Update URLs must be plain HTTPS addresses.", "更新地址必须是安全的 HTTPS 链接。", 400)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError("bad_url", "Update URLs must use HTTPS on port 443 without credentials.",
                          "更新地址必须使用标准 HTTPS 端口且不能包含凭据。", 400)
    host = (parsed.hostname or "").lower()
    if host == "api.github.com":
        return True
    if host in ALLOWED_DOWNLOAD_HOSTS:
        return False
    raise UpdateError("bad_url", "Disallowed update host: %s" % host[:80], "不允许的更新服务器：%s" % host[:80], 400)


@contextlib.contextmanager
def _open_stream(method: str, url: str, headers: Dict[str, str], *,
                 max_bytes: int, transport: Optional[UpdateTransport] = None) -> Iterator[Iterator[bytes]]:
    """Yield bounded byte chunks from ``transport`` (in tests) or HTTPS without buffering the body in memory."""
    is_api = _validate_request_url(url)
    if transport is not None:
        raw = transport(method, url, headers)

        def from_stand_in() -> Iterator[bytes]:
            total = 0
            try:
                if isinstance(raw, (bytes, bytearray)):
                    view = memoryview(raw)
                    for offset in range(0, len(view), _CHUNK):
                        piece = bytes(view[offset:offset + _CHUNK])
                        total += len(piece)
                        if total > max_bytes:
                            raise UpdateError("too_large", "Update response exceeded the size limit.",
                                              "下载的数据超过了大小上限。", 502)
                        yield piece
                elif hasattr(raw, "read"):
                    while True:
                        piece = raw.read(_CHUNK)
                        if not piece:
                            break
                        total += len(piece)
                        if total > max_bytes:
                            raise UpdateError("too_large", "Update response exceeded the size limit.",
                                              "下载的数据超过了大小上限。", 502)
                        yield bytes(piece)
                else:
                    for item in raw:
                        view = memoryview(item)
                        for offset in range(0, len(view), _CHUNK):
                            piece = bytes(view[offset:offset + _CHUNK])
                            total += len(piece)
                            if total > max_bytes:
                                raise UpdateError("too_large", "Update response exceeded the size limit.",
                                                  "下载的数据超过了大小上限。", 502)
                            yield piece
            finally:
                close = getattr(raw, "close", None)
                if callable(close):
                    close()

        yield from_stand_in()
        return

    opener = _API_OPENER if is_api else _ASSET_OPENER
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        resp = opener.open(req, timeout=15 if is_api else 60)
    except urllib.error.HTTPError as exc:
        raise UpdateError(
            "update_http_error",
            "GitHub returned HTTP %d while checking or downloading the release." % exc.code,
            "连接 GitHub 获取版本信息或安装包时返回 HTTP %d。" % exc.code,
            502,
        )
    except (urllib.error.URLError, OSError) as exc:
        reason = str(getattr(exc, "reason", exc))[:160]
        raise UpdateError(
            "update_network_error",
            "Could not reach GitHub Releases: %s" % reason,
            "无法连接 GitHub Releases：%s" % reason,
            502,
        )

    def from_http() -> Iterator[bytes]:
        try:
            with resp:
                raw_len = (resp.headers.get("Content-Length") or "").strip() if hasattr(resp, "headers") else ""
                if raw_len and raw_len.isascii() and raw_len.isdigit():
                    if len(raw_len) > 18 or int(raw_len) > max_bytes:
                        raise UpdateError("too_large", "Update response exceeded the size limit.",
                                          "下载的数据超过了大小上限。", 502)
                total = 0
                while True:
                    piece = resp.read(_CHUNK)
                    if not piece:
                        break
                    total += len(piece)
                    if total > max_bytes:
                        raise UpdateError("too_large", "Update response exceeded the size limit.",
                                          "下载的数据超过了大小上限。", 502)
                    yield piece
        except UpdateError:
            raise
        except (urllib.error.URLError, OSError) as exc:
            reason = str(getattr(exc, "reason", exc))[:160]
            raise UpdateError(
                "update_network_error",
                "Download from GitHub Releases was interrupted: %s" % reason,
                "从 GitHub 下载中断：%s" % reason,
                502,
            )

    yield from_http()


def _read_bounded_bytes(method: str, url: str, headers: Dict[str, str], *,
                        max_bytes: int, transport: Optional[UpdateTransport] = None) -> bytes:
    """Read a small metadata file (releases JSON or ``.sha256``) into bytes up to ``max_bytes``."""
    with _open_stream(method, url, headers, max_bytes=max_bytes, transport=transport) as chunks:
        return b"".join(chunks)


def _default_transport(method: str, url: str, headers: Dict[str, str]) -> bytes:
    """Default small-response reader; kept as the single module-level network hook tests may patch."""
    is_api = _validate_request_url(url)
    limit = MAX_RELEASES_JSON_BYTES if is_api else MAX_ASSET_BYTES
    return _read_bounded_bytes(method, url, headers, max_bytes=limit, transport=None)


# ---- sanitizing release metadata from GitHub or disk ------------------------

def _clean_notes(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    kept: List[str] = []
    for ch in raw:
        if ch in ("\n", "\t"):
            kept.append(ch)
        elif ch == "\r":
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            continue
        else:
            kept.append(ch)
            if len(kept) >= MAX_NOTES_CHARS:
                break
    return "".join(kept).strip()


def _clean_release_html_url(raw_url: Any, tag: str) -> str:
    repo = repo_name()
    expected_prefix = "/%s/releases/" % repo
    if isinstance(raw_url, str) and _allowed_https_url(raw_url, hosts=("github.com",)):
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.path.startswith(expected_prefix) and ".." not in parsed.path.split("/"):
            return raw_url
    return "https://github.com/%s/releases/tag/%s" % (repo, urllib.parse.quote(tag, safe="._-"))


def _clean_asset_entry(raw: Any, expected_name: str, max_size: int) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    url = raw.get("browser_download_url") if "browser_download_url" in raw else raw.get("url")
    size = raw.get("size")
    if not isinstance(name, str) or name.strip() != expected_name:
        return None
    if not _allowed_https_url(url, hosts=ALLOWED_DOWNLOAD_HOSTS):
        return None
    clean_size: Optional[int] = None
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > max_size:
            return None
        clean_size = size
    return {"name": expected_name, "url": url, "size": clean_size}


def _select_asset(assets: Any, version: str, target: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Select ONLY the exact expected platform asset and its exact ``<name>.sha256`` sidecar."""
    if not isinstance(assets, list):
        return None, None
    wanted = expected_asset_name(version, target)
    wanted_sum = wanted + ".sha256"
    primary: Optional[Dict[str, Any]] = None
    checksum: Optional[Dict[str, Any]] = None
    for item in assets[:MAX_ASSETS_PER_RELEASE]:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        if raw_name == wanted and primary is None:
            primary = _clean_asset_entry(item, wanted, MAX_ASSET_BYTES)
        elif raw_name == wanted_sum and checksum is None:
            checksum = _clean_asset_entry(item, wanted_sum, MAX_CHECKSUM_BYTES)
    if primary is None:
        return None, None
    return primary, checksum


def _pick_best_release(releases: Any, *, include_prereleases: bool,
                       target: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not isinstance(releases, list):
        raise UpdateError("bad_release_json", "GitHub Releases did not return a release list.",
                          "GitHub Releases 返回的数据不是版本列表。", 502)
    candidates: List[Tuple[VersionKey, Dict[str, Any]]] = []
    for rel in releases[:MAX_RELEASES_INSPECTED]:
        if not isinstance(rel, dict) or rel.get("draft") is True:
            continue
        raw_tag = rel.get("tag_name")
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip()
        if not tag or len(tag) > 64:
            continue
        ver = tag[1:] if tag.startswith("v") else tag
        if "+" in ver or buildinfo._VERSION.fullmatch(ver) is None:
            continue
        parsed = parse_version(ver)
        if parsed is None:
            continue
        is_pre = bool(rel.get("prerelease")) or is_prerelease_version(ver)
        if is_pre and not include_prereleases:
            continue
        asset, checksum = _select_asset(rel.get("assets"), ver, target)
        html_url = _clean_release_html_url(rel.get("html_url"), tag)
        raw_pub = rel.get("published_at")
        published_at = raw_pub.strip()[:32] if isinstance(raw_pub, str) and raw_pub.isascii() else ""
        candidates.append((parsed, {
            "version": ver,
            "tag": tag if tag.startswith("v") else ("v" + ver),
            "prerelease": is_pre,
            "published_at": published_at,
            "html_url": html_url,
            "notes": _clean_notes(rel.get("body")),
            "asset": asset,
            "checksum_asset": checksum,
        }))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def _clean_cached_release(raw: Any, target: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Validate cached release metadata read back from ``config.json`` so damaged or edited files stay harmless."""
    if not isinstance(raw, dict):
        return None
    ver = raw.get("version")
    if not isinstance(ver, str) or "+" in ver or buildinfo._VERSION.fullmatch(ver) is None or parse_version(ver) is None:
        return None
    raw_tag = raw.get("tag")
    tag = raw_tag.strip() if isinstance(raw_tag, str) and raw_tag.strip() and len(raw_tag) <= 64 else ("v" + ver)
    wanted = expected_asset_name(ver, target)
    asset = _clean_asset_entry(raw.get("asset"), wanted, MAX_ASSET_BYTES)
    checksum = _clean_asset_entry(raw.get("checksum_asset"), wanted + ".sha256", MAX_CHECKSUM_BYTES) if asset else None
    raw_pub = raw.get("published_at")
    published_at = raw_pub.strip()[:32] if isinstance(raw_pub, str) and raw_pub.isascii() else ""
    return {
        "version": ver,
        "tag": tag,
        "prerelease": bool(raw.get("prerelease")) or is_prerelease_version(ver),
        "published_at": published_at,
        "html_url": _clean_release_html_url(raw.get("html_url"), tag),
        "notes": _clean_notes(raw.get("notes")),
        "asset": asset,
        "checksum_asset": checksum,
    }


def _stored_settings(store: Store, target: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    raw = store.load_config().get("updates")
    data = raw if isinstance(raw, dict) else {}
    cur = current_version_info()
    tgt = target or current_platform_target(is_desktop=cur["desktop"])
    default_pre = is_prerelease_version(cur.get("version"))
    raw_auto = data.get("auto_check")
    raw_pre = data.get("include_prereleases")
    raw_dismissed = data.get("dismissed_version")
    dismissed = raw_dismissed.strip()[:64] if isinstance(raw_dismissed, str) and parse_version(raw_dismissed) is not None else ""
    raw_ts = data.get("last_checked_at")
    checked_at = float(raw_ts) if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool) and raw_ts > 0 else 0.0
    return {
        "auto_check": raw_auto if isinstance(raw_auto, bool) else True,
        "include_prereleases": raw_pre if isinstance(raw_pre, bool) else default_pre,
        "dismissed_version": dismissed,
        "last_checked_at": checked_at,
        "cached": _clean_cached_release(data.get("cached"), tgt),
    }


def save_update_settings(store: Store, changes: Dict[str, Any], *, checks_on: bool = False) -> Dict[str, Any]:
    """Persist startup check preference or skipped version inside ``config.json["updates"]``. Asks GitHub nothing."""
    def mutate(cfg: Dict[str, Any]) -> None:
        cur = dict(cfg.get("updates") if isinstance(cfg.get("updates"), dict) else {})
        if "auto_check" in changes and isinstance(changes["auto_check"], bool):
            cur["auto_check"] = changes["auto_check"]
        if "include_prereleases" in changes and isinstance(changes["include_prereleases"], bool):
            cur["include_prereleases"] = changes["include_prereleases"]
        if "dismissed_version" in changes and isinstance(changes["dismissed_version"], str):
            val = changes["dismissed_version"].strip()[:64]
            cur["dismissed_version"] = val if (not val or parse_version(val) is not None) else ""
        cfg["updates"] = cur

    store.update_config(mutate)
    return status_payload(store, checks_on=checks_on)


def check_for_updates(store: Store, *, checks_on: bool = False, force: bool = False,
                      scan_previous: bool = False,
                      transport: Optional[UpdateTransport] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
    """Return update status, asking GitHub Releases only when allowed and needed.

    - With ``checks_on`` false, nothing is ever asked, forced or not, and ``transport`` is never called.
    - With ``force`` false and ``auto_check`` false in settings, nothing is asked either: turning off startup
      checks genuinely stops startup requests to GitHub.
    """
    if not checks_on:
        return status_payload(store, checks_on=False, scan_previous=scan_previous)
    now_ts = time.time() if now is None else float(now)
    cur = current_version_info()
    target = current_platform_target(is_desktop=cur["desktop"])
    prefs = _stored_settings(store, target)

    if not force and not prefs["auto_check"]:
        return _compose_status(store, cur, target, prefs, prefs["cached"],
                               checks_on=True, scan_previous=scan_previous)

    use_cache = (
        not force
        and prefs["cached"] is not None
        and prefs["last_checked_at"] > 0
        and 0 <= (now_ts - prefs["last_checked_at"]) < CACHE_TTL_SEC
    )

    latest = prefs["cached"] if use_cache else None
    check_error: Optional[Dict[str, str]] = None

    if not use_cache:
        url = "%s/repos/%s/releases?per_page=15" % (GITHUB_API_BASE, repo_name())
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "learn-my-voice-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            if transport is None:
                raw_bytes = _default_transport("GET", url, headers)
            else:
                raw_bytes = _read_bounded_bytes("GET", url, headers,
                                                max_bytes=MAX_RELEASES_JSON_BYTES, transport=transport)
            parsed_json = llm.loads_strict(raw_bytes.decode("utf-8"))
            latest = _pick_best_release(
                parsed_json,
                include_prereleases=prefs["include_prereleases"],
                target=target,
            )

            def save_cache(cfg: Dict[str, Any]) -> None:
                u = dict(cfg.get("updates") if isinstance(cfg.get("updates"), dict) else {})
                u["last_checked_at"] = now_ts
                u["cached"] = latest
                cfg["updates"] = u

            store.update_config(save_cache)
            prefs = _stored_settings(store, target)
        except (UpdateError, ValueError, UnicodeDecodeError, RecursionError) as exc:
            if force:
                if isinstance(exc, UpdateError):
                    raise
                raise UpdateError("bad_release_json", "GitHub returned unreadable release metadata.",
                                  "GitHub 返回的版本信息无法读取。", 502)
            check_error = {
                "code": getattr(exc, "code", "bad_release_json"),
                "message": getattr(exc, "message", "Could not read GitHub release metadata."),
                "message_zh": getattr(exc, "message_zh", "暂时无法读取 GitHub 版本信息。"),
            }
            latest = prefs["cached"]

    return _compose_status(store, cur, target, prefs, latest, check_error=check_error,
                           checks_on=True, scan_previous=scan_previous)


def status_payload(store: Store, *, checks_on: bool = False, scan_previous: bool = False) -> Dict[str, Any]:
    """What is known about updates from local storage alone. Asks GitHub nothing, whatever ``checks_on`` is."""
    cur = current_version_info()
    target = current_platform_target(is_desktop=cur["desktop"])
    prefs = _stored_settings(store, target)
    return _compose_status(store, cur, target, prefs, prefs["cached"],
                           checks_on=checks_on, scan_previous=scan_previous)


def is_data_empty(store: Store) -> bool:
    """True only when ``store``'s data folder holds none of the creator's work or profiles.

    Checked: no script, no library item, no creator profile (nor an unreadable ``creator.json``), no custom or
    unreadable voice profile, no waiting voice proposal, no earlier voice profile, no tracked keyword, no video
    plan, no sponsor brief, no remembered edit, no sample comment or metric snapshot, and no saved topic ideas.
    UI preferences (``page``) and cached update metadata (``updates``) in ``config.json`` do not count as work.
    """
    with contextlib.closing(store._db()) as db:
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in _WORK_TABLES:
            if table in existing and db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0] > 0:
                return False
    if creator.is_set(store.load_creator()):
        return False
    if os.path.lexists(str(store.creator_path)):
        try:
            loaded_creator = Store._read_stored(store.creator_path)
            if not isinstance(loaded_creator, dict) or creator.is_set(loaded_creator):
                return False
        except (OSError, ValueError):
            return False
    own, unreadable = store._own_dna()
    if own is not None or unreadable:
        return False
    if store.load_pending() is not None or os.path.lexists(str(store.pending_path)):
        return False
    if backup._history_names(store.dna_history_dir):
        return False
    ideas = store.load_recommendations()
    if (isinstance(ideas, dict) and bool(ideas)) or os.path.lexists(str(store.recommendations_path)):
        return False
    return True


def _compose_status(store: Store, cur: Dict[str, Any], target: Dict[str, str],
                    prefs: Dict[str, Any], latest: Optional[Dict[str, Any]],
                    check_error: Optional[Dict[str, str]] = None,
                    checks_on: bool = False,
                    scan_previous: bool = False) -> Dict[str, Any]:
    if isinstance(latest, dict) and latest.get("prerelease") and not prefs["include_prereleases"]:
        latest = None
    latest_ver = latest.get("version") if isinstance(latest, dict) else None
    newer = bool(latest_ver and is_newer(latest_ver, cur["version"]))
    prompt_startup = bool(
        newer
        and checks_on
        and prefs["auto_check"]
        and not cur["from_source"]
        and latest_ver != prefs["dismissed_version"]
    )
    empty = is_data_empty(store)
    # U6: Only scan sibling folders when the current portable folder is empty OR explicitly requested (?scan=1).
    should_scan = bool(scan_previous or (not cur["desktop"] and empty))
    previous = discover_previous_installations(store, explicit=scan_previous) if should_scan else []
    out: Dict[str, Any] = {
        "checks": {"on": bool(checks_on)},
        "current": cur,
        "target": target,
        "data_dir": str(Path(store.data_dir).resolve()),
        "data_empty": empty,
        "settings_persisted": True,
        "persistence_mode": "os_app_data" if cur["desktop"] else "portable_folder",
        "auto_check": prefs["auto_check"],
        "include_prereleases": prefs["include_prereleases"],
        "dismissed_version": prefs["dismissed_version"],
        "last_checked_at": prefs["last_checked_at"] or None,
        "update_available": newer,
        "prompt_on_startup": prompt_startup,
        "latest": latest,
        "previous_versions": previous,
        "previous_scanned": should_scan,
        "releases_url": "https://github.com/%s/releases" % repo_name(),
    }
    if check_error:
        out["check_error"] = check_error
    return out


def updates_off() -> UpdateError:
    """The refusal for anything that would ask GitHub while looking for updates is off for this run."""
    return UpdateError(
        "updates_off",
        "Looking for updates is switched off for this run of the app, so nothing was asked of GitHub and nothing "
        "was downloaded. It is off when the app is started with LEARN_MY_VOICE_NO_UPDATE_CHECK=1, or by a script "
        "that tries it.",
        "这次运行没有开启检查更新，所以没有向 GitHub 询问，也没有下载任何东西。"
        "用 LEARN_MY_VOICE_NO_UPDATE_CHECK=1 启动应用，或者由试运行的脚本启动时，检查更新是关着的。",
        409,
    )


# ---- SHA-256 sidecar parsing & streaming download ---------------------------

def _parse_sha256_sidecar(raw: bytes, asset_name: str) -> Optional[str]:
    """Extract the 64-hex SHA-256 for ``asset_name`` from a ``.sha256`` file, or None when invalid."""
    if not raw or len(raw) > MAX_CHECKSUM_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    bare: List[str] = []
    named: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _SHA256_LINE_RE.fullmatch(line)
        if not m:
            return None
        digest = m.group(1).lower()
        label = (m.group(2) or "").strip()
        if label:
            named[label] = digest
        else:
            bare.append(digest)
    if asset_name in named:
        return named[asset_name]
    if not named and len(bare) == 1:
        return bare[0]
    return None


def _place_part_file(part_path: Path, folder: Path, safe_name: str) -> Path:
    """Put ``part_path`` under the first free ``name`` / ``name (2)`` in ``folder`` without overwriting anything."""
    last_err: Optional[OSError] = None
    for number in range(1, outside.MAX_SAME_NAME + 1):
        candidate = outside._numbered(folder, safe_name, number)
        if os.path.lexists(str(candidate)):
            last_err = FileExistsError("%s already exists" % candidate.name)
            continue
        try:
            if os.name != "nt":
                os.link(str(part_path), str(candidate))
                os.unlink(str(part_path))
                try:
                    os.chmod(str(candidate), 0o644)
                except OSError:
                    pass
                return candidate
            os.rename(str(part_path), str(candidate))
            return candidate
        except FileExistsError as exc:
            last_err = exc
            continue
        except OSError as exc:
            # Filesystem without hard links on POSIX: fall back after re-checking non-existence.
            if os.name != "nt" and not os.path.lexists(str(candidate)):
                try:
                    os.rename(str(part_path), str(candidate))
                    try:
                        os.chmod(str(candidate), 0o644)
                    except OSError:
                        pass
                    return candidate
                except OSError as ren_exc:
                    last_err = ren_exc
                    break
            last_err = exc
            break
    raise OSError(str(last_err) if last_err is not None else "could not place downloaded file")


def _stream_to_destination(url: str, headers: Dict[str, str], asset_name: str, expected_sha256: str,
                           folders: Sequence[Path], *, transport: Optional[UpdateTransport]) -> Tuple[Path, str, int]:
    """Stream ``url`` chunk-by-chunk to a ``.part`` file in ``folders``, verify SHA-256, then rename into place."""
    safe_name = outside.safe_name(asset_name)
    chosen_folder: Optional[Path] = None
    part_path: Optional[Path] = None
    fd: Optional[int] = None
    last_open_err: Optional[OSError] = None

    for raw_folder in folders:
        folder = Path(raw_folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            candidate_part = folder / (".%s.%s.part" % (safe_name, uuid.uuid4().hex[:12]))
            fd = os.open(str(candidate_part), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            chosen_folder = folder
            part_path = candidate_part
            break
        except OSError as exc:
            last_open_err = exc
            continue

    if fd is None or chosen_folder is None or part_path is None:
        raise UpdateError(
            "save_failed",
            "Could not create a download file in Downloads or exports: %s" % (last_open_err or "permission denied"),
            "无法在「下载」或导出文件夹中创建下载文件：%s" % (last_open_err or "没有写入权限"),
            507,
        )

    hasher = hashlib.sha256()
    total = 0
    try:
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = None
                if transport is None and _default_transport is not _ORIG_DEFAULT_TRANSPORT:
                    # A test patched updater._default_transport directly (e.g. to assert nothing is called).
                    data = _default_transport("GET", url, headers)
                    if len(data) > MAX_ASSET_BYTES:
                        raise UpdateError("too_large", "Update response exceeded the size limit.",
                                          "下载的数据超过了大小上限。", 502)
                    hasher.update(data)
                    handle.write(data)
                    total = len(data)
                else:
                    with _open_stream("GET", url, headers, max_bytes=MAX_ASSET_BYTES, transport=transport) as chunks:
                        for chunk in chunks:
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > MAX_ASSET_BYTES:
                                raise UpdateError("too_large", "Downloaded file exceeded the %d MB size limit."
                                                  % (MAX_ASSET_BYTES // (1024 * 1024)),
                                                  "下载的安装包超过了 %d MB 的大小上限。"
                                                  % (MAX_ASSET_BYTES // (1024 * 1024)), 502)
                            hasher.update(chunk)
                            handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise UpdateError(
                "save_failed",
                "Could not write downloaded package to disk: %s" % exc,
                "无法把下载的安装包写入磁盘：%s" % exc,
                507,
            )

        if total == 0:
            raise UpdateError("empty_download", "Downloaded package from GitHub was empty. File discarded.",
                              "从 GitHub 下载的安装包为空，已丢弃。", 502)

        actual_sha256 = hasher.hexdigest()
        if actual_sha256 != expected_sha256:
            raise UpdateError(
                "checksum_mismatch",
                "Downloaded package SHA-256 (%s) did not match the published .sha256 file (%s). The partial file was removed."
                % (actual_sha256[:12], expected_sha256[:12]),
                "下载文件的 SHA-256（%s）与发布的 .sha256 校验值（%s）不一致，临时文件已删除。"
                % (actual_sha256[:12], expected_sha256[:12]),
                502,
            )

        try:
            saved_path = _place_part_file(part_path, chosen_folder, safe_name)
        except OSError as exc:
            raise UpdateError("save_failed", "Could not finalize downloaded package: %s" % exc,
                              "无法保存已完成校验的安装包：%s" % exc, 507)
        return saved_path, actual_sha256, total
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if part_path is not None and os.path.lexists(str(part_path)):
            backup._remove(part_path)


_ORIG_DEFAULT_TRANSPORT = _default_transport


def download_release_asset(store: Store, outside_mod: Any, *, checks_on: bool = False,
                           transport: Optional[UpdateTransport] = None,
                           on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Stream the exact matching release asset, check its published ``.sha256``, and save to Downloads.

    - Refused with ``updates_off()`` when ``checks_on`` is false, before ``transport`` is called.
    - Refused without downloading the asset when the release published no ``<asset>.sha256`` sidecar or the
      sidecar does not hold a valid SHA-256 for that exact filename; the refusal carries ``release_url``.
    - Matching SHA-256 proves the downloaded bytes match the checksum published with the release on GitHub; it
      does NOT prove release authenticity (both come from GitHub and desktop installers are unsigned).
    """
    if not checks_on:
        raise updates_off()

    now_ts = time.time()
    status = status_payload(store, checks_on=True)
    prefs = _stored_settings(store, status["target"])
    cache_fresh = (
        prefs["cached"] is not None
        and prefs["last_checked_at"] > 0
        and 0 <= (now_ts - prefs["last_checked_at"]) < CACHE_TTL_SEC
    )
    if not cache_fresh:
        status = check_for_updates(store, checks_on=True, force=True, transport=transport, now=now_ts)

    latest = status.get("latest")
    if not isinstance(latest, dict) or not status.get("update_available"):
        raise UpdateError(
            "no_update",
            "There is no newer release to download right now.",
            "目前没有可供下载的新版本。",
            404,
        )

    release_url = latest.get("html_url") or status["releases_url"]
    wanted_name = expected_asset_name(latest["version"], status["target"])
    asset = latest.get("asset")
    if not isinstance(asset, dict) or asset.get("name") != wanted_name or not isinstance(asset.get("url"), str):
        raise UpdateError(
            "no_update_asset",
            "Release v%s has no package named %s for this system. Open the release page on GitHub to see what was published: %s"
            % (latest["version"], wanted_name, release_url),
            "v%s 没有发布适用于当前系统的安装包（%s）。请打开 GitHub 发布页查看：%s"
            % (latest["version"], wanted_name, release_url),
            404,
            extra={"release_url": release_url, "expected_asset": wanted_name},
        )

    checksum_asset = latest.get("checksum_asset")
    if not isinstance(checksum_asset, dict) or checksum_asset.get("name") != wanted_name + ".sha256" \
            or not isinstance(checksum_asset.get("url"), str):
        raise UpdateError(
            "missing_checksum",
            "Release v%s did not publish %s.sha256, so the app did not download %s. You can inspect the release on GitHub: %s"
            % (latest["version"], wanted_name, wanted_name, release_url),
            "v%s 没有发布对应的校验文件 %s.sha256，因此没有下载 %s。你可以前往 GitHub 发布页查看：%s"
            % (latest["version"], wanted_name, wanted_name, release_url),
            502,
            extra={"release_url": release_url},
        )

    headers = {"User-Agent": "learn-my-voice-updater", "Accept": "application/octet-stream"}
    if on_progress:
        on_progress("verifying_checksum")
    if transport is None and _default_transport is not _ORIG_DEFAULT_TRANSPORT:
        raw_sum = _default_transport("GET", checksum_asset["url"], headers)
    else:
        raw_sum = _read_bounded_bytes("GET", checksum_asset["url"], headers,
                                      max_bytes=MAX_CHECKSUM_BYTES, transport=transport)
    expected_sha256 = _parse_sha256_sidecar(raw_sum, wanted_name)
    if expected_sha256 is None:
        raise UpdateError(
            "bad_checksum",
            "The published checksum file %s.sha256 could not be read, so the package was not downloaded. Release page: %s"
            % (wanted_name, release_url),
            "发布的校验文件 %s.sha256 格式无效，因此没有下载安装包。发布页：%s"
            % (wanted_name, release_url),
            502,
            extra={"release_url": release_url},
        )

    if on_progress:
        on_progress("downloading")
    downloads = Path(outside_mod.downloads_folder())
    fallback = Path(store.data_dir).resolve().parent / "exports"
    saved_path, actual_sha256, size_bytes = _stream_to_destination(
        asset["url"], headers, wanted_name, expected_sha256, [downloads, fallback], transport=transport,
    )
    saved_info = {
        "name": saved_path.name,
        "folder": str(saved_path.parent),
        "path": str(saved_path),
        "size": size_bytes,
        "in_downloads": saved_path.parent == downloads,
    }
    return {
        "version": latest["version"],
        "tag": latest["tag"],
        "asset_name": wanted_name,
        "sha256": actual_sha256,
        "sha256_verified": True,
        "integrity_note": (
            "SHA-256 matched %s.sha256: this confirms the download arrived complete and matches the checksum published "
            "on GitHub, not that the release is signed by an independent certificate." % wanted_name
        ),
        "integrity_note_zh": (
            "SHA-256 与 %s.sha256 一致：这说明下载完整、与 GitHub 发布页上的校验值一致；"
            "但文件和校验值都来自同一个 GitHub 仓库，且安装包未做代码签名，它不代表第三方机构对发布者身份的签名认证。"
            % wanted_name
        ),
        "release_url": release_url,
        "saved": saved_info,
        "desktop": bool(status["current"]["desktop"]),
        "data_dir": status["data_dir"],
    }


# ---- Cross-version settings & data preservation for portable .zip builds ----

def _safe_count_tables(db_file: Path) -> Tuple[int, int]:
    """Read ``(scripts, samples)`` counts from a neighbouring ``studio.db`` without trusting views or triggers."""
    if not backup._is_plain_file(db_file):
        return 0, 0
    try:
        with open(str(db_file), "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                return 0, 0
        conn = backup._open_read_only(db_file)
    except (OSError, sqlite3.Error):
        return 0, 0
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('scripts', 'samples')"
            ).fetchall()
        }
        scripts_n = int(conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]) if "scripts" in tables else 0
        samples_n = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]) if "samples" in tables else 0
        return max(0, scripts_n), max(0, samples_n)
    except sqlite3.Error:
        return 0, 0
    finally:
        conn.close()


def discover_previous_installations(store: Store, search_root: Optional[Path] = None, *,
                                    explicit: bool = False) -> List[Dict[str, Any]]:
    """Find sibling ``learn-my-voice-<version>/data`` folders beside this portable folder."""
    if not explicit and search_root is None and os.environ.get("LEARN_MY_VOICE_NO_SIBLING_SCAN") == "1":
        return []
    current_data = Path(store.data_dir).resolve()
    app_root = current_data.parent
    parent = Path(search_root).resolve() if search_root is not None else app_root.parent
    if not parent.is_dir() or parent.is_symlink():
        return []

    found: List[Tuple[VersionKey, Dict[str, Any]]] = []
    try:
        children = sorted(parent.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    for child in children[:100]:
        if child.is_symlink() or not child.is_dir():
            continue
        m = _SIBLING_DIR_RE.fullmatch(child.name)
        if not m:
            continue
        ver_str = m.group(1)
        parsed = parse_version(ver_str)
        if parsed is None:
            continue
        raw_cand = child / "data"
        if raw_cand.is_symlink() or not raw_cand.is_dir():
            continue
        cand_data = raw_cand.resolve()
        if cand_data == current_data:
            continue
        db_file = cand_data / "studio.db"
        cfg_file = cand_data / "config.json"
        dna_file = cand_data / "voice_dna.json"
        has_db = backup._is_plain_file(db_file)
        has_cfg = backup._is_plain_file(cfg_file)
        has_dna = backup._is_plain_file(dna_file)
        if not (has_db or has_cfg or has_dna):
            continue
        scripts_n, samples_n = _safe_count_tables(db_file) if has_db else (0, 0)
        whisper_dir = child / "local_tools" / "whisper" / "venv"
        has_whisper = whisper_dir.is_dir() and not whisper_dir.is_symlink()
        found.append((parsed, {
            "folder_name": child.name,
            "path": str(child.resolve()),
            "version": ver_str,
            "scripts": scripts_n,
            "samples": samples_n,
            "has_config": has_cfg,
            "has_voice_dna": has_dna,
            "has_whisper": has_whisper,
        }))

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:10]]


def _data_not_empty_error() -> UpdateError:
    return UpdateError(
        "data_not_empty",
        "This folder already holds your work, so nothing was copied over it. To replace what is here with everything "
        "from another folder, make a backup there and restore it under Backup and restore in Settings.",
        "当前数据文件夹里已经有内容了，所以没有覆盖任何东西。如果要用另一个文件夹的内容替换这里，请先在旧版本里备份，"
        "再到「设置」的「备份和恢复」中恢复。",
        409,
    )


def _migrate_busy_error() -> UpdateError:
    return UpdateError(
        "migrate_busy",
        "Something is still running in the app (a transcription, another task, or a save). Wait until it finishes "
        "before migrating data.",
        "应用里还有任务或保存正在进行（例如转写或生成任务）。请等它完成后再迁移数据。",
        409,
    )


def _bad_source_data_error(detail_en: str, detail_zh: str) -> UpdateError:
    return UpdateError(
        "bad_source_data",
        "The earlier folder's data did not pass the safety checks (%s), so nothing here was changed." % detail_en,
        "旧版本文件夹里的数据没有通过安全检查（%s），当前内容没有改动。" % detail_zh,
        400,
    )


def _clean_migrated_config(src_cfg: Dict[str, Any], target_existing_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the earlier folder's settings and sealed API keys, without carrying over stale release caches."""
    out = dict(src_cfg)
    old_updates = out.get("updates") if isinstance(out.get("updates"), dict) else {}
    cur_updates = target_existing_cfg.get("updates") if isinstance(target_existing_cfg.get("updates"), dict) else {}
    merged_updates: Dict[str, Any] = {}
    for key in ("auto_check", "include_prereleases"):
        if isinstance(old_updates.get(key), bool):
            merged_updates[key] = old_updates[key]
        elif isinstance(cur_updates.get(key), bool):
            merged_updates[key] = cur_updates[key]
    if isinstance(old_updates.get("dismissed_version"), str) and parse_version(old_updates["dismissed_version"]) is not None:
        merged_updates["dismissed_version"] = old_updates["dismissed_version"].strip()[:64]
    if "cached" in cur_updates:
        merged_updates["cached"] = cur_updates["cached"]
    if "last_checked_at" in cur_updates:
        merged_updates["last_checked_at"] = cur_updates["last_checked_at"]
    if merged_updates:
        out["updates"] = merged_updates
    else:
        out.pop("updates", None)
    return out


def _read_checked_json_dict(path: Path, label: str) -> Dict[str, Any]:
    if not backup._is_plain_file(path):
        raise _bad_source_data_error("%s is not a plain file" % label, "%s 不是普通文件" % label)
    try:
        size = os.lstat(str(path)).st_size
        if size > backup.MAX_SMALL_FILE_BYTES:
            raise _bad_source_data_error("%s exceeds size limit" % label, "%s 超过大小上限" % label)
        parsed = llm.loads_strict(path.read_bytes().decode("utf-8"))
    except UpdateError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise _bad_source_data_error("%s is not readable JSON" % label, "%s 不是有效的 JSON" % label)
    if not isinstance(parsed, dict):
        raise _bad_source_data_error("%s is not a JSON object" % label, "%s 格式无效" % label)
    return parsed


def _stage_folder_migration(src_data: Path, work: Path, target_existing_cfg: Dict[str, Any]) -> Tuple[Store, List[str], Optional[bytes]]:
    """Validate and stage ``src_data`` into ``work / 'new'`` using ``backup._rebuild`` and ``backup._try_out``."""
    arrived = work / "in"
    new_dir = work / "new"
    arrived.mkdir(mode=0o700, parents=True)
    new_dir.mkdir(mode=0o700)
    copied: List[str] = []

    # 1. Check and stage config.json (preserving sealed API keys & UI preferences).
    cfg_bytes: Optional[bytes] = None
    src_cfg = src_data / "config.json"
    if os.path.lexists(str(src_cfg)):
        parsed_cfg = _read_checked_json_dict(src_cfg, "config.json")
        cleaned_cfg = _clean_migrated_config(parsed_cfg, target_existing_cfg)
        cfg_bytes = Store._encode(cleaned_cfg)
        Store._write_bytes(new_dir / "config.json", cfg_bytes, mode=0o600)
        copied.append("config.json")

    # 2. Check and stage creator, voice DNA, waiting voice proposal, and recommendations.
    for name in backup.PLAIN_FILES:
        src_f = src_data / name
        if os.path.lexists(str(src_f)):
            parsed_obj = _read_checked_json_dict(src_f, name)
            Store._write_bytes(new_dir / name, Store._encode(parsed_obj), mode=0o600)
            copied.append(name)

    # 3. Check and stage Voice DNA history.
    src_hist = src_data / backup.HISTORY_FOLDER
    if os.path.lexists(str(src_hist)):
        if src_hist.is_symlink() or not src_hist.is_dir():
            raise _bad_source_data_error("voice_dna_history is a link or not a folder", "voice_dna_history 不是普通文件夹")
        for entry in src_hist.iterdir():
            if entry.is_symlink():
                raise _bad_source_data_error("voice_dna_history contains a symbolic link", "voice_dna_history 含有符号链接")
        names = backup._history_names(src_hist)[-backup.MAX_HISTORY_FILES:]
        if names:
            dst_hist = new_dir / backup.HISTORY_FOLDER
            dst_hist.mkdir(mode=0o700, exist_ok=True)
            for hname in names:
                hpath = src_hist / hname
                if hname.endswith(".json"):
                    parsed_hist = _read_checked_json_dict(hpath, hname)
                    Store._write_bytes(dst_hist / hname, Store._encode(parsed_hist), mode=0o600)
                else:
                    if os.lstat(str(hpath)).st_size <= backup.MAX_SMALL_FILE_BYTES:
                        Store._write_bytes(dst_hist / hname, hpath.read_bytes(), mode=0o600)
            copied.append(backup.HISTORY_FOLDER)

    # 4. Snapshot, validate, and rebuild SQLite database through backup._rebuild.
    src_db = src_data / backup.DATABASE
    if os.path.lexists(str(src_db)):
        if not backup._is_plain_file(src_db):
            raise _bad_source_data_error("studio.db is not a plain file", "studio.db 不是普通文件")
        for sidecar in ("studio.db-wal", "studio.db-shm", "studio.db-journal"):
            if os.path.islink(str(src_data / sidecar)):
                raise _bad_source_data_error("%s is a symbolic link" % sidecar, "%s 是符号链接" % sidecar)
        if os.lstat(str(src_db)).st_size > backup.MAX_UNPACKED_BYTES:
            raise _bad_source_data_error("studio.db exceeds size limit", "studio.db 超过大小上限")
        try:
            with open(str(src_db), "rb") as fh:
                if fh.read(16) != b"SQLite format 3\x00":
                    raise _bad_source_data_error("studio.db is not a SQLite database", "studio.db 不是有效的 SQLite 数据库")
            snap = arrived / backup.DATABASE
            backup._copy_database(src_db, snap)
            with contextlib.closing(sqlite3.connect(str(snap))) as snap_conn:
                try:
                    snap_conn.execute("PRAGMA trusted_schema = OFF").fetchall()
                except sqlite3.Error:
                    pass
                snap_conn.execute("PRAGMA journal_mode = DELETE").fetchall()
            backup._rebuild(snap, new_dir / backup.DATABASE)
        except UpdateError:
            raise
        except backup.Refused as exc:
            raise _bad_source_data_error(exc.message, exc.message_zh)
        except (OSError, sqlite3.Error, TimeoutError) as exc:
            raise _bad_source_data_error("database could not be read: %s" % str(exc)[:80],
                                         "数据库无法安全读取：%s" % str(exc)[:80])
        copied.append(backup.DATABASE)

    # 5. Open Store on the staged folder (applying schema migrations for older versions) and verify every reader.
    try:
        staged = Store(new_dir)
        backup._check_scripts(staged)
        backup._try_out(staged)
    except backup.Refused as exc:
        raise _bad_source_data_error(exc.message, exc.message_zh)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise _bad_source_data_error("staged data failed verification: %s" % str(exc)[:80],
                                     "迁移后的数据未通过读取检查：%s" % str(exc)[:80])

    # Re-read config.json after Store(new_dir) in case legacy plaintext keys were sealed during Store.__init__.
    if (new_dir / "config.json").is_file():
        cfg_bytes = (new_dir / "config.json").read_bytes()

    return staged, copied, cfg_bytes


def _apply_staged_to_empty_store(target_store: Store, new_dir: Path, cfg_bytes: Optional[bytes]) -> None:
    """Copy verified staged data into ``target_store`` (replacing DB content via SQLite backup, never unlinking DB)."""
    backup._copy_database(new_dir / backup.DATABASE, target_store.db_path)
    with store_module._DNA_LOCK:
        for name in backup.PLAIN_FILES:
            staged_f = new_dir / name
            if backup._is_plain_file(staged_f):
                Store._write_bytes(target_store.data_dir / name, staged_f.read_bytes(), mode=0o600)
        staged_hist = new_dir / backup.HISTORY_FOLDER
        earlier = backup._history_names(staged_hist)
        if earlier:
            target_store.dna_history_dir.mkdir(mode=0o700, exist_ok=True)
            for hname in earlier:
                Store._write_bytes(target_store.dna_history_dir / hname, (staged_hist / hname).read_bytes(), mode=0o600)
    if cfg_bytes is not None:
        with store_module._CONFIG_LOCK:
            Store._write_bytes(target_store.config_path, cfg_bytes, mode=0o600)
    target_store.__dict__.pop("_voice_reference", None)
    _owner_only(target_store.data_dir)


def migrate_from_previous_folder(target_store: Store, source_app_dir: Path, *,
                                 backups: Optional[backup.Backups] = None,
                                 jobs: Any = None) -> Dict[str, Any]:
    """Fill an empty ``target_store`` from ``source_app_dir / 'data'``.

    - Refuses unless ``target_store`` is completely empty (otherwise names Backup and restore in Settings).
    - Refuses while any background job or concurrent write is running.
    - Respects ``Backups``'s gate and advances ``Backups.epoch`` so a page open before migration cannot overwrite
      what arrived.
    - Validates and rebuilds ``studio.db`` through ``studio.backup`` before anything in ``target_store`` is touched.
    - Never copies ``local_tools/whisper``; reports ``whisper_copied=False`` and ``had_whisper`` so the caller/page
      can state plainly that local transcription must be set up again in a new ``.zip`` folder.
    """
    raw_src = Path(source_app_dir)
    if raw_src.is_symlink() or not raw_src.is_dir():
        raise UpdateError("no_source_data", "The selected earlier version folder does not exist.",
                          "找不到所选的旧版本文件夹。", 400)
    src_root = raw_src.resolve()
    raw_src_data = src_root / "data"
    if raw_src_data.is_symlink() or not raw_src_data.is_dir():
        raise UpdateError("no_source_data", "No data/ folder was found inside the selected version folder.",
                          "所选旧版本文件夹中没有找到 data/ 数据目录。", 400)
    src_data = raw_src_data.resolve()
    dst_data = Path(target_store.data_dir).resolve()
    if src_data == dst_data:
        raise UpdateError("same_folder", "Source and current data folders are the same.",
                          "源目录与当前数据目录相同。", 400)

    def run_migration() -> Dict[str, Any]:
        active_jobs = jobs if jobs is not None else (backups.jobs if backups is not None else None)
        if active_jobs is not None and active_jobs.unfinished():
            raise _migrate_busy_error()
        if not is_data_empty(target_store):
            raise _data_not_empty_error()

        work_parent = backups.folder if backups is not None else dst_data
        work_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(dir=str(work_parent), prefix="work-migrate-"))
        try:
            existing_cfg = target_store._read_raw_config_dict()
            _staged, copied_files, cfg_bytes = _stage_folder_migration(src_data, work, existing_cfg)
            if backups is not None:
                backups.gate.keep_everyone_out()
            _apply_staged_to_empty_store(target_store, work / "new", cfg_bytes)
            if backups is not None:
                backups._set_state(dict(backups._state, epoch=uuid.uuid4().hex[:12]))
        finally:
            backup._remove_folder(work)

        upgraded = Store(dst_data)
        whisper_venv = src_root / "local_tools" / "whisper" / "venv"
        had_whisper = whisper_venv.is_dir() and not whisper_venv.is_symlink()
        return {
            "migrated_from": str(src_root),
            "copied": copied_files,
            "whisper_copied": False,
            "had_whisper": had_whisper,
            "not_copied": ["local_tools/whisper"],
            "transcription_note": (
                "On-device transcription (local_tools/whisper) is not copied between .zip folders; if you use it, "
                "set it up again in the new folder."
            ),
            "transcription_note_zh": (
                "本机转写环境（local_tools/whisper）不会在压缩包版本之间复制；如果需要使用本机转写，请在新版本文件夹中重新安装。"
            ),
            "scripts": len(upgraded.list_scripts()),
            "samples": upgraded.samples_count(),
            "settings": upgraded.public_settings(),
        }

    if backups is None:
        return run_migration()

    try:
        with backups._working():
            try:
                backups.gate.keep_changes_out()
            except backup._Busy:
                raise _migrate_busy_error()
            try:
                return run_migration()
            finally:
                backups.gate.open()
    except backup.Refused as exc:
        if exc.code == "backup_busy":
            raise _migrate_busy_error()
        raise UpdateError(exc.code, exc.message, exc.message_zh, exc.status)


# ---- Strict bounded .zip unpacking into a new sibling folder ----------------

def _is_windows_safe_part(part: str) -> bool:
    """True when one path segment is safe across macOS, Windows, and Linux."""
    if not part or part in (".", "..") or part != part.rstrip(" ."):
        return False
    if len(part) > 120 or len(part.encode("utf-8")) > 200:
        return False
    for ch in part:
        if ch in _WINDOWS_FORBIDDEN or ord(ch) < 32 or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            return False
    stem = part.split(".", 1)[0].strip().upper()
    if stem in _WINDOWS_DEVICES:
        return False
    return True


def _check_zip_central_directory(pkg: Path, size: int) -> None:
    """Inspect the end-of-central-directory record before ``zipfile`` parses all headers."""
    with open(str(pkg), "rb") as fh:
        if fh.read(4) != b"PK\x03\x04":
            raise UpdateError("bad_zip", "Downloaded file is not a valid zip archive.",
                              "下载的文件不是有效的 zip 压缩包。", 400)
        tail_len = min(size, 65557)
        fh.seek(size - tail_len)
        tail = fh.read(tail_len)
    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos == -1 or len(tail) - eocd_pos < 22:
        raise UpdateError("bad_zip", "Downloaded zip archive is incomplete or damaged.",
                          "下载的 zip 压缩包不完整或已损坏。", 400)
    _mark, disk, first_disk, here, total, dir_size, _dir_at, _remark = struct.unpack(
        "<4s4H2LH", tail[eocd_pos:eocd_pos + 22]
    )
    if disk or first_disk or here != total or 0xFFFF in (here, total):
        raise UpdateError("bad_zip", "Multi-disk or Zip64 packages are not accepted here.",
                          "不支持分卷或超大目录结构的压缩包。", 400)
    if total == 0:
        raise UpdateError("bad_zip", "Archive is empty.", "压缩包为空。", 400)
    if total > MAX_ZIP_ENTRIES or dir_size > MAX_ZIP_DIRECTORY_BYTES:
        raise UpdateError("zip_too_large",
                          "Archive holds too many entries (%d; limit %d)." % (total, MAX_ZIP_ENTRIES),
                          "压缩包内文件数量过多（%d 个，上限 %d 个）。" % (total, MAX_ZIP_ENTRIES), 413)


def _destination_exists_error(folder_name: str) -> UpdateError:
    return UpdateError(
        "destination_exists",
        "The destination folder %s already exists, so the package was not unpacked over it. Move or rename that "
        "folder first, or unpack the zip yourself in another place." % folder_name,
        "目标文件夹 %s 已经存在，为避免覆盖现有内容，没有解压到该位置。请先移走或重命名该文件夹，或手动解压到其他目录。"
        % folder_name,
        409,
    )


def unpack_and_migrate_zip(current_store: Store, zip_path: Path, *,
                           dest_parent: Optional[Path] = None,
                           jobs: Any = None) -> Dict[str, Any]:
    """Unpack a verified release ``.zip`` into a new sibling folder and copy ``current_store``'s data into it.

    - Never unpacks into an existing destination folder (refuses in two languages with ``destination_exists``).
    - Unpacks into a sibling temporary directory with strict bounds on entry count, per-entry and total
      uncompressed bytes, refusing symlinks, encrypted entries, traversal, ``data/``, ``local_tools/`` and
      Windows-unsafe names, copies ``data/`` into the temporary directory, and renames into place at the end.
    - Never copies ``local_tools/whisper``.
    """
    if jobs is not None and jobs.unfinished():
        raise _migrate_busy_error()

    raw_pkg = Path(zip_path)
    if not backup._is_plain_file(raw_pkg):
        raise UpdateError("missing_zip", "Downloaded package file is no longer on disk.",
                          "找不到已下载的压缩包文件。", 404)
    pkg = raw_pkg.resolve()
    try:
        pkg_size = os.lstat(str(pkg)).st_size
    except OSError:
        raise UpdateError("missing_zip", "Downloaded package file is no longer on disk.",
                          "找不到已下载的压缩包文件。", 404)
    if pkg_size < 30:
        raise UpdateError("bad_zip", "Downloaded file is not a valid zip archive.",
                          "下载的文件不是有效的 zip 压缩包。", 400)
    if pkg_size > MAX_ASSET_BYTES:
        raise UpdateError("zip_too_large", "Package exceeds the maximum archive size.",
                          "压缩包超过了允许的大小上限。", 413)

    _check_zip_central_directory(pkg, pkg_size)

    current_app_dir = Path(current_store.data_dir).resolve().parent
    parent = Path(dest_parent).resolve() if dest_parent is not None else current_app_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise UpdateError("save_failed", "Destination parent folder is not available.",
                          "目标上级目录不可用。", 507)

    try:
        with zipfile.ZipFile(str(pkg), "r") as zf:
            infos = zf.infolist()
            if not infos:
                raise UpdateError("bad_zip", "Archive is empty.", "压缩包为空。", 400)
            if len(infos) > MAX_ZIP_ENTRIES:
                raise UpdateError("zip_too_large", "Archive holds too many entries.", "压缩包内条目过多。", 413)

            tops = set()
            seen_names = set()
            declared_total = 0
            for info in infos:
                name = info.filename
                if info.orig_filename != name or not name or name.startswith("/") or "\\" in name:
                    raise UpdateError("bad_zip", "Unsafe path inside archive.", "压缩包内包含不安全路径。", 400)
                if info.flag_bits & 0x1:
                    raise UpdateError("bad_zip", "Encrypted archive entries are refused.", "拒绝加密的压缩包条目。", 400)
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise UpdateError("bad_zip", "Unsupported compression method in archive.",
                                      "压缩包使用了不支持的压缩格式。", 400)
                kind = stat.S_IFMT(info.external_attr >> 16)
                if info.is_dir():
                    if kind not in (0, stat.S_IFDIR):
                        raise UpdateError("bad_zip", "Unsafe entry type inside archive.", "压缩包内包含异常文件类型。", 400)
                else:
                    if kind not in (0, stat.S_IFREG):
                        # Refuses symbolic links (S_IFLNK), FIFOs, devices, sockets.
                        raise UpdateError("bad_zip", "Symbolic links and special files in archives are refused.",
                                          "压缩包内不允许包含符号链接或特殊文件。", 400)

                trimmed = name[:-1] if info.is_dir() and name.endswith("/") else name
                parts = trimmed.split("/")
                if len(parts) > 16 or not all(_is_windows_safe_part(part) for part in parts):
                    raise UpdateError("bad_zip", "Archive contains a path name that is unsafe on Windows or POSIX.",
                                      "压缩包内包含不安全或不兼容 Windows 的文件名。", 400)
                norm_key = trimmed.lower()
                if norm_key in seen_names:
                    raise UpdateError("bad_zip", "Archive contains duplicate or case-colliding file names.",
                                      "压缩包内包含重复或大小写冲突的文件名。", 400)
                seen_names.add(norm_key)
                tops.add(parts[0])
                if len(parts) > 1:
                    rel_check = "/".join(parts[1:]) + ("/" if info.is_dir() else "")
                    if any(rel_check == pref[:-1] or rel_check.startswith(pref) for pref in _FORBIDDEN_ZIP_PREFIXES):
                        raise UpdateError("bad_zip", "Archive holds %s, which a release package never carries." % parts[1],
                                          "压缩包内含有不应出现的 %s 目录。" % parts[1], 400)
                if info.file_size < 0 or info.file_size > MAX_ZIP_ENTRY_BYTES:
                    raise UpdateError("zip_too_large", "An entry in the archive exceeds the size limit.",
                                      "压缩包内单个文件超过大小上限。", 413)
                declared_total += info.file_size
                if declared_total > MAX_ZIP_UNPACKED_BYTES:
                    raise UpdateError("zip_too_large", "Archive unpacks to more than the allowed size limit.",
                                      "压缩包解压总大小超过上限。", 413)

            if len(tops) != 1:
                raise UpdateError("bad_zip", "Package must contain a single version folder.",
                                  "压缩包必须只包含一个版本根目录。", 400)
            top_folder = next(iter(tops))
            m_top = _SIBLING_DIR_RE.fullmatch(top_folder)
            if not m_top or parse_version(m_top.group(1)) is None:
                raise UpdateError("bad_zip", "Unexpected package root folder: %s" % top_folder[:80],
                                  "压缩包根目录名异常：%s" % top_folder[:80], 400)
            expected_ver = m_top.group(1)
            build_member = "%s/%s" % (top_folder, "studio/build.json")
            if build_member.lower() not in seen_names:
                raise UpdateError("bad_zip", "Package has no studio/build.json.",
                                  "压缩包缺少 studio/build.json。", 400)

            target_dir = parent / top_folder
            if os.path.normcase(str(target_dir.resolve())) == os.path.normcase(str(current_app_dir)):
                raise UpdateError("already_at_target", "This folder is already at %s." % top_folder,
                                  "当前已经运行在 %s 文件夹中。" % top_folder, 409)
            if os.path.lexists(str(target_dir)):
                raise _destination_exists_error(top_folder)

            staging_dir = Path(tempfile.mkdtemp(dir=str(parent), prefix=".%s.unpacking-" % top_folder))
            placed = False
            try:
                staging_resolved = staging_dir.resolve()
                actual_total = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    rel = info.filename[len(top_folder) + 1:]
                    if not rel:
                        continue
                    out_file = (staging_resolved / rel).resolve()
                    if not str(out_file).startswith(str(staging_resolved) + os.sep):
                        raise UpdateError("bad_zip", "Path traversal refused.", "拒绝越界解压路径。", 400)
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with zf.open(info, "r") as src_entry, \
                            open(os.open(str(out_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb") as dst_entry:
                        while True:
                            block = src_entry.read(_CHUNK)
                            if not block:
                                break
                            written += len(block)
                            actual_total += len(block)
                            if written > info.file_size or written > MAX_ZIP_ENTRY_BYTES or actual_total > MAX_ZIP_UNPACKED_BYTES:
                                raise UpdateError("zip_too_large", "Archive unpacked to more bytes than stated or allowed.",
                                                  "压缩包实际解压大小超过声明值或安全上限。", 413)
                            dst_entry.write(block)
                    if written != info.file_size:
                        raise UpdateError("bad_zip", "Archive entry size did not match its header.",
                                          "压缩包内文件大小与目录头不一致。", 400)
                    unix_mode = (info.external_attr >> 16) & 0o777
                    if unix_mode & 0o111:
                        try:
                            os.chmod(str(out_file), 0o755)
                        except OSError:
                            pass

                # Check studio/build.json inside the unpacked folder matches the folder's version.
                built_info = buildinfo.read(staging_resolved / "studio" / "build.json")
                if built_info.get("from_source") or built_info.get("version") != expected_ver:
                    raise UpdateError("bad_zip", "Package build.json does not match folder %s." % top_folder,
                                      "压缩包内的 build.json 与目录版本 %s 不一致。" % top_folder, 400)

                # Copy current data/ into the newly unpacked staging folder before moving it into place.
                staged_new_store = Store(staging_resolved / "data")
                mig = migrate_from_previous_folder(staged_new_store, current_app_dir, jobs=jobs)

                if os.path.lexists(str(target_dir)):
                    raise _destination_exists_error(top_folder)
                try:
                    os.rename(str(staging_dir), str(target_dir))
                except FileExistsError:
                    raise _destination_exists_error(top_folder)
                except OSError as exc:
                    if os.path.lexists(str(target_dir)):
                        raise _destination_exists_error(top_folder)
                    raise UpdateError("save_failed", "Could not place unpacked folder %s: %s" % (top_folder, exc),
                                      "无法放置解压后的新版本目录 %s：%s" % (top_folder, exc), 507)
                placed = True
            finally:
                if not placed:
                    backup._remove_folder(staging_dir)
    except UpdateError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, struct.error):
        raise UpdateError("bad_zip", "Downloaded file is not a valid zip archive.",
                          "下载的文件不是有效的 zip 压缩包。", 400)

    resolved_target = target_dir.resolve()
    mig["migrated_from"] = str(current_app_dir)
    return {
        "unpacked_to": str(resolved_target),
        "folder_name": resolved_target.name,
        "migration": mig,
    }
