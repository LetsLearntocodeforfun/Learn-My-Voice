"""Auto-updater, startup version checker, SHA-256 downloader, and cross-version settings migrator.

Works identically across Desktop (Tauri 2) and portable ``.zip`` / source modes using Python stdlib only:
- Queries GitHub Releases over HTTPS without proxies or credential forwarding, caching results for 12 hours in
  ``data/config.json["updates"]`` so startup never blocks server port binding or hits rate limits.
- Compares SemVer + pre-release channels (``0.2.0-beta.dev < 0.2.0-beta.rc.1 < 0.2.0-beta < 0.2.0``).
- Downloads the matching platform installer (``.dmg``, ``-setup.exe``) or ``.zip`` archive together with its
  published ``.sha256`` checksum sidecar, verifying integrity byte-for-byte before saving to Downloads.
- Preserves user settings, sealed API keys, Voice DNA, library, and scripts across versions:
  - In Desktop mode, data lives in the OS application-data folder outside the app bundle, surviving upgrades
    automatically.
  - In portable ``.zip`` mode (where each release unpacks to ``learn-my-voice-<version>/``), detects sibling
    earlier version folders and offers 1-click migration (or unpacks a verified new ``.zip`` and carries over
    ``data/`` via SQLite online backup + ``config.json`` with sealed API keys + ``local_tools/whisper``).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import buildinfo, llm
from .store import ROOT, Store, _owner_only

DEFAULT_REPO = "LetsLearntocodeforfun/Learn-My-Voice"
GITHUB_API_BASE = "https://api.github.com"
ALLOWED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)
CACHE_TTL_SEC = 12 * 3600
MAX_RELEASES_JSON_BYTES = 1024 * 1024
MAX_CHECKSUM_BYTES = 8192
MAX_ASSET_BYTES = 512 * 1024 * 1024     # 512 MB cap for installer / zip packages
_CHUNK = 256 * 1024

_SEMVER_RE = re.compile(
    r"^v?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+))?"
    r"(?:\.rc\.(?P<rc>[0-9]+)(?:\.(?P<rc_attempt>[0-9]+))?)?"
    r"(?P<dev>\.dev)?$"
)
_SHA256_LINE = re.compile(r"^\s*([0-9a-fA-F]{64})\b")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SIBLING_DIR_RE = re.compile(r"^learn-my-voice-(.+)$")

UpdateTransport = Callable[[str, str, Dict[str, str]], bytes]


class UpdateError(Exception):
    """An update check, download, or migration step failed."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = status


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Allow HTTPS redirects strictly to GitHub release asset CDN hosts."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlparse(resolved)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_DOWNLOAD_HOSTS):
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
    if env and _REPO_RE.fullmatch(env):
        return env
    return DEFAULT_REPO


def parse_version(raw: Any) -> Optional[Tuple[int, int, int, int, str, int, int]]:
    """Parse version string into a strictly comparable tuple.

    Ordering:
    ``0.2.0-beta.dev < 0.2.0-beta.rc.1 < 0.2.0-beta.rc.1.2 < 0.2.0-beta < 0.2.0 < 0.3.0-beta.rc.1``
    """
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    # Strip +build metadata if present
    cleaned = cleaned.split("+", 1)[0]
    m = _SEMVER_RE.fullmatch(cleaned)
    if not m:
        return None
    major = int(m.group("major"))
    minor = int(m.group("minor"))
    patch = int(m.group("patch"))
    pre = (m.group("pre") or "").lower()
    rc_str = m.group("rc")
    rc_att_str = m.group("rc_attempt")
    is_dev = bool(m.group("dev"))

    # Release stability rank within same X.Y.Z:
    # 0 = .dev build
    # 1 = pre-release .rc.N (e.g. 0.2.0-beta.rc.1)
    # 2 = pre-release final (e.g. 0.2.0-beta)
    # 3 = stable .rc.N (e.g. 0.2.0.rc.1)
    # 4 = stable final (e.g. 0.2.0)
    if is_dev:
        stage = 0
    elif pre and rc_str is not None:
        stage = 1
    elif pre:
        stage = 2
    elif rc_str is not None:
        stage = 3
    else:
        stage = 4

    rc_num = int(rc_str) if rc_str is not None else 0
    rc_att = int(rc_att_str) if rc_att_str is not None else 1
    return (major, minor, patch, stage, pre, rc_num, rc_att)


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
    return parsed[3] < 4


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
            m = re.search(r"^## v([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)\b",
                          changelog.read_text(encoding="utf-8"), re.M)
            if m:
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
    """Determine which release asset suffix matches this OS, CPU architecture, and runtime mode."""
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


