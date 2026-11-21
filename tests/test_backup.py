"""Tests for backing up everything into one file, and for restoring such a file.

Each class covers one thing the creator can rely on:

- a backup is ONE file that holds everything they made or taught the app, with a list of its contents, a
  SHA-256 for each, the version of the app and of the data, and the day; the copy of the database in it is
  whole also when saves happen at that moment;
- it holds no API key and nothing of the AI connection, whatever name that is stored under; nor uploads,
  exported files or earlier automatic backups;
- a data folder as the code at 3fdd3a6 leaves it, backed up and restored into an empty folder, gives the same
  rows in every table and the same files; a database from an older version is brought up to date the way an
  older data folder is;
- a file to be restored is hostile input: cut short, changed, with a path that leaves the folder, a link, more
  than it claims, a newer version, something that is no backup at all, a database with anything in it this app
  does not write. Each is refused with a code and a sentence in English and in Chinese, and nothing is touched;
- a restore is all or nothing: stopped at any step, everything is as it was; closed in the middle, the next
  start puts it back; what was there is kept first and the newest three stay; it is refused while a job runs;
- the database is never replaced as a file, so a restore works where an open file cannot be replaced;
- a restore leaves the AI connection of the computer, with its keys, as it is;
- a page that was open before a restore cannot write into the restored data: its save is refused;
- none of it is offered through MCP.

No model and no real creator material is involved. Everything is made up here, in temporary folders.
"""
import contextlib
import hashlib
import http.client
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
import tracemalloc
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from studio import backup, creator, mcp_server, outside, posted, server, wordlist
from studio.jobs import Jobs
from studio.store import ROOT, Store

WEB = Path(server.WEB_DIR)
EXAMPLE = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
SECRET, SECRET_TOO, SECRET_ELSEWHERE = "sk-not-a-real-key-1", "AIza-not-a-real-key-2", "not-a-real-key-3"
ADDRESS = "http://127.0.0.1:1234/v1"
OWN = [
    "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈 / 然后我们拿了四盒小笼包啦 / 其实家里还有嘛",
    "入冬以后脸特别干嘛 / 所以我换了一款面霜 / 结果用得最勤哈 / 其实 toner 也换了",
    "周日早上煮咖啡啦 / 然后做了个三明治 / 我老公说好吃嘛 / 结果他吃了两个哈",
]
MARKUP = "<img src=x onerror=alert(1)>"
ASSUMES = re.compile(r"\b(she|her|hers|herself|husband|wife|couple)\b", re.I)
# The layout of the database this version of the data stands for (backup.layout_fingerprint()).
LAYOUT_OF_DATA_VERSION = {1: "dc489db00a5395ce", 2: "b0612eef1d02873d", 3: "a28cf92a48fb189a"}

# The tables as the code at 3fdd3a6 creates them: the commit this feature was built on.
SCHEMA_AT_3FDD3A6 = """
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
"""
# The same two tables as a much earlier version left them: before keywords, before posted scripts, before
# transcripts had to be confirmed. ``kind`` is there, so a video can be told from a script.
OLDER_SCHEMA = """
CREATE TABLE scripts (
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
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE samples (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'script',
    timing_json TEXT,
    source_name TEXT NOT NULL DEFAULT ''
);
"""
LINES = json.dumps([{"line_no": 1, "spoken_script_zh": "今天去超市买菜啦", "audio_mode": "VOICEOVER"}], ensure_ascii=False)


# ---- helpers -----------------------------------------------------------------------------------------------------------------------
def rows(path):
    """Every row of every table of a database, with its place in the table."""
    with contextlib.closing(sqlite3.connect(str(path))) as db:
        tables = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {table: db.execute("SELECT rowid, * FROM %s ORDER BY rowid" % table).fetchall() for table in tables}


def small_files(data_dir):
    """The files beside the database that a backup holds, by name."""
    found = {name: (data_dir / name).read_bytes() for name in backup.PLAIN_FILES if (data_dir / name).is_file()}
    history = data_dir / backup.HISTORY_FOLDER
    if history.is_dir():
        for path in sorted(history.iterdir()):
            found["%s/%s" % (backup.HISTORY_FOLDER, path.name)] = path.read_bytes()
    return found


def state(store):
    """Everything a restore may change: the rows, the files, the settings."""
    settings = store.load_config() if store.config_path.is_file() else None
    return rows(store.db_path), small_files(store.data_dir), settings


def fill(store, tag="甲"):
    """Put one of everything into a data folder, the way the app does it."""
    drafted = store.create_script(
        title=tag + "面霜", notes="入冬换面霜", from_ai=True, basis={"samples": [], "profile": "example"}, keywords=["秋冬护肤"],
        lines=[{"spoken_script_zh": "入冬以后脸特别干嘛 / 所以我换了一款面霜"}, {"spoken_script_zh": "结果用得最勤哈"}],
        post={"title_options": [{"title_zh": "入冬面霜"}], "hashtags_zh": ["#面霜"]})
    store.update_script(drafted["id"], {"lines": [{"spoken_script_zh": "入冬以后脸真的很干嘛"}, {"spoken_script_zh": "结果用得最勤哈"}]})
    store.add_ai_suggestions(drafted["id"], ["换一个开头试试", "再短一点"])
    typed = store.create_script(title=tag + "超市 " + MARKUP, lines=[{"spoken_script_zh": "今天去超市买菜啦 / 人特别多嘛"}])
    first_sample = store.create_sample(title=tag + "素材一", text=OWN[0], metrics={"views": 1200, "likes": 80})
    store.pin_sample(drafted["id"], first_sample["id"], limit=10)
    store.add_sample_comments(first_sample["id"], [{"text": tag + "求链接？", "likes": 3, "sentiment": "positive", "is_question": True}])
    store.save_sample_sentiment(first_sample["id"], {"overall_sentiment": "positive", "summary_zh": tag + "好评"})
    store.create_video_plan(title=tag + "下期计划", scheduled_for="2026-11-28", script_id=drafted["id"])
    store.create_sample(title=tag + "字幕", text=OWN[1], kind="subtitle", source_name="a.srt",
                        timing={"units": 40, "spoken_sec": 12.5, "speaking_rate_cpm": 192})
    store.create_sample(title=tag + "视频", text=OWN[2], kind="video", source_name="b.mp4", is_sponsored=True,
                        timing={"units": 36, "spoken_sec": 11.0, "speaking_rate_cpm": 196, "unclear_dropped": 0})
    posted.mark(store, typed["id"], {"likes": 5, "posted_on": "2026-11-10"})     # a posted script has its item in the library
    store.upsert_trends([{"keyword": "秋冬护肤", "heat": 125000}], source="manual")
    store.upsert_trends([{"keyword": "早餐吃什么", "source_url": "https://example.com/a", "note_zh": "查到的"}], source="ai")
    store.add_lesson(script_id=drafted["id"], ai="因此我强烈推荐", final="所以我真的很推荐嘛", note="口语一点")
    store.save_brief("draft", {"notes": "入冬换面霜", "samples": ["素材一"]}, fingerprint="f1")
    store.save_creator(creator.normalize({"name": tag + "小满", "pronoun": "she", "niches": "护肤", "never": ["宝子们"]}))
    store.save_dna(dict(EXAMPLE, profile_version=tag + "-0"))
    store.save_dna(dict(EXAMPLE, profile_version=tag + "-1"))
    store.dna_path.write_bytes(b"{cut short")                                  # a profile file that cannot be read is kept as well
    store.save_dna(dict(EXAMPLE, profile_version=tag + "-2"))
    store.save_dna(dict(EXAMPLE, profile_version=tag + "-3"))
    store.save_pending({"dna": dict(EXAMPLE, profile_version=tag + "-4"), "created_at": 5.0})
    store.save_recommendations({"ideas": [{"title_zh": tag + "选题"}], "generated_at": 7.0})
    off = wordlist.entry_ids(wordlist.shipped())[0]
    store.update_config(lambda config: config.update(
        provider="openai", model="gpt-x", local_base_url=ADDRESS, trend_weight="trend",
        keys={"openai": SECRET, "gemini": SECRET_TOO}, wordlist={"off": [off], "phrases": ["包邮"]}))
    return drafted, typed


def unpacked(path):
    """``{name: bytes}`` of a backup file, the manifest left out."""
    with zipfile.ZipFile(str(path)) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name != backup.MANIFEST}


def said_by(path):
    with zipfile.ZipFile(str(path)) as archive:
        return json.loads(archive.read(backup.MANIFEST).decode("utf-8"))


