"""Tests for counted wording habits and measured sound: studio/tone_analyzer.py and studio/soundstats.py.

Every sound is made here with ``wave``: tones, noise, silence. Nothing is installed, nothing is transcribed and
no connection leaves this computer. What these tests cannot show is how the numbers come out for a recording of
a real person; no such recording is part of this repository.
"""
from __future__ import annotations

import ast
import http.client
import io
import json
import math
import os
import random
import re
import shutil
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from studio import mcp_server, pipeline, posted, server, soundstats, timing, tone_analyzer
from studio.store import Store
from tests import browser

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000


def tone(hz: float, seconds: float, height: int = 9000, overtones: tuple = (1.0,)) -> list[int]:
    """Samples of a tone; ``overtones`` are the heights of the tone itself, twice its pitch, three times, and so on."""
    return [
        int(sum(height * part * math.sin(2 * math.pi * hz * (k + 1) * i / RATE) for k, part in enumerate(overtones)))
        for i in range(int(seconds * RATE))
    ]


def silence(seconds: float) -> list[int]:
    return [0] * int(round(seconds * RATE))


def noise(seconds: float, height: int = 6000, seed: int = 7) -> list[int]:
    rng = random.Random(seed)
    return [int((rng.random() * 2 - 1) * height) for _ in range(int(seconds * RATE))]


def talk(stretches: int = 11, hz: float = 220, gap: float = 0.5) -> list[int]:
    """1.2 s of a tone and then a silence, repeated; every second stretch louder."""
    samples: list[int] = []
    for n in range(stretches):
        samples += tone(hz, 1.2, 9000 if n % 2 else 3000) + silence(gap)
    return samples


