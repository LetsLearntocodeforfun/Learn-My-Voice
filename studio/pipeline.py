"""Model-backed steps: drafting, learning her voice, and recommending topics.

Each step is split in two so it can run three ways:

- ``*_prompt``  builds the exact text a model needs (her profile, her scripts, the rules);
- ``apply_*``   validates a model's JSON reply and saves it.

The app calls a model in between (``generate_script`` etc.). Copy-and-paste
mode and the MCP server hand the prompt to an outside model (for example a
ChatGPT subscription) and feed its reply back through the same ``apply_*``.

Three rules hold on all three routes, because they are enforced here and not in
the callers:

- A learned voice is a proposal. ``propose_voice`` stores it with its evidence
  report and leaves the active profile alone; only ``approve_voice``, which the
  creator triggers in the app, makes it active.
- What a result is later said to be based on describes the prompt the model
  actually read. The outside routes build the prompt in one request and get the
  reply in another, so ``draft_prompt`` and ``voice_prompt`` write down what
  they put in the prompt, and the reply is matched to that record.
- Wording is learned only from text the creator wrote or confirmed. A video
  transcribed by a machine counts toward the measured speaking rate at once.
  Until the creator confirms it, it is left out of everything that learns or
  checks wording (``confirmed_samples``). Where only the topic matters (topic
  ideas, trend ranking) it is used like any other item.

The markdown blueprints in prompts/ stay the single source of truth for prompt
text; this module only extracts the fenced blocks and fills ``{{placeholders}}``.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import corpus, creator, evidence, guardrails, hooks, learning, llm, timing, trends
from .store import (MANUAL_PROVIDER, MAX_KEYWORD_CHARS, Store, clean_keyword, keyword_key, normalize_keywords,
                    normalize_lines)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
STAGE2 = "02_voice_dna_synthesizer.md"
STAGE4 = "04_script_generator_normal_and_sponsored.md"
STAGE6 = "06_content_recommender.md"
STAGE7 = "07_trend_researcher.md"
VIDEO_FORMATS = ("VLOG_VOICEOVER", "COUPLE_VLOG", "FAVORITES_ROUNDUP", "SINGLE_PRODUCT_SHARE")

DEFAULT_SPEAKING_RATE = 240       # used until her real rate is typed in or measured from timed material
MIN_CLAUSES_FOR_MEASURED_CADENCE = 30
LEARN_CORPUS_CHARS = 40000
RECOMMEND_EXCERPT_CHARS = 400
RECOMMEND_MAX_VIDEOS = 60
IDEAS_MAX_TRENDS = 12
TREND_SOURCE_MAX_AGE_DAYS = 30
LOOKUP_MAX_TITLES = 20            # titles of the creator's own items that go with a web lookup, to show what they make
MAX_FOCUS_CHARS = 500             # what the creator asked for this time, typed or said aloud: a short request, not a document
MAX_LINK_CHARS = 2000             # a source link longer than this is not stored or shown
MAX_LOOKUP_KEYWORDS = 24          # keywords of one web lookup that are remembered with the ideas that followed it
MAX_LOOKUP_PAGES = 20             # pages a web lookup cited that are kept, to be shown as links
LOOKUP_DONE, LOOKUP_FAILED, LOOKUP_SKIPPED = "done", "failed", "skipped"    # how the web lookup before a set of ideas went
MAX_LESSON_CHARS = 2000           # one side of a remembered change: a script line, however many breaths it has
MAX_LESSON_NOTE_CHARS = 300
RATE_TYPED, RATE_MEASURED, RATE_DEFAULT = "typed", "measured", "default"   # where a profile's speaking rate came from
TIME_FORMAT = "%Y-%m-%d %H:%M"

PROFILE_VERSION = "1.0"           # written by this app. A model's own value is not kept: prompts/04 gives "-example" a meaning.
# The length of a breath, when there is too little material to measure it (under MIN_CLAUSES_FOR_MEASURED_CADENCE
# clauses). A model's numbers are then all there is, so each is held inside these bounds, and the defaults stand in
# for a number that is missing or not a number. ``hard_max`` is what the cadence check enforces.
LENGTHS_MEASURED, LENGTHS_AI_BOUNDED, LENGTHS_DEFAULT = "measured", "ai_bounded", "default"
CLAUSE_LENGTH_DEFAULTS = {"min": 5, "median": 11, "hard_max": 20}
CLAUSE_LENGTH_BOUNDS = {"min": (1, 12), "median": (4, 20), "hard_max": (10, 30)}

# Text taken from an ideas reply. ``script_notes_zh`` becomes the notes of a draft prompt when the creator picks the idea.
MAX_IDEAS = 12
MAX_IDEA_NOTES_CHARS = 600
MAX_IDEA_TEXT_CHARS = 300         # a title, a reason, the note about the data
IDEA_KINDS = ("BUILD_ON_WINNER", "ADJACENT_EXPERIMENT")
IDEA_CONFIDENCE = ("low", "medium", "high")

MANUAL_REPLY_RULE = (
    "IMPORTANT: Reply with ONLY the JSON object described above, inside one ```json code block. "
    "No explanation before or after it."
)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_UNSET: Any = object()
_VOICE_LOCK = threading.Lock()    # one proposal or approval at a time


class PromptError(Exception):
    pass


class VoiceReviewError(Exception):
    """A proposed voice profile cannot be accepted.

    ``code``: ``no_pending`` (there is none), ``stale_proposal`` (the one named is not the one stored) or
    ``no_samples`` (nothing left to check it against).
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def load_prompt_pair(filename: str) -> Tuple[str, str]:
    """Return ``(system, user_template)`` from a blueprint's two ```xml blocks."""
    text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")

    def block_after(heading: str) -> str:
        m = re.search(r"^#{2,3} [^\n]*%s[^\n]*\n.*?```xml\n(.*?)\n```" % re.escape(heading), text, re.S | re.M)
        if not m:
            raise PromptError("No ```xml block under a '%s' heading in %s" % (heading, filename))
        return m.group(1)

    return block_after("System Prompt"), block_after("User Prompt Template")


def render(template: str, values: Dict[str, str]) -> str:
    missing = sorted(set(_PLACEHOLDER.findall(template)) - set(values))
    if missing:
        raise PromptError("Missing prompt values: %s" % ", ".join(missing))
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _no_tags(encoded: str) -> str:
    """JSON text in which no angle bracket stands as itself: each is written as its ``\\u`` escape.

    The prompts wrap every value in a block of tags. In JSON an angle bracket can only occur inside a
    string, so after this no value can spell a tag: not the closing tag of its own block, not a block of
    its own. A JSON reader gives back the same value.
    """
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e")


def _dump(value: Any) -> str:
    """A value as it is put into a prompt: JSON, with angle brackets escaped (``_no_tags``)."""
    return _no_tags(json.dumps(value, ensure_ascii=False, indent=1))


def _inline(value: Any) -> str:
    """Like ``_dump``, on one line: for a short value such as the notes or the keywords of a draft."""
    return _no_tags(json.dumps(value, ensure_ascii=False))


def _blueprint(filename: str, values: Dict[str, str]) -> Tuple[str, str]:
    system, user_template = load_prompt_pair(filename)
    return system, render(user_template, values)


def single_prompt(system: str, user: str) -> str:
    """One pasteable text for chat products that have no separate system prompt."""
    return "%s\n\n%s\n\n%s\n" % (system.strip(), user.strip(), MANUAL_REPLY_RULE)


