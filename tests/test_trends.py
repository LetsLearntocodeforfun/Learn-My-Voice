"""Tests for trend keywords: reading pasted lists, scoring, coverage, AI lookups, and the HTTP and MCP surfaces."""
import http.client
import json
import math
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from studio import llm, mcp_server, pipeline, server, trends
from studio.store import Store, normalize_keywords

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
DAY = 86400.0
NOW = 1_800_000_000.0

SCRIPT_REPLY = {"script_lines": [{"spoken_script_zh": "最近换季嘛"}]}
IDEAS_REPLY = {"ideas": [{"title_zh": "秋冬护肤第二弹", "evidence_titles": ["护肤那期"],
                          "trend_keywords": [" #秋冬 护肤", "没在跟踪的词", 7]}]}


def video(title, text, likes=None, sponsored=False):
    return {"id": title, "title": title, "text": text, "is_sponsored": sponsored, "views": None,
            "likes": likes, "collects": None, "comments": None, "created_at": 1}


def trend(keyword, heat=None, age_days=0.0, source="manual"):
    return {"id": keyword, "keyword": keyword, "heat": heat, "source": source, "updated_at": NOW - age_days * DAY}


SAMPLES = [
    video("秋冬护肤步骤", "秋冬护肤 / 我换了面霜", likes=400),
    video("超市补货", "去超市买菜", likes=100),
    video("早餐日常", "早上煮咖啡", likes=100),
    video("商单护肤", "秋冬护肤 这款面霜", likes=9000, sponsored=True),
    video("没数据的护肤", "秋冬护肤 随便聊聊"),
]
ROWS = [trend("秋冬护肤", 1000), trend("露营", 100000), trend("露营装备", 100000, age_days=14, source="ai"),
        trend("早餐", None, age_days=60)]


class ParseKeywordsTest(unittest.TestCase):
    def parse(self, *lines):
        items, skipped = trends.parse_keywords("\n".join(lines))
        return [(i["keyword"], i["heat"]) for i in items], skipped

    def test_heat_follows_a_separator_or_is_clearly_a_count(self):
        items, skipped = self.parse("秋冬护肤, 125000", "早餐吃什么 12.5万", "通勤穿搭 12,500", "Costco 好物\t3k",
                                    "减脂餐｜2亿", "夫妻日常")
        self.assertEqual(skipped, 0)
        self.assertEqual(items, [("秋冬护肤", 125000), ("早餐吃什么", 125000), ("通勤穿搭", 12500), ("Costco 好物", 3000),
                                 ("减脂餐", 200000000), ("夫妻日常", None)])

    def test_numbers_that_belong_to_the_keyword_stay(self):
        items, _ = self.parse("iPhone 15", "2024 穿搭", "搬家 999")
        self.assertEqual(items, [("iPhone 15", None), ("2024 穿搭", None), ("搬家 999", None)])

    def test_list_marks_blank_lines_and_repeats(self):
        items, skipped = self.parse("1. #护肤", "  ", "- 露营", "护肤, 500", "护肤", "#", "x" * 41)
        self.assertEqual(items, [("护肤", 500), ("露营", None)])      # the repeat without a number keeps the heat
        self.assertEqual(skipped, 2)                                  # a bare # and an over-long line; blanks are not counted

    def test_one_paste_is_capped(self):
        items, skipped = self.parse(*["词%d" % i for i in range(trends.MAX_KEYWORDS_PER_PASTE + 5)])
        self.assertEqual((len(items), skipped), (trends.MAX_KEYWORDS_PER_PASTE, 5))
        self.assertEqual(trends.parse_keywords(None), ([], 0))


