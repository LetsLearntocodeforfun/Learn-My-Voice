"""Tests for subtitle parsing and speech timing measured from timestamps."""
import unittest

from studio import timing

SRT = (
    "\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\n今天去超市买菜啦\r\n\r\n"
    "2\r\n00:00:03,800 --> 00:00:06,000\r\n<i>结果</i>人特别多嘛\r\n排队排了半小时\r\n\r\n"
)
VTT = "\n".join([
    "WEBVTT - my captions", "Kind: captions", "Language: zh", "",
    "NOTE", "这是备注，不是台词", "",
    "STYLE", "::cue { color: yellow }", "",
    "intro",
    "00:01.000 --> 00:03.000 align:start position:0%",
    "<v 她>其实 weekend 就该<00:02.000><c> 出门</c>走走", "",
    "NOTE 单行备注", "",
    "1:00:03.250 --> 1:00:05.000",
    "Tom &amp; Jerry 看了 &lt;3 遍", "",
])


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class ParseSubtitlesTest(unittest.TestCase):
    def test_srt_with_bom_crlf_tags_and_two_line_cue(self):
        self.assertEqual(timing.parse_subtitles(SRT), [
            seg(1.0, 3.5, "今天去超市买菜啦"),
            seg(3.8, 6.0, "结果人特别多嘛 排队排了半小时"),           # tag gone, two lines joined by one space
        ])

    def test_webvtt_header_notes_settings_and_missing_hours(self):
        self.assertEqual(timing.parse_subtitles(VTT), [
            seg(1.0, 3.0, "其实 weekend 就该 出门走走"),             # cue id, settings, voice and timestamp tags dropped
            seg(3603.25, 3605.0, "Tom & Jerry 看了 <3 遍"),          # entities decoded; "<3" is text, not a tag
        ])

    def test_cue_numbers_missing_and_no_blank_lines(self):
        unnumbered = "00:00:01,000 --> 00:00:02,000\n一共\n3\n00:00:02,500 --> 00:00:03,500\n个\n"
        self.assertEqual([s["text"] for s in timing.parse_subtitles(unnumbered)], ["一共 3", "个"])   # "3" was said
        glued = ("1\n00:00:01,000 --> 00:00:02,000\n你好\n2\n00:00:02,500 --> 00:00:03,500\n我今年\n25\n"
                 "3\n00:00:04,000 --> 00:00:05,000\n再见\n")
        self.assertEqual([s["text"] for s in timing.parse_subtitles(glued)], ["你好", "我今年 25", "再见"])  # cue numbers gone

    def test_blank_line_inside_a_cue_keeps_the_words(self):
        text = "5\n00:00:10,000 --> 00:00:12,000\n今天去超市\n\n买了好多菜\n\n6\n00:00:13,000 --> 00:00:14,000\n真的蛮多的哈\n\n7\n"
        self.assertEqual([s["text"] for s in timing.parse_subtitles(text)], ["今天去超市 买了好多菜", "真的蛮多的哈"])

    def test_sorted_by_start_and_empty_cues_dropped(self):
        text = ("00:00:09.000 --> 00:00:10.000\n第三句\n\n00:00:05.000 --> 00:00:06.000\n<b></b>\n\n"
                "00:00:01.5 --> 00:00:02\n第一句\n\n00:00:04,000 --> 00:00:03,000\n{\\an8}第二句\n\n"
                "00:00:07,000 --> 00:00:08,000\n\n")
        self.assertEqual(timing.parse_subtitles(text), [
            seg(1.5, 2.0, "第一句"),                                   # ".5" is 500 ms; milliseconds may be absent
            seg(4.0, 4.0, "第二句"),                                   # end before start: words kept, no duration
            seg(9.0, 10.0, "第三句"),
        ])

    def test_wording_is_left_exactly_as_written(self):
        line = "那个…其实吧，Trader Joe's的cream我用了1周啦～真的!!  蛮OK的哈？"
        parsed = timing.parse_subtitles("1\n00:00:00,000 --> 00:00:04,000\n  %s  \n" % line)
        self.assertEqual(parsed[0]["text"], line)

    def test_no_cues_is_an_error(self):
        for text in ("", "今天去超市买菜啦\n结果人特别多嘛", "WEBVTT\n\nNOTE nothing here\n", "1\n2\n3\n", None):
            with self.assertRaises(ValueError) as ctx:
                timing.parse_subtitles(text)
            self.assertEqual(str(ctx.exception), "no_cues")

    def test_file_bytes_in_common_chinese_encodings(self):
        expected = timing.parse_subtitles(SRT)
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            self.assertEqual(timing.parse_subtitles(SRT.lstrip("\ufeff").encode(encoding)), expected, encoding)

    def test_transcript_text_is_one_cue_per_line(self):
        self.assertEqual(timing.transcript_text(timing.parse_subtitles(SRT)), "今天去超市买菜啦\n结果人特别多嘛 排队排了半小时")
        self.assertEqual(timing.transcript_text([seg(0, 1, " 第一句\n还是第一句 "), seg(1, 2, "  "), seg(2, 3, "第二句")]),
                         "第一句 还是第一句\n第二句")
        self.assertEqual(timing.transcript_text([]), "")


