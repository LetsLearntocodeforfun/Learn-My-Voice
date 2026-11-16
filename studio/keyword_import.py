"""Keyword exports from analytics tools: reading a spreadsheet file, and finding the keywords in it.

Paid Xiaohongshu analytics tools (新红, 千瓜, 灰豚) let a creator save keyword lists as spreadsheets. This
module turns such a file (``.csv``, ``.tsv``, ``.txt`` or ``.xlsx``) into a table of cell strings, guesses
which column holds the keywords and which a number, and lists what importing it would store. Scoring stays
in ``trends``, and storing in ``store.upsert_trends``.

Nobody on this project has seen a real export from any of those tools. The header names below are therefore
guesses, and nothing depends on them: the page shows what was found, and the creator picks the two columns
herself before anything is stored.

A file somebody handed the creator is hostile input, so every reader here is bounded in time and memory:

- the file as it arrives is at most MAX_FILE_BYTES;
- at most MAX_ROWS rows and MAX_COLUMNS columns are read, and a cell is cut at MAX_CELL_CHARS;
- an ``.xlsx`` is a zip archive. It is never extracted to disk. Its parts are unpacked a piece at a time
  straight into the XML parser, at most MAX_PART_BYTES of any one part, and a part of more than
  RATIO_FLOOR_BYTES that claims to unpack to more than MAX_RATIO times its packed size is refused unread;
- the XML parser refuses a document type declaration, which is where entities are declared. No workbook has
  one, and the parser that Python 3.9 on a Mac comes with (expat 2.2.8) expands a "billion laughs" file as
  far as it is written: a file of 523 bytes came out as three million characters when this was measured;
- a formula is never evaluated: a cell shows the value the spreadsheet program saved with it;
- no pattern is used that can go quadratic. Numbers are read by hand, from the end of the cell.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import csv
import io
import math
import posixpath
import struct
import zipfile
import zlib
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple
from xml.parsers import expat

from . import trends
from .store import MAX_KEYWORD_CHARS, clean_file_name, clean_keyword, keyword_key

MAX_FILE_BYTES = 8 * 1024 * 1024        # the file as it arrives; must match KI_MAX_FILE_BYTES in studio/web/kwimport.js
MAX_PART_BYTES = 20 * 1024 * 1024       # the most that is ever unpacked from one part of an .xlsx
MAX_RATIO = 200                         # a part of an .xlsx may unpack to this many times its packed size
RATIO_FLOOR_BYTES = 1024 * 1024         # ... once it is this large: a small sheet of one repeated value packs better, honestly
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 200                    # a longer cell is cut and ends in "…", so it is MAX_CELL_CHARS + 1 long: that is how a cut cell is known
MAX_LINE_CHARS = 100000                 # one line of a text file; under the csv module's own limit for one cell (131,072)
MAX_SHARED_STRINGS = MAX_ROWS * MAX_COLUMNS     # texts of an .xlsx that the cells read can refer to
MAX_SHEET_ELEMENTS = 800000             # XML elements read from a worksheet: room for MAX_ROWS x MAX_COLUMNS cells with formulas
MAX_STRING_ELEMENTS = 4 * MAX_SHARED_STRINGS
MAX_SHEETS = 50                         # sheets of a workbook that are looked at and named
PREVIEW_ROWS = 20                       # rows the preview shows
HEADER_SCAN_ROWS = 30                   # a header row is looked for this far down
GUESS_ROWS = 200                        # rows the guess by content looks at
MAX_HEADER_CHARS = 24                   # a longer cell is a title or a sentence, not the name of a column
SAMPLE_CHARS = 30                       # a sample cell in the list of columns is cut to this

TEXT_EXTENSIONS = ("csv", "tsv", "txt")
# Guesses at what an export calls its columns, best guess first. A name counts when the header cell contains it.
KEYWORD_HEADERS = ("关键词", "热词", "搜索词", "话题", "词", "keyword", "query", "topic")
HEAT_HEADERS = ("热度", "搜索指数", "搜索量", "笔记数", "互动量", "声量", "指数", "heat", "volume", "index")

_DELIMITERS = "\t;,，"                  # in the order a tie is settled: the comma inside "1,234" must not beat the tab beside it
_SNIFF_CHARS, _SNIFF_LINES = 65536, 50  # how much of a text file the delimiter is worked out from
_END = "\x1e"                           # put after the last line of a text file; see _read_text
_UNIT_CHARS = "万亿wWkK"
_CUT = "…"
_RAW_CELL_CHARS = 32768                 # text of one .xlsx cell that is collected before it is tidied and cut
_CHUNK = 65536
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"       # how an old .xls starts, and an .xlsx saved with a password
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_UTF16_MARKS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_UNREADABLE = (zipfile.BadZipFile, KeyError, NotImplementedError, RuntimeError, zlib.error, EOFError, OSError, ValueError,
               struct.error, OverflowError, expat.ExpatError)
_MB = 1024 * 1024


class ImportRefused(Exception):
    """The file cannot be imported as a keyword table. Raised before anything is stored.

    ``code`` is what a page can map; ``message`` (English) and ``message_zh`` are written to be shown.
    """

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.message_zh, self.status = code, message, message_zh, status


class _Stop(Exception):
    """Raised inside a parser callback when enough has been read. The rest of the part is left packed."""


# ---- refusals ---------------------------------------------------------------
def _no_file() -> ImportRefused:
    return ImportRefused("import_no_file", "No file arrived with the request.", "请求里没有文件。")


def _too_large() -> ImportRefused:
    limit = MAX_FILE_BYTES // _MB
    return ImportRefused(
        "import_too_large",
        "This file is larger than the %d MB that can be imported. Remove the rows or sheets that are not needed, "
        "or save the sheet as .csv, and try again." % limit,
        "这个文件超过了 %d MB 的导入上限。请删掉不需要的行或工作表，或者把那张表另存为 .csv，再试一次。" % limit, 413)


def _unpacks_too_far() -> ImportRefused:
    return ImportRefused(
        "import_too_large",
        "This workbook unpacks to far more than a spreadsheet of its size does, so it was not read. "
        "Open it in Excel, WPS or Numbers and save the sheet with the keywords as .csv.",
        "这个工作簿解开以后比同样大小的表格大得多，所以没有读取。"
        "请用 Excel、WPS 或 Numbers 打开，把有关键词的那张表另存为 .csv。", 413)


def _too_many_texts() -> ImportRefused:
    return ImportRefused(
        "import_too_large",
        "This workbook holds more text than can be read here. Save the sheet with the keywords as .csv and import that.",
        "这个工作簿里的文字太多，这里读不完。请把有关键词的那张表另存为 .csv，再导入。", 413)


def _old_excel() -> ImportRefused:
    return ImportRefused(
        "import_old_excel",
        "This is an old Excel file (.xls), which cannot be read here. Open it in Excel, WPS or Numbers and "
        "save it as .xlsx or .csv.",
        "这是旧版 Excel 文件（.xls），这里读不了。请用 Excel、WPS 或 Numbers 打开，另存为 .xlsx 或 .csv。", 415)


def _unsupported() -> ImportRefused:
    return ImportRefused("import_unsupported", "Only .csv, .tsv, .txt and .xlsx files can be imported here.",
                         "这里只能导入 .csv、.tsv、.txt 和 .xlsx 文件。", 415)


def _not_text(extension: str, holds: str, holds_zh: str) -> ImportRefused:
    return ImportRefused(
        "import_wrong_kind",
        "The name of this file ends in .%s, but it holds %s. Open it in the program it came from and save it "
        "as .xlsx or .csv." % (extension, holds),
        "这个文件的名字以 .%s 结尾，里面却是%s。请用导出它的软件打开，另存为 .xlsx 或 .csv。" % (extension, holds_zh), 415)


def _not_a_workbook() -> ImportRefused:
    return ImportRefused(
        "import_wrong_kind",
        "The name of this file ends in .xlsx, but it could not be read as an Excel workbook. Open it in Excel, "
        "WPS or Numbers and save it again as .xlsx or .csv.",
        "这个文件的名字以 .xlsx 结尾，却没法当作 Excel 工作簿读取。请用 Excel、WPS 或 Numbers 打开，重新另存为 .xlsx 或 .csv。", 415)


def _locked_or_old() -> ImportRefused:
    return ImportRefused(
        "import_wrong_kind",
        "The name of this file ends in .xlsx, but it is an old Excel file (.xls) or a workbook saved with a "
        "password. Open it in Excel and save it as .xlsx without a password, or as .csv.",
        "这个文件的名字以 .xlsx 结尾，里面却是旧版 Excel 文件（.xls），或者是加了密码的工作簿。"
        "请用 Excel 打开，另存为不带密码的 .xlsx，或者另存为 .csv。", 415)


def _not_a_table() -> ImportRefused:
    return ImportRefused(
        "import_not_a_table",
        "Nothing to read was found: the file, or the first sheet of the workbook, is empty.",
        "没有读到内容：这个文件（或者工作簿的第一张表）是空的。")


def _open_quote(row: int) -> ImportRefused:
    return ImportRefused(
        "import_not_a_table",
        "Around row %d a quotation mark is never closed, or one cell is longer than a spreadsheet allows, so "
        "the rows after it cannot be told apart. Save the file again from the spreadsheet program." % row,
        "在第 %d 行附近，有一个引号一直没有结束，或者有一格长得超出了表格的限度，后面的行分不开了。"
        "请从表格软件里重新导出这个文件。" % row)


def _no_keyword_column() -> ImportRefused:
    return ImportRefused(
        "import_no_keyword_column",
        "No column of this table looks like keywords: none holds text of %d characters or fewer." % MAX_KEYWORD_CHARS,
        "这张表里没有哪一列像关键词：没有一列是 %d 个字符以内的文字。" % MAX_KEYWORD_CHARS)


def _no_column_chosen() -> ImportRefused:
    return ImportRefused("import_no_keyword_column", "Choose the column that holds the keywords before importing.",
                         "导入之前，请先选关键词在哪一列。")


def _bad_column() -> ImportRefused:
    return ImportRefused("import_bad_column", "That column is not in this table. Choose the columns again.",
                         "表格里没有这一列，请重新选择。")


def _no_keywords() -> ImportRefused:
    return ImportRefused("import_no_keywords", "No keyword could be read from the chosen column.",
                         "选的这一列里没有读到关键词。")


# ---- cells and numbers ------------------------------------------------------
def _tidy(text: str) -> str:
    """A cell as it is kept: one line, single spaces, cut after MAX_CELL_CHARS with an ellipsis."""
    text = " ".join(text.split())
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + _CUT


def _cell(row: Sequence[str], column: Optional[int]) -> str:
    return row[column] if column is not None and column < len(row) else ""


def _keep(rows: List[List[str]], cells: List[str]) -> None:
    """Add a row to the table without the empty cells at its end; a row with nothing in it is left out."""
    while cells and not cells[-1]:
        cells.pop()
    if cells:
        rows.append(cells)


def _number(cell: str) -> Optional[Tuple[str, Optional[str]]]:
    """``(number, unit)`` when the whole cell is a count as the paste box reads one, else None.

    The same forms as a number at the end of a pasted line (``trends._TRAILING_HEAT_REVERSED``): digits with
    thousands commas and at most one decimal point, then an optional unit (万, 亿, w, k) and an optional "+".
    It is read by hand from the end of the cell, so the work grows with the length of the cell and no more.
    """
    text = cell.strip()
    if text.endswith("+"):
        text = text[:-1]
    unit = None
    if text and text[-1] in _UNIT_CHARS:
        unit, text = text[-1], text[:-1]
    text = text.rstrip()
    whole, dot, fraction = text.partition(".")
    if not whole or not whole[0].isdecimal() or not whole.replace(",", "").isdecimal():
        return None
    if dot and not fraction.isdecimal():
        return None
    return text, unit


def read_heat(cell: str) -> Optional[int]:
    """The heat number in a cell (``12.5万``, ``1,234``, ``3k``, ``2亿+``), by the rules of the paste box; None for anything else.

    A number that rounds to 0 is no heat either: the store keeps none for it, so the preview must not show one.
    """
    parts = _number(cell)
    return (trends._heat(parts[0], parts[1]) or None) if parts else None    # one definition of the units and of the largest number kept


# ---- text files -------------------------------------------------------------
def _decode(data: bytes) -> str:
    """The text of a file: UTF-8 with or without a byte-order mark, UTF-16 when it starts with that mark, else GB18030.

    GB18030 is what a Chinese Windows program writes when it is not asked for anything else. UTF-16 is what
    Excel calls "Unicode text". A byte that fits neither becomes the replacement character and is shown.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8", "replace")
    if data.startswith(_UTF16_MARKS):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", "replace")


