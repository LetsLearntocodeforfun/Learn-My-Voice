"""Back up everything the creator made or taught the app into one file, and restore such a file.

What is in a backup, and nothing else (the names are the ones the app itself writes into its data folder):

- ``studio.db``: every script with the AI's draft it began as and what that draft was based on, the library
  with its numbers and timings, the tracked keywords, the lessons from the creator's edits, the records of
  what prompts contained, and the texts an AI suggested for lines;
- ``creator.json``, ``voice_dna.json``, ``voice_pending.json``, ``recommendations.json``: the creator profile,
  the voice profile in use, the proposal waiting for a decision, and the latest topic ideas;
- ``voice_dna_history/``: the earlier voice profiles;
- ``settings.json``: the two settings the creator taught the app (``CARRIED_SETTINGS``): how keywords are
  ranked, and the changes to the word list.

What is never in one: API keys and the rest of the AI connection (the service, the model, the address of a
local model), what transcription set up, uploads waiting to be transcribed, exported files, logs, and the
automatic backups themselves. A backup gets mailed and put on cloud drives, so it holds no key; and the AI
connection belongs to the computer, so a restore leaves the one that is there exactly as it is. A backup can
therefore never change where the creator's text is sent.

The file is a zip archive with its own ending (``.lmvbackup``: some browsers unpack a ``.zip`` as soon as it
is downloaded, and then there is no longer one file to choose). ``manifest.json`` in it lists every other
file with its size and SHA-256, and says which version of the app and of the data made it, and on which day.
The database is not copied as a file: SQLite hands over a snapshot of it (``_copy_database``), which is whole
also when a save happens at that moment.

A backup file that is to be restored came from outside and is hostile input (``_stage``):

- it is at most MAX_BACKUP_BYTES, holds at most MAX_ENTRIES files, and unpacks to at most MAX_UNPACKED_BYTES;
  how many files it holds is read from its last 22 bytes before anything else is opened;
- it may hold only the names listed above, each once. Nothing in it is ever written under a name the file
  chose: a path that leads out of the folder, a link, a folder, an encrypted or oddly packed entry are refused;
- every size and every SHA-256 has to be as the manifest says, and unpacking stops at the stated size;
- the database has to be of a layout this version knows, written the plain way the app writes its tables
  (no trigger, no view, no computed column, no check: nothing in which a file could hide work to be done),
  and has to pass SQLite's own check. Then its rows are copied into a database this app creates itself, so
  nothing of the file's own table definitions is kept;
- a file made by a newer version is refused with a sentence. One from an older version is opened the way an
  older data folder is (``Store`` adds the columns it lacks);
- last, the staged data is read the way the app's pages read theirs (``_try_out``). If that fails, the file
  is refused as well.

All of this happens in a folder of its own inside the data folder. Nothing is replaced until the creator has
seen what the file holds and said yes (``Backups.restore``). Then:

1. no new change to the data is let in, and a restore is refused while a job or another change is under way;
2. what is there is kept as an automatic backup (the newest KEEP_AUTOMATIC stay), and copied for step 4;
3. the data is replaced. The database is not swapped as a file: SQLite copies the new content into the
   database that is there, in one step it undoes itself when it cannot finish. That also works on Windows,
   where a file that is open cannot be replaced, and for another program that has the database open;
4. if any part of step 3 fails, everything is put back from the copies of step 2. If the app is closed in the
   middle, the next start puts it back (``Backups.recover``): a note in the folder says a restore was under way.

After a restore the data has a new name (``Backups.epoch``), sent with every answer. A page says with each
change which data it means; one that was open before the restore names the old data and is refused, the way
a save based on an older version of a script is.

Nothing of this is offered through MCP: an AI app must not be able to carry off or replace everything.
Standard library only.
"""
from __future__ import annotations

import contextlib
import datetime
import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import buildinfo, creator, llm, pipeline, posted, trends, wordlist
from . import setup as setup_status
from . import store as store_module
from .store import Store, loads_stored

APP = "learn-my-voice"
FORMAT = 1                              # how a backup file is laid out; a file that names a later one is refused
# The version of the data: of the tables in studio.db and of the files beside it. Raise it by one whenever a
# table or a column is added or what a stored value means changes (tests/test_backup.py holds the layout this
# number stands for, and fails when the layout moves without it). A backup that names a later one is refused.
DATA_VERSION = 3
EXTENSION = "lmvbackup"
EPOCH_HEADER = "X-Data-Epoch"           # with every answer: which data this is; with a change: which data the page means
FIRST_EPOCH = "0"                       # the name of the data in a folder nothing was ever restored into

MANIFEST, DATABASE, SETTINGS = "manifest.json", "studio.db", "settings.json"
PLAIN_FILES = ("creator.json", "voice_dna.json", "voice_pending.json", "recommendations.json")
HISTORY_FOLDER = "voice_dna_history"
# The settings a backup carries: what the creator taught the app. Everything else in config.json is the AI
# connection of one computer (with its keys) and is neither put into a backup nor changed by a restore.
CARRIED_SETTINGS = ("trend_weight", wordlist.KEY)
NOT_INCLUDED = ("api_keys", "ai_connection", "transcription_setup", "uploads", "exports", "logs", "automatic_backups")

MAX_BACKUP_BYTES = 1024 ** 3            # a backup file as it arrives
MAX_UNPACKED_BYTES = 2 * 1024 ** 3      # everything in it, unpacked
MAX_HISTORY_FILES = 1000                # earlier voice profiles in one backup; the newest are taken
MAX_ENTRIES = MAX_HISTORY_FILES + 8     # files in one backup: the manifest, the database, the five small files, the profiles
MAX_SMALL_FILE_BYTES = 4 * 1024 ** 2    # each file other than the database
MAX_MANIFEST_BYTES = 1024 ** 2
MAX_DIRECTORY_BYTES = 1024 ** 2         # the list of entries at the end of the archive
MAX_CELL_CHARS = 16 * 1000 * 1000       # one text in the database; the longest the app stores is a library item of 500,000
KEEP_AUTOMATIC = 3                      # automatic backups made before a restore that are kept
KEEP_MADE = 2                           # backups made for a download that are kept until they are fetched
KEPT_FOR_SEC = 3600                     # a staged restore, or a backup made for a download, nobody came back for
DATABASE_PATIENCE_SEC = 30.0            # how long a copy of the database waits for another program to let go of it
CHECK_PATIENCE_SEC = 900.0              # how long SQLite may work on one file from outside
_CHUNK = 256 * 1024

_AUTOMATIC = re.compile(r"before-restore-([0-9]{8}-[0-9]{6})(?:-([0-9]{1,4}))?\.%s" % EXTENSION)
_ID = re.compile(r"[0-9a-f]{12}")
_WORK = re.compile(r"work-[0-9A-Za-z_]{1,40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}")
_UNREADABLE = (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError,
               struct.error, ValueError, OverflowError, KeyError)
_MB = 1024 * 1024


class Refused(Exception):
    """A backup that was not made, or a file that was not restored. Nothing was changed.

    ``code`` is what a page can map; ``message`` (English) and ``message_zh`` are written to be shown;
    ``status`` is the HTTP status; ``extra`` holds further fields for the answer, among them ``reason``:
    which check refused the file, for a test or a report of a problem.
    """

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400, reason: str = "",
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code, self.message, self.message_zh, self.status = code, message, message_zh, status
        self.extra = dict(extra or {})
        if reason:
            self.extra["reason"] = reason


class _Busy(Exception):
    """Another change to the data is under way."""


# ---- refusals ---------------------------------------------------------------
def _not_a_backup() -> Refused:
    return Refused("backup_not_a_backup",
                   "This file is not a backup made by this app. Choose a file whose name ends in .%s." % EXTENSION,
                   "这个文件不是这个应用做的备份。请选择名字以 .%s 结尾的文件。" % EXTENSION, 415)