class MatchingTest(unittest.TestCase):
    def test_chinese_keywords_match_by_overlapping_pieces(self):
        self.assertEqual(trends.keyword_pieces("秋冬护肤 routine"), ["秋冬", "冬护", "护肤", "routine"])
        hay = trends._Hay(video("秋冬我怎么护肤", "最近天气干 / 护肤步骤换了一下"))
        self.assertEqual(trends.relevance("护肤步骤", hay), 1.0)           # appears as written
        self.assertAlmostEqual(trends.relevance("秋冬护肤", hay), 2 / 3)    # 秋冬 and 护肤 are there, 冬护 is not
        self.assertEqual(trends.relevance("露营", hay), 0.0)

    def test_english_words_match_whole_words_only(self):
        self.assertFalse(trends.is_on_topic(video("gymnastics class", ""), ["gym"]))
        self.assertTrue(trends.is_on_topic(video("去 Gym 的一天", ""), ["gym"]))

    def test_on_topic_needs_most_of_one_keyword(self):
        sample = video("秋冬我怎么护肤", "")
        self.assertTrue(trends.is_on_topic(sample, ["露营", "秋冬护肤"]))   # two of three pieces
        self.assertFalse(trends.is_on_topic(sample, ["秋冬穿搭"]))          # one of three pieces
        self.assertFalse(trends.is_on_topic(sample, []))


class ScoreTest(unittest.TestCase):
    def score(self, rows=ROWS, samples=SAMPLES, dna=None, weight="balanced"):
        out = trends.score_trends(rows, samples, dna, weight, NOW)
        return out, {t["keyword"]: t for t in out["trends"]}

    def test_baseline_is_her_median_non_sponsored_video(self):
        out, _ = self.score()
        self.assertEqual((out["baseline_interactions"], out["videos_with_numbers"]), (100, 3))
        out, by = self.score(samples=[video("没数据", "秋冬护肤")])
        self.assertIsNone(out["baseline_interactions"])
        self.assertEqual((by["秋冬护肤"]["her_videos"], by["秋冬护肤"]["lift_raw"], by["秋冬护肤"]["performance"]), (1, None, 0.5))

    def test_trend_side_is_heat_times_freshness(self):
        _, by = self.score()
        self.assertEqual((by["露营"]["trend"], by["露营"]["freshness"]), (1.0, 1.0))             # hottest, seen today
        self.assertEqual(by["秋冬护肤"]["trend"], round(math.log1p(1000) / math.log1p(100000), 3))
        self.assertEqual((by["露营装备"]["trend"], by["露营装备"]["stale"]), (0.5, False))          # one half-life old
        self.assertEqual(by["早餐"]["freshness"], round(0.5 ** (60 / trends.HALF_LIFE_DAYS), 3))
        self.assertTrue(by["早餐"]["stale"])
        self.assertFalse(by["早餐"]["heat_known"])                                               # no number was entered

    def test_her_side_uses_only_her_own_non_sponsored_videos(self):
        _, by = self.score()
        skincare = by["秋冬护肤"]
        self.assertEqual(skincare["evidence_titles"], ["秋冬护肤步骤", "没数据的护肤"])            # the sponsored one is not evidence
        self.assertEqual((skincare["her_videos"], skincare["her_videos_with_numbers"]), (2, 1))
        self.assertEqual(skincare["lift_raw"], 4.0)                                              # 400 against her median of 100
        self.assertEqual(skincare["performance"], round(0.5 + (2 / (1 + trends.PRIOR_VIDEOS)) / 4, 3))  # one video is shrunk
        self.assertEqual(skincare["score"], 63)
        camping = by["露营"]
        self.assertEqual((camping["her_videos"], camping["lift_raw"], camping["performance"], camping["score"]), (0, None, 0.5, 75))

    def test_one_viral_video_is_capped(self):
        samples = [video("露营那次", "露营 好玩", likes=1000000)] + [video("日常%d" % i, "买菜", likes=100) for i in range(4)]
        _, by = self.score([trend("露营", 500)], samples)
        self.assertEqual(by["露营"]["lift_raw"], 2 ** trends.LOG_CAP)                            # 8x, not 10,000x
        self.assertEqual(by["露营"]["performance"], 0.75)

    def test_weighting_changes_the_order(self):
        samples = [video("护肤一", "秋冬护肤", likes=800), video("护肤二", "秋冬护肤", likes=800)]
        samples += [video("日常%d" % i, "买菜", likes=100) for i in range(3)]
        rows = [trend("秋冬护肤", 1000), trend("露营", 100000)]

        def order(weight):
            return [t["keyword"] for t in trends.score_trends(rows, samples, None, weight, NOW)["trends"]]

        self.assertEqual(order("trend"), ["露营", "秋冬护肤"])
        self.assertEqual(order("mine"), ["秋冬护肤", "露营"])
        out = trends.score_trends(rows, samples, None, "nonsense", NOW)
        self.assertEqual((out["weight"], out["weights"]), ("balanced", {"trend": 0.5, "mine": 0.5}))

    def test_keywords_that_clash_with_her_voice_are_marked_not_removed(self):
        rows = [trend(k) for k in ("绝绝子好物", "美白祛斑", "秋冬护肤")]
        _, by = self.score(rows, [], EXAMPLE_DNA)
        self.assertEqual((by["绝绝子好物"]["flags"], by["绝绝子好物"]["usable"]), (["voice"], False))
        self.assertEqual((by["美白祛斑"]["flags"], by["美白祛斑"]["usable"]), (["compliance"], True))
        self.assertEqual((by["秋冬护肤"]["flags"], by["秋冬护肤"]["usable"]), ([], True))
        self.assertEqual(self.score(rows, [])[1]["绝绝子好物"]["flags"], [])                       # no profile, no voice check

    def test_nothing_tracked(self):
        out, _ = self.score(rows=[])
        self.assertEqual(out["trends"], [])


