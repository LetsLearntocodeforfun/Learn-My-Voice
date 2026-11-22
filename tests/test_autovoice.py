"""Tests for Autopilot continuous voice learning, watched folder ingestion, and recurring lessons."""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import autopilot, autovoice, creator, llm, pipeline, server, transcribe, video_ingest
from studio.store import MODE_ORGANIC, MODE_SPONSORED, ROOT, Store

EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
CHINESE_RE = r"[\u4e00-\u9fff]"

TRUSTED_SEGMENTS = [
    {"start": 0.0, "end": 6.0, "text": "上周末跟我老公去 Costco 补货嘛 / 结果门口排队的人特别多哈"},
    {"start": 6.4, "end": 13.0, "text": "其实这个燕麦奶我已经连喝三周啦 / 早上配冰美式真的蛮顺口的诶"},
    {"start": 13.5, "end": 20.5, "text": "不过说实话它稍微有一点点甜 / 如果你平时只喝无糖可能会觉得偏浓一点啦"},
]

SHORT_SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "今天去买咖啡啦"},
]

LOW_CJK_SEGMENTS = [
    {"start": 0.0, "end": 8.0, "text": "Weekend grocery haul at Trader Joes with morning coffee routine啦"},
    {"start": 8.5, "end": 16.0, "text": "Honestly everything inside this shopping bag feels super cozy today嘛"},
]

TOO_FAST_SEGMENTS = [
    {"start": 0.0, "end": 5.5, "text": "今天去超市买了一大堆好吃的水果和酸奶回来放进冰箱里慢慢吃啦而且顺便还买了一束特别新鲜的向日葵摆在客厅桌子上哈"},
    {"start": 5.6, "end": 11.0, "text": "结果某人一进门就开始翻零食柜找巧克力饼干吃真的让我哭笑不得嘛不过周末在家窝着看电影确实蛮舒服的诶"},
]

PLACEHOLDER_SEGMENTS = [
    {"start": 0.0, "end": 12.0, "text": "哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈"},
]


def _loopback_guard():
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    blocked = []

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            blocked.append(address)
            raise OSError("external network blocked in test: %r" % (address,))
        return real_connect(sock, address)

    def guarded_lookup(host, *args, **kwargs):
        if host not in ("127.0.0.1", "::1", "localhost", None):
            blocked.append(host)
            raise socket.gaierror("external DNS lookup blocked in test: %r" % (host,))
        return real_getaddrinfo(host, *args, **kwargs)

    return mock.patch.object(socket.socket, "connect", guarded_connect), mock.patch.object(socket, "getaddrinfo", guarded_lookup), blocked


class StandInTranscriber:
    def __init__(self, *, installed: bool = True, supported: bool = True, segments=None, fail_exc=None) -> None:
        self.installed = installed
        self.supported = supported
        self.segments = copy.deepcopy(segments if segments is not None else TRUSTED_SEGMENTS)
        self.fail_exc = fail_exc
        self.calls = []

    def status(self):
        return {
            "supported": self.supported,
            "reason": None if self.supported else "not_macos",
            "reason_en": None if self.supported else "Transcribing only works on Apple Silicon Mac.",
            "reason_zh": None if self.supported else "本机转写仅支持 Apple 芯片 Mac。",
            "installed": self.installed,
            "model_ready": self.installed,
            "model": "stand-in-whisper",
        }

    def transcribe_file(self, path: str, progress=None):
        p = Path(path)
        self.calls.append({"path": str(p), "exists": p.is_file(), "size": p.stat().st_size if p.is_file() else 0})
        if self.fail_exc is not None:
            raise self.fail_exc
        return {
            "segments": copy.deepcopy(self.segments),
            "language": "zh",
            "duration_sec": 21.0,
            "model": "stand-in-whisper",
            "unclear_dropped": 0,
            "sound": None,
        }


def _stage2_voice_reply(*, banned=None, uses=None, essence_zh="松弛自然的日常分享感，像给闺蜜发语音一样真实克制"):
    profile = copy.deepcopy(EXAMPLE_DNA)
    profile["persona_summary"]["one_line_essence_zh"] = essence_zh
    if uses is not None:
        profile["persona_summary"]["audience_address_terms"]["uses"] = list(uses)
    if banned is not None:
        profile["anti_voice_blacklist"]["banned_phrases_zh"] = list(banned)
    return profile


