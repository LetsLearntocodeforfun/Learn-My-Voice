"""Tests for automatic plan keyword suggestions and non-destructive topic-idea refreshes (studio/autosuggest.py),
plus plan/comment basis markers in studio/planner.py and studio/rednote_analytics.py.

No network connection is opened and no test sleeps: a stand-in transport answers model calls and an injected clock
moves time forward.
"""
from __future__ import annotations

import datetime
import http.client
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import autopilot, autosuggest, planner, posted, rednote_analytics, server, trends
from studio.store import MANUAL_PROVIDER, MAX_KEYWORDS_PER_SCRIPT, Store

HOUR, DAY = 3600, 86400
BASE = time.mktime((2026, 11, 22, 9, 0, 0, 0, 0, -1))
CHINESE = r"[\u4e00-\u9fff]"


class Clock:
    def __init__(self, now: float = BASE) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def make_ideas_reply(tag: str = "A") -> dict:
    return {
        "data_note_zh": f"基于素材生成（{tag}）",
        "data_note_en": f"Generated from library ({tag})",
        "patterns": [{"pattern_zh": "真实日常收藏更高", "pattern_en": "Everyday clips get more saves", "evidence_titles": ["秋冬干皮面霜实测"]}],
        "ideas": [
            {
                "title_zh": f"{tag}｜大干皮暖气房过夜护肤",
                "title_en": f"{tag}: Overnight dry-skin care",
                "why_zh": "延续高收藏护肤方向",
                "why_en": "Builds on high-save skincare videos",
                "video_format": "VLOG_VOICEOVER",
                "target_duration_sec": 60,
                "trend_keywords": ["秋冬护肤"],
                "script_notes_zh": "睡前实拍涂抹与第二天早起状态",
                "evidence_titles": ["秋冬干皮面霜实测"],
            },
            {
                "title_zh": f"{tag}｜周末超市常买清单",
                "title_en": f"{tag}: Weekend grocery staples",
                "why_zh": "日常采购互动稳定",
                "why_en": "Steady engagement on grocery vlogs",
                "video_format": "VLOG_VOICEOVER",
                "target_duration_sec": 60,
                "trend_keywords": [],
                "script_notes_zh": "推车进门常买三样",
                "evidence_titles": ["秋冬干皮面霜实测"],
            },
        ],
    }


class AutosuggestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-autosuggest-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")
        self.clock = Clock()
        self.registry = autopilot.Registry()
        autosuggest.register(self.registry)
        self.calls = []
        self.reply_tag = "第一组"
        self.pilot = autopilot.Autopilot(
            self.store,
            registry=self.registry,
            transport=self._transport,
            clock=self.clock,
        )
        self.pilot.on = True
        self.addCleanup(self._stop_pilot)

    def _stop_pilot(self) -> None:
        self.pilot.stop()
        self.pilot.wait(10)

    def _transport(self, method: str, url: str, headers: dict, body: object) -> dict:
        self.calls.append((method, url, body))
        payload = make_ideas_reply(self.reply_tag)
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    def configure_ai(self) -> None:
        self.store.update_config(lambda c: (
            c.update(provider="openai", model="gpt-test"),
            c["keys"].update(openai="sk-test-key"),
        ))

    def seed_library_and_trends(self) -> dict:
        sample = self.store.create_sample(
            title="秋冬干皮面霜实测",
            text="一入冬吹暖气我这张干皮真的服了嘛 / 结果换了面霜稳多了哈 / 第二天起来也不起皮啦",
            metrics={"views": 12000, "likes": 420, "collects": 560, "comments": 35},
        )
        with mock.patch("studio.store.time.time", return_value=self.clock.now):
            self.store.upsert_trends([
                {"keyword": "秋冬护肤", "heat": 180000},
                {"keyword": "暖气房保湿", "heat": 95000},
                {"keyword": "露营装备", "heat": 220000},
            ], source="manual")
        return sample

    def day_str(self, delta_days: int) -> str:
        base_day = datetime.date.fromtimestamp(self.clock.now)
        return (base_day + datetime.timedelta(days=delta_days)).isoformat()

    def db_snapshot(self) -> tuple:
        with sqlite3.connect(str(self.store.db_path)) as db:
            tables = sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            rows = {t: db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
        rec = self.store.read_recommendations_bytes()
        return rows, rec


class SwitchOffAndRegistrationTest(AutosuggestCase):
    def test_global_registry_has_both_tasks_and_switched_off_tasks_do_nothing(self) -> None:
        p_task = autopilot.REGISTRY.task("plans.suggest")
        i_task = autopilot.REGISTRY.task("ideas.refresh")
        self.assertIsNotNone(p_task)
        self.assertIsNotNone(i_task)
        self.assertFalse(p_task.calls_model)
        self.assertTrue(i_task.calls_model)

        self.configure_ai()
        self.seed_library_and_trends()
        planner.create_plan_item(self.store, {"title": "秋冬护肤面霜分享"})

        # 1. Master enabled=False -> nothing runs
        before = self.db_snapshot()
        self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.calls, [])

        # 2. Master enabled=True, individual tasks still False -> nothing runs
        autopilot.save_settings(self.store, {"enabled": True, "tasks": {"plans.suggest": False, "ideas.refresh": False}})
        before2 = self.db_snapshot()
        self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(self.db_snapshot(), before2)
        self.assertEqual(self.calls, [])

        # 3. Manual run_now while task is off -> refused with ZH and EN messages
        with self.assertRaises(autopilot.AutopilotError) as ctx:
            self.pilot.run_now("plans.suggest")
        self.assertEqual(ctx.exception.code, "autopilot_task_off")
        self.assertRegex(ctx.exception.message_zh, CHINESE)
        self.assertTrue(ctx.exception.message)


