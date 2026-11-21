"""Numbers of posted videos by day, a table from the Creator Center, and what viewers wrote under a video.

Nothing here is fetched from Xiaohongshu, and nothing here asks any other computer for anything. Numbers reach
the library by the creator's own act: typed on a page, given when a script is marked as posted
(``studio.posted``), or read from a table the creator saved from the Creator Center (创作服务平台) and chose
here. Comments are pasted in.

NUMBERS BY DAY. A video's numbers keep changing, and a library item carries only the last reading. Each
reading is also kept under the day it was read (``Store.record_metric_snapshot``), so two readings say how
many views came in between (``views_per_day``). The day a reading belongs to is the day the numbers were
read. It is never the day the video was posted: round 1 dated readings by a "发布时间" column, and every
"views per day" that followed was wrong. Nothing is adjusted, projected or normalised.

A TABLE FROM THE CREATOR CENTER. It is read by ``keyword_import.read_table``, the one reader of this project
for a file somebody hands the creator: bounded in size, rows, columns and cell length, an ``.xlsx`` never
unpacked to disk, no formula worked out. The import has two steps, like the keyword import:

- ``preview`` stores nothing. It says which column was taken for the title, for each number and for the
  days, how every row reads, and for every row which library item it would change, or why it changes none.
- ``bring_in`` stores exactly what a preview showed. It is sent the columns and the preview's
  ``fingerprint``; when the same file would now be read differently (another column, a library that changed
  in between, another day), it refuses and nothing is stored. What it stores is written in one transaction.

A row belongs to a library item when their titles are the same, whole: compared without regard to spaces, to
upper and lower case and to full-width and half-width forms, and in no other way. Part of a title is never
enough (round 1 took the first library item whose title contained the row's, or was contained in it). A title
that two library items carry, or two rows of the table, matches nothing: which is which cannot be told.

A row that matches nothing creates no library item. A library item is the words of a video, and a row of a
table has a title and numbers and no words. Round 1 stored such a row with its title as the text, as a
script of the creator's, so a title became material the voice is learned from. The preview lists these rows;
the creator adds the video (its file, its subtitles or its script) under that title and imports again.

Nobody on this project has seen a real export of the Creator Center. The column names in HEADERS are this
project's guesses, and nothing depends on them: the preview shows what was taken for what, and the creator
picks every column on the page before anything is stored. A column that names a day the numbers were read
dates them; without one they are dated by the day of the import, and the preview says which it is. A column
called only "日期" or "date" is not guessed to be either day.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import corpus, creator, keyword_import, llm, trends
from .store import METRIC_FIELDS, ROOT, Store, _metric

STAGE9_PATH = ROOT / "prompts" / "09_sentiment_analyzer.md"

MAX_IMPORT_ROWS = 500           # rows with a title that one import looks at; a creator's table has one row for each video
HISTORY_SHOWN = 12              # readings of one item that a page is sent, the newest
TITLE, READ_ON, POSTED_ON = "title", "read_on", "posted_on"
NUMBER_FIELDS = METRIC_FIELDS   # views, likes, collects, comments: the numbers a library item carries
DAY_FIELDS = (READ_ON, POSTED_ON)
FIELDS = (TITLE,) + NUMBER_FIELDS + DAY_FIELDS
# Guesses at what a table calls its columns, best guess first, written without spaces and in lower case. A name
# counts when the header cell contains it (``keyword_import._match``). "曝光" (how often a cover was shown) is
# not among the names for views on purpose: being shown is not being watched.
HEADERS: Dict[str, Tuple[str, ...]] = {
    TITLE: ("笔记标题", "作品标题", "视频标题", "笔记名称", "作品名称", "标题", "title"),
    "views": ("观看量", "观看数", "观看次数", "播放量", "播放数", "阅读量", "浏览量", "观看", "播放", "阅读", "views", "plays", "reads"),
    "likes": ("点赞量", "点赞数", "点赞", "likes"),
    "collects": ("收藏量", "收藏数", "收藏", "collects", "saves", "favorites"),
    "comments": ("评论量", "评论数", "评论", "comments"),
    READ_ON: ("统计日期", "数据日期", "统计时间", "数据时间", "数据更新时间", "更新时间", "数据截至", "截至", "readon", "asof"),
    POSTED_ON: ("首次发布时间", "发布时间", "发布日期", "发表时间", "posted", "published", "publishdate"),
}
# A header that holds one of these names a rate, a share or a length of time, not one of the four counts:
# "收藏率" is not the collects and "人均观看时长" is not the views.
NOT_A_COUNT = ("率", "占比", "时长", "人均", "平均", "%", "％", "rate", "ratio", "avg", "average", "time")
UPDATE, READING_ONLY, UNMATCHED, SKIPPED = "update", "reading_only", "unmatched", "skipped"
SKIP_REASONS = ("no_title", "title_cut", "over_limit", "repeated", "no_numbers", "bad_day", "future_day", "ambiguous",
                "read_before_posted")

_DAY_IN_CELL = re.compile(r"\s*([0-9]{4})\s*[-/.年]\s*([0-9]{1,2})\s*[-/.月]\s*([0-9]{1,2})\s*(?:日|号)?(?:$|[\sT])", re.ASCII)
_EXCEL_DAY_0 = datetime.date(1899, 12, 30)      # the day a spreadsheet counts its days from
_EXCEL_DAYS = (36526, 73050)                    # 2000-01-01 to 2099-12-31 as such a count: outside it a number is no day

_COMMENT_META_TAIL = re.compile(
    r"(?:\s+(?:\d+\s*(?:分钟|小时|天|周|个月|年)前|昨天|前天|\d{2}-\d{2}|\d{4}-\d{2}-\d{2})"
    r"(?:\s+[\u4e00-\u9fff]{2,8})?(?:\s*(?:回复|赞\s*\d+|❤️\s*\d+))*\s*)$"
)
_TRAILING_LIKES = re.compile(r"(?:\s*(?:赞|❤️|👍|likes?:?)\s*(\d+)\s*)$", re.I)

POSITIVE_CUES = (
    "喜欢", "好看", "好美", "温柔", "舒服", "真实", "自然", "实用", "干货", "种草", "爱看", "太棒",
    "笑死", "好笑", "可爱", "甜", "治愈", "同感", "买了", "好用", "划算", "省心", "松弛", "宝藏",
    "绝了", "舒服了", "期待更新", "催更", "love", "helpful", "cozy", "great", "cute", "funny",
)
NEGATIVE_CUES = (
    "踩雷", "拔草", "不好用", "假滑", "搓泥", "闷痘", "过敏", "太贵", "智商税", "鸡肋", "广告",
    "硬广", "失望", "卡粉", "起球", "缩水", "翻车", "太沉", "不值", "难用", "太干", "太油",
    "disappointed", "expensive", "heavy", "broke out",
)
QUESTION_CUES = (
    "?", "？", "求链接", "求同款", "求牌子", "求货号", "求教程", "求分享", "求更新", "求下期",
    "请问", "想问", "问一下", "怎么买", "哪里买", "哪买的", "什么牌子", "哪个牌子", "多少钱",
    "重不重", "沉不沉", "干皮", "油皮", "敏感肌", "适合", "够不够", "会不会", "能不能", "可以吗",
    "尺码", "色号", "型号", "link", "where did you", "which brand", "how much",
)


class AnalyticsError(Exception):
    """A request about numbers or comments that cannot be met. Raised before anything is stored.

    ``code`` is what a page can map; ``message`` (English) and ``message_zh`` are written to be shown;
    ``status`` is the HTTP status for it.
    """

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.message_zh, self.status = code, message, message_zh, status


def today() -> datetime.date:
    """Today by this computer's clock: the day an import is made, when the table names no day for its numbers."""
    return datetime.date.today()


