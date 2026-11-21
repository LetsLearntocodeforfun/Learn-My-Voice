"""A script that was posted joins the library, with its numbers.

The loop this closes: a script is drafted and edited here, filmed and posted, and the numbers the video then
gets are typed in. "Mark as posted" makes ONE library item from the script as it stands and links the two.
Everything that ranks by the creator's own results (the usual result, how a topic did, the lift of a trend
keyword, what the ideas prompt is told) reads the library, so it now sees what was made here as well.

What a posted item counts for, and what it does not:

- Its NUMBERS count at once, wherever numbers are used.
- Its WORDING does not, by default. The text began in this app, often as an AI's draft, and a model that is
  shown its own sentences as "how the creator talks" drifts towards itself. So the item is stored with where
  it came from (``origin``) and it waits (``needs_review``), the way a machine transcript nobody has read
  waits. That is the one mechanism that already keeps wording out of the measured habits, the material a
  voice is learned from, the examples a draft is anchored on, the voice-match reference and the creator's own
  openings (``pipeline.confirmed_samples``, ``corpus.waits_as_posted_script``). Nothing here remembers a
  lesson either: lessons are remembered one at a time, by the creator, on the script page.
- The creator makes the wording count by an act of their own: confirming "this is what I said, in my words"
  (the confirmation a transcript gets, ``Store.update_sample``), or putting the words of the posted video in
  the text's place, from a subtitle file or a transcription (``attach_transcript``). Before they confirm, the
  page says how many of the item's lines are still exactly as an AI wrote them (``wording_facts``): counted
  here, from what the AI wrote and the lines as they stand, never a model's opinion.
- An opening that is still an AI's sentence is never among the creator's own openings, confirmed or not
  (``hooks.opening_by_ai``).

Numbers keep changing for days after a video goes out. The day they were read is kept beside them, and the
pages say how many days after posting that was (``corpus.read_after_days``). Nothing is adjusted, projected
or normalised: numbers read after 2 days and numbers read after 60 are shown as what they are.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import corpus, learning, pipeline, textstats, voicematch
from .store import MAX_SAMPLE_CHARS, METRIC_FIELDS, MODE_SPONSORED, ORIGIN_SCRIPT, StaleScript, Store, TextTooLong, _metric

WAITING, CONFIRMED, SPOKEN, TRANSCRIPT_WAITING = "waiting", "confirmed", "spoken", "transcript_waiting"

# Refusals: code -> (HTTP status, English, Chinese). Both sentences are written to be shown as they are.
REFUSALS = {
    "not_found": (404, "There is no such script.", "没有这份脚本。"),
    "not_posted": (409, "This script is not marked as posted: its library item is no longer there. Nothing was changed.",
                   "这篇脚本现在没有标记为已发布：素材库里对应的那一条已经不在了。这次没有改动任何内容。"),
    "nothing_spoken": (400, "This script has no spoken lines yet, so there is nothing to put in the library.",
                       "这篇脚本还没有口播内容，没有可以放进素材库的文字。"),
    "bad_date": (400, "A date has to be given as year-month-day, for example 2026-11-16.",
                 "日期要写成「年-月-日」，例如 2026-11-16。"),
    "date_in_future": (400, "That date has not come yet. The day a video was posted and the day its numbers were read "
                            "can be today at the latest.", "这个日期还没到。发布日期和看数据的日期最晚只能是今天。"),
    "read_before_posted": (400, "The numbers cannot have been read before the video was posted. Check the two dates.",
                           "看数据的日期不能早于发布日期。请核对这两个日期。"),
    "bad_link": (400, "The link has to be one address that begins with http:// or https://, with no spaces in it. "
                      "It is kept as text only; the studio never opens it.",
                 "链接只能是一个以 http:// 或 https:// 开头的网址，中间不能有空格。工作台只把它当文字保存，不会去打开它。"),
    "bad_base_updated_at": (400, "base_updated_at has to be the updated_at of a script", "base_updated_at 必须是脚本的 updated_at"),
    "holds_transcript": (409, "The library item now holds what was said in the video (from its subtitle file or a "
                              "transcript), so it is not replaced with the script's text. Nothing was changed.",
                         "素材库里的这一条现在存的是视频里实际说的话（来自字幕文件或转写），所以不会用脚本的文字去替换。这次没有改动任何内容。"),
    "bad_attach": (404, "The posted item these words belong to is not in the library any more, so they were not added.",
                   "这些文字要附到的那条已发布素材已经不在素材库里了，所以没有加进去。"),
    "attach_needs_words": (400, "Only the words of the posted video can be attached: a subtitle file, or the video "
                                "itself to be transcribed on this computer.",
                           "这里只能附上视频里实际说的话：字幕文件，或者让这台电脑转写视频本身。"),
}


class PostedError(ValueError):
    """A request about a posted script that cannot be met. Nothing was changed.

    ``code`` is one of REFUSALS; ``status`` is the HTTP status for it; ``message`` (English) and
    ``message_zh`` are written to be shown.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.message, self.message_zh = REFUSALS[code]
        super().__init__(self.message)


