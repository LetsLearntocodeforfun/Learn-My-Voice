"""Model-backed steps: drafting, learning her voice, and recommending topics.

Each step is split in two so it can run three ways:

- ``*_prompt``  builds the exact text a model needs (her profile, her scripts, the rules);
- ``apply_*``   validates a model's JSON reply and saves it.

The app calls a model in between (``generate_script`` etc.). Copy-and-paste
mode and the MCP server hand the prompt to an outside model (for example a
ChatGPT subscription) and feed its reply back through the same ``apply_*``.

The markdown blueprints in prompts/ stay the single source of truth for prompt
text; this module only extracts the fenced blocks and fills ``{{placeholders}}``.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import corpus, llm, trends
from .store import (MANUAL_PROVIDER, MAX_KEYWORD_CHARS, Store, clean_keyword, keyword_key, normalize_keywords,
                    normalize_lines)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
STAGE2 = "02_voice_dna_synthesizer.md"
STAGE4 = "04_script_generator_normal_and_sponsored.md"
STAGE6 = "06_content_recommender.md"
STAGE7 = "07_trend_researcher.md"
VIDEO_FORMATS = ("VLOG_VOICEOVER", "COUPLE_VLOG", "FAVORITES_ROUNDUP", "SINGLE_PRODUCT_SHARE")

DEFAULT_SPEAKING_RATE = 240       # used until her real rate is entered; cannot be measured from text
MIN_CLAUSES_FOR_MEASURED_CADENCE = 30
LEARN_CORPUS_CHARS = 40000
RECOMMEND_EXCERPT_CHARS = 400
RECOMMEND_MAX_VIDEOS = 60
IDEAS_MAX_TRENDS = 12
TREND_SOURCE_MAX_AGE_DAYS = 30

MANUAL_REPLY_RULE = (
    "IMPORTANT: Reply with ONLY the JSON object described above, inside one ```json code block. "
    "No explanation before or after it."
)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptError(Exception):
    pass


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


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


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
def draft_prompt(store: Store, *, notes: str, target_sec: int, video_format: str,
                 keywords: Any = None) -> Tuple[str, str]:
    """Prompt for a normal (non-sponsored) script, anchored on her own past scripts.

    ``keywords`` are trend or search terms to work in where they fit her voice.
    When given, her own scripts on those topics come first among the examples.
    """
    dna, _ = store.load_dna()
    words = normalize_keywords(keywords)
    on_topic = (lambda sample: trends.is_on_topic(sample, words)) if words else None
    anchors = corpus.exemplars(store.list_samples(), on_topic=on_topic)
    exemplars_text = _dump(anchors) if anchors else "[]  (no past scripts have been imported yet; rely on the Voice DNA)"
    return _blueprint(STAGE4, {
        "voice_dna_json": _dump(dna),
        "retrieved_beat_exemplars_json": exemplars_text,
        "top_10_wife_edit_diff_rules_json": "[]",
        "mode": "NORMAL_ORGANIC",
        "video_format": video_format,
        "target_duration_sec": str(int(target_sec)),
        "user_topic_and_story_notes": notes.strip(),
        "approved_humanized_sponsor_brief_json": "null",
        "target_keywords_json": json.dumps(words, ensure_ascii=False),
    })


def apply_draft(store: Store, result: Dict[str, Any], *, notes: str, target_sec: int, video_format: str,
                default_title: str = "未命名脚本", keywords: Any = None) -> Dict[str, Any]:
    """Validate a drafted script and save it with its target keywords; returns the stored script."""
    lines = [l for l in normalize_lines(result.get("script_lines")) if l["spoken_script_zh"].strip()]
    if not lines:
        raise llm.LLMError("Model reply contained no script lines", "empty_result")
    post = result.get("rednote_post_package")
    return store.create_script(
        notes=notes, target_sec=target_sec, video_format=video_format, default_title=default_title,
        lines=normalize_lines(lines), post=post if isinstance(post, dict) else {}, from_ai=True, keywords=keywords,
    )


def generate_script(store: Store, *, notes: str, target_sec: int, video_format: str,
                    transport: Optional[llm.Transport] = None, default_title: str = "未命名脚本",
                    keywords: Any = None) -> Dict[str, Any]:
    system, user = draft_prompt(store, notes=notes, target_sec=target_sec, video_format=video_format, keywords=keywords)
    result = complete(store, system, user, transport)
    return apply_draft(store, result, notes=notes, target_sec=target_sec, video_format=video_format,
                       default_title=default_title, keywords=keywords)


# ---- learning her voice -----------------------------------------------------
def _organic_texts(samples: List[Dict[str, Any]]) -> List[str]:
    return [s["text"] for s in samples if not s["is_sponsored"] and s["text"].strip()]


def voice_prompt(store: Store, corpus_chars: int = LEARN_CORPUS_CHARS) -> Tuple[str, str]:
    """Prompt that turns her imported scripts into a Voice DNA profile."""
    samples = store.list_samples()
    organic_texts = _organic_texts(samples)
    if not organic_texts:
        raise llm.LLMError("No non-sponsored scripts to learn from", "no_samples")
    stats = corpus.voice_stats(organic_texts)
    organic = corpus.corpus_slice(samples, sponsored=False, budget_chars=corpus_chars)
    sponsored = corpus.corpus_slice(samples, sponsored=True, budget_chars=corpus_chars // 4)
    return _blueprint(STAGE2, {
        "python_aggregated_acoustic_metrics_json": _dump({
            "note": "Measured from her hand-written scripts. No audio was analysed, so there is no speaking-rate "
                    "measurement; do not infer one. Use these clause-length and particle numbers as ground truth.",
            **stats,
        }),
        "organic_videos_segmented_beats_and_captions_json": _dump({
            "note": "Scripts she wrote by hand for her own videos, newest first. '/' or line breaks mark breath pauses.",
            "scripts": organic,
        }),
        "sponsored_videos_bridge_and_review_beats_only_json": _dump(sponsored) if sponsored else "[]",
        "wife_past_script_edits_diff_log_if_any": "[]",
    })


def apply_voice(store: Store, dna: Dict[str, Any], corpus_chars: int = LEARN_CORPUS_CHARS) -> Dict[str, Any]:
    """Validate a Voice DNA reply, overwrite its numbers with measurements, and make it the active profile.

    Clause-length numbers come from measurement, not from the model, once there
    are enough clauses. Speaking rate cannot be measured from text: an existing
    confirmed rate is kept, otherwise a default is used and flagged.
    """
    if not isinstance(dna.get("cadence_and_syntax"), dict) or not isinstance(dna.get("anti_voice_blacklist"), dict):
        raise llm.LLMError("Model reply is not a usable voice profile", "empty_result")
    samples = store.list_samples()
    organic_texts = _organic_texts(samples)
    if not organic_texts:
        raise llm.LLMError("No non-sponsored scripts to learn from", "no_samples")
    stats = corpus.voice_stats(organic_texts)
    organic = corpus.corpus_slice(samples, sponsored=False, budget_chars=corpus_chars)
    sponsored = corpus.corpus_slice(samples, sponsored=True, budget_chars=corpus_chars // 4)

    previous, previous_is_example = store.load_dna()
    previous_rate = previous.get("cadence_and_syntax", {}).get("target_speaking_rate_cpm")
    rate_known = (not previous_is_example) and bool(previous.get("source_corpus_summary", {}).get("speaking_rate_confirmed"))
    cadence = dna["cadence_and_syntax"]
    cadence["target_speaking_rate_cpm"] = previous_rate if rate_known else DEFAULT_SPEAKING_RATE
    measured = stats["clauses"] >= MIN_CLAUSES_FOR_MEASURED_CADENCE
    if measured:
        lengths = stats["clause_length_units"]
        cadence["ideal_clause_length_chars"] = {"min": lengths["p5"], "median": lengths["median"], "hard_max": lengths["p95"]}
    dna["source_corpus_summary"] = {
        "organic_scripts_analyzed": len(organic), "sponsored_scripts_analyzed": len(sponsored),
        "clauses_measured": stats["clauses"], "clause_lengths_measured": measured,
        "speaking_rate_confirmed": rate_known, "learned_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    store.save_dna(dna)
    return dna


def learn_voice(store: Store, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    system, user = voice_prompt(store)
    return apply_voice(store, complete(store, system, user, transport))


def set_speaking_rate(store: Store, cpm: int) -> Dict[str, Any]:
    """Record her real speaking rate on her own profile (never on the bundled example)."""
    dna, is_example = store.load_dna()
    if is_example:
        raise ValueError("no personal profile yet")
    dna.setdefault("cadence_and_syntax", {})["target_speaking_rate_cpm"] = int(cpm)
    dna.setdefault("source_corpus_summary", {})["speaking_rate_confirmed"] = True
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
        "keyword": t["keyword"], "score": t["score"], "trend": t["trend"], "her_videos": t["her_videos"],
        "her_lift": t["lift_raw"], "her_evidence_titles": t["evidence_titles"],
        "source": "ai_lookup_unverified" if t["source"] == "ai" else "entered_by_her",
    } for t in trends.payload(store)["trends"] if t["usable"]][:IDEAS_MAX_TRENDS]


def ideas_prompt(store: Store, idea_count: int = 6) -> Tuple[str, str]:
    """Prompt that suggests next videos from her own history, her numbers and current trend keywords."""
    samples = _usable_samples(store)
    perf = corpus.performance(samples)
    ranked_ids = [r["id"] for r in perf["rows"]]
    ordered = sorted(samples, key=lambda s: ranked_ids.index(s["id"]) if s["id"] in ranked_ids else len(ranked_ids))
    history = [{
        "title": s["title"], "is_sponsored": s["is_sponsored"],
        "views": s["views"], "likes": s["likes"], "collects": s["collects"], "comments": s["comments"],
        "script_excerpt": s["text"][:RECOMMEND_EXCERPT_CHARS],
    } for s in ordered[:RECOMMEND_MAX_VIDEOS]]
    dna, is_example = store.load_dna()
    voice_summary: Any = "(no personal voice profile yet)" if is_example else {
        "persona_summary": dna.get("persona_summary"), "beat_playbooks": dna.get("beat_playbooks")}
    return _blueprint(STAGE6, {
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


def apply_ideas(store: Store, result: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitise suggested ideas and save them.

    Cited video titles that are not hers and trend keywords she is not tracking are removed.
    """
    samples = _usable_samples(store)
    perf = corpus.performance(samples)
    known_titles = {s["title"] for s in samples}
    tracked = {keyword_key(t["keyword"]): t["keyword"] for t in store.list_trends()}
    ideas: List[Dict[str, Any]] = []
    for raw in result.get("ideas") if isinstance(result.get("ideas"), list) else []:
        if not isinstance(raw, dict) or not str(raw.get("title_zh") or "").strip():
            continue
        idea: Dict[str, Any] = {f: str(raw.get(f) or "") for f in _IDEA_TEXT_FIELDS}
        idea["video_format"] = raw.get("video_format") if raw.get("video_format") in VIDEO_FORMATS else VIDEO_FORMATS[0]
        idea["target_duration_sec"] = raw.get("target_duration_sec") if raw.get("target_duration_sec") in (30, 60, 90) else 60
        cited = [str(t) for t in raw.get("evidence_titles") or [] if isinstance(t, str)]
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
        "videos_with_metrics": perf["with_metrics"], "total_videos": perf["total_videos"],
        "data_note_zh": str(result.get("data_note_zh") or ""), "data_note_en": str(result.get("data_note_en") or ""),
        "ideas": ideas,
    }
    store.save_recommendations(payload)
    return payload