class AutoVoiceCase(unittest.TestCase):
    def setUp(self) -> None:
        p1, p2, self.blocked_net = _loopback_guard()
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.watch_dir = self.tmp / "creator_videos"
        self.watch_dir.mkdir()

        self.now = 1_800_000_000.0
        self.transcriber = StandInTranscriber()
        self.transport_calls = []
        self.cloud_stage1_reply = {
            "content_classification": {"is_sponsored": False, "video_primary_format": "VLOG_VOICEOVER"},
            "beats": [
                {
                    "beat_index": 1,
                    "beat_type": "HOOK",
                    "start_sec": 0.0,
                    "end_sec": 7.0,
                    "audio_mode": "VOICEOVER",
                    "verbatim_transcript_zh_en": "上周末跟我老公去 Costco 补货嘛 / 结果门口排队的人特别多哈",
                },
                {
                    "beat_index": 2,
                    "beat_type": "VLOG_STORY",
                    "start_sec": 7.4,
                    "end_sec": 15.0,
                    "audio_mode": "VOICEOVER",
                    "verbatim_transcript_zh_en": "其实这个燕麦奶我已经连喝三周啦 / 早上配冰美式真的蛮顺口的诶",
                },
                {
                    "beat_index": 3,
                    "beat_type": "REALISTIC_CAVEAT",
                    "start_sec": 15.4,
                    "end_sec": 22.0,
                    "audio_mode": "VOICEOVER",
                    "verbatim_transcript_zh_en": "不过说实话它稍微有一点点甜 / 平时喝无糖的朋友可能会觉得偏浓啦",
                },
            ],
        }
        self.voice_reply = _stage2_voice_reply()

        def stand_in_transport(method, url, headers, body):
            self.transport_calls.append((method, url, headers, body))
            payload_str = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else ""
            if "inlineData" in payload_str:
                reply_obj = self.cloud_stage1_reply
            else:
                reply_obj = self.voice_reply
            text = json.dumps(reply_obj, ensure_ascii=False)
            if "generativelanguage" in url:
                return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
            return {"choices": [{"message": {"content": text}}]}

        self.transport = stand_in_transport
        fh_patch = mock.patch.dict(autovoice._FILE_HANDLERS, {}, clear=True)
        fh_patch.start()
        self.addCleanup(fh_patch.stop)
        self.registry = autopilot.Registry()
        autovoice.register_into(self.registry)
        self.pilot = autopilot.Autopilot(
            self.store,
            registry=self.registry,
            transport_of=lambda: self.transport,
            transcriber_of=lambda: self.transcriber,
            clock=lambda: self.now,
        )
        self.pilot.on = True

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    def configure_ai(self, provider: str = "gemini", model: str = "gemini-2.5-pro") -> None:
        if provider == "manual":
            self.store.update_config(lambda cfg: cfg.update(provider="manual", model=""))
        else:
            self.store.update_config(
                lambda cfg: (cfg.update(provider=provider, model=model), cfg["keys"].update({provider: "test-key-12345"}))
            )

    def enable_tasks(self, *names: str, level: str = "suggest", daily_calls: int = 12) -> None:
        self.pilot.save_settings({
            "enabled": True,
            "level": level,
            "daily_model_calls": daily_calls,
            "tasks": {name: True for name in names},
        })

    def write_media(self, rel_path: str, data: bytes = b"\x00\x00\x00\x18ftypmp42-creator-video") -> Path:
        target = self.watch_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target


