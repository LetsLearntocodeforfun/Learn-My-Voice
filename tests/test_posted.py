"""Tests for "mark as posted": a script that was posted joins the library, with its numbers.

Each class covers one thing the creator can rely on:

- marking makes ONE library item from the script as it stands, linked both ways, and never a second;
- the numbers and the day they were read change together, from the script page and from the library item,
  and nothing is adjusted;
- THE HONESTY RULE: what was posted counts for performance at once, and not as the creator's own wording
  until they confirm it. The measured habits, the voice-match reference, the lessons, the examples and the
  openings that reach a prompt are the same before and after a script is marked as posted;
- an opening that is still an AI's sentence is never among the creator's own openings;
- the words of the posted video (a subtitle file, a transcription) take the text's place in the same item;
- a data folder made by an earlier version opens and works unchanged;
- every request is a JSON write, and a refusal has a code and a sentence in English and in Chinese;
- markup in a title or a link stays text, and the page script says everything in both languages.

No model and no real creator material is involved: the "AI draft" here is a script stored as one.
"""
import datetime
import http.client
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import checks, corpus, hooks, learning, mcp_server, pipeline, posted, server, suggest, trends, voicematch
from studio import setup as setup_status
from studio.store import ORIGIN_SCRIPT, StaleScript, Store, TextTooLong

WEB = Path(server.WEB_DIR)
TODAY = datetime.date(2026, 11, 16)

# Her own hand-written material: short breaths, particles at the end, spoken connectors.
OWN = [
    "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈 / 然后我们拿了四盒小笼包啦 / 其实家里还有嘛",
    "入冬以后脸特别干嘛 / 所以我换了一款面霜 / 结果用得最勤哈 / 其实 toner 也换了",
    "周日早上煮咖啡啦 / 然后做了个三明治 / 我老公说好吃嘛 / 结果他吃了两个哈",
    "今天去 Trader Joe's 啦 / 然后又买了小笼包 / 其实家里还有嘛 / 但是忍不住哈",
    "最近风特别大嘛 / 所以出门都戴帽子 / 结果帽子被吹跑了哈 / 然后我老公去追啦",
]
# What an AI drafted: written-style connectors, long breaths, no particles. Nothing like the material above.
AI_DRAFT = [
    "综上所述这款面霜的质地非常轻盈 / 与此同时它的吸收速度令人印象深刻",
    "因此我强烈推荐给大家",
    "总而言之这是一个值得入手的选择",
    "然而价格略高需要理性消费",
]
AI_OPENING = "综上所述这款面霜的质地非常轻盈"
HER_FIRST_LINE = "入冬以后我换了一款面霜嘛 / 结果脸真的不干了哈"
HER_OPENING = "入冬以后我换了一款面霜嘛"
HER_SECOND_LINE = "所以我真的很推荐嘛"
PROBE = [{"spoken_script_zh": "这周又去超市补货啦 / 然后拿了两盒水饺"}, {"spoken_script_zh": "结果我老公又走不动哈 / 其实家里还有嘛"}]
# The likes of her ten videos. The two in the middle are equal on purpose: her usual result (the middle value, 150)
# then stays 150 when one more video with numbers at or above it is added, so a test can tell "the wording was
# learned from" apart from "her usual result moved", which marking as posted is meant to do.
LIKES = [100, 110, 120, 130, 150, 150, 160, 170, 180, 190]

# The tables exactly as the code at 658ac1d creates them (studio/store.py, _SCHEMA), before this feature.
SCHEMA_AT_658AC1D = """
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
CREATE TABLE IF NOT EXISTS ai_suggestions (
    script_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_suggestions_by_script ON ai_suggestions (script_id);
"""
NEW_SAMPLE_COLUMNS = {"origin", "script_id", "posted_on", "note_url", "metrics_on", "ai_json", "snapshot"}


def lines(*texts):
    return [{"spoken_script_zh": text} for text in texts]


