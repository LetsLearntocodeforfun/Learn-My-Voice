"""Tests for deterministic leave-one-out voice calibration holdout benchmark (studio/benchmark.py)."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from studio import benchmark, creator, mcp_server, server, wordlist
from studio.store import ROOT, Store

WEB = ROOT / "studio" / "web"
ASSUMES = re.compile(r"\b(she|her|hers|herself|husband|wife|couple)\b", re.I)

ORGANIC_SCRIPTS = [
    (
        "周末超市补货",
        "上周末跟某人去 Costco 补货嘛 / 本来只打算买两袋咖啡豆\n"
        "结果站在零食货架前面研究了十分钟哈 / 最后还是拿了原味薯片\n"
        "然后我们又顺手抱了四盒小笼包啦 / 其实家里冷冻室还有两盒嘛",
    ),
    (
        "入冬干皮面霜",
        "一入冬吹暖气这张干皮真的服了 / 所以最近洗完澡赶紧厚涂一层嘛\n"
        "推开水水的 / 一点不糊脸啦 / 后续上妆不打架哈\n"
        "唯一的小缺点就是瓶子稍微有点沉 / 出差带不太方便嘛",
    ),
    (
        "周六买花碎片",
        "周六早上去练完 pilates 顺路拐去 Trader Joe's 买束花嘛\n"
        "结果结账的时候看到季节限定南瓜饼干哈 / 眼神一下就亮了\n"
        "不过说实话这款稍微有点甜 / 配黑咖啡刚刚好啦",
    ),
    (
        "软糯开衫分享",
        "最近评论区问得最多的就是身上这件开衫啦 / 这周已经穿了三次嘛\n"
        "摸起来软软糯糯的 / 随便抓一件就能出门巨省心哈\n"
        "洗了两次也没变形 / 但如果你怕热室内穿可能会觉得稍微厚一点啦",
    ),
    (
        "周日早餐日常",
        "周日早上先给自己煮杯热咖啡嘛 / 然后煎了两个鸡蛋做 brunch\n"
        "结果某人闻到香味五分钟就出来了哈 / 最搞笑的是还问今天有没有培根\n"
        "吃完去 gym 走一圈清清爽爽啦 / 下午回家继续看书嘛",
    ),
    (
        "晚霜空瓶记录",
        "这罐晚霜已经空瓶第二罐了嘛 / 连某人都顺手拿去涂手哈\n"
        "它不是那种一夜惊艳型 / 就是脸摸着软软的很稳啦\n"
        "前两天去 Sephora 顺便补了一罐 / 算下来真的蛮舒服的诶",
    ),
]


class TempStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def add_organic(self, count: int) -> None:
        for i in range(count):
            title, text = ORGANIC_SCRIPTS[i % len(ORGANIC_SCRIPTS)]
            self.store.create_sample(title="%s-%d" % (title, i + 1), text=text, is_sponsored=False)


class HoldoutCalibrationTest(TempStoreCase):
    def test_insufficient_samples_excludes_sponsored_and_unconfirmed_transcripts(self) -> None:
        empty = benchmark.evaluate_store(self.store)
        self.assertEqual(empty["readiness_tier"], "insufficient_samples")
        self.assertEqual((empty["organic_samples_total"], empty["samples_evaluated"]), (0, 0))
        self.assertIsNone(empty["own_mean_score"])
        self.assertIsNone(empty["slop_mean_score"])
        self.assertIsNone(empty["margin"])
        self.assertIsNone(empty["pairwise_separation_rate"])
        self.assertIsNone(empty["own_guardrail_clean_rate"])
        self.assertEqual(empty["slop_flagged_rate"], 1.0)

        self.add_organic(2)
        self.store.create_sample(title="商单", text=ORGANIC_SCRIPTS[2][1], is_sponsored=True)
        unconfirmed = self.store.create_sample(title="待确认转写", text=ORGANIC_SCRIPTS[3][1], kind="video")
        self.assertTrue(unconfirmed["needs_review"])

        two = benchmark.evaluate_store(self.store)
        self.assertEqual(two["readiness_tier"], "insufficient_samples")
        self.assertEqual(two["organic_samples_total"], 2)

        # Once the creator confirms the transcript, 3 confirmed organic items trigger holdout calibration.
        self.store.update_sample(unconfirmed["id"], {"needs_review": False})
        three = benchmark.evaluate_store(self.store)
        self.assertEqual(three["readiness_tier"], "calibrating")
        self.assertEqual((three["organic_samples_total"], three["samples_evaluated"]), (3, 3))
        self.assertGreater(three["margin"], 12.0)

    def test_calibrating_vs_calibrated_tiers_and_low_margin_honesty(self) -> None:
        self.add_organic(4)
        four = benchmark.evaluate_store(self.store)
        self.assertEqual(four["readiness_tier"], "calibrating")
        self.assertEqual(four["organic_samples_total"], 4)

        # Adding a 5th and 6th organic sample with strong separation reaches 'calibrated'.
        for title, text in ORGANIC_SCRIPTS[4:6]:
            self.store.create_sample(title=title, text=text, is_sponsored=False)
        six = benchmark.evaluate_store(self.store)
        self.assertEqual(six["readiness_tier"], "calibrated")
        self.assertEqual((six["organic_samples_total"], six["samples_evaluated"]), (6, 6))
        self.assertGreater(six["own_mean_score"], six["slop_mean_score"] + 15.0)
        self.assertGreaterEqual(six["pairwise_separation_rate"], 0.9)
        self.assertEqual(six["own_guardrail_clean_rate"], 1.0)
        self.assertEqual(six["slop_flagged_rate"], 1.0)
        self.assertTrue(six["reference_breaths_sufficient"])

        # A corpus made of generic AI-slop templates itself cannot separate from slop -> stays 'calibrating'.
        slop_store = Store(self.tmp / "slop_data")
        for idx, item in enumerate(benchmark.SYNTHETIC_SLOP_SCRIPTS):
            slop_store.create_sample(title="模板%d" % idx, text=item["text"], is_sponsored=False)
        slop_eval = benchmark.evaluate_store(slop_store)
        self.assertEqual(slop_eval["organic_samples_total"], len(benchmark.SYNTHETIC_SLOP_SCRIPTS))
        self.assertLess(slop_eval["margin"], benchmark.CALIBRATED_MIN_MARGIN)
        self.assertEqual(slop_eval["readiness_tier"], "calibrating")
        self.assertLess(slop_eval["own_guardrail_clean_rate"], 0.5)

    def test_wordlist_changes_update_clean_rate_and_invalidate_cache(self) -> None:
        self.add_organic(5)
        before = benchmark.evaluate_store(self.store)
        self.assertEqual(before["own_guardrail_clean_rate"], 1.0)
        self.assertEqual(before["wordlist"]["own_phrases"], 0)

        # Adding '小笼包' to creator's own word list flags the sample that mentions it via checks.lint_payload.
        wordlist.save(self.store, {"phrases": ["小笼包"]})
        after = benchmark.evaluate_store(self.store)
        self.assertEqual(after["wordlist"]["own_phrases"], 1)
        self.assertLess(after["own_guardrail_clean_rate"], 1.0)
        self.assertGreaterEqual(after["own_compliance_flagged_count"], 1)

    def test_sample_cap_bounds_holdout_work_and_reports_capped(self) -> None:
        self.add_organic(12)
        capped = benchmark.evaluate_store(self.store, max_samples=5)
        self.assertTrue(capped["capped"])
        self.assertEqual(capped["organic_samples_total"], 12)
        self.assertEqual(capped["samples_evaluated"], 5)
        self.assertEqual(capped["sample_cap"], 5)

    def test_bundled_benchmark_and_cli_modes(self) -> None:
        bundled = benchmark.run_bundled_benchmark()
        self.assertTrue(bundled["bundled_self_check"])
        self.assertEqual(bundled["readiness_tier"], "calibrated")
        self.assertGreaterEqual(bundled["margin"], 20.0)
        self.assertEqual(bundled["pairwise_separation_rate"], 1.0)
        self.assertEqual(bundled["own_guardrail_clean_rate"], 1.0)
        self.assertEqual(bundled["slop_flagged_rate"], 1.0)

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code = benchmark.main(["--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(out_json.getvalue())
        self.assertEqual(parsed["readiness_tier"], "calibrated")

        self.add_organic(5)
        out_text = io.StringIO()
        with contextlib.redirect_stdout(out_text):
            code_dir = benchmark.main(["--data-dir", str(self.store.data_dir)])
        self.assertEqual(code_dir, 0)
        self.assertIn("Voice calibration tier : calibrated", out_text.getvalue())


class ApiMcpAndUiTest(TempStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), self.thread.join(5)))

    def get_json(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            res = conn.getresponse()
            body = json.loads(res.read().decode("utf-8"))
            return res.status, body
        finally:
            conn.close()

    def test_http_endpoint_and_mcp_tool_agree(self) -> None:
        status0, body0 = self.get_json("/api/voice/calibration")
        self.assertEqual((status0, body0["readiness_tier"]), (200, "insufficient_samples"))

        self.add_organic(6)
        self.store.save_creator(creator.normalize({"name": "林溪", "pronoun": "they"}))
        status1, body1 = self.get_json("/api/voice/calibration")
        self.assertEqual((status1, body1["readiness_tier"]), (200, "calibrated"))

        mcp_res = mcp_server.call_tool(self.store, "get_voice_calibration", {})
        self.assertFalse(mcp_res["isError"])
        mcp_payload = json.loads(mcp_res["content"][0]["text"])
        self.assertEqual(mcp_payload["readiness_tier"], "calibrated")
        self.assertEqual(mcp_payload["own_mean_score"], body1["own_mean_score"])
        self.assertEqual(mcp_payload["slop_mean_score"], body1["slop_mean_score"])
        self.assertEqual(mcp_payload["margin"], body1["margin"])
        self.assertIn("no model calls", mcp_payload["note"])

    def test_voice_page_ui_strings_and_wiring_are_bilingual_and_gender_neutral(self) -> None:
        app_js = (WEB / "app.js").read_text(encoding="utf-8")
        pages_js = (WEB / "pages.js").read_text(encoding="utf-8")
        self.assertIn('id="voice-calibration"', app_js)
        self.assertIn("paintVoiceCalibration(", app_js)
        self.assertIn("async function paintVoiceCalibration(", pages_js)
        self.assertIn("'/api/voice/calibration'", pages_js)

        zh_start = app_js.index("  zh: {")
        en_start = app_js.index("  en: {", zh_start)
        zh_block = app_js[zh_start:en_start]
        en_block = app_js[en_start:app_js.index("\n};\n", en_start)]
        cal_keys = [
            "v_cal_title", "v_cal_tier_insufficient_samples", "v_cal_tier_calibrating", "v_cal_tier_calibrated",
            "v_cal_anchor", "v_cal_anchor_v", "v_cal_anchor_capped", "v_cal_need_more",
            "v_cal_own_vs_slop", "v_cal_scores_v", "v_cal_sep", "v_cal_sep_v",
            "v_cal_fp", "v_cal_fp_v", "v_cal_slop_gate", "v_cal_slop_gate_v",
        ]
        for key in cal_keys:
            self.assertIn(key + ": '", zh_block, key)
            self.assertIn(key + ": '", en_block, key)
        en_values = re.findall(r"\bv_cal_[a-z_]+: '((?:[^'\\]|\\.)*)'", en_block)
        self.assertEqual(len(en_values), len(cal_keys))
        self.assertIsNone(ASSUMES.search(" ".join(en_values)))


if __name__ == "__main__":
    unittest.main()
