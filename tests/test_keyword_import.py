"""Tests for importing keyword exports: text tables, .xlsx workbooks, finding the columns, the two requests, hostile files.

No real export from 新红, 千瓜 or 灰豚 was available when these were written. Every file here is made up, and the
workbooks are built in the tests with ``zipfile``: they show that the reader handles the file formats, not that it
knows what those tools call their columns.

Timing tests use the same generous limit as tests/test_hardening.py. The reader needs well under a second for a
file that would hold an unbounded one for a long time, or fill the memory.
"""
import base64
import bz2
import codecs
import html
import http.client
import io
import json
import os
import random
import re
import shutil
import struct
import tempfile
import threading
import time
import tracemalloc
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from studio import keyword_import as ki
from studio import server, trends
from studio.store import MAX_KEYWORD_CHARS, Store

WEB = Path(server.WEB_DIR)
SLOW = 3.0              # seconds
MB = 1024 * 1024
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
LINKS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
BOMB = ('<!DOCTYPE lolz [<!ENTITY lol "lol">'
        + "".join('<!ENTITY lol%d "%s">' % (n, ("&lol%s;" % (n - 1 if n > 1 else "")) * 10) for n in range(1, 10)) + "]>")


# ---- building files -------------------------------------------------------------------------------------------------
def letters(column):
    """The letters of a column counted from 0: A, B, ... Z, AA."""
    out = ""
    column += 1
    while column:
        column, rest = divmod(column - 1, 26)
        out = chr(65 + rest) + out
    return out


def cell(value, reference=""):
    """One cell: a number for an int or a float, a text written into the cell for a str, an empty cell for None."""
    at = ' r="%s"' % reference if reference else ""
    if value is None:
        return "<c%s/>" % at
    if isinstance(value, bool):
        return '<c%s t="b"><v>%d</v></c>' % (at, value)
    if isinstance(value, (int, float)):
        return "<c%s><v>%r</v></c>" % (at, value)
    return '<c%s t="inlineStr"><is><t>%s</t></is></c>' % (at, html.escape(value))


def rows_xml(rows):
    return "".join('<row r="%d">%s</row>' % (n, "".join(cell(value, "%s%d" % (letters(i), n)) for i, value in enumerate(row)))
                   for n, row in enumerate(rows, 1))


def sheet(rows="", before="", doctype=""):
    """A worksheet part. ``rows`` is a list of rows of Python values, or the XML of the rows as it is."""
    inner = rows if isinstance(rows, str) else rows_xml(rows)
    return '%s%s<worksheet xmlns="%s">%s<sheetData>%s</sheetData></worksheet>' % (HEAD, doctype, MAIN, before, inner)


def shared(strings, doctype=""):
    return '%s%s<sst xmlns="%s">%s</sst>' % (HEAD, doctype, MAIN, "".join("<si><t>%s</t></si>" % html.escape(s) for s in strings))


def workbook(sheets, parts=None, method=zipfile.ZIP_DEFLATED):
    """An .xlsx as bytes. ``sheets`` is ``[(name of the tab, worksheet part)]``.

    ``parts`` adds parts by their name in the archive, replaces one, or leaves one out (None).
    """
    files = {
        "[Content_Types].xml": HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": '%s<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (
            HEAD, MAIN, LINKS, "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (html.escape(name), n, n)
                                       for n, (name, _) in enumerate(sheets, 1))),
        "xl/_rels/workbook.xml.rels": '%s<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
                                      '<Relationship Id="rId900" Type="%s/sharedStrings" Target="sharedStrings.xml"/></Relationships>' % (
            HEAD, "".join('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (n, LINKS, n)
                          for n in range(1, len(sheets) + 1)), LINKS),
    }
    for n, (_, part) in enumerate(sheets, 1):
        files["xl/worksheets/sheet%d.xml" % n] = part
    files.update(parts or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, part in files.items():
            if part is not None:
                archive.writestr(name, part)
    return buffer.getvalue()


def one_part_zip(name, stream, method, declared_size, crc=0):
    """A zip with one entry whose headers say what the test chooses, whatever the stream really holds."""
    encoded = name.encode()
    local = struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0, method, 0, 0x21, crc, len(stream), declared_size, len(encoded), 0) + encoded
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, 20, 20, 0, method, 0, 0x21, crc, len(stream), declared_size,
                          len(encoded), 0, 0, 0, 0, 0, 0) + encoded
    body = local + stream
    return body + central + struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, 1, 1, len(central), len(body), 0)


def b64(data):
    return base64.b64encode(data).decode("ascii")


def traced_peak(call):
    """``(what call() returned or raised, the most memory in bytes it held at one time)``."""
    was_tracing = tracemalloc.is_tracing()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        try:
            outcome = call()
        except Exception as exc:        # handed back, for the caller to judge
            outcome = exc
        return outcome, tracemalloc.get_traced_memory()[1] - before
    finally:
        if not was_tracing:
            tracemalloc.stop()


class Case(unittest.TestCase):
    def refused(self, code, call, *args, **kwargs):
        """The refusal ``call`` ends in. It has to carry ``code`` and a message in each language."""
        with self.assertRaises(ki.ImportRefused) as caught:
            call(*args, **kwargs)
        refusal = caught.exception
        self.assertEqual(refusal.code, code, refusal.message)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", refusal.message_zh), refusal.message_zh)
        self.assertFalse(re.search(r"[\u4e00-\u9fff]", refusal.message), refusal.message)
        return refusal

    def quickly(self, call, *args, **kwargs):
        """What ``call`` returned, or the refusal it raised. Fails when it took longer than SLOW."""
        started = time.time()
        try:
            outcome = call(*args, **kwargs)
        except ki.ImportRefused as refusal:
            outcome = refusal
        self.assertLess(time.time() - started, SLOW)
        return outcome