class CoverageTest(unittest.TestCase):
    def test_reports_where_each_keyword_appears(self):
        lines = [{"spoken_script_zh": "今天聊聊秋冬 护肤 [轻笑]", "audio_mode": "VOICEOVER"},
                 {"spoken_script_zh": "现场说了露营", "audio_mode": "LIVE_CLIP"}]
        post = {"title_options": [{"title_zh": "通勤穿搭分享"}], "caption_body_zh": "减脂餐也有",
                "hashtags_zh": ["#夫妻日常", "＃Costco好物"]}
        found = {c["keyword"]: c for c in trends.coverage(
            ["秋冬护肤", "露营", "通勤穿搭", "减脂餐", "夫妻日常", "costco 好物", "没有"], lines, post)}
        where = {k: [f for f in ("spoken", "title", "caption", "hashtags") if c[f]] for k, c in found.items()}
        self.assertEqual(where, {"秋冬护肤": ["spoken"], "露营": [], "通勤穿搭": ["title"], "减脂餐": ["caption"],
                                 "夫妻日常": ["hashtags"], "costco 好物": ["hashtags"], "没有": []})
        self.assertFalse(found["露营"]["covered"])                    # only said in a live clip, which is not scripted
        self.assertTrue(found["costco 好物"]["covered"])

    def test_missing_or_malformed_script_parts(self):
        self.assertEqual(trends.coverage([], None, None), [])
        self.assertFalse(trends.coverage(["露营"], "oops", ["oops"])[0]["covered"])


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)


