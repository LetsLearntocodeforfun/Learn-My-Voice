"""Tests for copy-and-paste mode, the 剪映 / CapCut exports, coded errors and the MCP server."""
import http.client
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from studio import exports, llm, mcp_server, pipeline, server
from studio.store import Store
from tests.test_features import DNA_REPLY, HER_SCRIPT, IDEAS_REPLY

ROOT = Path(__file__).resolve().parent.parent
RATE_240 = {"cadence_and_syntax": {"target_speaking_rate_cpm": 240}}   # 4 units per second
CLEAN_LINES = [{"spoken_script_zh": "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈"}]   # 21 units


def script_fixture():
    return {"title": "周日补货", "lines": [
        {"line_no": 1, "audio_mode": "VOICEOVER", "time_range_sec": "00:00-00:04",
         "spoken_script_zh": "周日下午趁冰箱空了 / 拉着某人去趟超市嘛 [轻笑]",
         "onscreen_flower_text_zh": "(周末采购小分队)", "visual_broll_en_zh": "走进超市"},
        {"line_no": 2, "audio_mode": "LIVE_CLIP", "time_range_sec": "00:04-00:07",
         "spoken_script_zh": "Wait, four boxes?", "onscreen_flower_text_zh": "", "visual_broll_en_zh": ""},
        {"line_no": 3, "audio_mode": "VOICEOVER", "time_range_sec": "",
         "spoken_script_zh": "好啦", "onscreen_flower_text_zh": "(完)", "visual_broll_en_zh": ""},
        {"line_no": 4, "audio_mode": "VOICEOVER", "time_range_sec": "",
         "spoken_script_zh": "", "onscreen_flower_text_zh": "(彩蛋)", "visual_broll_en_zh": ""},
    ]}


