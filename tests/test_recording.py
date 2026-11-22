"""Tests for Workstream V1: voiceover recording booth, audio-aligned subtitles & FCPXML 1.9 export."""
from __future__ import annotations

import base64
import http.client
import io
import json
import math
import os
import re
import shutil
import struct
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from studio import backup, exports, mcp_server, recording, server, store as store_mod
from studio.store import Store
from tests import browser


def _make_tone_wav(
    bursts: List[Tuple[float, float, float]],
    *,
    rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    """Build deterministic PCM WAV bytes from ``[(duration_sec, freq_hz, amplitude_0_to_1), ...]``."""
    mono_floats: List[float] = []
    phase = 0.0
    for dur_sec, freq_hz, amp in bursts:
        n = int(round(dur_sec * rate))
        step = 2.0 * math.pi * freq_hz / float(rate) if freq_hz > 0 else 0.0
        for _ in range(n):
            if amp > 0 and freq_hz > 0:
                # Fundamental + 2nd harmonic so autocorrelation pitch detection sees a clear period at freq_hz.
                val = amp * (0.75 * math.sin(phase) + 0.25 * math.sin(2.0 * phase))
                phase += step
            else:
                val = 0.0
            mono_floats.append(max(-1.0, min(1.0, val)))

    payload = bytearray()
    for sample in mono_floats:
        for _ in range(channels):
            if bits == 16:
                iv = int(round(sample * 30000))
                payload.extend(struct.pack("<h", max(-32768, min(32767, iv))))
            elif bits == 24:
                iv = max(-8388608, min(8388607, int(round(sample * 7500000))))
                payload.extend(int(iv).to_bytes(3, "little", signed=True))
            elif bits == 8:
                u8 = max(0, min(255, int(round(128 + sample * 110))))
                payload.append(u8)
            else:
                raise ValueError("Unsupported test bits: %d" % bits)

    bytes_per_sample = bits // 8
    block_align = channels * bytes_per_sample
    byte_rate = rate * block_align
    head = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(payload),
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        len(payload),
    )
    return head + bytes(payload)


class _FakeWhisperTranscriber:
    """Deterministic offline stand-in for studio.transcribe when testing whisper_aligned takes."""

    def __init__(self, segments: List[Dict[str, Any]], *, duration_sec: float = 6.0, sound: Optional[Dict[str, Any]] = None) -> None:
        self._segments = segments
        self._duration_sec = duration_sec
        self._sound = sound

    def status(self) -> Dict[str, Any]:
        return {"supported": True, "installed": True, "model_ready": True}

    def transcribe_file(self, path: str) -> Dict[str, Any]:
        assert os.path.exists(path), "temp file should exist while transcribing"
        return {
            "segments": list(self._segments),
            "duration_sec": self._duration_sec,
            "sound": self._sound,
        }


class _UnavailableTranscriber:
    def status(self) -> Dict[str, Any]:
        return {"supported": False, "installed": False, "model_ready": False}


class _RecordingBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-rec-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.tmp / "data")

    def make_sample_script(self) -> Dict[str, Any]:
        return self.store.create_script(
            title="周日采购日志",
            target_sec=30,
            lines=[
                {
                    "line_no": 1,
                    "audio_mode": "VOICEOVER",
                    "spoken_script_zh": "[笑] 家人们今天去超市 / 结果风超级大",
                    "visual_broll_en_zh": "推着购物车走进大风里的超市门口",
                    "onscreen_flower_text_zh": "周日大风天补货！",
                },
                {
                    "line_no": 2,
                    "audio_mode": "LIVE_CLIP",
                    "time_range_sec": "3.0-4.5",
                    "spoken_script_zh": "哇外面真的好冷",
                    "visual_broll_en_zh": "现场原声：关车门缩脖子",
                    "onscreen_flower_text_zh": "冻到发抖",
                },
                {
                    "line_no": 3,
                    "audio_mode": "VOICEOVER",
                    "spoken_script_zh": "最后还是拿了四盒小笼包 / 真的太香啦",
                    "visual_broll_en_zh": "冰柜特写拿四盒小笼包",
                    "onscreen_flower_text_zh": "四盒小笼包安排",
                },
            ],
        )


