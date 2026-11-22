"""Trend keywords: reading what the creator pastes in, keeping what switched-on sources saw, and scoring it
against the creator's own history.

Where a keyword comes from. This module itself fetches nothing and calls no model:

- from the creator: typed, pasted, or a keyword export imported by hand (``manual``: their own entry);
- from an AI that looked it up on the live web when asked (``ai``: unverified, kept with its source link);
- from the background sources in ``studio/trend_sources.py``, each running ONLY when Autopilot, the task
  ``trends.refresh`` (or watched-folder intake), and that specific source are explicitly switched on by the
  creator: the scheduled Stage 7 AI web lookup (``ai``); a public ``https://`` page the creator explicitly
  listed in Settings, fetched politely after checking ``robots.txt`` and read via ``prompts/10_trend_page_reader.md``
  (``web_page``, with that page as ``source_url``); and a keyword spreadsheet in the watched folder (``file``).
  All of these are stored as unverified.

Nothing is ever fetched from Xiaohongshu: ``studio/trend_sources.py`` refuses ``xiaohongshu.com``,
``rednote.com``, and ``xhslink.com`` (and their subdomains) by name before any DNS or socket call. With
Autopilot and automatic sources off, which is how every new data folder starts, nothing runs in the
background and no trend page or lookup is fetched from anywhere.

What an automatic source finds is only ever ADDED (``store_found``): a keyword that is not tracked yet
becomes a row, without a heat number, and every keyword it saw is written down as a sighting (the table
``trend_observations``: which keyword, which source, which day, with the number or the place in a list when
the source showed one). A row that was there before is left exactly as it was, whoever made it. So taking a
run back (``remove_found``) is exact: its sightings go, and so do the rows it added, unless one was changed
since. A number a source showed is kept with the sighting and never becomes a row's heat: only a number the
creator entered counts in the score.

Scoring is deterministic:

    trend       = strength * freshness                              0..1
      strength  = log(1 + heat) / log(1 + highest heat)   when she entered a heat number
      freshness = 0.5 ** (days since last seen / HALF_LIFE_DAYS)
                  "last seen" is the row's own date. For an unverified keyword it is the day a source
                  last saw it, when that is later: the row is not touched by a later sighting (see
                  above), so without this a keyword seen again today would count as old. A keyword
                  she typed keeps her date whatever a source reports.
    performance = 0.5 + shrunk / 4, clamped                          0..1; 0.5 = like her usual video
      shrunk    = average log2(interactions / her median) over her on-topic
                  videos (or log2(views / her median views) for a video that
                  carries views alone), each capped at +-LOG_CAP, then averaged
                  together with PRIOR_VIDEOS ordinary videos. Working in logs
                  with a cap means one viral video cannot dominate.
    score       = w_trend * trend + w_mine * performance             shown as 0..100

Three marks are shown beside the score and are not part of it:

- ``sightings``: on how many different days of the last SIGHTING_DAYS an automatic source saw the keyword.
  The app sees a source only on days it was open and the task ran, so a gap is not a fall.
- ``rising`` (``sightings``): seen on at least RISING_MIN_DAYS different days of the last
  RISING_WINDOW_DAYS, and one of: the number one source showed for it grew by RISING_HEAT_RATIO; its place
  in one source's list moved up by RISING_RANK_PLACES; or it is new, first seen inside that window by a
  source that had been read on an earlier day and did not show it then.
- ``niche_match`` (``niche_matches``): whether the keyword and one of the niches in the creator profile
  share most of their characters, by the matching that ties a keyword to a video. It compares characters,
  not meaning: 面霜 does not match the niche 护肤. That is why it does not move the score: it would push
  down keywords that fit.

The constants and weights below are judgement calls, the rule for "rising" among them. They have not been tuned
against real outcomes, because no outcome data exists yet.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import math
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import corpus, creator, guardrails, textstats, wordlist
from .store import MAX_KEYWORD_CHARS, MAX_TRENDS, clean_keyword, keyword_key

HALF_LIFE_DAYS = 14.0    # a trend counts half as much two weeks after it was last seen
PRIOR_VIDEOS = 2.0       # her on-topic videos are averaged with this many "ordinary" ones
LOG_CAP = 3.0            # one video counts as at most 8x (or 1/8) of her median
RELEVANT_AT = 0.6        # share of a keyword's pieces a video must contain to count as on-topic
STALE_BELOW = 0.25       # freshness under this is shown as possibly out of date (after 28 days)
MAX_KEYWORDS_PER_PASTE = 200
WEIGHTS = {"trend": (0.7, 0.3), "balanced": (0.5, 0.5), "mine": (0.3, 0.7)}  # (trend, her own results)

MANUAL, AI, WEB_PAGE, FILE = "manual", "ai", "web_page", "file"   # ``trends.source``: creator entry; AI lookup; public web page; keyword file
SEEN_BY = ("ai", "page", "file")            # ``trend_observations.source``: the scheduled lookup, a named page, a keyword file
SIGHTING_DAYS = 60          # a sighting older than this is not counted
MAX_SIGHTINGS = 20000       # rows kept in trend_observations; the oldest go first. Two runs a day of 112 keywords fill it in 89 days
MAX_ORIGIN_CHARS = 300      # which page or which file, as it is kept with a sighting
MAX_RANK = 500              # a place in a list beyond this is not kept
RISING_WINDOW_DAYS = 7      # "rising" looks at this many days, today included
RISING_MIN_DAYS = 2         # ... and wants sightings on this many different days of them: one sighting is not a trend
RISING_HEAT_RATIO = 1.2     # the number one source showed last is this many times the first one in the window
RISING_RANK_PLACES = 3      # or the keyword moved up this many places in one source's list

_UNITS = {"万": 10_000, "w": 10_000, "k": 1_000, "亿": 100_000_000}
_EXPLICIT_SEPARATORS = "\t,，;；|｜"
# A line that ends in a heat number, "<keyword><separator><number><unit>+", written for the line read backwards:
# an optional "+", an optional unit, the number, the separator, then the keyword. Read forwards, the same pattern
# has to begin with "any text, as little as possible", and it then tries the rest again from every position in
# the line: one pasted line of 32,000 spaces between two words held the server for 7.5 seconds, and so did
# "a,1,1,1,...,x". Read from the end there is one starting point, and the work grows with the length of the line.
_TRAILING_HEAT_REVERSED = re.compile(
    r"\s*\+?(万|亿|[wWkK])?\s*((?:\d+\.)?[\d,]*\d)(\s*[\t,，;；|｜]\s*|\s+)(.+)")
_BULLET = re.compile(r"^\s*(?:\d{1,3}\s*[.、)）:：](?!\d)\s*|[-•·*]\s+)")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+")


# ---- reading pasted keywords ------------------------------------------------
def _heat(number: str, unit: Optional[str]) -> Optional[int]:
    value = float(number.replace(",", "")) * _UNITS.get((unit or "").lower(), 1)
    return int(round(value)) if 0 < value < 1e13 else None


def _trailing_heat(line: str) -> Optional[Tuple[str, str, str, Optional[str]]]:
    """``(keyword part, separator, number, unit)`` for a line that ends in a number, else None.

    The keyword part is the shortest start of the line that leaves a separator and a number behind it,
    so ``护肤,1,234`` is the keyword ``护肤`` with the number ``1,234``.
    """
    match = _TRAILING_HEAT_REVERSED.fullmatch(line[::-1])
    if not match:
        return None
    return match.group(4)[::-1], match.group(3)[::-1], match.group(2)[::-1], match.group(1)


def parse_keywords(text: str) -> Tuple[List[Dict[str, Any]], int]:
    """Turn pasted lines into ``[{"keyword", "heat"}]`` plus a count of lines skipped.

    One keyword per line. An optional heat number may follow after a comma or
    tab (``秋冬护肤, 125000``), or after a space when it is clearly a count
    (``秋冬护肤 12.5万``, ``秋冬护肤 12,500``); ``iPhone 15`` stays a keyword.
    List numbering and a leading # are removed.
    """
    items: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        heat = None
        found = _trailing_heat(line)
        if found:
            before, separator, number, unit = found
            value = _heat(number, unit)
            explicit = any(ch in separator for ch in _EXPLICIT_SEPARATORS)
            if explicit or unit or (value or 0) >= 1000:
                line, heat = before, value
        keyword = clean_keyword(line)
        key = keyword_key(keyword)
        if not key or len(keyword) > MAX_KEYWORD_CHARS or (key not in items and len(items) >= MAX_KEYWORDS_PER_PASTE):
            skipped += 1
            continue
        if heat is None and key in items:
            heat = items[key]["heat"]
        items[key] = {"keyword": keyword, "heat": heat}
    return list(items.values()), skipped


# ---- matching a keyword to her videos ---------------------------------------
class _Hay:
    """A video's title and script, prepared for keyword matching."""

    __slots__ = ("compact", "words")

    def __init__(self, sample: Dict[str, Any]) -> None:
        text = ("%s\n%s" % (sample.get("title") or "", sample.get("text") or "")).replace("\u2019", "'").lower()
        self.compact = re.sub(r"\s+", "", text)
        self.words = set(_LATIN.findall(text))