class TimingStatsTest(unittest.TestCase):
    def test_rate_counts_breaths_and_excludes_silence(self):
        segments = [seg(0.0, 4.0, "今天去超市买菜"), seg(4.5, 9.0, "结果人特别多嘛"), seg(12.0, 16.0, "排队排了半小时啦")]
        self.assertEqual(timing.timing_stats(segments), {
            "cues": 3, "units": 22,
            "spoken_sec": 13.0,            # 4 + 0.5 breath + 4.5 + 4; the 3 s gap is silence
            "span_sec": 16.0,
            "pauses": 2,                   # both gaps are at least 0.3 s
            "speaking_rate_cpm": 102,      # 22 units / 13 s * 60 = 101.5
        })
        self.assertEqual(timing.timing_stats(list(reversed(segments))), timing.timing_stats(segments))   # order-independent

    def test_gap_thresholds_are_exact(self):
        def stats(gap, **kw):
            return timing.timing_stats([seg(0.0, 2.0, "今天去超市买菜啦真的"), seg(2.0 + gap, 12.0 + gap, "结果人特别多嘛")], **kw)

        self.assertEqual((stats(0.3)["pauses"], stats(0.299)["pauses"]), (1, 0))       # 2.3 - 2.0 is 0.2999... in floats
        self.assertEqual((stats(1.2)["spoken_sec"], stats(1.201)["spoken_sec"]), (13.2, 12.0))
        self.assertEqual((stats(2.0, max_gap_sec=2.0)["spoken_sec"], stats(2.0)["spoken_sec"]), (14.0, 12.0))
        self.assertEqual(stats(0.0)["pauses"], 0)

    def test_overlapping_cues_are_not_counted_twice(self):
        overlapping = [seg(0.0, 5.0, "今天去超市买菜"), seg(3.0, 8.0, "结果人特别多嘛"), seg(1.0, 2.0, "对呀"), seg(8.0, 12.0, "排队排了半小时啦")]
        stats = timing.timing_stats(overlapping)
        self.assertEqual((stats["spoken_sec"], stats["span_sec"], stats["pauses"], stats["units"]), (12.0, 12.0, 0, 24))
        self.assertEqual(stats["speaking_rate_cpm"], 120)

    def test_bracketed_notes_are_not_speech(self):
        segments = [seg(0.0, 6.0, "今天去超市买菜"), seg(6.0, 26.0, "[音乐]"), seg(26.0, 32.0, "【轻笑】真的假的")]
        stats = timing.timing_stats(segments)
        self.assertEqual((stats["cues"], stats["units"], stats["spoken_sec"], stats["span_sec"]), (2, 11, 12.0, 32.0))
        self.assertEqual(stats["speaking_rate_cpm"], 55)                                # the 20 s of music is not in the rate

    def test_units_follow_textstats(self):
        stats = timing.timing_stats([seg(0.0, 10.0, "weekend 去 Trader Joe's 买了 3 样，花了 25 块")])
        self.assertEqual((stats["units"], stats["speaking_rate_cpm"]), (13, 78))        # 3 English words, 7 characters, 3 digits

    def test_no_rate_under_ten_seconds_of_speech(self):
        self.assertIsNone(timing.timing_stats([seg(0.0, 9.999, "今天去超市买菜啦")])["speaking_rate_cpm"])
        self.assertEqual(timing.timing_stats([seg(0.0, 10.0, "今天去超市买菜啦")])["speaking_rate_cpm"], 48)
        long_silence = [seg(0.0, 5.0, "今天去超市买菜啦"), seg(60.0, 64.0, "结果人特别多嘛")]
        self.assertEqual((timing.timing_stats(long_silence)["spoken_sec"], timing.timing_stats(long_silence)["speaking_rate_cpm"]),
                         (9.0, None))                                                    # 64 s on the clock, 9 s of speech

    def test_empty_and_unusable_input(self):
        empty = {"cues": 0, "units": 0, "spoken_sec": 0.0, "span_sec": 0.0, "pauses": 0, "speaking_rate_cpm": None}
        self.assertEqual(timing.timing_stats([]), empty)
        self.assertEqual(timing.timing_stats(None), empty)
        junk = [seg(None, 2.0, "你好"), seg("0", "2", "你好"), seg(3.0, 3.0, "没有时长"), seg(5.0, 4.0, "倒着的"), "not a segment", seg(0.0, 2.0, "")]
        self.assertEqual(timing.timing_stats(junk), empty)

    def test_stats_straight_from_a_subtitle_file(self):
        stats = timing.timing_stats(timing.parse_subtitles(SRT))
        self.assertEqual((stats["cues"], stats["units"], stats["spoken_sec"], stats["pauses"]), (2, 22, 5.0, 1))
        self.assertIsNone(stats["speaking_rate_cpm"])                                   # 5 s is too little to call a rate