def _incomplete() -> Refused:
    return Refused("backup_incomplete",
                   "This backup file is not complete: it stops before its end. It was probably cut short while it was "
                   "downloaded or copied. Copy it again from where it is kept.",
                   "这个备份文件不完整：它没有到结尾就断了，多半是下载或复制的时候中断了。请从保存它的地方重新复制一份。")


def _too_large() -> Refused:
    limit = MAX_BACKUP_BYTES // _MB
    return Refused("backup_too_large", "This file is larger than a backup can be (%d MB), so it was not read." % limit,
                   "这个文件超过了备份的大小上限（%d MB），所以没有读取。" % limit, 413, extra={"limit": MAX_BACKUP_BYTES})


def _unexpected(reason: str) -> Refused:
    return Refused("backup_unexpected_content",
                   "This file holds something the app never puts into a backup: a file of another name, a path that "
                   "leads out of the folder, a link, or an entry packed in another way. Nothing of it was used.",
                   "这个文件里有应用从来不会放进备份的东西：别的名字的文件、指向文件夹外面的路径、链接，或者用别的方式打包的内容。"
                   "里面的东西一样都没有用。", reason=reason)


def _unpacks_too_far(reason: str) -> Refused:
    return Refused("backup_unpacks_too_far",
                   "This file unpacks to more than it says, or to more than a backup can hold, so it was not unpacked.",
                   "这个文件解开以后比它自己说的要大，或者超过了备份能装的量，所以没有解开。", 413, reason=reason)


def _changed(reason: str) -> Refused:
    return Refused("backup_changed",
                   "This backup file is not as it was made: what it holds does not match its own list of contents. "
                   "It was damaged or changed afterwards, so nothing of it was used.",
                   "这个备份文件和做出来的时候不一样了：里面的内容和它自带的清单对不上。它后来被损坏或者改动过，所以里面的东西一样都没有用。",
                   reason=reason)


def _unreadable(reason: str) -> Refused:
    return Refused("backup_unreadable",
                   "This backup file could not be read: it is damaged, or it was not made by this app.",
                   "这个备份文件读不出来：它已经损坏，或者不是这个应用做的。", reason=reason)


def _newer() -> Refused:
    return Refused("backup_newer",
                   "This backup was made by a newer version of the app than the one on this computer, so it cannot be "
                   "read here. Update the app first, then restore the backup.",
                   "这个备份是用比这台电脑上更新的版本做的，这里读不了。请先更新应用，再恢复这个备份。", 409)


def _bad_database(reason: str) -> Refused:
    return Refused("backup_database_damaged",
                   "The database in this backup did not pass the checks: it is damaged, or it is not a database this "
                   "version of the app knows. Nothing was replaced.",
                   "这个备份里的数据库没有通过检查：它已经损坏，或者不是这个版本的应用认识的数据库。没有替换任何内容。", reason=reason)


def _unusable(reason: str) -> Refused:
    return Refused("backup_unusable",
                   "The app could not read what this backup holds the way its pages would, so nothing was replaced.",
                   "应用没法像各个页面那样读出这个备份里的内容，所以没有替换任何内容。", reason=reason)


def _no_space() -> Refused:
    return Refused("backup_no_space", "There is not enough free space on this computer for this.",
                   "这台电脑的剩余空间不够做这件事。", 507)


def _not_staged() -> Refused:
    return Refused("restore_not_found",
                   "That backup is no longer waiting to be restored. Choose the file again.",
                   "那个备份已经不在等待恢复了，请重新选择文件。", 404)


def _at_work() -> Refused:
    return Refused("backup_busy", "Another backup or restore is still at work. Wait until it is done.",
                   "另一次备份或恢复还在进行，请等它完成。", 409)


def _in_use() -> Refused:
    return Refused("restore_busy",
                   "Something is still at work in the app: a transcription or another task, or a change that is just "
                   "being saved. A backup can be restored once that is done. Nothing was replaced.",
                   "应用里还有事情在进行：转写或别的任务，或者正在保存的改动。等它完成以后才能恢复备份。没有替换任何内容。", 409)


def stale_page() -> Refused:
    """The refusal for a change sent by a page that was open before a restore."""
    return Refused("stale_data",
                   "The data in the app was replaced by a restored backup after this page was opened, so nothing from "
                   "this page was saved. Reload the page to see what is there now.",
                   "这一页打开之后，应用里的数据已经被恢复的备份替换了，所以这一页上的改动没有保存。请刷新页面，查看现在的内容。", 409)


# ---- small helpers ----------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_plain_file(path: Path) -> bool:
    """Whether ``path`` is a file of its own: there, and neither a link nor a folder."""
    try:
        return stat.S_ISREG(os.lstat(str(path)).st_mode)
    except OSError:
        return False


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _remove_folder(path: Path) -> None:
    target = str(path)
    if not os.path.exists(target):
        return
    if os.name != "nt":
        shutil.rmtree(target, ignore_errors=True)
        return

    def _unlock(func: Any, failed_path: str, _exc: Any) -> None:
        try:
            os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD)
            func(failed_path)
        except OSError:
            pass

    for attempt in range(10):
        shutil.rmtree(target, ignore_errors=True)
        if not os.path.exists(target):
            return
        gc.collect()
        try:
            shutil.rmtree(target, onerror=_unlock)
        except OSError:
            pass
        if not os.path.exists(target):
            return
        time.sleep(0.02 * (attempt + 1))


