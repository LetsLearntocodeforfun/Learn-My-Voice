"""Model-backed steps: drafting, learning her voice, and recommending topics.

The markdown blueprints in prompts/ stay the single source of truth for prompt
text; this module only extracts the fenced blocks and fills ``{{placeholders}}``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import corpus, llm
from .store import Store, normalize_lines

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
STAGE2 = "02_voice_dna_synthesizer.md"
STAGE4 = "04_script_generator_normal_and_sponsored.md"
STAGE6 = "06_content_recommender.md"
VIDEO_FORMATS = ("VLOG_VOICEOVER", "COUPLE_VLOG", "FAVORITES_ROUNDUP", "SINGLE_PRODUCT_SHARE")

DEFAULT_SPEAKING_RATE = 240       # used until her real rate is entered; cannot be measured from text
MIN_CLAUSES_FOR_MEASURED_CADENCE = 30
LEARN_CORPUS_CHARS = 40000
RECOMMEND_EXCERPT_CHARS = 400
RECOMMEND_MAX_VIDEOS = 60

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


def _ask(store: Store, blueprint: str, values: Dict[str, str], transport: Optional[llm.Transport]) -> Dict[str, Any]:
    """Run one blueprint against the configured provider."""
    config = store.load_config()
    provider, model = config.get("provider") or "", config.get("model") or ""
    api_key = config["keys"].get(provider) or ""
    base_url = config.get("local_base_url") or None
    if not model or (provider == "local" and not base_url) or (provider != "local" and not api_key):
        raise llm.LLMError("AI is not set up yet")
    system, user_template = load_prompt_pair(blueprint)
    return llm.complete_json(provider, api_key, model, system, render(user_template, values),
                             transport=transport, base_url=base_url)


# ---- drafting ---------------------------------------------------------------
def generate_script(store: Store, *, notes: str, target_sec: int, video_format: str,
                    transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Draft a normal (non-sponsored) script, anchored on her own past scripts, and save it."""
    dna, _ = store.load_dna()
    anchors = corpus.exemplars(store.list_samples())
    exemplars_text = _dump(anchors) if anchors else "[]  (no past scripts have been imported yet; rely on the Voice DNA)"
    result = _ask(store, STAGE4, {
        "voice_dna_json": _dump(dna),
        "retrieved_beat_exemplars_json": exemplars_text,
        "top_10_wife_edit_diff_rules_json": "[]",
        "mode": "NORMAL_ORGANIC",
        "video_format": video_format,
        "target_duration_sec": str(int(target_sec)),
        "user_topic_and_story_notes": notes.strip(),
        "approved_humanized_sponsor_brief_json": "null",
    }, transport)
    lines = [l for l in normalize_lines(result.get("script_lines")) if l["spoken_script_zh"].strip()]
    if not lines:
        raise llm.LLMError("Model reply contained no script lines")
    post = result.get("rednote_post_package")
    return store.create_script(
        notes=notes, target_sec=target_sec, video_format=video_format,
        lines=normalize_lines(lines), post=post if isinstance(post, dict) else {}, from_ai=True,
    )


# ---- learning her voice -----------------------------------------------------
def learn_voice(store: Store, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Build her Voice DNA from imported scripts and save it as the active profile.

    Clause-length numbers come from measurement, not from the model, once there
    are enough clauses. Speaking rate cannot be measured from text: an existing
    real profile's rate is kept, otherwise a default is used and flagged.
    """
    samples = store.list_samples()
    organic_texts = [s["text"] for s in samples if not s["is_sponsored"] and s["text"].strip()]
    if not organic_texts:
        raise llm.LLMError("No non-sponsored scripts to learn from")
    stats = corpus.voice_stats(organic_texts)
    organic = corpus.corpus_slice(samples, sponsored=False, budget_chars=LEARN_CORPUS_CHARS)
    sponsored = corpus.corpus_slice(samples, sponsored=True, budget_chars=LEARN_CORPUS_CHARS // 4)

    dna = _ask(store, STAGE2, {
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
    }, transport)

    if not isinstance(dna.get("cadence_and_syntax"), dict) or not isinstance(dna.get("anti_voice_blacklist"), dict):
        raise llm.LLMError("Model reply is not a usable voice profile")

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


def recommend_topics(store: Store, idea_count: int = 6, transport: Optional[llm.Transport] = None) -> Dict[str, Any]:
    """Suggest next videos from her own history and numbers; saves and returns the result."""
    samples = [s for s in store.list_samples() if s["text"].strip()]
    if not samples:
        raise llm.LLMError("No past scripts to analyse")
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

    result = _ask(store, STAGE6, {
        "voice_summary_json": _dump(voice_summary),
        "video_history_json": _dump(history),
        "performance_json": _dump({
            "with_metrics": perf["with_metrics"], "total_videos": perf["total_videos"],
            "median_interactions": perf["median_interactions"],
            "ranking": [{k: r[k] for k in ("title", "interactions", "interaction_rate_pct", "views")} for r in perf["rows"]],
        }),
        "idea_count": str(int(idea_count)),
    }, transport)

    known_titles = {s["title"] for s in samples}
    ideas: List[Dict[str, Any]] = []
    for raw in result.get("ideas") if isinstance(result.get("ideas"), list) else []:
        if not isinstance(raw, dict) or not str(raw.get("title_zh") or "").strip():
            continue
        idea: Dict[str, Any] = {f: str(raw.get(f) or "") for f in _IDEA_TEXT_FIELDS}
        idea["video_format"] = raw.get("video_format") if raw.get("video_format") in VIDEO_FORMATS else VIDEO_FORMATS[0]
        idea["target_duration_sec"] = raw.get("target_duration_sec") if raw.get("target_duration_sec") in (30, 60, 90) else 60
        cited = [str(t) for t in raw.get("evidence_titles") or [] if isinstance(t, str)]
        idea["evidence_titles"] = [t for t in cited if t in known_titles]   # drop citations that are not her videos
        idea["unverified_evidence"] = len(cited) - len(idea["evidence_titles"])
        ideas.append(idea)
    if not ideas:
        raise llm.LLMError("Model reply contained no ideas")
    payload = {
        "generated_at": time.time(),
        "videos_with_metrics": perf["with_metrics"], "total_videos": perf["total_videos"],
        "data_note_zh": str(result.get("data_note_zh") or ""), "data_note_en": str(result.get("data_note_en") or ""),
        "ideas": ideas,
    }
    store.save_recommendations(payload)
    return payload
