"""Tests for the Autopilot page (#/autopilot), Setup card (#/start), Plan keyword suggestion chips (#/plan),
and Ideas automatic refresh banner (#/ideas).

Covers:
- I18N completeness (exact zh/en key parity, Chinese characters in zh, gender-neutral tokens in en, XSS escaping)
- Fallback /api/autopilot/voice and /api/autopilot/sources validation & Xiaohongshu domain refusal
- Read-only get_autopilot_status MCP tool reporting open plan suggestions and waiting automatic ideas
- Real headless Chrome E2E in both zh/light and en/dark with page.problems == []
"""
from __future__ import annotations

import datetime
import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import autopilot, autosuggest, mcp_server, planner, server
from studio.store import ROOT, Store
from tests import browser

WEB = ROOT / "studio" / "web"
CHINESE = re.compile(r"[\u4e00-\u9fff]")
GENDER_PRONOUNS = re.compile(r"\b(she|her|hers|herself|he|him|his|himself)\b", re.I)


def extract_i18n_blocks(js_text: str) -> dict:
    """Extract Object.assign(I18N.zh, {...}) and Object.assign(I18N.en, {...}) keys and string values."""
    out = {}
    for lang_code in ("zh", "en"):
        m = re.search(rf"Object\.assign\(I18N\.{lang_code},\s*\{{(.*?)\}}\);", js_text, re.S)
        if not m:
            raise AssertionError(f"Missing Object.assign(I18N.{lang_code}, ...) block")
        block = m.group(1)
        pairs = {}
        for km in re.finditer(r"\b([a-zA-Z0-9_]+)\s*:\s*'((?:\\'|[^'])*)'", block):
            pairs[km.group(1)] = km.group(2)
        out[lang_code] = pairs
    return out