def _write(path: Path, data: bytes, durable: bool = False) -> None:
    """Write a file of a work folder. ``durable``: handed to the disk before this returns."""
    if durable:
        Store._write_bytes(path, data)
        return
    with open(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
        handle.write(data)


def _history_order(name: str) -> Tuple[str, int]:
    match = store_module._HISTORY_FILE.fullmatch(name)
    return (match.group(1), int(match.group(2) or 1)) if match else ("", 0)


def _history_names(folder: Path) -> List[str]:
    """The earlier voice profiles in ``folder``, oldest first: files of their own with the names ``Store`` gives them."""
    try:
        names = os.listdir(str(folder))
    except OSError:
        return []
    return sorted((name for name in names if store_module._HISTORY_FILE.fullmatch(name) and _is_plain_file(folder / name)),
                  key=_history_order)


# ---- the database: a snapshot, and putting one in place ---------------------
def _copy_database(source: Path, target: Path) -> None:
    """Make the database at ``target`` hold exactly what the one at ``source`` holds.

    SQLite does the copying itself, page by page inside one transaction on the target. A save into the source
    at that moment is either in the copy whole or not at all. A target that exists is not replaced as a file:
    its content is, in one step, and if that step cannot finish (the disk is full, the app is closed) SQLite
    puts the old content back. Other programs that have either database open wait, or carry on with what they
    then find. When the target stays in use by another program for longer than DATABASE_PATIENCE_SEC this
    raises ``TimeoutError`` or ``sqlite3.OperationalError`` and the target is as it was.
    """
    patience = DATABASE_PATIENCE_SEC
    deadline = time.monotonic() + patience

    def watch(status: int, remaining: int, total: int) -> None:
        if status in (5, 6) and time.monotonic() > deadline:        # SQLITE_BUSY, SQLITE_LOCKED: somebody else holds it
            raise TimeoutError("the database stayed in use by another program")

    with contextlib.closing(sqlite3.connect(str(source), timeout=min(10.0, patience))) as old, \
            contextlib.closing(sqlite3.connect(str(target), timeout=min(10.0, patience))) as new:
        old.backup(new, progress=watch, sleep=0.05)


def _snapshot(source: Path, target: Path) -> None:
    """A copy of the live database as a file of its own, whole whatever is being saved at that moment."""
    _copy_database(source, target)
    with contextlib.closing(sqlite3.connect(str(target))) as db:
        db.execute("PRAGMA journal_mode = DELETE").fetchall()       # one file, also when the live one keeps a log beside it


# ---- the layout of the database --------------------------------------------
_TOKEN = re.compile(r"\s*(?:([A-Za-z_][A-Za-z0-9_]*)|('(?:[^']|'')*')|([+-]?[0-9]+(?:\.[0-9]+)?)|([(),]))")
_TYPES = ("TEXT", "INTEGER", "REAL")
Column = Tuple[str, str, List[str]]     # (its name, or "" for a rule about several columns; its type; the tokens it is written with)


def _tokens(sql: str) -> Optional[List[str]]:
    """The words, texts, numbers, brackets and commas ``sql`` is made of, or None when anything else is in it.

    Each comes back as written; a text keeps its quotation marks. Each character is looked at once.
    """
    found: List[str] = []
    at, end = 0, len(sql.rstrip())
    while at < end:
        match = _TOKEN.match(sql, at)
        if match is None:
            return None
        found.append(match.group(match.lastindex))
        at = match.end()
    return found


def _is_word(token: str) -> bool:
    return bool(token) and (token[0].isalpha() or token[0] == "_")


def _definition(tokens: List[str]) -> Optional[Tuple[str, str]]:
    """``(column, type)`` for one plain column of a table, ``("", "")`` for a plain rule about several columns, else None.

    Plain is what this app writes: a name, one of three types, and any of PRIMARY KEY, NOT NULL, UNIQUE and
    DEFAULT with a text, a number or NULL; or ``UNIQUE (a, b)`` and ``PRIMARY KEY (a)`` for the table. A check,
    a computed column, a reference to another table, a collation or a default that is worked out are not plain:
    each of them lets a file say what SQLite is to work out while the file is merely being read.
    """
    if not tokens or not _is_word(tokens[0]):
        return None
    words = [token.upper() if _is_word(token) else token for token in tokens]
    if words[0] in ("UNIQUE", "PRIMARY"):
        rest = words[1:] if words[0] == "UNIQUE" else (words[2:] if words[1:2] == ["KEY"] else [])
        if len(rest) < 3 or rest[0] != "(" or rest[-1] != ")":
            return None
        inside = rest[1:-1]
        if len(inside) % 2 == 0 or not all(_is_word(token) if at % 2 == 0 else token == "," for at, token in enumerate(inside)):
            return None
        return "", ""
    if len(words) < 2 or words[1] not in _TYPES:
        return None
    at = 2
    while at < len(words):
        if words[at:at + 2] in (["PRIMARY", "KEY"], ["NOT", "NULL"]):
            at += 2
        elif words[at] == "UNIQUE":
            at += 1
        elif words[at] == "DEFAULT" and at + 1 < len(words) and words[at + 1] not in ("(", ")", ",") \
                and (words[at + 1] == "NULL" or not _is_word(words[at + 1])):
            at += 2
        else:
            return None
    return tokens[0], words[1]


def _table(sql: Any) -> Optional[Tuple[str, List[Column]]]:
    """``(name, columns)`` for a table written the plain way, or None. Nothing may follow the closing bracket."""
    tokens = _tokens(sql) if isinstance(sql, str) else None
    if not tokens or len(tokens) < 6 or [token.upper() for token in tokens[:2]] != ["CREATE", "TABLE"] \
            or not _is_word(tokens[2]) or tokens[3] != "(" or tokens[-1] != ")":
        return None
    parts: List[List[str]] = [[]]
    depth = 0
    for token in tokens[4:-1]:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if depth < 0 or depth > 1:
            return None
        if token == "," and depth == 0:
            parts.append([])
        else:
            parts[-1].append(token)
    if depth != 0:
        return None
    columns: List[Column] = []
    for part in parts:
        plain = _definition(part)
        if plain is None:
            return None
        columns.append((plain[0], plain[1], part))
    return tokens[2], columns


def _squash(sql: Any) -> str:
    return " ".join(str(sql or "").split()).lower()


def reference_layout() -> Dict[str, Any]:
    """The tables and indexes this version of the app makes, read off a database it has just made.

    ``{"tables": {name: columns}, "added": {name: {columns an older database may lack}}, "indexes":
    {name: (table, its statement)}}``. Taken from ``Store``'s own statements each time, so a table or a column
    added there is known here without anything being written twice.
    """
    with contextlib.closing(sqlite3.connect(":memory:")) as db:
        db.executescript(store_module._SCHEMA)
        db.execute(store_module._ONE_ITEM_A_SCRIPT)
        made = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
    tables: Dict[str, List[Column]] = {}
    indexes: Dict[str, Tuple[str, str]] = {}
    for kind, name, table, sql in made:
        if kind == "table":
            plain = _table(sql)
            if plain is None:
                raise RuntimeError("the table %s is not written the plain way a backup can check" % name)
            tables[name] = plain[1]
        elif kind == "index" and sql:
            indexes[name] = (table, _squash(sql))
    added: Dict[str, set] = {}
    for table, column, _definition_text in store_module._ADDED_COLUMNS:
        added.setdefault(table, set()).add(column)
    return {"tables": tables, "added": added, "indexes": indexes}


def layout_fingerprint() -> str:
    """A short name for the layout of the database: it changes whenever a table, a column or a type does.

    DATA_VERSION is the number of one such layout (see the comment there).
    """
    layout = reference_layout()
    said = sorted("%s(%s)" % (name, ",".join("%s %s" % (column, kind) if column else " ".join(tokens)
                                                for column, kind, tokens in columns))
                  for name, columns in layout["tables"].items())
    return hashlib.sha256(";".join(said).encode("utf-8")).hexdigest()[:16]


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open a database that came from outside so that nothing in it is changed and as little of it as possible is trusted."""
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
    for setting in ("trusted_schema = OFF", "cell_size_check = ON", "mmap_size = 0"):
        try:
            db.execute("PRAGMA " + setting).fetchall()
        except sqlite3.Error:
            pass                        # an older SQLite does not know the setting; the checks below do not rest on it
    return db


def _looks_like_a_database(path: Path) -> bool:
    """Whether the file starts the way a database the app writes does: SQLite, of the kind that keeps no log beside it."""
    try:
        with open(str(path), "rb") as handle:
            head = handle.read(100)
    except OSError:
        return False
    return len(head) == 100 and head[:16] == b"SQLite format 3\x00" and head[18] == 1 and head[19] == 1


def _found_tables(db: sqlite3.Connection, layout: Dict[str, Any]) -> Dict[str, List[str]]:
    """``{table: [its columns, in the order the table lists them]}`` for a database from outside, or ``Refused``.

    Everything the database says it holds has to be something this app makes: its tables, written the plain way,
    with columns it knows, of the types it gives them; its two indexes, word for word; and the indexes SQLite
    makes by itself. A view, a trigger, a table or a column of another name, and a table SQLite works out or
    keeps elsewhere are refused.
    """
    entries = db.execute("SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master LIMIT 200").fetchall()
    found: Dict[str, List[str]] = {}
    for kind, name, table, rootpage, sql in entries:
        if kind == "index":
            if sql is None and isinstance(name, str) and name.startswith("sqlite_autoindex_"):
                continue
            if not isinstance(name, str) or layout["indexes"].get(name) != (table, _squash(sql)):
                raise _bad_database("unknown_index")
            continue
        if kind != "table":
            raise _bad_database("not_a_table")
        if not isinstance(name, str) or name not in layout["tables"] or not isinstance(rootpage, int) or rootpage < 1:
            raise _bad_database("unknown_table")
        plain = _table(sql)
        if plain is None or plain[0] != name:
            raise _bad_database("table_not_plain")
        known = {column: kind_of for column, kind_of, _written in layout["tables"][name] if column}
        have = [column for column, _kind_of, _written in plain[1] if column]
        if len(set(have)) != len(have):
            raise _bad_database("table_not_plain")
        for column, kind_of, _written in plain[1]:
            if column and known.get(column) != kind_of:
                raise _bad_database("unknown_column")
        if not all(column in have or column in layout["added"].get(name, ()) for column in known):
            raise _bad_database("column_missing")
        found[name] = have
    if "scripts" not in found:
        raise _bad_database("no_scripts_table")
    return found


def _rebuild(source: Path, target: Path) -> None:
    """Check the database at ``source`` and copy its rows into a new database at ``target`` that this app lays out.

    The new tables are created from this app's own definitions, with the columns the old ones have, so a
    database from an older version comes out in the shape that version left it in and is brought up to date by
    ``Store`` like any older data folder. Rows keep their order. A value of a kind no column of this app
    holds (bytes anywhere, a text where a number belongs), or a text longer than MAX_CELL_CHARS, refuses the file.
    """
    if not _looks_like_a_database(source):
        raise _bad_database("not_a_database")
    layout = reference_layout()
    deadline = time.monotonic() + CHECK_PATIENCE_SEC
    try:
        db = _open_read_only(source)
    except sqlite3.Error:
        raise _bad_database("cannot_open")
    try:
        db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 200000)
        found = _found_tables(db, layout)
        if db.execute("PRAGMA integrity_check(5)").fetchall() != [("ok",)]:
            raise _bad_database("integrity_check")
        db.execute("ATTACH DATABASE ? AS fresh", (str(target),))
        db.execute("BEGIN")
        for name, columns in found.items():
            definitions = {column: written for column, _kind, written in layout["tables"][name] if column}
            parts = [" ".join(definitions[column]) for column in columns] + [
                " ".join(written) for column, _kind, written in layout["tables"][name] if not column
            ]
            db.execute("CREATE TABLE fresh.%s (%s)" % (name, ", ".join(parts)))
            listed = ", ".join(columns)
            db.execute("INSERT INTO fresh.%s (rowid, %s) SELECT rowid, %s FROM main.%s" % (name, listed, listed, name))
            kinds = {column: kind for column, kind, _written in layout["tables"][name] if column}
            wrong = " OR ".join(
                "typeof(%s) NOT IN ('text', 'null') OR length(%s) > %d" % (column, column, MAX_CELL_CHARS)
                if kinds[column] == "TEXT" else "typeof(%s) NOT IN ('integer', 'real', 'null')" % column
                for column in columns)
            if db.execute("SELECT COUNT(*) FROM fresh.%s WHERE %s" % (name, wrong)).fetchone()[0]:
                raise _bad_database("value_of_another_kind")
        db.execute("COMMIT")
        db.execute("DETACH DATABASE fresh")
    except sqlite3.Error as exc:
        raise _bad_database("sqlite: %s" % str(exc)[:80])
    finally:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        try:
            db.execute("DETACH DATABASE fresh")
        except sqlite3.Error:
            pass
        db.close()


def _columns_of(path: Path) -> Dict[str, List[str]]:
    with contextlib.closing(sqlite3.connect(str(path))) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {table: [row[1] for row in db.execute("PRAGMA table_info(%s)" % table)] for table in tables}


def _check_scripts(store: Store) -> None:
    """Each script's stored lines, post copy and keywords have the shapes every reader of a script takes for granted."""
    with contextlib.closing(store._db()) as db:
        for row in db.execute("SELECT lines_json, post_json, ai_lines_json, keywords_json FROM scripts"):
            try:
                lines = loads_stored(row["lines_json"])
                draft = loads_stored(row["ai_lines_json"]) if row["ai_lines_json"] is not None else []
                post = loads_stored(row["post_json"])
                keywords = loads_stored(row["keywords_json"] or "[]")
            except (ValueError, RecursionError):
                raise _bad_database("script_not_json")
            for listed in (lines, draft):
                if not isinstance(listed, list) or not all(isinstance(line, dict) for line in listed):
                    raise _bad_database("script_lines")
                for line in listed:
                    if not all(isinstance(line.get(field, ""), str) for field in store_module.LINE_FIELDS):
                        raise _bad_database("script_lines")
            if not isinstance(post, dict) or not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
                raise _bad_database("script_fields")


def _try_out(store: Store) -> None:
    """Read staged data the way the app's pages will read it, before any of it takes the place of what is there.

    What the first page, the list of scripts, the guided setup, the voice pages and Settings are answered with
    is worked out here and written as JSON, as it would be sent. Data the app cannot show is thus found out
    while the creator's own is still in place, and not afterwards, when the page with the way back might not load.
    """
    try:
        answers = [
            store.public_settings(), store.load_dna(), store.dna_unreadable(), creator.for_page(store.load_creator()),
            len(store.list_samples()), len(store.list_trends()), setup_status.status(store),
            posted.with_posted_dates(store, store.list_scripts()), pipeline.pending_voice(store),
            setup_status.voice_history(store), wordlist.payload(store), pipeline.lessons_payload(store),
            store.load_recommendations(), store.list_video_plans(),
            len(store.list_metric_snapshots()), store.list_sample_sentiments(),
            store.list_sponsor_briefs(),
        ]
        json.dumps(answers, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - whatever a reader trips over, the file is not used
        raise _unusable("%s: %s" % (type(exc).__name__, str(exc)[:120]))


# ---- what the data holds ----------------------------------------------------
def holds(store: Store) -> Dict[str, Any]:
    """What the data folder of ``store`` holds, counted: for the page, before a backup is made and before one is restored.

    ``voice_profile`` is ``own`` (a profile of the creator's that can be read), ``unreadable`` (a file is there
    and cannot be read) or ``none``.
    """
    with contextlib.closing(store._db()) as db:
        counts = {table: db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                  for table in ("scripts", "samples", "trends", "lessons")}
    own, unreadable = store._own_dna()
    ideas = store.load_recommendations()
    counts.update({
        "voice_profile": "own" if own is not None else ("unreadable" if unreadable else "none"),
        "earlier_profiles": sum(1 for name in _history_names(store.dna_history_dir) if name.endswith(".json")),
        "creator": creator.is_set(store.load_creator()),
        "pending_voice": store.load_pending() is not None,
        "ideas": isinstance(ideas, dict) and bool(ideas),
    })
    return counts


# ---- making a backup --------------------------------------------------------
def _carried(config: Any) -> Dict[str, Any]:
    """The settings a backup carries, out of what ``config.json`` or a backup's ``settings.json`` holds.

    Only CARRIED_SETTINGS, and each only when it is of the kind the app stores: one of the known ways of ranking
    keywords, and a change to the word list that the word list itself can use. Anything else is left out, so
    neither a key nor an address ever comes out of or goes into a backup, whatever name it is stored under.
    """
    kept: Dict[str, Any] = {}
    if not isinstance(config, dict):
        return kept
    weight = config.get("trend_weight")
    if isinstance(weight, str) and weight in trends.WEIGHTS:
        kept["trend_weight"] = weight
    off, phrases, unusable = wordlist._stored(config)
    if config.get(wordlist.KEY) is not None and not unusable:
        kept[wordlist.KEY] = {"off": [entry for entry in off if len(entry) <= 200][:2000], "phrases": phrases}
    return kept


def _collect(store: Store, raw: Path, durable: bool = False) -> Tuple[List[Tuple[str, Path]], List[Dict[str, Any]]]:
    """Copy everything a backup holds out of the data folder into ``raw``.

    Returns ``([(name in the backup, the copy)], [what was left out and why])``. The database is a snapshot; the
    voice profile and the earlier ones are copied while no profile is being replaced, so they belong together.
    A file larger than MAX_SMALL_FILE_BYTES is left out and named, as are earlier profiles beyond the newest
    MAX_HISTORY_FILES: a backup the app makes is one the app restores.
    """
    raw.mkdir(mode=0o700)
    files: List[Tuple[str, Path]] = []
    left_out: List[Dict[str, Any]] = []
    _snapshot(store.db_path, raw / DATABASE)
    files.append((DATABASE, raw / DATABASE))
    _write(raw / SETTINGS, Store._encode(_carried(store.load_config())), durable)
    files.append((SETTINGS, raw / SETTINGS))

    def take(source: Path, name: str) -> None:
        try:
            if os.lstat(str(source)).st_size > MAX_SMALL_FILE_BYTES:
                left_out.append({"name": name, "why": "too_large"})
                return
            data = source.read_bytes()
        except OSError:
            return                      # gone in the meantime
        target = raw / name
        _write(target, data, durable)
        files.append((name, target))

    with store_module._DNA_LOCK:
        for name in PLAIN_FILES:
            if _is_plain_file(store.data_dir / name):
                take(store.data_dir / name, name)
        earlier = _history_names(store.dna_history_dir)
        if len(earlier) > MAX_HISTORY_FILES:
            left_out.append({"name": HISTORY_FOLDER, "why": "too_many", "count": len(earlier) - MAX_HISTORY_FILES})
            earlier = earlier[-MAX_HISTORY_FILES:]
        if earlier:
            (raw / HISTORY_FOLDER).mkdir(mode=0o700)
        for name in earlier:
            take(store.dna_history_dir / name, "%s/%s" % (HISTORY_FOLDER, name))
    return files, left_out


def _pack(files: List[Tuple[str, Path]], target: Path, kind: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Write the backup file at ``target`` from the collected copies. Returns its manifest."""
    now = time.time() if now is None else now
    build = buildinfo.current()
    manifest = {
        "app": APP, "format": FORMAT, "data_version": DATA_VERSION,
        "app_version": build.get("version"), "app_commit": build.get("commit"),
        "created_on": time.strftime("%Y-%m-%d", time.localtime(now)), "created_at": round(now, 3), "kind": kind,
        "files": [{"name": name, "size": os.path.getsize(str(path)), "sha256": _sha256(path)} for name, path in files],
        "not_included": list(NOT_INCLUDED),
    }
    part = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(str(part), "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
            for name, path in files:
                archive.write(str(path), name)
        if os.name == "posix":
            os.chmod(str(part), 0o600)
        store_module._replace(str(part), str(target))
    except BaseException:
        _remove(part)
        raise
    return manifest


def create(store: Store, target: Path, kind: str = "manual", work: Optional[Path] = None,
           keep: Optional[Path] = None, check: bool = True) -> Dict[str, Any]:
    """Make a backup of everything in ``store``'s data folder at ``target``.

    The copies it is packed from are made in a folder under ``work`` (the folder of ``target`` by default),
    which is removed again; with ``keep`` they are made there, durably, and stay: a restore puts them back if
    it cannot finish. With ``check``, the snapshot of the database is first put through the checks a restore
    applies (``_rebuild``), so the app does not hand out a backup it would itself refuse. The backup made
    before a restore is not checked: what is there is kept as it is, whatever state it is in.

    Returns ``{"name", "path", "size", "created_on", "kind", "holds", "left_out"}``. Raises ``Refused``.
    """
    folder = Path(work) if work is not None else target.parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = keep if keep is not None else Path(tempfile.mkdtemp(dir=str(folder), prefix="work-")) / "raw"
    try:
        try:
            files, left_out = _collect(store, raw, durable=keep is not None)
            if check:
                scratch = raw.parent / (raw.name + "-check.db")
                try:
                    _rebuild(raw / DATABASE, scratch)
                except Refused as exc:
                    raise Refused("backup_data_damaged",
                                  "The database on this computer did not pass its own check, so no backup was made from it.",
                                  "这台电脑上的数据库没有通过自己的检查，所以没有用它做备份。", 500,
                                  reason=str(exc.extra.get("reason", "")))
                finally:
                    _remove(scratch)
            manifest = _pack(files, target, kind)
        except (OSError, sqlite3.Error, zipfile.LargeZipFile) as exc:
            _remove(target)
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
                raise _no_space()
            raise Refused("backup_not_made", "The backup could not be made on this computer: %s" % str(exc)[:200],
                          "备份没能在这台电脑上做出来：%s" % str(exc)[:200], 507)
    finally:
        if keep is None:
            _remove_folder(raw.parent)
    return {"name": target.name, "path": str(target), "size": os.path.getsize(str(target)),
            "created_on": manifest["created_on"], "kind": kind, "holds": holds(store), "left_out": left_out}


# ---- reading a backup file from outside -------------------------------------
def _entry_count(path: Path, size: int) -> int:
    """How many files the archive at ``path`` says it holds, read from its last 22 bytes; ``Refused`` when it is no backup.

    A backup this app writes is a zip archive with nothing before it, nothing after it and no remark at its
    end, small enough never to need the 64-bit form. Holding a file to exactly that makes the number below the
    one ``zipfile`` goes by, and it is known before ``zipfile`` reads a list of entries of whatever length.
    """
    with open(str(path), "rb") as handle:
        if handle.read(4) != b"PK\x03\x04":
            raise _not_a_backup()
        if size < 22 + 30:
            raise _incomplete()
        handle.seek(size - 42)
        before, tail = handle.read(20), handle.read(22)
    mark, disk, first_disk, here, total, directory_size, directory_at, remark = struct.unpack("<4s4H2LH", tail)
    if mark != b"PK\x05\x06":
        raise _incomplete()
    if before[:4] == b"PK\x06\x07" or disk or first_disk or here != total or remark \
            or 0xFFFF in (here, total) or 0xFFFFFFFF in (directory_size, directory_at):
        raise _unexpected("archive_form")
    if directory_at + directory_size != size - 22:
        raise _unexpected("archive_form")
    if total > MAX_ENTRIES or directory_size > MAX_DIRECTORY_BYTES:
        raise _unpacks_too_far("entries")
    return total


def _allowed_name(name: str) -> bool:
    if name in (MANIFEST, DATABASE, SETTINGS) or name in PLAIN_FILES:
        return True
    folder, slash, rest = name.partition("/")
    return bool(slash) and folder == HISTORY_FOLDER and store_module._HISTORY_FILE.fullmatch(rest) is not None


def _size_limit(name: str) -> int:
    return MAX_UNPACKED_BYTES if name == DATABASE else (MAX_MANIFEST_BYTES if name == MANIFEST else MAX_SMALL_FILE_BYTES)


def _checked_entries(archive: zipfile.ZipFile, expected: int) -> Dict[str, zipfile.ZipInfo]:
    """The entries of an archive by name, each one a file the app itself puts into a backup; else ``Refused``."""
    infos = archive.infolist()
    if len(infos) != expected:
        raise _unexpected("archive_form")
    entries: Dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        name = info.filename
        if info.orig_filename != name or not _allowed_name(name) or name in entries:
            raise _unexpected("name")
        kind = stat.S_IFMT(info.external_attr >> 16)        # what kind of thing the entry says it is: a file, a link, a folder
        if info.flag_bits & 0x1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) \
                or kind not in (0, stat.S_IFREG):
            raise _unexpected("kind")
        if info.file_size > _size_limit(name):
            raise _unpacks_too_far("entry")
        total += info.file_size
        entries[name] = info
    if total > MAX_UNPACKED_BYTES:
        raise _unpacks_too_far("total")
    if MANIFEST not in entries or DATABASE not in entries:
        raise _unexpected("name")
    return entries


def _read_manifest(raw: bytes, entries: Dict[str, zipfile.ZipInfo]) -> Dict[str, Any]:
    """The manifest of a backup, checked: who made it, which versions, the day, and a list that names every entry."""
    try:
        manifest = llm.loads_strict(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _unreadable("manifest")
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise _unreadable("manifest")

    def number(key: str) -> int:
        value = manifest.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _unreadable("manifest")
        return value

    if number("format") > FORMAT or number("data_version") > DATA_VERSION:
        raise _newer()
    day, version, kind = manifest.get("created_on"), manifest.get("app_version"), manifest.get("kind", "manual")
    if not isinstance(day, str) or not _DAY.fullmatch(day):
        raise _unreadable("manifest")
    try:
        datetime.date(int(day[:4]), int(day[5:7]), int(day[8:]))
    except ValueError:
        raise _unreadable("manifest")
    if version is not None and not (isinstance(version, str) and _VERSION.fullmatch(version)):
        raise _unreadable("manifest")
    if kind not in ("manual", "before_restore"):
        raise _unreadable("manifest")
    listed = manifest.get("files")
    if not isinstance(listed, list) or len(listed) != len(entries) - 1:
        raise _changed("listing")
    files: Dict[str, Tuple[int, str]] = {}
    for item in listed:
        if not isinstance(item, dict):
            raise _unreadable("manifest")
        name, size, digest = item.get("name"), item.get("size"), item.get("sha256")
        if not isinstance(name, str) or isinstance(size, bool) or not isinstance(size, int) or size < 0 \
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise _unreadable("manifest")
        if name == MANIFEST or name not in entries or name in files:
            raise _changed("listing")
        if entries[name].file_size != size:
            raise _unpacks_too_far("size") if entries[name].file_size > size else _changed("size")
        files[name] = (size, digest)
    return {"created_on": day, "app_version": version, "data_version": manifest["data_version"], "kind": kind,
            "files": files}


def _unpack(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, size: int, digest: str) -> None:
    """Write one entry to ``target``, a name this app chose. It has to be ``size`` bytes long and hash to ``digest``."""
    seen = hashlib.sha256()
    got = 0
    with archive.open(info) as part, open(os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        while True:
            block = part.read(min(_CHUNK, size - got + 1))
            if not block:
                break
            got += len(block)
            if got > size:
                raise _unpacks_too_far("more_than_stated")
            seen.update(block)
            out.write(block)
    if got != size:
        raise _changed("size")
    if seen.hexdigest() != digest:
        raise _changed("sha256")


def _stage(source: Path, work: Path) -> Dict[str, Any]:
    """Check the backup file at ``source`` and lay out what it holds as a data folder at ``work/new``.

    Returns ``{"manifest", "store", "settings", "older"}``: what the file says of itself, a ``Store`` on the
    staged folder, the settings it carries, and whether its database was brought up to date. Raises ``Refused``.
    """
    try:
        size = os.path.getsize(str(source))
    except OSError:
        raise _not_staged()
    if size > MAX_BACKUP_BYTES:
        raise _too_large()
    if size < 4:
        raise _not_a_backup()
    arrived, new = work / "in", work / "new"
    arrived.mkdir(mode=0o700, parents=True)
    new.mkdir(mode=0o700)
    try:
        expected = _entry_count(source, size)
        with zipfile.ZipFile(str(source)) as archive:
            entries = _checked_entries(archive, expected)
            with archive.open(entries[MANIFEST]) as part:
                raw = part.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise _unpacks_too_far("manifest")
            manifest = _read_manifest(raw, entries)
            wanted = sum(found[0] for found in manifest["files"].values())
            try:
                free = shutil.disk_usage(str(work)).free
            except OSError:
                free = None
            if free is not None and free < 3 * wanted + 64 * _MB:
                raise _no_space()
            for name, (stated, digest) in manifest["files"].items():
                target = arrived / DATABASE if name == DATABASE else new / name
                target.parent.mkdir(mode=0o700, exist_ok=True)
                _unpack(archive, entries[name], target, stated, digest)
    except Refused:
        raise
    except _UNREADABLE:
        raise _unreadable("archive")
    except OSError as exc:
        if getattr(exc, "errno", None) == 28:
            raise _no_space()
        raise Refused("restore_not_staged", "The backup could not be unpacked on this computer: %s" % str(exc)[:200],
                      "备份没能在这台电脑上解开：%s" % str(exc)[:200], 507)
    try:
        settings = _carried(loads_stored((new / SETTINGS).read_bytes().decode("utf-8")))
    except (OSError, ValueError, RecursionError):
        settings = {}
    _remove(new / SETTINGS)
    _rebuild(arrived / DATABASE, new / DATABASE)
    _remove(arrived / DATABASE)
    before = _columns_of(new / DATABASE)
    # The settings the staged folder is read with: the ones the backup carries, and no AI connection.
    _write(new / "config.json", Store._encode(settings))
    try:
        staged = Store(new)             # adds what an older database lacks, the way an older data folder gets it
    except (sqlite3.Error, OSError) as exc:
        raise _bad_database("cannot_bring_up_to_date: %s" % str(exc)[:80]) from None
    older = before != _columns_of(new / DATABASE) or manifest["data_version"] < DATA_VERSION
    _check_scripts(staged)
    _try_out(staged)
    return {"manifest": manifest, "store": staged, "settings": settings, "older": older}


# ---- requests side by side, a restore alone ---------------------------------
class _Gate:
    """Lets requests run side by side, and lets a restore first keep new changes out and then be alone.

    A request that may change the data is counted from its start to its answer. A restore is refused while
    another one is counted. From then on new changes wait; reading goes on while what is there is being kept.
    For the moment in which the data is replaced every request waits. The gate is this process's: it does not
    know about a second copy of the app, or an AI app, working on the same data folder.
    """

    def __init__(self) -> None:
        self._moved = threading.Condition()
        self._changing = 0
        self._no_changes = False
        self._nobody = False
        self._here = threading.local()

    @contextlib.contextmanager
    def request(self, changes: bool) -> Iterator[None]:
        with self._moved:
            while self._nobody or (changes and self._no_changes):
                self._moved.wait()
            if changes:
                self._changing += 1
                self._here.counted = getattr(self._here, "counted", 0) + 1
        try:
            yield
        finally:
            if changes:
                with self._moved:
                    self._changing -= 1
                    self._here.counted -= 1
                    self._moved.notify_all()

    def keep_changes_out(self) -> None:
        """Raises ``_Busy`` when a change other than the caller's own request is under way; else new ones wait from now on."""
        with self._moved:
            if self._no_changes or self._changing > getattr(self._here, "counted", 0):
                raise _Busy()
            self._no_changes = True

    def keep_everyone_out(self) -> None:
        with self._moved:
            self._nobody = True

    def open(self) -> None:
        with self._moved:
            self._no_changes = self._nobody = False
            self._moved.notify_all()


# ---- what the app holds on to ------------------------------------------------
class Backups:
    """Backups and restores of one data folder, for the running app (``server.App.backups``).

    Everything it writes is under ``<data>/backups``, a folder no backup includes: the automatic backups made
    before a restore, a small file that names the data (``state.json``), and work folders that are removed
    when their work is done, at the latest when the app next starts.

    ``jobs`` is the app's ``Jobs``: a restore is refused while any of them runs.
    """

    def __init__(self, store: Store, jobs: Any = None) -> None:
        self.store, self.jobs = store, jobs
        self.folder = Path(store.data_dir) / "backups"
        self.gate = _Gate()
        self._one_at_a_time = threading.Lock()
        self._staged: Optional[Dict[str, Any]] = None
        self._made: Dict[str, Dict[str, Any]] = {}
        self._before_step: Callable[[str], None] = lambda name: None     # a test raises here to stop a restore halfway
        self._replaced_any = False          # whether the restore under way has changed anything in the data folder yet
        self.put_back_at_start = False
        self._state: Dict[str, Any] = {}
        self.recover()

    # -- the folder ---------------------------------------------------------
    def _ensure_folder(self) -> None:
        self.folder.mkdir(mode=0o700, exist_ok=True)

    @property
    def _state_path(self) -> Path:
        return self.folder / "state.json"

    @property
    def _note_path(self) -> Path:
        return self.folder / "restoring.json"

    @property
    def epoch(self) -> str:
        """The name of the data in the folder now: FIRST_EPOCH until a backup is restored, a new one after each restore."""
        value = self._state.get("epoch")
        return value if isinstance(value, str) and _ID.fullmatch(value) else FIRST_EPOCH

    def recover(self) -> None:
        """Run when the app starts: finish undoing a restore the app was closed in the middle of, and tidy the folder.

        A note in the folder says a restore had begun to replace the data and where the copies of what was
        there are. They are put back, so the app goes on with what it had. Work folders nobody is using any
        more are removed.
        """
        try:
            loaded = Store._read_stored(self._state_path)
            self._state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self._state = {}
        try:
            note = Store._read_stored(self._note_path)
        except (OSError, ValueError):
            note = None
        if isinstance(note, dict) and isinstance(note.get("work"), str) and _WORK.fullmatch(note["work"]):
            old = self.folder / note["work"] / "old"
            if old.is_dir() and _is_plain_file(old / DATABASE):
                self._put_back(old, note.get("had_settings") is True)
                self.put_back_at_start = True
                # Pages left open name the data as it was; whatever they still hold, they start afresh.
                self._set_state(dict(self._state, epoch=uuid.uuid4().hex[:12]))
        _remove(self._note_path)
        if self.folder.is_dir():
            for entry in list(self.folder.iterdir()):
                if entry.name.startswith("work-") or entry.name in ("in", "out"):
                    if entry.is_dir() and not entry.is_symlink():
                        _remove_folder(entry)
                    else:
                        _remove(entry)
                elif entry.name.endswith(".part"):
                    _remove(entry)

    def close(self) -> None:
        """The app is closing: what was staged, or made for a download and not fetched, is removed."""
        self._forget_staged()
        for made in list(self._made.values()):
            _remove(made["path"])
        self._made.clear()
        for name in ("in", "out"):
            _remove_folder(self.folder / name)

    def _set_state(self, state: Dict[str, Any]) -> None:
        self._ensure_folder()
        Store._write_bytes(self._state_path, Store._encode(state))
        self._state = state

    @contextlib.contextmanager
    def _working(self) -> Iterator[None]:
        if not self._one_at_a_time.acquire(False):
            raise _at_work()
        try:
            yield
        finally:
            self._one_at_a_time.release()

    # -- requests -----------------------------------------------------------
    @contextlib.contextmanager
    def passage(self, changes: bool, named: Optional[str]) -> Iterator[None]:
        """What every request to the app's API runs inside.

        ``changes`` says the request may change the data (it is not a GET); ``named`` is the data it says it
        means (EPOCH_HEADER), or None when it does not say. A change that names other data than the folder
        holds now is refused with ``stale_page()``: it was made by a page that was open before a restore. It is
        looked at after any wait for a restore, so a save that arrives while the data is being replaced is
        refused as well. A request that names nothing is served as before: a program that is no page of this app.
        """
        with self.gate.request(changes):
            if changes and named is not None and named != self.epoch:
                raise stale_page()
            yield

    # -- what the page is told ----------------------------------------------
    def automatic(self) -> List[Dict[str, Any]]:
        """The automatic backups made before a restore, newest first: ``[{"name", "made_at", "size"}]``."""
        found = []
        try:
            names = os.listdir(str(self.folder))
        except OSError:
            names = []
        for name in names:
            match = _AUTOMATIC.fullmatch(name)
            path = self.folder / name
            if not match or not _is_plain_file(path):
                continue
            try:
                made_at = time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S"))
                found.append(((match.group(1), int(match.group(2) or 1)),
                              {"name": name, "made_at": made_at, "size": path.stat().st_size}))
            except (OSError, ValueError, OverflowError):
                continue
        found.sort(key=lambda entry: entry[0], reverse=True)
        return [entry[1] for entry in found]

    def _ai(self) -> Dict[str, bool]:
        settings = self.store.public_settings()
        return {"configured": bool(settings["configured"]), "manual": bool(settings["manual"])}

    def info(self) -> Dict[str, Any]:
        """What the Settings page shows before anything is done (``GET /api/backup``).

        ``holds`` is what a backup made now would hold, counted; ``automatic`` the backups made before a restore;
        ``last_restore`` what was last restored and when, or None; ``put_back_at_start`` true when this start of
        the app undid a restore it had been closed in the middle of; ``busy`` true while a job runs, when a
        restore would be refused; ``ai`` whether an AI is connected on this computer.
        """
        last = self._state.get("last_restore")
        return {
            "holds": holds(self.store), "automatic": self.automatic(), "keep_automatic": KEEP_AUTOMATIC,
            "last_restore": last if isinstance(last, dict) else None,
            "put_back_at_start": self.put_back_at_start,
            "limits": {"max_bytes": MAX_BACKUP_BYTES}, "extension": EXTENSION,
            "busy": bool(self.jobs is not None and self.jobs.unfinished()),
            "ai": self._ai(),
        }

    # -- making a backup ----------------------------------------------------
    def make(self) -> Dict[str, Any]:
        """Make a backup of everything, to be fetched (``made``). Returns what ``create`` returns without the path, and ``id``."""
        with self._working():
            self._ensure_folder()
            self._drop_old_made()
            out = self.folder / "out"
            out.mkdir(mode=0o700, exist_ok=True)
            made_id = uuid.uuid4().hex[:12]
            name = "learn-my-voice-backup-%s.%s" % (time.strftime("%Y-%m-%d"), EXTENSION)
            made = create(self.store, out / ("%s.%s" % (made_id, EXTENSION)), work=self.folder)
            self._made[made_id] = {"path": Path(made["path"]), "name": name, "at": time.time()}
            made.update(id=made_id, name=name)
            return {key: value for key, value in made.items() if key != "path"}

    def _drop_old_made(self) -> None:
        """Forget backups made for a download that are older than KEPT_FOR_SEC, and all but the newest so that KEEP_MADE stay."""
        now = time.time()
        order = sorted(self._made, key=lambda made_id: self._made[made_id]["at"])
        stale = [made_id for made_id in order if now - self._made[made_id]["at"] > KEPT_FOR_SEC]
        extra = order[:max(0, len(order) - (KEEP_MADE - 1))]
        for made_id in set(stale + extra):
            self.forget_made(made_id)

    def made(self, made_id: Any) -> Optional[Dict[str, Any]]:
        """``{"path", "name", "at"}`` of a backup made by ``make`` that is still there, or None."""
        found = self._made.get(made_id) if isinstance(made_id, str) else None
        return found if found is not None and _is_plain_file(found["path"]) else None

    def forget_made(self, made_id: Any) -> None:
        found = self._made.pop(made_id, None) if isinstance(made_id, str) else None
        if found is not None:
            _remove(found["path"])

    # -- staging a file that is to be restored ------------------------------
    def arriving(self) -> Path:
        """Where a backup file that is being sent to the app is written while it arrives."""
        self._ensure_folder()
        folder = self.folder / "in"
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder / ("%s.part" % uuid.uuid4().hex[:12])

    def _forget_staged(self) -> None:
        staged, self._staged = self._staged, None
        if staged is not None:
            staged.pop("store", None)
            _remove_folder(staged["work"])

    def stage(self, source: Path, remove_source: bool = True) -> Dict[str, Any]:
        """Check a backup file and get it ready to be restored. Nothing of the creator's data is touched.

        Returns what the page shows before it asks: ``{"restore_id", "file": {"created_on", "app_version",
        "data_version", "kind", "size", "older", "holds"}, "now": holds}``. ``file.holds`` is counted from the
        checked data, not read from the file's own claims; the day and the version are what the file says.
        One staged backup is kept at a time, for KEPT_FOR_SEC at most. Raises ``Refused``.
        """
        try:
            with self._working():
                self._forget_staged()
                self._ensure_folder()
                work = Path(tempfile.mkdtemp(dir=str(self.folder), prefix="work-"))
                try:
                    size = os.path.getsize(str(source)) if _is_plain_file(source) else 0
                    found = _stage(source, work)
                    counted = holds(found["store"])
                except BaseException:
                    _remove_folder(work)
                    raise
                _remove_folder(work / "in")
                manifest = found["manifest"]
                self._staged = {
                    "id": uuid.uuid4().hex[:12], "work": work, "store": found["store"], "settings": found["settings"],
                    "at": time.time(),
                    "file": {"created_on": manifest["created_on"], "app_version": manifest["app_version"],
                             "data_version": manifest["data_version"], "kind": manifest["kind"], "size": size,
                             "older": found["older"], "holds": counted},
                }
                return {"restore_id": self._staged["id"], "file": self._staged["file"], "now": holds(self.store)}
        finally:
            if remove_source:
                _remove(source)

    def stage_automatic(self, name: Any) -> Dict[str, Any]:
        """Get one of the automatic backups ready to be restored: the way back after a restore."""
        if not isinstance(name, str) or not _AUTOMATIC.fullmatch(name) or not _is_plain_file(self.folder / name):
            raise Refused("restore_not_found", "That automatic backup is no longer there.", "那一份自动备份已经不在了。", 404)
        return self.stage(self.folder / name, remove_source=False)

    def discard(self, restore_id: Any) -> bool:
        """Forget the staged backup with this id. False when there is none."""
        if self._staged is None or self._staged["id"] != restore_id:
            return False
        with self._working():
            self._forget_staged()
        return True

    # -- replacing the data -------------------------------------------------
    def restore(self, restore_id: Any) -> Dict[str, Any]:
        """Replace everything in the data folder with the staged backup. All of it, or none of it.

        Returns ``{"restored", "file", "holds", "automatic", "ai", "left_out"}``: what was put in place, what
        the folder holds now, the automatic backup of what was there before (and anything that one left out),
        and whether an AI is connected on this computer: a backup never holds a key, and a restore does not
        touch the connection. Raises ``Refused``; then the data is as it was.
        """
        with self._working():
            staged = self._staged
            if staged is not None and time.time() - staged["at"] > KEPT_FOR_SEC:
                self._forget_staged()
                staged = None
            if staged is None or staged["id"] != restore_id:
                raise _not_staged()
            try:
                self.gate.keep_changes_out()
            except _Busy:
                raise _in_use()
            try:
                if self.jobs is not None and self.jobs.unfinished():
                    raise _in_use()
                result = self._replace(staged)
            finally:
                self.gate.open()
            self._forget_staged()
            for old in self.automatic()[KEEP_AUTOMATIC:]:
                _remove(self.folder / old["name"])
            return result

    def _automatic_path(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        taken = [int(match.group(2) or 1) for match in map(_AUTOMATIC.fullmatch, os.listdir(str(self.folder)))
                 if match and match.group(1) == stamp]
        number = max(taken) + 1 if taken else 1
        return self.folder / ("before-restore-%s%s.%s" % (stamp, "" if number == 1 else "-%d" % number, EXTENSION))

    def _replace(self, staged: Dict[str, Any]) -> Dict[str, Any]:
        store, work = self.store, staged["work"]
        old = work / "old"
        _remove_folder(old)
        try:
            kept = create(store, self._automatic_path(), kind="before_restore", keep=old, check=False)
        except Refused as exc:
            raise Refused("restore_failed",
                          "What is there now could not be kept first, so nothing was replaced: %s" % exc.message,
                          "现在的内容没能先保存下来，所以没有替换任何内容：%s" % exc.message_zh, exc.status)
        kept_path = Path(kept["path"])
        try:
            settings_before = store.config_path.read_bytes() if _is_plain_file(store.config_path) else None
            if settings_before is not None:
                Store._write_bytes(old / "config.json", settings_before)
            Store._write_bytes(self._note_path, Store._encode({"work": work.name, "had_settings": settings_before is not None,
                                                               "started_at": time.time()}))
        except OSError as exc:
            _remove(kept_path)
            raise Refused("restore_failed", "The restore could not begin: %s. Everything is as it was." % str(exc)[:200],
                          "恢复没能开始：%s。所有内容都和原来一样。" % str(exc)[:200], 507)
        self.gate.keep_everyone_out()
        try:
            self._put_in_place(work / "new", staged["settings"])
        except BaseException as exc:
            try:
                # When the first step, the database, could not be done, SQLite has left it as it was and nothing
                # else was touched: there is nothing to put back.
                if self._replaced_any:
                    self._put_back(old, settings_before is not None)
            except Exception as failed:  # noqa: BLE001 - said plainly below, with where the creator's data is
                raise Refused("restore_undo_failed",
                              "The restore failed (%s), and what was there before could not all be put back (%s). It is "
                              "whole in the automatic backup %s in %s. Close the app and start it again: it then tries "
                              "once more to put everything back."
                              % (str(exc)[:120], str(failed)[:120], kept_path.name, self.folder),
                              "恢复失败了（%s），原来的内容也没能全部放回去（%s）。它完整地保存在自动备份 %s 里，位置是 %s。"
                              "请关闭应用再重新打开：它会再试一次把所有内容放回去。"
                              % (str(exc)[:120], str(failed)[:120], kept_path.name, self.folder), 500)
            _remove(self._note_path)
            _remove(kept_path)
            if isinstance(exc, Refused) or not isinstance(exc, Exception):
                raise
            raise Refused("restore_failed",
                          "The backup could not be put in place: %s. Everything is as it was before." % str(exc)[:200],
                          "备份没能放到位：%s。所有内容都和原来一样。" % str(exc)[:200], 500)
        last = dict(staged["file"], at=time.time(), automatic=kept_path.name)
        self._set_state(dict(self._state, epoch=uuid.uuid4().hex[:12], last_restore=last))
        _remove(self._note_path)
        store.__dict__.pop("_voice_reference", None)        # what was measured from the library that is no longer there
        self.put_back_at_start = False
        return {"restored": True, "file": staged["file"], "holds": holds(store), "ai": self._ai(),
                "automatic": {"name": kept_path.name, "size": kept["size"]}, "left_out": kept["left_out"]}

    def _place_files(self, folder: Path, step: Callable[[str], None]) -> None:
        """Make the small files and the earlier profiles of the data folder be exactly those in ``folder``."""
        store = self.store
        plain = {name: folder / name for name in PLAIN_FILES if _is_plain_file(folder / name)}
        earlier = {name: folder / HISTORY_FOLDER / name for name in _history_names(folder / HISTORY_FOLDER)}
        with store_module._DNA_LOCK:
            for name in PLAIN_FILES:
                step(name)
                if name in plain:
                    Store._write_bytes(store.data_dir / name, plain[name].read_bytes())
                elif os.path.lexists(str(store.data_dir / name)):
                    (store.data_dir / name).unlink()
            step(HISTORY_FOLDER)
            for name in _history_names(store.dna_history_dir):
                if name not in earlier:
                    (store.dna_history_dir / name).unlink()
            if earlier:
                store.dna_history_dir.mkdir(mode=0o700, exist_ok=True)
            for name, path in earlier.items():
                Store._write_bytes(store.dna_history_dir / name, path.read_bytes())

    def _put_in_place(self, new: Path, settings: Dict[str, Any]) -> None:
        """Replace the data with the staged folder ``new``. Any failure leaves it to the caller to put everything back."""
        store = self.store
        self._replaced_any = False
        self._before_step(DATABASE)
        _copy_database(new / DATABASE, store.db_path)
        self._replaced_any = True
        self._place_files(new, self._before_step)
        self._before_step(SETTINGS)

        def carry(config: Dict[str, Any]) -> None:
            for key in CARRIED_SETTINGS:
                config.pop(key, None)
            config.update(settings)

        store.update_config(carry)          # the AI connection and its keys stay as they are on this computer

    def _put_back(self, old: Path, had_settings: bool) -> None:
        """Put the data back from the copies a restore made before it began: the database, the files, the settings."""
        store = self.store
        _copy_database(old / DATABASE, store.db_path)
        self._place_files(old, lambda name: None)
        with store_module._CONFIG_LOCK:
            if _is_plain_file(old / "config.json"):
                Store._write_bytes(store.config_path, (old / "config.json").read_bytes())
            elif not had_settings and os.path.lexists(str(store.config_path)):
                store.config_path.unlink()
        store.__dict__.pop("_voice_reference", None)
