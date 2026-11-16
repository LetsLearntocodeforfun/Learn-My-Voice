"""Lessons from the creator's own edits to an AI draft.

Nothing here calls a model. The draft and the version the creator kept are
compared line by line, and only real differences in wording come back; the
creator then accepts or dismisses each one. Accepted lessons reach later drafts
through ``prompt_rules``; phrases they keep removing become suggestions for the
never-say list through ``recurring_removals``.

Spacing, punctuation, breath marks, bracketed cues, full-width forms and the
order of lines are not wording, so changing only those is not a change.
"""
from __future__ import annotations

import difflib
import unicodedata
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from . import textstats

REWRITTEN, DELETED, ADDED = "rewritten", "deleted", "added"

SIMILAR_ENOUGH = 0.5        # share of spoken characters two lines must have in common to be the same line reworded
MIN_PHRASE_CHARS = 2        # a single character is noise, not a phrase
MIN_SCRIPTS = 2             # a removal is a habit once it happens in this many scripts
DEFAULT_RULE_LIMIT = 10     # the draft prompt asks for the top ten

_BREAK = "\n"
# The characters textstats.split_clauses treats as the end of a breath.
_BREATH_BREAKS = "/\n\uff0c,\u3002\uff01!\uff1f?\uff1b;\uff5e~\u2026"
_UNSEEN_CATEGORIES = ("Mn", "Me", "Cf")            # combining marks and zero-width characters


class _Token(NamedTuple):
    value: str      # one CJK character, one whole Latin word or number, or _BREAK
    start: int      # span in the line's source text
    end: int


class _Line(NamedTuple):
    no: int                        # 1-based position in the list it came from
    text: str                      # as written
    source: str                    # cues removed; phrases are cut from this
    tokens: Tuple[_Token, ...]
    key: str                       # the spoken characters alone


def _is_cjk(ch: str) -> bool:
    return ord(ch) >= 0x2E80 and ch.isalnum()


def _tokenize(source: str) -> List[_Token]:
    """Spoken tokens of a line. Latin words stay whole so a diff never reports half a word."""
    tokens: List[_Token] = []
    word, word_start, word_end = "", 0, 0
    for i, raw in enumerate(source):
        breath_break = raw in _BREATH_BREAKS
        for ch in ("" if breath_break else unicodedata.normalize("NFKC", raw)):
            if ch.isalnum() and not _is_cjk(ch):
                word, word_start, word_end = word + ch, (word_start if word else i), i + 1
                continue
            if unicodedata.category(ch) in _UNSEEN_CATEGORIES:
                continue
            if word:
                tokens.append(_Token(word, word_start, word_end))
                word = ""
            if ch.isalnum():
                tokens.append(_Token(ch, i, i + 1))
        if breath_break:
            if word:
                tokens.append(_Token(word, word_start, word_end))
                word = ""
            if tokens and tokens[-1].value != _BREAK:
                tokens.append(_Token(_BREAK, i, i + 1))
    if word:
        tokens.append(_Token(word, word_start, word_end))
    while tokens and tokens[-1].value == _BREAK:
        tokens.pop()
    return tokens


def _read(text: str, no: int = 0) -> _Line:
    source = unicodedata.normalize("NFC", textstats.strip_cues(text))
    tokens = tuple(_tokenize(source))
    return _Line(no, text.strip(), source, tokens, "".join(t.value for t in tokens if t.value != _BREAK))


def _read_lines(lines: Any) -> List[_Line]:
    """Lines that have something spoken in them; anything else in the list is skipped."""
    out: List[_Line] = []
    for no, raw in enumerate(lines if isinstance(lines, (list, tuple)) else [], start=1):
        value = raw.get("spoken_script_zh") if isinstance(raw, dict) else None
        if isinstance(value, str):
            line = _read(value, no)
            if line.key:
                out.append(line)
    return out


def _key(text: Any) -> str:
    return _read(text).key if isinstance(text, str) else ""


