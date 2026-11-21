"""Tests for numbers kept by day, the import of a table from the Creator Center, and what viewers wrote.

No real export of the Creator Center was available when these were written, and none was read about either.
Every table here is made up, and the workbooks are built in the tests (with the helpers of
tests/test_keyword_import.py). They show that the import reads the file formats and keeps what it promises: a
preview first, the whole title only, numbers dated by the day they were read. They do not show that it knows
what the Creator Center calls its columns.

Every test fixes the day (``Case.on``): none depends on the day it is run.
"""
import datetime
import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from studio import keyword_import as ki
from studio import posted, rednote_analytics as ra, server
from studio.store import MAX_SNAPSHOTS_PER_SAMPLE, Store
from tests import browser
from tests.test_keyword_import import b64, sheet, workbook

WEB = Path(server.WEB_DIR)
TODAY = datetime.date(2026, 11, 21)
NUMBERS = ("views", "likes", "collects", "comments")
CJK = re.compile(r"[\u4e00-\u9fff]")
# A table as the Creator Center might save it: a line above the header, the day each video was posted, the four
# numbers, and two columns that are no counts.
EXPORT = ("笔记数据 2026-11-01 至 2026-11-20\n"
          "笔记标题,首次发布时间,观看量,点赞量,收藏量,评论量,收藏率,人均观看时长\n"
          "秋冬干皮护肤,2026-11-01 20:00,2.5万,900,\"1,200\",85,4.8%,00:45\n"
          "周末 超市日常,2026-11-03 18:30,3000,-,20,5,0.7%,00:30\n"
          "没有进素材库的那条,2026-11-04 09:00,10,1,1,1,10%,00:05\n")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-numbers-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.on(TODAY)

    def on(self, day):
        """From here on it is ``day``: for an import, for a number typed on a page, for a script marked as posted."""
        for target, stand_in in (("studio.rednote_analytics.today", lambda: day), ("studio.posted.today", lambda: day),
                                 ("studio.store._today", lambda now=None: day.isoformat())):
            patcher = mock.patch(target, stand_in)
            patcher.start()
            self.addCleanup(patcher.stop)

    def item(self, title, text="今天去超市买菜啦 / 人特别多嘛", **numbers):
        return self.store.create_sample(title=title, text=text, metrics=numbers)

    def numbers(self, sample):
        fresh = self.store.get_sample(sample["id"])
        return [fresh[field] for field in NUMBERS]

    def readings(self, sample):
        return [tuple(reading[key] for key in ("read_on",) + NUMBERS) for reading in self.store.list_metric_snapshots(sample["id"])]

    def per_day(self, sample):
        return ra.views_per_day(self.store.get_sample(sample["id"]), self.store.list_metric_snapshots(sample["id"]))

    def refused(self, code, call, *args, **kwargs):
        """The refusal ``call`` ends in. It has to carry ``code`` and a sentence in each language."""
        with self.assertRaises(ra.AnalyticsError) as caught:
            call(*args, **kwargs)
        refusal = caught.exception
        self.assertEqual(refusal.code, code, refusal.message)
        self.assertTrue(CJK.search(refusal.message_zh), refusal.message_zh)
        self.assertFalse(CJK.search(refusal.message), refusal.message)
        return refusal


# ---- numbers kept by day ----------------------------------------------------------------------------------------------
class ReadingsTest(Case):
    def test_a_reading_holds_what_was_read_that_day_and_nothing_else(self):
        video, other = self.item("超市那天"), self.item("面霜那期")
        day = "2026-11-10"
        self.assertEqual(self.store.record_metric_snapshot(video["id"], day, {"views": 1000})["views"], 1000)
        self.store.record_metric_snapshot(video["id"], day, {"likes": "50"})                    # read later on the same day
        self.assertEqual(self.readings(video), [(day, 1000, 50, None, None)])
        self.store.record_metric_snapshot(video["id"], day, {"views": 1200})                    # read again: the day's reading changes
        self.store.record_metric_snapshot(video["id"], "2026-11-12", {"views": 1500})
        self.assertEqual(self.readings(video), [(day, 1200, 50, None, None), ("2026-11-12", 1500, None, None, None)])
        self.store.record_metric_snapshot(video["id"], day, {"views": None})                    # cleared
        self.assertEqual(self.readings(video)[0], (day, None, 50, None, None))
        self.assertIsNone(self.store.record_metric_snapshot(video["id"], day, {"likes": "not a number"}))
        self.assertEqual(self.readings(video), [("2026-11-12", 1500, None, None, None)])         # a reading left without a number is gone
        self.assertIsNone(self.store.record_metric_snapshot(video["id"], day, {}))              # nothing read: nothing kept
        self.assertIsNone(self.store.record_metric_snapshot("0" * 12, day, {"views": 5}))       # no such item
        self.assertEqual((self.numbers(video), self.readings(other)), ([None] * 4, []))          # a reading is not the item's numbers

    def test_the_day_of_a_reading_has_to_be_written_as_a_day(self):
        video = self.item("超市那天")
        for bad in ("", "2026-11-1", "20261110", "2026/11/10", "2026-11-10 08:00", None, 20261110):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.store.record_metric_snapshot(video["id"], bad, {"views": 5})
        self.assertEqual(self.readings(video), [])

    def test_numbers_are_dated_by_the_day_they_were_given_and_a_number_not_named_is_not_dated_again(self):
        self.on(datetime.date(2026, 11, 10))
        video = self.item("超市那天", views=1000, likes=40)
        self.assertEqual(self.readings(video), [("2026-11-10", 1000, 40, None, None)])           # what an item arrives with is its first reading
        self.on(datetime.date(2026, 11, 15))
        self.store.update_sample(video["id"], {"likes": "55"})                                    # the library page sends the one number that was typed
        self.assertEqual(self.readings(video), [("2026-11-10", 1000, 40, None, None), ("2026-11-15", None, 55, None, None)])
        # The views were read on the 10th. Round 1 wrote all four numbers under the day one of them was typed, and
        # "0 views gained in five days" followed.
        self.assertIsNone(self.per_day(video)["since_reading"])
        self.store.update_sample(video["id"], {"title": "超市那天（上）", "is_sponsored": True})    # no number named: no reading
        self.assertEqual(len(self.readings(video)), 2)
        self.store.update_sample(video["id"], {"views": 1000})                                    # read again and found the same: still a reading
        self.assertEqual(self.readings(video)[-1], ("2026-11-15", 1000, 55, None, None))
        self.assertEqual(self.per_day(video)["since_reading"]["views"], 0)
        self.store.update_sample(video["id"], {"likes": "", "views": ""})                         # both cleared today
        self.assertEqual((self.readings(video), self.numbers(video)), ([("2026-11-10", 1000, 40, None, None)], [None] * 4))

    def test_a_posted_scripts_numbers_are_a_reading_of_the_day_they_were_read_and_move_with_that_day(self):
        script = self.store.create_script(title="面霜", lines=[{"spoken_script_zh": "入冬以后脸特别干嘛 / 所以我换了一款面霜"}])
        posted.mark(self.store, script["id"], {"posted_on": "2026-11-01", "views": 900, "likes": 30, "metrics_on": "2026-11-03"})
        item = self.store.get_posted_sample(script["id"])
        self.assertEqual(self.readings(item), [("2026-11-03", 900, 30, None, None)])
        posted.mark(self.store, script["id"], {"views": 2500, "likes": 80, "collects": "", "comments": "", "metrics_on": "2026-11-10"},
                    update_only=True)
        self.assertEqual(self.readings(item), [("2026-11-03", 900, 30, None, None), ("2026-11-10", 2500, 80, None, None)])
        # The day was mistyped: the same numbers, another day. The reading moves with them and none is left under the wrong day.
        posted.update_item(self.store, self.store.get_sample(item["id"]), {"metrics_on": "2026-11-09"})
        self.assertEqual(self.readings(item), [("2026-11-03", 900, 30, None, None), ("2026-11-09", 2500, 80, None, None)])
        found = self.per_day(item)
        self.assertEqual(found["since_reading"], {"from": "2026-11-03", "to": "2026-11-09", "days": 6, "views": 1600, "per_day": 266.7})
        self.assertEqual(found["since_posted"], {"posted_on": "2026-11-01", "to": "2026-11-09", "days": 8, "views": 2500, "per_day": 312.5})

    def test_the_readings_of_a_year_are_kept_for_one_item_and_the_oldest_go_first(self):
        video, other = self.item("超市那天"), self.item("面霜那期")
        self.store.record_metric_snapshot(other["id"], "2020-01-01", {"views": 1})
        first = datetime.date(2025, 1, 1)
        for n in range(MAX_SNAPSHOTS_PER_SAMPLE + 5):
            self.store.record_metric_snapshot(video["id"], (first + datetime.timedelta(days=n)).isoformat(), {"views": n})
        kept = self.readings(video)
        self.assertEqual((len(kept), kept[0][:2], kept[-1][1]), (MAX_SNAPSHOTS_PER_SAMPLE, ("2025-01-06", 5), MAX_SNAPSHOTS_PER_SAMPLE + 4))
        self.assertEqual(self.readings(other), [("2020-01-01", 1, None, None, None)])            # each item has its own year

    def test_removing_an_item_removes_its_readings(self):
        video, other = self.item("超市那天", views=10), self.item("面霜那期", views=20)
        self.store.delete_sample(video["id"])
        self.assertEqual([reading["sample_id"] for reading in self.store.list_metric_snapshots()], [other["id"]])

    def test_the_readings_of_an_import_are_stored_all_or_none(self):
        video, other = self.item("超市那天", views=10), self.item("面霜那期")
        was = self.store.list_metric_snapshots()
        with self.assertRaises(KeyError):
            self.store.apply_readings([
                {"sample_id": video["id"], "read_on": "2026-11-20", "values": {"views": 500}, "current": True},
                {"sample_id": "0" * 12, "read_on": "2026-11-20", "values": {"views": 7}, "current": True}])
        self.assertEqual((self.store.list_metric_snapshots(), self.numbers(video)), (was, [10, None, None, None]))
        self.assertEqual(self.store.apply_readings([
            {"sample_id": video["id"], "read_on": "2026-11-20", "values": {"views": 500, "likes": 9}, "current": True},
            {"sample_id": other["id"], "read_on": "2026-11-02", "values": {"views": 7}, "current": False}]), 2)
        self.assertEqual((self.numbers(video), self.numbers(other)), ([500, 9, None, None], [None] * 4))
        self.assertEqual(self.readings(other), [("2026-11-02", 7, None, None, None)])