def complete(store: Store, system: str, user: str, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Call the provider chosen in settings."""
    config = store.load_config()
    provider, model = config.get("provider") or "", config.get("model") or ""
    if provider == MANUAL_PROVIDER:
        raise llm.LLMError("Copy-and-paste mode is on; there is no model to call directly", "manual_mode")
    api_key = config["keys"].get(provider) or ""
    base_url = config.get("local_base_url") or None
    if not model or (provider == "local" and not base_url) or (provider != "local" and not api_key):
        raise llm.LLMError("AI is not set up yet", "not_configured")
    return llm.complete_json(provider, api_key, model, system, user, transport=transport, base_url=base_url)


# ---- drafting ---------------------------------------------------------------
def _draft_fingerprint(notes: Any, target_sec: Any, video_format: Any, keywords: Any) -> str:
    """Identifies a drafting request, so a reply can be matched to the prompt that was built for it."""
    raw = json.dumps([" ".join(str(notes or "").split()), int(target_sec), str(video_format), normalize_keywords(keywords)],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_draft(store: Store, *, notes: str, target_sec: int, video_format: str,
                 keywords: Any = None) -> Tuple[str, str, Dict[str, Any]]:
    """``(system, user, basis)``: the draft prompt, and what it was built from.

    ``basis`` is what the script will later be said to be based on, so every part of it is read off the
    values that go into this prompt: the titles of the creator's items used as examples, the titles of the
    videos whose openings are in it (``hooks.best``: the creator's own, with results at or above their
    usual), how many remembered lessons are in it, and whether the voice profile is the creator's own or
    the bundled example.
    """
    dna, is_example = store.load_dna()
    who = store.load_creator()
    words = normalize_keywords(keywords)
    on_topic = (lambda sample: trends.is_on_topic(sample, words)) if words else None
    anchors = [{"title": a["title"], "creator_own_script": a["her_own_script"]}
               for a in corpus.exemplars(confirmed_samples(store.list_samples()), on_topic=on_topic)]
    exemplars_text = _dump(anchors) if anchors else "[]  (no past scripts have been imported yet; rely on the Voice DNA)"
    rules = learning.prompt_rules(store.list_lessons())      # newest first; the prompt takes the first ten
    openings = hooks.best(store.list_samples())
    system, user = _blueprint(STAGE4, {
        "creator_profile_json": _dump(creator.prompt_block(who)),
        "voice_dna_json": _dump(creator.apply_never(dna, who)),
        "retrieved_beat_exemplars_json": exemplars_text,
        "own_best_openings_json": _dump(openings),
        "top_10_wife_edit_diff_rules_json": _dump(rules),
        "mode": "NORMAL_ORGANIC",
        # One of the known names, never text from a request: this value is not encoded.
        "video_format": video_format if video_format in VIDEO_FORMATS else VIDEO_FORMATS[0],
        "target_duration_sec": str(int(target_sec)),
        # Typed by the creator, or taken from an idea a model wrote: encoded like every other value, as one JSON string.
        "user_topic_and_story_notes": _inline(notes.strip()),
        "approved_humanized_sponsor_brief_json": "null",
        "target_keywords_json": _inline(words),
    })
    learned_at = profile_summary(dna).get("learned_at")
    basis = {
        "exemplar_titles": [a["title"] for a in anchors],
        "opening_titles": [o["title"] for o in openings],
        "lessons_used": len(rules),
        "profile": "example" if is_example else "learned",
        "learned_at": learned_at if (not is_example and isinstance(learned_at, str)) else None,
    }
    return system, user, basis


def draft_brief(store: Store, *, notes: str, target_sec: int, video_format: str, keywords: Any = None) -> Dict[str, Any]:
    """Build the draft prompt for an outside model and write down what it contains.

    Returns ``{"system", "user", "brief_id", "basis"}``. The record is what ``apply_draft`` attaches to the
    script when the reply comes back, in another request and possibly after the library, the lessons or the
    voice profile have changed.
    """
    system, user, basis = _build_draft(store, notes=notes, target_sec=target_sec, video_format=video_format,
                                       keywords=keywords)
    fingerprint = _draft_fingerprint(notes, target_sec, video_format, keywords)
    # The request is written into the record as well, so that the record can say which request it belongs to.
    brief_id = store.save_brief("draft", {"basis": basis, "fingerprint": fingerprint}, fingerprint)
    return {"system": system, "user": user, "brief_id": brief_id, "basis": basis}


def draft_prompt(store: Store, *, notes: str, target_sec: int, video_format: str,
                 keywords: Any = None) -> Tuple[str, str]:
    """Prompt for a normal (non-sponsored) script, anchored on her own past scripts.

    ``keywords`` are trend or search terms to work in where they fit her voice.
    When given, her own scripts on those topics come first among the examples.
    The voice profile in the prompt includes the phrases the creator refuses to say,
    and remembered lessons from the creator's edits fill the learned-edit block.
    What the prompt contains is written down (see ``draft_brief``).
    """
    brief = draft_brief(store, notes=notes, target_sec=target_sec, video_format=video_format, keywords=keywords)
    return brief["system"], brief["user"]


def recorded_basis(store: Store, *, notes: str, target_sec: int, video_format: str, keywords: Any = None,
                   brief_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What the prompt for this drafting request contained, as written down when it was built.

    Found by ``brief_id`` when the reply quotes one and that record was made for this same request (same
    notes, length, format and keywords). An id that belongs to another request is not honoured: it would
    attach "what this draft was based on" to a script the prompt was never for. Then, as when no id is
    quoted, the newest prompt built for this request is used. None when no prompt was built for it here:
    nothing is known about what the model read, and nothing is claimed.
    """
    fingerprint = _draft_fingerprint(notes, target_sec, video_format, keywords)
    found = store.find_brief("draft", brief_id=brief_id if isinstance(brief_id, str) else None, fingerprint=fingerprint)
    if found and found["payload"].get("fingerprint") != fingerprint:
        found = store.find_brief("draft", fingerprint=fingerprint)     # another request's record, or one made before records said
    basis = found["payload"].get("basis") if found else None
    return basis if isinstance(basis, dict) else None


_DRAFT_WRAPPERS = ("script", "draft", "package", "result", "data")
_LINE_SPOKEN_ALIASES = ("spoken_zh", "script_zh", "voiceover_zh", "line_zh", "text_zh", "text")
_VOICE_DNA_SECTIONS = (
    "persona_summary",
    "cadence_and_syntax",
    "lexical_fingerprint",
    "code_switching_grammar",
    "beat_playbooks",
    "onscreen_flower_text_and_caption_dna",
    "anti_voice_blacklist",
)
_VOICE_WRAPPERS = ("voice_dna", "voice_dna_profile", "profile", "voice_profile", "dna", "result", "data")


def _coerce_draft_lines(raw_lines: Any) -> Any:
    if not isinstance(raw_lines, list):
        return raw_lines
    out: List[Any] = []
    for item in raw_lines:
        if isinstance(item, dict) and not str(item.get("spoken_script_zh") or "").strip():
            mapped = item
            for alias in _LINE_SPOKEN_ALIASES:
                val = item.get(alias)
                if isinstance(val, str) and val.strip():
                    mapped = dict(item, spoken_script_zh=val)
                    break
            out.append(mapped)
        else:
            out.append(item)
    return out


def apply_draft(store: Store, result: Dict[str, Any], *, notes: str, target_sec: int, video_format: str,
                default_title: str = "未命名脚本", keywords: Any = None, brief_id: Optional[str] = None,
                basis: Any = _UNSET) -> Dict[str, Any]:
    """Validate a drafted script and save it with its target keywords and its basis; returns the stored script.

    ``basis`` is passed by ``generate_script``, which built the prompt itself. Left out, it is taken from
    the record made when the prompt was built (``recorded_basis``).
    """
    unwrapped: Any = result
    for _ in range(2):
        if not isinstance(unwrapped, dict) or "script_lines" in unwrapped or "lines" in unwrapped:
            break
        moved = False
        for key in _DRAFT_WRAPPERS:
            inner = unwrapped.get(key)
            if isinstance(inner, dict):
                unwrapped = inner
                moved = True
                break
            if key == "script" and isinstance(inner, list):
                unwrapped = dict(unwrapped, script_lines=inner)
                moved = True
                break
        if not moved:
            break
    result = unwrapped if isinstance(unwrapped, dict) else {}
    raw_lines = result.get("script_lines")
    if raw_lines is None:
        raw_lines = result.get("lines")
    if raw_lines is None and any(k in result for k in ("persona_summary", "lexical_fingerprint", "code_switching_grammar", "beat_playbooks")):
        raise llm.LLMError(
            "This looks like a Voice DNA profile JSON rather than a script. Import it in Step 3 (Your Voice) instead.",
            "wrong_step_draft",
            "这段 JSON 看起来是声音档案，不是脚本。请到「开始」第三步（你的声音）里导入它。",
        )
    lines = [l for l in normalize_lines(_coerce_draft_lines(raw_lines)) if l["spoken_script_zh"].strip()]
    if not lines:
        raise llm.LLMError("Model reply contained no script lines", "empty_result")
    if basis is _UNSET:
        basis = recorded_basis(store, notes=notes, target_sec=target_sec, video_format=video_format,
                               keywords=keywords, brief_id=brief_id)
    post = result.get("rednote_post_package")
    if not isinstance(post, dict):
        post = result.get("post_package") if isinstance(result.get("post_package"), dict) else (
            result.get("post") if isinstance(result.get("post"), dict) else {})
    inferred_title = ""
    if not notes.strip():
        if isinstance(result.get("title"), str) and result["title"].strip():
            inferred_title = result["title"].strip()[:60]
        elif isinstance(post.get("title_options"), list) and post["title_options"]:
            first_opt = post["title_options"][0]
            if isinstance(first_opt, str) and first_opt.strip():
                inferred_title = first_opt.strip()[:60]
            elif isinstance(first_opt, dict) and isinstance(first_opt.get("title_zh"), str) and first_opt["title_zh"].strip():
                inferred_title = first_opt["title_zh"].strip()[:60]
    return store.create_script(
        title=inferred_title, notes=notes, target_sec=target_sec, video_format=video_format,
        default_title=default_title, lines=normalize_lines(lines), post=post,
        from_ai=True, keywords=keywords, basis=basis,
    )


def generate_script(store: Store, *, notes: str, target_sec: int, video_format: str,
                    transport: Optional[llm.Transport] = None, default_title: str = "未命名脚本",
                    keywords: Any = None) -> Dict[str, Any]:
    system, user, basis = _build_draft(store, notes=notes, target_sec=target_sec, video_format=video_format,
                                       keywords=keywords)
    result = complete(store, system, user, transport)
    return apply_draft(store, result, notes=notes, target_sec=target_sec, video_format=video_format,
                       default_title=default_title, keywords=keywords, basis=basis)


# ---- learning her voice -----------------------------------------------------
def profile_summary(dna: Any) -> Dict[str, Any]:
    """A profile's ``source_corpus_summary``, or an empty dict when it has none."""
    summary = dna.get("source_corpus_summary") if isinstance(dna, dict) else None
    return summary if isinstance(summary, dict) else {}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def confirmed_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The items whose wording may be learned from and checked against: text the creator wrote or confirmed.

    A video transcribed on this computer waits (``needs_review``) until the creator has read it and
    confirmed it. A machine transcript gets the timing right but can get a sentence-final particle
    wrong, and particles are a large part of what a voice is here. So until it is confirmed an item
    counts toward the measured speaking rate (``measured_rate``) and toward nothing that learns or checks
    wording: the measured habits, the corpus the voice is learned from, the examples a draft is
    anchored on, and the material a proposed profile's quotes are looked up in.

    A script written in this app and then posted waits in the same way (``corpus.waits_as_posted_script``),
    for another reason: it may have begun as an AI's draft, and a model that is shown its own sentences
    as how the creator talks drifts towards itself. Its numbers count wherever numbers are used.
    """
    return [s for s in samples if not s.get("needs_review")]


def _organic_texts(samples: List[Dict[str, Any]]) -> List[str]:
    return [s["text"] for s in samples if not s["is_sponsored"] and s["text"].strip()]


def measured_rate(samples: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Speaking rate measured from timestamps, pooled over the non-sponsored items that have timing.

    ``{"speaking_rate_cpm", "spoken_sec", "videos"}``, or None while there is under a minute of timed
    speech (``timing.combined_rate``). Sponsored items are left out here as they are from everything
    else that describes the creator's own voice. Transcripts still waiting for the creator's
    confirmation are counted: their timing is a measurement and does not depend on the wording.
    """
    return timing.combined_rate(s.get("timing") for s in samples if not s.get("is_sponsored"))


def _build_voice(store: Store, corpus_chars: int = LEARN_CORPUS_CHARS) -> Tuple[str, str, Dict[str, Any]]:
    """``(system, user, info)``: the voice-learning prompt, and how many of the creator's items it contains.

    ``info`` is ``{"organic", "sponsored", "as_of"}``: the items counted from what was put in the prompt
    (a large library is cut to ``corpus_chars``, so this can be fewer than the library holds), and a
    moment just before the library was read. Taken before, not after: an item added in between is then
    counted as new since the profile was learned, which asks for a refresh, never as already learned from.

    Only confirmed items go in (``confirmed_samples``), as text and in the measured habits.
    """
    as_of = time.time()
    library = store.list_samples()
    samples = confirmed_samples(library)
    organic_texts = _organic_texts(samples)
    if not organic_texts:
        raise llm.LLMError("No non-sponsored material to learn from. A video transcript counts once the creator has "
                           "confirmed it in the app.", "no_samples")
    stats = corpus.voice_stats(organic_texts)
    organic = corpus.corpus_slice(samples, sponsored=False, budget_chars=corpus_chars)
    sponsored = corpus.corpus_slice(samples, sponsored=True, budget_chars=corpus_chars // 4)

    # The notes tell the model what it is looking at, so they have to stay true for subtitle files and videos.
    measured = measured_rate(library)                 # every timed item, also a transcript not yet confirmed
    written = all(s.get("kind", "script") == "script" for s in samples if s["text"].strip())
    timed = any(isinstance(s.get("timing"), dict) for s in library)
    if measured:
        rate_note = ("Speaking rate was measured from timestamps (%s seconds of speech in %d timed item(s)) and is "
                     "given as `measured_speaking_rate`; do not infer another."
                     % (measured["spoken_sec"], measured["videos"]))
    elif not timed:
        rate_note = "No audio was analysed, so there is no speaking-rate measurement; do not infer one."
    else:
        rate_note = "There is too little timed material for a speaking-rate measurement; do not infer one."
    measured_from = ("the creator's hand-written scripts" if written else
                     "the creator's own material (scripts they wrote, subtitle files, and transcripts of their "
                     "videos that they have read and confirmed)")
    corpus_note = ("Scripts the creator wrote by hand for their own videos, newest first." if written else
                   "The creator's own material, newest first: scripts they wrote, and the words of their videos "
                   "taken from subtitle files or transcribed on this computer and then confirmed by the creator.")
    metrics: Dict[str, Any] = {
        "note": "Clause lengths, particles and English words were measured from %s. %s Use these clause-length "
                "and particle numbers as ground truth." % (measured_from, rate_note),
    }
    metrics.update(stats)
    if measured:
        metrics["measured_speaking_rate"] = {"units_per_minute": measured["speaking_rate_cpm"],
                                             "seconds_of_speech": measured["spoken_sec"],
                                             "timed_items": measured["videos"]}
    system, user = _blueprint(STAGE2, {
        "creator_profile_json": _dump(creator.prompt_block(store.load_creator())),
        "python_aggregated_acoustic_metrics_json": _dump(metrics),
        "organic_videos_segmented_beats_and_captions_json": _dump({
            "note": corpus_note + " '/' or line breaks mark breath pauses.",
            "scripts": organic,
        }),
        "sponsored_videos_bridge_and_review_beats_only_json": _dump(sponsored) if sponsored else "[]",
        "wife_past_script_edits_diff_log_if_any": "[]",
    })
    return system, user, {"organic": len(organic), "sponsored": len(sponsored), "as_of": as_of}


def voice_prompt(store: Store, corpus_chars: int = LEARN_CORPUS_CHARS) -> Tuple[str, str]:
    """Prompt that turns her imported scripts into a Voice DNA profile.

    How many of the creator's items the prompt contains is written down, so that the proposal made
    from the reply, in a later request, says truthfully how many it was learned from.
    """
    system, user, info = _build_voice(store, corpus_chars)
    store.save_brief("voice", info)
    return system, user


def strip_refused(dna: Any, who: Any) -> int:
    """Take the creator's own refused phrases out of a learned profile's ban lists, in place. Returns how many went.

    Those phrases are enforced from the creator profile (``creator.apply_never``). A copy kept inside a
    learned profile would go on being enforced after the creator removed the phrase from their list. An
    entry counts as a copy when it bans exactly the same text (``guardrails.ban_key``): a note in
    brackets or different letter case does not make it a different ban. A longer or shorter phrase is a
    different ban, found by the model in the material, and stays.
    """
    refused = {guardrails.ban_key(p) for p in creator.never_phrases(who)} - {""}
    if not refused or not isinstance(dna, dict):
        return 0
    removed = 0
    for path, _, _ in guardrails.DNA_BAN_LISTS:
        node: Any = dna
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        values = node.get(keys[-1]) if isinstance(node, dict) else None
        if not isinstance(values, list):
            continue
        kept = [v for v in values if not (isinstance(v, str) and guardrails.ban_key(v) in refused)]
        removed += len(values) - len(kept)
        values[:] = kept
    return removed


def _typed_rate(store: Store) -> Optional[Any]:
    """The speaking rate the creator typed in on their active profile, or None when they have not."""
    active, is_example = store.load_dna()
    if is_example or not isinstance(active, dict):
        return None
    cadence = active.get("cadence_and_syntax")
    rate = guardrails._number(cadence.get("target_speaking_rate_cpm") if isinstance(cadence, dict) else None)
    return rate if (rate is not None and profile_summary(active).get("speaking_rate_confirmed")) else None


def _decide_rate(store: Store, samples: List[Dict[str, Any]]) -> Tuple[Any, str, Optional[Dict[str, Any]]]:
    """``(rate, where it came from, the measurement used)``: typed by the creator, else measured, else the default."""
    typed = _typed_rate(store)
    if typed is not None:
        return typed, RATE_TYPED, None
    measured = measured_rate(samples)
    if measured:
        return measured["speaking_rate_cpm"], RATE_MEASURED, measured
    return DEFAULT_SPEAKING_RATE, RATE_DEFAULT, None


def _bounded_lengths(given: Any) -> Tuple[Dict[str, int], str]:
    """Breath lengths for a profile when there is too little material to measure them: ``(lengths, source)``.

    Each of a model's numbers is held inside CLAUSE_LENGTH_BOUNDS, and CLAUSE_LENGTH_DEFAULTS stands in for
    one that is missing or not a number, so a reply can neither switch the length check off (``hard_max``
    999) nor make it flag every line (``hard_max`` 1). ``source`` is ``ai_bounded`` when any of the model's
    numbers was used, else ``default``.
    """
    given = given if isinstance(given, dict) else {}
    lengths: Dict[str, int] = {}
    from_model = False
    for key in ("min", "median", "hard_max"):
        value = guardrails._number(given.get(key))
        low, high = CLAUSE_LENGTH_BOUNDS[key]
        if value is None:
            lengths[key] = CLAUSE_LENGTH_DEFAULTS[key]
        else:
            lengths[key], from_model = int(min(max(round(value), low), high)), True
    lengths["median"] = max(lengths["median"], lengths["min"])
    lengths["hard_max"] = max(lengths["hard_max"], lengths["median"])
    return lengths, LENGTHS_AI_BOUNDED if from_model else LENGTHS_DEFAULT


def _own_fields(dna: Dict[str, Any]) -> Dict[str, Any]:
    """Set the fields of a proposal that are this app's to write, in place; returns ``dna``.

    ``profile_version`` is fixed. ``anti_voice_blacklist.learned_from_her_edits`` is empty: the prompt
    hands the model an empty list, the review does not show that list, and the linter enforces it, so
    nothing a model puts there may ever arrive.
    """
    dna["profile_version"] = PROFILE_VERSION
    if isinstance(dna.get("anti_voice_blacklist"), dict):
        dna["anti_voice_blacklist"]["learned_from_her_edits"] = []
    return dna


def _stored_proposal(dna: Any) -> Dict[str, Any]:
    """The profile of a stored proposal as it may be shown and accepted, as a new dict.

    A proposal is given its shape when the reply arrives (``propose_voice``). The same is done again each
    time one is read back, so a proposal stored by a version before these rules cannot be shown in part and
    accepted whole: unknown fields go, the unshown ban list is emptied, breath lengths that were not measured
    are held within bounds. Doing it to a proposal that already has its shape changes nothing.
    """
    shaped = _own_fields(evidence.shape_profile(dna))
    summary = dict(profile_summary(dna))               # written by this app when the proposal was made
    if summary.get("clause_lengths_measured") is not True:
        cadence = shaped.get("cadence_and_syntax")
        if not isinstance(cadence, dict):
            cadence = shaped["cadence_and_syntax"] = {}
        cadence["ideal_clause_length_chars"], source = _bounded_lengths(cadence.get("ideal_clause_length_chars"))
        summary.setdefault("clause_lengths_source", source)
    if summary:
        shaped["source_corpus_summary"] = summary
    return shaped


def propose_voice(store: Store, dna: Dict[str, Any], brief: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate a Voice DNA reply, replace its numbers with measurements, check it, and store it as a PROPOSAL.

    The active profile is not touched: every route that learns a voice (the direct call, a pasted
    reply, the MCP tool) ends here, and only ``approve_voice`` goes further. Returns the proposal,
    ``{"dna", "report", "created_at", "based_on"}``; a new one replaces an older one. ``created_at`` names
    the proposal: it is later than that of the proposal it replaces, and ``approve_voice`` has to quote it.

    - Only the fields of the known profile shape are kept (``evidence.shape_profile``). Anything else in
      the reply is dropped here, so it can never be stored, enforced or sent with a later prompt.
      ``profile_version`` is written by this app and ``learned_from_her_edits`` is emptied (``_own_fields``).
    - Clause-length numbers come from measurement, not from the model, once there are enough clauses.
      Below that the model's numbers are held within fixed bounds (``_bounded_lengths``).
      ``source_corpus_summary.clause_lengths_source`` records which: ``measured`` | ``ai_bounded`` | ``default``.
    - Speaking rate is never the model's: a rate the creator typed in, else one measured from timed
      material, else a default. ``source_corpus_summary.speaking_rate_source`` records which
      (``typed`` | ``measured`` | ``default``) and ``speaking_rate_measured`` the measurement used.
    - The creator's own refused phrases are taken out of its ban lists (``strip_refused``).
    - ``report`` is ``evidence.verify_profile`` over the library: every phrase the profile attributes to
      the creator, looked up in their own material, and every piece of free text nothing can be looked up for.
    - ``based_on`` is how many non-sponsored items the prompt contained. ``brief`` is passed by
      ``learn_voice``, which built the prompt itself; otherwise the newest record made by
      ``voice_prompt`` is used, and 0 when no prompt was ever built here.
    - The measured habits and the report use confirmed items only (``confirmed_samples``); the speaking
      rate uses every timed item.
    """
    if not isinstance(dna, dict):
        raise llm.LLMError("Model reply is not a usable voice profile", "empty_result")
    dna = dict(dna)
    for _ in range(2):
        if any(k in dna for k in _VOICE_DNA_SECTIONS):
            break
        moved = False
        for key in _VOICE_WRAPPERS:
            inner = dna.get(key)
            if isinstance(inner, dict):
                dna = dict(inner)
                moved = True
                break
        if not moved:
            break
    if not any(k in dna for k in _VOICE_DNA_SECTIONS) and (
        "script_lines" in dna or "lines" in dna or "rednote_post_package" in dna
    ):
        raise llm.LLMError(
            "This looks like a script JSON rather than a Voice DNA profile. Paste it in New Script, or copy the Step 3 prompt to generate your Voice DNA.",
            "wrong_step_voice",
            "这段 JSON 看起来是一篇脚本，不是第三步的声音档案。请确认复制的是第三步的提示词和回复，或到「写新脚本」里导入它。",
        )
    if isinstance(dna.get("persona_summary"), str) and dna["persona_summary"].strip():
        dna["persona_summary"] = {"one_line_essence_zh": dna["persona_summary"].strip()}
    avb = dna.get("anti_voice_blacklist")
    if isinstance(avb, list):
        dna["anti_voice_blacklist"] = {"banned_phrases_zh": list(avb), "learned_from_her_edits": []}
    elif isinstance(avb, dict) and "banned_phrases_zh" not in avb and isinstance(avb.get("banned_phrases"), list):
        dna["anti_voice_blacklist"] = dict(avb, banned_phrases_zh=list(avb["banned_phrases"]))

    def _has_usable_content(node: Any) -> bool:
        if isinstance(node, str):
            return bool(node.strip())
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return True
        if isinstance(node, list):
            return any(_has_usable_content(item) for item in node)
        if isinstance(node, dict):
            return any(_has_usable_content(val) for key, val in node.items() if key != "profile_version")
        return False

    shaped_check = _own_fields(evidence.shape_profile(dna))
    if not _has_usable_content(shaped_check):
        raise llm.LLMError("Model reply is not a usable voice profile", "empty_result")
    if not isinstance(dna.get("cadence_and_syntax"), dict):
        dna["cadence_and_syntax"] = {}
    if not isinstance(dna.get("anti_voice_blacklist"), dict):
        dna["anti_voice_blacklist"] = {"banned_phrases_zh": [], "learned_from_her_edits": []}
    with _VOICE_LOCK:
        library = store.list_samples()
        samples = confirmed_samples(library)
        organic_texts = _organic_texts(samples)
        if not organic_texts:
            raise llm.LLMError("No non-sponsored material to learn from. A video transcript counts once the creator "
                               "has confirmed it in the app.", "no_samples")
        now = time.time()
        waiting = store.load_pending()
        earlier = guardrails._number(waiting.get("created_at")) if waiting else None
        if earlier is not None and now <= earlier:    # the clock stood still or went back: still a different proposal
            now = earlier + 0.000001
        if brief is None:
            recorded = store.find_brief("voice")
            brief = recorded["payload"] if recorded else {}
        based_on, sponsored = _count(brief.get("organic")), _count(brief.get("sponsored"))
        as_of = guardrails._number(brief.get("as_of"))    # when the prompt was built: what the model read dates from then
        material_as_of = as_of if as_of is not None else now

        dna = evidence.shape_profile(dna)             # the known fields only; evidence_review and the like are not among them
        if not isinstance(dna.get("cadence_and_syntax"), dict):
            dna["cadence_and_syntax"] = {}
        if not isinstance(dna.get("anti_voice_blacklist"), dict):
            dna["anti_voice_blacklist"] = {"banned_phrases_zh": [], "learned_from_her_edits": []}
        else:
            dna["anti_voice_blacklist"].setdefault("banned_phrases_zh", [])
        removed = strip_refused(dna, store.load_creator())
        _own_fields(dna)
        stats = corpus.voice_stats(organic_texts)
        cadence = dna["cadence_and_syntax"]
        rate, rate_source, measurement = _decide_rate(store, library)
        cadence["target_speaking_rate_cpm"] = rate
        lengths_measured = stats["clauses"] >= MIN_CLAUSES_FOR_MEASURED_CADENCE
        if lengths_measured:
            lengths = stats["clause_length_units"]
            cadence["ideal_clause_length_chars"] = {"min": lengths["p5"], "median": lengths["median"], "hard_max": lengths["p95"]}
            lengths_source = LENGTHS_MEASURED
        else:
            cadence["ideal_clause_length_chars"], lengths_source = _bounded_lengths(cadence.get("ideal_clause_length_chars"))
        dna["source_corpus_summary"] = {
            "organic_scripts_analyzed": based_on, "sponsored_scripts_analyzed": sponsored,
            "clauses_measured": stats["clauses"], "clause_lengths_measured": lengths_measured,
            "clause_lengths_source": lengths_source,
            "speaking_rate_confirmed": rate_source == RATE_TYPED, "speaking_rate_source": rate_source,
            "speaking_rate_measured": measurement, "refused_phrases_removed": removed,
            "based_on": based_on, "material_as_of": material_as_of, "proposed_at": now,
            "learned_at": time.strftime(TIME_FORMAT, time.localtime(now)),
        }
        pending = {"dna": dna, "report": evidence.verify_profile(dna, samples), "created_at": now, "based_on": based_on}
        store.save_pending(pending)
        return pending


def pending_voice(store: Store) -> Optional[Dict[str, Any]]:
    """The proposal waiting for the creator, with its receipts checked against the library as it is now.

    The stored report dates from when the proposal was made. Material may have been added, edited,
    confirmed or deleted since, and a receipt has to be true when it is shown, so the report is
    computed again here, over the confirmed items (``confirmed_samples``). The profile is given the
    proposal shape again first (``_stored_proposal``), so what is returned is exactly what
    ``approve_voice`` would work from.
    """
    pending = store.load_pending()
    if pending is None:
        return None
    dna = _stored_proposal(pending["dna"])
    return dict(pending, dna=dna, report=evidence.verify_profile(dna, confirmed_samples(store.list_samples())))


def approve_voice(store: Store, rejected: Any = None, created_at: Any = None, keep: Any = None) -> Dict[str, Any]:
    """Make the proposal the active profile, as the creator decided. Returns the new active profile.

    Only the app calls this, on the creator's click. There is deliberately no MCP tool for it.

    ``created_at`` names the proposal the creator was looking at: the ``created_at`` that
    ``pending_voice`` returned with it. A proposal can be replaced while a review is open (a second
    reply pasted in, an AI application saving another profile), and what is accepted has to be what was
    on the screen. So when it is left out, or is not the stored proposal's, nothing is accepted.

    ``rejected`` are the paths the creator unticked, of report items and of free text
    (``report["unverified"]``). ``keep`` are the paths of items the material did not bear out that the
    creator ticked all the same: a habit that was not found, an entry that could not be checked.
    ``evidence.apply_decisions`` applies both, and removes whatever the creator says quotes that are
    not found in their material and bans on things they do say. The report it works from is computed
    here, against the confirmed items of the library as it is now. Refused phrases the creator typed
    since the proposal was made are taken out of its ban lists, and a speaking rate they typed in keeps
    priority over a measured or default one. The previous profile is kept in history (``save_dna``).

    Raises ``VoiceReviewError``: ``no_pending``; ``stale_proposal`` when ``created_at`` does not name the
    stored proposal; or ``no_samples`` when the library no longer holds anything to check the proposal against.
    """
    with _VOICE_LOCK:
        pending = store.load_pending()
        if pending is None:
            raise VoiceReviewError("no_pending", "There is no proposed voice profile to accept")
        named, stored = guardrails._number(created_at), guardrails._number(pending.get("created_at"))
        if named is None or stored is None or named != stored:
            raise VoiceReviewError("stale_proposal", "The proposal to accept is not the one that is stored now")
        samples = confirmed_samples(store.list_samples())
        if not _organic_texts(samples):
            raise VoiceReviewError("no_samples", "There is no non-sponsored material left to check the proposal against")
        proposal = _stored_proposal(pending["dna"])
        report = evidence.verify_profile(proposal, samples)
        dna = evidence.apply_decisions(proposal, report, rejected if isinstance(rejected, (list, tuple)) else [],
                                       drop_not_found=True, keep_paths=keep if isinstance(keep, (list, tuple)) else [])
        summary = dict(profile_summary(dna))
        summary["refused_phrases_removed"] = (_count(summary.get("refused_phrases_removed"))
                                              + strip_refused(dna, store.load_creator()))
        typed = _typed_rate(store)
        if typed is not None:
            if not isinstance(dna.get("cadence_and_syntax"), dict):
                dna["cadence_and_syntax"] = {}
            dna["cadence_and_syntax"]["target_speaking_rate_cpm"] = typed
            summary.update({"speaking_rate_confirmed": True, "speaking_rate_source": RATE_TYPED,
                            "speaking_rate_measured": None})
        now = time.time()
        summary.setdefault("based_on", _count(pending.get("based_on")))
        summary.setdefault("proposed_at", pending.get("created_at"))
        summary.update({"accepted_at": now, "learned_at": time.strftime(TIME_FORMAT, time.localtime(now))})
        dna["source_corpus_summary"] = summary
        store.save_dna(dna)
        store.clear_pending()
        return dna


def discard_voice(store: Store) -> bool:
    """Drop the proposal. The active profile is not touched. False when there was none."""
    with _VOICE_LOCK:
        return store.clear_pending()


def learn_voice(store: Store, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Ask the configured model for a profile and store it as a proposal (see ``propose_voice``)."""
    system, user, info = _build_voice(store)
    return propose_voice(store, complete(store, system, user, transport), brief=info)


def set_speaking_rate(store: Store, cpm: int) -> Dict[str, Any]:
    """Record her real speaking rate on her own profile (never on the bundled example).

    A typed rate outranks a measured one from then on, also when a later proposal is accepted.
    A profile whose ``cadence_and_syntax`` or ``source_corpus_summary`` is not an object (one stored by
    an early version from a wrong-shaped reply) gets a fresh one there.
    """
    dna, is_example = store.load_dna()
    if is_example or not isinstance(dna, dict):
        raise ValueError("no personal profile yet")
    for key in ("cadence_and_syntax", "source_corpus_summary"):
        if not isinstance(dna.get(key), dict):
            dna[key] = {}
    dna["cadence_and_syntax"]["target_speaking_rate_cpm"] = int(cpm)
    dna["source_corpus_summary"].update({"speaking_rate_confirmed": True, "speaking_rate_source": RATE_TYPED,
                                         "speaking_rate_measured": None})
    store.save_dna(dna)
    return dna


# ---- topic recommendations --------------------------------------------------
_IDEA_TEXT_FIELDS = ("title_zh", "title_en", "kind", "why_zh", "why_en", "confidence", "script_notes_zh")


def _usable_samples(store: Store) -> List[Dict[str, Any]]:
    samples = [s for s in store.list_samples() if s["text"].strip()]
    if not samples:
        raise llm.LLMError("No past scripts to analyse", "no_samples")
    return samples


def _current_trends(store: Store) -> List[Dict[str, Any]]:
    """Scored trend keywords for the ideas prompt. Keywords that clash with her voice are left out."""
    return [{
        "keyword": t["keyword"], "score": t["score"], "trend": t["trend"], "creator_videos": t["her_videos"],
        "creator_lift": t["lift_raw"], "creator_evidence_titles": t["evidence_titles"],
        "source": "ai_lookup_unverified" if t["source"] == "ai" else "entered_by_creator",
    } for t in trends.payload(store)["trends"] if t["usable"]][:IDEAS_MAX_TRENDS]


def _history_entry(sample: Dict[str, Any]) -> Dict[str, Any]:
    """One item of the library as the ideas prompt receives it.

    The title, whether it is sponsored and the numbers the creator typed go for every item. The wording
    (the first RECOMMEND_EXCERPT_CHARS characters) goes only for text the creator wrote or confirmed. A
    transcript a machine wrote that is still waiting for the creator is said to be "used for nothing but
    the speaking rate until confirmed", so its words stay here: ``script_excerpt`` is null and
    ``transcript_not_confirmed`` says why.

    A posted script that is still waiting (``corpus.waits_as_posted_script``) is the video's own script, so
    its excerpt goes: this prompt asks what the video was about and how it did, and learns no wording.
    ``script_written_in_app_wording_not_confirmed`` says that the words may be an AI's. For an item with
    both days, ``numbers_read_days_after_posting`` says how old the numbers were when they were read.
    """
    entry: Dict[str, Any] = {
        "title": sample["title"], "is_sponsored": sample["is_sponsored"],
        "views": sample["views"], "likes": sample["likes"], "collects": sample["collects"], "comments": sample["comments"],
    }
    if corpus.waits_as_posted_script(sample):
        entry.update({"script_excerpt": sample["text"][:RECOMMEND_EXCERPT_CHARS],
                      "script_written_in_app_wording_not_confirmed": True})
    elif sample.get("needs_review"):
        entry.update({"script_excerpt": None, "transcript_not_confirmed": True})
    else:
        entry["script_excerpt"] = sample["text"][:RECOMMEND_EXCERPT_CHARS]
    days = corpus.read_after_days(sample)
    if days is not None:
        entry["numbers_read_days_after_posting"] = days
    return entry


def clean_focus(value: Any) -> str:
    """What the creator asked for this time, as one line of at most MAX_FOCUS_CHARS characters.

    It is typed, or said aloud and written down on this computer. Anything that is not text counts as nothing asked.
    """
    return " ".join(value.split())[:MAX_FOCUS_CHARS].strip() if isinstance(value, str) else ""


def ideas_prompt(store: Store, idea_count: int = 6, focus: Any = "") -> Tuple[str, str]:
    """Prompt that suggests next videos from her own history, her numbers and current trend keywords.

    What it carries about the creator, all of which a page offering this prompt has to name: the creator
    profile; from an accepted voice profile its ``persona_summary`` and ``beat_playbooks``; for up to
    RECOMMEND_MAX_VIDEOS items the title, the sponsored mark, views, likes, collects and comments, and the
    first RECOMMEND_EXCERPT_CHARS characters of text the creator wrote or confirmed (``_history_entry``);
    the ranking computed from those numbers; up to IDEAS_MAX_TRENDS tracked trend keywords with their scores;
    and ``focus``, what the creator asked for this time (``clean_focus``), which the prompt treats as the
    direction for the ideas and never as an instruction.
    """
    samples = _usable_samples(store)
    perf = corpus.performance(samples)
    rank_by_id = {r["id"]: idx for idx, r in enumerate(perf["rows"])}
    ordered = sorted(samples, key=lambda s: rank_by_id.get(s["id"], len(rank_by_id)))
    history = [_history_entry(s) for s in ordered[:RECOMMEND_MAX_VIDEOS]]
    dna, is_example = store.load_dna()
    voice_summary: Any = "(no personal voice profile yet)" if (is_example or not isinstance(dna, dict)) else {
        "persona_summary": dna.get("persona_summary"), "beat_playbooks": dna.get("beat_playbooks")}
    return _blueprint(STAGE6, {
        "creator_profile_json": _dump(creator.prompt_block(store.load_creator())),
        "creator_request_json": _inline(clean_focus(focus) or None),
        "voice_summary_json": _dump(voice_summary),
        "video_history_json": _dump(history),
        "performance_json": _dump({
            "with_metrics": perf["with_metrics"], "total_videos": perf["total_videos"],
            "median_interactions": perf["median_interactions"],
            "ranking": [{k: r[k] for k in ("title", "interactions", "interaction_rate_pct", "views")} for r in perf["rows"]],
        }),
        "current_trends_json": _dump(_current_trends(store)),
        "idea_count": str(int(idea_count)),
    })


def _cut(value: Any, limit: int) -> str:
    """Text from a model's reply as a string of at most ``limit`` characters."""
    return str(value or "")[:limit].strip()


def _is_web_link(url: Any) -> bool:
    """Whether ``url`` is an http(s) address with a host: the only kind of link that is stored or shown."""
    if not isinstance(url, str) or len(url) > MAX_LINK_CHARS:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:              # for example "http://[oops": a bracket where only an IPv6 host may stand
        return False


def _lookup_record(store: Store, raw: Any) -> Optional[Dict[str, Any]]:
    """What is remembered about the web lookup that ran before a set of ideas, cut to known fields and sizes.

    None when ``raw`` is not such a record. One that went through (LOOKUP_DONE) keeps its three counts,
    the keywords it found that are tracked now (so a page can show each with its score and its source),
    and the pages the reply cited that have an http(s) address. One that failed or was skipped keeps
    why: ``reason`` (a code) and, for a failure, the message in English and Chinese.
    """
    if not isinstance(raw, dict) or raw.get("status") not in (LOOKUP_DONE, LOOKUP_FAILED, LOOKUP_SKIPPED):
        return None
    if raw["status"] != LOOKUP_DONE:
        return {"status": raw["status"], "reason": _cut(raw.get("reason"), 40),
                "message": _cut(raw.get("message"), MAX_IDEA_TEXT_CHARS),
                "message_zh": _cut(raw.get("message_zh"), MAX_IDEA_TEXT_CHARS)}
    tracked = {keyword_key(t["keyword"]): t["keyword"] for t in store.list_trends()}
    named = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
    keywords = _distinct_keywords([tracked[keyword_key(k)] for k in named
                                   if isinstance(k, str) and keyword_key(k) in tracked])
    pages: List[Dict[str, str]] = []
    for page in raw.get("pages") if isinstance(raw.get("pages"), list) else []:
        if len(pages) == MAX_LOOKUP_PAGES:
            break
        if isinstance(page, dict) and _is_web_link(page.get("url")):
            pages.append({"url": page["url"], "title": _cut(page.get("title"), 200)})
    return {"status": LOOKUP_DONE, "added": _count(raw.get("added")),
            "dropped_no_source": _count(raw.get("dropped_no_source")),
            "dropped_too_old": _count(raw.get("dropped_too_old")), "keywords": keywords, "pages": pages}


def _distinct_keywords(keywords: List[str]) -> List[str]:
    """Keywords without repeats, in the order given, at most MAX_LOOKUP_KEYWORDS of them."""
    seen, out = set(), []
    for keyword in keywords:
        key = keyword_key(keyword)
        if key and key not in seen:
            seen.add(key)
            out.append(keyword)
    return out[:MAX_LOOKUP_KEYWORDS]


def apply_ideas(store: Store, result: Dict[str, Any], focus: Any = "", lookup: Any = None) -> Dict[str, Any]:
    """Sanitise suggested ideas and save them.

    Cited video titles that are not hers and trend keywords she is not tracking are removed.
    Every piece of text is cut to a limit. ``script_notes_zh`` matters most (MAX_IDEA_NOTES_CHARS): when
    the creator picks an idea it becomes the notes of a draft prompt. ``kind`` and ``confidence`` are kept
    only when they are one of the values the prompt allows, and at most MAX_IDEAS ideas are kept.

    ``focus`` is what the creator asked for, as it went into the prompt, and ``lookup`` how the web lookup
    before these ideas went (``_lookup_record``; None when there was none). Both are saved with the ideas,
    so the page can say what they answer and where their trend keywords came from.
    """
    samples = _usable_samples(store)
    perf = corpus.performance(samples)
    known_titles = {s["title"] for s in samples}
    tracked = {keyword_key(t["keyword"]): t["keyword"] for t in store.list_trends()}
    ideas: List[Dict[str, Any]] = []
    for raw in result.get("ideas") if isinstance(result.get("ideas"), list) else []:
        if len(ideas) == MAX_IDEAS:
            break
        if not isinstance(raw, dict) or not _cut(raw.get("title_zh"), MAX_IDEA_TEXT_CHARS):
            continue
        idea: Dict[str, Any] = {f: _cut(raw.get(f), MAX_IDEA_TEXT_CHARS) for f in _IDEA_TEXT_FIELDS}
        idea["script_notes_zh"] = _cut(raw.get("script_notes_zh"), MAX_IDEA_NOTES_CHARS)
        idea["kind"] = raw.get("kind") if raw.get("kind") in IDEA_KINDS else ""
        idea["confidence"] = raw.get("confidence") if raw.get("confidence") in IDEA_CONFIDENCE else ""
        idea["video_format"] = raw.get("video_format") if raw.get("video_format") in VIDEO_FORMATS else VIDEO_FORMATS[0]
        idea["target_duration_sec"] = raw.get("target_duration_sec") if raw.get("target_duration_sec") in (30, 60, 90) else 60
        cited = [str(t) for t in (raw.get("evidence_titles") if isinstance(raw.get("evidence_titles"), list) else [])
                 if isinstance(t, str)]
        idea["evidence_titles"] = [t for t in cited if t in known_titles]
        idea["unverified_evidence"] = len(cited) - len(idea["evidence_titles"])
        named = raw.get("trend_keywords") if isinstance(raw.get("trend_keywords"), list) else []
        idea["trend_keywords"] = normalize_keywords(
            [tracked[keyword_key(k)] for k in named if isinstance(k, str) and keyword_key(k) in tracked])
        ideas.append(idea)
    if not ideas:
        raise llm.LLMError("Model reply contained no ideas", "empty_result")
    payload = {
        "generated_at": time.time(),
        "focus": clean_focus(focus), "lookup": _lookup_record(store, lookup),
        "videos_with_metrics": perf["with_metrics"], "total_videos": perf["total_videos"],
        "data_note_zh": _cut(result.get("data_note_zh"), MAX_IDEA_TEXT_CHARS),
        "data_note_en": _cut(result.get("data_note_en"), MAX_IDEA_TEXT_CHARS),
        "ideas": ideas,
    }
    store.save_recommendations(payload)
    return payload


def recommend_topics(store: Store, idea_count: int = 6, transport: Optional[llm.Transport] = None,
                     focus: Any = "") -> Dict[str, Any]:
    system, user = ideas_prompt(store, idea_count, focus)
    return apply_ideas(store, complete(store, system, user, transport), focus=focus)


# ---- trend research ---------------------------------------------------------
def trends_prompt(store: Store, focus: Any = "", keyword_count: int = 12) -> Tuple[str, str]:
    """Prompt for an AI that can search the web to look up current trend keywords in her niches.

    This prompt leaves for a web-searching AI, so it carries no more about the creator than the lookup
    needs. That is exactly: the niches from the creator profile, the titles of up to LOOKUP_MAX_TITLES
    non-sponsored items, the keywords already tracked, and the focus typed or said for this lookup
    (``clean_focus``).

    Nothing else from the creator profile goes (not the name, the account name, the self-description,
    the pronoun, an on-camera partner or the refused phrases), and nothing from the voice profile: its
    one-line persona is written by a model and could name the creator.
    """
    titles = [s["title"] for s in store.list_samples() if not s["is_sponsored"]][:LOOKUP_MAX_TITLES]
    focus = clean_focus(focus)
    niches = creator.prompt_block(store.load_creator())["niches"]
    if not focus and not titles and not niches:
        raise llm.LLMError("Nothing says what the creator's niches are: no focus was typed, the creator profile "
                           "lists none and the library is empty", "no_focus")
    return _blueprint(STAGE7, {
        "today": time.strftime("%Y-%m-%d"),
        "max_age_days": str(TREND_SOURCE_MAX_AGE_DAYS),
        "creator_profile_json": _dump({"niches": niches}),
        "niche_json": _dump({"typed_focus": focus or None, "creator_video_titles": titles}),
        "known_keywords_json": _dump([t["keyword"] for t in store.list_trends()]),
        "keyword_count": str(int(keyword_count)),
    })


def _source_time(value: Any, now: float) -> Optional[float]:
    """When the evidence dates from: ``now`` if no usable date was given, None if it is too old."""
    try:
        seen = time.mktime(time.strptime(str(value or "").strip()[:10], "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return now
    if seen > now:
        return now
    return None if now - seen > TREND_SOURCE_MAX_AGE_DAYS * 86400 else seen


def store_trends(store: Store, result: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Store keywords an AI found on the web, marked as AI-found and unverified.

    A keyword is kept only if it comes with an http(s) source link and its
    source is not older than TREND_SOURCE_MAX_AGE_DAYS. Heat numbers from an AI are ignored.

    Returns ``{"added", "dropped_no_source", "dropped_too_old", "keywords"}``. ``keywords`` are the
    ones that came with a usable source, in the reply's order. That can be more than ``added``: a
    keyword the creator typed themselves stays exactly as they have it and is not counted (``upsert_trends``).
    """
    if result.get("cannot_browse") is True:
        raise llm.LLMError("The AI said it cannot search the web, so nothing was added", "cannot_browse")
    now = time.time() if now is None else now
    items: List[Dict[str, Any]] = []
    no_source = too_old = 0
    for raw in result.get("keywords") if isinstance(result.get("keywords"), list) else []:
        if not isinstance(raw, dict):
            continue
        keyword = clean_keyword(raw.get("keyword"))
        url = str(raw.get("source_url") or "").strip()
        if not keyword or len(keyword) > MAX_KEYWORD_CHARS:
            continue
        if not _is_web_link(url):
            no_source += 1
            continue
        seen = _source_time(raw.get("source_date"), now)
        if seen is None:
            too_old += 1
            continue
        items.append({"keyword": keyword, "source_url": url, "seen_at": seen,
                      "note_zh": str(raw.get("why_zh") or "")[:200], "note_en": str(raw.get("why_en") or "")[:200]})
    if not items:
        raise llm.LLMError("No keyword came with a usable source link", "empty_result")
    return {"added": store.upsert_trends(items, source="ai"), "dropped_no_source": no_source, "dropped_too_old": too_old,
            "keywords": _distinct_keywords([item["keyword"] for item in items])}


def apply_trends(store: Store, result: Dict[str, Any], now: Optional[float] = None) -> Dict[str, int]:
    """``store_trends``, returning its three counts only: ``{"added", "dropped_no_source", "dropped_too_old"}``."""
    found = store_trends(store, result, now)
    return {key: found[key] for key in ("added", "dropped_no_source", "dropped_too_old")}


def research_trends(store: Store, focus: Any = "", transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Look trend keywords up with the web search of the provider chosen in settings, and store what comes back sourced.

    What leaves is ``trends_prompt`` and nothing more. Returns what ``store_trends`` returns, plus
    ``pages``: the pages the reply cites (``[{"url", "title"}]``, as the provider reported them).

    Raises ``llm.LLMError``. ``cannot_browse``: the provider in settings has no web search of its own
    (a local model, copy-and-paste mode), or the model answered that it could not search.
    ``not_configured``: no model or key. Otherwise whatever ``trends_prompt``, the call or
    ``store_trends`` raise.
    """
    config = store.load_config()
    provider, model = config.get("provider") or "", config.get("model") or ""
    if not llm.can_search(provider):
        raise llm.LLMError("The AI service chosen in settings cannot search the web", "cannot_browse",
                           "设置里选的 AI 服务不能联网搜索")
    api_key = config["keys"].get(provider) or ""
    if not model or not api_key:
        raise llm.LLMError("AI is not set up yet", "not_configured")
    system, user = trends_prompt(store, focus)
    result, pages = llm.search_json(provider, api_key, model, single_prompt(system, user), transport=transport)
    return dict(store_trends(store, result), pages=pages)


def discover(store: Store, focus: Any = "", transport: Optional[llm.Transport] = None,
             progress: Optional[Callable[[str, Optional[float]], None]] = None, lookup: bool = True,
             found: Optional[List[Any]] = None) -> Dict[str, Any]:
    """One request, the whole way: look up what is current on the web, then suggest what to film.

    Two exchanges, kept apart on purpose. The first goes to a model that searches the web and may put
    what it reads into search queries, so it carries only what ``trends_prompt`` names. The second is
    ``ideas_prompt``: the fuller picture of the creator, sent with no web search switched on. It sees
    what the first found only as tracked keywords, scored against the creator's own results.

    The lookup runs when ``lookup`` is true and the provider in settings can search the web
    (``llm.can_search``); otherwise it is skipped and the ideas use the keywords already tracked.
    ``found`` says the lookup was already done by hand, just before (copy and paste with a web-searching
    AI, stored by ``store_trends``): it lists the keywords that one brought in. No lookup is made here
    then, and the ideas are saved with that one, of which only keywords that are tracked are kept.

    Returns ``{"focus", "lookup", "ideas"}`` and raises ``llm.LLMError`` for neither step, so that one
    failing does not hide what the other did:

    - ``lookup`` is a ``_lookup_record``: LOOKUP_DONE with its counts, keywords and pages; LOOKUP_FAILED
      with the reason; or LOOKUP_SKIPPED with ``reason`` ``not_asked`` or ``no_web_search``.
    - ``ideas`` is ``{"status": "done", "count"}`` once they are saved (``apply_ideas``, which records the
      focus and the lookup with them), or ``{"status": "failed", "reason", "message", "message_zh"}``.
      Ideas that fail leave the ideas saved earlier as they were.

    ``progress(stage, fraction)`` is told ``looking_up`` and then ``thinking``.
    """
    focus = clean_focus(focus)
    say = progress if progress is not None else (lambda stage, fraction=None: None)
    looked: Dict[str, Any]
    if found is not None:
        looked = {"status": LOOKUP_DONE, "keywords": found}
    elif not lookup:
        looked = {"status": LOOKUP_SKIPPED, "reason": "not_asked"}
    elif not llm.can_search(store.load_config().get("provider") or ""):
        looked = {"status": LOOKUP_SKIPPED, "reason": "no_web_search"}
    else:
        say("looking_up", None)
        try:
            looked = dict(research_trends(store, focus, transport), status=LOOKUP_DONE)
        except llm.LLMError as exc:
            looked = {"status": LOOKUP_FAILED, "reason": exc.code, "message": str(exc), "message_zh": exc.message_zh}
    record = _lookup_record(store, looked)
    say("thinking", None)
    try:
        system, user = ideas_prompt(store, focus=focus)
        saved = apply_ideas(store, complete(store, system, user, transport), focus=focus, lookup=record)
        ideas: Dict[str, Any] = {"status": "done", "count": len(saved["ideas"])}
    except llm.LLMError as exc:
        ideas = {"status": "failed", "reason": exc.code, "message": _cut(str(exc), MAX_IDEA_TEXT_CHARS),
                 "message_zh": _cut(exc.message_zh, MAX_IDEA_TEXT_CHARS)}
    return {"focus": focus, "lookup": record, "ideas": ideas}


# ---- lessons from the creator's edits ---------------------------------------
# No model is called here. These live in this module because what they produce fills the draft prompt
# (``_build_draft`` reads the remembered lessons through ``learning.prompt_rules``).
def script_changes(store: Store, script_id: str) -> Optional[Dict[str, Any]]:
    """What the creator changed in an AI draft: ``{"has_ai_draft", "changes"}``. None for an unknown script.

    ``changes`` is ``learning.line_changes`` between the lines as the AI wrote them and the lines as they
    stand now. Each carries ``lesson_id``: the id of the remembered lesson with the same ``ai`` and
    ``final``, or None. A script that was not drafted by AI has nothing to compare with.

    ``by_ai`` is true for a change whose final line says the words of a proposal an AI made for this
    script (``suggest.apply_suggest`` writes each one down). The creator chose it, but did not write it:
    it is shown as such and cannot be remembered as a lesson about how the creator words things.
    """
    script = store.get_script(script_id)
    if script is None:
        return None
    ai_lines = store.get_ai_lines(script_id)
    if ai_lines is None:
        return {"has_ai_draft": False, "changes": []}
    remembered = {(lesson["ai"], lesson["final"]): lesson["id"] for lesson in store.list_lessons()}
    proposed = _proposed_words(store, script_id)
    changes = learning.line_changes(ai_lines, script["lines"])
    for change in changes:
        change["lesson_id"] = remembered.get((change["ai"], change["final"]))
        change["by_ai"] = _is_proposed(change["final"], proposed)
    return {"has_ai_draft": True, "changes": changes}


def _proposed_words(store: Store, script_id: Optional[str] = None) -> Set[str]:
    """The words of every text an AI proposed for a line: of one script, or of all scripts when no id is given."""
    return {key for key in (learning.spoken_key(text) for text in store.list_ai_suggestions(script_id)) if key}


def _is_proposed(final: Any, proposed: Set[str]) -> bool:
    """Whether a line as it stands now says the words of a proposal an AI made, punctuation and spacing aside."""
    return bool(proposed) and learning.spoken_key(final) in proposed


def remember_lesson(store: Store, *, script_id: Any, ai: Any, final: Any, note: Any = "") -> Tuple[Dict[str, Any], bool]:
    """Remember one change the creator made to an AI draft. Returns ``(lesson, created)``.

    The same ``ai`` and ``final`` again returns the lesson already remembered. Raises ``ValueError`` whose
    message is a code: ``not_found`` (no such script), ``no_ai_draft`` (the script was not drafted by AI,
    so there is no "what the AI wrote"), ``bad_lesson`` (wrong types or over-long text), ``no_change``
    (the two versions differ in nothing but spacing or punctuation, which a later draft could not use) or
    ``ai_text`` (the final wording is a proposal an AI made for this script: the creator chose it but did
    not write it, so it says nothing about how the creator words things).
    """
    if not isinstance(script_id, str) or store.get_script(script_id) is None:
        raise ValueError("not_found")
    if store.get_ai_lines(script_id) is None:
        raise ValueError("no_ai_draft")
    ai, final, note = ("" if value is None else value for value in (ai, final, note))
    if not all(isinstance(value, str) for value in (ai, final, note)):
        raise ValueError("bad_lesson")
    ai, final = ai.strip(), final.strip()
    if len(ai) > MAX_LESSON_CHARS or len(final) > MAX_LESSON_CHARS:
        raise ValueError("bad_lesson")
    if not learning.prompt_rules([{"ai": ai, "final": final}]):
        raise ValueError("no_change")
    if _is_proposed(final, _proposed_words(store, script_id)):
        raise ValueError("ai_text")
    return store.add_lesson(script_id=script_id, ai=ai, final=final, note=" ".join(note.split())[:MAX_LESSON_NOTE_CHARS])


def _already_refused(phrase: str, who: Any) -> bool:
    """Whether the creator's own list already catches this phrase when it is said on its own.

    Asked of the linter (through ``creator.never_hits``) rather than compared as text, so letter case
    and the stored form of a phrase make no difference.
    """
    return bool(creator.never_hits({"script_lines": [{"line_no": 1, "spoken_script_zh": phrase}]}, who))


def lessons_payload(store: Store) -> Dict[str, Any]:
    """``{"lessons", "suggestions"}``: remembered lessons, newest first, and phrases worth refusing outright.

    ``suggestions`` are ``learning.recurring_removals`` over every AI-drafted script: phrases the creator
    took out of AI drafts in at least two scripts. Phrases their refused list already catches are left out.
    A line that now holds an AI's proposal is not counted: what went missing there was the proposal's doing.
    """
    who = store.load_creator()
    proposed = _proposed_words(store)
    changes_by_script = {draft["id"]: [change for change in learning.line_changes(draft["ai_lines"], draft["lines"])
                                       if not _is_proposed(change["final"], proposed)]
                         for draft in store.list_ai_drafts()}
    suggestions = [s for s in learning.recurring_removals(changes_by_script) if not _already_refused(s["phrase"], who)]
    return {"lessons": store.list_lessons(), "suggestions": suggestions}
