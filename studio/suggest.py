"""Proposals for single lines of a script: other openings, rewordings, one-breath cuts, a critic's repairs.

A model writes the proposals (``prompts/08_line_editor.md``). Everything else here is code:

- each proposal is checked as if it stood in the script: the same findings any line gets (breath length,
  refused phrases, written-style and translated wording, advertising claims), next to the findings of the
  line as it stands, so the creator sees whether it is cleaner or not;
- each is compared with the creator's counted speech habits (``voicematch.line_fit``), and the whole
  script's voice-match number with the proposal in place is given next to the number now;
- proposals are put in order by those checks, not by the order the model gave them;
- nothing is applied. The creator picks one, or none.

Every text proposed is written down for its script (``Store.add_ai_suggestions``), so a line taken over
from a proposal is never presented as the creator's own edit (``pipeline.script_changes``).

Like every AI step: ``suggest_prompt`` builds the prompt, ``apply_suggest`` reads a reply, and ``suggest``
does both through the service chosen in settings. Copy-and-paste mode uses the first two.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from . import checks, corpus, creator, guardrails, hooks, learning, llm, pipeline, textstats, voicematch
from .store import Store

STAGE8 = "08_line_editor.md"
HOOKS, REWRITE, SHORTEN, CRITIC = "hooks", "rewrite", "shorten", "critic"
KINDS = (HOOKS, REWRITE, SHORTEN, CRITIC)
ASKED_FOR = {HOOKS: 3, REWRITE: 3, SHORTEN: 2, CRITIC: 8}      # how many proposals the prompt asks for
MAX_SHOWN = {HOOKS: 5, REWRITE: 5, SHORTEN: 4, CRITIC: 12}     # how many of a reply are kept at most
MAX_INSTRUCTION_CHARS = 300     # what the creator asked for, in their own words: a short request
MAX_SUGGESTION_CHARS = 600      # one proposed line; a spoken line is far shorter
MAX_NOTE_CHARS = 300            # the model's sentence about what it changed, and its English meaning of the line
OWN_SCRIPTS_CHARS = 3000        # the creator's own scripts that go along, to show how they speak
OWN_SCRIPT_CHARS = 800
OPENINGS_IN_PROMPT = 8


class SuggestError(ValueError):
    """A request for proposals that cannot be met. ``code``: ``not_found`` (no such script), ``bad_kind``,
    ``bad_line`` (no such line, or none given where one is needed), ``live_clip`` (the line is somebody's
    recorded words) or ``empty_line`` (nothing is written there yet)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clean_instruction(value: Any) -> str:
    """What the creator asked for, as one line of bounded length. Anything that is not text is no request."""
    return " ".join(value.split())[:MAX_INSTRUCTION_CHARS] if isinstance(value, str) else ""


def _is_live(line: Dict[str, Any]) -> bool:
    return str(line.get("audio_mode") or "").strip().upper() == "LIVE_CLIP"


def _target(store: Store, script_id: Any, kind: Any, line_index: Any) -> Tuple[Dict[str, Any], Optional[int]]:
    """The stored script and the index of the line the request is about (None for the critic, which reads all)."""
    script = store.get_script(script_id) if isinstance(script_id, str) else None
    if script is None:
        raise SuggestError("not_found")
    if kind not in KINDS:
        raise SuggestError("bad_kind")
    if kind == CRITIC:
        if not any(str(line.get("spoken_script_zh") or "").strip() and not _is_live(line) for line in script["lines"]):
            raise SuggestError("empty_line")
        return script, None
    if isinstance(line_index, bool) or not isinstance(line_index, int) or not 0 <= line_index < len(script["lines"]):
        raise SuggestError("bad_line")
    line = script["lines"][line_index]
    if _is_live(line):
        raise SuggestError("live_clip")
    if not str(line.get("spoken_script_zh") or "").strip():
        raise SuggestError("empty_line")
    return script, line_index


