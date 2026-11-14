"""Tests for local models, the script library, voice learning and topic recommendations."""
import base64
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from studio import corpus, llm, server
from studio.store import Store

HER_SCRIPT = "\n".join(["今天去超市买菜 / 结果人特别多嘛 / 排队排了半小时啦"] * 12)   # 36 clauses: 24x7 units, 12x8 units

DNA_REPLY = {
    "profile_version": "1.0",
    "persona_summary": {"one_line_essence_zh": "松弛", "audience_address_terms": {"uses": ["你们看"], "never_uses": ["宝子们"]}},
    "cadence_and_syntax": {"target_speaking_rate_cpm": 235, "ideal_clause_length_chars": {"min": 6, "median": 11, "hard_max": 20}},
    "anti_voice_blacklist": {"banned_phrases_zh": ["绝绝子"], "learned_from_her_edits": []},
}
IDEAS_REPLY = {
    "data_note_zh": "只有两条有数据", "data_note_en": "Only two videos have numbers",
    "ideas": [
        {"title_zh": "超市补货第二弹", "title_en": "Grocery run part 2", "kind": "BUILD_ON_WINNER", "why_zh": "上次互动最高",
         "why_en": "Top interactions", "evidence_titles": ["超市那天", "一条不存在的视频"], "confidence": "low",
         "video_format": "COUPLE_VLOG", "target_duration_sec": 90, "script_notes_zh": "周末再去一次"},
        {"title_zh": "", "why_zh": "空标题应被丢弃"},
        {"title_zh": "早餐", "video_format": "NOT_A_FORMAT", "target_duration_sec": 45, "evidence_titles": "oops"},
    ],
}
SCRIPT_REPLY = {"script_lines": [{"spoken_script_zh": "今天去超市买菜"}]}


def make_docx(paragraphs):
    body = "".join('<w:p w:rsidR="1"><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document><w:body>%s</w:body></w:document>' % body)
    return buf.getvalue()


