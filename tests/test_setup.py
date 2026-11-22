"""Tests for the guided setup: timed material, videos, the proposed voice profile, lessons from edits, setup status.

Real HTTP against a server on a temporary folder. The model and the transcriber are stand-ins, so
nothing here needs a network, a key or Whisper.

Numbers used throughout, worked out by hand:

- ``HER_SCRIPT`` is 12 lines of three breaths: 36 clauses, 24 of 7 units and 12 of 8.
- ``CUES`` is 30 cues of 2.0 s, each 0.5 s after the one before, each saying 8 units:
  240 units; speech is 2.0 + 29 x (0.5 + 2.0) = 74.5 s; 29 pauses; 240 x 60 / 74.5 = 193.3, so 193 a minute.
"""
import contextlib
import copy
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from studio import creator, evidence, guardrails, mcp_server, pipeline, server, transcribe
from studio import setup as setup_status
from studio.store import MAX_BRIEFS, Store, clean_file_name
from tests.test_features import HER_SCRIPT

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))

LINE = "今天去超市买菜啦"                                           # 8 units
CUES = [(i * 2.5, i * 2.5 + 2.0, LINE) for i in range(30)]
SEGMENTS = [{"start": start, "end": end, "text": text} for start, end, text in CUES]
TRANSCRIPT = "\n".join([LINE] * 30)
TIMING_30 = {"cues": 30, "units": 240, "spoken_sec": 74.5, "span_sec": 74.5, "pauses": 29, "speaking_rate_cpm": 193}
MEASURED_30 = {"speaking_rate_cpm": 193, "spoken_sec": 74.5, "videos": 1}
SHORT_VTT = "WEBVTT\n\n00:00.000 --> 00:02.000\n今天去超市买菜啦\n\n00:02.500 --> 00:04.500\n<i>结果人特别多嘛</i>\n"
SHORT_TIMING = {"cues": 2, "units": 15, "spoken_sec": 4.5, "span_sec": 4.5, "pauses": 1, "speaking_rate_cpm": None}

BREAKFAST = "早上煮咖啡啦 / 顺便烤了面包嘛"                          # 2 clauses
HIKE = "周末去爬山啦"                                               # 1 clause
AD = "这款面霜很好用"

# A proposed profile in which every checkable entry has a known answer against HER_SCRIPT alone.
DNA_CHECKED = {
    "profile_version": "1.0",
    "persona_summary": {"one_line_essence_zh": "松弛", "audience_address_terms": {"uses": ["你们看"], "never_uses": ["宝子们"]}},
    "cadence_and_syntax": {"target_speaking_rate_cpm": 235, "ideal_clause_length_chars": {"min": 6, "median": 11, "hard_max": 20}},
    "lexical_fingerprint": {
        "signature_openers": [{"pattern_zh": "今天去…", "verbatim_sample": "今天去超市买菜"},
                              {"pattern_zh": "编的", "verbatim_sample": "这句话她从来没说过"}],
        "modal_particles": {"primary": [{"particle": "啦"}]},
    },
    "anti_voice_blacklist": {"banned_phrases_zh": ["绝绝子", "排队排了半小时啦"], "learned_from_her_edits": []},
}
CHECKED_STATUSES = [
    ("persona_summary.audience_address_terms.uses[0]", "not_found"),             # 你们看: not in her script
    ("persona_summary.audience_address_terms.never_uses[0]", "absent"),          # 宝子们: indeed never said
    ("lexical_fingerprint.signature_openers[0].verbatim_sample", "found"),       # opens all 12 lines
    ("lexical_fingerprint.signature_openers[1].verbatim_sample", "not_found"),   # invented by the model
    ("lexical_fingerprint.modal_particles.primary[0].particle", "found"),        # 啦 ends 12 breaths
    ("anti_voice_blacklist.banned_phrases_zh[0]", "absent"),
    ("anti_voice_blacklist.banned_phrases_zh[1]", "conflict"),                   # a ban on something she says 12 times
]
OPENER, INVENTED, USES = CHECKED_STATUSES[2][0], CHECKED_STATUSES[3][0], CHECKED_STATUSES[0][0]

AI_SCRIPT = {"script_lines": [{"spoken_script_zh": "今天去超市买菜"}, {"spoken_script_zh": "这个真的绝绝子嘛"},
                              {"spoken_script_zh": "最后买了两盒草莓"}]}
AI_LINES = [line["spoken_script_zh"] for line in AI_SCRIPT["script_lines"]]
EXEMPLARS_TAG = "retrieved_real_exemplars_from_creator_past_videos"
LESSONS_TAG = "learned_edit_preferences"