def suggest_prompt(store: Store, script_id: Any, kind: Any, line_index: Any = None,
                   instruction: Any = "") -> Tuple[str, str]:
    """``(system, user)`` asking for proposals of one kind for a stored script. Raises ``SuggestError``.

    ``kind``: ``hooks`` (other openings for the line), ``rewrite``, ``shorten`` (every breath within the
    longest the voice profile allows) or ``critic`` (the whole script; ``line_index`` is not used).
    ``instruction`` is the creator's own short request for a rewording.
    """
    script, index = _target(store, script_id, kind, line_index)
    dna, _ = store.load_dna()
    who = store.load_creator()
    dna = creator.apply_never(dna, who)
    samples = store.list_samples()
    confirmed = pipeline.confirmed_samples(samples)
    own_scripts = [{"title": a["title"], "creator_own_script": a["her_own_script"]}
                   for a in corpus.exemplars(confirmed, budget_chars=OWN_SCRIPTS_CHARS, per_script_chars=OWN_SCRIPT_CHARS)]
    openings = [{"title": o["title"], "opening": o["opening"], "times_usual": o["times_usual"]}
                for o in hooks.distinct(hooks.library(samples)["openers"])[:OPENINGS_IN_PROMPT]]
    stats = guardrails.script_stats({"script_lines": script["lines"]}, dna, script["target_sec"])
    lines = [{"line_no": no, "audio_mode": line.get("audio_mode") or "VOICEOVER",
              "spoken_script_zh": line.get("spoken_script_zh") or ""}
             for no, line in enumerate(script["lines"], start=1)]
    return pipeline._blueprint(STAGE8, {
        "creator_profile_json": pipeline._dump(creator.prompt_block(who)),
        "voice_dna_json": pipeline._dump(dna),
        "own_scripts_json": pipeline._dump(own_scripts),
        "own_openings_json": pipeline._dump(openings),
        "learned_rules_json": pipeline._dump(learning.prompt_rules(store.list_lessons())),
        "script_lines_json": pipeline._dump(lines),
        # One of the known names and numbers made here, never text from a request: these values are not encoded.
        "task": kind.upper(),
        "line_no": "null" if index is None else str(index + 1),
        "count": str(ASKED_FOR[kind]),
        "max_breath_units": str(int(stats["hard_max"])),
        # Typed by the creator: encoded like every other value, as one JSON string.
        "instruction": pipeline._inline(clean_instruction(instruction) if kind == REWRITE else ""),
    })


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _line_findings(payload: Dict[str, Any], line_no: int) -> List[Dict[str, Any]]:
    return [f for f in payload["findings"] if f["line_no"] == line_no]


def _counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    errors = sum(1 for f in findings if f["severity"] == guardrails.ERROR)
    return {"errors": errors, "reviews": len(findings) - errors}


def _match_score(payload: Dict[str, Any]) -> Optional[int]:
    match = payload["voice_match"]
    return match["score"] if match.get("available") else None


def _checked(store: Store, script: Dict[str, Any], index: int, proposal: Dict[str, str], kind: str,
             reference: voicematch.Reference, now: Dict[str, Any]) -> Dict[str, Any]:
    """One proposal with what the application's own checks say about it, were it to replace the line."""
    lines = copy.deepcopy(script["lines"])
    original = str(lines[index].get("spoken_script_zh") or "")
    lines[index]["spoken_script_zh"] = proposal["text"]
    after = checks.lint_payload(store, lines, script["target_sec"], script["keywords"], script["post"])
    findings = _line_findings(after, index + 1)
    out = {
        "line_index": index, "original": original, "text": proposal["text"], "english": proposal["english"],
        "why_zh": proposal["why_zh"], "why_en": proposal["why_en"],
        "units": textstats.spoken_units(textstats.strip_cues(proposal["text"])),
        "findings": findings, "problems": _counts(findings), "problems_now": _counts(_line_findings(now, index + 1)),
        "fit": voicematch.line_fit(proposal["text"], reference),
        "voice_match": _match_score(after), "estimated_sec": after["stats"].get("estimated_sec"),
    }
    if kind == HOOKS:
        out["patterns"] = hooks.describe(proposal["text"])["patterns"]
    if kind == SHORTEN:
        out["one_breath"] = not any(f["category"] == guardrails.CADENCE_TOO_LONG for f in findings)
    return out


def _order(item: Dict[str, Any]) -> Tuple[int, int, float, int]:
    """Fewest findings first; then the one with more of its breaths inside the creator's usual length, and
    fewer words their material never has."""
    fit = item["fit"] or {"breaths": 0, "inside": 0, "unfamiliar": []}
    outside = 1.0 - (fit["inside"] / fit["breaths"] if fit["breaths"] else 0.0)
    return item["problems"]["errors"], item["problems"]["reviews"], outside, len(fit["unfamiliar"])


