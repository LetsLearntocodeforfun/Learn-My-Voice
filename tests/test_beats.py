"""Tests for the Beat Bank (`sample_beats`): Stage 1B beat storage, wording safety gate, editor References, and draft examples.

What a creator can rely on:
- Stage 1B video replies keep their structured beats (kind, how spoken, delivery cue, timestamps, spoken words,
  English meaning, what is on screen) in `sample_beats`, bounded against hostile input; plain Whisper segment replies
  invent no beat roles.
- Wording safety gate: beats from unconfirmed transcripts (`needs_review=True`) or sponsored videos (`is_sponsored=True`)
  never enter draft prompts or the editor's References panel.
- Editing a sample's text prunes any beat whose spoken words no longer stand in that sample's text while keeping beats
  whose words were left intact; replacing a sample's text or deleting the sample clears its beats; backups carry and
  restore `sample_beats` intact.
- Draft prompts include a bounded, tag-escaped `<creator_beat_examples>` block only when confirmed non-sponsored beats
  exist, and record `beat_ids` and `beat_sample_ids` in `basis`.
- Both `#/library` and the script editor's References panel render beats in Chinese and English and in light and dark
  appearances with zero browser errors and hostile markup escaped.
"""
from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from studio import backup, creator, pipeline, posted, refs, server, video_ingest
from studio.store import (
    BEAT_TYPES,
    MAX_BEAT_DELIVERY_CHARS,
    MAX_BEAT_MEANING_CHARS,
    MAX_BEAT_SPOKEN_CHARS,
    MAX_BEAT_VISUAL_CHARS,
    MAX_BEATS_PER_SAMPLE,
    Store,
)
from tests import browser

MARKUP = "<img src=x onerror=alert(1)>"


def _stage1_reply(*, sponsored: bool = False, extra_beats: list | None = None) -> dict:
    beats = [
        {
            "beat_index": 1,
            "beat_type": "HOOK",
            "start_sec": 0.0,
            "end_sec": 3.6,
            "audio_mode": "TALKING_HEAD",
            "delivery_cue": "[语速轻快]",
            "verbatim_transcript_zh_en": "入冬以后脸特别干嘛 / 所以我换了一款面霜",
            "english_meaning": "My face got super dry in winter so I switched face creams",
            "visual_broll_summary": "Close-up applying cream at bathroom mirror",
        },
        {
            "beat_index": 2,
            "beat_type": "PRODUCT_EXPERIENCE",
            "start_sec": 3.6,
            "end_sec": 8.4,
            "audio_mode": "VOICEOVER",
            "delivery_cue": "[自然聊天]",
            "verbatim_transcript_zh_en": "这瓶 cream 推开很润哈 / 晚上涂完第二天不紧绷",
            "english_meaning": "This cream spreads smoothly and keeps skin comfortable overnight",
            "visual_broll_summary": "Texture swatch on back of hand",
        },
        {
            "beat_index": 3,
            "beat_type": "REALISTIC_CAVEAT",
            "start_sec": 8.4,
            "end_sec": 12.0,
            "audio_mode": "VOICEOVER",
            "verbatim_transcript_zh_en": "不过油皮夏天用可能会觉得有一点厚啦",
            "english_meaning": "Oily skin might find it slightly rich in summer",
            "visual_broll_summary": "Holding jar by window light",
        },
        {
            "beat_index": 4,
            "beat_type": "COUPLE_BANTER",
            "start_sec": 12.0,
            "end_sec": 15.5,
            "audio_mode": "LIVE_CLIP",
            "verbatim_transcript_zh_en": "Did you take my cream? / 你是不是又偷涂我的面霜哈",
            "english_meaning": "Teasing partner about borrowing the face cream",
            "visual_broll_summary": "Partner laughing in hallway",
        },
        {
            "beat_index": 5,
            "beat_type": "CLOSING_CTA",
            "start_sec": 15.5,
            "end_sec": 18.2,
            "audio_mode": "VOICEOVER",
            "verbatim_transcript_zh_en": "干皮姐妹秋冬可以试试看嘛",
            "english_meaning": "Dry-skin friends can give it a try for autumn and winter",
            "visual_broll_summary": "Putting lid back on jar",
        },
    ]
    if extra_beats:
        beats.extend(extra_beats)
    return {
        "content_classification": {"is_sponsored": sponsored, "video_primary_format": "SINGLE_PRODUCT_SHARE"},
        "delivery_observations": {"vocal_energy": "温暖放松的聊天感"},
        "onscreen_flower_texts": [{"timestamp": "00:04", "text": "干皮救星"}],
        "beats": beats,
    }


class BeatIngestAndBoundsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-beats-ingest-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def test_stage1b_reply_stores_structured_beats_while_whisper_segments_invent_none(self) -> None:
        sample = video_ingest.parse_stage1_reply(self.store, _stage1_reply(), title="秋冬面霜实测", source_name="cream.mp4")
        self.assertTrue(sample["needs_review"])
        beats = self.store.list_sample_beats(sample["id"])
        self.assertEqual([b["beat_type"] for b in beats], [
            "HOOK", "PRODUCT_EXPERIENCE", "REALISTIC_CAVEAT", "COUPLE_BANTER", "CLOSING_CTA"
        ])
        self.assertEqual([b["beat_index"] for b in beats], [1, 2, 3, 4, 5])
        self.assertEqual(beats[0]["audio_mode"], "TALKING_HEAD")
        self.assertEqual(beats[0]["delivery"], "[语速轻快]")
        self.assertEqual((beats[0]["start_sec"], beats[0]["end_sec"]), (0.0, 3.6))
        self.assertIn("入冬以后脸特别干嘛", beats[0]["spoken_text"])
        self.assertIn("switched face creams", beats[0]["english_meaning"])
        self.assertIn("bathroom mirror", beats[0]["visual_summary"])
        # LIVE_CLIP beat keeps its [LIVE_CLIP] prefix and matches sample text
        self.assertEqual(beats[3]["audio_mode"], "LIVE_CLIP")
        self.assertTrue(beats[3]["spoken_text"].startswith("[LIVE_CLIP]"))

        # Plain Whisper segment replies have no beat_type and must not fabricate sample_beats rows
        whisper_sample = video_ingest.parse_stage1_reply(
            self.store,
            {"segments": [{"start": 0.0, "end": 3.0, "text": "今天去超市买菜啦"}]},
            title="超市买菜",
            source_name="market.m4a",
        )
        self.assertEqual(self.store.list_sample_beats(whisper_sample["id"]), [])

    def test_hostile_beats_are_bounded_filtered_and_checked_against_sample_text(self) -> None:
        many_beats = [
            {
                "beat_type": "VLOG_STORY",
                "audio_mode": "UNKNOWN_MODE",
                "delivery_cue": "笑 " * 300,
                "start_sec": 10.0,
                "end_sec": 5.0,  # reversed timestamps -> cleared to None
                "verbatim_transcript_zh_en": "第%d句日常碎碎念啦" % i,
                "english_meaning": "Natural English translation for bilingual partner reference",
                "visual_broll_summary": "What is shown on screen during this beat",
            }
            for i in range(MAX_BEATS_PER_SAMPLE + 15)
        ]
        many_beats.insert(0, {
            "beat_type": "NOT_A_REAL_BEAT",
            "verbatim_transcript_zh_en": "这句虽然在正文里但类型不对啦",
        })
        reply = {"beats": many_beats}
        sample = video_ingest.parse_stage1_reply(self.store, reply, title="超长测试")
        stored = self.store.list_sample_beats(sample["id"])
        self.assertEqual(len(stored), MAX_BEATS_PER_SAMPLE)
        first = stored[0]
        self.assertEqual(first["beat_type"], "VLOG_STORY")
        self.assertEqual(first["audio_mode"], "VOICEOVER")
        self.assertIsNone(first["start_sec"])
        self.assertIsNone(first["end_sec"])
        self.assertLessEqual(len(first["delivery"]), MAX_BEAT_DELIVERY_CHARS)
        # Echoed schema hints are stripped rather than stored as real annotations
        self.assertEqual(first["english_meaning"], "")
        self.assertEqual(first["visual_summary"], "")

        # Direct save_sample_beats refuses beats whose spoken words do not stand in the sample's text
        injected = self.store.save_sample_beats(sample["id"], [
            {
                "beat_type": "HOOK",
                "spoken_text": "完全不在原视频里的私货台词",
                "english_meaning": "Hallucinated line" * 50,
                "visual_summary": "Scene " * 100,
            },
            {
                "beat_type": "HOOK",
                "spoken_text": "第0句日常碎碎念啦",
                "english_meaning": "M" * (MAX_BEAT_MEANING_CHARS + 80),
                "visual_summary": "V" * (MAX_BEAT_VISUAL_CHARS + 80),
            },
        ])
        self.assertEqual(len(injected), 1)
        self.assertEqual(injected[0]["spoken_text"], "第0句日常碎碎念啦")
        self.assertEqual(len(injected[0]["english_meaning"]), MAX_BEAT_MEANING_CHARS)
        self.assertEqual(len(injected[0]["visual_summary"]), MAX_BEAT_VISUAL_CHARS)
        self.assertLessEqual(len(injected[0]["spoken_text"]), MAX_BEAT_SPOKEN_CHARS)


class BeatSafetyGateAndLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-beats-gate-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def test_unconfirmed_and_sponsored_beats_never_pass_the_safety_gate(self) -> None:
        organic_video = video_ingest.parse_stage1_reply(self.store, _stage1_reply(sponsored=False), title="非商单面霜")
        sponsored_video = video_ingest.parse_stage1_reply(self.store, _stage1_reply(sponsored=True), title="商单面霜")

        # Confirm the sponsored video right away, leave organic_video unconfirmed at first: neither may pass!
        self.store.update_sample(sponsored_video["id"], {"needs_review": False})
        self.assertEqual(self.store.list_confirmed_organic_beats(), [])

        script = self.store.create_script(
            title="秋冬面霜分享",
            keywords=["面霜"],
            lines=[{"beat_type": "HOOK", "spoken_script_zh": "先写一句开头"}],
        )
        self.assertEqual(refs.for_script(self.store, script["id"])["beats"]["items"], [])

        # Confirming the non-sponsored video lets its beats through; the sponsored video's beats stay excluded
        self.store.update_sample(organic_video["id"], {"needs_review": False})
        confirmed_beats = self.store.list_confirmed_organic_beats()
        self.assertEqual(len(confirmed_beats), 5)
        self.assertTrue(all(b["sample_id"] == organic_video["id"] for b in confirmed_beats))

        # Flipping is_sponsored=True or needs_review=True immediately closes the gate again
        self.store.update_sample(organic_video["id"], {"is_sponsored": True})
        self.assertEqual(self.store.list_confirmed_organic_beats(), [])
        self.store.update_sample(organic_video["id"], {"is_sponsored": False, "needs_review": True})
        self.assertEqual(self.store.list_confirmed_organic_beats(), [])
        self.store.update_sample(organic_video["id"], {"needs_review": False})
        self.assertEqual(len(self.store.list_confirmed_organic_beats()), 5)

    def test_editing_sample_text_prunes_modified_beats_and_deleting_or_replacing_clears_all(self) -> None:
        sample = video_ingest.parse_stage1_reply(self.store, _stage1_reply(), title="面霜记录")
        self.store.update_sample(sample["id"], {"needs_review": False})
        self.assertEqual(len(self.store.list_sample_beats(sample["id"])), 5)

        # Rewrite the HOOK line and remove CLOSING_CTA while keeping the middle three beats intact
        kept_lines = sample["text"].splitlines()[1:4]
        new_text = "\n".join(["入冬以后皮肤干到起皮啦 / 我换掉了旧面霜"] + kept_lines)
        self.store.update_sample(sample["id"], {"text": new_text})
        remaining = self.store.list_sample_beats(sample["id"])
        self.assertEqual([b["beat_type"] for b in remaining], [
            "PRODUCT_EXPERIENCE", "REALISTIC_CAVEAT", "COUPLE_BANTER"
        ])

        # replace_sample_text clears old beats unless new Stage 1B beats are attached
        self.store.replace_sample_text(sample["id"], text="完全重写的一段文字啦", kind="script", needs_review=False)
        self.assertEqual(self.store.list_sample_beats(sample["id"]), [])

        # Re-save a beat, then delete_sample removes all sample_beats rows for that sample
        self.store.save_sample_beats(sample["id"], [{"beat_type": "HOOK", "spoken_text": "完全重写的一段文字啦"}])
        self.assertEqual(len(self.store.list_sample_beats(sample["id"])), 1)
        self.store.delete_sample(sample["id"])
        self.assertEqual(self.store.list_sample_beats(sample["id"]), [])
        self.assertEqual(self.store.list_sample_beats(), [])

    def test_unedited_ai_draft_lines_on_posted_script_never_count_as_creator_beats(self) -> None:
        script = self.store.create_script(
            title="AI 初稿测试",
            from_ai=True,
            lines=[
                {"beat_type": "HOOK", "spoken_script_zh": "综上所述这款面霜非常值得入手"},
                {"beat_type": "VLOG_STORY", "spoken_script_zh": "我自己后来改成顺口的日常话啦"},
            ],
        )
        # Creator rewrites line 2 in their own words, leaves line 1 as AI wrote it, marks posted and confirms
        self.store.update_script(script["id"], {"lines": [
            {"beat_type": "HOOK", "spoken_script_zh": "综上所述这款面霜非常值得入手"},
            {"beat_type": "VLOG_STORY", "spoken_script_zh": "其实我晚上洗完脸随手涂一层就很舒服嘛"},
        ]})
        posted.mark(self.store, script["id"], {"likes": 30, "posted_on": "2026-11-15"})
        item = self.store.get_posted_sample(script["id"])
        assert item is not None
        self.store.update_sample(item["id"], {"needs_review": False})
        self.store.save_sample_beats(item["id"], [
            {"beat_type": "HOOK", "spoken_text": "综上所述这款面霜非常值得入手"},
            {"beat_type": "VLOG_STORY", "spoken_text": "其实我晚上洗完脸随手涂一层就很舒服嘛"},
        ])
        passed = self.store.list_confirmed_organic_beats()
        self.assertEqual([b["beat_type"] for b in passed], ["VLOG_STORY"])

    def test_backup_and_restore_round_trip_preserves_sample_beats(self) -> None:
        sample = video_ingest.parse_stage1_reply(self.store, _stage1_reply(), title="备份段落测试")
        self.store.update_sample(sample["id"], {"needs_review": False})
        original_beats = self.store.list_sample_beats(sample["id"])
        self.assertEqual(len(original_beats), 5)

        archive = self.tmp / "beats.lmvbackup"
        backup.create(self.store, archive)

        fresh_store = Store(self.tmp / "restored_data")
        mgr = backup.Backups(fresh_store)
        arrived = self.tmp / "arrived.part"
        shutil.copyfile(str(archive), str(arrived))
        staged = mgr.stage(arrived)
        mgr.restore(staged["restore_id"])
        self.assertEqual(fresh_store.list_sample_beats(sample["id"]), original_beats)


class BeatDraftPromptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-beats-draft-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")

    def test_draft_prompt_omits_beat_block_until_confirmed_organic_beats_exist_and_records_basis(self) -> None:
        # With only a plain script sample (no beats) and an unconfirmed video, no beat block or basis keys appear
        self.store.create_sample(title="手写脚本", text="今天去买咖啡啦 / 顺便逛了一下超市嘛")
        unconfirmed = video_ingest.parse_stage1_reply(
            self.store,
            _stage1_reply(extra_beats=[
                {
                    "beat_type": "TRANSITION_BRIDGE",
                    "start_sec": 18.2,
                    "end_sec": 20.0,
                    "audio_mode": "VOICEOVER",
                    "delivery_cue": "[UNCONFIRMED_DELIVERY_PROSE]",
                    "verbatim_transcript_zh_en": "说到面霜顺便提一嘴 <护手霜> 啦",
                    "english_meaning": "UNCONFIRMED_ENGLISH_PROSE <script>alert(1)</script>",
                    "visual_broll_summary": "UNCONFIRMED_VISUAL_PROSE",
                },
                {
                    "beat_type": "COUPLE_BANTER",
                    "start_sec": 20.0,
                    "end_sec": 22.5,
                    "audio_mode": "TALKING_HEAD",
                    "verbatim_transcript_zh_en": "某人每次都要顺手抹一点我的护手霜哈",
                    "english_meaning": "UNCONFIRMED_BANTER_MEANING",
                    "visual_broll_summary": "UNCONFIRMED_BANTER_VISUAL",
                },
            ]),
            title="秋冬面霜分享 " + MARKUP,
        )
        _, user_before, basis_before = pipeline._build_draft(
            self.store, notes="聊聊秋冬面霜", target_sec=60, video_format="SINGLE_PRODUCT_SHARE", keywords=["面霜"]
        )
        self.assertNotIn("<creator_beat_examples>", user_before)
        self.assertNotIn("beat_ids", basis_before)
        self.assertNotIn("beat_sample_ids", basis_before)

        # Confirm the video: solo creator (no on_camera_partner) on SINGLE_PRODUCT_SHARE omits COUPLE_BANTER,
        # escapes angle brackets inside JSON, and records beat_ids + beat_sample_ids in basis
        self.store.save_creator(creator.normalize({"name": "小满", "pronoun": "she", "on_camera_partner": ""}))
        self.store.update_sample(unconfirmed["id"], {"needs_review": False})
        brief = pipeline.draft_brief(
            self.store, notes="聊聊秋冬面霜", target_sec=60, video_format="SINGLE_PRODUCT_SHARE", keywords=["面霜"]
        )
        user_after = brief["user"]
        basis_after = brief["basis"]
        self.assertNotIn("semantic", user_after.lower())
        self.assertIn("example_snippets", user_after)
        self.assertIn("<creator_beat_examples>", user_after)
        self.assertLess(user_after.index("<creator_beat_examples>"), user_after.index("<script_request>"))
        # Raw angle brackets never stand unescaped inside the fenced JSON block
        block_json = user_after.split("<creator_beat_examples>", 1)[1].split("</creator_beat_examples>", 1)[0]
        self.assertNotIn("<护手霜>", block_json)
        self.assertIn(r"\u003c护手霜\u003e", block_json)
        for unconfirmed_prose in (
            "UNCONFIRMED_DELIVERY_PROSE",
            "UNCONFIRMED_ENGLISH_PROSE",
            "UNCONFIRMED_VISUAL_PROSE",
            "Did you take my cream?",
        ):
            self.assertNotIn(unconfirmed_prose, block_json)
        parsed_block = json.loads("\n".join(
            ln for ln in block_json.strip().splitlines() if not ln.strip().startswith("<!--")
        ))
        for item in parsed_block:
            self.assertEqual(set(item.keys()), {"beat_type", "audio_mode", "spoken_text"})
            self.assertNotEqual(item["audio_mode"], "LIVE_CLIP")
        roles = [b["beat_type"] for b in parsed_block]
        self.assertIn("HOOK", roles)
        self.assertIn("PRODUCT_EXPERIENCE", roles)
        self.assertNotIn("COUPLE_BANTER", roles)
        self.assertEqual(len(basis_after["beat_ids"]), len(parsed_block))
        self.assertEqual(basis_after["beat_sample_ids"], [unconfirmed["id"]])

        # Applying a manual/MCP draft reply restores the recorded beat_ids on the saved script
        saved_script = pipeline.apply_draft(
            self.store,
            {"script_lines": [{"beat_type": "HOOK", "spoken_script_zh": "秋冬干皮面霜我最近只用这一罐啦"}]},
            notes="聊聊秋冬面霜",
            target_sec=60,
            video_format="SINGLE_PRODUCT_SHARE",
            keywords=["面霜"],
            brief_id=brief["brief_id"],
        )
        self.assertEqual(saved_script["basis"]["beat_ids"], basis_after["beat_ids"])
        self.assertEqual(saved_script["basis"]["beat_sample_ids"], [unconfirmed["id"]])

        # For COUPLE_VLOG (or when on_camera_partner is set), TALKING_HEAD/VOICEOVER COUPLE_BANTER is eligible,
        # while LIVE_CLIP beats remain excluded from <creator_beat_examples>.
        _, couple_user, _ = pipeline._build_draft(
            self.store, notes="周末跟搭档逛街", target_sec=60, video_format="COUPLE_VLOG"
        )
        couple_block = couple_user.split("<creator_beat_examples>", 1)[1].split("</creator_beat_examples>", 1)[0]
        couple_beats = json.loads("\n".join(
            ln for ln in couple_block.strip().splitlines() if not ln.strip().startswith("<!--")
        ))
        self.assertIn("COUPLE_BANTER", [b["beat_type"] for b in couple_beats])
        self.assertTrue(all(b["audio_mode"] != "LIVE_CLIP" for b in couple_beats))
        self.assertNotIn("Did you take my cream?", couple_block)


class BeatPagesAndRefsBrowserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-beats-ui-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.store.save_creator(creator.normalize({"name": "小满", "pronoun": "they"}))

    def test_http_samples_and_refs_endpoints_expose_beats_and_prune_on_edit(self) -> None:
        httpd = server.make_server(0, self.store)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]

        def api_call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                headers = {"Host": "127.0.0.1:%d" % port}
                raw = b""
                if payload is not None:
                    raw = json.dumps(payload).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                    headers["Content-Length"] = str(len(raw))
                conn.request(method, path, body=raw if payload is not None else None, headers=headers)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()

        st_ing, ing = api_call("POST", "/api/manual/reply", {
            "task": "video_ingest",
            "title": "面霜实测 " + MARKUP,
            "reply": json.dumps(_stage1_reply()),
        })
        self.assertEqual(st_ing, 201, ing)
        sample_id = ing["sample"]["id"]
        self.assertEqual(len(ing["sample"]["beats"]), 5)

        st_script, script = api_call("POST", "/api/scripts", {
            "title": "面霜选购",
            "keywords": ["面霜"],
            "lines": [
                {"beat_type": "HOOK", "spoken_script_zh": "秋冬面霜怎么选嘛"},
                {"beat_type": "REALISTIC_CAVEAT", "spoken_script_zh": "先说一个小缺点哈"},
            ],
        })
        self.assertEqual(st_script, 201)

        # Unconfirmed: refs returns 0 beats
        _, refs_before = api_call("GET", f"/api/scripts/{script['id']}/refs")
        self.assertEqual(refs_before["beats"]["items"], [])
        self.assertEqual(refs_before["beats"]["wanted_types"], ["HOOK", "REALISTIC_CAVEAT"])

        # Confirm via PUT /api/samples/<id>: refs now returns matching HOOK and REALISTIC_CAVEAT beats
        st_put, confirmed = api_call("PUT", f"/api/samples/{sample_id}", {"needs_review": False, "likes": 120})
        self.assertEqual(st_put, 200)
        self.assertEqual(len(confirmed["beats"]), 5)

        _, refs_after = api_call("GET", f"/api/scripts/{script['id']}/refs")
        self.assertEqual([b["beat_type"] for b in refs_after["beats"]["items"]], ["HOOK", "REALISTIC_CAVEAT"])
        self.assertTrue(all(b["on_topic"] for b in refs_after["beats"]["items"]))
        self.assertTrue(all(t in BEAT_TYPES for t in refs_after["beats"]["wanted_types"]))

    @unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
    def test_library_and_editor_references_render_beats_safely_in_zh_en_and_light_dark(self) -> None:
        reply = _stage1_reply()
        reply["beats"][0]["visual_broll_summary"] = "Mirror shot " + MARKUP
        sample = video_ingest.parse_stage1_reply(self.store, reply, title="面霜视频 " + MARKUP)
        self.store.update_sample(sample["id"], {"needs_review": False, "likes": 200, "collects": 90})
        script = self.store.create_script(
            title="秋冬面霜草稿",
            keywords=["面霜"],
            lines=[
                {"beat_type": "HOOK", "spoken_script_zh": "今天聊聊秋冬面霜嘛"},
                {"beat_type": "PRODUCT_EXPERIENCE", "spoken_script_zh": "质地真的蛮舒服哈"},
            ],
        )
        for lang_code, theme, lib_phrase, rf_heading in (
            ("zh", "light", "段落拆解 · 5 段", "同段落参考"),
            ("en", "dark", "5 video beats", "Matching beats"),
        ):
            with browser.open_app(self.store, lang=lang_code, theme=theme) as page:
                page.go("#/library", "document.querySelectorAll('[data-beat-row]').length === 5")
                summary_txt = page.eval_js("document.querySelector('.sample-beats summary').textContent")
                self.assertIn(lib_phrase, summary_txt)
                # Hostile markup in visual_summary and title is rendered as literal text, never an <img> element
                self.assertEqual(page.eval_js("document.querySelectorAll('.sample-beats img').length"), 0)
                self.assertIn(MARKUP, page.eval_js("document.querySelector('.sample-beats').textContent"))

                page.go(
                    f"#/s/{script['id']}",
                    "Boolean(document.getElementById('rf-panel') || document.querySelector('[data-rf=\"open\"]'))",
                )
                page.eval_js("const b = document.querySelector('[data-rf=\"open\"]'); if (b) b.click();")
                page.wait_for("document.querySelectorAll('[data-rf-part=\"beats\"] [data-rf-beat]').length === 2")
                sec_text = page.eval_js("document.querySelector('[data-rf-part=\"beats\"]').textContent")
                self.assertIn(rf_heading, sec_text)
                self.assertIn("入冬以后脸特别干嘛", sec_text)
                self.assertIn(MARKUP, sec_text)
                self.assertEqual(page.eval_js("document.querySelectorAll('[data-rf-part=\"beats\"] img').length"), 0)
                self.assertEqual(page.problems, [])


if __name__ == "__main__":
    unittest.main()