def today() -> datetime.date:
    """Today by this computer's clock. The app and the creator's browser run on the same machine, so it is their today."""
    return datetime.date.today()


def _day(value: Any, default: Optional[datetime.date] = None) -> str:
    """A day from a request, as ``YYYY-MM-DD``: a day that exists and is not after today.

    A value left out or empty gives ``default`` when there is one. Raises ``PostedError``: ``bad_date``,
    ``date_in_future``.
    """
    if value is None or value == "":
        if default is None:
            raise PostedError("bad_date")
        return default.isoformat()
    day = corpus.parse_day(value)
    if day is None:
        raise PostedError("bad_date")
    if day > today():
        raise PostedError("date_in_future")
    return day.isoformat()


def clean_link(value: Any) -> str:
    """The link to the posted note as it is kept: text, an http(s) address with a host, or "" for none.

    The app keeps it and shows it; it never opens or fetches it. Raises ``PostedError("bad_link")`` for
    anything else: another scheme, more than an address (a pasted share text), spaces or control characters.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PostedError("bad_link")
    link = value.strip()
    if not link:
        return ""
    if any(ch.isspace() or not ch.isprintable() for ch in link) or not pipeline._is_web_link(link):
        raise PostedError("bad_link")
    return link


def script_text(lines: Any) -> str:
    """The spoken lines of a script as the library stores a script's text: one line of the script on each line.

    A live clip is somebody else's recorded words and is left out, as wherever a script's own words are
    counted (``voicematch.script_text_lines``). A line break inside a line becomes the breath mark " / ",
    which is what it means there, so that one line of the script stays one line of the item.
    """
    out = []
    for text in voicematch.script_text_lines(lines):
        parts = [part.strip() for part in text.splitlines() if part.strip()]
        if parts:
            out.append(" / ".join(parts))
    return "\n".join(out)


def _has_words(text: str) -> bool:
    return textstats.spoken_units(textstats.strip_cues(text)) > 0


def _fingerprint(title: str, text: str) -> str:
    """Identifies a script's title and spoken text, to tell later whether the script was edited after its item was made."""
    return hashlib.sha256(json.dumps([title, text]).encode("ascii")).hexdigest()


