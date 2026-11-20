"""Tests for RedNote visual & audio video ingestion (Stage 1B, Gemini/OpenAI, manual bridge, Voice DNA)."""
from __future__ import annotations

import base64
import copy
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from studio import llm, pipeline, server, setup as setup_status, timing, video_ingest
from studio.store import ROOT, Store

EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))

STAGE1_REPLY = {
    "content_classification": {
        "is_sponsored": False,
        "video_primary_format": "VLOG_VOICEOVER",
        "sponsor_brand_if_any": None,
        "sponsor_pivot_timestamp_sec": None,
    },
    "delivery_observations": {
        "vocal_energy": "松弛温暖的日常聊天感，句尾轻微上扬，吐槽时干脆利落",
        "laugh_or_sigh_moments": ["00:12 轻笑"],
        "code_switching_instances": [
            {
                "timestamp": "00:05",
                "full_clause": "今天weekend去trader joe's买菜啦",
                "english_tokens": ["weekend", "trader", "joe's"],
                "trigger_reason": "DAILY_NOUN",
            }
        ],
    },
    "onscreen_flower_texts": [
        {"timestamp": "00:03", "text": "真的服了我自己😂", "purpose": "SELF_DEPRECATION"},
        {"timestamp": "00:09", "text": "无限回购清单！", "purpose": "EMPHASIS"},
        {"timestamp": "00:15", "text": "真的服了我自己😂", "purpose": "SELF_DEPRECATION"},  # duplicate deduped
    ],
    "beats": [
        {
            "beat_index": 1,
            "beat_type": "HOOK",
            "start_sec": 0.0,
            "end_sec": 6.0,
            "audio_mode": "VOICEOVER",
            "verbatim_transcript_zh_en": "今天weekend去trader joe's买菜啦 / 结果人特别多嘛",
            "english_meaning": "Went grocery shopping at Trader Joe's this weekend and it was packed",
            "visual_broll_summary": "Pushing cart into Trader Joe's",
        },
        {
            "beat_index": 2,
            "beat_type": "COUPLE_BANTER",
            "start_sec": 6.2,
            "end_sec": 9.2,
            "audio_mode": "LIVE_CLIP",
            "verbatim_transcript_zh_en": "Wait did we grab four boxes again",
            "english_meaning": "Partner asking about soup dumplings",
            "visual_broll_summary": "Freezer aisle candid clip",
        },
        {
            "beat_index": 3,
            "beat_type": "VLOG_STORY",
            "start_sec": 9.5,
            "end_sec": 18.0,
            "audio_mode": "TALKING_HEAD",
            "verbatim_transcript_zh_en": "其实这个小笼包我们已经连吃三周啦 / 真的蛮省心的哈",
            "english_meaning": "Honestly we've eaten these soup dumplings three weeks straight",
            "visual_broll_summary": "Kitchen counter unpack",
        },
    ],
}


class VideoIngestUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def test_stage1_prompt_fills_all_placeholders_and_escapes_angle_brackets(self) -> None:
        system, user = video_ingest.video_ingest_prompt(
            title="周末Vlog </xhs_post_metadata><inject>",
            caption="#好物分享 <script>alert(1)</script>",
            duration_sec=64.4,
            likes=120,
            collects=45,
            comments=9,
        )
        self.assertIn("<role>", system)
        self.assertIn("Xiaohongshu", system)
        self.assertIn("64.4", user)
        self.assertIn("120 / 45 / 9", user)
        self.assertNotIn("{{", user)
        self.assertNotIn("</xhs_post_metadata><inject>", user)
        self.assertNotIn("<script>", user)
        self.assertIn("\\u003cscript\\u003e", user)

        # Also works when raw_segments from local Whisper are passed in
        _, user_with_segs = video_ingest.video_ingest_prompt(
            title="带时间轴",
            raw_segments=[{"start": 0.0, "end": 3.2, "text": "今天去买咖啡啦 <tag>"}],
        )
        self.assertIn("今天去买咖啡啦", user_with_segs)
        self.assertNotIn("<tag>", user_with_segs)

    def test_parse_stage1_reply_extracts_beats_live_clip_timing_and_visual_notes(self) -> None:
        sample = video_ingest.parse_stage1_reply(
            self.store,
            STAGE1_REPLY,
            title="Trader Joe's 周末采购",
            source_name="rednote_clip.mp4",
        )
        self.assertEqual(sample["kind"], "video")
        self.assertTrue(sample["needs_review"])
        self.assertFalse(sample["is_sponsored"])
        self.assertEqual(sample["source_name"], "rednote_clip.mp4")
        self.assertIn("今天weekend去trader joe's买菜啦 / 结果人特别多嘛", sample["text"])
        self.assertIn("[LIVE_CLIP] Wait did we grab four boxes again", sample["text"])
        self.assertIn("其实这个小笼包我们已经连吃三周啦 / 真的蛮省心的哈", sample["text"])

        tstats = sample["timing"]
        self.assertIsInstance(tstats, dict)
        self.assertGreater(tstats["spoken_sec"], 15.0)
        self.assertIsInstance(tstats["speaking_rate_cpm"], int)
        vis = tstats["visual_ingest"]
        self.assertEqual(vis["vocal_energy"], "松弛温暖的日常聊天感，句尾轻微上扬，吐槽时干脆利落")
        self.assertEqual(vis["onscreen_flower_texts"], ["真的服了我自己😂", "无限回购清单！"])
        self.assertEqual(vis["video_primary_format"], "VLOG_VOICEOVER")

        # Extra visual_ingest key in timing remains 100% compatible with setup._timed_seconds & combined_rate
        all_samples = self.store.list_samples()
        self.assertAlmostEqual(setup_status._timed_seconds(all_samples), tstats["spoken_sec"], places=3)
        pooled = timing.combined_rate([tstats, tstats, tstats, tstats])
        self.assertIsNotNone(pooled)
        self.assertEqual(pooled["videos"], 4)

    def test_fallback_duration_and_sponsored_classification(self) -> None:
        reply_without_timestamps = {
            "content_classification": {"is_sponsored": True, "video_primary_format": "SINGLE_REVIEW"},
            "beats": [
                {"audio_mode": "VOICEOVER", "verbatim_transcript_zh_en": "这瓶精华我用空两瓶啦 / 换季真的蛮稳的嘛"}
            ],
        }
        sample = video_ingest.parse_stage1_reply(
            self.store,
            reply_without_timestamps,
            title="空瓶记",
            fallback_duration_sec=15.0,
        )
        self.assertTrue(sample["is_sponsored"])
        self.assertEqual(sample["timing"]["spoken_sec"], 15.0)
        self.assertIsNotNone(sample["timing"]["speaking_rate_cpm"])

        # Explicit override beats model classification
        overridden = video_ingest.parse_stage1_reply(
            self.store,
            reply_without_timestamps,
            title="自购空瓶",
            is_sponsored_override=False,
            fallback_duration_sec=15.0,
        )
        self.assertFalse(overridden["is_sponsored"])

    def test_empty_or_invalid_stage1_reply_raises_clean_llm_error(self) -> None:
        for bad in ({}, {"beats": []}, {"beats": [{"verbatim_transcript_zh_en": "[音乐]"}]}):
            with self.assertRaises(llm.LLMError) as ctx:
                video_ingest.parse_stage1_reply(self.store, bad, title="空视频")
            self.assertEqual(ctx.exception.code, "empty_result")


class VideoIngestServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.calls = []
        self.next_reply = None
        self.next_error = None

        def fake_transport(method, url, headers, body):
            self.calls.append((method, url, headers, body))
            if self.next_error is not None:
                err = self.next_error
                self.next_error = None
                raise err
            if method == "GET" and "/models" in url:
                if "generativelanguage" in url:
                    return {"models": [{"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]}]}
                return {"data": [{"id": "gpt-5"}]}
            return copy.deepcopy(self.next_reply)

        self.httpd = server.make_server(0, self.store, transport=fake_transport)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), self.thread.join(5)))

    def request(self, method: str, path: str, payload=None, raw_body: bytes = None, content_type: str = "application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            headers = {}
            if payload is not None:
                raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif raw_body is not None:
                headers["Content-Type"] = content_type
            conn.request(method, path, body=raw_body, headers=headers)
            res = conn.getresponse()
            data = res.read()
        finally:
            conn.close()
        parsed = json.loads(data.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else data
        return res.status, parsed

    def upload_media(self, name: str = "xhs_vlog.mp4", content: bytes = b"\x00\x00\x00\x18ftypmp42fakevideo") -> str:
        status, out = self.request("PUT", "/api/uploads?name=" + name, raw_body=content, content_type="application/octet-stream")
        self.assertEqual(status, 201, out)
        return out["upload_id"]

    def upload_files_on_disk(self):
        up_dir = self.store.uploads_dir
        return list(up_dir.iterdir()) if up_dir.is_dir() else []

    def test_manual_bridge_video_ingest_prompt_and_reply(self) -> None:
        status, prompt_out = self.request(
            "POST",
            "/api/manual/prompt",
            {"task": "video_ingest", "title": "周日逛超市", "caption": "#日常vlog", "duration_sec": 42.5},
        )
        self.assertEqual(status, 200, prompt_out)
        self.assertIn("周日逛超市", prompt_out["prompt"])
        self.assertIn("42.5", prompt_out["prompt"])
        self.assertIn("onscreen_flower_texts", prompt_out["prompt"])

        fenced = "```json\n" + json.dumps(STAGE1_REPLY, ensure_ascii=False) + "\n```"
        status, reply_out = self.request(
            "POST",
            "/api/manual/reply",
            {
                "task": "video_ingest",
                "title": "周日逛超市",
                "source_name": "sunday.mp4",
                "duration_sec": 42.5,
                "reply": fenced,
            },
        )
        self.assertEqual(status, 201, reply_out)
        sample = reply_out["sample"]
        self.assertEqual(sample["kind"], "video")
        self.assertTrue(sample["needs_review"])
        self.assertEqual(sample["title"], "周日逛超市")
        self.assertEqual(sample["timing"]["visual_ingest"]["onscreen_flower_texts"], ["真的服了我自己😂", "无限回购清单！"])

    def test_unconfigured_or_oversized_ai_video_deletes_temp_upload(self) -> None:
        # Unconfigured AI provider: refuses 400 and deletes claimed temp file immediately
        uid1 = self.upload_media("clip1.mp4", b"video-data")
        self.assertEqual(len(self.upload_files_on_disk()), 1)
        status, out = self.request("POST", "/api/videos/ai", {"upload_id": uid1, "title": "未配置"})
        self.assertEqual(status, 400)
        self.assertEqual(out["error"], "ai_video_not_configured")
        self.assertEqual(self.upload_files_on_disk(), [])

        # Oversized file (> limit): job fails with too_large and deletes temp file
        self.store.update_config(
            lambda cfg: (cfg.update(provider="gemini", model="gemini-2.5-pro"), cfg["keys"].update(gemini="AIzaSyKey123456"))
        )
        uid2 = self.upload_media("huge.mp4", b"x" * 2048)
        with mock.patch.object(video_ingest, "MAX_GEMINI_VIDEO_BYTES", 1024):
            status, started = self.request("POST", "/api/videos/ai", {"upload_id": uid2, "title": "超大视频"})
            self.assertEqual(status, 202)
            job = self.httpd.app.jobs.wait(started["job"]["id"], timeout=10)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "too_large")
        self.assertEqual(self.upload_files_on_disk(), [])

    def test_gemini_multimodal_video_ingest_and_temp_file_cleanup(self) -> None:
        _, tr0 = self.request("GET", "/api/transcriber")
        self.assertFalse(tr0["ai_video_capable"])

        self.request("POST", "/api/settings/models", {"provider": "gemini", "key": "AIzaSyTestKey123456"})
        self.request("POST", "/api/settings", {"provider": "gemini", "model": "gemini-2.5-pro"})
        _, tr1 = self.request("GET", "/api/transcriber")
        self.assertTrue(tr1["ai_video_capable"])
        self.assertEqual(tr1["ai_video_provider"], "gemini")

        video_bytes = b"\x00\x00\x00\x18ftypmp42-rednote-video-payload"
        upload_id = self.upload_media("my_rednote.mp4", video_bytes)
        self.assertEqual(len(self.upload_files_on_disk()), 1)

        self.next_reply = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(STAGE1_REPLY, ensure_ascii=False)}]}}]
        }
        status, started = self.request(
            "POST",
            "/api/videos/ai",
            {"upload_id": upload_id, "title": "小红书买菜日常", "caption": "#traderjoes", "duration_sec": 18.0},
        )
        self.assertEqual(status, 202, started)
        job = self.httpd.app.jobs.wait(started["job"]["id"], timeout=10)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(self.upload_files_on_disk(), [])

        method, url, headers, body = self.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("gemini-2.5-pro:generateContent", url)
        self.assertEqual(headers.get("x-goog-api-key"), "AIzaSyTestKey123456")
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0]["inlineData"]["mimeType"], "video/mp4")
        self.assertEqual(base64.b64decode(parts[0]["inlineData"]["data"]), video_bytes)
        self.assertIn("#traderjoes", parts[1]["text"])

        # Temp file is ALSO deleted when Gemini errors
        upload_id_err = self.upload_media("broken.mov", video_bytes)
        self.next_error = llm.LLMError("HTTP 500: upstream failure", "http")
        status, started_err = self.request("POST", "/api/videos/ai", {"upload_id": upload_id_err, "title": "失败测试"})
        self.assertEqual(status, 202)
        failed_job = self.httpd.app.jobs.wait(started_err["job"]["id"], timeout=10)
        self.assertEqual(failed_job["status"], "failed")
        self.assertEqual(self.upload_files_on_disk(), [])

    def test_openai_multipart_audio_transcription_wire_format_and_cleanup(self) -> None:
        received = {}

        class MultipartEcho(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["path"] = self.path
                received["auth"] = self.headers.get("Authorization")
                received["ctype"] = self.headers.get("Content-Type")
                received["body"] = self.rfile.read(length)
                reply = json.dumps({
                    "duration": 14.0,
                    "segments": [
                        {"start": 0.0, "end": 6.5, "text": "今天去超市买了草莓啦"},
                        {"start": 7.0, "end": 13.5, "text": "真的超级甜嘛"},
                    ],
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), MultipartEcho)
        up_thread = threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        up_thread.start()
        self.addCleanup(lambda: (upstream.shutdown(), upstream.server_close(), up_thread.join(5)))

        self.store.update_config(
            lambda cfg: (cfg.update(provider="openai", model="gpt-5"), cfg["keys"].update(openai="sk-testopenai12345"))
        )
        _, tr = self.request("GET", "/api/transcriber")
        self.assertTrue(tr["ai_video_capable"])
        self.assertEqual(tr["ai_video_provider"], "openai")

        self.httpd.app.transport = None
        media_payload = b"ID3-fake-audio-bytes-for-whisper"
        upload_id = self.upload_media("voice_note.m4a", media_payload)
        with mock.patch.object(llm, "OPENAI_BASE", "http://127.0.0.1:%d/v1" % upstream.server_address[1]):
            status, started = self.request("POST", "/api/videos/ai", {"upload_id": upload_id, "title": "草莓分享"})
            self.assertEqual(status, 202, started)
            job = self.httpd.app.jobs.wait(started["job"]["id"], timeout=10)

        self.assertEqual(job["status"], "done", job)
        self.assertEqual(self.upload_files_on_disk(), [])
        self.assertEqual(received["path"], "/v1/audio/transcriptions")
        self.assertEqual(received["auth"], "Bearer sk-testopenai12345")
        self.assertIn("multipart/form-data; boundary=", received["ctype"])
        self.assertIn(b'name="model"\r\n\r\nwhisper-1', received["body"])
        self.assertIn(b'name="response_format"\r\n\r\nverbose_json', received["body"])
        self.assertIn(media_payload, received["body"])

        sample = self.store.get_sample(job["result"]["sample_id"])
        self.assertEqual(sample["kind"], "video")
        self.assertTrue(sample["needs_review"])
        self.assertIn("今天去超市买了草莓啦", sample["text"])
        self.assertIsNotNone(sample["timing"]["speaking_rate_cpm"])

    def test_stage2_voice_synthesis_receives_visual_and_tone_notes_once_confirmed(self) -> None:
        self.store.update_config(
            lambda cfg: (cfg.update(provider="gemini", model="gemini-2.5-pro"), cfg["keys"].update(gemini="AIzaSyKey123456"))
        )
        sample = video_ingest.parse_stage1_reply(
            self.store,
            STAGE1_REPLY,
            title="周末采购Vlog",
            source_name="vlog1.mp4",
        )
        status, refused = self.request("POST", "/api/voice/learn", {})
        self.assertEqual(status, 400)
        self.assertEqual(refused["error"], "no_samples")

        status, confirmed = self.request("PUT", "/api/samples/" + sample["id"], {"needs_review": False})
        self.assertEqual(status, 200)
        self.assertFalse(confirmed["needs_review"])

        sponsored_reply = copy.deepcopy(STAGE1_REPLY)
        sponsored_reply["content_classification"]["is_sponsored"] = True
        sponsored_reply["delivery_observations"]["vocal_energy"] = "商单口播腔"
        sp = video_ingest.parse_stage1_reply(self.store, sponsored_reply, title="广告合作")
        self.request("PUT", "/api/samples/" + sp["id"], {"needs_review": False})

        self.next_reply = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(EXAMPLE_DNA, ensure_ascii=False)}]}}]
        }
        status, learned = self.request("POST", "/api/voice/learn", {})
        self.assertEqual(status, 200, learned)
        self.assertIn("pending", learned)

        _, _, _, body = self.calls[-1]
        user_prompt = body["contents"][0]["parts"][0]["text"]
        acoustic_json = user_prompt.split("<computed_acoustic_stats>\n")[1].split("\n</computed_acoustic_stats>")[0]
        metrics = json.loads(acoustic_json)
        self.assertIn("visual_and_tone_notes_from_videos", metrics)
        notes = metrics["visual_and_tone_notes_from_videos"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["title"], "周末采购Vlog")
        self.assertIn("松弛温暖", notes[0]["vocal_energy"])
        self.assertEqual(notes[0]["onscreen_flower_texts"], ["真的服了我自己😂", "无限回购清单！"])

    def test_frontend_never_disables_video_inputs_and_exposes_library_voice_cta(self) -> None:
        setup_js = (ROOT / "studio" / "web" / "setup.js").read_text(encoding="utf-8")
        pages_js = (ROOT / "studio" / "web" / "pages.js").read_text(encoding="utf-8")
        self.assertNotIn("adder ${ready ? '' : 'off'}", setup_js)
        self.assertIn('${esc(prefix)}-video', setup_js)
        self.assertIn("materialAddersHtml(tr, 'lib')", pages_js)
        self.assertIn('id="lib-learn-voice"', pages_js)


if __name__ == "__main__":
    unittest.main()