def _default_transport(method: str, url: str, headers: Dict[str, str]) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise UpdateError("bad_url", "Update URLs must use HTTPS.", "更新地址必须使用 HTTPS。", 400)
    host = (parsed.hostname or "").lower()
    is_api = host == "api.github.com"
    if not is_api and not any(host == h or host.endswith("." + h) for h in ALLOWED_DOWNLOAD_HOSTS):
        raise UpdateError("bad_url", "Disallowed update host: %s" % host, "不允许的更新服务器：%s" % host, 400)
    opener = _API_OPENER if is_api else _ASSET_OPENER
    max_bytes = MAX_RELEASES_JSON_BYTES if is_api else MAX_ASSET_BYTES
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(req, timeout=15 if is_api else 120) as resp:
            chunks: List[bytes] = []
            total = 0
            while True:
                piece = resp.read(_CHUNK)
                if not piece:
                    break
                total += len(piece)
                if total > max_bytes:
                    raise UpdateError("too_large", "Update response exceeded size limit.", "更新数据超过大小限制。", 502)
                chunks.append(piece)
            return b"".join(chunks)
    except UpdateError:
        raise
    except urllib.error.HTTPError as exc:
        raise UpdateError(
            "update_http_error",
            "GitHub release check returned HTTP %d." % exc.code,
            "检查 GitHub 更新时返回 HTTP %d。" % exc.code,
            502,
        )
    except (urllib.error.URLError, OSError) as exc:
        reason = str(getattr(exc, "reason", exc))[:160]
        raise UpdateError(
            "update_network_error",
            "Could not reach GitHub Releases: %s" % reason,
            "无法连接 GitHub 更新服务：%s" % reason,
            502,
        )


def _stored_settings(store: Store) -> Dict[str, Any]:
    raw = store.load_config().get("updates")
    data = raw if isinstance(raw, dict) else {}
    cur = current_version_info()
    default_pre = is_prerelease_version(cur.get("version"))
    return {
        "auto_check": bool(data.get("auto_check", True)),
        "include_prereleases": bool(data.get("include_prereleases", default_pre)),
        "dismissed_version": str(data.get("dismissed_version") or "")[:64],
        "last_checked_at": float(data.get("last_checked_at") or 0.0)
        if isinstance(data.get("last_checked_at"), (int, float)) else 0.0,
        "cached": data.get("cached") if isinstance(data.get("cached"), dict) else None,
    }


