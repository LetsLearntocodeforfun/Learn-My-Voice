"""Tests for the creator profile: validation, storage, the routes, the never-say list, and neutral prompts."""
import copy
import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from studio import checks, creator, guardrails, llm, mcp_server, pipeline, server, trends
from studio.store import Store
from tests.test_features import DNA_REPLY, HER_SCRIPT

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
STAGES = (pipeline.STAGE2, pipeline.STAGE4, pipeline.STAGE6, pipeline.STAGE7)
ASSUMES = re.compile(r"\b(she|her|hers|herself|husband|wife|couple)\b", re.I)   # key names such as authentic_her do not match

FORM = {   # what the setup page sends
    "name": "  小满 ", "pronoun": "she", "channel": "小满的日常", "niches": "护肤、日常 Vlog，#好物分享, 护肤",
    "about": "在上海，拍通勤和周末。\r\n喜欢慢慢讲。", "partner": {"called": "我老公", "language": "英语"},
    "never": ["宝子们", " 走不动 ", "", "宝子们"],
}
FORM_BLOCK = {   # what the prompts must then receive
    "profile_set": True, "name": "小满", "pronoun": "she", "channel": "小满的日常",
    "niches": ["护肤", "日常 Vlog", "好物分享"], "about": "在上海，拍通勤和周末。\n喜欢慢慢讲。",
    "on_camera_partner": {"referred_to_as": "我老公", "speaks": "英语"}, "never_say": ["宝子们", "走不动"],
}
CLEAN_LINE = {"spoken_script_zh": "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动哈"}   # 21 units: fits 5 seconds


def code_of(body, previous=None):
    try:
        creator.normalize(body, previous)
    except ValueError as exc:
        return str(exc)
    return None