# ---- text files -----------------------------------------------------------------------------------------------------
class TextTableTest(Case):
    TABLE = "关键词,热度\n秋冬护肤,125000\n露营,98000\n"
    ROWS = [["关键词", "热度"], ["秋冬护肤", "125000"], ["露营", "98000"]]

    def rows(self, text, name="词表.csv"):
        return ki.read_table(name, text=text)["rows"]

    def test_utf8_with_or_without_a_mark_gb18030_and_excels_unicode_text(self):
        tabbed = self.TABLE.replace(",", "\t")
        files = {
            "UTF-8": self.TABLE.encode("utf-8"),
            "UTF-8 with a byte-order mark": codecs.BOM_UTF8 + self.TABLE.encode("utf-8"),
            "GB18030": self.TABLE.encode("gb18030"),
            "UTF-16 with its mark, as Excel saves Unicode text": tabbed.encode("utf-16"),
            "UTF-16 the other way round": codecs.BOM_UTF16_BE + tabbed.encode("utf-16-be"),
        }
        self.assertNotEqual(files["GB18030"], files["UTF-8"])
        with self.assertRaises(UnicodeDecodeError):
            files["GB18030"].decode("utf-8")                                     # so the fallback is what reads it
        for label, data in files.items():
            self.assertEqual(ki.read_table("词表.csv", data=data)["rows"], self.ROWS, label)
        self.assertEqual(self.rows("\ufeff" + self.TABLE), self.ROWS)            # a mark left in text the sender decoded
        damaged = ki.read_table("词表.csv", data="关键词\n露营\n".encode("gb18030") + b"\x81")
        self.assertEqual(damaged["rows"][:2], [["关键词"], ["露营"]])             # a byte that fits nothing does not stop the rest

    def test_the_delimiter_is_found_among_comma_tab_semicolon_and_full_width_comma(self):
        for delimiter in (",", "\t", ";", "，"):
            table = ki.read_table("a.txt", text=self.TABLE.replace(",", delimiter))
            self.assertEqual((table["delimiter"], table["rows"]), (delimiter, self.ROWS), repr(delimiter))
        # A comma inside a number does not make a tab or semicolon file a comma file.
        self.assertEqual(self.rows("关键词\t热度\n秋冬护肤\t125,000\n露营\t98,000\n"), [["关键词", "热度"], ["秋冬护肤", "125,000"], ["露营", "98,000"]])
        self.assertEqual(self.rows("keyword;volume\nskincare;1,234\ncamping;5,678\n"), [["keyword", "volume"], ["skincare", "1,234"], ["camping", "5,678"]])
        self.assertEqual(self.rows("秋冬护肤,125000"), [["秋冬护肤", "125000"]])   # one line is enough
        self.assertEqual(self.rows("a\r\nb,1\r\nc,2\r\n"), [["a"], ["b", "1"], ["c", "2"]])
        self.assertEqual(self.rows("关键词,热度\r露营,5\r"), [["关键词", "热度"], ["露营", "5"]])     # line ends of an old Mac

    def test_a_title_and_a_date_range_above_a_short_table_do_not_hide_its_delimiter(self):
        text = "千瓜数据 热搜词榜\n统计时间：2026-11-01 至 2026-11-07\n数据仅供参考\n\n关键词,热度\n露营,98000\n"
        self.assertEqual(self.rows(text)[-2:], [["关键词", "热度"], ["露营", "98000"]])

    def test_a_list_with_one_keyword_on_each_line_keeps_its_commas(self):
        text = "\n".join(["露营", "秋冬护肤", "护肤, 干皮", "早餐", "通勤穿搭", "周末, 去哪儿", "减脂餐", '"夫妻日常"', '5" 手机', '"他说""好"""'])
        table = ki.read_table("词表.txt", text=text)
        self.assertEqual(table["delimiter"], "")
        self.assertEqual([row[0] for row in table["rows"]],
                         ["露营", "秋冬护肤", "护肤, 干皮", "早餐", "通勤穿搭", "周末, 去哪儿", "减脂餐", "夫妻日常", '5" 手机', '他说"好"'])

    def test_quoted_cells_may_hold_the_delimiter_line_breaks_and_quotation_marks(self):
        text = '关键词,热度\r\n"护肤, 干皮",5\r\n"两行\n关键词",7\r\n"他说""好""",3\r\n"好物"推荐,2\r\n露营, "1,234"\r\n'
        self.assertEqual(self.rows(text), [["关键词", "热度"], ["护肤, 干皮", "5"], ["两行 关键词", "7"], ['他说"好"', "3"],
                                           ["好物推荐", "2"], ["露营", "1,234"]])

    def test_a_quotation_mark_that_is_never_closed_is_refused_not_folded_into_one_cell(self):
        refusal = self.refused("import_not_a_table", self.rows, '关键词,热度\n露营,5\n"护肤,7\n早餐,9\n')
        self.assertIn("row 3", refusal.message)
        self.assertIn("第 3 行", refusal.message_zh)
        self.refused("import_not_a_table", self.rows, '关键词,热度\n"' + "护肤,7\n" * 40000)    # too long for one cell of any spreadsheet

    def test_a_line_that_holds_only_the_readers_own_end_mark_does_not_end_the_file(self):
        """The reader puts a line with U+001E after the last line. A file with such a line of its own is read to its end."""
        text = "关键词,热度\n露营,5\n\x1e\n早餐,7\n\x1e护肤\x1e,9\n\x00\n通勤,11\n"
        self.assertEqual(self.rows(text), [["关键词", "热度"], ["露营", "5"], ["早餐", "7"], ["护肤", "9"], ["通勤", "11"]])
        self.assertEqual(ki.read_table("a.csv", text=text)["more_rows"], False)
        self.refused("import_not_a_table", self.rows, '关键词,热度\n"露营,5\n\x1e\n早餐,7\n')      # and it does not close a quotation mark either

    def test_blank_rows_and_empty_cells_at_the_end_of_a_row_are_left_out(self):
        table = ki.read_table("a.csv", text="露营,5,,\n\n,,\n  \n早餐,,7\n")
        self.assertEqual((table["rows"], table["width"]), ([["露营", "5"], ["早餐", "", "7"]], 3))
        for nothing in ("", "\n\n", ",,,\n,,\n", " \t \n"):
            self.refused("import_not_a_table", self.rows, nothing)

    def test_rows_columns_and_cells_are_capped_and_the_table_says_so(self):
        full = ki.read_table("a.csv", text="".join("词%d,%d\n" % (n, n) for n in range(ki.MAX_ROWS)))
        self.assertEqual((len(full["rows"]), full["more_rows"], full["more_columns"]), (ki.MAX_ROWS, False, False))
        over = ki.read_table("a.csv", text="".join("词%d,%d\n" % (n, n) for n in range(ki.MAX_ROWS + 1)))
        self.assertEqual((len(over["rows"]), over["more_rows"], over["rows"][-1]), (ki.MAX_ROWS, True, ["词4999", "4999"]))
        listed = ki.read_table("a.txt", text="".join("词%d\n" % n for n in range(ki.MAX_ROWS + 1)))
        self.assertEqual((len(listed["rows"]), listed["more_rows"]), (ki.MAX_ROWS, True))
        wide = ki.read_table("a.csv", text=(",".join("c%d" % n for n in range(60)) + "\n") * 2)
        self.assertEqual((wide["width"], wide["more_columns"], wide["rows"][0][-1]), (ki.MAX_COLUMNS, True, "c49"))
        long = ki.read_table("a.csv", text="露营,5\n%s,7\n" % ("长" * 300))["rows"][1][0]
        self.assertEqual((len(long), long[-1], long[:-1]), (ki.MAX_CELL_CHARS + 1, "…", "长" * ki.MAX_CELL_CHARS))

    def test_markup_in_a_cell_stays_text(self):
        hostile = "<img src=x onerror=alert(1)>"
        self.assertEqual(self.rows("关键词,热度\n%s,5\n" % hostile), [["关键词", "热度"], [hostile, "5"]])
        out = ki.preview({"name": "a.csv", "text": "关键词,热度\n%s,5\n<script>alert(2)</script>,6\n" % hostile})
        self.assertEqual([row["keyword"] for row in out["rows"]], [hostile, "<script>alert(2)</script>"])
        self.assertEqual(out["columns"][0]["samples"], [hostile, "<script>alert(2)</script>"])


# ---- workbooks ------------------------------------------------------------------------------------------------------
class WorkbookTest(Case):
    def rows(self, data, name="词表.xlsx"):
        return ki.read_table(name, data=data)["rows"]

    def test_texts_numbers_and_the_saved_values_of_formulas_are_read(self):
        xml = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
               '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2"><v>125000</v></c><c r="C2" t="b"><v>1</v></c></row>'
               '<row r="3"><c r="A3" t="inlineStr"><is><t>露营</t></is></c><c r="B3"><v>1.25E4</v></c><c r="C3" t="b"><v>0</v></c></row>'
               '<row r="4"><c r="A4" t="inlineStr"><is><r><t>早</t></r><r><rPr><b/></rPr><t xml:space="preserve">餐 </t></r>'
               '<rPh sb="0" eb="1"><t>zao can</t></rPh></is></c><c r="B4"><v>0.30000000000000004</v></c></row>'
               '<row r="5"><c r="A5" t="str"><f>CONCATENATE("通勤","穿搭")</f><v>通勤穿搭</v></c><c r="B5"><f>SUM(B2:B4)</f><v>137500.3</v></c></row>'
               '<row r="6"><c r="A6" t="s"><v>4</v></c><c r="B6" t="e"><f>1/0</f><v>#DIV/0!</v></c><c r="C6"><f>NOW()</f></c></row>'
               '<row r="7"><c r="A7" t="s"><v>5</v></c><c r="B7" s="3"><v>125000.0</v></c></row>')
        strings = ('%s<sst xmlns="%s" count="6" uniqueCount="6"><si><t>关键词</t></si><si><t>热度</t></si><si><t>在用</t></si>'
                   '<si><t>秋冬护肤</t></si><si><r><t>周末</t></r><r><t>去哪儿</t></r><rPh sb="0" eb="2"><t>zhou mo</t></rPh><phoneticPr fontId="1"/></si>'
                   '<si><t xml:space="preserve"> A &amp; B &lt;b&gt; </t></si></sst>' % (HEAD, MAIN))
        for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
            data = workbook([("热词", sheet(xml))], {"xl/sharedStrings.xml": strings}, method)
            self.assertEqual(self.rows(data), [
                ["关键词", "热度", "在用"], ["秋冬护肤", "125000", "TRUE"], ["露营", "12500", "FALSE"], ["早餐", "0.3"],
                ["通勤穿搭", "137500.3"], ["周末去哪儿"], ["A & B <b>", "125000"]], method)

    def test_a_formula_is_never_worked_out(self):
        """What a cell shows is what was saved with it. A formula without a saved value shows nothing."""
        xml = ('<row r="1"><c r="A1" t="inlineStr"><is><t>露营</t></is></c><c r="B1"><f>2+3</f></c><c r="C1"><f>2+3</f><v>999</v></c>'
               '<c r="D1" t="str"><f>HYPERLINK("http://example.com/steal","click")</f><v>click</v></c></row>')
        self.assertEqual(self.rows(workbook([("s", sheet(xml))])), [["露营", "", "999", "click"]])
        source = Path(ki.__file__).read_text(encoding="utf-8")
        for call in ("eval(", "exec(", "compile(", "__import__(", "subprocess", "os.system"):
            self.assertNotIn(call, source, call)                                 # nothing in the reader can run what a file holds

    def test_cells_are_placed_by_their_reference_and_rows_in_the_order_of_the_file(self):
        xml = ('<row r="2"><c r="C2"><v>3</v></c></row><row r="9"/><row r="40"><c r="B40" t="inlineStr"><is><t>露营</t></is></c>'
               '<c><v>7</v></c></row><row><c t="inlineStr"><is><t>没有位置</t></is></c><c><v>1</v></c></row>'
               '<row r="50"><c r="a50" t="inlineStr"><is><t>小写</t></is></c><c r="XFD50"><v>9</v></c></row>')
        table = ki.read_table("a.xlsx", data=workbook([("s", sheet(xml))]))
        self.assertEqual(table["rows"], [["", "", "3"], ["", "露营", "7"], ["没有位置", "1"], ["小写"]])
        self.assertEqual((table["more_columns"], table["width"]), (True, 3))     # XFD is column 16,384: past the limit, and said so

    def test_the_first_worksheet_is_read_and_the_others_are_named(self):
        data = workbook([("热词榜", sheet([["关键词"], ["露营"]])), ("说明 & 备注", sheet([["别的表"]])), ("第三张", sheet())])
        table = ki.read_table("a.xlsx", data=data)
        self.assertEqual((table["sheet"], table["other_sheets"], table["rows"]), ("热词榜", ["说明 & 备注", "第三张"], [["关键词"], ["露营"]]))
        self.assertEqual((table["kind"], table["delimiter"]), ("xlsx", ""))
        # A chart can stand first among the tabs. The first tab that is a worksheet is the one read.
        chart_first = workbook([("图表", "<chartsheet/>"), ("数据", sheet([["露营"]]))], {
            "xl/_rels/workbook.xml.rels": '%s<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                          '<Relationship Id="rId1" Type="%s/chartsheet" Target="worksheets/sheet1.xml"/>'
                                          '<Relationship Id="rId2" Type="%s/worksheet" Target="/xl/worksheets/sheet2.xml"/></Relationships>'
                                          % (HEAD, LINKS, LINKS)})
        table = ki.read_table("a.xlsx", data=chart_first)
        self.assertEqual((table["sheet"], table["other_sheets"], table["rows"]), ("数据", ["图表"], [["露营"]]))
        self.refused("import_not_a_table", ki.read_table, "a.xlsx", data=workbook([("空表", sheet()), ("有内容", sheet([["露营"]]))]))

    def test_workbooks_written_in_other_ways_are_read_too(self):
        # A prefix on every element, as some programs write it.
        prefixed = ('%s<x:worksheet xmlns:x="%s"><x:sheetData><x:row r="1"><x:c r="A1" t="inlineStr"><x:is><x:t>露营</x:t></x:is></x:c>'
                    '<x:c r="B1"><x:v>5</x:v></x:c></x:row></x:sheetData></x:worksheet>' % (HEAD, MAIN))
        self.assertEqual(self.rows(workbook([("s", prefixed)])), [["露营", "5"]])
        # The other namespace of the same format ("strict").
        strict = sheet([["露营", 5]]).replace(MAIN, "http://purl.oclc.org/ooxml/spreadsheetml/main")
        self.assertEqual(self.rows(workbook([("s", strict)])), [["露营", "5"]])
        # Links that cannot be followed: the usual name of the first sheet is tried.
        self.assertEqual(self.rows(workbook([("s", sheet([["露营"]]))], {"xl/_rels/workbook.xml.rels": None})), [["露营"]])
        # The list of shared texts under another name, and a cell that points past its end.
        xml = '<row><c t="s"><v>1</v></c><c t="s"><v>7</v></c><c t="s"><v>x</v></c><c><v>5</v></c></row>'
        moved = workbook([("s", sheet(xml))], {"xl/strings/all.xml": shared(["不用", "露营"]),
                                               "xl/_rels/workbook.xml.rels": '%s<Relationships xmlns="p"><Relationship Id="rId1" Type="%s/worksheet" '
                                               'Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="%s/sharedStrings" Target="strings/all.xml"/>'
                                               '</Relationships>' % (HEAD, LINKS, LINKS)})
        self.assertEqual(self.rows(moved), [["露营", "", "", "5"]])

    def test_a_link_out_of_the_archive_is_not_followed_and_nothing_is_written_to_disk(self):
        outside = ('%s<Relationships xmlns="p"><Relationship Id="rId1" Type="%s/worksheet" Target="../../../../etc/passwd"/>'
                   '<Relationship Id="rId2" Type="%s/worksheet" Target="file:///etc/passwd" TargetMode="External"/></Relationships>' % (HEAD, LINKS, LINKS))
        data = workbook([("s", sheet([["露营"]]))], {"xl/_rels/workbook.xml.rels": outside})
        other = workbook([("s", sheet('<row><c t="s"><v>0</v></c><c><v>5</v></c></row>'))], {"xl/sharedStrings.xml": shared(["露营"])})
        here = threading.get_ident()

        def not_from_here(real):
            """``real``, refused when this test's own thread calls it. Other threads of the test run are left alone."""
            def guarded(*args, **kwargs):
                if threading.get_ident() == here:
                    raise AssertionError("a file was opened or extracted")
                return real(*args, **kwargs)
            return guarded

        with mock.patch.object(zipfile.ZipFile, "extract", not_from_here(zipfile.ZipFile.extract)), \
                mock.patch.object(zipfile.ZipFile, "extractall", not_from_here(zipfile.ZipFile.extractall)), \
                mock.patch("builtins.open", not_from_here(open)), mock.patch("io.open", not_from_here(io.open)), \
                mock.patch("os.open", not_from_here(os.open)):
            first, second = self.rows(data), self.rows(other)
            with self.assertRaises(AssertionError):
                open(__file__)                                                   # the guard is in place while the workbooks are read
        self.assertEqual(first, [["露营"]])                                       # read from sheet1.xml inside the archive, in memory
        self.assertEqual(second, [["露营", "5"]])

    def test_rows_and_columns_of_a_sheet_are_capped_and_the_table_says_so(self):
        many = workbook([("s", sheet("".join('<row><c t="inlineStr"><is><t>词%d</t></is></c></row>' % n for n in range(ki.MAX_ROWS + 5))))])
        table = ki.read_table("a.xlsx", data=many)
        self.assertEqual((len(table["rows"]), table["more_rows"], table["rows"][-1]), (ki.MAX_ROWS, True, ["词4999"]))
        exact = workbook([("s", sheet("".join('<row><c><v>%d</v></c></row>' % n for n in range(ki.MAX_ROWS))))])
        self.assertEqual(ki.read_table("a.xlsx", data=exact)["more_rows"], False)
        wide = ki.read_table("a.xlsx", data=workbook([("s", sheet([list(range(60))]))]))
        self.assertEqual((wide["width"], wide["more_columns"], wide["rows"][0][-1]), (ki.MAX_COLUMNS, True, "49"))
        styled_only = ki.read_table("a.xlsx", data=workbook([("s", sheet('<row><c r="A1"><v>1</v></c><c r="BZ1" s="2"/></row>'))]))
        self.assertEqual(styled_only["more_columns"], False)                     # an empty cell far to the right holds nothing that was left out


