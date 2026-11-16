"""How close a script's countable speech habits are to the creator's own material.

Nothing here is a model's opinion. Four habits are counted in the creator's own material and in a script,
and the two counts are compared:

- breath length: how many spoken units stand between two breath marks;
- how a breath ends: with which sentence-final particle, or with none;
- how a breath begins: with which spoken connector (然后, 所以, 但是 and so on), or with none;
- English mixed in: how many breaths hold an English word, and whether the words are ones the creator has used.

Each comparison gives a number from 0 to 1. The score is their average, shown as 0 to 100.

What a score means is given by the creator's own scripts, not by a scale chosen here: each of their
scripts is scored the same way against the rest of their material, and that range is reported with every
score (``band``). A draft inside the range is, on these four counts, as close to the creator's material as
their own scripts are to each other.

What the score does not say: whether a script is good, whether it will do well, or anything about habits
that are not counted here (word choice, humour, what the creator talks about). The connector list is a
fixed list of common spoken connectors, a judgement call; the same list is applied to both sides.

The reference is text the creator wrote or confirmed, non-sponsored only, like everything that learns
wording (``pipeline.confirmed_samples``).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import textstats
from .corpus import FINAL_PARTICLES

MIN_REFERENCE_BREATHS = 30      # under this the creator's habits are not measured, as for breath-length limits
MIN_SCRIPT_BREATHS = 4          # a script shorter than this has too few breaths to compare
MIN_BAND_SCRIPTS = 3            # the creator's own range is shown once this many of their scripts could be scored
RATE_FLOOR = 0.05               # a habit the creator shows in under 5% of breaths is compared as if it were 5%
USED_SHARE = 0.03               # a particle or connector counts as the creator's own from 3% of their uses of that kind
NOTED_GAP = 0.05                # a gap of 5 points or more that chance explains is said to be so, not passed over in silence
TOP_SHOWN = 3
UNFAMILIAR_SHOWN = 5

# Spoken connectors a breath can begin with. Longer ones are tried first, so 然后呢 is not counted as 然后.
CONNECTORS = tuple(sorted((
    "然后", "然后呢", "所以", "所以说", "但是", "可是", "不过", "结果", "其实", "就是", "就是说", "因为", "而且",
    "还有", "另外", "反正", "那么", "于是", "后来", "接着", "最后", "首先", "其次", "总之", "再说", "况且", "然而",
    "因此", "此外", "同时", "虽然", "既然", "如果", "要是", "比如", "比如说", "对了", "话说", "说实话", "讲真",
    "本来", "一开始", "当时", "没想到", "谁知道", "总而言之", "综上所述", "与此同时", "除此之外", "值得一提的是",
), key=len, reverse=True))
_END_PUNCT = "，,。！!？?；;～~… \u3000"
_START_SKIP = " \u3000\"'“”‘’「」『』（）()"


class Counts:
    """What is counted in a body of text. Two of them can be added and subtracted, so the creator's material
    without one script is the whole minus that script."""

    __slots__ = ("breaths", "lengths", "endings", "starts", "english_breaths", "english_words")

    def __init__(self) -> None:
        self.breaths = 0
        self.lengths: Counter = Counter()           # spoken units in a breath -> how many breaths
        self.endings: Counter = Counter()           # final particle -> how many breaths end with it
        self.starts: Counter = Counter()            # connector -> how many breaths begin with it
        self.english_breaths = 0
        self.english_words: Counter = Counter()     # English word, lower case -> how many times

    def add_text(self, text: Any) -> "Counts":
        """Count the breaths of ``text``. Bracketed cues are not speech; a breath with nothing spoken is skipped."""
        for clause in textstats.split_clauses(text if isinstance(text, str) else ""):
            units = textstats.spoken_units(clause)
            if units == 0:
                continue
            self.breaths += 1
            self.lengths[units] += 1
            tail = clause.rstrip(_END_PUNCT)
            if tail and tail[-1] in FINAL_PARTICLES:
                self.endings[tail[-1]] += 1
            head = clause.lstrip(_START_SKIP)
            connector = next((c for c in CONNECTORS if head.startswith(c)), None)
            if connector:
                self.starts[connector] += 1
            words = [w.lower() for w in textstats.english_words(clause)]
            if words:
                self.english_breaths += 1
                self.english_words.update(words)
        return self

    def without(self, other: "Counts") -> "Counts":
        out = Counts()
        out.breaths = self.breaths - other.breaths
        out.english_breaths = self.english_breaths - other.english_breaths
        for name in ("lengths", "endings", "starts", "english_words"):
            mine: Counter = getattr(self, name)
            setattr(out, name, Counter({key: n - getattr(other, name)[key] for key, n in mine.items()
                                        if n - getattr(other, name)[key] > 0}))
        return out

    def plus(self, other: "Counts") -> "Counts":
        out = Counts()
        out.breaths = self.breaths + other.breaths
        out.english_breaths = self.english_breaths + other.english_breaths
        for name in ("lengths", "endings", "starts", "english_words"):
            setattr(out, name, getattr(self, name) + getattr(other, name))
        return out

    def percentile(self, pct: float) -> int:
        """Nearest-rank percentile of breath length, as ``corpus.voice_stats`` computes it."""
        if not self.breaths:
            return 0
        rank = max(1, int(-(-pct * self.breaths // 100)))
        seen = 0
        for units in sorted(self.lengths):
            seen += self.lengths[units]
            if seen >= rank:
                return units
        return max(self.lengths)


def script_text_lines(lines: Any) -> List[str]:
    """The spoken text of a script's voiced lines. A LIVE_CLIP line is somebody else's words and is left out."""
    out = []
    for line in lines if isinstance(lines, (list, tuple)) else []:
        if not isinstance(line, dict) or str(line.get("audio_mode", "")).strip().upper() == "LIVE_CLIP":
            continue
        value = line.get("spoken_script_zh", "")
        out.append("" if value is None else str(value))
    return out


def count_texts(texts: Iterable[Any]) -> Counts:
    counts = Counts()
    for text in texts:
        counts.add_text(text)
    return counts


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _closeness(script_rate: float, own_rate: float, breaths: int) -> float:
    """1 when the script shows a habit as often as the creator does, falling to 0 as the two rates move apart.

    A short script cannot show a rate exactly: four breaths hold a 16% habit either not at all or in a
    quarter of them. So a difference no larger than chance alone would give in a script of this many
    breaths (one standard deviation) is not counted. What is left is taken relative to the creator's
    rate, so going from 30% to 15% counts like going from 10% to 5%. A habit the creator hardly shows is
    compared as if it were RATE_FLOOR.
    """
    by_chance = math.sqrt(own_rate * (1.0 - own_rate) / breaths) if breaths else 0.0
    apart = max(0.0, abs(script_rate - own_rate) - by_chance)
    return max(0.0, 1.0 - apart / max(own_rate, RATE_FLOOR))


def _within_chance(script_rate: float, own_rate: float, breaths: int) -> bool:
    """True when the two rates visibly differ and the whole difference is what chance allows (``_closeness`` is 1).

    The page shows both rates next to a full mark. Without a word about it, 16% against 0% with full marks
    reads like a mistake.
    """
    return abs(script_rate - own_rate) >= NOTED_GAP and _closeness(script_rate, own_rate, breaths) == 1.0


def _own(kinds: Counter) -> List[str]:
    """The entries the creator really uses: each at least USED_SHARE of their uses of that kind, most used first."""
    total = sum(kinds.values())
    return [key for key, n in kinds.most_common() if total and n / total >= USED_SHARE]


def _habit(script: Counts, own: Counts, kind: str) -> Dict[str, Any]:
    """One habit of the "which word, how often" sort: sentence-final particles (``endings``) or connectors (``starts``).

    Two things are compared: how often a breath has one at all, and whether the ones the script uses are
    the creator's. With none in the script only the first can be compared.
    """
    mine, theirs = getattr(script, kind), getattr(own, kind)
    own_rate, script_rate = _share(sum(theirs.values()), own.breaths), _share(sum(mine.values()), script.breaths)
    used = _own(theirs)
    uses = sum(mine.values())
    score = _closeness(script_rate, own_rate, script.breaths)
    if uses:
        score = (score + sum(n for key, n in mine.items() if key in used) / uses) / 2
    unfamiliar = [key for key, _ in mine.most_common() if key not in theirs]
    return {"score": round(score, 3),
            "own": {"rate_pct": round(100 * own_rate), "top": used[:TOP_SHOWN]},
            "script": {"rate_pct": round(100 * script_rate), "unfamiliar": unfamiliar[:UNFAMILIAR_SHOWN],
                       "within_chance": _within_chance(script_rate, own_rate, script.breaths)}}


def _breath(script: Counts, own: Counts) -> Dict[str, Any]:
    """Breath length: how many of the script's breaths are inside the creator's usual span (their 5th to 95th
    percentile), and how close the script's middle length is to theirs."""
    low, middle, high = own.percentile(5), own.percentile(50), own.percentile(95)
    inside = _share(sum(n for units, n in script.lengths.items() if low <= units <= high), script.breaths)
    script_middle = script.percentile(50)
    centre = max(0.0, 1.0 - abs(script_middle - middle) / max(middle, 1))
    return {"score": round((inside + centre) / 2, 3),
            "own": {"p5": low, "median": middle, "p95": high},
            "script": {"median": script_middle, "inside_pct": round(100 * inside)}}