def _delimiter(text: str) -> str:
    """What separates the cells of a text file: a tab, a semicolon, a comma or a full-width comma; "" when nothing does.

    Worked out from the first lines: the candidate found on the most of them wins, counting only what stands
    outside quotation marks. It has to be on two lines at least, and on half of the lines from the first one
    that holds it: a title and a date range may stand above a table, but a list of keywords of which a few
    hold a comma is not a table. Such a file has one cell on each line, and the comma stays part of the
    keyword.
    """
    lines = text[:_SNIFF_CHARS].splitlines()
    if len(text) > _SNIFF_CHARS and len(lines) > 1:
        lines.pop()                             # the last of them was cut in the middle
    lines = [line for line in lines if line.strip()][:_SNIFF_LINES]
    best, best_found = "", 0
    for candidate in _DELIMITERS:
        holds = [any(candidate in part for part in line.split('"')[::2]) for line in lines]
        found = sum(holds)
        if found > best_found and (found >= 2 or len(lines) == 1) and found * 2 >= len(lines) - holds.index(True):
            best, best_found = candidate, found
    return best


def _lines(text: str) -> Iterator[str]:
    """The lines of a text file for the csv reader, none longer than MAX_LINE_CHARS, and the end mark after the last."""
    for line in io.StringIO(text, newline=""):
        yield line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "\n"
    yield _END