class PerDayTest(unittest.TestCase):
    def found(self, readings, **sample):
        return ra.views_per_day(sample, [{"read_on": day, "views": views, "likes": None, "collects": None, "comments": None}
                                         for day, views in readings])

    def test_two_readings_say_how_many_views_came_in_between(self):
        found = self.found([("2026-11-10", 1000), ("2026-11-15", 3500)])
        self.assertEqual(found["since_reading"], {"from": "2026-11-10", "to": "2026-11-15", "days": 5, "views": 2500, "per_day": 500.0})
        self.assertEqual((found["readings"], found["last_read_on"], found["since_posted"]), (2, "2026-11-15", None))
        later = self.found([("2026-11-10", 1000), ("2026-11-15", 3500), ("2026-11-22", 3600)])
        self.assertEqual(later["since_reading"], {"from": "2026-11-15", "to": "2026-11-22", "days": 7, "views": 100, "per_day": 14.3})

    def test_it_takes_two_days_with_views(self):
        self.assertIsNone(self.found([("2026-11-10", 1000)])["since_reading"])
        self.assertEqual(self.found([]), {"readings": 0, "last_read_on": None, "since_reading": None, "since_posted": None, "history": []})
        passed_over = self.found([("2026-11-10", 1000), ("2026-11-12", None), ("2026-11-15", 3500)])   # the likes were read on the 12th, not the views
        self.assertEqual((passed_over["since_reading"]["from"], passed_over["since_reading"]["days"], passed_over["readings"]), ("2026-11-10", 5, 3))

    def test_views_since_posting_need_the_day_it_was_posted_and_a_whole_day(self):
        found = self.found([("2026-11-11", 5000)], posted_on="2026-11-01")
        self.assertEqual(found["since_posted"], {"posted_on": "2026-11-01", "to": "2026-11-11", "days": 10, "views": 5000, "per_day": 500.0})
        self.assertIsNone(self.found([("2026-11-01", 800)], posted_on="2026-11-01")["since_posted"])    # read on the day it went out
        self.assertIsNone(self.found([("2026-10-30", 800)], posted_on="2026-11-01")["since_posted"])    # days that cannot be
        self.assertIsNone(self.found([("2026-11-11", 5000)])["since_posted"])
        self.assertIsNone(self.found([("2026-11-11", 5000)], posted_on="not a day")["since_posted"])

    def test_a_number_that_went_down_is_given_as_it_is(self):
        self.assertEqual(self.found([("2026-11-10", 1000), ("2026-11-12", 900)])["since_reading"],
                         {"from": "2026-11-10", "to": "2026-11-12", "days": 2, "views": -100, "per_day": -50.0})

    def test_a_page_is_sent_the_newest_readings_oldest_first(self):
        first = datetime.date(2026, 10, 1)
        found = self.found([((first + datetime.timedelta(days=n)).isoformat(), n) for n in range(20)])
        self.assertEqual((len(found["history"]), found["history"][0]["views"], found["history"][-1]),
                         (ra.HISTORY_SHOWN, 8, {"read_on": "2026-10-20", "views": 19, "likes": None, "collects": None, "comments": None}))


