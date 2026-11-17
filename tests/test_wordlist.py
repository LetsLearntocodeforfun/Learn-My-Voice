"""Tests for the word list: what the shipped list flags and what each entry rests on, the hints that a line may
sound written, the creator's own changes to the list, and that every check reads the list as the creator keeps it.

Nothing here checks an entry against a law or against a platform's rules. What is tested is that the app says
what ``lexicon.json`` says about an entry, and no more than that.
"""
import contextlib
import http.client
import io
import json
import random
import re
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from studio import checks, creator, mcp_server, server, suggest, trends, wordlist
from studio import guardrails as g
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "studio" / "web"
DNA_PATH = ROOT / "schemas" / "voice_dna.example.json"
DNA = json.loads(DNA_PATH.read_text(encoding="utf-8"))
SHIPPED = g.load_lexicon()
RULES, TELLS = SHIPPED["compliance"], SHIPPED["ai_tells"]
NOW = 1_800_000_000.0
LONG = 100000       # characters in a line made to take long
# Seconds one timed step may take. A step that grows with the length of the line takes a few hundredths of a
# second here. The pattern that was corrected took about 9 seconds on such a line, and the overlap check as it
# was took minutes.
SLOW = 2.0
ASSUMES = re.compile(r"\b(she|her|hers|herself|husband|wife|couple)\b", re.I)

# The advertising-rule entries as they stood before this list replaced them (commit 658ac1d): pattern, severity, reason.
BEFORE = [
    ("100 ?%|百分之百|百分百", "error", "Absolute claim (lexicon 3A)"),
    ("全网最低|最低价", "error", "Price-war claim (lexicon 3A)"),
    ("国家级", "error", "Absolute superlative (lexicon 3A)"),
    ("消炎|杀菌|治疗|根治|祛斑|生发|排毒", "error", "Medical / efficacy claim (lexicon 3B, prompt 03)"),
    ("再生细胞|逆龄|一夜回春|立竿见影", "error", "Anti-aging / instant-result claim (lexicon 3B)"),
    ("私信领|私信发链接|点击下方购买|下单立减|赶紧下单|赶紧抢|加微", "error", "Hard-commerce / off-platform trigger (lexicon 3C)"),
    ("最(?!近|后|终|初|搞笑|戳|勤|多|爱|喜欢)", "review",
     "Possible superlative 最; personal-habit uses such as 最近 / 用得最勤 are allowed (lexicon 3A)"),
    ("第一(?!件|次|罐|瓶|天|步|眼|口|反应|时间|个|期)", "review", "Possible ranking claim 第一 (lexicon 3A)"),
    ("唯一(?!的小?缺点)", "review", "Possible absolute claim 唯一 (prompt 03)"),
    ("顶级|极致|绝对|秒杀", "review", "Possible absolute superlative (lexicon 3A)"),
    ("美白|抗衰", "review", "Whitening / anti-aging wording needs product registration check (lexicon 3B)"),
    ("微信|VX|vx|买它|冲就完了", "review", "Possible off-platform or hard-sell trigger (lexicon 3C)"),
]
# Lines nobody would call advertising wording or machine-written. Several stand next to an entry on purpose:
# 第一次, 最后三天, No.10, 进行曲, 随着音乐, V领, 加了, 三天 without 见效.
ORDINARY = [
    "上周末跟我老公去 Costco 补货嘛 / 结果某人又走不动道哈", "所以最近回家洗完澡第一件事 / 最搞笑的是某人 / 唯一的小缺点就是瓶子沉",
    "今天第一次用这个面霜 / 最后还是买了两瓶", "最近早上起来先喝一杯水 / 然后去遛狗", "这个颜色我最喜欢 / 用得最勤的是这一支",
    "周末去超市买了三盒小笼包啦", "这瓶我用了三天 / 感觉还不错", "最后三天我们去了海边", "No.10 那家店的咖啡好喝", "top10 好物分享",
    "进行曲很好听", "随着音乐跳舞", "V领毛衣很好看", "加了一点牛奶", "这家店不加味精", "我们私下聊了很久", "顶楼的风景很好",
    "值得一去的小店", "家里的微波炉坏了", "他是我们班第一个到的", "一天喝八杯水", "十天以后再看效果", "医生开了药", "免疫力这件事要慢慢来",
]


def script(*spoken, **line_overrides):
    lines = []
    for i, text in enumerate(spoken, start=1):
        line = {"line_no": i, "audio_mode": "VOICEOVER", "spoken_script_zh": text}
        line.update(line_overrides)
        lines.append(line)
    return {"script_lines": lines}


def lint(*spoken, **kwargs):
    """Findings for lines checked with the shipped list (or ``lexicon=``) and an empty voice profile (or ``dna=``).

    ``line`` holds further fields for every line, such as its ``audio_mode``.
    """
    kwargs.setdefault("lexicon", SHIPPED)
    return g.lint_script(script(*spoken, **kwargs.pop("line", {})), kwargs.pop("dna", {}), **kwargs)


def of(findings, *categories):
    return [(f.category, f.severity, f.matched) for f in findings if f.category in categories]


def examples(entry):
    return entry["shows"].split("、")


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def check(self, *spoken, target_sec=30):
        return checks.lint_payload(self.store, script(*spoken)["script_lines"], target_sec)

    def flagged(self, *spoken):
        return [(f["category"], f["severity"], f["matched"]) for f in self.check(*spoken)["findings"] if f["line_no"] is not None]

    def stored(self):
        return json.loads(self.store.config_path.read_text(encoding="utf-8"))

    def keep(self, value):
        """Put ``value`` under the word list's key of config.json, as a hand edit or another version would."""
        self.store.update_config(lambda config: config.update({wordlist.KEY: value}))


# ---- the shipped list -------------------------------------------------------------------------------------------------
class ShippedListTest(unittest.TestCase):
    def test_the_list_has_the_shape_the_app_reads(self):
        self.assertEqual((len(RULES), len(TELLS)), (22, 11))
        ids = [entry["id"] for entry in RULES + TELLS]
        self.assertEqual(len(set(ids)), 33)
        self.assertEqual(wordlist.entry_ids(SHIPPED), ids)
        reasons = [entry["reason_en"] for entry in RULES]
        self.assertEqual(len(set(reasons)), len(reasons))                     # a finding names its entry by this
        sources = SHIPPED["_sources"]
        self.assertEqual(sorted(sources), ["ad_law", "humanizer_zh"])
        for source in sources.values():
            for key in ("title_zh", "title_en", "url", "read_on", "note_zh", "note_en"):
                self.assertTrue(source[key].strip(), key)
            self.assertTrue(source["url"].startswith("https://"))
        self.assertRegex(SHIPPED["_reviewed_on"], r"^\d{4}-\d{2}-\d{2}$")
        for language in ("zh", "en"):
            self.assertTrue(SHIPPED["_disclaimer_" + language].strip())
        for entry in RULES:
            self.assertIn(entry["severity"], (g.ERROR, g.REVIEW), entry["id"])
            for key in ("shows", "why_zh", "why_en"):
                self.assertTrue(entry[key].strip(), entry["id"])
            re.compile(entry["pattern"])
            basis = entry["basis"]
            if basis is not None:
                self.assertIn(basis["source"], sources, entry["id"])
                for key in ("where_zh", "where_en", "says_zh", "says_en"):
                    self.assertTrue(basis[key].strip(), entry["id"])
                self.assertIsInstance(basis["by_analogy"], bool, entry["id"])
        for entry in TELLS:
            re.compile(entry["pattern"])
            self.assertTrue(entry["shows"].strip(), entry["id"])
            self.assertTrue(entry["source"] is None or entry["source"] in sources, entry["id"])
            self.assertEqual(entry["source"] is None, entry["pattern_no"] is None, entry["id"])
        # What rests on nothing that was read says so by having no basis: four advertising entries and two hints.
        self.assertEqual([entry["id"] for entry in RULES if entry["basis"] is None],
                         ["hard_commerce", "whitening_antiaging", "off_platform", "more_off_platform"])
        self.assertEqual([entry["id"] for entry in TELLS if entry["source"] is None], ["tell_worth_noting", "tell_slogan"])

    def test_the_entries_that_were_there_before_are_unchanged_and_the_new_ones_only_ask(self):
        self.assertEqual([(entry["pattern"], entry["severity"], entry["reason_en"]) for entry in RULES[:len(BEFORE)]], BEFORE)
        self.assertEqual({entry["severity"] for entry in RULES[len(BEFORE):]}, {g.REVIEW})

    def test_every_example_an_entry_shows_is_matched_by_that_entry(self):
        for entry in RULES:
            for example in examples(entry):
                self.assertIsNotNone(re.search(entry["pattern"], example), (entry["id"], example))
                alone = of(lint(example, lexicon={"compliance": [entry]}), g.COMPLIANCE_RISK)
                self.assertEqual(alone, [(g.COMPLIANCE_RISK, entry["severity"], example)], entry["id"])

    def test_every_example_is_flagged_when_the_whole_list_is_read(self):
        for entry in RULES:
            for example in examples(entry):
                found = [(f.category, f.matched, f.detail) for f in lint(example) if f.category in (g.COMPLIANCE_RISK, g.GENERIC_XHS_SLOP)]
                if example == "冲就完了":       # also on the list of clichés, where it is an error: that finding stands
                    self.assertEqual([(category, matched) for category, matched, _ in found], [(g.GENERIC_XHS_SLOP, "冲就完了")])
                else:
                    self.assertEqual(found, [(g.COMPLIANCE_RISK, example, entry["reason_en"])], entry["id"])

    def test_ordinary_sentences_are_left_alone(self):
        for text in ORDINARY:
            self.assertEqual(of(lint(text, dna=DNA), g.COMPLIANCE_RISK, g.AI_TELL), [], text)

    def test_the_two_corrected_entries_match_their_examples_with_and_without_the_space(self):
        for text, matched in (("3 天见效", "3 天见效"), ("3天见效", "3天见效"), ("三天见效", "三天见效"), ("坚持用十天见效", "十天见效"),
                              ("最后 3 件", "最后 3 件"), ("最后3件", "最后3件"), ("最后三件", "最后三件")):
            self.assertEqual(of(lint(text), g.COMPLIANCE_RISK), [(g.COMPLIANCE_RISK, g.REVIEW, matched)], text)
        for text in ("用了三天", "三 天以后见效果", "最后三天", "最后 一 天"):
            self.assertEqual(of(lint(text), g.COMPLIANCE_RISK), [], text)

    def test_no_entry_takes_the_ranking_claim_away(self):
        found = of(lint("这是我用过最好用的面霜 / 销量第一", dna=DNA), g.COMPLIANCE_RISK)
        self.assertEqual(found, [(g.COMPLIANCE_RISK, g.REVIEW, "最"), (g.COMPLIANCE_RISK, g.REVIEW, "第一")])
        # the wording the law names is found as a whole, not as a bare 最
        self.assertEqual(of(lint("这款是最佳选择 / 最高级的面料"), g.COMPLIANCE_RISK),
                         [(g.COMPLIANCE_RISK, g.REVIEW, "最佳"), (g.COMPLIANCE_RISK, g.REVIEW, "最高级")])