def _one_cell(line: str) -> str:
    """A line of a file with one cell on each line. Quotation marks around the whole line are taken off, as a program that quotes every cell writes them."""
    cell = line[:MAX_LINE_CHARS].strip()
    if len(cell) >= 2 and cell[0] == '"' and cell[-1] == '"':
        cell = cell[1:-1].replace('""', '"')
    return _tidy(cell)


def _read_text(text: str) -> Dict[str, Any]:
    """The table in a text file. At most MAX_ROWS rows are read, blank ones among them included.

    With a delimiter the standard csv module reads it, so a cell in quotation marks may hold the delimiter
    and line breaks. That module has no way to say that the file ended inside such a cell: it hands back
    the rest of the file as one cell. So a line holding only an end mark is put after the last line. When
    that mark does not come back as a row of its own, a quotation mark was never closed, and the file is
    refused instead of being imported with its last rows folded into one cell.

    Without a delimiter each line is one cell, and a quotation mark inside it means nothing.
    """
    # Two characters are taken out before anything is read: NUL, at which the csv module of Python 3.9 stops, and
    # the one used as the end mark, so that no line of the file can be taken for that mark and end the reading early.
    text = text.replace("\x00", "").replace(_END, "")
    if text.startswith("\ufeff"):
        text = text[1:]
    delimiter = _delimiter(text)
    rows: List[List[str]] = []
    more_rows = more_columns = False
    scanned = 0
    if not delimiter:
        for line in io.StringIO(text, newline=""):
            if scanned == MAX_ROWS:
                more_rows = True
                break
            scanned += 1
            _keep(rows, [_one_cell(line)])
    else:
        closed, failed_on = False, 0
        try:
            for row in csv.reader(_lines(text), delimiter=delimiter, skipinitialspace=True):
                if row == [_END]:
                    closed = True
                    break
                if scanned == MAX_ROWS:
                    more_rows = closed = True
                    break
                scanned += 1
                more_columns = more_columns or any(cell.strip() for cell in row[MAX_COLUMNS:])
                _keep(rows, [_tidy(cell) for cell in row[:MAX_COLUMNS]])
            failed_on = scanned                 # the mark came back inside the last row
        except csv.Error:
            failed_on = scanned + 1             # a cell of the next row passed the csv module's limit
        if not closed:
            raise _open_quote(max(failed_on, 1))
    return {"kind": "text", "delimiter": delimiter, "sheet": "", "other_sheets": [], "rows": rows,
            "more_rows": more_rows, "more_columns": more_columns}