class AutopilotI18nAndRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-ap-ui-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")

    def test_i18n_parity_chinese_script_and_gender_neutral_english_across_w5_modules(self) -> None:
        ap = extract_i18n_blocks((WEB / "autopilot.js").read_text(encoding="utf-8"))
        self.assertEqual(set(ap["zh"]), set(ap["en"]))
        self.assertGreaterEqual(len(ap["zh"]), 50)
        for key, zh_val in ap["zh"].items():
            self.assertRegex(zh_val, CHINESE, f"autopilot.js zh.{key} must contain Chinese characters")
        for key, en_val in ap["en"].items():
            self.assertIsNone(GENDER_PRONOUNS.search(en_val), f"autopilot.js en.{key} has gendered pronoun: {en_val}")
            self.assertIsNone(CHINESE.search(en_val), f"autopilot.js en.{key} has unexpected CJK: {en_val}")

        # Verify all 6 canonical task titles & descriptions exist in both zh and en
        for task_slug in (
            "trends_refresh",
            "voice_ingest_folder",
            "voice_relearn",
            "lessons_accept_recurring",
            "plans_suggest",
            "ideas_refresh",
        ):
            for suffix in ("_title", "_d"):
                k = f"ap_task_{task_slug}{suffix}"
                self.assertIn(k, ap["zh"])
                self.assertIn(k, ap["en"])

        # Also check new keys added to planner.js, discover.js, setup.js
        for filename, prefix in (("planner.js", "pl_sug_"), ("discover.js", "dv_auto_"), ("setup.js", "st_ap_")):
            blocks = extract_i18n_blocks((WEB / filename).read_text(encoding="utf-8"))
            zh_keys = {k for k in blocks["zh"] if k.startswith(prefix)}
            en_keys = {k for k in blocks["en"] if k.startswith(prefix)}
            self.assertTrue(zh_keys, filename)
            self.assertEqual(zh_keys, en_keys, filename)
            for k in en_keys:
                self.assertIsNone(GENDER_PRONOUNS.search(blocks["en"][k]), f"{filename} en.{k}")

    def test_voice_and_sources_routes_and_mcp_get_autopilot_status(self) -> None:
        httpd = server.make_server(0, self.store)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(5)))

        def send(method: str, path: str, body: object = None) -> tuple:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                headers = {"Host": f"127.0.0.1:{port}"}
                raw = json.dumps(body).encode("utf-8") if body is not None else None
                if raw is not None:
                    headers["Content-Type"] = "application/json"
                conn.request(method, path, body=raw, headers=headers)
                res = conn.getresponse()
                return res.status, json.loads(res.read().decode("utf-8"))
            finally:
                conn.close()

        watch_folder = self.tmp / "watch_inbox"
        watch_folder.mkdir()
        st_v, v_out = send("POST", "/api/autopilot/voice", {"watch_dir": str(watch_folder), "transcribe_route": "local"})
        self.assertEqual((st_v, v_out["folder_exists"], v_out["transcribe_route"], v_out["sponsored_subfolder"]),
                         (200, True, "local", "sponsored"))

        # Xiaohongshu / RedNote URLs are strictly refused in trend sources
        for bad_url in ("https://www.xiaohongshu.com/explore/123", "https://rednote.com/hot", "http://example.com/not-https"):
            st_bad, err_bad = send("POST", "/api/autopilot/sources", {"pages": [bad_url]})
            self.assertEqual(st_bad, 400, bad_url)
            self.assertRegex(err_bad["message_zh"], CHINESE)

        st_s, s_out = send("POST", "/api/autopilot/sources", {
            "ai_lookup": True,
            "keyword_files": True,
            "pages": ["https://trends.example.com/feed"],
        })
        self.assertEqual((st_s, s_out["ai_lookup"], s_out["keyword_files"], s_out["pages"]),
                         (200, True, True, ["https://trends.example.com/feed"]))

        # Seed open plan suggestion + waiting idea set and verify MCP get_autopilot_status returns both read-only
        self.store.upsert_trends([{"keyword": "秋冬护肤", "heat": 150000}], source="manual")
        plan = planner.create_plan_item(self.store, {"title": "秋冬护肤晚间实测", "basis": "model"})
        self.store.add_plan_suggestions([{
            "id": "aa11bb22cc33", "plan_id": plan["id"], "keyword": "秋冬护肤", "keyword_key": "秋冬护肤",
            "matched": "秋冬护肤", "matched_in": ["title"], "relevance": 1.0, "score": 88,
            "source": "manual", "rising": True, "sightings": 3, "run_id": "", "created_at": 10.0,
        }])
        self.store.add_idea_set({
            "id": "112233aabbcc", "run_id": "", "status": "waiting", "placed_by": "",
            "ideas_json": json.dumps({"ideas": [{"title_zh": "候选选题一"}], "generated_at": 12.0}, ensure_ascii=False),
            "ideas_sha": "b" * 64, "had_ideas": False, "earlier": None,
            "trends_json": json.dumps(["秋冬护肤"], ensure_ascii=False), "created_at": 12.0,
        })
        mcp_res = mcp_server.call_tool(self.store, "get_autopilot_status", {"run_limit": 5})
        self.assertFalse(mcp_res["isError"])
        mcp_data = json.loads(mcp_res["content"][0]["text"])
        self.assertIs(mcp_data["autopilot"]["on"], False)
        self.assertEqual(mcp_data["open_plan_suggestions"][0]["plan_id"], plan["id"])
        self.assertEqual(mcp_data["open_plan_suggestions"][0]["suggestions"][0]["keyword"], "秋冬护肤")
        self.assertEqual(mcp_data["automatic_ideas"]["waiting"]["set_id"], "112233aabbcc")
        self.assertEqual(mcp_data["automatic_ideas"]["waiting"]["idea_titles"], ["候选选题一"])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class BrowserAutopilotUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-ap-chrome-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")
        self.watch_dir = self.tmp / "draft_watch"
        self.watch_dir.mkdir()
        self.model_tag = "候选第一批"
        self.store.update_config(lambda c: (
            c.update(provider="openai", model="gpt-test"),
            c["keys"].update(openai="sk-test-key"),
        ))
        self.store.create_sample(
            title="秋冬干皮面霜实测",
            text="一入冬吹暖气我这张干皮真的服了嘛 / 结果换了面霜稳多了哈 / 第二天起来也不起皮啦",
            metrics={"views": 16000, "likes": 520, "collects": 680, "comments": 42},
        )
        self.store.upsert_trends([
            {"keyword": "秋冬护肤", "heat": 200000},
            {"keyword": "暖气房保湿", "heat": 140000},
        ], source="manual")

    def _transport(self, method: str, url: str, headers: dict, body: object) -> dict:
        reply = {
            "data_note_zh": "基于素材库生成",
            "data_note_en": "From library",
            "patterns": [],
            "ideas": [{
                "title_zh": f"{self.model_tag}｜暖气房干皮夜间修护",
                "title_en": f"{self.model_tag}: Night dry-skin repair",
                "why_zh": "延续面霜高收藏话题",
                "why_en": "Extends high-save skincare topic",
                "video_format": "VLOG_VOICEOVER",
                "target_duration_sec": 60,
                "trend_keywords": ["秋冬护肤"],
                "script_notes_zh": "睡前厚涂对比早起状态",
                "evidence_titles": ["秋冬干皮面霜实测"],
            }],
        }
        return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

    def test_autopilot_setup_plan_and_ideas_in_zh_light_and_en_dark(self) -> None:
        today_iso = datetime.date.today().isoformat()
        plan = planner.create_plan_item(self.store, {
            "title": "秋冬护肤面霜问答",
            "notes": "重点测暖气房保湿够不够",
            "scheduled_for": today_iso,
            "basis": "word_lists",
        })
        self.store.save_recommendations({
            "generated_at": 1000.0,
            "focus": "我原本保存的选题",
            "ideas": [{
                "title_zh": "原本保存的超市日常选题",
                "title_en": "Original saved grocery idea",
                "why_zh": "原本就有",
                "why_en": "Saved earlier",
                "video_format": "VLOG_VOICEOVER",
                "target_duration_sec": 60,
                "trend_keywords": [],
                "script_notes_zh": "逛超市日常",
                "evidence_titles": [],
            }],
        })

        # ---- Pass 1: zh + light ----
        with browser.open_app(self.store, lang="zh", theme="light", transport=self._transport) as page:
            # 1. Off state first (make_server leaves autopilot.on == False by default)
            page.go("#/autopilot", "Boolean(document.getElementById('ap-off'))")
            self.assertIn("LEARN_MY_VOICE_NO_AUTOPILOT=1", page.eval_js("document.getElementById('ap-off').textContent"))
            self.assertIsNone(page.eval_js("document.getElementById('ap-master-card')"))
            self.assertEqual(page.problems, [])

            # 2. Switch session gate on -> test #/start quick-enable card, then full #/autopilot interactive flow
            page.httpd.app.autopilot.on = True
            page.go("#/start", "Boolean(document.getElementById('st-autopilot-card'))")
            self.assertTrue(page.eval_js("Boolean(document.querySelector('[data-st-ap-enable=\"suggest\"]'))"))
            page.eval_js("document.querySelector('[data-st-ap-enable=\"suggest\"]').click(); true")
            page.wait_for("Boolean(document.getElementById('st-ap-badge'))")
            self.assertEqual(autopilot.settings(self.store)["enabled"], True)

            page.eval_js("document.getElementById('st-ap-open').click(); true")
            page.wait_for("location.hash === '#/autopilot' && Boolean(document.getElementById('ap-master-card'))")
            self.assertTrue(page.eval_js("document.getElementById('ap-enabled').checked"))

            # Switch mode suggest -> auto -> suggest, and update daily model call cap
            page.eval_js("document.querySelector('[data-ap-level=\"auto\"]').click(); true")
            page.wait_for("document.querySelector('[data-ap-level=\"auto\"]').getAttribute('aria-pressed') === 'true'")
            self.assertEqual(autopilot.settings(self.store)["level"], "auto")
            page.eval_js("document.querySelector('[data-ap-level=\"suggest\"]').click(); true")
            page.wait_for("document.querySelector('[data-ap-level=\"suggest\"]').getAttribute('aria-pressed') === 'true'")

            page.eval_js("""(() => {
              const el = document.getElementById('ap-daily-limit');
              el.value = '9';
              document.getElementById('ap-save-limit').click();
            })()""")
            page.wait_for("document.getElementById('ap-spent').textContent.includes('9')")
            self.assertEqual(autopilot.settings(self.store)["daily_model_calls"], 9)

            # Save voice folder & trend sources forms
            page.eval_js("""(() => {
              document.getElementById('ap-watch-dir').value = %s;
              document.getElementById('ap-transcribe-route').value = 'local';
              document.getElementById('ap-save-voice').click();
            })()""" % json.dumps(str(self.watch_dir)))
            page.wait_for("Boolean(document.getElementById('ap-folder-status'))")
            self.assertIn("已找到", page.eval_js("document.getElementById('ap-folder-status').textContent"))

            page.eval_js("""(() => {
              document.getElementById('ap-src-ai').checked = true;
              document.getElementById('ap-src-files').checked = true;
              document.getElementById('ap-src-pages').value = 'https://trends.example.com/public';
              document.getElementById('ap-save-sources').click();
            })()""")
            page.wait_for("document.getElementById('ap-src-pages').value.includes('trends.example.com')")
            self.wait_until_sources_saved()

            # Toggle on plans.suggest & ideas.refresh, click Run now on plans.suggest, then Undo and Run now again!
            for tname in ("plans.suggest", "ideas.refresh"):
                page.eval_js(f"""(() => {{
                  const box = document.querySelector('[data-ap-task-toggle="{tname}"]');
                  box.checked = true;
                  box.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})()""")
                page.wait_for(f"!document.querySelector('[data-ap-run-now=\"{tname}\"]').disabled")

            page.eval_js("document.querySelector('[data-ap-run-now=\"plans.suggest\"]').click(); true")
            page.wait_for("document.querySelectorAll('[data-ap-undo]').length === 1")
            self.assertEqual(len(self.store.list_plan_suggestions("open")), 2)

            # Test Undo button on #/autopilot!
            page.eval_js("document.querySelector('[data-ap-undo]').click(); true")
            page.wait_for("Boolean(document.querySelector('[data-ap-undone=\"1\"]'))")
            self.assertEqual(self.store.list_plan_suggestions("open"), [])

            # Add fresh trend keywords so plans.suggest has 2 new suggestions, run both plans.suggest & ideas.refresh
            self.store.upsert_trends([
                {"keyword": "面霜问答", "heat": 190000},
                {"keyword": "护肤面霜", "heat": 175000},
            ], source="manual")
            page.eval_js("document.querySelector('[data-ap-run-now=\"plans.suggest\"]').click(); true")
            page.wait_for("document.querySelectorAll('[data-ap-run]').length >= 2 && !document.querySelector('[data-ap-run-now=\"ideas.refresh\"]').disabled")
            page.eval_js("document.querySelector('[data-ap-run-now=\"ideas.refresh\"]').click(); true")
            page.wait_for("document.querySelectorAll('[data-ap-run]').length >= 3 && document.querySelectorAll('[data-ap-undo]').length >= 2")

            # 3. Open #/plan: verify plan basis badge + 2 suggestion chips -> Take one, Dismiss one!
            page.go("#/plan", "Boolean(document.getElementById('pl-list'))")
            self.assertEqual(
                page.eval_js(f"document.querySelector('[data-plan-card=\"{plan['id']}\"] [data-plan-basis]').dataset.planBasis"),
                "word_lists",
            )
            self.assertEqual(page.eval_js(f"document.querySelectorAll('[data-pl-suggestions=\"{plan['id']}\"] [data-pl-sug]').length"), 2)
            page.eval_js(f"document.querySelector('[data-pl-suggestions=\"{plan['id']}\"] [data-pl-sug-take]').click(); true")
            page.wait_for(f"document.querySelectorAll('[data-pl-suggestions=\"{plan['id']}\"] [data-pl-sug]').length === 1")
            self.assertEqual(len(planner.get(self.store, plan["id"])["keywords"]), 1)

            page.eval_js(f"document.querySelector('[data-pl-suggestions=\"{plan['id']}\"] [data-pl-sug-dismiss]').click(); true")
            page.wait_for(f"!document.querySelector('[data-pl-suggestions=\"{plan['id']}\"]')")
            self.assertEqual(len(planner.get(self.store, plan["id"])["keywords"]), 1)

            # 4. Open #/ideas: verify #dv-auto-waiting banner -> Dismiss first, then generate another waiting set for en/dark Take!
            page.go("#/ideas", "Boolean(document.getElementById('dv-auto-waiting'))")
            banner_zh = page.eval_js("document.getElementById('dv-auto-waiting').textContent")
            self.assertIn("候选第一批", banner_zh)
            self.assertIn("秋冬护肤", banner_zh)
            self.assertIn("原本保存的超市日常选题", page.eval_js("document.getElementById('ideas').textContent"))
            page.eval_js("document.getElementById('dv-auto-dismiss').click(); true")
            page.wait_for("!document.getElementById('dv-auto-waiting')")
            self.assertIn("原本保存的超市日常选题", page.eval_js("document.getElementById('ideas').textContent"))
            self.assertEqual(page.problems, [])

            # Trigger one more ideas.refresh for Pass 2 (en + dark) to Take!
            self.model_tag = "CandidateSetTwo"
            page.go("#/autopilot", "Boolean(document.getElementById('ap-tasks-card'))")
            page.eval_js("document.querySelector('[data-ap-run-now=\"ideas.refresh\"]').click(); true")
            page.wait_for("document.querySelectorAll('[data-ap-run]').length >= 4 && !document.querySelector('[data-ap-run-now=\"ideas.refresh\"]').disabled")
            self.assertEqual(page.problems, [])

        # ---- Pass 2: en + dark ----
        with browser.open_app(self.store, lang="en", theme="dark", transport=self._transport) as page:
            page.httpd.app.autopilot.on = True
            page.go("#/autopilot", "Boolean(document.getElementById('ap-master-card'))")
            self.assertEqual(page.eval_js("document.documentElement.dataset.theme"), "dark")
            self.assertEqual(page.eval_js("document.documentElement.lang"), "en")
            self.assertIn("Master switch & mode", page.eval_js("document.getElementById('ap-master-card').textContent"))
            self.assertIn("Watched folder & voice learning", page.eval_js("document.getElementById('ap-voice-card').textContent"))
            self.assertIn("Trend sources", page.eval_js("document.getElementById('ap-sources-card').textContent"))
            self.assertIn("Run log & Undo", page.eval_js("document.getElementById('ap-runs-card').textContent"))

            # Open #/ideas in English dark mode and click "Use these ideas" (#dv-auto-take)
            page.go("#/ideas", "Boolean(document.getElementById('dv-auto-waiting'))")
            self.assertIn("Use these ideas", page.eval_js("document.getElementById('dv-auto-take').textContent"))
            page.eval_js("document.getElementById('dv-auto-take').click(); true")
            page.wait_for("!document.getElementById('dv-auto-waiting') && Boolean(document.getElementById('dv-auto-in-use'))")
            self.assertIn("CandidateSetTwo", page.eval_js("document.getElementById('ideas').textContent"))
            self.assertEqual(page.problems, [])

    def wait_until_sources_saved(self) -> None:
        import time
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            srcs = (self.store.load_config().get(autopilot.KEY) or {}).get("sources")
            if isinstance(srcs, dict) and srcs.get("pages") == ["https://trends.example.com/public"]:
                return
            time.sleep(0.05)
        self.fail("sources not saved within 5s")


    def test_hostile_markup_in_suggestions_ideas_and_autopilot_fields_is_escaped_in_chrome(self) -> None:
        xss = "<img src=x onerror=alert(1)>"
        self.store.upsert_trends([{"keyword": f"秋冬护肤{xss}", "heat": 100000}], source="manual")
        plan = planner.create_plan_item(self.store, {"title": f"秋冬护肤{xss}"})
        self.store.add_plan_suggestions([{
            "id": "deadbeef1234", "plan_id": plan["id"], "keyword": f"秋冬护肤{xss}",
            "keyword_key": f"秋冬护肤{xss}".lower().replace(" ", ""),
            "matched": f"秋冬护肤{xss}", "matched_in": ["title"], "relevance": 1.0,
            "score": 80, "source": "manual", "rising": True, "sightings": 1,
            "run_id": "", "created_at": 20.0,
        }])
        self.store.add_idea_set({
            "id": "feedface5678", "run_id": "", "status": "waiting", "placed_by": "",
            "ideas_json": json.dumps({"ideas": [{"title_zh": f"选题{xss}", "why_zh": xss}], "generated_at": 25.0}, ensure_ascii=False),
            "ideas_sha": "c" * 64, "had_ideas": False, "earlier": None,
            "trends_json": json.dumps([f"热词{xss}"], ensure_ascii=False), "created_at": 25.0,
        })
        autopilot.save_other_setting(self.store, "voice", {"watch_dir": f"/tmp/{xss}", "transcribe_route": "off"})

        with browser.open_app(self.store, lang="zh", theme="light", transport=self._transport) as page:
            page.httpd.app.autopilot.on = True
            for route, ready in (
                ("#/autopilot", "Boolean(document.getElementById('ap-voice-card'))"),
                ("#/plan", "Boolean(document.getElementById('pl-list'))"),
                ("#/ideas", "Boolean(document.getElementById('dv-auto-waiting'))"),
            ):
                page.go(route, ready)
                self.assertEqual(page.eval_js("document.querySelectorAll('#view img').length"), 0, route)
                self.assertEqual(page.problems, [], route)


if __name__ == "__main__":
    unittest.main()
