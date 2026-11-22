"""Autopilot continuous voice learning, watched folder ingestion, and recurring edit lessons.

Tasks registered with ``studio.autopilot``:

- ``voice.ingest_folder`` (every 10 minutes, ``calls_model=False``): watches a folder the creator chose
  (depth <= 2, skipping hidden files/folders and symbolic links), waits until a file has been seen across
  at least 60 seconds with unchanged ``(size_bytes, mtime_ns)`` and ``size_bytes > 0``, and ingests up to
  5 settled files per run (tracking at most 200 per scan). The creator's files are never modified, renamed,
  or deleted: media is copied to a temporary file in ``store.uploads_dir`` and removed in ``finally:``.
  Media inside ``sponsored/`` or ``商单/`` is marked ``is_sponsored=True`` and never enters organic voice
  learning. Cloud transcription reserves ``context.spend(1)`` dynamically per file and never runs unless
  ``transcribe_route == "cloud"``.
- ``voice.relearn`` (every hour, ``calls_model=True``): proposes a refreshed Voice DNA profile when >= 3
  newly confirmed non-sponsored samples exist since the last run (or since custom DNA was saved), or >= 7
  days have passed with >= 1 newly confirmed non-sponsored sample, and AI is configured (not manual). At
  ``suggest`` it leaves the proposal in ``voice_pending.json``; at ``auto`` it refuses to overwrite a manual
  pending proposal and approves only within deterministic safety bounds.
- ``lessons.accept_recurring`` (every 24 hours, ``calls_model=False``): at ``auto``, accepts undecided
  recurring creator edit patterns across >= 2 non-sponsored AI-drafted scripts, with clean undo.

Standard library only.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from . import autopilot, creator, evidence, guardrails, learning, llm, pipeline, soundstats, textstats, timing, transcribe, video_ingest
from .jobs import JobError
from .store import MAX_SAMPLE_CHARS, MODE_SPONSORED, ROOT, Store, TextTooLong, clean_file_name

SETTING_KEY = "voice"
TRANSCRIBE_ROUTES = ("off", "local", "cloud")
OWN_VOICE_FIELDS = ("watch_dir", "transcribe_route")
SPONSORED_SUBFOLDER = "sponsored"
SPONSORED_SUBFOLDERS = frozenset({"sponsored", "商单"})

MEDIA_EXTENSIONS = frozenset({"mp4", "mov", "m4v", "m4a", "mp3", "wav", "aac", "aif", "aiff", "caf", "flac", "3gp"})
_IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".download")

SETTLE_SEC = 60.0
MAX_FILES_PER_RUN = 5
MAX_TRACKED_PER_SCAN = 200
MAX_WATCHED_ROWS = 1000
RECENT_FILES_LIMIT = 25
MAX_LOCAL_MEDIA_BYTES = 8 * 1024 ** 3

INGEST_EVERY_SEC = 600
RELEARN_EVERY_SEC = 3600
LESSONS_EVERY_SEC = 86400
RELEARN_MIN_NEW_SAMPLES = 3
RELEARN_STALE_SEC = 7 * 86400

TRUST_MIN_SPOKEN_CHARS = 60
TRUST_MIN_CJK_RATIO = 0.40
TRUST_MIN_CPM = 90
TRUST_MAX_CPM = 420
MAX_RATE_SHIFT = 0.35

_KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)
_OS_ROOT_LINKS = frozenset({"/var", "/tmp", "/etc"})
_PLACEHOLDER_EXACT = frozenset({
    "placeholder", "todo", "tbd", "none", "null", "n/a", "test",
    "subtitles by the amara.org community", "amara.org",
    "谢谢观看", "感谢观看", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "字幕制作", "优优独播剧场",
})
_PLACEHOLDER_SUBSTRINGS = (
    "verbatim_transcript_zh_en",
    "spoken_script_zh",
    "[placeholder]",
    "{{",
)

FileMatcher = Callable[..., bool]
FileIngester = Callable[[autopilot.Context, Path], Dict[str, Any]]
FileActiveCheck = Optional[Callable[[Store], bool]]
_HANDLER_LOCK = threading.Lock()
_FILE_HANDLERS: Dict[str, Tuple[FileMatcher, FileIngester, FileActiveCheck]] = {}


class VoiceAutoError(Exception):
    """A request to configure watched-folder voice settings that cannot be met."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = int(status)
        self.extra = dict(extra or {})


@contextlib.contextmanager
def _db(store: Store) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(store._db()) as db:
        yield db


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ---- external file-handler registry --------------------------------------------
def register_file_handler(
    kind: str,
    matches: FileMatcher,
    take_in: FileIngester,
    *,
    active: FileActiveCheck = None,
) -> None:
    """Register a non-media watched-folder file handler (invoked once a matching file settles)."""
    if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind) or kind == "media":
        raise ValueError("file handler kind must be a short lower-case identifier (not 'media'): %r" % (kind,))
    if not callable(matches) or not callable(take_in):
        raise ValueError("matches and take_in must be callables")
    if active is not None and not callable(active):
        raise ValueError("active must be callable or None")
    with _HANDLER_LOCK:
        if kind in _FILE_HANDLERS:
            raise ValueError("file handler %r is already registered" % (kind,))
        _FILE_HANDLERS[kind] = (matches, take_in, active)


def unregister_file_handler(kind: str) -> bool:
    """Remove a registered external file handler (primarily for test isolation)."""
    with _HANDLER_LOCK:
        return _FILE_HANDLERS.pop(kind, None) is not None


def _snapshot_handlers(store: Optional[Store] = None) -> List[Tuple[str, FileMatcher, FileIngester]]:
    with _HANDLER_LOCK:
        entries = list(_FILE_HANDLERS.items())
    out: List[Tuple[str, FileMatcher, FileIngester]] = []
    for kind, (matches, take_in, active) in entries:
        if active is not None and store is not None:
            try:
                if not active(store):
                    continue
            except Exception:  # noqa: BLE001 - treat faulty predicate as inactive
                continue
        out.append((kind, matches, take_in))
    return out


# ---- watched-folder path checks & settings -------------------------------------
def _strip_trailing_sep(raw: str) -> str:
    if len(raw) <= 1:
        return raw
    if os.name == "nt" and len(raw) <= 3 and raw[1:2] == ":":
        return raw
    stripped = raw.rstrip("/\\")
    return stripped if stripped else raw


def _has_user_symlink(path: Path) -> bool:
    """True if ``path`` itself or any non-system-alias ancestor is a symbolic link."""
    try:
        if path.is_symlink():
            return True
        for parent in path.parents:
            s = str(parent)
            if s in _OS_ROOT_LINKS:
                continue
            if parent.is_symlink():
                return True
    except OSError:
        return True
    return False


def _is_inside_or_equal(candidate: Path, forbidden: Path) -> bool:
    return candidate == forbidden or forbidden in candidate.parents


def _forbidden_watch_target(store: Store, candidate: Path) -> Optional[str]:
    """Return ``'root'`` / ``'app_dir'`` / ``'data_dir'`` if ``candidate`` is a forbidden location, else None."""
    try:
        resolved = candidate.resolve()
        app_root = ROOT.resolve()
        data_root = Path(store.data_dir).resolve()
    except OSError:
        return "unresolvable"
    if resolved.parent == resolved:
        return "filesystem_root"
    if _is_inside_or_equal(resolved, app_root):
        return "app_dir"
    if _is_inside_or_equal(resolved, data_root):
        return "data_dir"
    return None