def _english(script: Counts, own: Counts) -> Dict[str, Any]:
    own_rate, script_rate = _share(own.english_breaths, own.breaths), _share(script.english_breaths, script.breaths)
    score = _closeness(script_rate, own_rate, script.breaths)
    words = list(script.english_words)
    if words:
        score = (score + sum(1 for word in words if word in own.english_words) / len(words)) / 2
    unfamiliar = [word for word, _ in script.english_words.most_common() if word not in own.english_words]
    return {"score": round(score, 3), "own": {"rate_pct": round(100 * own_rate)},
            "script": {"rate_pct": round(100 * script_rate), "unfamiliar": unfamiliar[:UNFAMILIAR_SHOWN],
                       "within_chance": _within_chance(script_rate, own_rate, script.breaths)}}


def compare(script: Counts, own: Counts) -> Optional[Dict[str, Any]]:
    """The four comparisons and their average for ``script`` against ``own``; None when either side is too thin."""
    if own.breaths < MIN_REFERENCE_BREATHS or script.breaths < MIN_SCRIPT_BREATHS:
        return None
    parts = {"breath": _breath(script, own), "endings": _habit(script, own, "endings"),
             "starts": _habit(script, own, "starts"), "english": _english(script, own)}
    return {"score": round(100 * sum(part["score"] for part in parts.values()) / len(parts)), "parts": parts}