def stamp(seconds):
    ms = int(round(seconds * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def make_srt(cues):
    return "\n\n".join("%d\n%s --> %s\n%s" % (n, stamp(start), stamp(end), text)
                       for n, (start, end, text) in enumerate(cues, start=1)) + "\n"


def lines(*spoken):
    return [{"spoken_script_zh": text} for text in spoken]


def block(prompt, tag):
    """The JSON inside <tag> of a rendered prompt; the comment that opens some blocks is skipped."""
    inner = prompt.split("<%s>\n" % tag)[1].split("</%s>" % tag)[0]
    return json.loads(inner.split("-->")[1] if "-->" in inner else inner)


class FakeModel:
    """Stands in for a local OpenAI-compatible model; answers by which blueprint is asking."""

    def __init__(self):
        self.calls = []
        self.dna = copy.deepcopy(DNA_CHECKED)
        self.script = copy.deepcopy(AI_SCRIPT)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "GET":
            return {"data": [{"id": "local-model"}]}
        reply = self.dna if "sociolinguist" in body["messages"][0]["content"] else self.script
        return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

    def prompt_for(self, marker):
        for _, _, _, body in reversed(self.calls):
            if body and marker in body["messages"][0]["content"]:
                return body["messages"][1]["content"]
        raise AssertionError("no call for " + marker)


class FakeTranscriber:
    """Stands in for studio.transcribe: no Whisper, no install, no network."""

    def __init__(self):
        self.installed = True
        self.segments = copy.deepcopy(SEGMENTS)
        self.unclear_dropped = 0                # stretches the transcriber says it could not make out and left out
        self.error = None
        self.hold = None                        # an Event: transcribe_file waits for it
        self.started = threading.Event()
        self.seen = []                          # (file name, bytes) of what was on disk while transcribing
        self.active = self.max_active = 0
        self.lock = threading.Lock()
        self.install_calls = self.uninstall_calls = 0
        self.install_hold = None
        self.install_started = threading.Event()
        self.install_error = None

    def status(self):
        return {"supported": True, "reason": None, "reason_en": None, "reason_zh": None, "installed": self.installed,
                "model_ready": self.installed, "model": "stand-in", "audio_tool": "afconvert", "location": "/nowhere"}

    def install(self, progress=None):
        self.install_calls += 1
        if progress:
            progress("model", 0.5)
        self.install_started.set()
        if self.install_hold is not None:
            self.install_hold.wait(15)
        if self.install_error is not None:
            raise self.install_error
        self.installed = True
        if progress:
            progress("done", 1.0)

    def uninstall(self):
        self.uninstall_calls += 1
        self.installed = False

    def transcribe_file(self, path, progress=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.seen.append((Path(path).name, Path(path).read_bytes()))
            if progress:
                progress("transcribe", None)
            self.started.set()
            if self.hold is not None:
                self.hold.wait(15)
            if self.error is not None:
                raise self.error
            return {"segments": copy.deepcopy(self.segments), "language": "zh", "duration_sec": 74.5,
                    "model": "stand-in", "unclear_dropped": self.unclear_dropped}
        finally:
            with self.lock:
                self.active -= 1


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.fake = FakeModel()
        self.whisper = FakeTranscriber()
        self.httpd = server.make_server(0, self.store, self.fake, self.whisper)
        self.app = self.httpd.app
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        for gate in (self.whisper.hold, self.whisper.install_hold):
            if gate is not None:
                gate.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    # ---- talking to the server ----
    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res.status, (json.loads(raw.decode("utf-8")) if raw else None)

    def call(self, method, path, body=None, origin=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if origin:
            headers["Origin"] = origin
        return self.request(method, path, data, headers)

    def upload(self, name, data, content_type="application/octet-stream", origin=None):
        headers = {"Content-Type": content_type} if content_type else {}
        if origin:
            headers["Origin"] = origin
        return self.request("PUT", "/api/uploads?name=" + urllib.parse.quote(name, safe=""), data, headers)

    def mcp(self, name, **arguments):
        result = mcp_server.call_tool(self.store, name, arguments)
        text = result["content"][0]["text"]
        return result["isError"], (json.loads(text) if text.startswith("{") else text)

    # ---- getting into a state ----
    def connect_model(self):
        self.call("POST", "/api/settings/models", {"provider": "local", "base_url": "http://localhost:11434/v1"})
        _, out = self.call("POST", "/api/settings", {"provider": "local", "model": "local-model"})
        self.assertTrue(out["settings"]["configured"])

    def add(self, title, text, **more):
        status, sample = self.call("POST", "/api/samples", dict({"title": title, "text": text}, **more))
        self.assertEqual(status, 201, sample)
        return sample

    def add_subtitles(self, title="字幕那期", cues=CUES, **more):
        status, sample = self.call("POST", "/api/samples", dict({"title": title, "subtitle_text": make_srt(cues)}, **more))
        self.assertEqual(status, 201, sample)
        return sample

    def approve(self, body=None, origin=None):
        """Accept the waiting proposal as the review page does when the creator changes nothing more.

        The request names the proposal on the screen (``created_at``) and keeps the free text shown with it
        (``keep``); a test passes its own ``created_at`` or ``keep`` in ``body`` to do otherwise.
        """
        named = dict(body or {})
        pending = self.pending()
        if pending is not None:
            named.setdefault("created_at", pending["created_at"])
            named.setdefault("keep", [field["path"] for field in pending["report"]["unverified"]])
        return self.call("POST", "/api/voice/approve", named, origin=origin)

    def learn_and_accept(self, rejected=()):
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, out)
        status, out = self.approve({"rejected": list(rejected)})
        self.assertEqual(status, 200, out)
        return out["dna"]

    def draft(self, notes="再去一次超市", **more):
        status, script = self.call("POST", "/api/generate", dict({"notes": notes, "target_sec": 30}, **more))
        self.assertEqual(status, 201, script)
        return script

    # ---- looking at what is there ----
    def samples(self):
        return self.call("GET", "/api/samples")[1]

    def pending(self):
        return self.call("GET", "/api/voice/pending")[1]["pending"]

    def setup(self):
        status, out = self.call("GET", "/api/setup")
        self.assertEqual(status, 200)
        return out

    def in_use(self):
        """The voice profile in use: its bytes on disk, the kept history, and what the app reports."""
        on_disk = self.store.dna_path.read_bytes() if self.store.dna_path.exists() else None
        history = sorted(p.name for p in self.store.dna_history_dir.glob("*.json")) if self.store.dna_history_dir.exists() else []
        return on_disk, history, self.call("GET", "/api/dna")[1]

    def uploaded_files(self):
        folder = self.store.uploads_dir
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def wait(self, job):
        done = self.app.jobs.wait(job["id"], 20)
        self.assertNotEqual(done["status"], "running", done)
        return done

    def slop(self, *spoken):
        """(severity, matched, Chinese message) of the phrase findings the editor's check reports for these lines."""
        _, lint = self.call("POST", "/api/lint", {"lines": lines(*spoken), "target_sec": 60})
        return [(f["severity"], f["matched"], f["message_zh"]) for f in lint["findings"] if f["category"] == "GENERIC_XHS_SLOP"]


# ---------------------------------------------------------------------------------------------- 1. material
class TimedMaterialTest(SetupCase):
    def test_a_subtitle_file_becomes_her_words_and_a_measured_timing(self):
        pasted = self.add("超市那天", HER_SCRIPT)
        self.assertEqual((pasted["kind"], pasted["timing"], pasted["source_name"]), ("script", None, ""))
        self.assertIsNone(self.samples()["measured"])                      # nothing timed yet

        status, item = self.call("POST", "/api/samples", {"title": "字幕那期", "subtitle_text": make_srt(CUES),
                                                          "source_name": "字幕那期.srt"})
        self.assertEqual(status, 201)
        self.assertEqual((item["kind"], item["source_name"], item["title"]), ("subtitle", "字幕那期.srt", "字幕那期"))
        self.assertEqual(item["text"], TRANSCRIPT)                         # one cue per line, wording untouched
        self.assertEqual(item["timing"], TIMING_30)

        listing = self.samples()
        self.assertEqual(listing["measured"], MEASURED_30)
        self.assertEqual([(s["title"], s["kind"]) for s in listing["samples"]], [("字幕那期", "subtitle"), ("超市那天", "script")])
        self.assertEqual(self.store.get_sample(item["id"])["timing"], TIMING_30)          # stored, not recomputed per request

    def test_webvtt_is_read_and_a_short_clip_gives_no_rate(self):
        status, item = self.call("POST", "/api/samples", {"subtitle_text": SHORT_VTT, "source_name": "clip.vtt"})
        self.assertEqual((status, item["kind"], item["text"]), (201, "subtitle", "今天去超市买菜啦\n结果人特别多嘛"))
        self.assertEqual(item["timing"], SHORT_TIMING)                     # 4.5 s of speech: too little for a rate
        self.assertEqual(item["title"], "今天去超市买菜啦")                  # no title given: the first line
        self.assertIsNone(self.samples()["measured"])                      # and under a minute in total: no combined rate

    def test_sponsored_material_does_not_set_her_speaking_rate(self):
        slow = [(i * 4.0, i * 4.0 + 4.0, LINE) for i in range(30)]         # 240 units in 120 s: 120 a minute
        sponsored = self.add_subtitles("商单字幕", slow, is_sponsored=True)
        self.assertEqual(sponsored["timing"]["speaking_rate_cpm"], 120)
        self.assertIsNone(self.samples()["measured"])                      # only sponsored material is timed
        self.add_subtitles("字幕那期")
        self.assertEqual(self.samples()["measured"], MEASURED_30)          # hers alone; pooled with the ad it would be 148
        self.call("PUT", "/api/samples/" + sponsored["id"], {"is_sponsored": False})
        self.assertEqual(self.samples()["measured"], {"speaking_rate_cpm": 148, "spoken_sec": 194.5, "videos": 2})

    def test_unreadable_subtitles_are_refused_and_nothing_is_stored(self):
        for bad in ("今天去超市买菜啦\n这是一篇普通的脚本，没有时间轴", "", "   ", 5, ["00:00:01,000 --> 00:00:02,000"], {"a": 1},
                    "1\n00:00:01,000 --> 00:00:02,000\n\n"):               # the last has a cue with no words in it
            status, out = self.call("POST", "/api/samples", {"title": "坏字幕", "subtitle_text": bad})
            self.assertEqual((status, out["error"]), (400, "bad_subtitles"), bad)
        status, out = self.call("POST", "/api/samples", {"subtitle_text": "不是字幕", "text": "这段文字不该被悄悄当成脚本收下"})
        self.assertEqual((status, out["error"]), (400, "bad_subtitles"))
        self.assertEqual(self.samples()["samples"], [])

    def test_kind_and_timing_come_from_the_server_never_from_the_request(self):
        claimed = {"cues": 1, "units": 9000, "spoken_sec": 60.0, "span_sec": 60.0, "pauses": 0, "speaking_rate_cpm": 9000}
        item = self.add("手写的", HER_SCRIPT, kind="video", timing=claimed, source_name="../../x.srt")
        self.assertEqual((item["kind"], item["timing"], item["source_name"]), ("script", None, "x.srt"))
        timed = self.add_subtitles()
        _, edited = self.call("PUT", "/api/samples/" + timed["id"], {"kind": "script", "timing": claimed, "title": "改了标题"})
        self.assertEqual((edited["kind"], edited["timing"], edited["title"]), ("subtitle", TIMING_30, "改了标题"))
        self.assertEqual(self.samples()["measured"], MEASURED_30)          # a number that was not measured never becomes "measured"


class OldDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_a_database_from_before_this_change_is_upgraded_in_place(self):
        db = sqlite3.connect(str(self.tmp / "studio.db"))
        db.execute("CREATE TABLE scripts (id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',"
                   " video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER', target_sec INTEGER NOT NULL DEFAULT 60,"
                   " notes TEXT NOT NULL DEFAULT '', lines_json TEXT NOT NULL DEFAULT '[]', post_json TEXT NOT NULL DEFAULT '{}',"
                   " ai_lines_json TEXT, status TEXT NOT NULL DEFAULT 'DRAFT', keywords_json TEXT NOT NULL DEFAULT '[]',"
                   " created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        db.execute("CREATE TABLE samples (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,"
                   " is_sponsored INTEGER NOT NULL DEFAULT 0, views INTEGER, likes INTEGER, collects INTEGER, comments INTEGER,"
                   " created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        old_lines = json.dumps([{"line_no": 1, "spoken_script_zh": "这个真的绝绝子嘛"}], ensure_ascii=False)
        new_lines = json.dumps([{"line_no": 1, "spoken_script_zh": "这个真的蛮好吃嘛"}], ensure_ascii=False)
        db.execute("INSERT INTO scripts (id, title, lines_json, ai_lines_json, keywords_json, created_at, updated_at)"
                   " VALUES ('aaaaaaaaaaaa', '旧脚本', ?, ?, '[\"露营\"]', 1, 2)", (new_lines, old_lines))
        db.execute("INSERT INTO samples (id, title, text, is_sponsored, likes, created_at, updated_at)"
                   " VALUES ('bbbbbbbbbbbb', '旧素材', '以前写的脚本啦', 1, 120, 3, 4)")
        db.commit()
        db.close()

        store = Store(self.tmp)
        self.assertEqual(store.list_samples(), [{
            "id": "bbbbbbbbbbbb", "title": "旧素材", "text": "以前写的脚本啦", "is_sponsored": True, "views": None, "likes": 120,
            "collects": None, "comments": None, "kind": "script", "timing": None, "source_name": "",
            "needs_review": False, "reviewed_at": None, "created_at": 3.0, "updated_at": 4.0}])
        script = store.get_script("aaaaaaaaaaaa")
        self.assertEqual((script["title"], script["keywords"], script["has_ai_draft"], script["basis"]), ("旧脚本", ["露营"], True, None))

        # and everything new works on it: timed material, the comparison with the AI's draft, lessons, prompt records
        timed = store.create_sample(title="新字幕", text=TRANSCRIPT, kind="subtitle", timing=TIMING_30, source_name="a.srt")
        self.assertEqual((timed["kind"], timed["timing"], timed["needs_review"]), ("subtitle", TIMING_30, False))
        changes = pipeline.script_changes(store, "aaaaaaaaaaaa")["changes"]
        self.assertEqual([(c["ai"], c["final"]) for c in changes], [("这个真的绝绝子嘛", "这个真的蛮好吃嘛")])
        lesson, created = pipeline.remember_lesson(store, script_id="aaaaaaaaaaaa", ai="这个真的绝绝子嘛", final="这个真的蛮好吃嘛")
        self.assertTrue(created)
        self.assertEqual(store.list_lessons(), [lesson])
        self.assertEqual(store.find_brief("voice", brief_id=store.save_brief("voice", {"organic": 1}))["payload"], {"organic": 1})
        self.assertEqual(setup_status.status(store)["first_script"], {"done": True, "count": 1})

        again = Store(self.tmp)                                            # opening it a second time changes nothing
        self.assertEqual(len(again.list_samples()), 2)
        self.assertEqual(again.get_script("aaaaaaaaaaaa")["title"], "旧脚本")

    def test_a_video_stored_before_transcripts_had_to_be_confirmed_starts_out_waiting(self):
        db = sqlite3.connect(str(self.tmp / "studio.db"))                  # the samples table as it was one version ago
        db.execute("CREATE TABLE samples (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,"
                   " is_sponsored INTEGER NOT NULL DEFAULT 0, views INTEGER, likes INTEGER, collects INTEGER, comments INTEGER,"
                   " kind TEXT NOT NULL DEFAULT 'script', timing_json TEXT, source_name TEXT NOT NULL DEFAULT '',"
                   " created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        for n, (sample_id, kind) in enumerate((("aaaaaaaaaaaa", "script"), ("bbbbbbbbbbbb", "subtitle"), ("cccccccccccc", "video")), start=1):
            db.execute("INSERT INTO samples (id, title, text, kind, created_at, updated_at) VALUES (?, ?, '今天去超市买菜啦', ?, ?, ?)",
                       (sample_id, kind, kind, n, n))
        db.commit()
        db.close()

        store = Store(self.tmp)
        waiting = {s["kind"]: (s["needs_review"], s["reviewed_at"]) for s in store.list_samples()}
        self.assertEqual(waiting, {"script": (False, None), "subtitle": (False, None), "video": (True, None)})   # nobody ever confirmed that video
        self.assertEqual(setup_status.status(store)["material"]["unreviewed"], 1)

        confirmed = store.update_sample("cccccccccccc", {"needs_review": False})
        self.assertFalse(confirmed["needs_review"])
        again = Store(self.tmp)                                            # the marking happens once: a confirmation is not undone
        self.assertFalse(again.get_sample("cccccccccccc")["needs_review"])
        self.assertEqual(again.get_sample("cccccccccccc")["reviewed_at"], confirmed["reviewed_at"])

    def test_prompt_records_do_not_pile_up(self):
        store = Store(self.tmp)
        ids = [store.save_brief("draft", {"basis": {"n": n}}, "same") for n in range(MAX_BRIEFS + 5)]
        self.assertIsNone(store.find_brief("draft", brief_id=ids[0]))      # the oldest are dropped
        self.assertEqual(store.find_brief("draft", brief_id=ids[5])["payload"], {"basis": {"n": 5}})
        self.assertEqual(store.find_brief("draft", fingerprint="same")["payload"], {"basis": {"n": MAX_BRIEFS + 4}})   # newest
        self.assertIsNone(store.find_brief("draft", brief_id="no-such-id"))                  # an unknown id is never guessed at
        self.assertIsNone(store.find_brief("draft", fingerprint="other"))

    def test_a_file_name_is_only_a_label(self):
        for given, label in (("../../etc/passwd", "passwd"), ("C:\\Users\\x\\vlog.mp4", "vlog.mp4"), ("/abs/周末 vlog.MP4", "周末 vlog.MP4"),
                             ("a\x00b.mp4", "ab.mp4"), ("line\nbreak.srt", "line break.srt"), ("..", ".."), ("", ""), (None, ""), (5, "")):
            self.assertEqual(clean_file_name(given), label, given)
        self.assertEqual(len(clean_file_name("长" * 500 + ".mp4")), 200)


# ---------------------------------------------------------------------------------------------- 2. videos
class VideoTest(SetupCase):
    def start(self, name="clip.mp4", data=b"not really a video", **body):
        status, up = self.upload(name, data)
        self.assertEqual(status, 201, up)
        status, out = self.call("POST", "/api/videos", dict({"upload_id": up["upload_id"]}, **body))
        self.assertEqual(status, 202, out)
        return up, out["job"]

    def test_a_video_is_transcribed_here_added_with_its_timing_and_its_copy_deleted(self):
        data = os.urandom(2 * server.UPLOAD_CHUNK + 4321)                  # arrives in three pieces
        status, up = self.upload("周末 vlog.MP4", data)
        self.assertEqual(status, 201)
        self.assertEqual(set(up), {"upload_id", "name", "size"})
        self.assertRegex(up["upload_id"], r"^[0-9a-f]{12}$")
        self.assertEqual((up["name"], up["size"]), ("周末 vlog.MP4", len(data)))
        stored = self.store.uploads_dir / (up["upload_id"] + ".mp4")       # a name made by the server, inside the data folder
        self.assertEqual(self.uploaded_files(), [stored.name])
        self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), hashlib.sha256(data).hexdigest())

        status, out = self.call("POST", "/api/videos", {"upload_id": up["upload_id"]})
        self.assertEqual(status, 202)
        self.assertEqual((out["job"]["kind"], out["job"]["status"], out["job"]["label"]), ("transcribe", "running", "周末 vlog.MP4"))
        self.wait(out["job"])
        status, got = self.call("GET", "/api/jobs/" + out["job"]["id"])
        self.assertEqual((status, got["job"]["status"], got["job"]["error"]), (200, "done", None))

        self.assertEqual(self.whisper.seen, [(stored.name, data)])         # the transcriber was given that very file
        self.assertEqual(self.uploaded_files(), [])                        # and afterwards no copy is kept
        listing = self.samples()
        video = listing["samples"][0]
        self.assertEqual(got["job"]["result"], {"sample_id": video["id"], "title": "周末 vlog"})
        self.assertEqual((video["kind"], video["title"], video["source_name"], video["is_sponsored"]), ("video", "周末 vlog", "周末 vlog.MP4", False))
        self.assertEqual(video["text"], TRANSCRIPT)
        self.assertEqual(video["timing"], dict(TIMING_30, unclear_dropped=0))
        self.assertEqual((video["needs_review"], video["reviewed_at"]), (True, None))        # a machine wrote these words: they wait for her
        self.assertEqual(listing["measured"], MEASURED_30)                 # the timing counts at once

    def test_stretches_the_transcriber_could_not_make_out_are_counted_on_the_item(self):
        self.whisper.unclear_dropped = 3
        _, job = self.start()
        self.wait(job)
        video = self.samples()["samples"][0]
        self.assertEqual(video["timing"], dict(TIMING_30, unclear_dropped=3))
        self.assertEqual(self.samples()["measured"], MEASURED_30)          # the extra number does not disturb the rate
        for odd in (None, "2", True, -1, 2.5):                             # anything that is not a count is stored as none left out
            self.whisper.unclear_dropped = odd
            _, job = self.start()
            self.wait(job)
            self.assertEqual(self.samples()["samples"][0]["timing"]["unclear_dropped"], 0, odd)

    def test_title_and_sponsored_flag_are_taken_from_the_request(self):
        _, job = self.start(title="  面霜那期 ", is_sponsored=True)
        self.wait(job)
        video = self.samples()["samples"][0]
        self.assertEqual((video["title"], video["is_sponsored"], video["kind"]), ("面霜那期", True, "video"))
        self.assertIsNone(self.samples()["measured"])                      # sponsored: not her own speaking rate

    def test_a_failed_transcription_says_why_and_still_deletes_the_copy(self):
        self.whisper.error = transcribe.TranscribeError("This file has no sound track.", "no_audio", message_zh="这个文件没有声音。")
        up, job = self.start()
        done = self.wait(job)
        self.assertEqual((done["status"], done["result"]), ("failed", None))
        self.assertEqual(done["error"], {"code": "no_audio", "message": "This file has no sound track.", "message_zh": "这个文件没有声音。"})
        self.assertEqual(self.call("GET", "/api/jobs/" + job["id"])[1]["job"]["error"], done["error"])   # both languages reach the page
        self.assertEqual(len(self.whisper.seen), 1)                        # it was really tried
        self.assertEqual(self.uploaded_files(), [])                        # the copy is gone all the same
        self.assertEqual(self.samples()["samples"], [])

    def test_a_crash_in_the_transcriber_also_deletes_the_copy(self):
        self.whisper.error = RuntimeError("boom")
        with contextlib.redirect_stderr(io.StringIO()):                    # the job runner prints the traceback
            _, job = self.start()
            done = self.wait(job)
        self.assertEqual((done["status"], done["error"]), ("failed", {"code": "failed", "message": "internal error", "message_zh": ""}))
        self.assertEqual((self.uploaded_files(), self.samples()["samples"]), ([], []))

    def test_no_speech_is_a_failed_job_with_a_plain_message(self):
        for segments in ([], [{"start": 0.0, "end": 5.0, "text": "[音乐]"}], [{"start": 0.0, "end": 5.0, "text": "  "}]):
            self.whisper.segments = segments
            _, job = self.start()
            done = self.wait(job)
            self.assertEqual((done["status"], done["error"]["code"]), ("failed", "no_speech"), segments)
            self.assertEqual(done["error"]["message"], "No speech was found in this file, so nothing was added to your material.")
            self.assertEqual(done["error"]["message_zh"], "这个文件里没有听到说话的声音，所以没有加进你的素材。")
            self.assertEqual((self.uploaded_files(), self.samples()["samples"]), ([], []))

        self.whisper.segments, self.whisper.unclear_dropped = [], 4        # there was speech, and none of it could be made out
        _, job = self.start()
        done = self.wait(job)
        self.assertEqual(done["error"], {
            "code": "no_speech", "message": "The speech in this file could not be made out clearly, so nothing was added to your material.",
            "message_zh": "这个文件里的话没能听清，所以没有加进你的素材。"})
        self.assertEqual((self.uploaded_files(), self.samples()["samples"]), ([], []))

    def test_only_one_video_is_transcribed_at_a_time(self):
        self.whisper.hold = threading.Event()
        _, first = self.start("one.mp4", b"1")
        self.assertTrue(self.whisper.started.wait(10))
        _, second = self.start("two.mov", b"2")
        time.sleep(0.2)                                                    # time for a second one to start, if it could
        self.assertEqual(self.call("GET", "/api/jobs/" + first["id"])[1]["job"]["stage"], "transcribe")
        waiting = self.call("GET", "/api/jobs/" + second["id"])[1]["job"]
        self.assertEqual((waiting["status"], waiting["stage"]), ("running", "queued"))
        self.assertEqual(len(self.whisper.seen), 1)
        self.assertEqual(len(self.uploaded_files()), 2)                    # both copies wait on disk
        self.whisper.hold.set()
        self.assertEqual([self.wait(job)["status"] for job in (first, second)], ["done", "done"])
        self.assertEqual(self.whisper.max_active, 1)
        self.assertEqual(sorted(s["title"] for s in self.samples()["samples"]), ["one", "two"])
        self.assertEqual(self.uploaded_files(), [])

    def test_what_is_not_a_video_or_recording_is_refused_before_anything_is_written(self):
        for name in ("notes.txt", "page.html", "run.command", "script.py", "noextension", "clip.mp4.exe", "", "clip.mkv"):
            status, out = self.upload(name, b"<script>alert(1)</script>")
            self.assertEqual((status, out["error"]), (415, "bad_media_type"), name)
            self.assertIn(".mp4", out["message"])                          # says what is accepted, in both languages
            self.assertIn(".mp4", out["message_zh"])
        for content_type in ("application/json", "multipart/form-data; boundary=x", "text/plain", None):
            status, out = self.upload("clip.mp4", b"data", content_type=content_type)
            self.assertEqual((status, out["error"]), (415, "octet_stream_required"), content_type)
        self.assertEqual(self.upload("clip.mp4", b"")[1]["error"], "empty_upload")
        self.assertFalse(self.store.uploads_dir.exists())                  # not even the folder was made

    def test_an_upload_must_state_its_size_and_stay_under_the_limit(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("PUT", "/api/uploads?name=clip.mp4")               # no Content-Length
        conn.putheader("Content-Type", "application/octet-stream")
        conn.endheaders()
        res = conn.getresponse()
        self.assertEqual((res.status, json.loads(res.read())["error"]), (411, "length_required"))
        conn.close()

        self.app.max_upload_bytes = 10
        status, out = self.upload("clip.mp4", b"x" * 11)
        self.assertEqual((status, out["error"]), (413, "too_large"))
        self.assertEqual(self.uploaded_files(), [])
        status, out = self.upload("clip.mp4", b"x" * 10)                   # exactly at the limit is fine
        self.assertEqual((status, out["size"]), (201, 10))

    def test_the_file_name_cannot_choose_where_the_file_goes(self):
        outside = [self.tmp.parent / "escape.mp4", self.tmp / "escape.mp4", self.tmp / "config.mp4"]
        for name in ("../../escape.mp4", "..\\..\\escape.mp4", "/tmp/escape.mp4", str(self.tmp / "escape.mp4"), "../config.mp4"):
            status, up = self.upload(name, b"video bytes")
            self.assertEqual(status, 201, name)
            self.assertIn(up["name"], ("escape.mp4", "config.mp4"))        # the label is the last path component
            self.assertTrue((self.store.uploads_dir / (up["upload_id"] + ".mp4")).is_file())
        names = self.uploaded_files()
        self.assertEqual(len(names), 5)
        for name in names:
            self.assertRegex(name, r"^[0-9a-f]{12}\.mp4$")                 # only generated names, only in the uploads folder
        for path in outside:
            self.assertFalse(path.exists(), path)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name != "studio.db"), ["uploads"])

    def test_the_upload_id_cannot_point_anywhere_else_and_is_used_once(self):
        self.store.save_config(dict(self.store.load_config(), provider="manual"))
        for bad in ("../config", "..%2Fconfig", "config.json", "", None, 5, "0123456789ABCDEF", "0123456789ab.mp4"):
            status, out = self.call("POST", "/api/videos", {"upload_id": bad})
            self.assertEqual((status, out["error"]), (400, "bad_upload_id"), bad)
        self.assertEqual(self.call("POST", "/api/videos", {"upload_id": "0" * 12})[1]["error"], "upload_not_found")
        self.assertEqual(self.call("POST", "/api/videos", {"upload_id": "0" * 12, "title": 5})[1]["error"], "bad_title")

        self.whisper.hold = threading.Event()
        up, job = self.start()
        status, out = self.call("POST", "/api/videos", {"upload_id": up["upload_id"]})       # the same upload again
        self.assertEqual((status, out["error"]), (404, "upload_not_found"))
        self.whisper.hold.set()
        self.wait(job)
        self.assertEqual(len(self.whisper.seen), 1)
        self.assertTrue(self.store.config_path.exists())                   # and nothing else in the data folder was deleted
        self.assertEqual(self.call("GET", "/api/jobs/" + "0" * 12)[0], 404)

    def test_an_upload_that_stops_half_way_leaves_nothing(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        head = ("PUT /api/uploads?name=clip.mp4 HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                "Content-Type: application/octet-stream\r\nContent-Length: 100\r\n\r\n" % self.port)
        sock.sendall(head.encode("ascii") + b"x" * 10)                     # 10 of the promised 100 bytes, then nothing
        sock.shutdown(socket.SHUT_WR)
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
        sock.close()
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])
        self.assertIn(b"incomplete_upload", reply)
        self.assertEqual(self.uploaded_files(), [])

    def test_an_upload_nobody_transcribes_is_not_kept(self):
        _, up = self.upload("clip.mp4", b"video bytes")
        self.assertEqual(len(self.uploaded_files()), 1)
        self.app.uploads.drop_stale(now=time.time() + server.UPLOAD_TTL_SEC - 5)
        self.assertEqual(len(self.uploaded_files()), 1)                    # still within the hour
        self.app.uploads.drop_stale(now=time.time() + server.UPLOAD_TTL_SEC + 5)
        self.assertEqual(self.uploaded_files(), [])
        self.assertEqual(self.call("POST", "/api/videos", {"upload_id": up["upload_id"]})[1]["error"], "upload_not_found")

    def expiry_timers(self, uploads=None):
        uploads = uploads or self.app.uploads
        return [t for t in threading.enumerate()
                if isinstance(t, threading.Timer) and getattr(t.function, "__self__", None) is uploads]

    def test_an_upload_nobody_transcribes_is_deleted_when_its_time_is_up_with_no_further_request(self):
        with mock.patch.object(server, "UPLOAD_TTL_SEC", 0.3):
            status, up = self.upload("clip.mp4", b"video bytes")           # and then the page never asks for it
        self.assertEqual(status, 201)
        deadline = time.time() + 10
        while self.uploaded_files() and time.time() < deadline:            # only the folder is looked at: no request is made
            time.sleep(0.05)
        self.assertEqual(self.uploaded_files(), [])
        self.assertEqual(self.call("POST", "/api/videos", {"upload_id": up["upload_id"]})[1]["error"], "upload_not_found")

    def test_the_time_limit_does_not_apply_once_a_video_was_asked_for(self):
        folder = self.tmp / "direct"
        folder.mkdir()
        uploads = server.Uploads(folder)
        asked_for, forgotten = folder / "aaaaaaaaaaaa.mp4", folder / "bbbbbbbbbbbb.mp4"
        for path in (asked_for, forgotten):
            path.write_bytes(b"video bytes")
        with mock.patch.object(server, "UPLOAD_TTL_SEC", 0.3):
            uploads.add("aaaaaaaaaaaa", asked_for, "a.mp4", 11)
            uploads.add("bbbbbbbbbbbb", forgotten, "b.mp4", 11)
        item = uploads.claim("aaaaaaaaaaaa")                               # at once, as the page does
        self.assertEqual(item, {"path": asked_for, "name": "a.mp4", "size": 11, "created_at": item["created_at"]})
        deadline = time.time() + 10
        while forgotten.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(forgotten.exists())                               # its time ran out
        time.sleep(0.2)
        self.assertTrue(asked_for.exists())                                # the same time ran out for this one, which waits for its transcription
        self.assertIsNone(uploads.claim("aaaaaaaaaaaa"))                   # handed out once
        self.assertEqual(self.expiry_timers(uploads), [])

    def test_uploads_still_waiting_are_deleted_when_the_app_closes(self):
        self.upload("one.mp4", b"video bytes")
        self.upload("two.mov", b"video bytes")
        self.assertEqual((len(self.uploaded_files()), len(self.expiry_timers())), (2, 2))
        self.httpd.shutdown()
        self.httpd.server_close()
        self.assertEqual(self.uploaded_files(), [])
        deadline = time.time() + 10
        while self.expiry_timers() and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.expiry_timers(), [])                         # and nothing is left running for them

    def test_starting_on_a_port_that_is_taken_fails_as_a_plain_os_error(self):
        with self.assertRaises(OSError):                                   # what main() relies on to try the next port
            server.make_server(self.port, self.store)

    def test_copies_left_by_an_earlier_run_are_removed_at_start(self):
        data_dir = self.tmp / "earlier"
        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True)
        for name in ("0123456789ab.mp4", "ba9876543210.mov.part", "notes.txt", "holiday.mp4"):
            (uploads / name).write_bytes(b"left over")
        (data_dir / "0123456789ab.mp4").write_bytes(b"not in the uploads folder")
        httpd = server.make_server(0, Store(data_dir), self.fake, self.whisper)
        httpd.server_close()
        self.assertEqual(sorted(p.name for p in uploads.iterdir()), ["holiday.mp4", "notes.txt"])   # only what the server itself wrote
        self.assertTrue((data_dir / "0123456789ab.mp4").exists())

    def test_the_transcriber_is_the_real_one_unless_replaced(self):
        httpd = server.make_server(0, Store(self.tmp / "other"))
        httpd.server_close()
        self.assertIs(httpd.app.transcriber, transcribe)
        for needed in ("status", "install", "transcribe_file", "uninstall", "TranscribeError"):
            self.assertTrue(hasattr(transcribe, needed), needed)


# ---------------------------------------------------------------------------------------------- 3. transcriber
class TranscriberControlTest(SetupCase):
    def test_install_runs_once_as_a_job_and_can_be_removed(self):
        self.whisper.installed = False
        self.whisper.install_hold = threading.Event()
        status, before = self.call("GET", "/api/transcriber")
        self.assertEqual((status, before["installed"], before["supported"], before["job"]), (200, False, True, None))
        self.assertEqual(before["upload"], {"max_bytes": server.MAX_UPLOAD_BYTES, "extensions": list(server.MEDIA_EXTENSIONS)})

        status, out = self.call("POST", "/api/transcriber/install", {})
        self.assertEqual((status, out["job"]["kind"], out["job"]["status"]), (202, "install", "running"))
        self.assertTrue(self.whisper.install_started.wait(10))
        status, again = self.call("POST", "/api/transcriber/install", {})  # a second click while it runs
        self.assertEqual((status, again["job"]["id"]), (202, out["job"]["id"]))
        during = self.call("GET", "/api/transcriber")[1]
        self.assertEqual((during["job"]["id"], during["job"]["status"], during["job"]["stage"], during["job"]["fraction"]),
                         (out["job"]["id"], "running", "model", 0.5))
        self.assertFalse(during["installed"])
        status, busy = self.call("DELETE", "/api/transcriber")
        self.assertEqual((status, busy["error"]), (409, "transcriber_busy"))
        self.assertEqual((self.whisper.install_calls, self.whisper.uninstall_calls), (1, 0))

        self.whisper.install_hold.set()
        self.assertEqual(self.wait(out["job"])["status"], "done")
        after = self.call("GET", "/api/transcriber")[1]
        self.assertEqual((after["installed"], after["model_ready"], after["job"]["status"], after["job"]["stage"]), (True, True, "done", "done"))

        status, removed = self.call("DELETE", "/api/transcriber")
        self.assertEqual((status, removed["installed"], self.whisper.uninstall_calls), (200, False, 1))
        self.assertEqual(removed["job"]["status"], "done")                 # the last install is still reported, finished

    def test_a_failed_install_says_why(self):
        self.whisper.installed = False
        self.whisper.install_error = transcribe.TranscribeError("There is not enough free disk space.", "failed", "disk_full",
                                                                "这台电脑的剩余空间不够。")
        _, out = self.call("POST", "/api/transcriber/install", {})
        done = self.wait(out["job"])
        self.assertEqual((done["status"], done["error"]), ("failed", {
            "code": "failed", "message": "There is not enough free disk space.", "message_zh": "这台电脑的剩余空间不够。"}))
        shown = self.call("GET", "/api/transcriber")[1]
        self.assertEqual((shown["installed"], shown["job"]["status"], shown["job"]["error"]), (False, "failed", done["error"]))
        self.whisper.install_error = None
        _, retry = self.call("POST", "/api/transcriber/install", {})       # a finished job does not block a new try
        self.assertNotEqual(retry["job"]["id"], out["job"]["id"])
        self.assertEqual(self.wait(retry["job"])["status"], "done")
        self.assertEqual(self.whisper.install_calls, 2)

    def test_removal_waits_for_a_running_transcription(self):
        self.whisper.hold = threading.Event()
        _, up = self.upload("clip.mp4", b"video")
        _, out = self.call("POST", "/api/videos", {"upload_id": up["upload_id"]})
        self.assertTrue(self.whisper.started.wait(10))
        self.assertEqual(self.call("DELETE", "/api/transcriber")[0], 409)
        self.assertEqual(self.whisper.uninstall_calls, 0)
        self.whisper.hold.set()
        self.wait(out["job"])
        self.assertEqual(self.call("DELETE", "/api/transcriber")[0], 200)


# ---------------------------------------------------------------------------------------------- writes from other sites
class CrossSiteTest(SetupCase):
    def own(self, host="127.0.0.1"):
        return "http://%s:%d" % (host, self.port)

    def test_a_page_on_another_site_cannot_accept_a_proposal(self):
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.call("POST", "/api/voice/learn", {})
        before = self.in_use()
        approve = json.dumps({"rejected": []}).encode()
        for headers, refused in (
            ({"Content-Type": "application/x-www-form-urlencoded"}, (415, "json_required")),        # a plain form
            ({"Content-Type": "text/plain"}, (415, "json_required")),                                # a form posting JSON-looking text
            ({"Content-Type": "text/plain;application/json"}, (415, "json_required")),               # the words inside another type
            ({"Content-Type": "multipart/form-data; boundary=application/json"}, (415, "json_required")),
            ({}, (415, "json_required")),
            ({"Content-Type": "application/json", "Origin": "http://evil.example"}, (403, "bad_origin")),
            ({"Content-Type": "application/json", "Origin": "null"}, (403, "bad_origin")),
            ({"Content-Type": "application/json", "Origin": "http://localhost:%d" % (self.port + 1)}, (403, "bad_origin")),
            ({"Content-Type": "application/json", "Origin": "https://127.0.0.1:%d" % self.port}, (403, "bad_origin")),
        ):
            status, out = self.request("POST", "/api/voice/approve", approve, headers)
            self.assertEqual((status, out["error"]), refused, headers)
            self.assertEqual(self.in_use(), before, headers)
            self.assertIsNotNone(self.pending(), headers)
        status, out = self.call("DELETE", "/api/voice/pending", origin="http://evil.example")
        self.assertEqual((status, out["error"]), (403, "bad_origin"))
        self.assertIsNotNone(self.pending())

        for origin in (self.own(), self.own("localhost")):                 # the app's own pages are let through
            self.call("POST", "/api/voice/learn", {}, origin=origin)
            status, out = self.approve({"rejected": []}, origin=origin)
            self.assertEqual((status, out["is_example"]), (200, False), origin)

    def test_a_page_on_another_site_cannot_upload_or_start_an_install(self):
        self.whisper.installed = False
        status, out = self.upload("clip.mp4", b"video bytes", origin="http://evil.example")
        self.assertEqual((status, out["error"]), (403, "bad_origin"))
        status, out = self.request("POST", "/api/uploads?name=clip.mp4", b"video bytes", {"Content-Type": "application/octet-stream"})
        self.assertEqual((status, out["error"]), (415, "json_required"))   # a form can POST, so POST never takes a raw body
        self.assertFalse(self.store.uploads_dir.exists())
        for headers in ({}, {"Content-Type": "text/plain"}, {"Content-Type": "application/json", "Origin": "http://evil.example"}):
            status, out = self.request("POST", "/api/transcriber/install", b"{}", headers)
            self.assertIn((status, out["error"]), ((415, "json_required"), (403, "bad_origin")), headers)
        self.assertEqual(self.whisper.install_calls, 0)
        status, up = self.upload("clip.mp4", b"video bytes", origin=self.own())
        self.assertEqual(status, 201)

    def test_no_route_invites_other_sites_in(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("OPTIONS", "/api/uploads?name=clip.mp4", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"})
        res = conn.getresponse()                                           # the browser's question before a cross-site PUT
        res.read()
        self.assertEqual(res.status, 501)                                  # is never answered with a yes
        self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/setup", headers={"Origin": "http://evil.example"})
        res = conn.getresponse()
        res.read()
        self.assertEqual(res.status, 200)                                  # reads are answered, but without permission to read them
        self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))
        conn.close()


# ---------------------------------------------------------------------------------------------- 4. a proposal until accepted
class ProposalTest(SetupCase):
    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)

    def learn_direct(self):
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, out)
        return out["pending"]

    def learn_by_paste(self):
        self.assertEqual(self.call("POST", "/api/manual/prompt", {"task": "voice"})[0], 200)
        status, out = self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(self.fake.dna, ensure_ascii=False)})
        self.assertEqual(status, 200, out)
        return out["pending"]

    def learn_by_mcp(self):
        self.assertFalse(self.mcp("get_voice_learning_brief")[0])
        is_error, out = self.mcp("save_voice_profile", profile=self.fake.dna)
        self.assertFalse(is_error, out)
        return out

    def test_learning_leaves_the_profile_in_use_alone_on_every_route(self):
        routes = (("direct", self.learn_direct), ("paste", self.learn_by_paste), ("mcp", self.learn_by_mcp))
        self.assertEqual(self.in_use(), (None, [], {"dna": EXAMPLE_DNA, "is_example": True}))
        for name, learn in routes:                                         # first while the bundled example is in use
            learn()
            self.assertEqual(self.in_use(), (None, [], {"dna": EXAMPLE_DNA, "is_example": True}), name)
            self.assertEqual(self.pending()["dna"]["persona_summary"]["one_line_essence_zh"], "松弛", name)
            self.assertEqual(self.setup()["voice"]["state"], "pending", name)
            self.assertEqual(self.call("DELETE", "/api/voice/pending")[0], 200)
            self.assertIsNone(self.pending(), name)

        mine = self.learn_and_accept()                                     # then while a profile of her own is in use
        before = self.in_use()
        self.assertEqual((before[1], before[2]), ([], {"dna": mine, "is_example": False}))
        self.fake.dna["persona_summary"]["one_line_essence_zh"] = "第二版"
        for name, learn in routes:
            learn()
            self.assertEqual(self.in_use(), before, name)                  # byte for byte, and no history entry was made
            self.assertEqual(self.pending()["dna"]["persona_summary"]["one_line_essence_zh"], "第二版", name)
            self.assertEqual(self.call("GET", "/api/dna")[1]["dna"]["persona_summary"]["one_line_essence_zh"], "松弛", name)
            _, check = self.call("POST", "/api/lint", {"lines": lines("今天去超市买菜"), "target_sec": 60})
            self.assertFalse(check["dna_is_example"], name)
            self.call("DELETE", "/api/voice/pending")

    def test_only_approval_puts_a_learned_profile_into_use(self):
        proposal = self.learn_direct()
        self.assertEqual(set(proposal), {"dna", "report", "created_at", "based_on"})
        self.assertEqual(proposal["based_on"], 1)
        self.assertEqual(self.pending(), proposal)
        self.assertTrue(self.call("GET", "/api/dna")[1]["is_example"])

        status, out = self.approve({"rejected": []})
        self.assertEqual((status, out["is_example"]), (200, False))
        self.assertEqual(self.call("GET", "/api/dna")[1], {"dna": out["dna"], "is_example": False})
        self.assertEqual(json.loads(self.store.dna_path.read_text(encoding="utf-8")), out["dna"])
        self.assertIsNone(self.pending())                                  # the proposal is used up
        self.assertFalse(self.store.pending_path.exists())
        self.assertEqual(self.setup()["voice"]["state"], "active")

        first = self.store.dna_path.read_bytes()
        self.learn_direct()
        self.approve()
        history = list(self.store.dna_history_dir.glob("*.json"))
        self.assertEqual([p.read_bytes() for p in history], [first])       # the profile it replaced is kept

    def test_an_ai_application_has_no_way_to_accept(self):
        result = self.learn_by_mcp()
        self.assertEqual((result["active"], result["status"]), (False, "proposal_stored_waiting_for_the_creator"))
        self.assertIn("Only the creator can accept it", result["next_step"])
        self.assertIn("You cannot accept it for them", result["next_step"])
        self.assertEqual(result["based_on_scripts"], 1)
        self.assertEqual({k: result["evidence"][k] for k in ("checked", "found", "not_found", "absent", "conflict")},
                         {"checked": 7, "found": 2, "not_found": 2, "absent": 2, "conflict": 1})
        tools = {name: tool["public"] for name, tool in mcp_server.TOOLS.items()}
        self.assertEqual(len(tools), 20)                                   # nothing was added for approval
        for name, public in tools.items():
            self.assertIsNone(re.search(r"approv|accept_|activate", name), name)
        self.assertIn("does NOT become the active voice profile", tools["save_voice_profile"]["description"])
        self.assertIn("PROPOSAL", mcp_server.INSTRUCTIONS)
        # every tool that writes, called with what an eager client might send: the profile in use stays the example
        script_id = self.store.create_script(title="t", lines=lines("好啦"))["id"]
        for name, arguments in (("save_voice_profile", {"profile": self.fake.dna, "approve": True}),
                                ("save_script", {"script": AI_SCRIPT, "notes": "x"}),
                                ("update_script", {"script_id": script_id, "script_lines": lines("好啦好啦")}),
                                ("save_topic_ideas", {"ideas": {"ideas": [{"title_zh": "选题"}]}}),
                                ("export_jianying_kit", {"script_id": script_id}),
                                ("add_trend_keywords", {"keywords": [{"keyword": "露营", "source_url": "https://example.com/a"}]}),
                                ("add_video_plan", {"title": "下周选题"})):
            mcp_server.call_tool(self.store, name, arguments)
            self.assertEqual(self.store.load_dna(), (EXAMPLE_DNA, True), name)
        _, profile = self.mcp("get_voice_profile")
        self.assertEqual((profile["is_example"], profile["proposal_waiting"]), (True, True))
        self.assertEqual(profile["profile"], EXAMPLE_DNA)                  # the proposal is not handed out as the profile

    def test_every_claim_is_checked_and_approval_applies_her_decisions(self):
        proposal = self.learn_direct()
        report = proposal["report"]
        self.assertEqual([(item["path"], item["status"]) for item in report["items"]], CHECKED_STATUSES)
        by_path = {item["path"]: item for item in report["items"]}
        self.assertEqual((by_path[OPENER]["count"], by_path[OPENER]["titles"]), (12, ["超市那天"]))
        self.assertEqual({k: report["summary"][k] for k in ("checked", "found", "not_found", "absent", "conflict")},
                         {"checked": 7, "found": 2, "not_found": 2, "absent": 2, "conflict": 1})
        cadence = proposal["dna"]["cadence_and_syntax"]
        self.assertEqual(cadence["ideal_clause_length_chars"], {"min": 7, "median": 7, "hard_max": 8})   # measured, not the model's
        self.assertEqual(self.slop("排队排了半小时啦"), [])                    # the example profile has no opinion on her own line

        status, out = self.approve({"rejected": [USES]})                    # she unticks 你们看
        self.assertEqual(status, 200)
        dna = out["dna"]
        self.assertEqual(dna["persona_summary"]["audience_address_terms"], {"uses": [], "never_uses": ["宝子们"]})
        self.assertEqual(dna["lexical_fingerprint"]["signature_openers"], [{"pattern_zh": "今天去…", "verbatim_sample": "今天去超市买菜"}])
        self.assertEqual(dna["lexical_fingerprint"]["modal_particles"], {"primary": [{"particle": "啦"}]})
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["绝绝子"])          # the ban on her own words is gone
        # free text kept: the one-line summary and the pattern of the opener that stayed
        self.assertEqual(dna["evidence_review"], {"checked": 7, "found": 2, "removed_unverified": 1, "rejected_by_creator": 1,
                                                  "kept_unverified": 0, "free_text_kept": 2, "free_text_removed": 0,
                                                  "conflicts_dropped": ["排队排了半小时啦"]})
        self.assertNotIn("这句话她从来没说过", json.dumps(dna, ensure_ascii=False))             # the invented quote is nowhere in it
        self.assertEqual(self.slop("排队排了半小时啦"), [])                    # so her own line is still not flagged
        self.assertEqual([(s, m) for s, m, _ in self.slop("这个真的绝绝子")], [("error", "绝绝子")])

    def test_receipts_are_checked_against_the_material_as_it_is_now(self):
        self.add("周末爬山", "周末去爬山啦 / 风景很好嘛")
        supermarket = [s for s in self.samples()["samples"] if s["title"] == "超市那天"][0]
        self.learn_direct()
        self.call("DELETE", "/api/samples/" + supermarket["id"])           # the script the quotes came from is deleted
        now = {item["path"]: (item["status"], item["count"]) for item in self.pending()["report"]["items"]}
        self.assertEqual(now[OPENER], ("not_found", 0))                    # the receipt it had no longer exists
        self.assertEqual(now["lexical_fingerprint.modal_particles.primary[0].particle"], ("found", 1))   # 啦 still ends one breath
        self.assertEqual(now["anti_voice_blacklist.banned_phrases_zh[1]"], ("absent", 0))                # and that ban no longer contradicts her
        dna = self.approve({"rejected": []})[1]["dna"]
        self.assertEqual(dna["lexical_fingerprint"]["signature_openers"], [])                 # so the quote does not get in
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["绝绝子", "排队排了半小时啦"])
        # both quotes go for lack of a receipt, and so does the habit 你们看: not found, and nobody ticked it
        self.assertEqual((dna["evidence_review"]["found"], dna["evidence_review"]["removed_unverified"]), (1, 3))

    def test_approving_with_nothing_pending_or_nothing_to_check_against(self):
        before = self.in_use()
        status, out = self.approve({"rejected": []})
        self.assertEqual((status, out["error"]), (409, "no_pending"))
        self.assertEqual(self.in_use(), before)

        self.learn_direct()
        for bad in ("uses[0]", {"a": 1}, [1, 2], [USES, None]):
            status, out = self.call("POST", "/api/voice/approve", {"rejected": bad})
            self.assertEqual((status, out["error"]), (400, "bad_rejected"), bad)
        for sample in self.samples()["samples"]:
            self.call("DELETE", "/api/samples/" + sample["id"])
        status, out = self.approve({"rejected": []})
        self.assertEqual((status, out["error"]), (400, "no_samples"))      # nothing left to verify it with: not accepted unverified
        self.assertEqual(self.in_use(), before)
        self.assertIsNotNone(self.pending())

        status, out = self.call("DELETE", "/api/voice/pending")
        self.assertEqual((status, out), (200, {"ok": True, "discarded": True}))
        self.assertEqual(self.call("DELETE", "/api/voice/pending")[1], {"ok": True, "discarded": False})
        self.assertEqual(self.approve()[1]["error"], "no_pending")
        self.assertEqual(self.in_use(), before)

    def test_a_new_proposal_replaces_the_older_one_and_a_bad_reply_stores_none(self):
        self.learn_direct()
        self.fake.dna["persona_summary"]["one_line_essence_zh"] = "第二版"
        self.learn_by_paste()
        self.assertEqual(self.pending()["dna"]["persona_summary"]["one_line_essence_zh"], "第二版")
        self.call("DELETE", "/api/voice/pending")

        self.fake.dna = {"persona_summary": {}}
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual((status, out["error"], out["reason"]), (502, "ai_failed", "empty_result"))
        self.assertEqual(self.call("POST", "/api/manual/reply", {"task": "voice", "reply": "{}"})[1]["reason"], "empty_result")
        self.assertTrue(self.mcp("save_voice_profile", profile={"cadence_and_syntax": {}})[0])
        self.assertIsNone(self.pending())

    def test_based_on_counts_what_the_prompt_held_not_what_arrived_later(self):
        self.add("早餐日常", BREAKFAST)
        self.add("商单面霜", AD, is_sponsored=True)
        self.assertEqual(self.learn_direct()["based_on"], 2)               # two of hers; the ad is not counted

        self.call("POST", "/api/manual/prompt", {"task": "voice"})         # the prompt she copies holds two scripts
        self.add("周末爬山", HIKE)                                          # a third arrives before she pastes the reply
        pasted = self.learn_by_reply_only()
        self.assertEqual(pasted["based_on"], 2)
        self.assertEqual(pasted["dna"]["source_corpus_summary"]["organic_scripts_analyzed"], 2)

        self.mcp("get_voice_learning_brief")                               # a brief built now holds all three
        self.assertEqual(self.mcp("save_voice_profile", profile=self.fake.dna)[1]["based_on_scripts"], 3)

    def learn_by_reply_only(self):
        status, out = self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(self.fake.dna, ensure_ascii=False)})
        self.assertEqual(status, 200, out)
        return out["pending"]

    def test_a_profile_saved_without_ever_asking_for_the_material_is_based_on_nothing(self):
        result = self.learn_by_mcp_without_brief()
        self.assertEqual(result["based_on_scripts"], 0)
        self.assertEqual(self.pending()["based_on"], 0)

    def learn_by_mcp_without_brief(self):
        is_error, out = self.mcp("save_voice_profile", profile=self.fake.dna)
        self.assertFalse(is_error, out)
        return out