# ---- .xlsx files ------------------------------------------------------------
def _local(name: str) -> str:
    """The name of an element or attribute without its namespace."""
    return name.rpartition(" ")[2]


def _no_doctype(*_args: Any) -> None:
    raise _not_a_workbook()


def _column_of(reference: str, fallback: int) -> int:
    """The column a cell reference such as ``AB12`` names, counted from 0; ``fallback`` when it names none."""
    column = 0
    for ch in reference[:4].upper():            # a real reference has at most three letters
        if not "A" <= ch <= "Z":
            break
        column = column * 26 + ord(ch) - 64
    return column - 1 if column else fallback


def _number_text(raw: str) -> str:
    """A number cell as text: ``125000.0`` and ``1.25E5`` both read ``125000``, and ``0.30000000000000004`` reads ``0.3``."""
    raw = raw.strip()
    try:
        value = float(raw)
    except ValueError:
        return _tidy(raw)
    if not math.isfinite(value):
        return ""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return "%.10g" % value


def _parse_part(archive: zipfile.ZipFile, name: str, reader: Any) -> bool:
    """Run one XML part of a workbook through ``reader``. True when all of it was read.

    False when the reader had enough (it raised ``_Stop``) or MAX_PART_BYTES of the part had been unpacked.
    What the reader holds by then is good: the part is parsed as it is unpacked, a piece at a time, and never
    held whole. Only the two ways a spreadsheet program packs a part are read (stored and deflated); the
    other methods a zip may name unpack without any ceiling. An archive says of itself how large each part
    is; ``zipfile`` hands out no more than that, so a part that claims a small size and holds more ends in
    a checksum error. One that claims to unpack to more than MAX_RATIO times its packed size is refused
    here, once it is over RATIO_FLOOR_BYTES: below that, a sheet of one repeated value packs as well.
    """
    info = archive.getinfo(name)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise _not_a_workbook()
    if info.file_size > RATIO_FLOOR_BYTES and info.file_size > MAX_RATIO * info.compress_size:
        raise _unpacks_too_far()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _no_doctype        # called before any entity it declares is read
    parser.StartElementHandler = reader.start
    parser.EndElementHandler = reader.end
    parser.CharacterDataHandler = reader.text
    unpacked = 0
    try:
        with archive.open(info) as part:
            while unpacked < MAX_PART_BYTES:
                chunk = part.read(min(_CHUNK, MAX_PART_BYTES - unpacked))
                if not chunk:
                    parser.Parse(b"", True)
                    return True
                unpacked += len(chunk)
                parser.Parse(chunk, False)
            if not part.read(1):                # it ended exactly at the limit: nothing was left out
                parser.Parse(b"", True)
                return True
    except _Stop:
        pass
    return False


class _WorkbookReader:
    """The sheets a workbook lists, in the order of their tabs, as ``(name, relationship id)``."""

    def __init__(self) -> None:
        self.sheets: List[Tuple[str, str]] = []

    def start(self, name: str, attrs: Dict[str, str]) -> None:
        if _local(name) != "sheet":
            return
        link = next((value for key, value in attrs.items() if _local(key) == "id"), "")
        self.sheets.append((_tidy(attrs.get("name", "")), link))
        if len(self.sheets) >= MAX_SHEETS:
            raise _Stop()

    def end(self, name: str) -> None:
        pass

    def text(self, data: str) -> None:
        pass


class _LinksReader:
    """Where the parts of a workbook are: relationship id to ``(kind, path as written)``. Links out of the file are left out."""

    def __init__(self) -> None:
        self.links: Dict[str, Tuple[str, str]] = {}

    def start(self, name: str, attrs: Dict[str, str]) -> None:
        if _local(name) == "Relationship" and attrs.get("TargetMode") != "External":
            self.links[attrs.get("Id", "")] = (attrs.get("Type", ""), attrs.get("Target", ""))
            if len(self.links) >= 4 * MAX_SHEETS:
                raise _Stop()

    def end(self, name: str) -> None:
        pass

    def text(self, data: str) -> None:
        pass


