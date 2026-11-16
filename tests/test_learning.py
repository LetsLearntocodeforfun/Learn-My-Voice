"""Tests for learning from the creator's edits (studio/learning.py).

Run from the project root with: python3 -m unittest discover -t . -s tests
"""
import copy
import json
import re
import unittest
from pathlib import Path

from studio import learning, textstats

ROOT = Path(__file__).resolve().parent.parent
DRAFT_PROMPT = ROOT / "prompts" / "04_script_generator_normal_and_sponsored.md"


def lines(*spoken):
    return [{"line_no": n, "audio_mode": "VOICEOVER", "spoken_script_zh": text} for n, text in enumerate(spoken, start=1)]


def brief(changes):
    return [(c["kind"], c["ai"], c["final"]) for c in changes]


class LineChangesTest(unittest.TestCase):
    def test_same_words_are_not_a_change(self):
        ai = lines(
            "上周末跟我老公去 Costco 补货嘛 / 结果人特别多",
            "你们看 / 他又开始了哈",
            "排了半小时啦 [轻笑]",
            "顺路去Ｃｏｓｔｃｏ买花",
        )
        final = lines(
            "你们看，他又开始了哈！",                            # moved up, breath mark swapped for punctuation
            "上周末跟我老公去Costco补货嘛，结果人特别多",        # spacing and breath mark only
            "排了半小时啦",                                      # the cue is a direction, not spoken words
            "顺路去 Costco 买花",                                # full-width letters retyped
        )
        self.assertEqual(learning.line_changes(ai, final), [])
        self.assertEqual(learning.line_changes(ai, ai), [])

    def test_rewritten_line_shows_what_went_out_and_what_came_in(self):
        changes = learning.line_changes(lines("这个面霜真的绝绝子"), lines("这个面霜真的蛮舒服的"))
        self.assertEqual(changes, [{
            "kind": "rewritten", "ai": "这个面霜真的绝绝子", "final": "这个面霜真的蛮舒服的",
            "removed": ["绝绝子"], "added": ["蛮舒服的"],          # 这个面霜真的 was kept
            "ai_line_no": 1, "final_line_no": 1,
        }])

    def test_one_surviving_character_does_not_split_a_rewrite(self):
        # 绝 happens to appear in both versions; reporting 真的 and 绝子 as two removals would be word fragments
        change = learning.line_changes(lines("这个真的绝绝子"), lines("这个也太绝了"))[0]
        self.assertEqual((change["kind"], change["removed"], change["added"]), ("rewritten", ["真的绝绝子"], ["也太绝了"]))

    def test_one_changed_character_is_a_change_but_not_a_phrase(self):
        change = learning.line_changes(lines("排了半小时啦"), lines("排了半小时哈"))[0]
        self.assertEqual(brief([change]), [("rewritten", "排了半小时啦", "排了半小时哈")])            # the line did change
        self.assertEqual((change["removed"], change["added"]), ([], []))                              # 啦 and 哈 alone are noise

    def test_latin_words_stay_whole_and_read_as_written(self):
        change = learning.line_changes(lines("周末去 weekend market"), lines("周末去 weekday market"))[0]
        self.assertEqual((change["removed"], change["added"]), (["weekend"], ["weekday"]))            # not "end" and "day"
        change = learning.line_changes(lines("顺路去 Whole Foods 买花"), lines("顺路去 Trader Joe's 买花"))[0]
        self.assertEqual((change["removed"], change["added"]), (["Whole Foods"], ["Trader Joe's"]))   # space and apostrophe kept

    def test_phrases_stop_at_a_breath(self):
        change = learning.line_changes(lines("早上喝咖啡 / 真的绝绝子 / 氛围感拉满，冲就完了"),
                                       lines("早上喝咖啡 / 真的蛮舒服的 / 很松弛，慢慢来"))[0]
        self.assertEqual(change["removed"], ["绝绝子", "氛围感拉满", "冲就完了"])                     # never one phrase across a pause
        self.assertEqual(change["added"], ["蛮舒服的", "很松弛", "慢慢来"])

    def test_deleted_and_added_lines_list_their_breaths(self):
        ai = lines("家人们谁懂啊 / 这个面霜也太好用了吧", "我最近天天在用", "好啦下期见")
        final = lines("我最近天天在用", "好啦下期见", "对了 / 瓶子有点沉 / 出差不太方便带")
        self.assertEqual(learning.line_changes(ai, final), [
            {"kind": "deleted", "ai": "家人们谁懂啊 / 这个面霜也太好用了吧", "final": "",
             "removed": ["家人们谁懂啊", "这个面霜也太好用了吧"], "added": [], "ai_line_no": 1, "final_line_no": None},
            {"kind": "added", "ai": "", "final": "对了 / 瓶子有点沉 / 出差不太方便带",
             "removed": [], "added": ["对了", "瓶子有点沉", "出差不太方便带"], "ai_line_no": None, "final_line_no": 3},
        ])                                                    # opposite ends of the script and nothing in common: not a rewrite

    def test_line_replaced_in_place_is_a_rewrite_even_with_no_words_in_common(self):
        ai = lines("上周末去超市补货", "质地非常轻盈好吸收", "好啦下期见")
        final = lines("上周末去超市补货", "推开水水的，一点不糊脸", "好啦下期见")
        self.assertEqual(learning.line_changes(ai, final), [{
            "kind": "rewritten", "ai": "质地非常轻盈好吸收", "final": "推开水水的，一点不糊脸",
            "removed": ["质地非常轻盈好吸收"], "added": ["推开水水的", "一点不糊脸"],
            "ai_line_no": 2, "final_line_no": 2,              # one line out, one line in, between the same two neighbours
        }])

    def test_two_lines_replaced_by_one_are_not_paired_by_guesswork(self):
        ai = lines("上周末去超市补货", "今天给大家分享一个超好用的面霜", "质地非常轻盈好吸收", "好啦下期见")
        final = lines("上周末去超市补货", "最近干皮全靠这一罐", "好啦下期见")
        self.assertEqual(brief(learning.line_changes(ai, final)), [
            ("deleted", "今天给大家分享一个超好用的面霜", ""),
            ("deleted", "质地非常轻盈好吸收", ""),            # which of the two became the new line cannot be known
            ("added", "", "最近干皮全靠这一罐"),
        ])

    def test_moved_line_is_unchanged_and_moved_rewrite_is_still_recognised(self):
        ai = lines("结果某人又走不动道哈", "这个面霜真的绝绝子", "好啦下期见")
        final = lines("这个面霜真的蛮舒服的", "好啦下期见", "结果某人又走不动道哈")
        changes = learning.line_changes(ai, final)
        self.assertEqual(brief(changes), [("rewritten", "这个面霜真的绝绝子", "这个面霜真的蛮舒服的")])
        self.assertEqual((changes[0]["ai_line_no"], changes[0]["final_line_no"]), (2, 1))

    def test_moved_line_does_not_hide_a_replacement_in_place(self):
        ai = lines("上周末去超市补货", "质地非常轻盈好吸收", "回家路上下雨了", "你们敢信这是两天的量吗")
        final = lines("你们敢信这是两天的量吗", "上周末去超市补货", "推开水水的，一点不糊脸", "回家路上下雨了")
        # the hook moved to the front; between 补货 and 下雨了 one line still gave way to one new line
        self.assertEqual(brief(learning.line_changes(ai, final)), [("rewritten", "质地非常轻盈好吸收", "推开水水的，一点不糊脸")])

    def test_line_split_or_merged_is_not_a_change(self):
        whole = "本来以为会很厚结果上脸嗖一下就吃进去了"
        halves = ("本来以为会很厚", "结果上脸嗖一下就吃进去了")
        self.assertEqual(learning.line_changes(lines(whole), lines(*halves)), [])                    # only Enter was pressed
        self.assertEqual(learning.line_changes(lines(*halves), lines(whole)), [])
        self.assertEqual(learning.line_changes(lines("上周末去超市补货", whole, "好啦下期见"),
                                               lines("上周末去超市补货", *halves, "好啦下期见")), [])

    def test_breath_marks_are_the_ones_the_app_uses(self):
        text = "一一/二二\n三三，四四,五五。六六！七七!八八？九九?十十；甲甲;乙乙～丙丙~丁丁…戊戊"
        added = learning.line_changes([], lines(text))[0]["added"]
        self.assertEqual(added, textstats.split_clauses(text))                                       # 15 breaths either way
        self.assertEqual(len(added), 15)

    def test_same_input_gives_same_result_and_inputs_are_not_modified(self):
        ai = lines("家人们谁懂啊", "这个面霜真的绝绝子", "好啦下期见")
        final = lines("这个面霜真的蛮舒服的", "好啦下期见", "对了 / 瓶子有点沉")
        before = copy.deepcopy((ai, final))
        first = learning.line_changes(ai, final)
        self.assertEqual(first, learning.line_changes(ai, final))
        self.assertEqual((ai, final), before)
        json.dumps(first)                                                                            # storable as is

    def test_malformed_lines_never_raise(self):
        for ai, final in ((None, None), ("text", 5), ({}, []), ([None, 3, "x", {}], [{"spoken_script_zh": None}, {"spoken_script_zh": 7}])):
            self.assertEqual(learning.line_changes(ai, final), [])
        ai = [None, 3, {"spoken_script_zh": None}, {"spoken_script_zh": "好啦下期见"}, {"spoken_script_zh": "[轻笑]"}]
        final = [{"spoken_script_zh": "好啦下期见"}, "oops", {"delivery_cue": "[轻笑]"}, {"spoken_script_zh": "   "}]
        self.assertEqual(learning.line_changes(ai, final), [])                                       # only one real line each, and it is the same
        self.assertEqual(learning.line_changes([None, {"spoken_script_zh": "家人们谁懂啊"}], []), [{
            "kind": "deleted", "ai": "家人们谁懂啊", "final": "", "removed": ["家人们谁懂啊"], "added": [],
            "ai_line_no": 2, "final_line_no": None,           # numbered by its place in the list it was given
        }])


