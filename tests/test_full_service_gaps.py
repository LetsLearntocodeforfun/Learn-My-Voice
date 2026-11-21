"""End-to-end tests for Phase 1 full-service upgrades:
1. Auto-Updater, SHA-256 verification & cross-version settings/keys/DB persistence (Desktop & .zip)
2. Dynamic RedNote view-count velocity, Creator Center batch import & audience comment sentiment
3. Seamless Upcoming Video Planner (#/plan) & Stage 6 sentiment/plan feedback loop
4. Multi-dimensional Voice & Tone Analyzer, WAV acoustic prosody & Anthropic Claude provider
"""
from __future__ import annotations

import array
import base64
import hashlib
import http.client
import io
import json
import math
import os
import shutil
import tempfile
import threading
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock

from studio import backup, llm, mcp_server, outside, pipeline, planner, posted, rednote_analytics, server, tone_analyzer, trends, updater
from studio.store import EXAMPLE_DNA_PATH, Store


def _make_wav_bytes(duration_sec: float = 2.2, sample_rate: int = 16000) -> bytes:
    """Synthesize a 16-bit mono WAV with loud opening speech, a 0.45s breath pause, and softer closing speech."""
    n = int(duration_sec * sample_rate)
    samples = array.array("h")
    for i in range(n):
        t = i / float(sample_rate)
        if 0.85 <= t <= 1.35:
            val = 0
        elif t < 0.85:
            amp = 14000 * (0.6 + 0.4 * math.sin(2.0 * math.pi * 4.0 * t))
            val = int(amp * math.sin(2.0 * math.pi * 210.0 * t))
        else:
            amp = 4500
            val = int(amp * math.sin(2.0 * math.pi * 185.0 * t))
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


class ServerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-gaps-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.key_env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "os-keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        self.key_env.start()
        self.addCleanup(self.key_env.stop)
        self.app_dir = self.tmp / "learn-my-voice-0.2.0-beta"
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.app_dir / "data")
        self.downloads_dir = self.tmp / "Downloads"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        self.httpd.app.outside = mock.Mock(
            downloads_folder=lambda: self.downloads_dir,
            save=outside.save,
            reveal=lambda p: True,
            open_link=lambda u: True,
            link_problem=outside.link_problem,
        )
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def api(self, method: str, path: str, body: object = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": "127.0.0.1:%d" % self.port}
            raw = None
            if body is not None:
                raw = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif method == "POST":
                raw = b"{}"
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=raw, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
        finally:
            conn.close()


class UpdaterAndPersistenceTest(ServerHarness):
    def test_semver_and_channel_ordering(self) -> None:
        ordered = [
            "0.2.0-beta.dev",
            "0.2.0-beta.rc.1",
            "0.2.0-beta.rc.1.2",
            "0.2.0-beta.rc.9",
            "0.2.0-beta",
            "0.2.0.rc.1",
            "0.2.0",
            "0.3.0-beta.rc.1",
            "0.3.0-beta",
            "0.3.0",
        ]
        for i in range(len(ordered) - 1):
            self.assertTrue(updater.is_newer(ordered[i + 1], ordered[i]), (ordered[i], ordered[i + 1]))
            self.assertFalse(updater.is_newer(ordered[i], ordered[i + 1]))

    def test_startup_check_sha256_download_and_zip_cross_version_settings_migration(self) -> None:
        # Build a valid synthetic new version .zip archive (v0.3.0-beta)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("learn-my-voice-0.3.0-beta/README.md", "# Learn My Voice 0.3.0\n")
            zf.writestr(
                "learn-my-voice-0.3.0-beta/studio/build.json",
                json.dumps({"version": "0.3.0-beta", "commit": "a" * 40, "built_on": "2026-11-21"}),
            )
        zip_bytes = zip_buf.getvalue()
        good_sha = hashlib.sha256(zip_bytes).hexdigest()

        releases_payload = [{
            "tag_name": "v0.3.0-beta",
            "prerelease": True,
            "draft": False,
            "published_at": "2026-11-21T00:00:00Z",
            "html_url": "https://github.com/LetsLearntocodeforfun/Learn-My-Voice/releases/tag/v0.3.0-beta",
            "body": "Auto-updater and RedNote insights",
            "assets": [
                {
                    "name": "learn-my-voice-0.3.0-beta.zip",
                    "browser_download_url": "https://github.com/LetsLearntocodeforfun/Learn-My-Voice/releases/download/v0.3.0-beta/learn-my-voice-0.3.0-beta.zip",
                    "size": len(zip_bytes),
                },
                {
                    "name": "learn-my-voice-0.3.0-beta.zip.sha256",
                    "browser_download_url": "https://github.com/LetsLearntocodeforfun/Learn-My-Voice/releases/download/v0.3.0-beta/learn-my-voice-0.3.0-beta.zip.sha256",
                    "size": 90,
                },
            ],
        }]

        checksum_holder = {"sha": good_sha}

        def fake_update_transport(method: str, url: str, headers: dict) -> bytes:
            if url.endswith("/releases?per_page=15"):
                return json.dumps(releases_payload).encode("utf-8")
            if url.endswith(".zip.sha256"):
                return ("%s  learn-my-voice-0.3.0-beta.zip\n" % checksum_holder["sha"]).encode("utf-8")
            if url.endswith(".zip"):
                return zip_bytes
            raise AssertionError("Unexpected URL: " + url)

        self.httpd.app.update_transport = fake_update_transport
        self.httpd.app.update_checks = True          # off for every server a test makes, unless the test says so

        # Save user settings, sealed Anthropic key, UI preferences, Voice DNA, and a script in 0.2.0-beta
        self.store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5"),
            c["keys"].update(anthropic="sk-ant-secret-key-9999"),
        ))
        self.store.keep_for_page({"lang": "en", "theme": "dark", "accent": "purple"})
        dna = json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8"))
        self.store.save_dna(dna)
        self.store.create_script(title="My Saved Script", notes="Keep me across versions")

        fake_build = {"from_source": False, "version": "0.2.0-beta", "commit": "b" * 40, "built_on": "2026-11-18"}
        with mock.patch("studio.buildinfo.current", return_value=fake_build), \
             mock.patch("studio.buildinfo.desktop", return_value=None):
            st, up = self.api("GET", "/api/update?check=1")
            self.assertEqual(st, 200)
            self.assertTrue(up["update_available"])
            self.assertTrue(up["prompt_on_startup"])
            self.assertEqual(up["latest"]["version"], "0.3.0-beta")

            # Tampered checksum is rejected before saving
            checksum_holder["sha"] = "0" * 64
            st_bad, bad_res = self.api("POST", "/api/update/download", {})
            self.assertEqual((st_bad, bad_res["error"]), (502, "checksum_mismatch"))

            # Valid SHA-256 download succeeds
            checksum_holder["sha"] = good_sha
            st_dl, dl_res = self.api("POST", "/api/update/download", {})
            self.assertEqual(st_dl, 201)
            self.assertTrue(dl_res["sha256_verified"])
            saved_zip = Path(dl_res["saved"]["path"])
            self.assertTrue(saved_zip.is_file())

            # Unpack new .zip and migrate all user settings, sealed API keys, Voice DNA, and scripts!
            st_ap, ap_res = self.api("POST", "/api/update/apply-zip", {"zip_path": str(saved_zip)})
            self.assertEqual(st_ap, 201, ap_res)
            new_app_dir = Path(ap_res["unpacked_to"])
            self.assertEqual(new_app_dir.name, "learn-my-voice-0.3.0-beta")

            new_store = Store(new_app_dir / "data")
            new_cfg = new_store.load_config()
            self.assertEqual(new_cfg["provider"], "anthropic")
            self.assertEqual(new_cfg["model"], "claude-sonnet-4-5")
            self.assertEqual(new_cfg["keys"]["anthropic"], "sk-ant-secret-key-9999")
            self.assertEqual(new_store.load_page_keeps(), {"lang": "en", "theme": "dark", "accent": "purple"})
            _, is_ex = new_store.load_dna()
            self.assertFalse(is_ex)
            self.assertEqual(new_store.list_scripts()[0]["title"], "My Saved Script")


