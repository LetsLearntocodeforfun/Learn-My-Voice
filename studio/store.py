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

import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA_PATH = ROOT / "schemas" / "voice_dna.example.json"
CLOUD_PROVIDERS = ("gemini", "openai")
PROVIDERS = CLOUD_PROVIDERS + ("local",)
MANUAL_PROVIDER = "manual"  # copy-and-paste with a chat subscription: no key, no model
METRIC_FIELDS = ("views", "likes", "collects", "comments")
MAX_KEYWORD_CHARS = 40
MAX_KEYWORDS_PER_SCRIPT = 8
SAMPLE_KINDS = ("script", "subtitle", "video")   # typed or pasted text; a subtitle file; a video transcribed here
MAX_SOURCE_NAME_CHARS = 200
MAX_BRIEFS = 200            # records of what a prompt contained; the oldest are dropped
# The text of one library item, in characters. No real script or transcript comes near it: at a fast 300
# characters a minute this is more than 27 hours of speech in Chinese, and about 9 hours in English. It is
# there so that one file somebody handed the creator cannot make every page slow or fill the database.
MAX_SAMPLE_CHARS = 500000

_CONFIG_LOCK = threading.RLock()    # one read-change-save of the settings at a time (Store.update_config)
_DNA_LOCK = threading.RLock()       # one replacement of the active voice profile at a time
# Copies of earlier voice profiles: voice_dna-<date>-<time>[-<n>].json, or .unreadable for one that could not be read.
_HISTORY_FILE = re.compile(r"voice_dna-([0-9]{8}-[0-9]{6})(?:-([0-9]{1,6}))?\.(json|unreadable)")
HISTORY_ID = r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,6})?"     # the id of an earlier profile: the part of the name between "voice_dna-" and ".json"


class TextTooLong(ValueError):
    """The text of a library item is over MAX_SAMPLE_CHARS. Raised before anything is stored.

    ``code`` is what a page or an MCP tool can map; ``message`` (English) and ``message_zh`` are written to be shown.
    """

    code = "text_too_long"

    def __init__(self, length: int, limit: Optional[int] = None) -> None:
        self.length, self.limit = length, MAX_SAMPLE_CHARS if limit is None else limit
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
"""

# Columns added after a table first shipped: (table, column, definition). A database created by an
# earlier version gets them in place when it is opened; nothing already stored is touched.
_ADDED_COLUMNS = (
    ("scripts", "keywords_json", "TEXT NOT NULL DEFAULT '[]'"),    # before trend keywords existed
    ("scripts", "basis_json", "TEXT"),                             # before drafts recorded what they were based on
    ("samples", "kind", "TEXT NOT NULL DEFAULT 'script'"),         # before subtitle files and videos could be added
    ("samples", "timing_json", "TEXT"),
    ("samples", "source_name", "TEXT NOT NULL DEFAULT ''"),
    ("samples", "needs_review", "INTEGER NOT NULL DEFAULT 0"),     # before machine transcripts had to be confirmed
    ("samples", "reviewed_at", "REAL"),
)
# What has to happen once, together with adding a column. A video stored before ``needs_review`` existed
# was never confirmed by the creator, so it starts out waiting like any video added from now on.
_AFTER_ADDING = {
    ("samples", "needs_review"): ("UPDATE samples SET needs_review = 1 WHERE kind = 'video'",),
}


def normalize_lines(lines: Any) -> List[Dict[str, Any]]:
    """Coerce arbitrary input into the Stage 4 line shape, renumbered from 1."""
    out: List[Dict[str, Any]] = []
    for raw in lines if isinstance(lines, list) else []:
        if not isinstance(raw, dict):
            continue
        line: Dict[str, Any] = {"line_no": len(out) + 1}
        for field in LINE_FIELDS:
            value = raw.get(field, "")
            line[field] = value if isinstance(value, str) else ("" if value is None else str(value))
        line["audio_mode"] = line["audio_mode"] or "VOICEOVER"
        out.append(line)
    return out


def _metric(value: Any) -> Optional[int]:
    """A non-negative count, or None for blank / invalid input (a number that is not finite or too large to store included)."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number < 2 ** 63 else None


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