class VoiceSettingsAndRoutesTest(AutoVoiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.httpd = server.make_server(0, self.store, transport=self.transport, transcriber=self.transcriber)
        self.httpd.app.autopilot.clock = lambda: self.now
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), self.thread.join(5)))

    def request(self, method: str, path: str, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            body = None
            headers = {}
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, json.loads(raw.decode("utf-8"))

    def test_get_and_post_voice_contract_and_path_refusals(self) -> None:
        status, initial = self.request("GET", "/api/autopilot/voice")
        self.assertEqual(status, 200)
        self.assertEqual(initial, {
            "watch_dir": "",
            "transcribe_route": "off",
            "folder_exists": False,
            "sponsored_subfolder": "sponsored",
            "counts": {"ready": 0, "ingested": 0, "waiting": 0, "failed": 0},
            "recent_files": [],
        })

        # Valid save via POST /api/autopilot/voice
        status, saved = self.request("POST", "/api/autopilot/voice", {
            "watch_dir": str(self.watch_dir),
            "transcribe_route": "local",
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["watch_dir"], os.path.normpath(str(self.watch_dir)))
        self.assertEqual(saved["transcribe_route"], "local")
        self.assertTrue(saved["folder_exists"])
        self.assertEqual(saved["sponsored_subfolder"], "sponsored")

        # Refuses relative, missing, file, symlink, ROOT, inside ROOT, store.data_dir, inside data_dir, bad route
        plain_file = self.tmp / "plain.mp4"
        plain_file.write_bytes(b"abc")
        inside_data = self.store.data_dir / "nested"
        inside_data.mkdir(parents=True, exist_ok=True)
        bad_cases = [
            {"watch_dir": "relative/folder"},
            {"watch_dir": str(self.tmp / "does_not_exist")},
            {"watch_dir": str(plain_file)},
            {"watch_dir": str(ROOT)},
            {"watch_dir": str(ROOT / "studio")},
            {"watch_dir": str(self.store.data_dir)},
            {"watch_dir": str(inside_data)},
            {"watch_dir": "/"},
            {"transcribe_route": "whisper_cloud"},
            {"unexpected_field": 123},
        ]
        if hasattr(os, "symlink") and os.name == "posix":
            sym = self.tmp / "sym_watch"
            os.symlink(str(self.watch_dir), str(sym))
            bad_cases.append({"watch_dir": str(sym)})

        for bad in bad_cases:
            st_code, err = self.request("POST", "/api/autopilot/voice", bad)
            self.assertEqual(st_code, 400, (bad, err))
            self.assertRegex(err.get("message_zh", ""), CHINESE_RE, (bad, err))
            self.assertTrue(err.get("message"), (bad, err))

        # Corrupt config.json is cleaned on every read without crashing or exposing forbidden dirs
        self.store.update_config(lambda cfg: cfg.update(autopilot={
            "enabled": True,
            "voice": {"watch_dir": str(self.store.data_dir), "transcribe_route": "invalid"},
        }))
        status, cleaned = self.request("GET", "/api/autopilot/voice")
        self.assertEqual(status, 200)
        self.assertEqual(cleaned["watch_dir"], "")
        self.assertEqual(cleaned["transcribe_route"], "off")
        self.assertFalse(cleaned["folder_exists"])

        # Clearing watch_dir with "" works cleanly
        status, reset = self.request("POST", "/api/autopilot/voice", {"watch_dir": "", "transcribe_route": "off"})
        self.assertEqual(status, 200)
        self.assertEqual((reset["watch_dir"], reset["folder_exists"]), ("", False))
        self.assertEqual(self.blocked_net, [])


class WatchedScannerAndIngestTest(AutoVoiceCase):
    def test_task_off_and_route_off_read_nothing(self) -> None:
        self.write_media("clip1.mp4")
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        # Task switch off -> scheduler runs nothing and reads nothing
        self.pilot.save_settings({"enabled": True, "tasks": {"voice.ingest_folder": False}})
        with mock.patch("os.scandir", side_effect=AssertionError("scandir should not run when task is off")):
            self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(self.store.list_watched_files(), [])

        # Task switch on, but transcribe_route == "off" and no external handlers -> due() is False without scanning
        autovoice.save_voice_settings(self.store, {"transcribe_route": "off"})
        self.enable_tasks("voice.ingest_folder")
        with mock.patch("os.scandir", side_effect=AssertionError("scandir should not run when route is off")):
            self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(self.store.list_samples(), [])

    def test_settle_window_changing_file_and_creator_file_untouched(self) -> None:
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        self.enable_tasks("voice.ingest_folder", level="suggest")

        raw_initial = b"\x00\x00\x00\x18ftypmp42-part-1"
        clip = self.write_media("vlog_01.mp4", raw_initial)

        # due() is strictly read-only: calling it directly leaves watched_files empty
        ctx = autopilot.Context(self.pilot, "voice.ingest_folder", "", False, None)
        self.assertTrue(autovoice._ingest_due(ctx))
        self.assertEqual(self.store.list_watched_files(), [])

        # First run at T=0 records file as waiting, ingests nothing
        runs = self.pilot.run_due()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["summary"]["seen"], 1)
        self.assertEqual(runs[0]["summary"]["ingested"], 0)
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"],
                         {"ready": 0, "ingested": 0, "waiting": 1, "failed": 0})
        self.assertEqual(self.transcriber.calls, [])

        # At T+30s (<60s), forced run still waits; after cadence (600s) with changing size, resets settle timer!
        self.advance(30)
        self.pilot.run_now("voice.ingest_folder")
        self.assertTrue(self.pilot.wait(10))
        self.assertEqual(self.store.list_samples(), [])

        # Simulate ongoing copy finishing right before T+630s with new size & fixed mtime
        self.advance(600)
        final_bytes = b"\x00\x00\x00\x18ftypmp42-complete-recording-bytes-1234567890"
        clip.write_bytes(final_bytes)
        fixed_ns = 1_760_000_123_456_789_000
        os.utime(str(clip), ns=(fixed_ns, fixed_ns))
        before_stat = os.lstat(str(clip))
        before_sha = hashlib.sha256(clip.read_bytes()).hexdigest()

        # Scan sees changed (size, mtime_ns) -> resets waiting baseline, does NOT ingest yet!
        runs = self.pilot.run_due()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["summary"]["reset"], 1)
        self.assertEqual(runs[0]["summary"]["ingested"], 0)
        self.assertEqual(self.store.list_samples(), [])

        # 40s later (<60s after change): voice_payload still counts waiting, not ready
        self.advance(40)
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"]["waiting"], 1)

        # Once >= 60s and cadence (600s) pass with identical (size_bytes, mtime_ns), file settles and is ingested once!
        self.advance(560)
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"]["ready"], 1)
        runs = self.pilot.run_due()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["summary"]["ingested"], 1)
        self.assertEqual(len(self.store.list_samples()), 1)

        # Creator's file is byte-for-byte & mtime_ns untouched, temp copy in uploads_dir is gone, local never called cloud
        after_stat = os.lstat(str(clip))
        self.assertEqual(hashlib.sha256(clip.read_bytes()).hexdigest(), before_sha)
        self.assertEqual((after_stat.st_size, after_stat.st_mtime_ns), (before_stat.st_size, before_stat.st_mtime_ns))
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])
        self.assertNotEqual(Path(self.transcriber.calls[0]["path"]).parent, self.watch_dir)
        self.assertEqual(Path(self.transcriber.calls[0]["path"]).parent, self.store.uploads_dir)
        self.assertEqual(self.transport_calls, [])

        # Next cadence: settled file is NOT ingested a second time
        self.advance(600)
        self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(len(self.store.list_samples()), 1)

    def test_depth_two_hidden_symlinks_zero_byte_and_batch_bounds(self) -> None:
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        self.enable_tasks("voice.ingest_folder", level="suggest")

        # Ignored files: hidden, hidden subfolder, depth 3, 0-byte, .part, symlink
        self.write_media(".hidden.mp4", b"hidden")
        self.write_media(".hidden_dir/inside.mp4", b"hidden2")
        self.write_media("sub1/sub2/depth3.mp4", b"too-deep")
        self.write_media("empty.mp4", b"")
        self.write_media("downloading.mp4.part", b"partial")
        if hasattr(os, "symlink") and os.name == "posix":
            outside_media = self.tmp / "outside.mp4"
            outside_media.write_bytes(b"outside-secret")
            os.symlink(str(outside_media), str(self.watch_dir / "linked.mp4"))

        # 7 valid files across root + immediate subfolder -> bounded to 5 ingested per run!
        for idx in range(4):
            self.write_media("root_%d.mp4" % idx, b"video-%d" % idx)
        for idx in range(3):
            self.write_media("2026_11/sub_%d.mov" % idx, b"subvideo-%d" % idx)

        self.pilot.run_due()  # Sighting 1
        self.assertEqual(len(self.store.list_watched_files()), 7)

        self.advance(600)
        runs = self.pilot.run_due()  # Sighting 2 -> 5 processed, 2 stay ready
        self.assertEqual(runs[0]["summary"]["ingested"], 5)
        payload = autovoice.voice_payload(self.store, now=self.now)
        self.assertEqual(payload["counts"], {"ready": 2, "ingested": 5, "waiting": 0, "failed": 0})

        self.advance(600)
        runs2 = self.pilot.run_due()  # Remaining 2 processed
        self.assertEqual(runs2[0]["summary"]["ingested"], 2)
        self.assertEqual(len(self.store.list_samples()), 7)

    def test_cloud_daily_cap_and_local_uninstalled_and_vanished_folder(self) -> None:
        # 1. Local route when transcriber is None or uninstalled skips cleanly with bilingual message
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        self.enable_tasks("voice.ingest_folder", level="auto", daily_calls=1)
        self.transcriber.installed = False
        self.write_media("a.mp4", b"video-a")
        self.write_media("b.mp4", b"video-b")

        self.pilot.run_due()
        self.advance(600)
        skipped_run = self.pilot.run_due()[0]
        self.assertEqual(skipped_run["status"], "done")
        self.assertEqual(skipped_run["summary"]["skipped_reason"], "transcriber_not_ready")
        self.assertRegex(skipped_run["summary"]["message_zh"], CHINESE_RE)
        self.assertEqual(self.store.list_samples(), [])

        # 2. Switch to cloud route with daily_model_calls = 1 -> first file ingested, second stopped by daily cap!
        self.configure_ai("gemini", "gemini-2.5-pro")
        autovoice.save_voice_settings(self.store, {"transcribe_route": "cloud"})
        self.advance(600)
        cloud_run = self.pilot.run_due()[0]
        self.assertEqual(cloud_run["status"], "done")
        self.assertEqual(cloud_run["summary"]["ingested"], 1)
        self.assertEqual(cloud_run["summary"]["deferred_daily_cap"], 1)
        self.assertEqual(cloud_run["model_calls"], 1)
        self.assertEqual(cloud_run["calls_refused"], 1)
        self.assertEqual(len(self.transport_calls), 1)
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])

        # 3. Vanished folder skips cleanly without failing
        shutil.rmtree(str(self.watch_dir))
        self.assertFalse(autovoice.voice_payload(self.store, now=self.now)["folder_exists"])
        self.advance(600)
        self.assertEqual(self.pilot.run_due(), [])
        forced = self.pilot.run_now("voice.ingest_folder")
        self.assertTrue(self.pilot.wait(10))
        finished = self.pilot.run(forced["run"]["id"])
        self.assertEqual((finished["status"], finished["summary"]["reason"]), ("done", "folder_missing"))

    def test_sponsored_subfolder_never_confirms_or_triggers_voice_relearn(self) -> None:
        self.configure_ai("gemini", "gemini-2.5-pro")
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        self.enable_tasks("voice.ingest_folder", "voice.relearn", level="auto")

        self.write_media("Sponsored/ad1.mp4", b"ad-1")
        self.write_media("sponsored/ad2.mp4", b"ad-2")
        self.write_media("商单/ad3.mov", b"ad-3")

        self.pilot.run_due()
        self.advance(3600)
        ran = self.pilot.run_due()
        self.assertEqual([r["task"] for r in ran], ["voice.ingest_folder"])  # voice.relearn did NOT trigger!
        samples = self.store.list_samples()
        self.assertEqual(len(samples), 3)
        self.assertTrue(all(s["is_sponsored"] for s in samples))
        self.assertTrue(all(s["needs_review"] for s in samples))
        self.assertEqual(pipeline.confirmed_samples(samples), [])
        self.assertEqual(self.transport_calls, [])


    def test_scan_cap_200_files_and_temp_cleanup_on_failure(self) -> None:
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})
        self.enable_tasks("voice.ingest_folder", level="suggest")

        for i in range(215):
            self.write_media("clip_%03d.mp4" % i, b"v")
        self.pilot.run_due()
        self.assertEqual(len(self.store.list_watched_files()), 200)

        # Transcriber error marks row failed and still cleans up temp file in uploads_dir
        self.transcriber.fail_exc = transcribe.TranscribeError("no audio track", code="no_audio", message_zh="没有音轨")
        self.advance(600)
        fail_run = self.pilot.run_due()[0]
        self.assertEqual(fail_run["summary"]["failed"], 5)
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"]["failed"], 5)

    def test_external_file_handler_hook_and_undo(self) -> None:
        imported_sheets = []
        self.registry.add_undo("sheets.imported", lambda _store, name: imported_sheets.remove(name))

        def take_sheet(context: autopilot.Context, path: Path):
            imported_sheets.append(path.name)
            context.record_undo("sheets.imported", path.name)
            return {"rows": 5}

        autovoice.register_file_handler("sheets", lambda p: p.suffix.lower() == ".csv", take_sheet)
        self.addCleanup(autovoice.unregister_file_handler, "sheets")

        # Works even when transcribe_route == "off"!
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "off"})
        self.enable_tasks("voice.ingest_folder", level="auto")
        csv_path = self.write_media("keywords.csv", "keyword,heat\n秋冬护肤,1000\n".encode("utf-8"))

        self.pilot.run_due()
        self.assertEqual(imported_sheets, [])
        self.advance(600)
        ran = self.pilot.run_due()
        self.assertEqual(ran[0]["summary"]["ingested"], 1)
        self.assertEqual(imported_sheets, ["keywords.csv"])
        self.assertTrue(csv_path.is_file())

        undone = self.pilot.undo(ran[0]["id"])
        self.assertEqual(undone["undone"], 2)
        self.assertEqual(imported_sheets, [])
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"]["ingested"], 0)