def apply_suggest(store: Store, result: Any, script_id: Any, kind: Any, line_index: Any = None) -> Dict[str, Any]:
    """Read a model's proposals for a stored script, check each one, and return them for the creator to choose from.

    ``{"kind", "line_index", "suggestions", "voice_match_now", "left_out"}``. Each suggestion:
    ``line_index``, ``original`` (the line as stored now), ``text``, ``english``, ``why_zh``, ``why_en``,
    ``units``, ``findings`` (what the checks say about the line with the proposal in it), ``problems`` and
    ``problems_now`` (``{"errors", "reviews"}`` with the proposal, and for the line as it stands), ``fit``
    (``voicematch.line_fit``), ``voice_match`` (the whole script's number with the proposal in place, None
    when there is none) and ``estimated_sec``. Openings also carry ``patterns``; cuts carry ``one_breath``.

    For one line the proposals are ordered by the checks, cleanest first. The critic's come in script order.
    ``left_out`` counts proposals that were not usable: no text, the line's own words again, a repeat, a
    line that does not exist or is a live clip, or more than are shown.

    Nothing is changed in the script. Every proposed text is recorded for the script. A reply with nothing
    usable raises ``llm.LLMError("empty_result")``, except for the critic: finding nothing to repair is an
    answer. Raises ``SuggestError`` as ``suggest_prompt`` does.
    """
    script, index = _target(store, script_id, kind, line_index)
    raw = result.get("suggestions") if isinstance(result, dict) else None
    raw = raw if isinstance(raw, list) else []
    wanted: List[Tuple[int, Dict[str, str]]] = []
    seen = set()
    left_out = 0
    for item in raw:
        if not isinstance(item, dict):
            left_out += 1
            continue
        at = index
        if kind == CRITIC:
            no = item.get("line_no")
            at = no - 1 if isinstance(no, int) and not isinstance(no, bool) else None
            if at is None or not 0 <= at < len(script["lines"]) or _is_live(script["lines"][at]):
                left_out += 1
                continue
        value = item.get("spoken_script_zh")
        text = value.strip()[:MAX_SUGGESTION_CHARS] if isinstance(value, str) else ""
        key = learning.spoken_key(text)
        same_as_now = key == learning.spoken_key(str(script["lines"][at].get("spoken_script_zh") or ""))
        # The critic gets one repair a line; for one line, the same words twice are one proposal.
        mark = at if kind == CRITIC else key
        if not key or same_as_now or mark in seen or len(wanted) >= MAX_SHOWN[kind]:
            left_out += 1
            continue
        seen.add(mark)
        wanted.append((at, {"text": text, "english": _text(item.get("english_partner_translation"), MAX_NOTE_CHARS),
                            "why_zh": _text(item.get("why_zh"), MAX_NOTE_CHARS),
                            "why_en": _text(item.get("why_en"), MAX_NOTE_CHARS)}))
    if not wanted and kind != CRITIC:
        raise llm.LLMError("Model reply contained no usable proposal", "empty_result")
    reference = voicematch.reference_for(store)
    now = checks.lint_payload(store, script["lines"], script["target_sec"], script["keywords"], script["post"])
    suggestions = [_checked(store, script, at, proposal, kind, reference, now) for at, proposal in wanted]
    if kind == CRITIC:
        suggestions.sort(key=lambda item: item["line_index"])
    else:
        suggestions.sort(key=_order)            # a stable sort: equal ones stay in the order the model gave
    store.add_ai_suggestions(script["id"], [item["text"] for item in suggestions])
    return {"kind": kind, "line_index": index, "suggestions": suggestions, "voice_match_now": _match_score(now),
            "left_out": left_out}


def suggest(store: Store, script_id: Any, kind: Any, line_index: Any = None, instruction: Any = "",
            transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Ask the AI service in settings for proposals and return them checked (``apply_suggest``)."""
    system, user = suggest_prompt(store, script_id, kind, line_index, instruction)
    return apply_suggest(store, pipeline.complete(store, system, user, transport), script_id, kind, line_index)
