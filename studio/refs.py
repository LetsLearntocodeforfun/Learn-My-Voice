"""The creator's own material beside the script being edited ("参考 / References").

While a script is edited, a panel beside it lists the library items that bear on that script, each with what
it is and how it did. No model is asked and nothing leaves the computer: the lists are made by the plain
rules below, the panel states those rules, and next to every item it says what was found in it.

What the panel lists, in this order (``for_script``):

- PINNED. The items the creator pinned to this script, at most MAX_PINS (``set_pin``). Pins are kept in a
  table of their own and are no part of the script: pinning never moves the script's ``updated_at``, so a
  save based on the version from before a pin is accepted like any other, and nothing typed is touched.
- WHAT THE DRAFT SAW. For an AI draft with a recorded ``basis``: the items whose text, or whose opening, that
  draft's prompt contained. The basis recorded their titles and nothing else, so they are found again by
  title. A title no item carries any more is reported as missing, and a title several items carry is shown
  with one of them and the count.
- ON THE SAME TOPIC. Items that bear on the script's target keywords or on the words of its title
  (``terms``), measured with ``trends.relevance`` and counted from ``trends.RELEVANT_AT``: the measure and the
  threshold by which the trends page calls a video related. The order is read off counts, never off a score:
  more keywords found first, then more words of the title, then more mentions, then the better result, then
  the newer item. A script with no keyword and no usable title gets no list, and the panel says what to add.
- SEARCH (``search``). Items whose title or text holds what was typed, compared as it was typed: no word is
  split off, letter case and spaces make no difference. Chinese has no spaces to split at.

The honesty rule of this project holds here as everywhere. A machine transcript the creator has not confirmed,
and a posted script whose wording they have not confirmed (``corpus.waits_as_posted_script``), are shown for
what they are about and how they did, and each carries ``wording`` so that the panel labels the text as not
confirmed to be theirs. An opening that is still an AI's sentence (``hooks.opening_by_ai``) carries
``opening_by_ai``. What an AI wrote for a posted item (``ai_draft``) and its ``snapshot`` are for counting
only and are never in an answer; every item is built here field by field (``_shown``).

Bounds. A library item can hold 500,000 characters and a library hundreds of items, so:

- an answer holds a fixed number of items (MAX_PINS, BASIS_MAX, RELATED_MAX, SEARCH_MAX), each with a title of
  at most TITLE_CHARS, an opening and an excerpt of at most EXCERPT_CHARS; the whole text of an item is sent
  only when it is asked for, one item at a time, TEXT_PART_CHARS characters a request (``text_part``);
- every request looks at each library item once, and reads only the first SCAN_CHARS characters of its text
  (``Store.list_sample_heads``): the work grows with the number of items, not with its square. Of a longer
  item only that start reaches this program; the database still passes over the rest of it to cut it off,
  which is the one part of the work that grows with the length of the items. An item that is longer says so
  (``long``), since a mention further in was not counted.

Every place in a text that an answer names (``excerpt.start``, ``excerpt.mark``, ``start`` and ``next`` of
``text_part``) is counted in characters as Python counts them. A page counts an emoji as two, so it must not
cut a text at these places with its own count (``refs.js`` cuts by characters).

The numbers above are judgement calls, like those of the trends page. They bound size and work; they were
not tuned against real material, because none was available.
"""
from __future__ import annotations

import bisect
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import corpus, hooks, trends
from .store import BEAT_TYPES, METRIC_FIELDS, ORIGIN_SCRIPT, Store, keyword_key, normalize_keywords

MAX_PINS = 8                # items pinned to one script
BASIS_MAX = 8               # titles of "what the draft saw" that are looked up and listed
RELATED_MAX = 8             # items listed as on the same topic
BEATS_MAX = 8               # confirmed non-sponsored beats matching the script's beat roles
SEARCH_MAX = 8              # items one search lists
TITLE_CHARS = 120           # of a title, in an answer
TITLE_SCAN_CHARS = 400      # of a title, read for measuring and for finding an item by its title
SCAN_CHARS = 20000          # of an item's text, read for measuring and searching: more than an hour of speech
EXCERPT_CHARS = 240         # of an item's text, shown before the whole text is asked for
EXCERPT_LEAD = 60           # of those, how many stand before what was found
TEXT_PART_CHARS = 8000      # of an item's text, sent by one request for the whole text
QUERY_CHARS = 60            # of a search
TITLE_READ_CHARS = 80       # of the script's own title, read for its words
TITLE_PARTS_MAX = 6         # words of the script's title that are measured
PIECES_SHOWN = 6            # pieces of a term named next to an item