# ---- numbers --------------------------------------------------------------------------------------------------------
class NumberTest(Case):
    def test_the_forms_the_paste_box_reads_are_read_in_a_cell(self):
        forms = {"125000": 125000, "12.5万": 125000, "1,234": 1234, "3k": 3000, "3K": 3000, "2亿": 200000000, "1.5w": 15000,
                 "1.5W+": 15000, "3k+": 3000, "999+": 999, " 12 万 ": 120000, "12 +": 12, "１２３": 123, "1,234,567": 1234567,
                 "0.6": 1, "12.5": 12, "1,": 1}
        for text, heat in forms.items():
            self.assertEqual(ki.read_heat(text), heat, text)
            self.assertEqual(trends.parse_keywords("词\t" + text.strip()), ([{"keyword": "词", "heat": heat}], 0), text)
        for text in ("", " ", "abc", "-5", "+5", "12%", "1.2.3", "12.", ".5", "万", "+", "k", "3k +", "3 k k", "1 2", "12次", "约 3 万",
                     "2026-11-01", "上升", "#N/A", ",5", "１２²"):
            self.assertIsNone(ki.read_heat(text), text)
        for text in ("0", "0.2", "0.00001k", "99999999999999", "9" * 400, "1亿亿"):
            self.assertIsNone(ki.read_heat(text), text)                           # a number, but not one that is kept as heat

    def test_a_number_cell_reads_as_the_paste_box_reads_the_same_number_after_a_tab(self):
        alphabet = ["0", "1", "2", "5", "9", ",", ".", "万", "亿", "w", "K", "k", "+"]
        chooser = random.Random(20261116)
        numbers = 0
        for length in range(1, 9):
            for _ in range(2500):
                text = "".join(chooser.choice(alphabet) for _ in range(length))
                parts = ki._number(text)
                if parts is None:
                    continue
                numbers += 1
                self.assertEqual(trends._trailing_heat("词\t" + text), ("词", "\t", parts[0], parts[1]), text)
                self.assertEqual(ki.read_heat(text), trends._heat(parts[0], parts[1]) or None, text)
        self.assertGreater(numbers, 1500)                                        # the comparison is not of a handful only

    def test_numbers_of_a_workbook_read_as_they_look_in_the_sheet(self):
        for raw, text in (("125000", "125000"), ("125000.0", "125000"), ("1.25E5", "125000"), ("1.25e+05", "125000"), ("12.5", "12.5"),
                          ("0.30000000000000004", "0.3"), ("-3", "-3"), ("1e400", ""), ("NaN", ""), ("", ""), (" 7 ", "7"),
                          ("12345678901234567890", "1.23456789e+19"), ("abc", "abc")):
            self.assertEqual(ki._number_text(raw), text, raw)


