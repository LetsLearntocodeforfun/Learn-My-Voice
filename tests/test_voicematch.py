"""Tests for the voice-match score: four speech habits counted in code and compared with the creator's own material."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from studio import checks, corpus, mcp_server, server, voicematch
from studio.store import Store

OWN = [
    "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈 / 然后我们拿了四盒小笼包啦",
    "入冬以后脸特别干嘛 / 所以我换了一套护肤步骤 / 结果面霜用得最勤哈 / 其实 toner 也换了",
    "周日早上煮咖啡啦 / 然后做了个三明治 / 我老公说好吃嘛 / 结果他吃了两个哈",
    "今天去 Trader Joe's 啦 / 然后又买了小笼包 / 其实家里还有嘛 / 但是忍不住哈",
    "最近风特别大嘛 / 所以出门都戴帽子 / 结果帽子被吹跑了哈 / 然后我老公去追啦",
]
IN_HER_WAY = [{"spoken_script_zh": "这周又去超市补货啦 / 然后拿了两盒水饺"},
              {"spoken_script_zh": "结果我老公又走不动哈 / 其实家里还有嘛"}]
NOT_HER_WAY = [{"spoken_script_zh": "综上所述，这款面霜不仅质地非常轻盈而且吸收速度十分令人印象深刻。与此同时，它的保湿效果也值得一提，"
                                    "因此我强烈推荐给大家呗。总而言之这是一个 amazing 的 serum 选择。然而价格略高。"}]


def reference(times=2):
    return voicematch.Reference(OWN * times)


class CountingTest(unittest.TestCase):
    def test_breaths_endings_beginnings_and_english_are_counted(self):
        counts = voicematch.Counts().add_text("然后呢我们去了 Costco 嘛 / [轻笑] / 结果，买了 3 盒！所以说 really 好吃哈")
        self.assertEqual(counts.breaths, 4)                                  # a cue alone is not a breath
        self.assertEqual(dict(counts.lengths), {9: 1, 2: 1, 4: 1, 7: 1})
        self.assertEqual(dict(counts.endings), {"嘛": 1, "哈": 1})
        self.assertEqual(dict(counts.starts), {"然后呢": 1, "结果": 1, "所以说": 1})   # the longest connector wins
        self.assertEqual((counts.english_breaths, dict(counts.english_words)), (2, {"costco": 1, "really": 1}))
        for nothing in (None, 5, "", "  ", "[音乐]"):
            self.assertEqual(voicematch.Counts().add_text(nothing).breaths, 0)

    def test_a_live_clip_is_somebody_elses_words(self):
        lines = [{"spoken_script_zh": "这周又去超市啦"}, {"spoken_script_zh": "老板说今天打折呗", "audio_mode": "live_clip"},
                 "oops", {"spoken_script_zh": None}, {}]
        self.assertEqual(voicematch.script_text_lines(lines), ["这周又去超市啦", "", ""])
        self.assertEqual(voicematch.script_text_lines("oops"), [])

    def test_material_without_one_script_is_the_whole_minus_that_script(self):
        whole = voicematch.count_texts(OWN)
        for i, text in enumerate(OWN):
            rest, direct = whole.without(voicematch.Counts().add_text(text)), voicematch.count_texts(OWN[:i] + OWN[i + 1:])
            for field in ("breaths", "english_breaths", "lengths", "endings", "starts", "english_words"):
                self.assertEqual(getattr(rest, field), getattr(direct, field), field)

    def test_breath_lengths_are_the_ones_the_library_page_shows(self):
        measured = corpus.voice_stats(OWN)["clause_length_units"]
        whole = voicematch.count_texts(OWN)
        self.assertEqual((whole.percentile(5), whole.percentile(50), whole.percentile(95)),
                         (measured["p5"], measured["median"], measured["p95"]))
        self.assertEqual(voicematch.Counts().percentile(50), 0)


class ScoreTest(unittest.TestCase):
    def test_a_script_in_her_way_scores_inside_her_own_range_and_one_that_is_not_far_below(self):
        ref = reference()
        hers, other = voicematch.score(IN_HER_WAY, ref), voicematch.score(NOT_HER_WAY, ref)
        self.assertTrue(hers["available"] and other["available"])
        band = hers["band"]
        self.assertEqual(band["scripts"], 8)                 # two of the ten have three breaths: too short to be scored
        self.assertTrue(band["low"] <= band["median"] <= band["high"] <= 100)
        self.assertGreaterEqual(hers["score"], band["low"])
        self.assertLess(other["score"], band["low"] - 30)
        self.assertEqual(set(hers["parts"]), {"breath", "endings", "starts", "english"})
        for part in list(hers["parts"].values()) + list(other["parts"].values()):
            self.assertTrue(0.0 <= part["score"] <= 1.0)
        self.assertEqual(hers["score"], round(100 * sum(p["score"] for p in hers["parts"].values()) / 4))

    def test_each_part_says_what_was_counted_on_both_sides(self):
        parts = voicematch.score(NOT_HER_WAY, reference())["parts"]
        self.assertEqual(parts["breath"]["own"], {"p5": 6, "median": 8, "p95": 12})
        self.assertEqual((parts["breath"]["script"]["median"], parts["breath"]["script"]["inside_pct"]), (11, 43))
        self.assertEqual((parts["endings"]["own"]["rate_pct"], parts["endings"]["own"]["top"]), (74, ["嘛", "哈", "啦"]))
        self.assertEqual((parts["endings"]["script"]["rate_pct"], parts["endings"]["script"]["unfamiliar"]), (14, ["呗"]))
        self.assertEqual(parts["starts"]["own"]["top"], ["结果", "然后", "所以"])
        self.assertEqual(parts["starts"]["script"]["unfamiliar"], ["综上所述", "与此同时", "因此", "总而言之", "然而"])
        self.assertEqual(parts["english"]["script"]["unfamiliar"], ["amazing", "serum"])

    def test_a_short_script_is_not_marked_down_for_what_chance_explains(self):
        # She mixes English into 16% of her breaths. Four breaths with none is what chance gives, so it costs nothing.
        four = [{"spoken_script_zh": "这周又去超市补货啦 / 然后拿了两盒水饺 / 结果我老公又走不动哈 / 其实家里还有嘛"}]
        english = voicematch.score(four, reference())["parts"]["english"]
        self.assertEqual((english["score"], english["own"]["rate_pct"], english["script"]["rate_pct"]), (1.0, 16, 0))
        self.assertTrue(english["script"]["within_chance"])          # 16% against 0% with full marks: the page says why
        # Forty breaths with none is not chance any more.
        forty = [{"spoken_script_zh": four[0]["spoken_script_zh"]}] * 10
        english = voicematch.score(forty, reference())["parts"]["english"]
        self.assertLess(english["score"], 0.7)
        self.assertFalse(english["script"]["within_chance"])
        # Rates that are as good as equal need no remark.
        endings = voicematch.score(four, reference())["parts"]["endings"]
        self.assertEqual((endings["own"]["rate_pct"], endings["script"]["rate_pct"]), (74, 75))
        self.assertFalse(endings["script"]["within_chance"])
        self.assertEqual(voicematch._closeness(0.3, 0.3, 10), 1.0)
        self.assertEqual(voicematch._closeness(1.0, 0.0, 10), 0.0)
        self.assertEqual(voicematch._closeness(0.0, 0.0, 0), 1.0)

    def test_too_little_on_either_side_gives_no_number(self):
        thin = voicematch.score(IN_HER_WAY, voicematch.Reference(OWN[:1]))
        self.assertEqual((thin["available"], thin["reason"], thin["reference"]), (False, "too_little_material", {"scripts": 1, "breaths": 3}))
        self.assertNotIn("score", thin)
        short = voicematch.score([{"spoken_script_zh": "这周又去超市啦"}], reference())
        self.assertEqual((short["available"], short["reason"], short["script"]), (False, "too_short", {"breaths": 1}))
        self.assertEqual(short["needed"], {"reference_breaths": voicematch.MIN_REFERENCE_BREATHS,
                                           "script_breaths": voicematch.MIN_SCRIPT_BREATHS})
        for lines in (None, "oops", [], [5]):
            self.assertEqual(voicematch.score(lines, reference())["reason"], "too_short")

    def test_her_own_range_needs_several_scripts(self):
        one_long = voicematch.Reference([" / ".join(OWN * 2)])         # enough breaths, but a single script
        scored = voicematch.score(IN_HER_WAY, one_long)
        self.assertTrue(scored["available"])
        self.assertIsNone(scored["band"])

    def test_one_line_is_described_by_facts_not_a_score(self):
        ref = reference()
        self.assertEqual(voicematch.line_fit("这周又去超市补货啦 / 然后拿了两盒水饺", ref), {"breaths": 2, "inside": 2, "unfamiliar": []})
        fit = voicematch.line_fit("综上所述这款面霜的质地非常轻盈而且吸收速度十分令人印象深刻呗 / really 好", ref)
        self.assertEqual((fit["breaths"], fit["inside"], fit["unfamiliar"]), (2, 0, ["呗", "综上所述", "really"]))
        self.assertIsNone(voicematch.line_fit("这周又去超市啦", voicematch.Reference(OWN[:1])))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def add_own(self, times=2):
        for n in range(times):
            for i, text in enumerate(OWN):
                self.store.create_sample(title="第%d-%d篇" % (n, i), text=text)


class LibraryTest(StoreCase):
    def test_the_check_of_a_script_carries_the_score(self):
        empty = checks.lint_payload(self.store, IN_HER_WAY, 30)["voice_match"]
        self.assertEqual((empty["available"], empty["reason"]), (False, "too_little_material"))
        self.add_own()
        scored = checks.lint_payload(self.store, IN_HER_WAY, 30)["voice_match"]
        self.assertTrue(scored["available"])
        self.assertEqual(scored["reference"], {"scripts": 10, "breaths": 38})
        self.assertGreater(scored["score"], checks.lint_payload(self.store, NOT_HER_WAY, 30)["voice_match"]["score"] + 30)

    def test_only_her_own_confirmed_non_sponsored_words_are_the_reference(self):
        self.add_own()
        before = voicematch.reference_for(self.store).facts()
        self.store.create_sample(title="商单", text=NOT_HER_WAY[0]["spoken_script_zh"], is_sponsored=True)
        video = self.store.create_sample(title="机器转写", text=NOT_HER_WAY[0]["spoken_script_zh"], kind="video")
        self.assertTrue(video["needs_review"])
        self.assertEqual(voicematch.reference_for(self.store).facts(), before)       # neither counts
        self.store.update_sample(video["id"], {"needs_review": False})               # she read it and confirmed it
        self.assertEqual(voicematch.reference_for(self.store).facts()["scripts"], before["scripts"] + 1)

    def test_the_library_is_counted_again_only_when_it_changed(self):
        self.add_own()
        first = voicematch.reference_for(self.store)
        self.assertIs(voicematch.reference_for(self.store), first)
        checks.lint_payload(self.store, IN_HER_WAY, 30)
        self.assertIs(voicematch.reference_for(self.store), first)                   # a check does not count it again
        sample = self.store.create_sample(title="新的一篇", text=OWN[0])
        second = voicematch.reference_for(self.store)
        self.assertIsNot(second, first)
        self.store.update_sample(sample["id"], {"text": OWN[1]})
        third = voicematch.reference_for(self.store)
        self.assertIsNot(third, second)
        self.store.update_sample(sample["id"], {"is_sponsored": True})
        fourth = voicematch.reference_for(self.store)
        self.assertIsNot(fourth, third)
        self.store.delete_sample(sample["id"])
        self.assertIsNot(voicematch.reference_for(self.store), fourth)

    def test_an_ai_application_gets_the_score_with_its_check(self):
        self.add_own()
        result = mcp_server.call_tool(self.store, "check_script", {"script_lines": IN_HER_WAY, "target_duration_sec": 10})
        self.assertFalse(result["isError"])
        self.assertTrue(json.loads(result["content"][0]["text"])["voice_match"]["available"])


class ServerTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        res = conn.getresponse()
        payload = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, payload

    def test_the_editor_gets_the_score_after_every_save(self):
        status, out = self.call("POST", "/api/lint", {"lines": IN_HER_WAY, "target_sec": 30})
        self.assertEqual((status, out["voice_match"]["available"], out["voice_match"]["reason"]), (200, False, "too_little_material"))
        for i, text in enumerate(OWN * 2):
            self.call("POST", "/api/samples", {"title": "第%d篇" % i, "text": text})
        status, out = self.call("POST", "/api/lint", {"lines": IN_HER_WAY, "target_sec": 30})
        match = out["voice_match"]
        self.assertEqual((status, match["available"], match["band"]["scripts"]), (200, True, 8))
        self.assertGreaterEqual(match["score"], match["band"]["low"])
        status, out = self.call("POST", "/api/lint", {"lines": "oops"})
        self.assertEqual((status, out["voice_match"]["reason"]), (200, "too_short"))


if __name__ == "__main__":
    unittest.main()