class StoreTrendTest(StoreCase):
    def test_same_keyword_is_refreshed_not_duplicated(self):
        self.assertEqual(self.store.upsert_trends([{"keyword": "秋冬护肤", "heat": 500}, {"keyword": "x" * 41}, {"keyword": " "}]), 1)
        self.store.upsert_trends([{"keyword": "#秋冬 护肤"}])              # same keyword again, without a number
        self.assertEqual([(r["keyword"], r["heat"], r["source"]) for r in self.store.list_trends()], [("秋冬护肤", 500, "manual")])

    def test_heat_can_be_set_and_cleared_and_a_trend_deleted(self):
        self.store.upsert_trends([{"keyword": "露营"}])
        trend_id = self.store.list_trends()[0]["id"]
        self.assertEqual(self.store.update_trend(trend_id, "1200")["heat"], 1200)
        self.assertIsNone(self.store.update_trend(trend_id, "")["heat"])
        self.assertTrue(self.store.delete_trend(trend_id))
        self.assertFalse(self.store.delete_trend(trend_id))
        self.assertEqual(self.store.list_trends(), [])

    def test_script_keywords_are_cleaned_and_capped(self):
        self.assertEqual(normalize_keywords(["#护肤", "护 肤", "护肤", 5, "x" * 41, "A", "a"] + ["k%d" % i for i in range(10)]),
                         ["护肤", "A", "k0", "k1", "k2", "k3", "k4", "k5"])
        script = self.store.create_script(lines=[{"spoken_script_zh": "嗯"}], keywords=["#秋冬护肤", "露营"])
        self.assertEqual(script["keywords"], ["秋冬护肤", "露营"])
        self.assertEqual(self.store.update_script(script["id"], {"title": "新标题"})["keywords"], ["秋冬护肤", "露营"])
        self.assertEqual(self.store.update_script(script["id"], {"keywords": ["早餐"]})["keywords"], ["早餐"])

    def test_database_from_before_keywords_is_upgraded_in_place(self):
        old = self.tmp / "old"
        old.mkdir()
        db = sqlite3.connect(str(old / "studio.db"))
        db.execute("CREATE TABLE scripts (id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',"
                   " video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER', target_sec INTEGER NOT NULL DEFAULT 60,"
                   " notes TEXT NOT NULL DEFAULT '', lines_json TEXT NOT NULL DEFAULT '[]', post_json TEXT NOT NULL DEFAULT '{}',"
                   " ai_lines_json TEXT, status TEXT NOT NULL DEFAULT 'DRAFT', created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        db.execute("INSERT INTO scripts (id, title, created_at, updated_at) VALUES ('aaaaaaaaaaaa', '旧脚本', 1, 1)")
        db.commit()
        db.close()
        store = Store(old)
        script = store.get_script("aaaaaaaaaaaa")
        self.assertEqual((script["title"], script["keywords"]), ("旧脚本", []))
        self.assertEqual(store.update_script("aaaaaaaaaaaa", {"keywords": ["露营"]})["keywords"], ["露营"])


class TrendLookupTest(StoreCase):
    now = time.mktime(time.strptime("2027-01-15", "%Y-%m-%d"))

    def test_only_sourced_recent_keywords_are_kept(self):
        self.store.upsert_trends([{"keyword": "秋冬护肤", "heat": 500}], source="manual")
        counts = pipeline.apply_trends(self.store, {"keywords": [
            {"keyword": "#秋冬 护肤", "source_url": "https://example.com/a", "source_date": "2027-01-10", "why_zh": "平台榜单"},
            {"keyword": "没日期", "source_url": "http://example.com/c", "heat": 99},
            {"keyword": "围炉煮茶", "source_url": "https://example.com/b", "source_date": "2026-01-01"},
            {"keyword": "没有来源"}, {"keyword": "坏链接", "source_url": "javascript:alert(1)"},
            "oops", {"keyword": "", "source_url": "https://example.com/e"},
        ]}, now=self.now)
        self.assertEqual(counts, {"added": 2, "dropped_no_source": 2, "dropped_too_old": 1})
        rows = {r["keyword"]: r for r in self.store.list_trends()}
        self.assertEqual(sorted(rows), sorted(["秋冬护肤", "没日期"]))
        hers = rows["秋冬护肤"]                                            # she entered it first, so it stays hers
        self.assertEqual((hers["source"], hers["heat"], hers["source_url"], hers["note_zh"]),
                         ("manual", 500, "https://example.com/a", "平台榜单"))
        found = rows["没日期"]
        self.assertEqual((found["source"], found["heat"]), ("ai", None))  # a heat number from an AI is never stored
        self.assertEqual(found["updated_at"], self.now)                   # no date given: counted as seen now

    def test_source_date_sets_how_fresh_the_keyword_is(self):
        pipeline.apply_trends(self.store, {"keywords": [
            {"keyword": "围炉煮茶", "source_url": "https://example.com/a", "source_date": "2027-01-05"},
            {"keyword": "未来", "source_url": "https://example.com/b", "source_date": "2030-01-01"}]}, now=self.now)
        age = {r["keyword"]: (self.now - r["updated_at"]) / DAY for r in self.store.list_trends()}
        self.assertAlmostEqual(age["围炉煮茶"], 10, places=1)
        self.assertEqual(age["未来"], 0)                                   # a date in the future cannot make it fresher than now

    def test_refusals_and_empty_replies_add_nothing(self):
        sourced = {"keyword": "x", "source_url": "https://example.com"}
        for reply, code in (({"cannot_browse": True, "keywords": [sourced]}, "cannot_browse"), ({"keywords": []}, "empty_result"),
                            ({"keywords": [{"keyword": "没有来源"}]}, "empty_result"), ({}, "empty_result")):
            with self.assertRaises(llm.LLMError) as ctx:
                pipeline.apply_trends(self.store, reply, now=self.now)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.store.list_trends(), [])

    def test_prompt_needs_a_niche_and_lists_what_is_tracked(self):
        with self.assertRaises(llm.LLMError) as ctx:
            pipeline.trends_prompt(self.store)
        self.assertEqual(ctx.exception.code, "no_focus")
        self.store.upsert_trends([{"keyword": "露营"}])
        self.store.create_sample(title="超市那天", text="去超市买菜")
        self.store.create_sample(title="商单面霜", text="这款面霜", is_sponsored=True)
        system, user = pipeline.trends_prompt(self.store, focus=" 护肤 ")
        self.assertIn("cannot_browse", system)
        self.assertIn('"focus_she_typed": "护肤"', user)
        self.assertIn("超市那天", user)
        self.assertNotIn("商单面霜", user)
        self.assertIn("露营", user.split("<already_tracked>")[1])
        self.assertIn('"persona": null', user)                            # the bundled example profile is not hers
        self.assertNotIn("{{", user)
        pipeline.trends_prompt(self.store)                                # her library is enough without a typed focus