def pack(path, files, manifest=None, change=None, extra=None, **said):
    """Write a backup file by hand. ``files`` is ``{name: bytes}``.

    The manifest lists them truthfully unless ``manifest`` (bytes, written as they are) is given; ``said`` sets or
    replaces fields of it. ``change`` is ``{name: function(ZipInfo)}`` for an entry that is to be written oddly,
    ``extra`` further entries the manifest does not list.
    """
    listed = {"app": backup.APP, "format": backup.FORMAT, "data_version": backup.DATA_VERSION, "app_version": None,
              "created_on": "2026-11-01", "kind": "manual",
              "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()]}
    listed.update(said)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                         # a name written twice is what one of the tests is about
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(backup.MANIFEST, manifest if manifest is not None else json.dumps(listed, ensure_ascii=False))
            for name, data in list(files.items()) + list((extra or {}).items()):
                info = zipfile.ZipInfo(name, (2026, 11, 1, 0, 0, 0))
                info.filename = name.split("\x00", 1)[0]
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                if change and name in change:
                    change[name](info)
                wanted_flags = info.flag_bits
                archive.writestr(info, data)
                info.flag_bits |= wanted_flags
    return path


def database(statements=(), layout=None, script=True):
    """The bytes of a database made with this app's tables (or ``layout``), then ``statements`` run on it."""
    folder = Path(tempfile.mkdtemp(prefix="lmv-backup-db-"))
    try:
        path = folder / "made.db"
        with contextlib.closing(sqlite3.connect(str(path))) as db:
            db.executescript(layout if layout is not None else SCHEMA_AT_3FDD3A6)
            if script:
                db.execute("INSERT INTO scripts (id, title, lines_json, created_at, updated_at) VALUES ('aaaaaaaaaaaa', '一篇', ?, 1.0, 2.0)",
                           (LINES,))
            for statement in statements:
                db.execute(*statement) if isinstance(statement, tuple) else db.execute(statement)
            db.commit()
        return path.read_bytes()
    finally:
        shutil.rmtree(str(folder), ignore_errors=True)


def stated_sizes(data, name, size):
    """A copy of the bytes of an archive in which the entry ``name`` says, in both places, that it unpacks to ``size`` bytes."""
    data = bytearray(data)
    directory_at = struct.unpack("<L", data[-6:-2])[0]
    at = directory_at
    while data[at:at + 4] == b"PK\x01\x02":
        name_length, extra_length, remark_length = struct.unpack("<3H", data[at + 28:at + 34])
        local_at = struct.unpack("<L", data[at + 42:at + 46])[0]
        if bytes(data[at + 46:at + 46 + name_length]).decode("utf-8") == name:
            data[at + 24:at + 28] = struct.pack("<L", size)
            data[local_at + 22:local_at + 26] = struct.pack("<L", size)
            return bytes(data)
        at += 46 + name_length + extra_length + remark_length
    raise AssertionError("no entry called %s" % name)


class Killed(BaseException):
    """Stands for the app being closed at that very moment: nothing after it runs."""


class Case(unittest.TestCase):
    """A data folder of its own, and what the running app holds on to for backups (``Backups``)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-backup-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "home" / "data")
        self.backups = backup.Backups(self.store)
        self.count = 0

    def made_from(self, store, **options):
        """A backup of ``store``, as a file."""
        self.count += 1
        path = self.tmp / "made" / ("backup-%d.%s" % (self.count, backup.EXTENSION))
        backup.create(store, path, **options)
        return path

    def other(self, tag="乙"):
        """A second data folder with other content, and a backup of it."""
        there = Store(self.tmp / ("other-%d" % (self.count + 1)) / "data")
        fill(there, tag)
        return there, self.made_from(there)

    def staged(self, path, backups=None):
        """Hand a copy of the file to the app the way an upload arrives: the app removes what it was given."""
        self.count += 1
        copy = self.tmp / ("arrived-%d.part" % self.count)
        shutil.copyfile(str(path), str(copy))
        try:
            return (backups or self.backups).stage(copy)
        finally:
            self.assertFalse(copy.exists(), "the file that arrived is not kept")

    def refused(self, work):
        with self.assertRaises(backup.Refused) as caught:
            work()
        refusal = caught.exception
        self.assertTrue(refusal.message and refusal.message_zh, refusal.code)
        self.assertNotEqual(refusal.message, refusal.message_zh)
        self.assertRegex(refusal.message_zh, "[\u4e00-\u9fff]")
        self.assertNotIn("internal error", refusal.message.lower())
        return refusal

    def left_behind(self):
        """What is in the app's backup folder beside automatic backups and the file that names the data."""
        if not self.backups.folder.is_dir():
            return []
        return sorted(path.name for path in self.backups.folder.iterdir()
                      if not path.name.startswith("before-restore-") and path.name != "state.json")

    def not_restored(self, path, code, reason=None):
        """The file is refused when it is handed over, with this code, and nothing in the data folder has changed."""
        before = state(self.store)
        refusal = self.refused(lambda: self.staged(path))
        self.assertEqual(refusal.code, code, refusal.extra)
        if reason is not None:
            self.assertEqual(str(refusal.extra.get("reason", "")).split(":")[0], reason)
        self.assertEqual(state(self.store), before)
        self.assertEqual(self.left_behind(), [])
        self.assertIsNone(self.backups._staged)
        return refusal


# ---------------------------------------------------------------------------------------------- 1. what a backup holds
class ContentsTest(Case):
    def setUp(self):
        super().setUp()
        fill(self.store)

    def test_it_is_one_file_with_a_list_of_its_contents_the_versions_and_the_day(self):
        path = self.made_from(self.store)
        said = said_by(path)
        self.assertEqual((said["app"], said["format"], said["data_version"]), ("learn-my-voice", backup.FORMAT, backup.DATA_VERSION))
        self.assertEqual(said["created_on"], time.strftime("%Y-%m-%d"))
        self.assertIn("app_version", said)                                   # None from the source; a release names its version
        self.assertIsNone(said["app_version"])
        held = unpacked(path)
        self.assertEqual(sorted(item["name"] for item in said["files"]), sorted(held))
        for item in said["files"]:
            self.assertEqual((item["size"], item["sha256"]), (len(held[item["name"]]), hashlib.sha256(held[item["name"]]).hexdigest()), item["name"])
        self.assertEqual(set(said["not_included"]), {"api_keys", "ai_connection", "transcription_setup", "uploads", "exports", "logs",
                                                     "automatic_backups"})
        self.assertTrue(path.name.endswith(".lmvbackup"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_a_release_says_which_version_made_it(self):
        build = {"from_source": False, "version": "0.2.0-beta.3", "commit": "a" * 40, "built_on": "2026-11-18"}
        with mock.patch.object(backup.buildinfo, "current", return_value=build):
            path = self.made_from(self.store)
        self.assertEqual(said_by(path)["app_version"], "0.2.0-beta.3")
        self.assertEqual(self.staged(path)["file"]["app_version"], "0.2.0-beta.3")

    def test_everything_the_creator_made_or_taught_the_app_is_in_it(self):
        path = self.made_from(self.store)
        held = unpacked(path)
        files = small_files(self.store.data_dir)
        self.assertEqual(sorted(files), sorted(name for name in held if name not in ("studio.db", "settings.json")))
        self.assertEqual({name: held[name] for name in files}, files)        # byte for byte, the unreadable earlier profile too
        self.assertEqual(sum(name.endswith(".unreadable") for name in files), 1)
        self.assertEqual(sum(name.startswith("voice_dna_history/") for name in files), 3)
        inside = self.tmp / "inside.db"
        inside.write_bytes(held["studio.db"])
        self.assertEqual(rows(inside), rows(self.store.db_path))
        self.assertEqual(sorted(rows(inside)), ["ai_suggestions", "briefs", "lessons", "metric_snapshots",
                                                "sample_comments", "sample_sentiments", "samples", "script_pins",
                                                "scripts", "trends", "video_plans"])
        for table, found in rows(inside).items():
            self.assertTrue(found, table)                                    # the made-up folder has something in every table
        self.assertEqual(json.loads(held["settings.json"].decode("utf-8")),
                         {"trend_weight": "trend", "wordlist": self.store.load_config()["wordlist"]})

    def test_no_key_and_nothing_of_the_ai_connection_is_in_it_whatever_it_is_stored_as(self):
        # What a later version might store beside the keys it stores today: none of it may travel either.
        self.store.update_config(lambda config: config.update(
            anthropic_key=SECRET_ELSEWHERE, endpoints=[{"url": "https://models.example/v1", "key": SECRET_ELSEWHERE}]))
        path = self.made_from(self.store)
        raw = path.read_bytes()
        held = unpacked(path)
        for secret in (SECRET, SECRET_TOO, SECRET_ELSEWHERE, "models.example", ADDRESS, "gpt-x"):
            self.assertNotIn(secret.encode("utf-8"), raw, secret)
            for name, data in held.items():
                self.assertNotIn(secret.encode("utf-8"), data, "%s in %s" % (secret, name))
        self.assertEqual(sorted(json.loads(held["settings.json"].decode("utf-8"))), ["trend_weight", "wordlist"])
        self.assertNotIn(b"config.json", raw)

    def test_uploads_exports_and_automatic_backups_are_not_in_it(self):
        for folder, name in (("uploads", "0123456789ab.mp4"), ("exports", "kit.zip"), ("backups", "before-restore-20261101-101010.lmvbackup")):
            (self.store.data_dir / folder).mkdir(exist_ok=True)
            (self.store.data_dir / folder / name).write_bytes(b"not for a backup " * 50)
        (self.store.data_dir / "notes of my own.txt").write_text("something a person put there", encoding="utf-8")
        held = unpacked(self.made_from(self.store))
        self.assertEqual(sorted(name.split("/")[0] for name in held),
                         sorted(["creator.json", "recommendations.json", "settings.json", "studio.db", "voice_dna.json",
                                 "voice_pending.json"] + ["voice_dna_history"] * 3))
        for data in held.values():
            self.assertNotIn(b"not for a backup", data)

    def test_a_link_in_the_data_folder_is_not_followed(self):
        if not hasattr(os, "symlink") or os.name != "posix":
            self.skipTest("links are made here the POSIX way")
        outside_file = self.tmp / "elsewhere.json"
        outside_file.write_text('{"not": "the creator profile"}', encoding="utf-8")
        self.store.creator_path.unlink()
        os.symlink(str(outside_file), str(self.store.creator_path))
        held = unpacked(self.made_from(self.store))
        self.assertNotIn("creator.json", held)

    def test_the_copy_of_the_database_is_whole_while_saves_happen(self):
        """Each save here is several statements in one step. A copy must hold all of a step or none of it."""
        stop = threading.Event()
        failed = []

        def keep_saving():
            try:
                while not stop.is_set():
                    script = self.store.create_script(title="一会儿就删", lines=[{"spoken_script_zh": "今天去超市买菜啦"}])
                    self.store.add_ai_suggestions(script["id"], ["换一个开头试试", "再短一点"])
                    self.store.delete_script(script["id"])       # the suggestions and the script go in one step
            except Exception as exc:                             # noqa: BLE001 - reported below
                failed.append(exc)

        saver = threading.Thread(target=keep_saving)
        saver.start()
        try:
            for number in range(8):
                held = unpacked(self.made_from(self.store))
                copy = self.tmp / ("copy-%d.db" % number)
                copy.write_bytes(held["studio.db"])
                with contextlib.closing(sqlite3.connect(str(copy))) as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                    orphans = db.execute("SELECT COUNT(*) FROM ai_suggestions WHERE script_id NOT IN (SELECT id FROM scripts)").fetchone()[0]
                    each = [found[0] for found in db.execute("SELECT COUNT(*) FROM ai_suggestions GROUP BY script_id")]
                self.assertEqual(orphans, 0)
                self.assertEqual([count for count in each if count != 2], [])
        finally:
            stop.set()
            saver.join(15)
        self.assertEqual(failed, [])

    def test_what_does_not_fit_is_left_out_and_named(self):
        self.store.recommendations_path.write_bytes(b" " * (backup.MAX_SMALL_FILE_BYTES + 1))
        with mock.patch.object(backup, "MAX_HISTORY_FILES", 2):
            path = self.tmp / "made" / "partial.lmvbackup"
            made = backup.create(self.store, path)
        self.assertEqual(made["left_out"], [{"name": "recommendations.json", "why": "too_large"},
                                            {"name": "voice_dna_history", "why": "too_many", "count": 1}])
        held = unpacked(path)
        self.assertNotIn("recommendations.json", held)
        earlier = sorted(name for name in held if name.startswith("voice_dna_history/"))
        chronological = sorted(
            (name for name in small_files(self.store.data_dir) if name.startswith("voice_dna_history/")),
            key=lambda name: backup._history_order(name.split("/", 1)[1]),
        )
        self.assertEqual(earlier, sorted(chronological[-2:]))
        self.assertEqual(self.staged(path)["file"]["holds"]["scripts"], 2)     # and such a backup is one the app restores

    def test_the_app_does_not_hand_out_a_backup_it_would_itself_refuse(self):
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("CREATE VIEW not_ours AS SELECT 1")
            db.commit()
        refusal = self.refused(lambda: self.made_from(self.store))
        self.assertEqual((refusal.code, refusal.extra.get("reason")), ("backup_data_damaged", "not_a_table"))
        self.assertEqual([path.name for path in (self.tmp / "made").iterdir()], [])

    def test_the_page_is_told_what_a_backup_would_hold(self):
        info = self.backups.info()
        self.assertEqual(info["holds"], {"scripts": 2, "samples": 4, "trends": 2, "lessons": 1, "voice_profile": "own",
                                         "earlier_profiles": 2, "creator": True, "pending_voice": True, "ideas": True})
        self.assertEqual((info["automatic"], info["last_restore"], info["put_back_at_start"], info["busy"]), ([], None, False, False))
        self.assertEqual((info["limits"], info["extension"], info["keep_automatic"]), ({"max_bytes": backup.MAX_BACKUP_BYTES}, "lmvbackup", 3))
        self.assertEqual(info["ai"], {"configured": True, "manual": False})
        self.assertNotIn(SECRET, json.dumps(info))
        self.store.dna_path.write_bytes(b"{cut short")
        self.assertEqual(backup.holds(self.store)["voice_profile"], "unreadable")
        self.assertEqual(backup.holds(Store(self.tmp / "empty"))["voice_profile"], "none")


# ---------------------------------------------------------------------------------------------- 2. the same again after a restore
class RoundTripTest(Case):
    def test_a_folder_backed_up_and_restored_into_an_empty_one_has_the_same_rows_and_the_same_files(self):
        there, path = self.other("甲")
        preview = self.staged(path)
        self.assertEqual(preview["now"]["scripts"], 0)
        self.assertEqual(preview["file"]["holds"], backup.holds(there))
        self.assertEqual((preview["file"]["created_on"], preview["file"]["older"], preview["file"]["kind"]),
                         (time.strftime("%Y-%m-%d"), False, "manual"))
        self.assertEqual(state(self.store)[0], rows(Store(self.tmp / "untouched").db_path))     # nothing yet: the creator was only shown it
        result = self.backups.restore(preview["restore_id"])
        self.assertEqual(result["holds"], backup.holds(there))
        self.assertEqual(rows(self.store.db_path), rows(there.db_path))
        self.assertEqual(small_files(self.store.data_dir), small_files(there.data_dir))
        self.assertEqual(self.left_behind(), [])
        # and it works: what the app reads there is what it read in the folder the backup was made of
        self.assertEqual(self.store.list_scripts(), there.list_scripts())
        self.assertEqual(self.store.list_samples(), there.list_samples())
        self.assertEqual(self.store.load_dna(), there.load_dna())
        self.assertEqual([entry["id"] for entry in self.store.list_dna_history()], [entry["id"] for entry in there.list_dna_history()])
        self.assertEqual(wordlist.payload(self.store), wordlist.payload(there))
        script = self.store.list_scripts()[0]
        self.assertEqual(self.store.update_script(script["id"], {"title": "恢复以后改的"})["title"], "恢复以后改的")

    def test_a_database_as_the_code_at_3fdd3a6_leaves_it(self):
        """The tables of that commit, written out here, with rows and files put there without this version's code."""
        old = self.tmp / "as-it-was" / "data"
        old.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(str(old / "studio.db"))) as db:
            db.executescript(SCHEMA_AT_3FDD3A6)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS samples_by_script ON samples (script_id) WHERE script_id IS NOT NULL")
            db.execute("INSERT INTO scripts (id, title, lines_json, ai_lines_json, keywords_json, basis_json, created_at, updated_at)"
                       " VALUES ('aaaaaaaaaaaa', ?, ?, ?, '[\"秋冬护肤\"]', '{\"samples\": []}', 10.0, 11.5)", ("面霜 " + MARKUP, LINES, LINES))
            db.execute("INSERT INTO samples (id, title, text, likes, kind, timing_json, source_name, needs_review, origin, script_id,"
                       " posted_on, note_url, ai_json, snapshot, created_at, updated_at) VALUES ('bbbbbbbbbbbb', '发过的', ?, 5, 'script',"
                       " NULL, '', 1, 'script', 'aaaaaaaaaaaa', '2026-11-10', 'https://www.xiaohongshu.com/x', '{\"lines\": []}', 'snap', 12.0, 13.0)",
                       (OWN[0],))
            db.execute("INSERT INTO trends (id, key, keyword, heat, created_at, updated_at) VALUES ('cccccccccccc', '秋冬护肤', '秋冬护肤', 1200, 1.0, 2.0)")
            db.execute("INSERT INTO lessons (id, script_id, ai, final, created_at) VALUES ('dddddddddddd', 'aaaaaaaaaaaa', '因此', '所以', 3.0)")
            db.execute("INSERT INTO briefs (id, kind, payload_json, created_at) VALUES ('eeeeeeeeeeee', 'draft', '{\"notes\": \"x\"}', 4.0)")
            db.execute("INSERT INTO ai_suggestions (script_id, text, created_at) VALUES ('aaaaaaaaaaaa', '换一个开头', 5.0)")
            db.commit()
        (old / "creator.json").write_text(json.dumps({"name": "小满", "pronoun": "she"}, ensure_ascii=False, indent=2), encoding="utf-8")
        (old / "voice_dna.json").write_text(json.dumps(dict(EXAMPLE, profile_version="hers"), ensure_ascii=False, indent=2), encoding="utf-8")
        (old / "voice_dna_history").mkdir()
        (old / "voice_dna_history" / "voice_dna-20261101-090000.json").write_text(json.dumps(EXAMPLE, ensure_ascii=False), encoding="utf-8")
        (old / "config.json").write_text(json.dumps({"provider": "gemini", "model": "m", "keys": {"gemini": SECRET}, "trend_weight": "mine"}),
                                         encoding="utf-8")
        there = Store(old)                                       # upgrades 3fdd3a6 DB with line_count, preview, script_pins
        was = rows(there.db_path), small_files(old)
        self.assertEqual(there.get_script("aaaaaaaaaaaa")["title"], "面霜 " + MARKUP)
        path = self.made_from(there)
        self.assertEqual((rows(there.db_path), small_files(old)), was)             # and making a backup changes nothing in it
        preview = self.staged(path)
        self.assertFalse(preview["file"]["older"])
        self.backups.restore(preview["restore_id"])
        self.assertEqual((rows(self.store.db_path), small_files(self.store.data_dir)), was)
        self.assertEqual(self.store.load_config()["trend_weight"], "mine")
        self.assertEqual(self.store.get_posted_sample("aaaaaaaaaaaa")["note_url"], "https://www.xiaohongshu.com/x")

    def test_the_layout_the_data_version_stands_for(self):
        """Fails when a table or a column was added or changed and backup.DATA_VERSION stayed as it was."""
        self.assertEqual(
            LAYOUT_OF_DATA_VERSION.get(backup.DATA_VERSION), backup.layout_fingerprint(),
            "The tables of studio.db are no longer the ones data version %d stands for. Raise backup.DATA_VERSION by one, "
            "add the new fingerprint (%s) to LAYOUT_OF_DATA_VERSION in this file, and keep the older entries: a backup "
            "made before the change names the older version, and a newer one is refused by an older app."
            % (backup.DATA_VERSION, backup.layout_fingerprint()))
        self.assertEqual(sorted(LAYOUT_OF_DATA_VERSION), list(range(1, backup.DATA_VERSION + 1)))
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            db.executescript(SCHEMA_AT_3FDD3A6)
            at_base = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
        if backup.DATA_VERSION == 1:
            self.assertEqual(sorted(backup.reference_layout()["tables"]), at_base)

    def test_every_table_the_app_makes_is_written_the_plain_way_a_backup_can_check(self):
        layout = backup.reference_layout()
        self.assertEqual(sorted(layout["tables"]), ["ai_suggestions", "briefs", "lessons", "metric_snapshots",
                                                    "sample_comments", "sample_sentiments", "samples", "script_pins",
                                                    "scripts", "trends", "video_plans"])
        self.assertEqual(sorted(layout["indexes"]), ["ai_suggestions_by_script", "metric_snapshots_by_sample",
                                                     "sample_comments_by_sample", "samples_by_script"])
        columns = {name: [column for column, _kind, _written in found if column] for name, found in layout["tables"].items()}
        self.assertEqual(columns["scripts"][:3], ["id", "title", "mode"])
        self.assertEqual(columns["ai_suggestions"], ["script_id", "text", "created_at"])
        for table, added in layout["added"].items():
            self.assertTrue(added <= set(columns[table]), table)
        # ... and so is every table an older database has after it was brought up to date, column by column.
        old = self.tmp / "older" / "data"
        old.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(str(old / "studio.db"))) as db:
            db.executescript(OLDER_SCHEMA)
            db.commit()
        Store(old)
        with contextlib.closing(sqlite3.connect(str(old / "studio.db"))) as db:
            for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall():
                found = backup._table(sql)
                self.assertIsNotNone(found, sql)
                self.assertEqual(sorted(column for column, _kind, _written in found[1] if column), sorted(columns[name]), name)

    def older_database(self):
        return database(layout=OLDER_SCHEMA, script=False, statements=[
            ("INSERT INTO scripts (id, title, lines_json, created_at, updated_at) VALUES ('aaaaaaaaaaaa', '旧的一篇', ?, 1.0, 2.0)", (LINES,)),
            ("INSERT INTO samples (id, title, text, created_at, updated_at, kind) VALUES ('bbbbbbbbbbbb', '自己写的', ?, 3.0, 4.0, 'script')", (OWN[0],)),
            ("INSERT INTO samples (id, title, text, created_at, updated_at, kind, source_name) VALUES ('cccccccccccc', '转写的', ?, 5.0, 6.0, 'video', 'a.mp4')",
             (OWN[1],)),
        ])

    def test_a_backup_from_an_older_version_is_brought_up_to_date_the_way_an_older_folder_is(self):
        data = self.older_database()
        direct = self.tmp / "opened-directly" / "data"
        direct.mkdir(parents=True)
        (direct / "studio.db").write_bytes(data)
        opened = Store(direct)                                   # what opening that folder with this version makes of it
        path = pack(self.tmp / "older.lmvbackup", {"studio.db": data, "creator.json": b'{"name": "\xe5\xb0\x8f\xe6\xbb\xa1"}'})
        preview = self.staged(path)
        self.assertTrue(preview["file"]["older"])
        self.assertEqual((preview["file"]["holds"]["scripts"], preview["file"]["holds"]["samples"], preview["file"]["created_on"]), (1, 2, "2026-11-01"))
        self.backups.restore(preview["restore_id"])
        self.assertEqual(rows(self.store.db_path), rows(opened.db_path))
        waits = {sample["title"]: sample["needs_review"] for sample in self.store.list_samples()}
        self.assertEqual(waits, {"自己写的": False, "转写的": True})         # a transcript from before confirming existed waits, as it does there
        self.assertEqual(self.store.get_script("aaaaaaaaaaaa")["keywords"], [])
        self.assertEqual(self.store.load_creator(), {"name": "小满"})
        self.assertIsNotNone(self.store.create_sample(title="新的", text="今天去超市买菜啦"))

    def test_a_restore_can_be_undone_from_the_automatic_backup(self):
        fill(self.store, "原")
        before = state(self.store)
        there, path = self.other("新")
        result = self.backups.restore(self.staged(path)["restore_id"])
        self.assertEqual(rows(self.store.db_path), rows(there.db_path))
        kept = self.backups.automatic()
        self.assertEqual([entry["name"] for entry in kept], [result["automatic"]["name"]])
        self.assertEqual(said_by(self.backups.folder / kept[0]["name"])["kind"], "before_restore")
        preview = self.backups.stage_automatic(kept[0]["name"])
        self.assertEqual((preview["file"]["kind"], preview["file"]["holds"]["scripts"]), ("before_restore", 2))
        self.assertTrue((self.backups.folder / kept[0]["name"]).is_file())    # looking at it does not use it up
        self.backups.restore(preview["restore_id"])
        self.assertEqual(state(self.store), before)                           # the rows, the files and the settings with their keys
        self.assertEqual(len(self.backups.automatic()), 2)
        for name in ("before-restore-20261101-101010.lmvbackup", "../state.json", "nothing", 7, None):
            self.assertEqual(self.refused(lambda: self.backups.stage_automatic(name)).code, "restore_not_found")

    def test_the_newest_three_automatic_backups_are_kept(self):
        fill(self.store, "原")
        _there, path = self.other("新")
        names = []
        for _ in range(5):
            names.append(self.backups.restore(self.staged(path)["restore_id"])["automatic"]["name"])
        self.assertEqual(len(set(names)), 5)                                  # also when several are made within one second
        self.assertEqual([entry["name"] for entry in self.backups.automatic()], names[:1:-1])
        self.assertEqual(self.backups.info()["last_restore"]["automatic"], names[-1])
        self.assertEqual(self.left_behind(), [])

    def test_a_restore_leaves_the_ai_connection_of_this_computer_as_it_is(self):
        self.store.update_config(lambda config: config.update(provider="manual", model="", keys={"gemini": "the-key-on-this-computer"},
                                                              trend_weight="mine", wordlist={"off": [], "phrases": ["这里的说法"]}))
        there, path = self.other("新")                             # that folder is connected to another service, with other keys
        result = self.backups.restore(self.staged(path)["restore_id"])
        config = self.store.load_config()
        self.assertEqual((config["provider"], config["model"], config["keys"], config["local_base_url"]),
                         ("manual", "", {"gemini": "the-key-on-this-computer"}, ""))
        self.assertEqual((config["trend_weight"], config["wordlist"]), ("trend", there.load_config()["wordlist"]))
        self.assertEqual(result["ai"], {"configured": True, "manual": True})
        # On a computer with no connection, the answer says so: the page then tells the creator to type the key in again.
        empty = Store(self.tmp / "new-computer" / "data")
        result = backup.Backups(empty)
        answer = result.restore(self.staged(path, result)["restore_id"])
        self.assertEqual(answer["ai"], {"configured": False, "manual": False})
        self.assertEqual(empty.load_config()["keys"], {})
        self.assertEqual(empty.load_config()["provider"], "")

    def test_settings_a_backup_does_not_carry_are_cleared_not_kept(self):
        self.store.update_config(lambda config: config.update(trend_weight="mine", wordlist={"off": [], "phrases": ["这里的说法"]}))
        path = pack(self.tmp / "bare.lmvbackup", {"studio.db": database()})
        self.backups.restore(self.staged(path)["restore_id"])
        config = self.store.load_config()
        self.assertEqual(config["trend_weight"], "balanced")                 # as in a folder that never had the setting
        self.assertNotIn("wordlist", config)
        self.assertEqual(wordlist.payload(self.store)["phrases"], [])


# ---------------------------------------------------------------------------------------------- 3. a file from outside
class HostileFileTest(Case):
    def setUp(self):
        super().setUp()
        fill(self.store, "原")
        self.there, self.good = self.other("新")
        self.held = unpacked(self.good)

    def write(self, data, name="hostile.lmvbackup"):
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_something_that_is_no_backup_at_all(self):
        for data in (b"just some text that somebody saved under the wrong name\n" * 20, b"\x00" * 4096, b"PK", b"",
                     json.dumps({"app": "learn-my-voice"}).encode("utf-8"), self.held["studio.db"], b"%PDF-1.7 " + b"x" * 500):
            refusal = self.not_restored(self.write(data), "backup_not_a_backup")
            self.assertEqual(refusal.status, 415)

    def test_a_backup_that_was_cut_short(self):
        whole = self.good.read_bytes()
        for length in (len(whole) - 1, len(whole) - 22, len(whole) * 2 // 3, len(whole) // 3, 100, 40):
            self.not_restored(self.write(whole[:length]), "backup_incomplete")
        self.assertIn("cut short", self.not_restored(self.write(whole[:-5]), "backup_incomplete").message)

    def test_a_wrong_sha_256(self):
        said = said_by(self.good)
        for item in said["files"]:
            if item["name"] == "creator.json":
                item["sha256"] = hashlib.sha256(b"something else").hexdigest()
        path = pack(self.tmp / "wrong.lmvbackup", self.held, manifest=json.dumps(said))
        self.not_restored(path, "backup_changed", "sha256")
        # the same when the content was changed and the list was not: one character of the profile, the length kept
        changed = dict(self.held, **{"creator.json": self.held["creator.json"].replace(b"she", b"he ")})
        self.assertNotEqual(changed["creator.json"], self.held["creator.json"])
        self.not_restored(pack(self.tmp / "changed.lmvbackup", changed, manifest=json.dumps(said_by(self.good))), "backup_changed", "sha256")
        # and a byte of the archive itself that was flipped is found by the archive's own checksum
        raw = bytearray(self.good.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        refusal = self.not_restored(self.write(bytes(raw)), None) if False else self.refused(lambda: self.staged(self.write(bytes(raw))))
        self.assertIn(refusal.code, ("backup_unreadable", "backup_changed", "backup_unexpected_content", "backup_database_damaged"))

    def test_a_list_of_contents_that_does_not_name_what_is_there(self):
        said = said_by(self.good)
        fewer = dict(said, files=[item for item in said["files"] if item["name"] != "creator.json"])
        self.not_restored(pack(self.tmp / "a.lmvbackup", self.held, manifest=json.dumps(fewer)), "backup_changed", "listing")
        without = {name: data for name, data in self.held.items() if name != "creator.json"}
        self.not_restored(pack(self.tmp / "b.lmvbackup", without, manifest=json.dumps(said)), "backup_changed", "listing")
        twice = dict(said, files=said["files"][:-1] + [said["files"][0]])
        self.not_restored(pack(self.tmp / "c.lmvbackup", self.held, manifest=json.dumps(twice)), "backup_changed", "listing")
        shorter = dict(said, files=[dict(item, size=item["size"] + 1) if item["name"] == "creator.json" else item for item in said["files"]])
        self.not_restored(pack(self.tmp / "d.lmvbackup", self.held, manifest=json.dumps(shorter)), "backup_changed", "size")

    def test_a_path_that_leaves_the_folder(self):
        names = ["../creator.json", "../../planted.json", "/etc/planted.json", "voice_dna_history/../../planted.json",
                 "voice_dna_history/../creator.json", "C:\\planted.json", "..\\planted.json", "voice_dna_history\\voice_dna-20261101-090000.json",
                 "./creator.json", "creator.json/", "voice_dna_history/", "data/creator.json", "voice_dna_history/voice_dna-1.json",
                 "voice_dna_history/sub/voice_dna-20261101-090000.json", "config.json", "state.json", "notes.txt", "studio.db-journal",
                 "uploads/0123456789ab.mp4", "CREATOR.JSON", "creator.json\x00.txt", " creator.json"]
        for number, name in enumerate(names):
            path = pack(self.tmp / ("leaves-%d.lmvbackup" % number), dict(self.held, **{name: b'{"planted": true}'}))
            refusal = self.not_restored(path, "backup_unexpected_content")
            self.assertIn(refusal.extra["reason"], ("name", "kind"), name)
        planted = [path for path in self.tmp.rglob("*planted*")]
        self.assertEqual(planted, [])
        self.assertFalse((self.store.data_dir.parent / "creator.json").exists())
        self.assertEqual(sorted(path.name for path in self.store.data_dir.iterdir()),
                         sorted(["backups", "config.json", "creator.json", "recommendations.json", "studio.db", "voice_dna.json",
                                 "voice_dna_history", "voice_pending.json"]))

    def test_a_name_twice_a_link_a_lock_and_another_way_of_packing(self):
        self.not_restored(pack(self.tmp / "twice.lmvbackup", self.held, extra={"creator.json": b"{}"}), "backup_unexpected_content", "name")

        def link(info):
            info.external_attr = (stat.S_IFLNK | 0o777) << 16

        def folder(info):
            info.external_attr = (stat.S_IFDIR | 0o700) << 16

        def locked(info):
            info.flag_bits |= 0x1

        def stored(info):
            info.compress_type = zipfile.ZIP_STORED

        linked = dict(self.held, **{"recommendations.json": b"/etc/passwd"})
        self.not_restored(pack(self.tmp / "link.lmvbackup", linked, change={"recommendations.json": link}), "backup_unexpected_content", "kind")
        self.not_restored(pack(self.tmp / "folder.lmvbackup", self.held, change={"creator.json": folder}), "backup_unexpected_content", "kind")
        self.not_restored(pack(self.tmp / "locked.lmvbackup", self.held, change={"creator.json": locked}), "backup_unexpected_content", "kind")
        for method in ("ZIP_BZIP2", "ZIP_LZMA"):
            def other_way(info, method=method):
                info.compress_type = getattr(zipfile, method)
            try:
                path = pack(self.tmp / (method + ".lmvbackup"), self.held, change={"creator.json": other_way})
            except RuntimeError:
                continue                                        # this Python was built without that way of packing
            self.not_restored(path, "backup_unexpected_content", "kind")
        # packed the plain way, without squeezing, is still a backup
        self.assertEqual(self.staged(pack(self.tmp / "stored.lmvbackup", self.held, change={"studio.db": stored}))["file"]["holds"]["scripts"], 2)

    def test_an_archive_that_is_not_written_the_way_the_app_writes_one(self):
        whole = self.good.read_bytes()
        with zipfile.ZipFile(str(self.write(whole, "remark.lmvbackup")), "a") as archive:
            archive.comment = b"a remark at the end"
        self.not_restored(self.tmp / "remark.lmvbackup", "backup_incomplete")
        self.not_restored(self.write(b"PK\x03\x04" + b"\x00" * 200 + whole, "in-front.lmvbackup"), "backup_unexpected_content", "archive_form")
        self.not_restored(self.write(whole + b"\x00" * 10, "behind.lmvbackup"), "backup_incomplete")
        # the 64-bit form, which a file of this size never needs, lets an archive say again how many entries it has
        marked = bytearray(whole)
        marked[-22 - 20:-22 - 16] = b"PK\x06\x07"
        self.not_restored(self.write(bytes(marked), "64.lmvbackup"), "backup_unexpected_content", "archive_form")

    def test_one_that_unpacks_to_far_more_than_it_claims(self):
        big = b"\x00" * (30 * 1024 * 1024)                       # 30 MB that pack into a few thousand bytes
        said = said_by(self.good)
        # The list says the profile is as small as it was; the archive holds 30 MB under that name.
        swollen = pack(self.tmp / "swollen.lmvbackup", dict(self.held, **{"creator.json": big}), manifest=json.dumps(said))
        self.assertLess(swollen.stat().st_size, 200 * 1024)
        refusal = self.not_restored(swollen, "backup_unpacks_too_far")
        self.assertEqual(refusal.status, 413)
        # The archive itself says so as well, in both places a size is written: no more than the stated size is unpacked.
        honest = pack(self.tmp / "honest.lmvbackup", dict(self.held, **{"studio.db": self.held["studio.db"] + big}))
        lying = stated_sizes(honest.read_bytes(), "studio.db", len(self.held["studio.db"]))
        said_small = dict(said_by(honest), files=said["files"])
        with zipfile.ZipFile(str(self.write(lying, "lying.lmvbackup"))) as archive:
            self.assertEqual(archive.getinfo("studio.db").file_size, len(self.held["studio.db"]))
        written = []
        real_open = open

        def counting_open(*args, **kwargs):
            handle = real_open(*args, **kwargs)
            if "w" in str(args[1] if len(args) > 1 else kwargs.get("mode", "r")):
                written.append(handle)
            return handle

        refusal = self.refused(lambda: self.staged(self.tmp / "lying.lmvbackup"))
        self.assertIn(refusal.code, ("backup_unreadable", "backup_unpacks_too_far", "backup_changed"))
        self.assertEqual(self.left_behind(), [])
        del counting_open, written, said_small
        # A file other than the database may be 4 MB at most, the manifest 1 MB, and all of it together has a ceiling.
        self.not_restored(pack(self.tmp / "large-profile.lmvbackup", dict(self.held, **{"creator.json": big})), "backup_unpacks_too_far", "entry")
        self.not_restored(pack(self.tmp / "large-list.lmvbackup", self.held, manifest=b" " * (backup.MAX_MANIFEST_BYTES + 1) + b"{}"),
                          "backup_unpacks_too_far", "entry")
        with mock.patch.object(backup, "MAX_UNPACKED_BYTES", 100 * 1024):
            self.not_restored(self.good, "backup_unpacks_too_far")
        with mock.patch.object(backup, "MAX_BACKUP_BYTES", 1024):
            self.assertEqual(self.not_restored(self.good, "backup_too_large").status, 413)

    def test_one_that_says_it_holds_more_files_than_a_backup_can(self):
        earlier = {"voice_dna_history/voice_dna-20261101-%06d.json" % number: b"{}" for number in range(backup.MAX_ENTRIES)}
        path = pack(self.tmp / "many.lmvbackup", dict(self.held, **earlier))
        with mock.patch.object(backup.zipfile, "ZipFile", side_effect=AssertionError("the list of entries was read")):
            self.not_restored(path, "backup_unpacks_too_far", "entries")        # known from the last 22 bytes alone
        # and one that says so only there
        whole = bytearray(self.good.read_bytes())
        whole[-14:-10] = struct.pack("<2H", 60000, 60000)
        self.not_restored(self.write(bytes(whole), "says-many.lmvbackup"), "backup_unpacks_too_far", "entries")
        whole[-14:-10] = struct.pack("<2H", 3, 3)
        self.not_restored(self.write(bytes(whole), "says-few.lmvbackup"), "backup_unexpected_content", "archive_form")

    def test_one_from_a_newer_version(self):
        for field in ("data_version", "format"):
            path = pack(self.tmp / (field + ".lmvbackup"), self.held, **{field: getattr(backup, field.upper()) + 1})
            refusal = self.not_restored(path, "backup_newer")
            self.assertEqual(refusal.status, 409)
            self.assertIn("newer version", refusal.message)
            self.assertIn("Update the app", refusal.message)
            self.assertIn("更新", refusal.message_zh)
        # the number is what is gone by: the same content under the version this app writes is restored
        self.assertEqual(self.staged(pack(self.tmp / "same.lmvbackup", self.held))["file"]["holds"]["scripts"], 2)

    def test_a_manifest_that_cannot_be_believed(self):
        good = said_by(self.good)
        bad = [b"not json", b"[1, 2]", b'{"app": "learn-my-voice", "format": NaN}', b"[" * 100000, "\ud800".encode("utf-8", "surrogatepass"),
               json.dumps(dict(good, app="another-app")), json.dumps({key: value for key, value in good.items() if key != "created_on"}),
               json.dumps(dict(good, created_on="2026-13-40")), json.dumps(dict(good, created_on="yesterday")),
               json.dumps(dict(good, created_on=20261101)), json.dumps(dict(good, app_version=MARKUP)),
               json.dumps(dict(good, app_version="x" * 41)), json.dumps(dict(good, app_version=["0.2.0"])),
               json.dumps(dict(good, kind="another")), json.dumps(dict(good, format=0)), json.dumps(dict(good, data_version=True)),
               json.dumps(dict(good, data_version="1")), json.dumps(dict(good, files="studio.db")),
               json.dumps(dict(good, files=[dict(item, sha256="0" * 63) for item in good["files"]])),
               json.dumps(dict(good, files=[dict(item, size=-1) for item in good["files"]])),
               json.dumps(dict(good, files=[[item["name"]] for item in good["files"]]))]
        for number, manifest in enumerate(bad):
            path = pack(self.tmp / ("manifest-%d.lmvbackup" % number), self.held, manifest=manifest)
            refusal = self.refused(lambda: self.staged(path))
            self.assertIn(refusal.code, ("backup_unreadable", "backup_changed"), manifest[:60])
            self.assertEqual(self.left_behind(), [])
        without = pack(self.tmp / "none.lmvbackup", self.held)
        with zipfile.ZipFile(str(without)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}
        with zipfile.ZipFile(str(without), "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        self.not_restored(without, "backup_unexpected_content", "name")

    def with_database(self, data, name="db.lmvbackup"):
        return pack(self.tmp / name, dict(self.held, **{"studio.db": data}))

    def test_a_database_with_anything_in_it_this_app_does_not_write(self):
        plain = "CREATE TABLE scripts (id TEXT PRIMARY KEY, title TEXT NOT NULL, lines_json TEXT NOT NULL DEFAULT '[]', " \
                "post_json TEXT NOT NULL DEFAULT '{}', ai_lines_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL%s)%s"
        cases = [
            ("a view", ["CREATE VIEW seen AS SELECT * FROM scripts"], None, "not_a_table"),
            ("a trigger", ["CREATE TRIGGER fires AFTER INSERT ON scripts BEGIN DELETE FROM samples; END"], None, "not_a_table"),
            ("a table of another name", ["CREATE TABLE planted (id TEXT)"], None, "unknown_table"),
            ("a table SQLite works out", ["CREATE VIRTUAL TABLE planted USING fts5(body)"], None, None),
            ("an index of another name", ["CREATE INDEX planted ON scripts (title)"], None, "unknown_index"),
            ("an index that works something out", ["DROP INDEX ai_suggestions_by_script",
                                                   "CREATE INDEX ai_suggestions_by_script ON ai_suggestions (lower(script_id))"], None, "unknown_index"),
            ("a column of another name", ["ALTER TABLE scripts ADD COLUMN planted TEXT"], None, "unknown_column"),
            ("a column of another type", [], SCHEMA_AT_3FDD3A6.replace("heat INTEGER,", "heat TEXT,"), "unknown_column"),
            ("a check", [], plain % (", CHECK (length(title) < 9)", ""), "table_not_plain"),
            ("a check on a column", [], (plain % ("", "")).replace("title TEXT NOT NULL", "title TEXT NOT NULL CHECK (title <> '')"), "table_not_plain"),
            ("a default that is worked out", [], (plain % ("", "")).replace("DEFAULT '[]'", "DEFAULT (hex(randomblob(9)))"), "table_not_plain"),
            ("a computed column", [], plain % (", shouted TEXT AS (upper(title))", ""), "table_not_plain"),
            ("a reference to another table", [], plain % (", owner TEXT REFERENCES samples (id)", ""), "table_not_plain"),
            ("a collation", [], (plain % ("", "")).replace("title TEXT NOT NULL", "title TEXT NOT NULL COLLATE NOCASE"), "table_not_plain"),
            ("no row ids", [], plain % ("", " WITHOUT ROWID"), "table_not_plain"),
            ("a quoted name", [], plain.replace("CREATE TABLE scripts", 'CREATE TABLE "scripts"') % ("", ""), "table_not_plain"),
            ("a remark in the table", [], plain % (" /* remark */", ""), "table_not_plain"),
            ("a column missing", [], "CREATE TABLE scripts (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
             "column_missing"),
            ("no scripts", [], "CREATE TABLE samples (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, is_sponsored INTEGER NOT NULL "
                               "DEFAULT 0, views INTEGER, likes INTEGER, collects INTEGER, comments INTEGER, created_at REAL NOT NULL, "
                               "updated_at REAL NOT NULL)", "no_scripts_table"),
        ]
        for number, (what, statements, layout, reason) in enumerate(cases):
            try:
                data = database(statements, layout=layout, script=False)
            except sqlite3.OperationalError:
                continue                                        # this SQLite cannot even make it (an old one has no computed columns, or no fts5)
            refusal = self.not_restored(self.with_database(data, "db-%d.lmvbackup" % number), "backup_database_damaged")
            if reason is not None:
                self.assertEqual(refusal.extra["reason"], reason, what)
            self.assertIn("Nothing was replaced", refusal.message)

    def test_a_database_with_values_no_column_of_this_app_holds(self):
        row = "INSERT INTO scripts (id, title, lines_json, created_at, updated_at) VALUES ('bbbbbbbbbbbb', %s, %s, %s, 2.0)"
        cases = [
            ("bytes where a title belongs", row % ("x'00ff10'", "'[]'", "1.0"), "value_of_another_kind"),
            ("text where a time belongs", row % ("'t'", "'[]'", "'yesterday'"), "value_of_another_kind"),
            ("bytes where a number belongs", "INSERT INTO samples (id, title, text, views, created_at, updated_at) VALUES ('s', 't', 'x', x'01', 1.0, 1.0)",
             "value_of_another_kind"),
            ("lines that are not JSON", row % ("'t'", "'not json'", "1.0"), "script_not_json"),
            ("lines that are not a list", row % ("'t'", "'{\"a\": 1}'", "1.0"), "script_lines"),
            ("lines that are not objects", row % ("'t'", "'[1, 2]'", "1.0"), "script_lines"),
            ("a line whose words are not text", row % ("'t'", "'[{\"spoken_script_zh\": [\"a\"]}]'", "1.0"), "script_lines"),
            ("lines nested without end", row % ("'t'", "'" + "[" * 5000 + "'", "1.0"), "script_not_json"),
            ("post copy that is a list", "INSERT INTO scripts (id, title, post_json, created_at, updated_at) VALUES ('b', 't', '[]', 1.0, 2.0)", "script_fields"),
            ("keywords that are not texts", "INSERT INTO scripts (id, title, keywords_json, created_at, updated_at) VALUES ('b', 't', '[1]', 1.0, 2.0)",
             "script_fields"),
            ("an id twice", "INSERT INTO ai_suggestions (script_id, text, created_at) VALUES ('a', 't', 'never')", "value_of_another_kind"),
        ]
        for number, (what, statement, reason) in enumerate(cases):
            refusal = self.not_restored(self.with_database(database([statement]), "value-%d.lmvbackup" % number), "backup_database_damaged")
            self.assertEqual(refusal.extra["reason"], reason, what)
        # a number written as text where the app writes a number is read as that number, as SQLite itself does
        fine = database(["INSERT INTO samples (id, title, text, views, created_at, updated_at) VALUES ('s', 't', 'x', '12', 1.0, 1.0)"])
        self.backups.restore(self.staged(self.with_database(fine, "fine.lmvbackup"))["restore_id"])
        self.assertEqual(self.store.list_samples()[0]["views"], 12)

    def test_a_text_longer_than_any_the_app_stores(self):
        with mock.patch.object(backup, "MAX_CELL_CHARS", 1000):
            long_text = database([("INSERT INTO samples (id, title, text, created_at, updated_at) VALUES ('s', 't', ?, 1.0, 1.0)", ("长" * 1001,))])
            self.assertEqual(self.not_restored(self.with_database(long_text), "backup_database_damaged").extra["reason"], "value_of_another_kind")
            fits = database([("INSERT INTO samples (id, title, text, created_at, updated_at) VALUES ('s', 't', ?, 1.0, 1.0)", ("长" * 1000,))])
            self.assertEqual(self.staged(self.with_database(fits, "fits.lmvbackup"))["file"]["holds"]["samples"], 1)

    def test_a_database_that_is_damaged_or_is_none(self):
        self.assertEqual(self.not_restored(self.with_database(b"not a database " * 100), "backup_database_damaged").extra["reason"], "not_a_database")
        self.assertEqual(self.not_restored(self.with_database(b""), "backup_database_damaged").extra["reason"], "not_a_database")
        good = database([("INSERT INTO samples (id, title, text, created_at, updated_at) VALUES (?, 't', ?, 1.0, 1.0)", ("s%d" % n, OWN[0] * 40))
                         for n in range(40)])
        self.assertGreater(len(good), 40000)
        cut = self.not_restored(self.with_database(good[:len(good) // 2]), "backup_database_damaged")
        self.assertIn(cut.extra["reason"].split(":")[0], ("integrity_check", "sqlite"))
        scribbled = bytearray(good)
        for at in range(8192, len(scribbled), 4096):
            scribbled[at:at + 64] = b"\xff" * 64
        hurt = self.not_restored(self.with_database(bytes(scribbled)), "backup_database_damaged")
        self.assertIn(hurt.extra["reason"].split(":")[0], ("integrity_check", "sqlite", "value_of_another_kind"))
        # one that keeps a log file beside it, which a backup never holds
        folder = self.tmp / "wal"
        folder.mkdir()
        with contextlib.closing(sqlite3.connect(str(folder / "w.db"))) as db:
            db.execute("PRAGMA journal_mode = WAL").fetchall()
            db.executescript(SCHEMA_AT_3FDD3A6)
            db.commit()
        logged = (folder / "w.db").read_bytes()
        if logged[18:20] == b"\x02\x02":
            self.assertEqual(self.not_restored(self.with_database(logged), "backup_database_damaged").extra["reason"], "not_a_database")
        # two library items for one script: the rule the database itself holds is not broken by a restore
        twice = database(["INSERT INTO samples (id, title, text, script_id, created_at, updated_at) VALUES ('s%d', 't', 'x', 'aaaaaaaaaaaa', 1.0, 1.0)" % n
                          for n in (1, 2)])
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            pass
        refusal = self.not_restored(self.with_database(twice, "twice.lmvbackup"), "backup_database_damaged")
        self.assertIn(refusal.extra["reason"].split(":")[0], ("cannot_bring_up_to_date", "integrity_check", "sqlite"))

    def test_data_the_pages_could_not_show_is_found_out_before_anything_is_replaced(self):
        with mock.patch.object(backup.setup_status, "status", side_effect=KeyError("created_at")):
            refusal = self.not_restored(self.good, "backup_unusable")
        self.assertIn("KeyError", refusal.extra["reason"])
        self.assertIn("nothing was replaced", refusal.message)
        with mock.patch.object(backup.creator, "for_page", return_value={"name": b"bytes cannot be sent to a page"}):
            self.not_restored(self.good, "backup_unusable")

    def test_files_beside_the_database_are_taken_as_they_are_and_stop_nothing(self):
        odd = dict(self.held)
        odd.update({"creator.json": b"[1, 2, 3]", "voice_dna.json": b"{cut short", "voice_pending.json": b"\xff\xfe not text",
                    "recommendations.json": b"[" * 50000, "settings.json": b"not json"})
        preview = self.staged(pack(self.tmp / "odd.lmvbackup", odd))
        self.assertEqual((preview["file"]["holds"]["voice_profile"], preview["file"]["holds"]["creator"], preview["file"]["holds"]["pending_voice"],
                          preview["file"]["holds"]["ideas"]), ("unreadable", False, False, False))
        self.backups.restore(preview["restore_id"])
        self.assertEqual(self.store.dna_path.read_bytes(), b"{cut short")
        self.assertTrue(self.store.dna_unreadable())
        self.assertIsNone(self.store.load_creator())
        self.assertEqual(self.store.load_config()["trend_weight"], "balanced")

    def test_settings_in_a_backup_cannot_reach_the_ai_connection(self):
        before = self.store.load_config()
        planted = {"provider": "local", "model": "theirs", "local_base_url": "https://collect.example/v1", "keys": {"openai": "theirs"},
                   "trend_weight": "mine", "wordlist": {"off": ["x" * 500, 7], "phrases": ["一" * 500]}, "another_setting": {"url": "https://collect.example"}}
        hostile = dict(self.held, **{"settings.json": json.dumps(planted).encode("utf-8")})
        self.backups.restore(self.staged(pack(self.tmp / "settings.lmvbackup", hostile))["restore_id"])
        after = self.store.load_config()
        for key in ("provider", "model", "local_base_url", "keys"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["trend_weight"], "mine")
        self.assertNotIn("wordlist", after)                                   # a change to the word list the word list cannot use is not carried
        self.assertNotIn("another_setting", after)
        self.assertNotIn("collect.example", self.store.config_path.read_text(encoding="utf-8"))
        for weight in ("heaviest", 5, None, ["trend"]):
            hostile["settings.json"] = json.dumps({"trend_weight": weight}).encode("utf-8")
            self.backups.restore(self.staged(pack(self.tmp / "weight.lmvbackup", hostile))["restore_id"])
            self.assertEqual(self.store.load_config()["trend_weight"], "balanced", weight)

    def test_nothing_is_left_of_a_file_that_was_looked_at_and_not_restored(self):
        preview = self.staged(self.good)
        work = [path for path in self.backups.folder.iterdir() if path.name.startswith("work-")]
        self.assertEqual(len(work), 1)
        self.assertEqual(sorted(path.name for path in work[0].iterdir()), ["new"])
        self.assertNotIn(SECRET.encode("utf-8"), b"".join(path.read_bytes() for path in work[0].rglob("*") if path.is_file()))
        second = self.staged(self.good)                                       # looking at another one forgets the first
        self.assertNotEqual(second["restore_id"], preview["restore_id"])
        self.assertEqual(len([path for path in self.backups.folder.iterdir() if path.name.startswith("work-")]), 1)
        self.assertEqual(self.refused(lambda: self.backups.restore(preview["restore_id"])).code, "restore_not_found")
        self.assertFalse(self.backups.discard(preview["restore_id"]))
        self.assertTrue(self.backups.discard(second["restore_id"]))
        self.assertEqual(self.left_behind(), [])
        third = self.staged(self.good)
        with mock.patch.object(backup, "KEPT_FOR_SEC", -1):                   # nobody came back for it in time
            self.assertEqual(self.refused(lambda: self.backups.restore(third["restore_id"])).code, "restore_not_found")
        self.assertEqual(self.left_behind(), [])
        self.staged(self.good)
        backup.Backups(self.store)                                            # the app starts again: what an earlier run left is cleared
        self.assertEqual(self.left_behind(), [])


# ---------------------------------------------------------------------------------------------- 4. all of it, or none of it
class AllOrNothingTest(Case):
    def setUp(self):
        super().setUp()
        fill(self.store, "原")
        self.there, self.good = self.other("新")
        self.before = state(self.store)

    def stop_at(self, step, error):
        def before_step(name):
            if name == step:
                raise error
        self.backups._before_step = before_step

    def test_a_restore_that_fails_at_any_step_leaves_everything_as_it_was(self):
        steps = [backup.DATABASE] + list(backup.PLAIN_FILES) + [backup.HISTORY_FOLDER, backup.SETTINGS]
        for step in steps:
            preview = self.staged(self.good)
            self.stop_at(step, OSError(28, "No space left on device"))
            refusal = self.refused(lambda: self.backups.restore(preview["restore_id"]))
            self.assertEqual((refusal.code, refusal.status), ("restore_failed", 500), step)
            self.assertIn("Everything is as it was before", refusal.message)
            self.assertIn("No space left", refusal.message)
            self.assertEqual(state(self.store), self.before, step)
            self.assertEqual((self.backups.automatic(), self.backups.epoch, self.backups._note_path.exists()), ([], "0", False), step)
            self.assertIsNone(self.backups.info()["last_restore"])
        # the file is still waiting, and once nothing stands in the way it is restored
        self.backups._before_step = lambda name: None
        self.backups.restore(preview["restore_id"])
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))

    def test_the_app_closed_in_the_middle_goes_on_with_what_it_had(self):
        for step in (backup.PLAIN_FILES[0], backup.PLAIN_FILES[-1], backup.HISTORY_FOLDER, backup.SETTINGS):
            backups = backup.Backups(self.store)
            preview = self.staged(self.good, backups)

            def before_step(name, step=step):
                if name == step:
                    raise Killed()
            backups._before_step = before_step
            backups._put_back = mock.Mock(side_effect=Killed())      # nothing runs after the moment it was closed
            with self.assertRaises(Killed):
                backups.restore(preview["restore_id"])
            self.assertNotEqual(state(self.store), self.before, step)           # half of each: the new database, some old files
            self.assertTrue(backups._note_path.is_file())
            started = backup.Backups(self.store)                                # the app starts again
            self.assertEqual(state(self.store), self.before, step)
            self.assertTrue(started.put_back_at_start)
            self.assertTrue(started.info()["put_back_at_start"])
            self.assertNotEqual(started.epoch, "0")                             # a page left open starts afresh
            self.assertFalse(started._note_path.exists())
            self.assertEqual([path.name for path in started.folder.iterdir() if path.name.startswith("work-")], [])
            self.assertEqual(len(started.automatic()), 1)                       # what was kept before the restore is still there
            for kept in started.automatic():
                (started.folder / kept["name"]).unlink()
            again = backup.Backups(self.store)                                  # and the start after that has nothing to undo
            self.assertFalse(again.put_back_at_start)
            self.assertEqual(state(self.store), self.before)

    def test_closed_before_anything_was_replaced_there_is_nothing_to_undo(self):
        preview = self.staged(self.good)
        self.stop_at(backup.DATABASE, Killed())
        with self.assertRaises(Killed):
            self.backups.restore(preview["restore_id"])
        self.assertEqual(state(self.store), self.before)
        started = backup.Backups(self.store)
        self.assertEqual(state(self.store), self.before)
        self.assertEqual(started.left_behind() if hasattr(started, "left_behind") else self.left_behind(), [])

    def test_a_note_that_is_not_one_of_the_apps_is_not_acted_on(self):
        self.backups._ensure_folder()
        for note in ('{"work": "../../home"}', '{"work": 7}', "[1]", "not json", '{"work": "work-nothere"}'):
            self.backups._note_path.write_text(note, encoding="utf-8")
            started = backup.Backups(self.store)
            self.assertEqual(state(self.store), self.before, note)
            self.assertFalse(started.put_back_at_start)
            self.assertFalse(started._note_path.exists())

    def test_when_what_was_there_cannot_be_put_back_the_creator_is_told_where_it_is(self):
        preview = self.staged(self.good)
        self.stop_at(backup.HISTORY_FOLDER, OSError("the disk went away"))
        self.backups._put_back = mock.Mock(side_effect=OSError("and it is still away"))
        refusal = self.refused(lambda: self.backups.restore(preview["restore_id"]))
        self.assertEqual(refusal.code, "restore_undo_failed")
        kept = self.backups.automatic()
        self.assertEqual(len(kept), 1)
        for said in (kept[0]["name"], str(self.backups.folder), "the disk went away", "still away", "start it again"):
            self.assertIn(said, refusal.message)
        self.assertIn(kept[0]["name"], refusal.message_zh)
        self.assertEqual(backup.holds(self.there)["scripts"], said_holds(self, self.backups.folder / kept[0]["name"]))
        started = backup.Backups(self.store)                                    # starting again does put it back
        self.assertEqual(state(self.store), self.before)
        self.assertTrue(started.put_back_at_start)

    def test_what_was_there_is_kept_before_anything_is_replaced(self):
        seen = []

        def before_step(name):
            if name == backup.DATABASE:
                kept = self.backups.automatic()
                seen.append((len(kept), state(self.store) == self.before, self.backups._note_path.is_file()))
                held = unpacked(self.backups.folder / kept[0]["name"])
                copy = self.tmp / "kept.db"
                copy.write_bytes(held["studio.db"])
                seen.append(rows(copy) == self.before[0])
                seen.append({name: data for name, data in held.items() if name not in ("studio.db", "settings.json")} == self.before[1])
                seen.append(all(SECRET.encode("utf-8") not in data for data in held.values()))

        self.backups._before_step = before_step
        self.backups.restore(self.staged(self.good)["restore_id"])
        self.assertEqual(seen, [(1, True, True), True, True, True])

    def test_a_restore_is_refused_while_a_job_runs(self):
        jobs = Jobs()
        backups = backup.Backups(self.store, jobs)
        release = threading.Event()
        job = jobs.start("transcribe", lambda progress: release.wait(30))
        try:
            self.assertEqual(jobs.unfinished(), 1)
            self.assertTrue(backups.info()["busy"])
            preview = self.staged(self.good, backups)                           # looking at a backup is not stopped by it
            refusal = self.refused(lambda: backups.restore(preview["restore_id"]))
            self.assertEqual((refusal.code, refusal.status), ("restore_busy", 409))
            self.assertIn("Nothing was replaced", refusal.message)
            self.assertEqual(state(self.store), self.before)
            self.assertEqual(backups.automatic(), [])
            self.assertTrue(self.made_from(self.store).is_file())               # nor is making a backup
        finally:
            release.set()
        jobs.wait(job["id"], 15)
        self.assertEqual(jobs.unfinished(), 0)
        backups.restore(preview["restore_id"])                                  # the same checked file, once the job is done
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))

    def test_a_restore_is_refused_while_another_change_is_being_saved(self):
        preview = self.staged(self.good)
        inside, release = threading.Event(), threading.Event()

        def another_request():
            with self.backups.passage(True, None):
                inside.set()
                release.wait(30)

        other = threading.Thread(target=another_request)
        other.start()
        try:
            self.assertTrue(inside.wait(15))
            self.assertEqual(self.refused(lambda: self.backups.restore(preview["restore_id"])).code, "restore_busy")
            self.assertEqual(state(self.store), self.before)
        finally:
            release.set()
            other.join(15)
        with self.backups.passage(True, None):                                  # the restore's own request is not in its own way
            self.backups.restore(preview["restore_id"])
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))

    def test_while_the_data_is_replaced_every_request_waits_and_a_save_from_before_is_refused(self):
        preview = self.staged(self.good)
        reached, release = threading.Event(), threading.Event()

        def before_step(name):
            if name == backup.PLAIN_FILES[0]:                                   # the database is the new one, the files are still the old
                reached.set()
                release.wait(30)

        self.backups._before_step = before_step
        outcome = {}

        def restore():
            with self.backups.passage(True, "0"):
                outcome["restore"] = self.backups.restore(preview["restore_id"])

        def read():
            with self.backups.passage(False, "0"):
                outcome["read"] = (backup.holds(self.store), self.backups.epoch)

        def save():
            try:
                with self.backups.passage(True, "0"):
                    outcome["save"] = self.store.create_script(title="旧页面写的")
            except backup.Refused as refusal:
                outcome["save"] = refusal

        threads = [threading.Thread(target=restore)]
        threads[0].start()
        try:
            self.assertTrue(reached.wait(15))
            threads += [threading.Thread(target=read), threading.Thread(target=save)]
            for thread in threads[1:]:
                thread.start()
            time.sleep(0.4)
            self.assertEqual(sorted(outcome), [])                               # neither has got anywhere: nobody sees half of each
        finally:
            release.set()
            for thread in threads:
                thread.join(15)
        self.assertEqual(outcome["read"], (backup.holds(self.there), self.backups.epoch))
        self.assertNotEqual(self.backups.epoch, "0")
        self.assertEqual(outcome["save"].code, "stale_data")
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))    # nothing of the old page got in

    def test_the_database_is_not_replaced_as_a_file(self):
        """Where an open file cannot be replaced (Windows), a restore must still work: so the file stays, its content changes."""
        if os.name == "posix":
            was = os.stat(str(self.store.db_path)).st_ino
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as held_open:
            self.assertEqual(held_open.execute("SELECT COUNT(*) FROM scripts").fetchone()[0], 2)
            self.assertEqual(held_open.execute("SELECT title FROM samples ORDER BY rowid LIMIT 1").fetchone()[0], "原素材一")
            self.backups.restore(self.staged(self.good)["restore_id"])
            # the connection that was open all along reads the restored rows
            self.assertEqual(held_open.execute("SELECT title FROM samples ORDER BY rowid LIMIT 1").fetchone()[0], "新素材一")
        if os.name == "posix":
            self.assertEqual(os.stat(str(self.store.db_path)).st_ino, was)
        self.assertEqual([name for name in os.listdir(str(self.store.data_dir)) if name.startswith("studio.db") and name != "studio.db"], [])

    def test_another_program_that_holds_the_database_makes_a_restore_fail_cleanly(self):
        preview = self.staged(self.good)
        with contextlib.closing(sqlite3.connect(str(self.store.db_path), isolation_level=None)) as holder:
            holder.execute("BEGIN IMMEDIATE")                                   # it is about to write: others may read, nobody else may write
            holder.execute("UPDATE scripts SET title = title")
            started = time.time()
            with mock.patch.object(backup, "DATABASE_PATIENCE_SEC", 0.3):
                refusal = self.refused(lambda: self.backups.restore(preview["restore_id"]))
            self.assertLess(time.time() - started, 15)
            self.assertEqual(refusal.code, "restore_failed")
            self.assertIn("Everything is as it was before", refusal.message)
            holder.execute("ROLLBACK")
        self.assertEqual(state(self.store), self.before)
        self.assertEqual((self.backups.automatic(), self.backups._note_path.exists(), self.backups.epoch), ([], False, "0"))
        self.backups.restore(preview["restore_id"])                             # and when it has let go, the restore goes through
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))

    def test_one_backup_or_restore_at_a_time(self):
        self.assertTrue(self.backups._one_at_a_time.acquire(False))
        try:
            for work in (self.backups.make, lambda: self.staged(self.good), lambda: self.backups.restore("0" * 12)):
                refusal = self.refused(work)
                self.assertEqual((refusal.code, refusal.status), ("backup_busy", 409))
        finally:
            self.backups._one_at_a_time.release()
        self.assertEqual(state(self.store), self.before)

    def test_what_was_measured_from_the_old_library_is_not_used_for_the_new_one(self):
        self.store._voice_reference = (self.store.samples_version(), "measured from the library that was there")
        self.backups.restore(self.staged(self.good)["restore_id"])
        self.assertFalse(hasattr(self.store, "_voice_reference"))