def _validate_watch_dir_for_save(store: Store, raw: Any) -> str:
    if not isinstance(raw, str) or "\x00" in raw or len(raw) > 4096:
        raise VoiceAutoError(
            "bad_watch_dir",
            "The watched folder path has to be a valid text path.",
            "监控文件夹路径必须是有效的文本路径。",
        )
    cleaned = raw.strip()
    if not cleaned:
        return ""
    trimmed = _strip_trailing_sep(cleaned)
    cand = Path(trimmed)
    if not cand.is_absolute():
        raise VoiceAutoError(
            "watch_dir_not_absolute",
            "Choose a full absolute folder path on this computer.",
            "请选择这台电脑上的完整绝对文件夹路径。",
        )
    if _has_user_symlink(cand):
        raise VoiceAutoError(
            "watch_dir_symlink",
            "A symbolic link cannot be used as the watched folder. Choose the real folder directly.",
            "监控文件夹不能是符号链接（替身），请直接选择真实的文件夹。",
        )
    if not cand.is_dir():
        raise VoiceAutoError(
            "watch_dir_missing",
            "That folder does not exist on this computer. Create or choose an existing folder first.",
            "这台电脑上找不到这个文件夹，请先创建或选择一个已存在的文件夹。",
        )
    forbidden = _forbidden_watch_target(store, cand)
    if forbidden is not None:
        raise VoiceAutoError(
            "watch_dir_forbidden",
            "Choose a separate folder of your own: the app folder, the studio data folder, and the disk root cannot be watched.",
            "请选择你自己的独立文件夹：不能把应用目录、工作台数据目录或磁盘根目录设为监控文件夹。",
        )
    return os.path.normpath(trimmed)


def _clean_stored_voice(store: Store, raw: Any) -> Tuple[str, str, bool]:
    """Clean stored voice settings on read without writing: ``(watch_dir, transcribe_route, folder_exists)``."""
    held = raw if isinstance(raw, dict) else {}
    route = held.get("transcribe_route")
    clean_route = route if isinstance(route, str) and route in TRANSCRIBE_ROUTES else "off"

    raw_dir = held.get("watch_dir")
    if not isinstance(raw_dir, str) or "\x00" in raw_dir or len(raw_dir) > 4096:
        return "", clean_route, False
    cleaned = raw_dir.strip()
    if not cleaned:
        return "", clean_route, False
    trimmed = _strip_trailing_sep(cleaned)
    cand = Path(trimmed)
    if not cand.is_absolute() or _has_user_symlink(cand):
        return "", clean_route, False
    if _forbidden_watch_target(store, cand) is not None:
        return "", clean_route, False
    norm = os.path.normpath(trimmed)
    exists = Path(norm).is_dir() and not _has_user_symlink(Path(norm))
    return norm, clean_route, exists


def _stored_voice_dict(store: Store) -> Any:
    auto_cfg = store.load_config().get(autopilot.KEY)
    return auto_cfg.get(SETTING_KEY) if isinstance(auto_cfg, dict) else None


def _regular_file_stat(path: Path) -> Optional[Tuple[int, int]]:
    """Return ``(size_bytes, mtime_ns)`` for an existing non-symlink regular file, else None."""
    try:
        st = os.lstat(str(path))
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return int(st.st_size), int(st.st_mtime_ns)


def _effective_status(row: Dict[str, Any], now: float) -> str:
    st = row.get("status")
    if st in ("ingested", "failed", "ready"):
        return str(st)
    if st == "waiting":
        size_bytes = int(row.get("size_bytes") or 0)
        first_seen = float(row.get("first_seen_at") or 0.0)
        if size_bytes > 0 and (now - first_seen) >= SETTLE_SEC:
            disk = _regular_file_stat(Path(str(row.get("path") or "")))
            if disk is not None and disk == (size_bytes, int(row.get("mtime_ns") or 0)):
                return "ready"
    return "waiting"


def voice_payload(store: Store, now: Optional[float] = None) -> Dict[str, Any]:
    """Cleaned watched-folder settings, file status counts, and recent watched files."""
    current_now = time.time() if now is None else float(now)
    watch_dir, route, folder_exists = _clean_stored_voice(store, _stored_voice_dict(store))
    rows = store.list_watched_files()
    counts = {"ready": 0, "ingested": 0, "waiting": 0, "failed": 0}
    recent_files: List[Dict[str, Any]] = []
    for row in rows:
        eff = _effective_status(row, current_now)
        counts[eff] = counts.get(eff, 0) + 1
        if len(recent_files) < RECENT_FILES_LIMIT:
            recent_files.append({
                "id": row["id"],
                "name": clean_file_name(Path(row["path"]).name),
                "path": row["path"],
                "kind": row["kind"],
                "status": eff,
                "size_bytes": row["size_bytes"],
                "is_sponsored": row["is_sponsored"],
                "sample_id": row["sample_id"],
                "error_text": row["error_text"],
                "first_seen_at": row["first_seen_at"],
                "settled_at": row["settled_at"],
                "updated_at": row["updated_at"],
            })
    return {
        "watch_dir": watch_dir,
        "transcribe_route": route,
        "folder_exists": folder_exists,
        "sponsored_subfolder": SPONSORED_SUBFOLDER,
        "counts": counts,
        "recent_files": recent_files,
    }