class _SheetReader:
    """The cells of one worksheet as text, row by row: at most MAX_ROWS rows and MAX_COLUMNS columns.

    Read: numbers, texts written into the cell, true and false, and for a formula the value saved with it
    (the formula itself is skipped). A cell of the kind "shared string" holds only a position in the
    workbook's list of texts; those are noted in ``refs`` as ``(row, column, position)`` and filled in once
    that list has been read. A cell holding an error has no value.
    """

    def __init__(self) -> None:
        self.rows: List[List[str]] = []
        self.refs: List[Tuple[int, int, int]] = []
        self.more_rows = self.more_columns = False
        self._rows_seen = 0
        self._budget = MAX_SHEET_ELEMENTS
        self._row: Optional[List[str]] = None
        self._row_refs: List[Tuple[int, int]] = []
        self._column = -1               # of the cell being read; -1 outside a cell, -2 in one past MAX_COLUMNS
        self._next_column = 0
        self._kind = ""
        self._grab = False              # inside <v>, or inside the <t> of a text written into the cell
        self._inline = False            # inside <is>
        self._phonetic = 0              # inside <rPh>: how a text is pronounced, not the text
        self._parts: List[str] = []
        self._size = 0

    def start(self, name: str, attrs: Dict[str, str]) -> None:
        self._budget -= 1
        if self._budget < 0:
            self.more_rows = True
            raise _Stop()
        tag = _local(name)
        if tag == "c":
            if self._row is None:
                return
            column = _column_of(attrs.get("r", ""), self._next_column)
            self._next_column = column + 1
            self._column = column if column < MAX_COLUMNS else -2
            self._kind = attrs.get("t", "")
            self._grab, self._parts, self._size = False, [], 0
        elif tag == "v":
            self._grab = self._column >= 0
            self.more_columns = self.more_columns or self._column == -2
        elif tag == "t":
            self._grab = self._column >= 0 and self._inline and not self._phonetic
        elif tag == "is":
            self._inline = True
            self.more_columns = self.more_columns or self._column == -2
        elif tag == "rPh":
            self._phonetic += 1
        elif tag == "row":
            if self._rows_seen >= MAX_ROWS:
                self.more_rows = True
                raise _Stop()
            self._rows_seen += 1
            self._row, self._row_refs, self._next_column, self._column = [], [], 0, -1

    def text(self, data: str) -> None:
        if self._grab and self._size < _RAW_CELL_CHARS:
            self._parts.append(data)
            self._size += len(data)

    def end(self, name: str) -> None:
        tag = _local(name)
        if tag in ("v", "t"):
            self._grab = False
        elif tag == "is":
            self._inline = False
        elif tag == "rPh":
            self._phonetic = max(0, self._phonetic - 1)
        elif tag == "c":
            if self._column >= 0 and self._row is not None:
                self._store(self._row, self._column, "".join(self._parts))
            self._column, self._parts = -1, []
        elif tag == "row" and self._row is not None:
            index = len(self.rows)
            self.rows.append(self._row)
            self.refs.extend((index, column, position) for column, position in self._row_refs)
            self._row = None

    def _store(self, row: List[str], column: int, raw: str) -> None:
        kind, value = self._kind, ""
        if kind == "s":
            position = raw.strip()
            if position.isascii() and position.isdigit() and len(position) <= 9:
                self._row_refs.append((column, int(position)))
        elif kind in ("inlineStr", "str", "d"):
            value = _tidy(raw)
        elif kind == "b":
            value = {"1": "TRUE", "0": "FALSE"}.get(raw.strip(), "")
        elif kind != "e":
            value = _number_text(raw)
        if len(row) <= column:
            row.extend([""] * (column + 1 - len(row)))
        row[column] = value


class _StringsReader:
    """The first ``wanted`` texts of a workbook's shared list, each tidied and cut like a cell."""

    def __init__(self, wanted: int) -> None:
        self.strings: List[str] = []
        self.complete = False           # every text that was wanted has been read
        self._wanted = wanted
        self._budget = MAX_STRING_ELEMENTS
        self._inside = self._grab = False
        self._phonetic = 0
        self._parts: List[str] = []
        self._size = 0

    def start(self, name: str, attrs: Dict[str, str]) -> None:
        self._budget -= 1
        if self._budget < 0:
            raise _Stop()
        tag = _local(name)
        if tag == "si":
            self._inside, self._grab, self._parts, self._size = True, False, [], 0
        elif tag == "t":
            self._grab = self._inside and not self._phonetic
        elif tag == "rPh":
            self._phonetic += 1

    def text(self, data: str) -> None:
        if self._grab and self._size < _RAW_CELL_CHARS:
            self._parts.append(data)
            self._size += len(data)

    def end(self, name: str) -> None:
        tag = _local(name)
        if tag == "t":
            self._grab = False
        elif tag == "rPh":
            self._phonetic = max(0, self._phonetic - 1)
        elif tag == "si" and self._inside:
            self.strings.append(_tidy("".join(self._parts)))
            self._inside = False
            if len(self.strings) >= self._wanted:
                self.complete = True
                raise _Stop()


def _part_path(target: str) -> str:
    """The name inside the archive that a link from the workbook points to."""
    target = target.replace("\\", "/")
    return posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))