class Reference:
    """The creator's material, counted once: the whole, each script on its own, and the range their own scripts score in."""

    def __init__(self, texts: Iterable[Any]) -> None:
        self.scripts: List[Counts] = [counts for counts in (Counts().add_text(text) for text in texts) if counts.breaths]
        self.all = Counts()
        for counts in self.scripts:
            self.all = self.all.plus(counts)
        self.band = self._band()

    def _band(self) -> Optional[Dict[str, int]]:
        """Each of the creator's scripts scored against the rest of their material; None with too few to say."""
        scores = []
        for counts in self.scripts:
            scored = compare(counts, self.all.without(counts))
            if scored is not None:
                scores.append(scored["score"])
        if len(scores) < MIN_BAND_SCRIPTS:
            return None
        scores.sort()
        return {"low": scores[0], "median": scores[len(scores) // 2], "high": scores[-1], "scripts": len(scores)}

    def facts(self) -> Dict[str, int]:
        return {"scripts": len(self.scripts), "breaths": self.all.breaths}


def score(lines: Any, reference: Reference) -> Dict[str, Any]:
    """The voice-match answer for a script's lines.

    ``{"available": True, "score", "band", "parts", "reference", "script"}``, or ``{"available": False,
    "reason", ...}`` with ``reason`` ``too_little_material`` (the creator's confirmed non-sponsored material
    holds under MIN_REFERENCE_BREATHS breaths) or ``too_short`` (the script holds under MIN_SCRIPT_BREATHS).
    ``band`` is None until MIN_BAND_SCRIPTS of the creator's own scripts could be scored.
    """
    counts = count_texts(script_text_lines(lines))
    answer: Dict[str, Any] = {"reference": reference.facts(), "script": {"breaths": counts.breaths},
                              "needed": {"reference_breaths": MIN_REFERENCE_BREATHS, "script_breaths": MIN_SCRIPT_BREATHS}}
    if reference.all.breaths < MIN_REFERENCE_BREATHS:
        return dict(answer, available=False, reason="too_little_material")
    compared = compare(counts, reference.all)
    if compared is None:
        return dict(answer, available=False, reason="too_short")
    return dict(answer, available=True, score=compared["score"], parts=compared["parts"], band=reference.band)


def line_fit(text: Any, reference: Reference) -> Optional[Dict[str, Any]]:
    """How one line sits against the creator's material, as plain facts: a single line is too short for a score.

    ``{"breaths", "inside", "unfamiliar"}``: how many breaths the line has, how many are inside the creator's
    usual length span, and the particles, connectors and English words in it that their material never has.
    None when the material is too thin to say.
    """
    if reference.all.breaths < MIN_REFERENCE_BREATHS:
        return None
    counts = Counts().add_text(text)
    own = reference.all
    low, high = own.percentile(5), own.percentile(95)
    unfamiliar = [key for kind in ("endings", "starts", "english_words")
                  for key in getattr(counts, kind) if key not in getattr(own, kind)]
    return {"breaths": counts.breaths,
            "inside": sum(n for units, n in counts.lengths.items() if low <= units <= high),
            "unfamiliar": unfamiliar[:UNFAMILIAR_SHOWN]}


def reference_for(store: Any) -> Reference:
    """The reference for a store's library: confirmed, non-sponsored material. Counted again only when the library changed.

    Counting reads every item's text, and a check runs after every save in the editor, so the result is
    kept on the store object together with the library's version (``Store.samples_version``).
    """
    version = store.samples_version()
    cached: Optional[Tuple[Any, Reference]] = getattr(store, "_voice_reference", None)
    if cached is not None and cached[0] == version:
        return cached[1]
    texts = [s["text"] for s in store.list_samples() if not s.get("needs_review") and not s.get("is_sponsored")]
    reference = Reference(texts)
    store._voice_reference = (version, reference)
    return reference
