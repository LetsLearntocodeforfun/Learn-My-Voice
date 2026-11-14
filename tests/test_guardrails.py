"""Run from the project root with: python3 -m unittest discover -t . -s tests"""
import json
import unittest
from pathlib import Path

from studio import guardrails as g
from studio import textstats as ts

ROOT = Path(__file__).resolve().parent.parent
DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
WORKED = json.loads((ROOT / "tests" / "fixtures" / "worked_example_script.json").read_text(encoding="utf-8"))


def script(*spoken, **line_overrides):
    lines = []
    for i, text in enumerate(spoken, start=1):
        line = {"line_no": i, "audio_mode": "VOICEOVER", "spoken_script_zh": text}
        line.update(line_overrides)
        lines.append(line)
    return {"script_lines": lines}


def lint(*spoken, **kwargs):
    return g.lint_script(script(*spoken), DNA, **kwargs)


def cats(findings, severity=None):
    return [(f.line_no, f.category, f.matched) for f in findings if severity in (None, f.severity)]


class TextStatsTest(unittest.TestCase):
    def test_units_count_cjk_english_words_and_digits(self):
        self.assertEqual(ts.spoken_units("拉着某人去趟 Trader Joe's 补货嘛"), 11)
        self.assertEqual(ts.spoken_units("用了3天"), 4)
        self.assertEqual(ts.spoken_units("，。～ / "), 0)

    def test_cues_and_live_clip_quotes_are_not_spoken(self):
        text = '[原声: "Wait can we get four boxes?"] / 最后被我摁住只拿了两盒 [轻笑]'
        self.assertEqual(ts.split_clauses(text), ["最后被我摁住只拿了两盒"])

    def test_punctuation_breaks_toggle(self):
        text = "我现在睡前涂完，第二天上妆基本不怎么卡粉啦 / 不过它是偏滋润挂的"
        self.assertEqual([ts.spoken_units(c) for c in ts.split_clauses(text)], [7, 13, 9])
        self.assertEqual([ts.spoken_units(c) for c in ts.split_clauses(text, punctuation_breaks=False)], [20, 9])

    def test_time_range_and_budget(self):
        self.assertEqual(ts.parse_time_range("00:53-00:59"), (53, 59))
        self.assertEqual(ts.parse_time_range("01:02–01:10"), (62, 70))
        self.assertIsNone(ts.parse_time_range("soon"))
        self.assertAlmostEqual(ts.unit_budget(60, 238), 238.0)
        self.assertAlmostEqual(ts.unit_budget(60, 240, live_clip_sec=15), 180.0)