class FakeLocal:
    """Stands in for an OpenAI-compatible local server; answers by which blueprint is asking."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "GET":
            return {"data": [{"id": "local-model-b"}, {"id": "local-model-a"}]}
        system = body["messages"][0]["content"]
        reply = DNA_REPLY if "sociolinguist" in system else IDEAS_REPLY if "content strategist" in system else SCRIPT_REPLY
        return {"choices": [{"message": {"content": "Here you go:\n" + json.dumps(reply, ensure_ascii=False)}}]}

    def prompt_for(self, marker):
        for _, _, _, body in reversed(self.calls):
            if body and marker in body["messages"][0]["content"]:
                return body["messages"][1]["content"]
        raise AssertionError("no call for " + marker)


class CorpusTest(unittest.TestCase):
    def test_docx_text_extraction(self):
        text = corpus.extract_docx_text(make_docx(["周日去超市 &amp; 补货", "", "结果某人又走不动道哈"]))
        self.assertEqual(text, "周日去超市 & 补货\n\n结果某人又走不动道哈")
        with self.assertRaises(corpus.ImportError_):
            corpus.extract_docx_text(b"plain text, not a zip")
        empty_zip = io.BytesIO()
        zipfile.ZipFile(empty_zip, "w").close()
        with self.assertRaises(corpus.ImportError_):
            corpus.extract_docx_text(empty_zip.getvalue())

    def test_voice_stats_measures_clauses_and_particles(self):
        stats = corpus.voice_stats([HER_SCRIPT, "   ", "周末去 brunch 啦 [轻笑]"])
        self.assertEqual((stats["scripts"], stats["clauses"]), (2, 37))
        self.assertEqual(stats["clause_length_units"], {"p5": 7, "median": 7, "p95": 8, "max": 8})  # sorted: 5, 7x24, 8x12
        self.assertEqual(stats["clause_final_particles"][0], {"particle": "啦", "count": 13, "share_pct": 52})
        self.assertIn("哈", stats["particles_never_seen"])
        self.assertEqual(stats["top_english_tokens"], ["brunch"])

    def test_performance_ranking_and_exemplars(self):
        samples = [
            {"id": "a", "title": "A", "text": "甲", "is_sponsored": False, "views": 1000, "likes": 50, "collects": 30, "comments": 20, "created_at": 1},
            {"id": "b", "title": "B", "text": "乙", "is_sponsored": True, "views": None, "likes": 500, "collects": None, "comments": None, "created_at": 2},
            {"id": "c", "title": "C", "text": "丙", "is_sponsored": False, "views": None, "likes": None, "collects": None, "comments": None, "created_at": 3},
            {"id": "d", "title": "D", "text": "丁", "is_sponsored": False, "views": 0, "likes": 0, "collects": 0, "comments": 0, "created_at": 4},
        ]
        perf = corpus.performance(samples)
        self.assertEqual([r["id"] for r in perf["rows"]], ["b", "a", "d"])      # c has no numbers at all
        self.assertEqual((perf["with_metrics"], perf["total_videos"], perf["median_interactions"]), (3, 4, 100))
        rates = {r["id"]: r["interaction_rate_pct"] for r in perf["rows"]}
        self.assertEqual(rates, {"a": 10.0, "b": None, "d": None})               # no views or zero views: no rate
        self.assertEqual([e["title"] for e in corpus.exemplars(samples)], ["A", "D", "C"])  # sponsored excluded


class LocalProviderTest(unittest.TestCase):
    def test_local_address_must_be_loopback(self):
        self.assertEqual(llm.normalize_local_base(""), llm.DEFAULT_LOCAL_BASE)
        self.assertEqual(llm.normalize_local_base("http://localhost:1234/v1/"), "http://localhost:1234/v1")
        for bad in ("http://example.com/v1", "http://192.168.1.5:11434/v1", "file:///etc/passwd", "http://127.0.0.1.evil.com/v1"):
            with self.assertRaises(llm.LLMError):
                llm.normalize_local_base(bad)

    def test_json_is_recovered_from_surrounding_prose(self):
        self.assertEqual(llm.parse_json_text('Sure!\n{"a": {"b": 1}}\nHope that helps.'), {"a": {"b": 1}})
        with self.assertRaises(llm.LLMError):
            llm.parse_json_text("no braces here")

    def test_real_http_round_trip_against_loopback_server(self):
        seen = []

        class Compat(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                seen.append(("GET", self.path, self.headers.get("Authorization")))
                self._reply(200, {"object": "list", "data": [{"id": "qwen-test"}, {"id": "embed-test"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(("POST", self.path, body))
                if body["model"] == "missing":
                    return self._reply(404, {"error": {"message": "model 'missing' not found"}})
                self._reply(200, {"choices": [{"message": {"content": '```json\n{"ok": "好"}\n```'}}]})

        httpd = HTTPServer(("127.0.0.1", 0), Compat)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d/v1" % httpd.server_address[1]
        try:
            self.assertEqual(llm.list_models("local", base_url=base), ["embed-test", "qwen-test"])
            self.assertEqual(llm.complete_json("local", "", "qwen-test", "sys", "用户", base_url=base), {"ok": "好"})
            with self.assertRaises(llm.LLMError) as ctx:
                llm.complete_json("local", "", "missing", "s", "u", base_url=base)
            self.assertIn("404", str(ctx.exception))
            self.assertIn("not found", str(ctx.exception))
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(seen[0], ("GET", "/v1/models", None))                   # no auth header sent locally
        self.assertEqual(seen[1][1], "/v1/chat/completions")
        self.assertEqual(seen[1][2]["messages"][1], {"role": "user", "content": "用户"})
        with self.assertRaises(llm.LLMError):                                     # nothing listening
            llm.list_models("local", base_url="http://127.0.0.1:9/v1")


class FeatureServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.fake = FakeLocal()
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

    def connect_local(self):
        status, out = self.call("POST", "/api/settings/models", {"provider": "local", "base_url": "http://localhost:11434/v1"})
        self.assertEqual((status, out["models"]), (200, ["local-model-a", "local-model-b"]))
        status, out = self.call("POST", "/api/settings", {"provider": "local", "model": "local-model-a"})
        self.assertTrue(out["settings"]["configured"])
        self.assertEqual(out["settings"]["local_base_url"], "http://localhost:11434/v1")

    def test_local_settings_reject_remote_address(self):
        status, out = self.call("POST", "/api/settings/models", {"provider": "local", "base_url": "http://example.com/v1"})
        self.assertEqual((status, out["error"]), (502, "ai_failed"))
        self.assertFalse(self.store.public_settings()["keys"]["local"])
        self.assertEqual(self.fake.calls, [])                                     # never contacted
        self.connect_local()
        self.assertEqual(self.fake.calls[0][1], "http://localhost:11434/v1/models")
        self.assertEqual(self.fake.calls[0][2], {})

    def test_library_import_edit_delete(self):
        self.assertEqual(self.call("POST", "/api/samples", {"text": "   "})[1]["error"], "empty_text")
        self.assertEqual(self.call("POST", "/api/samples", {"docx_base64": "!!!"})[1]["error"], "bad_file")
        self.assertEqual(self.call("POST", "/api/samples", {"docx_base64": base64.b64encode(b"not a zip").decode()})[1]["error"], "bad_file")

        status, pasted = self.call("POST", "/api/samples", {"text": "\n周日去超市补货的那天\r\n第二行"})
        self.assertEqual((status, pasted["title"], pasted["text"]), (201, "周日去超市补货的那天", "周日去超市补货的那天\n第二行"))
        self.assertEqual([pasted[k] for k in ("views", "likes", "collects", "comments")], [None] * 4)

        docx = base64.b64encode(make_docx(["第一段啦", "第二段嘛"])).decode()
        _, from_docx = self.call("POST", "/api/samples", {"title": "面霜那期", "docx_base64": docx, "likes": "120", "views": 3000})
        self.assertEqual((from_docx["title"], from_docx["text"], from_docx["likes"], from_docx["views"]), ("面霜那期", "第一段啦\n第二段嘛", 120, 3000))

        sid = from_docx["id"]
        _, edited = self.call("PUT", "/api/samples/" + sid, {"collects": "45", "comments": "", "views": "-5", "is_sponsored": True, "title": " 面霜 "})
        self.assertEqual((edited["collects"], edited["comments"], edited["views"], edited["is_sponsored"], edited["title"]), (45, None, None, True, "面霜"))

        _, listing = self.call("GET", "/api/samples")
        self.assertEqual([s["title"] for s in listing["samples"]], ["面霜", "周日去超市补货的那天"])
        self.assertEqual(listing["stats"]["scripts"], 1)                          # sponsored one is excluded from voice stats
        self.assertEqual(self.call("GET", "/api/state")[1]["sample_count"], 2)
        self.assertEqual(self.call("DELETE", "/api/samples/" + sid)[0], 200)
        self.assertEqual(self.call("PUT", "/api/samples/" + sid, {"title": "x"})[0], 404)

    def test_learn_voice_from_her_scripts(self):
        self.assertEqual(self.call("POST", "/api/voice/learn", {})[1]["error"], "ai_not_configured")
        self.connect_local()
        self.assertEqual(self.call("POST", "/api/voice/learn", {})[1]["error"], "no_samples")
        self.assertEqual(self.call("PUT", "/api/voice/rate", {"cpm": 260})[1]["error"], "no_profile")
        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        self.call("POST", "/api/samples", {"title": "商单面霜", "text": "这款面霜搭载三重神经酰胺", "is_sponsored": True})

        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200)
        cadence = out["dna"]["cadence_and_syntax"]
        self.assertEqual(cadence["ideal_clause_length_chars"], {"min": 7, "median": 7, "hard_max": 8})   # measured, not the model's 6/11/20
        self.assertEqual(cadence["target_speaking_rate_cpm"], 240)                                        # default, not the model's 235
        summary = out["dna"]["source_corpus_summary"]
        self.assertEqual((summary["organic_scripts_analyzed"], summary["sponsored_scripts_analyzed"]), (1, 1))
        self.assertTrue(summary["clause_lengths_measured"])
        self.assertFalse(summary["speaking_rate_confirmed"])
        self.assertFalse(self.call("GET", "/api/dna")[1]["is_example"])

        prompt = self.fake.prompt_for("sociolinguist")
        organic, sponsored = prompt.split("<past_sponsored_bridges_corpus>")
        self.assertIn("排队排了半小时啦", organic)
        self.assertNotIn("神经酰胺", organic)                                     # sponsored copy kept out of the organic corpus
        self.assertIn("神经酰胺", sponsored)
        self.assertIn('"clauses": 36', prompt)
        self.assertNotIn("{{", prompt)

        # the editor now checks against her measured limits
        _, lint = self.call("POST", "/api/lint", {"lines": [{"spoken_script_zh": "今天去超市买了好多菜"}], "target_sec": 60})
        self.assertFalse(lint["dna_is_example"])
        self.assertEqual(lint["stats"]["hard_max"], 8)
        self.assertIn("CADENCE_TOO_LONG", [f["category"] for f in lint["findings"]])

        self.assertEqual(self.call("PUT", "/api/voice/rate", {"cpm": 90})[1]["error"], "bad_rate")
        _, rated = self.call("PUT", "/api/voice/rate", {"cpm": 265})
        self.assertEqual(rated["dna"]["cadence_and_syntax"]["target_speaking_rate_cpm"], 265)
        _, again = self.call("POST", "/api/voice/learn", {})
        self.assertEqual(again["dna"]["cadence_and_syntax"]["target_speaking_rate_cpm"], 265)             # her confirmed rate survives re-learning
        self.assertTrue(again["dna"]["source_corpus_summary"]["speaking_rate_confirmed"])
        self.assertEqual(len(list(self.store.dna_history_dir.glob("*.json"))), 2)                         # earlier profiles are kept

    def test_unusable_profile_reply_does_not_replace_profile(self):
        self.connect_local()
        self.call("POST", "/api/samples", {"text": HER_SCRIPT})
        self.httpd.app.transport = lambda *a: {"choices": [{"message": {"content": '{"persona_summary": {}}'}}]}
        status, out = self.call("POST", "/api/voice/learn", {})
        self.assertEqual((status, out["error"]), (502, "ai_failed"))
        self.assertTrue(self.call("GET", "/api/dna")[1]["is_example"])

    def test_drafts_are_anchored_on_her_scripts(self):
        self.connect_local()
        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT, "likes": 10})
        self.call("POST", "/api/samples", {"title": "商单面霜", "text": "这款面霜搭载三重神经酰胺", "is_sponsored": True, "likes": 999})
        status, script = self.call("POST", "/api/generate", {"notes": "再去一次超市", "target_sec": 30})
        self.assertEqual((status, script["lines"][0]["spoken_script_zh"]), (201, "今天去超市买菜"))
        prompt = self.fake.prompt_for("ghostwriting")
        exemplars = prompt.split("<retrieved_real_exemplars_from_her_past_videos>")[1].split("</retrieved_real_exemplars_from_her_past_videos>")[0]
        self.assertIn("排队排了半小时啦", exemplars)
        self.assertNotIn("神经酰胺", exemplars)

    def test_recommendations_use_her_numbers_and_drop_invented_evidence(self):
        self.connect_local()
        self.assertEqual(self.call("POST", "/api/recommendations", {})[1]["error"], "no_samples")
        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT, "views": 2000, "likes": 150, "collects": 40, "comments": 10})
        self.call("POST", "/api/samples", {"title": "早餐日常", "text": "早上煮咖啡啦", "views": 1000, "likes": 20})
        self.call("POST", "/api/samples", {"title": "没填数据", "text": "随便写写"})

        _, before = self.call("GET", "/api/recommendations")
        self.assertIsNone(before["result"])
        self.assertEqual([(r["title"], r["interactions"], r["interaction_rate_pct"]) for r in before["performance"]["rows"]],
                         [("超市那天", 200, 10.0), ("早餐日常", 20, 2.0)])

        status, out = self.call("POST", "/api/recommendations", {})
        self.assertEqual(status, 200)
        ideas = out["result"]["ideas"]
        self.assertEqual([i["title_zh"] for i in ideas], ["超市补货第二弹", "早餐"])                      # blank-title idea dropped
        self.assertEqual((ideas[0]["evidence_titles"], ideas[0]["unverified_evidence"]), (["超市那天"], 1))
        self.assertEqual((ideas[0]["video_format"], ideas[0]["target_duration_sec"]), ("COUPLE_VLOG", 90))
        self.assertEqual((ideas[1]["video_format"], ideas[1]["target_duration_sec"], ideas[1]["evidence_titles"]), ("VLOG_VOICEOVER", 60, []))
        self.assertEqual((out["result"]["videos_with_metrics"], out["result"]["total_videos"]), (2, 3))

        prompt = self.fake.prompt_for("content strategist")
        self.assertIn('"median_interactions": 20', prompt)
        self.assertIn('"interaction_rate_pct": 10.0', prompt)
        self.assertIn("没填数据", prompt)
        self.assertIn("Recommend exactly 6", prompt)
        self.assertNotIn("{{", prompt)
        self.assertEqual(self.call("GET", "/api/recommendations")[1]["result"]["ideas"], ideas)           # saved for next visit


if __name__ == "__main__":
    unittest.main()
