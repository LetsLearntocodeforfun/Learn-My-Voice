"""Her own material: imported scripts, measured writing habits, and video performance.

Everything here is deterministic. Model calls that use these results live in
``pipeline``.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from . import textstats

FINAL_PARTICLES = "啦嘛哈喔呐诶呀呗啊呢吧哦哇"
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
METRIC_FIELDS = ("views", "likes", "collects", "comments")


class ImportError_(Exception):
    """File could not be read as a script."""


def extract_docx_text(data: bytes) -> str:
    """Plain text of a .docx, one paragraph per line (stdlib only, no XML parser)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ImportError_("document too large")
            xml = archive.read(info).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise ImportError_("not a .docx file")
    paragraphs: List[str] = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        para = re.sub(r"<w:(?:br|cr)\b[^>]*/>", "\n", para)
        para = re.sub(r"<w:tab\b[^>]*/>", "\t", para)
        runs = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", para, re.S)
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


def interactions(sample: Dict[str, Any]) -> Optional[int]:
    """Likes + collects + comments, or None when none of them were entered."""
    values = [sample.get(k) for k in ("likes", "collects", "comments")]
    if all(v is None for v in values):
        return None
    return sum(v or 0 for v in values)


def performance(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rank her videos by interactions; rate is interactions / views when views are known."""
    rows = []
    for s in samples:
        total = interactions(s)
        if total is None and s.get("views") is None:
            continue
        views = s.get("views")
        rows.append({
            "id": s["id"], "title": s["title"], "is_sponsored": bool(s.get("is_sponsored")),
            "views": views, "likes": s.get("likes"), "collects": s.get("collects"), "comments": s.get("comments"),
            "interactions": total,
            "interaction_rate_pct": round(100 * total / views, 1) if (total is not None and views) else None,
        })
    rows.sort(key=lambda r: (r["interactions"] is not None, r["interactions"] or 0, r["views"] or 0), reverse=True)
    totals = sorted(r["interactions"] for r in rows if r["interactions"] is not None)
    return {
        "rows": rows,
        "with_metrics": len(rows),
        "total_videos": len(samples),
        "median_interactions": _percentile(totals, 50) if totals else None,
    }


def exemplars(samples: List[Dict[str, Any]], budget_chars: int = 6000, per_script_chars: int = 1500) -> List[Dict[str, str]]:
    """Her own non-sponsored scripts to anchor a draft: best-performing first, then newest."""
    organic = [s for s in samples if not s.get("is_sponsored") and (s.get("text") or "").strip()]
    organic.sort(key=lambda s: (interactions(s) or 0, s.get("created_at") or 0), reverse=True)
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
