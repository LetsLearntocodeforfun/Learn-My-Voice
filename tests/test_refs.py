"""Tests for "参考 / References": the creator's own material beside the script being edited.

Each class covers one thing the creator can rely on:

- pins belong to the script, survive a restart, number at most eight, and are no edit of the script: a save
  based on the version from before a pin is accepted; a removed item leaves the pins without an error;
- what the draft saw is found again by what the draft's basis recorded, and a missing item is said to be missing;
- what is on the same topic is chosen with the measure and the threshold of the trends page, ordered by
  counts, with what was found next to each item; with nothing to measure by, nothing is listed;
- a search matches what was typed as it was typed;
- an item says what it is and how it did, by the same usual result the openings use;
- THE HONESTY RULE: wording that is not confirmed as the creator's own is labelled, an AI's opening is said to
  be one, and what is kept for counting never reaches a page;
- an answer is bounded in size whatever the library holds, and each request looks at every item once;
- a data folder made by the version before this feature opens and works unchanged;
- reading is a GET, pinning a JSON write, and a refusal has a code and a sentence in English and in Chinese;
- markup in a title, a text or a query stays text, and the page script says everything in both languages.

No model and no real creator material is involved: the library here is made up, and the "AI draft" is a
script stored as one.
"""
import datetime
import http.client
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from studio import corpus, hooks, pipeline, posted, refs, server, trends
from studio.store import MAX_SAMPLE_CHARS, PAGE_WORDS, StaleScript, Store

WEB = Path(server.WEB_DIR)
GONE = "0" * 12                 # an id in the right form that names nothing

# Made-up material: (title, text, likes). The first two are about a face cream, the others are not.
OWN = (
    ("入冬换面霜", "入冬以后脸特别干嘛 / 所以我换了一款面霜 / 结果用得最勤哈 / 其实 toner 也换了 / 这款面霜真的不错", 300),
    ("面霜空瓶记", "这瓶面霜用了三个月嘛 / 终于空瓶了哈 / 秋冬护肤我就靠它 / 然后又回购了一瓶面霜 / 面霜真的要选对 / 这款面霜我会一直用", 450),
    ("Costco 补货", "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈 / 然后我们拿了四盒小笼包啦 / 其实家里还有嘛", 100),
    ("周日早餐", "周日早上煮咖啡啦 / 然后做了个三明治 / 我老公说好吃嘛 / 结果他吃了两个哈", 150),
    ("Trader Joe's 那天", "今天去 Trader Joe's 啦 / 然后又买了小笼包 / 其实家里还有嘛 / 但是忍不住哈", None),
)
# What an AI drafted, and what the creator made of its first line.
AI_DRAFT = ("综上所述这款面霜的质地非常轻盈 / 与此同时它的吸收速度令人印象深刻", "因此我强烈推荐给大家", "总而言之这是一个值得入手的选择")
AI_OPENING = "综上所述这款面霜的质地非常轻盈"
HER_FIRST_LINE = "入冬以后我换了一款面霜嘛 / 结果脸真的不干了哈"

# What every item in an answer is made of, and nothing else (``refs._shown``).
ITEM_KEYS = {"id", "title", "title_cut", "kind", "is_sponsored", "has_numbers", "interactions", "times_usual", "posted",
             "of_this_script", "wording", "opening", "opening_by_ai", "excerpt", "long", "pinned", "why",
             "views", "likes", "collects", "comments"}

# The tables exactly as the code at c157f8a creates them (studio/store.py, _SCHEMA), before this feature. The code
# at 450e618, which this feature was finished on, has the same file.
SCHEMA_AT_C157F8A = """
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
ONE_ITEM_A_SCRIPT_AT_C157F8A = "CREATE UNIQUE INDEX IF NOT EXISTS samples_by_script ON samples (script_id) WHERE script_id IS NOT NULL"


def lines(*texts):
    return [{"spoken_script_zh": text} for text in texts]


def keys_of(value):
    """Every key that occurs anywhere in a JSON value."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_of(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys_of(item)}
    return set()


def code_of(source):
    """A script file without its comments. No text of refs.js holds two slashes or a comment mark."""
    return re.sub(r"(?m)[ \t]//[^\n]*$", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def insertions(source):
    """Every ``${...}`` of a script file, whole, also when it holds braces of its own."""
    found, at = [], 0
    while True:
        at = source.find("${", at)
        if at < 0:
            return found
        depth, end = 1, at + 2
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        found.append(source[at + 2:end - 1])
        at += 2


class RefsCase(unittest.TestCase):
    """A store on a temporary data folder, with made-up material in it. Each item is a second newer than the one before."""

    material = OWN

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp)
        self.clock = 1000.0
        self.ids = {}
        for title, text, likes in self.material:
            self.ids[title] = self.add(title, text, likes)["id"]

    def sql(self, statement, args=()):
        db = sqlite3.connect(str(self.store.db_path))
        try:
            rows = db.execute(statement, args).fetchall()
            db.commit()
            return rows
        finally:
            db.close()

    def add(self, title, text, likes=None, **more):
        sample = self.store.create_sample(title=title, text=text, metrics={"likes": likes} if likes is not None else None, **more)
        self.clock += 1
        self.sql("UPDATE samples SET created_at = ?, updated_at = ? WHERE id = ?", (self.clock, self.clock, sample["id"]))
        return self.store.get_sample(sample["id"])

    def script(self, title="未命名脚本", keywords=("面霜",), texts=("今天聊聊面霜",), **more):
        return self.store.create_script(title=title, lines=lines(*texts), keywords=list(keywords), **more)

    def refs(self, script):
        return refs.for_script(self.store, script["id"])

    def related(self, script):
        return self.refs(script)["related"]

    def pin(self, script, sample_id, pinned=True):
        return refs.set_pin(self.store, script["id"], sample_id, pinned)

    def find(self, query, script=None):
        return refs.search(self.store, (script or self.script())["id"], query)

    def one(self, title, script=None):
        """The item with this title, as a search of the library gives it."""
        return next(item for item in self.find(title, script)["items"] if item["title"] == title)

    def pin_rows(self):
        return self.sql("SELECT script_id, sample_id FROM script_pins ORDER BY rowid")

    def refusal(self, call):
        with self.assertRaises(refs.RefsError) as refused:
            call()
        return refused.exception.code


# ---------------------------------------------------------------------------------------------- pins
class PinTest(RefsCase):
    def test_pins_belong_to_the_script_keep_their_order_and_survive_a_restart(self):
        a, b = self.script(title="甲"), self.script(title="乙")
        order = [self.ids["周日早餐"], self.ids["入冬换面霜"], self.ids["Costco 补货"]]
        for sample_id in order:
            answer = self.pin(a, sample_id)
        self.assertEqual([item["id"] for item in answer["pinned"]], order)
        self.assertTrue(all(item["pinned"] for item in answer["pinned"]))
        self.assertEqual(self.refs(b)["pinned"], [])                              # another script has pins of its own
        again = Store(self.tmp)                                                   # the app is closed and started again
        self.assertEqual([item["id"] for item in refs.for_script(again, a["id"])["pinned"]], order)
        self.assertEqual([item["id"] for item in self.pin(a, order[1], False)["pinned"]], [order[0], order[2]])
        # Pinning what is pinned and unpinning what is not change nothing, and are no error.
        self.assertEqual([item["id"] for item in self.pin(a, order[0])["pinned"]], [order[0], order[2]])
        self.assertEqual([item["id"] for item in self.pin(a, order[1], False)["pinned"]], [order[0], order[2]])
        self.assertEqual(self.pin_rows(), [(a["id"], order[0]), (a["id"], order[2])])

    def test_a_script_has_at_most_eight_pins(self):
        self.assertEqual(refs.MAX_PINS, 8)
        script = self.script()
        extra = [self.add("第%d条" % n, "内容 %d" % n)["id"] for n in range(9)]
        for sample_id in extra[:8]:
            self.pin(script, sample_id)
        self.assertEqual(self.refusal(lambda: self.pin(script, extra[8])), "too_many_pins")
        self.assertEqual([item["id"] for item in self.refs(script)["pinned"]], extra[:8])
        self.pin(script, extra[0])                                                # pinned already: not a ninth, and no error
        self.pin(script, extra[3], False)
        self.assertEqual([item["id"] for item in self.pin(script, extra[8])["pinned"]], extra[:3] + extra[4:9])
        self.assertEqual(self.refs(script)["limits"]["pins"], 8)

    def test_pinning_is_no_edit_of_the_script_and_a_save_from_before_the_pin_is_accepted(self):
        script = self.script(texts=("第一句", "第二句"))
        base = script["updated_at"]                       # what the editor holds: the version it loaded
        self.pin(script, self.ids["周日早餐"])
        self.pin(script, self.ids["入冬换面霜"])
        self.pin(script, self.ids["周日早餐"], False)
        self.assertEqual(self.store.get_script(script["id"]), script)             # nothing of the script changed, its time included
        saved = self.store.update_script(script["id"], {"lines": lines("第一句，改过了", "第二句")}, base_updated_at=base)
        self.assertEqual(saved["lines"][0]["spoken_script_zh"], "第一句，改过了")   # not refused as changed somewhere else
        self.assertEqual([item["title"] for item in self.refs(script)["pinned"]], ["入冬换面霜"])    # and the save left the pin alone
        # The rule itself still holds: a save from before an edit made somewhere else is refused.
        with self.assertRaises(StaleScript):
            self.store.update_script(script["id"], {"title": "旧页面的保存"}, base_updated_at=base)
        # Marking as posted compares the same time, and is taken after a pin as well.
        other = self.script(texts=("今天聊聊面霜",))
        self.pin(other, self.ids["周日早餐"])
        _, created = posted.mark(self.store, other["id"], {"likes": 5, "base_updated_at": other["updated_at"]})
        self.assertTrue(created)

    def test_a_pinned_item_removed_from_the_library_leaves_the_pins_without_an_error(self):
        script = self.script()
        self.pin(script, self.ids["周日早餐"])
        self.pin(script, self.ids["入冬换面霜"])
        self.assertTrue(self.store.delete_sample(self.ids["周日早餐"]))            # being pinned never keeps an item
        self.assertEqual([item["title"] for item in self.refs(script)["pinned"]], ["入冬换面霜"])
        self.assertEqual(self.pin_rows(), [(script["id"], self.ids["入冬换面霜"])])
        # A page that still shows it may ask to unpin it: no error. Pinning it is refused: it names no item.
        self.assertEqual([item["title"] for item in self.pin(script, self.ids["周日早餐"], False)["pinned"]], ["入冬换面霜"])
        self.assertEqual(self.refusal(lambda: self.pin(script, self.ids["周日早餐"])), "no_item")

    def test_a_pin_left_behind_by_an_item_removed_without_this_code_is_neither_shown_nor_counted(self):
        """The version before this feature knows nothing of pins: an item it removes leaves its pin behind."""
        script = self.script()
        extra = [self.add("第%d条" % n, "内容 %d" % n)["id"] for n in range(8)]
        for sample_id in extra:
            self.pin(script, sample_id)
        self.sql("DELETE FROM samples WHERE id = ?", (extra[0],))                 # as the code at c157f8a removes an item
        self.assertEqual([item["id"] for item in self.refs(script)["pinned"]], extra[1:])
        self.assertEqual(len(self.pin_rows()), 8)
        self.assertEqual(len(self.pin(script, self.ids["周日早餐"])["pinned"]), 8)  # its place is free
        self.assertEqual(len(self.pin_rows()), 8)                                 # and the pin left behind was cleared

    def test_deleting_a_script_removes_its_pins_and_only_its_own(self):
        a, b = self.script(title="甲"), self.script(title="乙")
        self.pin(a, self.ids["周日早餐"])
        self.pin(a, self.ids["入冬换面霜"])
        self.pin(b, self.ids["周日早餐"])
        self.assertTrue(self.store.delete_script(a["id"]))
        self.assertEqual(self.pin_rows(), [(b["id"], self.ids["周日早餐"])])
        self.assertEqual(len(self.store.list_samples()), len(OWN))                # the items themselves stay in the library
        self.assertEqual(self.refusal(lambda: self.refs(a)), "not_found")
        self.assertEqual(self.refusal(lambda: self.pin(a, self.ids["周日早餐"])), "not_found")
        self.assertEqual(self.pin_rows(), [(b["id"], self.ids["周日早餐"])])        # a pin on a script that is gone stores nothing

    def test_what_is_not_a_pin_is_refused_before_anything_is_stored(self):
        script = self.script()
        for sample_id, pinned in ((self.ids["周日早餐"], "true"), (self.ids["周日早餐"], 1), (self.ids["周日早餐"], None), (5, True), (None, True)):
            self.assertEqual(self.refusal(lambda: refs.set_pin(self.store, script["id"], sample_id, pinned)), "bad_pin", (sample_id, pinned))
        for sample_id in (GONE, "not an id", ""):
            self.assertEqual(self.refusal(lambda: self.pin(script, sample_id)), "no_item", sample_id)
        for script_id in (GONE, None, 5):
            self.assertEqual(self.refusal(lambda: refs.set_pin(self.store, script_id, self.ids["周日早餐"], True)), "not_found", script_id)
        self.assertEqual(self.pin_rows(), [])

    def test_two_windows_pinning_at_once_cannot_get_past_the_limit(self):
        script = self.script()
        extra = [self.add("第%d条" % n, "内容 %d" % n)["id"] for n in range(12)]
        for sample_id in extra[:7]:
            self.pin(script, sample_id)
        outcomes = []
        threads = [threading.Thread(target=lambda wanted=sample_id: outcomes.append(self.store.pin_sample(script["id"], wanted, refs.MAX_PINS)))
                   for sample_id in extra[7:]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(outcomes), ["full"] * 4 + ["pinned"])
        self.assertEqual(len(self.store.list_pins(script["id"])), 8)