# ---- phrases inside a line ----------------------------------------------------
def _phrases(line: _Line, start: int, end: int) -> List[Tuple[str, str]]:
    """``(key, text as written)`` for each run of tokens in ``line.tokens[start:end]`` that stays in one breath."""
    out: List[Tuple[str, str]] = []
    run: List[_Token] = []
    for token in line.tokens[start:end] + (_Token(_BREAK, 0, 0),):
        if token.value != _BREAK:
            run.append(token)
            continue
        key = "".join(t.value for t in run)
        if len(key) >= MIN_PHRASE_CHARS:
            out.append((key, line.source[run[0].start:run[-1].end]))
        run = []
    return out


def _display(phrases: List[Tuple[str, str]], drop: Set[str] = frozenset()) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for key, text in phrases:
        if key not in seen and key not in drop:
            seen.add(key)
            out.append(text)
    return out


def _changed_phrases(ai: _Line, final: _Line) -> Tuple[List[str], List[str]]:
    """What was taken out of the AI line and what was put in, as whole runs within a breath."""
    a = [t.value for t in ai.tokens]
    b = [t.value for t in final.tokens]
    removed: List[Tuple[str, str]] = []
    added: List[Tuple[str, str]] = []
    span: Optional[List[int]] = None
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag != "equal":
            span = [i1, i2, j1, j2] if span is None else [span[0], i2, span[2], j2]
            continue
        # One character that happens to survive between two changes is part of the rewrite, not an anchor:
        # 真的绝绝子 -> 也太绝了 is one change, not 真的 -> 也太 plus 绝子 -> 了.
        chance = i2 - i1 == 1 and len(a[i1]) == 1 and _is_cjk(a[i1]) and 0 < n < len(ops) - 1
        if chance and span is not None:
            span = [span[0], i2, span[2], j2]
        elif span is not None:
            removed += _phrases(ai, span[0], span[1])
            added += _phrases(final, span[2], span[3])
            span = None
    if span is not None:
        removed += _phrases(ai, span[0], span[1])
        added += _phrases(final, span[2], span[3])
    moved = {key for key, _ in removed} & {key for key, _ in added}    # same words elsewhere in the line
    return _display(removed, moved), _display(added, moved)


# ---- aligning two versions ----------------------------------------------------
def _run_spelling(key: str, lines: List[_Line], free: Set[int]) -> List[int]:
    """Two or more neighbouring unmatched lines that together say exactly ``key`` (a line split or merged)."""
    for start in sorted(free):
        run: List[int] = []
        pos, j = 0, start
        while j in free and key.startswith(lines[j].key, pos):
            pos += len(lines[j].key)
            run.append(j)
            j += 1
            if pos == len(key):
                break
        if pos == len(key) and len(run) >= 2:
            return run
    return []