class FixtureTest(unittest.TestCase):
    """What the scripts in tests/fixtures are told, with the list as it was and with the list as it is."""

    def test_the_new_list_changes_no_finding_on_the_fixtures(self):
        before = dict(SHIPPED, compliance=[{"pattern": p, "severity": s, "reason_en": r} for p, s, r in BEFORE])
        del before["ai_tells"]
        paths = sorted((ROOT / "tests" / "fixtures").glob("*.json"))
        self.assertEqual([path.name for path in paths], ["worked_example_script.json"])
        for path in paths:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            was = [(f.line_no, f.category, f.severity, f.matched, f.detail) for f in g.lint_script(fixture, DNA, lexicon=before)]
            now = [(f.line_no, f.category, f.severity, f.matched, f.detail) for f in g.lint_script(fixture, DNA, lexicon=SHIPPED)]
            self.assertEqual(now, was, path.name)
            self.assertEqual([(line_no, category, matched) for line_no, category, _, matched, _ in now], [
                (6, g.UNNATURAL_CODESWITCH, "jar"),
                (7, g.GENERIC_XHS_SLOP, "姐妹"),
                (8, g.CADENCE_TOO_LONG, "你们入冬还有什么救命面霜也可以种草给我哈"),
                (None, g.DURATION_OFF_BUDGET, "283 units"),
            ])