class SubtitleParsingAndAlignmentTest(_RecordingBase):
    def test_parse_srt_and_vtt_handles_bom_headers_and_invalid_input(self) -> None:
        srt = (
            "\ufeff1\n"
            "00:00:00,200 --> 00:00:01,800\n"
            "家人们今天去超市\n\n"
            "2\n"
            "00:00:01,900 --> 00:00:03,400\n"
            "结果风超级大\n"
        )
        parsed_srt = recording.parse_subtitles(srt)
        self.assertEqual(parsed_srt, [(0.2, 1.8, "家人们今天去超市"), (1.9, 3.4, "结果风超级大")])

        vtt = (
            "WEBVTT\n"
            "Kind: captions\n\n"
            "cue-1\n"
            "00:00.250 --> 00:01.750 align:middle\n"
            "第一口气\n\n"
            "00:02.000 --> 00:03.600\n"
            "第二口气\n"
        )
        parsed_vtt = recording.parse_subtitles(vtt)
        self.assertEqual(parsed_vtt, [(0.25, 1.75, "第一口气"), (2.0, 3.6, "第二口气")])
        self.assertEqual(recording.parse_subtitles("not a subtitle file at all"), [])

    def test_align_timed_segments_skips_live_clips_aligns_flowers_and_detects_adlibs(self) -> None:
        script = self.make_sample_script()
        segments = [
            (0.20, 1.60, "家人们今天去超市"),
            (1.65, 3.00, "结果风超级大"),
            # Gap 3.00..4.60 where LIVE_CLIP line 2 sits!
            (4.60, 6.20, "最后竟然搬了六盒小笼包"),
            (6.25, 7.50, "真的绝绝子"),
        ]
        res = recording.align_timed_segments_to_script(script["lines"], segments, alignment_mode="subtitle_aligned")
        self.assertEqual(res["alignment_mode"], "subtitle_aligned")
        cues = res["cues"]
        self.assertEqual(len(cues), 4)
        # Spoken cues keep exact scripted breath clauses (without [笑] stage cues), strictly monotonic:
        self.assertEqual([c["text"] for c in cues], [
            "家人们今天去超市",
            "结果风超级大",
            "最后还是拿了四盒小笼包",
            "真的太香啦",
        ])
        for prev, curr in zip(cues, cues[1:]):
            self.assertGreaterEqual(curr["start_sec"], prev["end_sec"])
            self.assertGreaterEqual(curr["end_sec"] - curr["start_sec"], recording.MIN_CUE_SEC - 1e-6)
        self.assertAlmostEqual(cues[0]["start_sec"], 0.20, places=2)
        self.assertAlmostEqual(cues[1]["end_sec"], 3.00, places=2)
        self.assertGreaterEqual(cues[2]["start_sec"], 4.50)

        # Flower cues cover line 1 (0.20..3.00), LIVE_CLIP line 2 inside the 3.00..4.60 gap, and line 3 (4.60..7.50)
        flowers = res["flower_cues"]
        self.assertEqual([f["text"] for f in flowers], ["周日大风天补货！", "冻到发抖", "四盒小笼包安排"])
        self.assertAlmostEqual(flowers[0]["start_sec"], cues[0]["start_sec"], places=2)
        self.assertAlmostEqual(flowers[0]["end_sec"], cues[1]["end_sec"], places=2)
        self.assertGreaterEqual(flowers[1]["start_sec"], 3.00)
        self.assertLessEqual(flowers[1]["end_sec"], cues[2]["start_sec"] + 0.01)

        # Line 1 matched verbatim -> no adlib; Line 3 diverged -> detected adlib!
        adlibs = res["adlibs"]
        self.assertEqual(len(adlibs), 1)
        self.assertEqual(adlibs[0]["line_no"], 3)
        self.assertIn("六盒小笼包", adlibs[0]["spoken_zh"])
        self.assertLess(adlibs[0]["similarity"], 0.85)


