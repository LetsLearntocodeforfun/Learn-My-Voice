"""Proposals for single lines of a script: other openings, rewordings, one-breath cuts, a critic's repairs.

A model writes the proposals (``prompts/08_line_editor.md``). Everything else here is code:

- each proposal is checked as if it stood in the script: the same findings any line gets (breath length,
  refused phrases, written-style and translated wording, advertising claims, with the word list as the
  creator keeps it), next to the findings of the line as it stands, so the creator sees whether it is
  cleaner or not. A hint that a line may sound written is shown with the proposal but is not counted
  among its problems;
- each is compared with the creator's counted speech habits (``voicematch.line_fit``), and the whole
  script's voice-match number with the proposal in place is given next to the number now;
- proposals are put in order by those checks, not by the order the model gave them;
- nothing is applied. The creator picks one, or none.

Every text proposed is written down for its script (``Store.add_ai_suggestions``), so a line taken over
from a proposal is never presented as the creator's own edit (``pipeline.script_changes``).

The critic also says why it picked a line (the Critic Addendum of the prompt). A reason is a model's reading,
so none of it is shown as a fact:

- the kind of problem is kept only when it is one of ``ISSUES``, and comes with whether the rule checks found
  the same kind of thing in that line (``rules_agree``);
- the words it points at are kept only when they stand in the line, and are looked up in the creator's material;
- what it says about how the creator talks (``voice_claims``) is looked up, phrase by phrase, in the
  non-sponsored material the creator wrote or confirmed, with the matcher the voice review uses
  (``evidence.verify_profile``). The answer carries how often each phrase was found; what was not found is
  marked so, and a page shows it as the model's opinion;
- a repair that is worse than the line it replaces, by the counts above, says so (``worse``).

A critic's reply that is not of the shape the prompt asks for is refused (``llm.LLMError("bad_shape")``).

Like every AI step: ``suggest_prompt`` builds the prompt, ``apply_suggest`` reads a reply, and ``suggest``
does both through the service chosen in settings. Copy-and-paste mode uses the first two.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from . import checks, corpus, creator, evidence, guardrails, hooks, learning, llm, pipeline, textstats, voicematch
from .store import Store

STAGE8 = "08_line_editor.md"
HOOKS, REWRITE, SHORTEN, CRITIC = "hooks", "rewrite", "shorten", "critic"
KINDS = (HOOKS, REWRITE, SHORTEN, CRITIC)
ASKED_FOR = {HOOKS: 3, REWRITE: 3, SHORTEN: 2, CRITIC: 8}      # how many proposals the prompt asks for
MAX_SHOWN = {HOOKS: 5, REWRITE: 5, SHORTEN: 4, CRITIC: 12}     # how many of a reply are kept at most
MAX_RAW_ITEMS = 64              # raw candidates read from one reply; any beyond are counted in left_out
MAX_FINDINGS_SHOWN = 16         # rule findings listed per proposal; counts still reflect all of them
MAX_INSTRUCTION_CHARS = 300     # what the creator asked for, in their own words: a short request
MAX_SUGGESTION_CHARS = 600      # one proposed line; a spoken line is far shorter
MAX_NOTE_CHARS = 300            # the model's sentence about what it changed, and its English meaning of the line
OWN_SCRIPTS_CHARS = 3000        # the creator's own scripts that go along, to show how they speak
OWN_SCRIPT_CHARS = 800
OPENINGS_IN_PROMPT = 8

# ---- the critic's reasons (the Critic Addendum of the prompt) ----
CRITIC_SECTION = "Critic Addendum"      # the heading in the blueprint under which the addendum stands
# The kinds of problem a critic may name. Anything else it writes there is dropped. Advertising wording is not
# among them: that is the word list's to flag, and the word list says what each entry rests on.
ISSUES = ("TRANSLATIONESE", "BOOKISH_MANDARIN", "GENERIC_XHS_SLOP", "CADENCE_TOO_LONG", "UNNATURAL_CODESWITCH",
          "STIFF_AD_TONE", "NOT_THEIR_VOICE")
# For each kind, the categories of the rule checks that say the same thing about a line. The rules report written
# wording and translated wording from one list, so either answers for both. A kind that is not here has no rule.
_SAME_KIND = {
    "TRANSLATIONESE": (guardrails.TRANSLATIONESE, guardrails.BOOKISH_MANDARIN, guardrails.AI_TELL),
    "BOOKISH_MANDARIN": (guardrails.TRANSLATIONESE, guardrails.BOOKISH_MANDARIN, guardrails.AI_TELL),
    "GENERIC_XHS_SLOP": (guardrails.GENERIC_XHS_SLOP,),
    "CADENCE_TOO_LONG": (guardrails.CADENCE_TOO_LONG,),
    "UNNATURAL_CODESWITCH": (guardrails.UNNATURAL_CODESWITCH,),
}
SAYS, NEVER_SAYS, OPENS_WITH = "says", "never_says", "opens_with"      # what a claim about the creator's voice asserts
CLAIMS = (SAYS, NEVER_SAYS, OPENS_WITH)
MAX_FLAGGED_CHARS = 60          # the words of a line a critic points at
MAX_CLAIMS = 3                  # claims about the creator's voice that are looked up for one line
MAX_CLAIM_CHARS = 40            # the phrase of one claim
MAX_TITLE_CHARS = 60            # the title of an item a phrase was found in, as it is handed out


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
    system, user = pipeline._blueprint(STAGE8, {
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
        "max_breath_units": str(int(stats["hard_max"] if stats["hard_max"] is not None else 16)),
        # Typed by the creator: encoded like every other value, as one JSON string.
        "instruction": pipeline._inline(clean_instruction(instruction) if kind == REWRITE else ""),
    })
    if kind == CRITIC:      # the other kinds read exactly the prompt they read before there was an addendum
        user = "%s\n\n%s" % (user, critic_addendum())
    return system, user


def critic_addendum() -> str:
    """What is added to the end of the critic's user prompt: the fields that say why a line was picked.

    The text is the ``xml`` block under the ``Critic Addendum`` heading of the blueprint, with the limits filled
    in. Nothing from a script, the library or a request goes into it. Raises ``pipeline.PromptError`` when the
    blueprint has no such block.
    """
    text = (pipeline.PROMPTS_DIR / STAGE8).read_text(encoding="utf-8")
    found = re.search(r"^#{2,3} [^\n]*%s[^\n]*\n.*?```xml\n(.*?)\n```" % re.escape(CRITIC_SECTION), text, re.S | re.M)
    if not found:
        raise pipeline.PromptError("No ```xml block under a '%s' heading in %s" % (CRITIC_SECTION, STAGE8))
    return pipeline.render(found.group(1), {"max_flagged_chars": str(MAX_FLAGGED_CHARS), "max_claims": str(MAX_CLAIMS),
                                            "max_claim_chars": str(MAX_CLAIM_CHARS)})


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _line_findings(payload: Dict[str, Any], line_no: int) -> List[Dict[str, Any]]:
    return [f for f in payload["findings"] if f["line_no"] == line_no]


def _hints(findings: List[Dict[str, Any]]) -> int:
    """How many of ``findings`` are only hints that the line may sound written (``guardrails.AI_TELL``)."""
    return sum(1 for f in findings if f["category"] == guardrails.AI_TELL)


def _counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many findings are errors and how many ask for a second look. A hint that the line may sound written is neither."""
    rules = [f for f in findings if f["category"] != guardrails.AI_TELL]
    errors = sum(1 for f in rules if f["severity"] == guardrails.ERROR)
    return {"errors": errors, "reviews": len(rules) - errors}


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
    all_findings = _line_findings(after, index + 1)
    findings = all_findings[:MAX_FINDINGS_SHOWN]
    out = {
        "line_index": index, "original": original, "text": proposal["text"], "english": proposal["english"],
        "why_zh": proposal["why_zh"], "why_en": proposal["why_en"],
        "units": textstats.spoken_units(textstats.strip_cues(proposal["text"])),
        "findings": findings, "findings_left_out": max(0, len(all_findings) - len(findings)),
        "problems": _counts(all_findings), "problems_now": _counts(_line_findings(now, index + 1)),
        "hints": _hints(all_findings),
        "fit": voicematch.line_fit(proposal["text"], reference),
        "voice_match": _match_score(after), "estimated_sec": after["stats"].get("estimated_sec"),
    }
    if kind == HOOKS:
        out["patterns"] = hooks.describe(proposal["text"])["patterns"]
    if kind == SHORTEN:
        out["one_breath"] = not any(f["category"] == guardrails.CADENCE_TOO_LONG for f in all_findings)
    return out


