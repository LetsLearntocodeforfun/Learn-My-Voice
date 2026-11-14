"""Local web server for the studio UI. Standard library only.

    python3 -m studio.server            # opens http://127.0.0.1:8765

Binds to 127.0.0.1 only. Requests with an unexpected Host header are refused
(DNS-rebinding protection) and writes must be JSON (blocks cross-site forms).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import re
import sys
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import corpus, guardrails, llm, pipeline, textstats
from .store import PROVIDERS, Store

WEB_DIR = Path(__file__).with_name("web")
MAX_BODY = 12 * 1024 * 1024  # room for a base64 .docx
DEFAULT_PORT = 8765


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


# ---- findings in her language -------------------------------------------
_MESSAGES = {
    (guardrails.CADENCE_TOO_LONG, "error"): ("这一句有 {n} 个字，一口气念不完，建议断开", "This clause is {n} units; too long for one breath"),
    (guardrails.GENERIC_XHS_SLOP, "error"): ("「{m}」是营销号常用说法，不像你的口吻", "\"{m}\" is generic influencer wording, not her voice"),
    (guardrails.GENERIC_XHS_SLOP, "review"): ("「{m}」你平时很少这么说，确认一下", "\"{m}\" is not something she usually says; please check"),
    (guardrails.BOOKISH_MANDARIN, "error"): ("「{m}」太书面了，口语里不这么说", "\"{m}\" is written-style Chinese, not spoken"),
    (guardrails.TRANSLATIONESE, "error"): ("「{m}」有点翻译腔", "\"{m}\" reads like translated English"),
    (guardrails.TRANSLATIONESE, "review"): ("「{m}」偏书面，确认一下", "\"{m}\" may be too formal; please check"),
    (guardrails.UNNATURAL_CODESWITCH, "review"): ("英文词「{m}」你平时不常夹在中文里，确认一下", "English word \"{m}\" is not in her usual code-switching; please check"),
    (guardrails.COMPLIANCE_RISK, "error"): ("「{m}」可能违规（极限词或功效词），建议换个说法", "\"{m}\" may break ad rules (absolute or efficacy claim)"),
    (guardrails.COMPLIANCE_RISK, "review"): ("「{m}」可能被当成极限词，确认一下", "\"{m}\" may be read as an absolute claim; please check"),
}


def describe(finding: guardrails.Finding, stats: Dict[str, Any]) -> Dict[str, Any]:
    if finding.category == guardrails.DURATION_OFF_BUDGET:
        over = stats["budget"] is not None and stats["units"] > stats["budget"]
        zh = "全篇 %s 字，这个时长大约念 %s 字，%s" % (stats["units"], stats["budget"], "偏长了" if over else "偏短了")
        en = "%s units against a budget of about %s: %s" % (stats["units"], stats["budget"], "too long" if over else "too short")
    else:
        zh, en = _MESSAGES.get((finding.category, finding.severity), ("「{m}」需要确认", "\"{m}\" needs a check"))
        n = textstats.spoken_units(finding.matched)
        zh, en = zh.format(m=finding.matched, n=n), en.format(m=finding.matched, n=n)
    return {"line_no": finding.line_no, "category": finding.category, "severity": finding.severity,
            "matched": finding.matched, "message_zh": zh, "message_en": en}


def lint_payload(store: Store, lines: Any, target_sec: Optional[float]) -> Dict[str, Any]:
    dna, is_example = store.load_dna()
    script = {"script_lines": lines if isinstance(lines, list) else []}
    stats = guardrails.script_stats(script, dna, target_sec)
    findings = guardrails.lint_script(script, dna, target_duration_sec=target_sec)
    return {"stats": stats, "findings": [describe(f, stats) for f in findings], "dna_is_example": is_example}


# ---- exports -------------------------------------------------------------
def export_text(script: Dict[str, Any], kind: str) -> Tuple[str, str]:
    """Return ``(filename, text)`` for a plain-text export."""
    lines = script["lines"]
    title = script["title"]
    if kind == "voiceover":  # one breath per line: teleprompter / subtitle text
        out: List[str] = []
        for line in lines:
            if str(line.get("audio_mode", "")).upper() == "LIVE_CLIP":
                continue
            out.extend(textstats.split_clauses(line.get("spoken_script_zh", ""), punctuation_breaks=False))
        return title + "-口播文稿.txt", "\n".join(out) + "\n"
    if kind == "shots":
        rows = ["%d. %s  %s" % (l["line_no"], l.get("time_range_sec", ""), l.get("visual_broll_en_zh", ""))
                for l in lines if l.get("visual_broll_en_zh", "").strip()]
        return title + "-拍摄清单.txt", "\n".join(rows) + "\n"
    if kind == "flower":
        rows = ["%d. %s  %s" % (l["line_no"], l.get("time_range_sec", ""), l.get("onscreen_flower_text_zh", ""))
                for l in lines if l.get("onscreen_flower_text_zh", "").strip()]
        return title + "-花字清单.txt", "\n".join(rows) + "\n"
    raise ApiError(404, "unknown_export")


# ---- http ----------------------------------------------------------------
class App:
    def __init__(self, store: Store, transport: Optional[llm.Transport] = None) -> None:
        self.store = store
        self.transport = transport


class Handler(BaseHTTPRequestHandler):
    server_version = "Studio"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, *args: Any) -> None:  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> Dict[str, Any]:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError(415, "json_required")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "too_large")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            raise ApiError(400, "bad_json")
        if not isinstance(body, dict):
            raise ApiError(400, "bad_json")
        return body

    def _require_ai(self) -> None:
        if not self.app.store.public_settings()["configured"]:
            raise ApiError(400, "ai_not_configured")

    def _dispatch(self, method: str) -> None:
        try:
            port = self.server.server_address[1]
            if (self.headers.get("Host") or "") not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
                raise ApiError(403, "bad_host")
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/"):
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                raise ApiError(405, "method_not_allowed")
        except ApiError as exc:
            self._json({"error": exc.code, "message": exc.message}, exc.status)
        except llm.LLMError as exc:
            self._json({"error": "ai_failed", "message": str(exc)}, 502)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 - last-resort handler; details go to the terminal only
            traceback.print_exc()
            self._json({"error": "internal", "message": "internal error"}, 500)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "not_found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _api(self, method: str, path: str) -> None:
        store = self.app.store
        route = (method, path)

        if route == ("GET", "/api/state"):
            _, is_example = store.load_dna()
            return self._json({"settings": store.public_settings(), "dna_is_example": is_example,
                               "video_formats": list(pipeline.VIDEO_FORMATS),
                               "sample_count": len(store.list_samples())})
        if route == ("GET", "/api/dna"):
            dna, is_example = store.load_dna()
            return self._json({"dna": dna, "is_example": is_example})
        if route == ("GET", "/api/scripts"):
            return self._json({"scripts": store.list_scripts()})
        if route == ("POST", "/api/scripts"):
            body = self._read_json()
            return self._json(store.create_script(
                title=str(body.get("title") or ""), notes=str(body.get("notes") or ""),
                target_sec=_target_sec(body), video_format=_video_format(body), lines=body.get("lines")), 201)
        if route == ("POST", "/api/lint"):
            body = self._read_json()
            return self._json(lint_payload(store, body.get("lines"), _target_sec(body)))
        if route == ("POST", "/api/generate"):
            body = self._read_json()
            notes = str(body.get("notes") or "").strip()
            if not notes:
                raise ApiError(400, "notes_required")
            if not store.public_settings()["configured"]:
                raise ApiError(400, "ai_not_configured")
            return self._json(pipeline.generate_script(
                store, notes=notes, target_sec=_target_sec(body), video_format=_video_format(body),
                transport=self.app.transport), 201)
        if route == ("POST", "/api/settings/models"):
            body = self._read_json()
            provider = _provider(body)
            config = store.load_config()
            if provider == "local":
                base_url = llm.normalize_local_base(str(body.get("base_url") or config.get("local_base_url") or ""))
                models = llm.list_models("local", transport=self.app.transport, base_url=base_url)
                config["local_base_url"] = base_url  # the server answered, so remember it
            else:
                key = str(body.get("key") or "").strip() or config["keys"].get(provider) or ""
                if not key:
                    raise ApiError(400, "key_required")
                models = llm.list_models(provider, key, transport=self.app.transport)
                config["keys"][provider] = key  # the key just worked, so keep it
            store.save_config(config)
            return self._json({"models": models, "settings": store.public_settings()})
        if route == ("POST", "/api/settings"):
            body = self._read_json()
            provider = _provider(body)
            config = store.load_config()
            key = str(body.get("key") or "").strip()
            if provider == "local":
                if body.get("base_url"):
                    config["local_base_url"] = llm.normalize_local_base(str(body["base_url"]))
            elif key:
                config["keys"][provider] = key
            config["provider"], config["model"] = provider, str(body.get("model") or "").strip()
            store.save_config(config)
            return self._json({"settings": store.public_settings()})

        if route == ("GET", "/api/samples"):
            samples = store.list_samples()
            stats = corpus.voice_stats(s["text"] for s in samples if not s["is_sponsored"])
            return self._json({"samples": samples, "stats": stats})
        if route == ("POST", "/api/samples"):
            body = self._read_json()
            text = str(body.get("text") or "")
            if body.get("docx_base64"):
                try:
                    text = corpus.extract_docx_text(base64.b64decode(str(body["docx_base64"]), validate=True))
                except (binascii.Error, ValueError, corpus.ImportError_):
                    raise ApiError(400, "bad_file")
            if not text.strip():
                raise ApiError(400, "empty_text")
            return self._json(store.create_sample(
                title=str(body.get("title") or ""), text=text,
                is_sponsored=bool(body.get("is_sponsored")), metrics=body), 201)
        if route == ("POST", "/api/voice/learn"):
            self._read_json()
            self._require_ai()
            if not any(not s["is_sponsored"] and s["text"].strip() for s in store.list_samples()):
                raise ApiError(400, "no_samples")
            return self._json({"dna": pipeline.learn_voice(store, self.app.transport), "is_example": False})
        if route == ("PUT", "/api/voice/rate"):
            cpm = self._read_json().get("cpm")
            if not isinstance(cpm, (int, float)) or isinstance(cpm, bool) or not 100 <= cpm <= 500:
                raise ApiError(400, "bad_rate")
            try:
                return self._json({"dna": pipeline.set_speaking_rate(store, int(cpm)), "is_example": False})
            except ValueError:
                raise ApiError(400, "no_profile")
        if route == ("GET", "/api/recommendations"):
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": store.load_recommendations()})
        if route == ("POST", "/api/recommendations"):
            self._read_json()
            self._require_ai()
            if not any(s["text"].strip() for s in store.list_samples()):
                raise ApiError(400, "no_samples")
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": pipeline.recommend_topics(store, transport=self.app.transport)})

        m = re.fullmatch(r"/api/samples/([0-9a-f]{12})", path)
        if m:
            if store.get_sample(m.group(1)) is None:
                raise ApiError(404, "not_found")
            if method == "PUT":
                return self._json(store.update_sample(m.group(1), self._read_json()))
            if method == "DELETE":
                store.delete_sample(m.group(1))
                return self._json({"ok": True})

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})(?:/export/(\w+))?", path)
        if m:
            script_id, export_kind = m.group(1), m.group(2)
            script = store.get_script(script_id)
            if script is None:
                raise ApiError(404, "not_found")
            if export_kind and method == "GET":
                filename, text = export_text(script, export_kind)
                disposition = "attachment; filename*=UTF-8''" + urllib.parse.quote(filename)
                return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8", {"Content-Disposition": disposition})
            if not export_kind and method == "GET":
                return self._json(script)
            if not export_kind and method == "PUT":
                return self._json(store.update_script(script_id, self._read_json()))
            if not export_kind and method == "DELETE":
                store.delete_script(script_id)
                return self._json({"ok": True})
        raise ApiError(404, "not_found")


def _target_sec(body: Dict[str, Any]) -> int:
    value = body.get("target_sec", 60)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ApiError(400, "bad_target_sec")
    return max(5, min(int(value), 900))


def _video_format(body: Dict[str, Any]) -> str:
    value = body.get("video_format") or pipeline.VIDEO_FORMATS[0]
    if value not in pipeline.VIDEO_FORMATS:
        raise ApiError(400, "bad_video_format")
    return value


def _provider(body: Dict[str, Any]) -> str:
    value = body.get("provider")
    if value not in PROVIDERS:
        raise ApiError(400, "bad_provider")
    return value


def make_server(port: int, store: Store, transport: Optional[llm.Transport] = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = App(store, transport)  # type: ignore[attr-defined]
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RedNote script studio (local web app)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    store = Store()
    server = None
    for port in range(args.port, args.port + 10):
        try:
            server = make_server(port, store)
            break
        except OSError:
            continue
    if server is None:
        print("无法启动：端口 %d–%d 都被占用。" % (args.port, args.port + 9), file=sys.stderr)
        return 1
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("工作台已启动 / Studio running: %s\n关闭这个窗口即可退出。/ Close this window to quit." % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