def wav_bytes(samples: list[int], rate: int = RATE, channels: int = 1, width: int = 2) -> bytes:
    held = io.BytesIO()
    with wave.open(held, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(struct.pack("<%dh" % len(samples), *samples) if width == 2 else bytes(len(samples)))
    return held.getvalue()


def extensible(samples: list[int], sub_format: bytes = bytes([1, 0])) -> bytes:
    """The WAVE_FORMAT_EXTENSIBLE header (0xFFFE) written by macOS afconvert for audio extracted from a video."""
    data = struct.pack("<%dh" % len(samples), *samples)
    fmt = (
        struct.pack("<HHIIHHHHI", 0xFFFE, 1, RATE, 2 * RATE, 2, 16, 22, 16, 4)
        + sub_format
        + bytes.fromhex("000000001000800000aa00389b71")
    )
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"FLLR" + struct.pack("<I", 13) + bytes(14)
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-tone-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def file(self, data: bytes, name: str = "sound.wav") -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def measured(self, samples: list[int]) -> dict:
        return soundstats.measure(self.file(wav_bytes(samples)))


class SoundStdlibIsolationTest(unittest.TestCase):
    def test_soundstats_imports_only_python_stdlib_and_no_audioop(self) -> None:
        tree = ast.parse((ROOT / "studio" / "soundstats.py").read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "soundstats.py must not import from studio")
                if node.module:
                    imported.append(node.module)
        self.assertNotIn("audioop", imported)
        self.assertNotIn("studio", imported)


class SoundFileTest(Folder):
    def test_the_header_the_macos_converter_writes_for_video_sound_is_read(self) -> None:
        samples = talk(3)
        plain, video = self.file(wav_bytes(samples), "plain.wav"), self.file(extensible(samples), "video.wav")
        with open(str(video), "rb") as handle:
            header = soundstats.wav_header(handle)
        self.assertEqual(header, {
            "pcm": True, "channels": 1, "rate": RATE, "byte_rate": 2 * RATE, "bits": 16,
            "data_at": 90, "data_bytes": 2 * len(samples),
        })
        self.assertEqual(soundstats.measure(video), soundstats.measure(plain))
        self.assertTrue(soundstats.measure(video)["measured"])

    def test_a_file_of_another_shape_is_not_measured_and_says_why(self) -> None:
        some = tone(220, 2)
        for label, data in (
            ("44.1 kHz", wav_bytes(some, rate=44100)),
            ("two channels", wav_bytes(some, channels=2)),
            ("8-bit", wav_bytes(some, width=1)),
            ("not plain samples", extensible(some, bytes([3, 0]))),
        ):
            self.assertEqual(soundstats.measure(self.file(data)), {"measured": False, "reason": "other_format"}, label)
        self.assertEqual(self.measured(tone(220, 0.9)), {"measured": False, "reason": "too_short"})
        self.assertEqual(self.measured(tone(220, 1.0))["measured_sec"], 1.0)
        self.assertEqual(set(soundstats.REASONS), {"unreadable", "other_format", "too_short"})

    def test_whatever_a_file_holds_nothing_is_raised(self) -> None:
        good = wav_bytes(tone(220, 2))
        fmt_at, data_at = good.index(b"fmt "), good.index(b"data")
        many_parts = good[:12] + (b"JUNK" + struct.pack("<I", 0)) * (soundstats.MAX_CHUNKS + 1) + good[12:]
        for label, data in (
            ("empty", b""),
            ("text", "这不是声音".encode("utf-8")),
            ("a video", bytes([0, 0, 0, 24]) + b"ftypmp42" + bytes(64)),
            ("the header only", good[:12]),
            ("cut inside the format", good[:fmt_at + 14]),
            ("no samples part", good[:data_at]),
            ("samples before the format", good[:12] + good[data_at:] + good[fmt_at:data_at]),
            ("a format part of 4 bytes", good[:12] + b"fmt " + struct.pack("<I", 4) + bytes(4) + good[data_at:]),
            ("more parts than are stepped over", many_parts),
            ("a part that says it is 4 GB long", good[:12] + b"JUNK" + struct.pack("<I", 0xFFFFFFFF) + good[12:]),
        ):
            self.assertEqual(soundstats.measure(self.file(data)), {"measured": False, "reason": "unreadable"}, label)
        self.assertEqual(soundstats.measure(self.tmp / "gone.wav"), {"measured": False, "reason": "unreadable"})
        self.assertEqual(soundstats.measure(self.tmp), {"measured": False, "reason": "unreadable"})
        lying = good[:data_at + 4] + struct.pack("<I", 0xFFFFFFF0) + good[data_at + 8:]
        self.assertEqual(soundstats.measure(self.file(lying)), soundstats.measure(self.file(good, "good.wav")))
        cut = good[:data_at + 8 + 2 * RATE + 1]
        self.assertEqual(
            (soundstats.measure(self.file(cut))["measured_sec"], soundstats.measure(self.file(cut))["duration_sec"]),
            (1.0, 1.0),
        )
        just_fits = good[:12] + (b"JUNK" + struct.pack("<I", 0)) * (soundstats.MAX_CHUNKS - 2) + good[12:]
        self.assertTrue(soundstats.measure(self.file(just_fits))["measured"])


class PitchTest(Folder):
    def test_a_220hz_and_other_tones_come_out_at_their_own_pitch(self) -> None:
        for hz in (80, 110, 220, 300, 390):
            pitch = self.measured(tone(hz, 3))["pitch"]
            self.assertIsNotNone(pitch)
            self.assertAlmostEqual(pitch["median_hz"], hz, delta=max(1.0, hz * 0.01), msg=str(hz))
            self.assertEqual((pitch["low_hz"], pitch["high_hz"], pitch["span_semitones"]), (pitch["median_hz"], pitch["median_hz"], 0.0))
        self.assertAlmostEqual(soundstats.pitch_of(tone(220, 0.045)), 220.0, delta=0.5)
        self.assertEqual(len(tone(220, 0.045)), soundstats.PITCH_SAMPLES)

    def test_a_voice_like_sound_is_placed_at_its_fundamental_not_an_overtone(self) -> None:
        rich = tone(120, 3, 2500, (1.0, 0.8, 0.6, 0.5, 0.4, 0.3))
        self.assertEqual(self.measured(rich)["pitch"]["median_hz"], 120)
        strong_second = tone(220, 3, 3000, (1.0, 3.0))
        self.assertEqual(self.measured(strong_second)["pitch"]["median_hz"], 220)

    def test_silence_and_noise_come_out_as_none_never_a_bogus_number(self) -> None:
        quiet = self.measured(silence(12))
        self.assertEqual(quiet, {
            "measured": True, "measured_sec": 12.0, "duration_sec": 12.0, "cut_short": False,
            "sound_pct": 0, "pauses": None, "loudness": None, "pitch": None,
        })
        hiss = self.measured(noise(12))
        self.assertEqual((hiss["sound_pct"], hiss["pitch"]), (100, None))
        self.assertIsNone(soundstats.pitch_of(noise(0.045)))
        self.assertIsNone(soundstats.pitch_of(silence(0.045)))
        self.assertIsNone(soundstats.pitch_of([5] * soundstats.PITCH_SAMPLES))
        self.assertIsNone(soundstats.pitch_of(tone(220, 0.01)))
        faint = self.measured(noise(12, height=20))
        self.assertEqual((faint["sound_pct"], faint["pauses"], faint["pitch"]), (0, None, None))

    def test_a_pitch_needs_enough_voiced_stretches(self) -> None:
        mostly_noise = self.measured(tone(220, 1) + noise(11, height=9000))
        self.assertIsNone(mostly_noise["pitch"])
        few = self.measured(tone(220, 1))
        self.assertEqual((few["pitch"]["median_hz"], few["pitch"]["stretches"], few["pitch"]["tried"]), (220, 39, 39))
        long = self.measured(tone(220, 30))
        self.assertEqual((long["pitch"]["tried"], long["pitch"]["stretches"]), (soundstats.MAX_PITCH_STRETCHES,) * 2)


class PausesAndLoudnessTest(Folder):
    def test_pauses_are_silences_of_0_3s_or_more_between_sound(self) -> None:
        spoken = (
            silence(2) + tone(220, 1.2) + silence(0.25) + tone(220, 1.2)
            + silence(0.3) + tone(220, 1.2) + silence(0.5) + tone(220, 1.2)
            + silence(1.5) + tone(220, 6) + silence(3)
        )
        got = self.measured(spoken)
        self.assertEqual(got["pauses"], {"count": 3, "median_sec": 0.5, "per_minute": 13.5})
        self.assertEqual(soundstats.PAUSE_SEC, timing.PAUSE_SEC)
        one = self.measured(tone(220, 6))
        self.assertEqual(one["pauses"], {"count": 0, "median_sec": None, "per_minute": None})

    def test_a_click_does_not_decide_what_counts_as_sound(self) -> None:
        soft = [v // 20 for v in talk(8)]
        soft[5:205] = [30000] * 200
        self.assertEqual(self.measured(soft)["pauses"]["count"], 7)

    def test_loudness_range_measures_10th_to_90th_percentile_of_voiced_seconds(self) -> None:
        got = self.measured(talk())
        self.assertEqual(got["loudness"], {"range_db": 9.5, "seconds": 18})
        self.assertEqual(self.measured(tone(220, 12))["loudness"], {"range_db": 0.0, "seconds": 12})
        self.assertIsNone(self.measured(tone(220, 9))["loudness"])
        self.assertEqual(self.measured([2 * v for v in talk()])["loudness"], got["loudness"])


class BoundAndStoredSoundTest(Folder):
    def test_recording_longer_than_ten_minutes_is_bounded_and_marked_cut_short(self) -> None:
        self.assertEqual(soundstats.MAX_MEASURED_SEC, 600)
        path = self.tmp / "long.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            head = talk(4)
            out.writeframes(struct.pack("<%dh" % len(head), *head))
            out.writeframes(bytes(2 * (RATE * 601 - len(head))))
            tail = tone(300, 5)
            out.writeframes(struct.pack("<%dh" % len(tail), *tail))
        got = soundstats.measure(path)
        self.assertEqual((got["measured_sec"], got["duration_sec"], got["cut_short"]), (600.0, 606.0, True))
        self.assertEqual(got["pitch"]["median_hz"], 220)
        whole = self.measured(talk(4))
        self.assertEqual((whole["cut_short"], whole["measured_sec"], whole["duration_sec"]), (False, 6.8, 6.8))

    def test_stored_measurements_are_cleaned_and_aggregated_without_averaging(self) -> None:
        good = self.measured(talk())
        self.assertEqual(soundstats.clean(json.loads(json.dumps(good))), good)
        for bad in (None, 5, "sound", {}, {"measured": False, "reason": "invented"}, dict(good, measured_sec=float("nan"))):
            self.assertIsNone(soundstats.clean(bad))
        cleaned = soundstats.clean(dict(good, pitch=dict(good["pitch"], median_hz=9000)))
        self.assertIsNone(cleaned["pitch"])
        self.assertEqual(cleaned["loudness"], good["loudness"])

        def rec(range_db: float | None, hz: int | None, span: float | None, cut: bool = False) -> dict:
            return {
                "measured": True, "measured_sec": 60.0, "duration_sec": 60.0, "cut_short": cut, "sound_pct": 80,
                "pauses": {"count": 5, "median_sec": 0.5, "per_minute": 10.0},
                "loudness": None if range_db is None else {"range_db": range_db, "seconds": 40},
                "pitch": None if hz is None else {
                    "median_hz": hz, "low_hz": hz - 20, "high_hz": hz + 20, "span_semitones": span,
                    "stretches": 200, "tried": 400,
                },
            }

        agg = soundstats.across([rec(8.0, 210, 5.0), rec(11.0, 230, 7.0, cut=True), rec(9.0, 200, 6.0), None, "bad"])
        self.assertEqual((agg["recordings"], agg["cut_short"]), (3, 1))
        self.assertEqual(agg["loudness_range_db"], {"median": 9.0, "low": 8.0, "high": 11.0, "recordings": 3})
        self.assertEqual(agg["pitch_hz"], {"median": 210, "low": 200, "high": 230, "recordings": 3})


class ToneCountingTest(unittest.TestCase):
    def test_no_untuned_personality_axes_or_composite_scores_exist(self) -> None:
        ling = tone_analyzer.analyze_text_linguistics(["今天去超市补货嘛 / 然后买了两盒小笼包啦"])
        for removed in ("tone_axes", "dominant_tone_zh", "dominant_tone_en", "warmth_intimacy", "playful_humor",
                        "restraint_authenticity", "spoken_naturalness", "pacing_energy"):
            self.assertNotIn(removed, ling)
        self.assertFalse(hasattr(tone_analyzer, "analyze_wav_prosody"))

    def test_connector_matching_has_no_overlapping_or_substring_collisions(self) -> None:
        # Overlapping connectors: longest wins once; shorter substrings inside it do not fire.
        overlap = tone_analyzer.analyze_text_linguistics([
            "不过说实话今天真冷 / 然后呢我们就回家了 / 所以说还是窝在家里舒服 / 顺便提一嘴咖啡很好喝"
        ])
        self.assertEqual(overlap["connectors"]["colloquial"], {
            "不过说实话": 1, "然后呢": 1, "所以说": 1, "顺便提一嘴": 1,
        })
        self.assertEqual(overlap["connectors"]["colloquial_breaths"], 4)

        # Compound words containing 2-char connector substrings must NOT be miscounted as connectors.
        traps = tone_analyzer.analyze_text_linguistics([
            "自然而然就习惯了 / 之所以选这款面霜 / 名副其实的好用 / 检查结果完全正常 / 敏感肌换季也不过敏 / "
            "防晒霜还没有过期 / 晚上涂着不过夜也行 / 忙得喘不过气 / 小时候爱吃干脆面"
        ])
        self.assertEqual(traps["connectors"]["colloquial"], {})
        self.assertEqual(traps["connectors"]["bookish"], {})

        # Genuine clause-initial 2-char connectors and 但其实 DO count.
        genuine = tone_analyzer.analyze_text_linguistics([
            "不过今天风很大 / 然而他还是出门了 / 结果帽子被吹跑了 / 但其实家里还有一顶"
        ])
        self.assertEqual(genuine["connectors"]["colloquial"], {"不过": 1, "其实": 1, "结果": 1})
        self.assertEqual(genuine["connectors"]["bookish"], {"然而": 1})

    def test_negated_hard_sell_and_hedges_are_not_counted(self) -> None:
        negated = tone_analyzer.analyze_text_linguistics([
            "大家千万不要闭眼入 / 别盲目按头安利 / 我从来不说绝绝子 / 这真算不上什么天花板 / "
            "没有任何小缺点 / 挑不出一点小缺点 / 这个颜色完全不挑人"
        ])
        self.assertEqual(negated["hard_sell"]["count"], 0)
        self.assertEqual(negated["hedges"]["count"], 0)

        # Contrast where negation belongs to an earlier verb still counts the positive phrase after 但.
        affirmative = tone_analyzer.analyze_text_linguistics([
            "虽然价格不便宜但真的闭眼入！ / 唯一的小缺点是瓶身沉 / 但如果你是干皮稍微有点不够润"
        ])
        self.assertEqual(affirmative["hard_sell"]["phrases"], {"闭眼入": 1})
        self.assertEqual(affirmative["hedges"]["phrases"], {"但如果你": 1, "唯一的小缺点": 1, "稍微有点": 1})

    def test_reduplications_particles_and_punctuation_ignore_stage_cues_and_kinship(self) -> None:
        ling = tone_analyzer.analyze_text_linguistics([
            "妈妈的毛衣软软糯糯的质感[轻笑!] / 宝宝的脸蛋水水的很舒服对吧？ / 哈哈哈哈谢谢的啦！！！"
        ])
        # 软软糯糯的 counts 软软糯糯 once (not 糯糯的); 妈妈的, 宝宝的, 谢谢的, 哈哈哈哈 are excluded.
        self.assertEqual(ling["reduplications"]["words"], {"水水的": 1, "软软糯糯": 1})
        self.assertEqual(ling["particles"]["multi"], {"对吧": 1})
        self.assertEqual(ling["particles"]["single"], {"啦": 1})
        # [轻笑!] cue exclamation is ignored; ！！！ on the 3rd breath counts as 1 exclamation breath.
        self.assertEqual(ling["punctuation"]["question_breaths"], 1)
        self.assertEqual(ling["punctuation"]["exclamation_breaths"], 1)
        self.assertEqual(ling["laugh_cues"], 1)

    def test_wordlists_and_bilingual_js_keys_are_documented_and_complete(self) -> None:
        self.assertIn("project's own", tone_analyzer.__doc__)
        self.assertIn("21 November 2026", tone_analyzer.__doc__)
        self.assertIn("21 November 2026", soundstats.__doc__)

        tone_js = (ROOT / "studio" / "web" / "tone.js").read_text(encoding="utf-8")
        zh_t = set(re.findall(r"\b(pl_tone_[a-z0-9_]+)\s*:", tone_js.split("Object.assign(I18N.zh, {")[1].split("});")[0]))
        en_t = set(re.findall(r"\b(pl_tone_[a-z0-9_]+)\s*:", tone_js.split("Object.assign(I18N.en, {")[1].split("});")[0]))
        self.assertGreater(len(zh_t), 15)
        self.assertEqual(zh_t, en_t)

        craft_js = (ROOT / "studio" / "web" / "craft.js").read_text(encoding="utf-8")
        zh_c = set(re.findall(r"\b(tn_ed_[a-z0-9_]+)\s*:", craft_js.split("Object.assign(I18N.zh, {")[3].split("});")[0]))
        en_c = set(re.findall(r"\b(tn_ed_[a-z0-9_]+)\s*:", craft_js.split("Object.assign(I18N.en, {")[3].split("});")[0]))
        self.assertGreater(len(zh_c), 15)
        self.assertEqual(zh_c, en_c)


class Ears:
    """Stand-in transcriber returning deterministic segments and whatever sound measurement is configured."""

    SEGMENTS = [
        {"start": 0.0, "end": 4.0, "text": "今天聊聊我入冬换的面霜嘛"},
        {"start": 4.5, "end": 8.0, "text": "结果脸真的不干了哈"},
    ]

    def __init__(self) -> None:
        self.sound: object = "absent"

    def status(self) -> dict:
        return {"supported": True, "installed": True, "model_ready": True, "reason": None}

    def transcribe_file(self, path: object, progress: object = None) -> dict:
        reply: dict = {"segments": self.SEGMENTS, "unclear_dropped": 0}
        if self.sound != "absent":
            reply["sound"] = self.sound
        return reply


class ServerCase(Folder):
    def setUp(self) -> None:
        super().setUp()
        self.env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "os-keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.tmp / "data")
        self.ears = Ears()
        self.httpd = server.make_server(0, self.store, lambda *args: {}, self.ears)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), thread.join(5)))

    def send(self, method: str, path: str, payload: object = None, body: bytes | None = None, content_type: str | None = None) -> tuple:
        head: dict[str, str] = {"Host": "127.0.0.1:%d" % self.port}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        if content_type:
            head["Content-Type"] = content_type
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        return res.status, (json.loads(raw.decode("utf-8")) if "json" in (res.getheader("Content-Type") or "") else raw)

    def transcribed(self, title: str = "一条视频", **more: object) -> dict:
        status, up = self.send("PUT", "/api/uploads?name=clip.mp4", body=b"not really a video", content_type="application/octet-stream")
        self.assertEqual(status, 201)
        status, started = self.send("POST", "/api/videos", dict({"upload_id": up["upload_id"], "title": title}, **more))
        self.assertEqual(status, 202)
        job, deadline = started["job"], time.time() + 20
        while job["status"] == "running" and time.time() < deadline:
            time.sleep(0.02)
            job = self.send("GET", "/api/jobs/" + job["id"])[1]["job"]
        self.assertEqual(job["status"], "done", job)
        return next(s for s in self.send("GET", "/api/samples")[1]["samples"] if s["id"] == job["result"]["sample_id"])