class RecurringRemovalsTest(unittest.TestCase):
    def test_phrase_removed_in_two_scripts_is_suggested(self):
        changes = {
            "s1": [{"kind": "rewritten", "removed": ["绝绝子", "今天给大家"], "added": ["蛮舒服的"]}],
            "s2": [{"kind": "rewritten", "removed": ["绝绝子啊"], "added": ["巨省心"]},
                   {"kind": "deleted", "removed": ["今天给大家分享一个好物"], "added": []}],
            "s3": [{"kind": "rewritten", "removed": ["绝绝子", "非常"], "added": ["真的非常喜欢"]}],
            "s4": [{"kind": "rewritten", "removed": ["非常"], "added": []}],
        }
        self.assertEqual(learning.recurring_removals(changes), [
            {"phrase": "绝绝子", "scripts": 3, "script_ids": ["s1", "s2", "s3"]},      # in s2 it went as part of 绝绝子啊
            {"phrase": "今天给大家", "scripts": 2, "script_ids": ["s1", "s2"]},        # in s2 the whole breath was deleted
        ])
        # 非常 left two scripts too, but the creator wrote 真的非常喜欢 themselves, so banning it would flag their own words.
        # 绝绝子啊 and 今天给大家分享一个好物 were each removed in one script only.

    def test_twice_in_one_script_is_not_a_habit(self):
        changes = {"s1": [{"removed": ["满满的高级感"], "added": []}, {"removed": ["满满的高级感"], "added": []}]}
        self.assertEqual(learning.recurring_removals(changes), [])

    def test_works_on_real_line_changes(self):
        changes = {
            "面霜": learning.line_changes(lines("这个面霜真的绝绝子"), lines("这个面霜真的蛮舒服的")),
            "开衫": learning.line_changes(lines("这件开衫绝绝子", "好啦下期见"), lines("这件开衫巨省心", "好啦下期见")),
            "床品": learning.line_changes(lines("感觉蛮舒服的"), lines("感觉还不错")),
            "沙发": learning.line_changes(lines("坐着蛮舒服的"), lines("坐着很放松")),
        }
        # 蛮舒服的 was taken out of two scripts but put into a third, so only 绝绝子 is suggested
        self.assertEqual(learning.recurring_removals(changes), [{"phrase": "绝绝子", "scripts": 2, "script_ids": ["面霜", "开衫"]}])
        as_list = learning.recurring_removals(list(changes.values()))
        self.assertEqual(as_list, [{"phrase": "绝绝子", "scripts": 2, "script_ids": [0, 1]}])        # positions stand in for ids

    def test_spacing_and_punctuation_do_not_hide_a_repeat(self):
        changes = {"a": [{"removed": ["性价比 极高"]}], "b": [{"removed": ["性价比极高！"]}]}
        self.assertEqual(learning.recurring_removals(changes), [{"phrase": "性价比 极高", "scripts": 2, "script_ids": ["a", "b"]}])

    def test_malformed_input_never_raises(self):
        for value in (None, "changes", 5, [], {}, {"a": None, "b": "x", "c": 3}):
            self.assertEqual(learning.recurring_removals(value), [])
        messy = {
            "a": [None, "x", {"removed": "绝绝子"}, {"removed": [None, 5, "绝", "绝绝子"], "added": 7}],   # a string is not a list; 绝 is one character
            "b": [{"kind": "rewritten"}, {"removed": ["绝绝子"], "added": [None]}],
        }
        self.assertEqual(learning.recurring_removals(messy), [{"phrase": "绝绝子", "scripts": 2, "script_ids": ["a", "b"]}])