def save_update_settings(store: Store, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Persist startup prompt preference or dismissed version inside ``config.json["updates"]``."""
    def mutate(cfg: Dict[str, Any]) -> None:
        cur = dict(cfg.get("updates") if isinstance(cfg.get("updates"), dict) else {})
        if "auto_check" in changes and isinstance(changes["auto_check"], bool):
            cur["auto_check"] = changes["auto_check"]
        if "include_prereleases" in changes and isinstance(changes["include_prereleases"], bool):
            cur["include_prereleases"] = changes["include_prereleases"]
        if "dismissed_version" in changes and isinstance(changes["dismissed_version"], str):
            cur["dismissed_version"] = changes["dismissed_version"].strip()[:64]
        cfg["updates"] = cur

    store.update_config(mutate)
    return status_payload(store, network=False)


def _select_asset(assets: Any, version: str, target: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Match platform binary/zip asset and its ``.sha256`` checksum sidecar."""
    if not isinstance(assets, list):
        return None, None
    clean_ver = version[1:] if version.startswith("v") else version
    wanted_desktop = "learn-my-voice-%s%s" % (clean_ver, target["suffix"])
    wanted_zip = "learn-my-voice-%s.zip" % clean_ver

    by_name: Dict[str, Dict[str, Any]] = {}
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        size = item.get("size")
        if name and url.startswith("https://"):
            by_name[name] = {
                "name": name,
                "url": url,
                "size": int(size) if isinstance(size, int) and size >= 0 else None,
            }

    primary = by_name.get(wanted_desktop)
    if primary is None and target["mode"] == "desktop":
        # Fallback if exact version string in filename differs slightly
        for name, entry in by_name.items():
            if name.endswith(target["suffix"]) and not name.endswith(".sha256"):
                primary = entry
                break
    if primary is None:
        primary = by_name.get(wanted_zip)
        if primary is None:
            for name, entry in by_name.items():
                if name.startswith("learn-my-voice-") and name.endswith(".zip"):
                    primary = entry
                    break

    checksum = by_name.get(primary["name"] + ".sha256") if primary else None
    return primary, checksum


def _pick_best_release(releases: Any, *, include_prereleases: bool,
                       target: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not isinstance(releases, list):
        return None
    candidates: List[Tuple[Tuple[int, int, int, int, str, int, int], Dict[str, Any]]] = []
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") is True:
            continue
        tag = str(rel.get("tag_name") or "").strip()
        ver = tag[1:] if tag.startswith("v") else tag
        parsed = parse_version(ver)
        if parsed is None:
            continue
        is_pre = bool(rel.get("prerelease")) or parsed[3] < 4
        if is_pre and not include_prereleases:
            continue
        asset, checksum = _select_asset(rel.get("assets"), ver, target)
        html_url = str(rel.get("html_url") or "").strip()
        if not html_url.startswith("https://github.com/"):
            html_url = "https://github.com/%s/releases/tag/%s" % (repo_name(), urllib.parse.quote(tag, safe="._-"))
        body = str(rel.get("body") or "").strip()[:4000]
        published_at = str(rel.get("published_at") or "")[:32]
        candidates.append((parsed, {
            "version": ver,
            "tag": tag or ("v" + ver),
            "prerelease": is_pre,
            "published_at": published_at,
            "html_url": html_url,
            "notes": body,
            "asset": asset,
            "checksum_asset": checksum,
        }))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def check_for_updates(store: Store, *, force: bool = False,
                      transport: Optional[UpdateTransport] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
    """Return update status, querying GitHub Releases when forced or when the 12h cache expired."""
    now_ts = time.time() if now is None else float(now)
    prefs = _stored_settings(store)
    cur = current_version_info()
    target = current_platform_target(is_desktop=cur["desktop"])

    use_cache = (
        not force
        and prefs["cached"] is not None
        and prefs["last_checked_at"] > 0
        and (now_ts - prefs["last_checked_at"]) < CACHE_TTL_SEC
    )

    latest = prefs["cached"] if use_cache else None
    check_error: Optional[Dict[str, str]] = None

    if not use_cache:
        call = transport or _default_transport
        url = "%s/repos/%s/releases?per_page=15" % (GITHUB_API_BASE, repo_name())
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "learn-my-voice-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            raw_bytes = call("GET", url, headers)
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
            prefs = _stored_settings(store)
        except (UpdateError, ValueError, UnicodeDecodeError) as exc:
            if force:
                if isinstance(exc, UpdateError):
                    raise
                raise UpdateError("bad_release_json", "Invalid release metadata from GitHub.",
                                  "GitHub 返回的版本信息格式无效。", 502)
            check_error = {
                "code": getattr(exc, "code", "update_network_error"),
                "message": getattr(exc, "message", str(exc)),
                "message_zh": getattr(exc, "message_zh", "暂时无法连接更新服务器"),
            }
            latest = prefs["cached"]

    return _compose_status(store, cur, target, prefs, latest, check_error=check_error)


def status_payload(store: Store, *, network: bool = False,
                   transport: Optional[UpdateTransport] = None) -> Dict[str, Any]:
    """Return current update status without network I/O unless ``network=True``."""
    if network:
        return check_for_updates(store, force=False, transport=transport)
    prefs = _stored_settings(store)
    cur = current_version_info()
    target = current_platform_target(is_desktop=cur["desktop"])
    return _compose_status(store, cur, target, prefs, prefs["cached"])


def _compose_status(store: Store, cur: Dict[str, Any], target: Dict[str, str],
                    prefs: Dict[str, Any], latest: Optional[Dict[str, Any]],
                    check_error: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    latest_ver = latest.get("version") if isinstance(latest, dict) else None
    newer = bool(latest_ver and is_newer(latest_ver, cur["version"]))
    # When running from a git checkout (from_source), report availability in Settings but do not nag at startup
    prompt_startup = bool(
        newer
        and prefs["auto_check"]
        and not cur["from_source"]
        and latest_ver != prefs["dismissed_version"]
    )
    previous = discover_previous_installations(store)
    out: Dict[str, Any] = {
        "current": cur,
        "target": target,
        "data_dir": str(Path(store.data_dir).resolve()),
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
        "releases_url": "https://github.com/%s/releases" % repo_name(),
    }
    if check_error:
        out["check_error"] = check_error
    return out


def download_release_asset(store: Store, outside_mod: Any, *,
                           transport: Optional[UpdateTransport] = None,
                           on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Download the latest matching release asset, verify its SHA-256 sidecar, and save to Downloads."""
    status = check_for_updates(store, force=False, transport=transport)
    latest = status.get("latest")
    if not isinstance(latest, dict) or not isinstance(latest.get("asset"), dict):
        status = check_for_updates(store, force=True, transport=transport)
        latest = status.get("latest")
    if not isinstance(latest, dict) or not isinstance(latest.get("asset"), dict):
        raise UpdateError(
            "no_update_asset",
            "No downloadable package was found for this system on GitHub Releases.",
            "在 GitHub Releases 中没有找到适用于当前系统的安装包。",
            404,
        )

    asset = latest["asset"]
    checksum_asset = latest.get("checksum_asset")
    call = transport or _default_transport
    headers = {"User-Agent": "learn-my-voice-updater", "Accept": "application/octet-stream"}

    expected_sha256: Optional[str] = None
    if isinstance(checksum_asset, dict) and isinstance(checksum_asset.get("url"), str):
        if on_progress:
            on_progress("verifying_checksum")
        raw_sum = call("GET", checksum_asset["url"], headers)
        if len(raw_sum) <= MAX_CHECKSUM_BYTES:
            m = _SHA256_LINE.match(raw_sum.decode("utf-8", "replace"))
            if m:
                expected_sha256 = m.group(1).lower()

    if on_progress:
        on_progress("downloading")
    payload = call("GET", asset["url"], headers)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise UpdateError(
            "checksum_mismatch",
            "Downloaded package SHA-256 (%s) did not match published checksum (%s). File discarded."
            % (actual_sha256[:12], expected_sha256[:12]),
            "下载的安装包 SHA-256 校验不一致（%s ≠ %s），已丢弃该文件。"
            % (actual_sha256[:12], expected_sha256[:12]),
            502,
        )

    downloads = Path(outside_mod.downloads_folder())
    fallback = Path(store.data_dir).resolve().parent / "exports"
    try:
        saved_path = outside_mod.save(asset["name"], payload, [downloads, fallback])
    except OSError as exc:
        raise UpdateError(
            "save_failed",
            "Could not save downloaded package: %s" % exc,
            "无法保存下载的安装包：%s" % exc,
            507,
        )
    saved_info = {
        "name": saved_path.name,
        "folder": str(saved_path.parent),
        "path": str(saved_path),
        "in_downloads": saved_path.parent == downloads,
    }
    return {
        "version": latest["version"],
        "tag": latest["tag"],
        "asset_name": asset["name"],
        "sha256": actual_sha256,
        "sha256_verified": bool(expected_sha256),
        "saved": saved_info,
        "desktop": bool(status["current"]["desktop"]),
        "data_dir": status["data_dir"],
    }


# ---- Cross-version settings & data preservation for portable .zip builds ----

def discover_previous_installations(store: Store, search_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Detect sibling ``learn-my-voice-<old-version>/data`` folders so portable ``.zip`` users never lose settings."""
    if os.environ.get("LEARN_MY_VOICE_NO_SIBLING_SCAN") == "1":
        return []
    current_data = Path(store.data_dir).resolve()
    app_root = current_data.parent
    parent = Path(search_root).resolve() if search_root is not None else app_root.parent
    if not parent.is_dir():
        return []

    found: List[Tuple[Tuple[int, int, int, int, str, int, int], Dict[str, Any]]] = []
    try:
        children = list(parent.iterdir())
    except OSError:
        return []

    for child in children[:100]:
        if not child.is_dir():
            continue
        m = _SIBLING_DIR_RE.fullmatch(child.name)
        if not m:
            continue
        cand_data = (child / "data").resolve()
        if cand_data == current_data or not cand_data.is_dir():
            continue
        db_file = cand_data / "studio.db"
        cfg_file = cand_data / "config.json"
        dna_file = cand_data / "voice_dna.json"
        if not (db_file.is_file() or cfg_file.is_file() or dna_file.is_file()):
            continue
        ver_str = m.group(1)
        parsed = parse_version(ver_str) or (0, 0, 0, 0, "", 0, 0)
        scripts_n, samples_n = 0, 0
        if db_file.is_file():
            try:
                conn = sqlite3.connect(db_file.as_uri() + "?mode=ro", uri=True, timeout=2)
                try:
                    scripts_n = int(conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0])
                    samples_n = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        has_whisper = (child / "local_tools" / "whisper" / "venv").is_dir()
        found.append((parsed, {
            "folder_name": child.name,
            "path": str(child),
            "version": ver_str,
            "scripts": scripts_n,
            "samples": samples_n,
            "has_config": cfg_file.is_file(),
            "has_voice_dna": dna_file.is_file(),
            "has_whisper": has_whisper,
        }))

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in found[:10]]


def _sqlite_backup_file(src_db: Path, dst_db: Path) -> None:
    src_conn = sqlite3.connect(str(src_db), timeout=10)
    try:
        dst_conn = sqlite3.connect(str(dst_db), timeout=10)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def migrate_from_previous_folder(target_store: Store, source_app_dir: Path, *,
                                 include_whisper: bool = True) -> Dict[str, Any]:
    """Copy user settings (including sealed API keys & UI look), SQLite DB, Voice DNA, and Whisper from an earlier folder."""
    src_root = Path(source_app_dir).resolve()
    src_data = src_root / "data"
    dst_data = Path(target_store.data_dir).resolve()
    if src_data == dst_data:
        raise UpdateError("same_folder", "Source and current data folders are the same.", "源目录与当前数据目录相同。", 400)
    if not src_data.is_dir():
        raise UpdateError("no_source_data", "No data/ folder found inside the selected version directory.",
                          "所选旧版文件夹中没有找到 data/ 数据目录。", 400)

    copied_files: List[str] = []
    dst_data.mkdir(mode=0o700, parents=True, exist_ok=True)

    # 1. Copy JSON settings & profiles (preserving sealed API keys — OS keystore.py unseals them seamlessly!)
    for name in ("config.json", "creator.json", "voice_dna.json", "voice_pending.json", "recommendations.json"):
        src_f = src_data / name
        if src_f.is_file() and not src_f.is_symlink():
            Store._write_bytes(dst_data / name, src_f.read_bytes(), mode=0o600)
            copied_files.append(name)

    # 2. Copy Voice DNA history
    src_hist = src_data / "voice_dna_history"
    if src_hist.is_dir() and not src_hist.is_symlink():
        dst_hist = dst_data / "voice_dna_history"
        dst_hist.mkdir(mode=0o700, exist_ok=True)
        for item in sorted(src_hist.iterdir())[:1000]:
            if item.is_file() and not item.is_symlink():
                Store._write_bytes(dst_hist / item.name, item.read_bytes(), mode=0o600)
        copied_files.append("voice_dna_history")

    # 3. Copy SQLite database safely via online backup API, then re-run Store schema migrations!
    src_db = src_data / "studio.db"
    if src_db.is_file() and not src_db.is_symlink():
        tmp_db = dst_data / "studio.db.migrating"
        if tmp_db.exists():
            tmp_db.unlink()
        _sqlite_backup_file(src_db, tmp_db)
        _sqlite_backup_file(tmp_db, dst_data / "studio.db")
        try:
            tmp_db.unlink()
        except OSError:
            pass
        copied_files.append("studio.db")

    # Re-run Store(dst_data) so any new columns/tables in this version are added immediately
    upgraded = Store(dst_data)
    _owner_only(dst_data)

    # 4. Optionally carry over on-device Whisper runtime (~2 GB) if target lacks it
    whisper_copied = False
    src_whisper = src_root / "local_tools" / "whisper"
    dst_whisper = dst_data.parent / "local_tools" / "whisper"
    if include_whisper and src_whisper.is_dir() and not dst_whisper.exists() and dst_data.parent != src_root:
        try:
            dst_whisper.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(src_whisper), str(dst_whisper), symlinks=True)
            whisper_copied = True
        except OSError:
            whisper_copied = False

    return {
        "migrated_from": str(src_root),
        "copied": copied_files,
        "whisper_copied": whisper_copied,
        "scripts": len(upgraded.list_scripts()),
        "samples": upgraded.samples_count(),
        "settings": upgraded.public_settings(),
    }


def unpack_and_migrate_zip(current_store: Store, zip_path: Path, *,
                           dest_parent: Optional[Path] = None) -> Dict[str, Any]:
    """Verify a downloaded release ``.zip``, unpack it next to the current folder, and migrate all user settings & data."""
    pkg = Path(zip_path).resolve()
    if not pkg.is_file():
        raise UpdateError("missing_zip", "Downloaded package file is no longer on disk.", "找不到已下载的压缩包文件。", 404)

    try:
        with zipfile.ZipFile(str(pkg), "r") as zf:
            names = zf.namelist()
            if not names:
                raise UpdateError("bad_zip", "Archive is empty.", "压缩包为空。", 400)
            tops = {n.split("/", 1)[0] for n in names if n}
            if len(tops) != 1:
                raise UpdateError("bad_zip", "Package must contain a single version folder.", "压缩包结构无效。", 400)
            top_folder = next(iter(tops))
            if not _SIBLING_DIR_RE.fullmatch(top_folder):
                raise UpdateError("bad_zip", "Unexpected package root folder: %s" % top_folder, "压缩包根目录名异常。", 400)
            for name in names:
                parts = name.split("/")
                if name.startswith("/") or ".." in parts or "\\" in name:
                    raise UpdateError("bad_zip", "Unsafe path inside archive.", "压缩包内包含不安全路径。", 400)
            parent = Path(dest_parent).resolve() if dest_parent is not None else Path(current_store.data_dir).resolve().parent.parent
            target_dir = (parent / top_folder).resolve()
            if target_dir == Path(current_store.data_dir).resolve().parent:
                raise UpdateError("already_at_target", "This folder is already at %s." % top_folder, "当前已在 %s 目录中。" % top_folder, 400)
            target_dir.mkdir(parents=True, exist_ok=True)
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = info.filename[len(top_folder) + 1:]
                if not rel or rel.startswith("data/"):
                    continue
                out_file = (target_dir / rel).resolve()
                if not str(out_file).startswith(str(target_dir) + os.sep):
                    raise UpdateError("bad_zip", "Path traversal refused.", "拒绝越界解压路径。", 400)
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_bytes(zf.read(info))
                unix_mode = (info.external_attr >> 16) & 0o777
                if unix_mode & 0o111:
                    try:
                        os.chmod(str(out_file), 0o755)
                    except OSError:
                        pass
    except zipfile.BadZipFile:
        raise UpdateError("bad_zip", "Downloaded file is not a valid zip archive.", "下载的文件不是有效的 zip 压缩包。", 400)

    new_store = Store(target_dir / "data")
    mig = migrate_from_previous_folder(new_store, Path(current_store.data_dir).resolve().parent)
    return {
        "unpacked_to": str(target_dir),
        "folder_name": target_dir.name,
        "migration": mig,
    }