def _ai_draft(store: Store, script: Dict[str, Any]) -> Dict[str, Any]:
    """What an AI wrote for this script, to be kept with its library item: ``{"drafted", "lines", "proposed"}``.

    ``drafted``: whether the script began as an AI's draft. ``lines``: the spoken lines of that draft as the
    AI wrote them, the same draft the creator's edits are compared with everywhere else
    (``Store.get_ai_lines``). ``proposed``: the proposals an AI made for single lines whose words stand in
    the script now (``Store.list_ai_suggestions``). It is kept with the item and not looked up when it is
    needed, because the item outlives the script.
    """
    ai_lines = store.get_ai_lines(script["id"])
    drafted = [text for text in voicematch.script_text_lines(ai_lines or []) if text.strip()]
    standing = {learning.spoken_key(text) for text in voicematch.script_text_lines(script["lines"])} - {""}
    proposed: List[str] = []
    seen = set()
    for text in store.list_ai_suggestions(script["id"]):
        key = learning.spoken_key(text)
        if key in standing and key not in seen:
            seen.add(key)
            proposed.append(text)
    return {"drafted": ai_lines is not None, "lines": drafted, "proposed": proposed}


def _as_lines(texts: Any) -> List[Dict[str, Any]]:
    return [{"spoken_script_zh": text} for text in (texts if isinstance(texts, (list, tuple)) else []) if isinstance(text, str)]


def wording_facts(sample: Dict[str, Any]) -> Dict[str, Any]:
    """How a posted item's wording stands: ``{"wording", "lines", "lines_by_ai", "drafted"}``.

    ``wording`` is WAITING (a script's text the creator has not said is theirs), CONFIRMED (they have),
    SPOKEN (the words of the posted video: a subtitle file, or a transcript they confirmed) or
    TRANSCRIPT_WAITING (a machine transcript of the posted video nobody has read yet).

    ``lines`` and ``lines_by_ai`` are counted for a script's text: how many of its lines have spoken words,
    and how many of those are still exactly as an AI wrote them (``learning.lines_by_ai``, from the AI's
    lines kept with the item and the item's text as it is now, so an edit made in the library is counted).
    They are None once the text is the words of the video. ``drafted``: whether the script began as an AI's draft.
    """
    draft = sample.get("ai_draft") if isinstance(sample.get("ai_draft"), dict) else {}
    found: Dict[str, Any] = {"lines": None, "lines_by_ai": None, "drafted": bool(draft.get("drafted"))}
    if sample.get("kind", "script") != "script":
        found["wording"] = TRANSCRIPT_WAITING if sample.get("needs_review") else SPOKEN
        return found
    marks = learning.lines_by_ai(_as_lines(draft.get("lines")), _as_lines(str(sample.get("text") or "").split("\n")),
                                 [text for text in draft.get("proposed") or [] if isinstance(text, str)])
    found.update({"wording": WAITING if sample.get("needs_review") else CONFIRMED,
                  "lines": len(marks), "lines_by_ai": sum(1 for _, by_ai in marks if by_ai)})
    return found


def from_brief(script: Dict[str, Any]) -> bool:
    """Whether a script was drafted from a brand's brief (``studio.sponsored``): its ``mode`` says so, for good.

    The library item of such a script is sponsored whatever a request to mark it as posted says. Being
    sponsored is what keeps an item out of the measured habits, the material a voice is learned from, the
    examples a draft is anchored on, the voice-match reference and the creator's own openings; a script
    written to a brief holds wording the brand asked for, and that must not arrive there as the creator's.
    In the library the item is the creator's like any other, and the tick there stays theirs to change.
    """
    return script.get("mode") == MODE_SPONSORED