class RedNoteAnalyticsAndPlannerTest(ServerHarness):
    def test_share_link_extraction_velocity_sentiment_and_planner_lifecycle(self) -> None:
        # 1. Extract RedNote mobile share snippet
        st, link = self.api("POST", "/api/rednote/extract-link", {
            "text": "67 秋冬干皮晚间护肤实话 复制后打开【小红书】查看笔记！ http://xhslink.com/a/AbCd123"
        })
        self.assertEqual(st, 200)
        self.assertEqual(link["url"], "http://xhslink.com/a/AbCd123")
        self.assertIn("秋冬干皮晚间护肤实话", link["title_hint"])

        # 2. Seed library samples and batch-import Creator Center CSV with time-series velocity
        s1 = self.store.create_sample(
            title="秋冬干皮护肤",
            text="一入冬吹暖气我这张干皮真的服了 / 结果换了面霜稳多了哈",
        )
        self.store.update_sample(s1["id"], {"views": 10000, "likes": 300, "collects": 400, "comments": 30}, record_snapshot=False)
        self.store.record_metric_snapshot(
            s1["id"], views=10000, likes=300, collects=400, comments=30, recorded_on="2026-11-15"
        )
        self.store.create_sample(
            title="周末超市日常",
            text="周末去超市买菜嘛 / 买了两盒草莓啦",
            metrics={"views": 2000, "likes": 80, "collects": 20, "comments": 5},
        )

        csv_text = "笔记标题,观看量,点赞量,收藏量,评论量,统计日期\n秋冬干皮护肤,2.5万,900,1200,85,2026-11-20\n"
        st_imp, imp = self.api("POST", "/api/rednote/import", {
            "filename": "xhs_creator.csv",
            "data_base64": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"),
        })
        self.assertEqual(st_imp, 201)
        self.assertEqual(imp["updated"], 1)
        top_vid = imp["overview"]["videos"][0]
        self.assertEqual(top_vid["title"], "秋冬干皮护肤")
        self.assertEqual(top_vid["views"], 25000)
        self.assertEqual(top_vid["quadrant"], "BREAKOUT_HIT")
        self.assertEqual(top_vid["velocity"]["views_per_day"], 3000.0)  # +15,000 views over 5 days

        # 3. Ingest RedNote viewer comments & run deterministic + AI sentiment synthesis
        raw_comments = "\n".join([
            "小鹿：大干皮吹暖气晚上单涂一层够不够润呀？ 赞 42",
            "阿May：某人出镜那段太真实好笑了哈哈哈 2天前 上海 赞 18",
            "Cici：求面霜链接！敏感肌能用吗？ 赞 15",
            "路人甲：玻璃瓶会不会太沉出差不好带 赞 4",
        ])
        st_c, c_res = self.api("POST", "/api/samples/%s/comments" % s1["id"], {"text": raw_comments})
        self.assertEqual(st_c, 201)
        self.assertEqual(c_res["total_comments"], 4)
        self.assertGreaterEqual(len(c_res["sentiment"]["audience_questions"]), 2)

        # AI sentiment synthesis via Anthropic Claude
        self.store.update_config(lambda c: (
            c.update(provider="anthropic", model="claude-sonnet-4-5"),
            c["keys"].update(anthropic="sk-ant-test-12345"),
        ))
        ai_sent_reply = {
            "overall_sentiment": "mostly_positive",
            "sentiment_breakdown_pct": {"positive": 70, "neutral": 15, "negative": 15},
            "summary_zh": "观众非常喜欢自然互动，集中追问暖气房保湿度与敏感肌适用性。",
            "summary_en": "Strong positive engagement; viewers ask about overnight heated-room hydration.",
            "praise_themes": [{"theme_zh": "互动真实好笑", "theme_en": "Funny authentic vibe", "sample_comment": "太真实好笑了"}],
            "objections_or_pain_points": [{"issue_zh": "瓶身偏沉", "issue_en": "Heavy jar", "sample_comment": "玻璃瓶会不会太沉"}],
            "audience_questions": [{"question_zh": "大干皮暖气房单涂一层够不够？", "question_en": "Enough for heated rooms?", "demand_count": 43}],
            "followup_video_angles": [{
                "title_zh": "回应评论区｜干皮暖气房过夜实测",
                "title_en": "Heated room overnight test Q&A",
                "video_format": "VLOG_VOICEOVER",
                "target_duration_sec": 60,
                "rationale_zh": "42赞高热追问",
                "rationale_en": "42-like top viewer question",
                "script_notes_zh": "实拍睡前涂抹与早起状态",
            }],
        }

        def fake_claude_transport(method: str, url: str, headers: dict, body: object) -> dict:
            if url.endswith("/messages"):
                self.assertEqual(headers["x-api-key"], "sk-ant-test-12345")
                self.assertEqual(body["temperature"], 0.2)
                return {"content": [{"type": "text", "text": json.dumps(ai_sent_reply, ensure_ascii=False)}]}
            raise AssertionError("Unexpected LLM call: " + url)

        self.httpd.app.transport = fake_claude_transport
        st_ai, ai_res = self.api("POST", "/api/samples/%s/sentiment/ai" % s1["id"], {})
        self.assertEqual(st_ai, 200)
        self.assertEqual(ai_res["source"], "ai")
        self.assertEqual(ai_res["summary"]["followup_video_angles"][0]["title_zh"], "回应评论区｜干皮暖气房过夜实测")

        # 4. Plan upcoming video -> draft script -> mark posted -> status auto-syncs to POSTED!
        st_pl, pl_res = self.api("POST", "/api/plans", {
            "title": "回应评论区｜干皮暖气房过夜实测",
            "scheduled_for": "2026-11-25",
            "questions": ["大干皮暖气房单涂一层够不够？"],
            "keywords": ["秋冬护肤"],
            "source_kind": "sentiment_followup",
        })
        self.assertEqual(st_pl, 201)
        plan_id = pl_res["plan"]["id"]

        # Verify Stage 6 ideas_prompt includes audience_sentiment AND already_planned_videos_do_not_duplicate
        _, ideas_usr = pipeline.ideas_prompt(self.store)
        self.assertIn("大干皮暖气房单涂一层够不够", ideas_usr)
        self.assertIn("already_planned_videos_do_not_duplicate", ideas_usr)

        # Verify apply_ideas preserves Stage 6 patterns
        saved_ideas = pipeline.apply_ideas(self.store, {
            "patterns": [{"pattern_zh": "干皮实测收藏率最高", "pattern_en": "Dry skin tests get top saves", "evidence_titles": ["秋冬干皮护肤", "假标题"]}],
            "ideas": [{"title_zh": "干皮晨间精简护肤", "evidence_titles": ["秋冬干皮护肤"]}],
        })
        self.assertEqual(saved_ideas["patterns"][0]["evidence_titles"], ["秋冬干皮护肤"])

        # Draft blank script from plan -> status moves to SCRIPTING
        st_dr, dr_res = self.api("POST", "/api/plans/%s/draft" % plan_id, {"use_ai": False})
        self.assertEqual(st_dr, 201)
        self.assertEqual(dr_res["plan"]["status"], "SCRIPTING")
        script_id = dr_res["script"]["id"]
        self.assertIn("大干皮暖气房单涂一层够不够", dr_res["script"]["notes"])

        # Write spoken lines, then mark script as posted -> plan status automatically promotes to POSTED!
        self.store.update_script(script_id, {"lines": [{"spoken_script_zh": "好多人问暖气房单涂够不够嘛 / 我实测了一整晚哈"}]})
        posted.mark(self.store, script_id, {"posted_on": "2026-11-20", "views": 18000, "likes": 600})
        _, synced_plans = self.api("GET", "/api/plans")
        matched_plan = next(p for p in synced_plans["plans"] if p["id"] == plan_id)
        self.assertEqual(matched_plan["status"], "POSTED")

        # 5. Full backup & restore round-trip preserves DATA_VERSION 3 tables!
        backups = self.httpd.app.backups
        made = backups.make()
        incoming = self.tmp / "incoming.lmvbackup"
        shutil.copyfile(str(backups.made(made["id"])["path"]), str(incoming))
        fresh_dir = self.tmp / "restored-data"
        fresh_store = Store(fresh_dir)
        fresh_backups = backup.Backups(fresh_store, self.httpd.app.jobs)
        staged = fresh_backups.stage(incoming)
        fresh_backups.restore(staged["restore_id"])
        self.assertEqual(len(fresh_store.list_video_plans()), 1)
        self.assertEqual(len(fresh_store.list_sample_comments(s1["id"])), 4)
        self.assertIsNotNone(fresh_store.get_sample_sentiment(s1["id"]))
        self.assertGreaterEqual(len(fresh_store.list_metric_snapshots(s1["id"])), 2)


