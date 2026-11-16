"""Tests for the receipts behind a proposed voice profile (studio/evidence.py).

Run from the project root with: python3 -m unittest discover -t . -s tests
"""
import copy
import json
import unittest
from pathlib import Path

from studio import corpus, evidence, guardrails

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))

# The creator's material: two scripts of their own and one written to a brand's brief.
COSTCO = {"title": "Costco 补货那天", "is_sponsored": False, "text": "\n".join([
    "上周末跟我老公去Costco补货嘛 / 结果人特别多",
    "结果某人站在货架前面研究了十分钟，最后还是拿了原味哈",
    "你们看 / 他又开始了哈",
    "其实我本来只想买牛奶 / 不过，说实话 / 没忍住啦",
    "回去路上我老公还在念叨那盒蛋挞 [轻笑]",
])}
SKINCARE = {"title": "入冬护肤", "is_sponsored": False, "text": "\n".join([
    "一入冬吹暖气我这张干皮真的服了",
    "这罐面霜推开水水的，一点不糊脸啦",
    "诶，比我想的还要轻一点",
    "周六早上去练完 Pilates 顺路拐去 Trader Joe\u2019s 买束花嘛",
    "晚上去吃烤鸭啦 / 巨省心",
])}
SPONSORED = {"title": "商单面霜", "is_sponsored": True, "text": "\n".join([
    "宝子们，这款面霜搭载三重神经酰胺",
    "质地真的巨省心",
])}
MATERIAL = [SPONSORED, COSTCO, SKINCARE]      # sponsored first on purpose: order of the list must not decide the titles

# A model's proposal: partly right, partly invented, and banning three things the creator really says.
PROPOSAL = {
    "profile_version": "1.0",
    "persona_summary": {
        "audience_address_terms": {"uses": ["你们看", "姐妹们"], "never_uses": ["宝子们", "某人", "家人们"]},
        "husband_and_family_references": {
            "spoken_nicknames_zh_en": ["我老公 (neutral/warm context)"],
            "verbatim_examples": [
                "结果某人站在货架前面研究了十分钟，最后还是拿了原味哈",
                "我老公每次看我敷面膜都要问一句，是不是又买新的啦",
                "诶，比我想的还要轻一点",
            ],
        },
    },
    "cadence_and_syntax": {"target_speaking_rate_cpm": 240, "ideal_clause_length_chars": {"min": 5, "median": 9, "hard_max": 18}},
    "lexical_fingerprint": {
        "signature_openers": [
            {"pattern_zh": "上周末跟某人去 [Place] 嘛", "verbatim_sample": "上周末跟我老公去 Costco 补货嘛"},
            {"pattern_zh": "最近被问得最多的就是这个 [Item] 啦", "verbatim_sample": "最近评论区问得最多的就是我身上这件开衫啦"},
            {"pattern_zh": "换季这几天我真的服了", "verbatim_sample": "一入冬吹暖气我这张干皮真的服了"},
        ],
        "discourse_connectors": {
            "high_frequency": ["结果", "反正就是"],
            "forbidden_bookish_connectors": ["因此", "其实", "总而言之", "不过说实话", "众所周知"],
        },
        "modal_particles": {
            "primary": [{"particle": "啦", "placement_rule": "warm wrap-up"}, {"particle": "喔", "placement_rule": "soft reminder"}],
            "rare_or_zero": ["鸭", "哈"],
        },
    },
    "anti_voice_blacklist": {
        "banned_phrases_zh": ["绝绝子", "宝子们", "(any English filler word the creator never says)"],
        "learned_from_her_edits": ["性价比极高 (prefer: 算下来真的蛮划算的)"],
    },
}
USES = "persona_summary.audience_address_terms.uses"
NEVER_USES = "persona_summary.audience_address_terms.never_uses"
EXAMPLES = "persona_summary.husband_and_family_references.verbatim_examples"
OPENERS = "lexical_fingerprint.signature_openers"
FORBIDDEN = "lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors"
HIGH_FREQUENCY = "lexical_fingerprint.discourse_connectors.high_frequency"
PARTICLES = "lexical_fingerprint.modal_particles.primary"
RARE = "lexical_fingerprint.modal_particles.rare_or_zero"
BANNED = "anti_voice_blacklist.banned_phrases_zh"