def _read_workbook(archive: zipfile.ZipFile) -> Dict[str, Any]:
    """The first worksheet of an open workbook as a table. Only parts the archive lists are opened, by name, in memory."""
    names = set(archive.namelist())
    book, links = _WorkbookReader(), _LinksReader()
    _parse_part(archive, "xl/workbook.xml", book)
    if "xl/_rels/workbook.xml.rels" in names:
        _parse_part(archive, "xl/_rels/workbook.xml.rels", links)
    # The first sheet in the order of the tabs that is a worksheet (a sheet may also be a chart) and is in the file.
    chosen = None
    for position, (_title, link) in enumerate(book.sheets):
        kind, target = links.links.get(link, ("", ""))
        if kind.endswith("/worksheet") and _part_path(target) in names:
            chosen = (position, _part_path(target))
            break
    if chosen is None and "xl/worksheets/sheet1.xml" in names:      # a workbook whose links cannot be followed
        chosen = (0, "xl/worksheets/sheet1.xml")
    if chosen is None:
        raise _not_a_workbook()
    position, path = chosen
    titles = [title for title, _link in book.sheets]

    sheet = _SheetReader()
    # Not read whole: the row or element limit was reached, or the sheet unpacks to more than MAX_PART_BYTES.
    # Either way rows were left out, and the table has to say so.
    cut = not _parse_part(archive, path, sheet)
    rows = sheet.rows
    if sheet.refs:
        wanted = max(ref[2] for ref in sheet.refs) + 1
        if wanted > MAX_SHARED_STRINGS:
            raise _too_many_texts()
        strings_path = next((_part_path(target) for kind, target in links.links.values() if kind.endswith("/sharedStrings")),
                            "xl/sharedStrings.xml")
        strings = _StringsReader(wanted)
        if strings_path in names and not _parse_part(archive, strings_path, strings) and not strings.complete:
            raise _too_many_texts()             # the list was not read as far as the cells refer to
        found = strings.strings
        for row, column, at in sheet.refs:
            if at < len(found):
                rows[row][column] = found[at]
    kept: List[List[str]] = []
    for cells in rows:
        _keep(kept, cells)
    return {"kind": "xlsx", "delimiter": "", "sheet": titles[position] if position < len(titles) else "",
            "other_sheets": [title for at, title in enumerate(titles) if at != position], "rows": kept,
            "more_rows": sheet.more_rows or cut, "more_columns": sheet.more_columns}