# ---------------------------------------------------------------------------------------------- wording only from confirmed text
class ReviewRuleTest(SetupCase):
    """A video's transcript is a machine's reading of her words: its timing counts at once, its wording once she confirms it.

    Numbers, worked out by hand. Her script has 36 breaths, 12 ending in 嘛 and 12 in 啦. The video is 30
    cues of ``VIDEO_LINE`` (8 units each, timed like ``CUES``): 30 more breaths, all ending in 呗, a
    particle her script never has. Together: 66 breaths; of the 54 that end in a particle, 30 / 12 / 12,
    which is 56% / 22% / 22%.
    """

    VIDEO_LINE = "周末去公园散步呗"
    VIDEO_TEXT = "\n".join([VIDEO_LINE] * 30)
    VIDEO_TIMING = dict(TIMING_30, unclear_dropped=0)
    SCRIPT_PARTICLES = [{"particle": "嘛", "count": 12, "share_pct": 50}, {"particle": "啦", "count": 12, "share_pct": 50}]
    BOTH_PARTICLES = [{"particle": "呗", "count": 30, "share_pct": 56}, {"particle": "嘛", "count": 12, "share_pct": 22},
                      {"particle": "啦", "count": 12, "share_pct": 22}]
    # three claims in the proposed profile that only the video could back up
    VIDEO_OPENER = "lexical_fingerprint.signature_openers[2].verbatim_sample"
    VIDEO_PARTICLE = "lexical_fingerprint.modal_particles.primary[1].particle"
    VIDEO_BAN = "anti_voice_blacklist.banned_phrases_zh[2]"
    HER_OPENER = {"pattern_zh": "今天去…", "verbatim_sample": "今天去超市买菜"}
    VIDEO_QUOTE = {"pattern_zh": "周末去…", "verbatim_sample": VIDEO_LINE}

    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.fake.dna["lexical_fingerprint"]["signature_openers"].append(dict(self.VIDEO_QUOTE))
        self.fake.dna["lexical_fingerprint"]["modal_particles"]["primary"].append({"particle": "呗"})
        self.fake.dna["anti_voice_blacklist"]["banned_phrases_zh"].append("公园散步")

    def add_video(self, title="散步那天", line=None, **body):
        self.whisper.segments = [{"start": start, "end": end, "text": line or self.VIDEO_LINE} for start, end, _ in CUES]
        status, up = self.upload("clip.mp4", b"video bytes")
        self.assertEqual(status, 201, up)
        status, out = self.call("POST", "/api/videos", dict({"upload_id": up["upload_id"], "title": title}, **body))
        self.assertEqual(status, 202, out)
        done = self.wait(out["job"])
        self.assertEqual(done["status"], "done", done)
        return self.store.get_sample(done["result"]["sample_id"])

    def confirm(self, item, needs_review=False):
        status, out = self.call("PUT", "/api/samples/" + item["id"], {"needs_review": needs_review})
        self.assertEqual(status, 200, out)
        return out

    # ---- the four things that learn or check wording, as each route builds them ----
    def voice_prompts(self):
        """The voice-learning prompt from the direct call, the copy-and-paste route and MCP."""
        self.assertEqual(self.call("POST", "/api/voice/learn", {})[0], 200)
        status, pasted = self.call("POST", "/api/manual/prompt", {"task": "voice"})
        self.assertEqual(status, 200)
        is_error, brief = self.mcp("get_voice_learning_brief")
        self.assertFalse(is_error, brief)
        return self.fake.prompt_for("sociolinguist"), pasted["prompt"], brief

    def corpus_titles(self, prompts):
        return [[s["title"] for s in block(prompt, "organic_video_corpus")["scripts"]] for prompt in prompts]

    def exemplar_titles(self):
        """The titles a draft is anchored on, from the direct call, the copy-and-paste route and MCP."""
        drafted = self.draft()
        status, pasted = self.call("POST", "/api/manual/prompt", {"task": "draft", "notes": "再去一次超市", "target_sec": 30})
        self.assertEqual(status, 200)
        is_error, brief = self.mcp("get_script_brief", notes="再去一次超市")
        self.assertFalse(is_error, brief)
        prompts = (self.fake.prompt_for("ghostwriting"), pasted["prompt"], brief)
        return [[e["title"] for e in block(prompt, EXEMPLARS_TAG)] for prompt in prompts] + [drafted["basis"]["exemplar_titles"]]

    def evidence(self):
        """How the three video-only claims stand, in a fresh proposal's report and in the report the page reads."""
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, out)
        fresh = {item["path"]: item for item in out["pending"]["report"]["items"]}
        shown = {item["path"]: item for item in self.pending()["report"]["items"]}
        self.assertEqual(fresh, shown)
        return {path: shown[path]["status"] for path in (self.VIDEO_OPENER, self.VIDEO_PARTICLE, self.VIDEO_BAN)}, shown

    def summary(self, report_summary):
        return {key: report_summary[key] for key in ("checked", "found", "not_found", "absent", "conflict")}

    def test_an_unconfirmed_video_sets_the_rate_and_nothing_about_her_wording(self):
        before = self.samples()
        self.assertEqual((before["measured"], before["stats"]["clauses"], before["stats"]["clause_final_particles"]),
                         (None, 36, self.SCRIPT_PARTICLES))
        video = self.add_video()
        self.assertEqual((video["kind"], video["needs_review"], video["text"], video["timing"]),
                         ("video", True, self.VIDEO_TEXT, self.VIDEO_TIMING))

        # ---- while it waits ----
        listing = self.samples()
        self.assertEqual(listing["measured"], MEASURED_30)                 # the speaking rate is measured from it at once
        self.assertEqual(listing["stats"], before["stats"])                # particle counts and every other measured habit: untouched
        self.assertEqual([(s["title"], s["needs_review"]) for s in listing["samples"]], [("散步那天", True), ("超市那天", False)])
        listed = {s["title"]: (s["kind"], s["needs_review"]) for s in self.mcp("list_library")[1]["scripts"]}
        self.assertEqual(listed, {"散步那天": ("video", True), "超市那天": ("script", False)})    # an AI application is told the same
        self.assertEqual(self.setup()["material"], {"done": False, "total": 2, "organic": 1, "unreviewed": 1, "clauses": 36,
                                                    "enough_for_cadence": True, "timed_sec": 74.5, "measured_rate": 193})
        prompts = self.voice_prompts()
        self.assertEqual(self.corpus_titles(prompts), [["超市那天"]] * 3)    # the learning corpus, on every route
        for prompt in prompts:
            self.assertNotIn(self.VIDEO_LINE, prompt)
            metrics = block(prompt, "computed_acoustic_stats")
            self.assertEqual((metrics["clauses"], metrics["clause_final_particles"]), (36, self.SCRIPT_PARTICLES))
            self.assertEqual(metrics["measured_speaking_rate"], {"units_per_minute": 193, "seconds_of_speech": 74.5, "timed_items": 1})
            self.assertIn("hand-written scripts", metrics["note"])         # the model is told the wording numbers come from her scripts
        self.assertEqual(self.exemplar_titles(), [["超市那天"]] * 4)        # the examples a draft is anchored on, on every route
        statuses, _ = self.evidence()                                      # the evidence report
        self.assertEqual(statuses, {self.VIDEO_OPENER: "not_found", self.VIDEO_PARTICLE: "not_found", self.VIDEO_BAN: "absent"})
        waiting = {"checked": 10, "found": 2, "not_found": 4, "absent": 3, "conflict": 1}
        self.assertEqual(self.summary(self.pending()["report"]["summary"]), waiting)
        proposed = self.pending()["dna"]                                   # in the proposal itself: the rate is the measured one,
        self.assertEqual((proposed["cadence_and_syntax"]["target_speaking_rate_cpm"], proposed["source_corpus_summary"]["speaking_rate_source"]),
                         (193, "measured"))
        self.assertEqual((proposed["cadence_and_syntax"]["ideal_clause_length_chars"], proposed["source_corpus_summary"]["clauses_measured"]),
                         ({"min": 7, "median": 7, "hard_max": 8}, 36))     # and the breath lengths are her script's: 24 of 7, 12 of 8
        _, pasted = self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(self.fake.dna, ensure_ascii=False)})
        self.assertEqual((self.summary(pasted["pending"]["report"]["summary"]), pasted["pending"]["based_on"]), (waiting, 1))
        self.assertEqual(self.summary(self.mcp("save_voice_profile", profile=self.fake.dna)[1]["evidence"]), waiting)

        # ---- once she has confirmed it ----
        confirmed = self.confirm(video)
        self.assertEqual((confirmed["needs_review"], confirmed["text"], confirmed["timing"]), (False, self.VIDEO_TEXT, self.VIDEO_TIMING))
        listing = self.samples()
        self.assertEqual(listing["measured"], MEASURED_30)                 # the rate is what it already was
        self.assertEqual((listing["stats"]["clauses"], listing["stats"]["clause_final_particles"]), (66, self.BOTH_PARTICLES))
        self.assertNotIn("呗", listing["stats"]["particles_never_seen"])
        self.assertEqual(self.setup()["material"], {"done": False, "total": 2, "organic": 2, "unreviewed": 0, "clauses": 66,
                                                    "enough_for_cadence": True, "timed_sec": 74.5, "measured_rate": 193})
        prompts = self.voice_prompts()
        self.assertEqual(self.corpus_titles(prompts), [["散步那天", "超市那天"]] * 3)
        for prompt in prompts:
            self.assertIn(self.VIDEO_LINE, prompt)
            metrics = block(prompt, "computed_acoustic_stats")
            self.assertEqual((metrics["clauses"], metrics["clause_final_particles"]), (66, self.BOTH_PARTICLES))
            self.assertIn("read and confirmed", metrics["note"])
        self.assertEqual(self.exemplar_titles(), [["散步那天", "超市那天"]] * 4)
        statuses, shown = self.evidence()
        self.assertEqual(statuses, {self.VIDEO_OPENER: "found", self.VIDEO_PARTICLE: "found", self.VIDEO_BAN: "conflict"})
        self.assertEqual(shown[self.VIDEO_OPENER]["titles"], ["散步那天"])  # and the receipt names where it was found
        settled = {"checked": 10, "found": 4, "not_found": 2, "absent": 2, "conflict": 2}
        self.assertEqual((self.summary(self.pending()["report"]["summary"]), self.pending()["based_on"]), (settled, 2))
        proposed = self.pending()["dna"]                                   # 66 breaths now, 24 of 7 and 42 of 8: the median moves to 8
        self.assertEqual((proposed["cadence_and_syntax"]["ideal_clause_length_chars"], proposed["source_corpus_summary"]["clauses_measured"]),
                         ({"min": 7, "median": 8, "hard_max": 8}, 66))
        self.assertEqual(self.summary(self.mcp("save_voice_profile", profile=self.fake.dna)[1]["evidence"]), settled)

    def test_correcting_the_text_does_not_confirm_it_and_what_counts_is_the_text_she_confirmed(self):
        video = self.add_video()
        corrected = "\n".join(["周末去公园散步嘛"] * 30)                    # she fixes the particle the transcriber got wrong
        status, edited = self.call("PUT", "/api/samples/" + video["id"], {"text": corrected, "title": "散步那天（校对过）", "likes": 300})
        self.assertEqual((status, edited["text"], edited["needs_review"], edited["reviewed_at"]), (200, corrected, True, None))
        self.assertEqual(self.samples()["stats"]["clause_final_particles"], self.SCRIPT_PARTICLES)   # an edit alone changes nothing learned
        self.assertEqual(self.setup()["material"]["unreviewed"], 1)

        confirmed = self.confirm(video)
        self.assertEqual((confirmed["needs_review"], confirmed["text"], confirmed["kind"], confirmed["timing"]),
                         (False, corrected, "video", self.VIDEO_TIMING))   # still a video, timing as measured
        self.assertEqual(self.samples()["stats"]["clause_final_particles"],        # 30 + 12 嘛 and 12 啦 of 54: her correction, not 呗
                         [{"particle": "嘛", "count": 42, "share_pct": 78}, {"particle": "啦", "count": 12, "share_pct": 22}])
        self.assertNotIn(self.VIDEO_LINE, self.voice_prompts()[0])

    def test_only_a_plain_yes_or_no_changes_the_flag(self):
        video = self.add_video()
        for bad in ("false", 0, None, [], "no"):
            status, out = self.call("PUT", "/api/samples/" + video["id"], {"needs_review": bad, "title": "不该改"})
            self.assertEqual((status, out["error"]), (400, "bad_needs_review"), bad)
        stored = self.store.get_sample(video["id"])
        self.assertEqual((stored["needs_review"], stored["title"]), (True, "散步那天"))       # refused whole: nothing else was applied either
        typed = self.call("POST", "/api/samples", {"title": "手写的", "text": HIKE, "needs_review": True, "kind": "video"})[1]
        self.assertEqual((typed["kind"], typed["needs_review"]), ("script", False))          # at creation the kind decides, not the request
        self.assertEqual([name for name in mcp_server.TOOLS if re.search(r"sample|review|confirm|material|library", name)],
                         ["list_library"])                                 # and an AI application can list items, never confirm one

        earliest = time.time()
        confirmed = self.confirm(video)
        self.assertFalse(confirmed["needs_review"])
        self.assertTrue(earliest <= confirmed["reviewed_at"] <= time.time())
        self.assertEqual(self.confirm(video)["reviewed_at"], confirmed["reviewed_at"])       # confirming again does not move the moment
        back = self.confirm(video, needs_review=True)                      # she can take it back
        self.assertEqual((back["needs_review"], back["reviewed_at"]), (True, None))
        script = [s for s in self.samples()["samples"] if s["title"] == "超市那天"][0]
        self.confirm(script, needs_review=True)                            # and set any item aside
        material = self.setup()["material"]
        self.assertEqual((material["total"], material["organic"], material["unreviewed"], material["clauses"]), (3, 1, 2, 1))

    def test_with_nothing_but_a_waiting_transcript_there_is_nothing_to_learn_from(self):
        self.call("PUT", "/api/creator", {"name": "小满"})
        self.call("DELETE", "/api/samples/" + self.samples()["samples"][0]["id"])
        self.add_video()
        status = self.setup()
        self.assertEqual(status["material"], {"done": False, "total": 1, "organic": 0, "unreviewed": 1, "clauses": 0,
                                              "enough_for_cadence": False, "timed_sec": 74.5, "measured_rate": 193})
        self.assertEqual(status["next"], "material")
        self.assertEqual(self.samples()["stats"]["clauses"], 0)

        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual((status, out["error"]), (400, "no_samples"))
        status, out = self.call("POST", "/api/manual/prompt", {"task": "voice"})
        self.assertEqual((status, out["error"], out["reason"]), (502, "ai_failed", "no_samples"))
        is_error, message = self.mcp("get_voice_learning_brief")
        self.assertTrue(is_error)
        self.assertIn("once the creator has confirmed it", message)        # an AI application is told why, not just "nothing"
        self.assertTrue(self.mcp("save_voice_profile", profile=self.fake.dna)[0])
        self.assertEqual(self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(self.fake.dna, ensure_ascii=False)})[1]["reason"],
                         "no_samples")
        self.assertIsNone(self.pending())
        self.assertEqual(self.draft()["basis"]["exemplar_titles"], [])     # and a draft has nothing of hers to be anchored on

    def test_a_transcript_confirmed_after_the_voice_was_learned_counts_as_new_material(self):
        video = self.add_video()
        self.learn_and_accept()
        voice = self.setup()["voice"]
        self.assertEqual((voice["based_on"], voice["new_since"]), (1, 0))  # learned from her script; a waiting transcript is not new material
        self.confirm(video)
        self.assertEqual(self.setup()["voice"]["new_since"], 1)            # added before, hers to learn from only now
        self.call("PUT", "/api/samples/" + video["id"], {"likes": 20})     # other edits do not make an item new
        self.assertEqual(self.setup()["voice"]["new_since"], 1)
        self.learn_and_accept()
        voice = self.setup()["voice"]
        self.assertEqual((voice["based_on"], voice["new_since"]), (2, 0))

    def test_a_quote_found_only_in_a_waiting_transcript_does_not_get_into_her_profile(self):
        self.add_video()
        dna = self.learn_and_accept()
        self.assertEqual(dna["lexical_fingerprint"]["signature_openers"], [self.HER_OPENER])
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["绝绝子", "公园散步"])   # nothing she has confirmed says it
        self.assertNotIn(self.VIDEO_LINE, json.dumps(dna, ensure_ascii=False))

    def test_a_proposal_is_checked_at_approval_against_what_she_has_confirmed_by_then(self):
        video = self.add_video()
        statuses, _ = self.evidence()                                      # proposed while the transcript waits
        self.assertEqual(statuses[self.VIDEO_OPENER], "not_found")
        self.confirm(video)                                                # she reads it and confirms before deciding
        shown = {item["path"]: item["status"] for item in self.pending()["report"]["items"]}
        self.assertEqual((shown[self.VIDEO_OPENER], shown[self.VIDEO_BAN]), ("found", "conflict"))
        dna = self.approve({"rejected": []})[1]["dna"]
        self.assertEqual(dna["lexical_fingerprint"]["signature_openers"], [self.HER_OPENER, self.VIDEO_QUOTE])
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["绝绝子"])          # a ban on what she does say is dropped

    def test_a_sponsored_transcript_waits_as_well(self):
        ad_line = "这款面霜真的很好用啦"
        ad = self.add_video("面霜那期", ad_line, is_sponsored=True)
        self.assertEqual((ad["needs_review"], ad["is_sponsored"]), (True, True))
        self.assertEqual((self.setup()["material"]["unreviewed"], self.samples()["measured"]), (1, None))   # counted as waiting; an ad sets no rate
        prompt = self.voice_prompts()[0]
        self.assertNotIn(ad_line, prompt)
        self.assertEqual(block(prompt, "past_sponsored_bridges_corpus"), [])
        self.assertEqual(self.pending()["dna"]["source_corpus_summary"]["sponsored_scripts_analyzed"], 0)
        self.confirm(ad)
        prompt = self.voice_prompts()[0]
        self.assertEqual([s["title"] for s in block(prompt, "past_sponsored_bridges_corpus")], ["面霜那期"])
        self.assertEqual(self.pending()["dna"]["source_corpus_summary"]["sponsored_scripts_analyzed"], 1)
        self.assertEqual(self.samples()["stats"]["clause_final_particles"], self.SCRIPT_PARTICLES)   # an ad's wording is never among her habits


