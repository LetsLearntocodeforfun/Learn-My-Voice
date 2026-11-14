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
from typing import Any, Dict, List, Optional

from . import checks, corpus, exports, llm, pipeline, trends
from .store import MANUAL_PROVIDER, PROVIDERS, ROOT, Store

WEB_DIR = Path(__file__).with_name("web")
MAX_BODY = 12 * 1024 * 1024  # room for a base64 .docx
DEFAULT_PORT = 8765
UNTITLED = {"zh": "未命名脚本", "en": "Untitled script"}
MANUAL_TASKS = ("draft", "voice", "ideas", "trends")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


class App:
    def __init__(self, store: Store, transport: Optional[llm.Transport] = None) -> None:
        self.store = store
        self.transport = transport


class Handler(BaseHTTPRequestHandler):
    server_version = "Studio"
    query: Dict[str, List[str]] = {}

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

    def _lang(self, body: Optional[Dict[str, Any]] = None) -> str:
        value = (body or {}).get("lang") or (self.query.get("lang") or [""])[0]
        return "en" if value == "en" else "zh"

    def _require_ai(self) -> None:
        settings = self.app.store.public_settings()
        if settings["manual"]:
            raise ApiError(400, "manual_mode")
        if not settings["configured"]:
            raise ApiError(400, "ai_not_configured")

    def _dispatch(self, method: str) -> None:
        try:
            port = self.server.server_address[1]
            if (self.headers.get("Host") or "") not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
                raise ApiError(403, "bad_host")
            split = urllib.parse.urlsplit(self.path)
            self.query = urllib.parse.parse_qs(split.query)
            if split.path.startswith("/api/"):
                self._api(method, split.path)
            elif method == "GET":
                self._static(split.path)
            else:
                raise ApiError(405, "method_not_allowed")
        except ApiError as exc:
            self._json({"error": exc.code, "message": exc.message}, exc.status)
        except llm.LLMError as exc:
            self._json({"error": "ai_failed", "reason": exc.code, "message": str(exc)}, 502)
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

    # ---- copy-and-paste mode ------------------------------------------------
    def _manual_prompt(self, body: Dict[str, Any]) -> str:
        store, task = self.app.store, _task(body)
        if task == "draft":
            notes = str(body.get("notes") or "").strip()
            if not notes:
                raise ApiError(400, "notes_required")
            system, user = pipeline.draft_prompt(store, notes=notes, target_sec=_target_sec(body),
                                                 video_format=_video_format(body), keywords=body.get("keywords"))
        elif task == "voice":
            system, user = pipeline.voice_prompt(store)
        elif task == "trends":
            system, user = pipeline.trends_prompt(store, focus=str(body.get("focus") or ""))
        else:
            system, user = pipeline.ideas_prompt(store)
        return pipeline.single_prompt(system, user)

    def _manual_reply(self, body: Dict[str, Any]) -> None:
        store, task = self.app.store, _task(body)
        result = llm.parse_json_text(str(body.get("reply") or ""))
        if task == "draft":
            script = pipeline.apply_draft(
                store, result, notes=str(body.get("notes") or "").strip(), target_sec=_target_sec(body),
                video_format=_video_format(body), default_title=UNTITLED[self._lang(body)],
                keywords=body.get("keywords"))
            return self._json(script, 201)
        if task == "voice":
            return self._json({"dna": pipeline.apply_voice(store, result), "is_example": False})
        if task == "trends":
            counts = pipeline.apply_trends(store, result)
            return self._json(dict(trends.payload(store), **counts))
        ideas = pipeline.apply_ideas(store, result)
        return self._json({"performance": corpus.performance(store.list_samples()), "result": ideas})

    # ---- api ----------------------------------------------------------------
    def _api(self, method: str, path: str) -> None:
        store = self.app.store
        route = (method, path)

        if route == ("GET", "/api/state"):
            _, is_example = store.load_dna()
            return self._json({
                "settings": store.public_settings(), "dna_is_example": is_example,
                "video_formats": list(pipeline.VIDEO_FORMATS), "sample_count": len(store.list_samples()),
                "trend_count": len(store.list_trends()),
                "mcp": {"python": sys.executable or "python3", "script": str(ROOT / "run_mcp.py")},
            })
        if route == ("GET", "/api/dna"):
            dna, is_example = store.load_dna()
            return self._json({"dna": dna, "is_example": is_example})
        if route == ("GET", "/api/scripts"):
            return self._json({"scripts": store.list_scripts()})
        if route == ("POST", "/api/scripts"):
            body = self._read_json()
            return self._json(store.create_script(
                title=str(body.get("title") or ""), notes=str(body.get("notes") or ""),
                target_sec=_target_sec(body), video_format=_video_format(body), lines=body.get("lines"),
                keywords=body.get("keywords"), default_title=UNTITLED[self._lang(body)]), 201)
        if route == ("POST", "/api/lint"):
            body = self._read_json()
            return self._json(checks.lint_payload(store, body.get("lines"), _target_sec(body),
                                                  body.get("keywords"), body.get("post")))
        if route == ("POST", "/api/generate"):
            body = self._read_json()
            notes = str(body.get("notes") or "").strip()
            if not notes:
                raise ApiError(400, "notes_required")
            self._require_ai()
            return self._json(pipeline.generate_script(
                store, notes=notes, target_sec=_target_sec(body), video_format=_video_format(body),
                transport=self.app.transport, default_title=UNTITLED[self._lang(body)],
                keywords=body.get("keywords")), 201)
        if route == ("POST", "/api/manual/prompt"):
            return self._json({"prompt": self._manual_prompt(self._read_json())})
        if route == ("POST", "/api/manual/reply"):
            return self._manual_reply(self._read_json())

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
            config = store.load_config()
            if body.get("provider") == MANUAL_PROVIDER:
                config["provider"] = MANUAL_PROVIDER
            else:
                provider = _provider(body)
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

        if route == ("GET", "/api/trends"):
            return self._json(trends.payload(store))
        if route == ("POST", "/api/trends"):
            items, skipped = trends.parse_keywords(str(self._read_json().get("text") or ""))
            if not items:
                raise ApiError(400, "no_keywords")
            added = store.upsert_trends(items, source="manual")
            return self._json(dict(trends.payload(store), added=added, skipped=skipped), 201)
        if route == ("PUT", "/api/trends/weight"):
            weight = self._read_json().get("weight")
            if weight not in trends.WEIGHTS:
                raise ApiError(400, "bad_weight")
            config = store.load_config()
            config["trend_weight"] = weight
            store.save_config(config)
            return self._json(trends.payload(store))

        m = re.fullmatch(r"/api/trends/([0-9a-f]{12})", path)
        if m:
            if store.get_trend(m.group(1)) is None:
                raise ApiError(404, "not_found")
            if method == "PUT":
                store.update_trend(m.group(1), self._read_json().get("heat"))
                return self._json(trends.payload(store))
            if method == "DELETE":
                store.delete_trend(m.group(1))
                return self._json(trends.payload(store))

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
                dna, _ = store.load_dna()
                try:
                    filename, data, content_type = exports.export_file(script, export_kind, dna, self._lang())
                except KeyError:
                    raise ApiError(404, "unknown_export")
                disposition = "attachment; filename*=UTF-8''" + urllib.parse.quote(filename)
                return self._send(200, data, content_type, {"Content-Disposition": disposition})
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


def _task(body: Dict[str, Any]) -> str:
    value = body.get("task")
    if value not in MANUAL_TASKS:
        raise ApiError(400, "bad_task")
    return value


def make_server(port: int, store: Store, transport: Optional[llm.Transport] = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = App(store, transport)  # type: ignore[attr-defined]
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Learn My Voice script studio (local web app)")
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
        print("无法启动：端口 %d–%d 都被占用。/ Could not start: ports %d-%d are all in use."
              % (args.port, args.port + 9, args.port, args.port + 9), file=sys.stderr)
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