# ---- a table from the Creator Center: cells, columns ------------------------------------------------------------------
class CellTest(unittest.TestCase):
    def test_a_count_is_read_in_the_forms_the_keyword_import_reads_and_zero_is_a_count(self):
        for cell, count in {"0": 0, "12": 12, "1,234": 1234, "1.2万": 12000, "3.5w": 35000, "8k": 8000, "2亿": 200000000,
                            "12.5万+": 125000, " 85 ": 85, "１２３": 123, "0.4": 0, "1e3": None, "": None, "-": None, "--": None,
                            "—": None, "暂无": None, "12.5%": None, "00:45": None, "-5": None, "1.2.3": None, "9" * 40: None}.items():
            self.assertEqual(ra.read_count(cell), count, repr(cell))

    def test_a_day_is_read_in_the_forms_a_table_writes_it_and_never_guessed(self):
        for cell in ("2026-11-20", "2026/11/20", "2026.11.20", "2026年11月20日", "2026-11-20 08:30:00", "2026-11-20T08:30", "2026 - 11 - 20",
                     "20261120", "46346", "46346.75"):
            self.assertEqual(ra.read_day(cell), "2026-11-20", cell)
        self.assertEqual(ra.read_day("2026-1-5"), "2026-01-05")
        for cell in ("", "11-20", "11月20日", "昨天", "3天前", "2026-13-01", "2026-02-30", "12345678", "123", "99999", "2026-11-2x",
                     "2026-11-201", "1,234", "2.5万"):
            self.assertIsNone(ra.read_day(cell), cell)

    def test_titles_are_compared_whole_without_regard_to_spaces_case_and_width(self):
        same = ["秋冬干皮｜护肤 Vlog！", " 秋冬 干皮|护肤vlog! ", "秋冬干皮｜护肤\u3000ＶＬＯＧ!"]
        self.assertEqual(len({ra.title_key(title) for title in same}), 1)
        for other in ("秋冬干皮｜护肤 Vlog！2", "秋冬干皮", "干皮｜护肤 Vlog！", "秋冬干皮｜护肤 Vlog"):
            self.assertNotEqual(ra.title_key(other), ra.title_key(same[0]), other)
        self.assertEqual(ra.title_key(None), "")


class GuessTest(unittest.TestCase):
    def found(self, *rows):
        return ra.guess([list(row) for row in rows])

    def test_columns_are_found_by_the_names_in_the_header(self):
        header_row, fields = self.found(["笔记数据"], ["序号", "笔记标题", "首次发布时间", "观看量", "点赞量", "收藏量", "评论量", "统计日期"],
                                        ["1", "超市那天", "2026-11-01", "100", "5", "3", "1", "2026-11-20"])
        self.assertEqual((header_row, fields), (1, {"title": 1, "views": 3, "likes": 4, "collects": 5, "comments": 6, "read_on": 7, "posted_on": 2}))
        _, english = self.found(["Title", "Views", "Likes", "Saves", "Comments", "Published"], ["a", "1", "2", "3", "4", "2026-11-01"])
        self.assertEqual(english, {"title": 0, "views": 1, "likes": 2, "collects": 3, "comments": 4, "read_on": None, "posted_on": 5})

    def test_the_day_a_video_was_posted_is_never_taken_for_the_day_the_numbers_were_read(self):
        """Round 1 had 发布时间 first in its list of names for the day of a reading."""
        for name in ("发布时间", "首次发布时间", "发布日期", "发表时间"):
            _, fields = self.found(["笔记标题", name, "观看量"], ["超市那天", "2026-11-01", "100"])
            self.assertEqual((fields["posted_on"], fields["read_on"]), (1, None), name)
        # A column that says only "date" is neither: which day it is cannot be told, so the creator says it.
        for name in ("日期", "时间", "date", "Date"):
            _, fields = self.found(["笔记标题", name, "观看量"], ["超市那天", "2026-11-01", "100"])
            self.assertEqual((fields["posted_on"], fields["read_on"]), (None, None), name)
        _, both = self.found(["笔记标题", "发布时间", "数据更新时间", "观看量"], ["超市那天", "2026-11-01", "2026-11-20", "100"])
        self.assertEqual((both["posted_on"], both["read_on"]), (1, 2))

    def test_a_rate_or_a_length_of_time_is_not_taken_for_a_count_and_being_shown_is_not_being_watched(self):
        _, fields = self.found(["笔记标题", "人均观看时长", "收藏率", "点赞率", "评论占比", "曝光量", "观看量", "收藏量"],
                               ["超市那天", "00:45", "4%", "3%", "1%", "9000", "100", "4"])
        self.assertEqual(fields, {"title": 0, "views": 6, "likes": None, "collects": 7, "comments": None, "read_on": None, "posted_on": None})
        _, only_rates = self.found(["笔记标题", "人均观看时长", "曝光量", "点赞量"], ["超市那天", "00:45", "9000", "3"])
        self.assertEqual((only_rates["views"], only_rates["likes"]), (None, 3))

    def test_a_header_names_the_title_and_a_number_and_holds_no_number_itself(self):
        self.assertEqual(self.found(["笔记标题", "备注"], ["超市那天", "不错"])[0], None)               # no number named
        self.assertEqual(self.found(["观看量", "点赞量"], ["100", "5"])[0], None)                       # no title named
        self.assertEqual(self.found(["标题", "观看量", "2026"], ["超市那天", "100", "1"])[0], None)      # a row with a number in it is data
        below = [["导出时间 2026-11-20"], ["共 2 条"], [], ["笔记标题", "观看量"], ["超市那天", "100"]]
        self.assertEqual(ra.guess(below)[0], 3)

    def test_without_a_header_only_the_title_is_guessed(self):
        header_row, fields = self.found(["2026-11-01", "超市那天", "100", "5"], ["2026-11-03", "面霜那期", "300", "9"], ["2026-11-04", "散步", "40", "2"])
        self.assertEqual((header_row, fields), (None, {"title": 1, "views": None, "likes": None, "collects": None, "comments": None,
                                                       "read_on": None, "posted_on": None}))
        self.assertEqual(self.found(["100", "5"], ["300", "9"])[1]["title"], None)