# ---------------------------------------------------------------------------------------------- 5. speaking rate
SETUP_JS = (ROOT / "studio" / "web" / "setup.js").read_text(encoding="utf-8")


def page_words(lang):
    """The texts of setup.js in one language, as written in the file."""
    start = SETUP_JS.index("Object.assign(I18N.%s, {" % lang)
    return SETUP_JS[start:SETUP_JS.index("\n});", start)]


def free_text_fields():
    """Every field of a profile that holds free text: text the evidence check looks nothing up for.

    The same walk as ``evidence._free_text``, over the shape of a profile instead of over one profile.
    """
    found = []

    def walk(shape, name):
        for key, sub in shape.items():
            here = "%s.%s" % (name, key) if name else key
            if sub in (evidence._TEXT, evidence._TEXTS):
                if here not in evidence._CHECKED_FIELDS and ("%s.*" % name) not in evidence._CHECKED_FIELDS:
                    found.append(here)
            elif isinstance(sub, list):
                walk(sub[0], here)
            elif isinstance(sub, dict):
                walk(sub, here)
    walk(evidence._SHAPE, "")
    return found


def as_the_page_sends(pending, unticked=(), ticked=()):
    """The body of POST /api/voice/approve as the review page builds it (reviewHtml and the click on
    "Accept this profile" in studio/web/setup.js), written out again here in Python.

    ``unticked`` and ``ticked`` are paths of boxes the creator changed before the click. The rules, as on the page:
    a ban the material contradicts and a quote that was not found have a locked box; what was found, what is
    indeed absent and what cannot be checked start ticked; a habit that was not found starts unticked. Free text
    starts ticked, except a note about an entry whose box is not ticked: that one is locked and goes with its entry.
    Sent: ``created_at``; ``rejected``, every unlocked box that is not ticked; ``keep``, every ticked box of a thing
    the material does not bear out (not found, not checkable, free text).
    """
    report = pending["report"]
    fixed = lambda it: it["status"] == "conflict" or (it["kind"] == "quote" and it["status"] == "not_found")
    on = {}
    for it in report["items"]:
        if not fixed(it):
            on[it["path"]] = (it["status"] in ("found", "absent", "not_checkable") or it["path"] in ticked) and it["path"] not in unticked
    needs_keep = {it["path"] for it in report["items"] if not fixed(it) and it["status"] in ("not_found", "not_checkable")}
    items = {it["path"] for it in report["items"]}
    free = {}
    for field in report["unverified"]:
        owner = field["belongs_to"] if field["belongs_to"] in items else None
        if owner is None or on.get(owner):
            free[field["path"]] = field["path"] not in unticked
    return {"created_at": pending["created_at"],
            "rejected": [path for path, kept in on.items() if not kept] + [path for path, kept in free.items() if not kept],
            "keep": [path for path, kept in on.items() if kept and path in needs_keep] + [path for path, kept in free.items() if kept]}