class GuardrailRuleTest(unittest.TestCase):
    def test_clean_line_in_her_voice_has_no_findings(self):
        self.assertEqual(lint("上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动道哈"), [])

    def test_clause_over_hard_max(self):
        found = lint("你们入冬还有什么救命面霜也可以种草给我哈")
        self.assertEqual([(f.category, f.severity) for f in found], [(g.CADENCE_TOO_LONG, g.ERROR)])
        self.assertIn("20 units > hard_max 18", found[0].detail)

    def test_dna_blacklist_and_ellipsis_pattern(self):
        found = cats(lint("这个真的绝绝子 / 它不仅好推开而且很润"))
        self.assertIn((1, g.GENERIC_XHS_SLOP, "绝绝子"), found)
        self.assertIn((1, g.GENERIC_XHS_SLOP, "它不仅好推开而且"), found)

    def test_learned_edit_entry_ignores_parenthetical_note(self):
        self.assertIn((1, g.GENERIC_XHS_SLOP, "性价比极高"), cats(lint("这罐性价比极高")))

    def test_bookish_connector(self):
        self.assertIn((1, g.BOOKISH_MANDARIN, "因此"), cats(lint("因此我就买了两罐")))

    def test_superlatives_are_context_aware(self):
        allowed = lint("所以最近回家洗完澡第一件事 / 最搞笑的是某人 / 唯一的小缺点就是瓶子沉")
        self.assertEqual([f for f in allowed if f.category == g.COMPLIANCE_RISK], [])
        flagged = cats(lint("这是我用过最好用的面霜 / 销量第一"))
        self.assertIn((1, g.COMPLIANCE_RISK, "最"), flagged)
        self.assertIn((1, g.COMPLIANCE_RISK, "第一"), flagged)

    def test_hard_compliance_terms_are_errors_and_overlaps_dedupe(self):
        found = lint("第二天底妆100%服帖 / 换季退红消炎 / 全网最低价")
        errors = cats(found, g.ERROR)
        self.assertIn((1, g.COMPLIANCE_RISK, "100%"), errors)
        self.assertIn((1, g.COMPLIANCE_RISK, "消炎"), errors)
        self.assertIn((1, g.COMPLIANCE_RISK, "全网最低"), errors)
        self.assertNotIn((1, g.COMPLIANCE_RISK, "最"), cats(found))

    def test_unattested_address_term_and_english_go_to_review(self):
        found = cats(lint("大油皮姐妹薄涂一层就够了 / 还问我那个 jar 叫什么哈"), g.REVIEW)
        self.assertIn((1, g.GENERIC_XHS_SLOP, "姐妹"), found)
        self.assertIn((1, g.UNNATURAL_CODESWITCH, "jar"), found)

    def test_attested_and_extra_allowed_english_pass(self):
        self.assertEqual(lint("顺路拐去 Trader Joe's 买束花嘛"), [])
        self.assertEqual(lint("这罐 CeraVe 我用了一周啦", extra_allowed_english=["CeraVe"]), [])

    def test_rare_particle_only_flagged_at_clause_end(self):
        self.assertIn((1, g.GENERIC_XHS_SLOP, "鸭"), cats(lint("今天也要开心鸭")))
        self.assertEqual(lint("晚上去吃烤鸭啦"), [])

    def test_live_clip_lines_are_skipped_and_reduce_budget(self):
        s = {"script_lines": [
            {"line_no": 1, "audio_mode": "LIVE_CLIP", "time_range_sec": "00:00-00:15",
             "spoken_script_zh": "This is absolutely the best, 100% amazing"},
            {"line_no": 2, "audio_mode": "VOICEOVER", "time_range_sec": "00:15-00:30",
             "spoken_script_zh": " / ".join(["结果某人又走不动道哈"] * 6)},
        ]}
        self.assertEqual(g.lint_script(s, DNA), [])  # 60 units vs budget 15s * 238/60 = 59.5

    def test_duration_budget(self):
        short = lint("好啦", target_duration_sec=60)
        self.assertEqual([f.category for f in short], [g.DURATION_OFF_BUDGET])
        self.assertEqual(lint("好啦"), [])  # no target and no time ranges: check skipped

    def test_accepts_stage_5a_wrapper(self):
        wrapped = {"voice_match_score": 90, "final_polished_script_json": script("这个真的绝绝子")}
        self.assertEqual(cats(g.lint_script(wrapped, DNA)), [(1, g.GENERIC_XHS_SLOP, "绝绝子")])


class WorkedExampleRegressionTest(unittest.TestCase):
    """The showcase script currently breaks four of its own rules; pin that."""

    def setUp(self):
        self.findings = g.lint_script(WORKED, DNA)

    def test_exact_findings(self):
        self.assertEqual(cats(self.findings), [
            (6, g.UNNATURAL_CODESWITCH, "jar"),
            (7, g.GENERIC_XHS_SLOP, "姐妹"),
            (8, g.CADENCE_TOO_LONG, "你们入冬还有什么救命面霜也可以种草给我哈"),
            (None, g.DURATION_OFF_BUDGET, "283 units"),
        ])

    def test_slash_only_mode_also_flags_line_7(self):
        found = g.lint_script(WORKED, DNA, punctuation_breaks=False)
        long_lines = [f.line_no for f in found if f.category == g.CADENCE_TOO_LONG]
        self.assertEqual(long_lines, [7, 8])

    def test_cli_exit_status(self):
        args = [str(ROOT / "tests" / "fixtures" / "worked_example_script.json"),
                "--dna", str(ROOT / "schemas" / "voice_dna.example.json"), "--json"]
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = g.main(args)
        self.assertEqual(status, 1)
        self.assertEqual(len(json.loads(buf.getvalue())), 4)


if __name__ == "__main__":
    unittest.main()