def one(text, title="随手拍", sponsored=False):
    return {"title": title, "text": text, "is_sponsored": sponsored}


def quotes(*lines):
    return {"persona_summary": {"husband_and_family_references": {"verbatim_examples": list(lines)}}}


def terms(*words):
    return {"persona_summary": {"audience_address_terms": {"uses": list(words)}}}


def banned(*phrases):
    return {"anti_voice_blacklist": {"banned_phrases_zh": list(phrases)}}


def particles(primary=(), rare=()):
    return {"lexical_fingerprint": {"modal_particles": {"primary": [{"particle": p} for p in primary], "rare_or_zero": list(rare)}}}


def look_up(dna, samples=MATERIAL):
    """Report items keyed by the entry's text."""
    return {item["text"]: item for item in evidence.verify_profile(dna, samples)["items"]}


def got(item, *fields):
    return tuple(item[f] for f in fields)


class MatchingTest(unittest.TestCase):
    def test_real_line_is_found_however_it_was_typed(self):
        found = look_up(quotes(
            "上周末跟我老公去 costco 补货嘛，结果人特别多",       # spaces added, lower case, comma where the script has a breath mark
            "上周末跟我老公去Ｃｏｓｔｃｏ补货嘛",                    # full-width letters
            "结果某人站在货架前面研究了十分钟最后还是拿了原味哈",   # the script's comma left out
            "回去路上我老公还在念叨那盒蛋挞",                       # the script's line carries a [轻笑] cue, which is not speech
        ))
        for item in found.values():
            self.assertEqual(got(item, "kind", "status", "count", "titles"), ("quote", "found", 1, ["Costco 补货那天"]), item["text"])

    def test_invented_or_altered_line_is_not_found(self):
        found = look_up(quotes(
            "我老公每次看我敷面膜都要问一句，是不是又买新的啦",     # appears nowhere
            "结果某人站在货架前面研究了二十分钟",                   # the script says 十分钟: one wrong character is a different line
            "verbatim mixed line from the creator's videos",        # schema filler left in a quote slot is nobody's line either
        ))
        for item in found.values():
            self.assertEqual(got(item, "status", "count", "scripts", "titles"), ("not_found", 0, 0, []), item["text"])

    def test_trailing_note_is_ignored_but_shown_text_is_untouched(self):
        found = look_up(terms("我老公 (neutral/warm context)", "某人（吐槽的时候）"))
        husband = found["我老公 (neutral/warm context)"]
        self.assertEqual(got(husband, "kind", "phrase", "status"), ("term", "我老公", "found"))
        self.assertEqual(got(husband, "count", "scripts", "titles"), (2, 1, ["Costco 补货那天"]))    # lines 1 and 5 of one script
        self.assertEqual(got(found["某人（吐槽的时候）"], "phrase", "status", "count"), ("某人", "found", 1))
        # only a note at the end is a note: this line must not be cut down to 结果某人, which is in the material
        altered = "结果某人（我老公）站在货架前面研究了十分钟"
        self.assertEqual(got(look_up(quotes(altered))[altered], "phrase", "status"), (altered, "not_found"))

    def test_descriptions_are_not_checked_as_phrases(self):
        found = look_up(terms(
            "(80% of videos jump straight into the story with NO greeting)",   # nothing left once the note is removed
            "NONE — jumps straight in",                                         # the model saying there is none
            "无",                                                               # same in Chinese; must not match inside 无语
            "exact terms the creator uses for viewers",                         # English prose, not something said on camera
            "无语",                                                             # a real word that merely starts with 无
        ))
        for text in list(found)[:4]:
            self.assertEqual(got(found[text], "status", "count"), ("not_checkable", 0), text)
        self.assertEqual(found["无语"]["status"], "not_found")                   # checked, and honestly missing
        summary = evidence.verify_profile(terms(*found), MATERIAL)["summary"]
        self.assertEqual(got(summary, "items", "checked", "not_checkable", "not_found"), (5, 1, 4, 1))

    def test_placeholders_stand_for_a_short_gap_inside_one_breath(self):
        script = one("哈喽大家好我是小林 / 今天不化妆\n它不仅好推开而且很润\n首先洗脸 / 其次拍水 / 最后涂面霜")
        found = look_up(banned("哈喽大家好我是XX", "它不仅…而且…", "它不仅...而且...", "首先…其次…最后…"), [script])
        self.assertEqual([i["status"] for i in found.values()], ["conflict", "conflict", "conflict", "absent"])
        # the last one is absent because the three words sit in three breaths, and a gap does not cross a breath mark
        one_breath = look_up(banned("首先…其次…最后…"), [one("首先洗脸其次拍水最后涂面霜")])
        self.assertEqual(one_breath["首先…其次…最后…"]["status"], "conflict")

    def test_reads_a_ban_the_way_the_linter_enforces_it(self):
        cases = [
            ("哈喽大家好我是XX", "哈喽大家好我是小林"),
            ("它不仅…而且…", "它不仅好推开而且很润"),
            ("它不仅…而且…", "它不仅好推开,而且很润"),            # the linter's gap crosses a half-width comma
            ("它不仅…而且…", "它不仅好推开，而且很润"),            # but not a full-width one
            ("首先…其次…最后…", "首先洗脸 / 其次拍水 / 最后涂面霜"),
            ("质地非常轻盈 (prefer: 推开水水的)", "这个质地非常轻盈"),
            ("绝绝子", "这个面霜蛮舒服的"),
            ("某人 (调侃) 的时候", "结果某人站在货架前面"),          # the linter enforces only what precedes the first bracket
        ]
        for phrase, own_line in cases:
            flagged = bool(guardrails._phrase_to_regex(phrase).search(own_line))
            status = look_up(banned(phrase), [one(own_line)])[phrase]["status"]
            self.assertEqual(status, "conflict" if flagged else "absent", (phrase, own_line))

    def test_short_phrase_is_not_pieced_together_across_a_pause(self):
        script = one("刚到家 / 人们都在排队\n这就是原因，此外还有一点\n大家要团结。果然还是老样子\n到家 人们都走了")
        found = look_up({**terms("家人们", "因此", "结果")}, [script])
        self.assertEqual(found["家人们"]["status"], "not_found")    # 家 ends one breath, 人们 starts the next (also with a subtitle space)
        self.assertEqual(found["因此"]["status"], "not_found")      # 原因，此外
        self.assertEqual(found["结果"]["status"], "not_found")      # 团结。果然
        # a longer phrase said with a pause in the middle is still that phrase: 不过，说实话
        self.assertEqual(got(look_up(terms("不过说实话"))["不过说实话"], "status", "count"), ("found", 1))
        # A ban is different: the linter reads it across whatever separates the characters (宝子/们 is 宝子们), so
        # these two would flag the creator's own lines. They are conflicts, and the report shows how they are written.
        found = look_up(banned("家人们", "因此"), [script])
        self.assertEqual(got(found["家人们"], "status", "count", "written_as"), ("conflict", 2, ["家 / 人们", "家 人们"]))
        self.assertEqual(got(found["因此"], "status", "written_as"), ("conflict", ["因，此"]))

    def test_latin_words_match_whole_and_ignore_case_and_apostrophe_style(self):
        found = look_up(terms("gym"), [one("下班去 gymnastics 馆接她", "体操课"), one("周末去 gym 练腿", "练腿日")])
        self.assertEqual(got(found["gym"], "count", "titles"), (1, ["练腿日"]))                      # not inside gymnastics
        found = look_up(terms("Trader Joe's", "TRADER JOE'S", "pilates"))                            # typed as Trader Joe’s and Pilates
        self.assertEqual([got(i, "status", "titles") for i in found.values()], [("found", ["入冬护肤"])] * 3)

    def test_particle_counts_only_at_the_end_of_a_breath(self):
        text = "排队排了半小时啦 / 啦啦队都来了 / 好啦，走吧"
        found = look_up(particles(primary=["啦"]), [one(text)])
        self.assertEqual(got(found["啦"], "where", "status", "count"), ("breath_end", "found", 2))   # 啦 is written four times, ends a breath twice
        measured = {p["particle"]: p["count"] for p in corpus.voice_stats([text])["clause_final_particles"]}
        self.assertEqual(found["啦"]["count"], measured["啦"])                                       # same number the app shows as measured
        noted = look_up(particles(primary=["啦 (softening)", "啦～"]), [one("好啦～ / 哈哈 / 走啦!")])
        self.assertEqual([got(i, "phrase", "count") for i in noted.values()], [("啦", 2), ("啦", 2)])   # the note and the tilde are not the particle

        rare = look_up(particles(rare=["鸭"]))
        self.assertEqual(got(rare["鸭"], "kind", "status"), ("never", "absent"))                     # 烤鸭啦: 鸭 is inside a word
        rare = look_up(particles(rare=["鸭"]), MATERIAL + [one("今天也要开心鸭", "周一")])
        self.assertEqual(got(rare["鸭"], "status", "count", "titles"), ("conflict", 1, ["周一"]))
        # as a banned phrase the linter matches 鸭 anywhere, so there 烤鸭 does make it a conflict
        self.assertEqual(look_up(banned("鸭"))["鸭"]["status"], "conflict")

    def test_sponsored_material_never_turns_a_ban_into_a_conflict(self):
        found = look_up(banned("宝子们"))
        self.assertEqual(got(found["宝子们"], "status", "count", "titles"), ("absent", 0, []))       # only the brand's script says it
        self.assertEqual(got(found["宝子们"], "organic_count", "sponsored_count", "sponsored_only"), (0, 1, True))
        found = look_up(banned("宝子们"), MATERIAL + [one("宝子们今天不化妆", "素颜日")])
        self.assertEqual(got(found["宝子们"], "status", "count", "titles"), ("conflict", 1, ["素颜日"]))

    def test_phrase_found_only_in_sponsored_material_is_marked(self):
        dna = {"persona_summary": {
            "husband_and_family_references": {"verbatim_examples": ["这款面霜搭载三重神经酰胺"]},
            "regional_and_phonetic_nuances": {"preserve_colloquialisms": ["巨省心"]},
        }}
        report = evidence.verify_profile(dna, MATERIAL)
        found = {item["text"]: item for item in report["items"]}
        brand_line = found["这款面霜搭载三重神经酰胺"]
        self.assertEqual(got(brand_line, "status", "count", "sponsored_only", "titles"), ("found", 1, True, ["商单面霜"]))
        both = found["巨省心"]
        self.assertEqual(got(both, "count", "organic_count", "sponsored_count", "sponsored_only"), (2, 1, 1, False))
        self.assertEqual(both["titles"], ["入冬护肤", "商单面霜"])                                   # the non-sponsored script is listed first
        self.assertEqual(got(report["summary"], "found", "sponsored_only", "organic_samples", "sponsored_samples"), (2, 1, 2, 1))

    def test_at_most_three_titles_but_every_occurrence_counted(self):
        scripts = [one("蛮舒服的", "一"), one("蛮舒服的 / 真的蛮舒服的", "二"), one("蛮舒服的", "三"), one("蛮舒服的", "四")]
        item = look_up(terms("蛮舒服的"), scripts)["蛮舒服的"]
        self.assertEqual(got(item, "count", "scripts", "titles"), (5, 4, ["一", "二", "三"]))

    def test_without_material_nothing_is_called_missing(self):
        report = evidence.verify_profile(PROPOSAL, [])
        self.assertEqual({i["status"] for i in report["items"]}, {"not_checkable"})                 # not "not found": nothing was searched
        self.assertEqual(got(report["summary"], "samples", "checked"), (0, 0))
        only_sponsored = look_up({**banned("绝绝子"), **terms("巨省心")}, [SPONSORED])
        self.assertEqual(only_sponsored["绝绝子"]["status"], "not_checkable")                        # a ban is judged on non-sponsored scripts only
        self.assertEqual(got(only_sponsored["巨省心"], "status", "sponsored_only"), ("found", True))


