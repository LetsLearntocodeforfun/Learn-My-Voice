"""Where the creator stands in the guided setup, worked out from what is actually stored.

Nothing here is saved: every answer is computed from the creator profile, the
library, the settings, the voice profile, the scripts and the remembered
lessons, so the page can never show a step as done that is not.

Steps, in order: ``creator`` (who they are), ``material`` (their own scripts,
subtitle files and videos), ``voice`` (a learned profile they accepted),
``script`` (a first draft). ``next`` is the first of those not done, or ``done``.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import corpus, creator, pipeline
from .store import Store

MATERIAL_DONE_AT = 3      # non-sponsored items with text; fewer is too little to learn a voice from
STEPS = ("creator", "material", "voice", "script")

VOICE_EXAMPLE, VOICE_PENDING, VOICE_ACTIVE = "example", "pending", "active"


def _organic(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Non-sponsored items with text that the creator wrote or confirmed: what a voice is measured and learned from.

    A transcript still waiting for the creator's confirmation is not among them (``pipeline.confirmed_samples``).
    """
    return [s for s in pipeline.confirmed_samples(samples) if not s["is_sponsored"] and s["text"].strip()]


def _timed_seconds(samples: List[Dict[str, Any]]) -> float:
    """Seconds of speech with timestamps in non-sponsored items, also when that is too little for a rate.

    Counted the way ``timing.combined_rate`` pools its videos, so once there is a rate this is the
    ``spoken_sec`` it was measured from. Transcripts waiting for confirmation count: timing is a measurement.
    """
    total = 0.0
    for sample in samples:
        stats = sample.get("timing")
        if sample.get("is_sponsored") or not isinstance(stats, dict):
            continue
        units, seconds = stats.get("units"), stats.get("spoken_sec")
        if isinstance(units, bool) or isinstance(seconds, bool) or not isinstance(units, int) \
                or not isinstance(seconds, (int, float)) or units <= 0 or seconds <= 0:
            continue
        total += seconds
    return round(total, 3)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _usable_since(sample: Dict[str, Any]) -> float:
    """When an item became material a voice can be learned from: when it was added, or confirmed if that was later."""
    reviewed_at = sample.get("reviewed_at")
    confirmed = reviewed_at if isinstance(reviewed_at, (int, float)) and not isinstance(reviewed_at, bool) else 0
    return max(sample["created_at"], confirmed)


def _learned_time(summary: Dict[str, Any]) -> Optional[float]:
    """When the active profile's reading of the library dates from, as a timestamp; None if it does not say.

    That is when its prompt was built: material added after that moment is material the profile has
    not seen, whether it arrived before or after the creator accepted the proposal. Profiles learned
    before proposals existed only carry ``learned_at`` as text, to the minute; the end of that minute
    is used, so material that was already there is never reported as added since.
    """
    for key in ("material_as_of", "proposed_at", "accepted_at"):
        value = summary.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    try:
        return time.mktime(time.strptime(str(summary.get("learned_at") or ""), pipeline.TIME_FORMAT)) + 60
    except (ValueError, OverflowError):
        return None


def _voice(store: Store, organic: List[Dict[str, Any]]) -> Dict[str, Any]:
    dna, is_example = store.load_dna()
    pending = store.load_pending()
    if is_example:
        voice = {"state": VOICE_PENDING if pending else VOICE_EXAMPLE, "learned_at": None,
                 "based_on": _count(pending.get("based_on")) if pending else 0,
                 "new_since": 0, "has_pending": pending is not None}
        if store.dna_unreadable():
            voice["unreadable"] = True      # their own profile file is there but cannot be read; the example is in use
        return voice
    summary = pipeline.profile_summary(dna)
    learned_at = summary.get("learned_at")
    based_on = summary.get("based_on", summary.get("organic_scripts_analyzed"))
    since = _learned_time(summary)
    return {
        # A profile of the creator's own is in use, whether or not a newer proposal is also waiting.
        "state": VOICE_ACTIVE,
        "learned_at": learned_at if isinstance(learned_at, str) else None,
        "based_on": _count(based_on),
        "new_since": sum(1 for s in organic if _usable_since(s) > since) if since is not None else 0,
        "has_pending": pending is not None,
    }