# ---- text made to take long -----------------------------------------------------------------------------------------------
class HostileInputTest(unittest.TestCase):
    GENERAL = {
        "digits": "1" * LONG, "numerals": "一二三四五六七八九十两" * (LONG // 11), "spaces": " " * LONG, "letters": "a" * LONG,
        "V": "V" * LONG, "No": "No" * (LONG // 2), "最": "最" * LONG, "最后": "最后" * (LONG // 2), "最后1": "最后1" * (LONG // 3),
        "最后 and digits": "最后" + "1" * LONG, "digit and space": "1 " * (LONG // 2), "最后 and space": "最后 " * (LONG // 3),
        "天见效": "天见效" * (LONG // 3), "随着": "随着" * (LONG // 2), "随着的": "随着的" * (LONG // 3), "不仅是": "不仅是" * (LONG // 3),
        "不仅仅是更": "不仅仅是更" * (LONG // 5), "进行": "进行" * (LONG // 2), "进行全面": "进行全面" * (LONG // 4), "加V": "加V" * (LONG // 2),
        "100 ": "100 " * (LONG // 4), "第一": "第一" * (LONG // 2), "唯一的小": "唯一的小" * (LONG // 4),
    }

    def timed(self, work, what):
        started = time.perf_counter()
        result = work()
        self.assertLess(time.perf_counter() - started, SLOW, what)
        return result

    def test_none_of_the_33_patterns_runs_long(self):
        entries = RULES + TELLS
        self.assertEqual(len(entries), 33)
        for entry in entries:
            rx = re.compile(entry["pattern"])
            texts = dict(self.GENERAL)
            for example in examples(entry):
                texts["its example, repeated: " + example] = example * (LONG // len(example))
                texts["its example's first character: " + example] = example[0] * LONG
                if len(example) > 1:
                    texts["its example without the end: " + example] = example[:-1] * (LONG // (len(example) - 1))
            for label, text in texts.items():
                self.timed(lambda: sum(1 for _ in rx.finditer(text)), "%s on %s" % (entry["id"], label))

    def test_the_pattern_that_was_corrected_grew_with_the_square_of_a_run_of_digits(self):
        # Why result_in_days was changed: "+" on the numerals the pattern starts with. Shown on short lines; on a
        # line of 80,000 digits it took over 5 seconds. Four times the work for twice the length is the square.
        was = re.compile("一次见效|立刻见效|当天见效|马上见效|[0-9一二三四五六七八九十两]+天见效")
        now = re.compile(next(entry["pattern"] for entry in RULES if entry["id"] == "result_in_days"))

        def cost(rx, n):
            started = time.perf_counter()
            self.assertEqual(list(rx.finditer("1" * n)), [])
            return time.perf_counter() - started

        self.assertGreater(cost(was, 16000), 3 * cost(was, 4000))             # 16 times as much if it is the square
        self.assertLess(cost(now, LONG), SLOW)
        self.assertLess(cost(now, LONG), cost(was, 16000))                    # six times the length in less time

    def test_a_whole_check_of_a_line_made_to_take_long_is_bounded(self):
        own = ["(a+)+$", "a{1000}", "最后1", "包邮", "a", "1", "最"] + ["词%d" % n for n in range(wordlist.MAX_OWN_PHRASES - 7)]
        lexicon = dict(SHIPPED, own_phrases=own)
        texts = dict(self.GENERAL, mixed="最后 1 天见效不仅是随着进行" * (LONG // 16), aab="aab" * (LONG // 3))
        for label, text in texts.items():
            self.timed(lambda: g.lint_script(script(text), DNA, lexicon=lexicon), label)

    def test_a_check_through_the_app_of_such_a_line_is_bounded(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            store = Store(tmp)
            wordlist.save(store, {"phrases": ["最", "a"]})
            for text in ("最" * LONG, "a" * LONG, "不仅是随着的" * (LONG // 6)):
                out = self.timed(lambda: checks.lint_payload(store, script(text)["script_lines"], 30), text[:6])
                self.assertIn("wordlist", out)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_the_overlap_check_keeps_what_it_kept_and_no_longer_compares_every_hit_with_every_other(self):
        def as_it_was(hits):
            hits.sort(key=lambda h: (g._SEVERITY_RANK[h[2].severity], -(h[1] - h[0]), h[0]))
            kept = []
            for start, end, finding in hits:
                if all(end <= ks or start >= ke for ks, ke, _ in kept):
                    kept.append((start, end, finding))
            kept.sort(key=lambda h: h[0])
            return [f for _, _, f in kept]

        chooser = random.Random(20261117)
        for _ in range(1500):
            size = chooser.choice((1, 3, 8, 30, 200))
            hits = []
            for n in range(chooser.randint(0, 40)):
                start = chooser.randrange(size)
                end = start + chooser.randint(1, max(1, size // chooser.choice((1, 2, 5))))
                hits.append((start, end, g.Finding(1, chooser.choice((g.COMPLIANCE_RISK, g.TRANSLATIONESE)),
                                                   chooser.choice((g.ERROR, g.REVIEW)), "m%d" % n, "d")))
            self.assertEqual(g._dedupe_overlaps(list(hits)), as_it_was(list(hits)))
        self.assertEqual(g._dedupe_overlaps([]), [])
        many = [(i, i + 1, g.Finding(1, g.COMPLIANCE_RISK, g.REVIEW, "最", "d")) for i in range(2 * LONG)]
        self.assertEqual(len(self.timed(lambda: g._dedupe_overlaps(many), "200,000 hits of one character")), 2 * LONG)


# ---- hints that a line may sound written ----------------------------------------------------------------------------------
class AiTellTest(StoreCase):
    def test_every_example_gives_a_hint_of_the_gentlest_kind(self):
        self.assertEqual(max(g._SEVERITY_RANK, key=g._SEVERITY_RANK.get), g.REVIEW)        # there is nothing gentler
        for entry in TELLS:
            for example in examples(entry):
                found = [f for f in lint(example) if f.category == g.AI_TELL]
                self.assertEqual([(f.severity, f.detail) for f in found], [(g.REVIEW, g.AI_TELL_DETAIL % entry["id"])], example)
                self.assertIn(found[0].matched, example)
        self.assertEqual([f.matched for f in lint("不仅是好看更是好用 / 随着直播的普及") if f.category == g.AI_TELL],
                         ["不仅是好看更是", "随着直播的普及"])

    def test_a_hint_is_not_looked_for_in_somebody_elses_words_or_in_a_cue(self):
        self.assertEqual(of(lint("这款堪称经典", line={"audio_mode": "LIVE_CLIP"}), g.AI_TELL), [])
        self.assertEqual(of(lint("今天聊面霜 [字幕：堪称经典]"), g.AI_TELL), [])
        self.assertEqual(of(lint("今天聊面霜 堪\u200b称经典"), g.AI_TELL), [(g.AI_TELL, g.REVIEW, "堪\u200b称")])   # a hidden character hides nothing

    def test_a_hint_and_a_rule_finding_never_take_each_other_away(self):
        self.assertEqual(of(lint("不仅是销量第一更是口碑第一"), g.COMPLIANCE_RISK, g.AI_TELL),
                         [(g.COMPLIANCE_RISK, g.REVIEW, "第一"), (g.COMPLIANCE_RISK, g.REVIEW, "第一"), (g.AI_TELL, g.REVIEW, "不仅是销量第一更是")])
        self.assertEqual(of(lint("随着美白的普及"), g.COMPLIANCE_RISK, g.AI_TELL),
                         [(g.COMPLIANCE_RISK, g.REVIEW, "美白"), (g.AI_TELL, g.REVIEW, "随着美白的普及")])
        self.assertEqual(of(lint("接下来让我们一起看看"), g.TRANSLATIONESE, g.AI_TELL),
                         [(g.TRANSLATIONESE, g.ERROR, "让我们一起"), (g.AI_TELL, g.REVIEW, "接下来让我们")])
        # two hints on the same words are one: the longer
        self.assertEqual(of(lint("不仅是标志着新时代的到来更是起点"), g.AI_TELL), [(g.AI_TELL, g.REVIEW, "不仅是标志着新时代的到来更是")])

    def test_what_a_hint_says_and_whom_it_credits(self):
        by_matched = {f["matched"]: f for f in self.check("这个面霜堪称经典嘛 / 值得一提的是它不贵")["findings"]}
        credited, own = by_matched["堪称"], by_matched["值得一提的是"]
        self.assertEqual((credited["category"], credited["severity"], credited["entry_id"], credited["own_phrase"]),
                         (g.AI_TELL, g.REVIEW, "tell_ad_copy", False))
        self.assertEqual(credited["lead_zh"], "「堪称」念出来可能像书面稿或机器写的，确认一下。")
        self.assertEqual(credited["basis_zh"], "这只是一条线索，不是规则：参照 Humanizer-zh 的第 16 类写法。")
        self.assertEqual(credited["basis_en"], "A clue, not a rule: after pattern 16 of Humanizer-zh.")
        self.assertEqual(credited["message_zh"], credited["lead_zh"] + credited["basis_zh"])
        self.assertEqual(credited["message_en"], credited["lead_en"] + " " + credited["basis_en"])
        self.assertEqual(own["basis_zh"], "这只是一条线索，不是规则：本项目自己加的，没有出处。")       # no source: none is named
        self.assertEqual(own["basis_en"], "A clue, not a rule: added by this project, with no source.")
        self.assertNotIn("Humanizer", own["message_zh"] + own["message_en"])

    def test_a_hint_blocks_nothing_and_is_never_counted_as_a_rule_being_broken(self):
        lines = [{"spoken_script_zh": "上周末跟我老公去 Costco 补货嘛 / 这个面霜堪称经典嘛"}]
        # the command line: findings, but no error status
        path = self.tmp / "draft.json"
        path.write_text(json.dumps({"script_lines": lines}, ensure_ascii=False), encoding="utf-8")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = g.main([str(path), "--dna", str(DNA_PATH)])
        self.assertEqual(status, 0)
        self.assertIn("0 error(s), 0 to review, 1 hint(s) that a line may sound written", printed.getvalue())
        # an AI app working through MCP is told there is nothing it must fix
        saved = mcp_server.t_save_script(self.store, {"script": {"script_lines": lines}, "notes": "面霜", "target_duration_sec": 5})
        self.assertEqual([(f["category"], f["severity"]) for f in saved["check"]["findings"]], [(g.AI_TELL, g.REVIEW)])
        self.assertIn("No must-fix", saved["next_step"])
        self.assertNotIn("update_script", saved["next_step"])
        # a trend keyword is neither marked as risky wording nor made unusable
        ranked = trends.score_trends([{"id": "k", "keyword": "堪称经典", "heat": None, "updated_at": NOW}], [], DNA, "balanced", NOW, SHIPPED)
        self.assertEqual((ranked["trends"][0]["flags"], ranked["trends"][0]["usable"]), ([], True))

    def test_a_proposal_with_a_hint_has_no_problem_counted_and_comes_after_one_without(self):
        stored = self.store.create_script(title="面霜", target_sec=30, lines=[{"spoken_script_zh": "今天聊一聊面霜"}])
        reply = {"suggestions": [{"line_no": 1, "spoken_script_zh": text}
                                 for text in ("这个面霜美白很好嘛", "这个面霜堪称经典嘛", "这个面霜真的很好用嘛")]}
        out = suggest.apply_suggest(self.store, reply, stored["id"], "rewrite", 0)["suggestions"]
        self.assertEqual([(item["text"], item["problems"], item["hints"]) for item in out], [
            ("这个面霜真的很好用嘛", {"errors": 0, "reviews": 0}, 0),
            ("这个面霜堪称经典嘛", {"errors": 0, "reviews": 0}, 1),             # shown with its hint, counted as clean
            ("这个面霜美白很好嘛", {"errors": 0, "reviews": 1}, 0),
        ])
        self.assertEqual([f["category"] for f in out[1]["findings"]], [g.AI_TELL])


# ---- what an advertising-rule finding says ------------------------------------------------------------------------------------
class FindingTextTest(StoreCase):
    def one(self, text):
        found = [f for f in self.check(text)["findings"] if f["category"] == g.COMPLIANCE_RISK]
        self.assertEqual(len(found), 1, text)
        return found[0]

    def test_a_finding_says_what_was_matched_why_what_it_rests_on_and_when_the_list_was_reviewed(self):
        f = self.one("换季退红消炎")
        self.assertEqual((f["severity"], f["matched"], f["entry_id"], f["own_phrase"]), (g.ERROR, "消炎", "medical_efficacy", False))
        self.assertEqual(f["lead_zh"], "「消炎」：医疗用语或治病功效的说法，建议换个说法。")
        self.assertEqual(f["basis_zh"], "依据：《中华人民共和国广告法》（2021 年修正）第十七条：医疗、药品、医疗器械以外的广告，"
                                        "不得涉及疾病治疗功能，不得使用医疗用语。词表上次整理：2026-11-16。")
        self.assertEqual(f["lead_en"], "\"消炎\": a medical term or a claim to treat. Better to say it another way.")
        self.assertEqual(f["basis_en"], "Basis: Advertising Law of the People's Republic of China (as amended in 2021), Article 17: "
                                        "Advertisements other than medical, drug and medical-device ones may not involve the treatment "
                                        "of disease or use medical terms. The list was last reviewed on 2026-11-16.")
        self.assertEqual(f["message_zh"], f["lead_zh"] + f["basis_zh"])
        self.assertEqual(f["message_en"], f["lead_en"] + " " + f["basis_en"])

    def test_wording_the_article_does_not_name_is_said_to_be_listed_by_analogy(self):
        named, analogy = self.one("这是国家级的"), self.one("第二天底妆100%服帖")
        self.assertNotIn("比照", named["message_zh"])
        self.assertNotIn("analogy", named["message_en"])
        self.assertIn("法条里没有点名这个说法，是比照列入的。", analogy["basis_zh"])
        self.assertIn("The law does not name this wording; it is listed here by analogy.", analogy["basis_en"])
        self.assertIn("第九条第（三）项", analogy["basis_zh"])

    def test_an_entry_without_a_source_says_so_and_cites_nothing(self):
        for text, entry_id, severity in (("加微聊一下", "hard_commerce", g.ERROR), ("这个美白很好", "whitening_antiaging", g.REVIEW),
                                         ("评论区扣一", "more_off_platform", g.REVIEW)):
            f = self.one(text)
            self.assertEqual((f["entry_id"], f["severity"]), (entry_id, severity))
            self.assertEqual(f["basis_zh"], "这一条没有核对过出处：它来自常见做法，没有对照过法规或平台规则。词表上次整理：2026-11-16。")
            self.assertEqual(f["basis_en"], "No source was checked for this entry: it rests on common practice and was not compared "
                                            "with a law or with platform rules. The list was last reviewed on 2026-11-16.")
            for cited in ("广告法", "依据：", "Advertising Law", "Article", "Basis:"):
                self.assertNotIn(cited, f["message_zh"] + f["message_en"], text)

    def test_every_entry_is_described_from_what_the_list_says_about_it(self):
        stats = {"units": 0, "budget": None}
        law = SHIPPED["_sources"]["ad_law"]
        for entry in RULES:
            finding = g.Finding(1, g.COMPLIANCE_RISK, entry["severity"], examples(entry)[0], entry["reason_en"])
            told = checks.describe(finding, stats, None, False, SHIPPED)
            self.assertEqual(told["entry_id"], entry["id"])
            self.assertIn(entry["why_zh"], told["lead_zh"])
            self.assertIn(entry["why_en"], told["lead_en"])
            self.assertIn("2026-11-16", told["basis_zh"])
            self.assertIn("2026-11-16", told["basis_en"])
            if entry["basis"] is None:
                self.assertIn("没有核对过出处", told["basis_zh"])
                self.assertNotIn(law["title_zh"], told["message_zh"])
            else:
                for language in ("zh", "en"):
                    for part in (law["title_" + language], entry["basis"]["where_" + language], entry["basis"]["says_" + language]):
                        self.assertIn(part, told["basis_" + language], entry["id"])
                self.assertEqual("比照" in told["basis_zh"], entry["basis"]["by_analogy"], entry["id"])
                self.assertEqual("by analogy" in told["basis_en"], entry["basis"]["by_analogy"], entry["id"])
            self.assertIsNone(ASSUMES.search(told["message_en"]), entry["id"])
        self.assertIsNone(ASSUMES.search(" ".join(text for name in dir(checks) if name.startswith("_") and name.isupper()
                                                  for text in _texts(getattr(checks, name)))))

    def test_without_the_list_a_finding_is_phrased_by_its_category_as_before(self):
        finding = g.Finding(1, g.COMPLIANCE_RISK, g.ERROR, "消炎", "Medical / efficacy claim (lexicon 3B, prompt 03)")
        for lexicon in (None, {}, {"compliance": [{"pattern": "消炎", "reason_en": "something else"}]}):
            told = checks.describe(finding, {"units": 0, "budget": None}, None, False, lexicon)
            self.assertEqual(told["message_zh"], "「消炎」可能违规（极限词或功效词），建议换个说法")
            self.assertNotIn("basis_zh", told)

    def test_a_hidden_character_in_the_flagged_text_is_still_remarked_on(self):
        f = self.one("换季退红消\u200b炎")
        self.assertTrue(f["hidden_characters"])
        self.assertEqual(f["lead_zh"], "「消\u200b炎」：医疗用语或治病功效的说法，建议换个说法。（这里夹了看不见的字符）")
        self.assertTrue(f["message_zh"].startswith(f["lead_zh"] + "依据："))

    def test_the_disclaimer_and_the_review_day_go_out_once_with_the_findings(self):
        for out in (self.check("今天去超市买菜"), self.check("换季退红消炎")):
            self.assertEqual(out["wordlist"], {
                "reviewed_on": "2026-11-16", "disclaimer_zh": SHIPPED["_disclaimer_zh"], "disclaimer_en": SHIPPED["_disclaimer_en"],
                "switched_off": 0, "own_phrases": 0, "unreadable": False})
        self.assertIn("不是法律意见", SHIPPED["_disclaimer_zh"])
        self.assertIn("not legal advice", SHIPPED["_disclaimer_en"])


def _texts(value):
    """Every text in a constant made of texts, tuples and dicts."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _texts(item)]
    return []


# ---- the creator's own phrases --------------------------------------------------------------------------------------------------
class OwnPhrasesTest(StoreCase):
    def own(self, phrases, *spoken, **line_overrides):
        found = g.lint_script(script(*spoken, **line_overrides), {}, lexicon={"own_phrases": phrases})
        return [(f.category, f.severity, f.matched, f.detail) for f in found if f.detail == g.OWN_PHRASE_DETAIL]

    def test_a_phrase_matches_only_itself_whatever_it_would_mean_as_a_pattern(self):
        hit = lambda text: (g.COMPLIANCE_RISK, g.REVIEW, text, g.OWN_PHRASE_DETAIL)
        cases = [("(.*)+$", "(.*)+$", ["随便什么都行", "", "(.*)", ".*+$"]), ("a{1000}", "a{1000}", ["a" * 1000, "a", "a{1000"]),
                 ("(a|b)+", "(a|b)+", ["ab", "a"]), ("[a-z", "[a-z", ["abc", "a"]), ("\\d", "\\d", ["3", "d"]), ("最.", "最.", ["最好", "最"]), ("^", "^", ["开头"]),
                 ("包邮|免运费", "包邮|免运费", ["包邮", "免运费"]), ("…", "…", ["等等"]), ("质地 (prefer: 轻)", "质地 (prefer: 轻)", ["质地"]),
                 ("XX", "XX", ["好的"])]
        for phrase, itself, others in cases:
            self.assertEqual(self.own([phrase], itself), [hit(itself)], phrase)
            self.assertEqual(self.own([phrase], "今天 %s 明天 %s" % (itself, itself)), [hit(itself), hit(itself)], phrase)      # each time it stands there
            for other in others:
                self.assertEqual(self.own([phrase], other), [], (phrase, other))

    def test_nothing_the_creator_typed_is_compiled_and_a_long_line_stays_quick(self):
        # As patterns the first four would hold a check for a very long time, and the last four are not patterns
        # at all: compiling one of them raises.
        phrases = ["(a+)+$", "(.*)*x", "a{1000}", "(?:a|aa)+b", "[", "(", "\\", "*"]
        for not_a_pattern in phrases[4:]:
            with self.assertRaises(re.error):
                re.compile(not_a_pattern)
        compiled = []
        real = re.compile

        def recording(pattern, flags=0):
            compiled.append(pattern)
            return real(pattern, flags)

        started = time.perf_counter()
        with mock.patch.object(g.re, "compile", recording):
            found = g.lint_script(script("a" * LONG, "(a+)+$ 和 ["), DNA, lexicon=dict(SHIPPED, own_phrases=phrases))
        self.assertLess(time.perf_counter() - started, SLOW)
        self.assertEqual([f.matched for f in found if f.detail == g.OWN_PHRASE_DETAIL], ["(a+)+$", "["])
        self.assertGreater(len(compiled), 30)                                    # the shipped patterns were compiled, as always
        shipped = ([entry["pattern"] for group in ("compliance", "ai_tells", "translationese") for entry in SHIPPED[group]]
                   + [rx.pattern for rx in self.shipped_phrase_patterns()])
        for pattern in compiled:
            self.assertIn(pattern, shipped)                                      # every pattern comes from the shipped list or the voice profile
            for phrase in phrases[:4]:
                self.assertNotIn(phrase, pattern)

    @staticmethod
    def shipped_phrase_patterns():
        """The patterns the built-in phrase lists and the example profile's ban lists are turned into: none comes from ``own_phrases``."""
        built_in = [g._plain_regex(phrase) for group in ("generic_slop", "audience_address_terms") for phrase in SHIPPED[group]]
        bans = [g._phrase_to_regex(phrase) for path, _, _ in g.DNA_BAN_LISTS for phrase in g._get(DNA, path, [])]
        return [rx for rx in built_in + bans if rx is not None]

    def test_letter_case_width_and_hidden_characters_make_no_difference_and_everything_else_does(self):
        hit = lambda text: (g.COMPLIANCE_RISK, g.REVIEW, text, g.OWN_PHRASE_DETAIL)
        self.assertEqual(self.own(["vx群"], "进VX群", "进ｖｘ群", "进v\u200bx群"), [hit("VX群"), hit("ｖｘ群"), hit("v\u200bx群")])
        self.assertEqual(self.own(["包邮"], "包 邮", "包，邮", "包/邮", "包\n邮"), [])          # nothing visible is skipped
        self.assertEqual(self.own(["no 1"], "我们是 NO 1", "NO1", "NO  1"), [hit("NO 1")])     # a space in the phrase is a space in the line
        self.assertEqual(self.own(["包邮"], "这款包邮", audio_mode="LIVE_CLIP"), [])            # somebody else's words
        self.assertEqual(self.own(["包邮"], "今天聊面霜 [花字：包邮]"), [])                      # a cue is not said
        self.assertEqual(self.own(["包邮", "包邮到家"], "全场包邮到家"), [hit("包邮到家")])      # of two of her own, the longer
        self.assertEqual(self.own(["\u200b", "", 5, None, ["包邮"]], "这款包邮"), [])            # nothing to look for
        self.assertEqual(self.own("包邮", "这款包邮"), [])                                       # not a list: not read

    def test_her_phrase_and_a_shipped_entry_never_take_each_other_away(self):
        wordlist.save(self.store, {"phrases": ["美白精华", "消炎", "堪称"]})
        self.assertEqual(self.flagged("这款美白精华可以消炎 / 堪称经典"), [
            (g.COMPLIANCE_RISK, g.REVIEW, "美白"), (g.COMPLIANCE_RISK, g.ERROR, "消炎"),
            (g.COMPLIANCE_RISK, g.REVIEW, "美白精华"), (g.COMPLIANCE_RISK, g.REVIEW, "消炎"), (g.COMPLIANCE_RISK, g.REVIEW, "堪称"),
            (g.AI_TELL, g.REVIEW, "堪称")])

    def test_a_finding_from_her_list_says_that_it_is_hers(self):
        wordlist.save(self.store, {"phrases": ["包邮"]})
        out = self.check("这款全场包邮")
        f = next(f for f in out["findings"] if f["matched"] == "包邮")
        self.assertEqual((f["category"], f["severity"], f["own_phrase"], f["entry_id"]), (g.COMPLIANCE_RISK, g.REVIEW, True, None))
        self.assertEqual(f["message_zh"], "「包邮」在你自己加的词表里，确认一下。这是你自己加的说法，不是自带词表里的条目。")
        self.assertEqual(f["message_en"], "\"包邮\" is on the creator's own word list; please check. "
                                          "This phrase was added by the creator; it is not an entry of the shipped list.")
        self.assertEqual(out["wordlist"]["own_phrases"], 1)
        self.store.save_creator(creator.normalize({"name": "小满"}))
        self.assertIn("\"包邮\" is on 小满's own word list", self.check("这款全场包邮")["findings"][0]["message_en"])
        for cited in ("广告法", "Advertising Law", "依据", "Basis"):                 # her phrase rests on no source, and none is named
            self.assertNotIn(cited, f["message_zh"] + f["message_en"])

    def test_phrases_are_tidied_kept_once_and_held_to_the_limits(self):
        shown = wordlist.save(self.store, {"phrases": ["  包 \n 邮 ", "VX群", "vx群", "ＶＸ群", "", "   ", "<b>加粗</b>"]})
        self.assertEqual(shown["phrases"], ["包 邮", "VX群", "<b>加粗</b>"])
        self.assertEqual(self.stored()[wordlist.KEY], {"off": [], "phrases": ["包 邮", "VX群", "<b>加粗</b>"]})
        self.assertEqual(shown["limits"], {"phrases": wordlist.MAX_OWN_PHRASES, "phrase_chars": wordlist.MAX_OWN_PHRASE_CHARS})
        before = self.stored()
        full = ["说法%d" % n for n in range(wordlist.MAX_OWN_PHRASES)]
        refused = [({"phrases": full + ["多一条"]}, "wordlist_too_many_phrases", {"limit": wordlist.MAX_OWN_PHRASES}),
                   ({"phrases": ["长" * (wordlist.MAX_OWN_PHRASE_CHARS + 1)]}, "wordlist_phrase_too_long", {"limit": wordlist.MAX_OWN_PHRASE_CHARS}),
                   ({"phrases": ["包邮", "\u200b\u200d"]}, "wordlist_phrase_uncheckable", {}),
                   # what stands in cue brackets is never said aloud, so such a phrase could never be found
                   ({"phrases": ["[赠品]"]}, "wordlist_phrase_uncheckable", {}), ({"phrases": ["买[赠品]送"]}, "wordlist_phrase_uncheckable", {}),
                   ({"phrases": ["【包邮】"]}, "wordlist_phrase_uncheckable", {}), ({"phrases": ["[a-z]+"]}, "wordlist_phrase_uncheckable", {}),
                   ({"phrases": "包邮"}, "bad_wordlist", {}), ({"phrases": [["包邮"]]}, "bad_wordlist", {}), ({"phrases": [5]}, "bad_wordlist", {}),
                   ({"off": "superlative_zui"}, "bad_wordlist", {}), ({"off": [5]}, "bad_wordlist", {}), ({"off": None}, "bad_wordlist", {}),
                   ({}, "bad_wordlist", {}), ({"other": 1}, "bad_wordlist", {}), ([], "bad_wordlist", {}), ("包邮", "bad_wordlist", {}), (None, "bad_wordlist", {})]
        for body, code, extra in refused:
            with self.assertRaises(wordlist.Refused) as raised:
                wordlist.save(self.store, body)
            self.assertEqual((raised.exception.code, raised.exception.extra), (code, extra), body)
            self.assertTrue(raised.exception.message and raised.exception.message_zh, code)
            self.assertEqual(self.stored(), before, body)                         # a refused change stores nothing
        self.assertEqual(wordlist.save(self.store, {"phrases": full})["phrases"], full)                                   # exactly the limit is kept
        self.assertEqual(wordlist.save(self.store, {"phrases": ["长" * wordlist.MAX_OWN_PHRASE_CHARS]})["phrases"], ["长" * wordlist.MAX_OWN_PHRASE_CHARS])


# ---- switching entries off and on, and what is stored -------------------------------------------------------------------------------
class SwitchTest(StoreCase):
    def test_with_nothing_stored_the_effective_list_is_the_shipped_one(self):
        effective = wordlist.effective(self.store)
        self.assertEqual({key: value for key, value in effective.items() if key not in ("own_phrases", wordlist.STATE)}, SHIPPED)
        self.assertEqual((effective["own_phrases"], effective[wordlist.STATE]), ([], {"switched_off": [], "unreadable": False}))
        self.assertNotIn(wordlist.KEY, self.stored() if self.store.config_path.exists() else {})

    def test_an_entry_can_be_switched_off_and_back_on_an_error_one_too(self):
        text = "换季退红消炎 / 这个美白很好"
        both = [(g.COMPLIANCE_RISK, g.ERROR, "消炎"), (g.COMPLIANCE_RISK, g.REVIEW, "美白")]
        self.assertEqual(self.flagged(text), both)
        shown = wordlist.save(self.store, {"off": ["medical_efficacy"]})
        self.assertEqual([entry["id"] for entry in shown["entries"] if not entry["on"]], ["medical_efficacy"])     # still listed, as off
        self.assertEqual((len(shown["entries"]), shown["switched_off"]), (33, 1))
        self.assertEqual(self.flagged(text), both[1:])
        self.assertEqual(self.check(text)["wordlist"]["switched_off"], 1)          # said wherever the findings are listed
        self.assertEqual(wordlist.effective(self.store)[wordlist.STATE]["switched_off"], ["medical_efficacy"])
        self.assertEqual(self.stored()[wordlist.KEY], {"off": ["medical_efficacy"], "phrases": []})
        wordlist.save(self.store, {"off": ["whitening_antiaging", "medical_efficacy"]})
        self.assertEqual(self.flagged(text), [])
        self.assertEqual(self.stored()[wordlist.KEY]["off"], ["medical_efficacy", "whitening_antiaging"])          # in the order of the list
        shown = wordlist.save(self.store, {"off": []})
        self.assertEqual((shown["switched_off"], all(entry["on"] for entry in shown["entries"])), (0, True))
        self.assertEqual(self.flagged(text), both)

    def test_a_hint_can_be_switched_off_like_any_other_entry(self):
        self.assertEqual(self.flagged("这个面霜堪称经典嘛"), [(g.AI_TELL, g.REVIEW, "堪称")])
        wordlist.save(self.store, {"off": ["tell_ad_copy"]})
        self.assertEqual(self.flagged("这个面霜堪称经典嘛"), [])
        self.assertEqual(self.flagged("这点至关重要"), [(g.AI_TELL, g.REVIEW, "至关重要")])       # the other hints stay

    def test_one_part_can_be_changed_and_the_other_stays(self):
        wordlist.save(self.store, {"off": ["superlative_zui"], "phrases": ["包邮"]})
        self.assertEqual(wordlist.save(self.store, {"phrases": ["包邮", "免运费"]})["switched_off"], 1)
        self.assertEqual(self.stored()[wordlist.KEY], {"off": ["superlative_zui"], "phrases": ["包邮", "免运费"]})
        self.assertEqual(wordlist.save(self.store, {"off": []})["phrases"], ["包邮", "免运费"])
        self.assertEqual(self.stored()[wordlist.KEY], {"off": [], "phrases": ["包邮", "免运费"]})

    def test_an_id_that_names_no_entry_is_ignored(self):
        shown = wordlist.save(self.store, {"off": ["no_such_entry", "superlative_zui", "superlative_zui", "", "translationese"]})
        self.assertEqual((shown["switched_off"], self.stored()[wordlist.KEY]["off"]), (1, ["superlative_zui"]))
        # stored by an earlier version that had an entry this one has not: the rest of the value still counts
        self.keep({"off": ["entry_of_an_earlier_version", "medical_efficacy"], "phrases": ["包邮"]})
        effective = wordlist.effective(self.store)
        self.assertEqual(effective[wordlist.STATE], {"switched_off": ["medical_efficacy"], "unreadable": False})
        self.assertEqual(len(effective["compliance"]), 21)
        self.assertEqual(self.flagged("换季退红消炎 / 全场包邮"), [(g.COMPLIANCE_RISK, g.REVIEW, "包邮")])
        self.assertEqual(wordlist.payload(self.store)["switched_off"], 1)
        wordlist.save(self.store, {"phrases": []})                               # the next save drops the id that names nothing
        self.assertEqual(self.stored()[wordlist.KEY], {"off": ["medical_efficacy"], "phrases": []})

    def test_a_stored_value_that_cannot_be_used_never_stops_a_check_and_the_shipped_list_is_read(self):
        broken = ["oops", 5, 1.5, True, [], ["medical_efficacy"], {"off": "medical_efficacy"}, {"off": [5]}, {"off": None},
                  {"off": {"medical_efficacy": True}}, {"phrases": "包邮"}, {"phrases": [5]}, {"phrases": [["包邮"]]}, {"phrases": None},
                  {"off": ["medical_efficacy"], "phrases": ["长" * (wordlist.MAX_OWN_PHRASE_CHARS + 1)]},
                  {"off": ["medical_efficacy"], "phrases": ["说法%d" % n for n in range(wordlist.MAX_OWN_PHRASES + 1)]},
                  {"off": ["medical_efficacy"], "phrases": ["包邮", ""]}]
        for value in broken:
            self.keep(value)
            effective = wordlist.effective(self.store)
            self.assertEqual({key: v for key, v in effective.items() if key not in ("own_phrases", wordlist.STATE)}, SHIPPED, value)
            self.assertEqual((effective["own_phrases"], effective[wordlist.STATE]), ([], {"switched_off": [], "unreadable": True}), value)
            out = self.check("换季退红消炎 / 全场包邮")
            self.assertEqual([f["matched"] for f in out["findings"] if f["category"] == g.COMPLIANCE_RISK], ["消炎"], value)
            self.assertEqual((out["wordlist"]["unreadable"], out["wordlist"]["switched_off"], out["wordlist"]["own_phrases"]), (True, 0, 0), value)
            shown = wordlist.payload(self.store)
            self.assertEqual((shown["unreadable"], shown["switched_off"], shown["phrases"], all(e["on"] for e in shown["entries"])),
                             (True, 0, [], True), value)
            self.assertTrue(trends.payload(self.store)["wordlist"]["unreadable"], value)
        for value in (None, {}, {"off": []}, {"phrases": []}, {"off": [], "phrases": [], "added_by_a_later_version": 1}):
            self.keep(value)
            self.assertFalse(wordlist.effective(self.store)[wordlist.STATE]["unreadable"], value)

    def test_saving_again_repairs_a_value_that_could_not_be_used(self):
        self.keep({"off": "medical_efficacy", "phrases": ["包邮"]})
        shown = wordlist.save(self.store, {"off": ["superlative_zui"]})          # the part that was not sent starts empty
        self.assertEqual((shown["unreadable"], shown["switched_off"], shown["phrases"]), (False, 1, []))
        self.assertEqual(self.stored()[wordlist.KEY], {"off": ["superlative_zui"], "phrases": []})

    def test_only_its_own_key_of_the_settings_is_written(self):
        others = {"provider": "gemini", "model": "gem-pro", "keys": {"gemini": "SECRET-KEY", "openai": "sk-2"}, "local_base_url": "",
                  "trend_weight": "mine", "a_setting_of_a_later_version": {"kept": [1, 2, {"deep": "值"}]}}
        self.store.update_config(lambda config: config.update(others))
        wordlist.save(self.store, {"off": ["superlative_zui"], "phrases": ["包邮"]})
        wordlist.save(self.store, {"off": []})
        stored = self.stored()
        self.assertEqual(stored.pop(wordlist.KEY), {"off": [], "phrases": ["包邮"]})
        self.assertEqual(stored, others)
        self.assertEqual(stat.S_IMODE(self.store.config_path.stat().st_mode), 0o600)
        self.assertEqual(self.store.public_settings()["provider"], "gemini")
        self.assertNotIn("SECRET-KEY", json.dumps(wordlist.payload(self.store)))

    def test_the_list_of_a_data_folder_is_read_without_creating_anything_there(self):
        wordlist.save(self.store, {"off": ["medical_efficacy"], "phrases": ["包邮"]})
        listing = sorted(path.name for path in self.tmp.iterdir())
        effective = wordlist.effective_in(self.tmp)
        self.assertEqual((effective[wordlist.STATE]["switched_off"], effective["own_phrases"]), (["medical_efficacy"], ["包邮"]))
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), listing)
        for folder in (self.tmp / "not-there", self.tmp / "empty"):
            (self.tmp / "empty").mkdir(exist_ok=True)
            plain = wordlist.effective_in(folder)
            self.assertEqual((len(plain["compliance"]), plain["own_phrases"], plain[wordlist.STATE]["unreadable"]), (22, [], False))
        self.assertFalse((self.tmp / "not-there").exists())
        self.assertEqual(list((self.tmp / "empty").iterdir()), [])
        (self.tmp / "empty" / "config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(len(wordlist.effective_in(self.tmp / "empty")["compliance"]), 22)


# ---- the two routes --------------------------------------------------------------------------------------------------------------
class RouteTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(0, self.store, None)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        hdrs = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res.status, json.loads(payload.decode("utf-8"))

    def test_the_list_as_the_settings_page_gets_it(self):
        status, shown = self.call("GET", "/api/wordlist")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(shown), ["disclaimer_en", "disclaimer_zh", "entries", "limits", "phrases", "reviewed_on", "sources",
                                         "switched_off", "unreadable"])
        self.assertEqual((shown["reviewed_on"], shown["disclaimer_zh"], shown["disclaimer_en"]),
                         ("2026-11-16", SHIPPED["_disclaimer_zh"], SHIPPED["_disclaimer_en"]))
        self.assertEqual(shown["sources"], SHIPPED["_sources"])
        self.assertEqual((shown["phrases"], shown["switched_off"], shown["unreadable"]), ([], 0, False))
        self.assertEqual(shown["limits"], {"phrases": 50, "phrase_chars": 40})
        self.assertEqual([(e["id"], e["group"]) for e in shown["entries"]],
                         [(e["id"], "compliance") for e in RULES] + [(e["id"], "ai_tells") for e in TELLS])
        for given, entry in zip(shown["entries"], RULES + TELLS):
            self.assertNotIn("pattern", given)                                    # the page shows the examples, never the pattern
            self.assertEqual((given["on"], given["shows"]), (True, entry["shows"]))
        rule, tell = shown["entries"][3], shown["entries"][22]
        self.assertEqual(rule, {"id": "medical_efficacy", "group": "compliance", "severity": "error", "on": True, "shows": RULES[3]["shows"],
                                "why_zh": RULES[3]["why_zh"], "why_en": RULES[3]["why_en"], "note_zh": None, "note_en": None,
                                "basis": RULES[3]["basis"]})
        self.assertEqual(tell, {"id": "tell_not_only_but", "group": "ai_tells", "severity": "review", "on": True,
                                "shows": "不仅是……更是……", "source": "humanizer_zh", "pattern_no": 1})
        self.assertEqual(shown["entries"][6]["note_zh"], RULES[6]["note_zh"])
        self.assertIsNone(shown["entries"][5]["basis"])                           # nothing was read for it, and nothing is cited

    def test_a_change_is_stored_and_the_next_check_reads_it(self):
        lines = script("换季退红消炎 / 全场包邮")["script_lines"]
        matched = lambda: [f["matched"] for f in self.call("POST", "/api/lint", {"lines": lines})[1]["findings"] if f["line_no"] == 1]
        self.assertEqual(matched(), ["消炎"])
        status, shown = self.call("PUT", "/api/wordlist", {"off": ["medical_efficacy"], "phrases": ["包邮", "<img src=x onerror=alert(1)>"]})
        self.assertEqual((status, shown["switched_off"], shown["phrases"]), (200, 1, ["包邮", "<img src=x onerror=alert(1)>"]))
        self.assertEqual(self.call("GET", "/api/wordlist")[1], shown)
        self.assertEqual(matched(), ["包邮"])
        _, lint_answer = self.call("POST", "/api/lint", {"lines": lines})
        self.assertEqual((lint_answer["wordlist"]["switched_off"], lint_answer["wordlist"]["own_phrases"]), (1, 2))
        self.assertEqual(self.call("PUT", "/api/wordlist", {"off": []})[1]["switched_off"], 0)
        self.assertEqual(matched(), ["消炎", "包邮"])

    def test_what_cannot_be_stored_is_refused_in_both_languages_and_nothing_changes(self):
        self.call("PUT", "/api/wordlist", {"phrases": ["包邮"]})
        before = self.stored()
        cases = [({"phrases": ["长" * 41]}, "wordlist_phrase_too_long", 40), ({"phrases": ["说法%d" % n for n in range(51)]}, "wordlist_too_many_phrases", 50),
                 ({"phrases": ["\u200b"]}, "wordlist_phrase_uncheckable", None), ({"phrases": "包邮"}, "bad_wordlist", None),
                 ({"off": "medical_efficacy"}, "bad_wordlist", None), ({}, "bad_wordlist", None)]
        for body, code, limit in cases:
            status, answer = self.call("PUT", "/api/wordlist", body)
            self.assertEqual((status, answer["error"], answer.get("limit")), (400, code, limit), body)
            self.assertTrue(answer["message"] and answer["message_zh"], code)
            self.assertRegex(answer["message_zh"], "[\u4e00-\u9fff]")
            self.assertEqual(self.stored(), before, body)
        # like every write: JSON only, and not from a page of another site
        self.assertEqual(self.call("PUT", "/api/wordlist", raw=b"off=medical_efficacy", headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.assertEqual(self.call("PUT", "/api/wordlist", raw=b"[1]", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/wordlist", {"off": ["medical_efficacy"]}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/wordlist", {"off": []})[0], 404)
        self.assertEqual(self.call("DELETE", "/api/wordlist")[0], 404)
        self.assertEqual(self.stored(), before)

    def test_a_value_that_cannot_be_used_is_said_so_by_both_routes_and_by_the_check(self):
        self.keep({"off": "medical_efficacy"})
        self.assertTrue(self.call("GET", "/api/wordlist")[1]["unreadable"])
        _, answer = self.call("POST", "/api/lint", {"lines": script("换季退红消炎")["script_lines"]})
        self.assertEqual(([f["matched"] for f in answer["findings"] if f["line_no"] == 1], answer["wordlist"]["unreadable"]), (["消炎"], True))
        self.assertFalse(self.call("PUT", "/api/wordlist", {"off": []})[1]["unreadable"])

    def test_the_page_script_is_served_and_loaded_before_the_pages(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", "/wordlist.js")
        res = conn.getresponse()
        body = res.read()
        conn.close()
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, (WEB / "wordlist.js").read_bytes())
        index = (WEB / "index.html").read_text(encoding="utf-8")
        order = [index.index('src="/%s.js"' % name) for name in ("app", "craft", "kwimport", "wordlist", "pages")]
        self.assertEqual(order, sorted(order))


# ---- every check reads the list as the creator keeps it ----------------------------------------------------------------------------
class EveryCallerTest(StoreCase):
    TEXT = "换季退红消炎 / 全场包邮"

    def setUp(self):
        super().setUp()
        wordlist.save(self.store, {"off": ["medical_efficacy"], "phrases": ["包邮"]})

    def matched(self, findings):
        return [f["matched"] for f in findings if f["line_no"] is not None]

    @staticmethod
    def calls_of(source, name):
        """The text of every call of ``name`` in ``source``, from the name to the bracket that closes the call."""
        found = []
        for at in (m.start() for m in re.finditer(r"\b%s\(" % name, source)):
            if source[:at].rstrip(" ").endswith("def"):
                continue
            depth, end = 0, at + len(name)
            while True:
                depth += {"(": 1, ")": -1}.get(source[end], 0)
                end += 1
                if depth == 0:
                    break
            found.append(source[at:end])
        return found

    def test_no_code_of_the_app_checks_text_without_saying_which_list(self):
        callers, loaders = {}, {}
        for path in sorted((ROOT / "studio").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            calls = self.calls_of(source, "lint_script")
            if calls:
                callers[path.name] = calls
            if self.calls_of(source, "load_lexicon"):
                loaders[path.name] = len(self.calls_of(source, "load_lexicon"))
        # Who checks text. A new caller has to be added here, with the list it reads.
        self.assertEqual(sorted(callers), ["checks.py", "creator.py", "guardrails.py", "trends.py", "wordlist.py"])
        for name, calls in callers.items():
            self.assertEqual(len(calls), 1, name)
            self.assertIn("lexicon=", calls[0], name)                             # none leaves the choice of list to the default
        self.assertIn("lexicon=lexicon", callers["checks.py"][0])                 # wordlist.effective(store), two lines above
        self.assertIn("lexicon={}", callers["creator.py"][0])                     # only the creator's never-say phrases: no word list at all
        self.assertIn('lexicon={"own_phrases": [phrase]}', callers["wordlist.py"][0])   # asks whether one new phrase could ever be found
        checks_source = (ROOT / "studio" / "checks.py").read_text(encoding="utf-8")
        self.assertIn("lexicon = wordlist.effective(store)", checks_source)
        trends_source = (ROOT / "studio" / "trends.py").read_text(encoding="utf-8")
        self.assertIn("lexicon = wordlist.effective(store)", trends_source)
        self.assertIn("score_trends(store.list_trends(), store.list_samples(), dna, weight, now, lexicon)", trends_source)
        # Who reads the shipped file: the linter itself (its default and its command line) and wordlist.shipped.
        self.assertEqual(loaders, {"guardrails.py": 2, "wordlist.py": 1})
        for name in ("server.py", "mcp_server.py", "suggest.py", "pipeline.py"):
            source = (ROOT / "studio" / name).read_text(encoding="utf-8")
            self.assertNotIn("lexicon", source, name)                             # they reach findings through checks.lint_payload or trends.payload

    def test_the_editor_and_the_mcp_tools(self):
        lines = script(self.TEXT)["script_lines"]
        self.assertEqual(self.matched(checks.lint_payload(self.store, lines, 30)["findings"]), ["包邮"])
        checked = mcp_server.t_check_script(self.store, {"script_lines": lines, "target_duration_sec": 30})
        self.assertEqual((self.matched(checked["findings"]), checked["wordlist"]["switched_off"], checked["wordlist"]["own_phrases"]), (["包邮"], 1, 1))
        saved = mcp_server.t_save_script(self.store, {"script": {"script_lines": [{"spoken_script_zh": self.TEXT}]}, "notes": "面霜"})
        self.assertEqual((self.matched(saved["check"]["findings"]), saved["check"]["wordlist"]["switched_off"]), (["包邮"], 1))
        read = mcp_server.t_get_script(self.store, {"script_id": saved["script_id"]})
        self.assertEqual(self.matched(read["check"]["findings"]), ["包邮"])
        updated = mcp_server.t_update_script(self.store, {"script_id": saved["script_id"], "script_lines": [{"spoken_script_zh": "这款可以消炎 / 买就包邮"}]})
        self.assertEqual(self.matched(updated["check"]["findings"]), ["包邮"])
        wordlist.save(self.store, {"off": [], "phrases": []})
        self.assertEqual(self.matched(mcp_server.t_get_script(self.store, {"script_id": saved["script_id"]})["check"]["findings"]), ["消炎"])

    def test_proposals_for_a_line(self):
        stored = self.store.create_script(title="面霜", target_sec=30, lines=[{"spoken_script_zh": "今天聊一聊面霜"}])
        reply = {"suggestions": [{"line_no": 1, "spoken_script_zh": "这款可以消炎嘛"}, {"line_no": 1, "spoken_script_zh": "这款全场包邮嘛"}]}
        out = suggest.apply_suggest(self.store, reply, stored["id"], "rewrite", 0)["suggestions"]
        self.assertEqual({item["text"]: [f["matched"] for f in item["findings"]] for item in out},
                         {"这款可以消炎嘛": [], "这款全场包邮嘛": ["包邮"]})
        wordlist.save(self.store, {"off": [], "phrases": []})
        out = suggest.apply_suggest(self.store, reply, stored["id"], "rewrite", 0)["suggestions"]
        self.assertEqual({item["text"]: [(f["matched"], f["severity"]) for f in item["findings"]] for item in out},
                         {"这款可以消炎嘛": [("消炎", g.ERROR)], "这款全场包邮嘛": []})

    def test_trend_keywords(self):
        self.store.upsert_trends([{"keyword": "消炎面膜"}, {"keyword": "包邮好物"}], source="manual")
        flags = lambda: {t["keyword"]: t["flags"] for t in trends.payload(self.store)["trends"]}
        self.assertEqual(flags(), {"消炎面膜": [], "包邮好物": []})                 # the entry is off; her own phrase only asks, so it marks nothing
        self.assertEqual(trends.payload(self.store)["wordlist"]["switched_off"], 1)
        self.assertEqual(mcp_server.t_list_trends(self.store, {})["trends"][0]["flags"], [])
        wordlist.save(self.store, {"off": []})
        self.assertEqual(flags(), {"消炎面膜": ["compliance"], "包邮好物": []})
        # called with no list, as code without a data folder does, the shipped one is read
        rows = [{"id": "k", "keyword": "消炎面膜", "heat": None, "updated_at": NOW}]
        self.assertEqual(trends.score_trends(rows, [], DNA, "balanced", NOW)["trends"][0]["flags"], ["compliance"])
        off = dict(SHIPPED, compliance=[entry for entry in RULES if entry["id"] != "medical_efficacy"])
        self.assertEqual(trends.score_trends(rows, [], DNA, "balanced", NOW, off)["trends"][0]["flags"], [])

    def test_the_command_line_reads_it_when_the_data_folder_is_named(self):
        folder = Path(tempfile.mkdtemp())
        try:
            path = folder / "draft.json"
            path.write_text(json.dumps(script(self.TEXT), ensure_ascii=False), encoding="utf-8")

            def run(*extra):
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    status = g.main([str(path), "--dna", str(DNA_PATH), *extra])
                return status, printed.getvalue()

            status, text = run("--json")
            self.assertEqual((status, [f["matched"] for f in json.loads(text)]), (1, ["消炎"]))
            status, text = run("--json", "--data-dir", str(self.tmp))
            self.assertEqual((status, [(f["matched"], f["severity"]) for f in json.loads(text)]), (0, [("包邮", "review")]))
            status, text = run()
            self.assertIn("Word list: as shipped (last reviewed 2026-11-16).", text)
            self.assertIn("--data-dir", text)
            self.assertIn(SHIPPED["_disclaimer_en"], text)
            status, text = run("--data-dir", str(self.tmp))
            self.assertIn("1 shipped entries switched off, 1 own phrases.", text)
            self.assertIn(SHIPPED["_disclaimer_en"], text)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["draft.json"])
        finally:
            shutil.rmtree(str(folder), ignore_errors=True)

    def test_the_phrases_never_to_be_used_are_a_different_list_and_are_left_as_they_were(self):
        self.store.save_creator(creator.normalize({"name": "小满", "never": ["宝子们"]}))
        found = {f["matched"]: f for f in self.check("宝 子、们看过来 / 全场包 邮 / 全场包邮")["findings"] if f["line_no"] is not None}
        self.assertEqual(sorted(found), ["包邮", "宝 子、们"])
        self.assertEqual((found["宝 子、们"]["severity"], found["宝 子、们"]["message_zh"]), (g.ERROR, "「宝 子、们」是你自己列的不说的话"))
        self.assertEqual((found["包邮"]["severity"], found["包邮"]["own_phrase"]), (g.REVIEW, True))
        self.assertNotIn("basis_zh", found["宝 子、们"])
        who = self.store.load_creator()
        self.assertEqual(creator.never_hits(script("宝子们好 / 换季退红消炎"), who), [(1, "宝子们")])      # no word list is read for this
        self.assertEqual(creator.never_phrases(who), ["宝子们"])                    # and nothing of the word list is stored with the profile
        self.assertNotIn("包邮", json.dumps(creator.prompt_block(who), ensure_ascii=False))


# ---- what an AI app working through MCP is told -----------------------------------------------------------------------------------
class McpTest(unittest.TestCase):
    def test_an_ai_app_is_told_what_the_findings_are_and_cannot_change_the_list(self):
        told = mcp_server.INSTRUCTIONS
        for part in ("AI_TELL is only a hint", "never must-fix and never a rule", "what it rests on, or that no source was checked",
                     "not legal advice", "never say that a script with no finding is allowed", "You cannot change the word list"):
            self.assertIn(part, told)
        self.assertIsNone(ASSUMES.search(told))
        public = json.dumps([tool["public"] for tool in mcp_server.TOOLS.values()], ensure_ascii=False)
        self.assertEqual([name for name in mcp_server.TOOLS if "wordlist" in name or "word_list" in name or "lexicon" in name], [])
        self.assertNotIn("/api/wordlist", public)


# ---- the page ----------------------------------------------------------------------------------------------------------------------
class PageFilesTest(unittest.TestCase):
    def setUp(self):
        self.script = (WEB / "wordlist.js").read_text(encoding="utf-8")

    def test_every_text_of_the_page_is_there_in_both_languages(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        keys = {language: set(re.findall(r"\b(wl_[a-z_]+): '", body)) for language, body in blocks}
        self.assertGreater(len(keys["zh"]), 35)
        self.assertEqual(keys["zh"], {key for key in keys["en"] if not key.endswith("_one")})      # English alone has singular forms
        used = set(re.findall(r"""['"](wl_[a-z_]+)['"]""", self.script))
        self.assertEqual(used - keys["zh"], set())
        self.assertEqual(keys["zh"] - used, set())                               # and none is left over
        zh = dict(re.findall(r"\b(wl_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(wl_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertIn("不等于", zh["wl_no_finding"])
        self.assertIn("does not mean the wording is allowed", en["wl_no_finding"])
        self.assertIn("小红书自己的规则和化妆品命名的规定都没有读过", zh["wl_group_none_d"])
        self.assertIn("were not read", en["wl_group_none_d"])
        self.assertIn("坚决不说的话", zh["wl_own_differs"])
        self.assertIn("phrases never to be used", en["wl_own_differs"])
        self.assertIsNone(ASSUMES.search(" ".join(en.values())))

    def test_nothing_about_a_law_is_written_into_the_page_and_nothing_is_asked_of_another_address(self):
        for said_by_the_list_only in ("广告法", "Advertising Law", "第九条", "Article", "Humanizer", "2026-11-16"):
            self.assertNotIn(said_by_the_list_only, self.script)
        self.assertEqual(sorted(set(re.findall(r"api\('(?:GET|PUT)', '([^']+)'", self.script))), ["/api/wordlist"])
        for word in ("fetch(", "XMLHttpRequest", "http://", "https://", "localStorage", "manualBridge", "aiRun", "innerText =", "RegExp("):
            self.assertNotIn(word, self.script, word)
        self.assertIn("/^https:\\/\\//.test(source.url || '')", self.script)     # an address from the list becomes a link only if it is https
        self.assertIn('rel="noopener noreferrer"', self.script)

    def test_the_other_scripts_only_call_into_it(self):
        app, craft, pages, discover = ((WEB / name).read_text(encoding="utf-8") for name in ("app.js", "craft.js", "pages.js", "discover.js"))
        for other in (app, craft, pages, discover):
            self.assertIsNone(re.search(r"\bwl_[a-z]", other))                    # its texts live in its own file
        self.assertEqual((pages.count("wlPaintSettings("), pages.count('<div id="wl-box"></div>'), pages.count("wlNoteHtml(")), (1, 1, 1))
        # Two pages show a keyword as the word list marks it. Each says the note about the list once.
        marking = sorted(path.name for path in WEB.glob("*.js") if "flags.includes('compliance')" in path.read_text(encoding="utf-8"))
        self.assertEqual(marking, ["discover.js", "pages.js"])
        self.assertEqual(discover.count("wlNoteHtml("), 1)
        self.assertEqual((app.count("wlIssueHtml"), app.count("wlPaintNote("), app.count('<div id="wl-note"></div>')), (1, 1, 1))
        self.assertEqual(craft.count("wlIssueHtml"), 1)
        for other in (app, craft):
            self.assertIsNone(re.search(r'class="issue[ "]', other))              # a finding is drawn in one place ("issues" is the list of them)
        self.assertEqual(self.script.count('class="issue '), 1)
        self.assertNotIn("👍", app)                                               # nothing found is not a thumbs-up
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn(".issue.tell {", css)
        self.assertIn(".issue.error {", css)

    def test_the_limits_the_page_shows_are_the_ones_the_server_sends(self):
        self.assertIn('maxlength="${Number(data.limits.phrase_chars)}"', self.script)
        self.assertIn("data.phrases.length >= data.limits.phrases", self.script)
        self.assertIsNone(re.search(r"\b(?:40|50)\b", self.script))               # neither limit is written into the page


if __name__ == "__main__":
    unittest.main()