def _reasons(item: Dict[str, Any], line_text: str) -> Dict[str, Any]:
    """What a critic's suggestion says about why it picked the line, cut to what may be looked up.

    ``{"issue", "flagged", "flagged_missing", "claims"}``. ``issue`` is one of ``ISSUES`` or "". ``flagged`` is
    the words pointed at when they stand in the line (spacing and punctuation aside), else ""; when words were
    named that do not stand there, ``flagged_missing`` is true and they are not kept: words that are not in the
    line are never shown as a quote from it. ``claims`` are at most MAX_CLAIMS ``(kind, phrase)`` pairs; a claim
    of an unknown kind, or with nothing in its phrase, is dropped.
    """
    rdict = item.get("reason") if isinstance(item.get("reason"), dict) else {}
    issue = item.get("issue") or item.get("issue_category") or rdict.get("issue") or rdict.get("issue_category")
    issue = issue.strip().upper() if isinstance(issue, str) else ""
    if issue == "STIFF_SPONSOR_TONE":
        issue = "STIFF_AD_TONE"
    raw_flagged = item.get("flagged") if item.get("flagged") is not None else rdict.get("flagged")
    pointed = _text(raw_flagged, MAX_FLAGGED_CHARS)
    key = learning.spoken_key(pointed)
    in_line = bool(key) and key in learning.spoken_key(line_text)
    claims: List[Tuple[str, str]] = []
    raw = item.get("voice_claims") if item.get("voice_claims") is not None else rdict.get("voice_claims")
    for claim in (raw if isinstance(raw, list) else [])[:MAX_CLAIMS * 4]:      # a few past the limit, for ones that are dropped
        if len(claims) == MAX_CLAIMS:
            break
        if not isinstance(claim, dict):
            continue
        kind = claim.get("claim")
        kind = kind.strip().lower() if isinstance(kind, str) else ""
        phrase = _text(claim.get("phrase"), MAX_CLAIM_CHARS)
        if kind in CLAIMS and phrase:
            claims.append((kind, phrase))
    return {"issue": issue if issue in ISSUES else "", "flagged": pointed if in_line else "",
            "flagged_missing": bool(pointed) and not in_line, "claims": claims}