def facts(sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the pages show about a library item that came from a script, or None for any other item.

    ``posted_on``, ``note_url``, ``metrics_on`` (the day the numbers were read, None while there are none),
    ``read_after_days``, ``has_numbers``, ``confirmed_at`` (when the wording began to count, else None) and
    everything ``wording_facts`` gives.
    """
    if sample.get("origin") != ORIGIN_SCRIPT:
        return None
    out = {"posted_on": sample.get("posted_on"), "note_url": sample.get("note_url") or "",
           "metrics_on": sample.get("metrics_on"), "read_after_days": corpus.read_after_days(sample),
           "has_numbers": any(sample.get(field) is not None for field in METRIC_FIELDS),
           "confirmed_at": None if sample.get("needs_review") else sample.get("reviewed_at")}
    out.update(wording_facts(sample))
    return out


# ---- what the pages are sent ----------------------------------------------------------------------------------
def shown(store: Store, sample: Dict[str, Any], titles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """A library item as a page gets it: with ``posted`` (``facts``, plus ``script_id`` and ``script_title`` of the
    script it came from while that script exists), and without the AI's lines kept with it, which are for counting."""
    out = {key: value for key, value in sample.items() if key not in ("ai_draft", "snapshot")}
    info = facts(sample)
    if info is not None:
        linked = sample.get("script_id")
        if linked and titles is None:
            titles = store.script_titles()
        linked = linked if (linked and linked in (titles or {})) else None       # a link to a script that is gone is no link
        info.update({"script_id": linked, "script_title": (titles or {}).get(linked) if linked else None})
    out["posted"] = info
    return out


def for_page(store: Store, samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every library item as the pages get it (``shown``)."""
    titles = store.script_titles() if any(sample.get("script_id") for sample in samples) else {}
    return [shown(store, sample, titles) for sample in samples]


def for_script(store: Store, script: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the script page shows about the script's library item, or None for a script that is not marked as posted.

    ``facts`` of the item, with ``sample_id``, the item's ``title``, ``is_sponsored`` and numbers, and
    ``text_differs``: true when the item still holds a script's text and the script's title or spoken lines
    are no longer what they were when that text was taken. The item is not changed by editing the script:
    it holds the text as posted until the creator asks for it to be taken again (``mark`` with ``refresh_text``).
    """
    item = store.get_posted_sample(script["id"])
    if item is None:
        return None
    info = facts(item) or {}
    info.update({"sample_id": item["id"], "title": item["title"], "is_sponsored": item["is_sponsored"]})
    info.update({field: item[field] for field in METRIC_FIELDS})
    info["text_differs"] = (item["kind"] == "script"
                            and item.get("snapshot") != _fingerprint(script["title"], script_text(script["lines"])))
    return info


def with_posted(store: Store, script: Dict[str, Any]) -> Dict[str, Any]:
    """A script as its page gets it: with ``posted`` (``for_script``)."""
    return dict(script, posted=for_script(store, script))


def with_posted_dates(store: Store, scripts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The scripts list with, for each script, ``posted`` (whether it is marked as posted) and ``posted_on`` (the day, or None)."""
    dates = store.posted_dates()
    return [dict(script, posted=script["id"] in dates, posted_on=dates.get(script["id"])) for script in scripts]


# ---- marking, and changing what was marked -------------------------------------------------------------------
def _not_stale(script: Dict[str, Any], base: Any) -> None:
    """Refuse when the caller worked from an older version of the script than the one stored (``StaleScript``).

    What goes into the library is the script as it stands, so it has to be the script the creator was
    looking at. ``base`` is the ``updated_at`` of that version; left out, nothing is compared, as for a save.
    """
    if base is None:
        return
    try:
        usable = not isinstance(base, bool) and isinstance(base, (int, float)) and math.isfinite(float(base))
    except OverflowError:
        usable = False
    if not usable:
        raise PostedError("bad_base_updated_at")
    if script["updated_at"] > float(base):
        raise StaleScript(script["updated_at"])


def _checked_columns(item: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """New values for a posted item's days and link (``store.POSTED_COLUMNS``), from what a request carries.

    Only what the request names is changed, with one exception that is the point of this function: the
    numbers and the day they were read belong together. When a request changes a number and names no day,
    the day becomes today: the number was read now. An item with no number has no such day.

    Raises ``PostedError`` before anything is written: ``bad_date``, ``date_in_future``, ``bad_link``, or
    ``read_before_posted`` when the two days, as they would stand, are the wrong way round.
    """
    columns: Dict[str, Any] = {}
    if "posted_on" in body:
        columns["posted_on"] = _day(body["posted_on"])
    if "note_url" in body:
        columns["note_url"] = clean_link(body["note_url"])
    posted_on = columns.get("posted_on", item.get("posted_on")) or ""
    touched = any(field in body for field in METRIC_FIELDS)
    numbers = [_metric(body[field]) if field in body else item.get(field) for field in METRIC_FIELDS]
    if all(number is None for number in numbers):
        if item.get("metrics_on") is not None:
            columns["metrics_on"] = None
    elif body.get("metrics_on") not in (None, ""):
        columns["metrics_on"] = _day(body["metrics_on"])
    elif touched or not item.get("metrics_on"):
        columns["metrics_on"] = max(today().isoformat(), posted_on)
    metrics_on = columns.get("metrics_on", item.get("metrics_on"))
    if metrics_on and posted_on and metrics_on < posted_on:
        raise PostedError("read_before_posted")
    return columns


def _create(store: Store, script: Dict[str, Any], body: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Make the library item for a script that has none: ``(item, True)``. Everything is checked before it is stored.

    ``(the item that is there, False)`` when another window made it in the same moment: one script has one item.
    """
    text = script_text(script["lines"])
    if not _has_words(text):
        raise PostedError("nothing_spoken")
    posted_on = _day(body.get("posted_on"), default=today())
    metrics = {field: body.get(field) for field in METRIC_FIELDS}
    metrics_on = None
    if any(_metric(value) is not None for value in metrics.values()):
        metrics_on = _day(body.get("metrics_on"), default=today())
        if metrics_on < posted_on:
            raise PostedError("read_before_posted")
    link = clean_link(body.get("note_url"))
    try:
        item = store.create_sample(
            # A script drafted from a brand's brief is sponsored, whatever the request says (``from_brief``).
            title=script["title"], text=text, is_sponsored=bool(body.get("is_sponsored")) or from_brief(script),
            metrics=metrics, kind="script",
            posted={"script_id": script["id"], "posted_on": posted_on, "note_url": link, "metrics_on": metrics_on,
                    "ai_draft": _ai_draft(store, script), "snapshot": _fingerprint(script["title"], text)})
    except sqlite3.IntegrityError:
        existing = store.get_posted_sample(script["id"])
        if existing is None:
            raise
        return existing, False
    return item, True


def _change(store: Store, script: Dict[str, Any], item: Dict[str, Any], body: Dict[str, Any]) -> None:
    """Apply a request to the item a script already has. Everything is checked before anything is written."""
    refresh = body.get("refresh_text") is True
    text = ""
    if refresh:
        if item["kind"] != "script":
            raise PostedError("holds_transcript")
        text = script_text(script["lines"])
        if not _has_words(text):
            raise PostedError("nothing_spoken")
        if len(text) > MAX_SAMPLE_CHARS:
            raise TextTooLong(len(text))
    columns = _checked_columns(item, body)
    changes = {key: body[key] for key in METRIC_FIELDS + ("is_sponsored",) if key in body}
    if "is_sponsored" in changes and from_brief(script):
        changes["is_sponsored"] = True          # marking never makes the item of such a script non-sponsored
    store.update_sample(item["id"], changes, posted=columns)
    if refresh:
        # New text is new wording: it waits again, and what an AI wrote of it is counted again.
        store.replace_sample_text(item["id"], text=text, kind="script", needs_review=True, title=script["title"],
                                  ai_draft=_ai_draft(store, script), snapshot=_fingerprint(script["title"], text))


def mark(store: Store, script_id: Any, body: Dict[str, Any], update_only: bool = False) -> Tuple[Dict[str, Any], bool]:
    """Mark a script as posted, or change what was recorded when it was. Returns ``(the script with "posted", created)``.

    A script that has no library item gets ONE, made from the script as it stands: its title, its spoken
    lines (``script_text``), the sponsored flag, the numbers. A script that has one keeps it: the same
    request then changes that item and never makes a second. ``update_only`` is for "update numbers" and
    "take the text again": it refuses (``not_posted``) when the item is gone, so that a window showing an
    old page cannot quietly mark the script as posted a second time.

    ``body`` may carry, each optional:

    - ``posted_on``: the day it was posted, ``YYYY-MM-DD``; today for a new item; never after today.
    - ``note_url``: the link to the note, an http(s) address kept as text (``clean_link``); "" for none.
    - ``is_sponsored``; ``views``, ``likes``, ``collects``, ``comments``: read as the library reads them
      (a blank or unusable value is "not entered"). The item of a script drafted from a brand's brief is
      sponsored whatever is sent here (``from_brief``).
    - ``metrics_on``: the day the numbers were read; today when numbers are given without it.
    - ``refresh_text`` true, for an item that exists: replace its title and text with the script as it
      stands now. The new text waits for the creator's confirmation again.
    - ``base_updated_at``: the ``updated_at`` of the script version the caller was looking at.

    Raises ``PostedError`` (see REFUSALS), ``StaleScript`` when ``base_updated_at`` names an older version
    than the stored one, and ``TextTooLong``. In each case nothing was changed.
    """
    script = store.get_script(script_id) if isinstance(script_id, str) else None
    if script is None:
        raise PostedError("not_found")
    _not_stale(script, body.get("base_updated_at"))
    item = store.get_posted_sample(script["id"])
    created = False
    if item is None:
        if update_only:
            raise PostedError("not_posted")
        item, created = _create(store, script, body)
    if not created:
        _change(store, script, item, body)
    return with_posted(store, script), created


def update_item(store: Store, sample: Dict[str, Any], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An edit made on the library item itself (``PUT /api/samples/<id>``): ``Store.update_sample``, and for an
    item that came from a script also its days and link (``_checked_columns``). The numbers typed here are the
    numbers the script page shows, and they take the day they were read with them. Raises ``PostedError``."""
    columns = _checked_columns(sample, body) if sample.get("origin") == ORIGIN_SCRIPT else None
    return store.update_sample(sample["id"], body, posted=columns)


def attachable(store: Store, sample_id: Any) -> Dict[str, Any]:
    """The posted item that words of its video may be attached to. Raises ``PostedError("bad_attach")`` when
    ``sample_id`` names no library item, or one that did not come from a script."""
    item = store.get_sample(sample_id) if isinstance(sample_id, str) else None
    if item is None or item.get("origin") != ORIGIN_SCRIPT:
        raise PostedError("bad_attach")
    return item


def attach_transcript(store: Store, sample_id: Any, *, text: str, kind: str, timing: Optional[Dict[str, Any]] = None,
                      source_name: str = "") -> Dict[str, Any]:
    """Put the words of the posted video in the place of the script's text, in the item the script already has.

    The words come in the two ways the library already has for a video's words: a subtitle file (``kind``
    ``subtitle``: the creator's own text, which counts at once) or a transcription on this computer
    (``kind`` ``video``: a machine wrote it down, so it waits for the creator to read it, like any
    transcript). Either way the text is then what was really said, and no second item is made: the title,
    the numbers, the days and the link to the script stay. What an AI wrote for the script stays with the
    item too, so an opening that was read out as the AI wrote it is still not listed as the creator's own.

    Raises ``PostedError``: ``bad_attach``, ``attach_needs_words`` (for any other ``kind``); and ``TextTooLong``.
    """
    if kind not in ("subtitle", "video"):
        raise PostedError("attach_needs_words")
    item = attachable(store, sample_id)
    updated = store.replace_sample_text(item["id"], text=text, kind=kind, timing=timing, source_name=source_name,
                                        needs_review=(kind == "video"))
    if updated is None:
        raise PostedError("bad_attach")
    return updated
