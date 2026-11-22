"""Local persistence: scripts and imported material in SQLite, settings and Voice DNA as JSON files.

Everything lives under ``data/`` (override with STUDIO_DATA_DIR) and never
leaves the machine. The folder is closed to the other accounts on the computer
(0700) and so is every file in it (0600), also when an earlier version created
them open. API keys are stored in ``data/config.json`` and are never sent back
to the browser.

A learned voice profile waits in ``voice_pending.json`` until the creator
accepts it; only ``save_dna`` and ``restore_dna`` change the active profile.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import keystore

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA_PATH = ROOT / "schemas" / "voice_dna.example.json"
CLOUD_PROVIDERS = ("gemini", "openai", "anthropic")
PROVIDERS = CLOUD_PROVIDERS + ("local",)
MANUAL_PROVIDER = "manual"  # copy-and-paste with a chat subscription: no key, no model
METRIC_FIELDS = ("views", "likes", "collects", "comments")
EXTRA_METRIC_FIELDS = ("shares", "new_followers", "click_rate_pct", "avg_watch_sec", "completion_rate_pct")
SNAPSHOT_FIELDS = METRIC_FIELDS + EXTRA_METRIC_FIELDS
MAX_KEYWORD_CHARS = 40
MAX_KEYWORDS_PER_SCRIPT = 8
MAX_TRENDS = 2000           # tracked trend keywords kept in SQLite; oldest unverified entries go first when full
SAMPLE_KINDS = ("script", "subtitle", "video")   # typed or pasted text; a subtitle file; a video transcribed here
BEAT_TYPES = (
    "HOOK", "VLOG_STORY", "COUPLE_BANTER", "TRANSITION_BRIDGE",
    "PRODUCT_EXPERIENCE", "REALISTIC_CAVEAT", "CLOSING_CTA",
)
BEAT_AUDIO_MODES = ("VOICEOVER", "TALKING_HEAD", "LIVE_CLIP")
MAX_BEATS_PER_SAMPLE = 80
MAX_BEAT_SPOKEN_CHARS = 600
MAX_BEAT_DELIVERY_CHARS = 160
MAX_BEAT_MEANING_CHARS = 400
MAX_BEAT_VISUAL_CHARS = 400
MAX_SOURCE_NAME_CHARS = 200
MAX_BRIEFS = 200            # records of what a prompt contained; the oldest are dropped
MAX_AI_SUGGESTIONS = 300    # texts an AI suggested for the lines of one script that are remembered; the oldest are dropped
MAX_SNAPSHOTS_PER_SAMPLE = 365      # days on which the numbers of one library item were read that are kept; the oldest are dropped
# ``mode`` of a script. SPONSORED is set once, when a script is drafted from a brand's brief (studio/sponsored.py),
# and stays when that brief is deleted: the script still holds wording a brand asked for.
MODE_ORGANIC, MODE_SPONSORED = "NORMAL_ORGANIC", "SPONSORED"
# Brand briefs kept at once (``sponsor_briefs``). A brief is somebody else's text with the creator's decisions about
# it, so none is ever dropped to make room: a new one is refused until the creator deletes one.
MAX_SPONSOR_BRIEFS = 30
# The text of one library item, in characters. No real script or transcript comes near it: at a fast 300
# characters a minute this is more than 27 hours of speech in Chinese, and about 9 hours in English. It is
# there so that one file somebody handed the creator cannot make every page slow or fill the database.
MAX_SAMPLE_CHARS = 500000
# Bounds on script fields stored in SQLite, preventing multi-megabyte script rows from slowing page loads.
MAX_SCRIPT_TITLE_CHARS = 500
MAX_NOTES_CHARS = 50000
MAX_SCRIPT_LINES = 500
MAX_LINE_CHARS = 5000
MAX_POST_CHARS = 30000
# Where a library item came from. "" is everything the creator brought in themselves. ORIGIN_SCRIPT is a script
# written in this app that was marked as posted (studio/posted.py); it stays on the item when that script is deleted.
ORIGIN_SCRIPT = "script"
# What a posted item carries beside its numbers, as columns ``update_sample`` can set: the day it was posted, the
# link to the note, and the day the numbers were read. Days are text, ``YYYY-MM-DD``.
POSTED_COLUMNS = ("posted_on", "note_url", "metrics_on")
# What the page asks the core to keep for it from one run of the app to the next: the language, the look, and how
# the teleprompter was last set. A page can keep such things for itself, but only for one address, and inside
# the desktop app the page's address has a different port at every start. Kept here, a choice is still there
# the next time the app is opened. PAGE_WORDS: each name with the words it may be. PAGE_NUMBERS: names that hold
# a whole number of at most four digits (the page checks the range each one may have; see tpRecall in prompter.js).
PAGE_WORDS = {
    "lang": ("zh", "en"),
    "theme": ("auto", "light", "dark"),
    "accent": ("blue", "purple", "pink", "orange", "green", "graphite"),
    "rf-open": ("0", "1"),      # the references beside the script editor (studio/web/refs.js): closed or open when last chosen
}
PAGE_NUMBERS = ("tp-pace", "tp-size", "tp-mirror")

_CONFIG_LOCK = threading.RLock()    # one read-change-save of the settings at a time (Store.update_config)


def kept_value(name: Any, value: Any) -> Optional[str]:
    """``value`` as the text kept for the page under ``name``; None when the page may not keep that name or that value.

    Both are text, as a page keeps them (``PAGE_WORDS``, ``PAGE_NUMBERS``). A number is returned in its plain form.
    """
    if not isinstance(name, str) or not isinstance(value, str):
        return None
    if name in PAGE_WORDS:
        return value if value in PAGE_WORDS[name] else None
    if name in PAGE_NUMBERS and re.fullmatch(r"-?[0-9]{1,4}", value, re.ASCII):
        return str(int(value))
    return None

_DNA_LOCK = threading.RLock()       # one replacement of the active voice profile at a time
# One replacement of the saved topic ideas at a time: a save (Store.save_recommendations) and the step in which
# studio/autosuggest.py looks at what is saved and then puts other ideas in its place or the earlier ones back.
_IDEAS_LOCK = threading.RLock()
_DAY_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)      # a day as it is stored: YYYY-MM-DD
# Copies of earlier voice profiles: voice_dna-<date>-<time>[-<n>].json, or .unreadable for one that could not be read.
_HISTORY_FILE = re.compile(r"voice_dna-([0-9]{8}-[0-9]{6})(?:-([0-9]{1,6}))?\.(json|unreadable)")
HISTORY_ID = r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,6})?"     # the id of an earlier profile: the part of the name between "voice_dna-" and ".json"


MAX_META_FIELD_CHARS = 64


def _post_chars(value: Any) -> int:
    """Count user-visible characters across all strings inside a post-copy dictionary."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_post_chars(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_post_chars(v) for v in value)
    return 0


class TextTooLong(ValueError):
    """The text of a library item or script field exceeds its configured maximum. Raised before anything is stored.

    ``code`` is what a page or an MCP tool can map; ``message`` (English) and ``message_zh`` are written to be shown.
    """

    code = "text_too_long"

    def __init__(self, length: int, limit: Optional[int] = None, *,
                 field: str = "sample", line_no: Optional[int] = None) -> None:
        self.length = length
        self.limit = MAX_SAMPLE_CHARS if limit is None else limit
        self.field = field
        self.line_no = line_no
        if field == "script_title":
            self.message = "The title is too long: it has {:,} characters and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "标题太长了：现在有 {:,} 个字符，上限是 {:,} 个。".format(self.length, self.limit)
        elif field == "notes":
            self.message = "The notes are too long: they have {:,} characters and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "想法备注太长了：现在有 {:,} 个字符，上限是 {:,} 个。".format(self.length, self.limit)
        elif field == "script_lines":
            self.message = "This script has too many lines: it has {:,} lines and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "这篇脚本行数太多了：现在有 {:,} 行，上限是 {:,} 行。".format(self.length, self.limit)
        elif field == "line":
            if line_no is not None:
                self.message = ("Line {line_no} is too long: one of its fields has {:,} characters "
                                "and the limit is {:,}.").format(self.length, self.limit, line_no=line_no)
                self.message_zh = "第 {line_no} 行太长了：其中一项有 {:,} 个字符，上限是 {:,} 个。".format(
                    self.length, self.limit, line_no=line_no)
            else:
                self.message = ("A script line is too long: one of its fields has {:,} characters "
                                "and the limit is {:,}.").format(self.length, self.limit)
                self.message_zh = "脚本中有一行太长了：其中一项有 {:,} 个字符，上限是 {:,} 个。".format(
                    self.length, self.limit)
        elif field == "post":
            self.message = "The post copy is too long: it has {:,} characters and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "发布文案太长了：现在有 {:,} 个字符，上限是 {:,} 个。".format(self.length, self.limit)
        elif field == "video_format":
            self.message = "The video format name is too long: it has {:,} characters and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "视频形式名称太长了：现在有 {:,} 个字符，上限是 {:,} 个。".format(self.length, self.limit)
        elif field == "status":
            self.message = "The script status is too long: it has {:,} characters and the limit is {:,}.".format(
                self.length, self.limit)
            self.message_zh = "脚本状态名称太长了：现在有 {:,} 个字符，上限是 {:,} 个。".format(self.length, self.limit)
        else:
            self.message = ("This text is too long for the library: it has {:,} characters and the limit is {:,}. "
                            "Split it into parts and add them one at a time.".format(self.length, self.limit))
            self.message_zh = ("这段文字太长，素材库放不下：它有 {:,} 个字符，上限是 {:,} 个。"
                               "请分成几段，一段一段地添加。".format(self.length, self.limit))
        super().__init__(self.message)


class StaleScript(Exception):
    """A save was based on an older version of a script than the one stored (``Store.update_script``).

    ``updated_at`` is the stored version's. Nothing was changed.
    """

    code = "stale_script"

    def __init__(self, updated_at: float) -> None:
        super().__init__("The script was changed after the version this save was based on")
        self.updated_at = updated_at