class TrustCheckAndIngestUndoTest(AutoVoiceCase):
    def test_suggest_never_confirms_and_auto_enforces_deterministic_trust_checks(self) -> None:
        autovoice.save_voice_settings(self.store, {"watch_dir": str(self.watch_dir), "transcribe_route": "local"})

        # 1. At SUGGEST: trusted transcript stays needs_review = True
        self.enable_tasks("voice.ingest_folder", level="suggest")
        self.write_media("suggest_ok.mp4", b"v1")
        self.pilot.run_due()
        self.advance(600)
        suggest_run = self.pilot.run_due()[0]
        self.assertEqual((suggest_run["summary"]["ingested"], suggest_run["summary"]["confirmed"]), (1, 0))
        s_suggest = self.store.list_samples()[0]
        self.assertTrue(s_suggest["needs_review"])

        # Undo at suggest deletes untouched sample and resets watched_files row
        self.pilot.undo(suggest_run["id"])
        self.assertEqual(self.store.list_samples(), [])
        self.assertEqual(autovoice.voice_payload(self.store, now=self.now)["counts"]["ingested"], 0)

        # 2. At AUTO: short, low-CJK, out-of-bounds CPM, and placeholder stay needs_review = True
        self.enable_tasks("voice.ingest_folder", level="auto")
        cases = [
            ("too_short.mp4", SHORT_SEGMENTS, False),
            ("low_cjk.mp4", LOW_CJK_SEGMENTS, False),
            ("too_fast.mp4", TOO_FAST_SEGMENTS, False),
            ("placeholder.mp4", PLACEHOLDER_SEGMENTS, False),
            ("trusted_good.mp4", TRUSTED_SEGMENTS, True),
        ]
        for fname, segs, expect_confirmed in cases:
            for existing in list(self.watch_dir.iterdir()):
                existing.unlink()
            self.transcriber.segments = segs
            self.write_media(fname, b"media-" + fname.encode("ascii"))
            self.advance(600)
            self.pilot.run_due()
            self.advance(600)
            run_line = self.pilot.run_due()[0]
            newest = self.store.list_samples()[0]
            self.assertEqual(not newest["needs_review"], expect_confirmed, (fname, newest))
            if expect_confirmed:
                self.assertEqual(run_line["summary"]["confirmed"], 1)

        # 3. Standalone voice.confirmed undo sets needs_review back to True; full undo deletes untouched sample
        good_sample = [s for s in self.store.list_samples() if s["title"] == "trusted_good"][0]
        self.assertFalse(good_sample["needs_review"])
        last_auto_run = self.pilot.status()["runs"][0]
        self.pilot.undo(last_auto_run["id"])
        self.assertIsNone(self.store.get_sample(good_sample["id"]))

        # 4. Edited sample refuses undo cleanly in both languages and keeps creator's edits
        for existing in list(self.watch_dir.iterdir()):
            existing.unlink()
        self.transcriber.segments = TRUSTED_SEGMENTS
        self.write_media("edited_later.mp4", b"edited-media")
        self.advance(600)
        self.pilot.run_due()
        self.advance(600)
        run_edit = self.pilot.run_due()[0]
        created_sample = [s for s in self.store.list_samples() if s["title"] == "edited_later"][0]
        self.store.update_sample(created_sample["id"], {"text": created_sample["text"] + "\n我自己后来补充的一句话哈"})
        with self.assertRaises(autopilot.AutopilotError) as ctx:
            self.pilot.undo(run_edit["id"])
        self.assertEqual(ctx.exception.code, "autopilot_undo_refused")
        self.assertRegex(ctx.exception.message_zh, CHINESE_RE)
        self.assertIn("我自己后来补充的一句话哈", self.store.get_sample(created_sample["id"])["text"])