def keyword_pieces(keyword: str) -> List[str]:
    """Chinese runs become overlapping two-character pieces; English words and numbers stay whole."""
    keyword = (keyword or "").replace("\u2019", "'").lower()
    pieces: List[str] = []
    for run in _CJK_RUN.findall(keyword):
        pieces += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return pieces + _LATIN.findall(keyword)


def relevance(keyword: str, hay: _Hay) -> float:
    """0..1: 1.0 when the keyword appears as written, else the share of its pieces present.

    English words must match whole words, so "gym" does not match "gymnastics".
    """
    key = keyword_key(keyword)
    if key and not key.isascii() and key in hay.compact:
        return 1.0
    pieces = keyword_pieces(keyword)
    if not pieces:
        return 0.0
    hits = sum(1 for p in pieces if (p in hay.words if p.isascii() else p in hay.compact))
    return hits / len(pieces)


def is_on_topic(sample: Dict[str, Any], keywords: Sequence[str]) -> bool:
    hay = _Hay(sample)
    return any(relevance(k, hay) >= RELEVANT_AT for k in keywords)


def niche_matches(keyword: str, niches: Sequence[str]) -> List[str]:
    """The niches of the creator profile that ``keyword`` fits, in the order they were given.

    The same matching that ties a keyword to a video (``relevance``, RELEVANT_AT), tried both ways, since
    either may be the longer: 秋冬护肤 holds the niche 护肤, and the keyword 探店 is held by the niche 美食探店.
    It compares characters and whole English words. It knows nothing of meaning: 面霜 fits no niche called 护肤.
    """
    held = _Hay({"title": keyword})
    return [niche for niche in niches if isinstance(niche, str) and niche.strip()
            and max(relevance(niche, held), relevance(keyword, _Hay({"title": niche}))) >= RELEVANT_AT]