def voice_history(store: Store) -> List[Dict[str, Any]]:
    """Earlier voice profiles for the page, newest first.

    ``[{"id", "saved_at", "learned_at", "based_on", "essence_zh", "essence_en"}]``, one for each profile
    that was in use and has since been replaced (``Store.list_dna_history``):

    - ``id`` is what ``POST /api/voice/history/<id>/restore`` takes.
    - ``saved_at`` is when it was replaced, in seconds since 1970.
    - ``learned_at`` (text, to the minute) and ``based_on`` (how many non-sponsored items) are read from the
      profile the same way the setup status reads them for the profile in use; None and 0 when it does not say.
    - ``essence_zh`` and ``essence_en`` are its one-line descriptions, or ``""``.
    """
    history = []
    for entry in store.list_dna_history():
        dna = entry["dna"]
        summary = pipeline.profile_summary(dna)
        learned_at = summary.get("learned_at")
        persona = dna.get("persona_summary")
        persona = persona if isinstance(persona, dict) else {}
        essence = {key: persona.get("one_line_essence_" + key) for key in ("zh", "en")}
        history.append({
            "id": entry["id"], "saved_at": entry["saved_at"],
            "learned_at": learned_at if isinstance(learned_at, str) else None,
            "based_on": _count(summary.get("based_on", summary.get("organic_scripts_analyzed"))),
            "essence_zh": essence["zh"] if isinstance(essence["zh"], str) else "",
            "essence_en": essence["en"] if isinstance(essence["en"], str) else "",
        })
    return history


def status(store: Store) -> Dict[str, Any]:
    """The setup status the page shows.

    ::

        {"creator": {"done", "name", "niches"},
         "material": {"done", "total", "organic", "unreviewed", "clauses", "enough_for_cadence", "timed_sec", "measured_rate"},
         "ai": {"configured", "manual"},
         "voice": {"state": "example" | "pending" | "active", "learned_at", "based_on", "new_since", "has_pending"},
         "first_script": {"done", "count"},
         "lessons": {"accepted"},
         "next": "creator" | "material" | "voice" | "script" | "done"}

    - ``material.organic`` counts non-sponsored items with text that the creator wrote or confirmed;
      material is done at MATERIAL_DONE_AT of them. ``unreviewed`` is how many items (sponsored or not)
      are transcripts still waiting for the creator's confirmation; they are in ``total`` and not in
      ``organic``. ``clauses`` and ``enough_for_cadence`` are the measurement and threshold the pipeline
      uses to decide whether breath length is measured or left to the model, over the same items as
      ``organic``. ``measured_rate`` is the speaking rate from timed material, or None under a minute of
      it; ``timed_sec`` is how much there is. Both include transcripts that are still waiting.
    - ``voice.state`` is ``active`` once the creator has accepted a profile of their own, ``pending``
      when a first proposal is waiting and they still have none, else ``example``. ``has_pending``
      says a proposal is waiting in either case. ``based_on`` is how many non-sponsored items the
      profile was learned from; ``new_since`` how many of the ``organic`` items it has not seen: added
      since, or confirmed since. ``voice.unreadable`` is present, and true, only when the creator has a
      profile file that cannot be read: the example is then in use and the page should say so.
    - ``first_script.count`` counts scripts drafted by AI: the step is about a draft and what it was
      based on, which a script typed by hand does not have.
    - ``lessons.accepted`` is how many of the creator's edits are remembered.
    """
    who = store.load_creator()
    page = creator.for_page(who)
    samples = store.list_samples()
    organic = _organic(samples)
    clauses = corpus.voice_stats(s["text"] for s in organic)["clauses"]
    measured = pipeline.measured_rate(samples)
    settings = store.public_settings()
    voice = _voice(store, organic)
    drafts = store.count_ai_drafts()

    done = {
        "creator": creator.is_set(who),
        "material": len(organic) >= MATERIAL_DONE_AT,
        "voice": voice["state"] == VOICE_ACTIVE,
        "script": drafts > 0,
    }
    return {
        "creator": {"done": done["creator"], "name": page["name"], "niches": page["niches"]},
        "material": {
            "done": done["material"], "total": len(samples), "organic": len(organic),
            "unreviewed": sum(1 for s in samples if s.get("needs_review")), "clauses": clauses,
            "enough_for_cadence": clauses >= pipeline.MIN_CLAUSES_FOR_MEASURED_CADENCE,
            "timed_sec": _timed_seconds(samples),
            "measured_rate": measured["speaking_rate_cpm"] if measured else None,
        },
        "ai": {"configured": settings["configured"], "manual": settings["manual"]},
        "voice": voice,
        "first_script": {"done": done["script"], "count": drafts},
        "lessons": {"accepted": store.count_lessons()},
        "next": next((step for step in STEPS if not done[step]), "done"),
    }