LINE_FIELDS = (
    "beat_type", "time_range_sec", "audio_mode", "spoken_script_zh", "delivery_cue",
    "english_partner_translation", "visual_broll_en_zh", "onscreen_flower_text_zh",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',
    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER',
    target_sec INTEGER NOT NULL DEFAULT 60,
    notes TEXT NOT NULL DEFAULT '',
    lines_json TEXT NOT NULL DEFAULT '[]',
    line_count INTEGER,
    preview TEXT,
    post_json TEXT NOT NULL DEFAULT '{}',
    ai_lines_json TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    basis_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    kind TEXT NOT NULL DEFAULT 'script',
    timing_json TEXT,
    source_name TEXT NOT NULL DEFAULT '',
    needs_review INTEGER NOT NULL DEFAULT 0,
    reviewed_at REAL,
    origin TEXT NOT NULL DEFAULT '',
    script_id TEXT,
    posted_on TEXT,
    note_url TEXT NOT NULL DEFAULT '',
    metrics_on TEXT,
    ai_json TEXT,
    snapshot TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trends (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    keyword TEXT NOT NULL,
    heat INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT NOT NULL DEFAULT '',
    note_zh TEXT NOT NULL DEFAULT '',
    note_en TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL DEFAULT '',
    ai TEXT NOT NULL,
    final TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE (ai, final)
);
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_suggestions (
    script_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_suggestions_by_script ON ai_suggestions (script_id);
CREATE TABLE IF NOT EXISTS script_pins (
    script_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (script_id, sample_id)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    read_on TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    shares INTEGER,
    new_followers INTEGER,
    click_rate_pct REAL,
    avg_watch_sec REAL,
    completion_rate_pct REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS metric_snapshots_by_sample ON metric_snapshots (sample_id);
CREATE TABLE IF NOT EXISTS sample_comments (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    text TEXT NOT NULL,
    likes INTEGER,
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    is_question INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_comments_by_sample ON sample_comments (sample_id);
CREATE TABLE IF NOT EXISTS sample_sentiments (
    sample_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'local',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sample_beats (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    beat_index INTEGER NOT NULL DEFAULT 1,
    beat_type TEXT NOT NULL,
    audio_mode TEXT NOT NULL DEFAULT 'VOICEOVER',
    delivery TEXT NOT NULL DEFAULT '',
    start_sec REAL,
    end_sec REAL,
    spoken_text TEXT NOT NULL,
    english_meaning TEXT NOT NULL DEFAULT '',
    visual_summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_beats_by_sample ON sample_beats (sample_id);
CREATE TABLE IF NOT EXISTS video_plans (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER',
    target_sec INTEGER NOT NULL DEFAULT 60,
    scheduled_for TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PLANNED',
    priority INTEGER NOT NULL DEFAULT 2,
    source_kind TEXT NOT NULL DEFAULT 'manual',
    notes TEXT NOT NULL DEFAULT '',
    rationale_zh TEXT NOT NULL DEFAULT '',
    rationale_en TEXT NOT NULL DEFAULT '',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    questions_json TEXT NOT NULL DEFAULT '[]',
    script_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    basis TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sponsor_briefs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL,
    reading_json TEXT,
    choice_json TEXT,
    ticks_json TEXT NOT NULL DEFAULT '{}',
    script_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS autopilot_runs (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    forced INTEGER NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT 'suggest',
    started_at REAL NOT NULL,
    finished_at REAL,
    model_calls INTEGER NOT NULL DEFAULT 0,
    calls_refused INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    detail TEXT NOT NULL DEFAULT '',
    undo_json TEXT NOT NULL DEFAULT '[]',
    undone_at REAL
);
CREATE TABLE IF NOT EXISTS trend_observations (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    keyword TEXT NOT NULL,
    source TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    seen_on TEXT NOT NULL,
    seen_at REAL NOT NULL,
    heat INTEGER,
    rank INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trend_observations_by_key ON trend_observations (key);
CREATE TABLE IF NOT EXISTS watched_files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    first_seen_at REAL NOT NULL,
    settled_at REAL,
    status TEXT NOT NULL DEFAULT 'waiting',
    kind TEXT NOT NULL DEFAULT 'media',
    sample_id TEXT,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    error_text TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_suggestions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    keyword_key TEXT NOT NULL,
    matched TEXT NOT NULL DEFAULT '',
    matched_in TEXT NOT NULL DEFAULT '',
    relevance REAL,
    score INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    rising INTEGER,
    sightings INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    run_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    decided_at REAL,
    UNIQUE (plan_id, keyword_key)
);
CREATE TABLE IF NOT EXISTS idea_sets (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',
    was TEXT NOT NULL DEFAULT '',
    replaced_by TEXT NOT NULL DEFAULT '',
    placed_by TEXT NOT NULL DEFAULT '',
    ideas_json TEXT NOT NULL,
    ideas_sha TEXT NOT NULL DEFAULT '',
    had_ideas INTEGER NOT NULL DEFAULT 0,
    earlier TEXT,
    trends_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    decided_at REAL
);
"""

# Columns added after a table first shipped: (table, column, definition). A database created by an
# earlier version gets them in place when it is opened; nothing already stored is touched.
_ADDED_COLUMNS = (
    ("scripts", "keywords_json", "TEXT NOT NULL DEFAULT '[]'"),    # before trend keywords existed
    ("scripts", "basis_json", "TEXT"),                             # before drafts recorded what they were based on
    ("scripts", "line_count", "INTEGER"),                          # fast summary columns for list_scripts()
    ("scripts", "preview", "TEXT"),
    ("samples", "kind", "TEXT NOT NULL DEFAULT 'script'"),         # before subtitle files and videos could be added
    ("samples", "timing_json", "TEXT"),
    ("samples", "source_name", "TEXT NOT NULL DEFAULT ''"),
    ("samples", "needs_review", "INTEGER NOT NULL DEFAULT 0"),     # before machine transcripts had to be confirmed
    ("samples", "reviewed_at", "REAL"),
    ("samples", "origin", "TEXT NOT NULL DEFAULT ''"),             # before a posted script could join the library
    ("samples", "script_id", "TEXT"),
    ("samples", "posted_on", "TEXT"),
    ("samples", "note_url", "TEXT NOT NULL DEFAULT ''"),
    ("samples", "metrics_on", "TEXT"),
    ("samples", "ai_json", "TEXT"),
    ("samples", "snapshot", "TEXT NOT NULL DEFAULT ''"),
    ("video_plans", "basis", "TEXT NOT NULL DEFAULT ''"),
)
# What has to happen once, together with adding a column. A video stored before ``needs_review`` existed
# was never confirmed by the creator, so it starts out waiting like any video added from now on.
_AFTER_ADDING = {
    ("samples", "needs_review"): ("UPDATE samples SET needs_review = 1 WHERE kind = 'video'",),
}
# One script has at most one item in the library, however often and from however many windows it is marked as
# posted. Made after the columns above, because a database from an earlier version gets ``script_id`` only then.
_ONE_ITEM_A_SCRIPT = "CREATE UNIQUE INDEX IF NOT EXISTS samples_by_script ON samples (script_id) WHERE script_id IS NOT NULL"
_KEEP: Any = object()       # an argument left out: what is stored stays as it is


def normalize_lines(lines: Any, *, max_lines: int = MAX_SCRIPT_LINES,
                    max_line_chars: int = MAX_LINE_CHARS) -> List[Dict[str, Any]]:
    """Coerce arbitrary input into the Stage 4 line shape, renumbered from 1."""
    if not isinstance(lines, list):
        return []
    if len(lines) > max(max_lines * 4, 10000):
        raise TextTooLong(len(lines), max_lines, field="script_lines")
    valid_dicts = [raw for raw in lines if isinstance(raw, dict)]
    if len(valid_dicts) > max_lines:
        raise TextTooLong(len(valid_dicts), max_lines, field="script_lines")
    out: List[Dict[str, Any]] = []
    for raw in valid_dicts:
        line_no = len(out) + 1
        line: Dict[str, Any] = {"line_no": line_no}
        for field in LINE_FIELDS:
            value = raw.get(field, "")
            text = value if isinstance(value, str) else ("" if value is None else str(value))
            if len(text) > max_line_chars:
                raise TextTooLong(len(text), max_line_chars, field="line", line_no=line_no)
            line[field] = text
        line["audio_mode"] = line["audio_mode"] or "VOICEOVER"
        out.append(line)
    return out


def _script_summary(lines: List[Dict[str, Any]]) -> Tuple[int, str]:
    preview = next((l.get("spoken_script_zh", "") for l in lines if l.get("spoken_script_zh")), "")
    return len(lines), preview[:60]


def _today(now: Optional[float] = None) -> str:
    """Today by this computer's clock, as a day is stored (``YYYY-MM-DD``)."""
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _spoken_words_key(text: Any) -> str:
    """Spoken words of ``text`` without bracketed cues, spacing or punctuation, for matching beats to a sample's text."""
    without_cues = re.sub(r"\[[^\]]*\]", "", str(text or ""))
    normalized = unicodedata.normalize("NFKC", without_cues).lower()
    return "".join(ch for ch in normalized if ch.isalnum())


def _beat_sec(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    return round(fval, 2) if (math.isfinite(fval) and 0.0 <= fval <= 86400.0) else None


def _metric(value: Any) -> Optional[int]:
    """A non-negative count, or None for blank / invalid input (a number that is not finite or too large to store included)."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number < 2 ** 63 else None


def _rate_pct(value: Any) -> Optional[float]:
    """A percentage from 0 to 100, or None for blank / invalid input."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(number, 2) if math.isfinite(number) and 0.0 <= number <= 100.0 else None


def _seconds_val(value: Any) -> Optional[float]:
    """A non-negative duration in seconds (< 86,400), or None for blank / invalid input."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(number, 1) if math.isfinite(number) and 0.0 <= number < 86400.0 else None


def _snapshot_value(field: str, value: Any) -> Any:
    if field in ("click_rate_pct", "completion_rate_pct"):
        return _rate_pct(value)
    if field == "avg_watch_sec":
        return _seconds_val(value)
    return _metric(value)


def clean_keyword(text: Any) -> str:
    """Display form of a keyword: single spaces, no leading #."""
    return re.sub(r"\s+", " ", str(text or "")).strip().lstrip("#\uff03").strip()


def keyword_key(text: Any) -> str:
    """Comparison form of a keyword: no spaces, no leading #, lower case."""
    return re.sub(r"\s+", "", str(text or "").replace("\u2019", "'")).lstrip("#\uff03").lower()


def normalize_keywords(value: Any) -> List[str]:
    """Up to MAX_KEYWORDS_PER_SCRIPT distinct, cleaned keyword strings."""
    out: List[str] = []
    seen = set()
    for raw in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(raw, str):
            continue
        keyword = clean_keyword(raw)
        key = keyword_key(keyword)
        if not key or key in seen or len(keyword) > MAX_KEYWORD_CHARS:
            continue
        seen.add(key)
        out.append(keyword)
        if len(out) == MAX_KEYWORDS_PER_SCRIPT:
            break
    return out


def clean_file_name(name: Any) -> str:
    """A file name as a label: its last path component, one line, no control characters, capped in length.

    For display and for reading the extension only. It must never be used to
    build a path: files this app writes get names it generates itself.
    """
    if not isinstance(name, str):
        return ""
    name = re.split(r"[/\\]", name.replace("\x00", ""))[-1]
    name = re.sub(r"\s+", " ", "".join(ch for ch in name if ch.isprintable() or ch.isspace())).strip()
    return name[:MAX_SOURCE_NAME_CHARS].strip()


def _dumps(value: Any, **options: Any) -> str:
    """JSON for storing. A number that is not finite (``NaN``, ``Infinity``) raises ``ValueError``: none is ever written."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **options)


def _not_a_number(_name: str) -> None:
    return None


def _finite_or_none(text: str) -> Optional[float]:
    value = float(text)
    return value if math.isfinite(value) else None


def _stored_json_too_deep(text: str, limit: int = 64) -> bool:
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in ("{", "["):
                depth += 1
                if depth > limit:
                    return True
            elif ch in ("}", "]") and depth > 0:
                depth -= 1
    return False


def loads_stored(text: str) -> Any:
    """Read JSON that this app stored earlier. ``NaN`` and ``Infinity`` come back as None (``null``).

    Nothing writes them any more (``_dumps``; replies and requests that contain them are refused when they
    are read). A version before 0.2.0 could store them from a model's reply, and a browser cannot read an
    answer that contains them: the page for that script or profile showed nothing, not even the button to
    delete it. Read as ``null`` they can be shown, edited and removed. The same goes for a number written
    so large that it would be read as Infinity (``1e999``).
    """
    if not isinstance(text, str) or _stored_json_too_deep(text):
        raise ValueError("JSON nested too deeply")
    try:
        return json.loads(text, parse_constant=_not_a_number, parse_float=_finite_or_none)
    except RecursionError:
        raise ValueError("JSON nested too deeply")


def _as_object(data: bytes) -> Optional[Dict[str, Any]]:
    """The JSON object these bytes hold, or None when they hold something else or cannot be read."""
    try:
        value = loads_stored(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _json_object(text: Any) -> Optional[Dict[str, Any]]:
    """A stored JSON object, or None when the column is empty or does not hold one."""
    if not text:
        return None
    try:
        value = loads_stored(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _owner_only(root: Path, _mode: Optional[int] = None) -> None:
    """Take group and other access away from ``root`` and from every folder and file in it.

    Run each time the data folder is opened, so that a folder an earlier version created (0755, with files
    0644) is closed as well, and so is anything put there since. Only the group and other bits are cleared;
    what the owner may do stays as it was. Symbolic links are neither changed nor followed: what a link
    points at may lie outside the folder and is not this app's to alter. On Windows, where ``chmod`` does
    not change NTFS permissions, ``keystore.restrict_windows_acl`` restricts directory inheritance to the
    current user.
    """
    if os.name == "nt" and root.is_dir() and not root.is_symlink():
        keystore.restrict_windows_acl(root)

    def close(path: str) -> None:
        try:
            info = os.lstat(path)
            if (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) and info.st_mode & 0o077:
                os.chmod(path, stat.S_IMODE(info.st_mode) & ~0o077)
        except OSError:
            pass                      # gone in the meantime, or not ours to change

    close(str(root))
    if root.is_dir() and not root.is_symlink():
        for folder, folders, files in os.walk(str(root)):     # os.walk does not descend into linked folders
            for name in folders + files:
                close(os.path.join(folder, name))


# Windows does not let a file be replaced while anyone has it open: another save of the same file that is
# just taking its place, or a reader in the middle of loading it. The refusal ends as soon as that one is
# done, so there the replacement is tried again for a moment. Everywhere else a file can always be replaced,
# and a refusal is a real one.
_REPLACE_MAY_BE_REFUSED = os.name == "nt"
_REPLACE_TRIES = 12                     # with the waits below, a little over half a second in all


def _replace(tmp: str, target: str) -> None:
    """``os.replace``, tried again where a refusal may only last a moment (see above)."""
    wait = 0.002
    for attempt in range(1, _REPLACE_TRIES + 1):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if not _REPLACE_MAY_BE_REFUSED or attempt == _REPLACE_TRIES:
                raise
        time.sleep(wait)
        wait = min(wait * 2, 0.1)


def _sync_folder(folder: Path) -> None:
    """Ask the system to record a rename in ``folder`` on the disk. Best effort: not every system allows it."""
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Store:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir or os.environ.get("STUDIO_DATA_DIR") or ROOT / "data")
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)     # a new folder is closed to other accounts from the start
        self.db_path = self.data_dir / "studio.db"
        self.config_path = self.data_dir / "config.json"
        self.creator_path = self.data_dir / "creator.json"
        self.dna_path = self.data_dir / "voice_dna.json"
        self.dna_history_dir = self.data_dir / "voice_dna_history"
        self.pending_path = self.data_dir / "voice_pending.json"
        self.recommendations_path = self.data_dir / "recommendations.json"
        self.uploads_dir = self.data_dir / "uploads"    # videos waiting to be transcribed; created on first use
        db = self._db()
        try:
            with db:
                db.executescript(_SCHEMA)
                for table, column, definition in _ADDED_COLUMNS:   # database created by an earlier version
                    if column in self._columns(db, table):
                        continue
                    try:
                        db.execute("BEGIN IMMEDIATE")     # the column and what follows from it arrive together or not at all
                        db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, definition))
                        for statement in _AFTER_ADDING.get((table, column), ()):
                            db.execute(statement)
                        db.commit()
                    except sqlite3.OperationalError:
                        db.rollback()
                        # The web app and the MCP server may open the same database at once: fine if the other one added it.
                        if column not in self._columns(db, table):
                            raise
                db.execute(_ONE_ITEM_A_SCRIPT)
        finally:
            db.close()
        # Last, so the database just created is covered too. Until then a new one sits in a folder that is
        # already closed; a folder an earlier version left open is closed here, with everything in it.
        _owner_only(self.data_dir)
        self._seal_legacy_plaintext_keys()

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set:
        return {row["name"] for row in db.execute("PRAGMA table_info(%s)" % table)}

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    @staticmethod
    def _encode(payload: Any) -> bytes:
        """``payload`` as the bytes of one of this app's JSON files.

        Raises when it cannot be stored, before any file is touched: ``UnicodeEncodeError`` for text that
        cannot be written as UTF-8, ``ValueError`` for a number that is not finite (``NaN``, ``Infinity``).
        """
        return _dumps(payload, indent=2).encode("utf-8")

    @staticmethod
    def _write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
        """Put ``data`` at ``path`` so that a reader finds the old file or the new one, never part of either.

        - It is written to a temporary file of its own in the same folder. Two saves at the same moment get
          two different ones, so neither can rename or remove the other's.
        - The file is owner-only (``mode``) before any content goes into it. A system that cannot set a mode
          on an open file (Windows before Python 3.13 has no ``os.fchmod``) leaves the file as it was
          created, which is owner-only already wherever modes say who may read a file. On Windows they do
          not: there the folder the file is in decides that.
        - The content is handed to the disk before the file takes the place of the old one, so a crash or a
          power cut does not leave an empty or half-written file under the real name.
        - If anything fails, the temporary file is removed and the old file is as it was.
        """
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                set_mode = getattr(os, "fchmod", None)      # looked up now, not at import: absent on some systems
                if set_mode is not None:
                    set_mode(fh.fileno(), mode)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            _replace(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _sync_folder(path.parent)

    def _write_json(self, path: Path, payload: Any, mode: int = 0o600) -> None:
        self._write_bytes(path, self._encode(payload), mode)

    @staticmethod
    def _read_stored(path: Path) -> Any:
        """What one of this app's JSON files holds (see ``loads_stored``).

        Raises ``OSError`` when it cannot be opened (``FileNotFoundError`` when there is none) and
        ``ValueError`` when it is not JSON: empty, cut short, or not text.
        """
        try:
            return loads_stored(path.read_bytes().decode("utf-8"))
        except RecursionError:
            raise ValueError("nested too deeply to read")

    # ---- settings -------------------------------------------------------
    def _read_raw_config_dict(self) -> Dict[str, Any]:
        try:
            loaded = self._read_stored(self.config_path)
            return loaded if isinstance(loaded, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _inspect_stored_keys(self, loaded: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        """Return ``(active_keys, unopened_sealed, plain_keys)`` from ``config.json``.

        When both a plaintext ``keys[p]`` and ``keys_sealed[p]`` exist (for instance after running an older
        build on the same data folder), the plaintext key is the newer one and wins; it will be sealed on save.
        """
        data = self._read_raw_config_dict() if loaded is None else loaded
        raw_keys = data.get("keys")
        plain_keys: Dict[str, str] = {}
        if isinstance(raw_keys, dict):
            for provider, secret in raw_keys.items():
                if isinstance(provider, str) and isinstance(secret, str) and secret:
                    plain_keys[provider] = secret
        raw_sealed = data.get("keys_sealed")
        active_keys: Dict[str, str] = {}
        unopened_sealed: Dict[str, str] = {}
        if isinstance(raw_sealed, dict):
            for provider, token in raw_sealed.items():
                if not isinstance(provider, str) or not isinstance(token, str) or not token:
                    continue
                if provider in plain_keys:
                    continue
                opened = keystore.unseal(token)
                if opened:
                    active_keys[provider] = opened
                else:
                    unopened_sealed[provider] = token
        active_keys.update(plain_keys)
        return active_keys, unopened_sealed, plain_keys

    def _seal_legacy_plaintext_keys(self) -> None:
        """Seal legacy plaintext keys left by an older build as soon as the store opens, if sealing works."""
        with _CONFIG_LOCK:
            loaded = self._read_raw_config_dict()
            _, _, plain_keys = self._inspect_stored_keys(loaded)
            if not plain_keys:
                return
            try:
                keystore.seal(next(iter(plain_keys.values())))
            except keystore.KeystoreUnavailable:
                return
            self.save_config(self.load_config())

    def load_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {"provider": "", "model": "", "keys": {}, "local_base_url": "", "trend_weight": "balanced"}
        loaded = self._read_raw_config_dict()
        if loaded:
            config.update(loaded)
        keys, _, _ = self._inspect_stored_keys(loaded)
        config.pop("keys_sealed", None)
        config["keys"] = keys
        return config

    def save_config(self, config: Dict[str, Any]) -> None:
        disk = dict(config)
        _, unopened_on_disk, _ = self._inspect_stored_keys()
        raw_keys = disk.get("keys")
        raw_keys_dict = raw_keys if isinstance(raw_keys, dict) else {}
        sealed: Dict[str, str] = {}
        plain_fallback: Dict[str, str] = {}
        # Preserve sealed keys that could not be opened on this machine unless explicitly replaced or cleared.
        for provider, token in unopened_on_disk.items():
            if provider not in raw_keys_dict:
                sealed[provider] = token
        for provider, secret in raw_keys_dict.items():
            if isinstance(provider, str) and isinstance(secret, str) and secret:
                try:
                    sealed[provider] = keystore.seal(secret)
                except keystore.KeystoreUnavailable:
                    plain_fallback[provider] = secret
                    sealed.pop(provider, None)
        disk["keys"] = plain_fallback
        if sealed:
            disk["keys_sealed"] = sealed
        else:
            disk.pop("keys_sealed", None)
        self._write_json(self.config_path, disk, mode=0o600)

    def update_config(self, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        """Read the settings, let ``change`` alter them in place, and save them. Returns what was saved.

        One such step runs at a time. Reading, changing and saving as three separate calls lets two requests
        read the same settings and each write back its own copy; the second then undoes the first without
        either being told. ``change`` should only set what it means to change, and must not wait for
        anything slow (a model server, say): look that up first, then call this.

        If ``change`` raises, nothing is saved. The lock is this process's: it does not cover a second
        copy of the app started on the same data folder.
        """
        with _CONFIG_LOCK:
            config = self.load_config()
            change(config)
            self.save_config(config)
            return config

    def load_page_keeps(self) -> Dict[str, str]:
        """What is kept for the page (``PAGE_WORDS``, ``PAGE_NUMBERS``). Anything else found in the file is left out."""
        held = self.load_config().get("page")
        kept: Dict[str, str] = {}
        if isinstance(held, dict):
            for name, value in held.items():
                text = kept_value(name, value)
                if text is not None:
                    kept[name] = text
        return kept

    def keep_for_page(self, changes: Dict[str, str]) -> Dict[str, str]:
        """Keep ``changes`` (names and values that ``kept_value`` passed) with what is kept already. Returns all of it."""
        def change(config: Dict[str, Any]) -> None:
            held = config.get("page")
            config["page"] = dict(held if isinstance(held, dict) else {}, **changes)

        self.update_config(change)
        return self.load_page_keeps()

    def public_settings(self) -> Dict[str, Any]:
        """Settings safe to send to the browser: no key material."""
        loaded = self._read_raw_config_dict()
        active_keys, unopened_sealed, plain_keys = self._inspect_stored_keys(loaded)
        config: Dict[str, Any] = {"provider": "", "model": "", "local_base_url": ""}
        if loaded:
            config.update(loaded)
        local_url = config.get("local_base_url") or ""
        keys = {p: bool(active_keys.get(p)) for p in CLOUD_PROVIDERS}
        keys["local"] = bool(local_url)
        provider, model = config.get("provider") or "", config.get("model") or ""
        keys_unreadable = sorted(p for p in unopened_sealed if p)
        out: Dict[str, Any] = {
            "provider": provider,
            "model": model,
            "keys": keys,
            "local_base_url": local_url,
            "manual": provider == MANUAL_PROVIDER,
            "configured": provider == MANUAL_PROVIDER or bool(provider in PROVIDERS and model and keys.get(provider)),
        }
        if keys_unreadable:
            out["keys_unreadable"] = keys_unreadable
        if plain_keys:
            out["keystore_unavailable_path"] = keystore.key_path_display()
        return out

    # ---- creator profile ------------------------------------------------
    def load_creator(self) -> Optional[Dict[str, Any]]:
        """The creator profile saved during setup, or None when there is none (or the file is unreadable)."""
        try:
            loaded = self._read_stored(self.creator_path)
        except (FileNotFoundError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def save_creator(self, creator: Dict[str, Any]) -> None:
        """Store a profile already validated by ``studio.creator.normalize``."""
        self._write_json(self.creator_path, creator)

    # ---- voice dna ------------------------------------------------------
    def _own_dna(self) -> Tuple[Optional[Dict[str, Any]], bool]:
        """``(profile, unreadable)`` for the creator's own profile file.

        ``(None, False)`` when there is no file. ``(None, True)`` when there is one that cannot be read as a
        profile: empty, cut short, not text, or holding something other than a JSON object.
        """
        try:
            data = self.dna_path.read_bytes()
        except FileNotFoundError:
            return None, False
        except OSError:
            return None, True
        own = _as_object(data)
        return own, own is None

    def load_dna(self) -> Tuple[Dict[str, Any], bool]:
        """Return ``(dna, is_example)``; falls back to the bundled example profile.

        A profile file that cannot be read (see ``_own_dna``) does not stop the app: the example profile is
        used, and ``dna_unreadable`` says so, so the page can tell the creator. The file itself is left
        where it is, untouched. When a later save replaces it, it is kept in voice_dna_history/.
        """
        own, _ = self._own_dna()
        if own is not None:
            return own, False
        return json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8")), True

    def dna_unreadable(self) -> bool:
        """True when the creator has a profile file and it cannot be read, so the example is in use instead."""
        return self._own_dna()[1]

    def save_dna(self, dna: Dict[str, Any]) -> None:
        """Replace her profile, keeping the previous one in voice_dna_history/.

        The previous profile is copied to the history folder, not moved there, so it stays in use if the
        new one cannot be written (text that cannot be encoded, a full disk). A new profile that cannot be
        stored at all (see ``_encode``) is refused before anything is touched, the history folder included.
        """
        data = self._encode(dna)
        with _DNA_LOCK:
            self._keep_current_dna()
            self._write_bytes(self.dna_path, data)

    def _keep_current_dna(self) -> None:
        """Copy the profile file in use into voice_dna_history/, byte for byte.

        The copy's name carries the time, and a number when several are made within one second, so that it
        sorts after every earlier copy. A file that cannot be read as a profile is kept as well, with the
        ending ``.unreadable``: it is never offered as an earlier profile, and it is not thrown away either.
        """
        try:
            data = self.dna_path.read_bytes()
        except FileNotFoundError:
            return
        self.dna_history_dir.mkdir(mode=0o700, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        taken = [int(match.group(2) or 1) for match in map(_HISTORY_FILE.fullmatch, os.listdir(str(self.dna_history_dir)))
                 if match and match.group(1) == stamp]
        number = max(taken) + 1 if taken else 1
        name = "voice_dna-%s%s.%s" % (stamp, "" if number == 1 else "-%d" % number,
                                     "json" if _as_object(data) is not None else "unreadable")
        self._write_bytes(self.dna_history_dir / name, data)

    def list_dna_history(self) -> List[Dict[str, Any]]:
        """Earlier profiles kept in voice_dna_history/, newest first: ``[{"id", "saved_at", "dna"}]``.

        ``id`` names the copy (the time in its file name) and is what ``restore_dna`` takes. ``saved_at`` is
        when the copy was made, in seconds since 1970: the moment that profile stopped being the one in
        use. Files that are not copies made by ``save_dna``, and copies that cannot be read as a profile,
        are left out.
        """
        try:
            names = os.listdir(str(self.dna_history_dir))
        except OSError:
            return []
        found = []
        for name in names:
            match = _HISTORY_FILE.fullmatch(name)
            path = self.dna_history_dir / name
            if not match or match.group(3) != "json" or path.is_symlink():
                continue
            try:
                saved_at = time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S"))
                dna = _as_object(path.read_bytes())
            except (OSError, ValueError, OverflowError):
                continue
            if dna is not None:
                history_id = name[len("voice_dna-"):-len(".json")]
                found.append((saved_at, int(match.group(2) or 1), {"id": history_id, "saved_at": saved_at, "dna": dna}))
        found.sort(key=lambda entry: entry[:2], reverse=True)
        return [entry[2] for entry in found]

    def restore_dna(self, history_id: Any) -> Optional[Dict[str, Any]]:
        """Make an earlier profile the one in use again. Returns it, or None when ``history_id`` names none.

        The profile in use until now is copied to the history folder first, as in ``save_dna``. Then the
        chosen copy is moved into its place, byte for byte as it was kept. So the chosen profile leaves the
        list of earlier ones, the replaced one joins it, and neither is ever only in a file being written.

        ``history_id`` is checked against the form of the ids ``list_dna_history`` gives before it is used in
        a file name, so it cannot name a file outside the history folder.
        """
        if not isinstance(history_id, str) or not re.fullmatch(HISTORY_ID, history_id):
            return None
        source = self.dna_history_dir / ("voice_dna-%s.json" % history_id)
        with _DNA_LOCK:
            try:
                dna = None if source.is_symlink() else _as_object(source.read_bytes())
            except OSError:
                return None
            if dna is None:
                return None
            self._keep_current_dna()
            os.replace(str(source), str(self.dna_path))
            os.chmod(str(self.dna_path), 0o600)
            _sync_folder(self.data_dir)
        return dna

    # ---- proposed voice profile ----------------------------------------
    def load_pending(self) -> Optional[Dict[str, Any]]:
        """The learned profile waiting for the creator's decision, or None. It is not the active profile."""
        try:
            loaded = self._read_stored(self.pending_path)
        except (FileNotFoundError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) and isinstance(loaded.get("dna"), dict) else None

    def save_pending(self, pending: Dict[str, Any]) -> None:
        """Store a proposal, replacing an older one. The active profile is left alone."""
        self._write_json(self.pending_path, pending)

    def clear_pending(self) -> bool:
        try:
            self.pending_path.unlink()
        except FileNotFoundError:
            return False
        return True

    # ---- recommendations ------------------------------------------------
    def load_recommendations(self) -> Optional[Dict[str, Any]]:
        try:
            return self._read_stored(self.recommendations_path)
        except (FileNotFoundError, ValueError):
            return None

    def save_recommendations(self, payload: Dict[str, Any]) -> None:
        with _IDEAS_LOCK:
            self._write_json(self.recommendations_path, payload)

    def read_recommendations_bytes(self) -> Optional[bytes]:
        """The ideas file exactly as it is, or None when there is none. For keeping it and comparing it, not for reading ideas.

        Raises ``OSError`` when the file is there and cannot be opened.
        """
        try:
            return self.recommendations_path.read_bytes()
        except FileNotFoundError:
            return None

    def write_recommendations_bytes(self, data: Optional[bytes]) -> None:
        """Make the ideas file hold exactly ``data``, or remove it when ``data`` is None.

        For putting back what the file held, byte for byte, and for putting a kept set in its place
        (``studio/autosuggest.py``). Written the way every file here is: whole, or not at all.
        """
        with _IDEAS_LOCK:
            if data is None:
                try:
                    self.recommendations_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                self._write_bytes(self.recommendations_path, data)

    # ---- sets of ideas an automatic refresh made (studio/autosuggest.py) ----
    # One row is one set of topic ideas that autopilot's task ``ideas.refresh`` made. This part only stores: what
    # a state means, and when a set may take the place of the saved ideas, is in ``studio/autosuggest.py``.
    #
    # ``ideas_json`` is the set as the ideas file holds one, and ``ideas_sha`` the SHA-256 of those bytes.
    # ``status`` is ``waiting`` (offered; the ideas file does not hold it), ``in_use`` (it was put into the ideas
    # file), ``dismissed``, or ``replaced`` (a later set took its place: ``was`` says what it was until then and
    # ``replaced_by`` which set that was). ``placed_by`` says who put it into the ideas file, ``run`` or
    # ``creator``. ``earlier`` is what the ideas file held when a run put the set there, byte for byte;
    # ``had_ideas`` 0 says there was no ideas file then.
    @staticmethod
    def _idea_set_row(row: sqlite3.Row) -> Dict[str, Any]:
        def text(value: Any) -> str:
            return value if isinstance(value, str) else ""

        return {"id": text(row["id"]), "run_id": text(row["run_id"]), "status": text(row["status"]),
                "was": text(row["was"]), "replaced_by": text(row["replaced_by"]), "placed_by": text(row["placed_by"]),
                "ideas_json": text(row["ideas_json"]), "ideas_sha": text(row["ideas_sha"]),
                "had_ideas": bool(row["had_ideas"]), "earlier": row["earlier"] if isinstance(row["earlier"], str) else None,
                "trends_json": text(row["trends_json"]), "created_at": row["created_at"], "decided_at": row["decided_at"]}

    def list_idea_sets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """The sets that are kept, the newest first, at most ``limit``: a database from elsewhere may hold any number."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM idea_sets ORDER BY created_at DESC, rowid DESC LIMIT ?",
                              (max(1, min(int(limit), 500)),)).fetchall()
        return [self._idea_set_row(row) for row in rows]

    def get_idea_set(self, set_id: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(set_id, str):
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM idea_sets WHERE id = ?", (set_id,)).fetchone()
        return self._idea_set_row(row) if row else None

    def add_idea_set(self, new: Dict[str, Any], *, replaces: Any = (), keep: int = 12) -> None:
        """Keep a new set and, in the same step, mark the sets whose place it takes and drop what is over ``keep``.

        ``new`` holds ``id``, ``run_id``, ``status``, ``placed_by``, ``ideas_json``, ``ideas_sha``, ``had_ideas``,
        ``earlier``, ``trends_json`` and ``created_at``. ``replaces`` are ids: each of those sets that is still
        waiting or in use becomes ``replaced`` and remembers what it was. A set that is waiting or in use is
        never dropped; of all the others the newest ``keep`` stay, and the oldest go first.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO idea_sets (id, run_id, status, placed_by, ideas_json, ideas_sha, had_ideas, earlier,"
                " trends_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new["id"], str(new.get("run_id") or ""), new["status"], str(new.get("placed_by") or ""),
                 new["ideas_json"], str(new.get("ideas_sha") or ""), 1 if new.get("had_ideas") else 0,
                 new.get("earlier") if isinstance(new.get("earlier"), str) else None,
                 str(new.get("trends_json") or "[]"), float(new["created_at"])))
            for old in replaces:
                db.execute("UPDATE idea_sets SET was = status, status = 'replaced', replaced_by = ?"
                           " WHERE id = ? AND id != ? AND status IN ('waiting', 'in_use')", (new["id"], old, new["id"]))
            db.execute("DELETE FROM idea_sets WHERE status NOT IN ('waiting', 'in_use') AND id NOT IN"
                       " (SELECT id FROM idea_sets WHERE status NOT IN ('waiting', 'in_use')"
                       " ORDER BY created_at DESC, rowid DESC LIMIT ?)", (max(0, int(keep)),))

    def decide_idea_set(self, set_id: str, *, expect: str, status: str, now: float, placed_by: str = "",
                        replaces: Any = ()) -> bool:
        """Move a set from the state ``expect`` to ``status``. False, and nothing written, when it is not in that state.

        ``replaces`` are the ids of sets whose place it takes by this, as in ``add_idea_set``.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            moved = db.execute("UPDATE idea_sets SET status = ?, placed_by = ?, decided_at = ? WHERE id = ? AND status = ?",
                               (status, placed_by, float(now), set_id, expect)).rowcount > 0
            if moved:
                for old in replaces:
                    db.execute("UPDATE idea_sets SET was = status, status = 'replaced', replaced_by = ?"
                               " WHERE id = ? AND id != ? AND status IN ('waiting', 'in_use')", (set_id, old, set_id))
        return moved

    def remove_idea_set(self, set_id: str) -> bool:
        """Take a set out again and, in the same step, give every set it replaced its earlier state back.

        True when there was such a set. This is how a run that made a set is undone. A replaced set does not
        get a state back that another set holds by now: one set waits and one is in use, at most.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            gone = db.execute("DELETE FROM idea_sets WHERE id = ?", (set_id,)).rowcount > 0
            if gone:
                for state in ("in_use", "waiting"):
                    if db.execute("SELECT 1 FROM idea_sets WHERE status = ?", (state,)).fetchone() is None:
                        db.execute("UPDATE idea_sets SET status = was, was = '', replaced_by = '' WHERE id IN"
                                   " (SELECT id FROM idea_sets WHERE replaced_by = ? AND status = 'replaced' AND was = ?"
                                   " ORDER BY created_at DESC, rowid DESC LIMIT 1)", (set_id, state))
        return gone

    # ---- imported material ---------------------------------------------
    @staticmethod
    def _sample_row(row: sqlite3.Row) -> Dict[str, Any]:
        """A library item as it is handed out.

        An item made from a script that was posted (``studio.posted``) carries seven fields more: ``origin``
        (ORIGIN_SCRIPT), ``script_id`` (None once that script is deleted), ``posted_on``, ``note_url``,
        ``metrics_on``, ``ai_draft`` (what an AI wrote for the script) and ``snapshot``. Every other item is
        handed out exactly as before those existed, so read them with ``.get``.
        """
        sample = {
            "id": row["id"], "title": row["title"], "text": row["text"],
            "is_sponsored": bool(row["is_sponsored"]),
            "views": row["views"], "likes": row["likes"], "collects": row["collects"], "comments": row["comments"],
            "kind": row["kind"] if row["kind"] in SAMPLE_KINDS else SAMPLE_KINDS[0],
            "timing": _json_object(row["timing_json"]), "source_name": row["source_name"] or "",
            "needs_review": bool(row["needs_review"]), "reviewed_at": row["reviewed_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        if row["origin"] == ORIGIN_SCRIPT:
            sample.update({
                "origin": ORIGIN_SCRIPT, "script_id": row["script_id"], "posted_on": row["posted_on"],
                "note_url": row["note_url"] or "", "metrics_on": row["metrics_on"],
                "ai_draft": _json_object(row["ai_json"]), "snapshot": row["snapshot"] or "",
            })
        elif row["posted_on"]:
            sample["posted_on"] = row["posted_on"]
        return sample

    def list_samples(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM samples ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._sample_row(r) for r in rows]

    def samples_count(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def samples_version(self) -> Tuple[Any, ...]:
        """A value that changes whenever the library changes: an item added, removed, edited, confirmed or
        marked sponsored. Reading it costs one aggregate row, so something measured from the whole library
        (``voicematch.reference_for``, ``setup.status``) can be kept until this value moves."""
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), 0), COALESCE(MAX(reviewed_at), 0),"
                " COALESCE(SUM(needs_review), 0), COALESCE(SUM(is_sponsored), 0),"
                " COALESCE(SUM(LENGTH(text)), 0), COALESCE(SUM(LENGTH(COALESCE(timing_json, ''))), 0),"
                " COALESCE(SUM(LENGTH(COALESCE(ai_json, ''))), 0) FROM samples"
            ).fetchone()
        return tuple(row)

    def get_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._sample_row(row) if row else None

    def get_posted_sample(self, script_id: str) -> Optional[Dict[str, Any]]:
        """The library item made from this script when it was marked as posted, or None when there is none."""
        with self._db() as db:
            row = db.execute("SELECT * FROM samples WHERE script_id = ?", (script_id,)).fetchone()
        return self._sample_row(row) if row else None

    def posted_dates(self) -> Dict[str, Optional[str]]:
        """``{script id: the day it was posted}`` for every script that has its item in the library."""
        with self._db() as db:
            rows = db.execute("SELECT script_id, posted_on FROM samples WHERE script_id IS NOT NULL").fetchall()
        return {row["script_id"]: row["posted_on"] for row in rows}

    def create_sample(self, *, title: str, text: str, is_sponsored: bool = False,
                      metrics: Optional[Dict[str, Any]] = None, kind: str = "script",
                      timing: Optional[Dict[str, Any]] = None, source_name: str = "",
                      posted: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add an item to the library.

        ``kind`` says how it came in (see SAMPLE_KINDS). ``timing`` is the dict
        from ``timing.timing_stats`` for material that has timestamps, else
        None; it is a measurement, so it is set here once and never edited.
        ``source_name`` is the name of the file it came from, kept for display only.

        A ``video`` item is created with ``needs_review`` true: its words were
        written by a transcriber, not by the creator, and wait for the creator
        to read and confirm them (``update_sample``). Scripts and subtitle
        files are the creator's own text and do not wait.

        ``posted`` is given for an item made from a script of this app that was marked as posted
        (``studio.posted``, which checks the values): ``{"script_id", "posted_on", "note_url", "metrics_on",
        "ai_draft", "snapshot"}``. The item is stored with where it came from (ORIGIN_SCRIPT) and it waits as
        well (``needs_review``): its text began in this app, possibly as an AI's draft, so its wording is not
        learned from until the creator says the words are theirs. One script has one item: a second one for
        the same script raises ``sqlite3.IntegrityError`` and nothing is stored.

        The numbers an item arrives with are its first reading, kept under the day they were read
        (``record_metric_snapshot``): the day ``posted`` names, else today.

        Text longer than MAX_SAMPLE_CHARS raises ``TextTooLong`` and nothing is stored.
        """
        text = text.replace("\r\n", "\n").strip()
        if len(text) > MAX_SAMPLE_CHARS:
            raise TextTooLong(len(text))
        raw_title = (title or "").strip()
        title_limit = MAX_SCRIPT_TITLE_CHARS
        if isinstance(posted, dict) and isinstance(posted.get("script_id"), str):
            src_script = self.get_script(posted["script_id"])
            if src_script is not None:
                title_limit = max(title_limit, len(src_script.get("title") or ""))
        if len(raw_title) > title_limit:
            raise TextTooLong(len(raw_title), title_limit, field="script_title")
        first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
        title = raw_title or first_line[:20] or "未命名"
        metrics = metrics or {}
        kind = kind if kind in SAMPLE_KINDS else SAMPLE_KINDS[0]
        now = time.time()
        sample_id = uuid.uuid4().hex[:12]
        posted = posted if isinstance(posted, dict) else None
        ai_draft = posted.get("ai_draft") if posted else None
        v_val = _metric(metrics.get("views"))
        l_val = _metric(metrics.get("likes"))
        c_val = _metric(metrics.get("collects"))
        m_val = _metric(metrics.get("comments"))
        with self._db() as db:
            db.execute(
                "INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments,"
                " kind, timing_json, source_name, needs_review, origin, script_id, posted_on, note_url, metrics_on,"
                " ai_json, snapshot, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sample_id, title, text, int(bool(is_sponsored)),
                 v_val, l_val, c_val, m_val,
                 kind, _dumps(timing) if isinstance(timing, dict) else None,
                 clean_file_name(source_name), int(kind == "video" or posted is not None),
                 ORIGIN_SCRIPT if posted else "", posted.get("script_id") if posted else None,
                 posted.get("posted_on") if posted else None, str(posted.get("note_url") or "") if posted else "",
                 posted.get("metrics_on") if posted else None,
                 _dumps(ai_draft) if isinstance(ai_draft, dict) else None,
                 str(posted.get("snapshot") or "") if posted else "", now, now),
            )
            numbers = {field: value for field, value in zip(METRIC_FIELDS, (v_val, l_val, c_val, m_val)) if value is not None}
            if numbers:
                self._keep_reading(db, sample_id, str((posted or {}).get("metrics_on") or _today(now)), numbers, now)
        return self.get_sample(sample_id)  # type: ignore[return-value]

    def update_sample(self, sample_id: str, changes: Dict[str, Any],
                      posted: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Apply ``changes`` (unknown keys are ignored; ``kind`` and ``timing`` cannot be changed).

        ``posted`` holds new values for a posted item's own columns (POSTED_COLUMNS), already checked by
        ``studio.posted``. They are written in the same statement as ``changes``, so the numbers and the day
        they were read never part. Those columns are never taken from ``changes``.

        ``needs_review`` changes only when it is given as a boolean. Editing the
        text does not confirm an item: confirming is its own act. ``reviewed_at``
        is the moment an item that was waiting got confirmed, and is cleared if it is put back to waiting.

        A number that ``changes`` names was read now, or cleared now, so it is also kept in the item's reading of
        the day (``record_metric_snapshot``): the day ``posted`` gives for the numbers, else the day the item
        already has for them, else today. A number the request does not name is not written into that reading: it
        was read on some other day. When ``posted`` only moves the day of numbers that stay as they are, their
        reading moves with them.

        Text longer than MAX_SAMPLE_CHARS raises ``TextTooLong`` and none of ``changes`` is applied.
        """
        existing = self.get_sample(sample_id)
        if existing is None:
            return None
        title_limit = max(MAX_SCRIPT_TITLE_CHARS, len(existing.get("title") or ""))
        text_limit = max(MAX_SAMPLE_CHARS, len(existing.get("text") or ""))
        sets: List[str] = []
        values: List[Any] = []
        now = time.time()
        if isinstance(changes.get("title"), str):
            new_title = changes["title"].strip()
            if len(new_title) > title_limit:
                raise TextTooLong(len(new_title), title_limit, field="script_title")
            sets.append("title = ?"); values.append(new_title or "未命名")
        if isinstance(changes.get("text"), str):
            text = changes["text"].replace("\r\n", "\n").strip()
            if len(text) > text_limit:
                raise TextTooLong(len(text), text_limit, field="sample")
            sets.append("text = ?"); values.append(text)
        if "is_sponsored" in changes:
            sets.append("is_sponsored = ?"); values.append(int(bool(changes["is_sponsored"])))
        read: Dict[str, Optional[int]] = {}     # the numbers this request names
        for field in METRIC_FIELDS:
            if field in changes:
                read[field] = _metric(changes[field])
                sets.append("%s = ?" % field); values.append(read[field])
        if changes.get("needs_review") is True:
            sets.append("needs_review = 1"); sets.append("reviewed_at = NULL")
        elif changes.get("needs_review") is False:
            # Every right-hand side reads the row as it was, so the time is set only on the change from waiting.
            sets.append("needs_review = 0")
            sets.append("reviewed_at = CASE WHEN needs_review = 1 THEN ? ELSE reviewed_at END"); values.append(now)
        for column in POSTED_COLUMNS:
            if isinstance(posted, dict) and column in posted:
                sets.append("%s = ?" % column); values.append(posted[column])
        text_changed = isinstance(changes.get("text"), str)
        if sets:
            sets.append("updated_at = ?"); values.append(now)
            values.append(sample_id)
            day_was = existing.get("metrics_on")
            day_is = posted["metrics_on"] if (isinstance(posted, dict) and "metrics_on" in posted) else day_was
            with self._db() as db:
                db.execute("UPDATE samples SET %s WHERE id = ?" % ", ".join(sets), values)
                if read:
                    self._keep_reading(db, sample_id, day_is or _today(now), read, now)
                if day_is and day_is != day_was and all(read[field] == existing.get(field) for field in read):
                    # Numbers that stay as they are and get a day, or another day: their reading is that day's.
                    if day_was:
                        db.execute("DELETE FROM metric_snapshots WHERE sample_id = ? AND read_on = ?", (sample_id, day_was))
                    self._keep_reading(db, sample_id, day_is, {field: existing.get(field) for field in METRIC_FIELDS}, now)
                if text_changed:
                    new_key = _spoken_words_key(changes["text"])
                    stale_ids = [
                        r["id"]
                        for r in db.execute(
                            "SELECT id, spoken_text FROM sample_beats WHERE sample_id = ?", (sample_id,)
                        ).fetchall()
                        if not _spoken_words_key(r["spoken_text"]) or _spoken_words_key(r["spoken_text"]) not in new_key
                    ]
                    if stale_ids:
                        marks = ",".join("?" for _ in stale_ids)
                        db.execute("DELETE FROM sample_beats WHERE id IN (%s)" % marks, stale_ids)
        return self.get_sample(sample_id)

    def replace_sample_text(self, sample_id: str, *, text: str, kind: str, needs_review: bool,
                            timing: Optional[Dict[str, Any]] = None, source_name: str = "",
                            title: Optional[str] = None, ai_draft: Any = _KEEP,
                            snapshot: Any = _KEEP) -> Optional[Dict[str, Any]]:
        """Give a posted item other words, and say how they came in (``studio.posted`` decides when).

        Used when the item's text is taken from its script again, and when the words of the posted video
        arrive from a subtitle file or a transcription. ``kind`` and ``timing`` are set here together with the
        text they belong to, which ``update_sample`` never does. ``needs_review`` says whether the new words
        wait for the creator; when they do not, ``reviewed_at`` is this moment: from now on they are material.

        The numbers, the days and the link to the script stay as they are, and so does the title unless
        ``title`` is given. ``ai_draft`` and ``snapshot`` replace what was kept when they are given.

        Text longer than MAX_SAMPLE_CHARS raises ``TextTooLong`` and nothing is changed.
        """
        text = text.replace("\r\n", "\n").strip()
        if len(text) > MAX_SAMPLE_CHARS:
            raise TextTooLong(len(text))
        now = time.time()
        sets = ["text = ?", "kind = ?", "timing_json = ?", "source_name = ?", "needs_review = ?", "reviewed_at = ?",
                "updated_at = ?"]
        values: List[Any] = [text, kind if kind in SAMPLE_KINDS else SAMPLE_KINDS[0],
                             _dumps(timing) if isinstance(timing, dict) else None, clean_file_name(source_name),
                             int(bool(needs_review)), None if needs_review else now, now]
        if isinstance(title, str) and title.strip():
            clean_t = title.strip()
            existing_sample = self.get_sample(sample_id)
            t_limit = max(MAX_SCRIPT_TITLE_CHARS, len((existing_sample or {}).get("title") or ""))
            if len(clean_t) > t_limit:
                raise TextTooLong(len(clean_t), t_limit, field="script_title")
            sets.append("title = ?"); values.append(clean_t)
        if ai_draft is not _KEEP:
            sets.append("ai_json = ?"); values.append(_dumps(ai_draft) if isinstance(ai_draft, dict) else None)
        if snapshot is not _KEEP:
            sets.append("snapshot = ?"); values.append(str(snapshot or ""))
        values.append(sample_id)
        with self._db() as db:
            db.execute("UPDATE samples SET %s WHERE id = ?" % ", ".join(sets), values)
            db.execute("DELETE FROM sample_beats WHERE sample_id = ?", (sample_id,))
        return self.get_sample(sample_id)

    def delete_sample(self, sample_id: str) -> bool:
        """Remove a library item. Being pinned to a script never keeps an item: its pins and beats go with it."""
        with self._db() as db:
            db.execute("DELETE FROM script_pins WHERE sample_id = ?", (sample_id,))
            db.execute("DELETE FROM metric_snapshots WHERE sample_id = ?", (sample_id,))
            db.execute("DELETE FROM sample_comments WHERE sample_id = ?", (sample_id,))
            db.execute("DELETE FROM sample_sentiments WHERE sample_id = ?", (sample_id,))
            db.execute("DELETE FROM sample_beats WHERE sample_id = ?", (sample_id,))
            db.execute("UPDATE watched_files SET sample_id = NULL WHERE sample_id = ?", (sample_id,))
            return db.execute("DELETE FROM samples WHERE id = ?", (sample_id,)).rowcount > 0

    def list_sample_heads(self, text_chars: int, title_chars: int) -> List[Dict[str, Any]]:
        """Every library item with only the start of its text, newest first: what a ranking or a search reads.

        One item can hold MAX_SAMPLE_CHARS characters and a library hundreds of items. Something that looks at
        every item on every request must not load all of that, so the text is cut by the database, before it
        reaches this program. Each row has ``id``, ``title`` (its first ``title_chars`` characters), ``text``
        (its first ``text_chars``), ``text_cut`` (whether the text goes on after that), ``is_sponsored``, the
        four numbers, ``kind``, ``needs_review``, ``origin``, ``script_id``, ``posted_on``, ``metrics_on`` and
        ``created_at``. What an AI wrote for a posted item (``ai_draft``) is not read here.

        The database cuts bytes and the characters are counted here. Its own way of cutting text by characters
        stops at a NUL character, and a pasted text or a file can hold one: such an item would be read only up
        to it. A character takes at most four bytes, so four bytes for each character wanted always hold enough.
        """
        title_chars, text_chars = max(0, int(title_chars)), max(0, int(text_chars))
        with self._db() as db:
            rows = db.execute(
                "SELECT id, substr(CAST(title AS BLOB), 1, ?) AS title, substr(CAST(text AS BLOB), 1, ?) AS text,"
                " length(CAST(text AS BLOB)) AS text_bytes,"
                " is_sponsored, views, likes, collects, comments, kind, needs_review, origin, script_id, posted_on,"
                " metrics_on, created_at FROM samples ORDER BY created_at DESC, rowid DESC",
                (4 * title_chars, 4 * text_chars)).fetchall()
        heads = []
        for row in rows:
            # A character the last byte cut in two stands after the characters that are kept, so it is never among them.
            text = bytes(row["text"] or b"").decode("utf-8", "replace")
            heads.append({
                "id": row["id"], "title": bytes(row["title"] or b"").decode("utf-8", "replace")[:title_chars],
                "text": text[:text_chars],
                "text_cut": (row["text_bytes"] or 0) > 4 * text_chars or len(text) > text_chars,
                "is_sponsored": bool(row["is_sponsored"]),
                "views": row["views"], "likes": row["likes"], "collects": row["collects"], "comments": row["comments"],
                "kind": row["kind"] if row["kind"] in SAMPLE_KINDS else SAMPLE_KINDS[0],
                "needs_review": bool(row["needs_review"]), "origin": row["origin"] or "", "script_id": row["script_id"],
                "posted_on": row["posted_on"], "metrics_on": row["metrics_on"], "created_at": row["created_at"],
            })
        return heads

    # ---- trend keywords ---------------------------------------------------
    @staticmethod
    def _trend_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in ("id", "keyword", "heat", "source", "source_url", "note_zh", "note_en",
                                          "created_at", "updated_at")}

    def list_trends(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM trends ORDER BY updated_at DESC, rowid DESC").fetchall()
        return [self._trend_row(r) for r in rows]

    def trends_count(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM trends").fetchone()[0])

    def get_trend(self, trend_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM trends WHERE id = ?", (trend_id,)).fetchone()
        return self._trend_row(row) if row else None

    def upsert_trends(self, items: List[Dict[str, Any]], source: str = "manual") -> int:
        """Add keywords, or refresh ones already tracked. Returns how many were added or changed.

        ``seen_at`` (when the evidence dates from) may be given per item.

        What an AI reports never lands on a keyword the creator typed. Such a row is shown without the
        "found by AI" label, so everything on it has to be the creator's:

        - A keyword the creator typed (``manual``) that an AI lookup names again is left exactly as it is:
          its source, link, notes, heat and date. It is not counted in the result.
        - A keyword an AI found that the creator then types becomes the creator's: marked ``manual``, seen
          now, and the AI's link and notes are removed from it.
        """
        now = time.time()
        stored = 0
        with self._db() as db:
            # Looking a keyword up and then adding or changing it has to be one step. Without this, two saves
            # of the same new keyword at the same moment (two tabs, or the page and an AI app) both find
            # nothing and both insert, and the second fails on the unique key. With it, the second waits for
            # the first and then finds its row.
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                keyword = clean_keyword(item.get("keyword"))
                key = keyword_key(keyword)
                if not key or len(keyword) > MAX_KEYWORD_CHARS:
                    continue
                heat = _metric(item.get("heat")) or None
                seen = float(item.get("seen_at") or now)
                seen = seen if math.isfinite(seen) else now
                src_url = str(item.get("source_url") or "")[:1000]
                note_zh = str(item.get("note_zh") or "")[:500]
                note_en = str(item.get("note_en") or "")[:500]
                row = db.execute("SELECT * FROM trends WHERE key = ?", (key,)).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO trends (id, key, keyword, heat, source, source_url, note_zh, note_en,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], key, keyword, heat, source, src_url, note_zh, note_en, now, seen))
                elif row["source"] == "manual" and source != "manual":
                    continue                    # the creator's own keyword: an AI naming it changes nothing on it
                elif source == "manual" and row["source"] != "manual":
                    # The creator typed a keyword an AI had found. It is theirs now, and carries nothing the AI wrote.
                    db.execute(
                        "UPDATE trends SET heat = ?, source = 'manual', source_url = ?, note_zh = ?, note_en = ?,"
                        " updated_at = ? WHERE id = ?",
                        (heat if heat is not None else row["heat"], src_url, note_zh, note_en,
                         max(seen, row["updated_at"]), row["id"]))
                else:
                    db.execute(
                        "UPDATE trends SET heat = ?, source = ?, source_url = ?, note_zh = ?, note_en = ?,"
                        " updated_at = ? WHERE id = ?",
                        (heat if heat is not None else row["heat"], source,
                         src_url or row["source_url"], note_zh or row["note_zh"],
                         note_en or row["note_en"], max(seen, row["updated_at"]), row["id"]))
                stored += 1
            total = int(db.execute("SELECT COUNT(*) FROM trends").fetchone()[0])
            if total > MAX_TRENDS:
                db.execute(
                    "DELETE FROM trends WHERE id IN ("
                    "SELECT id FROM trends ORDER BY CASE WHEN source = 'manual' THEN 1 ELSE 0 END ASC,"
                    " updated_at ASC, rowid ASC LIMIT ?)",
                    (total - MAX_TRENDS,),
                )
        return stored

    def update_trend(self, trend_id: str, heat: Any) -> Optional[Dict[str, Any]]:
        """Set (or clear) the heat number; counts as seeing the trend again today."""
        with self._db() as db:
            db.execute("UPDATE trends SET heat = ?, updated_at = ? WHERE id = ?",
                       (_metric(heat) or None, time.time(), trend_id))
        return self.get_trend(trend_id)

    def delete_trend(self, trend_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM trends WHERE id = ?", (trend_id,)).rowcount > 0

    # ---- scripts --------------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "mode": row["mode"],
            "video_format": row["video_format"], "target_sec": row["target_sec"],
            "notes": row["notes"], "lines": loads_stored(row["lines_json"]),
            "post": loads_stored(row["post_json"]), "status": row["status"],
            "has_ai_draft": row["ai_lines_json"] is not None,
            "keywords": loads_stored(row["keywords_json"] or "[]"),
            "basis": _json_object(row["basis_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_scripts(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, title, target_sec, status, updated_at, line_count, preview,"
                " CASE WHEN line_count IS NULL OR preview IS NULL THEN lines_json END AS legacy_lines_json"
                " FROM scripts ORDER BY updated_at DESC"
            ).fetchall()
        out = []
        for row in rows:
            if row["line_count"] is not None and row["preview"] is not None:
                line_count, preview = int(row["line_count"]), str(row["preview"])
            else:
                line_count, preview = _script_summary(loads_stored(row["legacy_lines_json"] or "[]"))
            out.append({
                "id": row["id"], "title": row["title"], "target_sec": row["target_sec"],
                "status": row["status"], "updated_at": row["updated_at"],
                "line_count": line_count, "preview": preview,
            })
        return out

    def get_script(self, script_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        return self._row(row) if row else None

    def script_titles(self) -> Dict[str, str]:
        """``{script id: title}`` for every script: what the library needs to name the script an item came from."""
        with self._db() as db:
            return {row["id"]: row["title"] for row in db.execute("SELECT id, title FROM scripts")}

    def get_ai_lines(self, script_id: str) -> Optional[List[Dict[str, Any]]]:
        """The lines as the AI wrote them, or None for a script that was not drafted by AI (or does not exist)."""
        with self._db() as db:
            row = db.execute("SELECT ai_lines_json FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if row is None or row["ai_lines_json"] is None:
            return None
        return loads_stored(row["ai_lines_json"])

    def list_ai_drafts(self) -> List[Dict[str, Any]]:
        """Every AI-drafted script as ``{"id", "ai_lines", "lines"}``: what the AI wrote and what stands now."""
        with self._db() as db:
            rows = db.execute("SELECT id, ai_lines_json, lines_json FROM scripts WHERE ai_lines_json IS NOT NULL"
                              " ORDER BY created_at, rowid").fetchall()
        return [{"id": r["id"], "ai_lines": loads_stored(r["ai_lines_json"]), "lines": loads_stored(r["lines_json"])}
                for r in rows]

    def count_ai_drafts(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM scripts WHERE ai_lines_json IS NOT NULL").fetchone()[0]

    def create_script(self, *, title: str = "", notes: str = "", target_sec: int = 60,
                      video_format: str = "VLOG_VOICEOVER", lines: Any = None,
                      post: Optional[Dict[str, Any]] = None, from_ai: bool = False,
                      default_title: str = "未命名脚本", keywords: Any = None,
                      basis: Optional[Dict[str, Any]] = None, mode: str = MODE_ORGANIC) -> Dict[str, Any]:
        """Save a new script. ``from_ai`` keeps the lines as the AI's draft, for later comparison with the
        creator's version; ``basis`` (what that draft's prompt contained) is only kept for an AI draft.

        ``mode`` is MODE_SPONSORED for a script drafted from a brand's brief (``studio.sponsored``) and
        MODE_ORGANIC for every other; anything else is stored as MODE_ORGANIC. Nothing changes it afterwards."""
        raw_title = (title or "").strip()
        if len(raw_title) > MAX_SCRIPT_TITLE_CHARS:
            raise TextTooLong(len(raw_title), MAX_SCRIPT_TITLE_CHARS, field="script_title")
        if len(notes) > MAX_NOTES_CHARS:
            raise TextTooLong(len(notes), MAX_NOTES_CHARS, field="notes")
        if len(video_format) > MAX_META_FIELD_CHARS:
            raise TextTooLong(len(video_format), MAX_META_FIELD_CHARS, field="video_format")
        normalized = normalize_lines(lines) or normalize_lines([{}])
        line_count, preview = _script_summary(normalized)
        post_obj = post or {}
        post_len = _post_chars(post_obj)
        post_json = _dumps(post_obj)
        if post_len > MAX_POST_CHARS or len(post_json) > MAX_POST_CHARS * 4 + 4096:
            raise TextTooLong(max(post_len, MAX_POST_CHARS + 1) if post_len <= MAX_POST_CHARS else post_len,
                              MAX_POST_CHARS, field="post")
        now = time.time()
        script_id = uuid.uuid4().hex[:12]
        first_note = next((l.strip() for l in notes.splitlines() if l.strip()), "")
        title = raw_title or first_note[:16] or default_title
        lines_json = _dumps(normalized)
        basis_json = _dumps(basis) if (from_ai and isinstance(basis, dict)) else None
        with self._db() as db:
            db.execute(
                "INSERT INTO scripts (id, title, mode, video_format, target_sec, notes, lines_json, line_count, preview,"
                " post_json, ai_lines_json, keywords_json, basis_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (script_id, title, MODE_SPONSORED if mode == MODE_SPONSORED else MODE_ORGANIC,
                 video_format, int(target_sec), notes, lines_json, line_count, preview,
                 post_json, lines_json if from_ai else None,
                 _dumps(normalize_keywords(keywords)), basis_json, now, now),
            )
        return self.get_script(script_id)  # type: ignore[return-value]

    def update_script(self, script_id: str, changes: Dict[str, Any], by_ai: bool = False,
                      base_updated_at: Any = None) -> Optional[Dict[str, Any]]:
        """Apply ``changes`` (unknown keys are ignored; the AI's draft and the basis cannot be changed this way).

        ``by_ai`` says the new lines were written by an AI revising its own draft (the MCP ``update_script``
        tool), not by the creator. They then also become the draft that later edits are compared with, so an
        AI's corrections are never presented as the creator's. A script that was not drafted by AI stays so.

        ``base_updated_at`` is the ``updated_at`` of the version the caller was looking at when it made these
        changes. When it is given and the stored script is newer, nothing is changed and ``StaleScript`` is
        raised with the stored ``updated_at``: someone else (another tab, an AI app) saved in between, and
        writing now would silently replace what they wrote. The comparison and the write are one statement,
        so two saves based on the same version cannot both get through. Left out, the save is applied
        whatever is stored, as before. A value that is not a number raises ``ValueError("bad_base_updated_at")``.

        Every save moves ``updated_at`` forward, also when the computer's clock was set back in between, so
        a newer version never carries an older time than the one it replaced.
        """
        if base_updated_at is not None:
            if isinstance(base_updated_at, bool) or not isinstance(base_updated_at, (int, float)):
                raise ValueError("bad_base_updated_at")
            try:
                base_updated_at = float(base_updated_at)
            except OverflowError:
                raise ValueError("bad_base_updated_at")
            if not math.isfinite(base_updated_at):
                raise ValueError("bad_base_updated_at")
        with self._db() as db:
            current = db.execute(
                "SELECT title, notes, video_format, status, lines_json, post_json FROM scripts WHERE id = ?",
                (script_id,),
            ).fetchone()
        if current is None:
            return None
        title_limit = max(MAX_SCRIPT_TITLE_CHARS, len(current["title"] or ""))
        format_limit = max(MAX_META_FIELD_CHARS, len(current["video_format"] or ""))
        status_limit = max(MAX_META_FIELD_CHARS, len(current["status"] or ""))
        stored_lines = loads_stored(current["lines_json"] or "[]")
        stored_lines_list = stored_lines if isinstance(stored_lines, list) else []
        lines_limit = max(MAX_SCRIPT_LINES, len(stored_lines_list))
        stored_max_line = max(
            (len(v) for item in stored_lines_list if isinstance(item, dict)
             for f_name, v in item.items() if f_name in LINE_FIELDS and isinstance(v, str)),
            default=0,
        )
        line_chars_limit = max(MAX_LINE_CHARS, stored_max_line)
        stored_post = loads_stored(current["post_json"] or "{}")
        post_limit = max(MAX_POST_CHARS, _post_chars(stored_post))
        post_json_limit = max(MAX_POST_CHARS * 4 + 4096, len(current["post_json"] or ""))

        sets: List[str] = []
        values: List[Any] = []
        if isinstance(changes.get("title"), str):
            new_title = changes["title"].strip()
            if len(new_title) > title_limit:
                raise TextTooLong(len(new_title), title_limit, field="script_title")
            sets.append("title = ?"); values.append(new_title or "未命名脚本")
        if isinstance(changes.get("video_format"), str):
            new_format = changes["video_format"]
            if len(new_format) > format_limit:
                raise TextTooLong(len(new_format), format_limit, field="video_format")
            sets.append("video_format = ?"); values.append(new_format)
        if isinstance(changes.get("status"), str):
            new_status = changes["status"]
            if len(new_status) > status_limit:
                raise TextTooLong(len(new_status), status_limit, field="status")
            sets.append("status = ?"); values.append(new_status)
        target = changes.get("target_sec")
        if isinstance(target, (int, float)) and (not isinstance(target, float) or math.isfinite(target)):
            sets.append("target_sec = ?"); values.append(max(5, min(int(target), 900)))
        if "lines" in changes:
            normalized = normalize_lines(changes["lines"], max_lines=lines_limit, max_line_chars=line_chars_limit)
            line_count, preview = _script_summary(normalized)
            lines_json = _dumps(normalized)
            sets.append("lines_json = ?"); values.append(lines_json)
            sets.append("line_count = ?"); values.append(line_count)
            sets.append("preview = ?"); values.append(preview)
            if by_ai:
                sets.append("ai_lines_json = CASE WHEN ai_lines_json IS NULL THEN NULL ELSE ? END"); values.append(lines_json)
        if isinstance(changes.get("post"), dict):
            post_len = _post_chars(changes["post"])
            post_json = _dumps(changes["post"])
            if post_len > post_limit or len(post_json) > post_json_limit:
                raise TextTooLong(max(post_len, post_limit + 1) if post_len <= post_limit else post_len,
                                  post_limit, field="post")
            sets.append("post_json = ?"); values.append(post_json)
        if "keywords" in changes:
            sets.append("keywords_json = ?")
            values.append(_dumps(normalize_keywords(changes["keywords"])))
        stored_at = None
        with self._db() as db:
            changed = 0
            if sets:
                # MAX(...): the new time is later than the stored one even if the clock went backwards.
                sets.append("updated_at = MAX(?, updated_at + 0.000001)"); values.append(time.time())
                where = "id = ?"
                values.append(script_id)
                if base_updated_at is not None:
                    where += " AND updated_at <= ?"
                    values.append(base_updated_at)
                changed = db.execute("UPDATE scripts SET %s WHERE %s" % (", ".join(sets), where), values).rowcount
            if base_updated_at is not None and not changed:
                row = db.execute("SELECT updated_at FROM scripts WHERE id = ?", (script_id,)).fetchone()
                if row is not None and row["updated_at"] > base_updated_at:
                    stored_at = row["updated_at"]
        if stored_at is not None:
            raise StaleScript(stored_at)
        return self.get_script(script_id)

    def delete_script(self, script_id: str) -> bool:
        """Delete a script. The library item of a script that was posted is the creator's video: it stays, with
        its numbers and with where it came from, and only stops pointing at a script that is no longer there.
        The pins of the script are removed with it; the items they named stay in the library. A brand's brief
        it was drafted from stays too, until the creator deletes it; it stops pointing here, and the ticks set
        on this script's list go with the script."""
        with self._db() as db:
            db.execute("DELETE FROM ai_suggestions WHERE script_id = ?", (script_id,))
            db.execute("DELETE FROM script_pins WHERE script_id = ?", (script_id,))      # its pins are about this script only
            db.execute("UPDATE sponsor_briefs SET script_id = NULL, ticks_json = '{}' WHERE script_id = ?", (script_id,))
            db.execute("UPDATE samples SET script_id = NULL WHERE script_id = ?", (script_id,))
            db.execute("UPDATE video_plans SET script_id = NULL WHERE script_id = ?", (script_id,))
            return db.execute("DELETE FROM scripts WHERE id = ?", (script_id,)).rowcount > 0

    # ---- library items pinned to a script -------------------------------
    # Pins have a table of their own (``script_pins``) and nothing below reads or writes a script's row. So a
    # pin never moves a script's ``updated_at``, and a save based on the version from before the pin is taken
    # as any save is (``update_script``): pinning is not an edit of the script.
    def list_pins(self, script_id: str) -> List[str]:
        """The ids of the library items pinned to this script, in the order they were pinned.

        Only items that are in the library are named: a pin whose item is gone is not a pin.
        """
        with self._db() as db:
            rows = db.execute("SELECT p.sample_id FROM script_pins p JOIN samples s ON s.id = p.sample_id"
                              " WHERE p.script_id = ? ORDER BY p.created_at, p.rowid", (script_id,)).fetchall()
        return [row["sample_id"] for row in rows]

    def pin_sample(self, script_id: str, sample_id: str, limit: int) -> str:
        """Pin a library item to a script. Returns what happened: ``"pinned"``, ``"already"`` (it was pinned and
        stays where it was), ``"no_script"``, ``"no_sample"``, or ``"full"`` when the script has ``limit`` pins.

        Looking and writing are one step, so two windows that pin at the same moment cannot get past the limit
        between them. A pin left behind by an item that is gone is cleared first and is never counted.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM scripts WHERE id = ?", (script_id,)).fetchone() is None:
                return "no_script"
            if db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone() is None:
                return "no_sample"
            db.execute("DELETE FROM script_pins WHERE script_id = ? AND sample_id NOT IN (SELECT id FROM samples)", (script_id,))
            if db.execute("SELECT 1 FROM script_pins WHERE script_id = ? AND sample_id = ?", (script_id, sample_id)).fetchone():
                return "already"
            if db.execute("SELECT COUNT(*) FROM script_pins WHERE script_id = ?", (script_id,)).fetchone()[0] >= limit:
                return "full"
            db.execute("INSERT INTO script_pins (script_id, sample_id, created_at) VALUES (?,?,?)",
                       (script_id, sample_id, time.time()))
        return "pinned"

    def unpin_sample(self, script_id: str, sample_id: str) -> bool:
        """Take a pin away. True when there was one."""
        with self._db() as db:
            return db.execute("DELETE FROM script_pins WHERE script_id = ? AND sample_id = ?",
                              (script_id, sample_id)).rowcount > 0

    # ---- what an AI suggested for a script's lines ----------------------
    def add_ai_suggestions(self, script_id: str, texts: Any) -> int:
        """Remember texts an AI suggested for lines of this script: a new opening, a rewording, a repair.

        They are kept so that a line the creator took over from a suggestion is never presented as the
        creator's own edit (``pipeline.script_changes``). It is written down here, when the suggestion is
        made, and not reported by the page when one is accepted: a line copied by hand counts the same.
        Returns how many were stored. Only the newest MAX_AI_SUGGESTIONS of a script are kept.
        """
        wanted = [text.strip() for text in (texts if isinstance(texts, (list, tuple)) else []) if isinstance(text, str) and text.strip()]
        if not wanted:
            return 0
        now = time.time()
        with self._db() as db:
            if db.execute("SELECT 1 FROM scripts WHERE id = ?", (script_id,)).fetchone() is None:
                return 0
            db.executemany("INSERT INTO ai_suggestions (script_id, text, created_at) VALUES (?,?,?)",
                           [(script_id, text, now) for text in wanted])
            db.execute("DELETE FROM ai_suggestions WHERE script_id = ? AND rowid NOT IN"
                       " (SELECT rowid FROM ai_suggestions WHERE script_id = ? ORDER BY rowid DESC LIMIT ?)",
                       (script_id, script_id, MAX_AI_SUGGESTIONS))
        return len(wanted)

    def list_ai_suggestions(self, script_id: Optional[str] = None) -> List[str]:
        """Texts an AI suggested for one script's lines, or for every script when no id is given; oldest first."""
        with self._db() as db:
            if script_id is None:
                rows = db.execute("SELECT text FROM ai_suggestions ORDER BY rowid").fetchall()
            else:
                rows = db.execute("SELECT text FROM ai_suggestions WHERE script_id = ? ORDER BY rowid", (script_id,)).fetchall()
        return [row["text"] for row in rows]

    # ---- lessons from the creator's edits -------------------------------
    @staticmethod
    def _lesson_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in ("id", "script_id", "ai", "final", "note", "created_at")}

    def list_lessons(self) -> List[Dict[str, Any]]:
        """Remembered lessons, newest first."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM lessons ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._lesson_row(r) for r in rows]

    def count_lessons(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]

    def add_lesson(self, *, script_id: str, ai: str, final: str, note: str = "") -> Tuple[Dict[str, Any], bool]:
        """Remember one change. Returns ``(lesson, created)``; the same ``ai`` and ``final`` again gives the existing one."""
        with self._db() as db:
            created = db.execute(
                "INSERT OR IGNORE INTO lessons (id, script_id, ai, final, note, created_at) VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], script_id, ai, final, note, time.time())).rowcount > 0
            row = db.execute("SELECT * FROM lessons WHERE ai = ? AND final = ?", (ai, final)).fetchone()
        return self._lesson_row(row), created

    def delete_lesson(self, lesson_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,)).rowcount > 0

    # ---- what a prompt contained ----------------------------------------
    def save_brief(self, kind: str, payload: Dict[str, Any], fingerprint: str = "") -> str:
        """Record what a prompt handed to an outside model contained; returns the record's id.

        Copy-and-paste mode and MCP build a prompt in one request and receive the reply in another, possibly
        after the library or the profile has changed. What is later said about the result ("based on these
        scripts") has to describe the prompt the model actually read, so it is written down when the prompt
        is built. ``fingerprint`` identifies the request, for a reply that does not quote the id.
        """
        brief_id = uuid.uuid4().hex[:12]
        with self._db() as db:
            db.execute("INSERT INTO briefs (id, kind, fingerprint, payload_json, created_at) VALUES (?,?,?,?,?)",
                       (brief_id, kind, fingerprint, _dumps(payload), time.time()))
            db.execute("DELETE FROM briefs WHERE rowid NOT IN (SELECT rowid FROM briefs ORDER BY rowid DESC LIMIT ?)",
                       (MAX_BRIEFS,))
        return brief_id

    def find_brief(self, kind: str, brief_id: Optional[str] = None,
                   fingerprint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The record with this id; failing that, the newest with this fingerprint; with neither, the newest of the kind."""
        with self._db() as db:
            row = None
            if brief_id:
                row = db.execute("SELECT * FROM briefs WHERE kind = ? AND id = ?", (kind, brief_id)).fetchone()
            if row is None and fingerprint is not None:
                row = db.execute("SELECT * FROM briefs WHERE kind = ? AND fingerprint = ? ORDER BY rowid DESC LIMIT 1",
                                 (kind, fingerprint)).fetchone()
            if row is None and not brief_id and fingerprint is None:
                row = db.execute("SELECT * FROM briefs WHERE kind = ? ORDER BY rowid DESC LIMIT 1", (kind,)).fetchone()
        payload = _json_object(row["payload_json"]) if row else None
        return {"id": row["id"], "payload": payload, "created_at": row["created_at"]} if payload is not None else None

    # ---- numbers by day (studio/rednote_analytics.py) ------------------------
    @staticmethod
    def _keep_reading(db: sqlite3.Connection, sample_id: str, read_on: str, values: Dict[str, Any], now: float) -> None:
        """Inside a transaction that already holds the write lock: what ``record_metric_snapshot`` does to one reading."""
        if not isinstance(read_on, str) or not _DAY_TEXT.fullmatch(read_on):
            raise ValueError("a reading needs the day it was read, as YYYY-MM-DD")
        given = {field: _snapshot_value(field, values[field]) for field in SNAPSHOT_FIELDS if field in values}
        if not given:
            return
        row = db.execute("SELECT * FROM metric_snapshots WHERE sample_id = ? AND read_on = ? ORDER BY rowid LIMIT 1",
                         (sample_id, read_on)).fetchone()
        held = {field: (row[field] if row else None) for field in SNAPSHOT_FIELDS}
        held.update(given)
        numbers = [held[field] for field in SNAPSHOT_FIELDS]
        if all(number is None for number in numbers):
            if row:
                db.execute("DELETE FROM metric_snapshots WHERE id = ?", (row["id"],))
        elif row:
            db.execute("UPDATE metric_snapshots SET %s, created_at = ? WHERE id = ?" % ", ".join("%s = ?" % f for f in SNAPSHOT_FIELDS),
                       numbers + [now, row["id"]])
        else:
            db.execute("INSERT INTO metric_snapshots (id, sample_id, read_on, %s, created_at) VALUES (?,?,?,%s,?)"
                       % (", ".join(SNAPSHOT_FIELDS), ",".join("?" * len(SNAPSHOT_FIELDS))),
                       [uuid.uuid4().hex[:12], sample_id, read_on] + numbers + [now])
            db.execute("DELETE FROM metric_snapshots WHERE sample_id = ? AND rowid NOT IN"
                       " (SELECT rowid FROM metric_snapshots WHERE sample_id = ? ORDER BY read_on DESC, rowid DESC LIMIT ?)",
                       (sample_id, sample_id, MAX_SNAPSHOTS_PER_SAMPLE))

    def record_metric_snapshot(self, sample_id: str, read_on: str, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Keep the numbers of a library item as they were read on one day. Returns that day's reading, or None.

        The numbers of a video keep changing, and what an item carries (``views`` and the rest) is only the last
        reading. Kept by day, two readings say how much was gained in between. ``read_on`` is the day the numbers
        were read (``YYYY-MM-DD``), never the day the video was posted and not the day this was stored.

        ``values`` names the numbers that were read (METRIC_FIELDS). A name left out was not read that day and keeps
        what the day's reading already holds; a number given as None, or as something that is no count, was cleared.
        An item has one reading a day: reading it again on the same day changes that reading. A reading left
        without any number is removed. Of one item the readings of MAX_SNAPSHOTS_PER_SAMPLE days are kept; the
        oldest days go first.

        None when the item is not in the library, or when nothing is left of that day's reading. A ``read_on`` in
        another form raises ``ValueError``: the callers check what a person typed before it gets here.
        """
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone() is None:
                return None
            self._keep_reading(db, sample_id, read_on, values, now)
            row = db.execute("SELECT * FROM metric_snapshots WHERE sample_id = ? AND read_on = ? ORDER BY rowid LIMIT 1",
                             (sample_id, read_on)).fetchone()
        return dict(row) if row else None

    def apply_readings(self, readings: List[Dict[str, Any]]) -> int:
        """Store the readings an import brings, all of them or none. Returns how many were stored.

        Each is ``{"sample_id", "read_on", "values", "current"}``. The numbers in ``values`` (METRIC_FIELDS) are kept
        as the item's reading of that day (``record_metric_snapshot``). Where ``current`` is true they also become
        the numbers the item carries, the ones every page and every ranking reads; an item made from a posted
        script keeps the day its numbers were read beside them (``metrics_on``), so that day moves with them.

        Raises ``KeyError`` when one of the items is not in the library (any more). Nothing is stored then: all of it
        is written in one transaction.
        """
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for reading in readings:
                sample_id, read_on = reading["sample_id"], reading["read_on"]
                row = db.execute("SELECT origin FROM samples WHERE id = ?", (sample_id,)).fetchone()
                if row is None:
                    raise KeyError(sample_id)
                snap_vals = {field: _snapshot_value(field, reading["values"][field])
                             for field in SNAPSHOT_FIELDS if field in reading["values"]}
                self._keep_reading(db, sample_id, read_on, snap_vals, now)
                numbers = {field: snap_vals[field] for field in METRIC_FIELDS if field in snap_vals}
                if reading.get("current") and numbers:
                    sets = ["%s = ?" % field for field in numbers] + ["updated_at = ?"]
                    values = list(numbers.values()) + [now]
                    if row["origin"] == ORIGIN_SCRIPT:
                        sets.append("metrics_on = ?"); values.append(read_on)
                    db.execute("UPDATE samples SET %s WHERE id = ?" % ", ".join(sets), values + [sample_id])
        return len(readings)

    def list_metric_snapshots(self, sample_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The readings that are kept, the oldest day first: those of one library item, or of all of them.

        Each is ``{"id", "sample_id", "read_on", "views", "likes", "collects", "comments", "created_at"}``. A number
        that was not read on that day is None.
        """
        query = "SELECT * FROM metric_snapshots %s ORDER BY read_on ASC, rowid ASC"
        with self._db() as db:
            if sample_id is None:
                rows = db.execute(query % "").fetchall()
            else:
                rows = db.execute(query % "WHERE sample_id = ?", (sample_id,)).fetchall()
        return [dict(row) for row in rows]

    # ---- what viewers wrote under a video (studio/rednote_analytics.py) -----
    def add_sample_comments(self, sample_id: str, items: List[Dict[str, Any]], *, replace: bool = False) -> int:
        """Store cleaned viewer comments for ``sample_id`` (up to 500, highest-liked and newest kept).

        No commenter names or handles are stored: ``sample_comments`` holds only the comment text, optional like
        count, word-list tag, and question flag.
        """
        now = time.time()
        cleaned: List[Tuple[Any, ...]] = []
        for raw in items[:500]:
            if not isinstance(raw, dict):
                continue
            txt = str(raw.get("text") or "").strip()[:2000]
            if not txt:
                continue
            likes = _metric(raw.get("likes"))
            sentiment = str(raw.get("sentiment") or "neutral").strip().lower()
            if sentiment not in ("positive", "neutral", "negative", "mixed"):
                sentiment = "neutral"
            is_q = 1 if bool(raw.get("is_question")) else 0
            cleaned.append((uuid.uuid4().hex[:12], sample_id, txt, likes, sentiment, is_q, now))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone() is None:
                return 0
            if replace:
                db.execute("DELETE FROM sample_comments WHERE sample_id = ?", (sample_id,))
            if cleaned:
                db.executemany(
                    "INSERT INTO sample_comments (id, sample_id, text, likes, sentiment, is_question, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    cleaned,
                )
                db.execute(
                    "DELETE FROM sample_comments WHERE sample_id = ? AND rowid NOT IN"
                    " (SELECT rowid FROM sample_comments WHERE sample_id = ? ORDER BY COALESCE(likes, 0) DESC, rowid DESC LIMIT 500)",
                    (sample_id, sample_id),
                )
        return len(cleaned)

    def list_sample_comments(self, sample_id: str) -> List[Dict[str, Any]]:
        """Stored viewer comments for ``sample_id``, highest-liked then newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM sample_comments WHERE sample_id = ? ORDER BY COALESCE(likes, 0) DESC, created_at DESC, rowid DESC",
                (sample_id,),
            ).fetchall()
        return [{
            "id": r["id"], "sample_id": r["sample_id"],
            "text": r["text"], "likes": r["likes"], "sentiment": r["sentiment"] or "neutral",
            "is_question": bool(r["is_question"]), "created_at": r["created_at"],
        } for r in rows]

    def clear_sample_comments(self, sample_id: str) -> int:
        with self._db() as db:
            db.execute("DELETE FROM sample_sentiments WHERE sample_id = ?", (sample_id,))
            return db.execute("DELETE FROM sample_comments WHERE sample_id = ?", (sample_id,)).rowcount

    def save_sample_sentiment(self, sample_id: str, summary: Dict[str, Any], source: str = "local") -> Optional[Dict[str, Any]]:
        src = source if source in ("local", "ai", "manual") else "local"
        now = time.time()
        encoded = _dumps(summary if isinstance(summary, dict) else {})
        with self._db() as db:
            if db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone() is None:
                return None
            db.execute(
                "INSERT OR REPLACE INTO sample_sentiments (sample_id, summary_json, source, updated_at) VALUES (?,?,?,?)",
                (sample_id, encoded, src, now),
            )
        return self.get_sample_sentiment(sample_id)

    def get_sample_sentiment(self, sample_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM sample_sentiments WHERE sample_id = ?", (sample_id,)).fetchone()
        if not row:
            return None
        return {
            "sample_id": row["sample_id"],
            "summary": _json_object(row["summary_json"]) or {},
            "source": row["source"] or "local",
            "updated_at": row["updated_at"],
        }

    def list_sample_sentiments(self) -> Dict[str, Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM sample_sentiments ORDER BY updated_at DESC").fetchall()
        return {
            r["sample_id"]: {
                "sample_id": r["sample_id"],
                "summary": _json_object(r["summary_json"]) or {},
                "source": r["source"] or "local",
                "updated_at": r["updated_at"],
            }
            for r in rows
        }

    # ---- beat bank (structured Stage 1B beats per video sample) ---------
    @staticmethod
    def _beat_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "sample_id": row["sample_id"],
            "beat_index": int(row["beat_index"] or 1),
            "beat_type": row["beat_type"],
            "audio_mode": row["audio_mode"] if row["audio_mode"] in BEAT_AUDIO_MODES else "VOICEOVER",
            "delivery": row["delivery"] or "",
            "start_sec": row["start_sec"],
            "end_sec": row["end_sec"],
            "spoken_text": row["spoken_text"],
            "english_meaning": row["english_meaning"] or "",
            "visual_summary": row["visual_summary"] or "",
            "created_at": row["created_at"],
        }

    def save_sample_beats(self, sample_id: str, beats: Any) -> List[Dict[str, Any]]:
        """Replace the stored beats of ``sample_id`` with validated, bounded beats whose spoken words match the item's text."""
        sample = self.get_sample(sample_id)
        if sample is None:
            return []
        sample_key = _spoken_words_key(sample.get("text") or "")
        now = time.time()
        rows_to_insert: List[Tuple[Any, ...]] = []
        raw_list = beats if isinstance(beats, (list, tuple)) else []
        for raw in raw_list:
            if len(rows_to_insert) >= MAX_BEATS_PER_SAMPLE:
                break
            if not isinstance(raw, dict):
                continue
            btype = str(raw.get("beat_type") or "").strip().upper()
            if btype not in BEAT_TYPES:
                continue
            mode = str(raw.get("audio_mode") or "").strip().upper()
            if mode not in BEAT_AUDIO_MODES:
                mode = "VOICEOVER"
            spoken = " ".join(str(raw.get("spoken_text") or "").replace("\r\n", "\n").splitlines()).strip()[:MAX_BEAT_SPOKEN_CHARS]
            spoken_key = _spoken_words_key(spoken)
            if not spoken_key or spoken_key not in sample_key:
                continue
            delivery = " ".join(str(raw.get("delivery") or "").split())[:MAX_BEAT_DELIVERY_CHARS]
            meaning = " ".join(str(raw.get("english_meaning") or "").split())[:MAX_BEAT_MEANING_CHARS]
            visual = " ".join(str(raw.get("visual_summary") or "").split())[:MAX_BEAT_VISUAL_CHARS]
            start_sec = _beat_sec(raw.get("start_sec"))
            end_sec = _beat_sec(raw.get("end_sec"))
            if start_sec is not None and end_sec is not None and end_sec <= start_sec:
                start_sec, end_sec = None, None
            beat_idx = len(rows_to_insert) + 1
            rows_to_insert.append((
                uuid.uuid4().hex[:12], sample_id, beat_idx, btype, mode, delivery,
                start_sec, end_sec, spoken, meaning, visual, now,
            ))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone() is None:
                return []
            db.execute("DELETE FROM sample_beats WHERE sample_id = ?", (sample_id,))
            if rows_to_insert:
                db.executemany(
                    "INSERT INTO sample_beats (id, sample_id, beat_index, beat_type, audio_mode, delivery,"
                    " start_sec, end_sec, spoken_text, english_meaning, visual_summary, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows_to_insert,
                )
        return self.list_sample_beats(sample_id)

    def list_sample_beats(self, sample_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Stored beats for ``sample_id`` (in video order), or across every sample when ``sample_id`` is None."""
        with self._db() as db:
            if sample_id is None:
                rows = db.execute(
                    "SELECT * FROM sample_beats ORDER BY sample_id ASC, beat_index ASC, created_at ASC, rowid ASC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM sample_beats WHERE sample_id = ? ORDER BY beat_index ASC, created_at ASC, rowid ASC",
                    (sample_id,),
                ).fetchall()
        return [self._beat_row(r) for r in rows]

    def attach_beats(self, samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return ``samples`` with ``beats`` attached on items that have stored beats (leaving other items unchanged)."""
        if not samples:
            return []
        by_sample: Dict[str, List[Dict[str, Any]]] = {}
        for beat in self.list_sample_beats():
            by_sample.setdefault(beat["sample_id"], []).append(beat)
        return [dict(s, beats=by_sample[s["id"]]) if s["id"] in by_sample else s for s in samples]

    def attach_beats_one(self, sample: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if sample is None:
            return None
        beats = self.list_sample_beats(sample["id"])
        return dict(sample, beats=beats) if beats else sample

    def list_confirmed_organic_beats(self, beat_types: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
        """Beats that pass the wording safety gate: ONLY from confirmed (``needs_review=0``), non-sponsored
        (``is_sponsored=0``) items whose current ``text`` still contains the beat's spoken words and was not
        left unchanged from an AI draft.
        """
        wanted = tuple(bt for bt in (beat_types or BEAT_TYPES) if bt in BEAT_TYPES)
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        with self._db() as db:
            rows = db.execute(
                "SELECT b.*, s.title AS sample_title, s.text AS sample_text, s.origin AS sample_origin,"
                " s.script_id AS sample_script_id, s.ai_json AS sample_ai_json, s.views AS sample_views,"
                " s.likes AS sample_likes, s.collects AS sample_collects, s.comments AS sample_comments,"
                " s.created_at AS sample_created_at"
                " FROM sample_beats b"
                " JOIN samples s ON s.id = b.sample_id"
                " WHERE s.needs_review = 0 AND s.is_sponsored = 0 AND b.beat_type IN (%s)"
                " ORDER BY s.created_at DESC, b.beat_index ASC, b.rowid ASC" % marks,
                wanted,
            ).fetchall()
        text_keys: Dict[str, str] = {}
        ai_keys_by_sample: Dict[str, set] = {}
        out: List[Dict[str, Any]] = []
        for r in rows:
            sid = r["sample_id"]
            if sid not in text_keys:
                text_keys[sid] = _spoken_words_key(r["sample_text"] or "")
                ai_set: set = set()
                if r["sample_origin"] == ORIGIN_SCRIPT and r["sample_ai_json"]:
                    ai_obj = _json_object(r["sample_ai_json"]) or {}
                    for raw_line in (ai_obj.get("lines") or []) + (ai_obj.get("proposed") or []):
                        if isinstance(raw_line, str):
                            k = _spoken_words_key(raw_line)
                            if k:
                                ai_set.add(k)
                ai_keys_by_sample[sid] = ai_set
            bkey = _spoken_words_key(r["spoken_text"])
            if not bkey or bkey not in text_keys[sid]:
                continue
            if bkey in ai_keys_by_sample[sid]:
                continue
            entry = self._beat_row(r)
            entry.update({
                "sample_title": r["sample_title"] or "",
                "sample_script_id": r["sample_script_id"],
                "sample_views": r["sample_views"],
                "sample_likes": r["sample_likes"],
                "sample_collects": r["sample_collects"],
                "sample_comments": r["sample_comments"],
                "sample_created_at": r["sample_created_at"],
            })
            out.append(entry)
        return out

    # ---- the plan: videos the creator means to make (studio/planner.py) ----
    # One row is one planned video. This part only stores: what a plan is, what may be put into one and what its
    # state is are in ``studio/planner.py``, which checks everything before it calls these methods.
    #
    # ``status`` holds what the creator said about the plan (planner.MARKS), never whether a script exists or
    # was posted: that is read from the scripts and the library each time (``planner.state_of``), so no row has
    # to be written when a script is made, posted or deleted elsewhere. ``script_id`` is the one link, set when
    # a script is made from the plan and cleared by ``delete_script``.
    #
    # The column ``priority`` is left from round 1. No page ever set it and nothing reads it. It stays in the
    # table because the layout of data version 3 is held by tests that other work changes at the same time.
    _PLAN_BASES = ("", "word_lists", "numbers", "model")
    _PLAN_TEXT_COLUMNS = ("title", "video_format", "scheduled_for", "status", "source_kind", "basis", "notes",
                          "rationale_zh", "rationale_en")

    @staticmethod
    def _plan_row(row: sqlite3.Row) -> Dict[str, Any]:
        def texts(encoded: Any) -> List[str]:
            try:
                value = loads_stored(encoded or "[]")
            except ValueError:
                return []
            return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

        return {
            "id": row["id"],
            "title": row["title"],
            "video_format": row["video_format"] or "VLOG_VOICEOVER",
            "target_sec": int(row["target_sec"] or 60),
            "scheduled_for": row["scheduled_for"] or "",
            "status": row["status"] or "PLANNED",
            "source_kind": row["source_kind"] or "manual",
            "basis": row["basis"] if ("basis" in row.keys() and row["basis"] in Store._PLAN_BASES) else "",
            "notes": row["notes"] or "",
            "rationale_zh": row["rationale_zh"] or "",
            "rationale_en": row["rationale_en"] or "",
            "keywords": texts(row["keywords_json"]),
            "questions": texts(row["questions_json"]),
            "script_id": row["script_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_video_plans(self) -> List[Dict[str, Any]]:
        """Every plan as it is stored: those with a day first, the earliest day first, then the rest, newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM video_plans ORDER BY"
                " CASE WHEN scheduled_for != '' THEN 0 ELSE 1 END ASC,"
                " scheduled_for ASC, created_at DESC, rowid DESC"
            ).fetchall()
        return [self._plan_row(r) for r in rows]

    def plans_count(self) -> int:
        """How many plans are not posted yet: those with no script, or with a script that has no item in the library."""
        with self._db() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM video_plans WHERE script_id IS NULL"
                " OR script_id NOT IN (SELECT script_id FROM samples WHERE script_id IS NOT NULL)").fetchone()[0])

    def plans_without_script(self) -> int:
        """How many plans have no script yet: what ``planner.MAX_OPEN_PLANS`` counts."""
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM video_plans WHERE script_id IS NULL").fetchone()[0])

    def get_video_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM video_plans WHERE id = ?", (plan_id,)).fetchone()
        return self._plan_row(row) if row else None

    def create_video_plans(self, plans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Store several new plans in one step: all of them, or, when one cannot be stored, none.

        Each is a dict with ``title`` and any of ``video_format``, ``target_sec``, ``scheduled_for``, ``status``,
        ``source_kind``, ``notes``, ``rationale_zh``, ``rationale_en``, ``keywords``, ``questions``, ``script_id``.
        Returns them as stored, in the order given.
        """
        now = time.time()
        ids: List[str] = []
        with self._db() as db:
            for offset, plan in enumerate(plans):
                plan_id = uuid.uuid4().hex[:12]
                ids.append(plan_id)
                script_id = plan.get("script_id")
                stamp = now + (len(plans) - offset) * 0.000001      # the first given stands first where the newest is shown first
                db.execute(
                    "INSERT INTO video_plans (id, title, video_format, target_sec, scheduled_for, status,"
                    " source_kind, notes, rationale_zh, rationale_en, keywords_json, questions_json, script_id,"
                    " created_at, updated_at, basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (plan_id, str(plan["title"]), str(plan.get("video_format") or "VLOG_VOICEOVER"),
                     int(plan.get("target_sec") or 60), str(plan.get("scheduled_for") or ""),
                     str(plan.get("status") or "PLANNED"), str(plan.get("source_kind") or "manual"),
                     str(plan.get("notes") or ""), str(plan.get("rationale_zh") or ""),
                     str(plan.get("rationale_en") or ""), _dumps(normalize_keywords(plan.get("keywords"))),
                     _dumps([q for q in plan.get("questions") or [] if isinstance(q, str)]),
                     script_id if isinstance(script_id, str) and script_id else None, stamp, stamp,
                     plan.get("basis") if plan.get("basis") in Store._PLAN_BASES else ""),
                )
            rows = {row["id"]: row for row in db.execute(
                "SELECT * FROM video_plans WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)} if ids else {}
        return [self._plan_row(rows[plan_id]) for plan_id in ids]

    def create_video_plan(self, *, title: str, video_format: str = "VLOG_VOICEOVER", target_sec: int = 60,
                          scheduled_for: str = "", status: str = "PLANNED", source_kind: str = "manual",
                          basis: str = "", notes: str = "", rationale_zh: str = "", rationale_en: str = "",
                          keywords: Any = None, questions: Any = None,
                          script_id: Optional[str] = None) -> Dict[str, Any]:
        """Store one new plan (``create_video_plans``)."""
        return self.create_video_plans([{
            "title": title, "video_format": video_format, "target_sec": target_sec, "scheduled_for": scheduled_for,
            "status": status, "source_kind": source_kind, "basis": basis, "notes": notes, "rationale_zh": rationale_zh,
            "rationale_en": rationale_en, "keywords": keywords, "questions": questions, "script_id": script_id}])[0]

    def update_video_plan(self, plan_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Write new values into a plan's row and return the row, or None when there is no such plan.

        ``changes`` may name the text columns, ``target_sec``, ``keywords`` and ``questions``; anything else in
        it is left alone. The link to a script is not changed here (``link_plan_script``).
        """
        sets: List[str] = []
        values: List[Any] = []
        for column in self._PLAN_TEXT_COLUMNS:
            if isinstance(changes.get(column), str):
                sets.append("%s = ?" % column)
                values.append(changes[column])
        if isinstance(changes.get("target_sec"), int) and not isinstance(changes.get("target_sec"), bool):
            sets.append("target_sec = ?")
            values.append(changes["target_sec"])
        if "keywords" in changes:
            sets.append("keywords_json = ?")
            values.append(_dumps(normalize_keywords(changes["keywords"])))
        if isinstance(changes.get("questions"), list):
            sets.append("questions_json = ?")
            values.append(_dumps([q for q in changes["questions"] if isinstance(q, str)]))
        if sets:
            with self._db() as db:
                db.execute("UPDATE video_plans SET %s, updated_at = ? WHERE id = ?" % ", ".join(sets),
                           values + [time.time(), plan_id])
        return self.get_video_plan(plan_id)

    def link_plan_script(self, plan_id: str, script_id: str) -> bool:
        """Say that this script was made from this plan. False, and nothing written, when the plan is gone,
        already has a script that exists, or the script is gone or belongs to another plan: one plan has one
        script and one script one plan, however many windows ask at the same moment."""
        with self._db() as db:
            return db.execute(
                "UPDATE video_plans SET script_id = ?, updated_at = ? WHERE id = ?"
                " AND (script_id IS NULL OR script_id NOT IN (SELECT id FROM scripts))"
                " AND EXISTS (SELECT 1 FROM scripts WHERE id = ?)"
                " AND NOT EXISTS (SELECT 1 FROM video_plans WHERE script_id = ? AND id != ?)",
                (script_id, time.time(), plan_id, script_id, script_id, plan_id)).rowcount > 0

    def delete_video_plan(self, plan_id: str) -> bool:
        """Remove a plan, and with it the trend keywords that were suggested for it (``plan_suggestions``).

        A script made from it is the creator's work and stays.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM plan_suggestions WHERE plan_id = ?", (plan_id,))
            return db.execute("DELETE FROM video_plans WHERE id = ?", (plan_id,)).rowcount > 0

    # ---- trend keywords suggested for a plan (studio/autosuggest.py) -------
    # One row is one tracked trend keyword offered for one plan, with why it fits: the pieces of the keyword that
    # were found (``matched``), in which of the plan's title, notes and questions (``matched_in``), which share of
    # the keyword that is (``relevance``), and the keyword's score and source when it was offered. ``rising`` and
    # ``sightings`` are NULL unless the trend list said them. This part only stores; ``studio/autosuggest.py``
    # decides what is offered.
    #
    # ``status`` is ``open`` until the creator takes the keyword (``taken``) or dismisses it (``dismissed``), or
    # the run that offered it is undone (``withdrawn``). A plan is offered a keyword at most once (the UNIQUE
    # rule), so one that was dismissed or withdrawn is not offered for that plan again. Nothing here changes a
    # plan: taking a keyword is ``planner.change``, asked for by the creator.
    MAX_SUGGESTIONS_LISTED = 5000       # rows handed out at once, whatever a database from elsewhere holds

    @staticmethod
    def _suggestion_row(row: sqlite3.Row) -> Dict[str, Any]:
        def text(value: Any) -> str:
            return value if isinstance(value, str) else ""

        def number(value: Any) -> Optional[float]:
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

        def whole(value: Any) -> Optional[int]:
            return int(number(value)) if number(value) is not None else None

        return {"id": text(row["id"]), "plan_id": text(row["plan_id"]), "keyword": text(row["keyword"]),
                "keyword_key": text(row["keyword_key"]), "matched": text(row["matched"]),
                "matched_in": [part for part in text(row["matched_in"]).split(",") if part],
                "relevance": number(row["relevance"]),
                "score": whole(row["score"]), "source": text(row["source"]) or "manual",
                "rising": None if row["rising"] is None else bool(row["rising"]), "sightings": whole(row["sightings"]),
                "status": text(row["status"]), "run_id": text(row["run_id"]),
                "created_at": row["created_at"], "decided_at": row["decided_at"]}

    def list_plan_suggestions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """The suggestions that are kept, or those in one state, the oldest first, at most MAX_SUGGESTIONS_LISTED."""
        with self._db() as db:
            if status is None:
                rows = db.execute("SELECT * FROM plan_suggestions ORDER BY created_at ASC, rowid ASC LIMIT ?",
                                  (self.MAX_SUGGESTIONS_LISTED,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM plan_suggestions WHERE status = ? ORDER BY created_at ASC, rowid ASC LIMIT ?",
                                  (status, self.MAX_SUGGESTIONS_LISTED)).fetchall()
        return [self._suggestion_row(row) for row in rows]

    def get_plan_suggestion(self, suggestion_id: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(suggestion_id, str):
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM plan_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return self._suggestion_row(row) if row else None

    def add_plan_suggestions(self, rows: List[Dict[str, Any]]) -> List[str]:
        """Keep new suggestions, all in one step, and return the ids of those that were kept.

        Each row holds ``id``, ``plan_id``, ``keyword``, ``keyword_key``, ``matched``, ``matched_in`` (a list),
        ``relevance``, ``score``, ``source``, ``rising``, ``sightings``, ``run_id`` and ``created_at``. One for a
        plan that is gone, or for a keyword that plan was offered before, is left out and not counted.
        """
        kept: List[str] = []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                rising, sightings = row.get("rising"), row.get("sightings")
                added = db.execute(
                    "INSERT OR IGNORE INTO plan_suggestions (id, plan_id, keyword, keyword_key, matched, matched_in,"
                    " relevance, score, source, rising, sightings, run_id, created_at)"
                    " SELECT ?,?,?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM video_plans WHERE id = ?)",
                    (row["id"], row["plan_id"], row["keyword"], row["keyword_key"], str(row.get("matched") or ""),
                     ",".join(row.get("matched_in") or []), row.get("relevance"), row.get("score"),
                     str(row.get("source") or "manual"), None if rising is None else (1 if rising else 0),
                     sightings if isinstance(sightings, int) and not isinstance(sightings, bool) else None,
                     str(row.get("run_id") or ""), float(row["created_at"]), row["plan_id"])).rowcount
                if added:
                    kept.append(row["id"])
        return kept

    def decide_plan_suggestion(self, suggestion_id: str, status: str, now: float) -> bool:
        """Mark an open suggestion as ``taken`` or ``dismissed``. False, and nothing written, when it is not open."""
        with self._db() as db:
            return db.execute("UPDATE plan_suggestions SET status = ?, decided_at = ? WHERE id = ? AND status = 'open'",
                              (status, float(now), suggestion_id)).rowcount > 0

    def withdraw_plan_suggestions(self, ids: List[str]) -> int:
        """Mark those of ``ids`` that are still open as ``withdrawn`` and return how many that were.

        This is how a run that offered them is undone. The rows stay, so the same keyword is not offered for
        the same plan by the next run. One the creator took or dismissed stays as it is: it is the creator's
        decision by then.
        """
        wanted = [one for one in ids if isinstance(one, str)][:1000]
        if not wanted:
            return 0
        with self._db() as db:
            return db.execute("UPDATE plan_suggestions SET status = 'withdrawn' WHERE status = 'open' AND id IN (%s)"
                              % ",".join("?" * len(wanted)), wanted).rowcount
    # ---- brand briefs for sponsored scripts (studio/sponsored.py) --------
    # A brief has a table of its own (``sponsor_briefs``) and nothing below reads or writes a script's row. So
    # nothing that hands out a script hands out a brief, and none of this moves a script's ``updated_at``.
    # Only ``studio.sponsored`` gives the stored values a meaning and checks them; this only keeps them.
    @staticmethod
    def _sponsor_row(row: sqlite3.Row) -> Dict[str, Any]:
        """A brief as it is handed out. A stored value that cannot be read, or is of another kind than this app
        writes there, comes back empty (no reading, no choice, no ticks): it never stops the brief being shown."""
        def stored(text: Any) -> Dict[str, Any]:
            return _json_object(text) or {}

        return {"id": row["id"], "title": row["title"] or "", "source_name": row["source_name"] or "",
                "brief": row["brief"] if isinstance(row["brief"], str) else "",
                "reading": stored(row["reading_json"]) or None, "choice": stored(row["choice_json"]) or None,
                "ticks": stored(row["ticks_json"]), "script_id": row["script_id"] if isinstance(row["script_id"], str) else None,
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def _sponsor_db(self) -> sqlite3.Connection:
        """A connection for changing ``sponsor_briefs``: what a change removes there is overwritten, not only unlisted.

        SQLite leaves the bytes of a deleted or replaced row in the file until the space is used again, and a
        backup copies the file page by page. A brief is a brand's confidential text: once the creator deletes
        it, it is not to be readable in the data folder any more, nor to travel in a backup made afterwards.
        ``secure_delete`` makes SQLite write zeros over what it frees.
        """
        db = self._db()
        db.execute("PRAGMA secure_delete = ON").fetchall()
        return db

    def list_sponsor_briefs(self) -> List[Dict[str, Any]]:
        """The briefs that are kept, newest first, each without its text: ``{"id", "title", "source_name",
        "chars", "has_reading", "has_choice", "script_id", "created_at", "updated_at"}``.

        ``chars`` is how long the brief is. At most four times MAX_SPONSOR_BRIEFS are listed: no more than
        MAX_SPONSOR_BRIEFS can be added here, and a database from elsewhere must not make the list endless.
        """
        with self._db() as db:
            rows = db.execute(
                "SELECT id, title, source_name, LENGTH(brief) AS chars, reading_json IS NOT NULL AS has_reading,"
                " choice_json IS NOT NULL AS has_choice, script_id, created_at, updated_at FROM sponsor_briefs"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?", (MAX_SPONSOR_BRIEFS * 4,)).fetchall()
        return [{"id": row["id"], "title": row["title"] or "", "source_name": row["source_name"] or "",
                 "chars": int(row["chars"] or 0), "has_reading": bool(row["has_reading"]), "has_choice": bool(row["has_choice"]),
                 "script_id": row["script_id"] if isinstance(row["script_id"], str) else None,
                 "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    def count_sponsor_briefs(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM sponsor_briefs").fetchone()[0])

    def get_sponsor_brief(self, brief_id: Any) -> Optional[Dict[str, Any]]:
        """One brief with what was made of it, or None when there is none with this id.

        ``{"id", "title", "source_name", "brief", "reading", "choice", "ticks", "script_id", "created_at",
        "updated_at"}``. ``brief`` is the brand's text as it was added. ``reading`` is what a model made of it
        (None until one did), ``choice`` what the creator decided (None until they did), ``ticks`` the creator's
        own ticks on the list of the script drafted from it, and ``script_id`` that script (None when none was
        drafted, or when it was deleted).
        """
        if not isinstance(brief_id, str):
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM sponsor_briefs WHERE id = ?", (brief_id,)).fetchone()
        return self._sponsor_row(row) if row else None

    def sponsor_brief_of_script(self, script_id: Any) -> Optional[Dict[str, Any]]:
        """The brief this script was drafted from, or None: it was not drafted from one, or that brief was deleted."""
        if not isinstance(script_id, str) or not script_id:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM sponsor_briefs WHERE script_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                             (script_id,)).fetchone()
        return self._sponsor_row(row) if row else None

    def create_sponsor_brief(self, *, title: str, brief: str, source_name: str = "",
                             limit: int = MAX_SPONSOR_BRIEFS) -> Optional[Dict[str, Any]]:
        """Keep a brand's brief. Returns it, or None when ``limit`` briefs are kept already: none is dropped to make room.

        Counting and adding are one step, so two windows that add at the same moment cannot get past the
        limit between them. ``studio.sponsored`` checks the text and its length before it comes here.
        """
        brief_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM sponsor_briefs").fetchone()[0] >= limit:
                return None
            db.execute("INSERT INTO sponsor_briefs (id, title, source_name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                       (brief_id, str(title or ""), clean_file_name(source_name), str(brief or ""), now, now))
        return self.get_sponsor_brief(brief_id)

    def save_sponsor_brief(self, brief_id: str, *, reading: Any = _KEEP, choice: Any = _KEEP, ticks: Any = _KEEP,
                           script_id: Any = _KEEP) -> Optional[Dict[str, Any]]:
        """Change what is kept with a brief. Returns it, or None when there is no brief with this id.

        Only what is given is written; everything else stays as stored. ``reading`` and ``choice`` take an
        object, or None to remove what was there; ``ticks`` an object; ``script_id`` the id of the script drafted
        from the brief, or None. The brief's own text is never changed: to change it, the creator deletes the
        brief and adds it again, so a reading always belongs to the text it is shown beside.
        """
        sets: List[str] = []
        values: List[Any] = []
        if reading is not _KEEP:
            sets.append("reading_json = ?"); values.append(_dumps(reading) if isinstance(reading, dict) else None)
        if choice is not _KEEP:
            sets.append("choice_json = ?"); values.append(_dumps(choice) if isinstance(choice, dict) else None)
        if ticks is not _KEEP:
            sets.append("ticks_json = ?"); values.append(_dumps(ticks if isinstance(ticks, dict) else {}))
        if script_id is not _KEEP:
            sets.append("script_id = ?"); values.append(script_id if isinstance(script_id, str) and script_id else None)
        if sets:
            sets.append("updated_at = ?"); values.append(time.time())
            with self._sponsor_db() as db:        # a reading or a choice that is replaced quotes the brief: it is overwritten
                db.execute("UPDATE sponsor_briefs SET %s WHERE id = ?" % ", ".join(sets), values + [brief_id])
        return self.get_sponsor_brief(brief_id)

    def delete_sponsor_brief(self, brief_id: str) -> bool:
        """Delete a brief with everything made of it: the reading, the choice and the ticks. True when there was one.

        A script drafted from it stays, with its lines, and stays a sponsored script (``mode``): only the list
        that stood beside it is gone. The row's bytes are overwritten in the file (``_sponsor_db``), so the text
        is not in a backup made afterwards either. A backup made while the brief was kept still holds it.
        """
        with self._sponsor_db() as db:
            db.execute("DELETE FROM briefs WHERE kind = 'sponsor_draft' AND fingerprint = ?", (brief_id,))
            return db.execute("DELETE FROM sponsor_briefs WHERE id = ?", (brief_id,)).rowcount > 0

    # ---- watched folder files & non-sponsored AI drafts (studio/autovoice.py) ----
    @staticmethod
    def _watched_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "path": row["path"],
            "size_bytes": int(row["size_bytes"]),
            "mtime_ns": int(row["mtime_ns"]),
            "first_seen_at": float(row["first_seen_at"]),
            "settled_at": float(row["settled_at"]) if row["settled_at"] is not None else None,
            "status": row["status"],
            "kind": row["kind"],
            "sample_id": row["sample_id"],
            "is_sponsored": bool(row["is_sponsored"]),
            "error_text": row["error_text"] or "",
            "updated_at": float(row["updated_at"]),
        }

    def list_watched_files(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with contextlib.closing(self._db()) as db:
            if limit is None:
                rows = db.execute("SELECT * FROM watched_files ORDER BY updated_at DESC, rowid DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM watched_files ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
        return [self._watched_row(r) for r in rows]

    def get_watched_file(self, watched_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(watched_id, str) or not watched_id:
            return None
        with contextlib.closing(self._db()) as db:
            row = db.execute("SELECT * FROM watched_files WHERE id = ?", (watched_id,)).fetchone()
        return self._watched_row(row) if row is not None else None

    def get_lesson(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(lesson_id, str) or not lesson_id:
            return None
        with contextlib.closing(self._db()) as db:
            row = db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        return self._lesson_row(row) if row is not None else None

    def list_organic_ai_drafts(self) -> List[Dict[str, Any]]:
        """Every non-sponsored AI-drafted script as ``{"id", "ai_lines", "lines"}``, oldest first."""
        with contextlib.closing(self._db()) as db:
            rows = db.execute(
                "SELECT id, ai_lines_json, lines_json FROM scripts"
                " WHERE ai_lines_json IS NOT NULL AND mode != ? ORDER BY created_at, rowid",
                (MODE_SPONSORED,),
            ).fetchall()
        return [
            {"id": r["id"], "ai_lines": loads_stored(r["ai_lines_json"]), "lines": loads_stored(r["lines_json"])}
            for r in rows
        ]