class NormalizeTest(unittest.TestCase):
    def test_form_is_cleaned_and_stamped(self):
        self.assertEqual(creator.normalize(FORM, now=100.0), {
            "name": "小满", "pronoun": "she", "channel": "小满的日常", "niches": ["护肤", "日常 Vlog", "好物分享"],
            "about": "在上海，拍通勤和周末。\n喜欢慢慢讲。", "partner": {"called": "我老公", "language": "英语"},
            "never": ["宝子们", "走不动"], "created_at": 100.0, "updated_at": 100.0})

    def test_only_a_name_is_required(self):
        bare = creator.normalize({"name": "阿远"}, now=5.0)
        self.assertEqual(bare, {"name": "阿远", "pronoun": "they", "channel": "", "niches": [], "about": "",
                                "partner": None, "never": [], "created_at": 5.0, "updated_at": 5.0})
        self.assertEqual(creator.normalize({"name": "阿远", "pronoun": " He "})["pronoun"], "he")
        for body in ({}, {"name": "   "}, {"name": 5}, {"name": None}):
            self.assertEqual(code_of(body), "name_required")
        for body in (None, "小满", ["小满"]):
            self.assertEqual(code_of(body), "bad_profile")

    def test_bad_input_raises_a_short_code(self):
        for field, value, code in [
            ("pronoun", "xe", "bad_pronoun"), ("pronoun", 5, "bad_pronoun"), ("channel", ["x"], "bad_channel"),
            ("niches", 5, "bad_niches"), ("niches", ["护肤", 5], "bad_niches"), ("about", 5, "bad_about"),
            ("partner", 5, "bad_partner"), ("partner", {"called": 5}, "bad_partner"),
            ("partner", {"called": "老公", "language": ["英语"]}, "bad_partner"),
            ("never", 5, "bad_never"), ("never", ["宝子们", None], "bad_never"),
        ]:
            self.assertEqual(code_of({"name": "小满", field: value}), code, (field, value))

    def test_lengths_are_capped(self):
        out = creator.normalize({
            "name": "名" * 60, "channel": "号" * 80, "about": "介" * 600, "niches": ["领域%d" % i for i in range(12)] + ["长" * 30],
            "partner": {"called": "称" * 30, "language": "语" * 40}})
        self.assertEqual([len(out[k]) for k in ("name", "channel", "about", "niches")], [40, 60, 500, 8])
        self.assertEqual((len(out["partner"]["called"]), len(out["partner"]["language"])), (20, 30))
        self.assertEqual(creator.normalize({"name": "小满", "niches": "长" * 30})["niches"], ["长" * 20])

    def test_partner_is_optional_and_may_be_given_as_text(self):
        for value in (None, "", "   ", {}, {"called": " "}, {"called": None, "language": "英语"}):
            self.assertIsNone(creator.normalize({"name": "小满", "partner": value})["partner"], value)
        self.assertEqual(creator.normalize({"name": "小满", "partner": " 室友 "})["partner"], {"called": "室友", "language": ""})
        self.assertEqual(creator.normalize({"name": "小满", "partner": {"called": "我妈", "language": None}})["partner"],
                         {"called": "我妈", "language": ""})

    def test_lists_take_a_list_or_one_separated_string(self):
        out = creator.normalize({"name": "小满", "niches": ["护肤", "穿搭, 探店", "＃护肤"], "never": "宝子们\n绝绝子、家人们；真的会谢"})
        self.assertEqual(out["niches"], ["护肤", "穿搭", "探店"])
        self.assertEqual(out["never"], ["宝子们", "绝绝子", "家人们", "真的会谢"])

    def test_never_phrases_must_be_few_and_checkable(self):
        for phrase in ("（笑）", "…", "...", "XX", "【】"):                    # nothing the linter could ever match
            self.assertEqual(code_of({"name": "小满", "never": ["宝子们", phrase]}), "never_uncheckable", phrase)
        # a cue in square brackets can be matched now that bans are read inside cue brackets: stored as what is enforced
        self.assertEqual(creator.normalize({"name": "小满", "never": ["[轻笑]"]})["never"], ["轻笑"])
        many = ["词%d" % i for i in range(creator.MAX_NEVER + 1)]
        self.assertEqual(code_of({"name": "小满", "never": many}), "too_many_never")
        self.assertEqual(len(creator.normalize({"name": "小满", "never": many[:-1]})["never"]), creator.MAX_NEVER)
        long_phrase = "这句话我是真的一个字都不想再说第二遍了因为实在是太尴尬太做作太不像我平时说话的样子了啊"
        kept = creator.normalize({"name": "小满", "never": [long_phrase]})["never"]
        self.assertEqual(kept, [long_phrase[:creator.MAX_NEVER_CHARS]])       # cut, and still matches the full phrase
        self.assertIn(kept[0], long_phrase)

    def test_never_phrases_are_stored_as_the_linter_enforces_them(self):
        typed = ["宝子们（太腻了）", "谁懂啊…", "哈喽大家好我是XX", "不仅…而且", "宝子们"]
        stored = creator.normalize({"name": "小满", "never": typed})["never"]
        self.assertEqual(stored, ["宝子们", "谁懂啊", "哈喽大家好我是", "不仅…而且"])   # notes and placeholders are not matched, so not kept
        self.assertEqual(creator.normalize({"name": "小满", "never": stored})["never"], stored)   # saving again changes nothing

    def test_update_keeps_what_was_not_sent(self):
        first = creator.normalize(FORM, now=100.0)
        second = creator.normalize({"never": ["家人们"], "created_at": 1, "is_set": False, "unknown": True}, first, now=200.0)
        self.assertEqual(second, dict(first, never=["家人们"], updated_at=200.0))
        self.assertEqual(creator.normalize({"partner": None, "channel": ""}, second, now=300.0),
                         dict(second, partner=None, channel="", updated_at=300.0))
        self.assertEqual(code_of({"name": ""}, first), "name_required")       # a name cannot be cleared
        self.assertEqual(code_of({"pronoun": "she"}, {"name": ""}), "name_required")   # nothing usable was stored before

    def test_is_set(self):
        for value in (None, {}, {"name": ""}, {"name": "  "}, {"name": 5}, "小满", ["小满"]):
            self.assertFalse(creator.is_set(value), value)
        self.assertTrue(creator.is_set(creator.normalize(FORM)))