class ExportTest(unittest.TestCase):
    def test_cue_timing_is_back_to_back_at_her_rate(self):
        subtitles, flowers, total = exports.build_cues(script_fixture()["lines"], 240)
        self.assertEqual(subtitles, [(0.0, 2.25, "周日下午趁冰箱空了"), (2.25, 4.5, "拉着某人去趟超市嘛"), (7.5, 8.0, "好啦")])
        self.assertEqual(flowers, [(0.0, 4.5, "(周末采购小分队)"), (7.5, 8.0, "(完)"), (8.0, 10.0, "(彩蛋)")])
        self.assertEqual(total, 10.0)

    def test_missing_rate_falls_back_and_live_clip_without_range_is_estimated(self):
        lines = [{"audio_mode": "LIVE_CLIP", "spoken_script_zh": "一二三四五六七八"}, {"spoken_script_zh": "好啦好啦"}]
        for rate in (None, 0, True, "fast"):
            self.assertEqual(exports.build_cues(lines, rate)[0], [(2.0, 3.0, "好啦好啦")])

    def test_srt_text(self):
        _, body, content_type = exports.export_file(script_fixture(), "srt", RATE_240)
        self.assertIn("subrip", content_type)
        self.assertEqual(body.decode("utf-8"), (
            "1\n00:00:00,000 --> 00:00:02,250\n周日下午趁冰箱空了\n\n"
            "2\n00:00:02,250 --> 00:00:04,500\n拉着某人去趟超市嘛\n\n"
            "3\n00:00:07,500 --> 00:00:08,000\n好啦\n"))
        self.assertEqual(exports._stamp(3661.5), "01:01:01,500")
        self.assertEqual(exports.to_srt([]), "")

    def test_kit_zip_in_both_languages(self):
        name, body, content_type = exports.export_file(script_fixture(), "jianying", RATE_240, "zh")
        self.assertEqual((name, content_type), ("周日补货-剪映素材包.zip", "application/zip"))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(z.namelist(), ["使用说明.txt", "字幕.srt", "口播文稿.txt", "花字.srt", "拍摄清单.txt"])
            howto = z.read("使用说明.txt").decode("utf-8")
            self.assertEqual(z.read("口播文稿.txt").decode("utf-8"), "周日下午趁冰箱空了\n拉着某人去趟超市嘛\n好啦\n")
            self.assertIn("00:00:08,000 --> 00:00:10,000\n(彩蛋)", z.read("花字.srt").decode("utf-8"))
        self.assertIn("约 10 秒", howto)
        self.assertIn("240 字/分钟", howto)
        self.assertIn("文稿匹配", howto)
        self.assertIn("估算", howto)
        self.assertNotIn("{", howto)

        name, body, _ = exports.export_file(script_fixture(), "jianying", RATE_240, "en")
        self.assertEqual(name, "周日补货-capcut-kit.zip")
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(z.namelist(), ["HOW_TO_USE.txt", "subtitles.srt", "voiceover.txt", "onscreen_text.srt", "shot_list.txt"])
            self.assertIn("estimated", z.read("HOW_TO_USE.txt").decode("utf-8"))

    def test_kit_without_flower_text_or_shots(self):
        bare = {"title": "t", "lines": [{"line_no": 1, "spoken_script_zh": "好啦好啦"}]}
        _, body, _ = exports.export_file(bare, "jianying", None, "zh")
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(z.namelist(), ["使用说明.txt", "字幕.srt", "口播文稿.txt"])
            self.assertIn("没有填花字", z.read("使用说明.txt").decode("utf-8"))
        with self.assertRaises(KeyError):
            exports.export_file(bare, "nope")


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.httpd = server.make_server(0, self.store)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def call(self, method, path, body=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res.status, res, (payload if raw else json.loads(payload.decode("utf-8")))


class ManualModeTest(ServerCase):
    def test_settings_and_direct_endpoints(self):
        _, _, out = self.call("POST", "/api/settings", {"provider": "manual"})
        self.assertEqual((out["settings"]["provider"], out["settings"]["manual"], out["settings"]["configured"]), ("manual", True, True))
        self.assertEqual(self.call("POST", "/api/generate", {"notes": "x"})[2]["error"], "manual_mode")
        self.assertEqual(self.call("POST", "/api/voice/learn", {})[2]["error"], "manual_mode")
        self.assertEqual(self.call("POST", "/api/recommendations", {})[2]["error"], "manual_mode")
        self.assertEqual(self.call("POST", "/api/settings/models", {"provider": "manual"})[2]["error"], "bad_provider")
        with self.assertRaises(llm.LLMError) as ctx:
            pipeline.complete(self.store, "s", "u")
        self.assertEqual(ctx.exception.code, "manual_mode")

    def test_draft_by_copy_and_paste(self):
        self.assertEqual(self.call("POST", "/api/manual/prompt", {"task": "nope"})[2]["error"], "bad_task")
        self.assertEqual(self.call("POST", "/api/manual/prompt", {"task": "draft", "notes": " "})[2]["error"], "notes_required")
        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        request = {"task": "draft", "notes": "去 Costco 补货", "target_sec": 30, "video_format": "COUPLE_VLOG"}
        _, _, out = self.call("POST", "/api/manual/prompt", request)
        prompt = out["prompt"]
        self.assertIn("去 Costco 补货", prompt)
        self.assertIn("Format: COUPLE_VLOG", prompt)
        self.assertIn("排队排了半小时啦", prompt)                    # her own script is in the prompt
        self.assertIn("<non_negotiable_voice_rules>", prompt)       # system part is included
        self.assertTrue(prompt.rstrip().endswith(pipeline.MANUAL_REPLY_RULE))
        self.assertNotIn("{{", prompt)

        status, _, bad = self.call("POST", "/api/manual/reply", dict(request, reply="Sorry, I can't do that."))
        self.assertEqual((status, bad["error"], bad["reason"]), (502, "ai_failed", "bad_json"))
        _, _, empty = self.call("POST", "/api/manual/reply", dict(request, reply='{"script_lines": []}'))
        self.assertEqual(empty["reason"], "empty_result")
        self.assertEqual(self.call("GET", "/api/scripts")[2]["scripts"], [])    # nothing saved by failed replies

        reply = "Here is the script:\n```json\n" + json.dumps({"script_lines": CLEAN_LINES}, ensure_ascii=False) + "\n```"
        status, _, script = self.call("POST", "/api/manual/reply", dict(request, reply=reply))
        self.assertEqual((status, script["has_ai_draft"], script["target_sec"], script["video_format"]), (201, True, 30, "COUPLE_VLOG"))
        self.assertEqual(script["lines"][0]["spoken_script_zh"], CLEAN_LINES[0]["spoken_script_zh"])
        self.assertEqual(script["title"], "去 Costco 补货")

    def test_voice_and_ideas_by_copy_and_paste(self):
        status, _, out = self.call("POST", "/api/manual/prompt", {"task": "voice"})
        self.assertEqual((status, out["error"], out["reason"]), (502, "ai_failed", "no_samples"))
        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT, "views": 2000, "likes": 150})
        self.assertIn("排队排了半小时啦", self.call("POST", "/api/manual/prompt", {"task": "voice"})[2]["prompt"])
        _, _, voice = self.call("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(DNA_REPLY, ensure_ascii=False)})
        self.assertEqual(voice["dna"]["cadence_and_syntax"]["ideal_clause_length_chars"], {"min": 7, "median": 7, "hard_max": 8})
        self.assertFalse(self.call("GET", "/api/dna")[2]["is_example"])

        self.assertIn("Recommend exactly 6", self.call("POST", "/api/manual/prompt", {"task": "ideas"})[2]["prompt"])
        _, _, ideas = self.call("POST", "/api/manual/reply", {"task": "ideas", "reply": json.dumps(IDEAS_REPLY, ensure_ascii=False)})
        self.assertEqual([i["title_zh"] for i in ideas["result"]["ideas"]], ["超市补货第二弹", "早餐"])
        self.assertEqual(ideas["result"]["ideas"][0]["evidence_titles"], ["超市那天"])
        self.assertEqual(ideas["performance"]["rows"][0]["interactions"], 150)

    def test_default_title_follows_language_and_state_has_mcp_command(self):
        self.assertEqual(self.call("POST", "/api/scripts", {"lang": "en"})[2]["title"], "Untitled script")
        self.assertEqual(self.call("POST", "/api/scripts", {})[2]["title"], "未命名脚本")
        mcp = self.call("GET", "/api/state")[2]["mcp"]
        self.assertTrue(Path(mcp["script"]).is_file())
        self.assertEqual(Path(mcp["script"]).name, "run_mcp.py")

    def test_export_endpoints(self):
        _, _, created = self.call("POST", "/api/scripts", {"title": "周日补货", "lines": script_fixture()["lines"]})
        base = "/api/scripts/%s/export/" % created["id"]
        status, res, body = self.call("GET", base + "jianying", raw=True)
        self.assertEqual((status, res.getheader("Content-Type")), (200, "application/zip"))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertIn("字幕.srt", z.namelist())
            self.assertIn("周日下午趁冰箱空了", z.read("字幕.srt").decode("utf-8"))
        _, res, body = self.call("GET", base + "jianying?lang=en", raw=True)
        self.assertIn("capcut-kit.zip", res.getheader("Content-Disposition"))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertIn("subtitles.srt", z.namelist())
        _, res, body = self.call("GET", base + "srt", raw=True)
        self.assertIn("subrip", res.getheader("Content-Type"))
        self.assertTrue(body.decode("utf-8").startswith("1\n00:00:00,000 --> "))
        self.assertEqual(self.call("GET", base + "flower_srt", raw=True)[0], 200)
        self.assertEqual(self.call("GET", base + "nope")[0], 404)


class McpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.next_id = 0

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def rpc(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        reply = mcp_server.handle(self.store, message)
        self.assertEqual((reply["jsonrpc"], reply["id"]), ("2.0", self.next_id))
        return reply

    def tool(self, name, **arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        self.assertEqual([c["type"] for c in result["content"]], ["text"])
        text = result["content"][0]["text"]
        return result["isError"], (json.loads(text) if text.startswith("{") else text)


class McpProtocolTest(McpCase):
    def test_initialize_negotiates_version(self):
        result = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"]["name"], "learn-my-voice")
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "2099-01-01"})["result"]["protocolVersion"], "2025-06-18")
        head = result["instructions"][:512]                               # must stand alone
        for name in ("get_script_brief", "save_script", "get_voice_learning_brief", "get_topic_ideas_brief"):
            self.assertIn(name, head)

    def test_notifications_and_errors(self):
        self.assertIsNone(mcp_server.handle(self.store, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(mcp_server.handle(self.store, {"jsonrpc": "2.0", "id": 9, "result": {}}))
        self.assertEqual(self.rpc("ping")["result"], {})
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "delete_everything", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.rpc("tools/call", {"name": "list_scripts", "arguments": []})["error"]["code"], -32602)
        self.assertEqual(mcp_server.handle(self.store, {"id": 1, "method": "ping"})["error"]["code"], -32600)
        self.assertEqual(mcp_server.handle(self.store, "hi")["error"]["code"], -32600)

    def test_tool_list_is_well_formed(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), len(set(names)))
        read_only = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
        self.assertEqual(read_only, {"get_script_brief", "check_script", "list_scripts", "get_script", "get_voice_profile",
                                     "list_library", "get_voice_learning_brief", "get_topic_ideas_brief"})
        self.assertEqual({t["name"] for t in tools} - read_only,
                         {"save_script", "update_script", "save_voice_profile", "save_topic_ideas", "export_jianying_kit"})
        for t in tools:
            schema = t["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertTrue(set(schema["required"]) <= set(schema["properties"]), t["name"])
            self.assertTrue(t["description"].startswith("Use this"), t["name"])
            self.assertNotIn("fn", t)
            json.dumps(t)

    def test_serve_loop_parse_error_batch_and_ascii_lines(self):
        stdin = io.BytesIO(b"\n".join([
            b"this is not json",
            b"",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
            json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"}]).encode(),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "get_voice_profile", "arguments": {}}}, ensure_ascii=False).encode("utf-8"),
        ]) + b"\n")
        stdout = io.BytesIO()
        mcp_server.serve(self.store, stdin, stdout)
        raw_lines = stdout.getvalue().split(b"\n")
        self.assertEqual(raw_lines[-1], b"")
        lines = [json.loads(l) for l in raw_lines[:-1]]
        self.assertEqual(len(lines), 3)                                   # the notification got no reply
        self.assertEqual((lines[0]["id"], lines[0]["error"]["code"]), (None, -32700))
        self.assertEqual(lines[1], [{"jsonrpc": "2.0", "id": 1, "result": {}}])
        self.assertIn("松弛", lines[2]["result"]["content"][0]["text"])    # Chinese survives the round trip
        stdout.getvalue().decode("ascii")                                 # and the wire format is pure ASCII


class McpToolTest(McpCase):
    def test_write_check_fix_flow(self):
        self.store.create_sample(title="超市那天", text=HER_SCRIPT)
        is_error, brief = self.tool("get_script_brief", notes="再去一次 Costco", target_duration_sec=5, video_format="COUPLE_VLOG")
        self.assertFalse(is_error)
        for expected in ("再去一次 Costco", "Format: COUPLE_VLOG", "Target Duration: 5 seconds", "排队排了半小时啦", "NEXT STEP", "save_script"):
            self.assertIn(expected, brief)
        self.assertNotIn("{{", brief)

        draft = {"script_lines": [{"spoken_script_zh": "这个真的绝绝子 / 上周末跟我老公去 Costco 补货嘛"}]}
        is_error, saved = self.tool("save_script", script=draft, notes="再去一次 Costco", target_duration_sec=5)
        self.assertFalse(is_error)
        slop = [f for f in saved["check"]["findings"] if f["category"] == "GENERIC_XHS_SLOP"]
        self.assertEqual((slop[0]["severity"], slop[0]["matched"]), ("error", "绝绝子"))
        self.assertIn("绝绝子", slop[0]["message_zh"])
        self.assertIn("update_script", saved["next_step"])
        self.assertEqual(self.store.get_script(saved["script_id"])["title"], "再去一次 Costco")

        is_error, fixed = self.tool("update_script", script_id=saved["script_id"], script_lines=CLEAN_LINES, title="补货")
        self.assertEqual((is_error, fixed["check"]["findings"], fixed["title"]), (False, [], "补货"))
        self.assertIn("No must-fix", fixed["next_step"])
        self.assertEqual(self.store.get_script(saved["script_id"])["lines"][0]["spoken_script_zh"], CLEAN_LINES[0]["spoken_script_zh"])

        _, listing = self.tool("list_scripts")
        self.assertEqual([s["id"] for s in listing["scripts"]], [saved["script_id"]])
        _, full = self.tool("get_script", script_id=saved["script_id"])
        self.assertEqual((full["script"]["title"], full["check"]["findings"]), ("补货", []))
        _, check = self.tool("check_script", script_lines=[{"spoken_script_zh": "第二天底妆100%服帖"}], target_duration_sec=60)
        self.assertIn("COMPLIANCE_RISK", [f["category"] for f in check["findings"]])
        self.assertEqual(len(self.store.list_scripts()), 1)                # check_script saved nothing

    def test_bad_arguments_are_tool_errors_not_crashes(self):
        for name, arguments in [
            ("get_script_brief", {}), ("get_script_brief", {"notes": "   "}), ("get_script_brief", {"notes": 5}),
            ("get_script_brief", {"notes": "x", "video_format": "TIKTOK"}),
            ("get_script_brief", {"notes": "x", "target_duration_sec": "long"}),
            ("save_script", {}), ("save_script", {"script": "not json"}), ("save_script", {"script": {"script_lines": []}}),
            ("update_script", {"script_id": "000000000000", "script_lines": CLEAN_LINES}),
            ("update_script", {"script_id": "x", "script_lines": []}), ("check_script", {"script_lines": "text"}),
            ("get_script", {}), ("export_jianying_kit", {"script_id": "missing"}),
            ("save_voice_profile", {"profile": DNA_REPLY}),                # no scripts in the library yet
            ("get_voice_learning_brief", {}), ("get_topic_ideas_brief", {}), ("save_topic_ideas", {"ideas": IDEAS_REPLY}),
        ]:
            is_error, text = self.tool(name, **arguments)
            self.assertTrue(is_error, (name, arguments))
            self.assertTrue(str(text).startswith("Error: "), (name, text))
        self.assertEqual(self.store.list_scripts(), [])
        self.assertTrue(self.store.load_dna()[1])                          # still the example profile

    def test_voice_ideas_library_and_kit(self):
        _, profile = self.tool("get_voice_profile")
        self.assertTrue(profile["is_example"])
        self.store.create_sample(title="超市那天", text=HER_SCRIPT, metrics={"views": 2000, "likes": 150})
        self.store.create_sample(title="商单面霜", text="这款面霜搭载三重神经酰胺", is_sponsored=True)

        _, brief = self.tool("get_voice_learning_brief")
        self.assertIn("排队排了半小时啦", brief)
        self.assertIn("save_voice_profile", brief)
        is_error, saved = self.tool("save_voice_profile", profile=json.dumps(DNA_REPLY, ensure_ascii=False))   # JSON as a string is accepted
        self.assertEqual((is_error, saved["saved"]), (False, True))
        self.assertEqual(saved["cadence_and_syntax"]["ideal_clause_length_chars"]["hard_max"], 8)
        self.assertFalse(saved["source"]["speaking_rate_confirmed"])
        self.assertFalse(self.tool("get_voice_profile")[1]["is_example"])

        _, library = self.tool("list_library")
        self.assertEqual([s["title"] for s in library["scripts"]], ["商单面霜", "超市那天"])
        self.assertEqual(library["performance"]["rows"][0]["interactions"], 150)
        _, brief = self.tool("get_topic_ideas_brief", idea_count=3)
        self.assertIn("Recommend exactly 3", brief)
        _, ideas = self.tool("save_topic_ideas", ideas=IDEAS_REPLY)
        self.assertEqual(ideas["ideas"][0]["evidence_titles"], ["超市那天"])
        self.assertEqual(self.store.load_recommendations()["ideas"][0]["title_zh"], "超市补货第二弹")

        script = self.store.create_script(title="周日/补货", lines=script_fixture()["lines"])
        is_error, kit = self.tool("export_jianying_kit", script_id=script["id"])
        self.assertFalse(is_error)
        path = Path(kit["saved_to"])
        self.assertEqual((path.parent, path.name), (self.tmp / "exports", "周日_补货-剪映素材包.zip"))
        with zipfile.ZipFile(str(path)) as z:
            self.assertIn("字幕.srt", z.namelist())
        self.assertIn("estimated", kit["caveat"])


class McpSubprocessTest(unittest.TestCase):
    def test_real_stdio_from_another_working_directory(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "save_script", "arguments": {"script": {"script_lines": CLEAN_LINES}, "notes": "补货", "target_duration_sec": 5}}},
            ]
            stdin = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages).encode("utf-8")
            done = subprocess.run([sys.executable, str(ROOT / "run_mcp.py"), "--data-dir", str(tmp / "data")],
                                  input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(tmp), timeout=60)
            self.assertEqual((done.returncode, done.stderr), (0, b""))
            replies = [json.loads(line) for line in done.stdout.decode("ascii").splitlines()]
            self.assertEqual([r["id"] for r in replies], [1, 2, 3])
            self.assertEqual(len(replies[1]["result"]["tools"]), 13)
            saved = json.loads(replies[2]["result"]["content"][0]["text"])
            self.assertEqual(saved["check"]["findings"], [])
            stored = Store(tmp / "data").get_script(saved["script_id"])
            self.assertEqual(stored["lines"][0]["spoken_script_zh"], CLEAN_LINES[0]["spoken_script_zh"])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