class PromptRulesTest(unittest.TestCase):
    LESSONS = [
        {"ai": "这个面霜真的绝绝子", "final": "这个面霜真的蛮舒服的", "note": "我不说绝绝子\n  太网红了"},
        {"ai": "家人们谁懂啊", "final": ""},
        {"ai": "", "final": "对了 / 瓶子有点沉"},
        {"ai": "你们看 / 他又开始了哈", "final": "你们看，他又开始了哈"},      # no wording changed: nothing to learn
        {"ai": " 这个面霜真的绝绝子 ", "final": "这个面霜真的蛮舒服的"},      # the first lesson again
        "oops", None, {"ai": 5, "final": None}, {},
    ]
    RULES = [
        {"ai_wrote": "这个面霜真的绝绝子", "changed_to": "这个面霜真的蛮舒服的", "note": "我不说绝绝子 太网红了"},
        {"ai_wrote": "家人们谁懂啊", "deleted": True},
        {"added": "对了 / 瓶子有点沉"},
    ]

    def test_rules_say_what_ai_wrote_and_what_it_became(self):
        lessons = copy.deepcopy(self.LESSONS)
        self.assertEqual(learning.prompt_rules(lessons), self.RULES)
        self.assertEqual(lessons, self.LESSONS)

    def test_a_change_from_line_changes_can_be_accepted_as_it_is(self):
        accepted = learning.line_changes(lines("这个面霜真的绝绝子", "家人们谁懂啊"), lines("这个面霜真的蛮舒服的"))
        self.assertEqual(learning.prompt_rules(accepted), [
            {"ai_wrote": "这个面霜真的绝绝子", "changed_to": "这个面霜真的蛮舒服的"},
            {"ai_wrote": "家人们谁懂啊", "deleted": True},
        ])

    def test_limit_keeps_the_first_lessons_in_the_order_given(self):
        lessons = [{"ai": "第%d句真的绝绝子" % n, "final": "第%d句蛮舒服的" % n} for n in range(1, 13)]
        rules = learning.prompt_rules(lessons)
        self.assertEqual([r["ai_wrote"] for r in rules], ["第%d句真的绝绝子" % n for n in range(1, 11)])   # the prompt takes ten
        self.assertEqual(len(learning.prompt_rules(lessons, limit=3)), 3)
        self.assertEqual(learning.prompt_rules(lessons, limit=0), [])
        self.assertEqual(len(learning.prompt_rules(lessons, limit="all")), 10)                       # a bad limit falls back to ten

    def test_malformed_lessons_never_raise(self):
        for value in (None, "lessons", 5, {}, [None, 5, "x", {}, {"ai": None, "final": 3, "note": ["x"]}]):
            self.assertEqual(learning.prompt_rules(value), [])
        self.assertEqual(learning.prompt_rules([{"ai": "家人们谁懂啊", "note": 5}]), [{"ai_wrote": "家人们谁懂啊", "deleted": True}])

    def test_rules_fill_the_block_in_the_draft_prompt(self):
        prompt = DRAFT_PROMPT.read_text(encoding="utf-8")
        block = re.search(r"<learned_edit_preferences>(.*?)</learned_edit_preferences>", prompt, re.S).group(1)
        placeholders = re.findall(r"\{\{\w+\}\}", block)
        self.assertEqual(len(placeholders), 1)                                                       # one slot, and it expects JSON
        filled = block.replace(placeholders[0], json.dumps(learning.prompt_rules(self.LESSONS), ensure_ascii=False, indent=1))
        self.assertEqual(json.loads(re.sub(r"<!--.*?-->", "", filled, flags=re.S)), self.RULES)


if __name__ == "__main__":
    unittest.main()