def save_voice_settings(store: Store, body: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Validate and save watched-folder settings under ``autopilot``'s ``voice`` slot; return ``voice_payload``."""
    if not isinstance(body, dict):
        raise VoiceAutoError(
            "bad_voice_settings",
            "Voice settings have to be sent as a JSON object.",
            "语音自动任务设置必须是一个 JSON 对象。",
        )
    for key in body:
        if key not in OWN_VOICE_FIELDS:
            raise VoiceAutoError(
                "bad_voice_settings",
                "Unknown voice setting: %s." % str(key)[:60],
                "未知的语音自动任务设置项：%s。" % str(key)[:60],
            )
    cur_dir, cur_route, _ = _clean_stored_voice(store, _stored_voice_dict(store))
    new_dir = _validate_watch_dir_for_save(store, body["watch_dir"]) if "watch_dir" in body else cur_dir
    if "transcribe_route" in body:
        raw_route = body["transcribe_route"]
        if not isinstance(raw_route, str) or raw_route not in TRANSCRIBE_ROUTES:
            raise VoiceAutoError(
                "bad_transcribe_route",
                'transcribe_route has to be "local", "cloud", or "off".',
                'transcribe_route 只能是 "local"、"cloud" 或 "off"。',
            )
        new_route = raw_route
    else:
        new_route = cur_route

    autopilot.save_other_setting(store, SETTING_KEY, {
        "watch_dir": new_dir,
        "transcribe_route": new_route,
    })
    return voice_payload(store, now=now)


# ---- watched-folder scanner ----------------------------------------------------
def _classify_path(
    path: Path,
    route: str,
    handlers: List[Tuple[str, FileMatcher, FileIngester]],
    store: Optional[Store] = None,
) -> Optional[str]:
    name_lower = path.name.lower()
    if path.name.startswith(".") or any(name_lower.endswith(suf) for suf in _IGNORED_SUFFIXES):
        return None
    for kind, matches, _take_in in handlers:
        try:
            try:
                matched = matches(path, store) if store is not None else matches(path)  # type: ignore[call-arg]
            except TypeError:
                matched = matches(path)
            if matched:
                return kind
        except Exception:  # noqa: BLE001 - a faulty matcher skips the file rather than crashing due()
            continue
    if route in ("local", "cloud"):
        ext = path.suffix.lower().lstrip(".")
        if ext in MEDIA_EXTENSIONS:
            return "media"
    return None


def _is_sponsored_subfolder(folder_name: str) -> bool:
    stripped = folder_name.strip()
    return stripped in SPONSORED_SUBFOLDERS or stripped.lower() in SPONSORED_SUBFOLDERS


def _scan_watch_dir(
    watch_dir: str,
    route: str,
    handlers: List[Tuple[str, FileMatcher, FileIngester]],
    store: Optional[Store] = None,
) -> List[Dict[str, Any]]:
    """Read-only scan up to depth 2 (root + immediate subfolders). Never modifies creator files."""
    if not watch_dir:
        return []
    if route == "off" and not handlers:
        return []
    root = Path(watch_dir)
    if _has_user_symlink(root) or not root.is_dir():
        return []

    found: List[Dict[str, Any]] = []

    def inspect_file(entry: os.DirEntry[str], sponsored: bool) -> None:
        if entry.name.startswith("."):
            return
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                return
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return
        if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
            return
        file_path = Path(os.path.normpath(entry.path))
        kind = _classify_path(file_path, route, handlers, store=store)
        if kind is None:
            return
        found.append({
            "path": str(file_path),
            "size_bytes": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
            "kind": kind,
            "is_sponsored": bool(sponsored and kind == "media"),
        })

    try:
        with os.scandir(str(root)) as top_iter:
            top_entries = sorted(list(top_iter), key=lambda e: e.name)
    except OSError:
        return []

    subdirs: List[os.DirEntry[str]] = []
    for entry in top_entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_symlink():
                continue
            if entry.is_file(follow_symlinks=False):
                inspect_file(entry, False)
            elif entry.is_dir(follow_symlinks=False):
                subdirs.append(entry)
        except OSError:
            continue

    for sub in subdirs:
        sponsored = _is_sponsored_subfolder(sub.name)
        try:
            with os.scandir(sub.path) as sub_iter:
                sub_entries = sorted(list(sub_iter), key=lambda e: e.name)
        except OSError:
            continue
        for child in sub_entries:
            inspect_file(child, sponsored)

    found.sort(key=lambda item: item["path"])
    return found[:MAX_TRACKED_PER_SCAN]


# ---- transcript trust check & sample edit digest -------------------------------
def _sample_content_digest(sample: Dict[str, Any]) -> str:
    payload = [
        sample.get("title") or "",
        hashlib.sha256((sample.get("text") or "").encode("utf-8")).hexdigest(),
        bool(sample.get("is_sponsored")),
        sample.get("views"),
        sample.get("likes"),
        sample.get("collects"),
        sample.get("comments"),
        sample.get("posted_on"),
        sample.get("note_url") or "",
        sample.get("metrics_on"),
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def _passes_transcript_trust(sample: Dict[str, Any]) -> bool:
    """Deterministic safety check before ``auto`` confirms a non-sponsored machine transcript."""
    raw_text = (sample.get("text") or "").strip()
    if not raw_text:
        return False
    stripped = textstats.strip_cues(raw_text).strip()
    if not stripped:
        return False
    lowered = " ".join(stripped.split()).lower()
    if lowered in _PLACEHOLDER_EXACT:
        return False
    if any(marker in lowered for marker in _PLACEHOLDER_SUBSTRINGS):
        return False

    normalized = unicodedata.normalize("NFKC", stripped)
    alnum = [ch for ch in normalized if ch.isalnum()]
    if len(alnum) < TRUST_MIN_SPOKEN_CHARS:
        return False
    if len(set(alnum)) < 6:
        return False
    cjk_count = sum(1 for ch in alnum if textstats._CJK.match(ch))
    if (cjk_count / float(len(alnum))) < TRUST_MIN_CJK_RATIO:
        return False

    timing_info = sample.get("timing")
    if isinstance(timing_info, dict) and timing_info.get("speaking_rate_cpm") is not None:
        cpm = timing_info.get("speaking_rate_cpm")
        if isinstance(cpm, bool) or not isinstance(cpm, (int, float)) or not math.isfinite(cpm):
            return False
        if not (TRUST_MIN_CPM <= float(cpm) <= TRUST_MAX_CPM):
            return False
    return True


def _local_transcriber_ready(transcriber: Any) -> Tuple[bool, str, str]:
    if transcriber is None or not callable(getattr(transcriber, "transcribe_file", None)):
        return (
            False,
            "On-device transcription is not available in this run.",
            "当前运行环境未启用本机离线转写。",
        )
    status_fn = getattr(transcriber, "status", None)
    if not callable(status_fn):
        return (
            False,
            "On-device transcription status could not be checked.",
            "无法检查本机离线转写状态。",
        )
    st = status_fn()
    if not isinstance(st, dict):
        return (
            False,
            "On-device transcription is not ready yet.",
            "本机离线转写尚未就绪。",
        )
    if st.get("supported") is False:
        return (
            False,
            str(st.get("reason_en") or "On-device transcription is not supported on this computer."),
            str(st.get("reason_zh") or "这台电脑不支持本机离线转写。"),
        )
    if not st.get("installed") or st.get("model_ready") is False:
        return (
            False,
            str(st.get("reason_en") or "On-device transcription is not set up yet. Set it up in Settings first."),
            str(st.get("reason_zh") or "本机离线转写还没有安装好，请先在「设置」里完成安装。"),
        )
    return True, "", ""


def _copy_to_temp_upload(store: Store, src: Path) -> Path:
    store.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ext = src.suffix.lower().lstrip(".") or "mp4"
    if not ext.isalnum() or len(ext) > 8:
        ext = "mp4"
    temp_path = store.uploads_dir / ("%s.%s" % (uuid.uuid4().hex[:12], ext))
    shutil.copyfile(str(src), str(temp_path))
    if os.name == "posix":
        os.chmod(str(temp_path), 0o600)
    return temp_path


def _ingest_local_media(
    store: Store,
    transcriber: Any,
    src: Path,
    *,
    is_sponsored: bool,
) -> Dict[str, Any]:
    source_name = clean_file_name(src.name) or "video.mp4"
    title = source_name.rsplit(".", 1)[0] if "." in source_name else source_name
    temp_path = _copy_to_temp_upload(store, src)
    try:
        result = transcriber.transcribe_file(str(temp_path), None)
        result = result if isinstance(result, dict) else {}
        segments = result.get("segments") if isinstance(result.get("segments"), list) else []
        dropped = result.get("unclear_dropped")
        dropped = dropped if isinstance(dropped, int) and not isinstance(dropped, bool) and dropped > 0 else 0
        text = timing.transcript_text(segments)
        if not textstats.spoken_units(textstats.strip_cues(text)):
            raise JobError(
                "no_speech",
                "No clear speech was found in this file.",
                "这个文件里没有识别出清晰的说话声。",
            )
        measured: Dict[str, Any] = dict(timing.timing_stats(segments), unclear_dropped=dropped)
        sound = soundstats.clean(result.get("sound"))
        if sound is not None:
            measured["sound"] = sound
        return store.create_sample(
            title=title,
            text=text,
            is_sponsored=is_sponsored,
            kind="video",
            timing=measured,
            source_name=source_name,
        )
    finally:
        _unlink_quietly(temp_path)


def _ingest_cloud_media(
    store: Store,
    transport: Any,
    src: Path,
    *,
    is_sponsored: bool,
) -> Dict[str, Any]:
    source_name = clean_file_name(src.name) or "video.mp4"
    title = source_name.rsplit(".", 1)[0] if "." in source_name else source_name
    temp_path = _copy_to_temp_upload(store, src)
    try:
        ingest_fn = getattr(video_ingest, "ingest_media_with_ai", None) or video_ingest.transcribe_via_ai
        out = ingest_fn(
            store,
            {"path": temp_path, "name": source_name},
            title=title,
            is_sponsored=is_sponsored,
            transport=transport,
        )
        sample_id = out.get("sample_id") if isinstance(out, dict) else None
        sample = store.get_sample(sample_id) if isinstance(sample_id, str) else None
        if sample is None:
            raise JobError("ingest_failed", "Cloud ingestion returned no sample.", "云端转写没有返回可用素材。")
        return sample
    finally:
        _unlink_quietly(temp_path)


# ---- Task 1: voice.ingest_folder -----------------------------------------------
def _ingest_due(context: autopilot.Context) -> bool:
    """Read-only check: True when there is a newly seen/changed file to record or a settled file ready to process."""
    watch_dir, route, exists = _clean_stored_voice(context.store, context.settings.get(SETTING_KEY))
    if not watch_dir or not exists:
        return False
    handlers = _snapshot_handlers(context.store)
    if route == "off" and not handlers:
        return False
    scanned = _scan_watch_dir(watch_dir, route, handlers, store=context.store)
    if not scanned:
        return False
    by_path = {row["path"]: row for row in context.store.list_watched_files()}
    now = context.now
    for item in scanned:
        row = by_path.get(item["path"])
        if row is None:
            return True
        same_stat = (row["size_bytes"], row["mtime_ns"]) == (item["size_bytes"], item["mtime_ns"])
        st = row["status"]
        if st == "waiting":
            if not same_stat or (now - row["first_seen_at"]) >= SETTLE_SEC:
                return True
        elif st == "ready":
            return True
        elif st == "failed" and not same_stat:
            return True
    return False


def _upsert_watched_waiting(
    db: sqlite3.Connection,
    existing: Optional[Dict[str, Any]],
    item: Dict[str, Any],
    now: float,
) -> str:
    if existing is None:
        wid = uuid.uuid4().hex[:12]
        db.execute(
            "INSERT INTO watched_files (id, path, size_bytes, mtime_ns, first_seen_at, settled_at,"
            " status, kind, sample_id, is_sponsored, error_text, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                wid,
                item["path"],
                item["size_bytes"],
                item["mtime_ns"],
                now,
                None,
                "waiting",
                item["kind"],
                None,
                1 if item["is_sponsored"] else 0,
                "",
                now,
            ),
        )
        return wid
    db.execute(
        "UPDATE watched_files SET size_bytes = ?, mtime_ns = ?, first_seen_at = ?, settled_at = NULL,"
        " status = 'waiting', kind = ?, is_sponsored = ?, error_text = '', updated_at = ? WHERE id = ?",
        (
            item["size_bytes"],
            item["mtime_ns"],
            now,
            item["kind"],
            1 if item["is_sponsored"] else 0,
            now,
            existing["id"],
        ),
    )
    return existing["id"]


def _prune_watched_files(db: sqlite3.Connection) -> None:
    total = int(db.execute("SELECT COUNT(*) FROM watched_files").fetchone()[0])
    if total > MAX_WATCHED_ROWS:
        db.execute(
            "DELETE FROM watched_files WHERE id IN ("
            "SELECT id FROM watched_files ORDER BY"
            " CASE WHEN status IN ('ingested', 'failed') THEN 0 ELSE 1 END ASC,"
            " updated_at ASC, rowid ASC LIMIT ?)",
            (total - MAX_WATCHED_ROWS,),
        )


def _ingest_run(context: autopilot.Context) -> Dict[str, Any]:
    store = context.store
    watch_dir, route, exists = _clean_stored_voice(store, context.settings.get(SETTING_KEY))
    if not watch_dir or not exists:
        return {
            "status": "skipped",
            "reason": "folder_missing",
            "seen": 0,
            "ingested": 0,
            "confirmed": 0,
            "failed": 0,
        }
    handlers = _snapshot_handlers(store)
    if route == "off" and not handlers:
        return {
            "status": "skipped",
            "reason": "route_off",
            "seen": 0,
            "ingested": 0,
            "confirmed": 0,
            "failed": 0,
        }

    scanned = _scan_watch_dir(watch_dir, route, handlers, store=store)
    by_path = {row["path"]: row for row in store.list_watched_files()}
    now = context.now

    newly_seen = 0
    reset_waiting = 0
    ready_candidates: List[Tuple[float, str, Dict[str, Any], Dict[str, Any]]] = []

    with _db(store) as db, db:
        for item in scanned:
            row = by_path.get(item["path"])
            if row is None:
                _upsert_watched_waiting(db, None, item, now)
                newly_seen += 1
                continue
            same_stat = (row["size_bytes"], row["mtime_ns"]) == (item["size_bytes"], item["mtime_ns"])
            st = row["status"]
            if st == "ingested":
                continue
            if not same_stat:
                _upsert_watched_waiting(db, row, item, now)
                reset_waiting += 1
                continue
            if st == "waiting":
                if (now - row["first_seen_at"]) >= SETTLE_SEC:
                    db.execute(
                        "UPDATE watched_files SET status = 'ready', settled_at = ?, kind = ?, is_sponsored = ?,"
                        " updated_at = ? WHERE id = ?",
                        (now, item["kind"], 1 if item["is_sponsored"] else 0, now, row["id"]),
                    )
                    updated_row = dict(row, status="ready", settled_at=now, kind=item["kind"],
                                       is_sponsored=item["is_sponsored"], updated_at=now)
                    ready_candidates.append((now, item["path"], updated_row, item))
            elif st == "ready":
                settled_when = row["settled_at"] if row["settled_at"] is not None else now
                ready_candidates.append((settled_when, item["path"], row, item))
        _prune_watched_files(db)

    ready_candidates.sort(key=lambda entry: (entry[0], entry[1]))
    handler_map = {kind: take_in for kind, _matches, take_in in handlers}

    ingested_count = 0
    confirmed_count = 0
    failed_count = 0
    deferred_cap = 0
    skipped_reason = ""
    skipped_en = ""
    skipped_zh = ""

    local_checked: Optional[Tuple[bool, str, str]] = None
    cloud_capable: Optional[bool] = None
    cloud_stopped_by_cap = False

    for _settled_when, path_str, row, item in ready_candidates[:MAX_FILES_PER_RUN]:
        src = Path(path_str)
        disk_stat = _regular_file_stat(src)
        if disk_stat is None or disk_stat != (item["size_bytes"], item["mtime_ns"]):
            if disk_stat is not None:
                with _db(store) as db, db:
                    _upsert_watched_waiting(
                        db,
                        row,
                        dict(item, size_bytes=disk_stat[0], mtime_ns=disk_stat[1]),
                        context.now,
                    )
            continue

        kind = item["kind"]
        if kind != "media":
            take_in = handler_map.get(kind)
            if take_in is None:
                continue
            try:
                ext_res = take_in(context, src)
            except Exception as exc:  # noqa: BLE001 - record failure on watched_files row
                with _db(store) as db, db:
                    db.execute(
                        "UPDATE watched_files SET status = 'failed', error_text = ?, updated_at = ? WHERE id = ?",
                        (str(exc)[:240], context.now, row["id"]),
                    )
                failed_count += 1
                continue
            if isinstance(ext_res, dict) and ext_res.get("status") == "skipped":
                continue
            ext_sample_id = ext_res.get("sample_id") if isinstance(ext_res, dict) and isinstance(ext_res.get("sample_id"), str) else None
            with _db(store) as db, db:
                db.execute(
                    "UPDATE watched_files SET status = 'ingested', sample_id = ?, error_text = '', updated_at = ? WHERE id = ?",
                    (ext_sample_id, context.now, row["id"]),
                )
            context.record_undo("voice.watched", {"watched_id": row["id"]})
            ingested_count += 1
            continue

        # Media file ingestion
        if route == "off":
            continue

        if route == "local":
            if local_checked is None:
                local_checked = _local_transcriber_ready(context.transcriber)
            ok_local, msg_en, msg_zh = local_checked
            if not ok_local:
                skipped_reason = "transcriber_not_ready"
                skipped_en, skipped_zh = msg_en, msg_zh
                continue
            if item["size_bytes"] > MAX_LOCAL_MEDIA_BYTES:
                with _db(store) as db, db:
                    db.execute(
                        "UPDATE watched_files SET status = 'failed', error_text = ?, updated_at = ? WHERE id = ?",
                        ("too_large", context.now, row["id"]),
                    )
                failed_count += 1
                continue
            try:
                sample = _ingest_local_media(
                    store,
                    context.transcriber,
                    src,
                    is_sponsored=item["is_sponsored"],
                )
            except (transcribe.TranscribeError, JobError, TextTooLong, OSError) as exc:
                with _db(store) as db, db:
                    db.execute(
                        "UPDATE watched_files SET status = 'failed', error_text = ?, updated_at = ? WHERE id = ?",
                        (str(exc)[:240], context.now, row["id"]),
                    )
                failed_count += 1
                continue
        else:
            # route == "cloud"
            if cloud_stopped_by_cap:
                deferred_cap += 1
                continue
            if cloud_capable is None:
                info = video_ingest.ai_video_info(store)
                cloud_capable = bool(info.get("ai_video_capable"))
                if not cloud_capable:
                    skipped_reason = "ai_video_not_configured"
                    skipped_en, skipped_zh = video_ingest.ai_video_refusal_messages(store)
            if not cloud_capable:
                continue
            provider = str(store.load_config().get("provider") or "")
            cloud_limit = (
                video_ingest.MAX_GEMINI_VIDEO_BYTES
                if provider == "gemini"
                else video_ingest.MAX_OPENAI_AUDIO_BYTES
            )
            if item["size_bytes"] > cloud_limit:
                with _db(store) as db, db:
                    db.execute(
                        "UPDATE watched_files SET status = 'failed', error_text = ?, updated_at = ? WHERE id = ?",
                        ("too_large", context.now, row["id"]),
                    )
                failed_count += 1
                continue
            if not context.spend(1):
                cloud_stopped_by_cap = True
                deferred_cap += 1
                continue
            try:
                sample = _ingest_cloud_media(
                    store,
                    context.transport,
                    src,
                    is_sponsored=item["is_sponsored"],
                )
            except (JobError, llm.LLMError, TextTooLong, OSError) as exc:
                with _db(store) as db, db:
                    db.execute(
                        "UPDATE watched_files SET status = 'failed', error_text = ?, updated_at = ? WHERE id = ?",
                        (str(exc)[:240], context.now, row["id"]),
                    )
                failed_count += 1
                continue

        with _db(store) as db, db:
            db.execute(
                "UPDATE watched_files SET status = 'ingested', sample_id = ?, is_sponsored = ?,"
                " error_text = '', updated_at = ? WHERE id = ?",
                (sample["id"], 1 if item["is_sponsored"] else 0, context.now, row["id"]),
            )
        digest = _sample_content_digest(sample)
        will_confirm = (
            context.level == autopilot.AUTO
            and not item["is_sponsored"]
            and not sample.get("is_sponsored")
            and _passes_transcript_trust(sample)
        )
        context.record_undo("voice.ingested", {
            "sample_id": sample["id"],
            "watched_id": row["id"],
            "updated_at": sample["updated_at"],
            "digest": digest,
            "auto_confirmed": will_confirm,
        })
        if will_confirm:
            confirmed = store.update_sample(sample["id"], {"needs_review": False})
            if confirmed is not None:
                context.record_undo("voice.confirmed", {
                    "sample_id": sample["id"],
                    "updated_at": confirmed["updated_at"],
                    "digest": digest,
                })
                confirmed_count += 1
        ingested_count += 1

    summary: Dict[str, Any] = {
        "route": route,
        "seen": newly_seen,
        "reset": reset_waiting,
        "ingested": ingested_count,
        "confirmed": confirmed_count,
        "failed": failed_count,
    }
    if deferred_cap:
        summary["deferred_daily_cap"] = deferred_cap
    if skipped_reason:
        summary["skipped_reason"] = skipped_reason
        summary["message"] = skipped_en[:240]
        summary["message_zh"] = skipped_zh[:240]
    return summary


def _sample_has_side_edits(store: Store, sample_id: str) -> bool:
    return bool(store.list_sample_comments(sample_id) or store.list_metric_snapshots(sample_id))


def _reset_watched_row(store: Store, watched_id: Any) -> None:
    if not isinstance(watched_id, str) or not watched_id:
        return
    now = time.time()
    with _db(store) as db, db:
        db.execute(
            "UPDATE watched_files SET status = 'waiting', settled_at = NULL, sample_id = NULL,"
            " error_text = '', first_seen_at = ?, updated_at = ? WHERE id = ?",
            (now, now, watched_id),
        )


def _undo_confirmed(store: Store, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    sample_id = payload.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id:
        return
    sample = store.get_sample(sample_id)
    if sample is None:
        return
    if (
        sample.get("needs_review") is not False
        or sample.get("updated_at") != payload.get("updated_at")
        or _sample_content_digest(sample) != payload.get("digest")
        or _sample_has_side_edits(store, sample_id)
    ):
        raise autopilot.UndoRefused(
            "This transcript was edited after Autopilot confirmed it, so it was left as you have it.",
            "这条转写素材在自动确认后被你编辑过，因此保留你现在的版本。",
        )
    store.update_sample(sample_id, {"needs_review": True})


def _undo_ingested(store: Store, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    sample_id = payload.get("sample_id")
    watched_id = payload.get("watched_id")
    sample = store.get_sample(sample_id) if isinstance(sample_id, str) and sample_id else None
    if sample is not None:
        auto_confirmed = bool(payload.get("auto_confirmed"))
        untouched = (
            sample.get("needs_review") is True
            and _sample_content_digest(sample) == payload.get("digest")
            and not _sample_has_side_edits(store, sample["id"])
            and (auto_confirmed or sample.get("updated_at") == payload.get("updated_at"))
        )
        if not untouched:
            raise autopilot.UndoRefused(
                "This library item was edited or reviewed after Autopilot added it, so it was not deleted.",
                "这条素材在自动导入后已经被你编辑或确认过，因此没有删除。",
            )
        store.delete_sample(sample["id"])
    _reset_watched_row(store, watched_id)


def _undo_watched(store: Store, payload: Any) -> None:
    if isinstance(payload, dict):
        _reset_watched_row(store, payload.get("watched_id"))


# ---- Task 2: voice.relearn -----------------------------------------------------
def _ai_configured(store: Store) -> bool:
    pub = store.public_settings()
    return bool(pub.get("configured") and not pub.get("manual"))


def _confirmed_organic_samples(store: Store) -> List[Dict[str, Any]]:
    return [
        s for s in pipeline.confirmed_samples(store.list_samples())
        if not s.get("is_sponsored") and (s.get("text") or "").strip()
    ]


def _sample_confirmed_at(sample: Dict[str, Any]) -> float:
    rev = sample.get("reviewed_at")
    if isinstance(rev, (int, float)) and not isinstance(rev, bool) and math.isfinite(rev):
        return float(rev)
    created = sample.get("created_at")
    if isinstance(created, (int, float)) and not isinstance(created, bool) and math.isfinite(created):
        return float(created)
    return 0.0


def _dna_saved_at(store: Store, dna: Dict[str, Any]) -> Optional[float]:
    summary = pipeline.profile_summary(dna)
    for key in ("accepted_at", "proposed_at", "material_as_of"):
        val = summary.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val > 0:
            return float(val)
    try:
        return float(store.dna_path.stat().st_mtime)
    except OSError:
        return None


def _dna_digest(dna: Any) -> str:
    return hashlib.sha256(json.dumps(dna, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _extract_banned_slop(dna: Any) -> List[str]:
    if not isinstance(dna, dict):
        return []
    avb = dna.get("anti_voice_blacklist")
    if isinstance(avb, dict):
        for key in ("banned_phrases_zh", "banned_slop_phrases", "banned_phrases"):
            vals = avb.get(key)
            if isinstance(vals, list):
                return [v for v in vals if isinstance(v, str) and v.strip()]
    top = dna.get("banned_slop_phrases")
    if isinstance(top, list):
        return [v for v in top if isinstance(v, str) and v.strip()]
    return []


def _relearn_new_info(context: autopilot.Context) -> Tuple[List[Dict[str, Any]], int, Optional[float]]:
    """Return ``(all_confirmed_organic, new_count, reference_timestamp_or_None)``."""
    store = context.store
    organic = _confirmed_organic_samples(store)
    last = context.last_done("voice.relearn")
    if last is not None:
        summary = last.get("summary") if isinstance(last.get("summary"), dict) else {}
        seen_ids = summary.get("seen_ids")
        last_started = last.get("started_at")
        ref_at = float(last_started) if isinstance(last_started, (int, float)) else None
        if isinstance(seen_ids, list):
            known = {sid for sid in seen_ids if isinstance(sid, str)}
            new_count = sum(1 for s in organic if s["id"] not in known)
        elif ref_at is not None:
            new_count = sum(1 for s in organic if _sample_confirmed_at(s) > ref_at)
        else:
            new_count = len(organic)
        return organic, new_count, ref_at

    dna, is_example = store.load_dna()
    if is_example:
        return organic, len(organic), None
    saved_at = _dna_saved_at(store, dna)
    if saved_at is None:
        return organic, len(organic), None
    new_count = sum(1 for s in organic if _sample_confirmed_at(s) > saved_at)
    return organic, new_count, saved_at


def _last_auto_pending_at(context: autopilot.Context) -> Optional[float]:
    last = context.last_done("voice.relearn")
    if last is None or not isinstance(last.get("summary"), dict):
        return None
    val = last["summary"].get("auto_pending_at")
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) else None


def _relearn_due(context: autopilot.Context) -> bool:
    store = context.store
    if not _ai_configured(store):
        return False
    pending = store.load_pending()
    if pending is not None and context.level == autopilot.AUTO:
        last = context.last_done("voice.relearn")
        last_sum = last.get("summary") if (last is not None and isinstance(last.get("summary"), dict)) else {}
        if last_sum.get("blocked_pending_at") is not None and last_sum.get("blocked_pending_at") == pending.get("created_at"):
            return False
    organic, new_count, ref_at = _relearn_new_info(context)
    if not organic:
        return False
    if new_count >= RELEARN_MIN_NEW_SAMPLES:
        return True
    if ref_at is not None and (context.now - ref_at) >= RELEARN_STALE_SEC and new_count >= 1:
        return True
    return False


def _candidate_approved_dna(store: Store, pending: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the exact DNA ``pipeline.approve_voice`` would produce from ``pending`` without mutating disk."""
    samples = pipeline.confirmed_samples(store.list_samples())
    proposal = pipeline._stored_proposal(pending["dna"])
    report = evidence.verify_profile(proposal, samples)
    dna = evidence.apply_decisions(proposal, report, [], drop_not_found=True, keep_paths=[])
    pipeline.strip_refused(dna, store.load_creator())
    typed = pipeline._typed_rate(store)
    if typed is not None:
        if not isinstance(dna.get("cadence_and_syntax"), dict):
            dna["cadence_and_syntax"] = {}
        dna["cadence_and_syntax"]["target_speaking_rate_cpm"] = typed
    return dna


def _check_auto_voice_bounds(
    store: Store,
    old_dna: Dict[str, Any],
    had_custom_dna: bool,
    candidate: Dict[str, Any],
) -> List[str]:
    """Return list of safety bound codes that failed (empty list == safe to auto-approve)."""
    failed: List[str] = []
    who = store.load_creator()
    never = creator.never_phrases(who)

    # 1. Every creator.never_phrases entry remains enforced (strip_refused strips ban clashes; no positive clash).
    pipeline.strip_refused(candidate, who)
    if never:
        attested = guardrails._attested_strings(candidate)
        clash = any(
            creator.never_hits({"script_lines": [{"line_no": 1, "spoken_script_zh": text}]}, who)
            for text in attested if isinstance(text, str) and text.strip()
        )
        merged = creator.apply_never(candidate, who)
        all_enforced = all(
            any(
                f.severity == guardrails.ERROR
                for f in guardrails.lint_script(
                    {"script_lines": [{"line_no": 1, "spoken_script_zh": phrase}]},
                    merged,
                    lexicon={},
                )
            )
            for phrase in never
        )
        if clash or not all_enforced:
            failed.append("never_phrases")

    if had_custom_dna and isinstance(old_dna, dict):
        # 2. Speaking rate shifts by at most 35% if existing custom DNA had one.
        old_cpm = guardrails._number(guardrails._get(old_dna, "cadence_and_syntax.target_speaking_rate_cpm"))
        new_cpm = guardrails._number(guardrails._get(candidate, "cadence_and_syntax.target_speaking_rate_cpm"))
        if old_cpm is not None and old_cpm > 0:
            if new_cpm is None or abs(float(new_cpm) - float(old_cpm)) / float(old_cpm) > MAX_RATE_SHIFT:
                failed.append("speaking_rate_shift")

        # 3. Existing custom banned_slop_phrases loses at most half its entries in one automatic step.
        refused_keys = {guardrails.ban_key(p) for p in never} - {""}
        old_bans = ({guardrails.ban_key(p) for p in _extract_banned_slop(old_dna)} - {""}) - refused_keys
        new_bans = ({guardrails.ban_key(p) for p in _extract_banned_slop(candidate)} - {""}) - refused_keys
        if old_bans:
            lost = len(old_bans - new_bans)
            if lost * 2 > len(old_bans):
                failed.append("banned_slop_drop")

    return failed


def _relearn_run(context: autopilot.Context) -> Dict[str, Any]:
    store = context.store
    if not _ai_configured(store):
        return {"status": "skipped", "reason": "ai_not_configured"}

    organic = _confirmed_organic_samples(store)
    if not organic:
        return {"status": "skipped", "reason": "no_samples"}

    existing_pending = store.load_pending()
    auto_pending_at = _last_auto_pending_at(context)
    is_manual_pending = (
        existing_pending is not None
        and (auto_pending_at is None or existing_pending.get("created_at") != auto_pending_at)
    )
    if is_manual_pending and context.level == autopilot.AUTO:
        return {
            "status": "held_back",
            "reason": "manual_pending_exists",
            "blocked_pending_at": existing_pending.get("created_at"),
        }
    if is_manual_pending and context.level == autopilot.SUGGEST:
        return {
            "status": "held_back",
            "reason": "manual_pending_exists",
            "blocked_pending_at": existing_pending.get("created_at"),
        }

    if not context.spend(1):
        return {"status": "skipped", "reason": "daily_cap"}

    old_dna, is_example = store.load_dna()
    had_custom_dna = not is_example
    pre_dna_digest = "example" if is_example else _dna_digest(old_dna)
    seen_ids = [s["id"] for s in organic[:150]]

    pending = pipeline.learn_voice(store, transport=context.transport)
    created_at = pending["created_at"]

    if context.level == autopilot.SUGGEST:
        context.record_undo("voice.proposed", {
            "created_at": created_at,
            "pre_dna_digest": pre_dna_digest,
        })
        return {
            "status": "proposed",
            "level": autopilot.SUGGEST,
            "created_at": created_at,
            "auto_pending_at": created_at,
            "samples": len(organic),
            "seen_ids": seen_ids,
        }

    # context.level == autopilot.AUTO
    candidate = _candidate_approved_dna(store, pending)
    failed_bounds = _check_auto_voice_bounds(store, old_dna, had_custom_dna, candidate)
    if failed_bounds:
        context.record_undo("voice.proposed", {
            "created_at": created_at,
            "pre_dna_digest": pre_dna_digest,
        })
        return {
            "status": "held_back",
            "reason": "safety_bounds",
            "bound": failed_bounds[0],
            "held_back_by": failed_bounds,
            "created_at": created_at,
            "auto_pending_at": created_at,
            "samples": len(organic),
            "seen_ids": seen_ids,
        }

    approved = pipeline.approve_voice(store, created_at=created_at)
    approved_digest = _dna_digest(approved)
    history = store.list_dna_history()
    history_id = history[0]["id"] if (had_custom_dna and history) else None

    undo_payload: Dict[str, Any] = {
        "had_custom_dna": had_custom_dna,
        "history_id": history_id,
        "approved_digest": approved_digest,
    }
    if had_custom_dna:
        encoded_prev = json.dumps(old_dna, ensure_ascii=False)
        if len(encoded_prev) <= 16000:
            undo_payload["previous_dna"] = copy.deepcopy(old_dna)
    context.record_undo("voice.relearned", undo_payload)

    return {
        "status": "approved",
        "level": autopilot.AUTO,
        "history_id": history_id,
        "samples": len(organic),
        "seen_ids": seen_ids,
    }


def _undo_proposed(store: Store, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    cur_dna, is_example = store.load_dna()
    cur_digest = "example" if is_example else _dna_digest(cur_dna)
    pending = store.load_pending()
    if pending is None:
        if payload.get("pre_dna_digest") and cur_digest != payload.get("pre_dna_digest"):
            raise autopilot.UndoRefused(
                "You already accepted or changed the voice profile since this proposal was made.",
                "这份声音档案提议生成之后，你已经采用或修改了声音档案，因此无法撤销。",
            )
        return
    if pending.get("created_at") != payload.get("created_at"):
        raise autopilot.UndoRefused(
            "A newer voice profile proposal is waiting now, so it was left untouched.",
            "现在有一份更新的声音档案提议正在等待确认，因此保留现状。",
        )
    store.clear_pending()


def _undo_relearned(store: Store, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    cur_dna, is_example = store.load_dna()
    if is_example or _dna_digest(cur_dna) != payload.get("approved_digest"):
        raise autopilot.UndoRefused(
            "Your voice profile was changed after Autopilot updated it, so your newer profile was kept.",
            "自动更新声音档案之后你又修改过它，因此保留你现在的版本。",
        )
    if not payload.get("had_custom_dna"):
        try:
            store.dna_path.unlink()
        except FileNotFoundError:
            pass
        return
    history_id = payload.get("history_id")
    if isinstance(history_id, str) and history_id:
        restored = store.restore_dna(history_id)
        if restored is not None:
            return
    previous_dna = payload.get("previous_dna")
    if isinstance(previous_dna, dict):
        store.save_dna(previous_dna)
        return
    raise autopilot.UndoRefused(
        "The earlier voice profile snapshot is no longer available to restore.",
        "找不到可恢复的上一版声音档案记录。",
    )


# ---- Task 3: lessons.accept_recurring ------------------------------------------
def _covered_lesson_keys(lessons: List[Dict[str, Any]]) -> Tuple[Set[Tuple[str, str]], Set[str]]:
    pairs: Set[Tuple[str, str]] = set()
    removed_keys: Set[str] = set()
    for lesson in lessons:
        ai = (lesson.get("ai") or "").strip()
        final = (lesson.get("final") or "").strip()
        ai_k, final_k = learning.spoken_key(ai), learning.spoken_key(final)
        pairs.add((ai_k, final_k))
        if ai and not final and ai_k:
            removed_keys.add(ai_k)
        if ai:
            for change in learning.line_changes([{"spoken_script_zh": ai}], [{"spoken_script_zh": final}] if final else []):
                for phrase in change.get("removed") or ():
                    pk = learning.spoken_key(phrase)
                    if pk:
                        removed_keys.add(pk)
    return pairs, removed_keys


def _undecided_recurring_lessons(store: Store) -> List[Dict[str, Any]]:
    drafts = store.list_organic_ai_drafts()
    if len(drafts) < 2:
        return []
    proposed = pipeline._proposed_words(store)
    existing_lessons = store.list_lessons()
    existing_pairs, covered_removed = _covered_lesson_keys(existing_lessons)
    who = store.load_creator()

    changes_by_script: Dict[str, List[Dict[str, Any]]] = {}
    by_pair: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {}

    for draft in drafts:
        sid = draft["id"]
        valid_changes: List[Dict[str, Any]] = []
        seen_in_script: Set[Tuple[str, str]] = set()
        for change in learning.line_changes(draft["ai_lines"], draft["lines"]):
            if pipeline._is_proposed(change.get("final"), proposed):
                continue
            valid_changes.append(change)
            ai = (change.get("ai") or "").strip()
            final = (change.get("final") or "").strip()
            if len(ai) > pipeline.MAX_LESSON_CHARS or len(final) > pipeline.MAX_LESSON_CHARS:
                continue
            if not learning.prompt_rules([{"ai": ai, "final": final}]):
                continue
            pair_key = (learning.spoken_key(ai), learning.spoken_key(final))
            if pair_key not in seen_in_script:
                seen_in_script.add(pair_key)
                by_pair.setdefault(pair_key, []).append((sid, ai, final))
        if valid_changes:
            changes_by_script[sid] = valid_changes

    planned: List[Dict[str, Any]] = []
    planned_pairs: Set[Tuple[str, str]] = set(existing_pairs)
    planned_removed: Set[str] = set(covered_removed)

    # 1. Identical line rewrites/deletions/additions across >= 2 non-sponsored scripts
    for pair_key, occurrences in by_pair.items():
        if len(occurrences) >= 2 and pair_key not in planned_pairs:
            sid, ai, final = occurrences[-1]
            planned.append({
                "script_id": sid,
                "ai": ai,
                "final": final,
                "note": "Recurring edit across %d scripts" % len(occurrences),
            })
            planned_pairs.add(pair_key)
            for ch in learning.line_changes([{"spoken_script_zh": ai}], [{"spoken_script_zh": final}] if final else []):
                for p in ch.get("removed") or ():
                    pk = learning.spoken_key(p)
                    if pk:
                        planned_removed.add(pk)

    # 2. Recurring phrase removals across >= 2 non-sponsored scripts (learning.recurring_removals)
    for removal in learning.recurring_removals(changes_by_script):
        phrase = (removal.get("phrase") or "").strip()
        pkey = learning.spoken_key(phrase)
        if not pkey or pkey in planned_removed or pipeline._already_refused(phrase, who):
            continue
        script_ids = list(removal.get("script_ids") or [])
        chosen: Optional[Tuple[str, str, str]] = None
        for sid in reversed(script_ids):
            for ch in changes_by_script.get(sid, ()):
                removed_keys_here = [learning.spoken_key(p) for p in (ch.get("removed") or ())]
                if not any(pkey in rk for rk in removed_keys_here if rk):
                    continue
                ai = (ch.get("ai") or "").strip()
                final = (ch.get("final") or "").strip()
                if (
                    len(ai) <= pipeline.MAX_LESSON_CHARS
                    and len(final) <= pipeline.MAX_LESSON_CHARS
                    and learning.prompt_rules([{"ai": ai, "final": final}])
                ):
                    chosen = (sid, ai, final)
                    break
            if chosen is not None:
                break
        if chosen is None and script_ids and len(phrase) <= pipeline.MAX_LESSON_CHARS:
            chosen = (str(script_ids[-1]), phrase, "")
        if chosen is not None:
            sid, ai, final = chosen
            pair_key = (learning.spoken_key(ai), learning.spoken_key(final))
            if pair_key not in planned_pairs:
                planned.append({
                    "script_id": sid,
                    "ai": ai,
                    "final": final,
                    "note": "Removed «%s» across %d scripts" % (phrase[:40], int(removal.get("scripts") or 2)),
                })
                planned_pairs.add(pair_key)
            planned_removed.add(pkey)

    return planned


def _lessons_due(context: autopilot.Context) -> bool:
    if context.level != autopilot.AUTO:
        return False
    return bool(_undecided_recurring_lessons(context.store))


def _lessons_run(context: autopilot.Context) -> Dict[str, Any]:
    if context.level != autopilot.AUTO:
        return {"accepted": 0, "level": context.level}
    store = context.store
    candidates = _undecided_recurring_lessons(store)
    accepted_ids: List[str] = []
    for item in candidates:
        lesson, created = store.add_lesson(
            script_id=item["script_id"],
            ai=item["ai"],
            final=item["final"],
            note=item["note"][:pipeline.MAX_LESSON_NOTE_CHARS],
        )
        if created:
            context.record_undo("lessons.accepted", {
                "id": lesson["id"],
                "ai": lesson["ai"],
                "final": lesson["final"],
                "note": lesson["note"],
                "created_at": lesson["created_at"],
            })
            accepted_ids.append(lesson["id"])
    return {"accepted": len(accepted_ids), "lesson_ids": accepted_ids}


def _undo_lessons_accepted(store: Store, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    lesson_id = payload.get("id")
    if not isinstance(lesson_id, str) or not lesson_id:
        return
    row = store.get_lesson(lesson_id)
    if row is None:
        return
    if (
        row.get("ai") != payload.get("ai")
        or row.get("final") != payload.get("final")
        or row.get("note") != payload.get("note")
    ):
        raise autopilot.UndoRefused(
            "This lesson was edited after Autopilot accepted it, so it was kept.",
            "这条写作经验在自动采纳后被修改过，因此予以保留。",
        )
    store.delete_lesson(lesson_id)


# ---- Registration --------------------------------------------------------------
def register_into(registry: autopilot.Registry) -> None:
    """Register all ``autovoice`` tasks and undo handlers onto ``registry``."""
    registry.add_undo("voice.ingested", _undo_ingested)
    registry.add_undo("voice.confirmed", _undo_confirmed)
    registry.add_undo("voice.watched", _undo_watched)
    registry.add_undo("voice.proposed", _undo_proposed)
    registry.add_undo("voice.relearned", _undo_relearned)
    registry.add_undo("lessons.accepted", _undo_lessons_accepted)
    registry.add(autopilot.Task(
        "voice.ingest_folder",
        _ingest_run,
        every_sec=INGEST_EVERY_SEC,
        calls_model=False,
        due=_ingest_due,
    ))
    registry.add(autopilot.Task(
        "voice.relearn",
        _relearn_run,
        every_sec=RELEARN_EVERY_SEC,
        calls_model=True,
        due=_relearn_due,
    ))
    registry.add(autopilot.Task(
        "lessons.accept_recurring",
        _lessons_run,
        every_sec=LESSONS_EVERY_SEC,
        calls_model=False,
        due=_lessons_due,
    ))


register_into(autopilot.REGISTRY)