class PostedCase(unittest.TestCase):
    """A store on a temporary data folder, her own material in it, and a fixed "today"."""

    own_material = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp)
        clock = mock.patch.object(posted, "today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        if self.own_material:
            for n, text in enumerate(OWN * 2):                  # 40 breaths
                self.store.create_sample(title="我的第%d篇" % (n + 1), text=text, metrics={"likes": LIKES[n]})

    def draft(self, texts=AI_DRAFT, title="面霜分享", from_ai=True):
        """A script as an AI draft arrives (or, with ``from_ai`` false, as the creator types one by hand)."""
        return self.store.create_script(title=title, lines=lines(*texts), from_ai=from_ai)

    def edit(self, script, changed):
        """The creator rewrites lines of a script: ``{index: new text}``. Returns the stored script."""
        now = [dict(line) for line in self.store.get_script(script["id"])["lines"]]
        for index, text in changed.items():
            now[index]["spoken_script_zh"] = text
        return self.store.update_script(script["id"], {"lines": now})

    def mark(self, script, **body):
        answer, created = posted.mark(self.store, script["id"], body)
        return answer["posted"], created

    def item(self, script):
        return self.store.get_posted_sample(script["id"])

    def confirm(self, script):
        return self.store.update_sample(self.item(script)["id"], {"needs_review": False})

    def refusal(self, call):
        with self.assertRaises(posted.PostedError) as refused:
            call()
        return refused.exception.code


# ---------------------------------------------------------------------------------------------- 1 and 2. one item, linked both ways
class MarkTest(PostedCase):
    def test_marking_makes_one_library_item_from_the_script_as_it_stands(self):
        script = self.store.create_script(title="面霜分享", from_ai=True, lines=[
            {"spoken_script_zh": "综上所述这款面霜很轻盈\n与此同时吸收很快"},                 # a line break inside a line is a breath
            {"spoken_script_zh": "店员说这个卖得最好", "audio_mode": "LIVE_CLIP"},          # somebody else's recorded words
            {"spoken_script_zh": ""},
            {"spoken_script_zh": "[笑] 因此我强烈推荐给大家"}])
        before = len(self.store.list_samples())
        recorded, created = self.mark(script, posted_on="2026-11-10", note_url=" https://www.xiaohongshu.com/explore/abc ",
                                      is_sponsored=True, views=9000, likes="500", collects="", comments=None)
        self.assertTrue(created)
        item = self.item(script)
        self.assertEqual(len(self.store.list_samples()), before + 1)
        self.assertEqual((item["title"], item["text"], item["kind"], item["is_sponsored"]),
                         ("面霜分享", "综上所述这款面霜很轻盈 / 与此同时吸收很快\n[笑] 因此我强烈推荐给大家", "script", True))
        self.assertEqual([item[field] for field in ("views", "likes", "collects", "comments")], [9000, 500, None, None])
        # Stored with where it came from, and linked both ways.
        self.assertEqual((item["origin"], item["script_id"]), (ORIGIN_SCRIPT, script["id"]))
        self.assertEqual((recorded["sample_id"], recorded["title"]), (item["id"], "面霜分享"))
        self.assertEqual((recorded["posted_on"], recorded["note_url"], recorded["metrics_on"], recorded["read_after_days"]),
                         ("2026-11-10", "https://www.xiaohongshu.com/explore/abc", "2026-11-16", 6))
        self.assertFalse(recorded["text_differs"])
        shown = posted.for_page(self.store, self.store.list_samples())[0]
        self.assertEqual((shown["posted"]["script_id"], shown["posted"]["script_title"]), (script["id"], "面霜分享"))
        self.assertNotIn("ai_draft", shown)                         # what the AI wrote is kept for counting, not sent to the page
        # The scripts list says which scripts are posted.
        other = self.draft(title="没发的")
        listed = {s["id"]: (s["posted"], s["posted_on"]) for s in posted.with_posted_dates(self.store, self.store.list_scripts())}
        self.assertEqual(listed, {script["id"]: (True, "2026-11-10"), other["id"]: (False, None)})
        self.assertIsNone(posted.with_posted(self.store, other)["posted"])

    def test_marking_again_changes_that_item_and_never_makes_a_second(self):
        script = self.draft()
        first, created = self.mark(script, likes=10)
        again, created_again = self.mark(script, likes=25, posted_on="2026-11-15", note_url="https://example.com/n")
        self.assertEqual((created, created_again), (True, False))
        self.assertEqual(again["sample_id"], first["sample_id"])
        self.assertEqual(sum(1 for s in self.store.list_samples() if s.get("script_id") == script["id"]), 1)
        self.assertEqual((again["likes"], again["posted_on"], again["note_url"]), (25, "2026-11-15", "https://example.com/n"))
        # A request that names nothing changes nothing.
        self.assertEqual(self.mark(script)[0], again)
        # The database itself holds the rule, whatever the code above it does.
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_sample(title="第二条", text="今天去超市", posted={"script_id": script["id"]})

    def test_two_windows_marking_at_once_make_one_item(self):
        script = self.draft()
        gate, outcomes = threading.Barrier(6), []

        def press():
            gate.wait(5)
            outcomes.append(posted.mark(self.store, script["id"], {"likes": 5})[1])

        threads = [threading.Thread(target=press) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(outcomes), [False] * 5 + [True])
        self.assertEqual(sum(1 for s in self.store.list_samples() if s.get("script_id") == script["id"]), 1)

    def test_the_day_is_today_by_default_and_never_in_the_future(self):
        script = self.draft()
        for bad, code in (("2026-11-17", "date_in_future"), ("2099-01-01", "date_in_future"), ("2026-02-30", "bad_date"),
                          ("16/11/2026", "bad_date"), ("20261116", "bad_date"), ("2026-11-16T10:00", "bad_date"),
                          (20261116, "bad_date"), (["2026-11-16"], "bad_date"), ("0000-01-01", "bad_date")):
            self.assertEqual(self.refusal(lambda: self.mark(script, posted_on=bad)), code, bad)
            self.assertEqual(self.refusal(lambda: self.mark(script, likes=5, metrics_on=bad)), code, bad)
        self.assertIsNone(self.item(script))                         # a refusal stores nothing
        recorded, _ = self.mark(script)
        self.assertEqual((recorded["posted_on"], recorded["metrics_on"], recorded["has_numbers"]), ("2026-11-16", None, False))
        self.assertEqual(self.mark(script, posted_on="2026-11-16")[0]["posted_on"], "2026-11-16")      # today itself is allowed
        self.assertEqual(self.refusal(lambda: self.mark(script, posted_on="2026-11-17")), "date_in_future")
        self.assertEqual(self.refusal(lambda: self.mark(script, posted_on="")), "bad_date")            # a day once given is not taken away

    def test_the_link_is_kept_as_text_and_only_http(self):
        script = self.draft()
        for bad in ("javascript:alert(1)", "ftp://example.com/a", "example.com/a", "//example.com/a", "https://", "http:///x",
                    "看看我的笔记 https://example.com/a", "https://example.com/a b", "https://example.com/a\nhttps://b.example",
                    "data:text/html,x", "file:///etc/passwd", 5, ["https://example.com"], "https://example.com/" + "a" * 2000):
            self.assertEqual(self.refusal(lambda: self.mark(script, note_url=bad)), "bad_link", bad)
        self.assertIsNone(self.item(script))
        markup = 'https://example.com/"><script>alert(1)</script>'
        for good in ("https://www.xiaohongshu.com/explore/abc?x=1", "http://xhslink.com/a/B2c", "HTTPS://Example.com/笔记", markup):
            self.assertEqual(self.mark(script, note_url="  %s  " % good)[0]["note_url"], good)         # kept exactly, as text
        self.assertEqual(self.mark(script, note_url="")[0]["note_url"], "")                             # and it can be taken away
        # Nothing in this module can open a link: it has no way to make a request.
        source = Path(posted.__file__).read_text(encoding="utf-8")
        for word in ("urllib.request", "urlopen", "http.client", "socket", "webbrowser", "subprocess", "llm."):
            self.assertNotIn(word, source, word)

    def test_numbers_are_read_the_way_the_library_reads_them(self):
        typed = {"views": "12,000", "likes": " 300 ", "collects": "-5", "comments": "1e3", "extra": 7}
        by_hand = self.store.create_sample(title="手动加的", text="今天去超市", metrics=typed)
        script = self.draft()
        self.mark(script, **typed)
        for field in ("views", "likes", "collects", "comments"):
            self.assertEqual(self.item(script)[field], by_hand[field], field)
        for unusable in (True, "abc", [], {}, float("inf"), 2 ** 70):
            self.mark(script, likes=unusable)
            self.assertIsNone(self.item(script)["likes"], unusable)

    def test_a_script_with_nothing_spoken_is_not_marked(self):
        for texts in (("",), ("[音乐]", "  "), ()):
            script = self.draft(texts)
            self.assertEqual(self.refusal(lambda: self.mark(script)), "nothing_spoken", texts)
        only_clip = self.store.create_script(title="只有原声", lines=[{"spoken_script_zh": "店员说的话", "audio_mode": "LIVE_CLIP"}])
        self.assertEqual(self.refusal(lambda: self.mark(only_clip)), "nothing_spoken")
        self.assertEqual(self.refusal(lambda: posted.mark(self.store, "0" * 12, {})), "not_found")
        self.assertEqual(self.refusal(lambda: posted.mark(self.store, None, {})), "not_found")
        self.assertEqual([s for s in self.store.list_samples() if s.get("origin")], [])

    def test_removing_the_library_item_unmarks_the_script(self):
        script = self.draft()
        self.mark(script, likes=5)
        self.assertTrue(self.store.delete_sample(self.item(script)["id"]))
        self.assertIsNone(posted.with_posted(self.store, self.store.get_script(script["id"]))["posted"])
        self.assertEqual(posted.with_posted_dates(self.store, self.store.list_scripts())[0]["posted"], False)
        self.assertEqual(self.store.get_script(script["id"])["lines"], script["lines"])       # the script itself is untouched
        # "Update numbers" from a page that still shows the old record does not mark it again behind her back.
        self.assertEqual(self.refusal(lambda: posted.mark(self.store, script["id"], {"likes": 9}, update_only=True)), "not_posted")
        self.assertIsNone(self.item(script))
        self.assertTrue(self.mark(script, likes=9)[1])                                         # marking it again is her own act

    def test_deleting_the_script_keeps_the_item_and_drops_the_link(self):
        script = self.draft()
        self.mark(script, likes=500, posted_on="2026-11-10")
        item = self.item(script)
        self.assertTrue(self.store.delete_script(script["id"]))
        kept = self.store.get_sample(item["id"])
        self.assertEqual((kept["title"], kept["text"], kept["likes"], kept["posted_on"]), (item["title"], item["text"], 500, "2026-11-10"))
        self.assertEqual((kept["origin"], kept["script_id"], kept["needs_review"]), (ORIGIN_SCRIPT, None, True))
        self.assertEqual(kept["ai_draft"], item["ai_draft"])            # what the AI wrote stays with the item
        shown = posted.shown(self.store, kept)["posted"]
        self.assertEqual((shown["script_id"], shown["script_title"], shown["lines_by_ai"]), (None, None, 4))
        # It is still her posted video (performance), and still not her wording.
        self.assertEqual(corpus.performance(self.store.list_samples())["rows"][0]["title"], "面霜分享")
        self.assertNotIn(kept["id"], [s["id"] for s in pipeline.confirmed_samples(self.store.list_samples())])
        self.assertEqual(posted.with_posted_dates(self.store, self.store.list_scripts()), [])

    def test_a_link_to_a_script_that_is_gone_is_shown_as_no_link(self):
        """An earlier version of the app deletes a script without knowing about the link."""
        script = self.draft()
        self.mark(script)
        with sqlite3.connect(str(self.store.db_path)) as db:
            db.execute("DELETE FROM scripts WHERE id = ?", (script["id"],))
        shown = posted.for_page(self.store, self.store.list_samples())[0]["posted"]
        self.assertEqual((shown["script_id"], shown["script_title"]), (None, None))

    def test_a_script_edited_after_posting_does_not_change_the_item_until_asked(self):
        script = self.draft()
        self.mark(script, likes=5)
        as_posted = self.item(script)["text"]
        self.confirm(script)
        script = self.edit(script, {0: HER_FIRST_LINE})
        self.store.update_script(script["id"], {"title": "面霜分享（改）"})
        item = self.item(script)
        self.assertEqual((item["text"], item["title"]), (as_posted, "面霜分享"))            # the item holds the text as posted
        recorded = posted.with_posted(self.store, self.store.get_script(script["id"]))["posted"]
        self.assertTrue(recorded["text_differs"])                                           # and the page is told so
        self.assertEqual(self.mark(script, likes=6)[0]["text_differs"], True)               # numbers alone do not take the text again
        self.assertEqual(self.item(script)["text"], as_posted)
        # Edits that change no spoken word and no title are no difference.
        other = self.draft(title="另一篇")
        self.mark(other)
        self.store.update_script(other["id"], {"keywords": ["面霜"], "target_sec": 90})
        self.assertFalse(posted.with_posted(self.store, self.store.get_script(other["id"]))["posted"]["text_differs"])
        # Asked for: the item takes the script as it stands now, and its wording waits again.
        answer, created = posted.mark(self.store, script["id"], {"refresh_text": True}, update_only=True)
        item = self.item(script)
        self.assertEqual((created, item["title"], item["text"].split("\n")[0]), (False, "面霜分享（改）", HER_FIRST_LINE))
        self.assertEqual((item["needs_review"], item["reviewed_at"], item["likes"]), (True, None, 6))
        self.assertEqual((answer["posted"]["text_differs"], answer["posted"]["lines_by_ai"], answer["posted"]["wording"]), (False, 3, "waiting"))
        # Only a real true asks for it.
        self.edit(script, {1: HER_SECOND_LINE})
        for not_true in ("true", 1, "yes", None, False):
            posted.mark(self.store, script["id"], {"refresh_text": not_true})
        self.assertNotIn(HER_SECOND_LINE, self.item(script)["text"])

    def test_marking_from_an_older_version_of_the_script_is_refused(self):
        script = self.draft()
        seen = script["updated_at"]
        newer = self.edit(script, {0: HER_FIRST_LINE})                  # saved somewhere else in the meantime
        with self.assertRaises(StaleScript) as refused:
            posted.mark(self.store, script["id"], {"likes": 5, "base_updated_at": seen})
        self.assertEqual(refused.exception.updated_at, newer["updated_at"])
        self.assertIsNone(self.item(script))
        for unusable in ("yesterday", True, [], {}, float("nan"), float("inf"), 10 ** 400):
            self.assertEqual(self.refusal(lambda: posted.mark(self.store, script["id"], {"base_updated_at": unusable})),
                             "bad_base_updated_at", unusable)
        self.assertTrue(posted.mark(self.store, script["id"], {"base_updated_at": newer["updated_at"]})[1])
        # The same holds for taking the text again.
        latest = self.edit(script, {1: HER_SECOND_LINE})
        with self.assertRaises(StaleScript):
            posted.mark(self.store, script["id"], {"refresh_text": True, "base_updated_at": newer["updated_at"]}, update_only=True)
        self.assertNotIn(HER_SECOND_LINE, self.item(script)["text"])
        posted.mark(self.store, script["id"], {"refresh_text": True, "base_updated_at": latest["updated_at"]}, update_only=True)
        self.assertIn(HER_SECOND_LINE, self.item(script)["text"])

    def test_text_over_the_library_limit_is_refused(self):
        script = self.draft(["今天去超市"])
        self.mark(script)
        with mock.patch.object(posted, "MAX_SAMPLE_CHARS", 3):
            with self.assertRaises(TextTooLong):
                posted.mark(self.store, script["id"], {"refresh_text": True, "likes": 5})
        self.assertIsNone(self.item(script)["likes"])                   # refused before anything was written


# ---------------------------------------------------------------------------------------------- 3. numbers, and the day they were read
class NumbersTest(PostedCase):
    def test_the_script_page_and_the_library_item_change_the_same_numbers_and_their_day(self):
        script = self.draft()
        recorded, _ = self.mark(script, posted_on="2026-11-01", views=1000, likes=50, metrics_on="2026-11-03")
        self.assertEqual((recorded["metrics_on"], recorded["read_after_days"]), ("2026-11-03", 2))
        # "Update numbers" on the script page.
        answer, _ = posted.mark(self.store, script["id"], {"views": 5000, "likes": 260, "collects": 40, "comments": "",
                                                           "metrics_on": "2026-11-10"}, update_only=True)
        item = self.item(script)
        self.assertEqual([item[f] for f in ("views", "likes", "collects", "comments")], [5000, 260, 40, None])
        self.assertEqual((answer["posted"]["metrics_on"], answer["posted"]["read_after_days"]), ("2026-11-10", 9))
        # Editing the library item: the same numbers, and a number typed without a day was read today.
        edited = posted.update_item(self.store, item, {"likes": 300})
        self.assertEqual((edited["likes"], edited["metrics_on"]), (300, "2026-11-16"))
        recorded = posted.with_posted(self.store, self.store.get_script(script["id"]))["posted"]
        self.assertEqual((recorded["likes"], recorded["views"], recorded["metrics_on"], recorded["read_after_days"]),
                         (300, 5000, "2026-11-16", 15))
        # The day can be corrected on its own, from either side.
        posted.update_item(self.store, self.item(script), {"metrics_on": "2026-11-12"})
        self.assertEqual(self.item(script)["metrics_on"], "2026-11-12")
        self.mark(script, metrics_on="2026-11-13")
        self.assertEqual(self.item(script)["metrics_on"], "2026-11-13")
        # An edit that touches no number leaves the day alone.
        posted.update_item(self.store, self.item(script), {"title": "面霜分享 2", "is_sponsored": True})
        self.assertEqual((self.item(script)["metrics_on"], self.item(script)["title"]), ("2026-11-13", "面霜分享 2"))

    def test_nothing_is_adjusted_projected_or_normalised(self):
        early, late = self.draft(title="发了两天"), self.draft(title="发了六十天")
        self.mark(early, posted_on="2026-11-14", likes=40, metrics_on="2026-11-16")
        self.mark(late, posted_on="2026-09-17", likes=400, metrics_on="2026-11-16")
        samples = self.store.list_samples()
        by_title = {s["title"]: s for s in samples}
        self.assertEqual((corpus.read_after_days(by_title["发了两天"]), corpus.read_after_days(by_title["发了六十天"])), (2, 60))
        rows = {row["title"]: row for row in corpus.performance(samples)["rows"]}
        self.assertEqual((rows["发了两天"]["interactions"], rows["发了六十天"]["interactions"]), (40, 400))    # as typed
        self.assertEqual((by_title["发了两天"]["likes"], by_title["发了六十天"]["likes"]), (40, 400))
        # The ideas prompt is told how old each reading was, next to the numbers as they are.
        history = {entry["title"]: entry for entry in map(pipeline._history_entry, samples)}
        self.assertEqual((history["发了两天"]["numbers_read_days_after_posting"], history["发了两天"]["likes"]), (2, 40))
        self.assertEqual(history["发了六十天"]["numbers_read_days_after_posting"], 60)
        self.assertNotIn("numbers_read_days_after_posting", history["我的第1篇"])
        self.assertIn("numbers_read_days_after_posting", pipeline.load_prompt_pair(pipeline.STAGE6)[1])

    def test_the_two_days_cannot_be_the_wrong_way_round(self):
        script = self.draft()
        self.assertEqual(self.refusal(lambda: self.mark(script, posted_on="2026-11-10", likes=5, metrics_on="2026-11-09")),
                         "read_before_posted")
        self.assertIsNone(self.item(script))
        self.mark(script, posted_on="2026-11-10", likes=5, metrics_on="2026-11-12")
        self.assertEqual(self.refusal(lambda: self.mark(script, metrics_on="2026-11-09")), "read_before_posted")
        self.assertEqual(self.refusal(lambda: posted.update_item(self.store, self.item(script), {"posted_on": "2026-11-13"})),
                         "read_before_posted")
        self.assertEqual(self.refusal(lambda: posted.update_item(self.store, self.item(script), {"likes": 9, "metrics_on": "2026-11-20"})),
                         "date_in_future")
        self.assertEqual((self.item(script)["posted_on"], self.item(script)["metrics_on"], self.item(script)["likes"]),
                         ("2026-11-10", "2026-11-12", 5))               # every refusal left all of it as it was
        self.mark(script, posted_on="2026-11-13", metrics_on="2026-11-14")              # both together is fine
        self.assertEqual(self.item(script)["posted_on"], "2026-11-13")

    def test_an_item_without_numbers_has_no_reading_day(self):
        script = self.draft()
        self.assertIsNone(self.mark(script, metrics_on="2026-11-15")[0]["metrics_on"])   # a day with no number means nothing
        self.assertEqual(self.mark(script, likes=5)[0]["metrics_on"], "2026-11-16")
        cleared = posted.update_item(self.store, self.item(script), {"likes": ""})
        self.assertEqual((cleared["likes"], cleared["metrics_on"]), (None, None))
        self.assertEqual(posted.facts(cleared)["read_after_days"], None)

    def test_an_item_that_came_in_by_hand_is_edited_as_before(self):
        by_hand = self.store.create_sample(title="手动加的", text="今天去超市", metrics={"likes": 5})
        edited = posted.update_item(self.store, by_hand, {"likes": 9, "posted_on": "2026-11-01", "metrics_on": "nonsense",
                                                          "note_url": "javascript:alert(1)", "origin": "script", "script_id": "x"})
        self.assertEqual(edited["likes"], 9)
        self.assertEqual(set(edited), set(by_hand))                     # nothing of a posted item was put on it
        self.assertIsNone(posted.shown(self.store, edited)["posted"])
        with sqlite3.connect(str(self.store.db_path)) as db:
            row = db.execute("SELECT origin, script_id, posted_on, note_url, metrics_on FROM samples WHERE id = ?", (by_hand["id"],)).fetchone()
        self.assertEqual(row, ("", None, None, "", None))


# ---------------------------------------------------------------------------------------------- 4. the honesty rule
class HonestyTest(PostedCase):
    def learned(self):
        """Everything that learns or checks the creator's wording, as one value that can be compared."""
        store = self.store
        samples = store.list_samples()
        reference = voicematch.reference_for(store)
        brief = pipeline.draft_brief(store, notes="再拍一期面霜", target_sec=60, video_format="VLOG_VOICEOVER")
        probe = store.create_script(title="探针", lines=PROBE)
        try:
            openings_asked = suggest.suggest_prompt(store, probe["id"], "hooks", 0)[1]
        finally:
            store.delete_script(probe["id"])
        return {
            "measured statistics": corpus.voice_stats(s["text"] for s in pipeline.confirmed_samples(samples) if not s["is_sponsored"]),
            "voice-match reference": (reference.facts(), reference.band, sorted(reference.all.lengths.items()),
                                      sorted(reference.all.endings.items()), sorted(reference.all.starts.items()),
                                      sorted(reference.all.english_words.items())),
            "voice match of a script": checks.lint_payload(store, PROBE, 30)["voice_match"],
            "lessons": pipeline.lessons_payload(store),
            "lesson rules in a prompt": learning.prompt_rules(store.list_lessons()),
            "openings that reach a draft prompt": hooks.best(samples),
            "her own openings": hooks.library(samples)["openers"],
            "how each kind of opening did": hooks.library(samples)["patterns"],
            "draft prompt": brief["user"], "what the draft is said to be based on": brief["basis"],
            "openings prompt": openings_asked,
            "voice-learning prompt": pipeline._build_voice(store)[1],
            "material a voice is learned from": (setup_status.status(store)["material"]["organic"],
                                                 setup_status.status(store)["material"]["clauses"]),
            "transcripts waiting": setup_status.status(store)["material"]["unreviewed"],
        }

    def posted_script(self, changed=None, **numbers):
        script = self.draft()
        if changed:
            script = self.edit(script, changed)
        self.mark(script, **(numbers or {"likes": 500, "views": 9000}))
        return script

    def test_wording_is_learned_from_only_after_she_confirms(self):
        before = self.learned()
        # She rewrote the opening line and one more; two lines are still the AI's. It did far better than her usual.
        script = self.posted_script({0: HER_FIRST_LINE, 1: HER_SECOND_LINE})
        waiting = self.learned()
        for what in before:
            self.assertEqual(waiting[what], before[what], what)        # marked as posted: nothing that learns wording moved
        for text in (HER_OPENING, AI_DRAFT[2], AI_DRAFT[3]):
            for what in ("draft prompt", "openings prompt", "voice-learning prompt"):
                self.assertNotIn(text, waiting[what], (what, text))
        # Other edits to the item are not a confirmation: confirming is its own act.
        item = self.item(script)
        posted.update_item(self.store, item, {"title": "面霜分享", "likes": 510, "is_sponsored": False, "text": item["text"]})
        self.mark(script, likes=520, posted_on="2026-11-15")
        self.assertTrue(self.item(script)["needs_review"])
        still = self.learned()
        for what in before:
            self.assertEqual(still[what], before[what], what)

        self.confirm(script)                                           # "this is what I said, in my words"
        after = self.learned()
        for what in ("measured statistics", "voice-match reference", "voice match of a script", "openings that reach a draft prompt",
                     "her own openings", "draft prompt", "what the draft is said to be based on", "openings prompt",
                     "voice-learning prompt", "material a voice is learned from"):
            self.assertNotEqual(after[what], before[what], what)
        self.assertEqual(after["measured statistics"]["scripts"], before["measured statistics"]["scripts"] + 1)
        self.assertEqual(after["material a voice is learned from"][0], before["material a voice is learned from"][0] + 1)
        self.assertEqual(after["openings that reach a draft prompt"][0], {"title": "面霜分享", "opening": HER_OPENING})
        self.assertIn(AI_DRAFT[3], after["draft prompt"])              # the whole text she confirmed is now one of her examples
        self.assertIn(AI_DRAFT[3], after["voice-learning prompt"])
        # Marking and confirming remember no lesson: lessons are kept one at a time, by her, on the script page.
        for what in ("lessons", "lesson rules in a prompt", "transcripts waiting"):
            self.assertEqual(after[what], before[what], what)
        self.assertEqual(self.store.count_lessons(), 0)

        # And she can take the confirmation back.
        self.store.update_sample(self.item(script)["id"], {"needs_review": True})
        back = self.learned()
        for what in before:
            self.assertEqual(back[what], before[what], what)

    def test_what_was_posted_counts_for_performance_at_once(self):
        self.store.upsert_trends([{"keyword": "面霜", "heat": 1000}])
        samples = self.store.list_samples()
        usual = hooks.library(samples)["usual_interactions"]
        trend = trends.payload(self.store)["trends"][0]
        self.assertEqual((usual, trend["her_videos"], trend["her_videos_with_numbers"]), (150.0, 2, 2))
        script = self.posted_script(likes=900, collects=300, comments=50, views=20000)
        self.assertTrue(self.item(script)["needs_review"])             # counted for performance while its wording still waits
        samples = self.store.list_samples()
        # "What worked": the ranking of her videos.
        top = corpus.performance(samples)["rows"][0]
        self.assertEqual((top["title"], top["interactions"], top["interaction_rate_pct"]), ("面霜分享", 1250, 6.2))
        # Her usual result, which openers and trends are measured against, is now taken over eleven videos
        # (it stays 150 here only because of how LIKES was chosen; the next test shows it moving).
        self.assertEqual(hooks.library(samples)["usual_interactions"], 150.0)
        self.assertEqual(hooks.library(samples)["videos_with_numbers"], 11)
        self.assertEqual(trends.payload(self.store)["videos_with_numbers"], 11)
        # Topic performance and trend lift: the keyword's evidence now includes the posted video.
        after = trends.payload(self.store)["trends"][0]
        self.assertEqual((after["her_videos"], after["her_videos_with_numbers"]), (3, 3))
        self.assertEqual(after["evidence_titles"][0], "面霜分享")
        self.assertGreater(after["lift_raw"], trend["lift_raw"])
        self.assertGreater(after["score"], trend["score"])
        self.assertEqual(trends.payload(self.store)["baseline_interactions"], 150.0)
        # The ideas prompt gets the video with its numbers and what it was about, and is told whose words they may be.
        ideas = pipeline.ideas_prompt(self.store)[1]
        entry = pipeline._history_entry(self.item(script))
        self.assertEqual((entry["likes"], entry["views"], entry["script_written_in_app_wording_not_confirmed"]), (900, 20000, True))
        self.assertTrue(entry["script_excerpt"].startswith(AI_OPENING))
        self.assertNotIn("transcript_not_confirmed", entry)             # it is not a machine transcript, and is not called one
        self.assertIn('"script_written_in_app_wording_not_confirmed": true', ideas)
        self.assertIn("script_written_in_app_wording_not_confirmed", pipeline.load_prompt_pair(pipeline.STAGE6)[1])
        self.assertEqual(json.loads(ideas.split("<computed_ranking>")[1].split("-->")[1].split("</computed_ranking>")[0])["ranking"][0]["title"],
                         "面霜分享")
        # A sponsored posted video counts where sponsored videos count, and nowhere else: as for any item.
        ad = self.draft(title="商单那条")
        self.mark(ad, likes=5000, is_sponsored=True)
        self.assertEqual(hooks.library(self.store.list_samples())["usual_interactions"], 150.0)

    def test_her_usual_result_moves_and_that_alone_can_change_which_of_her_openings_are_best(self):
        """Performance is meant to count, so this is the one way marking as posted may change a prompt:
        through her usual result, never through the posted item's own words."""
        store = Store(self.tmp / "few")
        store.create_sample(title="早餐", text="周日早上煮咖啡啦 / 然后做了个三明治", metrics={"likes": 100})
        store.create_sample(title="超市", text="上周末跟我老公去超市补货嘛", metrics={"likes": 300})
        self.assertEqual([o["title"] for o in hooks.best(store.list_samples())], ["超市"])              # her usual is 200
        script = store.create_script(title="面霜分享", lines=lines(*AI_DRAFT), from_ai=True)
        posted.mark(store, script["id"], {"likes": 20})
        self.assertEqual(hooks.library(store.list_samples())["usual_interactions"], 100.0)
        best = hooks.best(store.list_samples())
        self.assertEqual([o["title"] for o in best], ["超市", "早餐"])                                    # both are now at or above her usual
        self.assertNotIn("面霜分享", [o["title"] for o in best])

    def test_she_is_told_how_many_lines_are_still_the_ais_before_she_confirms(self):
        untouched = self.posted_script()
        facts = posted.for_script(self.store, untouched)
        self.assertEqual((facts["wording"], facts["lines"], facts["lines_by_ai"], facts["drafted"]), ("waiting", 4, 4, True))   # every line is the AI's
        # Changes that are no change of wording leave a line the AI's: punctuation, breath marks, cues, a moved or split line.
        cosmetic = self.draft(title="只动了标点")
        now = [dict(line) for line in cosmetic["lines"]]
        now[0]["spoken_script_zh"] = "综上所述，这款面霜的质地非常轻盈。[停顿] 与此同时它的吸收速度令人印象深刻！"
        now[1], now[2] = now[2], now[1]
        now[3:] = [{"spoken_script_zh": "然而价格略高"}, {"spoken_script_zh": "需要理性消费"}]
        self.store.update_script(cosmetic["id"], {"lines": now})
        self.mark(cosmetic)
        facts = posted.for_script(self.store, cosmetic)
        self.assertEqual((facts["lines"], facts["lines_by_ai"]), (5, 5))
        # Rewritten and added lines are hers; the rest are counted as the AI's.
        edited = self.draft(title="改过两句")
        now = [dict(line) for line in self.edit(edited, {0: HER_FIRST_LINE, 1: HER_SECOND_LINE})["lines"]]
        now.append({"spoken_script_zh": "我老公也说好用哈"})
        self.store.update_script(edited["id"], {"lines": now})
        self.mark(edited)
        facts = posted.for_script(self.store, edited)
        self.assertEqual((facts["lines"], facts["lines_by_ai"]), (5, 2))
        # A proposal an AI made that she took is the AI's line, also in a script she typed by hand.
        by_hand = self.draft([HER_FIRST_LINE, "原来的第二句嘛", "我老公也说好用哈"], title="自己打的", from_ai=False)
        self.store.add_ai_suggestions(by_hand["id"], ["换成 AI 提的这一句", "没被采用的另一句"])
        self.edit(by_hand, {1: "换成AI提的这一句！"})
        self.mark(by_hand)
        facts = posted.for_script(self.store, by_hand)
        self.assertEqual((facts["lines"], facts["lines_by_ai"], facts["drafted"], facts["wording"]), (3, 1, False, "waiting"))
        self.assertEqual(self.item(by_hand)["ai_draft"], {"drafted": False, "lines": [], "proposed": ["换成 AI 提的这一句"]})
        typed = self.draft([HER_FIRST_LINE, HER_SECOND_LINE], title="全是自己打的", from_ai=False)
        self.mark(typed)
        facts = posted.for_script(self.store, typed)
        self.assertEqual((facts["lines"], facts["lines_by_ai"], facts["wording"]), (2, 0, "waiting"))   # it waits all the same: one rule
        # The count follows the item's text: correcting a line in the library makes that line hers.
        item = self.item(untouched)
        fixed = item["text"].replace(AI_DRAFT[1], HER_SECOND_LINE)
        self.assertEqual(posted.facts(posted.update_item(self.store, item, {"text": fixed}))["lines_by_ai"], 3)
        self.assertEqual(posted.for_page(self.store, [self.item(untouched)])[0]["posted"]["lines_by_ai"], 3)

    def test_the_count_is_made_in_code_from_the_draft_and_the_final_lines(self):
        ai = lines("今天去超市补货啦 / 结果人特别多", "然后买了四盒小笼包", "最后回家煮了吃")
        final = lines("今天去超市补货啦 / 结果人特别多", "然后我们又拿了四盒小笼包嘛", "我老公说太多了哈", "", "最后回家煮了吃")
        self.assertEqual(learning.lines_by_ai(ai, final), [(1, True), (2, False), (3, False), (5, True)])
        self.assertEqual(learning.lines_by_ai(ai, final, ["我老公说，太多了哈！"]), [(1, True), (2, False), (3, True), (5, True)])
        self.assertEqual(learning.lines_by_ai([], final), [(1, False), (2, False), (3, False), (5, False)])
        self.assertEqual(learning.lines_by_ai(ai, []), [])
        for nothing in (None, "x", 5, [None, 3, {"spoken_script_zh": None}]):
            self.assertEqual(learning.lines_by_ai(ai, nothing), [])
            self.assertEqual(learning.lines_by_ai(nothing, final, nothing), [(1, False), (2, False), (3, False), (5, False)])

    def test_an_ai_application_and_the_setup_page_are_told_why_it_waits(self):
        script = self.posted_script(posted_on="2026-11-10", likes=500)
        video = self.store.create_sample(title="机器转写的", text="今天我们来聊一聊护肤", kind="video")
        listed = {s["title"]: s for s in mcp_server.t_list_library(self.store, {})["scripts"]}
        self.assertEqual((listed["面霜分享"]["from_script_in_app"], listed["面霜分享"]["needs_review"],
                          listed["面霜分享"]["numbers_read_days_after_posting"]), (True, True, 6))
        self.assertEqual((listed["机器转写的"]["from_script_in_app"], listed["机器转写的"]["needs_review"]), (False, True))
        self.assertEqual((listed["我的第1篇"]["from_script_in_app"], listed["我的第1篇"]["numbers_read_days_after_posting"]), (False, None))
        self.assertIn("from_script_in_app", mcp_server.t_list_library(self.store, {})["note"])
        material = setup_status.status(self.store)["material"]
        self.assertEqual((material["total"], material["organic"], material["unreviewed"]), (12, 10, 1))   # one transcript waits; the posted script is not one
        self.assertTrue(corpus.waits_as_posted_script(self.item(script)))
        self.assertFalse(corpus.waits_as_posted_script(video))
        self.assertFalse(corpus.waits_as_posted_script(self.store.list_samples()[-1]))


# ---------------------------------------------------------------------------------------------- 4, openers
class OpenerTest(PostedCase):
    def in_prompts(self, text):
        probe = self.store.create_script(title="探针", lines=PROBE)
        try:
            draft = pipeline.draft_brief(self.store, notes="再拍一期面霜", target_sec=60, video_format="VLOG_VOICEOVER")
            asked = suggest.suggest_prompt(self.store, probe["id"], "hooks", 0)[1]
        finally:
            self.store.delete_script(probe["id"])
        own = draft["user"].split("<creator_own_openings>")[1].split("</creator_own_openings>")[0]
        return text in own, text in asked.split("<creator_own_openings>")[1].split("</creator_own_openings>")[0] if "<creator_own_openings>" in asked else text in asked

    def test_an_opening_that_is_still_the_ais_sentence_is_never_hers(self):
        script = self.draft()
        self.edit(script, {2: "我老公也说好用哈"})                      # she changed a later line; the opening is the AI's
        self.mark(script, likes=5000)
        for confirmed in (False, True):
            if confirmed:
                self.confirm(script)                                   # she confirmed the item's wording, knowing 3 of 4 lines are the AI's
            found = hooks.library(self.store.list_samples())
            self.assertNotIn("面霜分享", [o["title"] for o in found["openers"]], confirmed)
            self.assertNotIn(AI_OPENING, [o["opening"] for o in found["openers"]], confirmed)
            self.assertEqual(sum(p["videos"] for p in found["patterns"]), sum(len(o["patterns"]) for o in found["openers"]))
            self.assertEqual([(o["title"], o["opening"], o["interactions"]) for o in found["written_by_ai"]],
                             [("面霜分享", AI_OPENING, 5000)], confirmed)   # shown apart, with the video's result
            self.assertEqual(set(found["written_by_ai"][0]), set(found["openers"][0]))
            self.assertNotIn("面霜分享", [o["title"] for o in hooks.best(self.store.list_samples())], confirmed)
            self.assertEqual(self.in_prompts(AI_OPENING), (False, False), confirmed)
            brief = pipeline.draft_brief(self.store, notes="再拍一期面霜", target_sec=60, video_format="VLOG_VOICEOVER")
            self.assertNotIn("面霜分享", brief["basis"]["opening_titles"], confirmed)
        # Once confirmed, the item is one of her examples; its opening is still not one of her openings.
        self.assertIn("面霜分享", brief["basis"]["exemplar_titles"])

    def test_an_opening_she_rewrote_is_hers_once_she_confirms(self):
        script = self.draft()
        self.edit(script, {0: HER_FIRST_LINE})
        self.mark(script, likes=5000)
        waiting = hooks.library(self.store.list_samples())
        self.assertNotIn(HER_OPENING, [o["opening"] for o in waiting["openers"]])      # not yet said to be hers
        self.assertNotIn("written_by_ai", waiting)                                     # and not an AI's either
        self.assertEqual(self.in_prompts(HER_OPENING), (False, False))
        self.confirm(script)
        found = hooks.library(self.store.list_samples())
        self.assertEqual((found["openers"][0]["title"], found["openers"][0]["opening"]), ("面霜分享", HER_OPENING))
        self.assertNotIn("written_by_ai", found)
        self.assertEqual(hooks.best(self.store.list_samples())[0], {"title": "面霜分享", "opening": HER_OPENING})
        self.assertEqual(self.in_prompts(HER_OPENING), (True, True))

    def test_what_counts_as_still_the_ais_sentence(self):
        draft = {"drafted": True, "lines": AI_DRAFT, "proposed": ["AI 提的一个开场，今天聊聊面霜"]}

        def by_ai(text):
            return hooks.opening_by_ai({"text": text, "ai_draft": draft}, hooks.describe(text)["opening"])

        self.assertTrue(by_ai("\n".join(AI_DRAFT)))
        self.assertTrue(by_ai("综上所述，这款面霜的质地非常轻盈！与此同时…"))            # punctuation is not wording
        self.assertTrue(by_ai(AI_DRAFT[2] + "\n" + HER_FIRST_LINE))                    # a line of the AI's moved to the top
        self.assertTrue(by_ai("AI提的一个开场 / 今天聊聊面霜\n" + HER_SECOND_LINE))      # a proposal that was taken
        self.assertTrue(by_ai("大家好\n" + AI_DRAFT[1]))                                # three words of hers, then the AI's sentence
        self.assertTrue(by_ai("综上所述这款面霜的\n质地非常轻盈\n与此同时它的吸收速度令人印象深刻"))   # cut into other lines, as a subtitle file does
        self.assertFalse(by_ai(HER_FIRST_LINE + "\n" + AI_DRAFT[1]))                   # her own opening line, enough words on its own
        self.assertFalse(by_ai("综上所述这款面霜的质地真的很轻盈嘛\n" + AI_DRAFT[1]))     # she reworded it: no longer the AI's sentence
        for nothing in ({"text": AI_DRAFT[0]}, {"text": AI_DRAFT[0], "ai_draft": None}, {"text": AI_DRAFT[0], "ai_draft": "x"},
                        {"text": "", "ai_draft": draft}, {"ai_draft": draft}):
            self.assertFalse(hooks.opening_by_ai(nothing, hooks.describe(nothing.get("text"))["opening"]), nothing)
        self.assertFalse(hooks.opening_by_ai({"text": AI_DRAFT[0], "ai_draft": {"lines": "综上所述", "proposed": 5}}, AI_OPENING))

    def test_the_library_keeps_its_shape_when_nothing_was_posted(self):
        self.assertNotIn("written_by_ai", hooks.library(self.store.list_samples()))
        self.assertEqual(hooks.library([]), {"openers": [], "patterns": [], "usual_interactions": None, "videos_with_numbers": 0})


# ---------------------------------------------------------------------------------------------- 4, the words of the posted video
class TranscriptTest(PostedCase):
    SUBS = "今天聊聊我入冬换的面霜嘛\n结果脸真的不干了哈\n所以我真的很推荐嘛"
    TIMING = {"units": 30, "spoken_sec": 9.0, "speaking_rate_cpm": 200}

    def test_a_subtitle_file_puts_what_she_said_in_the_same_item_and_counts_at_once(self):
        script = self.draft()
        self.mark(script, likes=500, posted_on="2026-11-10", note_url="https://example.com/n")
        item = self.item(script)
        before = len(self.store.list_samples())
        attached = posted.attach_transcript(self.store, item["id"], text=self.SUBS, kind="subtitle", timing=self.TIMING,
                                            source_name="面霜.srt")
        self.assertEqual(len(self.store.list_samples()), before)                        # no second item
        self.assertEqual((attached["id"], attached["text"], attached["kind"], attached["timing"], attached["source_name"]),
                         (item["id"], self.SUBS, "subtitle", self.TIMING, "面霜.srt"))
        self.assertEqual((attached["needs_review"], attached["title"], attached["likes"], attached["posted_on"], attached["note_url"],
                          attached["script_id"], attached["origin"]),
                         (False, "面霜分享", 500, "2026-11-10", "https://example.com/n", script["id"], ORIGIN_SCRIPT))
        self.assertIsNotNone(attached["reviewed_at"])
        facts = posted.for_script(self.store, script)
        self.assertEqual((facts["wording"], facts["lines"], facts["lines_by_ai"], facts["text_differs"]), ("spoken", None, None, False))
        self.assertIn(attached["id"], [s["id"] for s in pipeline.confirmed_samples(self.store.list_samples())])
        self.assertEqual(hooks.library(self.store.list_samples())["openers"][0]["opening"], "今天聊聊我入冬换的面霜嘛")
        # The item now holds what was said, so the script's text is not put back over it.
        self.assertEqual(self.refusal(lambda: posted.mark(self.store, script["id"], {"refresh_text": True}, update_only=True)),
                         "holds_transcript")
        self.assertEqual(self.item(script)["text"], self.SUBS)

    def test_an_ai_opening_read_out_as_written_is_still_not_her_opening(self):
        script = self.draft()
        self.mark(script, likes=5000)
        spoken = "综上所述这款面霜的质地\n非常轻盈\n与此同时它的吸收速度令人印象深刻\n所以我真的很推荐嘛"
        posted.attach_transcript(self.store, self.item(script)["id"], text=spoken, kind="subtitle")
        found = hooks.library(self.store.list_samples())
        self.assertNotIn("面霜分享", [o["title"] for o in found["openers"]])
        self.assertEqual([o["title"] for o in found["written_by_ai"]], ["面霜分享"])
        self.assertNotIn("面霜分享", [o["title"] for o in hooks.best(self.store.list_samples())])

    def test_a_transcription_waits_like_any_machine_transcript(self):
        script = self.draft()
        self.mark(script, likes=500)
        item = self.item(script)
        attached = posted.attach_transcript(self.store, item["id"], text=self.SUBS, kind="video", timing=self.TIMING, source_name="clip.mp4")
        self.assertEqual((attached["id"], attached["kind"], attached["needs_review"], attached["reviewed_at"]), (item["id"], "video", True, None))
        self.assertEqual(posted.facts(attached)["wording"], "transcript_waiting")
        self.assertFalse(corpus.waits_as_posted_script(attached))                       # it waits as a transcript now
        self.assertEqual(setup_status.status(self.store)["material"]["unreviewed"], 1)
        self.assertTrue(pipeline._history_entry(attached)["transcript_not_confirmed"])
        self.assertNotIn(attached["id"], [s["id"] for s in pipeline.confirmed_samples(self.store.list_samples())])
        confirmed = self.store.update_sample(item["id"], {"needs_review": False})
        self.assertEqual(posted.facts(confirmed)["wording"], "spoken")

    def test_words_are_attached_only_to_a_posted_item_and_only_as_a_videos_words(self):
        script = self.draft()
        self.mark(script)
        item = self.item(script)
        by_hand = self.store.list_samples()[-1]
        self.assertEqual(self.refusal(lambda: posted.attach_transcript(self.store, by_hand["id"], text="x", kind="subtitle")), "bad_attach")
        for unknown in ("0" * 12, "", None, 5, ["x"]):
            self.assertEqual(self.refusal(lambda: posted.attach_transcript(self.store, unknown, text="x", kind="subtitle")), "bad_attach")
            self.assertEqual(self.refusal(lambda: posted.attachable(self.store, unknown)), "bad_attach")
        for kind in ("script", "text", "", None):
            self.assertEqual(self.refusal(lambda: posted.attach_transcript(self.store, item["id"], text="x", kind=kind)), "attach_needs_words")
        self.assertEqual((self.store.get_sample(by_hand["id"]), self.item(script)), (by_hand, item))


# ---------------------------------------------------------------------------------------------- 7. a data folder from an earlier version
class EarlierVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        clock = mock.patch.object(posted, "today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def old_database(self, schema=SCHEMA_AT_658AC1D):
        """A database as the code at 658ac1d leaves it: its tables, and rows written the way that code writes them."""
        ai_lines = json.dumps(lines(*AI_DRAFT), ensure_ascii=False)
        now_lines = json.dumps(lines(HER_FIRST_LINE, *AI_DRAFT[1:]), ensure_ascii=False)
        db = sqlite3.connect(str(self.tmp / "studio.db"))
        db.executescript(schema)
        db.execute("INSERT INTO scripts (id, title, lines_json, ai_lines_json, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("aaaaaaaaaaaa", "面霜分享", now_lines, ai_lines, 1.0, 2.0))
        db.execute("INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments, kind, timing_json, source_name,"
                   " needs_review, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("bbbbbbbbbbbb", "超市那天", OWN[0], 0, 2000, 150, None, None, "script", None, "", 0, 3.0, 4.0))
        db.execute("INSERT INTO samples (id, title, text, kind, needs_review, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                   ("cccccccccccc", "散步那天", "今天我们去散步", "video", 1, 5.0, 6.0))
        db.execute("INSERT INTO lessons (id, script_id, ai, final, created_at) VALUES (?,?,?,?,?)",
                   ("dddddddddddd", "aaaaaaaaaaaa", AI_DRAFT[0], HER_FIRST_LINE, 7.0))
        db.execute("INSERT INTO ai_suggestions (script_id, text, created_at) VALUES (?,?,?)", ("aaaaaaaaaaaa", "AI 提过的一句", 8.0))
        db.commit()
        db.close()

    def columns(self, table="samples"):
        with sqlite3.connect(str(self.tmp / "studio.db")) as db:
            return [row[1] for row in db.execute("PRAGMA table_info(%s)" % table)]

    def test_a_database_made_at_658ac1d_opens_and_works_unchanged(self):
        self.old_database()
        self.assertEqual(NEW_SAMPLE_COLUMNS & set(self.columns()), set())
        store = Store(self.tmp)
        self.assertEqual(NEW_SAMPLE_COLUMNS - set(self.columns()), set())
        # Everything that was there is there, in the form it had: no field was added to an item that was not posted.
        self.assertEqual(store.list_samples(), [
            {"id": "cccccccccccc", "title": "散步那天", "text": "今天我们去散步", "is_sponsored": False, "views": None, "likes": None,
             "collects": None, "comments": None, "kind": "video", "timing": None, "source_name": "", "needs_review": True,
             "reviewed_at": None, "created_at": 5.0, "updated_at": 6.0},
            {"id": "bbbbbbbbbbbb", "title": "超市那天", "text": OWN[0], "is_sponsored": False, "views": 2000, "likes": 150,
             "collects": None, "comments": None, "kind": "script", "timing": None, "source_name": "", "needs_review": False,
             "reviewed_at": None, "created_at": 3.0, "updated_at": 4.0}])
        script = store.get_script("aaaaaaaaaaaa")
        self.assertEqual((script["title"], script["lines"][0]["spoken_script_zh"], script["has_ai_draft"]), ("面霜分享", HER_FIRST_LINE, True))
        self.assertEqual((store.count_lessons(), store.list_ai_suggestions("aaaaaaaaaaaa")), (1, ["AI 提过的一句"]))
        self.assertEqual([posted.shown(store, s)["posted"] for s in store.list_samples()], [None, None])
        self.assertEqual(posted.with_posted_dates(store, store.list_scripts())[0]["posted"], False)
        self.assertNotIn("written_by_ai", hooks.library(store.list_samples()))
        self.assertEqual(setup_status.status(store)["material"]["unreviewed"], 1)
        # And the new feature works on it: the old script is marked as posted, with what the AI wrote of it counted.
        answer, created = posted.mark(store, "aaaaaaaaaaaa", {"likes": 300, "posted_on": "2026-11-10"})
        self.assertTrue(created)
        self.assertEqual((answer["posted"]["lines"], answer["posted"]["lines_by_ai"], answer["posted"]["read_after_days"]), (4, 3, 6))
        self.assertEqual(len(store.list_samples()), 3)
        self.assertFalse(posted.mark(store, "aaaaaaaaaaaa", {"likes": 310})[1])
        with self.assertRaises(sqlite3.IntegrityError):
            store.create_sample(title="第二条", text="今天去超市", posted={"script_id": "aaaaaaaaaaaa"})
        # Opening it a second time changes nothing more.
        again = Store(self.tmp)
        self.assertEqual(len(again.list_samples()), 3)
        self.assertEqual(self.columns().count("origin"), 1)

    def test_a_database_from_before_any_added_column_gets_all_of_them(self):
        first_version = SCHEMA_AT_658AC1D
        for dropped in ("    keywords_json TEXT NOT NULL DEFAULT '[]',\n", "    basis_json TEXT,\n",
                        "    kind TEXT NOT NULL DEFAULT 'script',\n", "    timing_json TEXT,\n",
                        "    source_name TEXT NOT NULL DEFAULT '',\n", "    needs_review INTEGER NOT NULL DEFAULT 0,\n",
                        "    reviewed_at REAL,\n"):
            self.assertIn(dropped, first_version)
            first_version = first_version.replace(dropped, "")
        db = sqlite3.connect(str(self.tmp / "studio.db"))
        db.executescript(first_version)
        db.execute("INSERT INTO scripts (id, title, lines_json, ai_lines_json, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("aaaaaaaaaaaa", "面霜分享", json.dumps(lines(*AI_DRAFT), ensure_ascii=False), None, 1.0, 2.0))
        db.execute("INSERT INTO samples (id, title, text, likes, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("bbbbbbbbbbbb", "超市那天", OWN[0], 150, 3.0, 4.0))
        db.commit()
        db.close()
        store = Store(self.tmp)
        self.assertEqual(NEW_SAMPLE_COLUMNS - set(self.columns()), set())
        self.assertEqual([(s["title"], s["kind"], s["needs_review"], s.get("origin")) for s in store.list_samples()],
                         [("超市那天", "script", False, None)])
        answer, created = posted.mark(store, "aaaaaaaaaaaa", {"likes": 9})
        self.assertEqual((created, answer["posted"]["drafted"], answer["posted"]["lines_by_ai"]), (True, False, 0))   # it was typed by hand

    def test_two_programs_opening_an_old_database_at_once_both_succeed(self):
        self.old_database()
        opened, failed = [], []

        def open_it():
            try:
                opened.append(Store(self.tmp))
            except Exception as exc:        # noqa: BLE001 - reported below
                failed.append(exc)

        threads = [threading.Thread(target=open_it) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual((len(opened), failed), (4, []))
        self.assertEqual(len(opened[0].list_samples()), 2)
        with sqlite3.connect(str(self.tmp / "studio.db")) as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA index_list(samples)") if row[1] == "samples_by_script"], ["samples_by_script"])


# ---------------------------------------------------------------------------------------------- 5. the requests
class Ears:
    """Stands in for studio.transcribe: no Whisper, no microphone. ``during`` is called while a file is "transcribed"."""

    during = None

    def status(self):
        return {"supported": True, "installed": True, "model_ready": True, "reason": None}

    def install(self, progress=None):
        pass

    def uninstall(self):
        pass

    def transcribe_file(self, path, progress=None):
        if self.during is not None:
            self.during()
        return {"segments": [{"start": 0.0, "end": 4.0, "text": "今天聊聊我入冬换的面霜嘛"}, {"start": 4.0, "end": 8.0, "text": "结果脸真的不干了哈"}],
                "unclear_dropped": 0}


class ServerCase(PostedCase):
    own_material = False

    def setUp(self):
        super().setUp()
        self.ears = Ears()
        self.httpd = server.make_server(0, self.store, lambda *args: {}, self.ears)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None, body=None, content_type=None):
        head = {}
        if payload is not None:
            body, head["Content-Type"] = json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"
        if content_type:
            head["Content-Type"] = content_type
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, (json.loads(raw.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else raw)

    def new_script(self, texts=AI_DRAFT, title="面霜分享"):
        return self.send("GET", "/api/scripts/" + self.draft(texts, title)["id"])[1]

    def finished(self, job):
        deadline = time.time() + 20
        while job["status"] == "running" and time.time() < deadline:
            time.sleep(0.02)
            job = self.send("GET", "/api/jobs/" + job["id"])[1]["job"]
        return job


class RequestTest(ServerCase):
    def test_marking_updating_and_reading_over_http(self):
        script = self.new_script()
        self.assertIsNone(script["posted"])
        self.assertEqual([(s["posted"], s["posted_on"]) for s in self.send("GET", "/api/scripts")[1]["scripts"]], [(False, None)])
        status, marked = self.send("POST", "/api/scripts/%s/posted" % script["id"],
                                   {"posted_on": "2026-11-10", "note_url": "https://example.com/n", "views": 9000, "likes": 500,
                                    "base_updated_at": script["updated_at"]})
        self.assertEqual((status, marked["id"], marked["posted"]["likes"], marked["posted"]["read_after_days"]), (201, script["id"], 500, 6))
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1], marked)          # the answer is the script as a read gives it
        self.assertEqual([(s["posted"], s["posted_on"]) for s in self.send("GET", "/api/scripts")[1]["scripts"]], [(True, "2026-11-10")])
        # Marking again: 200, the same item.
        status, again = self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 510})
        self.assertEqual((status, again["posted"]["sample_id"], again["posted"]["likes"]), (200, marked["posted"]["sample_id"], 510))
        # "Update numbers".
        status, updated = self.send("PUT", "/api/scripts/%s/posted" % script["id"], {"likes": 600, "collects": 80, "metrics_on": "2026-11-14"})
        self.assertEqual((status, updated["posted"]["likes"], updated["posted"]["collects"], updated["posted"]["read_after_days"]), (200, 600, 80, 4))
        # The library shows the item with where it came from; editing it there changes the same numbers.
        listing = self.send("GET", "/api/samples")[1]
        self.assertEqual(len(listing["samples"]), 1)
        item = listing["samples"][0]
        self.assertEqual((item["posted"]["script_id"], item["posted"]["script_title"], item["posted"]["wording"],
                          item["posted"]["lines"], item["posted"]["lines_by_ai"], item["likes"]),
                         (script["id"], "面霜分享", "waiting", 4, 4, 600))
        self.assertEqual(listing["stats"]["scripts"], 0)                                       # its wording is in no statistic
        self.assertNotIn("ai_draft", item)
        status, edited = self.send("PUT", "/api/samples/" + item["id"], {"likes": 700})
        self.assertEqual((status, edited["likes"], edited["posted"]["metrics_on"], edited["posted"]["read_after_days"]), (200, 700, "2026-11-16", 6))
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1]["posted"]["likes"], 700)
        status, edited = self.send("PUT", "/api/samples/" + item["id"], {"posted_on": "2026-11-12", "note_url": ""})
        self.assertEqual((status, edited["posted"]["posted_on"], edited["posted"]["note_url"], edited["posted"]["read_after_days"]), (200, "2026-11-12", "", 4))
        # Confirming is the confirmation every waiting item gets; then the statistics move.
        status, confirmed = self.send("PUT", "/api/samples/" + item["id"], {"needs_review": False})
        self.assertEqual((status, confirmed["posted"]["wording"]), (200, "confirmed"))
        self.assertEqual(self.send("GET", "/api/samples")[1]["stats"]["scripts"], 1)
        self.assertEqual(self.send("PUT", "/api/samples/" + item["id"], {"needs_review": "false"})[0], 400)
        # A saved script answers in the same shape as a read one.
        status, saved = self.send("PUT", "/api/scripts/" + script["id"], {"title": "面霜分享（改）"})
        self.assertEqual((status, saved["posted"]["text_differs"]), (200, True))
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1], saved)
        # Removing each side.
        self.assertEqual(self.send("DELETE", "/api/samples/" + item["id"])[0], 200)
        self.assertIsNone(self.send("GET", "/api/scripts/" + script["id"])[1]["posted"])
        self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 5})
        self.assertEqual(self.send("DELETE", "/api/scripts/" + script["id"])[0], 200)
        left = self.send("GET", "/api/samples")[1]["samples"]
        self.assertEqual([(s["title"], s["likes"], s["posted"]["script_id"]) for s in left], [("面霜分享（改）", 5, None)])

    def test_every_request_is_a_json_write(self):
        script = self.new_script()
        path = "/api/scripts/%s/posted" % script["id"]
        for method in ("POST", "PUT"):
            for content_type, body in (("application/x-www-form-urlencoded", b"likes=5"), ("text/plain", b'{"likes": 5}'),
                                       ("text/plain;application/json", b'{"likes": 5}'), (None, b'{"likes": 5}')):
                status, answer = self.send(method, path, body=body, content_type=content_type)
                self.assertEqual((status, answer["error"]), (415, "json_required"), (method, content_type))
            for body in (b"{", b"[1]", b'"x"', b'{"likes": NaN}'):
                status, answer = self.send(method, path, body=body, content_type="application/json")
                self.assertEqual((status, answer["error"]), (400, "bad_json"), (method, body))
        self.assertEqual(self.send("GET", path)[0], 404)
        self.assertEqual(self.send("DELETE", path)[0], 404)
        self.assertEqual(self.send("GET", "/api/samples")[1]["samples"], [])

    def test_a_refusal_has_a_code_and_a_sentence_in_both_languages(self):
        script = self.new_script()
        empty = self.new_script([""], "空的")
        by_hand = self.store.create_sample(title="手动加的", text="今天去超市")
        path = "/api/scripts/%s/posted" % script["id"]
        refused = [
            ("POST", "/api/scripts/%s/posted" % ("0" * 12), {}, 404, "not_found"),
            ("PUT", path, {"likes": 5}, 409, "not_posted"),
            ("POST", "/api/scripts/%s/posted" % empty["id"], {}, 400, "nothing_spoken"),
            ("POST", path, {"posted_on": "next week"}, 400, "bad_date"),
            ("POST", path, {"posted_on": "2026-11-17"}, 400, "date_in_future"),
            ("POST", path, {"posted_on": "2026-11-10", "likes": 5, "metrics_on": "2026-11-01"}, 400, "read_before_posted"),
            ("POST", path, {"note_url": "javascript:alert(1)"}, 400, "bad_link"),
            ("POST", path, {"base_updated_at": "yesterday"}, 400, "bad_base_updated_at"),
            ("POST", path, {"base_updated_at": script["updated_at"] - 1}, 409, "stale_script"),
            ("POST", "/api/samples", {"subtitle_text": "1\n00:00:00,000 --> 00:00:02,000\n今天去超市\n", "attach_to": by_hand["id"]}, 404, "bad_attach"),
            ("POST", "/api/samples", {"text": "今天去超市", "attach_to": by_hand["id"]}, 400, "attach_needs_words"),
            ("POST", "/api/videos", {"upload_id": "0" * 12, "attach_to": by_hand["id"]}, 404, "bad_attach"),
        ]
        for method, where, payload, status, code in refused:
            got, answer = self.send(method, where, payload)
            self.assertEqual((got, answer["error"]), (status, code), (where, payload))
            self.assertRegex(answer["message"], r"^[A-Za-z][ -~]+$", code)                     # an English sentence
            self.assertRegex(answer["message_zh"], r"[\u4e00-\u9fff]", code)                   # and a Chinese one
        self.assertEqual([s["title"] for s in self.send("GET", "/api/samples")[1]["samples"]], ["手动加的"])   # none of them stored anything
        for code, (status, english, chinese) in posted.REFUSALS.items():
            self.assertIn(status, (400, 404, 409), code)
            self.assertTrue(english.strip() and chinese.strip(), code)
        # The library's own item route refuses the same way.
        self.send("POST", path, {"likes": 5, "posted_on": "2026-11-10"})
        item = self.send("GET", "/api/samples")[1]["samples"][0]
        for payload, code in (({"posted_on": "2026-11-17"}, "date_in_future"), ({"metrics_on": "x"}, "bad_date"),
                              ({"note_url": "ftp://example.com"}, "bad_link"), ({"posted_on": "2026-11-17", "likes": 99}, "date_in_future")):
            got, answer = self.send("PUT", "/api/samples/" + item["id"], payload)
            self.assertEqual((got, answer["error"]), (400, code), payload)
            self.assertTrue(answer["message"] and answer["message_zh"], payload)
        self.assertEqual(self.send("GET", "/api/samples")[1]["samples"][0], item)              # and changed nothing, the number included

    def test_a_save_based_on_an_older_version_is_still_refused(self):
        script = self.new_script()
        status, newer = self.send("PUT", "/api/scripts/" + script["id"], {"title": "在别处改过"})
        self.assertEqual(status, 200)
        stale_mark = self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 5, "base_updated_at": script["updated_at"]})
        stale_save = self.send("PUT", "/api/scripts/" + script["id"], {"title": "旧页面的保存", "base_updated_at": script["updated_at"]})
        for status, answer in (stale_mark, stale_save):
            self.assertEqual((status, answer["error"], answer["updated_at"]), (409, "stale_script", newer["updated_at"]))
            self.assertIn("Nothing was saved", answer["message"])
            self.assertIn("这次没有保存", answer["message_zh"])
        self.assertEqual(stale_mark[1], stale_save[1])                                         # one refusal, worded once
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1], newer)
        self.assertEqual(self.send("GET", "/api/samples")[1]["samples"], [])

    def test_markup_in_a_title_or_a_link_stays_text(self):
        title = '<img src=x onerror="alert(1)">&lt;b&gt;'
        link = 'https://example.com/"><script>alert(1)</script>'
        script = self.new_script(title=title)
        status, marked = self.send("POST", "/api/scripts/%s/posted" % script["id"], {"note_url": link, "likes": 5})
        self.assertEqual((status, marked["posted"]["title"], marked["posted"]["note_url"]), (201, title, link))
        item = self.send("GET", "/api/samples")[1]["samples"][0]
        self.assertEqual((item["title"], item["posted"]["script_title"], item["posted"]["note_url"]), (title, title, link))
        # The page builds markup from these only through esc(): the address of a link, and every text shown.
        script_file = (WEB / "posted.js").read_text(encoding="utf-8")
        self.assertEqual(sorted(set(re.findall(r'href="([^"]*)"', script_file))),
                         ["#", "#/library", "#/s/${esc(p.script_id)}", "${esc(p.note_url)}"])
        for inserted in re.findall(r"\$\{([^{}]*)\}", script_file):
            if re.search(r"note_url|script_title|\.title|\.opening|posted_on|metrics_on|\.error|\.text\b", inserted):
                self.assertTrue(inserted.startswith("esc("), inserted)
        self.assertNotIn("innerText", script_file)
        self.assertEqual([found.strip() for found in re.findall(r"\.innerHTML\s*=\s*([^;]{0,40})", script_file)],
                         ["`<div class=\"card soft\" id=\"po-card\">", "poItemInner(saved.id, saved.posted)"])

    def test_the_words_of_the_posted_video_arrive_through_the_ways_the_library_has(self):
        script = self.new_script()
        self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 5})
        item = self.send("GET", "/api/samples")[1]["samples"][0]
        # A video: uploaded and transcribed on this computer as any video is, into the same item, where it waits to be read.
        status, up = self.send("PUT", "/api/uploads?name=clip.mp4", body=b"not really a video", content_type="application/octet-stream")
        self.assertEqual(status, 201)
        status, started = self.send("POST", "/api/videos", {"upload_id": up["upload_id"], "attach_to": item["id"], "title": "别的标题"})
        self.assertEqual(status, 202)
        job = self.finished(started["job"])
        self.assertEqual((job["status"], job["result"]), ("done", {"sample_id": item["id"], "title": "面霜分享"}))
        listing = self.send("GET", "/api/samples")[1]["samples"]
        self.assertEqual(len(listing), 1)
        self.assertEqual((listing[0]["id"], listing[0]["kind"], listing[0]["needs_review"], listing[0]["posted"]["wording"], listing[0]["likes"]),
                         (item["id"], "video", True, "transcript_waiting", 5))
        self.assertEqual(listing[0]["text"], "今天聊聊我入冬换的面霜嘛\n结果脸真的不干了哈")
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])                           # no copy of the video is kept
        # A subtitle file: the same item again, and its words count at once.
        subtitles = "1\n00:00:00,000 --> 00:00:03,000\n今天聊聊我入冬换的面霜嘛\n\n2\n00:00:03,000 --> 00:00:06,000\n所以我真的很推荐嘛\n"
        status, attached = self.send("POST", "/api/samples", {"subtitle_text": subtitles, "source_name": "面霜.srt", "attach_to": item["id"],
                                                               "title": "别的标题", "likes": 99999})
        self.assertEqual((status, attached["id"], attached["kind"], attached["needs_review"], attached["posted"]["wording"]),
                         (200, item["id"], "subtitle", False, "spoken"))
        self.assertEqual((attached["title"], attached["likes"], attached["text"]), ("面霜分享", 5, "今天聊聊我入冬换的面霜嘛\n所以我真的很推荐嘛"))
        self.assertEqual(len(self.send("GET", "/api/samples")[1]["samples"]), 1)
        # An attachment that is refused leaves the upload unclaimed, to be used for something else.
        status, up = self.send("PUT", "/api/uploads?name=clip.mp4", body=b"not really a video", content_type="application/octet-stream")
        self.assertEqual(self.send("POST", "/api/videos", {"upload_id": up["upload_id"], "attach_to": "0" * 12})[0], 404)
        status, started = self.send("POST", "/api/videos", {"upload_id": up["upload_id"], "title": "另一条视频"})
        self.assertEqual((status, self.finished(started["job"])["status"]), (202, "done"))
        self.assertEqual(sorted(s["title"] for s in self.send("GET", "/api/samples")[1]["samples"]), ["另一条视频", "面霜分享"])
        # The item is removed while its video is being transcribed: the job says so, in both languages, and adds nothing.
        status, up = self.send("PUT", "/api/uploads?name=clip.mp4", body=b"not really a video", content_type="application/octet-stream")
        self.ears.during = lambda: self.store.delete_sample(item["id"])
        status, started = self.send("POST", "/api/videos", {"upload_id": up["upload_id"], "attach_to": item["id"]})
        job = self.finished(started["job"])
        self.assertEqual((status, job["status"], job["error"]["code"]), (202, "failed", "bad_attach"))
        self.assertEqual((job["error"]["message"], job["error"]["message_zh"]), posted.REFUSALS["bad_attach"][1:])
        self.assertEqual([s["title"] for s in self.send("GET", "/api/samples")[1]["samples"]], ["另一条视频"])
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])

    def test_the_hooks_route_keeps_ai_openings_apart(self):
        self.store.create_sample(title="超市", text=OWN[0], metrics={"likes": 100})
        script = self.new_script()
        self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 900})
        found = self.send("GET", "/api/hooks")[1]
        self.assertEqual([o["title"] for o in found["openers"]], ["超市"])
        self.assertEqual([(o["title"], o["opening"], o["times_usual"]) for o in found["written_by_ai"]], [("面霜分享", AI_OPENING, 1.8)])

    def test_an_item_that_came_in_by_hand_is_edited_as_before(self):
        by_hand = self.store.create_sample(title="手动加的", text="今天去超市", metrics={"likes": 5})
        status, edited = self.send("PUT", "/api/samples/" + by_hand["id"], {"likes": 7, "posted_on": "2026-11-10", "note_url": "https://example.com/n"})
        self.assertEqual((status, edited["likes"], edited["posted"]), (200, 7, None))
        stored = self.store.get_sample(by_hand["id"])                # a day and a link are a posted item's: not taken here
        self.assertEqual({key: value for key, value in edited.items() if key != "posted"}, stored)
        self.assertNotIn("posted_on", stored)
        with sqlite3.connect(str(self.store.db_path)) as db:
            self.assertEqual(db.execute("SELECT origin, script_id, posted_on, note_url, metrics_on FROM samples").fetchall(),
                             [("", None, None, "", None)])
        # Removed in the same moment as the edit: the answer is null, as it was before this feature.
        with mock.patch.object(posted, "update_item", return_value=None):
            self.assertEqual(self.send("PUT", "/api/samples/" + by_hand["id"], {"likes": 9}), (200, None))