class PlanSuggestInvariantsAndDecisionsTest(AutosuggestCase):
    def test_plans_never_modified_automatically_and_eligibility_skips_posted_past_and_far_future(self) -> None:
        self.seed_library_and_trends()
        p_unsched = planner.create_plan_item(self.store, {"title": "秋冬护肤晚间流程", "notes": "暖气房保湿怎么涂"})
        p_within = planner.create_plan_item(self.store, {
            "title": "回答观众护肤追问",
            "scheduled_for": self.day_str(14),
            "questions": ["暖气房保湿单涂面霜够不够？"],
        })
        p_far = planner.create_plan_item(self.store, {"title": "秋冬护肤年终总结", "scheduled_for": self.day_str(15)})
        p_past = planner.create_plan_item(self.store, {"title": "秋冬护肤上周旧计划", "scheduled_for": self.day_str(-1)})
        p_has_kw = planner.create_plan_item(self.store, {"title": "秋冬护肤空瓶记", "keywords": ["秋冬护肤"]})
        p_posted = planner.create_plan_item(self.store, {"title": "秋冬护肤已发布视频", "scheduled_for": self.day_str(1)})
        made = planner.make_script(self.store, p_posted["id"], {"use_ai": False})
        self.store.update_script(made["script"]["id"], {"lines": [{"spoken_script_zh": "秋冬护肤真好用哈"}]})
        posted.mark(self.store, made["script"]["id"], {"posted_on": datetime.date.today().isoformat(), "likes": 10})

        plans_before = self.store.list_video_plans()
        # Even at level="auto", plans.suggest NEVER modifies video_plans or scripts!
        autopilot.save_settings(self.store, {"enabled": True, "level": "auto", "tasks": {"plans.suggest": True}})
        ran = self.pilot.run_due()
        self.assertEqual(len(ran), 1)
        self.assertEqual((ran[0]["task"], ran[0]["status"], ran[0]["model_calls"]), ("plans.suggest", "done", 0))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.list_video_plans(), plans_before)

        shown = planner.payload(self.store)["plans"]
        by_id = {p["id"]: p["suggestions"] for p in shown}
        self.assertEqual({s["keyword"] for s in by_id[p_unsched["id"]]}, {"秋冬护肤", "暖气房保湿"})
        self.assertEqual([s["keyword"] for s in by_id[p_within["id"]]], ["暖气房保湿"])
        self.assertEqual(by_id[p_within["id"]][0]["matched_in"], ["questions"])
        self.assertEqual(by_id[p_far["id"]], [])
        self.assertEqual(by_id[p_past["id"]], [])
        self.assertEqual(by_id[p_has_kw["id"]], [])
        self.assertEqual(by_id[p_posted["id"]], [])

        # Without changes, plans_due is False on the next cadence tick and writes no extra run line
        self.clock.advance(autosuggest.PLANS_EVERY_SEC + 10)
        self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(len(self.pilot.status()["runs"]), 1)

    def test_take_adds_keyword_dismiss_suppresses_resuggestion_and_undo_withdraws_open_only(self) -> None:
        self.seed_library_and_trends()
        plan = planner.create_plan_item(self.store, {
            "title": "秋冬护肤面霜测评",
            "notes": "重点测暖气房保湿表现",
            "scheduled_for": self.day_str(3),
            "keywords": ["干皮面霜"],
        })
        autopilot.save_settings(self.store, {"enabled": True, "tasks": {"plans.suggest": True}})
        run_line = self.pilot.run_due()[0]

        offered = {s["keyword"]: s for s in planner.payload(self.store)["plans"][0]["suggestions"]}
        self.assertEqual(set(offered), {"秋冬护肤", "暖气房保湿"})

        # Take "秋冬护肤": adds after existing keyword, leaves title/notes/day untouched
        after_take = autosuggest.take(self.store, plan["id"], offered["秋冬护肤"]["id"], now=self.clock.now)
        self.assertEqual(after_take["keywords"], ["干皮面霜", "秋冬护肤"])
        self.assertEqual((after_take["title"], after_take["notes"], after_take["scheduled_for"]),
                         (plan["title"], plan["notes"], plan["scheduled_for"]))

        # Dismiss "暖气房保湿": plan keywords stay as they are, and next run never re-suggests it
        with mock.patch("studio.store.time.time", return_value=self.clock.now):
            self.store.upsert_trends([{"keyword": "面霜测评", "heat": 80000}], source="manual")
        autosuggest.dismiss(self.store, plan["id"], offered["暖气房保湿"]["id"], now=self.clock.now)
        self.assertEqual(planner.get(self.store, plan["id"])["keywords"], ["干皮面霜", "秋冬护肤"])

        self.clock.advance(autosuggest.PLANS_EVERY_SEC + 5)
        run2 = self.pilot.run_due()[0]
        open_now = planner.payload(self.store)["plans"][0]["suggestions"]
        self.assertEqual([s["keyword"] for s in open_now], ["面霜测评"])   # dismissed keyword NOT re-offered

        # Undo run2 withdraws "面霜测评", leaves taken "秋冬护肤" on the plan, and next run does not re-offer "面霜测评"
        self.pilot.undo(run2["id"])
        self.assertEqual(planner.payload(self.store)["plans"][0]["suggestions"], [])
        self.assertEqual(planner.get(self.store, plan["id"])["keywords"], ["干皮面霜", "秋冬护肤"])
        self.clock.advance(autosuggest.PLANS_EVERY_SEC + 5)
        self.assertEqual(self.pilot.run_due(), [])

        # Undo run1 succeeds (0 open left, taken/dismissed untouched)
        self.pilot.undo(run_line["id"])
        self.assertEqual(planner.get(self.store, plan["id"])["keywords"], ["干皮面霜", "秋冬护肤"])

        # Refusal when plan already has MAX_KEYWORDS_PER_SCRIPT keywords
        full_plan = planner.create_plan_item(self.store, {
            "title": "秋冬护肤合集",
            "keywords": [f"词{i}" for i in range(MAX_KEYWORDS_PER_SCRIPT - 1)],
        })
        self.clock.advance(autosuggest.PLANS_EVERY_SEC + 5)
        self.pilot.run_due()
        sug = [p for p in planner.payload(self.store)["plans"] if p["id"] == full_plan["id"]][0]["suggestions"][0]
        planner.change(self.store, full_plan["id"], {"keywords": [f"词{i}" for i in range(MAX_KEYWORDS_PER_SCRIPT)]})
        with self.assertRaises(planner.PlannerError) as full_err:
            autosuggest.take(self.store, full_plan["id"], sug["id"])
        self.assertEqual(full_err.exception.code, "keywords_full")
        self.assertRegex(full_err.exception.message_zh, CHINESE)