def _read_xlsx(data: bytes) -> Dict[str, Any]:
    """The first worksheet of an .xlsx as a table, with the names of the other sheets.

    Whatever is wrong with the archive or with the XML in it (an unknown way of packing, a password, a
    damaged stream, a document type declaration, markup that does not close) makes it a file that cannot be
    read as a workbook, not a server error.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _read_workbook(archive)
    except _UNREADABLE:
        raise _not_a_workbook()


# ---- a file as a table ------------------------------------------------------
def read_table(name: Any, text: Any = None, data: Optional[bytes] = None) -> Dict[str, Any]:
    """The file as a table, or ``ImportRefused``.

    ``name`` says what kind of file it is, by its ending; ``data`` is the file as bytes, or ``text`` its
    contents when the sender has already decoded a text file. Returns ``rows`` (lists of cell strings; rows
    with nothing in them are left out, and so are empty cells at the end of a row), ``width`` (the cells of
    the longest row), ``kind`` (``text`` or ``xlsx``), ``delimiter``, ``sheet`` and ``other_sheets`` for a
    workbook, and ``more_rows`` and ``more_columns``: whether the file holds more than was read.

    A file is refused when it is not what its name says: a workbook or another program's file named like a
    text file, and something that is no workbook named ``.xlsx``.
    """
    label = clean_file_name(name)
    extension = label.rsplit(".", 1)[-1].lower() if "." in label else ""
    if data is None and not isinstance(text, str):
        raise _no_file()
    if extension == "xls":
        raise _old_excel()
    if extension == "xlsx":
        if data is None:
            raise _not_a_workbook()
        if len(data) > MAX_FILE_BYTES:
            raise _too_large()
        if data.startswith(_OLE_MAGIC):
            raise _locked_or_old()
        if not data.startswith(_ZIP_MAGICS):
            raise _not_a_workbook()
        table = _read_xlsx(data)
    elif extension in TEXT_EXTENSIONS or (not extension and data is None):
        if data is not None:
            if len(data) > MAX_FILE_BYTES:
                raise _too_large()
            if data.startswith(_ZIP_MAGICS):
                raise _not_text(extension, "an Excel workbook or another packed file", " Excel 工作簿或别的压缩文件")
            if data.startswith(_OLE_MAGIC):
                raise _not_text(extension, "an old Excel file (.xls)", "旧版 Excel 文件（.xls）")
            if b"\x00" in data and not data.startswith(_UTF16_MARKS):
                raise _not_text(extension, "something that is not text", "不是文字的内容")
            text = _decode(data)
        elif len(text) > MAX_FILE_BYTES:
            raise _too_large()
        table = _read_text(text)
    else:
        raise _unsupported()
    if not table["rows"]:
        raise _not_a_table()
    table["width"] = max(len(row) for row in table["rows"])
    return table


# ---- which row is the header, which columns hold what -----------------------
def _match(header: str, names: Sequence[str]) -> Optional[Tuple[int, int]]:
    """How well a header cell fits a list of column names: ``(place of the name in the list, 0 when it is the whole cell else 1)``.

    None when the cell contains none of them or is too long to be the name of a column. Smaller is better.
    """
    text = header.replace(" ", "").lower()
    if not text or len(text) > MAX_HEADER_CHARS:
        return None
    for place, name in enumerate(names):
        if name in text:
            return place, 0 if name == text else 1
    return None


def _header_row(rows: List[List[str]]) -> Optional[int]:
    """The row that names the columns, or None when no row does.

    An export often starts with a title or a date range, so the first HEADER_SCAN_ROWS rows are looked at.
    A header row holds no number, and either two of its cells fit a known column name, or one cell is
    exactly such a name. Of the rows that qualify, the one with the most fitting cells is taken. The rule is
    strict on purpose: a keyword list with no header must not lose the rows above a keyword that happens to
    contain 词 or 话题. What a wrong guess costs is small either way, and shown: a header taken for a keyword
    is one row in the preview, and rows above a header are counted as left out.
    """
    best, best_score = None, 0
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if any(_number(cell) is not None for cell in row):
            continue
        fits = [fit for fit in (_match(cell, KEYWORD_HEADERS) or _match(cell, HEAT_HEADERS) for cell in row) if fit]
        exact = sum(1 for _place, loose in fits if not loose)
        if (len(fits) >= 2 or exact) and len(fits) + exact > best_score:
            best, best_score = index, len(fits) + exact
    return best


def _named(headers: Sequence[str], names: Sequence[str], exclude: Set[int]) -> Optional[int]:
    """The column whose header fits ``names`` best: the earliest name in the list, then a whole-cell fit, then the shorter header."""
    fits = [(fit, len(header), column) for column, header in enumerate(headers)
            for fit in (_match(header, names),) if fit is not None and column not in exclude]
    return min(fits)[2] if fits else None


def _keyword_by_content(rows: List[List[str]], width: int, exclude: Set[int]) -> Optional[int]:
    """The column with the most short text cells; of equals, the one with the most different texts, then the leftmost.

    A column of categories or of "up" and "down" has as many short texts as the keywords beside it, but
    they repeat.
    """
    best, best_score = None, (0, 0)
    for column in range(width):
        if column in exclude:
            continue
        texts = [cell for cell in (_cell(row, column) for row in rows)
                 if cell and len(cell) <= MAX_KEYWORD_CHARS and _number(cell) is None]
        score = (len(texts), len(set(texts)))
        if score > best_score:
            best, best_score = column, score
    return best


def _counts_up(values: List[int]) -> bool:
    """Whether a column is a running count (1, 2, 3, ... or from 0): a rank or a row number, not a measure of anything."""
    return len(values) >= 3 and values[0] in (0, 1) and all(b - a == 1 for a, b in zip(values, values[1:]))


def _heat_by_content(rows: List[List[str]], width: int, keyword_column: Optional[int]) -> Optional[int]:
    """The column with the most numbers, when at least half of the rows have one there.

    A running count is passed over. Of equals, a column to the right of the keywords comes first (a rank
    stands before the keyword, what was measured after it), then the leftmost.
    """
    best, best_score = None, (0, 0)
    for column in range(width):
        if column == keyword_column:
            continue
        numbers = [_number(_cell(row, column)) for row in rows]
        count = sum(1 for parts in numbers if parts)
        if not count or count * 2 < len(rows):
            continue
        plain = [int(parts[0]) for parts in numbers
                 if parts and parts[1] is None and parts[0].isascii() and parts[0].isdigit() and len(parts[0]) < 12]
        if len(plain) == count and _counts_up(plain):
            continue
        score = (count, 1 if keyword_column is not None and column > keyword_column else 0)
        if score > best_score:
            best, best_score = column, score
    return best


def detect(rows: List[List[str]]) -> Dict[str, Optional[int]]:
    """Where the header row is, and which columns look like the keywords and like their number.

    ``header_row`` is None when no row names the columns: every row is then data. With a header, the
    keyword column is the one whose name fits KEYWORD_HEADERS best and the number column the one that fits
    HEAT_HEADERS best; a header that fits both ("关键词热度") names a number. Where no name fits, the
    contents decide. ``heat_column`` may be None; ``keyword_column`` is None only when no column holds a
    short text at all.
    """
    header_row = _header_row(rows)
    data = (rows if header_row is None else rows[header_row + 1:])[:GUESS_ROWS]
    width = max((len(row) for row in rows), default=0)
    headers = [_cell(rows[header_row], column) for column in range(width)] if header_row is not None else [""] * width
    heat_named = {column for column in range(width) if _match(headers[column], HEAT_HEADERS) is not None}
    keyword = _named(headers, KEYWORD_HEADERS, heat_named)
    if keyword is None:
        keyword = _keyword_by_content(data, width, heat_named)
    if keyword is None:
        keyword = _keyword_by_content(data, width, set())
    heat = _named(headers, HEAT_HEADERS, set() if keyword is None else {keyword})
    if heat is None:
        heat = _heat_by_content(data, width, keyword)
    return {"header_row": header_row, "keyword_column": keyword, "heat_column": heat}


# ---- what an import would store ---------------------------------------------
def plan(rows: List[List[str]], header_row: Optional[int], keyword_column: int,
         heat_column: Optional[int]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """``(keywords to store, the first rows as they would be read, how many rows are left out and why)``.

    Every row is accounted for: it becomes a keyword, or it is counted under one reason. ``header`` is the
    header row and what stands above it; ``empty`` a row with no keyword; ``too_long`` a keyword over
    MAX_KEYWORD_CHARS; ``duplicate`` a keyword an earlier row already gave; ``over_limit`` a new keyword
    after the first ``trends.MAX_KEYWORDS_PER_PASTE``, in the order of the file.

    Of two rows with the same keyword the first is kept, since exports list the strongest first; the later
    one only gives its number when the first had none.
    """
    first = 0 if header_row is None else header_row + 1
    counts = {"header": min(first, len(rows)), "empty": 0, "too_long": 0, "duplicate": 0, "over_limit": 0}
    items: Dict[str, Dict[str, Any]] = {}
    seen: List[Tuple[str, str, Optional[int], Optional[str]]] = []
    for row in rows[first:]:
        cell = _cell(row, keyword_column)
        keyword = clean_keyword(cell)
        key = keyword_key(keyword)
        heat = read_heat(_cell(row, heat_column)) if heat_column is not None else None
        reason = None
        if not key:
            reason = "empty"
        elif len(cell) > MAX_CELL_CHARS or len(keyword) > MAX_KEYWORD_CHARS:
            reason = "too_long"
        elif key in items:
            reason = "duplicate"
            if items[key]["heat"] is None:
                items[key]["heat"] = heat
        elif len(items) >= trends.MAX_KEYWORDS_PER_PASTE:
            reason = "over_limit"
        else:
            items[key] = {"keyword": keyword, "heat": heat}
        if reason:
            counts[reason] += 1
        if len(seen) < PREVIEW_ROWS:
            seen.append((key, keyword, heat, reason))
    shown = [{"keyword": keyword, "heat": heat if reason else items[key]["heat"], "skipped": reason}
             for key, keyword, heat, reason in seen]
    return list(items.values()), shown, counts


def _column(value: Any, width: int) -> int:
    """A column the request names, checked against the table."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < width:
        raise _bad_column()
    return value