# ---------------------------------------------------------------------------------------------- 6. the page
class PageTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.script = (WEB / "posted.js").read_text(encoding="utf-8")

    def test_every_text_is_there_in_both_languages_under_its_own_prefix(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        keys = {language: set(re.findall(r"\b([a-z]+_[a-z_]+): '", body)) for language, body in blocks}
        self.assertGreater(len(keys["zh"]), 50)
        self.assertEqual({key for key in keys["zh"] | keys["en"] if not key.startswith("po_")}, set())
        self.assertEqual(keys["zh"], {key for key in keys["en"] if not key.endswith("_one")})    # English alone has singular forms
        used = set(re.findall(r"""['"](po_[a-z_]+)['"]""", self.script))
        self.assertEqual(used - keys["zh"], set())                                               # nothing is asked for that is not there
        self.assertEqual(keys["zh"] - used, set())                                               # and nothing is there that is never shown
        zh = dict(re.findall(r"\b(po_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(po_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertEqual((zh["po_mark"], en["po_mark"]), ("标记为已发布", "Mark as posted"))
        # Said plainly: readings of different age do not compare, and nothing is adjusted.
        for needed in ("2 天", "60 天", "不能直接比", "不折算", "不预估"):
            self.assertIn(needed, zh["po_compare"])
        for needed in ("2 days", "60 days", "not comparable", "nothing is adjusted or projected"):
            self.assertIn(needed, en["po_compare"])
        # If every line is the AI's, it says so.
        self.assertIn("每一句都是 AI 写的", zh["po_wait_all_ai"])
        self.assertIn("every line is the AI\\'s", en["po_wait_all_ai"])
        self.assertIn("全部还是 AI 写的原样", zh["po_c_all"])
        # No promise about how a video will do, anywhere.
        for text in list(zh.values()) + list(en.values()):
            self.assertIsNone(re.search(r"爆款|会火|上热门|涨粉|viral|blow up|guarantee", text, re.I), text)

    def test_it_is_served_loaded_before_pages_and_hooked_in_by_small_edits(self):
        index = (WEB / "index.html").read_text(encoding="utf-8")
        order = [index.index('src="/%s.js"' % name) for name in ("app", "setup", "discover", "craft", "prompter", "kwimport", "posted", "pages")]
        self.assertEqual(order, sorted(order))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", "/posted.js")
        res = conn.getresponse()
        body = res.read()
        conn.close()
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, (WEB / "posted.js").read_bytes())
        calls = {"app.js": ["poCardTag(s)", "paintPosted(document.getElementById('po-box'), s)", "poAfterSave(s)"],
                 "pages.js": ["poSampleHtml(s)", "poAfterSampleSave(card,", "poDeleteSampleText(card.dataset.id, 'confirm_delete_sample')"],
                 "setup.js": ["poWaits(x)", "poReviewNoteHtml(x, withButton)", "poConfirmLabel(x)", "poRowTag(x)",
                              "poDeleteSampleText(row.dataset.id, 'mt_confirm_delete')"],
                 "craft.js": ["poAiOpeningsHtml(found)"]}
        for name, wanted in calls.items():
            source = (WEB / name).read_text(encoding="utf-8")
            for call in wanted:
                self.assertIn(call, source, name)
                self.assertIn("function %s(" % call.split("(")[0], self.script.replace("const %s = (" % call.split("(")[0], "function %s(" % call.split("(")[0]))
            self.assertIsNone(re.search(r"['\"]po_[a-z]", source), name)                        # its texts live in its own file
        for name in ("discover.js", "prompter.js", "kwimport.js"):
            self.assertNotIn("po", re.findall(r"\b(po)[A-Z]\w*\(", (WEB / name).read_text(encoding="utf-8")), name)

    def test_it_uses_its_own_attribute_names_and_talks_to_this_server_only(self):
        self.assertNotIn("data-act", self.script)                   # the editor reads any data-act it does not know as "move down"
        self.assertNotIn("data-f=", self.script)                    # the library's own change handler must not take these fields
        self.assertIn('data-po-f="posted_on"', self.script)
        asked = set(re.findall(r"api\('(?:GET|POST|PUT|DELETE)', [`']([^`'$]+)", self.script))
        self.assertEqual(asked, {"/api/scripts/", "/api/samples/", "/api/samples", "/api/transcriber", "/api/videos", "/api/jobs/"})
        self.assertEqual(re.findall(r"fetch\(\s*([^,)]+)", self.script), ["'/api/uploads?name=' + encodeURIComponent(file.name"])
        for word in ("XMLHttpRequest", "window.open", "location.href", "getUserMedia", "mediaDevices", "localStorage", "manualBridge", "aiRun"):
            self.assertNotIn(word, self.script, word)
        self.assertEqual(re.search(r"const PO_METRICS = \[([^\]]+)\]", self.script).group(1).replace("'", "").split(", "),
                         list(posted.METRIC_FIELDS))


if __name__ == "__main__":
    unittest.main()