class VoiceRelearnTest(AutoVoiceCase):
    def _add_organic_samples(self, count: int, *, prefix: str = "日常", rate_cpm: int = 220) -> None:
        for i in range(count):
            self.store.create_sample(
                title="%s_%d" % (prefix, i),
                text=(
                    "上周末跟我老公去 Costco 补货嘛 / 结果某人站在货架前面研究了十分钟哈\n"
                    "其实这瓶面霜我入冬已经用空第二罐啦 / 推开水水的，一点不糊脸诶"
                ),
                is_sponsored=False,
                kind="subtitle",
                timing={"units": rate_cpm, "spoken_sec": 60.0, "speaking_rate_cpm": rate_cpm, "cues": 4, "pauses": 2, "span_sec": 60.0},
            )

    def test_relearn_due_conditions_suggest_mode_and_manual_pending_guard(self) -> None:
        self.enable_tasks("voice.relearn", level="suggest")

        # Unconfigured or manual provider -> never due
        self._add_organic_samples(3, prefix="首批")
        self.configure_ai("manual")
        self.assertEqual(self.pilot.run_due(), [])

        # Configured -> 3 confirmed non-sponsored samples trigger proposal at SUGGEST, leaving active DNA as example
        self.configure_ai("gemini", "gemini-2.5-pro")
        ran = self.pilot.run_due()
        self.assertEqual(len(ran), 1)
        self.assertEqual((ran[0]["task"], ran[0]["summary"]["status"]), ("voice.relearn", "proposed"))
        self.assertIsNotNone(self.store.load_pending())
        self.assertTrue(self.store.load_dna()[1])  # still example DNA!

        # 1 new sample after 2 hours (< 7 days) is NOT due; after 7 days IS due!
        self._add_organic_samples(1, prefix="新增一")
        self.advance(7200)
        self.assertEqual(self.pilot.run_due(), [])
        self.advance(7 * 86400)
        ran_7d = self.pilot.run_due()
        self.assertEqual(len(ran_7d), 1)
        self.assertEqual(ran_7d[0]["summary"]["status"], "proposed")

        # Undo of SUGGEST run clears autopilot's pending proposal
        self.pilot.undo(ran_7d[0]["id"])
        self.assertIsNone(self.store.load_pending())

        # Manual pending proposal waiting before AUTO run: refuses to overwrite it and spends 0 calls!
        manual_pending = {"dna": copy.deepcopy(EXAMPLE_DNA), "created_at": 999.5, "based_on": 2}
        self.store.save_pending(manual_pending)
        self._add_organic_samples(3, prefix="手动等待期")
        self.enable_tasks("voice.relearn", level="auto")
        self.advance(3600)
        calls_before = len(self.transport_calls)
        held = self.pilot.run_due()[0]
        self.assertEqual((held["summary"]["status"], held["summary"]["reason"]), ("held_back", "manual_pending_exists"))
        self.assertEqual(len(self.transport_calls), calls_before)
        self.assertEqual(self.store.load_pending()["created_at"], 999.5)

    def test_auto_relearn_safety_bounds_and_clean_dna_restore_undo(self) -> None:
        self.configure_ai("gemini", "gemini-2.5-pro")
        self.store.save_creator(creator.normalize({"name": "小满", "pronoun": "she", "niches": ["护肤"], "never": ["宝子们"]}))
        custom_initial = copy.deepcopy(EXAMPLE_DNA)
        custom_initial["profile_version"] = "custom-v0"
        custom_initial["cadence_and_syntax"]["target_speaking_rate_cpm"] = 220
        custom_initial["anti_voice_blacklist"]["banned_phrases_zh"] = [
            "绝绝子", "按头安利", "闭眼入", "天花板", "赋能", "心智",
        ]
        custom_initial["source_corpus_summary"] = {"accepted_at": 100.0}
        self.store.save_dna(custom_initial)

        self.enable_tasks("voice.relearn", level="auto")
        self._add_organic_samples(3, prefix="达标", rate_cpm=225)

        # Bound 1: Positive clash with creator.never_phrases ("宝子们") holds proposal back!
        self.store.create_sample(title="含禁用词", text="宝子们今天去逛超市买菜啦", is_sponsored=False, kind="script")
        self.voice_reply = _stage2_voice_reply(
            banned=["绝绝子", "按头安利", "闭眼入", "天花板"],
            uses=["宝子们", "跟你们讲"],
        )
        b1 = self.pilot.run_due()[0]
        self.assertEqual((b1["summary"]["status"], b1["summary"]["bound"]), ("held_back", "never_phrases"))
        self.assertEqual(self.store.load_dna()[0]["profile_version"], "custom-v0")
        self.assertIsNotNone(self.store.load_pending())

        # Bound 3: Dropping more than half of existing custom banned_slop_phrases holds proposal back!
        self._add_organic_samples(3, prefix="第二批", rate_cpm=225)
        self.voice_reply = _stage2_voice_reply(banned=["绝绝子"], uses=["跟你们讲"])
        self.advance(3600)
        b3 = self.pilot.run_due()[0]
        self.assertEqual((b3["summary"]["status"], b3["summary"]["bound"]), ("held_back", "banned_slop_drop"))
        self.assertEqual(self.store.load_dna()[0]["profile_version"], "custom-v0")

        # Bound 2: Speaking rate shift > 35% (220 -> ~360 CPM) holds proposal back!
        for _ in range(4):
            self.store.create_sample(
                title="超快语速",
                text="上周末跟我老公去超市买菜啦结果人特别多嘛",
                is_sponsored=False,
                kind="subtitle",
                timing={"units": 1200, "spoken_sec": 180.0, "speaking_rate_cpm": 400, "cues": 10, "pauses": 3, "span_sec": 180.0},
            )
        self.voice_reply = _stage2_voice_reply(
            banned=["绝绝子", "按头安利", "闭眼入", "天花板", "宝子们"],
            uses=["跟你们讲"],
        )
        self.advance(3600)
        b2 = self.pilot.run_due()[0]
        self.assertEqual((b2["summary"]["status"], b2["summary"]["bound"]), ("held_back", "speaking_rate_shift"))
        self.assertEqual(self.store.load_dna()[0]["profile_version"], "custom-v0")

        # Within all bounds: strips "宝子们" from ban list, keeps >= half of custom bans, rate within 35% -> approves!
        for s in self.store.list_samples():
            if s["title"] == "超快语速":
                self.store.delete_sample(s["id"])
        self._add_organic_samples(3, prefix="正常语速", rate_cpm=230)
        self.voice_reply = _stage2_voice_reply(
            banned=["绝绝子", "按头安利", "闭眼入", "天花板", "宝子们"],
            uses=["跟你们讲"],
            essence_zh="新学到的松弛真实聊天感",
        )
        self.advance(3600)
        ok_run = self.pilot.run_due()[0]
        self.assertEqual(ok_run["summary"]["status"], "approved")
        new_dna, is_ex = self.store.load_dna()
        self.assertFalse(is_ex)
        self.assertEqual(new_dna["profile_version"], "1.0")
        self.assertEqual(
            new_dna["anti_voice_blacklist"]["banned_phrases_zh"],
            ["绝绝子", "按头安利", "闭眼入", "天花板"],
        )
        self.assertIn(
            "推开水水的",
            new_dna["lexical_fingerprint"]["genuine_praise_lexicon"]["skincare_and_beauty"],
        )
        self.assertNotIn("宝子们", new_dna["anti_voice_blacklist"]["banned_phrases_zh"])
        self.assertIsNone(self.store.load_pending())

        # Undo restores exact previous custom DNA!
        self.pilot.undo(ok_run["id"])
        restored_dna, is_ex_after = self.store.load_dna()
        self.assertFalse(is_ex_after)
        self.assertEqual(restored_dna, custom_initial)