class TrendMcpTest(StoreCase):
    def tool(self, name, **arguments):
        result = mcp_server.call_tool(self.store, name, arguments)
        text = result["content"][0]["text"]
        return result["isError"], (json.loads(text) if text.startswith("{") else text)

    def test_an_ai_can_add_sourced_keywords_and_read_the_ranking(self):
        self.assertTrue(self.tool("add_trend_keywords", keywords=[])[0])
        is_error, message = self.tool("add_trend_keywords", keywords=[{"keyword": "没来源"}])
        self.assertTrue(is_error)
        self.assertIn("source link", message)
        is_error, out = self.tool("add_trend_keywords", keywords=[{"keyword": "围炉煮茶", "source_url": "https://example.com/a"}])
        self.assertEqual((is_error, out["added"]), (False, 1))
        is_error, listing = self.tool("list_trends")
        self.assertFalse(is_error)
        self.assertEqual([(t["keyword"], t["source"]) for t in listing["trends"]], [("围炉煮茶", "ai")])
        self.assertEqual(listing["weighting"], {"trend": 0.5, "mine": 0.5})


class FakeModel:
    """Stands in for a local OpenAI-compatible model; answers by which blueprint is asking."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(body)
        if method == "GET":
            return {"data": [{"id": "local-model"}]}
        reply = IDEAS_REPLY if "content strategist" in body["messages"][0]["content"] else SCRIPT_REPLY
        return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

    def prompt_for(self, marker):
        for body in reversed(self.calls):
            if body and marker in body["messages"][0]["content"]:
                return body["messages"][1]["content"]
        raise AssertionError("no call for " + marker)


class TrendServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.fake = FakeModel()
        self.httpd = server.make_server(0, self.store, self.fake)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        res = conn.getresponse()
        payload = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, payload

    def connect_model(self):
        self.call("POST", "/api/settings/models", {"provider": "local", "base_url": "http://localhost:11434/v1"})
        _, out = self.call("POST", "/api/settings", {"provider": "local", "model": "local-model"})
        self.assertTrue(out["settings"]["configured"])

    def test_paste_rank_edit_and_delete(self):
        status, empty = self.call("GET", "/api/trends")
        self.assertEqual((status, empty["trends"], empty["weight"]), (200, [], "balanced"))
        self.assertEqual(self.call("POST", "/api/trends", {"text": " \n#\n"})[1]["error"], "no_keywords")

        status, out = self.call("POST", "/api/trends", {"text": "秋冬护肤, 1000\n露营 10万\n" + "x" * 41})
        self.assertEqual((status, out["added"], out["skipped"]), (201, 2, 1))
        self.assertEqual([t["keyword"] for t in out["trends"]], ["露营", "秋冬护肤"])
        self.assertEqual(self.call("GET", "/api/state")[1]["trend_count"], 2)

        self.assertEqual(self.call("PUT", "/api/trends/weight", {"weight": "loud"})[1]["error"], "bad_weight")
        self.assertEqual(self.call("PUT", "/api/trends/weight", {"weight": "mine"})[1]["weight"], "mine")
        self.assertEqual(self.call("GET", "/api/trends")[1]["weights"], {"trend": 0.3, "mine": 0.7})   # remembered

        skincare = next(t for t in out["trends"] if t["keyword"] == "秋冬护肤")
        _, edited = self.call("PUT", "/api/trends/" + skincare["id"], {"heat": "2000000"})
        self.assertEqual([(t["keyword"], t["heat"]) for t in edited["trends"]], [("秋冬护肤", 2000000), ("露营", 100000)])
        self.assertEqual(self.call("PUT", "/api/trends/" + "0" * 12, {"heat": 1})[0], 404)
        _, after = self.call("DELETE", "/api/trends/" + skincare["id"])
        self.assertEqual([t["keyword"] for t in after["trends"]], ["露营"])
        self.assertEqual(self.call("DELETE", "/api/trends/" + skincare["id"])[0], 404)

    def test_her_library_feeds_the_ranking(self):
        self.call("POST", "/api/samples", {"title": "秋冬护肤步骤", "text": "秋冬护肤 / 换了面霜", "likes": 400})
        self.call("POST", "/api/samples", {"title": "超市那天", "text": "去超市买菜", "likes": 100})
        self.call("POST", "/api/samples", {"title": "早餐日常", "text": "早上煮咖啡", "likes": 100})
        _, out = self.call("POST", "/api/trends", {"text": "秋冬护肤\n露营"})
        by = {t["keyword"]: t for t in out["trends"]}
        self.assertEqual((out["baseline_interactions"], out["videos_with_numbers"]), (100, 3))
        self.assertEqual((by["秋冬护肤"]["her_videos"], by["秋冬护肤"]["lift_raw"], by["秋冬护肤"]["evidence_titles"]), (1, 4.0, ["秋冬护肤步骤"]))
        self.assertEqual([t["keyword"] for t in out["trends"]], ["秋冬护肤", "露营"])    # same heat unknown; her results decide

    def test_script_keywords_are_saved_and_checked(self):
        lines = [{"spoken_script_zh": "今天聊聊秋冬 护肤"}, {"spoken_script_zh": "现场说了露营", "audio_mode": "LIVE_CLIP"}]
        status, script = self.call("POST", "/api/scripts", {"lines": lines, "keywords": ["#秋冬护肤", "秋冬 护肤", 5, "露营"]})
        self.assertEqual((status, script["keywords"]), (201, ["秋冬护肤", "露营"]))
        self.assertEqual(self.call("PUT", "/api/scripts/" + script["id"], {"title": "换季"})[1]["keywords"], ["秋冬护肤", "露营"])
        self.assertEqual(self.call("PUT", "/api/scripts/" + script["id"], {"keywords": ["早餐"]})[1]["keywords"], ["早餐"])
        self.assertEqual(self.call("GET", "/api/scripts/" + script["id"])[1]["keywords"], ["早餐"])

        post = {"hashtags_zh": ["#夫妻日常"]}
        _, lint = self.call("POST", "/api/lint", {"lines": lines, "target_sec": 60, "post": post,
                                                  "keywords": ["秋冬护肤", "露营", "夫妻日常"]})
        coverage = {c["keyword"]: c for c in lint["keyword_coverage"]}
        self.assertTrue(coverage["秋冬护肤"]["spoken"])
        self.assertFalse(coverage["露营"]["covered"])
        self.assertEqual((coverage["夫妻日常"]["hashtags"], coverage["夫妻日常"]["spoken"]), (True, False))
        self.assertEqual(self.call("POST", "/api/lint", {"lines": lines})[1]["keyword_coverage"], [])

    def test_draft_puts_on_topic_scripts_first_and_keeps_the_keywords(self):
        self.connect_model()
        self.call("POST", "/api/samples", {"title": "超市那天", "text": "去超市买菜啦", "likes": 900})
        self.call("POST", "/api/samples", {"title": "护肤那期", "text": "秋冬护肤 / 我换了面霜嘛", "likes": 10})

        def exemplars():
            prompt = self.fake.prompt_for("ghostwriting")
            block = prompt.split("<retrieved_real_exemplars_from_her_past_videos>")[1]
            return prompt, block.split("</retrieved_real_exemplars_from_her_past_videos>")[0]

        status, script = self.call("POST", "/api/generate", {"notes": "聊聊护肤", "target_sec": 30, "keywords": ["#秋冬护肤"]})
        self.assertEqual((status, script["keywords"]), (201, ["秋冬护肤"]))
        prompt, block = exemplars()
        self.assertLess(block.index("护肤那期"), block.index("超市那天"))      # on topic comes first despite fewer likes
        self.assertIn('["秋冬护肤"]', prompt)
        self.assertNotIn("{{", prompt)

        status, script = self.call("POST", "/api/generate", {"notes": "聊聊护肤", "target_sec": 30})
        self.assertEqual((status, script["keywords"]), (201, []))
        prompt, block = exemplars()
        self.assertLess(block.index("超市那天"), block.index("护肤那期"))      # without keywords: best-performing first
        self.assertIn("[]", prompt.split("Target Keywords")[1][:120])

    def test_ideas_see_scored_trends_and_can_only_name_tracked_ones(self):
        self.connect_model()
        self.call("POST", "/api/samples", {"title": "护肤那期", "text": "秋冬护肤 / 我换了面霜嘛", "likes": 300})
        self.call("POST", "/api/samples", {"title": "超市那天", "text": "去超市买菜啦", "likes": 100})
        self.call("POST", "/api/trends", {"text": "秋冬护肤, 5000\n绝绝子好物, 9000"})
        status, out = self.call("POST", "/api/recommendations", {})
        self.assertEqual(status, 200)
        idea = out["result"]["ideas"][0]
        self.assertEqual((idea["trend_keywords"], idea["evidence_titles"]), (["秋冬护肤"], ["护肤那期"]))
        current = self.fake.prompt_for("content strategist").split("<current_trends>")[1].split("</current_trends>")[0]
        self.assertIn('"keyword": "秋冬护肤"', current)
        self.assertIn('"source": "entered_by_her"', current)
        self.assertIn('"her_lift": 1.5', current)                           # 300 against her median of 200
        self.assertNotIn("绝绝子", current)                                  # clashes with the voice profile: not offered

    def test_trend_lookup_through_copy_and_paste(self):
        status, out = self.call("POST", "/api/manual/prompt", {"task": "trends"})
        self.assertEqual((status, out["error"], out["reason"]), (502, "ai_failed", "no_focus"))
        days = {time.strftime("%Y-%m-%d")}
        status, out = self.call("POST", "/api/manual/prompt", {"task": "trends", "focus": "护肤"})
        days.add(time.strftime("%Y-%m-%d"))
        self.assertEqual(status, 200)
        self.assertIn('"focus_she_typed": "护肤"', out["prompt"])
        self.assertTrue(any("<today>%s</today>" % day in out["prompt"] for day in days))
        self.assertNotIn("{{", out["prompt"])

        status, out = self.call("POST", "/api/manual/reply", {"task": "trends", "reply": '{"cannot_browse": true}'})
        self.assertEqual((status, out["reason"]), (502, "cannot_browse"))
        reply = {"cannot_browse": False, "keywords": [
            {"keyword": "围炉煮茶", "why_zh": "榜单第三", "why_en": "Third on the list",
             "source_url": "https://example.com/list", "source_date": time.strftime("%Y-%m-%d")},
            {"keyword": "旧闻", "source_url": "https://example.com/old", "source_date": "2020-01-01"},
            {"keyword": "没来源"}]}
        text = "Here you go:\n```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"
        status, out = self.call("POST", "/api/manual/reply", {"task": "trends", "reply": text})
        self.assertEqual((status, out["added"], out["dropped_no_source"], out["dropped_too_old"]), (200, 1, 1, 1))
        self.assertEqual(len(out["trends"]), 1)
        found = out["trends"][0]
        self.assertEqual((found["keyword"], found["source"], found["source_url"], found["note_en"], found["heat_known"]),
                         ("围炉煮茶", "ai", "https://example.com/list", "Third on the list", False))


if __name__ == "__main__":
    unittest.main()