class UploadAndCorpusHygieneTest(ServerCase):
    ORGANIC_SCRIPTS = [
        "上周末跟我老公去 Costco 补货嘛 / 最搞笑的是某人站在货架前发呆哈 / 推开水水的很舒服对吧 / 回家煮了杯热咖啡啦",
        "不过说实话换季脸真的干干巴巴 / 但如果你是大油皮稍微有点润啦 / 软软糯糯的质感[轻笑] / 晚上涂一层就够哈",
        "顺便提一嘴这件毛衣洗了两次也没变形嘛 / 随便抓一件就能出门哈 / 真的巨省心喔唷 / 周末穿去散步啦",
    ]

    def test_upload_keeps_cleaned_sound_and_deletes_uploaded_video(self) -> None:
        self.ears.sound = self.measured(talk())
        item = self.transcribed()
        self.assertEqual(item["timing"]["sound"], self.ears.sound)
        self.assertEqual((item["timing"]["pauses"], item["timing"]["units"]), (1, 21))
        self.assertNotIn("acoustic_prosody", item["timing"])
        self.assertEqual(list(self.store.uploads_dir.iterdir()), [])

        self.ears.sound = {"measured": False, "reason": "other_format"}
        self.assertEqual(self.transcribed("甲")["timing"]["sound"], {"measured": False, "reason": "other_format"})
        for invalid in ("absent", None, "lively", {"measured": True, "mood": "lively"}, {"dynamic_range_db": 12.0}):
            self.ears.sound = invalid
            self.assertNotIn("sound", self.transcribed("乙")["timing"])

    def test_corpus_threshold_requires_10_confirmed_non_sponsored_breaths_and_4_script_breaths(self) -> None:
        # 6 breaths (< 10): round 1 accepted 6; round 2 requires >= 10.
        self.store.create_sample(title="一", text="今天去买菜嘛 / 结果人很多哈 / 然后回家啦")
        self.store.create_sample(title="二", text="周日煮咖啡嘛 / 配了个面包哈 / 味道不错啦")
        # Unconfirmed transcript, sponsored item, and posted AI script waiting for confirmation must NOT count!
        self.store.create_sample(title="未确认转写", text=" / ".join(["机器转写的句子嘛"] * 8), kind="video")
        self.store.create_sample(title="商单样本", text=" / ".join(["品牌方要求的台词"] * 8), is_sponsored=True)
        ai_script = pipeline.apply_draft(
            self.store,
            {"title": "AI初稿发布", "script_lines": [{"spoken_script_zh": " / ".join(["这是AI写的台词嘛"] * 6)}]},
            notes="测试",
            video_format="VLOG_VOICEOVER",
            target_sec=30,
        )
        posted.mark(self.store, ai_script["id"], {"posted_on": "2026-11-20"})

        draft = self.store.create_script(
            title="草稿",
            lines=[
                {"spoken_script_zh": "这周又去超市补货啦 / 然后拿了两盒水饺嘛"},
                {"spoken_script_zh": "结果某人又走不动哈 / 其实家里还有对吧"},
                {"spoken_script_zh": "这段是别人说话绝绝子闭眼入！！", "audio_mode": "LIVE_CLIP"},
            ],
        )

        st, thin = self.send("GET", "/api/scripts/%s/tone" % draft["id"])
        self.assertEqual(st, 200)
        self.assertEqual((thin["ready"], thin["reason"], thin["reference"]["breaths"]), (False, "too_little_material", 6))

        # Add a 3rd confirmed non-sponsored item (4 breaths -> total 10 breaths >= MIN_REFERENCE_BREATHS).
        self.store.create_sample(title="三", text="晚上去散步嘛 / 风吹着很舒服哈 / 顺便买了水果对吧 / 回家早点休息啦")
        st, ready = self.send("GET", "/api/scripts/%s/tone" % draft["id"])
        self.assertEqual(st, 200)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["reference"], {"scripts": 3, "breaths": 10})
        # LIVE_CLIP line's hard-sell words were excluded from the script's own spoken lines.
        self.assertEqual(ready["script"]["hard_sell"]["count"], 0)

        # Too-short script (< 4 breaths) -> ready=False, reason="too_short"
        short_script = self.store.create_script(title="太短", lines=[{"spoken_script_zh": "只有两口气嘛 / 还没写完哈"}])
        st, short_res = self.send("GET", "/api/scripts/%s/tone" % short_script["id"])
        self.assertEqual((st, short_res["ready"], short_res["reason"]), (200, False, "too_short"))

        # Unknown script -> 404
        st_404, _ = self.send("GET", "/api/scripts/000000000000/tone")
        self.assertEqual(st_404, 404)

    def test_pauses_per_100_units_reads_timing_units_key_and_build_voice_prompt_includes_delivery(self) -> None:
        sound = self.measured(talk())
        segs = [
            {"start": 0.0, "end": 12.0, "text": "上周末跟我老公去超市补货嘛然后拿了两盒水饺"},
            {"start": 12.6, "end": 25.0, "text": "结果某人站在货架前发呆哈推开水水的很舒服对吧"},
            {"start": 25.5, "end": 40.0, "text": "不过说实话换季脸真的干干巴巴晚上涂一层就够哈"},
            {"start": 40.6, "end": 65.0, "text": "顺便提一嘴这件毛衣洗了两次也没变形嘛随便抓一件就能出门哈"},
        ]
        t_stats = dict(timing.timing_stats(segs), sound=sound)
        self.assertIn("units", t_stats)
        self.assertNotIn("spoken_units", t_stats)

        for idx, txt in enumerate(self.ORGANIC_SCRIPTS):
            self.store.create_sample(title="样本%d" % idx, text=txt, timing=t_stats if idx == 0 else None)

        st, profile = self.send("GET", "/api/tone")
        self.assertEqual(st, 200)
        self.assertIsNotNone(profile["pauses_per_100_units"])
        self.assertGreater(profile["pauses_per_100_units"], 0.0)
        self.assertEqual(profile["sound"]["pitch_hz"]["median"], 220)
        self.assertIsNotNone(profile["reference_ranges"])
        self.assertEqual(profile["reference_ranges"]["particle_rate_pct"]["scripts"], 3)

        # Verify Stage 2 _build_voice prompt carries measured_delivery with pauses, loudness, pitch, and how-measured note.
        _, user_prompt = pipeline.voice_prompt(self.store)
        m = re.search(r"<computed_acoustic_stats>\s*(\{.*?\})\s*</computed_acoustic_stats>", user_prompt, re.S)
        self.assertIsNotNone(m)
        acoustic = json.loads(m.group(1))
        self.assertIn("measured_delivery", acoustic)
        md = acoustic["measured_delivery"]
        self.assertEqual(md["pauses_per_100_units"], profile["pauses_per_100_units"])
        self.assertEqual(md["pitch_hz"]["median"], 220)
        self.assertEqual(md["loudness_range_db"]["median"], 9.5)
        self.assertIn("autocorrelation", md["note"])

        # Verify MCP get_tone_analysis works for both corpus and script comparison without axes.
        mcp_corpus = mcp_server.call_tool(self.store, "get_tone_analysis", {})
        self.assertFalse(mcp_corpus["isError"])
        parsed_corpus = json.loads(mcp_corpus["content"][0]["text"])
        self.assertNotIn("tone_axes", parsed_corpus["linguistics"])


@unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
class BrowserToneTest(Folder):
    def setUp(self) -> None:
        super().setUp()
        self.env = mock.patch.dict(os.environ, {
            "STUDIO_KEYSTORE_FILE": str(self.tmp / "os-keystore.key"),
            "LEARN_MY_VOICE_NO_SIBLING_SCAN": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.tmp / "data")
        sound = self.measured(talk())
        for idx, txt in enumerate([
            "上周末跟我老公去 Costco 补货嘛 / 最搞笑的是某人站在货架前发呆哈 / 推开水水的很舒服对吧 / 回家煮了杯热咖啡啦",
            "不过说实话换季脸真的干干巴巴 / 但如果你是大油皮稍微有点润啦 / 软软糯糯的质感[轻笑] / 晚上涂一层就够哈",
            "顺便提一嘴这件毛衣洗了两次也没变形嘛 / 随便抓一件就能出门哈 / 真的巨省心喔唷 / 周末穿去散步啦",
        ]):
            self.store.create_sample(
                title='<img src=x onerror="window.__xss=1">样本%d' % idx,
                text=txt,
                timing={"speaking_rate_cpm": 232, "pauses": 4, "units": 80, "spoken_sec": 65.0, "sound": sound},
            )
        self.script = self.store.create_script(
            title="秋冬面霜草稿",
            lines=[
                {"spoken_script_zh": "因此今天绝绝子按头安利！ / 闭眼入天花板！！"},
                {"spoken_script_zh": "不仅如此众所周知真的炸裂必买！！ / 总而言之疯抢封神！！"},
            ],
        )

    def test_voice_page_and_script_editor_render_tone_counts_cleanly_in_zh_and_en(self) -> None:
        with browser.open_app(self.store, lang="zh", theme="dark") as page:
            # 1. #/voice card renders counted wording habits & measured sound (220 Hz, 9.5 dB) with zero problems.
            page.go("#/voice", settle="Boolean(document.getElementById('tone-analyzer-card'))")
            card_text = page.eval_js("document.getElementById('tone-analyzer-card').innerText")
            self.assertIn("220 Hz", card_text)
            self.assertIn("9.5 dB", card_text)
            self.assertIn("对吧", card_text)
            self.assertIn("软软糯糯", card_text)
            self.assertIsNone(page.eval_js("window.__xss || null"))
            self.assertEqual(page.problems, [])

            # 2. #/s/<id> script editor renders collapsible tone comparison beside voice match.
            page.go("#/s/" + self.script["id"], settle="Boolean(document.getElementById('tone-cmp-card'))")
            self.assertFalse(page.eval_js("document.getElementById('tone-cmp-details').open"))
            page.eval_js("document.querySelector('#tone-cmp-details > summary').click()")
            page.wait_for("document.getElementById('tone-cmp-details').open === true")
            cmp_text_zh = page.eval_js("document.getElementById('tone-cmp-card').innerText")
            self.assertIn("绝绝子", cmp_text_zh)
            self.assertIn("220 Hz", cmp_text_zh)

            # Switch language to English and verify open state survives redraw and renders English text cleanly.
            page.eval_js("document.getElementById('lang-btn').click()")
            # The language is set before the editor is drawn again: wait for the card as redrawn, not the one left over.
            page.wait_for("document.documentElement.lang === 'en' && Boolean(document.getElementById('tone-cmp-card'))"
                          " && document.getElementById('tone-cmp-card').innerText.includes('Tone & wording counts')")
            self.assertTrue(page.eval_js("document.getElementById('tone-cmp-details').open"))
            cmp_text_en = page.eval_js("document.getElementById('tone-cmp-card').innerText")
            self.assertIn("Tone & wording counts", cmp_text_en)
            self.assertIn("Hard-sell phrases", cmp_text_en)

            # Edit script lines in browser to remove hard-sell phrases; verify auto-save refreshes tone comparison!
            page.eval_js("""(() => {
                const areas = document.querySelectorAll('.spoken');
                areas[0].value = '上周末去超市买菜嘛 / 然后拿了两盒小笼包哈';
                areas[0].dispatchEvent(new Event('input', { bubbles: true }));
                areas[1].value = '结果回家煮了杯热咖啡对吧 / 晚上早点休息啦';
                areas[1].dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            })()""")
            page.wait_for("document.querySelectorAll('[data-tone-obs=hard_sell]').length === 0", timeout=10.0)
            self.assertTrue(page.eval_js("document.getElementById('tone-cmp-details').open"))

            page.go("#/voice", settle="Boolean(document.getElementById('tone-analyzer-card'))")
            en_voice_text = page.eval_js("document.getElementById('tone-box').innerText")
            self.assertIn("Counted wording & measured sound", en_voice_text)
            self.assertIn("220 Hz", en_voice_text)
            self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