class CombinedRateTest(unittest.TestCase):
    def test_pools_units_and_time_instead_of_averaging(self):
        slow = {"units": 60, "spoken_sec": 30.0, "speaking_rate_cpm": 120}
        fast = {"units": 450, "spoken_sec": 90.0, "speaking_rate_cpm": 300}
        self.assertEqual(timing.combined_rate([slow, fast]), {"speaking_rate_cpm": 255, "spoken_sec": 120.0, "videos": 2})   # mean of rates would be 210

    def test_one_short_clip_cannot_set_the_rate(self):
        clip = {"units": 200, "spoken_sec": 40.0, "speaking_rate_cpm": 300}
        self.assertIsNone(timing.combined_rate([clip]))
        self.assertIsNone(timing.combined_rate([]))
        self.assertIsNone(timing.combined_rate(None))
        self.assertIsNone(timing.combined_rate([{"units": 299, "spoken_sec": 59.999}]))
        self.assertEqual(timing.combined_rate([{"units": 300, "spoken_sec": 60.0}])["speaking_rate_cpm"], 300)

    def test_short_clips_add_up_and_unusable_entries_are_skipped(self):
        short = timing.timing_stats([seg(0.0, 9.0, "今天去超市买菜啦真的蛮多人的哈" * 2)])       # no rate on its own
        self.assertIsNone(short["speaking_rate_cpm"])
        entries = [short] * 7 + [None, {}, {"units": 0, "spoken_sec": 0.0}, {"units": 50, "spoken_sec": None}, {"units": True, "spoken_sec": 30.0}]
        self.assertEqual(timing.combined_rate(entries), {"speaking_rate_cpm": 200, "spoken_sec": 63.0, "videos": 7})


if __name__ == "__main__":
    unittest.main()