def loads_stored(text: str) -> Any:
    """Read JSON that this app stored earlier. ``NaN`` and ``Infinity`` come back as None (``null``).

    Nothing writes them any more (``_dumps``; replies and requests that contain them are refused when they
    are read). A version before 0.2.0 could store them from a model's reply, and a browser cannot read an
    answer that contains them: the page for that script or profile showed nothing, not even the button to
    delete it. Read as ``null`` they can be shown, edited and removed. The same goes for a number written
    so large that it would be read as Infinity (``1e999``).
    """
    return json.loads(text, parse_constant=_not_a_number, parse_float=_finite_or_none)


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


def _owner_only(root: Path) -> None:
    """Take group and other access away from ``root`` and from every folder and file in it.

    Run each time the data folder is opened, so that a folder an earlier version created (0755, with files
    0644) is closed as well, and so is anything put there since. Only the group and other bits are cleared;
    what the owner may do stays as it was. Symbolic links are neither changed nor followed: what a link
    points at may lie outside the folder and is not this app's to alter.
    """
    def close(path: str) -> None:
        try:
            info = os.lstat(path)
            if (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) and info.st_mode & 0o077:
                os.chmod(path, stat.S_IMODE(info.st_mode) & ~0o077)
        except OSError:
            pass                      # gone in the meantime, or not ours to change

    close(str(root))
    for folder, folders, files in os.walk(str(root)):     # os.walk does not descend into linked folders
        for name in folders + files:
            close(os.path.join(folder, name))


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
        with self._db() as db:
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
        # Last, so the database just created is covered too. Until then a new one sits in a folder that is
        # already closed; a folder an earlier version left open is closed here, with everything in it.
        _owner_only(self.data_dir)

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set:
        return {row["name"] for row in db.execute("PRAGMA table_info(%s)" % table)}

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
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
            os.replace(tmp, str(path))
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
    def load_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {"provider": "", "model": "", "keys": {}, "local_base_url": "", "trend_weight": "balanced"}
        try:
            loaded = self._read_stored(self.config_path)
            if isinstance(loaded, dict):
                config.update(loaded)
        except (FileNotFoundError, ValueError):
            pass
        if not isinstance(config.get("keys"), dict):
            config["keys"] = {}
        return config

    def save_config(self, config: Dict[str, Any]) -> None:
        self._write_json(self.config_path, config, mode=0o600)

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

    def public_settings(self) -> Dict[str, Any]:
        """Settings safe to send to the browser: no key material."""
        config = self.load_config()
        local_url = config.get("local_base_url") or ""
        keys = {p: bool(config["keys"].get(p)) for p in CLOUD_PROVIDERS}
        keys["local"] = bool(local_url)
        provider, model = config.get("provider") or "", config.get("model") or ""
        return {
            "provider": provider,
            "model": model,
            "keys": keys,
            "local_base_url": local_url,
            "manual": provider == MANUAL_PROVIDER,
            "configured": provider == MANUAL_PROVIDER or bool(provider in PROVIDERS and model and keys.get(provider)),
        }

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
        self._write_json(self.recommendations_path, payload)

    # ---- imported material ---------------------------------------------
    @staticmethod
    def _sample_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "text": row["text"],
            "is_sponsored": bool(row["is_sponsored"]),
            "views": row["views"], "likes": row["likes"], "collects": row["collects"], "comments": row["comments"],
            "kind": row["kind"] if row["kind"] in SAMPLE_KINDS else SAMPLE_KINDS[0],
            "timing": _json_object(row["timing_json"]), "source_name": row["source_name"] or "",
            "needs_review": bool(row["needs_review"]), "reviewed_at": row["reviewed_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_samples(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM samples ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._sample_row(r) for r in rows]

    def get_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._sample_row(row) if row else None

    def create_sample(self, *, title: str, text: str, is_sponsored: bool = False,
                      metrics: Optional[Dict[str, Any]] = None, kind: str = "script",
                      timing: Optional[Dict[str, Any]] = None, source_name: str = "") -> Dict[str, Any]:
        """Add an item to the library.

        ``kind`` says how it came in (see SAMPLE_KINDS). ``timing`` is the dict
        from ``timing.timing_stats`` for material that has timestamps, else
        None; it is a measurement, so it is set here once and never edited.
        ``source_name`` is the name of the file it came from, kept for display only.

        A ``video`` item is created with ``needs_review`` true: its words were
        written by a transcriber, not by the creator, and wait for the creator
        to read and confirm them (``update_sample``). Scripts and subtitle
        files are the creator's own text and do not wait.

        Text longer than MAX_SAMPLE_CHARS raises ``TextTooLong`` and nothing is stored.
        """
        text = text.replace("\r\n", "\n").strip()
        if len(text) > MAX_SAMPLE_CHARS:
            raise TextTooLong(len(text))
        first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
        title = (title or "").strip() or first_line[:20] or "未命名"
        metrics = metrics or {}
        kind = kind if kind in SAMPLE_KINDS else SAMPLE_KINDS[0]
        now = time.time()
        sample_id = uuid.uuid4().hex[:12]
        with self._db() as db:
            db.execute(
                "INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments,"
                " kind, timing_json, source_name, needs_review, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sample_id, title, text, int(bool(is_sponsored)),
                 _metric(metrics.get("views")), _metric(metrics.get("likes")),
                 _metric(metrics.get("collects")), _metric(metrics.get("comments")),
                 kind, _dumps(timing) if isinstance(timing, dict) else None,
                 clean_file_name(source_name), int(kind == "video"), now, now),
            )
        return self.get_sample(sample_id)  # type: ignore[return-value]

    def update_sample(self, sample_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply ``changes`` (unknown keys are ignored; ``kind`` and ``timing`` cannot be changed).

        ``needs_review`` changes only when it is given as a boolean. Editing the
        text does not confirm an item: confirming is its own act. ``reviewed_at``
        is the moment an item that was waiting got confirmed, and is cleared if it is put back to waiting.

        Text longer than MAX_SAMPLE_CHARS raises ``TextTooLong`` and none of ``changes`` is applied.
        """
        sets: List[str] = []
        values: List[Any] = []
        now = time.time()
        if isinstance(changes.get("title"), str):
            sets.append("title = ?"); values.append(changes["title"].strip() or "未命名")
        if isinstance(changes.get("text"), str):
            text = changes["text"].replace("\r\n", "\n").strip()
            if len(text) > MAX_SAMPLE_CHARS:
                raise TextTooLong(len(text))
            sets.append("text = ?"); values.append(text)
        if "is_sponsored" in changes:
            sets.append("is_sponsored = ?"); values.append(int(bool(changes["is_sponsored"])))
        for field in METRIC_FIELDS:
            if field in changes:
                sets.append("%s = ?" % field); values.append(_metric(changes[field]))
        if changes.get("needs_review") is True:
            sets.append("needs_review = 1"); sets.append("reviewed_at = NULL")
        elif changes.get("needs_review") is False:
            # Every right-hand side reads the row as it was, so the time is set only on the change from waiting.
            sets.append("needs_review = 0")
            sets.append("reviewed_at = CASE WHEN needs_review = 1 THEN ? ELSE reviewed_at END"); values.append(now)
        if sets:
            sets.append("updated_at = ?"); values.append(now)
            values.append(sample_id)
            with self._db() as db:
                db.execute("UPDATE samples SET %s WHERE id = ?" % ", ".join(sets), values)
        return self.get_sample(sample_id)

    def delete_sample(self, sample_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM samples WHERE id = ?", (sample_id,)).rowcount > 0

    # ---- trend keywords ---------------------------------------------------
    @staticmethod
    def _trend_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in ("id", "keyword", "heat", "source", "source_url", "note_zh", "note_en",
                                          "created_at", "updated_at")}

    def list_trends(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM trends ORDER BY updated_at DESC, rowid DESC").fetchall()
        return [self._trend_row(r) for r in rows]

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
                row = db.execute("SELECT * FROM trends WHERE key = ?", (key,)).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO trends (id, key, keyword, heat, source, source_url, note_zh, note_en,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], key, keyword, heat, source, str(item.get("source_url") or ""),
                         str(item.get("note_zh") or ""), str(item.get("note_en") or ""), now, seen))
                elif row["source"] == "manual" and source != "manual":
                    continue                    # the creator's own keyword: an AI naming it changes nothing on it
                elif source == "manual" and row["source"] != "manual":
                    # The creator typed a keyword an AI had found. It is theirs now, and carries nothing the AI wrote.
                    db.execute(
                        "UPDATE trends SET heat = ?, source = 'manual', source_url = ?, note_zh = ?, note_en = ?,"
                        " updated_at = ? WHERE id = ?",
                        (heat if heat is not None else row["heat"], str(item.get("source_url") or ""),
                         str(item.get("note_zh") or ""), str(item.get("note_en") or ""),
                         max(seen, row["updated_at"]), row["id"]))
                else:
                    db.execute(
                        "UPDATE trends SET heat = ?, source = ?, source_url = ?, note_zh = ?, note_en = ?,"
                        " updated_at = ? WHERE id = ?",
                        (heat if heat is not None else row["heat"], source,
                         str(item.get("source_url") or row["source_url"]), str(item.get("note_zh") or row["note_zh"]),
                         str(item.get("note_en") or row["note_en"]), max(seen, row["updated_at"]), row["id"]))
                stored += 1
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
            rows = db.execute("SELECT * FROM scripts ORDER BY updated_at DESC").fetchall()
        out = []
        for row in rows:
            lines = loads_stored(row["lines_json"])
            preview = next((l.get("spoken_script_zh", "") for l in lines if l.get("spoken_script_zh")), "")
            out.append({
                "id": row["id"], "title": row["title"], "target_sec": row["target_sec"],
                "status": row["status"], "updated_at": row["updated_at"],
                "line_count": len(lines), "preview": preview[:60],
            })
        return out

    def get_script(self, script_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        return self._row(row) if row else None

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
                      basis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Save a new script. ``from_ai`` keeps the lines as the AI's draft, for later comparison with the
        creator's version; ``basis`` (what that draft's prompt contained) is only kept for an AI draft."""
        normalized = normalize_lines(lines) or normalize_lines([{}])
        now = time.time()
        script_id = uuid.uuid4().hex[:12]
        first_note = next((l.strip() for l in notes.splitlines() if l.strip()), "")
        title = title.strip() or first_note[:16] or default_title
        lines_json = _dumps(normalized)
        basis_json = _dumps(basis) if (from_ai and isinstance(basis, dict)) else None
        with self._db() as db:
            db.execute(
                "INSERT INTO scripts (id, title, video_format, target_sec, notes, lines_json, post_json,"
                " ai_lines_json, keywords_json, basis_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (script_id, title, video_format, int(target_sec), notes, lines_json,
                 _dumps(post or {}), lines_json if from_ai else None,
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
        sets: List[str] = []
        values: List[Any] = []
        if isinstance(changes.get("title"), str):
            sets.append("title = ?"); values.append(changes["title"].strip() or "未命名脚本")
        if isinstance(changes.get("video_format"), str):
            sets.append("video_format = ?"); values.append(changes["video_format"])
        if isinstance(changes.get("status"), str):
            sets.append("status = ?"); values.append(changes["status"])
        target = changes.get("target_sec")
        if isinstance(target, (int, float)) and (not isinstance(target, float) or math.isfinite(target)):
            sets.append("target_sec = ?"); values.append(max(5, min(int(target), 900)))
        if "lines" in changes:
            lines_json = _dumps(normalize_lines(changes["lines"]))
            sets.append("lines_json = ?"); values.append(lines_json)
            if by_ai:
                sets.append("ai_lines_json = CASE WHEN ai_lines_json IS NULL THEN NULL ELSE ? END"); values.append(lines_json)
        if isinstance(changes.get("post"), dict):
            sets.append("post_json = ?"); values.append(_dumps(changes["post"]))
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
        with self._db() as db:
            return db.execute("DELETE FROM scripts WHERE id = ?", (script_id,)).rowcount > 0

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