class PromptBlockTest(unittest.TestCase):
    def test_usable_before_setup(self):
        for value in (None, {}, {"name": ""}, "garbage"):
            block = creator.prompt_block(value)
            self.assertEqual((block["profile_set"], block["name"], block["pronoun"], block["on_camera_partner"]),
                             (False, None, "they", None))
            self.assertEqual((block["niches"], block["never_say"]), ([], []))
            self.assertIn("the creator", block["note"])
            json.dumps(block)
        self.assertEqual(creator.english_name(None), "the creator")

    def test_after_setup(self):
        who = creator.normalize(FORM)
        self.assertEqual(creator.prompt_block(who), FORM_BLOCK)
        alone = creator.prompt_block(creator.normalize({"name": "阿远", "pronoun": "he", "partner": "室友"}))
        self.assertEqual((alone["channel"], alone["about"], alone["on_camera_partner"]),
                         (None, None, {"referred_to_as": "室友", "speaks": None}))
        self.assertEqual(creator.english_name(who), "小满")

    def test_a_hand_edited_file_does_not_break_readers(self):
        odd = {"name": " 小满 ", "pronoun": "xe", "niches": "护肤", "never": "宝子们", "partner": "老公", "about": 5}
        block = creator.prompt_block(odd)
        self.assertEqual((block["name"], block["pronoun"], block["niches"], block["never_say"], block["on_camera_partner"],
                          block["about"]), ("小满", "they", [], [], None, None))
        self.assertEqual(creator.never_phrases(dict(odd, never=["宝子们", 5, " "])), ["宝子们"])
        self.assertEqual(creator.apply_never(EXAMPLE_DNA, odd), EXAMPLE_DNA)
        self.assertEqual(creator.for_page(odd), {"name": "小满", "pronoun": "they", "channel": "", "niches": [], "about": "",
                                                 "partner": None, "never": [], "is_set": True})

    def test_page_payload_always_has_every_field(self):
        empty = {"name": "", "pronoun": "they", "channel": "", "niches": [], "about": "", "partner": None, "never": [],
                 "is_set": False}
        self.assertEqual(creator.for_page(None), empty)
        self.assertEqual(creator.for_page({"name": " "}), empty)
        who = creator.normalize(FORM, now=7.0)
        self.assertEqual(creator.for_page(who), dict(who, is_set=True))
        self.assertEqual(creator.for_page({"name": "小满", "extra": 1}), dict(empty, name="小满", is_set=True))


class NeverListTest(unittest.TestCase):
    def test_apply_never_returns_a_copy_and_leaves_the_profile_alone(self):
        who = creator.normalize({"name": "小满", "never": ["走不动", "绝绝子"]})
        original = copy.deepcopy(EXAMPLE_DNA)
        merged = creator.apply_never(EXAMPLE_DNA, who)
        self.assertEqual(EXAMPLE_DNA, original)
        banned = merged["anti_voice_blacklist"]["banned_phrases_zh"]
        self.assertEqual(banned, original["anti_voice_blacklist"]["banned_phrases_zh"] + ["走不动"])   # 绝绝子 was there already
        merged["persona_summary"]["one_line_essence_zh"] = "changed"
        self.assertEqual(EXAMPLE_DNA, original)                                # a deep copy, not a shared one
        untouched = creator.apply_never(EXAMPLE_DNA, None)
        self.assertEqual(untouched, original)
        self.assertIsNot(untouched, EXAMPLE_DNA)

    def test_profiles_without_a_ban_list_get_one(self):
        who = creator.normalize({"name": "小满", "never": ["走不动"]})
        for dna in ({}, None, {"anti_voice_blacklist": "broken"}, {"anti_voice_blacklist": {"banned_phrases_zh": None}}):
            self.assertEqual(creator.apply_never(dna, who)["anti_voice_blacklist"]["banned_phrases_zh"], ["走不动"])

    def test_typed_phrases_are_flagged_whatever_the_voice_profile(self):
        who = creator.normalize({"name": "小满", "never": ["走不动", "真的会谢"]})
        script = {"script_lines": [{"line_no": 1, "spoken_script_zh": "结果某人又走不动哈 / 我真的会谢"}]}

        def errors(dna):
            return [f.matched for f in guardrails.lint_script(script, dna, lexicon={}) if f.severity == guardrails.ERROR]

        for dna in (EXAMPLE_DNA, {}, {"anti_voice_blacklist": {"banned_phrases_zh": ["绝绝子"]}}):
            self.assertEqual((errors(dna), errors(creator.apply_never(dna, who))), ([], ["走不动", "真的会谢"]))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def set_creator(self, **changes):
        who = creator.normalize(dict(FORM, **changes), previous=self.store.load_creator())
        self.store.save_creator(who)
        return who

    def lint(self, *spoken, target_sec=5):
        lines = [{"line_no": i, "spoken_script_zh": text} for i, text in enumerate(spoken, start=1)]
        return checks.lint_payload(self.store, lines, target_sec)