def _in_order(pairs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """The longest run of matched lines that kept their order; lines moved elsewhere are left out."""
    pairs = sorted(pairs)
    best: List[Tuple[int, int]] = []                # (length of the chain ending here, previous link)
    for n, (_, j) in enumerate(pairs):
        length, previous = 1, -1
        for m in range(n):
            if pairs[m][1] < j and best[m][0] + 1 > length:
                length, previous = best[m][0] + 1, m
        best.append((length, previous))
    if not pairs:
        return []
    n = max(range(len(pairs)), key=lambda k: (best[k][0], -k))
    chain: List[Tuple[int, int]] = []
    while n >= 0:
        chain.append(pairs[n])
        n = best[n][1]
    return chain[::-1]


def _change(kind: str, ai: Optional[_Line], final: Optional[_Line], removed: List[str], added: List[str]) -> Dict[str, Any]:
    return {
        "kind": kind, "ai": ai.text if ai else "", "final": final.text if final else "",
        "removed": removed, "added": added,
        "ai_line_no": ai.no if ai else None, "final_line_no": final.no if final else None,
    }


def line_changes(ai_lines: Any, final_lines: Any) -> List[Dict[str, Any]]:
    """Real differences between the AI draft and the version the creator kept.

    Both arguments are lists of script line dicts; the spoken text is read from
    ``spoken_script_zh``. Returns dicts in script order with:

    - ``kind``: ``rewritten`` | ``deleted`` | ``added``
    - ``ai`` / ``final``: the line as written in each version (``""`` for the missing side)
    - ``removed`` / ``added``: phrases of two or more characters taken out of / put into the line.
      For a deleted or added line these are its breaths.
    - ``ai_line_no`` / ``final_line_no``: 1-based position in each list (``None`` for the missing side)

    A line is ``rewritten`` when the two versions share at least half their spoken
    characters, wherever the line now sits, or when exactly one AI line was replaced
    by exactly one new line in the same place. Anything less certain is reported as
    a deletion and an addition rather than guessed at. Unchanged lines, moved lines,
    a line split in two or two merged into one, and edits to spacing, punctuation,
    breath marks or bracketed cues are not reported.
    """
    ai, final = _read_lines(ai_lines), _read_lines(final_lines)
    free_ai, free_final = set(range(len(ai))), set(range(len(final)))
    same: List[Tuple[int, int]] = []
    rewritten: List[Tuple[int, int]] = []

    def match(pairs: List[Tuple[int, int]], i: int, j: int) -> None:
        pairs.append((i, j))
        free_ai.discard(i)
        free_final.discard(j)

    # 1. The same words, wherever the line ended up.
    waiting: Dict[str, List[int]] = {}
    for j, line in enumerate(final):
        waiting.setdefault(line.key, []).append(j)
    for i, line in enumerate(ai):
        if waiting.get(line.key):
            match(same, i, waiting[line.key].pop(0))

    # 2. The same words across a different number of lines.
    for i in sorted(free_ai):
        run = _run_spelling(ai[i].key, final, free_final)
        if run:
            match(same, i, run[0])
            free_final.difference_update(run)
    for j in sorted(free_final):
        run = _run_spelling(final[j].key, ai, free_ai)
        if run:
            match(same, run[0], j)
            free_ai.difference_update(run)

    # 3. Reworded lines: most alike first, nearest position on ties.
    scored = []
    for i in sorted(free_ai):
        for j in sorted(free_final):
            ratio = difflib.SequenceMatcher(None, ai[i].key, final[j].key, autojunk=False).ratio()
            if ratio >= SIMILAR_ENOUGH:
                scored.append((-ratio, abs(i - j), i, j))
    for _, _, i, j in sorted(scored):
        if i in free_ai and j in free_final:
            match(rewritten, i, j)

    # 4. One line replaced by one line between the same neighbours.
    anchors = _in_order(same + rewritten)
    slots: Dict[int, Tuple[List[int], List[int]]] = {}
    for i in sorted(free_ai):
        slots.setdefault(sum(1 for a, _ in anchors if a < i), ([], []))[0].append(i)
    for j in sorted(free_final):
        slots.setdefault(sum(1 for _, b in anchors if b < j), ([], []))[1].append(j)
    for in_ai, in_final in slots.values():
        if len(in_ai) == 1 and len(in_final) == 1:
            match(rewritten, in_ai[0], in_final[0])

    changes: List[Tuple[Tuple[int, int, int], Dict[str, Any]]] = []
    for i, j in rewritten:
        removed, added = _changed_phrases(ai[i], final[j])
        changes.append(((j, 0, 0), _change(REWRITTEN, ai[i], final[j], removed, added)))
    for j in free_final:
        breaths = _display(_phrases(final[j], 0, len(final[j].tokens)))
        changes.append(((j, 0, 0), _change(ADDED, None, final[j], [], breaths)))
    for i in free_ai:
        after = max((b for a, b in anchors if a < i), default=-1)      # shown where the line used to be
        breaths = _display(_phrases(ai[i], 0, len(ai[i].tokens)))
        changes.append(((after, 1, i), _change(DELETED, ai[i], None, breaths, [])))
    return [change for _, change in sorted(changes, key=lambda c: c[0])]


# ---- habits across scripts ----------------------------------------------------
def _strings(value: Any) -> List[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def recurring_removals(changes_by_script: Any) -> List[Dict[str, Any]]:
    """Phrases the creator took out in at least two different scripts: suggestions for the never-say list.

    ``changes_by_script`` maps a script id to that script's ``line_changes``
    result (a plain list of such results also works; positions then stand in for
    ids). Returns ``[{"phrase", "scripts", "script_ids"}]``, most scripts first.

    A phrase counts for a script when it was removed there on its own or as part
    of a longer removed phrase. Only phrases removed on their own at least once
    are suggested, and never one that appears in anything the creator added.
    """
    if isinstance(changes_by_script, dict):
        scripts = list(changes_by_script.items())
    elif isinstance(changes_by_script, (list, tuple)):
        scripts = list(enumerate(changes_by_script))
    else:
        scripts = []

    removed_in: List[Tuple[Any, Set[str]]] = []
    display: Dict[str, str] = {}
    added: Set[str] = set()
    for script_id, changes in scripts:
        removed: Set[str] = set()
        for change in changes if isinstance(changes, (list, tuple)) else []:
            if not isinstance(change, dict):
                continue
            for phrase in _strings(change.get("removed")):
                key = _key(phrase)
                if len(key) >= MIN_PHRASE_CHARS:
                    removed.add(key)
                    display.setdefault(key, phrase.strip())
            added.update(_key(phrase) for phrase in _strings(change.get("added")))
        removed_in.append((script_id, removed))

    out: List[Dict[str, Any]] = []
    for key, phrase in display.items():
        if any(key in other for other in added):
            continue
        ids = [script_id for script_id, removed in removed_in if any(key in other for other in removed)]
        if len(ids) >= MIN_SCRIPTS:
            out.append({"phrase": phrase, "scripts": len(ids), "script_ids": ids})
    out.sort(key=lambda r: (-r["scripts"], -len(_key(r["phrase"])), r["phrase"]))
    return out


# ---- what the draft prompt receives -------------------------------------------
def prompt_rules(lessons: Any, limit: int = DEFAULT_RULE_LIMIT) -> List[Dict[str, Any]]:
    """Accepted lessons as the list that fills ``learned_edit_preferences`` in the draft prompt.

    That block is described to the model as "what AI wrote vs what the creator
    changed it to", so each rule says exactly that and nothing else:

    - ``{"ai_wrote": ..., "changed_to": ...}`` for a rewritten line
    - ``{"ai_wrote": ..., "deleted": true}`` for a line the creator removed
    - ``{"added": ...}`` for a line the creator wrote themselves
    - plus ``"note"`` when the creator explained why

    Lessons are dicts with ``ai``, ``final`` and an optional ``note``. They are
    taken in the order given, so pass the ones that matter most first; the first
    ``limit`` usable ones are returned. A lesson that changes no wording, or
    repeats an earlier one, is skipped.
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = DEFAULT_RULE_LIMIT
    rules: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for lesson in lessons if isinstance(lessons, (list, tuple)) else []:
        if len(rules) >= limit:
            break
        if not isinstance(lesson, dict):
            continue
        ai, final, note = (lesson.get(field) for field in ("ai", "final", "note"))
        ai = ai.strip() if isinstance(ai, str) else ""
        final = final.strip() if isinstance(final, str) else ""
        signature = (_key(ai), _key(final))
        if signature[0] == signature[1] or signature in seen:
            continue
        seen.add(signature)
        if signature[0] and signature[1]:
            rule: Dict[str, Any] = {"ai_wrote": ai, "changed_to": final}
        elif signature[0]:
            rule = {"ai_wrote": ai, "deleted": True}
        else:
            rule = {"added": final}
        if isinstance(note, str) and note.strip():
            rule["note"] = " ".join(note.split())
        rules.append(rule)
    return rules
