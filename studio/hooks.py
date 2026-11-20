"""The creator's own openings, next to the results of the videos they opened.

No model is involved. An opening is the first breaths of an item in the library; its results are the
interactions the creator entered for that video. Openings are labelled by plain rules (a question, a
number, a time, a turn, speaking to the viewer), so the creator can see at a glance how their videos have
started and which starts came with their better results.

What this does not show: that an opening caused a result. A video's numbers come from its subject, its
pictures, the day it went out and much else. With a handful of videos, a label's middle result describes
what happened, nothing more; the count is always given next to it.

Only non-sponsored material the creator wrote or confirmed is used for the openings, as everywhere wording
is learned from. The creator's usual result is the middle value over their non-sponsored videos with
numbers, the same baseline the trends page uses.

A script written in this app and then posted is in the library too (``studio.posted``). Its numbers count
towards the usual result like any video's. Its opening is the creator's own only when they have confirmed
the item's wording and the opening is not still a sentence an AI wrote. An opening that is still an AI's
sentence is kept apart (``written_by_ai``): it is shown with the video's result and labelled, it is never
among the creator's own openings, and so it never reaches a prompt as the way the creator opens. A model
that is handed its own sentences as the creator's habit drifts towards itself.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import corpus, learning, textstats

OPENING_MAX_BREATHS = 3         # an opening is at most this many breaths
OPENING_ENOUGH_UNITS = 8        # and ends with the breath that brings it to this many spoken units
OPENING_MAX_CHARS = 80          # an opening is shown, and put into a prompt, no longer than this
HEAD_CHARS = 600                # only the start of a text is read for its opening
BEST_FOR_PROMPT = 5             # openings that go into a draft prompt
MIN_FOR_PATTERN = 2             # a label's middle result is given from this many videos with numbers

QUESTION, NUMBER, TIME, TURN, VIEWER, PLAIN = "question", "number", "time", "turn", "viewer", "plain"
PATTERNS = (QUESTION, NUMBER, TIME, TURN, VIEWER, PLAIN)

# The marks a breath ends at: the same ones ``textstats.split_clauses`` splits on. Kept here because the mark
# itself is needed (a question mark makes a question) and split_clauses throws it away. A test compares the two.
_MARK = re.compile(r"([/\n\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026])")
_QUESTION_MARKS = "?\uff1f"
_ASKS = re.compile(r"为什么|怎么|是不是|有没有|难道|到底|多少|哪里|哪个|哪儿|哪些|什么时候")
# A digit, or a Chinese numeral with a measure word. 一 and 几 are left out: 一个 is in nearly every sentence.
_NUMBER = re.compile(r"\d|[两三四五六七八九十百千万]+(?:个|件|款|种|天|次|块|年|月|周|岁|盒|瓶|支|本|条|步|招|样|点|分钟|小时|斤|元|倍)")
_TIME = re.compile(r"今天|昨天|前天|明天|今晚|昨晚|上周|这周|下周|周末|周[一二三四五六日天]|星期|礼拜|早上|上午|中午|下午|晚上|半夜|凌晨"
                   r"|最近|刚刚|刚才|前几天|这几天|去年|今年|上个月|这个月|入冬|入夏|入秋|开春|过年|放假|下班|起床")
_TURN = re.compile(r"结果|没想到|居然|竟然|谁知道|万万没|但是|可是|其实|原来|差点|以为")
_VIEWER = re.compile(r"你|大家|姐妹|各位|朋友们")
_ENDS_ASKING = "吗"


def _head(text: Any) -> Tuple[List[str], List[str]]:
    """The first breaths of ``text`` and the mark each one ended at ("" at the end of the text).

    Up to OPENING_MAX_BREATHS breaths, ending with the one that brings the opening to OPENING_ENOUGH_UNITS
    spoken units. Bracketed cues are not speech; a piece with nothing spoken in it is skipped.
    """
    raw = textstats.strip_cues(text[:HEAD_CHARS]) if isinstance(text, str) else ""
    pieces = _MARK.split(raw)                   # text, mark, text, mark, ..., text
    breaths: List[str] = []
    marks: List[str] = []
    units = 0
    for at in range(0, len(pieces), 2):
        piece = pieces[at].strip()
        spoken = textstats.spoken_units(piece)
        if spoken == 0:
            if breaths and at + 1 < len(pieces) and pieces[at + 1] in _QUESTION_MARKS:
                marks[-1] = pieces[at + 1]      # "真的吗！？": the question mark still belongs to the breath before it
            continue
        breaths.append(piece)
        marks.append(pieces[at + 1] if at + 1 < len(pieces) else "")
        units += spoken
        if units >= OPENING_ENOUGH_UNITS or len(breaths) >= OPENING_MAX_BREATHS:
            if at + 3 < len(pieces) and not pieces[at + 2].strip() and pieces[at + 3] in _QUESTION_MARKS:
                marks[-1] = pieces[at + 3]
            break
    return breaths, marks


def _labels(breaths: List[str], marks: List[str]) -> List[str]:
    text = " ".join(breaths)
    found = []
    if (any(mark and mark in _QUESTION_MARKS for mark in marks) or _ASKS.search(text)
            or any(breath.rstrip().endswith(_ENDS_ASKING) for breath in breaths)):
        found.append(QUESTION)
    for label, rule in ((NUMBER, _NUMBER), (TIME, _TIME), (TURN, _TURN), (VIEWER, _VIEWER)):
        if rule.search(text):
            found.append(label)
    return found or [PLAIN]


def describe(text: Any) -> Dict[str, Any]:
    """``{"opening", "patterns", "units"}`` for the start of a text; ``opening`` is "" when nothing is spoken in it.

    ``patterns`` are labels given by plain rules on the words: ``question`` (a question mark, a question
    word, or a breath ending in 吗), ``number`` (a digit, or a numeral with a measure word), ``time`` (a word
    for when: 今天, 上周, 最近 ...), ``turn`` (结果, 没想到, 其实 ...), ``viewer`` (你, 大家 ...), or ``plain``
    when none applies. They say what the words are, not whether the opening is good.
    """
    breaths, marks = _head(text)
    if not breaths:
        return {"opening": "", "patterns": [], "units": 0}
    return {"opening": " / ".join(breaths)[:OPENING_MAX_CHARS], "patterns": _labels(breaths, marks),
            "units": sum(textstats.spoken_units(breath) for breath in breaths)}


def _middle(values: List[float]) -> Optional[float]:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _times_usual(total: Optional[float], usual: Optional[float]) -> Optional[float]:
    return round(total / usual, 1) if (total is not None and usual) else None


def _lines(texts: Any) -> List[Dict[str, Any]]:
    return [{"spoken_script_zh": text} for text in (texts if isinstance(texts, (list, tuple)) else []) if isinstance(text, str)]


def opening_by_ai(sample: Dict[str, Any], opening: str) -> bool:
    """Whether the opening of an item that came from a script is still a sentence an AI wrote.

    Read off what was kept with the item when its script was posted (``ai_draft``: the lines of the AI's
    draft, and the proposals an AI made that stood in the script), never off a model's opinion. True when:

    - the opening's words stand in a row in the AI's draft or in one of those proposals, however the item's
      text is cut into lines (a subtitle file of the posted video cuts it its own way); or
    - any line the opening takes words from is one an AI wrote (``learning.lines_by_ai``). An opening made of
      a few words of the creator's followed by an AI's sentence is not an opening of the creator's.

    False for an item with nothing kept: one the creator brought in themselves.
    """
    draft = sample.get("ai_draft")
    wanted = learning.spoken_key(opening)
    if not isinstance(draft, dict) or not wanted:
        return False
    drafted, proposed = _lines(draft.get("lines")), [line["spoken_script_zh"] for line in _lines(draft.get("proposed"))]
    if wanted in "".join(learning.spoken_key(line["spoken_script_zh"]) for line in drafted):
        return True
    if any(wanted in learning.spoken_key(text) for text in proposed):
        return True
    head = str(sample.get("text") or "")[:HEAD_CHARS].split("\n")      # the opening is read from the same stretch
    taken = 0
    for no, by_ai in learning.lines_by_ai(drafted, _lines(head), proposed):
        if taken >= len(wanted):
            break
        if by_ai:
            return True
        taken += len(learning.spoken_key(head[no - 1]))
    return False


def library(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The creator's openings with their videos' results, and how each label has done.

    ``openers``: one per confirmed, non-sponsored item with spoken text: ``{"sample_id", "title", "opening",
    "patterns", "units", "interactions", "times_usual"}``. Those with numbers come first, best first; the
    rest follow, newest first. ``times_usual`` is the video's interactions over the creator's usual
    (``usual_interactions``: the middle value over all their non-sponsored videos with numbers), or None.

    ``patterns``: for each label that occurs, ``{"pattern", "videos", "with_numbers", "middle_interactions",
    "times_usual"}``. The last two are None under MIN_FOR_PATTERN videos with numbers.

    ``written_by_ai`` is present only when there is one: openings, in the same form and order as ``openers``,
    of posted scripts that are still a sentence an AI wrote (``opening_by_ai``), whether or not the creator
    has confirmed the rest of the item's wording. They are never in ``openers`` and are not counted in
    ``patterns``; their videos' numbers count in ``usual_interactions`` like any video's.
    """
    organic = [s for s in samples if not s.get("is_sponsored")]
    totals = [float(total) for total in (corpus.interactions(s) for s in organic) if total is not None]
    usual = _middle(totals) or (sum(totals) / len(totals) if totals else None) or None
    view_totals = [float(v) for v in (s.get("views") for s in organic) if v is not None]
    usual_views = _middle(view_totals) or (sum(view_totals) / len(view_totals) if view_totals else None) or None
    with_numbers = sum(1 for s in organic if corpus.interactions(s) is not None or s.get("views") is not None)
    openers = []
    written_by_ai = []
    for sample in organic:
        posted_waiting = corpus.waits_as_posted_script(sample)
        if sample.get("needs_review") and not posted_waiting:
            continue                            # a machine transcript nobody has read yet: the words may not be theirs
        found = describe(sample.get("text"))
        if not found["opening"]:
            continue
        total = corpus.interactions(sample)
        views = sample.get("views")
        rel_usual = _times_usual(total, usual) if total is not None else _times_usual(views, usual_views)
        opener = {"sample_id": sample["id"], "title": sample["title"], "opening": found["opening"],
                  "patterns": found["patterns"], "units": found["units"], "interactions": total,
                  "times_usual": rel_usual, "_views": views, "_at": sample.get("created_at") or 0}
        if sample.get("origin") == corpus.ORIGIN_SCRIPT and opening_by_ai(sample, found["opening"]):
            written_by_ai.append(opener)        # the video's result is the creator's; the sentence is not
        elif not posted_waiting:
            openers.append(opener)              # a posted script still waiting: its wording is not yet said to be theirs
    for found_openers in (openers, written_by_ai):
        found_openers.sort(
            key=lambda o: (o["interactions"] is not None or o["_views"] is not None, o["interactions"] or 0, o["_views"] or 0, o["_at"]),
            reverse=True,
        )
    patterns = []
    for label in PATTERNS:
        having = [o for o in openers if label in o["patterns"]]
        if not having:
            continue
        numbered = [float(o["interactions"]) for o in having if o["interactions"] is not None]
        if numbered:
            middle = _middle(numbered) if len(numbered) >= MIN_FOR_PATTERN else None
            pat_times = _times_usual(middle, usual)
            count_num = len(numbered)
        else:
            view_numbered = [float(o["_views"]) for o in having if o["_views"] is not None]
            middle = None
            view_middle = _middle(view_numbered) if len(view_numbered) >= MIN_FOR_PATTERN else None
            pat_times = _times_usual(view_middle, usual_views)
            count_num = len(view_numbered)
        patterns.append({"pattern": label, "videos": len(having), "with_numbers": count_num,
                         "middle_interactions": middle, "times_usual": pat_times})
    for opener in openers + written_by_ai:
        del opener["_at"]
        del opener["_views"]
    answer = {"openers": openers, "patterns": patterns, "usual_interactions": usual, "videos_with_numbers": with_numbers}
    if written_by_ai:
        answer["written_by_ai"] = written_by_ai
    return answer