def _request_table(body: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The file a request carries (``name`` and ``text`` or ``file_base64``) as ``(its name as a label, its table)``."""
    encoded, text, data = body.get("file_base64"), body.get("text"), None
    if isinstance(encoded, str) and encoded:
        if len(encoded) > MAX_FILE_BYTES * 4 // 3 + 4:
            raise _too_large()
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise _no_file()
    elif not isinstance(text, str):
        raise _no_file()
    return clean_file_name(body.get("name")), read_table(body.get("name"), text=text, data=data)


def _sample(cell: str) -> str:
    return cell if len(cell) <= SAMPLE_CHARS else cell[:SAMPLE_CHARS] + _CUT


def preview(body: Dict[str, Any]) -> Dict[str, Any]:
    """What ``POST /api/trends/import/preview`` answers. Nothing is stored.

    ``body`` holds the file and, once the creator has picked, ``keyword_column`` and ``heat_column``
    (a column counted from 0; ``heat_column`` may be null for "no number"). A column the request does not
    name is guessed. The answer says what was found (``header_row``, ``headers``, ``columns`` with two sample
    cells each, ``guessed``), which columns it was read with, the first PREVIEW_ROWS rows as
    ``{keyword, heat, skipped}``, and the counts: ``rows_read`` is ``would_import`` plus everything in
    ``skipped``.
    """
    name, table = _request_table(body)
    rows, width = table["rows"], table["width"]
    found = detect(rows)
    header_row = found["header_row"]
    keyword_column = found["keyword_column"]
    if body.get("keyword_column") is not None:
        keyword_column = _column(body["keyword_column"], width)
    if keyword_column is None:
        raise _no_keyword_column()
    heat_column = found["heat_column"]
    if "heat_column" in body:
        heat_column = None if body["heat_column"] is None else _column(body["heat_column"], width)
        if heat_column == keyword_column:
            raise _bad_column()
    elif heat_column == keyword_column:
        heat_column = None                      # she moved the keywords onto the column that was guessed for the number
    items, shown, counts = plan(rows, header_row, keyword_column, heat_column)
    data = (rows if header_row is None else rows[header_row + 1:])[:GUESS_ROWS]
    headers = [_cell(rows[header_row], column) for column in range(width)] if header_row is not None else []
    columns = [{"index": column, "header": headers[column] if headers else "",
                "samples": [_sample(cell) for cell in (_cell(row, column) for row in data) if cell][:2]}
               for column in range(width)]
    return {
        "name": name, "kind": table["kind"], "delimiter": table["delimiter"], "sheet": table["sheet"],
        "other_sheets": table["other_sheets"], "header_row": header_row, "headers": headers, "columns": columns,
        "keyword_column": keyword_column, "heat_column": heat_column,
        "guessed": {"keyword_column": found["keyword_column"], "heat_column": found["heat_column"]},
        "rows": shown, "rows_read": len(rows), "would_import": len(items), "skipped": counts,
        "more_rows": table["more_rows"], "more_columns": table["more_columns"],
        "limits": {"keywords": trends.MAX_KEYWORDS_PER_PASTE, "rows": MAX_ROWS, "columns": MAX_COLUMNS,
                   "keyword_chars": MAX_KEYWORD_CHARS},
    }


def collect(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """For ``POST /api/trends/import``: ``(keywords to store, counts for the answer)``, or ``ImportRefused``.

    Nothing is guessed here except where the header row is: ``keyword_column`` has to be given, and
    ``heat_column`` is "no number" when it is missing or null. The counts use the words of
    ``POST /api/trends``: ``skipped`` is the rows that gave no keyword (none in the cell, too long, or over
    the limit), ``left_out`` those of them that were over the limit of one import, ``duplicates`` the rows
    that repeated a keyword, and ``skipped_rows`` the full account as in the preview.
    """
    _name, table = _request_table(body)
    rows, width = table["rows"], table["width"]
    if body.get("keyword_column") is None:
        raise _no_column_chosen()
    keyword_column = _column(body["keyword_column"], width)
    heat_column = None if body.get("heat_column") is None else _column(body["heat_column"], width)
    if heat_column == keyword_column:
        raise _bad_column()
    items, _shown, counts = plan(rows, detect(rows)["header_row"], keyword_column, heat_column)
    if not items:
        raise _no_keywords()
    return items, {"skipped": counts["empty"] + counts["too_long"] + counts["over_limit"], "left_out": counts["over_limit"],
                   "duplicates": counts["duplicate"], "rows_read": len(rows), "skipped_rows": counts,
                   "more_rows": table["more_rows"], "more_columns": table["more_columns"]}