# ---- refusals -----------------------------------------------------------------
def _needs_preview() -> AnalyticsError:
    return AnalyticsError(
        "import_needs_preview",
        "Numbers from a table are imported in two steps: a preview that shows what would change, then the import of "
        "exactly that. Open the Library and choose the table there.",
        "从表格导入数据要分两步：先预览会改动什么，再照预览的内容导入。请到「素材库」里选择表格。")


def _no_title_column() -> AnalyticsError:
    return AnalyticsError(
        "import_no_title_column",
        "No column of this table was found to hold the titles of the videos. Choose it yourself.",
        "没有在这张表里找到哪一列是视频的标题，请自己选一下。")


def _no_columns_chosen() -> AnalyticsError:
    return AnalyticsError(
        "import_no_columns",
        "Choose the column that holds the titles and at least one column of numbers before importing.",
        "导入之前，请先选好标题在哪一列，并且至少选一列数据。")


def _bad_column() -> AnalyticsError:
    return AnalyticsError("import_bad_column", "That column is not in this table, or it was chosen twice. Choose the columns again.",
                          "表格里没有这一列，或者同一列被选了两次。请重新选择。")


def _changed() -> AnalyticsError:
    return AnalyticsError(
        "import_changed",
        "What this import would store is no longer what the preview showed: the library or the day changed in "
        "between, or the columns were chosen again. Nothing was stored. Look at the preview again.",
        "这次导入要存的内容和预览时看到的已经不一样了：素材库或者日期在这期间变了，或者列被重新选过。"
        "这次什么都没有存。请重新看一遍预览。", 409)


def _nothing_to_change() -> AnalyticsError:
    return AnalyticsError(
        "import_nothing_to_change",
        "No row of this table matches a library item by its whole title, so there is nothing to import.",
        "这张表里没有哪一行的标题和素材库里的条目完全一样，所以没有可以导入的内容。")


def _reworded(refusal: keyword_import.ImportRefused) -> AnalyticsError:
    """A refusal of the file reader, as this import says it. Two of the reader's sentences speak of "the sheet with the keywords"."""
    if refusal.code == "import_too_large" and "keywords" in refusal.message:
        return AnalyticsError(
            refusal.code,
            "This workbook holds more than can be read here, or unpacks to far more than a spreadsheet of its size "
            "does. Open it in Excel, WPS or Numbers and save the sheet with the numbers as .csv.",
            "这个工作簿里的内容太多，或者解开以后比同样大小的表格大得多，这里读不了。"
            "请用 Excel、WPS 或 Numbers 打开，把有数据的那张表另存为 .csv。", refusal.status)
    return AnalyticsError(refusal.code, refusal.message, refusal.message_zh, refusal.status)


# ---- numbers by day -------------------------------------------------------------
def _days_between(earlier: Any, later: Any) -> Optional[int]:
    first, second = corpus.parse_day(earlier), corpus.parse_day(later)
    return (second - first).days if (first is not None and second is not None) else None


