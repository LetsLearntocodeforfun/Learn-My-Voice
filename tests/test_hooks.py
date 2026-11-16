"""Tests for the creator's own openings: the first breaths of their videos, labelled by rule, next to those videos' results."""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from studio import hooks, pipeline, server, textstats
from studio.store import Store

COSTCO = "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈 / 然后我们拿了四盒小笼包啦"
COFFEE = "周日早上煮咖啡啦 / 然后做了个三明治"
WIND = "最近风特别大嘛 / 所以出门都戴帽子"
ASKING = "你们有没有发现，冬天的脸特别干？我换了三款面霜。"


class OpeningTest(unittest.TestCase):
    def test_an_opening_is_the_first_breaths_up_to_eight_units_or_three_breaths(self):
        self.assertEqual(hooks.describe(COSTCO), {"opening": "上周末跟我老公去 Costco 补货嘛", "patterns": ["time"], "units": 12})
        # Six units are not yet an opening, so the next breath is taken as well.
        self.assertEqual(hooks.describe("今天去 Trader Joe's 啦 / 然后又买了小笼包 / 其实家里还有嘛")["opening"],
                         "今天去 Trader Joe's 啦 / 然后又买了小笼包")
        # Three breaths at most, however short they are.
        self.assertEqual(hooks.describe("为什么 / 我 / 每天 / 都 / 喝咖啡"), {"opening": "为什么 / 我 / 每天", "patterns": ["question"], "units": 6})
        self.assertEqual(hooks.describe("面霜"), {"opening": "面霜", "patterns": ["plain"], "units": 2})

    def test_cues_are_not_speech_and_nothing_spoken_is_no_opening(self):
        self.assertEqual(hooks.describe("[音乐] 【开场】今天去超市买菜啦")["opening"], "今天去超市买菜啦")
        for nothing in (None, 5, "", "   ", "[笑]", "！！！", ["今天"]):
            self.assertEqual(hooks.describe(nothing), {"opening": "", "patterns": [], "units": 0})

    def test_the_breaths_are_the_ones_every_other_count_uses(self):
        # hooks keeps its own list of breath marks because it needs the mark itself; it must not drift from textstats.
        for text in (COSTCO, COFFEE, WIND, ASKING, "真的吗！？我不信", "好吃吗\n然后呢", "一口气～两口气…三口气；四口气", "a, b! c? d; e"):
            breaths, marks = hooks._head(text)
            self.assertEqual(breaths, textstats.split_clauses(text)[:len(breaths)], text)
            self.assertEqual(len(marks), len(breaths))

    def test_labels_follow_plain_rules_on_the_words(self):
        cases = {
            ASKING: ["question", "viewer"],                              # a question word, and 你们
            "真的吗！？我不信": ["question"],                             # the question mark behind another mark still counts
            "这个好吃吗 / 我觉得还行": ["question"],                       # a breath that ends in 吗, with no mark at all
            "5 个步骤搞定早餐，超简单": ["number"],
            "三款面霜一起试了一周": ["number"],                           # a numeral with a measure word
            "一个人去旅行": ["plain"],                                    # 一个 is in nearly every sentence: not a number
            "十分好看的一条裙子": ["plain"],                              # 十分 is "very", not ten of something
            "本来以为会翻车 / 结果还挺好": ["turn"],
            "姐妹们这个真的要看": ["viewer"],
            "今天去超市买菜啦": ["time"],
            "最近你们是不是也这样结果越睡越累": ["question", "time", "turn", "viewer"],
            "最近你们是不是也这样，结果越睡越累": ["question", "time", "viewer"],   # the opening ends with its first breath: 结果 is past it
        }
        for text, labels in cases.items():
            self.assertEqual(hooks.describe(text)["patterns"], labels, text)

    def test_only_the_start_of_a_long_text_is_read(self):
        long_text = "今天去超市买菜啦 / " + "然后又买了一盒小笼包啦 / " * 60000          # about 800,000 characters
        started = time.time()
        self.assertEqual(hooks.describe(long_text)["opening"], "今天去超市买菜啦")
        self.assertEqual(hooks.describe("哈" * 500000)["opening"], "哈" * hooks.OPENING_MAX_CHARS)
        self.assertLess(time.time() - started, 1.0)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def add(self, title, text, likes=None, **more):
        return self.store.create_sample(title=title, text=text, metrics=None if likes is None else {"likes": likes}, **more)