def _note_pair(item: Dict[str, Any]) -> Tuple[str, str]:
    """Extract ``(why_zh, why_en)`` from flat fields, a plain-string ``reason``, or a structured ``reason`` dict."""
    raw_reason = item.get("reason")
    rdict = raw_reason if isinstance(raw_reason, dict) else {}
    fallback = raw_reason if isinstance(raw_reason, str) else (rdict.get("text") if isinstance(rdict.get("text"), str) else "")
    zh = item.get("why_zh") or rdict.get("why_zh") or fallback
    en = item.get("why_en") or rdict.get("why_en") or fallback
    return _text(zh, MAX_NOTE_CHARS), _text(en, MAX_NOTE_CHARS)


def _dna_sets(store: Store) -> Tuple[set, set]:
    """Canonical phrases in the creator's own saved Voice DNA and declared never-say list (never the example DNA)."""
    dna, is_example = store.load_dna()
    own = creator.apply_never(dna if not is_example else {}, store.load_creator())
    uses, banned = set(), set()
    for entry in evidence._entries(own):
        canon = evidence._canon(evidence._without_note(entry.text, entry.kind, entry.where))
        if not canon:
            continue
        if entry.kind == evidence.NEVER:
            banned.add(canon)
        else:
            uses.add(canon)
    return uses, banned