class ReviewPageTest(SetupCase):
    """What the page sends when the creator accepts a proposal, and that the server accepts exactly that.

    Until 19 November 2026 the page sent ``{"rejected": [...]}`` and nothing else. The server needs ``created_at``
    and refused every such click (409 ``stale_proposal``), so no proposal could be accepted in the app at all;
    the tests did not see it because their own helper added ``created_at`` and ``keep`` for the page.
    """

    FULL = dict(copy.deepcopy(DNA_CHECKED), **{
        "cadence_and_syntax": {"target_speaking_rate_cpm": 235, "ideal_clause_length_chars": {"min": 6, "median": 11, "hard_max": 20},
                               "sentence_structure_habits": ["先说结论再解释", "一句话不超过两个逗号"]},
        "beat_playbooks": {"closing_formulas": ["好啦下期见"]},
    })

    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.fake.dna = copy.deepcopy(self.FULL)
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, out)
        self.shown = out["pending"]

    def test_the_click_sends_the_three_things_the_server_needs(self):
        self.assertEqual(re.findall(r"api\('POST', '/api/voice/approve', (\{[^}]*\})\)", SETUP_JS), ["{ created_at: p.created_at, keep, rejected }"])
        self.assertEqual(SETUP_JS.count("/api/voice/approve"), 2)                # the request, and the comment that says what it needs
        click = SETUP_JS[SETUP_JS.index("on('rv-approve'"):SETUP_JS.index("on('rv-discard'")]
        # Both kinds of box are read, a locked one is left out, and "keep" is asked for what the material does not bear out.
        self.assertEqual(click.count(".filter((x) => !x.disabled)"), 2)
        self.assertIn("x.checked && x.dataset.needsKeep === '1'", click)
        self.assertIn("freeBoxes.filter((x) => x.checked).map((x) => x.dataset.freePath)", click)
        self.assertIn("freeBoxes.filter((x) => !x.checked).map((x) => x.dataset.freePath)", click)
        # A proposal that was replaced meanwhile, or is gone: said in words, and the page shows what is there now.
        self.assertIn("err.code === 'stale_proposal' || err.code === 'no_pending'", click)
        self.assertIn("await renderSetup('voice');", click)
        for lang in ("zh", "en"):
            for key in ("err_stale_proposal", "err_no_pending"):
                self.assertRegex(page_words(lang), r"\b%s: '[^']+'" % key, (lang, key))

    def test_what_the_page_sends_when_nothing_is_changed_is_accepted(self):
        body = as_the_page_sends(self.shown)
        self.assertEqual(body["created_at"], self.shown["created_at"])
        free = [field["path"] for field in self.shown["report"]["unverified"]]
        self.assertEqual(free, ["persona_summary.one_line_essence_zh", "cadence_and_syntax.sentence_structure_habits[0]",
                                "cadence_and_syntax.sentence_structure_habits[1]", "lexical_fingerprint.signature_openers[0].pattern_zh",
                                "lexical_fingerprint.signature_openers[1].pattern_zh", "beat_playbooks.closing_formulas[0]"])
        # The note about the invented opener is locked (its quote was not found), so it is in neither list.
        self.assertEqual(sorted(body["keep"]), sorted(path for path in free if path != "lexical_fingerprint.signature_openers[1].pattern_zh"))
        self.assertEqual(body["rejected"], [USES])                              # 你们看: not found, so its box starts unticked
        status, out = self.call("POST", "/api/voice/approve", body)
        self.assertEqual(status, 200, out)
        self.assertIsNone(self.pending())
        dna = self.call("GET", "/api/dna")[1]
        self.assertFalse(dna["is_example"])
        dna = dna["dna"]
        self.assertEqual(dna["persona_summary"]["one_line_essence_zh"], "松弛")
        self.assertEqual(dna["cadence_and_syntax"]["sentence_structure_habits"], ["先说结论再解释", "一句话不超过两个逗号"])
        self.assertEqual(dna["beat_playbooks"]["closing_formulas"], ["好啦下期见"])
        self.assertEqual(dna["lexical_fingerprint"]["signature_openers"], [{"pattern_zh": "今天去…", "verbatim_sample": "今天去超市买菜"}])
        self.assertEqual(dna["persona_summary"]["audience_address_terms"]["uses"], [])
        review = dna["evidence_review"]
        self.assertEqual((review["free_text_kept"], review["free_text_removed"]), (5, 0))

    def test_unticked_free_text_goes_and_a_ticked_habit_that_was_not_found_stays(self):
        gone = "cadence_and_syntax.sentence_structure_habits[0]"
        body = as_the_page_sends(self.shown, unticked=[gone, "beat_playbooks.closing_formulas[0]"], ticked=[USES])
        self.assertIn(gone, body["rejected"])
        self.assertIn(USES, body["keep"])
        status, out = self.call("POST", "/api/voice/approve", body)
        self.assertEqual(status, 200, out)
        dna = self.call("GET", "/api/dna")[1]["dna"]
        self.assertEqual(dna["cadence_and_syntax"]["sentence_structure_habits"], ["一句话不超过两个逗号"])
        self.assertEqual(dna["beat_playbooks"]["closing_formulas"], [])
        self.assertEqual(dna["persona_summary"]["audience_address_terms"]["uses"], ["你们看"])
        self.assertEqual(dna["persona_summary"]["one_line_essence_zh"], "松弛")

    def test_a_note_goes_with_the_entry_it_describes_when_that_entry_is_unticked(self):
        body = as_the_page_sends(self.shown, unticked=[OPENER])
        note = "lexical_fingerprint.signature_openers[0].pattern_zh"
        self.assertIn(OPENER, body["rejected"])
        self.assertNotIn(note, body["keep"] + body["rejected"])                # its box is locked while the opener's is not ticked
        self.assertEqual(self.call("POST", "/api/voice/approve", body)[0], 200)
        self.assertEqual(self.call("GET", "/api/dna")[1]["dna"]["lexical_fingerprint"]["signature_openers"], [])

    def test_what_the_page_sent_before_is_refused_and_changes_nothing(self):
        status, out = self.call("POST", "/api/voice/approve", {"rejected": [USES]})
        self.assertEqual((status, out["error"]), (409, "stale_proposal"), out)
        self.assertTrue(out["message"] and out["message_zh"])
        self.assertEqual(self.pending()["created_at"], self.shown["created_at"])
        self.assertTrue(self.call("GET", "/api/dna")[1]["is_example"])

    def test_a_proposal_that_replaced_the_one_on_the_screen_is_not_accepted_unseen(self):
        body = as_the_page_sends(self.shown)
        self.fake.dna["persona_summary"]["one_line_essence_zh"] = "另一份"
        time.sleep(0.01)
        self.assertEqual(self.call("POST", "/api/voice/learn", {})[0], 200)
        newer = self.pending()
        self.assertNotEqual(newer["created_at"], self.shown["created_at"])
        status, out = self.call("POST", "/api/voice/approve", body)
        self.assertEqual((status, out["error"]), (409, "stale_proposal"), out)
        self.assertEqual(self.pending()["created_at"], newer["created_at"])
        self.assertEqual(self.call("POST", "/api/voice/approve", as_the_page_sends(newer))[0], 200)
        self.assertEqual(self.call("GET", "/api/dna")[1]["dna"]["persona_summary"]["one_line_essence_zh"], "另一份")

    def test_every_piece_of_free_text_is_shown_with_a_box_under_the_name_of_its_field(self):
        review = SETUP_JS[SETUP_JS.index("function reviewHtml(p)"):SETUP_JS.index("async function paintStepVoice")]
        self.assertIn("(p.report && p.report.unverified) || []", review)
        self.assertIn('data-free-path="${esc(f.path)}"', review)
        self.assertIn("FREE_TEXT_KEYS[g.name] ? t(FREE_TEXT_KEYS[g.name]) : g.name", review)      # a field without a name of its own is still shown
        # The summary no longer picks two fields out of the profile and leaves the rest unseen.
        self.assertNotIn("persona.one_line_essence", review)
        self.assertNotIn("sentence_structure_habits", review)
        # A note about an entry says which, and is locked while that entry's box is not ticked.
        self.assertIn("!owner || ticked(owner) ? 'checked' : 'disabled'", review)
        self.assertIn("tf('rvf_about', { text: owner.text })", review)
        follow = SETUP_JS[SETUP_JS.index("async function paintStepVoice"):SETUP_JS.index("on('rv-approve'")]
        self.assertIn("note.disabled = !box.checked;", follow)
        self.assertIn("note.checked = box.checked;", follow)
        names = SETUP_JS[SETUP_JS.index("const FREE_TEXT_KEYS = {"):]
        names = dict(re.findall(r"  '([a-z_.]+)': '(rvf_[a-z_]+)',\n", names[:names.index("};")]))
        fields = free_text_fields()
        self.assertEqual(len(fields), 21)
        self.assertEqual(sorted(names), sorted(fields))                        # a field added to the shape needs a name here
        self.assertEqual(len(set(names.values())), len(names))
        for lang in ("zh", "en"):
            for key in list(names.values()) + ["rvf_about", "rvf_goes_with", "rv_summary_d"]:
                self.assertRegex(page_words(lang), r"\b%s: '(?:[^'\\]|\\.)+'" % key, (lang, key))
        # The sentence above the free text says what a tick does, in both languages.
        self.assertIn("勾着的会保留", re.search(r"rv_summary_d: '([^']+)'", page_words("zh")).group(1))
        self.assertIn("Ticked items are kept", re.search(r"rv_summary_d: '([^']+)'", page_words("en")).group(1))


