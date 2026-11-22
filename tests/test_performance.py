"""Large-library scale, latency, and SQLite query-count bounds suite.

Seeds a realistic multi-year creator store:
- 300 scripts
- 600 library samples (520 confirmed non-sponsored, 80 sponsored)
- 5,000 audience comments + 120 comment summaries (60 model-sourced, 60 local word-list)
- 1,500 metric snapshots
- 200 video plans + open keyword suggestions
- 80 tracked trend keywords + observations

Every hot studio path is checked for BOTH wall-clock latency (CI-safe < 1.0s ceiling) AND bounded O(1) SQLite
statement count via ``conn.set_trace_callback``, catching any accidental O(N) N+1 query loop immediately.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable, List, Tuple
from unittest import mock

from studio import backup, benchmark, checks, creator, planner, rednote_analytics, trends, voicematch
from studio.store import EXAMPLE_DNA_PATH, Store

CI_WALL_BUDGET_SEC = 1.0

SAMPLE_TEMPLATES = (
    (
        "上周末跟我老公去 Costco 补货嘛 / 本来只打算买两袋咖啡豆\n"
        "结果某人站在零食货架前面研究了十分钟哈 / 最后还是拿了原味薯片\n"
        "然后我们又顺手抱了四盒小笼包啦 / 其实家里冷冻室还有两盒嘛"
    ),
    (
        "一入冬吹暖气我这张干皮真的服了 / 所以最近洗完澡赶紧厚涂一层秋冬护肤面霜嘛\n"
        "推开水水的 / 一点不糊脸啦 / 后续上妆不打架哈\n"
        "唯一的小缺点就是瓶子稍微有点沉 / 出差带不太方便嘛"
    ),
    (
        "周六早上去练完 pilates 顺路拐去 Trader Joe's 买束花嘛\n"
        "结果结账的时候看到季节限定南瓜饼干哈 / 我老公眼神一下就亮了\n"
        "不过说实话这款稍微有点甜 / 配黑咖啡刚刚好啦"
    ),
    (
        "最近评论区问得最多的就是我身上这件开衫啦 / 这周已经穿了三次嘛\n"
        "摸起来软软糯糯的 / 随便抓一件就能出门巨省心哈\n"
        "洗了两次也没变形 / 但如果你怕热室内穿可能会觉得稍微厚一点啦"
    ),
    (
        "周日早上趁某人还没醒 / 先给自己煮杯热咖啡嘛\n"
        "然后煎了两个鸡蛋做 brunch / 结果他闻到香味五分钟就出来了哈\n"
        "最搞笑的是他还问今天有没有培根 / 吃完去 gym 走一圈清清爽爽啦"
    ),
)


class LargeLibraryPerformanceTest(unittest.TestCase):
    tmp: Path
    store: Store
    active_script_id: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lmv-perf-"))
        cls.store = Store(cls.tmp / "data")

        dna = json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8"))
        cls.store.save_dna(dna)
        cls.store.save_creator(creator.normalize({
            "name": "小满",
            "pronoun": "she",
            "niches": ["秋冬护肤", "超市采购", "周末日常"],
            "never": ["宝子们"],
        }))

        now = 1_795_000_000.0
        lines_template = [
            {
                "line_no": 1,
                "beat_type": "HOOK",
                "time_range_sec": "0-6",
                "audio_mode": "VOICEOVER",
                "spoken_script_zh": "上周末跟我老公去 Costco 补货嘛 / 结果又拿了四盒小笼包啦",
                "delivery_cue": "自然聊天",
                "english_partner_translation": "Went on a Costco run last weekend and grabbed soup dumplings again",
                "visual_broll_en_zh": "购物车镜头",
                "onscreen_flower_text_zh": "又没忍住",
            },
            {
                "line_no": 2,
                "beat_type": "VLOG_STORY",
                "time_range_sec": "6-16",
                "audio_mode": "VOICEOVER",
                "spoken_script_zh": "一入冬吹暖气干皮真的服了 / 洗完澡赶紧厚涂一层水水的面霜哈",
                "delivery_cue": "放松",
                "english_partner_translation": "Dry winter heating calls for a rich cream right after showering",
                "visual_broll_en_zh": "涂面霜特写",
                "onscreen_flower_text_zh": "干皮续命",
            },
            {
                "line_no": 3,
                "beat_type": "CLOSING_CTA",
                "time_range_sec": "16-24",
                "audio_mode": "VOICEOVER",
                "spoken_script_zh": "其实算下来蛮划算的嘛 / 你们入冬有什么好物也可以告诉我哈",
                "delivery_cue": "轻快收尾",
                "english_partner_translation": "Pretty good value honestly; tell me your winter favorites too",
                "visual_broll_en_zh": "桌面合集",
                "onscreen_flower_text_zh": "",
            },
        ]
        lines_json = json.dumps(lines_template, ensure_ascii=False)
        post_json = json.dumps({
            "title_options": [{"title_zh": "周末 Costco 补货与秋冬护肤碎片"}],
            "caption_body_zh": "周末日常记录，干皮面霜分享。",
            "hashtags_zh": ["#秋冬护肤", "#超市采购"],
        }, ensure_ascii=False)
        kw_json = json.dumps(["秋冬护肤", "超市采购"], ensure_ascii=False)

        script_rows = []
        for i in range(300):
            sid = "scr%09d" % i
            ts = now + i * 60.0
            script_rows.append((
                sid,
                "周末碎片与护肤第%d期" % (i + 1),
                "NORMAL_ORGANIC",
                "VLOG_VOICEOVER",
                60,
                "记录周末采购和护肤心得 %d" % i,
                lines_json,
                len(lines_template),
                lines_template[0]["spoken_script_zh"][:60],
                post_json,
                lines_json if (i % 2 == 0) else None,
                "DRAFT",
                kw_json,
                None,
                ts,
                ts,
            ))
        cls.active_script_id = script_rows[-1][0]

        sample_rows = []
        for i in range(600):
            sm_id = "smp%09d" % i
            is_sponsored = 1 if i >= 520 else 0
            text = SAMPLE_TEMPLATES[i % len(SAMPLE_TEMPLATES)]
            views = 2500 + (i % 40) * 350
            likes = 90 + (i % 25) * 14
            collects = 60 + (i % 20) * 11
            comments_n = 12 + (i % 15) * 3
            posted_day = "2026-%02d-%02d" % (1 + (i % 10), 1 + (i % 28))
            linked_script = ("scr%09d" % i) if i < 60 else None
            origin = "script" if linked_script else ""
            ts = now + i * 30.0
            sample_rows.append((
                sm_id,
                "历史作品第%d篇｜秋冬护肤与采购" % (i + 1),
                text,
                is_sponsored,
                views,
                likes,
                collects,
                comments_n,
                "script",
                None,
                "",
                0,
                ts,
                origin,
                linked_script,
                posted_day,
                "",
                "2026-11-15",
                None,
                "",
                ts,
                ts,
            ))

        # 1,500 metric snapshots across 500 samples (3 snapshots each on distinct days)
        snap_rows = []
        snap_days = ("2026-11-01", "2026-11-08", "2026-11-15")
        for i in range(500):
            sm_id = "smp%09d" % i
            base_v = 1200 + (i % 40) * 200
            for d_idx, read_on in enumerate(snap_days):
                snap_id = "snp%06d_%d" % (i, d_idx)
                v = base_v + d_idx * 650
                snap_rows.append((
                    snap_id, sm_id, read_on,
                    v, 50 + d_idx * 20, 35 + d_idx * 15, 8 + d_idx * 4,
                    4 + d_idx * 2, 2 + d_idx, 8.5, 21.4, 46.2,
                    now + d_idx * 86400.0,
                ))

        # 5,000 audience comments across 100 samples (50 comments each) + 120 sentiment summaries (60 AI/model, 60 local)
        comment_rows = []
        for s_idx in range(100):
            sm_id = "smp%09d" % s_idx
            for c_idx in range(50):
                cid = "cmt%04d_%04d" % (s_idx, c_idx)
                is_q = 1 if (c_idx % 5 == 0) else 0
                txt = (
                    "请问这款面霜冬天干皮够不够润呀？第%d条" % c_idx
                    if is_q
                    else "喜欢这种松弛真实的超市采购日常第%d条" % c_idx
                )
                comment_rows.append((
                    cid, sm_id, txt, (c_idx % 12), "positive" if not is_q else "neutral", is_q, now + c_idx,
                ))

        sentiment_rows = []
        for s_idx in range(120):
            sm_id = "smp%09d" % s_idx
            source = "ai" if s_idx < 60 else "local"
            # Intentionally omit ``quoted`` on model questions so analytics_overview must check sample_comments!
            summary = {
                "overall_sentiment": "positive" if source == "ai" else "not_judged",
                "summary_zh": "观众主要关心干皮保湿力度和采购清单。",
                "audience_questions": [
                    {
                        "question_zh": "请问这款面霜冬天干皮够不够润呀",
                        "question_en": "Is this cream rich enough for dry winter skin?",
                        "demand_count": 6 + (s_idx % 5),
                    }
                ],
                "followup_video_angles": [
                    {
                        "title_zh": "干皮过冬面霜追测第%d期" % (s_idx + 1),
                        "title_en": "Winter cream follow-up #%d" % (s_idx + 1),
                        "video_format": "SINGLE_PRODUCT_SHARE",
                        "target_duration_sec": 60,
                        "rationale_zh": "回应评论区高频提问",
                    }
                ],
            }
            sentiment_rows.append((sm_id, json.dumps(summary, ensure_ascii=False), source, now))

        # 200 video plans (160 scheduled across Nov/Dec 2026, 40 unscheduled, 60 linked to scripts)
        plan_rows = []
        for i in range(200):
            pid = "pln%09d" % i
            sched = ("2026-11-%02d" % (1 + (i % 30))) if i < 160 else ""
            linked_scr = ("scr%09d" % i) if i < 60 else None
            status = "IDEA" if (i % 4 == 0 and not linked_scr) else "PLANNED"
            ts = now + i * 10.0
            plan_rows.append((
                pid,
                "拍摄计划第%d条｜秋冬好物与日常" % (i + 1),
                "VLOG_VOICEOVER",
                60,
                sched,
                status,
                "manual",
                "先拍开箱再拍上脸细节 %d" % i,
                "承接上一期高收藏话题",
                "Follow up on high-save topic",
                kw_json,
                json.dumps(["干皮冬天怎么选面霜？"], ensure_ascii=False),
                linked_scr,
                ts,
                ts,
                "numbers",
            ))

        # 80 tracked trend keywords + 160 trend observations
        trend_rows = []
        obs_rows = []
        for i in range(80):
            tid = "trd%09d" % i
            kw = "秋冬护肤热词%d" % (i + 1) if i > 2 else ["秋冬护肤", "超市采购", "干皮面霜"][i]
            key = kw.lower()
            trend_rows.append((tid, key, kw, 15000 + i * 400, "manual", "", "", "", now, now - (i % 10) * 86400.0))
            for d_idx, day_str in enumerate(("2026-11-18", "2026-11-20")):
                obs_rows.append((
                    "obs%05d_%d" % (i, d_idx),
                    key, kw, "page", "https://trends.example/list", "https://trends.example/list",
                    day_str, now, 12000 + d_idx * 5000, max(1, 50 - d_idx * 5), now,
                ))

        with contextlib.closing(cls.store._db()) as db, db:
            db.executemany(
                "INSERT INTO scripts (id, title, mode, video_format, target_sec, notes, lines_json, line_count,"
                " preview, post_json, ai_lines_json, status, keywords_json, basis_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                script_rows,
            )
            db.executemany(
                "INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments, kind,"
                " timing_json, source_name, needs_review, reviewed_at, origin, script_id, posted_on, note_url,"
                " metrics_on, ai_json, snapshot, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                sample_rows,
            )
            db.executemany(
                "INSERT INTO metric_snapshots (id, sample_id, read_on, views, likes, collects, comments, shares,"
                " new_followers, click_rate_pct, avg_watch_sec, completion_rate_pct, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                snap_rows,
            )
            db.executemany(
                "INSERT INTO sample_comments (id, sample_id, text, likes, sentiment, is_question, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                comment_rows,
            )
            db.executemany(
                "INSERT INTO sample_sentiments (sample_id, summary_json, source, updated_at) VALUES (?,?,?,?)",
                sentiment_rows,
            )
            db.executemany(
                "INSERT INTO video_plans (id, title, video_format, target_sec, scheduled_for, status, source_kind,"
                " notes, rationale_zh, rationale_en, keywords_json, questions_json, script_id, created_at, updated_at,"
                " basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                plan_rows,
            )
            db.executemany(
                "INSERT INTO trends (id, key, keyword, heat, source, source_url, note_zh, note_en, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                trend_rows,
            )
            db.executemany(
                "INSERT INTO trend_observations (id, key, keyword, source, origin, link, seen_on, seen_at, heat, rank,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                obs_rows,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def measure(self, fn: Callable[[], Any]) -> Tuple[Any, float, List[str]]:
        """Execute ``fn()``, returning ``(result, elapsed_seconds, non_pragma_sql_statements)``."""
        statements: List[str] = []
        real_db = self.store._db

        def traced_db() -> sqlite3.Connection:
            conn = real_db()
            conn.set_trace_callback(statements.append)
            return conn

        with mock.patch.object(self.store, "_db", side_effect=traced_db):
            started = time.perf_counter()
            result = fn()
            elapsed = time.perf_counter() - started

        meaningful_sql = [
            stmt.strip()
            for stmt in statements
            if stmt.strip() and not stmt.strip().upper().startswith("PRAGMA ")
        ]
        return result, elapsed, meaningful_sql

    def test_seeded_volume_and_schema_fingerprint_integrity(self) -> None:
        with contextlib.closing(self.store._db()) as db:
            counts = {
                table: int(db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
                for table in ("scripts", "samples", "sample_comments", "metric_snapshots", "video_plans", "trends")
            }
            fingerprint = backup.layout_fingerprint()
        self.assertEqual(counts, {
            "scripts": 300,
            "samples": 600,
            "sample_comments": 5000,
            "metric_snapshots": 1500,
            "video_plans": 200,
            "trends": 80,
        })
        from tests.test_backup import LAYOUT_OF_DATA_VERSION
        self.assertEqual(fingerprint, LAYOUT_OF_DATA_VERSION[backup.DATA_VERSION])

    def test_list_scripts_latency_and_single_query(self) -> None:
        scripts, elapsed, sql = self.measure(self.store.list_scripts)
        self.assertEqual(len(scripts), 300)
        self.assertLess(elapsed, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql), 1, sql)
        self.assertNotIn("lines_json", scripts[0])

    def test_list_samples_latency_and_single_query(self) -> None:
        samples, elapsed, sql = self.measure(self.store.list_samples)
        self.assertEqual(len(samples), 600)
        self.assertLess(elapsed, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql), 1, sql)

    def test_trends_payload_latency_and_bounded_queries(self) -> None:
        payload, elapsed, sql = self.measure(lambda: trends.payload(self.store, now=1_795_100_000.0))
        self.assertEqual(len(payload["trends"]), 80)
        self.assertGreater(payload["videos_with_numbers"], 500)
        self.assertLess(elapsed, CI_WALL_BUDGET_SEC)
        self.assertLessEqual(len(sql), 4, sql)

    def test_planner_list_month_grid_and_payload_have_zero_n_plus_one(self) -> None:
        raw_plans, elapsed_raw, sql_raw = self.measure(self.store.list_video_plans)
        self.assertEqual(len(raw_plans), 200)
        self.assertLess(elapsed_raw, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql_raw), 1, sql_raw)

        grid, elapsed_grid, sql_grid = self.measure(lambda: planner.month_grid(self.store, 2026, 11))
        self.assertEqual(grid["month"], "2026-11")
        self.assertEqual(grid["total_plans"], 200)
        self.assertEqual(len(grid["unscheduled"]), 40)
        self.assertGreaterEqual(len(grid["weeks"]), 5)
        self.assertLess(elapsed_grid, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql_grid), 3, sql_grid)

        full_plan, elapsed_full, sql_full = self.measure(lambda: planner.payload(self.store))
        self.assertEqual(len(full_plan["plans"]), 200)
        self.assertEqual(full_plan["counts"]["POSTED"], 60)
        self.assertLess(elapsed_full, CI_WALL_BUDGET_SEC)
        # Without N+1 batching, 60 AI sentiment summaries would trigger 60+ per-sample comment SELECTs here.
        self.assertLessEqual(len(sql_full), 10, sql_full)

    def test_rednote_analytics_rollup_eliminates_per_sample_comment_n_plus_one(self) -> None:
        overview, elapsed, sql = self.measure(lambda: rednote_analytics.analytics_overview(self.store))
        self.assertEqual(overview["total_videos"], 600)
        self.assertEqual(overview["videos_with_sentiment"], 120)
        self.assertTrue(overview["top_audience_questions"])
        self.assertTrue(any(q["quoted"] for q in overview["top_audience_questions"]))
        self.assertLess(elapsed, CI_WALL_BUDGET_SEC)
        # Exactly 4 SELECTs: samples, metric_snapshots, sample_sentiments, and 1 batched sample_comments IN (...) query.
        self.assertEqual(len(sql), 4, sql)

    def test_voicematch_and_checks_lint_payload_on_active_script(self) -> None:
        script = self.store.get_script(self.active_script_id)
        self.assertIsNotNone(script)
        assert script is not None

        # Cold reference build across 600 samples:
        if hasattr(self.store, "_voice_reference"):
            delattr(self.store, "_voice_reference")
        cold_lint, elapsed_cold, sql_cold = self.measure(
            lambda: checks.lint_payload(
                self.store, script["lines"], script["target_sec"], script["keywords"], script["post"]
            )
        )
        self.assertTrue(cold_lint["voice_match"]["available"])
        self.assertEqual(cold_lint["voice_match"]["reference"]["scripts"], 520)
        self.assertLess(elapsed_cold, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql_cold), 2, sql_cold)

        # Warm editor keystroke save check reuses cached Reference (only 1 samples_version aggregate query):
        warm_lint, elapsed_warm, sql_warm = self.measure(
            lambda: checks.lint_payload(
                self.store, script["lines"], script["target_sec"], script["keywords"], script["post"]
            )
        )
        self.assertEqual(warm_lint["voice_match"]["score"], cold_lint["voice_match"]["score"])
        self.assertLess(elapsed_warm, CI_WALL_BUDGET_SEC)
        self.assertEqual(len(sql_warm), 1, sql_warm)

    def test_benchmark_evaluate_store_cold_and_warm_bounded_at_600_samples(self) -> None:
        if hasattr(self.store, "_voice_calibration"):
            delattr(self.store, "_voice_calibration")
        report, elapsed_cold, sql_cold = self.measure(lambda: benchmark.evaluate_store(self.store))
        self.assertTrue(report["capped"])
        self.assertEqual(report["organic_samples_total"], 520)
        self.assertEqual(report["samples_evaluated"], benchmark.MAX_HOLDOUT_SAMPLES)
        self.assertEqual(report["readiness_tier"], "calibrated")
        self.assertGreater(report["margin"], 15.0)
        self.assertLess(elapsed_cold, CI_WALL_BUDGET_SEC)
        self.assertLessEqual(len(sql_cold), 6, sql_cold)

        warm_report, elapsed_warm, sql_warm = self.measure(lambda: benchmark.evaluate_store(self.store))
        self.assertEqual(warm_report["own_mean_score"], report["own_mean_score"])
        self.assertLess(elapsed_warm, 0.2)
        self.assertEqual(len(sql_warm), 1, sql_warm)


if __name__ == "__main__":
    unittest.main()