class IdeasRefreshPreservationCapAndUndoTest(AutosuggestCase):
    def test_suggest_level_and_creator_saved_ideas_are_never_overwritten_and_take_dismiss_undo_work(self) -> None:
        self.configure_ai()
        self.seed_library_and_trends()

        # Creator saved their own ideas earlier: must NEVER be overwritten automatically, even at level="auto"!
        creator_saved = {
            "generated_at": self.clock.now - 3600,
            "focus": "我亲手找的选题",
            "ideas": [{"title_zh": "创作者原本存着的选题", "why_zh": "保留不动", "video_format": "VLOG_VOICEOVER", "target_duration_sec": 60}],
        }
        self.store.save_recommendations(creator_saved)
        exact_creator_bytes = self.store.read_recommendations_bytes()

        autopilot.save_settings(self.store, {"enabled": True, "level": "auto", "tasks": {"ideas.refresh": True}})
        self.reply_tag = "自动第一组"
        run1 = self.pilot.run_due()[0]
        self.assertEqual((run1["task"], run1["status"], run1["model_calls"]), ("ideas.refresh", "done", 1))
        self.assertEqual(run1["summary"]["in_place"], False)
        self.assertEqual(run1["summary"]["waits_because"], "not_autopilots")
        # Creator's saved file is byte-for-byte untouched!
        self.assertEqual(self.store.read_recommendations_bytes(), exact_creator_bytes)

        state1 = autosuggest.ideas_state(self.store)
        self.assertIsNone(state1["in_use"])
        self.assertIsNotNone(state1["waiting"])
        self.assertIn("自动第一组", state1["waiting"]["result"]["ideas"][0]["title_zh"])
        self.assertIn("秋冬护肤", state1["waiting"]["trends"])

        # Undo while waiting removes the waiting set and leaves creator's saved ideas intact
        self.pilot.undo(run1["id"])
        self.assertIsNone(autosuggest.ideas_state(self.store)["waiting"])
        self.assertEqual(self.store.read_recommendations_bytes(), exact_creator_bytes)

        # Run again (after IDEAS_AT_LATEST_SEC), then dismiss waiting set -> creator's saved ideas still untouched
        self.clock.advance(autosuggest.IDEAS_AT_LATEST_SEC + 10)
        self.reply_tag = "自动第二组"
        self.pilot.run_due()
        waiting_id = autosuggest.ideas_state(self.store)["waiting"]["set_id"]
        after_dismiss = autosuggest.dismiss_ideas(self.store, waiting_id, now=self.clock.now)
        self.assertIsNone(after_dismiss["waiting"])
        self.assertEqual(after_dismiss["last"]["status"], "dismissed")
        self.assertEqual(self.store.read_recommendations_bytes(), exact_creator_bytes)

        # Run a third time, creator clicks take_ideas -> replaces saved ideas, and undo refuses to clobber creator's choice
        self.clock.advance(autosuggest.IDEAS_AT_LATEST_SEC + 10)
        self.reply_tag = "自动第三组"
        run3 = self.pilot.run_due()[0]
        waiting3 = autosuggest.ideas_state(self.store)["waiting"]["set_id"]
        after_take = autosuggest.take_ideas(self.store, waiting3, now=self.clock.now)
        self.assertIsNone(after_take["waiting"])
        self.assertEqual(after_take["in_use"]["placed_by"], "creator")
        self.assertIn("自动第三组", self.store.load_recommendations()["ideas"][0]["title_zh"])
        with self.assertRaises(autopilot.AutopilotError) as undo_err:
            self.pilot.undo(run3["id"])
        self.assertEqual(undo_err.exception.code, "autopilot_undo_refused")
        self.assertRegex(undo_err.exception.message_zh, CHINESE)

    def test_auto_level_with_no_saved_file_places_and_restores_exact_prior_bytes_on_undo(self) -> None:
        self.configure_ai()
        self.seed_library_and_trends()
        self.assertIsNone(self.store.read_recommendations_bytes())

        # In suggest mode even with no file, it waits rather than placing!
        autopilot.save_settings(self.store, {"enabled": True, "level": "suggest", "tasks": {"ideas.refresh": True}})
        self.reply_tag = "仅建议组"
        r_sug = self.pilot.run_due()[0]
        self.assertFalse(r_sug["summary"]["in_place"])
        self.assertIsNone(self.store.read_recommendations_bytes())
        self.pilot.undo(r_sug["id"])

        # Switch to auto mode with no file -> places set 1; next refresh places set 2 and remembers set 1 bytes;
        # undoing set 2 restores set 1 byte-for-byte; undoing set 1 removes recommendations.json cleanly!
        autopilot.save_settings(self.store, {"level": "auto"})
        self.clock.advance(autosuggest.IDEAS_AT_LATEST_SEC + 10)
        self.reply_tag = "自动落盘一"
        run_a = self.pilot.run_due()[0]
        self.assertTrue(run_a["summary"]["in_place"])
        bytes_a = self.store.read_recommendations_bytes()
        self.assertIsNotNone(bytes_a)

        # Top trends change after 1 day -> refresh runs again and replaces set A with set B
        self.clock.advance(autosuggest.IDEAS_EVERY_SEC + 10)
        with mock.patch("studio.store.time.time", return_value=self.clock.now):
            self.store.upsert_trends([{"keyword": "冬季通勤穿搭", "heat": 500000}], source="manual")
        self.reply_tag = "自动落盘二"
        run_b = self.pilot.run_due()[0]
        self.assertTrue(run_b["summary"]["in_place"])
        self.assertNotEqual(self.store.read_recommendations_bytes(), bytes_a)

        # Undoing run_a before run_b is refused so newer automatic set isn't silently overwritten out of order
        with self.assertRaises(autopilot.AutopilotError) as order_err:
            self.pilot.undo(run_a["id"])
        self.assertEqual(order_err.exception.code, "autopilot_undo_refused")

        # Undo run_b -> exact bytes_a restored; then undo run_a -> file removed!
        self.pilot.undo(run_b["id"])
        self.assertEqual(self.store.read_recommendations_bytes(), bytes_a)
        self.pilot.undo(run_a["id"])
        self.assertIsNone(self.store.read_recommendations_bytes())

    def test_daily_model_cap_skips_ideas_refresh_until_next_day_while_plans_suggest_still_runs(self) -> None:
        self.configure_ai()
        self.seed_library_and_trends()
        planner.create_plan_item(self.store, {"title": "秋冬护肤空瓶清单"})

        # Daily limit = 0 -> ideas.refresh is skipped with 0 model calls, while plans.suggest still runs!
        autopilot.save_settings(self.store, {
            "enabled": True,
            "daily_model_calls": 0,
            "tasks": {"plans.suggest": True, "ideas.refresh": True},
        })
        lines = self.pilot.run_due()
        by_task = {line["task"]: line["status"] for line in lines}
        self.assertEqual(by_task, {"plans.suggest": "done", "ideas.refresh": "skipped"})
        self.assertEqual(self.calls, [])

        # Raising daily_model_calls to 1 lets ideas.refresh run once, and blocks a second model call the same day
        autopilot.save_settings(self.store, {"daily_model_calls": 1})
        lines2 = self.pilot.run_due()
        self.assertEqual([(l["task"], l["status"]) for l in lines2], [("ideas.refresh", "done")])
        self.assertEqual(len(self.calls), 1)

        # Next day begins -> daily spend resets to 0, so a new trend change after 1 day runs ideas.refresh again
        self.clock.advance(DAY + 60)
        with mock.patch("studio.store.time.time", return_value=self.clock.now):
            self.store.upsert_trends([{"keyword": "换季敏感肌修护", "heat": 600000}], source="manual")
        self.assertEqual(self.pilot.spent_today(), 0)
        lines3 = self.pilot.run_due()
        self.assertEqual([(l["task"], l["status"]) for l in lines3], [("ideas.refresh", "done")])
        self.assertEqual(len(self.calls), 2)