def said_holds(case, path):
    """How many scripts an automatic backup holds: counted by looking at it the way a restore would."""
    scratch = backup.Backups(Store(case.tmp / "scratch-look" / "data"))
    return case.staged(path, scratch)["file"]["holds"]["scripts"]


# ---------------------------------------------------------------------------------------------- 5. over the wire
class World:
    """Stands in for studio.outside in the server: a Downloads folder of its own, and nothing is opened."""

    def __init__(self, downloads):
        self.downloads = downloads
        self.shown = []

    def downloads_folder(self):
        return self.downloads

    def save(self, name, data, folders):
        return outside.save(name, data, folders)

    def link_problem(self, url):
        return outside.link_problem(url)

    def open_link(self, url):
        return True

    def reveal(self, path):
        self.shown.append(str(path))
        return True


class ServerCase(Case):
    def setUp(self):
        super().setUp()
        self.downloads = self.tmp / "Downloads"
        self.world = World(self.downloads)
        self.calls = []
        self.httpd = server.make_server(0, self.store, lambda *args: self.calls.append(args) or {}, None, self.world)
        self.port = self.httpd.server_address[1]
        self.backups = self.httpd.app.backups
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None, body=None, headers=None):
        """One request; returns ``(status, the answer, its headers)``."""
        head = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        answer = json.loads(raw.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else raw
        return res.status, answer, res.headers

    def hand_over(self, data, **headers):
        head = {"Content-Type": "application/octet-stream"}
        head.update(headers)
        return self.send("PUT", "/api/restore/file", body=data, headers=head)

    def said(self, answer):
        """A refusal has a code and a sentence in each language, and is never the last-resort answer."""
        self.assertIn("error", answer)
        self.assertNotEqual(answer["error"], "internal")
        self.assertRegex(answer.get("message_zh", ""), "[\u4e00-\u9fff]")
        self.assertRegex(answer["message"], "[A-Za-z]{4}")
        return answer["error"]


class RequestTest(ServerCase):
    def setUp(self):
        super().setUp()
        fill(self.store, "原")
        self.there, self.good = self.other("新")

    def test_a_backup_is_made_and_then_fetched_as_a_download(self):
        status, info, _ = self.send("GET", "/api/backup")
        self.assertEqual((status, info["holds"]["scripts"], info["automatic"]), (200, 2, []))
        status, made, _ = self.send("POST", "/api/backup", {})
        self.assertEqual(status, 201)
        self.assertEqual(sorted(made), ["created_on", "holds", "id", "kind", "left_out", "name", "size"])
        self.assertEqual(made["name"], "learn-my-voice-backup-%s.lmvbackup" % time.strftime("%Y-%m-%d"))
        status, data, headers = self.send("GET", "/api/backup/%s/file" % made["id"])
        self.assertEqual((status, len(data), headers["Content-Type"]), (200, made["size"], "application/octet-stream"))
        self.assertEqual(headers["Content-Disposition"], "attachment; filename*=UTF-8''" + made["name"])
        self.assertEqual((headers["X-Frame-Options"], headers["X-Content-Type-Options"], headers["Content-Security-Policy"]),
                         ("DENY", "nosniff", server.CONTENT_SECURITY_POLICY))
        path = self.tmp / "fetched.lmvbackup"
        path.write_bytes(data)
        copy = self.tmp / "fetched.db"
        copy.write_bytes(unpacked(path)["studio.db"])
        self.assertEqual(rows(copy), rows(self.store.db_path))
        self.assertNotIn(SECRET.encode("utf-8"), data)
        # only what was made is handed out, and only the newest two are kept waiting
        upper_wrong = "ABCDEF123456" if made["id"].upper() == made["id"] else made["id"].upper()
        for wrong in ("0" * 12, "../state.json", upper_wrong):
            status, answer, _ = self.send("GET", "/api/backup/%s/file" % wrong)
            self.assertEqual(status, 404, wrong)
        ids = [self.send("POST", "/api/backup", {})[1]["id"] for _ in range(2)]
        self.assertEqual([self.send("GET", "/api/backup/%s/file" % made_id)[0] for made_id in [made["id"]] + ids], [404, 200, 200])
        self.assertEqual(self.said(self.send("GET", "/api/backup/%s/file" % made["id"])[1]), "not_found")
        self.assertEqual(len(list((self.backups.folder / "out").iterdir())), 2)

    def test_in_the_desktop_app_it_is_put_where_exports_are_put(self):
        status, saved, _ = self.send("POST", "/api/backup/save", {})
        self.assertEqual(status, 201)
        path = Path(saved["path"])
        self.assertEqual((path.parent, saved["folder"], saved["in_downloads"]), (self.downloads, str(self.downloads), True))
        self.assertEqual(saved["name"], "learn-my-voice-backup-%s.lmvbackup" % time.strftime("%Y-%m-%d"))
        self.assertEqual(path.stat().st_size, saved["size"])
        self.assertEqual(self.staged(path)["file"]["holds"]["scripts"], 2)      # what lies there is a whole backup
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        status, again, _ = self.send("POST", "/api/backup/save", {})            # a file that is there is never replaced
        self.assertEqual(again["name"], "learn-my-voice-backup-%s (2).lmvbackup" % time.strftime("%Y-%m-%d"))
        self.assertEqual(self.send("POST", "/api/outside/show", {"path": saved["path"]})[1], {"shown": True})
        self.assertEqual(self.world.shown, [saved["path"]])
        self.assertEqual(list((self.backups.folder / "out").iterdir()), [])     # no second copy stays in the data folder
        # Downloads cannot be written to: beside the data folder, as an export is
        self.world.downloads = self.tmp / "a-file-not-a-folder"
        self.world.downloads.write_text("x", encoding="utf-8")
        status, elsewhere, _ = self.send("POST", "/api/backup/save", {})
        self.assertEqual((status, elsewhere["in_downloads"], Path(elsewhere["path"]).parent), (201, False, self.store.data_dir.resolve().parent / "exports"))

    def test_the_whole_way_over_the_wire(self):
        status, preview, _ = self.hand_over(self.good.read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(sorted(preview), ["file", "now", "restore_id"])
        self.assertEqual((preview["file"]["holds"], preview["now"]), (backup.holds(self.there), backup.holds(self.store)))
        self.assertEqual(preview["file"]["size"], self.good.stat().st_size)
        self.assertNotEqual(rows(self.store.db_path), rows(self.there.db_path))           # nothing changes until the creator says yes
        status, result, headers = self.send("POST", "/api/restore", {"restore_id": preview["restore_id"]})
        self.assertEqual((status, result["restored"]), (200, True))
        self.assertEqual(rows(self.store.db_path), rows(self.there.db_path))
        self.assertEqual(result["ai"], {"configured": True, "manual": False})             # the connection of this computer, untouched
        self.assertEqual(self.store.load_config()["keys"], {"openai": SECRET, "gemini": SECRET_TOO})
        status, info, _ = self.send("GET", "/api/backup")
        self.assertEqual([entry["name"] for entry in info["automatic"]], [result["automatic"]["name"]])
        self.assertEqual((info["last_restore"]["created_on"], info["last_restore"]["automatic"]), (time.strftime("%Y-%m-%d"), result["automatic"]["name"]))
        # every page shows the restored data without the app being started again
        self.assertEqual(sorted(script["title"] for script in self.send("GET", "/api/scripts")[1]["scripts"]),
                         sorted(script["title"] for script in self.there.list_scripts()))
        self.assertEqual(self.send("GET", "/api/creator")[1]["name"], "新小满")
        self.assertEqual(self.send("GET", "/api/dna")[1]["dna"]["profile_version"], "新-3")
        self.assertEqual(self.send("GET", "/api/state")[1]["sample_count"], 4)
        # and back again, from the automatic backup
        status, undo, _ = self.send("POST", "/api/restore/automatic", {"name": result["automatic"]["name"]})
        self.assertEqual((status, undo["file"]["kind"]), (200, "before_restore"))
        self.assertEqual(self.send("POST", "/api/restore", {"restore_id": undo["restore_id"]})[0], 200)
        self.assertEqual(self.send("GET", "/api/creator")[1]["name"], "原小满")
        answers = json.dumps([preview, result, info, undo])
        self.assertNotIn("NaN", answers)
        self.assertNotIn("Infinity", answers)
        self.assertNotIn(SECRET, answers)

    def test_a_checked_backup_can_be_put_aside(self):
        preview = self.hand_over(self.good.read_bytes())[1]
        self.assertEqual(self.send("DELETE", "/api/restore/" + preview["restore_id"])[1], {"ok": True, "discarded": True})
        self.assertEqual(self.send("DELETE", "/api/restore/" + preview["restore_id"])[1], {"ok": True, "discarded": False})
        status, answer, _ = self.send("POST", "/api/restore", {"restore_id": preview["restore_id"]})
        self.assertEqual((status, self.said(answer)), (404, "restore_not_found"))
        for wrong in (None, 7, "", "x" * 12, ["a"]):
            status, answer, _ = self.send("POST", "/api/restore", {"restore_id": wrong})
            self.assertEqual((status, answer["error"]), (404, "restore_not_found"), wrong)
        status, answer, _ = self.send("POST", "/api/restore/automatic", {"name": "../../config.json"})
        self.assertEqual((status, self.said(answer)), (404, "restore_not_found"))

    def test_every_refusal_reaches_the_page_with_a_code_and_a_sentence_in_each_language(self):
        before = state(self.store)
        held = unpacked(self.good)
        whole = self.good.read_bytes()
        cases = [
            (b"not a backup at all, just something that was saved", 415, "backup_not_a_backup"),
            (whole[:len(whole) // 2], 400, "backup_incomplete"),
            (pack(self.tmp / "newer", held, data_version=backup.DATA_VERSION + 1).read_bytes(), 409, "backup_newer"),
            (pack(self.tmp / "leaves", dict(held, **{"../planted.json": b"{}"})).read_bytes(), 400, "backup_unexpected_content"),
            (pack(self.tmp / "sha", held, manifest=json.dumps(dict(said_by(self.good), files=[dict(item, sha256="0" * 64)
                                                                   for item in said_by(self.good)["files"]]))).read_bytes(), 400, "backup_changed"),
            (pack(self.tmp / "big", dict(held, **{"creator.json": b"\x00" * (5 * 1024 * 1024)})).read_bytes(), 413, "backup_unpacks_too_far"),
            (pack(self.tmp / "db", dict(held, **{"studio.db": database(["CREATE VIEW seen AS SELECT 1"])})).read_bytes(), 400, "backup_database_damaged"),
            (pack(self.tmp / "list", held, manifest=b"not json").read_bytes(), 400, "backup_unreadable"),
        ]
        for data, expected, code in cases:
            status, answer, _ = self.hand_over(data)
            self.assertEqual((status, self.said(answer)), (expected, code))
            self.assertIn("reason", answer) if code not in ("backup_not_a_backup", "backup_incomplete", "backup_newer") else None
        self.assertEqual(state(self.store), before)
        self.assertEqual(self.left_behind(), ["in"])
        self.assertEqual(list((self.backups.folder / "in").iterdir()), [])
        self.assertEqual(list(self.tmp.rglob("*planted*")), [])

    def test_how_a_backup_file_has_to_arrive(self):
        data = self.good.read_bytes()
        self.assertEqual(self.hand_over(data, **{"Content-Type": "application/zip"})[:2][0], 415)
        self.assertEqual(self.hand_over(data, **{"Content-Type": "application/json"})[1]["error"], "octet_stream_required")
        status, answer, _ = self.hand_over(b"")
        self.assertEqual((status, self.said(answer)), (400, "empty_upload"))
        with mock.patch.object(backup, "MAX_BACKUP_BYTES", 1000):
            status, answer, _ = self.hand_over(data)
            self.assertEqual((status, self.said(answer), answer["limit"]), (413, "backup_too_large", 1000))
        # it has to say how long it is, and the answer for one that does not comes without the body being read
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("PUT", "/api/restore/file")
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Transfer-Encoding", "chunked")
            conn.endheaders()
            res = conn.getresponse()
            self.assertEqual((res.status, json.loads(res.read().decode("utf-8"))["error"]), (411, "length_required"))
        finally:
            conn.close()
        # no page of another site can send one, or start a restore, or have a backup made
        port = self.port
        for origin in ("https://attacker.example", "null", "http://127.0.0.1:1"):
            self.assertEqual(self.hand_over(data, Origin=origin)[1]["error"], "bad_origin")
            self.assertEqual(self.send("POST", "/api/restore", {"restore_id": "0" * 12}, headers={"Origin": origin})[1]["error"], "bad_origin")
            self.assertEqual(self.send("POST", "/api/backup", {}, headers={"Origin": origin})[1]["error"], "bad_origin")
            self.assertEqual(self.send("POST", "/api/backup/save", {}, headers={"Origin": origin})[1]["error"], "bad_origin")
        self.assertEqual(self.send("POST", "/api/backup", body=b"x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})[1]["error"],
                         "json_required")
        self.assertEqual(self.send("POST", "/api/restore", body=b'{"restore_id": "a"}', headers={"Content-Type": "text/plain;application/json"})[0], 415)
        self.assertEqual(self.send("GET", "/api/backup", headers={"Host": "attacker.example"})[1]["error"], "bad_host")
        self.assertEqual(self.hand_over(data, Origin="http://127.0.0.1:%d" % port)[0], 200)      # the app's own page can
        self.assertEqual(self.send("GET", "/api/restore")[0], 404)
        self.assertEqual(self.send("DELETE", "/api/backup")[0], 404)
        self.assertEqual(self.send("POST", "/api/backup/nothing", {})[0], 404)
        self.assertEqual(list(self.downloads.iterdir()) if self.downloads.exists() else [], [])

    def test_a_restore_over_the_wire_is_refused_while_a_job_runs(self):
        release = threading.Event()
        job = self.httpd.app.jobs.start(server.TRANSCRIBE_JOB, lambda progress: release.wait(30))
        try:
            self.assertTrue(self.send("GET", "/api/backup")[1]["busy"])
            preview = self.hand_over(self.good.read_bytes())[1]
            status, answer, _ = self.send("POST", "/api/restore", {"restore_id": preview["restore_id"]})
            self.assertEqual((status, self.said(answer)), (409, "restore_busy"))
        finally:
            release.set()
        self.httpd.app.jobs.wait(job["id"], 15)
        self.assertEqual(self.send("POST", "/api/restore", {"restore_id": preview["restore_id"]})[0], 200)

    def test_the_server_answers_while_a_backup_is_being_made(self):
        inside, release = threading.Event(), threading.Event()
        real = backup._pack

        def slow_pack(*args, **kwargs):
            inside.set()
            release.wait(30)
            return real(*args, **kwargs)

        answers = {}
        with mock.patch.object(backup, "_pack", slow_pack):
            maker = threading.Thread(target=lambda: answers.update(made=self.send("POST", "/api/backup", {})))
            maker.start()
            try:
                self.assertTrue(inside.wait(15))
                self.assertEqual(self.send("GET", "/api/state")[0], 200)
                status, script, _ = self.send("POST", "/api/scripts", {"title": "备份的时候写的"})
                self.assertEqual(status, 201)
                self.assertEqual(self.send("PUT", "/api/scripts/" + script["id"], {"title": "还能改"})[1]["title"], "还能改")
                status, answer, _ = self.send("POST", "/api/backup", {})        # a second one at the same moment is told to wait
                self.assertEqual((status, self.said(answer)), (409, "backup_busy"))
            finally:
                release.set()
                maker.join(30)
        self.assertEqual(answers["made"][0], 201)

    def test_closing_the_app_removes_what_was_made_or_checked_and_not_used(self):
        self.send("POST", "/api/backup", {})
        self.hand_over(self.good.read_bytes())
        self.assertNotEqual(self.left_behind(), [])
        self.httpd.app.backups.close()
        self.assertEqual(self.left_behind(), [])


# ---------------------------------------------------------------------------------------------- 6. a page that was open before
class StalePageTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.drafted, self.typed = fill(self.store, "原")
        self.there, self.good = self.other("新")

    def restore(self):
        _, preview, stage_headers = self.hand_over(self.good.read_bytes())
        status, _, headers = self.send(
            "POST", "/api/restore", {"restore_id": preview["restore_id"]},
            headers={"X-Data-Epoch": stage_headers["X-Data-Epoch"]},
        )
        self.assertEqual(status, 200)
        return headers["X-Data-Epoch"]

    def test_every_answer_names_the_data_it_is_about(self):
        answers = [self.send("GET", "/"), self.send("GET", "/backup.js"), self.send("GET", "/api/state"), self.send("GET", "/api/nothing"),
                   self.send("POST", "/api/scripts", {"title": "x"}), self.send("POST", "/api/backup", {}),
                   self.send("GET", "/api/state", headers={"Host": "attacker.example"})]
        self.assertEqual([headers["X-Data-Epoch"] for _, _, headers in answers], ["0"] * len(answers))
        now = self.restore()
        self.assertRegex(now, "^[0-9a-f]{12}$")
        self.assertEqual(self.send("GET", "/api/state")[2]["X-Data-Epoch"], now)
        self.assertEqual(self.restore() == now, False)                           # each restore gives the data another name
        # the name is kept in the data folder: the app started again still knows it
        self.assertEqual(backup.Backups(self.store).epoch, self.send("GET", "/api/scripts")[2]["X-Data-Epoch"])

    def test_before_any_restore_a_page_and_a_program_write_as_they_always_did(self):
        self.assertEqual(self.send("POST", "/api/scripts", {"title": "页面写的"}, headers={"X-Data-Epoch": "0"})[0], 201)
        self.assertEqual(self.send("POST", "/api/scripts", {"title": "程序写的"})[0], 201)
        status, answer, _ = self.send("POST", "/api/scripts", {"title": "说错了"}, headers={"X-Data-Epoch": "a" * 12})
        self.assertEqual((status, answer["error"]), (409, "stale_data"))
        self.assertEqual(sorted(script["title"] for script in self.store.list_scripts())[-2:], ["程序写的", "页面写的"])

    def test_a_page_that_was_open_before_a_restore_cannot_write_into_the_restored_data(self):
        old_page = {"X-Data-Epoch": "0"}
        now = self.restore()
        after = state(self.store)
        script = self.store.list_scripts()[0]["id"]
        sample = self.store.list_samples()[0]["id"]
        writes = [
            ("PUT", "/api/scripts/" + self.typed["id"], {"title": "旧页面上的标题", "lines": [{"spoken_script_zh": "旧页面上的内容"}]}),
            ("PUT", "/api/scripts/" + script, {"title": "旧页面上的标题"}),
            ("POST", "/api/scripts", {"title": "旧页面新建的"}),
            ("DELETE", "/api/scripts/" + script, None),
            ("POST", "/api/samples", {"title": "旧页面加的", "text": "今天去超市买菜啦"}),
            ("PUT", "/api/samples/" + sample, {"text": "旧页面改的"}),
            ("DELETE", "/api/samples/" + sample, None),
            ("PUT", "/api/creator", {"name": "旧页面上的名字", "pronoun": "she"}),
            ("PUT", "/api/wordlist", {"phrases": ["旧页面上的说法"]}),
            ("POST", "/api/trends", {"text": "旧页面上的热词"}),
            ("PUT", "/api/trends/weight", {"weight": "mine"}),
            ("POST", "/api/settings", {"provider": "manual"}),
            ("POST", "/api/scripts/%s/posted" % script, {"likes": 5}),
            ("POST", "/api/lessons", {"script_id": script, "ai": "因此", "final": "所以"}),
            ("DELETE", "/api/voice/pending", None),
            ("POST", "/api/backup", {}),
            ("POST", "/api/restore", {"restore_id": "0" * 12}),
        ]
        for method, path, payload in writes:
            status, answer, headers = self.send(method, path, payload, headers=old_page)
            self.assertEqual((status, self.said(answer)), (409, "stale_data"), path)
            self.assertIn("Reload the page", answer["message"])
            self.assertIn("刷新页面", answer["message_zh"])
            self.assertEqual(headers["X-Data-Epoch"], now)                       # and the answer says what the data is called now
        status, answer, _ = self.send("PUT", "/api/uploads?name=a.mp4", body=b"0123", headers=dict(old_page, **{"Content-Type": "application/octet-stream"}))
        self.assertEqual((status, answer["error"]), (409, "stale_data"))
        status, answer, _ = self.hand_over(self.good.read_bytes(), **old_page)
        self.assertEqual((status, answer["error"]), (409, "stale_data"))
        self.assertEqual(state(self.store), after)
        # it can still read, and so finds out; and once it has loaded again it writes
        self.assertEqual(self.send("GET", "/api/scripts", headers=old_page)[0], 200)
        status, saved, _ = self.send("PUT", "/api/scripts/" + script, {"title": "重新加载以后改的"}, headers={"X-Data-Epoch": now})
        self.assertEqual((status, saved["title"]), (200, "重新加载以后改的"))
        self.assertEqual(self.send("POST", "/api/scripts", {"title": "不是页面的程序"})[0], 201)      # a program that names no data, as before


# ---------------------------------------------------------------------------------------------- 7. not through MCP
class NotThroughMcpTest(Case):
    def test_no_tool_makes_a_backup_or_restores_one(self):
        names = list(mcp_server.TOOLS)
        self.assertGreater(len(names), 8)
        self.assertEqual([name for name in names if re.search(r"backup|restore|archive|import_all|export_all", name)], [])
        public = json.dumps([tool["public"] for tool in mcp_server.TOOLS.values()], ensure_ascii=False).lower()
        for word in ("backup", "back up", "lmvbackup", "/api/restore", "/api/backup"):
            self.assertNotIn(word, public)
        source = (ROOT / "studio" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertNotIn("backup", source)                                       # the module is not even loaded there
        for name in ("backup", "make_backup", "create_backup", "restore", "restore_backup"):
            answer = mcp_server.handle(self.store, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}})
            self.assertEqual(answer["error"]["message"], "Unknown tool: " + name)

    def test_an_ai_app_connected_all_along_reads_the_restored_data_and_cannot_overwrite_it_blindly(self):
        fill(self.store, "原")
        connected = Store(self.store.data_dir)                                   # the store of the MCP server: another one on the same folder
        seen = connected.list_scripts()[0]
        there, path = self.other("新")
        self.backups.restore(self.staged(path)["restore_id"])
        listed = mcp_server.call_tool(connected, "list_scripts", {})
        self.assertNotIn("原", json.dumps(listed, ensure_ascii=False))
        self.assertIn("新面霜", json.dumps(listed, ensure_ascii=False))
        self.assertIsNone(connected.get_script(seen["id"]))                      # the script it had in view is no longer there


# ---------------------------------------------------------------------------------------------- 8. bounded work
class BoundsTest(Case):
    ITEMS, CHARS = 60, 20000

    def library(self, store):
        """A made-up library: ITEMS items of CHARS characters, each one different, and none of it anybody's material."""
        base = "今天去超市买菜啦然后人特别多嘛结果排队排了半小时哈其实家里还有嘛所以下次早点去"
        for number in range(self.ITEMS):
            text = ("%04d" % number + base[number % len(base):] + base) * (self.CHARS // (len(base) * 2) + 1)
            store.create_sample(title="素材 %d" % number, text=text[:self.CHARS], metrics={"likes": number})

    def test_a_backup_is_made_without_holding_the_library_in_memory_and_restores_to_the_same(self):
        there = Store(self.tmp / "large" / "data")
        self.library(there)
        size = there.db_path.stat().st_size
        self.assertGreater(size, self.ITEMS * self.CHARS * 2)                    # a few megabytes of text
        tracemalloc.start()
        try:
            path = self.made_from(there)
            _now, most = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(most, size / 2, "making a backup held %d bytes for a database of %d" % (most, size))
        self.assertLess(path.stat().st_size, size)                               # and the file is smaller than what it holds
        started = time.time()
        preview = self.staged(path)
        self.backups.restore(preview["restore_id"])
        self.assertLess(time.time() - started, 60)
        self.assertEqual(preview["file"]["holds"]["samples"], self.ITEMS)
        self.assertEqual(rows(self.store.db_path), rows(there.db_path))

    def test_one_item_of_the_longest_text_the_library_takes(self):
        there = Store(self.tmp / "long" / "data")
        for number in range(2):
            there.create_sample(title="很长的一篇 %d" % number, text=("%d今天去超市买菜啦/人特别多嘛\n" % number) * 31250)
        self.assertEqual(len(there.list_samples()[0]["text"]), 499999)
        self.backups.restore(self.staged(self.made_from(there))["restore_id"])
        self.assertEqual(rows(self.store.db_path), rows(there.db_path))


# ---------------------------------------------------------------------------------------------- 9. the page
class PageFilesTest(unittest.TestCase):
    def setUp(self):
        self.script = (WEB / "backup.js").read_text(encoding="utf-8")

    def test_every_text_of_the_page_is_there_in_both_languages(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        zh = dict(re.findall(r"\b(bk_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(bk_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertGreater(len(zh), 50)
        self.assertEqual(sorted(zh), sorted(en))
        used = set(re.findall(r"""['"](bk_[a-z_]+)['"]""", self.script))
        self.assertEqual(used - set(zh), set())
        self.assertEqual(set(zh) - used, set())                                  # and none is left over
        for key in zh:
            self.assertRegex(zh[key], "[\u4e00-\u9fff]", key)
            self.assertNotRegex(en[key], "[\u4e00-\u9fff]", key)
            self.assertEqual(sorted(re.findall(r"\{[a-z]+\}", zh[key])), sorted(re.findall(r"\{[a-z]+\}", en[key])), key)
        self.assertIsNone(ASSUMES.search(" ".join(en.values())))
        self.assertNotIn("她", "".join(zh.values()))                             # Chinese speaks to the creator as 你
        # what is in a backup and what is not is said before one is made; that keys have to be typed in again, after a restore
        for text in (zh["bk_out_list"], en["bk_out_list"]):
            self.assertRegex(text, "API")
        self.assertIn("密钥", zh["bk_out_list"])
        self.assertIn("never holds a key", en["bk_out_list"])
        self.assertIn("密钥需要重新填写", zh["bk_done_ai_none"])
        self.assertIn("The key has to be typed in again", en["bk_done_ai_none"])
        self.assertIn("什么都不会被替换", zh["bk_restore_hint"])
        self.assertIn("nothing is replaced until you say yes", en["bk_restore_hint"])
        self.assertIn("{d}", zh["bk_ask"])
        self.assertIn("counted by the studio", en["bk_says"])                    # what the file claims and what was counted are told apart

    def test_the_question_is_asked_inside_the_page_and_everything_shown_is_text(self):
        for word in (" confirm(", " alert(", " prompt(", "window.confirm", "window.alert", "innerText =", "outerHTML", "insertAdjacentHTML",
                     "document.write", "localStorage", "http://", "https://", "XMLHttpRequest"):
            self.assertNotIn(word, self.script, word)
        self.assertEqual(self.script.count("askConfirm("), 1)
        self.assertIn("{ ok: t('bk_ask_ok'), danger: true }", self.script)       # Enter alone replaces nothing
        # Everything put into markup goes through esc(), or is markup this file made itself.
        made_here = ("busy ?", "rows.map(", "f.older ?", "f.kind ===", "bkUi.working ?", "busy || bkUi.working ?", "info.put_back_at_start ?",
                     "bkDoneHtml()", "madeLine", "automatic", "last", "bkUi.problem ?", "bkUi.preview ?", "bkUi.working === 'checking' ?",
                     "out.id")
        for inserted in re.findall(r"\$\{((?:[^{}]|\{[^{}]*\})*)\}", self.script):
            inserted = inserted.strip()
            self.assertTrue(inserted.startswith("esc(") or inserted.startswith(made_here), inserted)
        self.assertEqual(sorted(set(re.findall(r"api\('(?:GET|POST|PUT|DELETE)', '([^'$]+)", self.script))),
                         ["/api/backup", "/api/backup/save", "/api/outside/show", "/api/restore", "/api/restore/", "/api/restore/automatic"])
        self.assertEqual(re.findall(r"fetch\(\s*([^,)]+)", self.script), ["'/api/backup/' + out.id + '/file'", "'/api/restore/file'"])
        self.assertEqual(self.script.count("studioHeaders("), 2)

    def test_the_page_names_the_data_it_means_with_every_request(self):
        self.assertIn("const BK_HEADER = 'X-Data-Epoch';", self.script)
        self.assertEqual(backup.EPOCH_HEADER, "X-Data-Epoch")
        self.assertIn("window.fetch = async function", self.script)
        self.assertIn("headers.set(BK_HEADER, bkData.epoch)", self.script)
        self.assertIn("location.reload()", self.script)
        pages, index = (WEB / "pages.js").read_text(encoding="utf-8"), (WEB / "index.html").read_text(encoding="utf-8")
        self.assertEqual((pages.count("bkPaintSettings("), pages.count('<div id="bk-box"></div>')), (1, 1))
        # loaded before the script that starts the app, so the first request is already one that is looked at
        self.assertLess(index.index('src="/backup.js"'), index.index('src="/pages.js"'))
        for other in ("app.js", "pages.js", "setup.js", "posted.js", "wordlist.js", "discover.js", "craft.js"):
            self.assertIsNone(re.search(r"\bbk_[a-z]", (WEB / other).read_text(encoding="utf-8")), other)
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("/* ---- backup and restore (backup.js)"), 1)
        self.assertGreater(css.index("/* ---- backup and restore (backup.js)"), css.index("#wl-box"))    # one block, at the end

    def test_the_page_script_is_served(self):
        store = Store(Path(tempfile.mkdtemp(prefix="lmv-backup-page-")))
        self.addCleanup(shutil.rmtree, str(store.data_dir), True)
        httpd = server.make_server(0, store)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(5)))
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
        try:
            conn.request("GET", "/backup.js")
            res = conn.getresponse()
            body = res.read()
        finally:
            conn.close()
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, (WEB / "backup.js").read_bytes())



class BaseEvolutionAndTokenAuthTest(Case):
    """Porting checks onto a2e77d7: X-Studio-Token auth, keys_sealed & server-token exclusion, script_pins, and over-ceiling scripts."""

    def test_require_token_enforced_on_all_backup_and_restore_routes(self):
        fill(self.store, "甲")
        there, good_path = self.other("乙")
        good_bytes = good_path.read_bytes()
        downloads = self.tmp / "downloads"
        downloads.mkdir()
        httpd = server.make_server(0, self.store, outside_world=World(downloads), require_token=True)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(5)))
        port = httpd.server_address[1]
        token = httpd.app.token

        def req(method, path, body=None, token_hdr=None, ctype="application/json"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            try:
                hdrs = {"Host": "127.0.0.1:%d" % port}
                if token_hdr is not None:
                    hdrs["X-Studio-Token"] = token_hdr
                raw = body
                if body is not None and not isinstance(body, (bytes, bytearray)):
                    raw = json.dumps(body).encode("utf-8")
                if raw is not None:
                    hdrs["Content-Type"] = ctype
                    hdrs["Content-Length"] = str(len(raw))
                conn.request(method, path, body=raw, headers=hdrs)
                res = conn.getresponse()
                return res.status, res.read()
            finally:
                conn.close()

        # Without token: every backup/restore route is refused with 401.
        for m, p, b, ct in (
            ("GET", "/api/backup", None, "application/json"),
            ("POST", "/api/backup", {}, "application/json"),
            ("POST", "/api/backup/save", {}, "application/json"),
            ("PUT", "/api/restore/file", good_bytes, "application/octet-stream"),
            ("POST", "/api/restore", {"restore_id": "0" * 12}, "application/json"),
        ):
            st, payload = req(m, p, b, token_hdr=None, ctype=ct)
            self.assertEqual(st, 401, (m, p, payload))

        # With valid X-Studio-Token: backup creation, file download, desktop save, upload stage, and restore all succeed,
        # and server-token on disk stays intact throughout!
        token_file = self.store.data_dir / "server-token"
        self.assertTrue(token_file.is_file())
        token_before = token_file.read_text(encoding="utf-8")

        st, body = req("POST", "/api/backup", {}, token_hdr=token)
        self.assertEqual(st, 201)
        made_id = json.loads(body.decode("utf-8"))["id"]

        st_no_tok, _ = req("GET", "/api/backup/%s/file" % made_id, token_hdr=None)
        self.assertEqual(st_no_tok, 401)
        st_dl, dl_bytes = req("GET", "/api/backup/%s/file" % made_id, token_hdr=token)
        self.assertEqual(st_dl, 200)
        self.assertTrue(dl_bytes.startswith(b"PK\x03\x04"))
        self.assertNotIn(token_before.encode("utf-8"), dl_bytes)

        st_up, up_body = req("PUT", "/api/restore/file", good_bytes, token_hdr=token, ctype="application/octet-stream")
        self.assertEqual(st_up, 200)
        rid = json.loads(up_body.decode("utf-8"))["restore_id"]

        st_res, _ = req("POST", "/api/restore", {"restore_id": rid}, token_hdr=token)
        self.assertEqual(st_res, 200)
        self.assertEqual(token_file.read_text(encoding="utf-8"), token_before)
        # Subsequent authenticated request with the same token still works!
        st_after, _ = req("GET", "/api/backup", token_hdr=token)
        self.assertEqual(st_after, 200)

    def test_sealed_keys_keystore_and_runtime_files_never_leak_and_survive_restore(self):
        drafted, _ = fill(self.store, "甲")
        sample = [s for s in self.store.list_samples() if s["id"] not in self.store.list_pins(drafted["id"])][0]
        self.assertEqual(self.store.pin_sample(drafted["id"], sample["id"], limit=10), "pinned")
        # Write runtime markers into data_dir.
        (self.store.data_dir / "server-token").write_text("tok-secret-runtime-123", encoding="utf-8")
        (self.store.data_dir / "desktop-running.json").write_text('{"pid": 123}', encoding="utf-8")

        raw_cfg = json.loads(self.store.config_path.read_text(encoding="utf-8"))
        sealed_map = raw_cfg.get("keys_sealed") or {}
        self.assertTrue(sealed_map)

        archive_path = self.made_from(self.store)
        archive_bytes = archive_path.read_bytes()
        uncompressed_all = b"".join(unpacked(archive_path).values())
        for forbidden in [SECRET.encode("utf-8"), SECRET_TOO.encode("utf-8"), b"tok-secret-runtime-123", b"keys_sealed", b"keystore.key"] + [
            str(v).encode("utf-8") for v in sealed_map.values()
        ]:
            self.assertNotIn(forbidden, archive_bytes)
            self.assertNotIn(forbidden, uncompressed_all)

        # Restore into another install that has its own sealed API key and server-token: both stay intact, and script_pins round-trip!
        target_store = Store(self.tmp / "dest" / "data")
        target_store.update_config(lambda c: c.update(provider="openai", model="gpt-4o", keys={"openai": SECRET_ELSEWHERE}))
        (target_store.data_dir / "server-token").write_text("dest-live-token", encoding="utf-8")
        dest_backups = backup.Backups(target_store)
        prev = self.staged(archive_path, backups=dest_backups)
        dest_backups.restore(prev["restore_id"])

        self.assertEqual(target_store.load_config()["keys"]["openai"], SECRET_ELSEWHERE)
        self.assertEqual((target_store.data_dir / "server-token").read_text(encoding="utf-8"), "dest-live-token")
        self.assertIn(sample["id"], target_store.list_pins(drafted["id"]))

    def test_script_longer_than_todays_ceilings_restores_and_opens(self):
        # Build an older backup containing 550 lines and a 6,000-char line (above Store's 500-line / 5,000-char save ceilings).
        long_lines = json.dumps(
            [{"line_no": i + 1, "spoken_script_zh": ("很长的一句台词嘛" * 700 if i == 0 else "普通台词 %d" % i), "audio_mode": "VOICEOVER"}
             for i in range(550)],
            ensure_ascii=False,
        )
        db_bytes = database(
            statements=[("INSERT INTO scripts (id, title, notes, lines_json, created_at, updated_at) VALUES ('bbbbbbbbbbbb', '超长旧稿', ?, ?, 1.0, 2.0)",
                         ("备忘" * 15000, long_lines))],
            layout=SCHEMA_AT_3FDD3A6,
        )
        old_backup = pack(self.tmp / "over-ceiling.lmvbackup", {backup.DATABASE: db_bytes, backup.SETTINGS: b"{}"}, data_version=1)
        preview = self.staged(old_backup)
        self.assertTrue(preview["file"]["older"])
        self.backups.restore(preview["restore_id"])
        restored = self.store.get_script("bbbbbbbbbbbb")
        self.assertIsNotNone(restored)
        self.assertEqual(len(restored["lines"]), 550)
        self.assertGreater(len(restored["lines"][0]["spoken_script_zh"]), 5000)


if __name__ == "__main__":
    unittest.main()
