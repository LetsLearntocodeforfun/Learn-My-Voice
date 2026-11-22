"""Script revision history, breath-by-breath diffs, safe non-destructive restore, and A/B variant cloning.

Every snapshot lives in the plain ``script_revisions`` SQLite table inside ``studio.db`` so portable folders
and ``.lmvbackup`` archives carry revision history automatically. Restoring an earlier revision first records
a ``before_restore`` snapshot when the live script differs, so nothing written in the editor can be lost.

Standard library only.
"""
from __future__ import annotations

import contextlib
import difflib
import sqlite3
import time
from typing import Any, Dict, List

from . import learning, textstats
from .store import (
    MAX_SCRIPT_TITLE_CHARS,
    MODE_ORGANIC,
    MODE_SPONSORED,
    Store,
    _dumps,
    loads_stored,
)

MAX_REVISIONS_PER_SCRIPT = 50
MAX_LABEL_CHARS = 80
VALID_KINDS = ("ai_draft", "before_critic", "before_posted", "before_restore", "checkpoint")
AUTO_KINDS = ("ai_draft", "before_critic", "before_posted", "before_restore")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class RevisionError(Exception):
    """A revision request could not be fulfilled: missing script, missing revision, or invalid identifier."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_zh = message_zh
        self.status = status


def _not_found(what: str = "script") -> RevisionError:
    if what == "revision":
        return RevisionError(
            "not_found",
            "That script revision is no longer there.",
            "那一条历史版本已经不在了。",
            status=404,
        )
    return RevisionError(
        "not_found",
        "That script is no longer there.",
        "那篇脚本已经不在了。",
        status=404,
    )


def _clean_rev_id(rev_id: Any) -> int:
    if isinstance(rev_id, bool):
        raise _not_found("revision")
    if isinstance(rev_id, int):
        if rev_id <= 0:
            raise _not_found("revision")
        return rev_id
    if isinstance(rev_id, str) and rev_id.strip().isdigit():
        val = int(rev_id.strip())
        if val > 0:
            return val
    raise _not_found("revision")


def _safe_lines(raw_json: Any) -> List[Dict[str, Any]]:
    try:
        parsed = loads_stored(raw_json or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _safe_dict(raw_json: Any) -> Dict[str, Any]:
    try:
        parsed = loads_stored(raw_json or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_keywords(raw_json: Any) -> List[str]:
    try:
        parsed = loads_stored(raw_json or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _spoken_units(lines: List[Dict[str, Any]]) -> int:
    total = 0
    for line in lines:
        if str(line.get("audio_mode") or "") == "LIVE_CLIP":
            continue
        spoken = str(line.get("spoken_script_zh") or "")
        total += textstats.spoken_units(textstats.strip_cues(spoken))
    return total


def _summary_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    lines = _safe_lines(row["lines_json"])
    return {
        "id": int(row["id"]),
        "script_id": str(row["script_id"]),
        "kind": str(row["kind"] or "checkpoint"),
        "label": str(row["label"] or ""),
        "title": str(row["title"] or ""),
        "target_sec": int(row["target_sec"] or 60),
        "video_format": str(row["video_format"] or ""),
        "line_count": len(lines),
        "spoken_units": _spoken_units(lines),
        "created_at": str(row["created_at"] or ""),
    }


def _full_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    out = _summary_from_row(row)
    out["lines"] = _safe_lines(row["lines_json"])
    out["post"] = _safe_dict(row["post_json"])
    out["keywords"] = _safe_keywords(row["keywords_json"])
    return out


def snapshot_script(
    store: Store,
    script_id: Any,
    kind: str = "checkpoint",
    label: str = "",
) -> Dict[str, Any]:
    """Capture the stored state of ``script_id`` in ``script_revisions``.

    Skips creating a duplicate automatic snapshot (or an identical repeat checkpoint with the same label)
    when ``title``, ``lines_json``, and ``post_json`` match the most recent revision for that script.
    Prunes oldest revisions beyond ``MAX_REVISIONS_PER_SCRIPT`` (50), preferring to keep user-named
    ``checkpoint`` entries over automatic ones.
    """
    if not isinstance(script_id, str) or not script_id:
        raise _not_found("script")
    clean_kind = kind if kind in VALID_KINDS else "checkpoint"
    clean_label = " ".join(str(label or "").split())[:MAX_LABEL_CHARS].strip()
    stamped = time.strftime(TIME_FORMAT, time.localtime())

    with store._db() as db:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute(
            "SELECT id, title, lines_json, post_json, keywords_json, target_sec, video_format"
            " FROM scripts WHERE id = ?",
            (script_id,),
        ).fetchone()
        if cur is None:
            raise _not_found("script")

        latest = db.execute(
            "SELECT * FROM script_revisions WHERE script_id = ? ORDER BY id DESC LIMIT 1",
            (script_id,),
        ).fetchone()
        if latest is not None:
            same_content = (
                latest["title"] == cur["title"]
                and latest["lines_json"] == cur["lines_json"]
                and latest["post_json"] == cur["post_json"]
            )
            if same_content and (
                clean_kind != "checkpoint"
                or (latest["kind"] == "checkpoint" and (latest["label"] or "") == clean_label)
            ):
                return _summary_from_row(latest)

        cursor = db.execute(
            "INSERT INTO script_revisions"
            " (script_id, kind, label, title, lines_json, post_json, keywords_json, target_sec, video_format, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                script_id,
                clean_kind,
                clean_label,
                str(cur["title"] or ""),
                str(cur["lines_json"] or "[]"),
                str(cur["post_json"] or "{}"),
                str(cur["keywords_json"] or "[]"),
                int(cur["target_sec"] or 60),
                str(cur["video_format"] or ""),
                stamped,
            ),
        )
        new_id = int(cursor.lastrowid or 0)

        total = int(
            db.execute("SELECT COUNT(*) FROM script_revisions WHERE script_id = ?", (script_id,)).fetchone()[0]
        )
        if total > MAX_REVISIONS_PER_SCRIPT:
            excess = total - MAX_REVISIONS_PER_SCRIPT
            db.execute(
                "DELETE FROM script_revisions WHERE id IN ("
                "  SELECT id FROM script_revisions WHERE script_id = ?"
                "  ORDER BY CASE"
                "    WHEN kind = 'checkpoint' AND label != '' THEN 2"
                "    WHEN kind = 'checkpoint' THEN 1"
                "    ELSE 0"
                "  END ASC, id ASC LIMIT ?"
                ")",
                (script_id, excess),
            )

        saved = db.execute("SELECT * FROM script_revisions WHERE id = ?", (new_id,)).fetchone()
        if saved is None:
            saved = db.execute(
                "SELECT * FROM script_revisions WHERE script_id = ? ORDER BY id DESC LIMIT 1",
                (script_id,),
            ).fetchone()
    return _summary_from_row(saved)


def list_revisions(store: Store, script_id: Any) -> List[Dict[str, Any]]:
    """Return revision summaries for ``script_id``, newest first."""
    if not isinstance(script_id, str) or not script_id:
        return []
    with contextlib.closing(store._db()) as db:
        rows = db.execute(
            "SELECT * FROM script_revisions WHERE script_id = ? ORDER BY id DESC",
            (script_id,),
        ).fetchall()
    return [_summary_from_row(row) for row in rows]


def get_revision(store: Store, script_id: Any, rev_id: Any) -> Dict[str, Any]:
    """Return one full revision record for ``script_id`` or raise ``RevisionError`` (404)."""
    if not isinstance(script_id, str) or not script_id or store.get_script(script_id) is None:
        raise _not_found("script")
    clean_id = _clean_rev_id(rev_id)
    with contextlib.closing(store._db()) as db:
        row = db.execute(
            "SELECT * FROM script_revisions WHERE id = ? AND script_id = ?",
            (clean_id, script_id),
        ).fetchone()
    if row is None:
        raise _not_found("revision")
    return _full_from_row(row)


def _split_breaths(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    return parts if parts else [raw]


def _breath_diff(before_text: str, after_text: str) -> List[Dict[str, str]]:
    before = _split_breaths(before_text)
    after = _split_breaths(after_text)
    before_keys = [learning.spoken_key(b) or b for b in before]
    after_keys = [learning.spoken_key(a) or a for a in after]
    matcher = difflib.SequenceMatcher(None, before_keys, after_keys, autojunk=False)
    out: List[Dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx_b, idx_a in zip(range(i1, i2), range(j1, j2)):
                if before[idx_b] == after[idx_a]:
                    out.append({"op": "unchanged", "text": after[idx_a]})
                else:
                    out.append({"op": "removed", "text": before[idx_b]})
                    out.append({"op": "added", "text": after[idx_a]})
        else:
            for idx_b in range(i1, i2):
                out.append({"op": "removed", "text": before[idx_b]})
            for idx_a in range(j1, j2):
                out.append({"op": "added", "text": after[idx_a]})
    return out


def _line_anchor(line: Dict[str, Any]) -> str:
    spoken = str(line.get("spoken_script_zh") or "").strip()
    key = learning.spoken_key(spoken)
    if key:
        return key
    if spoken:
        return spoken
    return "__meta__:%s|%s|%s" % (
        str(line.get("audio_mode") or ""),
        str(line.get("visual_broll_en_zh") or "").strip(),
        str(line.get("onscreen_flower_text_zh") or "").strip(),
    )


def diff_revision(store: Store, script_id: Any, rev_id: Any) -> Dict[str, Any]:
    """Compute structured line-by-line & breath-by-breath diff between ``rev_id`` and the live script."""
    script = store.get_script(script_id) if isinstance(script_id, str) else None
    if script is None:
        raise _not_found("script")
    rev = get_revision(store, script_id, rev_id)

    rev_lines: List[Dict[str, Any]] = rev["lines"]
    cur_lines: List[Dict[str, Any]] = script["lines"]
    rev_keys = [_line_anchor(line) for line in rev_lines]
    cur_keys = [_line_anchor(line) for line in cur_lines]

    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []

    matcher = difflib.SequenceMatcher(None, rev_keys, cur_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for ri, ci in zip(range(i1, i2), range(j1, j2)):
                r_line, c_line = rev_lines[ri], cur_lines[ci]
                r_spoken = str(r_line.get("spoken_script_zh") or "")
                c_spoken = str(c_line.get("spoken_script_zh") or "")
                if r_spoken.strip() == c_spoken.strip():
                    item = {
                        "status": "unchanged",
                        "rev_line_no": ri + 1,
                        "cur_line_no": ci + 1,
                        "before": r_spoken,
                        "after": c_spoken,
                        "breaths": [{"op": "unchanged", "text": b} for b in _split_breaths(c_spoken)],
                    }
                    unchanged.append(item)
                    entries.append(item)
                else:
                    item = {
                        "status": "changed",
                        "rev_line_no": ri + 1,
                        "cur_line_no": ci + 1,
                        "before": r_spoken,
                        "after": c_spoken,
                        "breaths": _breath_diff(r_spoken, c_spoken),
                    }
                    changed.append(item)
                    entries.append(item)
        elif tag == "replace":
            span_r = i2 - i1
            span_c = j2 - j1
            paired = min(span_r, span_c)
            for offset in range(paired):
                ri, ci = i1 + offset, j1 + offset
                r_spoken = str(rev_lines[ri].get("spoken_script_zh") or "")
                c_spoken = str(cur_lines[ci].get("spoken_script_zh") or "")
                item = {
                    "status": "changed",
                    "rev_line_no": ri + 1,
                    "cur_line_no": ci + 1,
                    "before": r_spoken,
                    "after": c_spoken,
                    "breaths": _breath_diff(r_spoken, c_spoken),
                }
                changed.append(item)
                entries.append(item)
            for ri in range(i1 + paired, i2):
                r_spoken = str(rev_lines[ri].get("spoken_script_zh") or "")
                item = {
                    "status": "removed",
                    "rev_line_no": ri + 1,
                    "cur_line_no": None,
                    "before": r_spoken,
                    "after": "",
                    "breaths": [{"op": "removed", "text": b} for b in _split_breaths(r_spoken)],
                }
                removed.append(item)
                entries.append(item)
            for ci in range(j1 + paired, j2):
                c_spoken = str(cur_lines[ci].get("spoken_script_zh") or "")
                item = {
                    "status": "added",
                    "rev_line_no": None,
                    "cur_line_no": ci + 1,
                    "before": "",
                    "after": c_spoken,
                    "breaths": [{"op": "added", "text": b} for b in _split_breaths(c_spoken)],
                }
                added.append(item)
                entries.append(item)
        elif tag == "delete":
            for ri in range(i1, i2):
                r_spoken = str(rev_lines[ri].get("spoken_script_zh") or "")
                item = {
                    "status": "removed",
                    "rev_line_no": ri + 1,
                    "cur_line_no": None,
                    "before": r_spoken,
                    "after": "",
                    "breaths": [{"op": "removed", "text": b} for b in _split_breaths(r_spoken)],
                }
                removed.append(item)
                entries.append(item)
        elif tag == "insert":
            for ci in range(j1, j2):
                c_spoken = str(cur_lines[ci].get("spoken_script_zh") or "")
                item = {
                    "status": "added",
                    "rev_line_no": None,
                    "cur_line_no": ci + 1,
                    "before": "",
                    "after": c_spoken,
                    "breaths": [{"op": "added", "text": b} for b in _split_breaths(c_spoken)],
                }
                added.append(item)
                entries.append(item)

    title_changed = rev["title"] != script["title"]
    sec_before = int(rev["target_sec"])
    sec_after = int(script["target_sec"])
    post_changed = rev["post"] != script["post"]
    keywords_changed = rev["keywords"] != script.get("keywords", [])
    identical = (
        not title_changed
        and sec_before == sec_after
        and not post_changed
        and not keywords_changed
        and not added
        and not removed
        and not changed
    )
    return {
        "revision_id": rev["id"],
        "script_id": script["id"],
        "identical": identical,
        "title": {
            "before": rev["title"],
            "after": script["title"],
            "changed": title_changed,
        },
        "target_sec": {
            "before": sec_before,
            "after": sec_after,
            "delta": sec_after - sec_before,
            "changed": sec_before != sec_after,
        },
        "post_changed": post_changed,
        "keywords_changed": keywords_changed,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "entries": entries,
    }


def _differs_from_revision(script: Dict[str, Any], rev: Dict[str, Any]) -> bool:
    return (
        script.get("title") != rev.get("title")
        or script.get("lines") != rev.get("lines")
        or script.get("post") != rev.get("post")
        or script.get("keywords") != rev.get("keywords")
        or int(script.get("target_sec") or 60) != int(rev.get("target_sec") or 60)
        or str(script.get("video_format") or "") != str(rev.get("video_format") or "")
    )


def restore_revision(store: Store, script_id: Any, rev_id: Any) -> Dict[str, Any]:
    """Safely restore ``rev_id`` onto ``script_id``.

    First records a ``before_restore`` snapshot of the live script if it differs from ``rev_id``,
    then restores ``title``, ``lines``, ``post``, ``keywords``, ``target_sec``, and ``video_format``,
    advancing ``updated_at``. Returns ``{"script", "revisions"}``.
    """
    script = store.get_script(script_id) if isinstance(script_id, str) else None
    if script is None:
        raise _not_found("script")
    rev = get_revision(store, script_id, rev_id)

    if _differs_from_revision(script, rev):
        snapshot_script(store, script_id, kind="before_restore")

    updated = store.update_script(
        script_id,
        {
            "title": rev["title"],
            "lines": rev["lines"],
            "post": rev["post"],
            "keywords": rev["keywords"],
            "target_sec": rev["target_sec"],
            "video_format": rev["video_format"],
        },
    )
    if updated is None:
        raise _not_found("script")
    return {
        "script": updated,
        "revisions": list_revisions(store, script_id),
    }


def duplicate_script(
    store: Store,
    script_id: Any,
    label: str = "",
    lang: str = "zh",
) -> Dict[str, Any]:
    """Clone ``script_id`` into a new draft for A/B variant testing and record an initial ``checkpoint``."""
    source = store.get_script(script_id) if isinstance(script_id, str) else None
    if source is None:
        raise _not_found("script")

    clean_label = " ".join(str(label or "").split())[:40].strip()
    default_tag = "Variant B" if lang == "en" else "版本 B"
    tag = clean_label or default_tag
    suffix = " (%s)" % tag
    base_title = (source.get("title") or "").strip() or ("Untitled script" if lang == "en" else "未命名脚本")
    max_base = max(1, MAX_SCRIPT_TITLE_CHARS - len(suffix))
    new_title = base_title[:max_base] + suffix

    cloned = store.create_script(
        title=new_title,
        notes=str(source.get("notes") or ""),
        target_sec=int(source.get("target_sec") or 60),
        video_format=str(source.get("video_format") or "VLOG_VOICEOVER"),
        lines=source.get("lines") or [],
        post=source.get("post") if isinstance(source.get("post"), dict) else {},
        from_ai=False,
        keywords=source.get("keywords") or [],
        mode=MODE_SPONSORED if source.get("mode") == MODE_SPONSORED else MODE_ORGANIC,
    )

    ai_lines = store.get_ai_lines(source["id"])
    basis = source.get("basis")
    if ai_lines is not None or isinstance(basis, dict):
        with store._db() as db:
            db.execute(
                "UPDATE scripts SET ai_lines_json = ?, basis_json = ? WHERE id = ?",
                (
                    _dumps(ai_lines) if ai_lines is not None else None,
                    _dumps(basis) if isinstance(basis, dict) else None,
                    cloned["id"],
                ),
            )
        cloned = store.get_script(cloned["id"]) or cloned

    snapshot_script(store, cloned["id"], kind="checkpoint", label=tag)
    return cloned