class SpeakingRateTest(SetupCase):
    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)

    def proposed(self):
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, out)
        dna = out["pending"]["dna"]
        summary = dna["source_corpus_summary"]
        return (dna["cadence_and_syntax"]["target_speaking_rate_cpm"], summary["speaking_rate_source"], summary["speaking_rate_measured"])

    def accepted(self):
        dna = self.approve()[1]["dna"]
        summary = dna["source_corpus_summary"]
        return (dna["cadence_and_syntax"]["target_speaking_rate_cpm"], summary["speaking_rate_source"], summary["speaking_rate_measured"])

    def test_default_when_nothing_is_timed(self):
        self.assertEqual(self.proposed(), (240, "default", None))          # never the model's 235
        self.assertEqual(self.accepted(), (240, "default", None))
        self.assertFalse(self.call("GET", "/api/dna")[1]["dna"]["source_corpus_summary"]["speaking_rate_confirmed"])
        metrics = block(self.fake.prompt_for("sociolinguist"), "computed_acoustic_stats")
        self.assertIn("No audio was analysed", metrics["note"])            # and the model is told the truth about it
        self.assertNotIn("measured_speaking_rate", metrics)

    def test_measured_from_timed_material_and_recorded_as_such(self):
        self.add_subtitles()
        self.assertEqual(self.proposed(), (193, "measured", MEASURED_30))
        metrics = block(self.fake.prompt_for("sociolinguist"), "computed_acoustic_stats")
        self.assertEqual(metrics["measured_speaking_rate"], {"units_per_minute": 193, "seconds_of_speech": 74.5, "timed_items": 1})
        self.assertNotIn("No audio was analysed", metrics["note"])
        self.assertNotIn("hand-written", metrics["note"])                  # a subtitle file is not a hand-written script
        self.assertEqual(self.accepted(), (193, "measured", MEASURED_30))
        _, check = self.call("POST", "/api/lint", {"lines": lines("好啦"), "target_sec": 60})
        self.assertEqual((check["stats"]["speaking_rate_cpm"], check["stats"]["budget"]), (193, 193))    # the editor budgets with it

    def test_too_little_or_only_sponsored_timing_falls_back_to_the_default(self):
        self.call("POST", "/api/samples", {"subtitle_text": SHORT_VTT})    # 4.5 s
        self.add_subtitles("商单字幕", is_sponsored=True)                   # 74.5 s, but an ad
        self.assertEqual(self.proposed(), (240, "default", None))
        metrics = block(self.fake.prompt_for("sociolinguist"), "computed_acoustic_stats")
        self.assertIn("too little timed material", metrics["note"])

    def test_a_rate_she_typed_outranks_a_measured_one(self):
        self.assertEqual(self.proposed(), (240, "default", None))
        self.accepted()
        _, rated = self.call("PUT", "/api/voice/rate", {"cpm": 265})
        summary = rated["dna"]["source_corpus_summary"]
        self.assertEqual((summary["speaking_rate_source"], summary["speaking_rate_confirmed"], summary["speaking_rate_measured"]),
                         ("typed", True, None))
        self.add_subtitles()                                               # now there is a measurement too
        self.assertEqual(self.samples()["measured"], MEASURED_30)
        self.assertEqual(self.proposed(), (265, "typed", None))
        self.assertEqual(self.accepted(), (265, "typed", None))
        self.assertTrue(self.call("GET", "/api/dna")[1]["dna"]["source_corpus_summary"]["speaking_rate_confirmed"])

    def test_a_rate_typed_while_a_proposal_waits_still_wins_when_it_is_accepted(self):
        self.proposed()
        self.accepted()                                                    # her profile, default rate
        self.add_subtitles()
        self.assertEqual(self.proposed(), (193, "measured", MEASURED_30))  # a new proposal, measured
        self.call("PUT", "/api/voice/rate", {"cpm": 250})                  # she types her rate before deciding
        self.assertEqual(self.pending()["dna"]["cadence_and_syntax"]["target_speaking_rate_cpm"], 193)
        self.assertEqual(self.accepted(), (250, "typed", None))