def views_per_day(sample: Dict[str, Any], readings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the readings of one library item say about its views over time. Counted, never projected.

    ``readings`` are the item's, the oldest day first (``Store.list_metric_snapshots``). The answer:

    - ``readings``: how many days have a reading; ``last_read_on``: the newest of those days, or None.
    - ``since_reading``: from the last two readings that hold views, ``{"from", "to", "days", "views",
      "per_day"}``: the views that came in between and that number over the days between. None with fewer
      than two such readings. A number that went down is given as it is.
    - ``since_posted``: ``{"posted_on", "to", "days", "views", "per_day"}``: the views of the newest reading
      over the days since the video was posted. None when the day it was posted is not known, and when the
      reading is of that same day: no whole day has passed.
    - ``history``: the newest HISTORY_SHOWN readings, oldest first, each ``{"read_on"}`` and the four numbers.

    An average over days says nothing about any single day: views come in fast at first and slowly later.
    """
    kept = [reading for reading in readings if isinstance(reading, dict) and corpus.parse_day(reading.get("read_on"))]
    viewed = [reading for reading in kept if reading.get("views") is not None]
    since_reading = since_posted = None
    if len(viewed) >= 2:
        before, last = viewed[-2], viewed[-1]
        days = _days_between(before["read_on"], last["read_on"])
        if days:
            gained = int(last["views"]) - int(before["views"])
            since_reading = {"from": before["read_on"], "to": last["read_on"], "days": days, "views": gained,
                             "per_day": round(gained / days, 1)}
    if viewed and sample.get("posted_on"):
        days = _days_between(sample["posted_on"], viewed[-1]["read_on"])
        if days and days > 0:
            since_posted = {"posted_on": sample["posted_on"], "to": viewed[-1]["read_on"], "days": days,
                            "views": int(viewed[-1]["views"]), "per_day": round(int(viewed[-1]["views"]) / days, 1)}
    return {
        "readings": len(kept), "last_read_on": kept[-1]["read_on"] if kept else None,
        "since_reading": since_reading, "since_posted": since_posted,
        "history": [dict({"read_on": reading["read_on"]}, **{field: reading.get(field) for field in NUMBER_FIELDS})
                    for reading in kept[-HISTORY_SHOWN:]],
    }


# ---- a table from the Creator Center: cells -------------------------------------
def read_count(cell: str) -> Optional[int]:
    """The count in a cell (``1,234``, ``1.2万``, ``3k``, ``0``), in the forms the keyword import reads; None for anything else.

    Unlike a keyword's heat, 0 is a count: a video can have no comments. An empty cell, a dash and a word are no
    number, and a row's number that is no number is left as the library has it.
    """
    parts = keyword_import._number(cell)
    if not parts:
        return None
    value = float(parts[0].replace(",", "")) * trends._UNITS.get((parts[1] or "").lower(), 1)
    return int(round(value)) if value < 1e13 else None


def read_day(cell: str) -> Optional[str]:
    """The day a cell names, as ``YYYY-MM-DD``; None when it names none.

    Read: a year, a month and a day at the start of the cell, separated by ``-``, ``/``, ``.`` or 年 月 (a time
    after them is ignored; anything else after them makes the cell no day); eight digits (``20261120``); and a
    spreadsheet's own count of days, which is what an ``.xlsx`` holds in a cell formatted as a date. A day
    without a year is not read: the year would be a guess.
    """
    text = cell.strip()
    found = _DAY_IN_CELL.match(text)
    if found:
        year, month, day = (int(part) for part in found.groups())
    elif len(text) == 8 and text.isascii() and text.isdigit():
        year, month, day = int(text[:4]), int(text[4:6]), int(text[6:])
    else:
        whole = text.partition(".")[0]
        if not (whole.isascii() and whole.isdigit() and len(whole) == 5 and keyword_import._number(text)):
            return None
        if not _EXCEL_DAYS[0] <= int(whole) <= _EXCEL_DAYS[1]:
            return None
        return (_EXCEL_DAY_0 + datetime.timedelta(days=int(whole))).isoformat()
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def title_key(title: Any) -> str:
    """A title as it is compared: the whole of it, without spaces, in one case, full-width forms as half-width ones."""
    return "".join(unicodedata.normalize("NFKC", str(title or "")).split()).casefold()


def _cut(cell: str) -> bool:
    """Whether the reader cut this cell (``keyword_import._tidy``): the whole of it is then not known."""
    return len(cell) > keyword_import.MAX_CELL_CHARS


# ---- a table from the Creator Center: which row names the columns, which column holds what ----
def _fit(header: str, field: str) -> Optional[Tuple[int, int]]:
    """How well a header cell fits a field (``keyword_import._match``); None when it does not, or names a rate where a count is wanted."""
    if field in NUMBER_FIELDS and any(word in header.replace(" ", "").lower() for word in NOT_A_COUNT):
        return None
    return keyword_import._match(header, HEADERS[field])


def _header_row(rows: List[List[str]]) -> Optional[int]:
    """The row that names the columns, or None when no row does.

    An export may start with a title or a date range, so the first rows are looked at, as far down as the keyword
    import looks. A header row holds no number, names the title and names at least one of the four numbers. Of the
    rows that qualify, the one that names the most fields is taken.
    """
    best, best_named = None, 0
    for index, row in enumerate(rows[:keyword_import.HEADER_SCAN_ROWS]):
        if any(keyword_import._number(cell) is not None for cell in row):
            continue
        named = {field for field in FIELDS if any(_fit(cell, field) is not None for cell in row)}
        if TITLE in named and named.intersection(NUMBER_FIELDS) and len(named) > best_named:
            best, best_named = index, len(named)
    return best


def _title_by_content(rows: List[List[str]], width: int) -> Optional[int]:
    """The column with the most different texts that are no numbers and no days; the leftmost of equals. For a table without a header."""
    best, best_found = None, 0
    for column in range(width):
        cells = {cell for cell in (keyword_import._cell(row, column) for row in rows)
                 if cell and keyword_import._number(cell) is None and read_day(cell) is None}
        if len(cells) > best_found:
            best, best_found = column, len(cells)
    return best


def guess(rows: List[List[str]]) -> Tuple[Optional[int], Dict[str, Optional[int]]]:
    """``(header row, {field: column})``: where the header is, and which column looks like which field (FIELDS).

    With a header, each field gets the column whose name fits it best (``keyword_import._named``: the earliest
    name in the list, then a whole-cell fit, then the shorter header), and no column is given to two fields. The
    days are settled first, so that "发布时间" is the day a video was posted and nothing else. Without a header
    only the title is guessed, from the contents; which number is which cannot be told from numbers.
    """
    header_row = _header_row(rows)
    width = max((len(row) for row in rows), default=0)
    found: Dict[str, Optional[int]] = {field: None for field in FIELDS}
    if header_row is None:
        found[TITLE] = _title_by_content(rows[:keyword_import.GUESS_ROWS], width)
        return header_row, found
    headers = [keyword_import._cell(rows[header_row], column) for column in range(width)]
    taken: Set[int] = set()
    for field in DAY_FIELDS + (TITLE,) + NUMBER_FIELDS:
        fits = [(fit, len(header), column) for column, header in enumerate(headers)
                for fit in (_fit(header, field),) if fit is not None and column not in taken]
        if fits:
            found[field] = min(fits)[2]
            taken.add(found[field])
    return header_row, found


def _column(value: Any, width: int) -> int:
    """A column the request names, checked against the table."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < width:
        raise _bad_column()
    return value


def _chosen(asked: Any, guessed: Optional[Dict[str, Optional[int]]], width: int) -> Dict[str, Optional[int]]:
    """The columns a request is read with: what it names (``fields``: a column counted from 0, or null for "none"),
    and for a field it does not name what was guessed, when a guess is allowed. No column serves two fields: one
    the creator gave to a field is no longer the guess for another."""
    asked = asked if isinstance(asked, dict) else {}
    fields: Dict[str, Optional[int]] = {}
    for field in FIELDS:
        if field in asked:
            fields[field] = None if asked[field] is None else _column(asked[field], width)
    named = [column for column in fields.values() if column is not None]
    if len(named) != len(set(named)):
        raise _bad_column()
    for field in FIELDS:
        if field not in fields:
            column = (guessed or {}).get(field)
            fields[field] = column if (column is not None and column not in named) else None
    return fields


# ---- a table from the Creator Center: what an import would store -----------------
def _latest_days(store: Store, samples: List[Dict[str, Any]]) -> Dict[str, str]:
    """For each library item, the newest day its numbers are known to have been read on."""
    latest: Dict[str, str] = {}
    for reading in store.list_metric_snapshots():
        latest[reading["sample_id"]] = max(latest.get(reading["sample_id"], ""), reading["read_on"])
    for sample in samples:
        if sample.get("metrics_on"):
            latest[sample["id"]] = max(latest.get(sample["id"], ""), sample["metrics_on"])
    return latest


def plan(rows: List[List[str]], header_row: Optional[int], fields: Dict[str, Optional[int]],
         samples: List[Dict[str, Any]], latest: Dict[str, str], day: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """``(every row below the header as it is read and what it would do, how many rows do what)``.

    ``fields`` says which column holds what; ``samples`` is the library; ``latest`` the newest day each item's
    numbers were read on; ``day`` is today, which dates the numbers when no column does and which no reading
    may lie after.

    Each row is ``{"row", "title", "views", "likes", "collects", "comments", "read_on", "posted_on", "effect",
    "why", "sample_id", "sample_title", "now"}``. ``row`` counts the rows that were read, from 1. A number the
    row does not hold is None and is left as the library has it. ``effect`` is one of:

    - UPDATE: the row's numbers become the item's numbers and are kept as its reading of ``read_on``;
    - READING_ONLY: the library already has numbers of this item read on a later day, so the row is kept as
      the reading of its own day and the item's numbers stay;
    - UNMATCHED: no library item has this title. Nothing is stored and nothing is created;
    - SKIPPED, with ``why``: ``no_title``; ``title_cut`` (longer than a cell is read, so the whole title is
      not known); ``over_limit`` (after the first MAX_IMPORT_ROWS rows with a title); ``repeated`` (the title
      stands on more than one row); ``no_numbers``; ``bad_day`` (no day could be read where the day of the
      numbers should stand); ``future_day``; ``ambiguous`` (more than one library item has the title);
      ``read_before_posted`` (the item says it was posted after that day).

    ``now`` holds the item's numbers as they are, for a row that names an item. The counts have a key for each
    effect and each reason, and ``header`` for the header row and what stands above it; they add up to the rows.
    """
    first = 0 if header_row is None else header_row + 1
    counts = dict({"header": min(first, len(rows)), UPDATE: 0, READING_ONLY: 0, UNMATCHED: 0}, **{why: 0 for why in SKIP_REASONS})
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    for sample in samples:
        by_title.setdefault(title_key(sample.get("title")), []).append(sample)
    used = [field for field in NUMBER_FIELDS if fields.get(field) is not None]
    # Which titles stand on more than one row is known only once every row was seen.
    on_rows: Dict[str, int] = {}
    titled = 0
    for row in rows[first:]:
        title = keyword_import._cell(row, fields[TITLE])
        if title and not _cut(title):
            titled += 1
            if titled <= MAX_IMPORT_ROWS:
                on_rows[title_key(title)] = on_rows.get(title_key(title), 0) + 1
    shown: List[Dict[str, Any]] = []
    titled = 0
    for number, row in enumerate(rows[first:], first + 1):
        title = keyword_import._cell(row, fields[TITLE])
        values = {field: read_count(keyword_import._cell(row, fields[field])) for field in used}
        read_on = read_day(keyword_import._cell(row, fields[READ_ON])) if fields.get(READ_ON) is not None else day
        posted_on = read_day(keyword_import._cell(row, fields[POSTED_ON])) if fields.get(POSTED_ON) is not None else None
        entry: Dict[str, Any] = dict({"row": number, "title": title}, **{field: values.get(field) for field in NUMBER_FIELDS})
        entry.update({"read_on": read_on, "posted_on": posted_on, "effect": SKIPPED, "why": None,
                      "sample_id": None, "sample_title": None, "now": None})
        shown.append(entry)
        found = by_title.get(title_key(title), []) if title else []
        if title and not _cut(title):
            titled += 1
        if not title:
            entry["why"] = "no_title"
        elif _cut(title):
            entry["why"] = "title_cut"
        elif titled > MAX_IMPORT_ROWS:
            entry["why"] = "over_limit"
        elif on_rows.get(title_key(title), 0) > 1:
            entry["why"] = "repeated"
        elif all(value is None for value in values.values()):
            entry["why"] = "no_numbers"
        elif read_on is None:
            entry["why"] = "bad_day"
        elif read_on > day:
            entry["why"] = "future_day"
        elif not found:
            entry["effect"] = UNMATCHED
        elif len(found) > 1:
            entry["why"] = "ambiguous"
        else:
            item = found[0]
            entry.update({"sample_id": item["id"], "sample_title": item["title"],
                          "now": {field: item.get(field) for field in NUMBER_FIELDS}})
            if item.get("posted_on") and read_on < item["posted_on"]:
                entry["why"] = "read_before_posted"
            else:
                entry["effect"] = READING_ONLY if latest.get(item["id"], "") > read_on else UPDATE
        counts[entry["why"] or entry["effect"]] += 1
    return shown, counts


def _fingerprint(fields: Dict[str, Optional[int]], shown: List[Dict[str, Any]]) -> str:
    """Identifies what a preview showed: the columns, and for every row what was read and what it would do."""
    said = [[fields.get(field) for field in FIELDS]]
    said += [[entry[key] for key in ("row", "title", "read_on", "effect", "why", "sample_id") + NUMBER_FIELDS] for entry in shown]
    return hashlib.sha256(json.dumps(said, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _table(body: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The file a request carries, read by the keyword import's reader: ``(its name as a label, its table)``."""
    try:
        return keyword_import._request_table(body)
    except keyword_import.ImportRefused as refusal:
        raise _reworded(refusal)


def _read(store: Store, body: Dict[str, Any], guessing: bool) -> Dict[str, Any]:
    """Read the file of a request with the columns it names: everything ``preview`` answers with.

    With ``guessing`` a column the request does not name is guessed; without, it is not used.
    """
    name, table = _table(body)
    rows, width = table["rows"], table["width"]
    header_row, guessed = guess(rows)
    fields = _chosen(body.get("fields"), guessed if guessing else None, width)
    if fields[TITLE] is None:
        raise _no_title_column() if guessing else _no_columns_chosen()
    if not guessing and all(fields[field] is None for field in NUMBER_FIELDS):
        raise _no_columns_chosen()
    samples = store.list_samples()
    day = today().isoformat()
    shown, counts = plan(rows, header_row, fields, samples, _latest_days(store, samples), day)
    data = (rows if header_row is None else rows[header_row + 1:])[:keyword_import.GUESS_ROWS]
    headers = [keyword_import._cell(rows[header_row], column) for column in range(width)] if header_row is not None else []
    columns = [{"index": column, "header": headers[column] if headers else "",
                "samples": [keyword_import._sample(cell) for cell in (keyword_import._cell(row, column) for row in data) if cell][:2]}
               for column in range(width)]
    return {
        "name": name, "kind": table["kind"], "delimiter": table["delimiter"], "sheet": table["sheet"],
        "other_sheets": table["other_sheets"], "header_row": header_row, "headers": headers, "columns": columns,
        "fields": fields, "guessed": guessed,
        "dated_by": "column" if fields[READ_ON] is not None else "import_day", "import_day": day,
        "rows": shown, "rows_read": len(rows), "counts": counts, "would_change": counts[UPDATE] + counts[READING_ONLY],
        "more_rows": table["more_rows"], "more_columns": table["more_columns"],
        "limits": {"rows": keyword_import.MAX_ROWS, "columns": keyword_import.MAX_COLUMNS, "import_rows": MAX_IMPORT_ROWS},
        "fingerprint": _fingerprint(fields, shown),
    }


def preview(store: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    """What ``POST /api/rednote/import/preview`` answers. Nothing is stored.

    ``body`` holds the file (``name`` and ``text`` or ``file_base64``) and, once the creator has picked,
    ``fields``: for any of FIELDS the column it is in, counted from 0, or null for "none". A field the request
    does not name is guessed (``guess``). The answer says what was found in the file (``header_row``,
    ``headers``, ``columns`` with two sample cells each, ``guessed``), which columns it was read with
    (``fields``), how the numbers are dated (``dated_by``: ``column``, or ``import_day`` with the day in
    ``import_day``), every row as ``plan`` gives it, the counts, and ``fingerprint``, which ``bring_in`` is
    handed back. ``rows_read`` is the sum of ``counts``.

    Raises ``AnalyticsError``: what the file reader refuses, ``import_bad_column``, ``import_no_title_column``.
    """
    return _read(store, body if isinstance(body, dict) else {}, guessing=True)


def bring_in(store: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    """What ``POST /api/rednote/import`` does: store exactly what a preview showed, or nothing.

    ``body`` holds the file again, ``fields`` as the preview answered them, and that preview's
    ``fingerprint``. Nothing is guessed here except where the header row is: a field ``fields`` does not name
    is not used. The file is read again, and when what it would store now is not what the fingerprint stands
    for, the import is refused (``import_changed``). Otherwise every UPDATE and READING_ONLY row is stored in
    one step (``Store.apply_readings``). No row creates a library item.

    The answer: ``changed`` (items whose numbers were replaced), ``readings_only``, ``unmatched``, ``counts``
    and ``rows_read`` as in the preview, ``dated_by`` and ``import_day``.

    Raises ``AnalyticsError``: ``import_needs_preview`` for the one-step request of round 1,
    ``import_no_columns``, ``import_changed``, ``import_nothing_to_change``, and what ``preview`` raises.
    """
    body = body if isinstance(body, dict) else {}
    if any(key in body for key in ("data_base64", "filename", "create_missing")) or not isinstance(body.get("fingerprint"), str):
        raise _needs_preview()
    if not isinstance(body.get("fields"), dict):
        raise _no_columns_chosen()
    read = _read(store, body, guessing=False)
    if read["fingerprint"] != body["fingerprint"]:
        raise _changed()
    readings = [{"sample_id": entry["sample_id"], "read_on": entry["read_on"], "current": entry["effect"] == UPDATE,
                 "values": {field: entry[field] for field in NUMBER_FIELDS if entry[field] is not None}}
                for entry in read["rows"] if entry["effect"] in (UPDATE, READING_ONLY)]
    if not readings:
        raise _nothing_to_change()
    try:
        store.apply_readings(readings)
    except KeyError:
        raise _changed()
    counts = read["counts"]
    return {"changed": counts[UPDATE], "readings_only": counts[READING_ONLY], "unmatched": counts[UNMATCHED], "counts": counts,
            "rows_read": read["rows_read"], "dated_by": read["dated_by"], "import_day": read["import_day"]}


# ---- what round 1 left below: the overview and the comments, reviewed in the commits that follow ----
def _median(nums: List[float]) -> Optional[float]:
    if not nums:
        return None
    s = sorted(nums)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _quadrant(view_lift: Optional[float], save_rate: Optional[float], comment_rate: Optional[float],
              median_save_rate: Optional[float], interaction_lift: Optional[float]) -> str:
    if view_lift is None and interaction_lift is None:
        return "UNMEASURED"
    reach_high = (view_lift is not None and view_lift >= 1.25) or (view_lift is None and (interaction_lift or 0) >= 1.35)
    save_high = (
        (save_rate is not None and median_save_rate is not None and save_rate >= max(1.5, median_save_rate * 1.2))
        or (save_rate is not None and median_save_rate is None and save_rate >= 3.0)
        or (save_rate is None and (interaction_lift or 0) >= 1.35)
    )
    if reach_high and save_high:
        return "BREAKOUT_HIT"
    if not reach_high and save_high:
        return "HIGH_SAVE_GEM"
    if comment_rate is not None and comment_rate >= 0.8:
        return "HIGH_DISCUSSION"
    if reach_high and not save_high:
        return "BROAD_LOW_CONVERT"
    if (view_lift is not None and view_lift < 0.65) and (interaction_lift is None or interaction_lift < 0.65):
        return "UNDERPERFORMING"
    return "STEADY"


def analytics_overview(store: Store) -> Dict[str, Any]:
    """Compute dynamic RedNote view-count, conversion, velocity, and sentiment insights across the library."""
    samples = store.list_samples()
    all_snaps = store.list_metric_snapshots()
    snaps_by_sample: Dict[str, List[Dict[str, Any]]] = {}
    for s in all_snaps:
        snaps_by_sample.setdefault(s["sample_id"], []).append(s)
    sentiments = store.list_sample_sentiments()

    organic = [s for s in samples if not s.get("is_sponsored")]
    org_views = [float(s["views"]) for s in organic if s.get("views") is not None and s["views"] > 0]
    org_inters = [float(c) for s in organic for c in (corpus.interactions(s),) if c is not None]
    org_save_rates = [
        100.0 * float(s["collects"]) / float(s["views"])
        for s in organic
        if s.get("views") and s["views"] > 0 and s.get("collects") is not None
    ]

    median_views = _median(org_views)
    median_inters = _median(org_inters)
    median_save_rate = _median(org_save_rates)

    videos: List[Dict[str, Any]] = []
    top_questions: List[Dict[str, Any]] = []
    opportunities: List[Dict[str, Any]] = []

    for sample in samples:
        sid = sample["id"]
        views = sample.get("views")
        likes = sample.get("likes")
        collects = sample.get("collects")
        comments_n = sample.get("comments")
        inter = corpus.interactions(sample)

        save_rate = round(100.0 * collects / views, 2) if (views and views > 0 and collects is not None) else None
        comment_rate = round(100.0 * comments_n / views, 2) if (views and views > 0 and comments_n is not None) else None
        inter_rate = round(100.0 * inter / views, 2) if (views and views > 0 and inter is not None) else None

        v_lift = round(float(views) / median_views, 2) if (views is not None and median_views and median_views > 0) else None
        i_lift = round(float(inter) / median_inters, 2) if (inter is not None and median_inters and median_inters > 0) else None

        if v_lift is not None and i_lift is not None:
            comp_lift = round(0.45 * v_lift + 0.55 * i_lift, 2)
        elif i_lift is not None:
            comp_lift = i_lift
        else:
            comp_lift = v_lift

        vel = views_per_day(sample, snaps_by_sample.get(sid, []))
        quad = _quadrant(v_lift, save_rate, comment_rate, median_save_rate, i_lift)
        sent_rec = sentiments.get(sid)
        sent_summary = sent_rec["summary"] if sent_rec else None

        if isinstance(sent_summary, dict):
            for q in (sent_summary.get("audience_questions") or [])[:4]:
                if isinstance(q, dict) and q.get("question_zh"):
                    top_questions.append({
                        "sample_id": sid,
                        "video_title": sample["title"],
                        "question_zh": str(q["question_zh"]),
                        "question_en": str(q.get("question_en") or q["question_zh"]),
                        "demand_count": int(q.get("demand_count") or 1),
                    })
            for angle in (sent_summary.get("followup_video_angles") or [])[:2]:
                if isinstance(angle, dict) and angle.get("title_zh"):
                    opportunities.append({
                        "kind": "sentiment_followup",
                        "source_sample_id": sid,
                        "source_title": sample["title"],
                        "title_zh": str(angle["title_zh"]),
                        "title_en": str(angle.get("title_en") or angle["title_zh"]),
                        "video_format": str(angle.get("video_format") or "VLOG_VOICEOVER"),
                        "target_sec": int(angle.get("target_duration_sec") or 60),
                        "rationale_zh": str(angle.get("rationale_zh") or ""),
                        "rationale_en": str(angle.get("rationale_en") or ""),
                        "notes": str(angle.get("script_notes_zh") or ""),
                    })

        if quad in ("BREAKOUT_HIT", "HIGH_SAVE_GEM") and not sample.get("is_sponsored"):
            opportunities.append({
                "kind": "high_resonance_sequel",
                "source_sample_id": sid,
                "source_title": sample["title"],
                "title_zh": "%s｜续集与真实后续分享" % sample["title"][:28],
                "title_en": "Follow-up & deeper dive on \"%s\"" % sample["title"][:36],
                "video_format": "VLOG_VOICEOVER",
                "target_sec": 60,
                "rationale_zh": (
                    "该笔记综合表现为常值的 %.1f 倍%s，适合趁热做第二弹或回答细节。"
                    % (comp_lift or 1.5, ("（收藏率 %.1f%%）" % save_rate) if save_rate is not None else "")
                ),
                "rationale_en": (
                    "Performed at %.1fx usual baseline%s — prime candidate for a part-2 follow-up."
                    % (comp_lift or 1.5, (" (%.1f%% save rate)" % save_rate) if save_rate is not None else "")
                ),
                "notes": "延续《%s》里观众最感兴趣的具体细节，补充使用一段时间后的真实感受。" % sample["title"][:30],
            })

        videos.append({
            "id": sid,
            "title": sample["title"],
            "is_sponsored": bool(sample.get("is_sponsored")),
            "views": views,
            "likes": likes,
            "collects": collects,
            "comments": comments_n,
            "interactions": inter,
            "save_rate_pct": save_rate,
            "comment_rate_pct": comment_rate,
            "interaction_rate_pct": inter_rate,
            "view_lift": v_lift,
            "interaction_lift": i_lift,
            "composite_lift": comp_lift,
            "quadrant": quad,
            "per_day": vel,
            "sentiment": sent_summary,
            "sentiment_source": sent_rec["source"] if sent_rec else None,
        })

    videos.sort(
        key=lambda v: (
            v["composite_lift"] is not None,
            v["composite_lift"] if v["composite_lift"] is not None else -1.0,
            v["views"] or 0,
            v["interactions"] or 0,
        ),
        reverse=True,
    )
    top_questions.sort(key=lambda q: q["demand_count"], reverse=True)

    return {
        "total_videos": len(samples),
        "videos_with_views": len(org_views),
        "videos_with_interactions": len(org_inters),
        "videos_with_sentiment": len(sentiments),
        "median_views": round(median_views, 1) if median_views is not None else None,
        "median_interactions": round(median_inters, 1) if median_inters is not None else None,
        "median_save_rate_pct": round(median_save_rate, 2) if median_save_rate is not None else None,
        "videos": videos,
        "top_audience_questions": top_questions[:15],
        "opportunities": opportunities[:12],
    }


# ---- Hybrid Audience Comment & Sentiment Engine -----------------------------

def classify_comment_text(text: str) -> Tuple[str, bool]:
    """Return ``(sentiment, is_question)`` deterministically for one viewer comment."""
    lowered = text.lower()
    pos = sum(1 for cue in POSITIVE_CUES if cue in lowered)
    neg = sum(1 for cue in NEGATIVE_CUES if cue in lowered)
    is_q = any(cue in lowered for cue in QUESTION_CUES)
    if pos > 0 and neg > 0:
        sentiment = "mixed"
    elif neg > pos:
        sentiment = "negative"
    elif pos > 0:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return sentiment, is_q


def parse_pasted_comments(raw_text: str) -> List[Dict[str, Any]]:
    """Parse pasted RedNote comments into clean records with likes, sentiment, and question flags."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw_line in raw_text.replace("\r\n", "\n").splitlines()[:600]:
        line = raw_line.strip()
        if not line or len(line) < 2:
            continue
        likes: Optional[int] = None
        m_likes = _TRAILING_LIKES.search(line)
        if m_likes:
            likes = _metric(int(m_likes.group(1)))
            line = line[:m_likes.start()].strip()
        line = _COMMENT_META_TAIL.sub("", line).strip()
        if not line:
            continue
        author = ""
        if "：" in line or ":" in line:
            sep = "：" if "：" in line else ":"
            left, right = line.split(sep, 1)
            if 1 <= len(left.strip()) <= 20 and len(right.strip()) >= 2 and not left.strip().startswith("http"):
                author = left.strip()
                line = right.strip()
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        sentiment, is_q = classify_comment_text(line)
        out.append({
            "author": author,
            "text": line[:1000],
            "likes": likes,
            "sentiment": sentiment,
            "is_question": is_q,
        })
    return out


def build_local_sentiment_summary(sample: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic instant sentiment & viewer-question summary (works offline without any LLM)."""
    total = len(comments)
    if total == 0:
        return {
            "overall_sentiment": "neutral",
            "comment_count": 0,
            "sentiment_breakdown_pct": {"positive": 0, "neutral": 0, "negative": 0},
            "summary_zh": "暂无评论内容可供分析。",
            "summary_en": "No viewer comments added yet.",
            "praise_themes": [],
            "objections_or_pain_points": [],
            "audience_questions": [],
            "followup_video_angles": [],
        }

    pos_list = [c for c in comments if c.get("sentiment") == "positive"]
    neg_list = [c for c in comments if c.get("sentiment") in ("negative", "mixed")]
    neu_count = max(0, total - len(pos_list) - len(neg_list))

    pos_pct = int(round(100.0 * len(pos_list) / total))
    neg_pct = int(round(100.0 * len(neg_list) / total))
    neu_pct = max(0, 100 - pos_pct - neg_pct)

    if pos_pct >= 65 and neg_pct <= 15:
        overall = "positive"
    elif pos_pct >= 45 and neg_pct <= 25:
        overall = "mostly_positive"
    elif neg_pct >= 40:
        overall = "critical"
    elif pos_pct > 0 and neg_pct > 0:
        overall = "mixed"
    else:
        overall = "neutral"

    questions = [c for c in comments if c.get("is_question")]
    questions.sort(key=lambda c: (c.get("likes") or 0, len(c.get("text") or "")), reverse=True)

    audience_qs = [
        {
            "question_zh": q["text"][:140],
            "question_en": q["text"][:140],
            "demand_count": max(1, (q.get("likes") or 0) + 1),
        }
        for q in questions[:6]
    ]

    praise_themes = [
        {
            "theme_zh": p["text"][:60],
            "theme_en": "Viewer praise highlight",
            "sample_comment": p["text"][:140],
        }
        for p in sorted(pos_list, key=lambda c: c.get("likes") or 0, reverse=True)[:4]
    ]

    objections = [
        {
            "issue_zh": n["text"][:60],
            "issue_en": "Viewer concern / hesitation",
            "sample_comment": n["text"][:140],
        }
        for n in sorted(neg_list, key=lambda c: c.get("likes") or 0, reverse=True)[:4]
    ]

    followups: List[Dict[str, Any]] = []
    if audience_qs:
        top_q = audience_qs[0]["question_zh"]
        followups.append({
            "title_zh": "回应评论区高频提问｜%s" % top_q[:22].rstrip("？?！!。"),
            "title_en": "Answering top viewer question from \"%s\"" % sample["title"][:32],
            "video_format": "VLOG_VOICEOVER",
            "target_duration_sec": 60,
            "rationale_zh": "本条笔记有 %d 条观众提问，最高赞问题：「%s」" % (len(questions), top_q[:40]),
            "rationale_en": "%d viewer questions spotted; top question: \"%s\"" % (len(questions), top_q[:50]),
            "script_notes_zh": "开头直接引用上期评论区提问：「%s」，实拍演示并给出真实建议。" % top_q[:60],
        })
    if objections:
        top_obj = objections[0]["sample_comment"]
        followups.append({
            "title_zh": "%s｜真实优缺点与避坑实话" % sample["title"][:20],
            "title_en": "Honest pros, cons & caveats follow-up on \"%s\"" % sample["title"][:30],
            "video_format": "SINGLE_REVIEW",
            "target_duration_sec": 60,
            "rationale_zh": "评论区存在顾虑反馈（「%s」），适合拍一期实测解答。" % top_obj[:36],
            "rationale_en": "Addresses viewer concern spotted in comments.",
            "script_notes_zh": "针对观众担心的点（%s）做真实对比与适用人群边界说明。" % top_obj[:50],
        })

    summary_zh = "共分析 %d 条评论：正面 %d%%、中性 %d%%、顾虑/负面 %d%%，识别出 %d 条观众提问。" % (
        total, pos_pct, neu_pct, neg_pct, len(questions)
    )
    summary_en = "Analyzed %d comments: %d%% positive, %d%% neutral, %d%% critical/mixed, with %d viewer questions." % (
        total, pos_pct, neu_pct, neg_pct, len(questions)
    )

    return {
        "overall_sentiment": overall,
        "comment_count": total,
        "sentiment_breakdown_pct": {"positive": pos_pct, "neutral": neu_pct, "negative": neg_pct},
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "praise_themes": praise_themes,
        "objections_or_pain_points": objections,
        "audience_questions": audience_qs,
        "followup_video_angles": followups,
    }


def ingest_comments_for_sample(store: Store, sample_id: str, raw_text: str, *,
                               replace: bool = False) -> Dict[str, Any]:
    """Parse and save viewer comments for ``sample_id``, computing an immediate deterministic sentiment summary."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    parsed = parse_pasted_comments(raw_text)
    if not parsed:
        raise AnalyticsError("no_comments", "Paste at least one viewer comment to analyze.", "请至少粘贴一条评论。", 400)
    added = store.add_sample_comments(sample_id, parsed, replace=replace)
    all_comments = store.list_sample_comments(sample_id)
    # If sample.comments count was unset, sync it to at least len(all_comments)
    if sample.get("comments") is None:
        store.update_sample(sample_id, {"comments": len(all_comments)})
    local_summary = build_local_sentiment_summary(sample, all_comments)
    existing = store.get_sample_sentiment(sample_id)
    if existing is None or existing.get("source") == "local" or replace:
        store.save_sample_sentiment(sample_id, local_summary, source="local")
    return {
        "added": added,
        "total_comments": len(all_comments),
        "comments": all_comments[:100],
        "sentiment": (store.get_sample_sentiment(sample_id) or {}).get("summary", local_summary),
        "sentiment_source": (store.get_sample_sentiment(sample_id) or {}).get("source", "local"),
    }


def _load_stage9_sections() -> Tuple[str, str]:
    text = STAGE9_PATH.read_text(encoding="utf-8")
    sys_idx = text.index("## System Prompt") + len("## System Prompt")
    usr_idx = text.index("## User Prompt Template")
    system = text[sys_idx:usr_idx].strip().strip("-").strip()
    user_tmpl = text[usr_idx + len("## User Prompt Template"):].strip()
    return system, user_tmpl


def build_sentiment_prompt(store: Store, sample_id: str) -> Tuple[str, str, str]:
    """Build ``(system, user, brief_id)`` for Stage 9 AI sentiment synthesis on ``sample_id``."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    comments = store.list_sample_comments(sample_id)
    if not comments:
        raise AnalyticsError("no_comments", "Add viewer comments before running AI sentiment synthesis.",
                             "请先添加观众评论，再运行 AI 评论情绪分析。", 400)
    local_pre = build_local_sentiment_summary(sample, comments)
    system, user_tmpl = _load_stage9_sections()
    prof = creator.prompt_block(store.load_creator())
    meta = {
        "title": sample["title"],
        "is_sponsored": bool(sample.get("is_sponsored")),
        "views": sample.get("views"),
        "likes": sample.get("likes"),
        "collects": sample.get("collects"),
        "comments": sample.get("comments"),
    }
    compact_comments = [
        {"text": c["text"][:300], "likes": c.get("likes") or 0, "pre_flag": c.get("sentiment")}
        for c in comments[:80]
    ]
    user = (
        user_tmpl
        .replace("{{creator_profile_json}}", json.dumps(prof, ensure_ascii=False, indent=2))
        .replace("{{video_metadata_json}}", json.dumps(meta, ensure_ascii=False, indent=2))
        .replace("{{deterministic_counts_json}}", json.dumps(local_pre["sentiment_breakdown_pct"], ensure_ascii=False))
        .replace("{{viewer_comments_json}}", json.dumps(compact_comments, ensure_ascii=False, indent=2))
    )
    brief_id = store.save_brief("sentiment", {"sample_id": sample_id, "comment_count": len(comments)}, fingerprint=sample_id)
    return system, user, brief_id


def apply_sentiment_reply(store: Store, sample_id: str, reply: Dict[str, Any], *,
                          source: str = "ai") -> Dict[str, Any]:
    """Validate and store a Stage 9 JSON reply for ``sample_id``."""
    sample = store.get_sample(sample_id)
    if sample is None:
        raise AnalyticsError("not_found", "Library item not found.", "未找到该素材条目。", 404)
    comments = store.list_sample_comments(sample_id)
    fallback = build_local_sentiment_summary(sample, comments)
    if not isinstance(reply, dict):
        raise llm.LLMError("Sentiment reply must be a JSON object", "bad_json")

    overall = str(reply.get("overall_sentiment") or fallback["overall_sentiment"]).strip().lower()
    if overall not in ("positive", "mostly_positive", "mixed", "critical", "neutral"):
        overall = fallback["overall_sentiment"]

    raw_pct = reply.get("sentiment_breakdown_pct")
    if isinstance(raw_pct, dict):
        p = max(0, min(100, int(raw_pct.get("positive") or 0)))
        n = max(0, min(100, int(raw_pct.get("negative") or 0)))
        u = max(0, min(100, int(raw_pct.get("neutral") if raw_pct.get("neutral") is not None else (100 - p - n))))
        pct = {"positive": p, "neutral": u, "negative": n}
    else:
        pct = fallback["sentiment_breakdown_pct"]

    praise = []
    for item in (reply.get("praise_themes") if isinstance(reply.get("praise_themes"), list) else [])[:8]:
        if isinstance(item, dict) and item.get("theme_zh"):
            praise.append({
                "theme_zh": str(item["theme_zh"])[:120],
                "theme_en": str(item.get("theme_en") or item["theme_zh"])[:160],
                "sample_comment": str(item.get("sample_comment") or "")[:200],
            })

    objections = []
    for item in (reply.get("objections_or_pain_points") if isinstance(reply.get("objections_or_pain_points"), list) else [])[:8]:
        if isinstance(item, dict) and item.get("issue_zh"):
            objections.append({
                "issue_zh": str(item["issue_zh"])[:120],
                "issue_en": str(item.get("issue_en") or item["issue_zh"])[:160],
                "sample_comment": str(item.get("sample_comment") or "")[:200],
            })

    questions = []
    for item in (reply.get("audience_questions") if isinstance(reply.get("audience_questions"), list) else [])[:10]:
        if isinstance(item, dict) and item.get("question_zh"):
            questions.append({
                "question_zh": str(item["question_zh"])[:200],
                "question_en": str(item.get("question_en") or item["question_zh"])[:200],
                "demand_count": max(1, min(9999, int(item.get("demand_count") or 1)))
                if isinstance(item.get("demand_count"), (int, float)) else 1,
            })

    angles = []
    for item in (reply.get("followup_video_angles") if isinstance(reply.get("followup_video_angles"), list) else [])[:6]:
        if isinstance(item, dict) and item.get("title_zh"):
            angles.append({
                "title_zh": str(item["title_zh"])[:160],
                "title_en": str(item.get("title_en") or item["title_zh"])[:180],
                "video_format": str(item.get("video_format") or "VLOG_VOICEOVER")[:40],
                "target_duration_sec": max(15, min(300, int(item.get("target_duration_sec") or 60)))
                if isinstance(item.get("target_duration_sec"), (int, float)) else 60,
                "rationale_zh": str(item.get("rationale_zh") or "")[:400],
                "rationale_en": str(item.get("rationale_en") or "")[:400],
                "script_notes_zh": str(item.get("script_notes_zh") or "")[:800],
            })

    cleaned = {
        "overall_sentiment": overall,
        "comment_count": len(comments),
        "sentiment_breakdown_pct": pct,
        "summary_zh": str(reply.get("summary_zh") or fallback["summary_zh"])[:600],
        "summary_en": str(reply.get("summary_en") or fallback["summary_en"])[:600],
        "praise_themes": praise or fallback["praise_themes"],
        "objections_or_pain_points": objections or fallback["objections_or_pain_points"],
        "audience_questions": questions or fallback["audience_questions"],
        "followup_video_angles": angles or fallback["followup_video_angles"],
    }
    saved = store.save_sample_sentiment(sample_id, cleaned, source=source)
    return saved or {"sample_id": sample_id, "summary": cleaned, "source": source}


def analyze_sample_sentiment_ai(store: Store, sample_id: str,
                                transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Run Stage 9 deep audience sentiment synthesis via configured AI provider (``temperature=0.2``)."""
    cfg = store.load_config()
    provider = cfg.get("provider") or ""
    model = cfg.get("model") or ""
    if provider == "manual":
        raise llm.LLMError("Manual copy-paste mode is selected", "manual_mode")
    if not provider or not model:
        raise llm.LLMError("AI provider not configured", "not_configured")
    api_key = (cfg.get("keys") or {}).get(provider, "") if provider != "local" else ""
    if provider != "local" and not api_key:
        raise llm.LLMError("API key required", "not_configured")
    system, user, _ = build_sentiment_prompt(store, sample_id)
    reply = llm.complete_json(
        provider, api_key, model, system, user,
        transport=transport,
        base_url=cfg.get("local_base_url") or None,
        temperature=0.2,
    )
    return apply_sentiment_reply(store, sample_id, reply, source="ai")