class ProfileWalkTest(unittest.TestCase):
    def test_example_profile_is_walked_completely(self):
        report = evidence.verify_profile(EXAMPLE_DNA, MATERIAL)
        by_path = {item["path"]: item for item in report["items"]}
        self.assertEqual(len(by_path), len(report["items"]))                                         # every path is unique
        expected = {
            "persona_summary.husband_and_family_references.verbatim_examples[0]": ("quote", EXAMPLES),
            "lexical_fingerprint.signature_openers[0].verbatim_sample": ("quote", OPENERS),
            "code_switching_grammar.good_vs_bad_examples[0].authentic_her": ("quote", "code_switching_grammar.good_vs_bad_examples"),
            "persona_summary.audience_address_terms.uses[3]": ("term", USES),
            "persona_summary.husband_and_family_references.spoken_nicknames_zh_en[2]": (
                "term", "persona_summary.husband_and_family_references.spoken_nicknames_zh_en"),
            "persona_summary.regional_and_phonetic_nuances.preserve_colloquialisms[1]": (
                "term", "persona_summary.regional_and_phonetic_nuances.preserve_colloquialisms"),
            "lexical_fingerprint.discourse_connectors.high_frequency[5]": ("term", "lexical_fingerprint.discourse_connectors.high_frequency"),
            "lexical_fingerprint.modal_particles.primary[3].particle": ("term", PARTICLES),
            "lexical_fingerprint.genuine_praise_lexicon.trust_building_caveats[2]": ("term", "lexical_fingerprint.genuine_praise_lexicon"),
            "persona_summary.audience_address_terms.never_uses[4]": ("never", NEVER_USES),
            "lexical_fingerprint.discourse_connectors.forbidden_bookish_connectors[6]": ("never", FORBIDDEN),
            "lexical_fingerprint.modal_particles.rare_or_zero[4]": ("never", RARE),
            "anti_voice_blacklist.banned_phrases_zh[13]": ("never", BANNED),
        }
        for path, facts in expected.items():
            self.assertEqual(got(by_path[path], "kind", "group"), facts, path)
        self.assertEqual({i["group"] for i in report["items"]}, {group for group, _ in evidence.GROUPS})
        self.assertNotIn("性价比极高", " ".join(i["text"] for i in report["items"]))                 # what the creator taught the app is not re-examined

        # The file holds 6 quotes (2 examples, 3 openers, 1 code-switch line), 37 habits (4 address terms, 3 nicknames,
        # 4 colloquialisms, 6 connectors, 4 particles, 16 praise phrases) and 31 never claims (5 + 7 + 5 + 14).
        # MATERIAL contains 4 of the quotes; 14 habits (你们看, 我老公, 某人, 巨省心 twice, 其实, 结果, 不过说实话,
        # 嘛 啦 哈 诶, 推开水水的, 一点不糊脸); and none of the banned phrases outside the sponsored script.
        self.assertEqual(report["summary"]["by_kind"], {
            "quote": {"found": 4, "not_found": 2, "not_checkable": 0},
            "term": {"found": 14, "not_found": 22, "not_checkable": 1},
            "never": {"absent": 31, "conflict": 0, "not_checkable": 0},
        })
        counts = {i["text"]: i["count"] for i in report["items"] if i["group"] == PARTICLES}
        self.assertEqual(counts, {"嘛": 2, "啦": 3, "哈": 2, "诶": 1})                               # breath endings in the two non-sponsored scripts

    def test_same_input_gives_same_report_and_inputs_are_not_modified(self):
        dna, samples = copy.deepcopy(PROPOSAL), copy.deepcopy(MATERIAL)
        first = evidence.verify_profile(dna, samples)
        self.assertEqual(first, evidence.verify_profile(dna, samples))
        self.assertEqual((dna, samples), (PROPOSAL, MATERIAL))
        json.dumps(first)                                                                            # storable as is

    def test_malformed_profile_and_samples_never_raise(self):
        empty = evidence.verify_profile(None, None)
        self.assertEqual((empty["items"], empty["summary"]["items"]), ([], 0))
        for dna in ([], "profile", 7, {"persona_summary": None}, {"persona_summary": {"audience_address_terms": []}},
                    {"lexical_fingerprint": {"genuine_praise_lexicon": {"texture": "软软的", "feel": None, "n": 3}}}):
            self.assertEqual(evidence.verify_profile(dna, "not a list")["items"], [], dna)

        broken = {
            "persona_summary": "oops",
            "lexical_fingerprint": {
                "signature_openers": "x",
                "modal_particles": {"primary": [1, None, {"particle": 5}, {"particle": "啦"}], "rare_or_zero": {"a": 1}},
                "genuine_praise_lexicon": ["a"],
            },
            "code_switching_grammar": {"good_vs_bad_examples": [{"fake_chinglish_to_avoid": "so healing"}, "x"]},
            "anti_voice_blacklist": {"banned_phrases_zh": [None, 3, "", "绝绝子", ["x"]]},
        }
        samples = [None, "text", {"title": 5, "text": None}, {"text": "   "}, {"text": "好啦"}]
        items = evidence.verify_profile(broken, samples)["items"]
        self.assertEqual([got(i, "path", "status") for i in items], [
            ("lexical_fingerprint.modal_particles.primary[3].particle", "found"),      # index is the real position in the list
            ("code_switching_grammar.good_vs_bad_examples[0].authentic_her", "not_found"),   # an example with no line quoted
            ("anti_voice_blacklist.banned_phrases_zh[3]", "absent"),
        ])
        self.assertEqual(got(items[0], "count", "titles"), (1, []))                                  # the only usable sample has no title