class ToneAnalyzerProsodyAndMcpTest(ServerHarness):
    def test_tone_analyzer_wav_prosody_and_mcp_tools(self) -> None:
        # 1. Pure-stdlib WAV acoustic prosody analyzer
        wav_bytes = _make_wav_bytes()
        prosody = tone_analyzer.analyze_wav_prosody(wav_bytes)
        self.assertIsNotNone(prosody)
        assert prosody is not None
        self.assertGreater(prosody["dynamic_range_db"], 5.0)
        self.assertGreaterEqual(prosody["pause_count"], 1)
        self.assertGreater(prosody["energy_arc_db"]["opening_db"], prosody["energy_arc_db"]["closing_db"])

        # 2. Seed organic corpus with multi-char particles, colloquial connectors, and sensory reduplications
        for idx, txt in enumerate([
            "上周末跟我老公去 Costco 补货嘛 / 最搞笑的是某人站在货架前发呆哈 / 推开水水的很舒服对吧",
            "不过说实话换季脸真的干干巴巴 / 但如果你是大油皮可能会觉得稍微有点润啦 / 软软糯糯的质感[轻笑]",
            "顺便提一嘴这件毛衣洗了两次也没变形嘛 / 随便抓一件就能出门哈 / 真的巨省心喔唷",
        ]):
            self.store.create_sample(
                title="有机样本%d" % idx,
                text=txt,
                timing={"speaking_rate_cpm": 236, "pauses": 3, "spoken_units": 42, "acoustic_prosody": prosody},
            )

        st, tone = self.api("GET", "/api/tone")
        self.assertEqual(st, 200)
        ling = tone["linguistics"]
        self.assertIn("对吧", ling["particles"]["multi"])
        self.assertIn("喔唷", ling["particles"]["multi"])
        self.assertIn("最搞笑的是", ling["connectors"]["colloquial"])
        self.assertIn("软软糯糯", ling["sensory_reduplications"])
        self.assertIn("水水的", ling["sensory_reduplications"])
        self.assertIsNotNone(tone["pauses_per_100_units"])
        self.assertEqual(len(tone["acoustic_prosody_samples"]), 3)

        # Compare a hype-heavy script against the restrained organic baseline
        hype_script = self.store.create_script(
            title="Hype Test",
            lines=[
                {"spoken_script_zh": "因此今天绝绝子按头安利！闭眼入天花板！！"},
                {"spoken_script_zh": "不仅如此众所周知真的炸裂必买！！"},
                {"spoken_script_zh": "总而言之疯抢封神！！"},
            ],
        )
        st_cmp, cmp_res = self.api("GET", "/api/scripts/%s/tone" % hype_script["id"])
        self.assertEqual(st_cmp, 200)
        self.assertTrue(cmp_res["ready"])
        self.assertTrue(any(d["axis"] == "restraint_authenticity" for d in cmp_res["diagnostics"]))

        # 3. Verify new MCP tools over JSON-RPC
        res_tone = mcp_server.handle(self.store, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "get_tone_analysis", "arguments": {"script_id": hype_script["id"]}},
        })
        self.assertFalse(res_tone["result"].get("isError", False))

        res_plan = mcp_server.handle(self.store, {
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "add_video_plan", "arguments": {"title": "MCP 规划的新视频", "scheduled_for": "2026-12-01"}},
        })
        self.assertFalse(res_plan["result"].get("isError", False))
        self.assertEqual(self.store.plans_count(), 1)


if __name__ == "__main__":
    unittest.main()
