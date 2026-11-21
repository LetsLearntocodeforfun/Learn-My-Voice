"""Her own material: imported scripts, measured writing habits, and video performance.

Everything here is deterministic. Model calls that use these results live in
``pipeline``.
"""
from __future__ import annotations

import datetime
import html
import io
import re
import struct
import zipfile
import zlib
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import textstats

FINAL_PARTICLES = "啦嘛哈喔呐诶呀呗啊呢吧哦哇"
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
METRIC_FIELDS = ("views", "likes", "collects", "comments")
ORIGIN_SCRIPT = "script"     # ``origin`` of a library item made from a script of this app that was posted (store.ORIGIN_SCRIPT)
_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


class ImportError_(Exception):
    """File could not be read as a script."""


_PARAGRAPH_START = re.compile(r"<w:p[ >]")
_RUN_TEXT_START = re.compile(r"<w:t(?:\s[^<>]*)?>")
_LINE_BREAK = re.compile(r"<w:(?:br|cr)\b[^<>]*/>")
_TAB = re.compile(r"<w:tab\b[^<>]*/>")


def _read_document_xml(data: bytes) -> str:
    """The text of ``word/document.xml`` inside a .docx, or ``ImportError_``.

    A .docx is a zip archive, and an archive says of itself how large its contents are. That claim is not
    trusted: at most MAX_DOCX_XML_BYTES are ever expanded, so a file of a few hundred bytes cannot fill the
    memory. Only the two ways Word itself packs a document are read (stored and deflated); the other
    methods a zip may name expand without any ceiling. Whatever else is wrong with the archive (an
    unknown method, a password, a damaged stream) makes it a file that cannot be read, not a server error.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ImportError_("document too large")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ImportError_("not packed the way a .docx is")
            with archive.open(info) as entry:
                raw = entry.read(MAX_DOCX_XML_BYTES + 1)      # never more than this, whatever the archive claims
    except (zipfile.BadZipFile, KeyError, NotImplementedError, RuntimeError, zlib.error, EOFError, OSError,
            ValueError, struct.error, OverflowError):
        raise ImportError_("not a .docx file")
    if len(raw) > MAX_DOCX_XML_BYTES:
        raise ImportError_("document too large")
    return raw.decode("utf-8", "replace")


def extract_docx_text(data: bytes) -> str:
    """Plain text of a .docx, one paragraph per line (stdlib only, no XML parser).

    Raises ``ImportError_`` for anything that cannot be read as a .docx (see ``_read_document_xml``).
    """
    xml = _read_document_xml(data)
    # Paragraphs and the text runs in them are found by walking forward once: the next opening tag, then the
    # next closing tag after it. Patterns of the form "<w:p ... </w:p>" did this before. On a document of
    # opening tags that are never closed they searched to the end of the file again from every opening tag:
    # a .docx of 437 bytes held the server for 10 seconds, and one of 31 KB would have held it for a day.
    paragraphs: List[str] = []
    position = 0
    while True:
        start = _PARAGRAPH_START.search(xml, position)
        end = xml.find("</w:p>", start.end()) if start else -1
        if end < 0:
            break                               # no closing tag is left, so no later paragraph can be complete either
        para, position = xml[start.start():end + len("</w:p>")], end + len("</w:p>")
        para = _LINE_BREAK.sub("\n", para)
        para = _TAB.sub("\t", para)
        runs: List[str] = []
        at = 0
        while True:
            opening = _RUN_TEXT_START.search(para, at)
            close = para.find("</w:t>", opening.end()) if opening else -1
            if close < 0:
                break
            runs.append(para[opening.end():close])
            at = close + len("</w:t>")
        paragraphs.append(html.unescape("".join(runs)))
    return "\n".join(paragraphs).strip()


def _percentile(sorted_values: List[int], pct: float) -> int:
    if not sorted_values:
        return 0
    rank = max(1, int(-(-pct * len(sorted_values) // 100)))  # nearest-rank, ceil
    return sorted_values[min(rank, len(sorted_values)) - 1]


def voice_stats(texts: Iterable[str]) -> Dict[str, Any]:
    """Measured habits across her scripts: clause lengths, final particles, English tokens."""
    lengths: List[int] = []
    particles: Counter = Counter()
    english: Counter = Counter()
    scripts = 0
    for text in texts:
        if not (text or "").strip():
            continue
        scripts += 1
        for clause in textstats.split_clauses(text):
            units = textstats.spoken_units(clause)
            if units == 0:
                continue
            lengths.append(units)
            tail = clause.rstrip("，,。！!？?；;～~… 　")
            if tail and tail[-1] in FINAL_PARTICLES:
                particles[tail[-1]] += 1
        english.update(w.lower() for w in textstats.english_words(textstats.strip_cues(text)))
    lengths.sort()
    total_particles = sum(particles.values())
    return {
        "scripts": scripts,
        "clauses": len(lengths),
        "total_units": sum(lengths),
        "clause_length_units": {
            "p5": _percentile(lengths, 5), "median": _percentile(lengths, 50),
            "p95": _percentile(lengths, 95), "max": lengths[-1] if lengths else 0,
        },
        "clause_final_particles": [
            {"particle": p, "count": c, "share_pct": round(100 * c / total_particles)}
            for p, c in particles.most_common()
        ],
        "particles_never_seen": [p for p in FINAL_PARTICLES if p not in particles],
        "top_english_tokens": [w for w, _ in english.most_common(25)],
    }


def waits_as_posted_script(sample: Dict[str, Any]) -> bool:
    """Whether this item is a posted script whose wording the creator has not yet said is theirs.

    Such an item waits (``needs_review``) like a machine transcript nobody has read, and for a different
    reason: its text began in this app, possibly as an AI's draft. Its numbers count wherever numbers are
    used. Its wording is left out of everything that learns or checks how the creator talks
    (``pipeline.confirmed_samples``) until they confirm it, or until the words of the posted video replace
    it (a subtitle file, a transcript: the item's ``kind`` is then no longer ``script``).
    """
    return bool(sample.get("needs_review")) and sample.get("origin") == ORIGIN_SCRIPT and sample.get("kind", "script") == "script"


def parse_day(value: Any) -> Optional[datetime.date]:
    """A day written as ``YYYY-MM-DD``, or None for anything else: another form, or a day that does not exist."""
    match = _DAY.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def read_after_days(sample: Dict[str, Any]) -> Optional[int]:
    """How many days after a video was posted its numbers were read; None when either day is not known.

    Numbers keep changing for days, so numbers read after 2 days and numbers read after 60 do not compare.
    This is stated next to the numbers and nothing more is done with it: no number is adjusted or projected.
    """
    posted, read = parse_day(sample.get("posted_on")), parse_day(sample.get("metrics_on"))
    return (read - posted).days if (posted is not None and read is not None) else None


def interactions(sample: Dict[str, Any]) -> Optional[int]:
    """Likes + collects + comments, or None when none of them were entered."""
    values = [sample.get(k) for k in ("likes", "collects", "comments")]
    if all(v is None for v in values):
        return None
    return sum(v or 0 for v in values)


def performance(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rank her videos by interactions and views; rates are interactions / views and collects / views when views are known."""
    rows = []
    for s in samples:
        total = interactions(s)
        if total is None and s.get("views") is None:
            continue
        views = s.get("views")
        collects = s.get("collects")
        rows.append({
            "id": s["id"], "title": s["title"], "is_sponsored": bool(s.get("is_sponsored")),
            "views": views, "likes": s.get("likes"), "collects": collects, "comments": s.get("comments"),
            "interactions": total,
            "interaction_rate_pct": round(100 * total / views, 1) if (total is not None and views) else None,
            "save_rate_pct": round(100 * collects / views, 1) if (collects is not None and views) else None,
        })
    rows.sort(key=lambda r: (r["interactions"] is not None, r["interactions"] or 0, r["views"] or 0), reverse=True)
    totals = sorted(r["interactions"] for r in rows if r["interactions"] is not None)
    view_totals = sorted(r["views"] for r in rows if r["views"] is not None)
    return {
        "rows": rows,
        "with_metrics": len(rows),
        "total_videos": len(samples),
        "median_interactions": _percentile(totals, 50) if totals else None,
        "median_views": _percentile(view_totals, 50) if view_totals else None,
    }


def exemplars(samples: List[Dict[str, Any]], budget_chars: int = 6000, per_script_chars: int = 1500,
              on_topic: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, str]]:
    """Her own non-sponsored scripts to anchor a draft.

    Order: scripts on the requested topic first (when ``on_topic`` is given),
    then best-performing, then newest.
    """
    organic = [s for s in samples if not s.get("is_sponsored") and (s.get("text") or "").strip()]
    relevant = on_topic or (lambda sample: False)
    organic.sort(
        key=lambda s: (bool(relevant(s)), interactions(s) or 0, s.get("views") or 0, s.get("created_at") or 0),
        reverse=True,
    )
    out: List[Dict[str, str]] = []
    used = 0
    for s in organic:
        text = s["text"].strip()[:per_script_chars]
        if used + len(text) > budget_chars:
            break
        out.append({"title": s["title"], "her_own_script": text})
        used += len(text)
    return out


def corpus_slice(samples: List[Dict[str, Any]], sponsored: bool, budget_chars: int) -> List[Dict[str, str]]:
    """Newest-first scripts of one kind, truncated to a character budget."""
    chosen = [s for s in samples if bool(s.get("is_sponsored")) == sponsored and (s.get("text") or "").strip()]
    chosen.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
    out: List[Dict[str, str]] = []
    used = 0
    for s in chosen:
        text = s["text"].strip()
        if used + len(text) > budget_chars:
            text = text[: max(budget_chars - used, 0)]
        if not text:
            break
        out.append({"title": s["title"], "script": text})
        used += len(text)
    return out