# ---- what the automatic sources saw ------------------------------------------
def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _whole(value: Any, low: int, high: int) -> Optional[int]:
    """A stored or reported whole number from ``low`` to ``high``; None for anything else."""
    number = _finite(value)
    return int(number) if number is not None and number == int(number) and low <= number <= high else None


def _date(now: float) -> Optional[datetime.date]:
    """The day ``now`` is in, by this computer's clock; None for a time this system has no calendar for."""
    try:
        return datetime.date.fromtimestamp(now)
    except (OverflowError, OSError, ValueError):
        return None


def _stored_day(value: Any) -> Optional[datetime.date]:
    """A day as this module writes it (``2026-11-22``); None for anything else that is stored there."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _mark(row: Any) -> str:
    """A short mark of a keyword's row as it stands: it differs once anything on the row was changed.

    An undo is handed this mark and not a copy of the row. A row whose mark has changed since is somebody's
    newer work (the creator typed the keyword or gave it a number, a lookup found it again) and is left alone.
    """
    said = json.dumps([row["source"], row["heat"], row["source_url"], row["note_zh"], row["note_en"], repr(row["updated_at"])],
                      ensure_ascii=False)
    return hashlib.sha256(said.encode("utf-8", "replace")).hexdigest()[:16]


def prepare_found(items: Sequence[Dict[str, Any]], *, row_source: str, seen_by: str, now: float, origin: str = "",
                  link: str = "") -> Dict[str, Any]:
    """What storing the keywords a source found would write, and how to take it back. Nothing is stored here.

    ``items`` are ``{"keyword"}`` with, when the source gave them, ``source_url``, ``seen_at`` (when the
    evidence dates from; never after ``now``), ``note_zh``, ``note_en``, ``heat`` and ``rank``. ``row_source``
    is what a new row is marked with (AI or FILE, never MANUAL: nothing automatic is the creator's own
    entry); ``seen_by`` is one of SEEN_BY; ``origin`` says which page or which file; ``link`` is the source
    link for an item that names none. ``now`` is the caller's clock.

    Returns ``{"rows": [(row, sighting)], "undo": {"trends": [[id, mark]], "sightings": [id]}}``. The ids are
    made here, so that a run can write down how to reverse the change (``undo``, for ``remove_found``) before
    it makes it (``store_found``). At most MAX_KEYWORDS_PER_PASTE keywords are taken, each once.
    """
    if row_source not in (AI, WEB_PAGE, FILE) or seen_by not in SEEN_BY:
        raise ValueError("an automatic source stores keywords as %r, %r or %r, seen by one of %r"
                         % (AI, WEB_PAGE, FILE, SEEN_BY))
    day = _date(now)
    if day is None:
        raise ValueError("no day can be told from the time %r" % (now,))
    rows: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    taken = set()
    for item in items:
        if len(rows) == MAX_KEYWORDS_PER_PASTE:
            break
        keyword = clean_keyword(item.get("keyword")) if isinstance(item, dict) and isinstance(item.get("keyword"), str) else ""
        key = keyword_key(keyword)
        if not key or len(keyword) > MAX_KEYWORD_CHARS or key in taken:
            continue
        taken.add(key)
        seen_at = _finite(item.get("seen_at"))
        seen_at = float(now) if seen_at is None or seen_at <= 0 or seen_at > now else seen_at
        source_url = str(item.get("source_url") or link or "")[:1000]
        row = {"id": uuid.uuid4().hex[:12], "key": key, "keyword": keyword, "heat": None, "source": row_source,
               "source_url": source_url, "note_zh": str(item.get("note_zh") or "")[:500],
               "note_en": str(item.get("note_en") or "")[:500], "created_at": float(now), "updated_at": seen_at}
        sighting = {"id": uuid.uuid4().hex[:12], "key": key, "keyword": keyword, "source": seen_by,
                    "origin": str(origin or "")[:MAX_ORIGIN_CHARS], "link": source_url, "seen_on": day.isoformat(),
                    "seen_at": seen_at, "heat": _whole(item.get("heat"), 1, 10 ** 13 - 1),
                    "rank": _whole(item.get("rank"), 1, MAX_RANK), "created_at": float(now)}
        rows.append((row, sighting))
    return {"rows": rows, "undo": {"trends": [[row["id"], _mark(row)] for row, _ in rows],
                                   "sightings": [sighting["id"] for _, sighting in rows]}}


def store_found(store: Any, prepared: Dict[str, Any]) -> Dict[str, Any]:
    """Store what ``prepare_found`` laid out, in one step. Rows are only ever added; none is changed or removed.

    A keyword that is not tracked becomes a row (unverified, without a heat number) and gets its sighting.
    One that is tracked already gets the sighting only: its row stays exactly as it is, also when the
    creator typed it. When the list is full (``store.MAX_TRENDS``) a new keyword is left out with its
    sighting: an automatic run pushes nothing out to make room.

    Returns ``{"found", "added", "seen_again", "left_out_full", "keywords"}``: how many keywords there were,
    how many became rows, how many were tracked already, how many found no room, and the ones that were added.
    Of the sightings the newest MAX_SIGHTINGS are kept; the oldest go first.
    """
    added: List[str] = []
    again = full = 0
    with contextlib.closing(store._db()) as db, db:
        db.execute("BEGIN IMMEDIATE")           # looking a keyword up and adding it are one step, as in ``upsert_trends``
        room = MAX_TRENDS - int(db.execute("SELECT COUNT(*) FROM trends").fetchone()[0])
        for row, sighting in prepared["rows"]:
            if db.execute("SELECT 1 FROM trends WHERE key = ?", (row["key"],)).fetchone() is not None:
                again += 1
            elif room <= 0:
                full += 1
                continue
            else:
                db.execute("INSERT INTO trends (id, key, keyword, heat, source, source_url, note_zh, note_en, created_at,"
                           " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           tuple(row[name] for name in ("id", "key", "keyword", "heat", "source", "source_url", "note_zh",
                                                        "note_en", "created_at", "updated_at")))
                room -= 1
                added.append(row["keyword"])
            db.execute("INSERT INTO trend_observations (id, key, keyword, source, origin, link, seen_on, seen_at, heat, rank,"
                       " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       tuple(sighting[name] for name in ("id", "key", "keyword", "source", "origin", "link", "seen_on",
                                                         "seen_at", "heat", "rank", "created_at")))
        db.execute("DELETE FROM trend_observations WHERE id NOT IN"
                   " (SELECT id FROM trend_observations ORDER BY rowid DESC LIMIT ?)", (MAX_SIGHTINGS,))
    return {"found": len(prepared["rows"]), "added": len(added), "seen_again": again, "left_out_full": full, "keywords": added}


def remove_found(store: Any, undo: Any) -> Dict[str, int]:
    """Take back what one ``store_found`` wrote, from the ``undo`` that ``prepare_found`` gave for it.

    Its sightings are removed. A row it added is removed when it still stands as it was added. It is kept
    when anything on it was changed since (the creator typed the keyword, which makes it theirs; gave it a
    number; a later lookup found it again): that is somebody's newer work. A keyword that was tracked before
    was never this run's row and is not touched. Whatever ``undo`` holds, nothing else is removed: an id
    that names no row, or a row whose mark does not fit, is passed over.

    Returns ``{"removed", "kept", "sightings"}``. Doing it a second time removes nothing more.
    """
    held = undo if isinstance(undo, dict) else {}
    pairs = [pair for pair in (held.get("trends") if isinstance(held.get("trends"), list) else [])
             if isinstance(pair, list) and len(pair) == 2 and all(isinstance(part, str) for part in pair)]
    ids = [one for one in (held.get("sightings") if isinstance(held.get("sightings"), list) else []) if isinstance(one, str)]
    removed = kept = gone = 0
    with contextlib.closing(store._db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for trend_id, mark in pairs[:MAX_KEYWORDS_PER_PASTE]:
            row = db.execute("SELECT * FROM trends WHERE id = ?", (trend_id,)).fetchone()
            if row is None:
                continue
            if _mark(row) != mark:
                kept += 1
                continue
            db.execute("DELETE FROM trends WHERE id = ?", (trend_id,))
            removed += 1
        for sighting_id in ids[:MAX_KEYWORDS_PER_PASTE]:
            gone += db.execute("DELETE FROM trend_observations WHERE id = ?", (sighting_id,)).rowcount
    return {"removed": removed, "kept": kept, "sightings": gone}


def _rising(seen: Dict[str, Any], first_read: Dict[Tuple[str, str], datetime.date], today: datetime.date) -> Optional[str]:
    """Why a keyword counts as rising (``heat``, ``rank`` or ``new``), or None. The rule is in the module's words.

    A judgement call like the weights: nothing here was compared with what then happened to a keyword.
    """
    def recent(day: datetime.date) -> bool:
        return (today - day).days < RISING_WINDOW_DAYS

    if sum(1 for day in seen["days"] if recent(day)) < RISING_MIN_DAYS:
        return None
    for what, grew in (("heat", lambda first, last: last >= RISING_HEAT_RATIO * first),
                       ("rank", lambda first, last: first - last >= RISING_RANK_PLACES)):
        for by_day in seen["places"].values():
            shown = [by_day[day][what] for day in sorted(by_day) if recent(day) and by_day[day][what] is not None]
            if len(shown) >= 2 and grew(shown[0], shown[-1]):
                return what
    first = min(seen["days"])
    if recent(first) and any(place in first_read and first_read[place] < first for place in seen["places"]):
        return "new"
    return None


def sightings(store: Any, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """What the automatic sources saw in the last SIGHTING_DAYS, by keyword in its comparison form (``keyword_key``).

    ``{"sightings", "seen_by", "first_seen_on", "last_seen_on", "last_seen_at", "heat", "rank", "rising"}``:
    on how many different days it was seen and by which kinds of source; the first and last of those days;
    when its newest evidence dates from; the number and the place in a list that a source last showed for it
    (None when none did); and why it counts as rising (``_rising``), or None.

    Reads only, the newest MAX_SIGHTINGS rows. The table may come out of a restored backup with anything in
    it: a row that is not as ``store_found`` writes it is passed over, and so is one dated after tomorrow.
    """
    now = time.time() if now is None else now
    today = _date(now)
    if today is None:
        return {}
    with contextlib.closing(store._db()) as db:
        rows = db.execute("SELECT key, source, origin, seen_on, seen_at, heat, rank FROM trend_observations"
                          " ORDER BY rowid DESC LIMIT ?", (MAX_SIGHTINGS,)).fetchall()
    seen: Dict[str, Dict[str, Any]] = {}
    first_read: Dict[Tuple[str, str], datetime.date] = {}      # the first day each page, file or lookup showed anything
    for row in reversed(rows):                                  # the oldest first: of two on one day the later one stands
        key, source, origin, day = row["key"], row["source"], row["origin"], _stored_day(row["seen_on"])
        if not isinstance(key, str) or not key or source not in SEEN_BY or not isinstance(origin, str) or day is None:
            continue
        if not -1 <= (today - day).days < SIGHTING_DAYS:
            continue
        day = min(day, today)
        place = (source, origin)
        if place not in first_read or day < first_read[place]:
            first_read[place] = day
        entry = seen.setdefault(key, {"days": set(), "by": set(), "places": {}, "at": None, "heat": None, "rank": None})
        entry["days"].add(day)
        entry["by"].add(source)
        at = _finite(row["seen_at"])
        if at is not None and 0 < at:
            entry["at"] = max(entry["at"] or 0.0, min(at, now))
        shown = entry["places"].setdefault(place, {}).setdefault(day, {"heat": None, "rank": None})
        heat, rank = _whole(row["heat"], 1, 10 ** 13 - 1), _whole(row["rank"], 1, MAX_RANK)
        if heat is not None:
            shown["heat"] = entry["heat"] = heat
        if rank is not None:
            shown["rank"] = entry["rank"] = rank
    return {key: {"sightings": len(entry["days"]), "seen_by": sorted(entry["by"]),
                  "first_seen_on": min(entry["days"]).isoformat(), "last_seen_on": max(entry["days"]).isoformat(),
                  "last_seen_at": entry["at"], "heat": entry["heat"], "rank": entry["rank"],
                  "rising": _rising(entry, first_read, today)}
            for key, entry in seen.items()}


# ---- scoring ----------------------------------------------------------------
def _median(sorted_values: List[float]) -> Optional[float]:
    n = len(sorted_values)
    if not n:
        return None
    mid = n // 2
    return sorted_values[mid] if n % 2 else (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _log_ratio(total: float, baseline: float) -> float:
    if total <= 0:
        return -LOG_CAP
    return max(-LOG_CAP, min(LOG_CAP, math.log2(total / baseline)))


def _flags(keyword: str, dna: Dict[str, Any], lexicon: Dict[str, Any],
           compiled: Optional[guardrails.CompiledRules] = None) -> List[str]:
    """"voice" if the keyword itself is something she never says; "compliance" if it is risky ad wording."""
    script = {"script_lines": [{"line_no": 1, "spoken_script_zh": keyword}]}
    flags = set()
    for finding in guardrails.lint_script(script, dna, lexicon=lexicon, compiled=compiled):
        if finding.severity != guardrails.ERROR:
            continue
        if finding.category == guardrails.COMPLIANCE_RISK:
            flags.add("compliance")
        elif finding.category in (guardrails.GENERIC_XHS_SLOP, guardrails.BOOKISH_MANDARIN, guardrails.TRANSLATIONESE):
            flags.add("voice")
    return sorted(flags)


def score_trends(trend_rows: List[Dict[str, Any]], samples: List[Dict[str, Any]], dna: Optional[Dict[str, Any]] = None,
                 weight: str = "balanced", now: Optional[float] = None,
                 lexicon: Optional[Dict[str, Any]] = None, seen: Optional[Dict[str, Dict[str, Any]]] = None,
                 niches: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Rank trend keywords by how hot they are and how her own videos on the topic performed.

    Only non-sponsored videos count as her evidence. Keywords that clash with
    her voice profile stay in the list but are marked ``usable: False``.

    ``lexicon`` is the word list each keyword is checked with. The app passes the one the creator keeps
    (``payload`` does); given none, the shipped list is read as it stands.

    ``seen`` is what the automatic sources saw (``sightings``), and ``niches`` the niches of the creator
    profile; ``payload`` passes both. From them each keyword gets ``unverified`` (anything but the creator's
    own entry), ``sightings``, ``seen_by``, ``first_sighted_on``, ``last_sighted_on``, ``sighted_heat`` and
    ``sighted_rank`` (what a source last showed; never part of the score), ``rising`` with ``rising_why``,
    and ``niche_match`` with ``niches_matched`` (``niche_match`` is None when the profile lists no niche).
    Of these only one thing reaches the score: an unverified keyword is as fresh as its newest sighting
    (``last_seen_at``; see the module's words).
    """
    now = time.time() if now is None else now
    weight = weight if weight in WEIGHTS else "balanced"
    w_trend, w_mine = WEIGHTS[weight]
    hays = [(s, _Hay(s), corpus.interactions(s), s.get("views")) for s in samples if not s.get("is_sponsored")]
    totals = sorted(float(total) for _, _, total, _ in hays if total is not None)
    baseline = _median(totals)
    if not baseline and totals:
        baseline = sum(totals) / len(totals)
    baseline = baseline or None

    view_totals = sorted(float(views) for _, _, _, views in hays if views is not None)
    views_baseline = _median(view_totals)
    if not views_baseline and view_totals:
        views_baseline = sum(view_totals) / len(view_totals)
    views_baseline = views_baseline or None

    heats = [t["heat"] for t in trend_rows if t.get("heat")]
    top_heat = max(heats) if heats else 0

    def strength_of(row: Dict[str, Any]) -> Optional[float]:
        return math.log1p(row["heat"]) / math.log1p(top_heat) if (row.get("heat") and top_heat) else None

    known = sorted(s for s in (strength_of(t) for t in trend_rows) if s is not None)
    default_strength = _median(known) if known else 1.0
    compiled: Optional[guardrails.CompiledRules] = None
    if dna:
        if lexicon is None:
            lexicon = wordlist.shipped()
        compiled = guardrails.compile_rules(dna, lexicon=lexicon)

    scored = []
    listed = [niche for niche in (niches or []) if isinstance(niche, str) and niche.strip()]
    for row in trend_rows:
        keyword = row["keyword"]
        strength = strength_of(row)
        heat_known = strength is not None
        sighted = (seen or {}).get(keyword_key(keyword)) or {}
        unverified = (row.get("source") or MANUAL) != MANUAL
        last_seen = row.get("updated_at") or now
        if unverified and sighted.get("last_seen_at"):
            last_seen = max(last_seen, sighted["last_seen_at"])
        fits = niche_matches(keyword, listed)
        age_days = max(0.0, (now - last_seen) / 86400.0)
        freshness = 0.5 ** (age_days / HALF_LIFE_DAYS)
        trend = (strength if heat_known else default_strength) * freshness

        matched = [(relevance(keyword, hay), sample, total, views) for sample, hay, total, views in hays]
        matched = [m for m in matched if m[0] >= RELEVANT_AT]
        log_ratios: List[Tuple[float, float]] = []
        for rel, _, total, views in matched:
            if total is not None and baseline:
                log_ratios.append((rel, _log_ratio(float(total), baseline)))
            elif total is None and views is not None and views_baseline:
                log_ratios.append((rel, _log_ratio(float(views), views_baseline)))
        with_numbers = sum(1 for _, _, total, views in matched if total is not None or views is not None)
        lift_raw, shrunk = None, 0.0
        if log_ratios:
            mass = sum(rel for rel, _ in log_ratios)
            mean_log = sum(rel * lr for rel, lr in log_ratios) / mass
            lift_raw = 2 ** mean_log
            shrunk = mass * mean_log / (mass + PRIOR_VIDEOS)
        performance = min(1.0, max(0.0, 0.5 + shrunk / 4.0))
        score = w_trend * trend + w_mine * performance

        matched.sort(key=lambda m: (m[0], m[2] if m[2] is not None else -1, m[3] if m[3] is not None else -1), reverse=True)
        flags = _flags(keyword, dna, lexicon, compiled=compiled) if dna else []
        scored.append((score, {
            "id": row.get("id"), "keyword": keyword, "heat": row.get("heat"), "heat_known": heat_known,
            "source": row.get("source") or "manual", "source_url": row.get("source_url") or "",
            "note_zh": row.get("note_zh") or "", "note_en": row.get("note_en") or "",
            "updated_at": row.get("updated_at"), "age_days": round(age_days, 1),
            "freshness": round(freshness, 3), "stale": freshness < STALE_BELOW, "trend": round(trend, 3),
            "her_videos": len(matched), "her_videos_with_numbers": with_numbers,
            "evidence_titles": [sample["title"] for _, sample, _, _ in matched[:3]],
            "lift_raw": round(lift_raw, 2) if lift_raw is not None else None, "lift": round(2 ** shrunk, 2),
            "performance": round(performance, 3), "score": int(round(score * 100)),
            "flags": flags, "usable": "voice" not in flags,
            "unverified": unverified, "last_seen_at": last_seen,
            "sightings": sighted.get("sightings", 0), "seen_by": sighted.get("seen_by", []),
            "first_sighted_on": sighted.get("first_seen_on"), "last_sighted_on": sighted.get("last_seen_on"),
            "sighted_heat": sighted.get("heat"), "sighted_rank": sighted.get("rank"),
            "rising": bool(sighted.get("rising")), "rising_why": sighted.get("rising"),
            "niche_match": bool(fits) if listed else None, "niches_matched": fits,
        }))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["keyword"]))
    return {
        "trends": [item for _, item in scored], "weight": weight,
        "weights": {"trend": w_trend, "mine": w_mine},
        "baseline_interactions": baseline, "videos_with_numbers": len(totals),
        "half_life_days": HALF_LIFE_DAYS, "prior_videos": PRIOR_VIDEOS,
        "niches": listed, "sighting_days": SIGHTING_DAYS,
        "rising_rule": {"window_days": RISING_WINDOW_DAYS, "min_days": RISING_MIN_DAYS,
                        "heat_ratio": RISING_HEAT_RATIO, "rank_places": RISING_RANK_PLACES},
    }