class StorageTest(StoreCase):
    def test_profile_is_kept_beside_the_settings(self):
        self.assertIsNone(self.store.load_creator())
        who = self.set_creator()
        self.assertEqual(self.store.creator_path, self.tmp / "creator.json")
        self.assertEqual(self.store.creator_path.parent, self.store.config_path.parent)
        self.assertEqual(Store(self.tmp).load_creator(), who)
        self.assertIn("小满", self.store.creator_path.read_text(encoding="utf-8"))   # readable, not \\u escapes

    def test_unreadable_file_counts_as_not_set(self):
        for content in (b"not json", b"[1, 2]", b"\xff\xfe"):
            self.store.creator_path.write_bytes(content)
            self.assertIsNone(self.store.load_creator())
            self.assertEqual(self.lint(CLEAN_LINE["spoken_script_zh"])["findings"], [])


class NeverIsEnforcedTest(StoreCase):
    def test_flagged_before_any_voice_is_learned_and_not_stored_in_the_voice_profile(self):
        self.assertEqual(self.lint(CLEAN_LINE["spoken_script_zh"])["findings"], [])
        self.set_creator()
        result = self.lint(CLEAN_LINE["spoken_script_zh"])
        self.assertTrue(result["dna_is_example"])                              # nothing has been learned yet
        self.assertEqual([(f["line_no"], f["category"], f["severity"], f["matched"]) for f in result["findings"]],
                         [(1, "GENERIC_XHS_SLOP", "error", "走不动")])
        self.assertEqual(result["findings"][0]["message_zh"], "「走不动」是你自己列的不说的话")
        self.assertEqual(result["findings"][0]["message_en"], "\"走不动\" is on the list of phrases 小满 refuses to say")
        self.assertFalse(self.store.dna_path.exists())                         # the check wrote nothing
        self.assertEqual(self.store.load_dna()[0], EXAMPLE_DNA)
        self.set_creator(never=[])
        self.assertEqual(self.lint(CLEAN_LINE["spoken_script_zh"])["findings"], [])   # taken off the list: no longer enforced

    def test_a_phrase_with_a_gap_is_also_reported_as_the_creators_own(self):
        self.set_creator(never=["真的…绝了"])
        findings = self.lint("这个真的太绝了嘛", target_sec=None)["findings"]
        self.assertEqual([(f["severity"], f["matched"], f["message_zh"]) for f in findings],
                         [("error", "真的太绝了", "「真的太绝了」是你自己列的不说的话")])

    def test_still_enforced_and_still_separate_after_a_voice_is_learned(self):
        self.set_creator()
        self.store.create_sample(title="超市那天", text=HER_SCRIPT)
        proposal = pipeline.propose_voice(self.store, copy.deepcopy(DNA_REPLY))
        self.assertFalse(self.store.dna_path.exists())                         # a learned profile is a proposal until accepted
        pipeline.approve_voice(self.store, created_at=proposal["created_at"])
        pipeline.set_speaking_rate(self.store, 260)
        stored = self.store.dna_path.read_text(encoding="utf-8")
        self.assertNotIn("走不动", stored)
        self.assertNotIn("宝子们", json.dumps(json.loads(stored)["anti_voice_blacklist"], ensure_ascii=False))
        history = "".join(p.read_text(encoding="utf-8") for p in self.store.dna_history_dir.glob("*.json"))
        self.assertNotIn("走不动", history)
        result = self.lint("结果又走不动哈", target_sec=None)
        self.assertFalse(result["dna_is_example"])
        self.assertIn(("GENERIC_XHS_SLOP", "error", "走不动"),
                      [(f["category"], f["severity"], f["matched"]) for f in result["findings"]])

    def test_english_messages_name_the_creator(self):
        def english():
            return {f["matched"]: f["message_en"] for f in self.lint("这个真的绝绝子 / 大油皮姐妹看过来")["findings"]}

        self.assertEqual(english()["绝绝子"], "\"绝绝子\" is generic influencer wording, not the creator's voice")
        self.assertEqual(english()["姐妹"], "\"姐妹\" is not something the creator usually says; please check")
        self.set_creator(name="阿远", pronoun="he")
        self.assertEqual(english()["绝绝子"], "\"绝绝子\" is generic influencer wording, not 阿远's voice")
        self.assertEqual(english()["姐妹"], "\"姐妹\" is not something 阿远 usually says; please check")
        self.assertIsNone(ASSUMES.search(" ".join(en for _, en in checks._MESSAGES.values())))

    def test_trend_keywords_containing_a_refused_phrase_are_not_offered(self):
        self.store.upsert_trends([{"keyword": "闺蜜下午茶"}, {"keyword": "秋冬护肤"}])
        usable = lambda: {t["keyword"]: (t["flags"], t["usable"]) for t in trends.payload(self.store)["trends"]}
        self.assertEqual(usable(), {"闺蜜下午茶": ([], True), "秋冬护肤": ([], True)})
        self.set_creator(never=["闺蜜"])
        self.assertEqual(usable(), {"闺蜜下午茶": (["voice"], False), "秋冬护肤": ([], True)})