def _is_particle(phrase: str) -> bool:
    """Whether a phrase is one sentence-final particle on its own: it is then looked for at the end of breaths."""
    core = phrase.strip("\uff5e~ \u3000")
    return len(core) == 1 and core in corpus.FINAL_PARTICLES


def _look_up(store: Store, queries: List[Tuple[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Look phrases up in the creator's own material. Nothing here is a model's opinion.

    ``queries`` are ``(where, phrase)`` with ``where`` ``anywhere`` (the phrase, in the material), ``breath_end``
    (a particle, at the end of breaths) or ``opening`` (the phrase, in the openings of the material). Returns one
    answer for each, in order, and what was looked in: ``{"items", "openings"}``.

    An answer is ``{"where", "phrase", "found", "count", "scripts", "titles", "of"}``. ``found`` is True, False,
    or None when nothing could be looked up (no material, or nothing in the phrase to look for). ``count`` is how
    often it stands there, ``scripts`` in how many items, ``titles`` up to three of them, ``of`` how many items
    (or openings) were looked in.

    The material is what every check of wording uses: items the creator wrote or confirmed
    (``pipeline.confirmed_samples``), non-sponsored. The matcher is the voice review's (``evidence``): a habit
    looked for anywhere, a particle at the end of a breath. The work is one pass over the material for each
    distinct phrase, and the phrases of one read-through are bounded (MAX_SHOWN lines, MAX_CLAIMS each).
    """
    samples = store.list_samples()
    material = [{"title": s["title"], "text": s["text"], "is_sponsored": False}
                for s in pipeline.confirmed_samples(samples) if not s.get("is_sponsored") and s["text"].strip()]
    openings = [{"title": o["title"], "text": o["opening"], "is_sponsored": False} for o in hooks.library(samples)["openers"]]
    dna_uses, dna_banned = _dna_sets(store)
    looked_in = {"items": len(material), "openings": len(openings)}
    answers: List[Optional[Dict[str, Any]]] = [None] * len(queries)
    for where, searched in (("anywhere", material), ("breath_end", material), ("opening", openings)):
        wanted = [(at, phrase) for at, (place, phrase) in enumerate(queries) if place == where]
        if not wanted:
            continue
        phrases = [phrase for _, phrase in wanted]
        if where == "breath_end":
            profile: Dict[str, Any] = {"lexical_fingerprint": {"modal_particles": {"primary": [{"particle": p} for p in phrases]}}}
            path = "lexical_fingerprint.modal_particles.primary[%d].particle"
        else:
            profile = {"persona_summary": {"audience_address_terms": {"uses": phrases}}}
            path = "persona_summary.audience_address_terms.uses[%d]"
        by_path = {item["path"]: item for item in evidence.verify_profile(profile, searched)["items"]}
        for n, (at, phrase) in enumerate(wanted):
            item = by_path.get(path % n)
            status = item["status"] if item else evidence.NOT_CHECKABLE
            canon = evidence._canon(evidence._without_note(phrase, evidence.TERM, where))
            in_dna = "banned" if (canon and canon in dna_banned) else ("uses" if (canon and canon in dna_uses) else None)
            answers[at] = {
                "where": where, "phrase": phrase,
                "found": None if status == evidence.NOT_CHECKABLE else status == evidence.FOUND,
                "count": item["count"] if item else 0, "scripts": item["scripts"] if item else 0,
                "titles": [str(title)[:MAX_TITLE_CHARS] for title in (item["titles"] if item else [])],
                "of": len(searched), "in_dna": in_dna,
            }
    return [answer for answer in answers if answer is not None], looked_in


def _rules_agree(issue: str, findings_now: List[Dict[str, Any]]) -> Optional[bool]:
    """Whether the rule checks found, in the line as it stands, the kind of thing the critic names.

    None for a kind no rule looks for, and when the critic named none. The rules know a list of wordings, not
    every one: False says what the rules found, not that the critic is wrong.
    """
    same = _SAME_KIND.get(issue)
    if same is None:
        return None
    return any(finding["category"] in same for finding in findings_now)


def _worse(item: Dict[str, Any], hints_now: int, fit_now: Optional[Dict[str, Any]], match_now: Optional[int]) -> List[Dict[str, Any]]:
    """In what a checked proposal is worse than the line it replaces, by the counts the application has.

    Each entry is ``{"what", "from", "to"}``, in this order:

    - ``rules``: more that must be fixed, or as much and more that asks for a second look
      (``from`` and ``to`` are ``{"errors", "reviews"}``);
    - ``hints``: more hints that it sounds written;
    - ``voice_match``: the whole script's number is lower with the proposal in place;
    - ``length``: a smaller share of its breaths is inside the creator's usual length (``[inside, breaths]``);
    - ``unfamiliar``: more particles, connectors or English words the creator's material never has.

    Empty when it is worse in none. It says nothing about whether the proposal is good.
    """
    out: List[Dict[str, Any]] = []
    new, now = item["problems"], item["problems_now"]
    if (new["errors"], new["reviews"]) > (now["errors"], now["reviews"]):
        out.append({"what": "rules", "from": dict(now), "to": dict(new)})
    if item["hints"] > hints_now:
        out.append({"what": "hints", "from": hints_now, "to": item["hints"]})
    if item["voice_match"] is not None and match_now is not None and item["voice_match"] < match_now:
        out.append({"what": "voice_match", "from": match_now, "to": item["voice_match"]})
    fit = item["fit"]
    if fit and fit_now and fit["breaths"] and fit_now["breaths"]:
        if fit["inside"] * fit_now["breaths"] < fit_now["inside"] * fit["breaths"]:      # shares compared without dividing
            out.append({"what": "length", "from": [fit_now["inside"], fit_now["breaths"]], "to": [fit["inside"], fit["breaths"]]})
        if len(fit["unfamiliar"]) > len(fit_now["unfamiliar"]):
            out.append({"what": "unfamiliar", "from": len(fit_now["unfamiliar"]), "to": len(fit["unfamiliar"])})
    return out


def _with_reasons(store: Store, script: Dict[str, Any], suggestions: List[Dict[str, Any]], reasons: List[Dict[str, Any]],
                  reference: voicematch.Reference, now: Dict[str, Any]) -> Dict[str, int]:
    """Add to each of the critic's checked proposals what its reasons come to once they are looked up, in place.

    ``reasons[n]`` is ``_reasons`` for ``suggestions[n]``. Each suggestion gets:

    - ``reading``: ``{"issue", "rules_agree", "flagged", "flagged_missing", "flagged_lookup", "claims"}``. Each
      claim is ``{"claim", ...}`` with the answer of ``_look_up``, and ``holds``: True when the material bears
      the claim out, False when it contradicts it or does not show it, None when nothing could be looked up.
      "Never says" holds when the phrase is nowhere in the material, which is all that can be said for it;
    - ``worse`` (``_worse``).

    Returns what the phrases were looked up in: ``{"items", "openings"}``.
    """
    queries: List[Tuple[str, str]] = []
    for reason in reasons:
        if reason["flagged"]:
            queries.append(("anywhere", reason["flagged"]))
        for kind, phrase in reason["claims"]:
            queries.append(("opening" if kind == OPENS_WITH else ("breath_end" if _is_particle(phrase) else "anywhere"), phrase))
    answers, looked_in = _look_up(store, queries)
    answer = iter(answers)
    match_now = _match_score(now)
    for item, reason in zip(suggestions, reasons):
        findings_now = _line_findings(now, item["line_index"] + 1)
        flagged_lookup = next(answer) if reason["flagged"] else None
        claims = []
        for kind, _ in reason["claims"]:
            looked = next(answer)
            holds = None if looked["found"] is None else (looked["found"] != (kind == NEVER_SAYS))
            claims.append(dict(looked, claim=kind, holds=holds))
        item["reading"] = {"issue": reason["issue"], "rules_agree": _rules_agree(reason["issue"], findings_now),
                           "flagged": reason["flagged"], "flagged_missing": reason["flagged_missing"],
                           "flagged_lookup": flagged_lookup, "claims": claims}
        item["worse"] = _worse(item, _hints(findings_now), voicematch.line_fit(item["original"], reference), match_now)
    return looked_in


def _order(item: Dict[str, Any]) -> Tuple[int, int, int, float, int]:
    """Fewest findings first; then the one with fewer hints that it sounds written; then the one with more of
    its breaths inside the creator's usual length, and fewer words their material never has."""
    fit = item["fit"] or {"breaths": 0, "inside": 0, "unfamiliar": []}
    outside = 1.0 - (fit["inside"] / fit["breaths"] if fit["breaths"] else 0.0)
    return item["problems"]["errors"], item["problems"]["reviews"], item["hints"], outside, len(fit["unfamiliar"])


def apply_suggest(store: Store, result: Any, script_id: Any, kind: Any, line_index: Any = None) -> Dict[str, Any]:
    """Read a model's proposals for a stored script, check each one, and return them for the creator to choose from.

    ``{"kind", "line_index", "suggestions", "voice_match_now", "left_out"}``. Each suggestion:
    ``line_index``, ``original`` (the line as stored now), ``text``, ``english``, ``why_zh``, ``why_en``,
    ``units``, ``findings`` (what the checks say about the line with the proposal in it), ``problems`` and
    ``problems_now`` (``{"errors", "reviews"}`` with the proposal, and for the line as it stands), ``hints``
    (how many of the findings only say that the proposal may sound written; they are in neither count), ``fit``
    (``voicematch.line_fit``), ``voice_match`` (the whole script's number with the proposal in place, None
    when there is none) and ``estimated_sec``. Openings also carry ``patterns``; cuts carry ``one_breath``.

    For one line the proposals are ordered by the checks, cleanest first. The critic's come in script order.
    ``left_out`` counts proposals that were not usable: no text, the line's own words again, a repeat, a
    line that does not exist or is a live clip, or more than are shown.

    The critic's suggestions also carry ``reading`` and ``worse`` (``_with_reasons``): what the critic said
    about the line, looked up in code, and in what the repair is worse than the line. The answer
    then has ``looked_in``, ``{"items", "openings"}``: how much of the creator's material the phrases were looked
    up in. ``why_zh`` and ``why_en`` are the model's sentence as it came; a page labels them as its reading.

    Nothing is changed in the script. Every proposed text is recorded for the script. A reply with nothing
    usable raises ``llm.LLMError("empty_result")``. For the critic an empty list is an answer: it found nothing
    to repair. A critic's reply that is not an object with a list of suggestions raises
    ``llm.LLMError("bad_shape")``: it is not presented as "nothing found". Nothing is recorded for a reply that
    is refused. Raises ``SuggestError`` as ``suggest_prompt`` does.
    """
    script, index = _target(store, script_id, kind, line_index)
    raw = result.get("suggestions") if isinstance(result, dict) else None
    if raw is None and kind == CRITIC and isinstance(result, dict) and isinstance(result.get("flagged_and_repaired_lines"), list):
        raw = [
            {
                "line_no": item.get("line_no"),
                "spoken_script_zh": item.get("spoken_script_zh") if item.get("spoken_script_zh") is not None else item.get("repaired_zh"),
                "english_partner_translation": item.get("english_partner_translation") or item.get("repaired_english_translation") or "",
                "why_zh": item.get("why_zh") or item.get("why_fixed_en_zh") or "",
                "why_en": item.get("why_en") or item.get("why_fixed_en_zh") or "",
                "issue": item.get("issue") or item.get("issue_category"),
                "flagged": item.get("flagged"),
                "voice_claims": item.get("voice_claims"),
                "reason": item.get("reason"),
            }
            if isinstance(item, dict) else item
            for item in result["flagged_and_repaired_lines"]
        ]
    if kind == CRITIC and not isinstance(raw, list):
        raise llm.LLMError(
            "The reply is not a read-through: it has to be one JSON object with a list named `suggestions`, which is "
            "empty when no line needs a repair. Nothing was checked, and the script was not changed.", "bad_shape",
            "这段回复不是审稿结果：它应该是一个 JSON 对象，里面有一个叫 suggestions 的列表（没有要改的句子时是空列表）。"
            "这次什么都没有检查，脚本也没有改动。")
    raw = raw if isinstance(raw, list) else []
    left_out = max(0, len(raw) - MAX_RAW_ITEMS)
    raw = raw[:MAX_RAW_ITEMS]
    line_keys = [learning.spoken_key(str(line.get("spoken_script_zh") or "")) for line in script["lines"]]
    wanted: List[Tuple[int, Dict[str, str]]] = []
    reasons: Dict[int, Dict[str, Any]] = {}     # for the critic: line index -> what it said about that line
    seen = set()
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
        same_as_now = key == line_keys[at]
        # The critic gets one repair a line; for one line, the same words twice are one proposal.
        mark = at if kind == CRITIC else key
        if not key or same_as_now or mark in seen or len(wanted) >= MAX_SHOWN[kind]:
            left_out += 1
            continue
        seen.add(mark)
        why_zh, why_en = _note_pair(item)
        proposal = {"text": text, "english": _text(item.get("english_partner_translation"), MAX_NOTE_CHARS),
                    "why_zh": why_zh, "why_en": why_en}
        if kind == CRITIC:
            reasons[at] = _reasons(item, str(script["lines"][at].get("spoken_script_zh") or ""))
        wanted.append((at, proposal))
    # The critic listing nothing found nothing to repair. Listing only what cannot be used is not that.
    if not wanted and (kind != CRITIC or raw):
        raise llm.LLMError("Model reply contained no usable proposal", "empty_result",
                           "AI 的回复里没有可用的修改建议。")
    reference = voicematch.reference_for(store)
    now = checks.lint_payload(store, script["lines"], script["target_sec"], script["keywords"], script["post"])
    suggestions = [_checked(store, script, at, proposal, kind, reference, now) for at, proposal in wanted]
    answer: Dict[str, Any] = {"kind": kind, "line_index": index, "suggestions": suggestions,
                              "voice_match_now": _match_score(now), "left_out": left_out}
    if kind == CRITIC:
        suggestions.sort(key=lambda item: item["line_index"])
        answer["looked_in"] = _with_reasons(store, script, suggestions, [reasons[item["line_index"]] for item in suggestions],
                                            reference, now)
    else:
        suggestions.sort(key=_order)            # a stable sort: equal ones stay in the order the model gave
    store.add_ai_suggestions(script["id"], [item["text"] for item in suggestions])
    return answer


def suggest(store: Store, script_id: Any, kind: Any, line_index: Any = None, instruction: Any = "",
            transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Ask the AI service in settings for proposals and return them checked (``apply_suggest``)."""
    system, user = suggest_prompt(store, script_id, kind, line_index, instruction)
    return apply_suggest(store, pipeline.complete(store, system, user, transport), script_id, kind, line_index)