# ---------------------------------------------------------------------------------------------- what the draft saw
class BasisTest(RefsCase):
    def draft(self, basis, **more):
        return self.store.create_script(title="未命名脚本", lines=lines(*AI_DRAFT), from_ai=True, basis=basis, **more)

    def test_the_items_the_prompt_contained_are_found_again_by_their_recorded_titles(self):
        basis = {"exemplar_titles": ["入冬换面霜", "Costco 补货"], "opening_titles": ["入冬换面霜", "周日早餐"],
                 "lessons_used": 0, "profile": "example", "learned_at": None}
        answer = self.refs(self.draft(basis))["basis"]
        self.assertEqual([(item["id"], item["roles"], item["same_title"]) for item in answer["items"]],
                         [(self.ids["入冬换面霜"], ["example", "opening"], 1), (self.ids["Costco 补货"], ["example"], 1),
                          (self.ids["周日早餐"], ["opening"], 1)])
        self.assertEqual((answer["missing"], answer["left_out"]), ([], 0))
        self.assertEqual(set(answer["items"][0]), ITEM_KEYS | {"roles", "same_title"})

    def test_what_a_real_draft_prompt_held_is_what_is_listed(self):
        """The basis is the one ``pipeline`` writes down when it builds a prompt, and the items found are in that prompt."""
        brief = pipeline.draft_brief(self.store, notes="想拍入冬换面霜", target_sec=60, video_format=pipeline.VIDEO_FORMATS[0], keywords=["面霜"])
        script = self.store.create_script(notes="想拍入冬换面霜", lines=lines(*AI_DRAFT), from_ai=True, keywords=["面霜"], basis=brief["basis"])
        listed = self.refs(script)["basis"]
        self.assertEqual([item["title"] for item in listed["items"] if "example" in item["roles"]], brief["basis"]["exemplar_titles"])
        self.assertEqual([item["title"] for item in listed["items"] if "opening" in item["roles"]],
                         [title for title in brief["basis"]["exemplar_titles"] if title in brief["basis"]["opening_titles"]])
        self.assertGreaterEqual(len(listed["items"]), 4)
        self.assertEqual(listed["missing"], [])
        texts = {sample["id"]: sample["text"] for sample in self.store.list_samples()}
        for item in listed["items"]:
            if "example" in item["roles"]:
                self.assertIn(texts[item["id"]], brief["user"])                   # the prompt did hold this item's text

    def test_a_title_no_item_carries_any_more_is_said_to_be_missing(self):
        script = self.draft({"exemplar_titles": ["入冬换面霜", "周日早餐", "Costco 补货"], "opening_titles": ["周日早餐"]})
        self.store.delete_sample(self.ids["周日早餐"])                                         # removed since
        self.store.update_sample(self.ids["入冬换面霜"], {"title": "入冬换面霜（改了名）"})      # renamed since
        answer = self.refs(script)["basis"]
        self.assertEqual([item["title"] for item in answer["items"]], ["Costco 补货"])
        self.assertEqual(answer["missing"], [{"title": "入冬换面霜", "roles": ["example"]}, {"title": "周日早餐", "roles": ["example", "opening"]}])

    def test_a_title_several_items_carry_is_shown_once_with_the_count(self):
        twin = self.add("入冬换面霜", "另一篇同名的", 5)
        self.add("入冬换面霜", "同名的商单", 5, is_sponsored=True)
        self.add("入冬换面霜", "同名的转写", kind="video")
        answer = self.refs(self.draft({"exemplar_titles": ["入冬换面霜"], "opening_titles": []}))["basis"]
        # The prompt held non-sponsored, confirmed material only: of the two that qualify, the newer is shown, and the count says so.
        self.assertEqual([(item["id"], item["same_title"], item["is_sponsored"], item["wording"]) for item in answer["items"]],
                         [(twin["id"], 4, False, "own")])

    def test_a_script_written_by_hand_or_a_draft_without_a_record_has_no_such_list(self):
        self.assertIsNone(self.refs(self.script())["basis"])
        self.assertIsNone(self.refs(self.store.create_script(title="面霜", lines=lines("今天"), from_ai=True))["basis"])
        # A basis is kept for an AI draft only (Store.create_script): handed over with a hand-written script it changes nothing.
        by_hand = self.store.create_script(title="面霜", lines=lines("今天"), basis={"exemplar_titles": ["周日早餐"]})
        self.assertIsNone(self.refs(by_hand)["basis"])
        # A draft whose prompt held none of the creator's material has the list, empty.
        self.assertEqual(self.refs(self.draft({"exemplar_titles": [], "opening_titles": [], "profile": "example"}))["basis"],
                         {"items": [], "missing": [], "left_out": 0})

    def test_only_a_fixed_number_of_recorded_titles_is_looked_up(self):
        titles = ["第%d条" % n for n in range(30)]
        for title in titles:
            self.add(title, "内容")
        answer = self.refs(self.draft({"exemplar_titles": titles, "opening_titles": titles[::-1]}))["basis"]
        self.assertEqual(([item["title"] for item in answer["items"]], answer["left_out"]), (titles[:refs.BASIS_MAX], 30 - refs.BASIS_MAX))

    def test_a_basis_that_is_not_what_it_should_be_is_read_without_failing(self):
        for basis, found in (({"exemplar_titles": "入冬换面霜"}, []), ({}, []), ({"opening_titles": None, "exemplar_titles": None}, []),
                             ({"exemplar_titles": [None, 5, {"a": 1}, ["x"], "周日早餐"], "opening_titles": 7}, ["周日早餐"])):
            answer = self.refs(self.draft(basis))["basis"]
            self.assertEqual(([item["title"] for item in answer["items"]], answer["missing"]), (found, []), basis)


# ---------------------------------------------------------------------------------------------- on the same topic
class RelatedTest(RefsCase):
    def test_the_measure_and_the_threshold_are_those_of_the_trends_page(self):
        keywords = ["面霜", "秋冬护肤", "Costco"]
        answer = self.related(self.script(keywords=keywords))
        self.assertEqual(answer["terms"], [{"text": keyword, "from": "keyword"} for keyword in keywords])
        samples = self.store.list_samples()
        by_the_trends_page = {s["id"] for s in samples if any(trends.relevance(k, trends._Hay(s)) >= trends.RELEVANT_AT for k in keywords)}
        self.assertEqual(by_the_trends_page, {s["id"] for s in samples if trends.is_on_topic(s, keywords)})
        self.assertEqual({item["id"] for item in answer["items"]}, by_the_trends_page)
        self.assertEqual(by_the_trends_page, {self.ids["入冬换面霜"], self.ids["面霜空瓶记"], self.ids["Costco 补货"]})
        self.assertEqual((answer["needs_terms"], answer["found"], answer["listed_above"]), (False, 3, 0))
        self.assertEqual(self.refs(self.script())["limits"]["relevant_at"], trends.RELEVANT_AT)

    def test_next_to_each_item_stands_what_was_found_in_it(self):
        found = {item["title"]: item["why"] for item in self.related(self.script(keywords=["面霜", "秋冬护肤"]))["items"]}
        self.assertEqual(found["面霜空瓶记"], [
            {"term": "面霜", "from": "keyword", "count": 5, "in_title": True, "pieces": ["面霜"], "pieces_found": 1, "of": 1},
            {"term": "秋冬护肤", "from": "keyword", "count": 1, "in_title": False, "pieces": ["秋冬", "冬护", "护肤"], "pieces_found": 3, "of": 3}])
        self.assertEqual(found["入冬换面霜"], [
            {"term": "面霜", "from": "keyword", "count": 3, "in_title": True, "pieces": ["面霜"], "pieces_found": 1, "of": 1}])
        # The counts can be checked by eye, and are checked here: how often the word stands in the title and the text.
        for title, text, _ in OWN[:2]:
            self.assertEqual(found[title][0]["count"], (title + text).count("面霜"), title)

    def test_a_keyword_that_is_not_there_as_written_counts_by_its_pieces(self):
        apart = self.add("护肤流程", "秋冬的时候皮肤干 / 我的护肤步骤很简单")          # 秋冬 and 护肤, never 秋冬护肤
        one_piece = self.add("秋冬穿搭", "秋冬的大衣怎么选")                         # one piece of three: under the threshold
        found = {item["id"]: item["why"] for item in self.related(self.script(keywords=["秋冬护肤"]))["items"]}
        self.assertEqual(found[apart["id"]], [{"term": "秋冬护肤", "from": "keyword", "count": 0, "in_title": False,
                                               "pieces": ["秋冬", "护肤"], "pieces_found": 2, "of": 3}])
        self.assertNotIn(one_piece["id"], found)
        self.assertEqual(sorted(found), sorted([apart["id"], self.ids["面霜空瓶记"]]))
        self.assertAlmostEqual(trends.relevance("秋冬护肤", trends._Hay(apart)), 2 / 3)
        self.assertLess(trends.relevance("秋冬护肤", trends._Hay(one_piece)), trends.RELEVANT_AT)

    def test_the_title_is_read_in_its_words_and_english_as_whole_words(self):
        gym = self.add("健身日记", "今天去 gym 练腿")
        self.add("体操比赛", "看了一场 gymnastics 比赛")                              # gym is not counted in gymnastics
        script = self.store.create_script(title="周末 Gym 打卡！", lines=lines("今天"), keywords=[])
        answer = self.related(script)
        self.assertEqual(answer["terms"], [{"text": "周末", "from": "title"}, {"text": "Gym", "from": "title"}, {"text": "打卡", "from": "title"}])
        self.assertEqual([(item["id"], item["why"]) for item in answer["items"]], [
            (self.ids["Costco 补货"], [{"term": "周末", "from": "title", "count": 1, "in_title": False, "pieces": ["周末"], "pieces_found": 1, "of": 1}]),
            (gym["id"], [{"term": "Gym", "from": "title", "count": 1, "in_title": False, "pieces": ["gym"], "pieces_found": 1, "of": 1}])])
        # A word of the title that is a keyword already is measured once, as the keyword; at most six words are taken.
        self.assertEqual(refs.terms({"title": "面霜 分享", "keywords": ["面霜"]}), [{"text": "面霜", "from": "keyword"}, {"text": "分享", "from": "title"}])
        many = refs.terms({"title": "一号 二号 三号 四号 五号 六号 七号 八号", "keywords": []})
        self.assertEqual([term["text"] for term in many], ["一号", "二号", "三号", "四号", "五号", "六号"])
        self.assertEqual(len(many), refs.TITLE_PARTS_MAX)
        # A title with no space in it is one word, and is measured whole like a keyword.
        self.assertEqual(refs.terms({"title": "入冬换面霜", "keywords": []}), [{"text": "入冬换面霜", "from": "title"}])

    def test_with_no_keyword_and_no_usable_title_nothing_is_listed_and_the_answer_says_so(self):
        for title in list(server.UNTITLED.values()) + ["未命名", "Untitled", "   ", "！！！", "7", "a", "的", "2026"]:
            script = self.store.create_script(title=title, lines=lines("今天聊聊面霜"), keywords=[])
            answer = self.related(script)
            self.assertEqual((answer["needs_terms"], answer["terms"], answer["items"], answer["found"]), (True, [], [], 0), title)
        for placeholder in server.UNTITLED.values():
            self.assertIn(placeholder, refs.PLACEHOLDER_TITLES)
        emptied = self.store.update_script(self.script(title="面霜", keywords=[])["id"], {"title": ""})     # the title field was cleared in the editor
        self.assertIn(emptied["title"], refs.PLACEHOLDER_TITLES)
        self.assertTrue(self.related(emptied)["needs_terms"])
        signs = self.store.create_script(title="未命名脚本", lines=lines("今天"), keywords=["！！", "……"])     # keywords with nothing to measure in them
        self.assertEqual((signs["keywords"], self.related(signs)["needs_terms"]), (["！！", "……"], True))
        # With something to measure by and nothing found, the list is empty and nothing is said to be missing.
        nothing = self.related(self.script(keywords=["滑雪"]))
        self.assertEqual((nothing["needs_terms"], nothing["items"], [term["text"] for term in nothing["terms"]]), (False, [], ["滑雪"]))

    def test_an_item_listed_above_is_not_repeated_and_the_scripts_own_item_is_no_reference_for_it(self):
        draft = self.store.create_script(title="未命名脚本", lines=lines("综上所述这款面霜非常轻盈"), from_ai=True, keywords=["面霜"],
                                         basis={"exemplar_titles": ["入冬换面霜"], "opening_titles": []})
        self.pin(draft, self.ids["面霜空瓶记"])
        another = self.add("面霜回购", "这款面霜回购了", 10)
        posted.mark(self.store, draft["id"], {"likes": 5})                        # the script's own item holds 面霜 as well
        answer = self.refs(draft)
        self.assertEqual([item["id"] for item in answer["related"]["items"]], [another["id"]])
        self.assertEqual((answer["related"]["found"], answer["related"]["listed_above"]), (1, 2))
        self.assertEqual(([item["title"] for item in answer["pinned"]], [item["title"] for item in answer["basis"]["items"]]),
                         (["面霜空瓶记"], ["入冬换面霜"]))
        # What was found is said next to an item wherever it is listed.
        self.assertEqual(answer["pinned"][0]["why"][0]["count"], 5)
        # A search still finds the script's own item, and says that it is.
        own = [item for item in self.find("综上所述", draft)["items"]]
        self.assertEqual([(item["of_this_script"], item["title"]) for item in own], [(True, "未命名脚本")])
        self.assertFalse(self.find("综上所述", self.script())["items"][0]["of_this_script"])

    def test_a_fixed_number_is_listed_and_the_answer_says_how_many_there_are(self):
        for n in range(20):
            self.add("面霜笔记%d" % n, "面霜" * (n + 1))
        answer = self.related(self.script())
        self.assertEqual((len(answer["items"]), answer["found"]), (refs.RELATED_MAX, 22))
        self.assertEqual([item["title"] for item in answer["items"]], ["面霜笔记%d" % n for n in range(19, 11, -1)])