class WavPauseAlignmentTest(_RecordingBase):
    def test_16khz_pcm_wav_pause_islands_and_soundstats(self) -> None:
        script = self.store.create_script(
            title="三口气测试",
            lines=[
                {"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "今天天气真不错 / 我们出门去逛街", "onscreen_flower_text_zh": "出门逛街"},
                {"line_no": 2, "audio_mode": "VOICEOVER", "spoken_script_zh": "顺便买杯热咖啡", "onscreen_flower_text_zh": "热美式"},
            ],
        )
        # 3 tone bursts separated by >= 0.4s pauses (total > 3.5s so soundstats pitch & pauses measure cleanly)
        wav_bytes = _make_tone_wav([
            (0.15, 0.0, 0.0),      # 0.00..0.15 lead silence
            (1.10, 210.0, 0.55),   # 0.15..1.25 burst 1
            (0.45, 0.0, 0.0),      # 1.25..1.70 pause 1 (>= 0.30s)
            (1.10, 210.0, 0.55),   # 1.70..2.80 burst 2
            (0.45, 0.0, 0.0),      # 2.80..3.25 pause 2 (>= 0.30s)
            (1.05, 210.0, 0.55),   # 3.25..4.30 burst 3
            (0.20, 0.0, 0.0),      # 4.30..4.50 tail silence
        ])
        wav_path = self.tmp / "bursts.wav"
        wav_path.write_bytes(wav_bytes)

        aligned = recording.align_wav_pauses_to_script(script["lines"], wav_path)
        self.assertEqual(aligned["alignment_mode"], "pause_aligned")
        self.assertEqual(aligned["adlibs"], [])
        self.assertGreater(aligned["measured_cpm"], 100.0)

        cues = aligned["cues"]
        self.assertEqual(len(cues), 3)
        # Each of the 3 clauses snaps to one of the 3 detected speech bursts!
        self.assertAlmostEqual(cues[0]["start_sec"], 0.15, delta=0.06)
        self.assertAlmostEqual(cues[0]["end_sec"], 1.25, delta=0.06)
        self.assertAlmostEqual(cues[1]["start_sec"], 1.70, delta=0.06)
        self.assertAlmostEqual(cues[1]["end_sec"], 2.80, delta=0.06)
        self.assertAlmostEqual(cues[2]["start_sec"], 3.25, delta=0.06)
        self.assertAlmostEqual(cues[2]["end_sec"], 4.30, delta=0.06)

        sound = aligned["sound"]
        self.assertTrue(sound.get("measured"))
        self.assertEqual(sound["pauses"]["count"], 2)
        self.assertIsNotNone(sound.get("pitch"))
        self.assertAlmostEqual(sound["pitch"]["median_hz"], 210, delta=15)

    def test_stereo_44k1_and_24bit_pcm_wav_resample_cleanly(self) -> None:
        script = self.store.create_script(
            title="立体声重采样",
            lines=[{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "第一句测试 / 第二句测试"}],
        )
        for rate, ch, bits in ((44100, 2, 16), (24000, 1, 24)):
            wav_bytes = _make_tone_wav([
                (0.8, 190.0, 0.5),
                (0.4, 0.0, 0.0),
                (0.8, 190.0, 0.5),
            ], rate=rate, channels=ch, bits=bits)
            p = self.tmp / ("test_%d_%d_%d.wav" % (rate, ch, bits))
            p.write_bytes(wav_bytes)
            res = recording.align_wav_pauses_to_script(script["lines"], p)
            self.assertEqual(res["alignment_mode"], "pause_aligned")
            self.assertEqual(len(res["cues"]), 2)
            self.assertTrue(res["sound"].get("measured"))

        bad = self.tmp / "not_wav.wav"
        bad.write_bytes(b"NOT A RIFF FILE")
        with self.assertRaises(recording.RecordingError) as ctx:
            recording.align_wav_pauses_to_script(script["lines"], bad)
        self.assertEqual(ctx.exception.code, "bad_wav")


class FcpxmlAndExportAlignmentTest(_RecordingBase):
    def test_fcpxml_1_9_structure_rational_frames_lanes_markers_and_xml_escaping(self) -> None:
        script = self.store.create_script(
            title='测试 <FCPXML> & "引号"\x07',
            lines=[
                {
                    "line_no": 1,
                    "audio_mode": "VOICEOVER",
                    "spoken_script_zh": "第一口气 <测试> & 符号 / 第二口气",
                    "visual_broll_en_zh": '镜头 1: <特写> & "灯光"',
                    "onscreen_flower_text_zh": "花字 <一> & 亮眼",
                },
                {
                    "line_no": 2,
                    "audio_mode": "VOICEOVER",
                    "spoken_script_zh": "结尾再来一句",
                    "visual_broll_en_zh": "镜头 2: 收尾全景",
                    "onscreen_flower_text_zh": "下期见！",
                },
            ],
        )
        for lang in ("zh", "en"):
            fname, body, ctype = exports.export_file(script, "fcpxml", lang=lang)
            self.assertTrue(fname.endswith("时间线.fcpxml" if lang == "zh" else "timeline.fcpxml"))
            self.assertEqual(ctype, "application/xml; charset=utf-8")
            xml_text = body.decode("utf-8")
            self.assertIn("<!DOCTYPE fcpxml>", xml_text)
            root = ET.fromstring(xml_text)
            self.assertEqual(root.tag, "fcpxml")
            self.assertEqual(root.attrib.get("version"), "1.9")

            fmt = root.find("./resources/format")
            self.assertIsNotNone(fmt)
            self.assertEqual(fmt.attrib["width"], "1080")
            self.assertEqual(fmt.attrib["height"], "1920")
            self.assertEqual(fmt.attrib["frameDuration"], "100/3000s")

            seq = root.find("./library/event/project/sequence")
            self.assertIsNotNone(seq)
            self.assertRegex(seq.attrib["duration"], r"^\d+/30s$")

            gap = seq.find("./spine/gap")
            self.assertIsNotNone(gap)
            titles = gap.findall("./title")
            lane1 = [t for t in titles if t.attrib.get("lane") == "1"]
            lane2 = [t for t in titles if t.attrib.get("lane") == "2"]
            self.assertEqual(len(lane1), 3)
            self.assertEqual(len(lane2), 2)

            for lane_titles in (lane1, lane2):
                prev_end_frames = 0
                for t_el in lane_titles:
                    self.assertRegex(t_el.attrib["offset"], r"^\d+/30s$")
                    self.assertRegex(t_el.attrib["duration"], r"^\d+/30s$")
                    off_f = int(t_el.attrib["offset"].split("/")[0])
                    dur_f = int(t_el.attrib["duration"].split("/")[0])
                    self.assertGreaterEqual(off_f, prev_end_frames)
                    self.assertGreaterEqual(dur_f, 1)
                    prev_end_frames = off_f + dur_f

            markers = gap.findall("./marker")
            self.assertEqual(len(markers), 2)
            self.assertIn('<特写> & "灯光"', markers[0].attrib["value"])

    def test_exports_switch_cleanly_between_rate_estimated_and_active_take(self) -> None:
        script = self.make_sample_script()
        # Rate-estimated baseline
        _, est_srt, _ = exports.export_file(script, "srt", lang="zh", active_take=None)
        _, est_zip, _ = exports.export_file(script, "jianying", lang="zh", active_take=None)
        with zipfile.ZipFile(io.BytesIO(est_zip)) as zf:
            self.assertNotIn("时间线.fcpxml", zf.namelist())
            self.assertIn("按语速", zf.read("使用说明.txt").decode("utf-8"))

        # Ingest an .srt take with distinct timestamps starting at 1.500s
        srt_upload = (
            "1\n00:00:01,500 --> 00:00:02,800\n家人们今天去超市\n\n"
            "2\n00:00:02,900 --> 00:00:04,200\n结果风超级大\n\n"
            "3\n00:00:05,500 --> 00:00:07,100\n最后还是拿了四盒小笼包\n\n"
            "4\n00:00:07,200 --> 00:00:08,600\n真的太香啦\n"
        ).encode("utf-8")
        take = recording.ingest_take(self.store, script["id"], "aligned.srt", srt_upload, label="剪映导出字幕")
        self.assertEqual(take["alignment_mode"], "subtitle_aligned")

        _, aligned_srt, _ = exports.export_file(script, "srt", lang="zh", active_take=take)
        self.assertNotEqual(aligned_srt, est_srt)
        self.assertIn("00:00:01,500 --> 00:00:02,800", aligned_srt.decode("utf-8"))

        _, aligned_flw, _ = exports.export_file(script, "flower_srt", lang="zh", active_take=take)
        self.assertIn("00:00:01,500 --> 00:00:04,200", aligned_flw.decode("utf-8"))

        _, aligned_zip, _ = exports.export_file(script, "jianying", lang="zh", active_take=take)
        with zipfile.ZipFile(io.BytesIO(aligned_zip)) as zf:
            self.assertIn("时间线.fcpxml", zf.namelist())
            howto = zf.read("使用说明.txt").decode("utf-8")
            self.assertIn("subtitle_aligned", howto)
            ET.fromstring(zf.read("时间线.fcpxml").decode("utf-8"))


class HttpTakesAndExportsTest(_RecordingBase):
    def _serve(self, transcriber: Any = None) -> int:
        httpd = server.make_server(0, self.store, transcriber=transcriber)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.03}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def _req(self, port: int, method: str, path: str, body: Any = None) -> Tuple[int, Any, Dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            headers = {"Host": "127.0.0.1:%d" % port}
            payload: Optional[bytes] = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            if "application/json" in hdrs.get("content-type", ""):
                return resp.status, json.loads(raw.decode("utf-8")), hdrs
            return resp.status, raw, hdrs
        finally:
            conn.close()

    def test_http_take_lifecycle_adlib_apply_temp_cleanup_and_export_wiring(self) -> None:
        fake_whisper = _FakeWhisperTranscriber([
            {"start": 0.30, "end": 1.80, "text": "家人们今天去超市"},
            {"start": 1.85, "end": 3.10, "text": "结果风超级大"},
            {"start": 4.40, "end": 6.00, "text": "最后居然抢了五盒小笼包"},
            {"start": 6.05, "end": 7.30, "text": "真的绝了"},
        ], duration_sec=7.4)
        port = self._serve(transcriber=fake_whisper)
        script = self.make_sample_script()
        sid = script["id"]

        st, init_state, _ = self._req(port, "GET", "/api/scripts/%s/takes" % sid)
        self.assertEqual(st, 200)
        self.assertEqual(init_state["takes"], [])
        self.assertIsNone(init_state["active_take"])
        self.assertTrue(init_state["capabilities"]["whisper"])

        # 1. Upload mic WAV -> aligned via fake Whisper + soundstats measured from WAV, temp file deleted!
        wav_bytes = _make_tone_wav([(1.2, 200.0, 0.5), (0.4, 0.0, 0.0), (1.2, 200.0, 0.5)])
        st, created_1, _ = self._req(port, "POST", "/api/scripts/%s/takes" % sid, {
            "filename": "mic-take.wav",
            "data_base64": "data:audio/wav;base64," + base64.b64encode(wav_bytes).decode("ascii"),
            "source_kind": "mic",
            "label": "第一遍干声",
        })
        self.assertEqual(st, 201)
        take1 = created_1["take"]
        self.assertEqual(take1["alignment_mode"], "whisper_aligned")
        self.assertTrue(take1["active"])
        self.assertTrue(take1["sound"]["measured"])
        self.assertEqual(len(take1["adlibs"]), 1)
        self.assertEqual(take1["adlibs"][0]["line_no"], 3)
        # Zero leftover files in uploads_dir, and raw RIFF WAVE bytes never stored in SQLite!
        if self.store.uploads_dir.exists():
            self.assertEqual(list(self.store.uploads_dir.iterdir()), [])
        self.assertNotIn(b"RIFF", self.store.db_path.read_bytes())

        # Apply spoken ad-lib to line 3
        st, applied, _ = self._req(port, "POST", "/api/scripts/%s/takes/%d/apply-adlib" % (sid, take1["id"]), {
            "line_no": 3,
        })
        self.assertEqual(st, 200)
        self.assertIn("五盒小笼包", applied["script"]["lines"][2]["spoken_script_zh"])
        self.assertEqual(applied["take"]["adlibs"], [])

        # 2. Upload a second take (.vtt) -> becomes active automatically
        vtt_bytes = (
            "WEBVTT\n\n"
            "00:01.000 --> 00:02.500\n家人们今天去超市\n\n"
            "00:02.600 --> 00:04.000\n结果风超级大\n\n"
            "00:05.000 --> 00:06.800\n最后居然抢了五盒小笼包\n\n"
            "00:06.900 --> 00:08.200\n真的绝了\n"
        ).encode("utf-8")
        st, created_2, _ = self._req(port, "POST", "/api/scripts/%s/takes" % sid, {
            "filename": "captions.vtt",
            "data_base64": base64.b64encode(vtt_bytes).decode("ascii"),
            "source_kind": "vtt",
            "label": "外部字幕",
        })
        self.assertEqual(st, 201)
        take2 = created_2["take"]
        self.assertEqual(take2["alignment_mode"], "subtitle_aligned")
        self.assertEqual(created_2["active_take"]["id"], take2["id"])
        self.assertEqual(len(created_2["takes"]), 2)

        # HTTP export endpoints reflect take2 (starts at 00:00:01,000) and FCPXML 1.9 downloads cleanly
        st, srt_raw, _ = self._req(port, "GET", "/api/scripts/%s/export/srt?lang=zh" % sid)
        self.assertEqual(st, 200)
        self.assertIn("00:00:01,000 --> 00:00:02,500", srt_raw.decode("utf-8"))

        st, fcpxml_raw, hdrs = self._req(port, "GET", "/api/scripts/%s/export/fcpxml?lang=en" % sid)
        self.assertEqual(st, 200)
        self.assertIn("application/xml", hdrs["content-type"])
        ET.fromstring(fcpxml_raw.decode("utf-8"))

        # Clear active take -> reverts export to rate-estimated timing (starts at 00:00:00,000)
        st, cleared, _ = self._req(port, "POST", "/api/scripts/%s/takes/clear-active" % sid, {})
        self.assertEqual(st, 200)
        self.assertIsNone(cleared["active_take"])
        st, est_srt_raw, _ = self._req(port, "GET", "/api/scripts/%s/export/srt?lang=zh" % sid)
        self.assertTrue(est_srt_raw.decode("utf-8").startswith("1\n00:00:00,000 -->"))

        # Re-activate take 1, then delete take 2, then delete script (cascading script_takes)
        st, reactivated, _ = self._req(port, "POST", "/api/scripts/%s/takes/%d/activate" % (sid, take1["id"]), {})
        self.assertEqual(st, 200)
        self.assertEqual(reactivated["active_take"]["id"], take1["id"])

        st, after_del, _ = self._req(port, "DELETE", "/api/scripts/%s/takes/%d" % (sid, take2["id"]))
        self.assertEqual(st, 200)
        self.assertEqual([t["id"] for t in after_del["takes"]], [take1["id"]])

        st, _, _ = self._req(port, "DELETE", "/api/scripts/%s" % sid)
        self.assertEqual(st, 200)
        self.assertEqual(self.store.list_script_takes(sid), [])

    def test_http_error_handling_and_missing_codec_honest_refusal(self) -> None:
        port = self._serve(transcriber=_UnavailableTranscriber())
        script = self.make_sample_script()
        sid = script["id"]

        # Bad base64
        st, err, _ = self._req(port, "POST", "/api/scripts/%s/takes" % sid, {
            "filename": "a.wav",
            "data_base64": "%%%not-base64%%%",
        })
        self.assertEqual((st, err["error"]), (400, "bad_base64"))

        # Unsupported extension
        st, err, _ = self._req(port, "POST", "/api/scripts/%s/takes" % sid, {
            "filename": "notes.pdf",
            "data_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
        })
        self.assertEqual((st, err["error"]), (415, "bad_media_type"))

        # Compressed .mp3 when neither Whisper nor afconvert is available -> clear recording_needs_wav_or_srt
        with mock.patch.object(recording, "has_afconvert", return_value=False):
            st, err, _ = self._req(port, "POST", "/api/scripts/%s/takes" % sid, {
                "filename": "voice.mp3",
                "data_base64": base64.b64encode(b"ID3-fake-mp3-bytes").decode("ascii"),
            })
        self.assertEqual((st, err["error"]), (400, "recording_needs_wav_or_srt"))
        if self.store.uploads_dir.exists():
            self.assertEqual(list(self.store.uploads_dir.iterdir()), [])


class McpAndBackupRecordingTest(_RecordingBase):
    def _mcp(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        reply = mcp_server.call_tool(self.store, name, args)
        self.assertFalse(reply.get("isError"), reply)
        return json.loads(reply["content"][0]["text"])

    def test_mcp_get_script_timing_and_takes_and_kit_export(self) -> None:
        script = self.make_sample_script()
        sid = script["id"]

        # Before any take: rate_estimated
        info0 = self._mcp("get_script_timing_and_takes", {"script_id": sid})
        self.assertEqual(info0["timing_source"], "rate_estimated")
        self.assertIsNone(info0["active_take"])
        self.assertEqual(info0["takes"], [])
        self.assertGreater(len(info0["estimated"]["subtitle_cues"]), 0)

        # Save a WAV pause-aligned take
        wav_bytes = _make_tone_wav([(1.1, 205.0, 0.5), (0.4, 0.0, 0.0), (1.1, 205.0, 0.5)])
        take = recording.ingest_take(
            self.store, sid, "booth.wav", wav_bytes, label="棚录第一版", source_kind="mic",
            transcriber=_UnavailableTranscriber(),
        )
        self.assertEqual(take["alignment_mode"], "pause_aligned")

        info1 = self._mcp("get_script_timing_and_takes", {"script_id": sid})
        self.assertEqual(info1["timing_source"], "pause_aligned")
        self.assertEqual(info1["active_take"]["id"], take["id"])
        self.assertEqual(len(info1["takes"]), 1)
        self.assertIn("without speech recognition", info1["note"])

        # export_jianying_kit uses the active take and bundles 时间线.fcpxml
        exported = self._mcp("export_jianying_kit", {"script_id": sid})
        zip_path = Path(exported["saved_to"])
        self.assertTrue(zip_path.exists())
        self.assertEqual(exported["timing_source"], "pause_aligned")
        with zipfile.ZipFile(zip_path) as zf:
            self.assertIn("时间线.fcpxml", zf.namelist())
            self.assertIn("pause_aligned", zf.read("使用说明.txt").decode("utf-8"))

    def test_backup_and_restore_preserves_script_takes_at_data_version_5(self) -> None:
        self.assertEqual(backup.DATA_VERSION, 5)
        script = self.make_sample_script()
        sid = script["id"]
        srt_bytes = (
            "1\n00:00:00,400 --> 00:00:01,900\n家人们今天去超市\n\n"
            "2\n00:00:02,000 --> 00:00:03,400\n结果风超级大\n\n"
            "3\n00:00:04,500 --> 00:00:06,200\n最后居然拿了八盒小笼包\n\n"
            "4\n00:00:06,300 --> 00:00:07,800\n真的太香啦\n"
        ).encode("utf-8")
        saved_take = recording.ingest_take(self.store, sid, "sub.srt", srt_bytes, label="备份测试字幕")
        self.assertTrue(saved_take["active"])
        self.assertEqual(len(saved_take["adlibs"]), 1)

        archive_path = self.tmp / "roundtrip.lmvbak"
        backup.create(self.store, archive_path)
        fresh_store = Store(self.tmp / "restored-data")
        mgr = backup.Backups(fresh_store)
        staged = mgr.stage(archive_path, remove_source=False)
        mgr.restore(staged["restore_id"])

        restored_active = fresh_store.get_active_script_take(sid)
        self.assertIsNotNone(restored_active)
        self.assertEqual(restored_active["label"], "备份测试字幕")
        self.assertEqual(restored_active["alignment_mode"], "subtitle_aligned")
        self.assertEqual(restored_active["cues"], saved_take["cues"])
        self.assertEqual(restored_active["adlibs"], saved_take["adlibs"])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class RecordingBoothBrowserTest(_RecordingBase):
    def test_recording_booth_panel_aligns_subtitle_applies_adlib_and_deletes_cleanly(self) -> None:
        script = self.make_sample_script()
        sid = script["id"]
        srt_content = (
            "1\n00:00:00,300 --> 00:00:01,700\n家人们今天去超市\n\n"
            "2\n00:00:01,800 --> 00:00:03,100\n结果风超级大\n\n"
            "3\n00:00:04,500 --> 00:00:06,100\n最后竟然买了六盒小笼包\n\n"
            "4\n00:00:06,200 --> 00:00:07,600\n真的太香啦\n"
        )
        with browser.open_app(self.store, lang="zh") as page:
            page.go("#/s/" + sid, "Boolean(document.getElementById('rc-panel')) && !rcUi.loading")
            self.assertEqual(page.problems, [])
            self.assertEqual(page.eval_js("document.getElementById('rc-mode-badge').textContent"), "按语速估算")

            # Open panel and attach .srt file through #rc-file-input
            page.eval_js("(() => { const p = document.getElementById('rc-panel'); p.open = true; p.dispatchEvent(new Event('toggle')); return true; })()")
            page.eval_js(
                "(() => {"
                "  const dt = new DataTransfer();"
                "  dt.items.add(new File([%s], 'booth.srt', { type: 'text/plain' }));"
                "  const inp = document.getElementById('rc-file-input');"
                "  inp.files = dt.files;"
                "  inp.dispatchEvent(new Event('change'));"
                "  return true;"
                "})()" % json.dumps(srt_content)
            )
            page.wait_for("!rcUi.busy && Boolean(rcUi.activeTake)")
            self.assertEqual(page.problems, [])
            self.assertEqual(page.eval_js("document.getElementById('rc-mode-badge').textContent"), "字幕轴对齐")
            self.assertEqual(page.eval_js("document.querySelectorAll('[data-rc-apply-adlib]').length"), 1)

            # One-click apply spoken ad-lib -> updates editor line 3 and persists to Store!
            page.eval_js("(() => { document.querySelector('[data-rc-apply-adlib=\"0\"]').click(); return true; })()")
            page.wait_for("!rcUi.busy && (!rcUi.activeTake.adlibs || rcUi.activeTake.adlibs.length === 0)")
            self.assertEqual(page.problems, [])
            spoken_line_3 = page.eval_js("document.querySelectorAll('.line .spoken')[2].value")
            self.assertIn("六盒小笼包", spoken_line_3)
            self.assertIn("六盒小笼包", self.store.get_script(sid)["lines"][2]["spoken_script_zh"])

            # Switch back to rate estimate, then delete take via inline two-step confirmation
            page.eval_js("(() => { document.getElementById('rc-clear-active-btn').click(); return true; })()")
            page.wait_for("!rcUi.busy && rcUi.activeTake === null")
            self.assertEqual(page.eval_js("document.getElementById('rc-mode-badge').textContent"), "按语速估算")

            page.eval_js("(() => { document.querySelector('[data-rc-del-ask]').click(); return true; })()")
            page.wait_for("Boolean(document.querySelector('[data-rc-del-yes]'))")
            page.eval_js("(() => { document.querySelector('[data-rc-del-yes]').click(); return true; })()")
            page.wait_for("!rcUi.busy && rcUi.takes.length === 0")
            self.assertEqual(page.problems, [])
            self.assertEqual(self.store.list_script_takes(sid), [])

    def test_recording_booth_wav_upload_in_english_mode(self) -> None:
        script = self.make_sample_script()
        sid = script["id"]
        wav_bytes = _make_tone_wav([
            (1.1, 210.0, 0.55),
            (0.45, 0.0, 0.0),
            (1.1, 210.0, 0.55),
            (0.45, 0.0, 0.0),
            (1.1, 210.0, 0.55),
        ])
        wav_b64 = base64.b64encode(wav_bytes).decode("ascii")
        with browser.open_app(self.store, lang="en", transcriber=_UnavailableTranscriber()) as page:
            page.go("#/s/" + sid, "Boolean(document.getElementById('rc-panel')) && !rcUi.loading")
            self.assertEqual(page.problems, [])
            self.assertEqual(page.eval_js("document.getElementById('rc-mode-badge').textContent"), "Rate estimate")

            page.eval_js("(() => { const p = document.getElementById('rc-panel'); p.open = true; p.dispatchEvent(new Event('toggle')); return true; })()")
            page.eval_js(
                "(() => {"
                "  const bin = atob(%s);"
                "  const arr = new Uint8Array(bin.length);"
                "  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);"
                "  const dt = new DataTransfer();"
                "  dt.items.add(new File([arr], 'voice.wav', { type: 'audio/wav' }));"
                "  const inp = document.getElementById('rc-file-input');"
                "  inp.files = dt.files;"
                "  inp.dispatchEvent(new Event('change'));"
                "  return true;"
                "})()" % json.dumps(wav_b64)
            )
            page.wait_for("!rcUi.busy && Boolean(rcUi.activeTake)")
            self.assertEqual(page.problems, [])
            self.assertEqual(page.eval_js("document.getElementById('rc-mode-badge').textContent"), "Speech-pause aligned")
            self.assertIn("no speech recognition", page.eval_js("document.getElementById('rc-explain').textContent"))
            self.assertIn("2 pauses", page.eval_js("document.querySelector('[data-rc-take]').textContent"))


if __name__ == "__main__":
    unittest.main()