# ---------------------------------------------------------------------------------------------- 6. refused phrases
class RefusedPhrasesTest(SetupCase):
    def test_a_refusal_ignores_latin_case_in_the_editor_and_over_mcp(self):
        self.call("PUT", "/api/creator", {"name": "小满", "never": ["yyds", "OMG"]})
        self.assertEqual(self.slop("这个面霜真的YYDS"), [("error", "YYDS", "「YYDS」是你自己列的不说的话")])
        self.assertEqual(self.slop("这个面霜真的Yyds"), [("error", "Yyds", "「Yyds」是你自己列的不说的话")])
        self.assertEqual(self.slop("omg这个也太好吃了"), [("error", "omg", "「omg」是你自己列的不说的话")])
        _, check = self.mcp("check_script", script_lines=lines("这个面霜真的YYDS"), target_duration_sec=60)
        self.assertIn(("error", "YYDS"), [(f["severity"], f["matched"]) for f in check["findings"]])
        self.store.upsert_trends([{"keyword": "YYDS好物"}, {"keyword": "秋冬护肤"}])
        usable = {t["keyword"]: t["usable"] for t in self.call("GET", "/api/trends")[1]["trends"]}
        self.assertEqual(usable, {"YYDS好物": False, "秋冬护肤": True})      # nor is it offered as a keyword

    def test_bans_in_a_profile_and_in_the_built_in_list_ignore_case_too(self):
        def errors(text, dna, **kwargs):
            script = {"script_lines": [{"line_no": 1, "spoken_script_zh": text}]}
            return [(f.category, f.matched) for f in guardrails.lint_script(script, dna, **kwargs) if f.severity == guardrails.ERROR]

        profile = {"anti_voice_blacklist": {"banned_phrases_zh": ["Chill"], "learned_from_her_edits": ["so…cute"]},
                   "persona_summary": {"audience_address_terms": {"never_uses": ["BABY们"]}},
                   "lexical_fingerprint": {"discourse_connectors": {"forbidden_bookish_connectors": ["In Conclusion"]}}}
        self.assertEqual(errors("今天真的很 CHILL", profile, lexicon={}), [(guardrails.GENERIC_XHS_SLOP, "CHILL")])
        self.assertEqual(errors("SO 真的 CUTE 啦", profile, lexicon={}), [(guardrails.GENERIC_XHS_SLOP, "SO 真的 CUTE")])
        self.assertEqual(errors("baby们看过来", profile, lexicon={}), [(guardrails.GENERIC_XHS_SLOP, "baby们")])
        self.assertEqual(errors("in conclusion 很好", profile, lexicon={}), [(guardrails.BOOKISH_MANDARIN, "in conclusion")])
        self.assertEqual(errors("今天真的很冷", profile, lexicon={}), [])
        # the built-in cliché list says "yyds"
        self.assertEqual(errors("这个面霜真的YYDS", {}), [(guardrails.GENERIC_XHS_SLOP, "YYDS")])
        # unless she really says it: what counts as hers ignores case as well
        hers = {"persona_summary": {"audience_address_terms": {"uses": ["YYDS 姐妹们"]}}}
        self.assertEqual(errors("这个面霜真的yyds", hers), [])
        self.assertEqual([guardrails.ban_key(p) for p in ("YYDS", "yyds (网络梗)", "宝子们（太腻）", "谁懂啊…", "不仅…而且", "（笑）", 5)],
                         ["yyds", "yyds", "宝子们", "谁懂啊", "不仅…而且", "", ""])

    def test_a_malformed_profile_does_not_break_the_check(self):
        odd = {"anti_voice_blacklist": {"banned_phrases_zh": "绝绝子", "learned_from_her_edits": [None, {"x": 1}, "按头安利"]},
               "persona_summary": {"audience_address_terms": {"uses": "你们看", "never_uses": [5]}},
               "lexical_fingerprint": {"signature_openers": ["上周末"], "modal_particles": {"rare_or_zero": [{"particle": "哒"}, "鸭"]}},
               "code_switching_grammar": {"good_vs_bad_examples": "none"}}
        script = {"script_lines": [{"line_no": 1, "spoken_script_zh": "这个绝了 / 真的按头安利 / 开心鸭"}]}
        found = [(f.severity, f.matched) for f in guardrails.lint_script(script, odd, lexicon={})]
        self.assertEqual(found, [("review", "鸭"), ("error", "按头安利")])  # a ban list given as one string is not read letter by letter

    def test_her_refusals_are_not_copied_into_a_learned_profile(self):
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.call("PUT", "/api/creator", {"name": "小满", "never": ["宝子们", "YYDS"]})
        self.fake.dna = {
            "persona_summary": {"audience_address_terms": {"uses": [], "never_uses": ["宝子们", "家人们"]}},
            "cadence_and_syntax": {},
            "lexical_fingerprint": {"discourse_connectors": {"forbidden_bookish_connectors": ["因此", "YYDS"]}},
            "anti_voice_blacklist": {"banned_phrases_zh": ["绝绝子", "yyds (网络梗)", "宝子们谁懂啊"], "learned_from_her_edits": ["宝子们（太腻）"]},
        }
        self.assertEqual(self.slop("宝子们看过来"), [("error", "宝子们", "「宝子们」是你自己列的不说的话")])
        _, out = self.call("POST", "/api/voice/learn", {})
        dna = out["pending"]["dna"]
        self.assertEqual(dna["persona_summary"]["audience_address_terms"]["never_uses"], ["家人们"])
        self.assertEqual(dna["anti_voice_blacklist"], {"banned_phrases_zh": ["绝绝子", "宝子们谁懂啊"], "learned_from_her_edits": []})
        self.assertEqual(dna["lexical_fingerprint"]["discourse_connectors"]["forbidden_bookish_connectors"], ["因此"])
        self.assertEqual(dna["source_corpus_summary"]["refused_phrases_removed"], 4)
        self.assertEqual([item["text"] for item in out["pending"]["report"]["items"]], ["家人们", "因此", "绝绝子", "宝子们谁懂啊"])

        self.approve()
        self.assertEqual(self.slop("宝子们看过来"), [("error", "宝子们", "「宝子们」是你自己列的不说的话")])     # still hers, still enforced
        self.call("PUT", "/api/creator", {"never": ["YYDS"]})              # she takes it off her list
        # the learned profile does not go on banning it: what is left is the built-in list asking her to look
        self.assertEqual(self.slop("宝子们看过来"), [("review", "宝子", "「宝子」你平时很少这么说，确认一下")])
        self.assertEqual([(s, m) for s, m, _ in self.slop("宝子们谁懂啊")], [("error", "宝子们谁懂啊")])   # a longer ban the model found is its own

    def test_a_refusal_typed_while_a_proposal_waits_is_not_copied_either(self):
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.fake.dna = {"cadence_and_syntax": {}, "anti_voice_blacklist": {"banned_phrases_zh": ["真的会谢", "绝绝子"]}}
        self.call("POST", "/api/voice/learn", {})
        self.assertEqual(self.pending()["dna"]["anti_voice_blacklist"]["banned_phrases_zh"], ["真的会谢", "绝绝子"])
        self.call("PUT", "/api/creator", {"name": "小满", "never": ["真的会谢"]})
        dna = self.approve()[1]["dna"]
        self.assertEqual(dna["anti_voice_blacklist"]["banned_phrases_zh"], ["绝绝子"])
        self.assertEqual(dna["source_corpus_summary"]["refused_phrases_removed"], 1)
        self.assertEqual([(s, m) for s, m, _ in self.slop("我真的会谢")], [("error", "真的会谢")])
        self.call("PUT", "/api/creator", {"never": []})
        self.assertEqual(self.slop("我真的会谢"), [])


# ---------------------------------------------------------------------------------------------- 7. trend lookup
class TrendPromptTest(SetupCase):
    PROFILE = {"name": "小满", "pronoun": "she", "channel": "小满的日常", "niches": "护肤、日常 Vlog", "about": "在上海，拍通勤和周末。",
               "partner": {"called": "我老公", "language": "英语"}, "never": ["宝子们"]}

    def test_only_the_niches_leave_with_a_trend_lookup(self):
        _, user = pipeline.trends_prompt(self.store, focus="护肤")
        self.assertEqual(block(user, "creator_profile"), {"niches": []})   # before setup: nothing to send
        self.call("PUT", "/api/creator", self.PROFILE)
        self.add("超市那天", HER_SCRIPT)
        system, user = pipeline.trends_prompt(self.store)
        self.assertEqual(block(user, "creator_profile"), {"niches": ["护肤", "日常 Vlog"]})
        status, out = self.call("POST", "/api/manual/prompt", {"task": "trends"})
        self.assertEqual(status, 200)
        self.assertNotIn("brief_id", out)
        for text in (system + user, out["prompt"]):
            for private in ("小满", "上海", "通勤", "我老公", "英语", "宝子们", '"she"', "pronoun\":"):
                self.assertNotIn(private, text, private)
            self.assertNotIn("排队排了半小时啦", text)                        # nor the words of her scripts; titles only
            self.assertIn("超市那天", text)

    def test_a_trend_lookup_holds_exactly_niches_titles_tracked_keywords_and_the_typed_focus(self):
        self.call("PUT", "/api/creator", self.PROFILE)
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.add("商单面霜", AD, is_sponsored=True)
        self.fake.dna["persona_summary"]["one_line_essence_zh"] = "小满是个松弛的上海博主"       # a model's summary, and it names her
        self.fake.dna["persona_summary"]["one_line_essence_en"] = "Xiaoman is a relaxed Shanghai vlogger"
        accepted = self.learn_and_accept()
        self.assertEqual(accepted["persona_summary"]["one_line_essence_zh"], "小满是个松弛的上海博主")   # it is in the profile in use
        self.call("POST", "/api/trends", {"text": "露营"})

        system, user = pipeline.trends_prompt(self.store, focus=" 秋冬护肤 ")
        self.assertEqual(block(user, "creator_profile"), {"niches": ["护肤", "日常 Vlog"]})
        self.assertEqual(block(user, "creator_niches"), {"typed_focus": "秋冬护肤", "creator_video_titles": ["超市那天"]})
        self.assertEqual(block(user, "already_tracked"), ["露营"])
        status, out = self.call("POST", "/api/manual/prompt", {"task": "trends", "focus": "秋冬护肤"})
        self.assertEqual(status, 200)
        for text in (system + user, out["prompt"]):
            for private in ("小满", "Xiaoman", "松弛", "上海", "relaxed", "商单面霜", '"persona"'):
                self.assertNotIn(private, text, private)

        for n in range(21):                                                # 22 of her own items now: the newest 20 titles go
            self.add("第%d期" % n, HIKE)
        titles = block(pipeline.trends_prompt(self.store)[1], "creator_niches")["creator_video_titles"]
        self.assertEqual(titles, ["第%d期" % n for n in range(20, 0, -1)])