# ---- a table from the Creator Center: what an import would store ----------------------------------------------------
class PlanTest(unittest.TestCase):
    FIELDS = {"title": 0, "views": 1, "likes": 2, "collects": None, "comments": None, "read_on": None, "posted_on": None}
    DAY = "2026-11-21"

    def library(self, *titles, **more):
        return [dict({"id": "id%d" % n, "title": title, "views": None, "likes": None, "collects": None, "comments": None}, **more)
                for n, title in enumerate(titles)]

    def plan(self, rows, library, fields=None, latest=None, header_row=0):
        table = [["笔记标题", "观看量", "点赞量", "统计日期"]] + [list(row) for row in rows]
        return ra.plan(table if header_row == 0 else table[1:], header_row, dict(self.FIELDS, **(fields or {})), library, latest or {}, self.DAY)

    def said(self, rows, library, **more):
        shown, counts = self.plan(rows, library, **more)
        self.assertEqual(sum(counts.values()), len(rows) + (1 if more.get("header_row", 0) == 0 else 0))     # every row is accounted for
        return [(entry["why"] or entry["effect"], entry["sample_id"]) for entry in shown]

    def test_a_row_changes_the_item_with_the_same_whole_title_and_no_other(self):
        library = self.library("秋冬干皮护肤", "秋冬干皮护肤 第二期", "超市")
        self.assertEqual(self.said([["秋冬干皮护肤", "100", "5"]], library), [("update", "id0")])
        self.assertEqual(self.said([[" 秋冬 干皮护肤 ", "100", "5"]], library), [("update", "id0")])
        self.assertEqual(self.said([["秋冬干皮护肤第二期", "100", "5"]], library), [("update", "id1")])
        # Part of a title is never enough, whichever of the two is the longer. Round 1 took the first item it found this way.
        for part in ("秋冬干皮", "干皮护肤", "秋冬干皮护肤 第二", "周末逛超市买菜", "超市那天的日常", "护肤"):
            self.assertEqual(self.said([[part, "100", "5"]], library), [("unmatched", None)], part)

    def test_a_row_says_what_it_would_do_and_shows_the_numbers_it_would_replace(self):
        library = self.library("超市那天", views=80, likes=None)
        shown, counts = self.plan([["超市那天", "1.2万", "-"]], library)
        self.assertEqual(shown, [{"row": 2, "title": "超市那天", "views": 12000, "likes": None, "collects": None, "comments": None,
                                  "read_on": self.DAY, "posted_on": None, "effect": "update", "why": None, "sample_id": "id0",
                                  "sample_title": "超市那天", "now": {"views": 80, "likes": None, "collects": None, "comments": None}}])
        self.assertEqual({key: value for key, value in counts.items() if value}, {"header": 1, "update": 1})

    def test_a_title_that_two_library_items_or_two_rows_carry_changes_nothing(self):
        twice = self.library("日常", "日常", "超市")
        self.assertEqual(self.said([["日常", "100", "5"], ["超市", "7", "1"]], twice), [("ambiguous", None), ("update", "id2")])
        once = self.library("日常", "超市")
        self.assertEqual(self.said([["日常", "100", "5"], ["超市", "7", "1"], ["日 常", "300", "9"]], once),
                         [("repeated", None), ("update", "id1"), ("repeated", None)])

    def test_rows_that_cannot_be_used_are_left_out_with_their_reason(self):
        library = self.library("超市", "面霜", posted_on="2026-11-10")
        rows = [["", "100", "5", "2026-11-20"], ["超市", "-", "", "2026-11-20"], ["超市", "", "", ""], ["面霜", "100", "5", "下周"],
                ["面霜", "100", "5", "2026-11-22"], ["面霜", "100", "5", "2026-11-09"], ["长" * 300, "100", "5", "2026-11-20"]]
        rows[-1][0] = ki._tidy(rows[-1][0])                                              # as the reader hands a cell of 300 characters on
        shown, counts = self.plan(rows, library, fields={"read_on": 3})
        self.assertEqual([entry["why"] for entry in shown], ["no_title", "repeated", "repeated", "repeated", "repeated", "repeated", "title_cut"])
        one_each = [[rows[1]], [rows[3]], [rows[4]], [rows[5]]]
        self.assertEqual([self.said(row, library, fields={"read_on": 3})[0][0] for row in one_each],
                         ["no_numbers", "bad_day", "future_day", "read_before_posted"])

    def test_the_numbers_are_dated_by_the_column_that_says_when_they_were_read_or_by_the_day_of_the_import(self):
        library = self.library("超市", "面霜")
        rows = [["超市", "100", "5", "2026-11-18"], ["面霜", "300", "9", "2026/11/19 23:59"]]
        by_column, _ = self.plan(rows, library, fields={"read_on": 3})
        self.assertEqual([entry["read_on"] for entry in by_column], ["2026-11-18", "2026-11-19"])
        by_import, _ = self.plan(rows, library)
        self.assertEqual([entry["read_on"] for entry in by_import], [self.DAY, self.DAY])
        # The same column taken for the day the videos were posted dates nothing.
        posted_only, _ = self.plan(rows, library, fields={"posted_on": 3})
        self.assertEqual([(entry["read_on"], entry["posted_on"]) for entry in posted_only], [(self.DAY, "2026-11-18"), (self.DAY, "2026-11-19")])

    def test_an_older_reading_than_the_library_has_is_kept_as_a_reading_and_the_items_numbers_stay(self):
        library = self.library("超市", "面霜")
        rows = [["超市", "100", "5", "2026-11-10"], ["面霜", "300", "9", "2026-11-10"]]
        said = self.said(rows, library, fields={"read_on": 3}, latest={"id0": "2026-11-15", "id1": "2026-11-10"})
        self.assertEqual(said, [("reading_only", "id0"), ("update", "id1")])

    def test_one_import_looks_at_a_limited_number_of_rows_with_a_title(self):
        library = self.library("第0条", "第%d条" % ra.MAX_IMPORT_ROWS)
        rows = [["第%d条" % n, "1", "1"] for n in range(ra.MAX_IMPORT_ROWS + 3)]
        shown, counts = self.plan(rows, library)
        self.assertEqual((shown[0]["effect"], shown[ra.MAX_IMPORT_ROWS - 1]["effect"], shown[ra.MAX_IMPORT_ROWS]["why"]), ("update", "unmatched", "over_limit"))
        self.assertEqual((counts["over_limit"], counts["update"], counts["unmatched"]), (3, 1, ra.MAX_IMPORT_ROWS - 1))

    def test_a_table_without_a_header_is_all_data(self):
        self.assertEqual(self.said([["超市", "100", "5"]], self.library("超市"), header_row=None), [("update", "id0")])


# ---- the two requests -------------------------------------------------------------------------------------------------
class ServerCase(Case):
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(0, self.store, lambda *args: {})
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def send(self, method, path, payload=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body is not None else {})
            res = conn.getresponse()
            return res.status, json.loads(res.read().decode("utf-8"))
        finally:
            conn.close()

    def preview(self, **payload):
        return self.send("POST", "/api/rednote/import/preview", payload)

    def bring_in(self, **payload):
        return self.send("POST", "/api/rednote/import", payload)

    def imported(self, seen, **file):
        """Import what the preview ``seen`` showed."""
        return self.bring_in(fields=seen["fields"], fingerprint=seen["fingerprint"], **file)

    def stored(self):
        """Everything an import may change: the items with their numbers, and the readings."""
        return ([(s["title"], s["text"], [s[field] for field in NUMBERS]) for s in self.store.list_samples()],
                [tuple(r[key] for key in ("sample_id", "read_on") + NUMBERS) for r in self.store.list_metric_snapshots()])

    def assert_refused(self, answer, status, code):
        got, body = answer
        self.assertEqual((got, body.get("error")), (status, code), body)
        self.assertTrue(body["message"] and CJK.search(body["message_zh"]), body)
        self.assertFalse(CJK.search(body["message"]), body)
        return body


class ImportTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.on(datetime.date(2026, 11, 10))
        self.skincare = self.item("秋冬干皮护肤", views=10000, likes=300)
        self.market = self.item("周末超市日常")
        self.on(TODAY)

    def test_the_preview_says_what_was_taken_for_what_and_what_every_row_would_do_and_stores_nothing(self):
        was = self.stored()
        status, out = self.preview(name="笔记数据.csv", text=EXPORT)
        self.assertEqual(status, 200, out)
        self.assertEqual((out["name"], out["kind"], out["delimiter"], out["header_row"]), ("笔记数据.csv", "text", ",", 1))
        self.assertEqual(out["headers"], ["笔记标题", "首次发布时间", "观看量", "点赞量", "收藏量", "评论量", "收藏率", "人均观看时长"])
        self.assertEqual(out["fields"], {"title": 0, "views": 2, "likes": 3, "collects": 4, "comments": 5, "read_on": None, "posted_on": 1})
        self.assertEqual(out["guessed"], out["fields"])
        self.assertEqual(out["columns"][2], {"index": 2, "header": "观看量", "samples": ["2.5万", "3000"]})
        self.assertEqual((out["dated_by"], out["import_day"]), ("import_day", "2026-11-21"))
        self.assertEqual([(r["row"], r["title"], [r[f] for f in NUMBERS], r["read_on"], r["posted_on"], r["effect"], r["sample_id"]) for r in out["rows"]], [
            (3, "秋冬干皮护肤", [25000, 900, 1200, 85], "2026-11-21", "2026-11-01", "update", self.skincare["id"]),
            (4, "周末 超市日常", [3000, None, 20, 5], "2026-11-21", "2026-11-03", "update", self.market["id"]),
            (5, "没有进素材库的那条", [10, 1, 1, 1], "2026-11-21", "2026-11-04", "unmatched", None)])
        self.assertEqual(out["rows"][0]["now"], {"views": 10000, "likes": 300, "collects": None, "comments": None})
        self.assertEqual({key: value for key, value in out["counts"].items() if value}, {"header": 2, "update": 2, "unmatched": 1})
        self.assertEqual((out["rows_read"], out["would_change"]), (5, 2))
        self.assertEqual(out["rows_read"], sum(out["counts"].values()))
        self.assertEqual(out["limits"], {"rows": ki.MAX_ROWS, "columns": ki.MAX_COLUMNS, "import_rows": ra.MAX_IMPORT_ROWS})
        self.assertEqual(self.stored(), was)                                             # looking is not importing
        self.assertEqual(self.preview(name="笔记数据.csv", text=EXPORT)[1]["fingerprint"], out["fingerprint"])

    def test_importing_stores_exactly_what_the_preview_showed(self):
        _, seen = self.preview(name="笔记数据.csv", text=EXPORT)
        status, out = self.imported(seen, name="笔记数据.csv", text=EXPORT)
        self.assertEqual(status, 201, out)
        self.assertEqual((out["changed"], out["readings_only"], out["unmatched"], out["rows_read"], out["dated_by"], out["import_day"]),
                         (2, 0, 1, 5, "import_day", "2026-11-21"))
        self.assertEqual((self.numbers(self.skincare), self.numbers(self.market)), ([25000, 900, 1200, 85], [3000, None, 20, 5]))
        self.assertEqual(self.readings(self.skincare), [("2026-11-10", 10000, 300, None, None), ("2026-11-21", 25000, 900, 1200, 85)])
        self.assertEqual(self.readings(self.market), [("2026-11-21", 3000, None, 20, 5)])   # a number the row does not hold is not a reading
        # The numbers are dated by the day of the import, not by 首次发布时间: 15,000 views in the 11 days since they were last read.
        self.assertEqual(self.per_day(self.skincare)["since_reading"], {"from": "2026-11-10", "to": "2026-11-21", "days": 11, "views": 15000, "per_day": 1363.6})

    def test_a_row_that_matches_nothing_creates_no_library_item(self):
        """Round 1 could store such a row as an item whose text was its title, as a script of the creator's."""
        _, seen = self.preview(name="a.csv", text=EXPORT)
        self.imported(seen, name="a.csv", text=EXPORT)
        self.assertEqual(sorted(s["title"] for s in self.store.list_samples()), ["周末超市日常", "秋冬干皮护肤"])
        only_strangers = "笔记标题,观看量\n没有的一条,5\n没有的另一条,7\n"
        _, seen = self.preview(name="a.csv", text=only_strangers)
        self.assertEqual((seen["would_change"], seen["counts"]["unmatched"]), (0, 2))
        self.assert_refused(self.imported(seen, name="a.csv", text=only_strangers), 400, "import_nothing_to_change")
        for extra in ({"create_missing": True}, {"create": [2, 3]}, {"create_missing": True, "data_base64": b64(only_strangers.encode())}):
            self.bring_in(name="a.csv", text=only_strangers, fields=seen["fields"], fingerprint=seen["fingerprint"], **extra)
        self.assertEqual(self.store.samples_count(), 2)

    def test_the_one_step_import_of_round_1_is_refused_and_stores_nothing(self):
        was = self.stored()
        old_bodies = [{"filename": "a.csv", "data_base64": b64(EXPORT.encode()), "create_missing": False},
                      {"filename": "a.csv", "data_base64": b64(EXPORT.encode()), "create_missing": True},
                      {"name": "a.csv", "text": EXPORT},                                  # no preview was looked at
                      {"name": "a.csv", "text": EXPORT, "fields": {"title": 0, "views": 2}},
                      {"name": "a.csv", "text": EXPORT, "fields": {"title": 0, "views": 2}, "fingerprint": 7}]
        for body in old_bodies:
            refusal = self.assert_refused(self.bring_in(**body), 400, "import_needs_preview")
            self.assertIn("preview", refusal["message"])
            self.assertIn("预览", refusal["message_zh"])
        self.assertEqual(self.stored(), was)

    def test_an_import_is_refused_when_it_would_no_longer_store_what_the_preview_showed(self):
        _, seen = self.preview(name="a.csv", text=EXPORT)
        was = self.stored()
        # The columns chosen again after the preview.
        moved = dict(seen["fields"], views=3, likes=2)
        self.assert_refused(self.bring_in(name="a.csv", text=EXPORT, fields=moved, fingerprint=seen["fingerprint"]), 409, "import_changed")
        # Another file under the same preview.
        self.assert_refused(self.imported(seen, name="a.csv", text=EXPORT.replace("2.5万", "2.6万")), 409, "import_changed")
        # The library changed in between: a second item took the title, so the row no longer names one item.
        twin = self.item("秋冬干皮护肤")
        self.assert_refused(self.imported(seen, name="a.csv", text=EXPORT), 409, "import_changed")
        self.store.delete_sample(twin["id"])
        # The item was renamed: the row would now match nothing.
        self.store.update_sample(self.market["id"], {"title": "周末超市日常（上）"})
        self.assert_refused(self.imported(seen, name="a.csv", text=EXPORT), 409, "import_changed")
        self.store.update_sample(self.market["id"], {"title": "周末超市日常"})
        # The day changed: the numbers would be dated differently from what was shown.
        self.on(datetime.date(2026, 11, 22))
        self.assert_refused(self.imported(seen, name="a.csv", text=EXPORT), 409, "import_changed")
        self.on(TODAY)
        self.assertEqual(self.stored(), was)
        self.assertEqual(self.imported(seen, name="a.csv", text=EXPORT)[0], 201)          # as it was shown: stored

    def test_the_creator_picks_the_columns_and_the_preview_follows(self):
        _, guessed = self.preview(name="a.csv", text=EXPORT)
        picked = dict(guessed["fields"], views=3, likes=2, collects=None, comments=None)
        status, out = self.preview(name="a.csv", text=EXPORT, fields=picked)
        self.assertEqual((status, out["fields"], out["guessed"]), (200, picked, guessed["fields"]))    # what was guessed is still said
        self.assertEqual([[r[f] for f in NUMBERS] for r in out["rows"]], [[900, 25000, None, None], [None, 3000, None, None], [1, 10, None, None]])
        self.assertNotEqual(out["fingerprint"], guessed["fingerprint"])
        # A field the request does not name is guessed, but never onto a column the creator gave to another field.
        _, partly = self.preview(name="a.csv", text=EXPORT, fields={"views": 5})
        self.assertEqual((partly["fields"]["views"], partly["fields"]["comments"], partly["fields"]["likes"]), (5, None, 3))
        for fields in ({"title": 8}, {"title": -1}, {"views": "2"}, {"views": True}, {"views": 2.0}, {"title": 0, "views": 0},
                       {"views": 2, "likes": 2}, {"read_on": [1]}):
            self.assert_refused(self.preview(name="a.csv", text=EXPORT, fields=fields), 400, "import_bad_column")
        self.assert_refused(self.preview(name="a.csv", text="100,5\n300,9\n"), 400, "import_no_title_column")
        self.assert_refused(self.preview(name="a.csv", text=EXPORT, fields={"title": None}), 400, "import_no_title_column")

    def test_importing_needs_the_columns_and_never_guesses_them(self):
        _, seen = self.preview(name="a.csv", text=EXPORT)
        for fields in (None, "all", {}, {"views": 2}, {"title": None, "views": 2}, {"title": 0}, {"title": 0, "read_on": 1}):
            body = {"name": "a.csv", "text": EXPORT, "fingerprint": seen["fingerprint"]}
            if fields is not None:
                body["fields"] = fields
            self.assert_refused(self.bring_in(**body), 400, "import_no_columns")
        # Only the title and the views were named: the other numbers of the file are not used, though a preview would have guessed them.
        _, narrow = self.preview(name="a.csv", text=EXPORT, fields={"title": 0, "views": 2, "likes": None, "collects": None, "comments": None,
                                                                   "read_on": None, "posted_on": None})
        status, _ = self.bring_in(name="a.csv", text=EXPORT, fields={"title": 0, "views": 2}, fingerprint=narrow["fingerprint"])
        self.assertEqual((status, self.numbers(self.skincare)), (201, [25000, 300, None, None]))

    def test_a_column_for_the_day_a_video_was_posted_never_dates_the_numbers(self):
        """The defect of round 1: with a 发布时间 column the numbers of today were stored as read on the day of posting."""
        _, seen = self.preview(name="a.csv", text=EXPORT)
        self.assertEqual((seen["fields"]["posted_on"], seen["fields"]["read_on"], seen["dated_by"]), (1, None, "import_day"))
        self.imported(seen, name="a.csv", text=EXPORT)
        self.assertEqual([day for day, *_ in self.readings(self.market)], ["2026-11-21"])
        self.assertNotIn("2026-11-01", [day for day, *_ in self.readings(self.skincare)])
        # Ten days later the same table is saved and imported again: the views gained are over those ten days.
        self.on(datetime.date(2026, 12, 1))
        later = EXPORT.replace("2.5万", "3.5万")
        _, seen = self.preview(name="a.csv", text=later)
        self.imported(seen, name="a.csv", text=later)
        self.assertEqual(self.per_day(self.skincare)["since_reading"], {"from": "2026-11-21", "to": "2026-12-01", "days": 10, "views": 10000, "per_day": 1000.0})

    def test_a_column_that_says_when_the_numbers_were_read_dates_them_and_an_older_reading_does_not_replace_newer_numbers(self):
        table = ("笔记标题,观看量,点赞量,统计日期\n秋冬干皮护肤,8000,250,2026-11-05\n周末超市日常,3000,90,2026/11/19\n"
                 "周末超市日常2,1,1,2026-11-30\n")
        self.item("周末超市日常2")
        _, seen = self.preview(name="a.csv", text=table)
        self.assertEqual((seen["fields"]["read_on"], seen["dated_by"]), (3, "column"))
        self.assertEqual([(r["read_on"], r["why"] or r["effect"]) for r in seen["rows"]],
                         [("2026-11-05", "reading_only"), ("2026-11-19", "update"), ("2026-11-30", "future_day")])
        status, out = self.imported(seen, name="a.csv", text=table)
        self.assertEqual((status, out["changed"], out["readings_only"]), (201, 1, 1))
        # Numbers of the 5th are older than what the library has of the 10th: kept as that day's reading, and the item's numbers stay.
        self.assertEqual(self.numbers(self.skincare), [10000, 300, None, None])
        self.assertEqual(self.readings(self.skincare), [("2026-11-05", 8000, 250, None, None), ("2026-11-10", 10000, 300, None, None)])
        self.assertEqual((self.numbers(self.market), self.readings(self.market)), ([3000, 90, None, None], [("2026-11-19", 3000, 90, None, None)]))

    def test_numbers_imported_for_a_posted_script_take_the_day_they_were_read_with_them(self):
        script = self.store.create_script(title="面霜那期", lines=[{"spoken_script_zh": "入冬以后脸特别干嘛 / 所以我换了一款面霜"}])
        posted.mark(self.store, script["id"], {"posted_on": "2026-11-12", "views": 900, "metrics_on": "2026-11-13"})
        table = "笔记标题,观看量,点赞量,统计日期\n面霜那期,5000,200,2026-11-20\n"
        _, seen = self.preview(name="a.csv", text=table)
        self.imported(seen, name="a.csv", text=table)
        recorded = posted.for_script(self.store, self.store.get_script(script["id"]))
        self.assertEqual((recorded["views"], recorded["likes"], recorded["metrics_on"], recorded["read_after_days"]), (5000, 200, "2026-11-20", 8))
        # Numbers that claim to have been read before the video went out are not taken, as on the script page.
        early = "笔记标题,观看量,统计日期\n面霜那期,7,2026-11-11\n"
        _, seen = self.preview(name="a.csv", text=early)
        self.assertEqual((seen["rows"][0]["why"], seen["would_change"]), ("read_before_posted", 0))

    def test_a_workbook_goes_through_both_requests(self):
        """Round 1 called a reader that does not exist for an .xlsx, so no workbook was ever imported."""
        data = workbook([("笔记数据", sheet([["笔记数据导出"], [], ["笔记标题", "观看量", "点赞量", "收藏量", "评论量", "统计日期", "备注"],
                                          ["秋冬干皮护肤", 25000, 900.0, "1,200", 85, 46345, "<b>粗体</b>"],
                                          ["周末超市日常", "3k", None, 20, 5, "2026-11-19", None]])), ("说明", sheet([["别的表"]]))])
        status, seen = self.preview(name="笔记数据.xlsx", file_base64=b64(data))
        self.assertEqual((status, seen["kind"], seen["sheet"], seen["other_sheets"], seen["header_row"]), (200, "xlsx", "笔记数据", ["说明"], 1))
        self.assertEqual(seen["fields"], {"title": 0, "views": 1, "likes": 2, "collects": 3, "comments": 4, "read_on": 5, "posted_on": None})
        self.assertEqual([([r[f] for f in NUMBERS], r["read_on"], r["effect"]) for r in seen["rows"]],
                         [([25000, 900, 1200, 85], "2026-11-19", "update"), ([3000, None, 20, 5], "2026-11-19", "update")])
        self.assertEqual(seen["columns"][6], {"index": 6, "header": "备注", "samples": ["<b>粗体</b>"]})
        status, out = self.imported(seen, name="笔记数据.xlsx", file_base64=b64(data))
        self.assertEqual((status, out["changed"]), (201, 2))
        self.assertEqual((self.numbers(self.skincare), self.numbers(self.market)), ([25000, 900, 1200, 85], [3000, None, 20, 5]))
        gb = "笔记标题,观看量\n周末超市日常,4000\n".encode("gb18030")                      # a text file as bytes: decoded by the reader, GB18030 included
        _, seen = self.preview(name="a.csv", file_base64=b64(gb))
        self.assertEqual(self.imported(seen, name="a.csv", file_base64=b64(gb))[0], 201)
        self.assertEqual(self.numbers(self.market)[0], 4000)

    def test_the_file_is_read_by_the_keyword_imports_reader_and_by_no_other(self):
        with mock.patch.object(ki, "read_table", wraps=ki.read_table) as reader:
            self.preview(name="a.csv", text=EXPORT)
            self.preview(name="a.xlsx", file_base64=b64(workbook([("s", sheet([["笔记标题", "观看量"], ["超市", 5]]))])))
        self.assertEqual(reader.call_count, 2)
        source = Path(ra.__file__).read_text(encoding="utf-8")
        for own_reader in ("import csv", "import zipfile", "import io", "xml", "b64decode", ".decode(", "open("):
            self.assertNotIn(own_reader, source, own_reader)
        self.assertNotIn("_xlsx_rows", source)                                            # the reader round 1 called, which never existed

    def test_a_file_that_cannot_be_read_is_refused_with_a_reason_in_both_languages_and_nothing_is_stored(self):
        good = workbook([("s", sheet([["笔记标题", "观看量"], ["秋冬干皮护肤", 5]]))])
        old_excel = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
        was = self.stored()
        cases = [
            ("an old Excel file", {"name": "数据.xls", "file_base64": b64(old_excel)}, 415, "import_old_excel"),
            ("another kind of file", {"name": "数据.pdf", "file_base64": b64(b"%PDF-1.7")}, 415, "import_unsupported"),
            ("a workbook named like a text file", {"name": "数据.csv", "file_base64": b64(good)}, 415, "import_wrong_kind"),
            ("a text file named .xlsx", {"name": "数据.xlsx", "file_base64": b64("笔记标题,观看量\n超市,5\n".encode())}, 415, "import_wrong_kind"),
            ("a workbook cut short", {"name": "数据.xlsx", "file_base64": b64(good[:len(good) // 2])}, 415, "import_wrong_kind"),
            ("nothing in it", {"name": "数据.csv", "text": "\n\n"}, 400, "import_not_a_table"),
            ("a quotation mark never closed", {"name": "数据.csv", "text": '笔记标题,观看量\n"超市,5\n面霜,7\n'}, 400, "import_not_a_table"),
            ("too large", {"name": "数据.csv", "file_base64": b64(b"a" * (ki.MAX_FILE_BYTES + 1))}, 413, "import_too_large"),
            ("no file", {"name": "数据.csv"}, 400, "import_no_file"),
            ("not base64", {"name": "数据.csv", "file_base64": "!!!not base64!!!"}, 400, "import_no_file"),
        ]
        for label, payload, status, code in cases:
            self.assert_refused(self.preview(**payload), status, code)
            self.assert_refused(self.bring_in(fields={"title": 0, "views": 1}, fingerprint="0" * 64, **payload), status, code)
        self.assertEqual(self.stored(), was)
        # Two of the reader's refusals speak of "the sheet with the keywords". Here they speak of numbers.
        for refusal in (ki._unpacks_too_far(), ki._too_many_texts()):
            said = ra._reworded(refusal)
            self.assertEqual((said.code, said.status), ("import_too_large", 413))
            self.assertNotIn("keyword", said.message)
            self.assertNotIn("关键词", said.message_zh)
            self.assertIn("numbers", said.message)
        self.assertEqual(ra._reworded(ki._too_large()).message, ki._too_large().message)

    def test_markup_in_a_file_stays_the_text_it_is(self):
        hostile = "<img src=x onerror=alert(1)>"
        self.item(hostile)
        table = "笔记标题,观看量\n%s,5\n\"<script>alert(2)</script>\",6\n" % hostile
        _, seen = self.preview(name="<b>a<i>.csv", text=table)
        self.assertEqual((seen["name"], [(r["title"], r["effect"], r["sample_title"]) for r in seen["rows"]]),
                         ("<b>a<i>.csv", [(hostile, "update", hostile), ("<script>alert(2)</script>", "unmatched", None)]))


# ---- the page ---------------------------------------------------------------------------------------------------------
class PageTest(ServerCase):
    """What the page states, and what it sends, has to fit what the server does."""

    def setUp(self):
        super().setUp()
        self.script = (WEB / "rednote.js").read_text(encoding="utf-8")

    def texts(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        return [dict(re.findall(r"\b(nc_[a-z_]+): '((?:[^'\\]|\\.)*)'", body)) for _, body in blocks]

    def test_the_size_and_the_kinds_of_file_the_page_offers_are_the_readers(self):
        self.assertIn("const NC_MAX_FILE_BYTES = %d;" % ki.MAX_FILE_BYTES, self.script)
        offered = re.search(r'id="nc-imp-file" accept="([^"]+)"', self.script).group(1)
        self.assertEqual(offered.split(","), ["." + kind for kind in ki.TEXT_EXTENSIONS] + [".xlsx"])
        fields = re.search(r"const NC_FIELDS = \[([^\]]+)\];", self.script).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", fields)), ra.FIELDS)

    def test_every_text_of_the_page_is_there_in_both_languages(self):
        zh, en = self.texts()
        self.assertGreater(len(zh), 60)
        self.assertEqual(set(zh), {key for key in en if not key.endswith("_one")})          # English alone has singular forms
        used = set(re.findall(r"""['"](nc_[a-z_]+)['"]""", self.script))
        completed = {"nc_s_", "nc_why_", "nc_f_"}                                           # completed with a reason or a field the server names
        self.assertEqual({key for key in used if key not in zh} - completed, set())
        for reason in ra.SKIP_REASONS:
            self.assertIn("nc_s_" + reason, zh)
            self.assertIn("nc_why_" + reason, zh)
        self.assertIn("nc_why_header", zh)
        for field in ra.FIELDS:
            self.assertIn("nc_f_" + field, zh)
        for key, text in zh.items():
            self.assertTrue(CJK.search(text) or "{" in text, key)
            self.assertFalse(CJK.search(en[key]), key)                                      # no Chinese left in the English page
        # What the page says about itself, held to what the code does.
        self.assertIn("只在这台电脑上读取", zh["nc_imp_hint"])
        self.assertIn("不会发到任何地方", zh["nc_imp_hint"])
        self.assertIn("read on this computer", en["nc_imp_hint"])
        self.assertIn("nothing is sent anywhere", en["nc_imp_hint"])
        self.assertIn("不新建", zh["nc_e_unmatched"])
        self.assertIn("nothing created", en["nc_e_unmatched"])
        self.assertIn("没有拿真实的导出表试过", zh["nc_guess_note"])
        self.assertIn("not been tried on a real export", en["nc_guess_note"])

    def test_the_page_asks_this_app_only_and_uses_nothing_of_the_plan_page(self):
        asked = set(re.findall(r"api\('(?:GET|POST|PUT|DELETE)', '([^']+)'", self.script))
        self.assertEqual({path for path in asked if "rednote" in path}, {"/api/rednote/import", "/api/rednote/import/preview"})
        self.assertTrue(all(path.startswith(("/api/rednote/", "/api/samples/")) for path in asked), asked)
        for word in ("fetch(", "XMLHttpRequest", "http://", "https://", "localStorage", "plFileToBase64", "'pl_", "extract-link", "create_missing"):
            self.assertNotIn(word, self.script, word)
        index = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index('src="/rednote.js"'), index.index('src="/pages.js"'))
        self.assertIn("plPaintLibraryTools(document.getElementById('rn-lib-box'), samples)", (WEB / "pages.js").read_text(encoding="utf-8"))

    @unittest.skipUnless(browser.AVAILABLE, browser.WHY_NOT)
    def test_in_a_browser_the_preview_shows_every_row_and_importing_stores_what_it_showed(self):
        skincare, market = self.item("秋冬干皮护肤", views=10000, likes=300), self.item("周末超市日常")
        hostile = self.item('<img src=x onerror="window.__ran = 1">')
        table = EXPORT + '"<img src=x onerror=""window.__ran = 1"">",2026-11-05,7,1,1,1,1%,00:01\n'
        choose = ("(() => { const dt = new DataTransfer(); dt.items.add(new File([%s], '笔记数据.csv'));"
                  " const input = document.getElementById('nc-imp-file'); input.files = dt.files;"
                  " input.dispatchEvent(new Event('change')); return true; })()" % json.dumps(table))
        effects = "Array.from(document.querySelectorAll('#nc-imp-table tbody tr')).map((row) => row.dataset.ncEffect)"
        for language in ("zh", "en"):
            with browser.open_app(self.store, lang=language) as page:
                page.go("#/library", "Boolean(document.getElementById('nc-card'))")
                page.eval_js("document.getElementById('nc-card').open = true")
                was = self.stored()
                page.eval_js(choose)
                page.wait_for("Boolean(document.getElementById('nc-imp-table'))")
                self.assertEqual(page.eval_js(effects), ["update", "update", "unmatched", "update"])
                self.assertEqual(page.eval_js("document.getElementById('nc-f-read_on').value"), "")
                self.assertEqual(page.eval_js("document.getElementById('nc-f-posted_on').value"), "1")
                card = page.eval_js("document.getElementById('nc-imp-card').textContent")
                self.assertIn("2026-11-21", page.eval_js("document.getElementById('nc-imp-dated').textContent"))
                self.assertNotRegex(card, r"\bnc_[a-z_]+")                                   # no text is missing in this language
                self.assertEqual(bool(CJK.search(page.eval_js("document.getElementById('nc-imp-counts').textContent"))), language == "zh")
                self.assertIn('<img src=x onerror="window.__ran = 1">', card)                # shown as the text it is
                self.assertEqual(self.stored(), was)                                         # nothing is stored by looking
                # The creator says the second column is the day the numbers were read: every row is dated by it, and
                # it is no longer the day of posting.
                page.eval_js("(() => { const s = document.getElementById('nc-f-read_on'); s.value = '1'; s.dispatchEvent(new Event('change')); return true; })()")
                page.wait_for("document.getElementById('nc-f-read_on') && document.getElementById('nc-f-read_on').value === '1'"
                              " && !document.getElementById('nc-f-read_on').disabled")
                self.assertEqual(page.eval_js("document.getElementById('nc-f-posted_on').value"), "")
                self.assertEqual(page.eval_js(effects), ["reading_only" if language == "zh" else "reading_only", "update", "unmatched", "update"])
                page.eval_js("(() => { const s = document.getElementById('nc-f-read_on'); s.value = ''; s.dispatchEvent(new Event('change')); return true; })()")
                page.wait_for("document.getElementById('nc-f-read_on') && document.getElementById('nc-f-read_on').value === ''"
                              " && !document.getElementById('nc-f-read_on').disabled")
                self.assertEqual(self.stored(), was)
                page.eval_js("document.getElementById('nc-imp-go').click()")
                page.wait_for("!document.getElementById('nc-imp-card') && Boolean(document.getElementById('nc-card'))")
                self.assertEqual(page.eval_js("window.__ran === undefined"), True)           # markup from a file never ran
                self.assertEqual([problem for problem in page.problems if "favicon.ico" not in problem], [])
            self.assertEqual((self.numbers(skincare), self.numbers(market), self.numbers(hostile)),
                             ([25000, 900, 1200, 85], [3000, None, 20, 5], [7, 1, 1, 1]))
            self.assertEqual(self.store.samples_count(), 3)                                  # and no item was created
            for sample in (skincare, market, hostile):                                       # back to the start for the other language
                self.store.update_sample(sample["id"], {field: "" for field in NUMBERS})
            self.store.update_sample(skincare["id"], {"views": 10000, "likes": 300})


if __name__ == "__main__":
    unittest.main()