class RecurringLessonsTest(AutoVoiceCase):
    def _make_edited_script(self, title: str, ai_spoken: str, final_spoken: str, *, mode: str = MODE_ORGANIC) -> str:
        script = self.store.create_script(
            title=title,
            lines=[{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": ai_spoken}],
            from_ai=True,
            mode=mode,
        )
        self.store.update_script(script["id"], {
            "lines": [{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": final_spoken}],
        })
        return script["id"]

    def test_accepts_recurring_edits_only_at_auto_across_non_sponsored_scripts_and_undoes(self) -> None:
        # 1 organic + 1 sponsored with same edit -> NOT recurring across >= 2 non-sponsored scripts!
        self._make_edited_script("有机一篇", "这个面霜真的绝绝子", "这个面霜真的蛮舒服的", mode=MODE_ORGANIC)
        self._make_edited_script("商单一篇", "这件开衫真的绝绝子", "这件开衫真的巨省心", mode=MODE_SPONSORED)

        self.enable_tasks("lessons.accept_recurring", level="auto")
        self.assertEqual(self.pilot.run_due(), [])
        self.assertEqual(self.store.list_lessons(), [])

        # Add a second organic script with recurring phrase removal ("绝绝子") + identical rewrite in another pair
        self._make_edited_script("有机二篇", "这瓶精华真的绝绝子", "这瓶精华真的巨省心", mode=MODE_ORGANIC)
        self._make_edited_script("结尾一", "家人们谁懂啊快冲", "好啦我们下期见哈", mode=MODE_ORGANIC)
        self._make_edited_script("结尾二", "家人们谁懂啊快冲", "好啦我们下期见哈", mode=MODE_ORGANIC)

        # At SUGGEST: never due, accepts nothing
        self.enable_tasks("lessons.accept_recurring", level="suggest")
        self.assertEqual(self.pilot.run_due(), [])

        # At AUTO: accepts recurring lessons, then is no longer due
        self.enable_tasks("lessons.accept_recurring", level="auto")
        ran = self.pilot.run_due()
        self.assertEqual(len(ran), 1)
        self.assertGreaterEqual(ran[0]["summary"]["accepted"], 2)
        self.assertGreaterEqual(len(self.store.list_lessons()), 2)

        self.advance(86400)
        self.assertEqual(self.pilot.run_due(), [])

        # Undo removes all untouched auto-accepted lessons cleanly!
        self.pilot.undo(ran[0]["id"])
        self.assertEqual(self.store.list_lessons(), [])


    def test_standalone_confirmed_undo_and_initial_dna_undo_and_edited_lesson_refusal(self) -> None:
        # 1. Initial example DNA -> AUTO voice.relearn -> undo restores back to example DNA
        self.configure_ai("gemini", "gemini-2.5-pro")
        for i in range(3):
            self.store.create_sample(
                title="初始_%d" % i,
                text="上周末跟我老公去超市买菜啦 / 结果人特别多嘛 / 真的蛮舒服的哈",
                is_sponsored=False,
                kind="script",
            )
        self.enable_tasks("voice.relearn", level="auto")
        r1 = self.pilot.run_due()[0]
        self.assertEqual(r1["summary"]["status"], "approved")
        self.assertFalse(self.store.load_dna()[1])
        self.pilot.undo(r1["id"])
        self.assertTrue(self.store.load_dna()[1])

        # 2. Standalone voice.confirmed undo handler sets needs_review back to True
        sample = self.store.create_sample(
            title="视频待确认",
            text="上周末跟我老公去超市买菜啦 / 结果人特别多嘛",
            is_sponsored=False,
            kind="video",
        )
        digest = autovoice._sample_content_digest(sample)
        confirmed = self.store.update_sample(sample["id"], {"needs_review": False})
        self.assertFalse(confirmed["needs_review"])
        autovoice._undo_confirmed(self.store, {
            "sample_id": sample["id"],
            "updated_at": confirmed["updated_at"],
            "digest": digest,
        })
        self.assertTrue(self.store.get_sample(sample["id"])["needs_review"])

        # 3. Modified auto-accepted lesson refuses undo cleanly
        for i in range(2):
            sc = self.store.create_script(
                title="编辑_%d" % i,
                lines=[{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "因此我非常推荐它"}],
                from_ai=True,
                mode=MODE_ORGANIC,
            )
            self.store.update_script(sc["id"], {
                "lines": [{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "所以我真的很推荐它嘛"}],
            })
        self.enable_tasks("lessons.accept_recurring", level="auto")
        self.advance(86400)
        lrun = self.pilot.run_due()[0]
        lid = lrun["summary"]["lesson_ids"][0]
        with autovoice._db(self.store) as db, db:
            db.execute("UPDATE lessons SET note = '我自己改了备注' WHERE id = ?", (lid,))
        with self.assertRaises(autopilot.AutopilotError) as ctx:
            self.pilot.undo(lrun["id"])
        self.assertEqual(ctx.exception.code, "autopilot_undo_refused")
        self.assertRegex(ctx.exception.message_zh, CHINESE_RE)
        self.assertIsNotNone(self.store.get_lesson(lid))

if __name__ == "__main__":
    unittest.main()