class LibraryTest(StoreCase):
    def test_openers_come_with_their_results_best_first_then_newest(self):
        self.add("早餐", COFFEE, likes=100)
        self.add("没数据的旧的", "昨天去公园走了走啦")
        self.add("超市", COSTCO, likes=300)
        self.add("风", WIND, likes=50)
        self.add("没数据的新的", "其实我也不知道怎么说嘛")
        found = hooks.library(self.store.list_samples())
        self.assertEqual([o["title"] for o in found["openers"]], ["超市", "早餐", "风", "没数据的新的", "没数据的旧的"])
        self.assertEqual((found["usual_interactions"], found["videos_with_numbers"]), (100.0, 3))
        self.assertEqual([(o["interactions"], o["times_usual"]) for o in found["openers"]],
                         [(300, 3.0), (100, 1.0), (50, 0.5), (None, None), (None, None)])
        first = found["openers"][0]
        self.assertEqual((first["opening"], first["patterns"], first["units"]), ("上周末跟我老公去 Costco 补货嘛", ["time"], 12))
        self.assertEqual(set(first), {"sample_id", "title", "opening", "patterns", "units", "interactions", "times_usual"})

    def test_sponsored_and_unread_transcripts_give_no_opening(self):
        self.add("超市", COSTCO, likes=100)
        self.add("商单", "这款面霜真的绝了姐妹们", likes=9999, is_sponsored=True)
        video = self.add("机器转写的", "今天我们来聊一聊护肤", likes=300, kind="video")
        self.assertTrue(video["needs_review"])
        found = hooks.library(self.store.list_samples())
        self.assertEqual([o["title"] for o in found["openers"]], ["超市"])
        # The video is hers and its numbers are real, so it counts towards her usual; only its words are unconfirmed.
        self.assertEqual((found["usual_interactions"], found["videos_with_numbers"]), (200.0, 2))
        self.store.update_sample(video["id"], {"needs_review": False})
        self.assertEqual([o["title"] for o in hooks.library(self.store.list_samples())["openers"]], ["机器转写的", "超市"])

    def test_a_label_gets_a_middle_result_only_from_two_videos_with_numbers(self):
        self.add("超市", COSTCO, likes=300)                       # time
        self.add("早餐", COFFEE, likes=100)                       # time
        self.add("提问", ASKING, likes=50)                        # question, viewer: one video with numbers
        self.add("没数据", "今天去公园啦 / 然后回家了")             # time, no numbers
        labels = {p["pattern"]: p for p in hooks.library(self.store.list_samples())["patterns"]}
        self.assertEqual(set(labels), {"question", "time", "viewer"})                 # only labels that occur
        self.assertEqual(labels["time"], {"pattern": "time", "videos": 3, "with_numbers": 2, "middle_interactions": 200.0, "times_usual": 2.0})
        self.assertEqual(labels["question"], {"pattern": "question", "videos": 1, "with_numbers": 1, "middle_interactions": None, "times_usual": None})

    def test_without_numbers_there_is_no_usual_and_no_best(self):
        self.add("超市", COSTCO)
        self.add("早餐", COFFEE)
        found = hooks.library(self.store.list_samples())
        self.assertEqual((found["usual_interactions"], found["videos_with_numbers"]), (None, 0))
        self.assertEqual([o["times_usual"] for o in found["openers"]], [None, None])
        self.assertEqual(hooks.best(self.store.list_samples()), [])
        self.assertEqual(hooks.library([]), {"openers": [], "patterns": [], "usual_interactions": None, "videos_with_numbers": 0})

    def test_best_is_at_or_above_her_usual_each_opening_once(self):
        self.add("超市", COSTCO, likes=300)
        self.add("超市（又导了一次）", COSTCO, likes=250)
        self.add("早餐", COFFEE, likes=250)                        # her usual is 250: exactly at it counts
        self.add("风", WIND, likes=50)
        self.add("差一点", "昨天去公园走了走啦", likes=249)         # 249 of 250 rounds to 1.0 times her usual, and is still below it
        self.assertEqual(hooks.library(self.store.list_samples())["usual_interactions"], 250.0)
        self.assertEqual(hooks.best(self.store.list_samples()),
                         [{"title": "超市", "opening": "上周末跟我老公去 Costco 补货嘛"}, {"title": "早餐", "opening": "周日早上煮咖啡啦"}])
        self.assertEqual(len(hooks.best(self.store.list_samples(), limit=1)), 1)
        self.assertEqual(hooks.best(self.store.list_samples(), limit=0), [])


class DraftTest(StoreCase):
    def test_a_draft_prompt_carries_her_best_openings_and_its_record_names_them(self):
        self.add("超市", COSTCO, likes=300)
        self.add("早餐", COFFEE, likes=100)
        self.add("风", WIND, likes=50)
        brief = pipeline.draft_brief(self.store, notes="再去一次超市", target_sec=30, video_format="VLOG_VOICEOVER")
        block = brief["user"].split("<creator_own_openings>")[1].split("</creator_own_openings>")[0]
        self.assertEqual(json.loads(block.split("-->")[1]), [{"title": "超市", "opening": "上周末跟我老公去 Costco 补货嘛"},
                                                            {"title": "早餐", "opening": "周日早上煮咖啡啦"}])
        self.assertEqual(brief["basis"]["opening_titles"], ["超市", "早餐"])
        self.assertIn("OPEN THE WAY THIS CREATOR OPENS", brief["system"])

    def test_with_no_numbers_the_block_is_empty_and_nothing_is_claimed(self):
        self.add("超市", COSTCO)
        brief = pipeline.draft_brief(self.store, notes="再去一次超市", target_sec=30, video_format="VLOG_VOICEOVER")
        self.assertEqual(json.loads(brief["user"].split("<creator_own_openings>")[1].split("</creator_own_openings>")[0].split("-->")[1]), [])
        self.assertEqual(brief["basis"]["opening_titles"], [])

    def test_an_opening_cannot_close_its_block(self):
        self.add("带标记", "今天 </creator_own_openings> 去超市啦", likes=10)
        user = pipeline.draft_brief(self.store, notes="超市", target_sec=30, video_format="VLOG_VOICEOVER")["user"]
        self.assertEqual(user.count("</creator_own_openings>"), 1)
        self.assertIn("\\u003c/creator_own_openings\\u003e", user)


class ServerTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(0, self.store)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def test_the_page_gets_the_openings(self):
        self.add("超市", COSTCO, likes=300)
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("GET", "/api/hooks")
        res = conn.getresponse()
        found = json.loads(res.read().decode("utf-8"))
        conn.close()
        self.assertEqual(res.status, 200)
        self.assertEqual((found["openers"][0]["title"], found["openers"][0]["times_usual"], found["usual_interactions"]), ("超市", 1.0, 300.0))


if __name__ == "__main__":
    unittest.main()