# ---------------------------------------------------------------------------------------------- 8. what a draft was based on
class BasisTest(SetupCase):
    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT, likes=10)
        self.add("早餐日常", BREAKFAST, likes=5)
        self.add("商单面霜", AD, is_sponsored=True, likes=999)
        self.request_body = {"task": "draft", "notes": "再去一次超市", "target_sec": 30, "video_format": "VLOG_VOICEOVER"}
        self.reply = json.dumps(AI_SCRIPT, ensure_ascii=False)

    def test_a_direct_draft_records_what_its_prompt_held(self):
        script = self.draft()
        self.assertEqual(script["basis"], {"exemplar_titles": ["超市那天", "早餐日常"], "opening_titles": ["超市那天"], "lessons_used": 0, "profile": "example", "learned_at": None})
        prompt = self.fake.prompt_for("ghostwriting")
        self.assertEqual([e["title"] for e in block(prompt, EXEMPLARS_TAG)], script["basis"]["exemplar_titles"])
        self.assertEqual(block(prompt, LESSONS_TAG), [])
        self.assertEqual(block(prompt, "creator_voice_dna")["profile_version"], "1.0-example")
        self.assertEqual(self.call("GET", "/api/scripts/" + script["id"])[1]["basis"], script["basis"])

        mine = self.learn_and_accept()
        learned = self.draft()["basis"]
        self.assertEqual((learned["profile"], learned["learned_at"]), ("learned", mine["source_corpus_summary"]["learned_at"]))
        self.assertRegex(learned["learned_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(block(self.fake.prompt_for("ghostwriting"), "creator_voice_dna")["profile_version"], "1.0")

    def test_scripts_she_writes_herself_have_no_basis_and_it_cannot_be_written_in(self):
        _, typed = self.call("POST", "/api/scripts", {"title": "自己写的", "lines": lines("好啦"), "basis": {"profile": "learned"}})
        self.assertEqual((typed["basis"], typed["has_ai_draft"]), (None, False))
        script = self.draft()
        forged = {"exemplar_titles": ["编的"], "lessons_used": 99, "profile": "learned", "learned_at": "2020-01-01 00:00"}
        _, after = self.call("PUT", "/api/scripts/" + script["id"], {"basis": forged, "ai_lines_json": "[]", "title": "改名"})
        self.assertEqual((after["title"], after["basis"], after["has_ai_draft"]), ("改名", script["basis"], True))

    def test_a_pasted_draft_is_matched_to_the_prompt_she_copied(self):
        status, out = self.call("POST", "/api/manual/prompt", self.request_body)
        self.assertEqual(status, 200)
        self.assertRegex(out["brief_id"], r"^[0-9a-f]{12}$")
        self.assertEqual([e["title"] for e in block(out["prompt"], EXEMPLARS_TAG)], ["超市那天", "早餐日常"])

        # before she pastes the reply, everything the basis reports changes
        self.add("新素材", HIKE, likes=999)                                 # would now be the first example
        self.learn_and_accept()                                            # a profile of her own is now in use
        made = self.draft("别的选题")
        self.call("PUT", "/api/scripts/" + made["id"], {"lines": lines("今天去超市买菜", "这个真的蛮好吃嘛", "最后买了两盒草莓")})
        self.call("POST", "/api/lessons", {"script_id": made["id"], "ai": AI_LINES[1], "final": "这个真的蛮好吃嘛"})

        copied = {"exemplar_titles": ["超市那天", "早餐日常"], "opening_titles": ["超市那天"], "lessons_used": 0, "profile": "example", "learned_at": None}
        status, script = self.call("POST", "/api/manual/reply", dict(self.request_body, reply=self.reply))
        self.assertEqual((status, script["basis"]), (201, copied))         # what was in the prompt, not what is true now
        status, script = self.call("POST", "/api/manual/reply", dict(self.request_body, reply=self.reply, brief_id=out["brief_id"]))
        self.assertEqual((status, script["basis"]), (201, copied))         # the same when the reply quotes the record

        _, fresh = self.call("POST", "/api/manual/prompt", self.request_body)                 # copying the prompt again
        self.assertEqual([e["title"] for e in block(fresh["prompt"], EXEMPLARS_TAG)], ["新素材", "超市那天", "早餐日常"])
        self.assertEqual(len(block(fresh["prompt"], LESSONS_TAG)), 1)
        _, script = self.call("POST", "/api/manual/reply", dict(self.request_body, reply=self.reply))
        self.assertEqual((script["basis"]["exemplar_titles"], script["basis"]["lessons_used"], script["basis"]["profile"]),
                         (["新素材", "超市那天", "早餐日常"], 1, "learned"))
        _, script = self.call("POST", "/api/manual/reply", dict(self.request_body, reply=self.reply, brief_id=out["brief_id"]))
        self.assertEqual(script["basis"], copied)                          # and the older record is still found by its id

    def test_a_draft_saved_over_mcp_is_matched_to_its_brief(self):
        is_error, brief = self.mcp("get_script_brief", notes="再去一次超市", target_duration_sec=30)
        self.assertFalse(is_error)
        brief_id = re.search(r'`brief_id` set to "([0-9a-f]{12})"', brief).group(1)
        self.assertEqual([e["title"] for e in block(brief, EXEMPLARS_TAG)], ["超市那天", "早餐日常"])
        self.add("新素材", HIKE, likes=999)

        seen = {"exemplar_titles": ["超市那天", "早餐日常"], "opening_titles": ["超市那天"], "lessons_used": 0, "profile": "example", "learned_at": None}
        _, saved = self.mcp("save_script", script=AI_SCRIPT, notes="再去一次超市", target_duration_sec=30, brief_id=brief_id)
        self.assertEqual((saved["based_on"], self.store.get_script(saved["script_id"])["basis"]), (seen, seen))
        _, saved = self.mcp("save_script", script=AI_SCRIPT, notes="  再去一次超市 \n", target_duration_sec=30)   # no id, same request
        self.assertEqual(self.store.get_script(saved["script_id"])["basis"], seen)

        _, saved = self.mcp("save_script", script=AI_SCRIPT, notes="从没要过简报的选题", target_duration_sec=30)
        stored = self.store.get_script(saved["script_id"])
        self.assertEqual((stored["basis"], stored["has_ai_draft"]), (None, True))             # nothing is known, so nothing is claimed
        self.assertIn("Nothing is recorded", saved["based_on"])
        _, saved = self.mcp("save_script", script=AI_SCRIPT, notes="从没要过简报的选题", target_duration_sec=30, brief_id="0" * 12)
        self.assertIsNone(self.store.get_script(saved["script_id"])["basis"])                 # an id that matches nothing is not guessed at

    def test_remembered_lessons_are_counted_as_they_stand_in_the_prompt(self):
        made = self.draft()
        self.call("PUT", "/api/scripts/" + made["id"], {"lines": lines("今天去超市买菜", "这个真的蛮好吃嘛")})
        self.call("POST", "/api/lessons", {"script_id": made["id"], "ai": AI_LINES[1], "final": "这个真的蛮好吃嘛", "note": " 太网络了 "})
        self.call("POST", "/api/lessons", {"script_id": made["id"], "ai": AI_LINES[2], "final": ""})
        rules = [{"ai_wrote": "最后买了两盒草莓", "deleted": True},            # newest first
                 {"ai_wrote": "这个真的绝绝子嘛", "changed_to": "这个真的蛮好吃嘛", "note": "太网络了"}]

        self.assertEqual(self.draft("第二篇")["basis"]["lessons_used"], 2)
        self.assertEqual(block(self.fake.prompt_for("ghostwriting"), LESSONS_TAG), rules)
        _, pasted = self.call("POST", "/api/manual/prompt", self.request_body)
        self.assertEqual(block(pasted["prompt"], LESSONS_TAG), rules)
        _, brief = self.mcp("get_script_brief", notes="第三篇")
        self.assertEqual(block(brief, LESSONS_TAG), rules)
        _, saved = self.mcp("save_script", script=AI_SCRIPT, notes="第三篇")
        self.assertEqual(saved["based_on"]["lessons_used"], 2)


# ---------------------------------------------------------------------------------------------- 9. lessons from edits
class LessonsTest(SetupCase):
    EDITED = ("今天去超市买菜", "这个真的蛮好吃嘛")                          # line 2 reworded, line 3 deleted
    REWORDED = {"kind": "rewritten", "ai": "这个真的绝绝子嘛", "final": "这个真的蛮好吃嘛", "removed": ["绝绝子"], "added": ["蛮好吃"],
                "ai_line_no": 2, "final_line_no": 2, "lesson_id": None, "by_ai": False}
    DELETED = {"kind": "deleted", "ai": "最后买了两盒草莓", "final": "", "removed": ["最后买了两盒草莓"], "added": [],
               "ai_line_no": 3, "final_line_no": None, "lesson_id": None, "by_ai": False}

    def setUp(self):
        super().setUp()
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)

    def changes(self, script):
        status, out = self.call("GET", "/api/scripts/%s/changes" % script["id"])
        self.assertEqual(status, 200)
        return out

    def edit(self, script, *spoken):
        return self.call("PUT", "/api/scripts/" + script["id"], {"lines": lines(*spoken)})[1]

    def test_her_edits_to_a_draft_are_listed_against_what_the_ai_wrote(self):
        script = self.draft()
        self.assertEqual([l["spoken_script_zh"] for l in script["lines"]], AI_LINES)
        self.assertEqual(self.changes(script), {"has_ai_draft": True, "changes": []})         # untouched so far
        self.edit(script, "今天去超市买菜，", "这个真的绝绝子嘛", "最后买了两盒 草莓")
        self.assertEqual(self.changes(script)["changes"], [])              # punctuation and spacing are not wording
        self.edit(script, *self.EDITED)
        self.assertEqual(self.changes(script), {"has_ai_draft": True, "changes": [self.REWORDED, self.DELETED]})
        self.assertEqual(self.store.get_ai_lines(script["id"])[1]["spoken_script_zh"], "这个真的绝绝子嘛")   # the AI's lines are kept

        _, typed = self.call("POST", "/api/scripts", {"lines": lines("自己写的一句")})
        self.edit(typed, "自己改的一句")
        self.assertEqual(self.changes(typed), {"has_ai_draft": False, "changes": []})         # nothing to compare a hand-written script with
        self.assertEqual(self.call("GET", "/api/scripts/%s/changes" % ("0" * 12))[0], 404)

    def test_remember_and_forget_a_lesson(self):
        script = self.draft()
        self.edit(script, *self.EDITED)
        self.assertEqual(self.call("GET", "/api/lessons")[1], {"lessons": [], "suggestions": []})

        body = {"script_id": script["id"], "ai": self.REWORDED["ai"], "final": self.REWORDED["final"], "note": "太网络了"}
        status, lesson = self.call("POST", "/api/lessons", body)
        self.assertEqual(status, 201)
        self.assertEqual({k: lesson[k] for k in ("script_id", "ai", "final", "note")}, body)
        self.assertRegex(lesson["id"], r"^[0-9a-f]{12}$")
        status, again = self.call("POST", "/api/lessons", dict(body, note="第二次"))
        self.assertEqual((status, again), (200, lesson))                   # the same change again: the one already remembered
        status, second = self.call("POST", "/api/lessons", {"script_id": script["id"], "ai": self.DELETED["ai"], "final": ""})
        self.assertEqual((status, second["note"]), (201, ""))

        self.assertEqual([l["id"] for l in self.call("GET", "/api/lessons")[1]["lessons"]], [second["id"], lesson["id"]])   # newest first
        self.assertEqual([c["lesson_id"] for c in self.changes(script)["changes"]], [lesson["id"], second["id"]])
        self.assertEqual(self.setup()["lessons"], {"accepted": 2})

        self.assertEqual(self.call("DELETE", "/api/lessons/" + lesson["id"]), (200, {"ok": True}))
        self.assertEqual(self.call("DELETE", "/api/lessons/" + lesson["id"])[0], 404)
        self.assertEqual([c["lesson_id"] for c in self.changes(script)["changes"]], [None, second["id"]])
        self.assertEqual([l["id"] for l in self.call("GET", "/api/lessons")[1]["lessons"]], [second["id"]])
        self.call("DELETE", "/api/scripts/" + script["id"])
        self.assertEqual(len(self.call("GET", "/api/lessons")[1]["lessons"]), 1)              # a lesson outlives its script

    def test_what_cannot_be_remembered(self):
        script = self.draft()
        _, typed = self.call("POST", "/api/scripts", {"lines": lines("自己写的一句")})
        good = {"script_id": script["id"], "ai": "这个真的绝绝子嘛", "final": "这个真的蛮好吃嘛"}
        for change, refused in (
            ({"script_id": "0" * 12}, (404, "not_found")), ({"script_id": None}, (404, "not_found")), ({"script_id": 5}, (404, "not_found")),
            ({"script_id": typed["id"]}, (400, "no_ai_draft")),                               # there is no "what the AI wrote"
            ({"ai": 5}, (400, "bad_lesson")), ({"final": ["x"]}, (400, "bad_lesson")), ({"note": {"a": 1}}, (400, "bad_lesson")),
            ({"ai": "长" * 2001}, (400, "bad_lesson")),
            ({"ai": "好啦，下期见", "final": "好啦 下期见"}, (400, "no_change")),                  # only punctuation differs
            ({"ai": "", "final": ""}, (400, "no_change")), ({"ai": None, "final": None}, (400, "no_change")),
        ):
            status, out = self.call("POST", "/api/lessons", dict(good, **change))
            self.assertEqual((status, out["error"]), refused, change)
        self.assertEqual(self.call("GET", "/api/lessons")[1]["lessons"], [])
        self.assertTrue(self.mcp("remember_lesson", **good)[0] if "remember_lesson" in mcp_server.TOOLS else True)
        self.assertEqual([name for name in mcp_server.TOOLS if "lesson" in name], [])         # an AI application cannot add one

    def test_phrases_she_keeps_removing_are_suggested_until_she_refuses_them(self):
        first, second = self.draft("第一篇"), self.draft("第二篇")
        self.edit(first, *self.EDITED)
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"], [])              # once is not a habit
        self.edit(second, "今天去超市买菜", "这个真的不错嘛", "最后买了两盒草莓")
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"],
                         [{"phrase": "绝绝子", "scripts": 2, "script_ids": [first["id"], second["id"]]}])
        _, typed = self.call("POST", "/api/scripts", {"lines": lines("这个真的绝绝子嘛")})
        self.edit(typed, "这个真的还行嘛")                                    # her own rewording of her own script counts for nothing
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"][0]["scripts"], 2)

        self.call("PUT", "/api/creator", {"name": "小满", "never": ["绝绝子"]})
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"], [])              # already on her list
        self.call("PUT", "/api/creator", {"never": []})
        self.assertEqual([s["phrase"] for s in self.call("GET", "/api/lessons")[1]["suggestions"]], ["绝绝子"])

    def test_a_suggestion_already_refused_in_other_letter_case_is_left_out(self):
        self.fake.script = {"script_lines": lines("这家店真的YYDS嘛")}
        first, second = self.draft("第一篇"), self.draft("第二篇")
        self.edit(first, "这家店真的不错嘛")
        self.edit(second, "这家店真的还行嘛")
        self.assertEqual([(s["phrase"], s["scripts"]) for s in self.call("GET", "/api/lessons")[1]["suggestions"]], [("YYDS", 2)])
        self.call("PUT", "/api/creator", {"name": "小满", "never": ["yyds"]})
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"], [])

    def test_an_ai_revising_its_own_draft_is_not_mistaken_for_her(self):
        _, saved = self.mcp("save_script", script={"script_lines": lines("这个真的绝绝子 / 上周末去超市补货嘛")}, notes="补货", target_duration_sec=5)
        script = {"id": saved["script_id"]}
        found = lambda out: [(f["category"], f["matched"]) for f in out["check"]["findings"]]   # noqa: E731
        self.assertEqual(found(saved), [("GENERIC_XHS_SLOP", "绝绝子"), ("DURATION_OFF_BUDGET", "16 units")])     # 7 + 9 units
        _, fixed = self.mcp("update_script", script_id=script["id"], script_lines=lines("上周末去超市补货嘛"))   # the AI fixes its cliché
        self.assertEqual(found(fixed), [("DURATION_OFF_BUDGET", "9 units")])                  # the cliché is gone; the length note is not about wording
        self.assertEqual(self.changes(script), {"has_ai_draft": True, "changes": []})         # that was not her edit
        self.assertEqual(self.call("GET", "/api/lessons")[1]["suggestions"], [])
        self.edit(script, "上周末去超市补货啦")                               # now she changes a particle
        self.assertEqual([(c["kind"], c["ai"], c["final"]) for c in self.changes(script)["changes"]],
                         [("rewritten", "上周末去超市补货嘛", "上周末去超市补货啦")])

        typed = self.store.create_script(title="自己写的", lines=lines("自己写的一句"))
        self.mcp("update_script", script_id=typed["id"], script_lines=lines("AI 改写的一句"))
        self.assertFalse(self.store.get_script(typed["id"])["has_ai_draft"])                  # and her own script does not become an AI draft


# ---------------------------------------------------------------------------------------------- 10. setup status
class SetupStatusTest(SetupCase):
    def test_a_fresh_installation(self):
        self.assertEqual(self.setup(), {
            "creator": {"done": False, "name": "", "niches": []},
            "material": {"done": False, "total": 0, "organic": 0, "unreviewed": 0, "clauses": 0, "enough_for_cadence": False,
                         "timed_sec": 0.0, "measured_rate": None},
            "ai": {"configured": False, "manual": False},
            "voice": {"state": "example", "learned_at": None, "based_on": 0, "new_since": 0, "has_pending": False},
            "first_script": {"done": False, "count": 0},
            "lessons": {"accepted": 0},
            "next": "creator",
        })
        self.assertEqual(self.call("GET", "/api/state")[1]["setup_next"], "creator")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["studio.db"])                 # asking stores nothing

    def test_each_step_is_done_only_when_it_really_is(self):
        self.add("早餐日常", BREAKFAST)
        self.assertEqual(self.setup()["next"], "creator")                  # material alone does not skip the first step
        self.call("PUT", "/api/creator", {"name": "小满", "niches": "护肤、日常 Vlog"})
        status = self.setup()
        self.assertEqual((status["creator"], status["next"]), ({"done": True, "name": "小满", "niches": ["护肤", "日常 Vlog"]}, "material"))

        self.add("周末爬山", HIKE)
        self.add("商单面霜", AD, is_sponsored=True)
        self.assertEqual(self.setup()["material"], {"done": False, "total": 3, "organic": 2, "unreviewed": 0, "clauses": 3,
                                                    "enough_for_cadence": False, "timed_sec": 0.0, "measured_rate": None})
        self.call("POST", "/api/samples", {"subtitle_text": SHORT_VTT, "is_sponsored": True})
        self.assertEqual((self.setup()["material"]["timed_sec"], self.setup()["next"]), (0.0, "material"))   # an ad's timing is not hers
        self.add("超市那天", HER_SCRIPT)
        self.assertEqual(self.setup()["material"], {"done": True, "total": 5, "organic": 3, "unreviewed": 0, "clauses": 39,
                                                    "enough_for_cadence": True, "timed_sec": 0.0, "measured_rate": None})
        self.assertEqual(self.setup()["next"], "voice")
        self.call("POST", "/api/samples", {"title": "短字幕", "subtitle_text": SHORT_VTT})
        self.assertEqual((self.setup()["material"]["timed_sec"], self.setup()["material"]["measured_rate"]), (4.5, None))
        self.add_subtitles()
        self.assertEqual(self.setup()["material"], {"done": True, "total": 7, "organic": 5, "unreviewed": 0, "clauses": 71,
                                                    "enough_for_cadence": True, "timed_sec": 79.0, "measured_rate": 194})

        self.assertEqual(self.setup()["ai"], {"configured": False, "manual": False})
        self.call("POST", "/api/settings", {"provider": "manual"})
        self.assertEqual(self.setup()["ai"], {"configured": True, "manual": True})
        self.connect_model()
        self.assertEqual(self.setup()["ai"], {"configured": True, "manual": False})

        self.call("POST", "/api/voice/learn", {})
        self.assertEqual(self.setup()["voice"], {"state": "pending", "learned_at": None, "based_on": 5, "new_since": 0, "has_pending": True})
        self.assertEqual(self.setup()["next"], "voice")                    # a proposal is not a voice of her own yet
        dna = self.approve()[1]["dna"]
        self.assertEqual(self.setup()["voice"], {"state": "active", "learned_at": dna["source_corpus_summary"]["learned_at"],
                                                 "based_on": 5, "new_since": 0, "has_pending": False})
        self.assertEqual(self.setup()["next"], "script")

        self.call("POST", "/api/scripts", {"lines": lines("自己写的一句")})
        self.assertEqual((self.setup()["first_script"], self.setup()["next"]), ({"done": False, "count": 0}, "script"))   # typed by hand: no draft yet
        script = self.draft()
        self.assertEqual((self.setup()["first_script"], self.setup()["next"]), ({"done": True, "count": 1}, "done"))
        self.assertEqual(self.call("GET", "/api/state")[1]["setup_next"], "done")
        self.call("PUT", "/api/scripts/" + script["id"], {"lines": lines("今天去超市买菜", "这个真的蛮好吃嘛", "最后买了两盒草莓")})
        self.call("POST", "/api/lessons", {"script_id": script["id"], "ai": AI_LINES[1], "final": "这个真的蛮好吃嘛"})
        self.assertEqual(self.setup()["lessons"], {"accepted": 1})

    def test_material_added_after_the_profile_was_learned_is_counted(self):
        self.connect_model()
        self.add("超市那天", HER_SCRIPT)
        self.add("早餐日常", BREAKFAST)
        self.learn_and_accept()
        self.assertEqual((self.setup()["voice"]["based_on"], self.setup()["voice"]["new_since"]), (2, 0))
        self.add("周末爬山", HIKE)
        self.add("商单面霜", AD, is_sponsored=True)                          # an ad is not material the voice is learned from
        self.assertEqual(self.setup()["voice"]["new_since"], 1)

        self.call("POST", "/api/voice/learn", {})                          # a newer proposal waits; hers is still the one in use
        voice = self.setup()["voice"]
        self.assertEqual((voice["state"], voice["has_pending"], voice["based_on"], voice["new_since"]), ("active", True, 2, 1))
        self.approve()
        voice = self.setup()["voice"]
        self.assertEqual((voice["state"], voice["has_pending"], voice["based_on"], voice["new_since"]), ("active", False, 3, 0))

    def test_material_that_arrived_between_copying_the_prompt_and_accepting_is_counted_too(self):
        self.add("超市那天", HER_SCRIPT)
        self.add("早餐日常", BREAKFAST)
        self.call("POST", "/api/manual/prompt", {"task": "voice"})         # the prompt holds two scripts
        self.add("周末爬山", HIKE)                                          # this one the model never read
        self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(DNA_CHECKED, ensure_ascii=False)})
        self.approve()
        voice = self.setup()["voice"]
        self.assertEqual((voice["state"], voice["based_on"], voice["new_since"]), ("active", 2, 1))

    def test_a_profile_learned_before_proposals_existed_still_reports(self):
        old = copy.deepcopy(DNA_CHECKED)
        old["source_corpus_summary"] = {"organic_scripts_analyzed": 4, "sponsored_scripts_analyzed": 1, "clauses_measured": 80,
                                        "clause_lengths_measured": True, "speaking_rate_confirmed": True, "learned_at": "2026-01-02 03:04"}
        self.store.save_dna(old)
        self.add("超市那天", HER_SCRIPT)
        voice = self.setup()["voice"]
        self.assertEqual(voice, {"state": "active", "learned_at": "2026-01-02 03:04", "based_on": 4, "new_since": 1, "has_pending": False})
        self.assertEqual(setup_status._learned_time({"learned_at": "not a date"}), None)
        self.assertEqual(setup_status._learned_time({"learned_at": "2026-01-02 03:04"}),
                         time.mktime(time.strptime("2026-01-02 03:05", "%Y-%m-%d %H:%M")))    # the end of that minute


if __name__ == "__main__":
    unittest.main()
