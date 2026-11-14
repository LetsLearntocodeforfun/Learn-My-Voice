"""Local persistence: scripts and imported material in SQLite, settings and Voice DNA as JSON files.

Everything lives under ``data/`` (override with STUDIO_DATA_DIR) and never
leaves the machine. API keys are stored in ``data/config.json`` with 0600
permissions and are never sent back to the browser.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DNA_PATH = ROOT / "schemas" / "voice_dna.example.json"
CLOUD_PROVIDERS = ("gemini", "openai")
PROVIDERS = CLOUD_PROVIDERS + ("local",)
MANUAL_PROVIDER = "manual"  # copy-and-paste with a chat subscription: no key, no model
METRIC_FIELDS = ("views", "likes", "collects", "comments")

LINE_FIELDS = (
    "beat_type", "time_range_sec", "audio_mode", "spoken_script_zh", "delivery_cue",
    "english_partner_translation", "visual_broll_en_zh", "onscreen_flower_text_zh",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'NORMAL_ORGANIC',
    video_format TEXT NOT NULL DEFAULT 'VLOG_VOICEOVER',
    target_sec INTEGER NOT NULL DEFAULT 60,
    notes TEXT NOT NULL DEFAULT '',
    lines_json TEXT NOT NULL DEFAULT '[]',
    post_json TEXT NOT NULL DEFAULT '{}',
    ai_lines_json TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def normalize_lines(lines: Any) -> List[Dict[str, Any]]:
    """Coerce arbitrary input into the Stage 4 line shape, renumbered from 1."""
    out: List[Dict[str, Any]] = []
    for raw in lines if isinstance(lines, list) else []:
        if not isinstance(raw, dict):
            continue
        line: Dict[str, Any] = {"line_no": len(out) + 1}
        for field in LINE_FIELDS:
            value = raw.get(field, "")
            line[field] = value if isinstance(value, str) else ("" if value is None else str(value))
        line["audio_mode"] = line["audio_mode"] or "VOICEOVER"
        out.append(line)
    return out


def _metric(value: Any) -> Optional[int]:
    """A non-negative count, or None for blank / invalid input."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class Store:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir or os.environ.get("STUDIO_DATA_DIR") or ROOT / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "studio.db"
        self.config_path = self.data_dir / "config.json"
        self.dna_path = self.data_dir / "voice_dna.json"
        self.dna_history_dir = self.data_dir / "voice_dna_history"
        self.recommendations_path = self.data_dir / "recommendations.json"
        with self._db() as db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _write_json(self, path: Path, payload: Any, mode: int = 0o644) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(path))

    # ---- settings -------------------------------------------------------
    def load_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {"provider": "", "model": "", "keys": {}, "local_base_url": ""}
        if self.config_path.exists():
            try:
                loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config.update(loaded)
            except ValueError:
                pass
        if not isinstance(config.get("keys"), dict):
            config["keys"] = {}
        return config

    def save_config(self, config: Dict[str, Any]) -> None:
        self._write_json(self.config_path, config, mode=0o600)

    def public_settings(self) -> Dict[str, Any]:
        """Settings safe to send to the browser: no key material."""
        config = self.load_config()
        local_url = config.get("local_base_url") or ""
        keys = {p: bool(config["keys"].get(p)) for p in CLOUD_PROVIDERS}
        keys["local"] = bool(local_url)
        provider, model = config.get("provider") or "", config.get("model") or ""
        return {
            "provider": provider,
            "model": model,
            "keys": keys,
            "local_base_url": local_url,
            "manual": provider == MANUAL_PROVIDER,
            "configured": provider == MANUAL_PROVIDER or bool(provider in PROVIDERS and model and keys.get(provider)),
        }

    # ---- voice dna ------------------------------------------------------
    def load_dna(self) -> Tuple[Dict[str, Any], bool]:
        """Return ``(dna, is_example)``; falls back to the bundled example profile."""
        if self.dna_path.exists():
            return json.loads(self.dna_path.read_text(encoding="utf-8")), False
        return json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8")), True

    def save_dna(self, dna: Dict[str, Any]) -> None:
        """Replace her profile, keeping the previous one in voice_dna_history/."""
        if self.dna_path.exists():
            self.dna_history_dir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = self.dna_history_dir / ("voice_dna-%s.json" % stamp)
            n = 1
            while target.exists():
                n += 1
                target = self.dna_history_dir / ("voice_dna-%s-%d.json" % (stamp, n))
            os.replace(str(self.dna_path), str(target))
        self._write_json(self.dna_path, dna)

    # ---- recommendations ------------------------------------------------
    def load_recommendations(self) -> Optional[Dict[str, Any]]:
        if not self.recommendations_path.exists():
            return None
        try:
            return json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save_recommendations(self, payload: Dict[str, Any]) -> None:
        self._write_json(self.recommendations_path, payload)

    # ---- imported material ---------------------------------------------
    @staticmethod
    def _sample_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "text": row["text"],
            "is_sponsored": bool(row["is_sponsored"]),
            "views": row["views"], "likes": row["likes"], "collects": row["collects"], "comments": row["comments"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_samples(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM samples ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._sample_row(r) for r in rows]

    def get_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._sample_row(row) if row else None

    def create_sample(self, *, title: str, text: str, is_sponsored: bool = False,
                      metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = text.replace("\r\n", "\n").strip()
        first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
        title = (title or "").strip() or first_line[:20] or "未命名"
        metrics = metrics or {}
        now = time.time()
        sample_id = uuid.uuid4().hex[:12]
        with self._db() as db:
            db.execute(
                "INSERT INTO samples (id, title, text, is_sponsored, views, likes, collects, comments, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sample_id, title, text, int(bool(is_sponsored)),
                 _metric(metrics.get("views")), _metric(metrics.get("likes")),
                 _metric(metrics.get("collects")), _metric(metrics.get("comments")), now, now),
            )
        return self.get_sample(sample_id)  # type: ignore[return-value]

    def update_sample(self, sample_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sets: List[str] = []
        values: List[Any] = []
        if isinstance(changes.get("title"), str):
            sets.append("title = ?"); values.append(changes["title"].strip() or "未命名")
        if isinstance(changes.get("text"), str):
            sets.append("text = ?"); values.append(changes["text"].replace("\r\n", "\n").strip())
        if "is_sponsored" in changes:
            sets.append("is_sponsored = ?"); values.append(int(bool(changes["is_sponsored"])))
        for field in METRIC_FIELDS:
            if field in changes:
                sets.append("%s = ?" % field); values.append(_metric(changes[field]))
        if sets:
            sets.append("updated_at = ?"); values.append(time.time())
            values.append(sample_id)
            with self._db() as db:
                db.execute("UPDATE samples SET %s WHERE id = ?" % ", ".join(sets), values)
        return self.get_sample(sample_id)

    def delete_sample(self, sample_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM samples WHERE id = ?", (sample_id,)).rowcount > 0

    # ---- scripts --------------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "mode": row["mode"],
            "video_format": row["video_format"], "target_sec": row["target_sec"],
            "notes": row["notes"], "lines": json.loads(row["lines_json"]),
            "post": json.loads(row["post_json"]), "status": row["status"],
            "has_ai_draft": row["ai_lines_json"] is not None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_scripts(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM scripts ORDER BY updated_at DESC").fetchall()
        out = []
        for row in rows:
            lines = json.loads(row["lines_json"])
            preview = next((l.get("spoken_script_zh", "") for l in lines if l.get("spoken_script_zh")), "")
            out.append({
                "id": row["id"], "title": row["title"], "target_sec": row["target_sec"],
                "status": row["status"], "updated_at": row["updated_at"],
                "line_count": len(lines), "preview": preview[:60],
            })
        return out

    def get_script(self, script_id: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        return self._row(row) if row else None

    def create_script(self, *, title: str = "", notes: str = "", target_sec: int = 60,
                      video_format: str = "VLOG_VOICEOVER", lines: Any = None,
                      post: Optional[Dict[str, Any]] = None, from_ai: bool = False,
                      default_title: str = "未命名脚本") -> Dict[str, Any]:
        normalized = normalize_lines(lines) or normalize_lines([{}])
        now = time.time()
        script_id = uuid.uuid4().hex[:12]
        first_note = next((l.strip() for l in notes.splitlines() if l.strip()), "")
        title = title.strip() or first_note[:16] or default_title
        lines_json = json.dumps(normalized, ensure_ascii=False)
        with self._db() as db:
            db.execute(
                "INSERT INTO scripts (id, title, video_format, target_sec, notes, lines_json, post_json,"
                " ai_lines_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (script_id, title, video_format, int(target_sec), notes, lines_json,
                 json.dumps(post or {}, ensure_ascii=False), lines_json if from_ai else None, now, now),
            )
        return self.get_script(script_id)  # type: ignore[return-value]

    def update_script(self, script_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sets: List[str] = []
        values: List[Any] = []
        if isinstance(changes.get("title"), str):
            sets.append("title = ?"); values.append(changes["title"].strip() or "未命名脚本")
        if isinstance(changes.get("video_format"), str):
            sets.append("video_format = ?"); values.append(changes["video_format"])
        if isinstance(changes.get("status"), str):
            sets.append("status = ?"); values.append(changes["status"])
        if isinstance(changes.get("target_sec"), (int, float)):
            sets.append("target_sec = ?"); values.append(max(5, min(int(changes["target_sec"]), 900)))
        if "lines" in changes:
            sets.append("lines_json = ?"); values.append(json.dumps(normalize_lines(changes["lines"]), ensure_ascii=False))
        if isinstance(changes.get("post"), dict):
            sets.append("post_json = ?"); values.append(json.dumps(changes["post"], ensure_ascii=False))
        if sets:
            sets.append("updated_at = ?"); values.append(time.time())
            values.append(script_id)
            with self._db() as db:
                db.execute("UPDATE scripts SET %s WHERE id = ?" % ", ".join(sets), values)
        return self.get_script(script_id)

    def delete_script(self, script_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM scripts WHERE id = ?", (script_id,)).rowcount > 0