def recommend_topics(store: Store, idea_count: int = 6, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    system, user = ideas_prompt(store, idea_count)
    return apply_ideas(store, complete(store, system, user, transport))


# ---- trend research ---------------------------------------------------------
def trends_prompt(store: Store, focus: str = "", keyword_count: int = 12) -> Tuple[str, str]:
    """Prompt for an AI that can search the web to look up current trend keywords in her niches."""
    titles = [s["title"] for s in store.list_samples() if not s["is_sponsored"]][:20]
    focus = focus.strip()
    if not focus and not titles:
        raise llm.LLMError("Nothing says what her niches are: no focus was typed and the library is empty", "no_focus")
    dna, is_example = store.load_dna()
    summary = dna.get("persona_summary")        # written by a model, so it may not be an object
    persona = None if is_example or not isinstance(summary, dict) else summary.get("one_line_essence_zh")
    return _blueprint(STAGE7, {
        "today": time.strftime("%Y-%m-%d"),
        "max_age_days": str(TREND_SOURCE_MAX_AGE_DAYS),
        "niche_json": _dump({"focus_she_typed": focus or None, "her_video_titles": titles, "persona": persona}),
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


def apply_trends(store: Store, result: Dict[str, Any], now: Optional[float] = None) -> Dict[str, int]:
    """Store keywords an AI found on the web, marked as AI-found and unverified.

    A keyword is kept only if it comes with an http(s) source link and its
    source is not older than TREND_SOURCE_MAX_AGE_DAYS. Heat numbers from an AI are ignored.
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
        parts = urllib.parse.urlsplit(url)
        if not keyword or len(keyword) > MAX_KEYWORD_CHARS:
            continue
        if parts.scheme not in ("http", "https") or not parts.hostname:
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
    return {"added": store.upsert_trends(items, source="ai"), "dropped_no_source": no_source, "dropped_too_old": too_old}