def payload(store: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Scored trends for the active library, voice profile and weighting preference.

    A keyword containing a phrase the creator refuses to say clashes with their voice like any other banned phrase.

    Keywords are checked with the word list as the creator keeps it (``wordlist.effective``), so an entry they
    switched off no longer marks a keyword. ``wordlist`` in the answer says how many are switched off, with the
    list's disclaimer (``wordlist.notice``).

    Each keyword also says what the automatic sources saw of it and whether it fits a niche of the creator
    profile (``score_trends``); ``niches``, ``sighting_days`` and ``rising_rule`` in the answer say what
    those marks were worked out from. Reads only.
    """
    now = time.time() if now is None else now
    who = store.load_creator()
    dna, _ = store.load_dna()
    dna = creator.apply_never(dna, who)
    weight = store.load_config().get("trend_weight") or "balanced"
    lexicon = wordlist.effective(store)
    scored = score_trends(store.list_trends(), store.list_samples(), dna, weight, now, lexicon,
                          seen=sightings(store, now), niches=creator.prompt_block(who)["niches"])
    scored["wordlist"] = wordlist.notice(lexicon)
    return scored


# ---- did a script use its keywords? -----------------------------------------
def coverage(keywords: Sequence[str], lines: Any, post: Any) -> List[Dict[str, Any]]:
    """Where each target keyword appears as written: spoken lines, title options, caption, hashtags."""
    post = post if isinstance(post, dict) else {}
    spoken = keyword_key(" ".join(
        textstats.strip_cues(str(line.get("spoken_script_zh", "")))
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, dict) and str(line.get("audio_mode", "")).strip().upper() != "LIVE_CLIP"))
    options = post.get("title_options") if isinstance(post.get("title_options"), list) else []
    titles = keyword_key(" ".join(
        str(o.get("title_zh", "")) if isinstance(o, dict) else (o if isinstance(o, str) else "")
        for o in options
    ) + " " + str(post.get("cover_text_zh") or "") + " " + str(post.get("cover_sub_zh") or ""))
    caption = keyword_key(str(post.get("body_zh") or post.get("caption_body_zh") or ""))
    raw_tags = post.get("hashtags") if isinstance(post.get("hashtags"), list) and post.get("hashtags") else post.get("hashtags_zh")
    tags = raw_tags if isinstance(raw_tags, list) else []
    hashtags = keyword_key(" ".join(str(tag) for tag in tags).replace("#", " ").replace("\uff03", " "))
    out = []
    for keyword in keywords:
        key = keyword_key(keyword)
        found = {"spoken": key in spoken, "title": key in titles, "caption": key in caption, "hashtags": key in hashtags}
        out.append(dict(found, keyword=keyword, covered=any(found.values())))
    return out
