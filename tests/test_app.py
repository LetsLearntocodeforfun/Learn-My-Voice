"""Server, storage, prompt and provider-client tests. No network: providers are faked."""
import http.client
import json
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from studio import guardrails, llm, pipeline, server
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
WORKED = json.loads((ROOT / "tests" / "fixtures" / "worked_example_script.json").read_text(encoding="utf-8"))

AI_REPLY = {
    "script_lines": [
        {"line_no": 7, "beat_type": "HOOK", "audio_mode": "VOICEOVER", "time_range_sec": "00:00-00:04",
         "spoken_script_zh": "上周末跟我老公去 Costco 补货嘛", "visual_broll_en_zh": "Walking in / 进门"},
        {"line_no": 9, "spoken_script_zh": "  "},
        {"line_no": 12, "spoken_script_zh": "结果某人又走不动道哈", "onscreen_flower_text_zh": "(某人又馋了)"},
    ],
    "rednote_post_package": {"hashtags_zh": ["#夫妻日常"]},
}


class FakeProvider:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "GET" and "openai.com" in url:
            return {"data": [{"id": "chat-b"}, {"id": "text-embedding-x"}, {"id": "chat-a"}, {"id": "whisper-1"}]}
        if method == "GET":
            return {"models": [
                {"name": "models/gem-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gem-embed", "supportedGenerationMethods": ["embedContent"]},
            ]}
        text = "```json\n" + json.dumps(AI_REPLY, ensure_ascii=False) + "\n```"
        if "openai.com" in url:
            return {"choices": [{"message": {"content": text}}]}
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class LLMTest(unittest.TestCase):
    def test_list_models_filters_non_text_models(self):
        fake = FakeProvider()
        self.assertEqual(llm.list_models("openai", "k", fake), ["chat-a", "chat-b"])
        self.assertEqual(llm.list_models("gemini", "k", fake), ["gem-pro"])

    def test_complete_json_both_providers_and_auth_headers(self):
        fake = FakeProvider()
        self.assertEqual(llm.complete_json("openai", "sk-1", "m", "sys", "usr", fake), AI_REPLY)
        self.assertEqual(llm.complete_json("gemini", "g-1", "m", "sys", "usr", fake), AI_REPLY)
        self.assertEqual(fake.calls[0][2], {"Authorization": "Bearer sk-1"})
        self.assertEqual(fake.calls[1][2], {"x-goog-api-key": "g-1"})
        self.assertNotIn("g-1", fake.calls[1][1])  # key travels in a header, not the URL

    def test_bad_replies_raise(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json_text("not json")
        with self.assertRaises(llm.LLMError):
            llm.parse_json_text("[1, 2]")
        with self.assertRaises(llm.LLMError):
            llm.complete_json("gemini", "k", "m", "s", "u", lambda *a: {"candidates": []})


class PromptTest(unittest.TestCase):
    def test_stage4_blueprint_blocks_load(self):
        system, user = pipeline.load_prompt_pair(pipeline.STAGE4)
        self.assertIn("<non_negotiable_voice_rules>", system)
        self.assertIn("{{user_topic_and_story_notes}}", user)
        self.assertIn('"script_lines"', user)

    def test_render_requires_every_placeholder(self):
        self.assertEqual(pipeline.render("a {{x}} b", {"x": "1"}), "a 1 b")
        with self.assertRaises(pipeline.PromptError):
            pipeline.render("{{x}} {{y}}", {"x": "1"})


class StatsTest(unittest.TestCase):
    def test_script_stats_agrees_with_duration_finding(self):
        stats = guardrails.script_stats(WORKED, DNA)
        self.assertEqual((stats["units"], stats["budget"], stats["hard_max"]), (283, 234, 18))
        duration = [f for f in guardrails.lint_script(WORKED, DNA) if f.category == guardrails.DURATION_OFF_BUDGET]
        self.assertEqual(duration[0].matched, "283 units")
        self.assertIn("budget 234", duration[0].detail)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.fake = FakeProvider()
        self.httpd = server.make_server(0, self.store, self.fake)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def call(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res.status, res, (payload if raw else json.loads(payload.decode("utf-8")))

    def test_static_ui_and_path_traversal(self):
        status, res, body = self.call("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("text/html", res.getheader("Content-Type"))
        self.assertIn('lang="zh-CN"', body.decode("utf-8"))
        self.assertEqual(self.call("GET", "/app.js", raw=True)[0], 200)
        self.assertEqual(self.call("GET", "/../store.py")[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/store.py")[0], 404)

    def test_rejects_foreign_host_and_non_json_writes(self):
        self.assertEqual(self.call("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/scripts", body=b"title=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(conn.getresponse().status, 415)
        conn.close()

    def test_script_crud_lint_and_exports(self):
        status, _, created = self.call("POST", "/api/scripts", {"notes": "周日去超市补货\n更多", "target_sec": 30})
        self.assertEqual(status, 201)
        self.assertEqual((created["title"], created["target_sec"], len(created["lines"])), ("周日去超市补货", 30, 1))
        sid = created["id"]

        lines = [
            {"spoken_script_zh": "这个真的绝绝子 / 第二天底妆100%服帖 [轻笑]", "visual_broll_en_zh": "桌面特写",
             "onscreen_flower_text_zh": "(表面镇定)", "time_range_sec": "00:00-00:05"},
            {"spoken_script_zh": "Wait what?", "audio_mode": "LIVE_CLIP"},
            {"spoken_script_zh": "好啦，下期见"},
        ]
        _, _, updated = self.call("PUT", "/api/scripts/" + sid, {"title": "测试", "lines": lines})
        self.assertEqual([l["line_no"] for l in updated["lines"]], [1, 2, 3])
        self.assertEqual(updated["lines"][2]["audio_mode"], "VOICEOVER")

        _, _, lint = self.call("POST", "/api/lint", {"lines": updated["lines"], "target_sec": 30})
        by_cat = {(f["line_no"], f["category"]): f for f in lint["findings"]}
        self.assertIn("绝绝子", by_cat[(1, "GENERIC_XHS_SLOP")]["message_zh"])
        self.assertEqual(by_cat[(1, "COMPLIANCE_RISK")]["severity"], "error")
        self.assertIn("偏短了", by_cat[(None, "DURATION_OFF_BUDGET")]["message_zh"])
        self.assertTrue(lint["dna_is_example"])
        self.assertEqual(lint["stats"]["budget"], 119)

        status, res, text = self.call("GET", "/api/scripts/%s/export/voiceover" % sid, raw=True)
        self.assertEqual(text.decode("utf-8"), "这个真的绝绝子\n第二天底妆100%服帖\n好啦，下期见\n")
        self.assertIn("filename*=UTF-8''", res.getheader("Content-Disposition"))
        self.assertEqual(self.call("GET", "/api/scripts/%s/export/shots" % sid, raw=True)[2].decode("utf-8"), "1. 00:00-00:05  桌面特写\n")
        self.assertEqual(self.call("GET", "/api/scripts/%s/export/flower" % sid, raw=True)[2].decode("utf-8"), "1. 00:00-00:05  (表面镇定)\n")
        self.assertEqual(self.call("GET", "/api/scripts/%s/export/nope" % sid)[0], 404)

        self.assertEqual(len(self.call("GET", "/api/scripts")[2]["scripts"]), 1)
        self.assertEqual(self.call("DELETE", "/api/scripts/" + sid)[0], 200)
        self.assertEqual(self.call("GET", "/api/scripts/" + sid)[0], 404)

    def test_settings_never_expose_keys_and_generation_works(self):
        self.assertEqual(self.call("POST", "/api/generate", {"notes": "x"})[2]["error"], "ai_not_configured")
        self.assertEqual(self.call("POST", "/api/settings/models", {"provider": "gemini"})[2]["error"], "key_required")
        self.assertEqual(self.call("POST", "/api/settings/models", {"provider": "other", "key": "k"})[0], 400)

        _, _, out = self.call("POST", "/api/settings/models", {"provider": "gemini", "key": "SECRET-KEY"})
        self.assertEqual(out["models"], ["gem-pro"])
        _, _, saved = self.call("POST", "/api/settings", {"provider": "gemini", "model": "gem-pro"})
        self.assertEqual(saved["settings"], {"provider": "gemini", "model": "gem-pro", "local_base_url": "",
                                             "keys": {"gemini": True, "openai": False, "local": False}, "configured": True})
        _, _, raw_state = self.call("GET", "/api/state", raw=True)
        self.assertNotIn(b"SECRET-KEY", raw_state)
        self.assertEqual(stat.S_IMODE(self.store.config_path.stat().st_mode), 0o600)

        self.assertEqual(self.call("POST", "/api/generate", {"notes": "  "})[2]["error"], "notes_required")
        status, _, script = self.call("POST", "/api/generate", {"notes": "去 Costco 补货", "target_sec": 30, "video_format": "COUPLE_VLOG"})
        self.assertEqual(status, 201)
        self.assertTrue(script["has_ai_draft"])
        self.assertEqual([l["line_no"] for l in script["lines"]], [1, 2])  # blank line dropped, renumbered
        self.assertEqual(script["post"], {"hashtags_zh": ["#夫妻日常"]})

        method, url, headers, body = self.fake.calls[-1]
        self.assertTrue(url.endswith("/models/gem-pro:generateContent"))
        self.assertEqual(headers, {"x-goog-api-key": "SECRET-KEY"})
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertIn("去 Costco 补货", prompt)
        self.assertIn("Format: COUPLE_VLOG", prompt)
        self.assertIn("Target Duration: 30 seconds", prompt)
        self.assertIn('"target_speaking_rate_cpm": 238', prompt)
        self.assertNotIn("{{", prompt)

    def test_provider_failure_is_reported_not_crashed(self):
        def failing(method, url, headers, body):
            raise llm.LLMError("HTTP 401: bad key")
        self.httpd.app.transport = failing
        status, _, out = self.call("POST", "/api/settings/models", {"provider": "openai", "key": "k"})
        self.assertEqual((status, out["error"], out["message"]), (502, "ai_failed", "HTTP 401: bad key"))
        self.assertFalse(self.store.public_settings()["keys"]["openai"])  # a key that failed is not saved


if __name__ == "__main__":
    unittest.main()