class OrderTest(RefsCase):
    material = ()

    def test_the_order_is_read_off_counts_the_panel_shows(self):
        made = [
            ("只有标题里的词", "换季 的 笔记", None),                 # no keyword, both words of the title
            ("无关的一篇", "今天去超市", 999),
            ("一个词没数据旧", "面霜一下", None),
            ("一个词没数据新", "面霜一趟", None),
            ("一个词提得少数据差", "面霜一回", 5),
            ("一个词提得少数据好", "面霜一次", 900),
            ("一个词提得多", "面霜面霜面霜面霜", None),
            ("一个词加标题词", "面霜 换季用", None),
            ("两个词都有", "面霜和精华", None),
        ]
        for title, text, likes in made:
            self.add(title, text, likes)
        script = self.store.create_script(title="换季 笔记", lines=lines("今天"), keywords=["面霜", "精华"])
        answer = self.related(script)
        self.assertEqual([item["title"] for item in answer["items"]],
                         ["两个词都有",                      # more keywords found
                          "一个词加标题词",                  # then more words of the title
                          "一个词提得多",                    # then more mentions
                          "一个词提得少数据好",              # then the better result
                          "一个词提得少数据差",
                          "一个词没数据新",                  # then the newer item; one without numbers after those with
                          "一个词没数据旧",
                          "只有标题里的词"])                 # a keyword counts before any word of the title
        self.assertEqual(answer["found"], 8)
        counted = [(sum(1 for w in item["why"] if w["from"] == "keyword"), sum(1 for w in item["why"] if w["from"] == "title"),
                    sum(w["count"] for w in item["why"])) for item in answer["items"]]
        self.assertEqual(counted, [(2, 0, 2), (1, 1, 2), (1, 0, 4), (1, 0, 1), (1, 0, 1), (1, 0, 1), (1, 0, 1), (0, 2, 2)])


# ---------------------------------------------------------------------------------------------- search
class SearchTest(RefsCase):
    def titles(self, query):
        return [item["title"] for item in self.find(query)["items"]]

    def test_a_query_is_matched_as_it_was_typed(self):
        self.assertEqual(self.titles("面霜"), ["面霜空瓶记", "入冬换面霜"])
        self.assertEqual(self.titles("换了一款面霜"), ["入冬换面霜"])
        self.assertEqual(self.titles("面霜 入冬"), [])                # one string, not two words: nothing holds 面霜入冬
        self.assertEqual(self.titles("入冬 换 面霜"), ["入冬换面霜"])    # spaces make no difference
        self.assertEqual(self.titles("霜面"), [])
        self.assertEqual(self.titles("TRADER joe"), ["Trader Joe's 那天"])
        self.assertEqual(self.titles("ostc"), ["Costco 补货"])        # part of a word is found, as in any search
        self.assertEqual(self.titles("toner 也换了"), ["入冬换面霜"])
        self.assertEqual(self.titles("买了小笼包"), ["Trader Joe's 那天"])

    def test_it_says_what_was_found_and_where(self):
        answer = self.find("面霜")
        self.assertEqual([(item["title"], item["found"]) for item in answer["items"]],
                         [("面霜空瓶记", {"in_title": True, "count": 4}), ("入冬换面霜", {"in_title": True, "count": 2})])
        self.assertEqual((answer["query"], answer["found"], answer["limit"]), ("面霜", 2, refs.SEARCH_MAX))
        self.assertEqual(set(answer["items"][0]), ITEM_KEYS | {"found"})
        for item in answer["items"]:
            excerpt = item["excerpt"]
            self.assertEqual(excerpt["text"][excerpt["mark"][0]:excerpt["mark"][1]], "面霜")
        self.add("只在标题里的词", "正文里没有")
        only_title = self.find("标题里的词")["items"][0]
        self.assertEqual((only_title["found"], only_title["excerpt"]["mark"], only_title["excerpt"]["text"]),
                         ({"in_title": True, "count": 0}, None, "正文里没有"))
        spaced = self.find("  trader   JOE'S ")["items"][0]["excerpt"]
        self.assertEqual(spaced["text"][spaced["mark"][0]:spaced["mark"][1]], "Trader Joe's")

    def test_anything_that_is_found_can_be_pinned(self):
        script = self.script()
        sponsored = self.add("商单面膜", "合作的面膜", 80, is_sponsored=True)
        waiting = self.add("面膜开箱", "今天开箱一款面膜", kind="video")
        draft = self.store.create_script(title="面膜测评", lines=lines(*AI_DRAFT), from_ai=True)
        posted.mark(self.store, draft["id"], {"likes": 9})
        for query, wanted in (("合作的面膜", sponsored["id"]), ("开箱一款面膜", waiting["id"]), ("面膜测评", self.store.get_posted_sample(draft["id"])["id"])):
            found = self.find(query, script)["items"]
            self.assertEqual([(item["id"], item["pinned"]) for item in found], [(wanted, False)])
            self.pin(script, wanted)
            self.assertEqual([(item["id"], item["pinned"]) for item in self.find(query, script)["items"]], [(wanted, True)])
        self.assertEqual(len(self.refs(script)["pinned"]), 3)

    def test_a_fixed_number_is_listed_those_with_it_in_the_title_first(self):
        for n in range(12):
            self.add("第%d篇" % n, "这里提到" + "小笼包" * (n + 1))
        self.add("小笼包测评", "正文不提")
        answer = self.find("小笼包")
        self.assertEqual((len(answer["items"]), answer["found"]), (refs.SEARCH_MAX, 15))
        self.assertEqual(answer["items"][0]["title"], "小笼包测评")
        self.assertEqual([item["found"]["count"] for item in answer["items"][1:]], [12, 11, 10, 9, 8, 7, 6])

    def test_an_empty_query_finds_nothing_and_a_long_one_is_refused(self):
        for query in ("", "   ", "\n", None, 5, ["面霜"]):
            self.assertEqual(self.find(query), {"query": query.strip() if isinstance(query, str) else "", "items": [], "found": 0, "limit": refs.SEARCH_MAX})
        self.assertEqual(self.find("x" * refs.QUERY_CHARS)["found"], 0)
        self.assertEqual(self.refusal(lambda: self.find("x" * (refs.QUERY_CHARS + 1))), "query_too_long")
        self.assertEqual(self.refusal(lambda: refs.search(self.store, GONE, "面霜")), "not_found")