class PromptTest(StoreCase):
    def prompts(self):
        return {
            "voice": pipeline.voice_prompt(self.store),
            "draft": pipeline.draft_prompt(self.store, notes="再去一次超市", target_sec=30, video_format="COUPLE_VLOG"),
            "ideas": pipeline.ideas_prompt(self.store),
            "trends": pipeline.trends_prompt(self.store, focus="护肤"),
        }

    @staticmethod
    def profile_block(text):
        """The JSON in the <creator_profile> block of a rendered prompt; prose mentions of the tag are skipped."""
        block = text.split("<creator_profile>\n")[1].split("</creator_profile>")[0]
        return json.loads(block.split("-->")[1])

    def test_templates_assume_nothing_and_keep_their_markers(self):
        for stage in STAGES:
            system, user = pipeline.load_prompt_pair(stage)
            self.assertIsNone(ASSUMES.search(system), stage)
            self.assertIsNone(ASSUMES.search(user), stage)
            self.assertIn("<creator_profile>", system, stage)
            self.assertEqual(user.count("{{creator_profile_json}}"), 1, stage)
        for stage, marker in ((pipeline.STAGE2, "sociolinguist"), (pipeline.STAGE4, "ghostwriting"),
                              (pipeline.STAGE6, "content strategist")):
            for other in STAGES:
                self.assertEqual(marker in pipeline.load_prompt_pair(other)[0], other == stage, (marker, other))
        voice_schema = pipeline.load_prompt_pair(pipeline.STAGE2)[1]
        for key in ("husband_and_family_references", "spoken_nicknames_zh_en", "couple_dynamic_tone", "authentic_her",
                    "learned_from_her_edits"):                                 # stored profiles and the linter read these
            self.assertIn('"%s"' % key, voice_schema)
        # the draft prompt tells the model to recognise the bundled sample profile by this tag and not copy its persona
        self.assertTrue(EXAMPLE_DNA["profile_version"].endswith("-example"))
        self.assertIn("ends in `-example`", pipeline.load_prompt_pair(pipeline.STAGE4)[0])

    def test_every_prompt_renders_before_and_after_setup(self):
        self.store.create_sample(title="超市那天", text=HER_SCRIPT, metrics={"views": 2000, "likes": 150})
        for name, (system, user) in self.prompts().items():
            self.assertNotIn("{{", system + user, name)
            block = self.profile_block(user)
            if name == "trends":      # goes to a web-searching AI: the niches, and nothing else from the profile
                self.assertEqual(block, {"niches": []})
                continue
            self.assertEqual((block["profile_set"], block["name"], block["pronoun"]), (False, None, "they"), name)
        self.set_creator()
        for name, (system, user) in self.prompts().items():
            self.assertNotIn("{{", system + user, name)
            expected = {"niches": FORM_BLOCK["niches"]} if name == "trends" else FORM_BLOCK
            self.assertEqual(self.profile_block(user), expected, name)

    def test_draft_prompt_bans_refused_phrases_without_touching_the_stored_profile(self):
        self.store.create_sample(title="超市那天", text=HER_SCRIPT)
        self.set_creator()
        _, user = pipeline.draft_prompt(self.store, notes="再去一次超市", target_sec=30, video_format="VLOG_VOICEOVER")
        shown = json.loads(user.split("<creator_voice_dna>")[1].split("</creator_voice_dna>")[0])
        self.assertEqual(shown["anti_voice_blacklist"]["banned_phrases_zh"][-1], "走不动")   # 宝子们 was already banned
        self.assertEqual(shown["anti_voice_blacklist"]["banned_phrases_zh"].count("宝子们"), 1)
        self.assertFalse(self.store.dna_path.exists())
        self.assertIn('"creator_own_script"', user)                            # the creator's scripts, labelled neutrally
        self.assertNotIn("her_own_script", user)

    def test_profile_niches_are_enough_for_a_trend_lookup(self):
        self.set_creator(niches=[])
        with self.assertRaises(llm.LLMError) as ctx:
            pipeline.trends_prompt(self.store)
        self.assertEqual(ctx.exception.code, "no_focus")
        self.assertIsNone(ASSUMES.search(str(ctx.exception)))
        self.set_creator(niches="护肤、探店")
        _, user = pipeline.trends_prompt(self.store)
        self.assertEqual(self.profile_block(user)["niches"], ["护肤", "探店"])
        self.assertIn('"typed_focus": null', user)

    def test_mcp_briefs_and_checks_use_the_profile(self):
        self.store.create_sample(title="超市那天", text=HER_SCRIPT, metrics={"views": 2000, "likes": 150})
        self.set_creator()

        def tool(name, **arguments):
            result = mcp_server.call_tool(self.store, name, arguments)
            text = result["content"][0]["text"]
            self.assertFalse(result["isError"], text)
            return json.loads(text) if text.startswith("{") else text

        for name, arguments in (("get_script_brief", {"notes": "再去一次超市"}), ("get_voice_learning_brief", {}),
                                ("get_topic_ideas_brief", {})):
            brief = tool(name, **arguments)
            self.assertNotIn("{{", brief, name)
            self.assertEqual(self.profile_block(brief), FORM_BLOCK, name)
        check = tool("check_script", script_lines=[CLEAN_LINE], target_duration_sec=5)
        self.assertEqual([f["matched"] for f in check["findings"]], ["走不动"])
        voice = tool("get_voice_profile")
        self.assertEqual(voice["creator"], FORM_BLOCK)
        self.assertEqual(voice["profile"], EXAMPLE_DNA)                         # handed out unmerged, so it can be saved back safely

    def test_mcp_text_assumes_nothing(self):
        public = json.dumps([t["public"] for t in mcp_server.TOOLS.values()], ensure_ascii=False)
        self.assertIsNone(ASSUMES.search(public))
        self.assertIsNone(ASSUMES.search(mcp_server.INSTRUCTIONS))
        self.store.create_script(title="t", lines=[CLEAN_LINE], target_sec=5, from_ai=True)   # update_script only revises an AI draft
        script_id = self.store.list_scripts()[0]["id"]
        for name, arguments in (("list_library", {}), ("list_trends", {}), ("list_scripts", {}),
                                ("update_script", {"script_id": script_id, "script_lines": [CLEAN_LINE]}),
                                ("export_jianying_kit", {"script_id": script_id})):
            result = mcp_server.call_tool(self.store, name, arguments)
            self.assertFalse(result["isError"], name)
            self.assertIsNone(ASSUMES.search(result["content"][0]["text"]), name)