class RoutesReadOnlyAndBasisTest(AutosuggestCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = server.make_server(0, self.store, transport=self._transport)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_http)

    def _stop_http(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def api(self, method: str, path: str, body: object = None) -> tuple:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": f"127.0.0.1:{self.port}"}
            payload = None
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            res = conn.getresponse()
            return res.status, json.loads(res.read().decode("utf-8"))
        finally:
            conn.close()

    def test_get_plans_and_recommendations_are_strictly_read_only_and_routes_decide_suggestions(self) -> None:
        self.configure_ai()
        self.seed_library_and_trends()
        p = planner.create_plan_item(self.store, {"title": "秋冬护肤实测", "notes": "暖气房保湿", "basis": "word_lists"})
        self.assertEqual(p["basis"], "word_lists")

        autopilot.save_settings(self.store, {"enabled": True, "level": "suggest", "tasks": {"plans.suggest": True, "ideas.refresh": True}})
        self.pilot.run_due()

        # GET /api/plans and GET /api/recommendations must not write anything
        snap = self.db_snapshot()
        st1, pl_payload = self.api("GET", "/api/plans")
        st2, rec_payload = self.api("GET", "/api/recommendations")
        self.assertEqual((st1, st2), (200, 200))
        self.assertEqual(self.db_snapshot(), snap)

        sugs = pl_payload["plans"][0]["suggestions"]
        self.assertEqual(len(sugs), 2)
        self.assertEqual(pl_payload["plans"][0]["basis"], "word_lists")

        # Take first suggestion, dismiss second suggestion via HTTP routes
        st_take, r_take = self.api("POST", f"/api/plans/{p['id']}/suggestions/{sugs[0]['id']}/take", {})
        self.assertEqual(st_take, 200)
        self.assertIn(sugs[0]["keyword"], r_take["plan"]["keywords"])
        st_dis, _ = self.api("POST", f"/api/plans/{p['id']}/suggestions/{sugs[1]['id']}/dismiss", {})
        self.assertEqual(st_dis, 200)
        # Deciding again returns 409 with Chinese & English messages
        st_dup, r_dup = self.api("POST", f"/api/plans/{p['id']}/suggestions/{sugs[1]['id']}/dismiss", {})
        self.assertEqual((st_dup, r_dup["error"]), (409, "suggestion_decided"))
        self.assertRegex(r_dup["message_zh"], CHINESE)

        # Take waiting automatic ideas via HTTP route
        wait_set = rec_payload["automatic"]["waiting"]
        self.assertIsNotNone(wait_set)
        st_it, r_it = self.api("POST", f"/api/recommendations/automatic/{wait_set['set_id']}/take", {})
        self.assertEqual(st_it, 200)
        self.assertIsNone(r_it["automatic"]["waiting"])
        self.assertEqual(r_it["automatic"]["in_use"]["placed_by"], "creator")
        self.assertEqual(len(r_it["result"]["ideas"]), 2)

    def test_rednote_analytics_question_basis_quote_check_and_factual_wordlist_descriptions(self) -> None:
        sample = self.store.create_sample(
            title="面霜实测",
            text="今天测一下面霜哈 / 保湿挺稳的嘛",
            metrics={"views": 5000, "likes": 200, "collects": 150, "comments": 10},
        )
        rednote_analytics.ingest_comments_for_sample(
            self.store,
            sample["id"],
            "小鹿：大干皮冬天涂一层够不够润？ 赞 18\n路人：瓶子太沉了不太好带 赞 5",
        )
        ov_local = rednote_analytics.analytics_overview(self.store)
        self.assertEqual(ov_local["top_audience_questions"][0]["basis"], "word_lists")
        self.assertIs(ov_local["top_audience_questions"][0]["quoted"], True)
        # Local word-list opportunities describe what was counted rather than ready-made video titles
        local_titles = [o["title_zh"] for o in ov_local["opportunities"] if o["basis"] == "word_lists"]
        self.assertTrue(all("按词表数出" in t for t in local_titles), local_titles)

        # Model reply with one grounded quote and one ungrounded paraphrase marks quoted True vs False
        rednote_analytics.apply_sentiment_reply(self.store, sample["id"], {
            "overall_sentiment": "mostly_positive",
            "summary_zh": "观众问保湿",
            "summary_en": "Viewers ask about hydration",
            "audience_questions": [
                {"question_zh": "大干皮冬天涂一层够不够润？", "demand_count": 19},
                {"question_zh": "油皮夏天能不能当晚霜？", "demand_count": 4},
            ],
        }, source="ai")
        ov_ai = rednote_analytics.analytics_overview(self.store)
        by_q = {q["question_zh"]: q for q in ov_ai["top_audience_questions"]}
        self.assertEqual((by_q["大干皮冬天涂一层够不够润？"]["basis"], by_q["大干皮冬天涂一层够不够润？"]["quoted"]), ("model", True))
        self.assertEqual((by_q["油皮夏天能不能当晚霜？"]["basis"], by_q["油皮夏天能不能当晚霜？"]["quoted"]), ("model", False))

        # Model reply with empty audience_questions falls back to local counted questions -> basis stays "word_lists"!
        rednote_analytics.apply_sentiment_reply(self.store, sample["id"], {
            "overall_sentiment": "mostly_positive",
            "summary_zh": "没返回问题列表",
            "summary_en": "No questions array",
            "audience_questions": [],
        }, source="ai")
        ov_fb = rednote_analytics.analytics_overview(self.store)
        self.assertEqual(ov_fb["top_audience_questions"][0]["basis"], "word_lists")
        self.assertIs(ov_fb["top_audience_questions"][0]["quoted"], True)


if __name__ == "__main__":
    unittest.main()