# ---- which row is the header, which columns hold what ----------------------------------------------------------------
class ColumnGuessTest(Case):
    def guess(self, *rows):
        found = ki.detect([list(row) for row in rows])
        return found["header_row"], found["keyword_column"], found["heat_column"]

    def test_columns_are_found_by_the_names_in_the_header(self):
        data = ("秋冬护肤", "125000", "3400", "上升")
        self.assertEqual(self.guess(("关键词", "热度"), ("露营", "5")), (0, 0, 1))
        self.assertEqual(self.guess(("排名", "关键词", "热度值", "笔记数", "趋势"), ("1",) + data), (0, 1, 2))
        self.assertEqual(self.guess(("Rank", "Keyword", "Search Volume"), ("1", "camping", "500")), (0, 1, 2))
        self.assertEqual(self.guess(("query", "index"), ("camping", "500")), (0, 0, 1))
        self.assertEqual(self.guess(("热搜词", "近7天搜索指数"), ("露营", "500")), (0, 0, 1))
        self.assertEqual(self.guess((" 关 键 词 ", " 互动量 "), ("露营", "500")), (0, 0, 1))
        for name in ki.KEYWORD_HEADERS:
            for number in ki.HEAT_HEADERS:
                self.assertEqual(self.guess(("序号", number, name), ("1", "500", "露营")), (0, 2, 1), (name, number))

    def test_the_earlier_name_in_the_list_wins_and_the_whole_cell_beats_a_part_of_it(self):
        self.assertEqual(self.guess(("话题", "关键词", "搜索量", "热度"), ("护肤", "露营", "5", "7")), (0, 1, 3))
        self.assertEqual(self.guess(("关键词分类", "关键词", "热度趋势", "热度"), ("户外", "露营", "上升", "7")), (0, 1, 3))
        self.assertEqual(self.guess(("相关关键词", "关键词"), ("帐篷", "露营")), (0, 1, None))

    def test_a_header_that_names_both_is_the_number_of_that_keyword(self):
        self.assertEqual(self.guess(("关键词", "关键词热度"), ("露营", "5")), (0, 0, 1))
        self.assertEqual(self.guess(("话题热度", "话题"), ("5", "露营")), (0, 1, 0))
        self.assertEqual(self.guess(("搜索词指数", "搜索词"), ("5", "露营")), (0, 1, 0))

    def test_the_header_row_may_stand_below_a_title_and_a_date_range(self):
        rows = [("千瓜数据 热搜词榜",), ("统计时间：2026-11-01 至 2026-11-07",), ("排名", "关键词", "热度值", "笔记数", "趋势"),
                ("1", "秋冬护肤", "125000", "3400", "上升"), ("2", "露营", "98000", "2100", "下降")]
        self.assertEqual(self.guess(*rows), (2, 1, 2))
        self.assertEqual(self.guess(("热词",), ("关键词", "热度"), ("露营", "5")), (1, 0, 1))        # a title that is itself a column name
        self.assertEqual(self.guess(*([("说明 %d" % n,) for n in range(ki.HEADER_SCAN_ROWS - 1)] + [("关键词", "热度"), ("露营", "5")])),
                         (ki.HEADER_SCAN_ROWS - 1, 0, 1))
        self.assertEqual(self.guess(*([("说明 %d" % n,) for n in range(ki.HEADER_SCAN_ROWS)] + [("关键词", "热度"), ("露营", "5")]))[0], None)

    def test_a_row_is_not_taken_for_a_header_lightly(self):
        """A list without a header must not lose the rows above a keyword that contains 词 or 话题."""
        self.assertEqual(self.guess(("秋冬护肤",), ("英语单词",), ("话题讨论",), ("露营",)), (None, 0, None))
        self.assertEqual(self.guess(("护肤话题", "12000"), ("热词盘点", "500")), (None, 0, 1))          # a number: this row is data
        self.assertEqual(self.guess(("小红书 2026 年度热词盘点和关键词趋势报告（内部资料，请勿外传）", "热度"), ("露营", "5"))[0], 0)
        self.assertEqual(self.guess(("这一列是我自己整理的热词", ), ("露营",)), (None, 0, None))

    def test_without_a_header_the_contents_decide(self):
        self.assertEqual(self.guess(("1", "秋冬护肤", "美妆", "125000"), ("2", "露营", "户外", "98000"), ("3", "早C晚A", "美妆", "5000")),
                         (None, 1, 3))                                           # most different short texts; the running count is no heat
        self.assertEqual(self.guess(("美妆", "秋冬护肤", "125000"), ("美妆", "早C晚A", "98000"), ("户外", "露营", "5000")), (None, 1, 2))
        self.assertEqual(self.guess(("500", "露营"), ("300", "早餐"), ("900", "护肤")), (None, 1, 0))
        self.assertEqual(self.guess(("露营", "x" * 60), ("早餐", "y" * 60)), (None, 0, None))            # long texts are not keywords
        self.assertEqual(self.guess(("露营", "12.5万", "上升"), ("早餐", "3k+", "下降"), ("护肤", "1,234", "上升")), (None, 0, 1))
        self.assertEqual(self.guess(("露营", "5", "500"), ("早餐", "—", "300"), ("护肤", "", "900")), (None, 0, 2))   # the column with the most numbers
        self.assertEqual(self.guess(*[("词%d" % n, "5" if n < 2 else "") for n in range(10)]), (None, 0, None))    # too few numbers to be a column of them
        self.assertEqual(self.guess(("名称", "热度"), ("露营", "5"), ("早餐", "7")), (0, 0, 1))          # one name known, the other by contents
        self.assertEqual(self.guess(("关键词", "数值"), ("露营", "5"), ("早餐", "7")), (0, 0, 1))

    def test_the_number_is_optional_and_a_table_of_numbers_has_no_keywords(self):
        self.assertEqual(self.guess(("露营",), ("早餐",)), (None, 0, None))
        self.assertEqual(self.guess(("关键词",), ("露营",)), (0, 0, None))
        self.assertEqual(self.guess(("1", "2"), ("3", "4"))[:2], (None, None))
        self.refused("import_no_keyword_column", ki.preview, {"name": "a.csv", "text": "1,2\n3,4\n"})
        self.refused("import_no_keyword_column", ki.preview, {"name": "a.txt", "text": "长" * 80 + "\n"})


# ---- what an import would store ---------------------------------------------------------------------------------------
class PlanTest(Case):
    def plan(self, rows, header_row=None, keyword_column=0, heat_column=1):
        items, shown, counts = ki.plan([list(row) for row in rows], header_row, keyword_column, heat_column)
        return [(item["keyword"], item["heat"]) for item in items], shown, counts

    def test_every_row_becomes_a_keyword_or_is_counted_with_its_reason(self):
        rows = [("标题",), ("关键词", "热度"), ("露营", "500"), ("", "7"), ("#", "8"), ("x" * (MAX_KEYWORD_CHARS + 1), "9"),
                ("y" * MAX_KEYWORD_CHARS, "10"), ("#露 营", "900"), ("  早餐  ", ""), ("早餐", "12.5万"), ("早餐", "3"), ("z" * 300, "1")]
        rows[-1] = (ki._tidy(rows[-1][0]), "1")                                   # as the reader hands on a cell that was cut
        items, shown, counts = self.plan(rows, header_row=1)
        self.assertEqual(items, [("露营", 500), ("y" * MAX_KEYWORD_CHARS, 10), ("早餐", 125000)])
        self.assertEqual(counts, {"header": 2, "empty": 2, "too_long": 2, "duplicate": 3, "over_limit": 0})
        self.assertEqual(len(rows), len(items) + sum(counts.values()))
        self.assertEqual([(row["keyword"][:6], row["heat"], row["skipped"]) for row in shown], [
            ("露营", 500, None), ("", 7, "empty"), ("", 8, "empty"), ("xxxxxx", 9, "too_long"), ("yyyyyy", 10, None), ("露 营", 900, "duplicate"),
            ("早餐", 125000, None), ("早餐", 125000, "duplicate"), ("早餐", 3, "duplicate"), ("zzzzzz", 1, "too_long")])

    def test_the_first_of_two_rows_with_one_keyword_is_kept_and_a_later_one_only_fills_in_its_number(self):
        self.assertEqual(self.plan([("露营", "500"), ("露营", "900")])[0], [("露营", 500)])
        self.assertEqual(self.plan([("露营", ""), ("露营", "900"), ("露营", "7")])[0], [("露营", 900)])
        self.assertEqual(self.plan([("Camping", "5"), ("camping", "7"), ("CAMP ING", "9")])[0], [("Camping", 5)])
        self.assertEqual(self.plan([("露营", "500"), ("早餐", "900")], heat_column=None)[0], [("露营", None), ("早餐", None)])

    def test_one_import_holds_the_first_keywords_up_to_the_limit_of_one_paste(self):
        limit = trends.MAX_KEYWORDS_PER_PASTE
        rows = [("词%d" % n, str(n + 1)) for n in range(limit + 30)] + [("词3", "1"), ("", "")]
        items, shown, counts = self.plan(rows)
        self.assertEqual((len(items), items[0], items[-1]), (limit, ("词0", 1), ("词%d" % (limit - 1), limit)))
        self.assertEqual(counts, {"header": 0, "empty": 1, "too_long": 0, "duplicate": 1, "over_limit": 30})
        self.assertEqual(len(shown), ki.PREVIEW_ROWS)

    def test_a_column_the_row_does_not_reach_is_empty(self):
        items, _, counts = self.plan([("露营",), ("早餐", "5")], keyword_column=1, heat_column=4)
        self.assertEqual((items, counts["empty"]), ([("5", None)], 1))


# ---- the two requests ---------------------------------------------------------------------------------------------------
class ServerCase(Case):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.store = Store(self.tmp / "data")
        self.httpd = server.make_server(0, self.store, lambda *args: {})
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def send(self, method, path, payload=None, body=None, headers=None):
        head = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            return res.status, json.loads(res.read().decode("utf-8"))
        finally:
            conn.close()

    def preview(self, **payload):
        return self.send("POST", "/api/trends/import/preview", payload)

    def bring_in(self, **payload):
        return self.send("POST", "/api/trends/import", payload)

    def tracked(self):
        return [(t["keyword"], t["heat"], t["source"]) for t in self.send("GET", "/api/trends")[1]["trends"]]