class CreatorServerTest(unittest.TestCase):
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

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        res = conn.getresponse()
        payload = json.loads(res.read().decode("utf-8"))
        conn.close()
        return res.status, payload

    def test_read_save_and_update(self):
        empty = {"name": "", "pronoun": "they", "channel": "", "niches": [], "about": "", "partner": None, "never": [],
                 "is_set": False}
        self.assertEqual(self.call("GET", "/api/creator"), (200, empty))
        self.assertEqual(self.call("GET", "/api/state")[1]["creator"], empty)
        self.assertEqual(self.call("PUT", "/api/creator", {"pronoun": "she"}),
                         (400, {"error": "name_required", "message": "name_required"}))
        self.assertEqual(self.call("PUT", "/api/creator", dict(FORM, pronoun="xe"))[1]["error"], "bad_pronoun")
        self.assertEqual(self.call("PUT", "/api/creator", dict(FORM, never=["（笑）"]))[1]["error"], "never_uncheckable")
        self.assertFalse(self.store.creator_path.exists())                     # refused input saves nothing

        status, saved = self.call("PUT", "/api/creator", FORM)
        self.assertEqual((status, saved["name"], saved["pronoun"], saved["is_set"]), (200, "小满", "she", True))
        self.assertEqual((saved["niches"], saved["partner"], saved["never"]),
                         (["护肤", "日常 Vlog", "好物分享"], {"called": "我老公", "language": "英语"}, ["宝子们", "走不动"]))
        self.assertEqual(self.call("GET", "/api/creator")[1], saved)
        self.assertEqual(self.call("GET", "/api/state")[1]["creator"], saved)

        # the setup page sends back what it read, with one phrase added
        _, more = self.call("PUT", "/api/creator", dict(saved, never=saved["never"] + ["家人们"]))
        self.assertEqual((more["never"], more["created_at"], more["name"]), (["宝子们", "走不动", "家人们"], saved["created_at"], "小满"))
        _, alone = self.call("PUT", "/api/creator", {"partner": None})        # only what changed
        self.assertEqual((alone["partner"], alone["name"], alone["never"]), (None, "小满", more["never"]))

    def test_a_typed_phrase_is_flagged_in_the_editor_and_in_copy_and_paste_prompts(self):
        check = {"lines": [CLEAN_LINE], "target_sec": 5}
        self.assertEqual(self.call("POST", "/api/lint", check)[1]["findings"], [])
        self.call("PUT", "/api/creator", FORM)
        _, lint = self.call("POST", "/api/lint", check)
        self.assertEqual([(f["severity"], f["matched"]) for f in lint["findings"]], [("error", "走不动")])
        self.assertTrue(lint["dna_is_example"])
        self.assertFalse(self.store.dna_path.exists())

        self.call("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT, "views": 2000, "likes": 150})
        for request in ({"task": "draft", "notes": "再去一次超市"}, {"task": "voice"}, {"task": "ideas"}):
            status, out = self.call("POST", "/api/manual/prompt", request)
            self.assertEqual(status, 200, request)
            self.assertIn('"name": "小满"', out["prompt"], request)
            self.assertIn('"referred_to_as": "我老公"', out["prompt"], request)
            self.assertNotIn("{{", out["prompt"], request)

        # The trend lookup goes to an AI that searches the web: the profile's niches are the focus, and nothing
        # else from the profile is sent (not the name, the account name, the description, the partner, the refusals).
        status, out = self.call("POST", "/api/manual/prompt", {"task": "trends"})
        self.assertEqual(status, 200)
        for niche in ("护肤", "日常 Vlog", "好物分享"):
            self.assertIn('"%s"' % niche, out["prompt"])
        for private in ("小满", "我老公", "英语", "上海", "喜欢慢慢讲", "宝子们", "走不动"):
            self.assertNotIn(private, out["prompt"], private)
        self.assertNotIn("{{", out["prompt"])


if __name__ == "__main__":
    unittest.main()