# ---------------------------------------------------------------------------------------------- what an item says
class ItemTest(RefsCase):
    def test_an_item_says_how_it_came_in_whether_it_was_sponsored_and_how_it_did(self):
        self.store.create_sample(title="字幕来的", text="今天聊面膜", kind="subtitle", metrics={"views": 2000, "likes": 30, "collects": 10, "comments": 5})
        video = self.store.create_sample(title="视频来的", text="今天聊面膜", kind="video")
        self.store.update_sample(video["id"], {"needs_review": False})
        self.add("商单来的", "合作的面膜", 80, is_sponsored=True)
        self.add("手打的", "自己写的面膜心得")
        found = {item["title"]: item for item in self.find("面膜")["items"]}
        self.assertEqual({title: (item["kind"], item["is_sponsored"]) for title, item in found.items()},
                         {"字幕来的": ("subtitle", False), "视频来的": ("video", False), "商单来的": ("script", True), "手打的": ("script", False)})
        # Her usual result: the middle value of 45, 100, 150, 300 and 450, the non-sponsored videos with numbers.
        subtitle = found["字幕来的"]
        self.assertEqual((subtitle["views"], subtitle["likes"], subtitle["collects"], subtitle["comments"]), (2000, 30, 10, 5))
        self.assertEqual((subtitle["has_numbers"], subtitle["interactions"], subtitle["times_usual"]), (True, 45, 0.3))
        self.assertEqual((found["商单来的"]["interactions"], found["商单来的"]["times_usual"]), (80, 0.5))
        for title in ("视频来的", "手打的"):                         # no numbers: said as that, never as zero
            self.assertEqual((found[title]["has_numbers"], found[title]["interactions"], found[title]["times_usual"], found[title]["likes"]),
                             (False, None, None, None), title)
        self.assertTrue(all(item["posted"] is None and item["wording"] == "own" and set(item) == ITEM_KEYS | {"found"} for item in found.values()))

    def test_the_usual_result_is_the_one_the_openings_use(self):
        script = self.script()
        self.add("没有互动的一条", "今天什么都没发生", 0)
        self.add("商单不算", "合作的内容", 99999, is_sponsored=True)
        answer = self.refs(script)
        library = hooks.library(self.store.list_samples())
        self.assertEqual((answer["usual_interactions"], answer["videos_with_numbers"]), (library["usual_interactions"], library["videos_with_numbers"]))
        self.assertEqual((answer["usual_interactions"], answer["videos_with_numbers"]), (150.0, 5))
        self.assertEqual(len(library["openers"]), 6)
        for opener in library["openers"]:                           # each of her openings, with its video's result
            self.pin(script, opener["sample_id"])
        shown = {item["id"]: item for item in self.refs(script)["pinned"]}
        for opener in library["openers"]:
            item = shown[opener["sample_id"]]
            self.assertEqual((item["opening"], item["interactions"], item["times_usual"]), (opener["opening"], opener["interactions"], opener["times_usual"]))
        # The same steps on libraries where the middle value is of no use, or there is none.
        made = lambda *likes: [{"id": str(n), "title": "t", "text": "今天聊聊", "is_sponsored": False, "likes": value, "collects": None, "comments": None}
                               for n, value in enumerate(likes)]                    # noqa: E731
        for samples in (made(), made(None, None), made(0), made(0, 0, 5), made(10, 20), made(7),
                        made(5) + [dict(made(900)[0], id="s", is_sponsored=True)]):
            library = hooks.library(samples)
            self.assertEqual(refs._usual(samples), (library["usual_interactions"], library["videos_with_numbers"]), samples)

    def test_a_posted_script_says_how_many_days_after_posting_its_numbers_were_read(self):
        writing = self.script()
        was_posted = self.store.create_script(title="面膜测评", lines=lines("入冬以后我换了一款面膜嘛", "所以我真的很推荐嘛"))
        with mock.patch.object(posted, "today", return_value=datetime.date(2026, 11, 16)):
            posted.mark(self.store, was_posted["id"], {"likes": 600, "posted_on": "2026-11-10", "metrics_on": "2026-11-16"})
        item = self.one("面膜测评", writing)
        self.assertEqual(item["posted"], {"posted_on": "2026-11-10", "metrics_on": "2026-11-16", "read_after_days": 6})
        self.assertEqual(item["posted"]["read_after_days"], corpus.read_after_days(self.store.get_posted_sample(was_posted["id"])))
        self.assertEqual((item["interactions"], item["of_this_script"]), (600, False))
        self.assertTrue(self.one("面膜测评", was_posted)["of_this_script"])
        without = self.store.create_script(title="面膜回购", lines=lines("这款面膜回购了"))
        posted.mark(self.store, without["id"], {})                                # posted, and no numbers entered yet
        item = self.one("面膜回购", writing)
        self.assertEqual((item["has_numbers"], item["posted"]["metrics_on"], item["posted"]["read_after_days"]), (False, None, None))

    def test_its_opening_and_an_excerpt_around_what_was_found_first(self):
        long_text = "开头的话嘛 / 今天想聊一个东西。" + "铺垫" * 200 + "这里终于说到面霜了" + "收尾" * 300
        self.add("很长的一篇", long_text)
        item = next(item for item in self.related(self.script())["items"] if item["title"] == "很长的一篇")
        self.assertEqual(item["opening"], hooks.describe(long_text)["opening"])
        excerpt = item["excerpt"]
        self.assertEqual(len(excerpt["text"]), refs.EXCERPT_CHARS)
        self.assertEqual(excerpt["text"], long_text[excerpt["start"]:excerpt["start"] + refs.EXCERPT_CHARS])
        self.assertEqual(excerpt["start"] + excerpt["mark"][0], long_text.index("面霜"))
        self.assertEqual(excerpt["text"][excerpt["mark"][0]:excerpt["mark"][1]], "面霜")
        self.assertEqual((excerpt["mark"][0], excerpt["cut_before"], excerpt["cut_after"]), (refs.EXCERPT_LEAD, True, True))
        # Found near the start, or not in the text at all: the excerpt is the start of the text.
        near = next(item for item in self.related(self.script())["items"] if item["title"] == "入冬换面霜")["excerpt"]
        self.assertEqual((near["start"], near["cut_before"], near["cut_after"], near["text"]), (0, False, False, OWN[0][1]))
        self.assertEqual(near["text"][near["mark"][0]:near["mark"][1]], "面霜")
        # English is found as whole words, whatever the capitals, the spaces and the kind of apostrophe.
        self.add("另一家超市", "先去了 gymnastics 馆 / 然后去 TRADER   Joe\u2019s 买菜")
        english = {item["title"]: item for item in self.related(self.script(keywords=["Trader Joe's"]))["items"]}
        self.assertEqual(sorted(english), ["Trader Joe's 那天", "另一家超市"])
        marked = english["另一家超市"]["excerpt"]
        self.assertEqual(marked["text"][marked["mark"][0]:marked["mark"][1]], "TRADER   Joe\u2019s")
        self.assertEqual((english["另一家超市"]["why"][0]["count"], english["Trader Joe's 那天"]["why"][0]["count"]), (1, 2))

    def test_the_whole_text_is_read_in_parts_of_a_stated_size(self):
        text = "".join("第%d句话嘛。" % n for n in range(3500))
        self.assertGreater(len(text), 3 * refs.TEXT_PART_CHARS)
        item = self.add("很长的文字", text)
        parts, start = [], 0
        while start is not None:
            part = refs.text_part(self.store, item["id"], start)
            self.assertLessEqual(len(part["text"]), refs.TEXT_PART_CHARS)
            self.assertEqual((part["id"], part["start"], part["chars"], part["limit"], part["wording"]),
                             (item["id"], start, len(text), refs.TEXT_PART_CHARS, "own"))
            parts.append(part["text"])
            start = part["next"]
        self.assertEqual(("".join(parts), len(parts)), (text, -(-len(text) // refs.TEXT_PART_CHARS)))
        short = refs.text_part(self.store, self.ids["周日早餐"])
        self.assertEqual((short["text"], short["next"], short["start"]), (OWN[3][1], None, 0))
        self.assertEqual(refs.text_part(self.store, self.ids["周日早餐"], "99999")["text"], "")      # past the end: nothing, and no error
        for start in ("-1", "1.5", "abc", "", " 5", None, True, "1" * 10):
            self.assertEqual(self.refusal(lambda: refs.text_part(self.store, item["id"], start)), "bad_start", start)
        for sample_id in (GONE, None, 5):
            self.assertEqual(self.refusal(lambda: refs.text_part(self.store, sample_id)), "no_item", sample_id)


# ---------------------------------------------------------------------------------------------- the honesty rule
class HonestyTest(RefsCase):
    def posted_draft(self, changed=None, title="面膜测评", **numbers):
        """A script that began as an AI draft, edited or not, and marked as posted. Returns its library item."""
        draft = self.store.create_script(title=title, lines=lines(*AI_DRAFT), from_ai=True)
        if changed:
            now = [dict(line) for line in draft["lines"]]
            for index, text in changed.items():
                now[index]["spoken_script_zh"] = text
            self.store.update_script(draft["id"], {"lines": now})
        posted.mark(self.store, draft["id"], numbers or {"likes": 900})
        return self.store.get_posted_sample(draft["id"])

    def test_an_unconfirmed_machine_transcript_is_shown_for_how_it_did_and_labelled_until_she_confirms_it(self):
        video = self.store.create_sample(title="面膜开箱", text="今天开箱一款面膜 然后我们试一下", kind="video", metrics={"likes": 700})
        item = self.one("面膜开箱")
        # Its numbers count towards her usual result like any video's: the middle value of 100, 150, 300, 450 and 700.
        self.assertEqual(hooks.library(self.store.list_samples())["usual_interactions"], 300.0)
        self.assertEqual((item["wording"], item["kind"], item["interactions"], item["times_usual"]), ("transcript_waiting", "video", 700, 2.3))
        self.assertEqual(refs.text_part(self.store, video["id"])["wording"], "transcript_waiting")
        self.assertNotIn(video["id"], [opener["sample_id"] for opener in hooks.library(self.store.list_samples())["openers"]])
        self.assertEqual(pipeline.confirmed_samples([self.store.get_sample(video["id"])]), [])       # the same rule as everywhere else
        self.store.update_sample(video["id"], {"needs_review": False})
        self.assertEqual((self.one("面膜开箱")["wording"], refs.text_part(self.store, video["id"])["wording"]), ("own", "own"))

    def test_a_posted_script_she_has_not_confirmed_is_labelled_and_an_ais_opening_is_said_to_be_one(self):
        stored = self.posted_draft()
        self.assertTrue(corpus.waits_as_posted_script(stored))
        item = self.one("面膜测评")
        self.assertEqual((item["wording"], item["opening"], item["opening_by_ai"], item["interactions"]), ("posted_waiting", AI_OPENING, True, 900))
        self.assertEqual(refs.text_part(self.store, stored["id"])["wording"], "posted_waiting")
        # She confirms the wording: it counts as hers. The opening is still a sentence an AI wrote, and is still said to be one.
        self.store.update_sample(stored["id"], {"needs_review": False})
        item = self.one("面膜测评")
        self.assertEqual((item["wording"], item["opening_by_ai"]), ("own", True))
        self.assertEqual(item["opening_by_ai"], hooks.opening_by_ai(self.store.get_sample(stored["id"]), item["opening"]))
        self.assertEqual([opener["sample_id"] for opener in hooks.library(self.store.list_samples())["written_by_ai"]], [stored["id"]])

    def test_an_opening_she_rewrote_is_not_called_the_ais_and_her_own_items_carry_no_label(self):
        self.posted_draft({0: HER_FIRST_LINE})
        item = self.one("面膜测评")
        self.assertEqual((item["wording"], item["opening_by_ai"]), ("posted_waiting", False))        # the wording still waits for her
        self.assertTrue(item["opening"].startswith("入冬以后我换了一款面霜嘛"))
        for own in self.find("小笼包")["items"]:
            self.assertEqual((own["wording"], own["opening_by_ai"], own["posted"]), ("own", False, None))

    def test_what_is_kept_for_counting_never_reaches_a_page(self):
        stored = self.posted_draft({0: HER_FIRST_LINE})                           # the AI's first line is now only in what was kept
        self.assertIn(AI_OPENING, json.dumps(self.store.get_sample(stored["id"])["ai_draft"], ensure_ascii=False))
        self.assertTrue(self.store.get_sample(stored["id"])["snapshot"])
        script = self.store.create_script(title="未命名脚本", lines=lines("今天聊聊面膜"), from_ai=True, keywords=["面霜", "推荐"],
                                          basis={"exemplar_titles": ["面膜测评", "周日早餐"], "opening_titles": ["面膜测评"]})
        answers = [self.pin(script, stored["id"]), self.refs(script), self.find("推荐", script), self.find("面膜测评", script),
                   refs.text_part(self.store, stored["id"])]
        shown = 0
        for answer in answers:
            self.assertEqual(keys_of(answer) & {"ai_draft", "ai_json", "snapshot", "text_cut", "script_id" if "id" in answer else "", "origin"}, set())
            self.assertNotIn(AI_OPENING, json.dumps(answer, ensure_ascii=False))
            shown += json.dumps(answer, ensure_ascii=False).count(stored["id"])
        self.assertGreaterEqual(shown, 6)                                         # the item was in every one of those answers
        # Every item is made of the same named fields, wherever it is listed.
        answer = self.refs(script)
        for item in answer["pinned"] + answer["related"]["items"]:
            self.assertEqual(set(item), ITEM_KEYS)
        for item in answer["basis"]["items"]:
            self.assertEqual(set(item), ITEM_KEYS | {"roles", "same_title"})


# ---------------------------------------------------------------------------------------------- bounds
class BoundsTest(unittest.TestCase):
    """A made-up library as large as one can get: hundreds of items, some as long as an item may be, every one
    of them on the script's topic, with titles thousands of characters long."""

    ITEMS = 300
    HUGE = 4                    # of them, as long as an item may be (MAX_SAMPLE_CHARS)
    FILLER = "今天聊聊面霜和秋冬护肤嘛 / 然后说说 Costco 的小笼包 / "
    LONG_TITLE = "面霜和秋冬护肤的长标题" * 400

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = Store(cls.tmp)
        huge = (cls.FILLER * (MAX_SAMPLE_CHARS // len(cls.FILLER) + 1))[:MAX_SAMPLE_CHARS]
        rows = [("%012x" % (n + 1), "%d 号：%s" % (n, cls.LONG_TITLE), huge if n < cls.HUGE else cls.FILLER * 40, int(n % 7 == 0), n,
                 ("script", "subtitle", "video")[n % 3], int(n % 3 == 2), 1000.0 + n, 1000.0 + n) for n in range(cls.ITEMS)]
        db = sqlite3.connect(str(cls.store.db_path))
        try:
            db.executemany("INSERT INTO samples (id, title, text, is_sponsored, likes, kind, needs_review, created_at, updated_at)"
                           " VALUES (?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
        finally:
            db.close()
        cls.item_ids = [row[0] for row in rows]
        recorded = [row[1] for row in rows[:4]] + ["已经不在的 %d 号：%s" % (n, cls.LONG_TITLE) for n in range(196)]
        made = cls.store.create_script(
            title="面霜", lines=lines("今天聊聊面霜"), from_ai=True,
            keywords=["面霜", "秋冬护肤", "Costco", "小笼包", "今天", "聊聊", "然后", "说说"],
            basis={"exemplar_titles": recorded, "opening_titles": recorded[::-1]})
        # A title this long can no longer be saved (MAX_SCRIPT_TITLE_CHARS), but a script saved by an earlier
        # version may carry one: it is written the way that version left it, past the limit.
        db = sqlite3.connect(str(cls.store.db_path))
        try:
            db.execute("UPDATE scripts SET title = ? WHERE id = ?", ("面霜 秋冬 护肤 笔记 分享 日常 好物 推荐 清单 合集 " * 500, made["id"]))
            db.commit()
        finally:
            db.close()
        cls.script = cls.store.get_script(made["id"])
        for sample_id in cls.item_ids[:refs.MAX_PINS]:
            refs.set_pin(cls.store, cls.script["id"], sample_id, True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    @staticmethod
    def size(answer):
        return len(json.dumps(answer, ensure_ascii=False).encode("utf-8"))

    def test_the_library_is_what_it_is_meant_to_be(self):
        self.assertEqual(len(self.store.list_sample_heads(10, 10)), self.ITEMS)
        whole = self.store.get_sample(self.item_ids[0])
        self.assertEqual((len(whole["text"]), len(whole["title"])), (MAX_SAMPLE_CHARS, len("0 号：") + len(self.LONG_TITLE)))
        self.assertEqual(len(self.script["keywords"]), 8)
        self.assertGreater(len(self.script["title"]), 10000)

    def test_what_the_panel_is_sent_is_bounded_in_size_whatever_the_library_holds(self):
        answer = refs.for_script(self.store, self.script["id"])
        self.assertLess(self.size(answer), 200000)                                # measured: see the numbers in the report of this change
        self.assertEqual(answer["library"], self.ITEMS)
        self.assertEqual((len(answer["pinned"]), len(answer["related"]["items"])), (refs.MAX_PINS, refs.RELATED_MAX))
        self.assertEqual((len(answer["basis"]["items"]), len(answer["basis"]["missing"]), answer["basis"]["left_out"]), (4, 4, 192))
        self.assertEqual(answer["related"]["found"] + answer["related"]["listed_above"], self.ITEMS)
        self.assertLessEqual(len(answer["related"]["terms"]), 8 + refs.TITLE_PARTS_MAX)
        self.assertTrue(all(len(term["text"]) <= refs.TITLE_READ_CHARS for term in answer["related"]["terms"]))
        items = answer["pinned"] + answer["basis"]["items"] + answer["related"]["items"]
        self.assertEqual(len(items), 20)
        for item in items:
            self.assertEqual((len(item["title"]), item["title_cut"]), (refs.TITLE_CHARS, True))
            self.assertLessEqual(len(item["excerpt"]["text"]), refs.EXCERPT_CHARS)
            self.assertLessEqual(len(item["opening"]), hooks.OPENING_MAX_CHARS)
            self.assertLessEqual(len(item["why"]), 8 + refs.TITLE_PARTS_MAX)
            self.assertTrue(all(len(found["pieces"]) <= refs.PIECES_SHOWN for found in item["why"]))
            self.assertLess(self.size(item), 8000)
        for missing in answer["basis"]["missing"]:
            self.assertEqual(len(missing["title"]), refs.TITLE_CHARS)
        # An item whose text goes on after what was read says so; the others do not.
        self.assertEqual([item["long"] for item in answer["pinned"]], [True] * self.HUGE + [False] * (refs.MAX_PINS - self.HUGE))
        self.assertTrue(all(item["wording"] == "transcript_waiting" for item in items if item["kind"] == "video"))

    def test_a_search_and_one_part_of_a_text_are_bounded_as_well(self):
        found = refs.search(self.store, self.script["id"], "面霜")
        self.assertEqual((len(found["items"]), found["found"]), (refs.SEARCH_MAX, self.ITEMS))
        self.assertLess(self.size(found), 60000)
        part = refs.text_part(self.store, self.item_ids[0])
        self.assertEqual((len(part["text"]), part["chars"], part["next"]), (refs.TEXT_PART_CHARS, MAX_SAMPLE_CHARS, refs.TEXT_PART_CHARS))
        self.assertLess(self.size(part), 4 * refs.TEXT_PART_CHARS + 500)
        last = refs.text_part(self.store, self.item_ids[0], MAX_SAMPLE_CHARS - 10)
        self.assertEqual((len(last["text"]), last["next"]), (10, None))            # the whole text can be read to its end, part by part

    def test_every_request_looks_at_each_item_once_and_only_at_its_start(self):
        looked = []
        real_hay = trends._Hay

        class Counted(real_hay):
            __slots__ = ()

            def __init__(self, sample):
                looked.append(len(sample["text"]))
                real_hay.__init__(self, sample)

        with mock.patch.object(trends, "_Hay", Counted), \
                mock.patch.object(trends, "relevance", side_effect=trends.relevance) as measured, \
                mock.patch.object(Store, "list_sample_heads", autospec=True, side_effect=Store.list_sample_heads) as heads, \
                mock.patch.object(Store, "list_samples", autospec=True, side_effect=Store.list_samples) as everything, \
                mock.patch.object(Store, "get_sample", autospec=True, side_effect=Store.get_sample) as whole_items:
            answer = refs.for_script(self.store, self.script["id"])
            refs.search(self.store, self.script["id"], "面霜")
        terms = len(answer["related"]["terms"])
        self.assertEqual(terms, 8 + refs.TITLE_PARTS_MAX)
        self.assertEqual(len(looked), self.ITEMS)                                 # each item once
        self.assertEqual(measured.call_count, self.ITEMS * terms)                 # measured once by each term
        self.assertEqual(max(looked), refs.SCAN_CHARS)                            # and only the start of a text was read
        self.assertEqual((heads.call_count, everything.call_count), (2, 0))       # the library was read once a request, never whole
        self.assertEqual(whole_items.call_count, 0)                               # a whole item is read only for one that came from a script
        heads_read = self.store.list_sample_heads(refs.SCAN_CHARS, refs.TITLE_SCAN_CHARS)
        self.assertEqual(max(len(head["text"]) for head in heads_read), refs.SCAN_CHARS)
        self.assertEqual(max(len(head["title"]) for head in heads_read), refs.TITLE_SCAN_CHARS)
        self.assertEqual(sum(1 for head in heads_read if head["text_cut"]), self.HUGE)


class WorkTest(RefsCase):
    material = ()

    def test_the_work_grows_with_the_number_of_items_and_not_with_its_square(self):
        script = self.script(keywords=["面霜", "护肤"])
        counts = []
        for total in (40, 80, 160):
            while len(self.store.list_sample_heads(1, 1)) < total:
                self.add("第%d条" % self.clock, "今天聊聊面霜和护肤")
            with mock.patch.object(trends, "relevance", side_effect=trends.relevance) as measured:
                answer = self.refs(script)
            self.assertEqual(answer["related"]["found"], total)
            counts.append(measured.call_count)
        self.assertEqual(counts, [40 * 2, 80 * 2, 160 * 2])

    def test_what_stands_past_the_part_that_is_read_is_not_counted_and_the_item_says_so(self):
        far = self.add("很长的一条", "铺垫" * (refs.SCAN_CHARS // 2) + "到这里才提到面霜")
        near = self.add("短的一条", "一开始就提到面霜")
        answer = self.related(self.script())
        self.assertEqual([item["id"] for item in answer["items"]], [near["id"]])
        self.assertEqual([item["id"] for item in self.find("面霜")["items"]], [near["id"]])
        shown = self.one("很长的一条")
        self.assertEqual((shown["long"], self.one("短的一条")["long"]), (True, False))
        # The whole text is still there to be read, to its last character.
        self.assertTrue(refs.text_part(self.store, far["id"], refs.SCAN_CHARS)["text"].endswith("到这里才提到面霜"))

    def test_a_character_the_database_counts_differently_does_not_hide_that_a_text_goes_on(self):
        text = "面霜\x00" + "后面还有" * 10
        item = self.add("有个怪字符", text)
        head = next(head for head in self.store.list_sample_heads(5, 400) if head["id"] == item["id"])
        self.assertEqual((head["text"], head["text_cut"]), (text[:5], True))
        whole = next(head for head in self.store.list_sample_heads(len(text), 400) if head["id"] == item["id"])
        self.assertEqual((whole["text"], whole["text_cut"]), (text, False))
        self.assertEqual(refs.text_part(self.store, item["id"])["chars"], len(text))

    def test_what_stands_after_such_a_character_is_still_found_and_a_title_that_holds_one_is_found_again(self):
        """SQLite cuts text by characters only up to a NUL character. The start of an item is cut as bytes instead."""
        item = self.add("怪\x00标题", "开头\x00之后才提到面霜和小笼包")
        self.assertEqual([found["id"] for found in self.related(self.script())["items"]], [item["id"]])
        self.assertEqual([(found["id"], found["found"]) for found in self.find("小笼包")["items"]], [(item["id"], {"in_title": False, "count": 1})])
        shown = self.one("怪\x00标题")
        self.assertEqual((shown["title"], shown["excerpt"]["text"], shown["long"]), ("怪\x00标题", "开头\x00之后才提到面霜和小笼包", False))
        draft = self.store.create_script(title="未命名脚本", lines=lines("今天"), from_ai=True, basis={"exemplar_titles": ["怪\x00标题"], "opening_titles": []})
        basis = self.refs(draft)["basis"]
        self.assertEqual(([found["id"] for found in basis["items"]], basis["missing"]), ([item["id"]], []))

    def test_the_start_of_an_item_is_cut_at_a_whole_character_whatever_its_size_in_bytes(self):
        """A character takes one to four bytes. The first so-many characters are handed out, never half of one."""
        made = {"ascii": "abcdefghij", "chinese": "一二三四五六七八九十", "emoji": "😀😁😂🤣😃😄😅😆😉😊", "mixed": "a一😀b二😁c三😂d"}
        ids = {name: self.add("标题" + name, text)["id"] for name, text in made.items()}
        for chars in (0, 1, 3, 9, 10, 11, 400):
            heads = {head["id"]: head for head in self.store.list_sample_heads(chars, 2)}
            for name, text in made.items():
                head = heads[ids[name]]
                self.assertEqual((head["text"], head["text_cut"], head["title"]), (text[:chars], len(text) > chars, "标题"), (name, chars))
        long_title = self.add("😀" * 500, "正文")
        head = next(head for head in self.store.list_sample_heads(10, refs.TITLE_SCAN_CHARS) if head["id"] == long_title["id"])
        self.assertEqual(head["title"], "😀" * refs.TITLE_SCAN_CHARS)

    def test_the_place_of_what_was_found_is_counted_in_characters_also_after_an_emoji(self):
        """A page counts an emoji as two. The answer counts characters as Python does, and the page cuts by characters."""
        text = "😊😊😊 开头先说点别的嘛 ✨ 然后才聊到眼霜 / 眼霜要轻轻拍"
        self.add("眼霜小记", text)
        excerpt = self.find("眼霜")["items"][0]["excerpt"]
        self.assertEqual((excerpt["text"], excerpt["mark"]), (text, [text.index("眼霜"), text.index("眼霜") + 2]))
        self.assertEqual(excerpt["text"][excerpt["mark"][0]:excerpt["mark"][1]], "眼霜")
        as_a_page_counts = text.encode("utf-16-le")                  # two bytes for each unit a page counts
        self.assertNotEqual(as_a_page_counts[2 * excerpt["mark"][0]:2 * excerpt["mark"][1]].decode("utf-16-le"), "眼霜")
        far = "😀" * 300 + "这里说到面霜" + "😀" * 300
        many = self.add("表情很多", far)
        excerpt = next(item for item in self.related(self.script())["items"] if item["title"] == "表情很多")["excerpt"]
        self.assertEqual((len(excerpt["text"]), excerpt["text"][excerpt["mark"][0]:excerpt["mark"][1]], excerpt["start"]),
                         (refs.EXCERPT_CHARS, "面霜", far.index("面霜") - refs.EXCERPT_LEAD))
        part = refs.text_part(self.store, many["id"], 299)
        self.assertEqual((part["text"][:5], part["chars"]), ("😀这里说到", len(far)))      # a part begins at a whole character


# ---------------------------------------------------------------------------------------------- a data folder from before
class EarlierVersionTest(unittest.TestCase):
    OLD_TABLES = ("scripts", "samples", "trends", "lessons", "briefs", "ai_suggestions")
    # Columns a version after c157f8a adds to a table of an earlier database when it opens it (``_ADDED_COLUMNS``
    # in studio/store.py): how many lines a script has and how it begins, so that the list of scripts need not
    # read every line of every script.
    ADDED_SINCE = {"scripts": ["line_count", "preview"]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def sql(self, statement, args=()):
        db = sqlite3.connect(str(self.tmp / "studio.db"))
        try:
            rows = db.execute(statement, args).fetchall()
            db.commit()
            return rows
        finally:
            db.close()

    def old_database(self):
        """A database as the code at c157f8a leaves it: its tables and its index, and rows written the way that code writes them."""
        db = sqlite3.connect(str(self.tmp / "studio.db"))
        try:
            db.executescript(SCHEMA_AT_C157F8A)
            db.execute(ONE_ITEM_A_SCRIPT_AT_C157F8A)
            dump = lambda value: json.dumps(value, ensure_ascii=False)             # noqa: E731
            db.execute("INSERT INTO scripts (id, title, video_format, target_sec, notes, lines_json, post_json, ai_lines_json, keywords_json,"
                       " basis_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("aaaaaaaaaaaa", "未命名脚本", "VLOG_VOICEOVER", 60, "想拍面霜", dump(lines(HER_FIRST_LINE, *AI_DRAFT[1:])), "{}",
                        dump(lines(*AI_DRAFT)), dump(["面霜"]), dump({"exemplar_titles": ["超市那天", "面霜那篇"], "opening_titles": ["面霜那篇"],
                                                                     "lessons_used": 0, "profile": "example", "learned_at": None}), 1.0, 2.0))
            db.execute("INSERT INTO scripts (id, title, lines_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                       ("eeeeeeeeeeee", "发过的那篇", dump(lines("这款面霜我发过了")), 1.5, 2.5))
            insert = ("INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments, kind, timing_json, source_name,"
                      " needs_review, origin, script_id, posted_on, note_url, metrics_on, ai_json, snapshot, created_at, updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
            db.execute(insert, ("bbbbbbbbbbbb", "面霜那篇", OWN[0][1], 0, 2000, 150, None, None, "script", None, "", 0, "", None, None, "", None, None, "", 3.0, 4.0))
            db.execute(insert, ("cccccccccccc", "超市那天", OWN[2][1], 0, None, None, None, None, "video", None, "clip.mp4", 1, "", None, None, "", None, None, "", 5.0, 6.0))
            db.execute(insert, ("ffffffffffff", "发过的那篇", "这款面霜我发过了", 0, None, 80, None, None, "script", None, "", 1, "script", "eeeeeeeeeeee",
                                "2026-11-10", "", "2026-11-12", dump({"drafted": False, "lines": [], "proposed": []}), "abc", 7.0, 8.0))
            db.execute("INSERT INTO lessons (id, script_id, ai, final, created_at) VALUES (?,?,?,?,?)", ("dddddddddddd", "aaaaaaaaaaaa", AI_DRAFT[0], HER_FIRST_LINE, 9.0))
            db.execute("INSERT INTO ai_suggestions (script_id, text, created_at) VALUES (?,?,?)", ("aaaaaaaaaaaa", "AI 提过的一句", 10.0))
            db.execute("INSERT INTO trends (id, key, keyword, heat, created_at, updated_at) VALUES (?,?,?,?,?,?)", ("111111111111", "面霜", "面霜", 1000, 11.0, 12.0))
            db.execute("INSERT INTO briefs (id, kind, fingerprint, payload_json, created_at) VALUES (?,?,?,?,?)", ("222222222222", "draft", "f", "{}", 13.0))
            db.commit()
        finally:
            db.close()

    def tables(self):
        return sorted(row[0] for row in self.sql("SELECT name FROM sqlite_master WHERE type = 'table'"))

    def columns(self, table):
        return [row[1] for row in self.sql("PRAGMA table_info(%s)" % table)]

    def everything(self):
        """Every row of every table the earlier version made, in the columns that version made, and those columns."""
        out = {}
        for table in self.OLD_TABLES:
            made_then = [row for row in self.sql("PRAGMA table_info(%s)" % table) if row[1] not in self.ADDED_SINCE.get(table, ())]
            out[table] = (made_then, self.sql("SELECT %s FROM %s ORDER BY rowid" % (", ".join(row[1] for row in made_then), table)))
        return out

    def test_a_database_made_at_c157f8a_opens_and_works_unchanged(self):
        self.old_database()
        self.assertEqual(self.tables(), sorted(self.OLD_TABLES))
        before = self.everything()
        columns_then = {table: self.columns(table) for table in self.OLD_TABLES}
        store = Store(self.tmp)
        self.assertEqual(self.tables(), sorted(self.OLD_TABLES + (
            "script_pins", "metric_snapshots", "sample_beats", "sample_comments", "sample_sentiments", "video_plans", "sponsor_briefs",
            "autopilot_runs", "idea_sets", "plan_suggestions", "trend_observations", "watched_files"
        )))
        added = {table: [name for name in self.columns(table) if name not in columns_then[table]] for table in self.OLD_TABLES}
        self.assertEqual({table: names for table, names in added.items() if names}, self.ADDED_SINCE)   # the columns named above, no other
        self.assertEqual(self.everything(), before)                                    # nothing the earlier version stored was touched
        self.assertEqual([(s["title"], s["kind"], s["needs_review"], s["likes"]) for s in store.list_samples()],
                         [("发过的那篇", "script", True, 80), ("超市那天", "video", True, None), ("面霜那篇", "script", False, 150)])
        script = store.get_script("aaaaaaaaaaaa")
        self.assertEqual((script["title"], script["keywords"], script["has_ai_draft"], script["updated_at"]), ("未命名脚本", ["面霜"], True, 2.0))
        self.assertEqual((store.count_lessons(), store.list_ai_suggestions("aaaaaaaaaaaa"), len(store.list_trends())), (1, ["AI 提过的一句"], 1))
        self.assertEqual(posted.for_script(store, store.get_script("eeeeeeeeeeee"))["likes"], 80)
        # The new feature works on it: what the old draft saw, what is on its topic, and a pin.
        answer = refs.for_script(store, "aaaaaaaaaaaa")
        self.assertEqual(answer["pinned"], [])
        self.assertEqual([(item["title"], item["roles"], item["wording"]) for item in answer["basis"]["items"]],
                         [("超市那天", ["example"], "transcript_waiting"), ("面霜那篇", ["example", "opening"], "own")])
        self.assertEqual([(item["title"], item["wording"], item["posted"]["read_after_days"]) for item in answer["related"]["items"]],
                         [("发过的那篇", "posted_waiting", 2)])
        pinned = refs.set_pin(store, "aaaaaaaaaaaa", "ffffffffffff", True)
        self.assertEqual([item["title"] for item in pinned["pinned"]], ["发过的那篇"])
        self.assertEqual(self.everything(), before)                                    # the pin is in the new table only
        saved = store.update_script("aaaaaaaaaaaa", {"title": "面霜这一篇"}, base_updated_at=2.0)      # a save from the version it was loaded as
        self.assertEqual(saved["title"], "面霜这一篇")
        # Opened again, as on the next start: everything is still there, the pin included.
        again = Store(self.tmp)
        self.assertEqual([item["title"] for item in refs.for_script(again, "aaaaaaaaaaaa")["pinned"]], ["发过的那篇"])
        self.assertEqual(self.tables().count("script_pins"), 1)
        self.assertEqual([row[1] for row in self.sql("PRAGMA index_list(samples)") if row[1] == "samples_by_script"], ["samples_by_script"])
        # And what the earlier version does with the folder still works: it removes an item without knowing of pins.
        self.sql("DELETE FROM samples WHERE id = 'ffffffffffff'")
        self.assertEqual(refs.for_script(again, "aaaaaaaaaaaa")["pinned"], [])

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
            thread.join(30)
        self.assertEqual((len(opened), failed), (4, []))
        self.assertEqual(self.tables().count("script_pins"), 1)
        self.assertEqual(opened[0].pin_sample("aaaaaaaaaaaa", "bbbbbbbbbbbb", refs.MAX_PINS), "pinned")
        self.assertEqual(opened[3].list_pins("aaaaaaaaaaaa"), ["bbbbbbbbbbbb"])


# ---------------------------------------------------------------------------------------------- the requests
class ServerCase(RefsCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(0, self.store, lambda *args: {})
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method, path, payload=None, body=None, content_type=None, raw=False):
        head = {}
        if payload is not None:
            body, head["Content-Type"] = json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"
        if content_type:
            head["Content-Type"] = content_type
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            data = res.read()
        finally:
            conn.close()
        if raw:
            return res.status, data, res.getheader("Content-Type")
        return res.status, json.loads(data.decode("utf-8"))


class RequestTest(ServerCase):
    def test_reading_is_a_get_and_answers_what_the_panel_shows(self):
        script = self.script(keywords=["面霜"])
        base = "/api/scripts/%s" % script["id"]
        status, answer = self.send("GET", base + "/refs")
        self.assertEqual((status, answer), (200, json.loads(json.dumps(self.refs(script)))))
        self.assertEqual([item["title"] for item in answer["related"]["items"]], ["面霜空瓶记", "入冬换面霜"])
        status, found = self.send("GET", base + "/refs/search?q=" + urllib.parse.quote("小笼包"))
        self.assertEqual((status, found["query"], sorted(item["title"] for item in found["items"])), (200, "小笼包", ["Costco 补货", "Trader Joe's 那天"]))
        self.assertEqual(self.send("GET", base + "/refs/search")[1], {"query": "", "items": [], "found": 0, "limit": refs.SEARCH_MAX})
        status, part = self.send("GET", "/api/samples/%s/text?start=5" % self.ids["周日早餐"])
        self.assertEqual((status, part["text"], part["start"], part["next"]), (200, OWN[3][1][5:], 5, None))
        self.assertEqual(self.send("GET", "/api/samples/%s/text" % self.ids["周日早餐"])[1]["text"], OWN[3][1])
        # Nothing but GET reads, and reading changes nothing.
        for method in ("POST", "PUT", "DELETE"):
            for path in (base + "/refs", base + "/refs/search?q=x", "/api/samples/%s/text" % self.ids["周日早餐"]):
                self.assertEqual(self.send(method, path, {})[0], 404, (method, path))
        self.assertEqual((self.store.get_script(script["id"]), self.pin_rows()), (script, []))

    def test_pinning_is_a_json_write_like_every_other(self):
        script = self.script()
        path = "/api/scripts/%s/pins" % script["id"]
        wanted = json.dumps({"sample_id": self.ids["周日早餐"], "pinned": True}).encode("utf-8")
        for content_type in ("application/x-www-form-urlencoded", "text/plain", "text/plain;application/json", None):
            status, answer = self.send("PUT", path, body=wanted, content_type=content_type)
            self.assertEqual((status, answer["error"]), (415, "json_required"), content_type)
        for body in (b"{", b"[1]", b'"x"', b'{"sample_id": NaN}'):
            status, answer = self.send("PUT", path, body=body, content_type="application/json")
            self.assertEqual((status, answer["error"]), (400, "bad_json"), body)
        for method in ("GET", "POST", "DELETE"):
            self.assertEqual(self.send(method, path, {} if method != "GET" else None)[0], 404, method)
        self.assertEqual(self.pin_rows(), [])
        status, answer = self.send("PUT", path, body=wanted, content_type="application/json; charset=utf-8")
        self.assertEqual((status, [item["title"] for item in answer["pinned"]]), (200, ["周日早餐"]))
        self.assertEqual(answer, json.loads(json.dumps(self.refs(script))))        # it answers what the GET answers
        status, answer = self.send("PUT", path, {"sample_id": self.ids["周日早餐"], "pinned": False})
        self.assertEqual((status, answer["pinned"]), (200, []))

    def test_a_save_made_after_a_pin_from_the_version_before_it_is_accepted(self):
        script = self.script(texts=("第一句", "第二句"))
        _, loaded = self.send("GET", "/api/scripts/" + script["id"])               # what the editor holds
        self.assertEqual(self.send("PUT", "/api/scripts/%s/pins" % script["id"], {"sample_id": self.ids["周日早餐"], "pinned": True})[0], 200)
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1], loaded)       # the script is as it was, its time included
        status, saved = self.send("PUT", "/api/scripts/" + script["id"], {"title": loaded["title"], "target_sec": 60, "keywords": [],
                                                                          "lines": lines("第一句，接着打的字", "第二句"), "base_updated_at": loaded["updated_at"]})
        self.assertEqual((status, saved["lines"][0]["spoken_script_zh"]), (200, "第一句，接着打的字"))
        self.assertEqual(self.send("PUT", "/api/scripts/%s/pins" % script["id"], {"sample_id": self.ids["周日早餐"], "pinned": False})[0], 200)
        status, saved = self.send("PUT", "/api/scripts/" + script["id"], {"title": "又改了标题", "base_updated_at": saved["updated_at"]})
        self.assertEqual((status, saved["title"]), (200, "又改了标题"))
        # Marking as posted from the version on the page is taken after a pin too.
        self.send("PUT", "/api/scripts/%s/pins" % script["id"], {"sample_id": self.ids["入冬换面霜"], "pinned": True})
        status, marked = self.send("POST", "/api/scripts/%s/posted" % script["id"], {"likes": 5, "base_updated_at": saved["updated_at"]})
        self.assertEqual((status, marked["posted"]["likes"]), (201, 5))
        # The rule still holds for a real edit made somewhere else.
        status, refused = self.send("PUT", "/api/scripts/" + script["id"], {"title": "旧页面", "base_updated_at": loaded["updated_at"]})
        self.assertEqual((status, refused["error"]), (409, "stale_script"))

    def test_a_refusal_has_a_code_and_a_sentence_in_both_languages(self):
        script = self.script()
        base = "/api/scripts/%s" % script["id"]
        item = self.ids["周日早餐"]
        extra = [self.add("第%d条" % n, "内容 %d" % n)["id"] for n in range(9)]
        for sample_id in extra[:8]:
            self.assertEqual(self.send("PUT", base + "/pins", {"sample_id": sample_id, "pinned": True})[0], 200)
        before = self.pin_rows()
        refused = [
            ("GET", "/api/scripts/%s/refs" % GONE, None, 404, "not_found"),
            ("GET", "/api/scripts/%s/refs/search?q=x" % GONE, None, 404, "not_found"),
            ("PUT", "/api/scripts/%s/pins" % GONE, {"sample_id": item, "pinned": True}, 404, "not_found"),
            ("PUT", base + "/pins", {"sample_id": GONE, "pinned": True}, 404, "no_item"),
            ("PUT", base + "/pins", {"sample_id": "not an id", "pinned": True}, 404, "no_item"),
            ("PUT", base + "/pins", {"sample_id": extra[8], "pinned": True}, 409, "too_many_pins"),
            ("PUT", base + "/pins", {"sample_id": item}, 400, "bad_pin"),
            ("PUT", base + "/pins", {"sample_id": item, "pinned": "true"}, 400, "bad_pin"),
            ("PUT", base + "/pins", {"sample_id": 5, "pinned": True}, 400, "bad_pin"),
            ("PUT", base + "/pins", {}, 400, "bad_pin"),
            ("GET", base + "/refs/search?q=" + "x" * (refs.QUERY_CHARS + 1), None, 400, "query_too_long"),
            ("GET", "/api/samples/%s/text" % GONE, None, 404, "no_item"),
            ("GET", "/api/samples/%s/text?start=-1" % item, None, 400, "bad_start"),
            ("GET", "/api/samples/%s/text?start=abc" % item, None, 400, "bad_start"),
        ]
        for method, where, payload, status, code in refused:
            got, answer = self.send(method, where, payload)
            self.assertEqual((got, answer["error"]), (status, code), (where, payload))
            self.assertRegex(answer["message"], r"^[A-Z][ -~]+\.$", code)                      # an English sentence
            self.assertRegex(answer["message_zh"], r"^[^ -~]*[\u4e00-\u9fff].*。$", code)        # and a Chinese one
            self.assertEqual((answer["message"], answer["message_zh"]), refs.REFUSALS[code][1:])
        self.assertEqual(self.pin_rows(), before)                                              # none of them stored anything
        self.assertEqual(sorted(refs.REFUSALS), ["bad_pin", "bad_start", "no_item", "not_found", "query_too_long", "too_many_pins"])
        for code, (status, english, chinese) in refs.REFUSALS.items():
            self.assertIn(status, (400, 404, 409), code)
            self.assertTrue(english.strip() and chinese.strip(), code)
        self.assertIn("8", refs.REFUSALS["too_many_pins"][1])
        self.assertIn("8", refs.REFUSALS["too_many_pins"][2])

    def test_markup_in_a_title_a_text_or_a_query_stays_text(self):
        title = '<img src=x onerror="alert(1)">&lt;b&gt;面霜'
        text = '今天聊<script>alert(1)</script>面霜 & "引号" 还有 <b>加粗</b> 的字'
        query = '<b>加粗</b>'
        status, made = self.send("POST", "/api/samples", {"title": title, "text": text, "likes": 5})
        self.assertEqual(status, 201)
        status, script = self.send("POST", "/api/scripts", {"title": '<i>换季</i> & "面霜"', "lines": lines("今天"), "keywords": ['<u>面霜</u>']})
        self.assertEqual(status, 201)
        base = "/api/scripts/%s" % script["id"]
        _, answer = self.send("GET", base + "/refs")
        self.assertEqual([term["text"] for term in answer["related"]["terms"]], ["<u>面霜</u>", "i", "换季", "面霜"][:1] + ["换季", "面霜"])
        shown = next(item for item in answer["related"]["items"] if item["id"] == made["id"])
        self.assertEqual((shown["title"], shown["excerpt"]["text"]), (title, text))             # as they were stored, character for character
        _, found = self.send("GET", base + "/refs/search?q=" + urllib.parse.quote(query))
        self.assertEqual((found["query"], [item["id"] for item in found["items"]]), (query, [made["id"]]))
        mark = found["items"][0]["excerpt"]["mark"]
        self.assertEqual(found["items"][0]["excerpt"]["text"][mark[0]:mark[1]], query)
        self.assertEqual(self.send("GET", "/api/samples/%s/text" % made["id"])[1]["text"], text)
        status, raw, kind = self.send("GET", base + "/refs", raw=True)
        self.assertEqual((status, kind), (200, "application/json; charset=utf-8"))             # never a page: data, which the page escapes
        # The page builds markup from these only through esc(). Everything else it inserts is listed here, and none of it is text from the library.
        page = (WEB / "refs.js").read_text(encoding="utf-8")
        unescaped = sorted({inserted for inserted in insertions(page) if not inserted.startswith("esc(")})
        self.assertEqual(unescaped, sorted([
            # markup made from escaped pieces
            "above", "body", "counted", "long", "looked", "more", "note", "nothing", "opening", "openingTag", "pin", "reading", "same", "whole",
            "tags.join('')", "why.join('')", "rfExcerptHtml(x.excerpt || {})", "f.items.map((x) => rfItemHtml(x, 'found')).join('')",
            "items(r.items, 'related')",
            # words of this file, and yes or no
            "part", "kind", "kind === 'script' ? '' : 'ok'", "full ? 'disabled' : ''", "rfUi.failed ? 'rf-failed' : 'rf-loading'",
            "x.is_sponsored ? 'warn' : ''", "x.is_sponsored ? 1 : 0",
            # numbers
            "Number(d.limits.query_chars)", "num(x[f])", "st.next",
            # the name of a number, from the pages' own texts; ids and a query, inside an address or a key, never as markup
            "t('m_' + f)", "s.id", "id", "x.id", "encodeURIComponent(q)",
        ]))
        self.assertNotIn("innerText", page)
        self.assertNotIn("insertAdjacentHTML", page)
        self.assertNotIn("outerHTML", page)
        self.assertEqual(sorted(set(re.findall(r'href="([^"]*)"', page))), ["#/library"])
        self.assertEqual([found.strip() for found in re.findall(r"\.innerHTML\s*=\s*([^;\n]{0,28})", page)],
                         ["rfListsHtml()", '`<section class="rf-sec" dat', "rfFootHtml()", "rfFoundHtml()", '`<div class="rf-bar"><button',
                          '`<aside class="rf-panel" id='])
        # What is cut out of an item's text for the excerpt goes through esc() piece by piece, and is cut by characters.
        self.assertIn("`${esc(piece(0, mark[0]))}<mark>${esc(piece(mark[0], mark[1]))}</mark>${esc(piece(mark[1]))}`", page)
        self.assertIn("const chars = Array.from(String(ex.text || ''));", page)
        self.assertIn("const piece = (from, to) => chars.slice(from, to).join('');", page)
        self.assertNotIn("text.slice(", page)
        self.assertIn('if (!/^[0-9a-f]{12}$/.test(String(id))) return;', page)                 # an id is checked before it is put into a selector


# ---------------------------------------------------------------------------------------------- the page
class PageTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.page = (WEB / "refs.js").read_text(encoding="utf-8")

    def texts(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.page, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        return {language: dict(re.findall(r"\b(rf_[a-z_]+): '((?:[^'\\]|\\.)*)'", body)) for language, body in blocks}, blocks

    def test_every_text_is_there_in_both_languages_under_its_own_prefix(self):
        texts, blocks = self.texts()
        keys = {language: set(re.findall(r"\b([a-z]+_[a-z_]+): '", body)) for language, body in blocks}
        self.assertGreater(len(keys["zh"]), 60)
        self.assertEqual({key for key in keys["zh"] | keys["en"] if not key.startswith("rf_")}, set())
        self.assertEqual(keys["zh"], {key for key in keys["en"] if not key.endswith("_one")})      # English alone has singular forms
        self.assertEqual((set(texts["zh"]), set(texts["en"])), (keys["zh"], keys["en"]))
        used = set(re.findall(r"""['"](rf_[a-z_]+)['"]""", self.page))
        self.assertEqual(used - keys["zh"], set())                                                 # nothing is asked for that is not there
        self.assertEqual(keys["zh"] - used, set())                                                 # and nothing is there that is never shown
        for key in keys["en"] - keys["zh"]:
            self.assertIn("countKey('%s'" % key[:-len("_one")], self.page, key)                    # a singular form is asked for through countKey
        zh, en = texts["zh"], texts["en"]
        self.assertEqual((zh["rf_title"], en["rf_title"]), ("参考", "References"))
        # Each part says in one sentence why its items are there, and the rule of the ranking is stated.
        for needed in ("目标关键词", "标题里的词", "「热点」页", "{pct}%"):
            self.assertIn(needed, zh["rf_related_why"])
        for needed in ("target keywords", "words of its title", "Trends page", "{pct}%"):
            self.assertIn(needed, en["rf_related_why"])
        self.assertIn("不拆词", zh["rf_search_why"])
        self.assertIn("not split into words", en["rf_search_why"])
        self.assertIn("按当时记下的标题", zh["rf_basis_why"])
        self.assertIn("by the titles recorded at the time", en["rf_basis_why"])
        self.assertIn("钉和取消都不会改动脚本", zh["rf_pinned_why"])
        # With nothing to measure by, it says what to add.
        for needed in ("目标关键词", "标题"):
            self.assertIn(needed, zh["rf_needs_terms"])
        for needed in ("Add target keywords", "title"):
            self.assertIn(needed, en["rf_needs_terms"])
        # Looked for by the title alone, with nothing found: it says how a title is read, and what finds more.
        for needed in ("按空格和标点分成词", "目标关键词"):
            self.assertIn(needed, zh["rf_related_none_title"])
        for needed in ("spaces and punctuation", "add target keywords"):
            self.assertIn(needed, en["rf_related_none_title"])
        self.assertTrue(zh["rf_related_none_title"].startswith(zh["rf_related_none"]) and en["rf_related_none_title"].startswith(en["rf_related_none"]))
        self.assertIn("r.terms.every((x) => x.from === 'title') ? 'rf_related_none_title' : 'rf_related_none'", self.page)
        # "Nothing mentions these words" is said only when nothing does: what is pinned or was seen by the draft is on the topic too.
        self.assertIn("const nothing = r.found || r.listed_above ? '' : `<p class=\"hint\" id=\"rf-related-none\">${esc(none)}</p>`;", self.page)
        # No AI, and nothing leaves the computer.
        self.assertIn("不调用 AI", zh["rf_about"])
        self.assertIn("不会离开这台电脑", zh["rf_about"])
        self.assertIn("No AI is asked and nothing leaves this computer", en["rf_about"])

    def test_wording_that_is_not_confirmed_is_said_to_be_so_and_nothing_is_called_the_way_she_talks(self):
        texts, _ = self.texts()
        zh, en = texts["zh"], texts["en"]
        for needed in ("机器转写", "还没确认", "不一定是你的原话"):
            self.assertIn(needed, zh["rf_w_transcript"])
        for needed in ("Machine transcript", "not confirmed", "may not be what {@name} said"):
            self.assertIn(needed, en["rf_w_transcript"])
        for needed in ("还没确认是你自己的说法", "可能有 AI 写的句子"):
            self.assertIn(needed, zh["rf_w_posted"])
        for needed in ("not confirmed as {@names} own", "sentences an AI wrote"):
            self.assertIn(needed, en["rf_w_posted"])
        self.assertIn("AI 写的，不算你的开场", zh["rf_opening_ai"])
        self.assertIn("written by AI, not {@names} own opening", en["rf_opening_ai"])
        # The labels are drawn from what the server says of the item, for every item, in every list.
        self.assertIn("x.wording === 'posted_waiting' || x.wording === 'transcript_waiting'", self.page)
        self.assertIn("x.opening_by_ai ? `<span class=\"tag warn\" data-rf-ai-opening>", self.page)
        self.assertEqual(self.page.count("function rfItemHtml("), 1)
        self.assertEqual(len(re.findall(r"(?<!function )rfItemHtml\(x, ", self.page)), 2)          # called for the lists, and for what a search found
        for text in list(zh.values()) + list(en.values()):
            # Nothing presents an item's text as how the creator talks, and nothing promises how a video will do.
            self.assertIsNone(re.search(r"说话习惯|你的声音|你的风格|像你|你的口吻|sounds? like|way of speaking|talks|voice|style", text, re.I), text)
            self.assertIsNone(re.search(r"爆款|会火|上热门|涨粉|viral|blow up|guarantee", text, re.I), text)
        # English names the creator through the placeholders; Chinese speaks to her as 你.
        for text in en.values():
            self.assertIsNone(re.search(r"\b(?:you|your|yours|she|her|hers|herself|he|his|him)\b", text, re.I), text)
        self.assertTrue(any("{@name}" in text for text in en.values()) and any("{@names}" in text for text in en.values())
                        and any("{@their}" in text for text in en.values()))
        self.assertIsNone(re.search(r"[她他]|TA\b|\{@", "".join(zh.values())))
        self.assertGreater(sum("你" in text for text in zh.values()), 8)

    def test_it_is_served_loaded_before_pages_and_hooked_in_by_small_edits(self):
        index = (WEB / "index.html").read_text(encoding="utf-8")
        order = [index.index('src="/%s.js"' % name) for name in ("app", "setup", "discover", "craft", "prompter", "kwimport", "posted", "wordlist", "refs", "pages")]
        self.assertEqual(order, sorted(order))
        status, body, kind = self.send("GET", "/refs.js", raw=True)
        self.assertEqual((status, kind), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, (WEB / "refs.js").read_bytes())
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertEqual(sorted(set(re.findall(r"\brf[A-Z]\w*\([^)]*\)|<div id=\"rf-[a-z]+\"></div>", app))),
                         ['<div id="rf-box"></div>', "rfAfterSave(s)", "rfAttach()"])
        # Two calls, one place for it, the name under which "open or closed" is kept, and nothing else.
        self.assertEqual(len(re.findall(r"\brf[A-Z_-]", app)), 4)
        self.assertEqual(app.count("'rf-open'"), 1)
        self.assertEqual(app.count('id="rf-'), 1)
        self.assertLess(app.index("craftAttach();"), app.index("rfAttach();"))                    # after everything else of the editor is drawn
        self.assertLess(app.index('<div id="craft-bar"></div>'), app.index('<div id="rf-box"></div>'))
        self.assertLess(app.index('<div id="rf-box"></div>'), app.index('<div id="lines">'))
        for call in ("rfAttach", "rfAfterSave"):
            self.assertIn("function %s(" % call, self.page)
        for path in sorted(WEB.glob("*.js")):
            if path.name != "refs.js":
                source = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"['\"]rf_[a-z]", source), path.name)                 # its texts live in its own file
                if path.name != "app.js":
                    self.assertIsNone(re.search(r"\brf[A-Z]\w*\(|rf-[a-z]", source), path.name)

    def test_it_uses_its_own_names_and_keeps_its_state_apart_from_the_page(self):
        # The editor reads any data-act it does not know as "move down", and binds every data-sec as a length.
        attributes = set(re.findall(r"\bdata-[a-z-]+", self.page))
        self.assertEqual({name for name in attributes if not name.startswith("data-rf")}, {"data-id"})      # the library's own, read to find an item there
        self.assertEqual(set(re.findall(r"dataset\.(\w+)", self.page)), {"rf", "rfItem", "rfKey", "rfWhole"})
        self.assertEqual({name for name in re.findall(r'\bid="([a-z-]+)', self.page) if not name.startswith("rf-")}, set())
        for line_class in ('class="line', 'class="card', "class=\"spoken"):
            self.assertNotIn(line_class, self.page)
        self.assertNotIn("<textarea", self.page)                    # the editor grows every textarea it finds
        # What is open and what was searched is kept in one object here, so that a redraw of the editor brings it back.
        state = re.search(r"const rfUi = \{(.*?)\};", self.page, re.S).group(1)
        for kept in ("scriptId", "data", "query", "found", "full", "scroll"):
            self.assertRegex(state, r"\b%s:" % kept)
        self.assertIn("body.scrollTop = rfUi.scroll;", self.page)
        # An open text is put back where it was being read, each time the lists are drawn.
        self.assertIn("if (st && st.scroll) el.scrollTop = st.scroll;", self.page)
        self.assertIn("if (st) st.scroll = el.scrollTop;", self.page)
        listener = self.page.split("document.addEventListener('scroll', (e) => {", 1)[1].split("\n", 5)
        self.assertEqual(listener[4], "}, true);")                   # caught on its way down: a scroll is not passed up to the document
        self.assertIn('value="${esc(rfUi.query)}"', self.page)
        # Whether it is open is kept under one name, the way the language and the look are: by the page and by the
        # core, which the desktop app needs (its page has another address at every start).
        self.assertNotIn("localStorage", self.page)
        self.assertEqual(re.findall(r"\b(recall|remember)\(([^)]*)\)", self.page), [("recall", "RF_STORED"), ("remember", "RF_STORED, what === 'open' ? '1' : '0'")])
        self.assertRegex(self.page, r"remember\(RF_STORED, [^\n]+\n\s+keepWithCore\(\);")
        self.assertIn("const RF_STORED = 'rf-open';", self.page)
        self.assertEqual(PAGE_WORDS["rf-open"], ("0", "1"))
        self.assertIn("'rf-open': ['0', '1']", (WEB / "app.js").read_text(encoding="utf-8"))

    def test_it_talks_to_this_server_only_and_pinning_saves_nothing_else(self):
        self.assertEqual(re.findall(r"api\('(GET|POST|PUT|DELETE)', `([^`]+)`", self.page), [
            ("GET", "/api/scripts/${s.id}/refs"), ("PUT", "/api/scripts/${s.id}/pins"),
            ("GET", "/api/scripts/${s.id}/refs/search?q=${encodeURIComponent(q)}"), ("GET", "/api/samples/${id}/text?start=${st.next}")])
        self.assertEqual(len(re.findall(r"\bapi\(", self.page)), 4)
        self.assertIn("{ sample_id: id, pinned }", self.page)
        for word in ("fetch(", "XMLHttpRequest", "window.open", "location.href", "getUserMedia", "mediaDevices", "manualBridge", "aiRun",
                     "http://", "https://", "navigator."):
            self.assertNotIn(word, self.page, word)
        # Pinning does not save the script, wait for a save, or draw the editor again: what is being typed is left alone.
        code = code_of(self.page)
        self.assertNotIn("paintScript", code)
        self.assertIn("paintScript", self.page)                      # the comments do name it: the rule is about the code
        self.assertIsNone(re.search(r"\b(?:flushSave|scheduleSave|saveNow|paintScript|renderScript|router|setSaved)\s*\(", code))
        self.assertIsNone(re.search(r"\bsaveTimer\b|base_updated_at|\blintSeq\b", code))
        # Whether something is typed and not yet saved is read in one place and never set: after a save, the lists are
        # asked for again only when the server holds what the page holds.
        self.assertEqual(re.findall(r"[^\n]*\bdirty\b[^\n]*", code), ["  if (!cur || cur.script !== s || dirty) return;"])
        after_save = code.split("function rfAfterSave(s) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("dirty) return;", after_save)
        self.assertIn("if (rfSig(s) !== rfUi.sig) rfLoad(true);", after_save)
        # The answer is taken to be for this title and these keywords only when it was asked for in step with the server.
        self.assertEqual(sorted(set(re.findall(r"\brfLoad\(([^)]*)\)", code))), ["false", "inStep", "reopened", "true"])
        self.assertIn("const sig = inStep ? rfSig(s) : '';", code)
        # The limits the page shows are the ones the server sends.
        for sent in ("d.limits.pins", "d.limits.query_chars", "d.limits.scan_chars", "d.limits.relevant_at", "rfUi.data.limits.text_part_chars"):
            self.assertIn(sent, self.page)
        code = self.page.split("});", 2)[2]                          # after the two blocks of texts
        self.assertIsNone(re.search(r"\b(?:%d|%d|%d|%d)\b" % (refs.SCAN_CHARS, refs.TEXT_PART_CHARS, refs.EXCERPT_CHARS, refs.QUERY_CHARS), code))
        self.assertEqual(re.search(r"const RF_METRICS = \[([^\]]+)\]", self.page).group(1).replace("'", "").split(", "), list(posted.METRIC_FIELDS))

    def test_its_styles_are_a_block_of_their_own(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        marker = "/* ---- references beside the script editor (refs.js)"
        self.assertEqual(css.count(marker), 1)
        before, block = css.split(marker)
        self.assertIsNone(re.search(r"\brf-", before))                                             # nothing of it stands among the other pages' rules
        rules = re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", marker + block, flags=re.S))
        self.assertGreater(len(rules), 20)
        for selector in rules:
            self.assertTrue(selector.strip().startswith("@media") or "rf-" in selector, selector)  # and it styles nothing but itself
        self.assertNotIn("!important", block)
        # The page and the sheet agree on where the window is wide, and on the names.
        wide = re.findall(r"@media \((min-width: \d+px)\)", block)
        self.assertEqual(len(wide), 1)
        self.assertIn("const RF_WIDE = '(%s)';" % wide[0], self.page)
        used = {name for names in re.findall(r'class="([^"]+)"', self.page) for name in names.split() if name.startswith("rf-")}
        self.assertEqual({name for name in used if "." + name not in block}, set())
        self.assertIn("classList.toggle('rf-on', open)", self.page)
        self.assertIn(".wrap.rf-on", block)
        # The text of an item can be selected and copied.
        self.assertRegex(block, r"\.rf-text \{[^}]*user-select: text")
        # Every colour is one of the sheet's variables, so that another look or a dark mode reaches the panel as well.
        rules_only = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", re.sub(r"#rf-[a-z-]+", "", rules_only)), [])
        sizes = {"--radius", "--view-pad", "--view-side", "--rf-w", "--rf-out"}       # the page's, and the two this block sets for itself
        self.assertEqual(sorted(set(re.findall(r"var\((--[a-z-]+)\)", rules_only)) - sizes),
                         ["--bg", "--card", "--ink", "--line", "--warn", "--warn-soft"])
        variables = set(re.findall(r"(--[a-z-]+):", css.split("}", 1)[0]))
        self.assertTrue({"--bg", "--card", "--ink", "--line", "--warn", "--warn-soft", "--muted", "--accent", "--accent-soft", "--ok"} <= variables)
        for declared in re.findall(r"(?:color|background|border(?:-[a-z]+)*)\s*:\s*([^;}]+)", rules_only):
            for word in re.findall(r"[a-z-]+", re.sub(r"var\([^)]*\)", "", declared)):
                self.assertIn(word, ("px", "solid", "inherit", "none", "transparent"), declared)       # no colour by name either


if __name__ == "__main__":
    unittest.main()