KEYWORD, TITLE = "keyword", "title"                     # where a term comes from
EXAMPLE, OPENING = "example", "opening"                 # what a draft's prompt held of an item
OWN, TRANSCRIPT_WAITING, POSTED_WAITING = "own", "transcript_waiting", "posted_waiting"     # how an item's wording stands

# The titles a script has when nobody gave it one (``server.UNTITLED``, ``Store.update_script``). They say
# nothing about a topic, so they are not measured.
PLACEHOLDER_TITLES = ("未命名脚本", "Untitled script", "未命名", "Untitled")
# The words of a title: a run of Chinese characters, or an English word. Digits alone are left out: "15" is in too many texts.
_TITLE_WORD = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z][A-Za-z'\u2019\-]*")

# Refusals: code -> (HTTP status, English, Chinese). Both sentences are written to be shown as they are.
REFUSALS = {
    "not_found": (404, "There is no such script.", "没有这份脚本。"),
    "no_item": (404, "That item is not in the library (it may have been removed). Nothing was changed.",
                "素材库里没有这一条（可能已经删除）。这次没有改动任何内容。"),
    "too_many_pins": (409, "A script can have at most %d pinned items. Unpin one first." % MAX_PINS,
                      "一篇脚本最多钉 %d 条素材。请先取消一条。" % MAX_PINS),
    "bad_pin": (400, "A pin is set with sample_id (the id of a library item) and pinned (true or false).",
                "钉素材时要给出 sample_id（素材的编号）和 pinned（true 或 false）。"),
    "query_too_long": (400, "The search text can be at most %d characters." % QUERY_CHARS,
                       "搜索的文字最多 %d 个字符。" % QUERY_CHARS),
    "bad_start": (400, "The place to start reading from (start) has to be a whole number that is not negative.",
                  "开始读的位置（start）必须是不小于 0 的整数。"),
}


class RefsError(ValueError):
    """A request about the references of a script that cannot be met. Nothing was changed.

    ``code`` is one of REFUSALS; ``status`` is the HTTP status for it; ``message`` (English) and
    ``message_zh`` are written to be shown.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.message, self.message_zh = REFUSALS[code]
        super().__init__(self.message)


def limits() -> Dict[str, Any]:
    """The bounds an answer keeps to, as the page states them."""
    return {"pins": MAX_PINS, "basis": BASIS_MAX, "related": RELATED_MAX, "beats": BEATS_MAX, "search": SEARCH_MAX,
            "excerpt_chars": EXCERPT_CHARS, "scan_chars": SCAN_CHARS, "text_part_chars": TEXT_PART_CHARS,
            "query_chars": QUERY_CHARS, "relevant_at": trends.RELEVANT_AT}


# ---- what a script is measured by ---------------------------------------------------------------------------
def terms(script: Dict[str, Any]) -> List[Dict[str, str]]:
    """The words a script's topic is read from: ``[{"text", "from"}]``, its target keywords, then its title.

    A keyword is taken whole (``from`` KEYWORD). The title is taken in its words (``from`` TITLE): each run of
    Chinese characters and each English word, of two characters or more, the first TITLE_PARTS_MAX of them. A
    title is a phrase and no keyword: measured as one string, "周末 Costco 补货" would ask an item for most of
    all three words. A placeholder title (PLACEHOLDER_TITLES) has no words, and a word that is a keyword
    already is not taken twice. Empty when there is nothing to measure by.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for keyword in normalize_keywords(script.get("keywords")):
        key = keyword_key(keyword)
        if key and key not in seen and trends.keyword_pieces(keyword):
            seen.add(key)
            out.append({"text": keyword, "from": KEYWORD})
    title = script.get("title") if isinstance(script.get("title"), str) else ""
    if title.strip() in PLACEHOLDER_TITLES:
        return out
    parts = 0
    for word in _TITLE_WORD.findall(title[:TITLE_READ_CHARS]):
        key = keyword_key(word)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append({"text": word, "from": TITLE})
        parts += 1
        if parts == TITLE_PARTS_MAX:
            break
    return out


def _compact(text: str) -> str:
    """Text as it is compared: lower case, no spaces, one kind of apostrophe. What ``trends._Hay`` does to an item."""
    return re.sub(r"\s+", "", text.replace("\u2019", "'").lower())


def _lowered(text: str) -> str:
    return text.replace("\u2019", "'").lower()


def _count(term: str, compact: str, words: List[str]) -> int:
    """How often ``term`` stands as written in a text given without its spaces (``compact``) and as its English
    words in order (``words``).

    A term with Chinese in it is counted wherever it stands. An English one is counted as whole words in a row,
    so ``gym`` is not counted in ``gymnastics``: the way ``trends.relevance`` reads English.
    """
    key = keyword_key(term)
    if not key:
        return 0
    if not key.isascii():
        return compact.count(key)
    pieces = trends.keyword_pieces(term)
    if not pieces:
        return 0
    if len(pieces) == 1:
        return words.count(pieces[0])
    return sum(1 for at in range(len(words) - len(pieces) + 1) if words[at:at + len(pieces)] == pieces)


def _evidence(head: Dict[str, Any], hay: Any, hits: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """What was found of each term an item bears on: ``{"term", "from", "count", "in_title", "pieces", "pieces_found", "of"}``.

    ``count``: how often the term stands as written in the title and the text read (``_count``). ``in_title``:
    whether it stands in the title. ``pieces`` and ``of``: for a term that is not there as written, which of
    its pieces are (``trends.keyword_pieces``: two Chinese characters, or an English word; the first
    PIECES_SHOWN are named), how many are, and of how many.
    """
    words = trends._LATIN.findall(_lowered("%s\n%s" % (head["title"], head["text"])))
    title_compact, title_words = _compact(head["title"]), trends._LATIN.findall(_lowered(head["title"]))
    out = []
    for term in hits:
        pieces = list(dict.fromkeys(trends.keyword_pieces(term["text"])))
        inside = [piece for piece in pieces if (piece in hay.words if piece.isascii() else piece in hay.compact)]
        out.append({"term": term["text"], "from": term["from"], "count": _count(term["text"], hay.compact, words),
                    "in_title": _count(term["text"], title_compact, title_words) > 0,
                    "pieces": inside[:PIECES_SHOWN], "pieces_found": len(inside), "of": len(pieces)})
    return out


def _matches(heads: List[Dict[str, Any]], wanted: List[Dict[str, str]]) -> Dict[str, List[Dict[str, Any]]]:
    """``{item id: what was found}`` for every item that bears on a term. One look at each item.

    An item bears on a term when ``trends.relevance`` gives ``trends.RELEVANT_AT`` or more: the term stands in
    it as written, or that share of the term's pieces does.
    """
    found: Dict[str, List[Dict[str, Any]]] = {}
    if not wanted:
        return found
    for head in heads:
        hay = trends._Hay(head)
        hits = [term for term in wanted if trends.relevance(term["text"], hay) >= trends.RELEVANT_AT]
        if hits:
            found[head["id"]] = _evidence(head, hay, hits)
    return found


def _order(head: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Tuple[Any, ...]:
    """Where an item stands among those on the same topic. Every part is a count the panel shows:
    keywords found, words of the title found, mentions, the video's interactions, how new the item is."""
    keywords = sum(1 for found in evidence if found["from"] == KEYWORD)
    total = corpus.interactions(head)
    return (-keywords, -(len(evidence) - keywords), -sum(found["count"] for found in evidence),
            -(total if total is not None else -1), -(head.get("created_at") or 0), head["id"])


# ---- an excerpt around what was found -----------------------------------------------------------------------
class _Places:
    """The start of an item's text, prepared for finding where something stands in it.

    What is looked for is compared as everywhere here (lower case, no spaces), and the place found is a place
    in the text as it is, spaces and capitals included, so that the excerpt can be cut from the text itself.
    """

    def __init__(self, text: str) -> None:
        low = text.lower()
        if len(low) != len(text):           # a few letters become two when lowered; those stay as they are
            low = "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in text)
        self.folded = low.replace("\u2019", "'")                    # as long as the text, place for place
        self.runs = [(found.start(), found.end()) for found in re.finditer(r"\S+", text)]
        self.squeezed = "".join(self.folded[start:end] for start, end in self.runs)
        self.offsets: List[int] = []                                # where each run begins in ``squeezed``
        at = 0
        for start, end in self.runs:
            self.offsets.append(at)
            at += end - start
        self._words: Optional[List[Any]] = None

    def _back(self, at: int) -> int:
        """The place in the text of the character at ``at`` in the text without its spaces."""
        run = bisect.bisect_right(self.offsets, at) - 1
        return self.runs[run][0] + (at - self.offsets[run])

    def written(self, needle: str) -> Optional[Tuple[int, int]]:
        """Where ``needle`` first stands as written, whatever the spaces and the letter case: ``(start, end)``."""
        key = _compact(needle)
        at = self.squeezed.find(key) if key else -1
        return (self._back(at), self._back(at + len(key) - 1) + 1) if at >= 0 else None

    def words(self, pieces: Sequence[str]) -> Optional[Tuple[int, int]]:
        """Where these English words or numbers first stand one after the other, as whole words."""
        if not pieces:
            return None
        if self._words is None:
            self._words = list(trends._LATIN.finditer(self.folded))
        wanted = list(pieces)
        for at in range(len(self._words) - len(wanted) + 1):
            if [found.group() for found in self._words[at:at + len(wanted)]] == wanted:
                return self._words[at].start(), self._words[at + len(wanted) - 1].end()
        return None


def _spots(evidence: Optional[List[Dict[str, Any]]]) -> List[Tuple[str, bool]]:
    """What to look for in an item's text, from what was found in it: ``(text, as whole English words)``."""
    spots: List[Tuple[str, bool]] = []
    for found in evidence or []:
        if found["count"]:
            spots.append((found["term"], keyword_key(found["term"]).isascii()))
        else:
            spots.extend((piece, piece.isascii()) for piece in found["pieces"])
    return spots


def _excerpt(head: Dict[str, Any], spots: Sequence[Tuple[str, bool]]) -> Dict[str, Any]:
    """Up to EXCERPT_CHARS of an item's text: around the earliest of ``spots`` that stands in it, else its start.

    ``{"text", "start", "cut_before", "cut_after", "mark"}``. ``mark`` is ``[from, to]`` inside ``text`` for
    what was found, or None. Only the text that was read (``Store.list_sample_heads``) is looked through.
    """
    text = head["text"]
    first: Optional[Tuple[int, int]] = None
    if spots and text:
        places = _Places(text)
        for needle, whole_words in spots:
            found = places.words(trends.keyword_pieces(needle)) if whole_words else places.written(needle)
            if found is not None and (first is None or found[0] < first[0]):
                first = found
    start = max(0, min(first[0] - EXCERPT_LEAD, len(text) - EXCERPT_CHARS)) if first else 0
    part = text[start:start + EXCERPT_CHARS]
    mark = [first[0] - start, min(first[1], start + len(part)) - start] if first and first[0] < start + len(part) else None
    return {"text": part, "start": start, "cut_before": start > 0,
            "cut_after": start + len(part) < len(text) or bool(head.get("text_cut")), "mark": mark}


# ---- one item, as the panel gets it -------------------------------------------------------------------------
def _usual(samples: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    """The creator's usual result, and how many videos it rests on: the middle value of the interactions of
    their non-sponsored videos with numbers.

    The value ``hooks.library`` gives as ``usual_interactions``, by the same steps; a test holds the two
    together. It is worked out here so that it can be read off the starts of the items alone.
    """
    totals = [float(total) for total in (corpus.interactions(s) for s in samples if not s.get("is_sponsored")) if total is not None]
    usual = hooks._middle(totals) or (sum(totals) / len(totals) if totals else None) or None
    return usual, len(totals)


def _wording(sample: Dict[str, Any]) -> str:
    """How an item's wording stands: OWN (written or confirmed by the creator), POSTED_WAITING (a script of this
    app that was posted and whose wording they have not said is theirs) or TRANSCRIPT_WAITING (a machine
    transcript they have not confirmed)."""
    if corpus.waits_as_posted_script(sample):
        return POSTED_WAITING
    return TRANSCRIPT_WAITING if sample.get("needs_review") else OWN


def _shown(store: Store, head: Dict[str, Any], usual: Optional[float], pinned: Any, script_id: str,
           evidence: Optional[List[Dict[str, Any]]] = None,
           spots: Optional[Sequence[Tuple[str, bool]]] = None) -> Dict[str, Any]:
    """A library item as the panel gets it. Every field is named here; nothing is copied over from the stored item.

    ``kind``: how it came in. ``interactions`` and ``times_usual``: its result, and that result over the
    creator's usual one (None without numbers, or without a usual result). ``posted``: for an item that came
    from a script, the day it was posted, the day its numbers were read and the days between
    (``corpus.read_after_days``). ``wording``: see ``_wording``. ``opening`` and ``opening_by_ai``: its first
    breaths (``hooks.describe``) and whether they are still a sentence an AI wrote. ``excerpt``: see
    ``_excerpt``. ``long``: its text goes on after what was read. ``why``: what was found in it of the script's
    terms. ``of_this_script``: it is the item this very script was posted as.
    """
    total = corpus.interactions(head)
    opening = hooks.describe(head["text"])["opening"]
    by_ai = False
    if opening and head.get("origin") == ORIGIN_SCRIPT:
        whole = store.get_sample(head["id"])            # what an AI wrote for the script is kept with the whole item
        by_ai = whole is not None and hooks.opening_by_ai(whole, opening)
    from_script = head.get("origin") == ORIGIN_SCRIPT
    item = {
        "id": head["id"], "title": head["title"][:TITLE_CHARS], "title_cut": len(head["title"]) > TITLE_CHARS,
        "kind": head["kind"], "is_sponsored": bool(head["is_sponsored"]),
        "has_numbers": any(head.get(field) is not None for field in METRIC_FIELDS),
        "interactions": total, "times_usual": hooks._times_usual(total, usual),
        "posted": {"posted_on": head.get("posted_on"), "metrics_on": head.get("metrics_on"),
                   "read_after_days": corpus.read_after_days(head)} if from_script else None,
        "of_this_script": bool(from_script and head.get("script_id") == script_id),
        "wording": _wording(head), "opening": opening, "opening_by_ai": by_ai,
        "excerpt": _excerpt(head, _spots(evidence) if spots is None else spots),
        "long": bool(head.get("text_cut")), "pinned": head["id"] in pinned, "why": evidence or [],
    }
    item.update({field: head.get(field) for field in METRIC_FIELDS})
    return item


# ---- what the draft saw ----------------------------------------------------------------------------------------
def _basis(script: Dict[str, Any], heads: List[Dict[str, Any]],
           show: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The items an AI draft's prompt contained, found again by the titles its ``basis`` recorded; None for a
    script that is no AI draft or has no basis.

    ``{"items", "missing", "left_out"}``. An item carries ``roles`` (EXAMPLE: its text was in the prompt as an
    example of the creator's writing; OPENING: its opening was) and ``same_title``, how many items carry that
    title now. When several do, the one shown is the likeliest: the prompt held only non-sponsored, confirmed
    material. ``missing``: ``{"title", "roles"}`` for a recorded title that no item carries any more (removed,
    or renamed since). ``left_out``: recorded titles past BASIS_MAX, which are not looked up.
    """
    basis = script.get("basis")
    if not script.get("has_ai_draft") or not isinstance(basis, dict):
        return None
    roles: Dict[str, List[str]] = {}
    for role, key in ((EXAMPLE, "exemplar_titles"), (OPENING, "opening_titles")):
        for title in basis.get(key) if isinstance(basis.get(key), list) else []:
            if isinstance(title, str) and role not in roles.setdefault(title, []):
                roles[title].append(role)
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    for head in heads:
        by_title.setdefault(head["title"], []).append(head)
    items, missing = [], []
    for title in list(roles)[:BASIS_MAX]:
        carrying = sorted(by_title.get(title[:TITLE_SCAN_CHARS], []),
                          key=lambda head: (bool(head["is_sponsored"]), bool(head["needs_review"])))
        if carrying:
            items.append(dict(show(carrying[0]), roles=roles[title], same_title=len(carrying)))
        else:
            missing.append({"title": title[:TITLE_CHARS], "roles": roles[title]})
    return {"items": items, "missing": missing, "left_out": max(0, len(roles) - BASIS_MAX)}


_FORMAT_DEFAULT_BEATS: Dict[str, Tuple[str, ...]] = {
    "COUPLE_VLOG": ("HOOK", "COUPLE_BANTER", "VLOG_STORY", "CLOSING_CTA"),
    "FAVORITES_ROUNDUP": ("HOOK", "PRODUCT_EXPERIENCE", "REALISTIC_CAVEAT", "CLOSING_CTA"),
    "SINGLE_PRODUCT_SHARE": ("HOOK", "PRODUCT_EXPERIENCE", "REALISTIC_CAVEAT", "CLOSING_CTA"),
    "VLOG_VOICEOVER": ("HOOK", "VLOG_STORY", "TRANSITION_BRIDGE", "CLOSING_CTA"),
}


def _script_beat_types(script: Dict[str, Any]) -> Tuple[Tuple[str, ...], bool]:
    """``(wanted_beat_types, from_script_lines)`` for a script."""
    seen: List[str] = []
    for line in script.get("lines") if isinstance(script.get("lines"), list) else []:
        if not isinstance(line, dict):
            continue
        bt = str(line.get("beat_type") or "").strip().upper()
        if bt in BEAT_TYPES and bt not in seen:
            seen.append(bt)
    if seen:
        return tuple(seen), True
    fmt = str(script.get("video_format") or "VLOG_VOICEOVER")
    return _FORMAT_DEFAULT_BEATS.get(fmt, _FORMAT_DEFAULT_BEATS["VLOG_VOICEOVER"]), False


def _beats_for_script(
    store: Store,
    script: Dict[str, Any],
    evidence: Dict[str, List[Dict[str, Any]]],
    usual: Optional[float],
) -> Dict[str, Any]:
    """Matching confirmed, non-sponsored beats for the beat roles of ``script``, bounded to ``BEATS_MAX``."""
    wanted, from_lines = _script_beat_types(script)
    raw = [
        b for b in store.list_confirmed_organic_beats(wanted)
        if b.get("sample_script_id") != script["id"]
    ]
    type_rank = {bt: idx for idx, bt in enumerate(wanted)}

    def beat_key(b: Dict[str, Any]) -> Tuple[Any, ...]:
        on_topic = b["sample_id"] in evidence
        sample_metrics = {
            "views": b.get("sample_views"),
            "likes": b.get("sample_likes"),
            "collects": b.get("sample_collects"),
            "comments": b.get("sample_comments"),
        }
        total = corpus.interactions(sample_metrics)
        return (
            0 if on_topic else 1,
            -(total if total is not None else -1),
            -(b.get("sample_created_at") or 0.0),
            type_rank.get(b["beat_type"], 99),
            b["beat_index"],
            b["id"],
        )

    ordered = sorted(raw, key=beat_key)
    by_type: Dict[str, List[Dict[str, Any]]] = {bt: [] for bt in wanted}
    for b in ordered:
        by_type.setdefault(b["beat_type"], []).append(b)
    picked: List[Dict[str, Any]] = []
    picked_ids: set = set()
    # One beat of each wanted role first so no single role crowds out the others, then the remaining in rank order.
    for bt in wanted:
        if len(picked) >= BEATS_MAX:
            break
        bucket = by_type.get(bt) or []
        if bucket:
            picked.append(bucket[0])
            picked_ids.add(bucket[0]["id"])
    for b in ordered:
        if len(picked) >= BEATS_MAX:
            break
        if b["id"] not in picked_ids:
            picked.append(b)
            picked_ids.add(b["id"])

    items: List[Dict[str, Any]] = []
    for b in picked:
        sample_metrics = {
            "views": b.get("sample_views"),
            "likes": b.get("sample_likes"),
            "collects": b.get("sample_collects"),
            "comments": b.get("sample_comments"),
        }
        total = corpus.interactions(sample_metrics)
        title = str(b.get("sample_title") or "")
        items.append({
            "id": b["id"],
            "sample_id": b["sample_id"],
            "sample_title": title[:TITLE_CHARS],
            "title_cut": len(title) > TITLE_CHARS,
            "beat_index": b["beat_index"],
            "beat_type": b["beat_type"],
            "audio_mode": b["audio_mode"],
            "delivery": b["delivery"],
            "start_sec": b["start_sec"],
            "end_sec": b["end_sec"],
            "spoken_text": b["spoken_text"],
            "english_meaning": b["english_meaning"],
            "visual_summary": b["visual_summary"],
            "on_topic": b["sample_id"] in evidence,
            "interactions": total,
            "times_usual": hooks._times_usual(total, usual),
        })
    return {
        "wanted_types": list(wanted),
        "from_script_lines": from_lines,
        "items": items,
        "found": len(ordered),
    }


# ---- the requests -------------------------------------------------------------------------------------------
def _script(store: Store, script_id: Any) -> Dict[str, Any]:
    script = store.get_script(script_id) if isinstance(script_id, str) else None
    if script is None:
        raise RefsError("not_found")
    return script


def for_script(store: Store, script_id: Any) -> Dict[str, Any]:
    """What the panel beside a script shows. Raises ``RefsError("not_found")`` for a script that does not exist.

    ``pinned``: the pinned items, in the order they were pinned. ``basis``: see ``_basis``. ``related``:
    ``{"terms", "needs_terms", "items", "found", "listed_above"}``: the terms the script was measured by,
    whether it has none, the first RELATED_MAX items on the same topic that are not listed above already, how
    many such items there are in all, and how many more are on the topic but stand above (pinned, or seen by
    the draft). The item this script was itself posted as is no reference for it and is left out of
    ``related``. ``library``: how many items the library holds. ``usual_interactions`` and
    ``videos_with_numbers``: the creator's usual result (``_usual``). ``limits``: see ``limits``.

    Each item is as ``_shown`` gives it. The library is read once, the start of each item only.
    """
    script = _script(store, script_id)
    heads = store.list_sample_heads(SCAN_CHARS, TITLE_SCAN_CHARS)
    by_id = {head["id"]: head for head in heads}
    usual, numbered = _usual(heads)
    wanted = terms(script)
    evidence = _matches(heads, wanted)
    pinned_ids = [sample_id for sample_id in store.list_pins(script["id"]) if sample_id in by_id][:MAX_PINS]
    pinned = set(pinned_ids)

    def show(head: Dict[str, Any]) -> Dict[str, Any]:
        return _shown(store, head, usual, pinned, script["id"], evidence.get(head["id"]))

    basis = _basis(script, heads, show)
    above = pinned | {item["id"] for item in (basis["items"] if basis else [])}
    on_topic = sorted((head for head in heads if head["id"] in evidence and head.get("script_id") != script["id"]),
                      key=lambda head: _order(head, evidence[head["id"]]))
    related = [head for head in on_topic if head["id"] not in above]
    return {
        "script_id": script["id"], "library": len(heads),
        "usual_interactions": usual, "videos_with_numbers": numbered, "limits": limits(),
        "pinned": [show(by_id[sample_id]) for sample_id in pinned_ids],
        "basis": basis,
        "related": {"terms": wanted, "needs_terms": not wanted, "items": [show(head) for head in related[:RELATED_MAX]],
                    "found": len(related), "listed_above": len(on_topic) - len(related)},
        "beats": _beats_for_script(store, script, evidence, usual),
    }


def search(store: Store, script_id: Any, query: Any) -> Dict[str, Any]:
    """Library items whose title or text holds ``query`` as it was typed: ``{"query", "items", "found", "limit"}``.

    The query is one string. It is not split into words (Chinese has no spaces to split at), and it is found
    whatever the letter case and the spaces: "trader joe" finds "Trader Joe's". Items with it in the title
    come first, then those that hold it more often, then the better result, then the newer item. ``items``
    are the first SEARCH_MAX, each as ``_shown`` gives it with ``found``: ``{"in_title", "count"}``; the
    excerpt stands around the first place in the text. ``found`` is how many items hold it in all.

    Raises ``RefsError``: ``not_found`` for a script that does not exist, ``query_too_long`` past QUERY_CHARS.
    """
    script = _script(store, script_id)
    query = query.strip() if isinstance(query, str) else ""
    if len(query) > QUERY_CHARS:
        raise RefsError("query_too_long")
    key = _compact(query)
    if not key:
        return {"query": query, "items": [], "found": 0, "limit": SEARCH_MAX}
    heads = store.list_sample_heads(SCAN_CHARS, TITLE_SCAN_CHARS)
    hits = []
    for head in heads:
        in_title, count = key in _compact(head["title"]), _compact(head["text"]).count(key)
        if in_title or count:
            total = corpus.interactions(head)
            hits.append(((not in_title, -count, -(total if total is not None else -1), -(head.get("created_at") or 0), head["id"]),
                         head, {"in_title": in_title, "count": count}))
    hits.sort(key=lambda hit: hit[0])
    usual, _ = _usual(heads)
    pinned = set(store.list_pins(script["id"]))
    items = [dict(_shown(store, head, usual, pinned, script["id"], spots=[(query, False)]), found=found)
             for _, head, found in hits[:SEARCH_MAX]]
    return {"query": query, "items": items, "found": len(hits), "limit": SEARCH_MAX}


def set_pin(store: Store, script_id: Any, sample_id: Any, pinned: Any) -> Dict[str, Any]:
    """Pin a library item to a script (``pinned`` true) or take the pin away (false). Returns ``for_script``.

    Pinning what is pinned already, and unpinning what is not pinned or is no longer in the library, change
    nothing and are no error. The script itself is not written to: its ``updated_at`` stays as it is.

    Raises ``RefsError``: ``bad_pin`` when ``pinned`` is no boolean or ``sample_id`` no text, ``not_found`` for a
    script that does not exist, ``no_item`` for an id that names no library item, ``too_many_pins`` when the
    script has MAX_PINS pins already. In each case nothing was changed.
    """
    if not isinstance(pinned, bool) or not isinstance(sample_id, str):
        raise RefsError("bad_pin")
    script = _script(store, script_id)
    if pinned:
        outcome = store.pin_sample(script["id"], sample_id, MAX_PINS)
        refused = {"no_script": "not_found", "no_sample": "no_item", "full": "too_many_pins"}.get(outcome)
        if refused:
            raise RefsError(refused)
    else:
        store.unpin_sample(script["id"], sample_id)
    return for_script(store, script["id"])


def text_part(store: Store, sample_id: Any, start: Any = 0) -> Dict[str, Any]:
    """One part of an item's whole text, for reading it in the panel: at most TEXT_PART_CHARS characters from ``start``.

    ``{"id", "start", "text", "chars", "next", "limit", "wording"}``. ``chars`` is the length of the whole
    text; ``next`` is where the following part begins, or None at the end. ``wording`` is how the item's
    wording stands (``_wording``), so that whoever shows the text can label it.

    Raises ``RefsError``: ``no_item`` for an id that names no library item, ``bad_start``.
    """
    if isinstance(start, bool) or not re.fullmatch(r"[0-9]{1,9}", str(start)):
        raise RefsError("bad_start")
    sample = store.get_sample(sample_id) if isinstance(sample_id, str) else None
    if sample is None:
        raise RefsError("no_item")
    at, text = int(str(start)), sample["text"]
    part = text[at:at + TEXT_PART_CHARS]
    return {"id": sample["id"], "start": at, "text": part, "chars": len(text),
            "next": at + len(part) if at + len(part) < len(text) else None, "limit": TEXT_PART_CHARS,
            "wording": _wording(sample)}