class ApplyDecisionsTest(unittest.TestCase):
    def setUp(self):
        self.report = evidence.verify_profile(PROPOSAL, MATERIAL)
        self.rejected = [USES + "[1]", FORBIDDEN + "[4]", PARTICLES + "[1].particle"]
        # What the creator ticked although the material did not bear it out: the habit 反正就是, the description among
        # the bans, and the free text the report lists. None of that is kept without a tick.
        self.keep = [HIGH_FREQUENCY + "[1]", BANNED + "[2]"] + [field["path"] for field in self.report["unverified"]]
        self.approved = evidence.apply_decisions(PROPOSAL, self.report, self.rejected, keep_paths=self.keep)

    def test_report_on_the_proposal(self):
        status = {i["path"]: i["status"] for i in self.report["items"]}
        self.assertEqual([status[NEVER_USES + "[%d]" % n] for n in range(3)], ["absent", "conflict", "absent"])   # 某人 is in the material
        self.assertEqual([status[EXAMPLES + "[%d]" % n] for n in range(3)], ["found", "not_found", "found"])
        self.assertEqual([status[OPENERS + "[%d].verbatim_sample" % n] for n in range(3)], ["found", "not_found", "found"])
        self.assertEqual([status[FORBIDDEN + "[%d]" % n] for n in range(5)],
                         ["absent", "conflict", "absent", "conflict", "absent"])                     # 其实…, 不过，说实话
        self.assertEqual([status[RARE + "[%d]" % n] for n in range(2)], ["absent", "conflict"])      # 原味哈, 开始了哈
        self.assertEqual([status[BANNED + "[%d]" % n] for n in range(3)], ["absent", "absent", "not_checkable"])

    def test_removes_rejected_unverified_and_conflicting_entries(self):
        persona, lexical = self.approved["persona_summary"], self.approved["lexical_fingerprint"]
        self.assertEqual(persona["audience_address_terms"], {"uses": ["你们看"], "never_uses": ["宝子们", "家人们"]})
        self.assertEqual(persona["husband_and_family_references"]["verbatim_examples"],
                         ["结果某人站在货架前面研究了十分钟，最后还是拿了原味哈", "诶，比我想的还要轻一点"])
        self.assertEqual([o["pattern_zh"] for o in lexical["signature_openers"]],
                         ["上周末跟某人去 [Place] 嘛", "换季这几天我真的服了"])                        # the unverified opener went as a whole
        # entries 1 and 3 conflicted and the creator rejected entry 4: the two left are the right two
        self.assertEqual(lexical["discourse_connectors"]["forbidden_bookish_connectors"], ["因此", "总而言之"])
        self.assertEqual(lexical["discourse_connectors"]["high_frequency"], ["结果", "反正就是"])     # a habit not found stays only on her tick
        self.assertEqual(lexical["modal_particles"], {"primary": [{"particle": "啦", "placement_rule": "warm wrap-up"}], "rare_or_zero": ["鸭"]})
        self.assertEqual(self.approved["anti_voice_blacklist"], PROPOSAL["anti_voice_blacklist"])    # nothing there conflicted

    def test_records_what_happened(self):
        # The proposal has 26 entries to look at; one is a description, so 25 were checked. 8 were found:
        # 你们看, 我老公, two examples, two openers, 结果, 啦. Kept on her tick alone: 反正就是 and the description.
        # Free text kept: the patterns of the two openers that stayed, 啦's placement rule, the learned edit.
        self.assertEqual(self.approved["evidence_review"], {
            "checked": 25, "found": 8, "removed_unverified": 2, "rejected_by_creator": 3,
            "kept_unverified": 2, "free_text_kept": 4, "free_text_removed": 0,
            "conflicts_dropped": ["某人", "其实", "不过说实话", "哈"],
        })
        self.assertNotIn("敷面膜", json.dumps(self.approved, ensure_ascii=False))                    # the invented line is gone everywhere

    def test_input_is_left_untouched_and_result_verifies_clean(self):
        proposal, report = copy.deepcopy(PROPOSAL), copy.deepcopy(self.report)
        evidence.apply_decisions(proposal, report, self.rejected)
        self.assertEqual((proposal, report), (PROPOSAL, self.report))
        self.assertNotIn("evidence_review", PROPOSAL)
        again = evidence.verify_profile(self.approved, MATERIAL)["summary"]
        self.assertEqual((again["conflict"], again["by_kind"]["quote"]["not_found"]), (0, 0))

    def test_linter_stops_flagging_the_creators_own_words(self):
        own_line = {"script_lines": [{"line_no": 1, "audio_mode": "VOICEOVER", "spoken_script_zh": "其实我本来只想买牛奶 / 他又开始了哈"}]}
        before = [f.matched for f in guardrails.lint_script(own_line, PROPOSAL)]
        self.assertCountEqual(before, ["其实", "哈"])                                                # the proposal bans both
        self.assertEqual(guardrails.lint_script(own_line, self.approved), [])

    def test_unfound_quotes_can_be_kept(self):
        kept = evidence.apply_decisions(PROPOSAL, self.report, [], drop_not_found=False)
        self.assertEqual(len(kept["persona_summary"]["husband_and_family_references"]["verbatim_examples"]), 3)
        self.assertEqual(len(kept["lexical_fingerprint"]["signature_openers"]), 3)
        self.assertEqual(kept["lexical_fingerprint"]["modal_particles"]["rare_or_zero"], ["鸭"])     # a conflicting ban still goes
        self.assertEqual(got(kept["evidence_review"], "removed_unverified", "rejected_by_creator"), (0, 0))
        # with the switch off, the creator's own rejection still removes an unfound quote, and is counted as theirs
        theirs = evidence.apply_decisions(PROPOSAL, self.report, [EXAMPLES + "[1]"], drop_not_found=False)
        self.assertEqual(len(theirs["persona_summary"]["husband_and_family_references"]["verbatim_examples"]), 2)
        self.assertEqual(got(theirs["evidence_review"], "removed_unverified", "rejected_by_creator"), (0, 1))

    def test_each_removal_is_counted_once(self):
        # rejecting what the report already removes changes nothing; a found entry the creator rejects is theirs alone
        paths = [NEVER_USES + "[1]", EXAMPLES + "[1]", USES + "[0]"]
        review = evidence.apply_decisions(PROPOSAL, self.report, paths)["evidence_review"]
        # removed for lack of a receipt: the two unfound quotes, and with no tick sent the three habits that were
        # not found (姐妹们, 反正就是, 喔) and the description among the bans
        self.assertEqual(got(review, "removed_unverified", "rejected_by_creator"), (6, 1))
        self.assertEqual(review["conflicts_dropped"], ["某人", "其实", "不过说实话", "哈"])

    def test_opener_with_no_quote_is_unverified(self):
        dna = {"lexical_fingerprint": {"signature_openers": [
            {"pattern_zh": "最近被问得最多的就是这个 [Item] 啦"},
            {"pattern_zh": "上周末跟某人去 [Place] 嘛", "verbatim_sample": "上周末跟我老公去 Costco 补货嘛"},
        ]}}
        report = evidence.verify_profile(dna, MATERIAL)
        self.assertEqual([got(i, "text", "status") for i in report["items"]],
                         [("", "not_found"), ("上周末跟我老公去 Costco 补货嘛", "found")])           # a pattern with no line quoted has no receipt
        approved = evidence.apply_decisions(dna, report, [], keep_paths=[field["path"] for field in report["unverified"]])
        self.assertEqual([o["pattern_zh"] for o in approved["lexical_fingerprint"]["signature_openers"]], ["上周末跟某人去 [Place] 嘛"])

    def test_stale_report_neither_condemns_nor_vouches_for_the_wrong_entry(self):
        edited = copy.deepcopy(PROPOSAL)
        connectors = edited["lexical_fingerprint"]["discourse_connectors"]
        connectors["forbidden_bookish_connectors"] = ["其实", "因此", "总而言之", "众所周知", "不过说实话"]   # reordered after the report
        approved = evidence.apply_decisions(edited, self.report, [FORBIDDEN + "[4]"], keep_paths=[FORBIDDEN + "[0]"])
        # Position 2 still holds what the report saw there, and stays. The report says nothing about the text now at
        # the other four places, so none of them is let through, not even the one whose place was ticked.
        self.assertEqual(approved["lexical_fingerprint"]["discourse_connectors"]["forbidden_bookish_connectors"], ["总而言之"])
        # None of them went on the strength of another entry's status or of a decision made about other text.
        self.assertEqual(approved["evidence_review"]["conflicts_dropped"], ["某人", "哈"])
        self.assertEqual(approved["evidence_review"]["rejected_by_creator"], 0)
        # With the "not borne out" rules switched off they are left alone, as before.
        lenient = evidence.apply_decisions(edited, self.report, [], drop_not_found=False)
        self.assertEqual(lenient["lexical_fingerprint"]["discourse_connectors"]["forbidden_bookish_connectors"],
                         ["其实", "因此", "总而言之", "众所周知", "不过说实话"])

    def test_malformed_arguments_never_raise(self):
        for report in (None, [], "report", {"items": None}, {"items": [None, 5, {"path": 3}, {"path": USES + "[0]"}]}):
            for rejected in (None, "persona_summary", 5, [None, 7, "no.such.path[0]"]):
                out = evidence.apply_decisions(PROPOSAL, report, rejected, drop_not_found=False)
                self.assertEqual({k: v for k, v in out.items() if k != "evidence_review"}, PROPOSAL)
                self.assertEqual(out["evidence_review"]["conflicts_dropped"], [])
                # by default a report that says nothing bears nothing out: no entry and no free text is let through on it
                out = evidence.apply_decisions(PROPOSAL, report, rejected, keep_paths=self.keep)
                left = evidence.verify_profile(out, MATERIAL)
                self.assertEqual((left["items"], left["unverified"]), ([], []))
                self.assertEqual(out["cadence_and_syntax"], PROPOSAL["cadence_and_syntax"])
                self.assertEqual(out["evidence_review"]["conflicts_dropped"], [])
        review = {"checked": 0, "found": 0, "removed_unverified": 0, "rejected_by_creator": 0,
                  "kept_unverified": 0, "free_text_kept": 0, "free_text_removed": 0, "conflicts_dropped": []}
        self.assertEqual(evidence.apply_decisions(None, None, None), {"evidence_review": review})
        self.assertEqual(evidence.apply_decisions("profile", self.report, self.rejected), {"evidence_review": review})
        # with no report at all and the rules switched off, a path the creator rejects is still removed
        out = evidence.apply_decisions(PROPOSAL, None, [BANNED + "[0]"], drop_not_found=False)
        self.assertEqual(out["anti_voice_blacklist"]["banned_phrases_zh"], ["宝子们", "(any English filler word the creator never says)"])


if __name__ == "__main__":
    unittest.main()