def best(samples: List[Dict[str, Any]], limit: int = BEST_FOR_PROMPT) -> List[Dict[str, Any]]:
    """The creator's openings from videos that did at least as well as their usual, best first: for a draft prompt.

    Empty until some non-sponsored video has numbers: without results there is no "best", and the creator's
    whole scripts are in the prompt already.
    """
    found = library(samples)
    usual = found["usual_interactions"]         # compared as counted, not through the rounded ``times_usual``
    if usual:
        wanted = [o for o in distinct(found["openers"]) if o["interactions"] is not None and o["interactions"] >= usual]
    else:
        organic = {s["id"]: s.get("views") for s in samples if not s.get("is_sponsored")}
        view_totals = [float(v) for v in organic.values() if v is not None]
        usual_views = _middle(view_totals) or (sum(view_totals) / len(view_totals) if view_totals else None) or None
        wanted = [
            o for o in distinct(found["openers"])
            if usual_views and organic.get(o["sample_id"]) is not None and float(organic[o["sample_id"]]) >= usual_views
        ]
    return [{"title": o["title"], "opening": o["opening"]} for o in wanted[:max(0, limit)]]


def distinct(openers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``openers`` with each opening once: the first in the list, which ``library`` orders best first. The same
    script added to the library twice would otherwise fill a short list with one opening."""
    seen = set()
    out = []
    for opener in openers:
        if opener["opening"] not in seen:
            seen.add(opener["opening"])
            out.append(opener)
    return out