class RouteTest(ServerCase):
    EXPORT = ("千瓜数据 热搜词榜\n统计时间：2026-11-01 至 2026-11-07\n\n排名,关键词,类目,热度值,笔记数\n"
              "1,秋冬护肤,美妆,12.5万,3400\n2,露营,户外,\"98,000\",2100\n3,#露营,户外,1,1\n4,,美妆,5,5\n5,早C晚A,美妆,3k+,900\n")

    def test_the_preview_says_what_was_found_and_stores_nothing(self):
        status, out = self.preview(name="千瓜-热搜词.csv", text=self.EXPORT)
        self.assertEqual(status, 200, out)
        self.assertEqual((out["name"], out["kind"], out["delimiter"], out["sheet"], out["other_sheets"]), ("千瓜-热搜词.csv", "text", ",", "", []))
        self.assertEqual((out["header_row"], out["headers"]), (2, ["排名", "关键词", "类目", "热度值", "笔记数"]))
        self.assertEqual((out["keyword_column"], out["heat_column"], out["guessed"]), (1, 3, {"keyword_column": 1, "heat_column": 3}))
        self.assertEqual(out["columns"], [
            {"index": 0, "header": "排名", "samples": ["1", "2"]}, {"index": 1, "header": "关键词", "samples": ["秋冬护肤", "露营"]},
            {"index": 2, "header": "类目", "samples": ["美妆", "户外"]}, {"index": 3, "header": "热度值", "samples": ["12.5万", "98,000"]},
            {"index": 4, "header": "笔记数", "samples": ["3400", "2100"]}])
        self.assertEqual(out["rows"], [
            {"keyword": "秋冬护肤", "heat": 125000, "skipped": None}, {"keyword": "露营", "heat": 98000, "skipped": None},
            {"keyword": "露营", "heat": 1, "skipped": "duplicate"}, {"keyword": "", "heat": 5, "skipped": "empty"},
            {"keyword": "早C晚A", "heat": 3000, "skipped": None}])
        self.assertEqual((out["rows_read"], out["would_import"]), (8, 3))
        self.assertEqual(out["skipped"], {"header": 3, "empty": 1, "too_long": 0, "duplicate": 1, "over_limit": 0})
        self.assertEqual(out["rows_read"], out["would_import"] + sum(out["skipped"].values()))
        self.assertEqual((out["more_rows"], out["more_columns"]), (False, False))
        self.assertEqual(out["limits"], {"keywords": trends.MAX_KEYWORDS_PER_PASTE, "rows": ki.MAX_ROWS, "columns": ki.MAX_COLUMNS,
                                         "keyword_chars": MAX_KEYWORD_CHARS})
        self.assertEqual(self.tracked(), [])                                     # looking is not importing
        self.assertEqual(self.store.list_trends(), [])

    def test_the_creator_picks_the_columns_and_the_preview_follows(self):
        status, out = self.preview(name="a.csv", text=self.EXPORT, keyword_column=2, heat_column=4)
        self.assertEqual((status, out["keyword_column"], out["heat_column"]), (200, 2, 4))
        self.assertEqual(out["guessed"], {"keyword_column": 1, "heat_column": 3})                # still said, beside what she chose
        self.assertEqual([(r["keyword"], r["heat"], r["skipped"]) for r in out["rows"]], [
            ("美妆", 3400, None), ("户外", 2100, None), ("户外", 1, "duplicate"), ("美妆", 5, "duplicate"), ("美妆", 900, "duplicate")])
        self.assertEqual((out["would_import"], out["skipped"]["duplicate"]), (2, 3))
        _, none = self.preview(name="a.csv", text=self.EXPORT, keyword_column=1, heat_column=None)
        self.assertEqual((none["heat_column"], [r["heat"] for r in none["rows"]]), (None, [None] * 5))
        _, moved = self.preview(name="a.csv", text=self.EXPORT, keyword_column=3)                # onto the column guessed for the number
        self.assertEqual((moved["keyword_column"], moved["heat_column"]), (3, None))
        for columns in ({"keyword_column": 5}, {"keyword_column": -1}, {"keyword_column": "1"}, {"keyword_column": True},
                        {"keyword_column": 1.0}, {"keyword_column": 1, "heat_column": 1}, {"keyword_column": 1, "heat_column": 99},
                        {"heat_column": [3]}):
            status, body = self.preview(name="a.csv", text=self.EXPORT, **columns)
            self.assertEqual((status, body.get("error")), (400, "import_bad_column"), columns)
        self.assertEqual(self.tracked(), [])

    def test_importing_stores_the_keywords_as_the_creators_own_and_answers_like_a_paste(self):
        _, pasted = self.send("POST", "/api/trends", {"text": "减脂餐, 800"})
        status, out = self.bring_in(name="千瓜-热搜词.csv", text=self.EXPORT, keyword_column=1, heat_column=3)
        self.assertEqual(status, 201, out)
        self.assertEqual((out["added"], out["skipped"], out["left_out"], out["duplicates"], out["rows_read"]), (3, 1, 0, 1, 8))
        self.assertEqual(out["skipped_rows"], {"header": 3, "empty": 1, "too_long": 0, "duplicate": 1, "over_limit": 0})
        self.assertEqual(set(pasted) - set(out), set())                          # everything a paste answers with is here too
        self.assertEqual([(t["keyword"], t["heat"], t["source"], t["heat_known"]) for t in out["trends"]], [
            ("秋冬护肤", 125000, "manual", True), ("露营", 98000, "manual", True), ("早C晚A", 3000, "manual", True),
            ("减脂餐", 800, "manual", True)])                                     # ranked by the scoring that was there: the hottest first
        self.assertEqual(self.tracked(), [(t["keyword"], t["heat"], t["source"]) for t in out["trends"]])
        self.assertEqual(self.send("GET", "/api/state")[1]["trend_count"], 4)

        status, again = self.bring_in(name="a.csv", text="关键词\n露营\n围炉煮茶\n", keyword_column=0, heat_column=None)
        self.assertEqual((status, again["added"]), (201, 2))
        self.assertIn(("露营", 98000, "manual"), self.tracked())                 # a file without numbers does not wipe one that is known
        self.assertIn(("围炉煮茶", None, "manual"), self.tracked())

    def test_a_keyword_an_ai_found_becomes_the_creators_when_her_file_holds_it(self):
        self.store.upsert_trends([{"keyword": "露营", "source_url": "https://example.com/list", "note_zh": "榜单第三"}], source="ai")
        self.bring_in(name="a.csv", text="露营,500\n", keyword_column=0, heat_column=1)
        row = self.store.list_trends()[0]
        self.assertEqual((row["keyword"], row["heat"], row["source"], row["source_url"], row["note_zh"]), ("露营", 500, "manual", "", ""))

    def test_importing_needs_the_columns_and_never_guesses_them(self):
        for columns, code in (({}, "import_no_keyword_column"), ({"keyword_column": None}, "import_no_keyword_column"),
                              ({"heat_column": 3}, "import_no_keyword_column"), ({"keyword_column": 9}, "import_bad_column"),
                              ({"keyword_column": 1, "heat_column": 1}, "import_bad_column"),
                              ({"keyword_column": 1, "heat_column": "3"}, "import_bad_column")):
            status, body = self.bring_in(name="a.csv", text=self.EXPORT, **columns)
            self.assertEqual((status, body.get("error")), (400, code), columns)
            self.assertTrue(body["message"] and re.search(r"[\u4e00-\u9fff]", body["message_zh"]), body)
        status, body = self.bring_in(name="a.csv", text="关键词,热度\n,5\n#,7\n%s,9\n" % ("长" * 50), keyword_column=0, heat_column=1)
        self.assertEqual((status, body.get("error")), (400, "import_no_keywords"))               # a column that gives no keyword at all
        self.assertEqual(self.tracked(), [])
        status, out = self.bring_in(name="a.csv", text=self.EXPORT, keyword_column=1)             # no number column named: none is used
        self.assertEqual((status, [t["heat"] for t in out["trends"]]), (201, [None, None, None]))

    def test_one_import_holds_at_most_one_paste_of_keywords_and_says_how_many_were_left_out(self):
        limit = trends.MAX_KEYWORDS_PER_PASTE
        text = "关键词,热度\n" + "".join("词%d,%d\n" % (n, 1000 - n) for n in range(limit + 30))
        _, seen = self.preview(name="a.csv", text=text)
        self.assertEqual((seen["would_import"], seen["skipped"]["over_limit"], len(seen["rows"])), (limit, 30, ki.PREVIEW_ROWS))
        status, out = self.bring_in(name="a.csv", text=text, keyword_column=0, heat_column=1)
        self.assertEqual((status, out["added"], out["left_out"], out["skipped"], len(out["trends"])), (201, limit, 30, 30, limit))
        self.assertEqual(out["trends"][0]["keyword"], "词0")

    def test_a_workbook_goes_through_both_requests_as_bytes(self):
        data = workbook([("热词榜", sheet([["新红 热词榜"], [], ["关键词", "搜索指数", "备注"], ["秋冬护肤", 125000, "<b>粗体</b>"],
                                         ["露营", 98000.0, None], ["早C晚A", "3k", None]])), ("说明", sheet([["别的表"]]))])
        status, out = self.preview(name="新红-热词.xlsx", file_base64=b64(data))
        self.assertEqual((status, out["kind"], out["sheet"], out["other_sheets"]), (200, "xlsx", "热词榜", ["说明"]))
        self.assertEqual((out["header_row"], out["keyword_column"], out["heat_column"], out["would_import"]), (1, 0, 1, 3))
        self.assertEqual(out["columns"][2], {"index": 2, "header": "备注", "samples": ["<b>粗体</b>"]})
        status, out = self.bring_in(name="新红-热词.xlsx", file_base64=b64(data), keyword_column=0, heat_column=1)
        self.assertEqual((status, out["added"]), (201, 3))
        self.assertEqual(self.tracked(), [("秋冬护肤", 125000, "manual"), ("露营", 98000, "manual"), ("早C晚A", 3000, "manual")])
        status, out = self.bring_in(name="a.csv", file_base64=b64("关键词,热度\n围炉煮茶,7\n".encode("gb18030")), keyword_column=0, heat_column=1)
        self.assertEqual((status, out["added"]), (201, 1))                       # a text file as bytes: decoded here, GB18030 included

    def test_markup_in_a_file_is_stored_and_answered_as_the_text_it_is(self):
        hostile = "<img src=x onerror=alert(1)>"
        text = "关键词,热度\n%s,5\n\"<script>alert(2)</script>\",6\n" % hostile
        _, seen = self.preview(name="<b>a<i>.csv", text=text)
        self.assertEqual((seen["name"], [row["keyword"] for row in seen["rows"]]), ("<b>a<i>.csv", [hostile, "<script>alert(2)</script>"]))
        _, out = self.bring_in(name="a.csv", text=text, keyword_column=0, heat_column=1)
        self.assertEqual(sorted(t["keyword"] for t in out["trends"]), sorted([hostile, "<script>alert(2)</script>"]))

    def test_a_file_that_cannot_be_imported_is_refused_with_a_reason_in_both_languages(self):
        good = workbook([("s", sheet([["关键词"], ["露营"]]))])
        old_excel = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
        cases = [
            ("an old Excel file", {"name": "词表.xls", "file_base64": b64(old_excel)}, 415, "import_old_excel", ".xlsx or .csv"),
            ("an old Excel file, whatever it holds", {"name": "词表.XLS", "text": "关键词\n露营\n"}, 415, "import_old_excel", ".xlsx or .csv"),
            ("another kind of file", {"name": "词表.pdf", "file_base64": b64(b"%PDF-1.7")}, 415, "import_unsupported", ".csv"),
            ("bytes with no kind in the name", {"name": "词表", "file_base64": b64(b"a,1")}, 415, "import_unsupported", ".csv"),
            ("a workbook named like a text file", {"name": "词表.csv", "file_base64": b64(good)}, 415, "import_wrong_kind", ".csv"),
            ("an old Excel file named .csv", {"name": "词表.csv", "file_base64": b64(old_excel)}, 415, "import_wrong_kind", ".xls"),
            ("an old Excel file named .xlsx", {"name": "词表.xlsx", "file_base64": b64(old_excel)}, 415, "import_wrong_kind", "password"),
            ("a picture named .txt", {"name": "词表.txt", "file_base64": b64(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR")}, 415, "import_wrong_kind", "not text"),
            ("a text file named .xlsx", {"name": "词表.xlsx", "file_base64": b64("关键词\n露营\n".encode())}, 415, "import_wrong_kind", "workbook"),
            ("text sent for a workbook", {"name": "词表.xlsx", "text": "关键词\n露营\n"}, 415, "import_wrong_kind", "workbook"),
            ("another zip named .xlsx", {"name": "词表.xlsx", "file_base64": b64(workbook([], {"xl/workbook.xml": None, "word/document.xml": "<w/>"}))},
             415, "import_wrong_kind", "workbook"),
            ("a workbook cut short", {"name": "词表.xlsx", "file_base64": b64(good[:len(good) // 2])}, 415, "import_wrong_kind", "workbook"),
            ("nothing in it", {"name": "词表.csv", "text": "\n\n"}, 400, "import_not_a_table", "empty"),
            ("an empty first sheet", {"name": "词表.xlsx", "file_base64": b64(workbook([("s", sheet())]))}, 400, "import_not_a_table", "empty"),
            ("a quotation mark never closed", {"name": "词表.csv", "text": '关键词,热度\n"露营,5\n早餐,7\n'}, 400, "import_not_a_table", "quotation mark"),
            ("too large", {"name": "词表.csv", "file_base64": b64(b"a" * (ki.MAX_FILE_BYTES + 1))}, 413, "import_too_large", "8 MB"),
            ("too large as text", {"name": "词表.csv", "text": "a" * (ki.MAX_FILE_BYTES + 1)}, 413, "import_too_large", "8 MB"),
            ("no file", {"name": "词表.csv"}, 400, "import_no_file", "No file"),
            ("not base64", {"name": "词表.csv", "file_base64": "!!!not base64!!!"}, 400, "import_no_file", "No file"),
            ("a file that is not text", {"name": "词表.csv", "file_base64": {"a": 1}, "text": 5}, 400, "import_no_file", "No file"),
        ]
        for label, payload, status, code, said in cases:
            for call in (self.preview, self.bring_in):
                got, body = call(keyword_column=0, **payload)
                self.assertEqual((got, body.get("error")), (status, code), label)
                self.assertIn(said, body["message"], label)
                self.assertTrue(re.search(r"[\u4e00-\u9fff]", body["message_zh"]), label)
                self.assertFalse(re.search(r"[\u4e00-\u9fff]", body["message"]), label)
        # No column looks like keywords. That is said when the server is asked to pick; a column the creator names is taken as she says.
        got, body = self.preview(name="词表.csv", text="1,2\n3,4\n")
        self.assertEqual((got, body.get("error")), (400, "import_no_keyword_column"))
        self.assertIn("%d characters" % MAX_KEYWORD_CHARS, body["message"])
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", body["message_zh"]))
        self.assertEqual(self.preview(name="词表.csv", text="1,2\n3,4\n", keyword_column=0)[1]["would_import"], 2)
        got, body = self.bring_in(name="词表.csv", text="关键词\n露营\n")              # an import that names no column: nothing is guessed
        self.assertEqual((got, body.get("error")), (400, "import_no_keyword_column"))
        self.assertIn("Choose the column", body["message"])
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", body["message_zh"]))
        self.assertEqual(self.tracked(), [])

    def test_both_requests_are_json_writes_like_every_other(self):
        for path in ("/api/trends/import/preview", "/api/trends/import"):
            self.assertEqual(self.send("GET", path)[0], 404, path)
            status, body = self.send("POST", path, body=b"name=a.csv&text=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual((status, body.get("error")), (415, "json_required"), path)
            status, body = self.send("POST", path, {"name": "a.csv", "text": "露营\n", "keyword_column": 0},
                                     headers={"Origin": "https://attacker.example"})
            self.assertEqual((status, body.get("error")), (403, "bad_origin"), path)
        self.assertEqual(self.tracked(), [])


# ---- hostile files ------------------------------------------------------------------------------------------------------
class HostileFileTest(ServerCase):
    def read(self, data, name="a.xlsx"):
        return self.quickly(ki.read_table, name, data=data)

    def assert_refused(self, outcome, code, label=""):
        self.assertIsInstance(outcome, ki.ImportRefused, label)
        self.assertEqual(outcome.code, code, label)

    def test_a_part_that_unpacks_to_far_more_than_its_size_is_refused_unread(self):
        huge = sheet('<row><c t="inlineStr"><is><t>%s</t></is></c></row>' % ("a" * (19 * MB)))
        data = workbook([("s", huge)])
        self.assertLess(len(data), 100 * 1024)                                   # 19 MB in a file of a few kilobytes
        started = time.time()
        outcome, peak = traced_peak(lambda: ki.read_table("a.xlsx", data=data))
        self.assertLess(time.time() - started, SLOW)
        self.assert_refused(outcome, "import_too_large")
        self.assertLess(peak, 2 * MB, "memory used while refusing: %d KB" % (peak // 1024))
        for part in ("xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/sharedStrings.xml"):
            bomb = workbook([("s", sheet('<row><c t="s"><v>0</v></c></row>'))], {part: "<a>%s</a>" % (" " * (19 * MB))})
            self.assert_refused(self.read(bomb), "import_too_large", part)
        status, body = self.quickly(self.preview, name="a.xlsx", file_base64=b64(data))
        self.assertEqual((status, body["error"]), (413, "import_too_large"))
        # A small sheet of one repeated value packs as well, honestly. It is read.
        honest = workbook([("s", sheet('<row><c t="inlineStr"><is><t>露营</t></is></c></row>' * 1000))])
        self.assertEqual(len(self.read(honest)["rows"]), 1000)

    def test_an_archive_that_lies_about_its_size_is_not_expanded(self):
        """The part really holds 64 MB; its header says 100 bytes. No more than the header says is ever unpacked."""
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        stream = b"".join([packer.compress(b"\0" * (4 * MB)) for _ in range(16)] + [packer.flush()])
        lying = one_part_zip("xl/workbook.xml", stream, zipfile.ZIP_DEFLATED, 100, zlib.crc32(b"\0" * 100))
        self.assertLess(len(lying), MB)
        started = time.time()
        outcome, peak = traced_peak(lambda: ki.read_table("a.xlsx", data=lying))
        self.assertLess(time.time() - started, SLOW)
        self.assert_refused(outcome, "import_wrong_kind")
        self.assertLess(peak, 2 * MB, "memory used while refusing: %d KB" % (peak // 1024))
        honest = one_part_zip("xl/workbook.xml", stream, zipfile.ZIP_DEFLATED, 64 * MB)       # says 64 MB: refused unread
        self.assert_refused(self.read(honest), "import_too_large")

    def test_only_the_two_ways_a_spreadsheet_is_packed_are_unpacked(self):
        """bzip2 and LZMA parts expand with no ceiling at all, and no spreadsheet program writes them."""
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            self.assert_refused(self.read(workbook([("s", sheet([["露营"]]))], method=method)), "import_wrong_kind", method)
        packed = bz2.compress(b"\0" * (64 * MB), 9)
        lying = one_part_zip("xl/workbook.xml", packed, zipfile.ZIP_BZIP2, 100, zlib.crc32(b"\0" * 100))
        outcome, peak = traced_peak(lambda: ki.read_table("a.xlsx", data=lying))
        self.assert_refused(outcome, "import_wrong_kind")
        self.assertLess(peak, 2 * MB)

    def test_a_sheet_that_declares_a_million_rows_is_read_up_to_the_limit(self):
        rows = "".join('<row r="%d"><c r="A%d" t="inlineStr"><is><t>词%d</t></is></c><c r="B%d"><v>%d</v></c></row>' % (n, n, n, n, n)
                       for n in range(1, 1000001, 7))
        data = workbook([("s", sheet(rows, before='<dimension ref="A1:B1000000"/>'))])
        table = self.read(data)
        self.assertEqual((len(table["rows"]), table["more_rows"], table["rows"][0], table["rows"][-1]),
                         (ki.MAX_ROWS, True, ["词1", "1"], ["词34994", "34994"]))
        status, out = self.quickly(self.preview, name="a.xlsx", file_base64=b64(data))
        self.assertEqual((status, out["rows_read"], out["would_import"], out["skipped"]["over_limit"], out["more_rows"]),
                         (200, ki.MAX_ROWS, trends.MAX_KEYWORDS_PER_PASTE, ki.MAX_ROWS - trends.MAX_KEYWORDS_PER_PASTE, True))
        # Rows numbered far apart take no room: a row is kept where it stands in the file, not at its number.
        sparse = workbook([("s", sheet('<row r="1"><c r="A1"><v>1</v></c></row><row r="1048576"><c r="A1048576"><v>2</v></c></row>'))])
        outcome, peak = traced_peak(lambda: ki.read_table("a.xlsx", data=sparse))
        self.assertEqual(outcome["rows"], [["1"], ["2"]])
        self.assertLess(peak, MB)

    def test_a_sheet_that_unpacks_to_more_than_is_read_is_cut_and_says_so(self):
        """Fewer rows than the row limit, but long cells: 22 MB of sheet, of which MAX_PART_BYTES are unpacked."""
        four_letters = bytes(b"abcd"[n % 4] for n in range(256))
        text = random.Random(5).randbytes(22 * MB).translate(four_letters).decode("ascii")     # packs to about a quarter: an honest ratio
        written = len(text) // 5000
        rows = "".join('<row><c t="inlineStr"><is><t>%s</t></is></c><c><v>%d</v></c></row>' % (text[n * 5000:(n + 1) * 5000], n)
                       for n in range(written))
        data = workbook([("s", sheet(rows))])
        self.assertLess(len(data), ki.MAX_FILE_BYTES)
        self.assertLess(written, ki.MAX_ROWS)                                    # so it is the size that cuts it, not the number of rows
        table = self.read(data)
        self.assertTrue(0 < len(table["rows"]) < written, len(table["rows"]))
        self.assertEqual(table["rows"][-1][1], str(len(table["rows"]) - 1))      # whole rows only: the one that was cut in two is not kept
        self.assertEqual(table["more_rows"], True)
        status, out = self.quickly(self.preview, name="a.xlsx", file_base64=b64(data), keyword_column=1)
        self.assertEqual((status, out["more_rows"], out["rows_read"]), (200, True, len(table["rows"])))

    def test_only_a_part_that_holds_more_than_was_unpacked_counts_as_cut(self):
        part = sheet([["露营", 5], ["早餐", 7], ["护肤", 9]])
        size = len(part.encode("utf-8"))
        with mock.patch.object(ki, "MAX_PART_BYTES", size):                      # it ends exactly at the limit: nothing was left out
            exact = ki.read_table("a.xlsx", data=workbook([("s", part)]))
        self.assertEqual((exact["rows"], exact["more_rows"]), ([["露营", "5"], ["早餐", "7"], ["护肤", "9"]], False))
        with mock.patch.object(ki, "MAX_PART_BYTES", part.encode("utf-8").rindex(b"<row") + 20):     # the limit falls inside the third row
            cut = ki.read_table("a.xlsx", data=workbook([("s", part)]))
        self.assertEqual((cut["rows"], cut["more_rows"]), ([["露营", "5"], ["早餐", "7"]], True))

    def test_a_row_of_a_million_cells_and_a_sheet_of_endless_markup_stop_at_a_budget(self):
        chooser = random.Random(7)
        cells = "".join("<c><v>%d</v></c>" % chooser.getrandbits(40) for _ in range(1000000))
        data = workbook([("s", sheet('<row><c t="inlineStr"><is><t>露营</t></is></c></row><row>%s</row>' % cells))])
        self.assertLess(len(data), ki.MAX_FILE_BYTES)
        table = self.read(data)
        self.assertEqual((table["rows"], table["more_rows"], table["more_columns"]), ([["露营"]], True, True))
        nested = workbook([("s", sheet("<row>" + "".join("<c><x%d/></c>" % chooser.getrandbits(30) for _ in range(900000)) + "</row>"))])
        self.assert_refused(self.read(nested), "import_not_a_table")             # nothing was read before the budget ran out

    def test_an_entity_bomb_is_refused_before_it_is_expanded(self):
        """A "billion laughs" file: nine entities, each ten of the one before. The parser of Python 3.9 would expand it."""
        plain = sheet([["露营"]], doctype="<!DOCTYPE worksheet>")
        self.assert_refused(self.read(workbook([("s", plain)])), "import_wrong_kind")      # no declaration at all is accepted: this is what stops the rest
        bombs = {
            "the sheet": workbook([("s", sheet('<row><c t="inlineStr"><is><t>&lol9;</t></is></c></row>', doctype=BOMB))]),
            "an attribute of the sheet": workbook([("s", sheet('<row r="&lol9;"><c><v>1</v></c></row>', doctype=BOMB))]),
            "the shared texts": workbook([("s", sheet('<row><c t="s"><v>0</v></c></row>'))],
                                         {"xl/sharedStrings.xml": '%s%s<sst xmlns="%s"><si><t>&lol9;</t></si></sst>' % (HEAD, BOMB, MAIN)}),
            "the workbook": workbook([("s", sheet([["露营"]]))],
                                     {"xl/workbook.xml": '%s%s<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="&lol9;" sheetId="1" r:id="rId1"/>'
                                                         '</sheets></workbook>' % (HEAD, BOMB, MAIN, LINKS)}),
            "the links": workbook([("s", sheet([["露营"]]))],
                                  {"xl/_rels/workbook.xml.rels": '%s%s<Relationships xmlns="p"><Relationship Id="&lol9;"/></Relationships>' % (HEAD, BOMB)}),
        }
        for label, data in bombs.items():
            self.assertLess(len(data), 4096, label)
            started = time.time()
            outcome, peak = traced_peak(lambda: ki.read_table("a.xlsx", data=data))
            self.assertLess(time.time() - started, SLOW, label)
            self.assert_refused(outcome, "import_wrong_kind", label)
            self.assertLess(peak, MB, label)
        status, body = self.quickly(self.preview, name="a.xlsx", file_base64=b64(bombs["the sheet"]))
        self.assertEqual((status, body["error"]), (415, "import_wrong_kind"))

    def test_an_entity_that_names_a_file_or_an_address_is_never_fetched(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("the creator's API key", encoding="utf-8")
        for target in (secret.as_uri(), "http://127.0.0.1:%d/api/state" % self.port):
            doctype = '<!DOCTYPE x [<!ENTITY outside SYSTEM "%s">]>' % target
            data = workbook([("s", sheet('<row><c t="inlineStr"><is><t>&outside;</t></is></c></row>', doctype=doctype))])
            self.assert_refused(self.read(data), "import_wrong_kind", target)
        # An entity nobody declared is damaged markup, not a server error.
        self.assert_refused(self.read(workbook([("s", sheet('<row><c t="inlineStr"><is><t>&nobody;</t></is></c></row>'))])), "import_wrong_kind")

    def test_a_very_long_cell_is_cut_and_the_rows_around_it_are_read(self):
        chooser = random.Random(3)
        block = "".join(chr(0x4e00 + chooser.randrange(20000)) for _ in range(20000))
        long = block * 100                  # two million characters that do not pack to almost nothing: that would be refused for its ratio
        files = {
            "written into the cell": workbook([("s", sheet([["关键词", "热度"], [long, 5], ["露营", 7]]))]),
            "a shared text": workbook([("s", sheet('<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row><row><c t="s"><v>2</v></c><c><v>5</v></c></row>'
                                                  '<row><c t="s"><v>3</v></c><c><v>7</v></c></row>'))],
                                      {"xl/sharedStrings.xml": shared(["关键词", "热度", long, "露营"])}),
            "a number of a million digits": workbook([("s", sheet('<row><c t="inlineStr"><is><t>关键词</t></is></c><c t="inlineStr"><is><t>热度</t></is></c></row>'
                                                                 '<row><c t="inlineStr"><is><t>%s</t></is></c><c><v>%s</v></c></row>'
                                                                 '<row><c t="inlineStr"><is><t>露营</t></is></c><c><v>7</v></c></row>' % ("长" * 300, "9" * 1000000)))]),
        }
        for label, data in files.items():
            started = time.time()
            outcome, peak = traced_peak(lambda: ki.preview({"name": "a.xlsx", "file_base64": b64(data)}))
            self.assertLess(time.time() - started, SLOW, label)
            self.assertNotIsInstance(outcome, Exception, label)
            self.assertEqual((outcome["rows_read"], outcome["would_import"], outcome["skipped"]["too_long"]), (3, 1, 1), label)
            self.assertEqual((len(outcome["rows"][0]["keyword"]), outcome["rows"][0]["skipped"]), (ki.MAX_CELL_CHARS + 1, "too_long"), label)
            self.assertEqual(outcome["rows"][1], {"keyword": "露营", "heat": 7, "skipped": None}, label)
            self.assertLess(len(json.dumps(outcome)), 20000, label)              # the answer does not carry the long cell back
            self.assertLess(peak, 40 * MB, "%s: %d MB" % (label, peak // MB))
        text = '关键词,热度\n"%s",5\n露营,7\n' % ("长" * 32767)                    # the longest cell Excel itself allows
        out = self.quickly(ki.preview, {"name": "a.csv", "text": text})
        self.assertEqual((out["would_import"], out["skipped"]["too_long"]), (1, 1))
        longer = '关键词,热度\n"%s",5\n露营,7\n' % ("长" * 200000)                 # no spreadsheet writes such a cell: refused, and the row named
        refusal = self.quickly(ki.preview, {"name": "a.csv", "text": longer})
        self.assert_refused(refusal, "import_not_a_table")
        self.assertIn("row 2", refusal.message)

    def test_a_line_of_200000_characters_is_cut_and_the_rows_around_it_are_read(self):
        long = "长" * 200000
        for label, name, text, expected in (
                ("a table", "a.csv", "关键词,热度\n%s,5\n露营,7\n" % long, [["关键词", "热度"], [ki._tidy(long)], ["露营", "7"]]),
                ("a table, the long cell second", "a.csv", "关键词,热度\n护肤,%s\n露营,7\n" % long, [["关键词", "热度"], ["护肤", ki._tidy(long)], ["露营", "7"]]),
                ("a list", "a.txt", "关键词\n%s\n露营\n" % long, [["关键词"], [ki._tidy(long)], ["露营"]]),
                ("spaces", "a.csv", "关键词,热度\n护肤%s,5\n露营,7\n" % (" " * 200000), [["关键词", "热度"], ["护肤"], ["露营", "7"]]),
                ("delimiters", "a.csv", "关键词,热度\n护肤,5%s\n露营,7\n" % ("," * 200000), [["关键词", "热度"], ["护肤", "5"], ["露营", "7"]])):
            table = self.quickly(ki.read_table, name, text=text)
            self.assertEqual(table["rows"], expected, label)
        status, out = self.quickly(self.preview, name="a.csv", text="关键词,热度\n%s,5\n露营,7\n" % long)
        self.assertEqual((status, out["would_import"], out["skipped"]["too_long"]), (200, 1, 1))

    def test_text_files_made_to_take_long_do_not(self):
        size = ki.MAX_FILE_BYTES
        hostile = {
            "one line as large as a file may be": "a" * size,
            "nothing but line ends": "\n" * size,
            "nothing but commas": "," * size,
            "nothing but quotation marks": '"' * size,
            "nothing but tabs and line ends": "\t\n" * (size // 2),
            "a quotation mark opened and never closed": '关键词,热度\n"' + "护肤,5\n" * (size // 8),
            "quotation marks opened on every line": '"a,1\n' * (size // 8),
            "digits": "9" * size,
            "a number on every line": "1,234\n" * (size // 8),
            "spaces between two words": "a" + " " * (size - 2) + "b",
            "every kind of delimiter": "\t;,，" * (size // 4),
            "units and signs": "万亿wk+" * (size // 8),
        }
        for label, text in hostile.items():
            for name in ("a.csv", "a.txt"):
                started = time.time()
                try:
                    out = ki.preview({"name": name, "text": text})
                    self.assertLessEqual(out["rows_read"], ki.MAX_ROWS, label)
                except ki.ImportRefused as refusal:
                    self.assertIn(refusal.code, ("import_not_a_table", "import_no_keyword_column"), label)
                self.assertLess(time.time() - started, SLOW, "%s as %s" % (label, name))
        table = self.quickly(ki.read_table, "a.csv", data=b"\xff" * size)        # and as bytes that are text in no encoding
        self.assertEqual([len(cell) for row in table["rows"] for cell in row], [ki.MAX_CELL_CHARS + 1])

    def test_a_workbook_with_more_texts_than_can_be_read_is_refused_and_a_long_list_is_not_read_past_what_is_needed(self):
        far = workbook([("s", sheet('<row><c t="s"><v>999999999</v></c></row>'))], {"xl/sharedStrings.xml": shared(["a"])})
        self.assert_refused(self.read(far), "import_too_large")
        just_over = workbook([("s", sheet('<row><c t="s"><v>%d</v></c></row>' % ki.MAX_SHARED_STRINGS))], {"xl/sharedStrings.xml": shared(["a"])})
        self.assert_refused(self.read(just_over), "import_too_large")
        texts = ["词%d" % n for n in range(300000)]
        first_only = workbook([("s", sheet('<row><c t="s"><v>0</v></c></row>'))], {"xl/sharedStrings.xml": shared(texts)})
        started = time.time()
        self.assertEqual(ki.read_table("a.xlsx", data=first_only)["rows"], [["词0"]])
        needed_one = time.time() - started
        last = workbook([("s", sheet('<row><c t="s"><v>%d</v></c></row>' % (ki.MAX_SHARED_STRINGS - 1)))], {"xl/sharedStrings.xml": shared(texts)})
        self.assertEqual(self.read(last)["rows"], [["词%d" % (ki.MAX_SHARED_STRINGS - 1)]])
        self.assertLess(needed_one, 0.5)
        endless = workbook([("s", sheet('<row><c t="s"><v>5</v></c></row>'))],
                           {"xl/sharedStrings.xml": "<sst><si>" + "".join("<r%d/>" % n for n in range(1200000)) + "</si></sst>"})
        self.assert_refused(self.read(endless), "import_too_large")

    def test_a_file_over_the_size_limit_is_refused_before_it_is_looked_at(self):
        with mock.patch.object(ki, "_read_xlsx", side_effect=AssertionError("opened")), \
                mock.patch.object(ki, "_read_text", side_effect=AssertionError("read")):
            self.assert_refused(self.read(b"PK\x03\x04" + b"\0" * ki.MAX_FILE_BYTES), "import_too_large")
            self.assert_refused(self.read(b"a" * (ki.MAX_FILE_BYTES + 1), "a.csv"), "import_too_large")
            self.assert_refused(self.quickly(ki.preview, {"name": "a.xlsx", "file_base64": "A" * (ki.MAX_FILE_BYTES * 4 // 3 + 8)}), "import_too_large")
        self.assertLess(ki.MAX_FILE_BYTES * 4 // 3 + 4096, server.MAX_BODY)      # a file at the limit fits into one request

    def test_whatever_is_wrong_with_a_workbook_it_is_a_refusal_not_a_server_error(self):
        good = workbook([("s", sheet([["关键词"], ["露营"]]))])
        chooser = random.Random(20261116)
        damaged = []
        for _ in range(60):
            data = bytearray(good)
            for _ in range(chooser.randint(1, 8)):
                data[chooser.randrange(4, len(data))] = chooser.randrange(256)
            damaged.append(bytes(data))
        damaged += [good[:n] for n in range(4, len(good), 97)]
        damaged += [workbook([("s", part)]) for part in ("", "<worksheet>", "<worksheet><sheetData><row><c>", "\x00\x01\x02", "<a/><b/>",
                                                         sheet('<row><c t="s"><v>-1</v></c><c t="s"><v>1e9</v></c><c r="!!"><v>x</v></c></row>'),
                                                         sheet("<row>" * 5000), sheet("<c><v>1</v></c>"), sheet('<row r="x"><c r="A1" t="?"><v/></c></row>'))]
        for data in damaged:
            status, body = self.preview(name="a.xlsx", file_base64=b64(data))
            self.assertIn(status, (200, 400, 413, 415), body)
            self.assertNotEqual(body.get("error"), "internal", body)
        self.assertEqual(self.tracked(), [])


# ---- the page ---------------------------------------------------------------------------------------------------------
class PageTest(ServerCase):
    """What the page states, and what it sends, has to fit what the server does."""

    def setUp(self):
        super().setUp()
        self.script = (WEB / "kwimport.js").read_text(encoding="utf-8")

    def test_the_size_the_page_refuses_at_is_the_servers(self):
        self.assertIn("const KI_MAX_FILE_BYTES = %d;" % ki.MAX_FILE_BYTES, self.script)
        self.assertIn("%d MB" % (ki.MAX_FILE_BYTES // MB), ki._too_large().message)
        self.assertEqual(ki.MAX_FILE_BYTES % MB, 0)                              # the page shows it as a whole number of MB

    def test_the_kinds_of_file_the_page_offers_are_the_ones_the_server_reads(self):
        offered = re.search(r'accept="([^"]+)"', self.script).group(1)
        self.assertEqual(offered.split(","), ["." + kind for kind in ki.TEXT_EXTENSIONS] + [".xlsx"])
        for kind in offered.split(","):
            self.assertIn(kind, ki._unsupported().message)
            self.assertIn(kind, ki._unsupported().message_zh)

    def test_every_text_of_the_page_is_there_in_both_languages(self):
        blocks = re.findall(r"Object\.assign\(I18N\.(zh|en), \{(.*?)\n\}\);", self.script, re.S)
        self.assertEqual([language for language, _ in blocks], ["zh", "en"])
        keys = {language: set(re.findall(r"\b(ki_[a-z_]+): '", body)) for language, body in blocks}
        self.assertGreater(len(keys["zh"]), 30)
        self.assertEqual(keys["zh"], {key for key in keys["en"] if not key.endswith("_one")})   # English alone has singular forms
        used = set(re.findall(r"""['"](ki_[a-z_]+)['"]""", self.script))
        prefixes = {"ki_skip_", "ki_why_"}                                       # completed with a reason the server gives
        self.assertEqual({key for key in used if key not in keys["zh"]} - prefixes, set())
        for reason in ("empty", "too_long", "duplicate", "over_limit"):
            self.assertIn("ki_skip_" + reason, keys["zh"])
            self.assertIn("ki_why_" + reason, keys["zh"])
        self.assertIn("ki_why_header", keys["zh"])
        zh = dict(re.findall(r"\b(ki_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[0][1]))
        en = dict(re.findall(r"\b(ki_[a-z_]+): '((?:[^'\\]|\\.)*)'", blocks[1][1]))
        self.assertEqual((zh["ki_pick"], en["ki_pick"]), ("从表格导入", "Import a spreadsheet"))
        self.assertIn("只在这台电脑上读取", zh["ki_hint"])
        self.assertIn("不会发到任何地方", zh["ki_hint"])
        self.assertIn("read on this computer", en["ki_hint"])
        self.assertIn("nothing is sent anywhere", en["ki_hint"])

    def test_the_script_is_served_and_loaded_between_discover_and_pages(self):
        index = (WEB / "index.html").read_text(encoding="utf-8")
        order = [index.index('src="/%s.js"' % name) for name in ("app", "setup", "discover", "kwimport", "pages")]
        self.assertEqual(order, sorted(order))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", "/kwimport.js")
        res = conn.getresponse()
        body = res.read()
        conn.close()
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, (WEB / "kwimport.js").read_bytes())
        pages = (WEB / "pages.js").read_text(encoding="utf-8")
        self.assertIn("${kiControlHtml()}", pages)
        self.assertEqual(pages.count("kiBind();"), 1)
        self.assertIsNone(re.search(r"\bki_[a-z]", pages))                       # the texts of the import live in its own file

    def test_the_file_goes_to_this_server_only_and_nothing_else_is_asked(self):
        asked = re.findall(r"api\('POST', '([^']+)'", self.script)
        self.assertEqual(sorted(set(asked)), ["/api/trends/import", "/api/trends/import/preview"])
        for word in ("fetch(", "XMLHttpRequest", "http://", "https://", "localStorage", "manualBridge", "aiRun"):
            self.assertNotIn(word, self.script, word)


if __name__ == "__main__":
    unittest.main()
