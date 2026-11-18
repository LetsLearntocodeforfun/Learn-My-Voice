"""Local web server for the studio UI. Standard library only.

    python3 -m studio.server            # opens http://127.0.0.1:8765

Binds to 127.0.0.1 only. Requests with an unexpected Host header are refused
(DNS-rebinding protection).

Writes from another website are kept out in three ways, checked in this order:

1. A write that names another site as its ``Origin`` is refused. Current
   browsers put that header on every POST, PUT and DELETE a page sends; the
   next two rules do not depend on it.
2. Every POST must be JSON. A page on another site can make a browser send a
   form here without asking, but not ``application/json``: for that the browser
   first asks this server for permission (a CORS preflight), which it never gives.
3. PUT and DELETE cannot be sent across sites at all without that permission,
   whatever they carry.

The one write that is not JSON is the video upload (``PUT /api/uploads``, the
raw file as the body). It is covered by 1 and 3, and must also declare
``application/octet-stream``, which a form cannot send either.

Every answer forbids being shown in a frame and carries a content security
policy (``CONTENT_SECURITY_POLICY``) that allows the pages only what they use.
A request has to state its size in plain digits, a connection that sends
nothing is closed after ``IDLE_TIMEOUT_SEC``, and ``NaN`` and ``Infinity`` are
neither accepted in a request nor sent in an answer.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import checks, corpus, creator, exports, keyword_import, llm, pipeline, textstats, timing, transcribe, trends
from . import buildinfo, hooks, outside, posted, suggest, wordlist
from . import setup as setup_status
from .jobs import JobError, Jobs, Progress
from .store import HISTORY_ID, MANUAL_PROVIDER, PROVIDERS, ROOT, StaleScript, Store, TextTooLong, clean_file_name

WEB_DIR = Path(__file__).with_name("web")
MAX_BODY = 12 * 1024 * 1024  # room for a base64 .docx
MAX_REMEMBERED_FILES = 200   # exported files of this run that can still be shown in the file manager
MAX_WINDOW_REPORT = 200_000  # characters of what the window check may report
# Set to 1 by the script that tries an installer of the desktop app: see ``App.start_window_check``.
WINDOW_CHECK_ENV = "LEARN_MY_VOICE_WINDOW_CHECK"
DEFAULT_PORT = 8765
UNTITLED = {"zh": "未命名脚本", "en": "Untitled script"}
MANUAL_TASKS = ("draft", "voice", "ideas", "trends", "suggest")
IDLE_TIMEOUT_SEC = 30                   # a connection that sends nothing for this long while it is being read is closed

# Sent with every answer. The pages load their scripts and their style sheet from this server, style some
# elements with style="..." attributes, and talk to /api/ on this server. Nothing else is allowed: no script
# written into a page, no script, style, image or font from another address, no request to another address,
# no form, and no other page may show this app inside a frame. Escaping keeps hostile text from becoming markup
# today; this is what makes a miss harmless.
# "style-src" is for browsers that do not know the two finer directives after it; browsers that do use those,
# which allow style attributes and no <style> element.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "style-src-elem 'self'",
    "style-src-attr 'unsafe-inline'",
    "img-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
))

# The type sent with each kind of file the pages are made of. They are named here and not looked up in the
# system's table (``mimetypes``): on Windows that table is read from the registry, where ".js" is sometimes
# "text/plain". Every answer tells the browser not to guess a type (nosniff, in ``_send``), so a script sent
# as plain text is not run and the page stays empty. A kind of file not named here still gets the system's answer.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

MAX_UPLOAD_BYTES = 8 * 1024 ** 3        # one video or recording; streamed to disk, never held in memory
UPLOAD_CHUNK = 1024 * 1024
UPLOAD_IDLE_SEC = 120                   # an upload that sends nothing for this long is abandoned
UPLOAD_TTL_SEC = 3600                   # an upload nobody asked to transcribe is deleted after this
MAX_DRAIN = 1024 * 1024                 # a refused request with a body this small is read to the end, so the browser gets the reason
DRAIN_TIMEOUT_SEC = 5
# What the tools built into macOS can pull sound out of (see transcribe._extract_audio).
MEDIA_EXTENSIONS = ("mp4", "mov", "m4v", "m4a", "mp3", "wav", "aac", "aif", "aiff", "caf", "flac", "3gp")
_UPLOAD_ID = re.compile(r"[0-9a-f]{12}")
_UPLOAD_FILE = re.compile(r"[0-9a-f]{12}\.[a-z0-9]{2,5}(?:\.part)?")     # the only names this server writes there

TRANSCRIBE_JOB, INSTALL_JOB = "transcribe", "install"
DISCOVER_JOB = "discover"               # one request the whole way: a web lookup, then topic ideas (pipeline.discover)
DICTATION_JOB = "dictation"             # a spoken request written down on this computer; nothing is added to the library
MAX_DICTATION_BYTES = 30 * 1024 * 1024  # a spoken request is a minute at most: about 6 MB as the page records it


class ApiError(Exception):
    """A refused request. ``code`` is what the page translates; ``message`` is English, ``message_zh`` Chinese when given.

    ``extra`` holds further fields for the answer, beside ``error`` and the messages.
    """

    def __init__(self, status: int, code: str, message: str = "", message_zh: str = "",
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.message_zh = status, code, message or code, message_zh
        self.extra = dict(extra or {})


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _stated_length(raw: Optional[str], missing: Optional[int] = None) -> Optional[int]:
    """The body size a request states in its ``Content-Length`` header; ``missing`` when it states none.

    Only plain digits are a size. ``-1`` made the reader take everything until the sender closed the
    connection, past the size limit, and ``abc`` ended in "internal error"; both are now refused (400).
    A number with more digits than any real size has is reported as larger than every limit here, without
    being worked out.
    """
    if raw is None:
        return missing
    raw = raw.strip()
    if not (raw.isascii() and raw.isdigit()):
        raise ApiError(400, "bad_content_length", "The request does not state its size as a number",
                       "请求没有用数字说明自己的大小")
    raw = raw.lstrip("0") or "0"
    return int(raw) if len(raw) <= 18 else 10 ** 18


def _finite(value: Any) -> Any:
    """``value`` with every number that is not finite (NaN, Infinity) replaced by None, so that it can be sent as JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


class Uploads:
    """Videos and recordings waiting to be transcribed, kept under ``<data>/uploads``.

    Every file there is written by this server under a name it generates
    (``<12 hex digits>.<extension>``). The name the browser sent is a label
    and never part of a path. A file is deleted:

    - when its transcription ends, whatever the outcome (``_transcribe_upload``, and ``_dictate`` for a spoken request);
    - UPLOAD_TTL_SEC after it arrived, if nobody asked for it to be transcribed.
      A timer does this, so it needs no further request;
    - when the server closes, if it was still waiting (``close``);
    - at start-up, for anything a run that did not close left behind (``clear_leftovers``).
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        # upload_id -> {"path", "name", "size", "created_at", "timer"}
        self._waiting: Dict[str, Dict[str, Any]] = {}

    def clear_leftovers(self) -> int:
        """Delete copies left by an earlier run (the app quit during an upload or a transcription).

        Only files with the names this server generates are touched.
        """
        try:
            entries = list(self.folder.iterdir()) if self.folder.is_dir() else []
        except OSError:
            entries = []
        removed = 0
        for entry in entries:
            if _UPLOAD_FILE.fullmatch(entry.name) and (entry.is_symlink() or entry.is_file()):
                _remove(entry)
                removed += 1
        return removed

    def new_paths(self, extension: str) -> Tuple[str, Path, Path]:
        """``(upload_id, final path, path to write to while the upload is arriving)``, both inside the folder."""
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        upload_id = uuid.uuid4().hex[:12]
        final = self.folder / ("%s.%s" % (upload_id, extension))
        return upload_id, final, final.with_name(final.name + ".part")

    def add(self, upload_id: str, path: Path, name: str, size: int) -> None:
        """Keep an upload that arrived whole until it is claimed, and for UPLOAD_TTL_SEC at most."""
        timer = threading.Timer(UPLOAD_TTL_SEC, self._expire, (upload_id,))
        timer.daemon = True
        with self._lock:
            self._waiting[upload_id] = {"path": path, "name": name, "size": size, "created_at": time.time(),
                                        "timer": timer}
        timer.start()

    def claim(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Hand an upload over to be transcribed. It can be claimed once; whoever claims it must delete the file.

        Its timer is stopped: from here on the file belongs to the transcription, however long that waits its turn.
        """
        with self._lock:
            item = self._waiting.pop(upload_id, None)
        if item is None:
            return None
        item.pop("timer").cancel()
        return item

    def _expire(self, upload_id: str) -> None:
        """Delete an upload nobody asked to transcribe: the creator was told no copy is kept."""
        item = self.claim(upload_id)
        if item is not None:
            _remove(item["path"])

    def drop_stale(self, now: Optional[float] = None) -> None:
        """Delete uploads older than UPLOAD_TTL_SEC by the clock.

        The timers normally get there first. A timer can run late (it may not count the time the
        computer spent asleep), so the clock is checked too whenever an upload or a transcription
        is requested.
        """
        now = time.time() if now is None else now
        with self._lock:
            stale = [key for key, item in self._waiting.items() if now - item["created_at"] > UPLOAD_TTL_SEC]
        for key in stale:
            self._expire(key)

    def close(self) -> None:
        """The server is closing: delete every upload still waiting, and stop their timers."""
        with self._lock:
            waiting = list(self._waiting)
        for key in waiting:
            self._expire(key)


class App:
    """What the request handlers share.

    ``transport`` (the model call) and ``transcriber`` (anything with ``status()``,
    ``install(progress)``, ``transcribe_file(path, progress)`` and ``uninstall()``;
    by default the ``studio.transcribe`` module) can be replaced, so tests need
    neither a model nor Whisper. So can ``outside`` (by default the ``studio.outside``
    module: the Downloads folder, the file manager and the browser), so tests write
    into a folder of their own and open nothing.
    """

    def __init__(self, store: Store, transport: Optional[llm.Transport] = None, transcriber: Any = None,
                 outside_world: Any = None) -> None:
        self.store = store
        self.transport = transport
        self.transcriber = transcriber if transcriber is not None else transcribe
        self.outside = outside_world if outside_world is not None else outside
        # Files this run of the app put into the Downloads folder. Only these can be shown in the file manager
        # on request, so the page cannot make the app point at any other place on the computer.
        self.saved_files: List[str] = []
        # None, or what a trial of the desktop app is told and tells: see ``start_window_check``.
        self.window_check: Optional[Dict[str, Any]] = None
        self.jobs = Jobs()
        self.max_upload_bytes = MAX_UPLOAD_BYTES
        self.install_lock = threading.Lock()
        self.discover_lock = threading.Lock()
        self.uploads = Uploads(store.uploads_dir)
        self.uploads.clear_leftovers()

    def remember_saved(self, path: Path) -> None:
        self.saved_files.append(str(path))
        del self.saved_files[:-MAX_REMEMBERED_FILES]

    def start_window_check(self) -> None:
        """Get ready for a trial of the desktop app's window by a script (LEARN_MY_VOICE_WINDOW_CHECK=1).

        The page then loads ``windowcheck.js``, waits to be told to start, uses itself the way a person would
        (exports a script through the menu, deletes one through the dialog, follows a link to another site) and
        reports what happened; the script that runs the trial reads the report. It is the only way to see what
        the system's web view does with the real page, short of a person looking. While it is on, a link or a
        folder is written down and not opened (``outside.Recorded``). Never on unless the app was started so.
        """
        self.window_check = {"start": False, "report": None, "so_far": None, "opened": [], "shown": []}
        self.outside = outside.Recorded(self.window_check, self.outside)


class Handler(BaseHTTPRequestHandler):
    server_version = "Studio"
    # How long a connection may send nothing while this server is waiting for it: the request line, the headers,
    # a JSON body. Without it a connection that says nothing held a thread for ever. It does not limit how long
    # this server works on a request (a model call can take minutes), and an upload sets its own, longer, wait.
    timeout = IDLE_TIMEOUT_SEC
    query: Dict[str, List[str]] = {}
    _body: Optional[Dict[str, Any]] = None      # the JSON body of the request being handled, once read
    _body_read = False                          # whether this request's body has been taken off the connection

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
        self.send_header("X-Frame-Options", "DENY")                         # no page may show this app inside a frame
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        """Send ``payload`` as JSON a browser can read: ``NaN`` and ``Infinity`` are never sent.

        Nothing stored holds them and nothing read from outside may (``llm.loads_strict``). Should a number
        that is not finite turn up in an answer all the same, it goes out as ``null``: one bare ``NaN``
        makes the whole answer unreadable to the page, which then shows nothing.
        """
        try:
            text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except ValueError:
            text = json.dumps(_finite(payload), ensure_ascii=False, allow_nan=False)
        self._send(status, text.encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> Dict[str, Any]:
        """The request body as a JSON object. Read once per request; later calls return the same object.

        The media type has to be exactly ``application/json``. Looking for those words anywhere in the
        header is not enough: ``text/plain;application/json`` contains them, and that is a value a page on
        another site can make a browser send without asking this server first.

        The stated size has to be plain digits (``_stated_length``), and ``NaN`` and ``Infinity`` in the body
        are refused like any other invalid JSON.
        """
        if self._body is not None:
            return self._body
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "json_required")
        length = _stated_length(self.headers.get("Content-Length"), missing=0)
        if length > MAX_BODY:
            raise ApiError(413, "too_large")
        self._body_read = True
        try:
            raw = self.rfile.read(length)
        except socket.timeout:              # the body stopped arriving: answer, do not wait for the rest
            raise ApiError(408, "request_timeout", "The request stopped arriving before it was complete",
                           "请求没有传完就停住了")
        try:
            body = llm.loads_strict(raw.decode("utf-8") or "{}")
        except (ValueError, RecursionError):
            raise ApiError(400, "bad_json")
        if not isinstance(body, dict):
            raise ApiError(400, "bad_json")
        self._body = body
        return body

    def _drain_unread(self) -> None:
        """Before a refusal is sent: read a small unread body to its end.

        A connection closed with unread data can be reset before the browser has
        read the answer, and the creator would see a network error instead of
        the reason. Large bodies are not read; the answer is sent and the
        connection closed.
        """
        if self._body_read:
            return
        self._body_read = True
        try:
            length = _stated_length(self.headers.get("Content-Length"), missing=0)
        except ApiError:
            # A size that cannot be read: take what has already arrived, without waiting for anything more.
            try:
                self.connection.settimeout(0.2)
                self.rfile.read1(MAX_DRAIN)
            except OSError:
                pass
            return
        if 0 < length <= MAX_DRAIN:
            try:
                self.connection.settimeout(DRAIN_TIMEOUT_SEC)
                self.rfile.read(length)
            except OSError:
                pass

    def _lang(self, body: Optional[Dict[str, Any]] = None) -> str:
        value = (body or {}).get("lang") or (self.query.get("lang") or [""])[0]
        return "en" if value == "en" else "zh"

    def _export(self, script_id: str, kind: str, lang: str) -> Tuple[str, bytes, str]:
        """``(file name, content, media type)`` of one export of one script; refused when either is not there."""
        store = self.app.store
        script = store.get_script(script_id)
        if script is None:
            raise ApiError(404, "not_found")
        dna, is_example = store.load_dna()
        try:
            return exports.export_file(script, kind, dna, lang, dna_is_example=is_example, who=store.load_creator())
        except KeyError:
            raise ApiError(404, "unknown_export")

    def _require_ai(self) -> None:
        settings = self.app.store.public_settings()
        if settings["manual"]:
            raise ApiError(400, "manual_mode")
        if not settings["configured"]:
            raise ApiError(400, "ai_not_configured")

    def _dispatch(self, method: str) -> None:
        self._body, self._body_read = None, False
        try:
            try:
                port = self.server.server_address[1]
                hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)
                if (self.headers.get("Host") or "") not in hosts:
                    raise ApiError(403, "bad_host")
                origin = self.headers.get("Origin")
                if method != "GET" and origin is not None and origin not in tuple("http://" + host for host in hosts):
                    raise ApiError(403, "bad_origin")      # a write sent by a page that is not this app
                split = urllib.parse.urlsplit(self.path)
                self.query = urllib.parse.parse_qs(split.query)
                if split.path.startswith("/api/"):
                    self._api(method, split.path)
                elif method == "GET":
                    self._static(split.path)
                else:
                    raise ApiError(405, "method_not_allowed")
            except ApiError as exc:
                self._drain_unread()
                payload = dict(exc.extra, error=exc.code, message=exc.message)
                if exc.message_zh:
                    payload["message_zh"] = exc.message_zh
                self._json(payload, exc.status)
            except llm.LLMError as exc:
                payload = {"error": "ai_failed", "reason": exc.code, "message": str(exc)}
                if getattr(exc, "message_zh", ""):
                    payload["message_zh"] = exc.message_zh
                self._json(payload, 502)
            except (BrokenPipeError, ConnectionResetError):
                raise
            except Exception:  # noqa: BLE001 - last-resort handler; details go to the terminal only
                traceback.print_exc()
                self._json({"error": "internal", "message": "internal error"}, 500)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass        # the browser went away; there is nobody to answer

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # ---- videos: upload, transcribe on this computer --------------------------
    def _check_upload(self, length: int) -> Tuple[str, str]:
        """Refuse an upload before any of it is read. Returns ``(label, extension)`` for one that may proceed."""
        app = self.app
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            raise ApiError(415, "octet_stream_required")
        name = clean_file_name((self.query.get("name") or [""])[0])
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in MEDIA_EXTENSIONS:
            kinds = ["." + e for e in MEDIA_EXTENSIONS]
            raise ApiError(415, "bad_media_type", "Only video or audio files can be added here: " + ", ".join(kinds),
                           "这里只能放视频或音频文件：" + "、".join(kinds))
        if length == 0:
            raise ApiError(400, "empty_upload", "The file is empty", "这个文件是空的")
        if length > app.max_upload_bytes:
            limit = "%.1f GB" % (app.max_upload_bytes / 1024 ** 3)
            raise ApiError(413, "too_large", "The file is larger than the limit of " + limit, "文件超过了 %s 的上限" % limit)
        try:
            free = shutil.disk_usage(str(app.store.data_dir)).free
        except OSError:
            free = None
        if free is not None and length > free:
            raise ApiError(507, "disk_full", "There is not enough free space on this computer for the file",
                           "这台电脑的剩余空间放不下这个文件")
        return name, extension

    def _upload(self) -> None:
        """``PUT /api/uploads?name=<file name>``: the raw file is the body.

        It is written to disk as it arrives, never held in memory, under a name
        generated here: the name in the query is only a label and the source of
        the extension, which must be one of MEDIA_EXTENSIONS.
        """
        app = self.app
        length = _stated_length(self.headers.get("Content-Length"))
        if self.headers.get("Transfer-Encoding") or length is None:
            raise ApiError(411, "length_required", "The upload must state its size", "上传时必须说明文件大小")
        name, extension = self._check_upload(length)       # refused here: nothing has been read or written yet
        app.uploads.drop_stale()
        upload_id, final, part = app.uploads.new_paths(extension)
        self.connection.settimeout(UPLOAD_IDLE_SEC)
        self._body_read = True
        received, saved = 0, False
        try:
            with os.fdopen(os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                while received < length:
                    try:
                        chunk = self.rfile.read(min(UPLOAD_CHUNK, length - received))
                    except OSError:                 # the browser went away, or sent nothing for too long
                        chunk = b""
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
            if received != length:
                raise ApiError(400, "incomplete_upload", "The upload stopped before the whole file arrived",
                               "文件没有传完就中断了")
            os.replace(str(part), str(final))
            saved = True
        except OSError:                             # writing failed: the disk filled up, or the folder went away
            raise ApiError(507, "upload_not_saved", "The file could not be saved on this computer",
                           "文件没能保存到这台电脑上")
        finally:
            if not saved:
                _remove(part)
        app.uploads.add(upload_id, final, name, received)
        self._json({"upload_id": upload_id, "name": name, "size": received}, 201)

    def _start_video(self, body: Dict[str, Any]) -> None:
        """``POST /api/videos``: transcribe an uploaded file on this computer and add it to the library, as a job."""
        app = self.app
        upload_id, title = body.get("upload_id"), body.get("title")
        if not isinstance(upload_id, str) or not _UPLOAD_ID.fullmatch(upload_id):
            raise ApiError(400, "bad_upload_id")
        if title is not None and not isinstance(title, str):
            raise ApiError(400, "bad_title")
        # ``attach_to``: the library item of a posted script. The video's words then go into that item
        # (``posted.attach_transcript``) and no second item is made. Refused here, before the upload is claimed.
        attach_to = body.get("attach_to")
        if attach_to is not None:
            _posting(lambda: posted.attachable(app.store, attach_to))
        app.uploads.drop_stale()
        upload = app.uploads.claim(upload_id)
        if upload is None:
            raise ApiError(404, "upload_not_found")
        title = (title or "").strip() or upload["name"].rsplit(".", 1)[0]
        sponsored = bool(body.get("is_sponsored"))
        try:
            job = app.jobs.start(TRANSCRIBE_JOB, lambda progress: _transcribe_upload(app, upload, title, sponsored, progress,
                                                                                     attach_to=attach_to),
                                 label=upload["name"])
        except BaseException:
            _remove(upload["path"])                 # claimed but never started: still no copy may be left
            raise
        self._json({"job": job}, 202)

    def _start_dictation(self, body: Dict[str, Any]) -> None:
        """``POST /api/dictation``: write down a short spoken request on this computer, as a job.

        The recording is an upload like any other (``PUT /api/uploads``), at most MAX_DICTATION_BYTES. It
        takes its turn with videos being transcribed, since one transcription runs at a time. The job's
        result is ``{"text"}``; nothing is added to the library, and the recording is deleted whatever happens.
        """
        app = self.app
        upload_id = body.get("upload_id")
        if not isinstance(upload_id, str) or not _UPLOAD_ID.fullmatch(upload_id):
            raise ApiError(400, "bad_upload_id")
        app.uploads.drop_stale()
        upload = app.uploads.claim(upload_id)
        if upload is None:
            raise ApiError(404, "upload_not_found")
        try:
            if upload["size"] > MAX_DICTATION_BYTES:
                limit = "%d MB" % (MAX_DICTATION_BYTES // (1024 * 1024))
                raise ApiError(413, "too_large", "A spoken request can be at most %s. Say it in a sentence or two." % limit,
                               "说的这段最多 %s。一两句话就够了。" % limit)
            job = app.jobs.start(DICTATION_JOB, lambda progress: _dictate(app, upload, progress), lane=TRANSCRIBE_JOB)
        except BaseException:
            _remove(upload["path"])                 # claimed but never started: still no copy may be left
            raise
        self._json({"job": job}, 202)

    def _transcriber_payload(self) -> Dict[str, Any]:
        app = self.app
        return dict(app.transcriber.status(), job=app.jobs.latest(INSTALL_JOB),
                    upload={"max_bytes": app.max_upload_bytes, "extensions": list(MEDIA_EXTENSIONS)})

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "not_found")
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None:                       # a kind of file not named there: what the system says, as before
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---- copy-and-paste mode ------------------------------------------------
    def _manual_prompt(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """``{"prompt"}``, plus ``brief_id`` for a draft: the record of what that prompt contains."""
        store, task = self.app.store, _task(body)
        if task == "draft":
            notes = str(body.get("notes") or "").strip()
            if not notes:
                raise ApiError(400, "notes_required")
            brief = pipeline.draft_brief(store, notes=notes, target_sec=_target_sec(body),
                                         video_format=_video_format(body), keywords=body.get("keywords"))
            return {"prompt": pipeline.single_prompt(brief["system"], brief["user"]), "brief_id": brief["brief_id"]}
        if task == "suggest":
            system, user = _suggesting(lambda: suggest.suggest_prompt(
                store, body.get("script_id"), body.get("kind"), body.get("line_index"), body.get("instruction")))
        elif task == "voice":
            system, user = pipeline.voice_prompt(store)
        elif task == "trends":
            system, user = pipeline.trends_prompt(store, focus=str(body.get("focus") or ""))
        else:
            system, user = pipeline.ideas_prompt(store, focus=_focus(body))
        return {"prompt": pipeline.single_prompt(system, user)}

    def _manual_reply(self, body: Dict[str, Any]) -> None:
        store, task = self.app.store, _task(body)
        # For ideas: what was asked, and the lookup that came before. Malformed ones are refused before the reply is read.
        focus, lookup = (_focus(body), _pasted_lookup(body)) if task == "ideas" else ("", None)
        result = llm.parse_json_text(str(body.get("reply") or ""))
        if task == "draft":
            brief_id = body.get("brief_id")
            script = pipeline.apply_draft(
                store, result, notes=str(body.get("notes") or "").strip(), target_sec=_target_sec(body),
                video_format=_video_format(body), default_title=UNTITLED[self._lang(body)],
                keywords=body.get("keywords"), brief_id=brief_id if isinstance(brief_id, str) else None)
            return self._json(script, 201)
        if task == "suggest":     # proposals for a line, checked and shown; the script itself is not touched
            return self._json(_suggesting(lambda: suggest.apply_suggest(
                store, result, body.get("script_id"), body.get("kind"), body.get("line_index"))))
        if task == "voice":       # a proposal, as on every other route: the active profile is not touched
            return self._json({"pending": pipeline.propose_voice(store, result)})
        if task == "trends":
            # ``found``: the keywords that came with a usable source. The page hands them back with the ideas
            # reply that follows (``_pasted_lookup``), so the ideas are saved with the lookup they came after.
            found = pipeline.store_trends(store, result)
            keywords = found.pop("keywords")
            return self._json(dict(trends.payload(store), found=keywords, **found))
        ideas = pipeline.apply_ideas(store, result, focus=focus, lookup=lookup)
        return self._json({"performance": corpus.performance(store.list_samples()), "result": ideas})

    # ---- api ----------------------------------------------------------------
    def _api(self, method: str, path: str) -> None:
        app = self.app
        store = app.store
        route = (method, path)
        if method == "POST":
            self._read_json()       # every POST must be JSON, also one that needs nothing from its body

        if route == ("GET", "/api/state"):
            _, is_example = store.load_dna()
            state = {
                "settings": store.public_settings(), "dna_is_example": is_example,
                "creator": creator.for_page(store.load_creator()),
                "video_formats": list(pipeline.VIDEO_FORMATS), "sample_count": len(store.list_samples()),
                "trend_count": len(store.list_trends()),
                # Whether the AI service in settings can search the web itself (``llm.can_search``): the one-prompt
                # flow then looks current keywords up before suggesting ideas.
                "lookup_available": llm.can_search(store.load_config().get("provider") or ""),
                "setup_next": setup_status.status(store)["next"],
                # Which build this is: a version from a release package, or "from source". Shown on the settings page.
                "build": buildinfo.current(),
                # {"python": version} inside the desktop app, where the Python is the app's own; else null.
                "desktop": buildinfo.desktop(),
                # How an AI app starts the MCP server so that it works on the data this app is showing: the same
                # Python, the script, and the data folder in use. Without the folder it would open the one next to
                # the script, which inside the desktop app is not where the data is.
                "mcp": {"python": sys.executable or "python3", "script": str(ROOT / "run_mcp.py"),
                        "data_dir": str(Path(store.data_dir).resolve())},
            }
            if store.dna_unreadable():
                state["dna_unreadable"] = True
            if app.window_check is not None:        # a script is trying the desktop app: the page loads windowcheck.js
                state["window_check"] = True
            return self._json(state)

        # ---- outside the app: the file manager and the browser (see ``studio.outside``) ----
        if route == ("POST", "/api/outside/show"):
            # {"path": a file this run of the app saved}. Shows it in the file manager. Any other path is refused.
            wanted = self._read_json().get("path")
            if not isinstance(wanted, str) or wanted not in app.saved_files:
                raise ApiError(404, "not_found")
            return self._json({"shown": bool(app.outside.reveal(Path(wanted)))})
        if route == ("POST", "/api/outside/open"):
            # {"url": an http(s) address}. Opens it in the system's browser. ``opened`` false: nothing could be started.
            url = self._read_json().get("url")
            problem = app.outside.link_problem(url)
            if problem:
                raise ApiError(400, "bad_link", "Only a web address (http or https) can be opened: " + problem,
                               "只能打开网址（http 或 https）")
            return self._json({"opened": bool(app.outside.open_link(url))})

        # ---- the window check: there only while a script is trying the desktop app ----
        if path in ("/api/window-check", "/api/window-check/start"):
            check = app.window_check
            if check is None:
                raise ApiError(404, "not_found")
            if route == ("GET", "/api/window-check"):
                return self._json(check)
            if route == ("POST", "/api/window-check/start"):
                check["start"] = True
                return self._json({"start": True})
            if route == ("POST", "/api/window-check"):
                report = self._read_json()
                if len(json.dumps(report, ensure_ascii=False)) > MAX_WINDOW_REPORT:
                    raise ApiError(413, "too_large")
                # While it is at work the page says how far it has come ({"unfinished": true, ...}). That is kept
                # apart: the trial must not take it for the report, and reads it when no report ever comes.
                check["so_far" if report.get("unfinished") is True else "report"] = report
                return self._json({"received": True})
            raise ApiError(405, "method_not_allowed")
        if route == ("GET", "/api/setup"):
            return self._json(setup_status.status(store))
        if route == ("GET", "/api/creator"):
            return self._json(creator.for_page(store.load_creator()))
        if route == ("PUT", "/api/creator"):
            body = self._read_json()
            try:
                profile = creator.normalize(body, previous=store.load_creator())
            except ValueError as exc:
                raise ApiError(400, str(exc))
            store.save_creator(profile)
            return self._json(creator.for_page(profile))
        if route == ("GET", "/api/dna"):
            dna, is_example = store.load_dna()
            answer = {"dna": dna, "is_example": is_example}
            if store.dna_unreadable():          # their own file is there but cannot be read: the example is shown, and the page must say so
                answer["unreadable"] = True
            return self._json(answer)
        if route == ("GET", "/api/scripts"):
            # Each with ``posted`` and ``posted_on``: whether the script is marked as posted, and the day.
            return self._json({"scripts": posted.with_posted_dates(store, store.list_scripts())})
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
            return self._json(self._manual_prompt(self._read_json()))
        if route == ("POST", "/api/manual/reply"):
            return self._manual_reply(self._read_json())

        if route == ("POST", "/api/settings/models"):
            body = self._read_json()
            provider = _provider(body)
            config = store.load_config()        # only read here, to find the address or the key to try
            if provider == "local":
                base_url = llm.normalize_local_base(str(body.get("base_url") or config.get("local_base_url") or ""))
                models = llm.list_models("local", transport=self.app.transport, base_url=base_url)
                store.update_config(lambda fresh: fresh.update(local_base_url=base_url))  # the server answered, so remember it
            else:
                key = str(body.get("key") or "").strip() or config["keys"].get(provider) or ""
                if not key:
                    raise ApiError(400, "key_required")
                models = llm.list_models(provider, key, transport=self.app.transport)
                store.update_config(lambda fresh: fresh["keys"].update({provider: key}))  # the key just worked, so keep it
            return self._json({"models": models, "settings": store.public_settings()})
        if route == ("POST", "/api/settings"):
            body = self._read_json()
            if body.get("provider") == MANUAL_PROVIDER:
                store.update_config(lambda config: config.update(provider=MANUAL_PROVIDER))
            else:
                provider = _provider(body)
                key = str(body.get("key") or "").strip()
                base_url = (llm.normalize_local_base(str(body["base_url"]))
                            if provider == "local" and body.get("base_url") else None)
                model = str(body.get("model") or "").strip()

                def choose(config: Dict[str, Any]) -> None:
                    if base_url is not None:
                        config["local_base_url"] = base_url
                    elif provider != "local" and key:
                        config["keys"][provider] = key
                    config["provider"], config["model"] = provider, model

                store.update_config(choose)
            return self._json({"settings": store.public_settings()})
        if route == ("GET", "/api/wordlist"):
            # The word list behind the wording findings, as the Settings page shows it: what each entry rests on,
            # whether it is on, the creator's own phrases and the limits on them.
            return self._json(wordlist.payload(store))
        if route == ("PUT", "/api/wordlist"):
            # {"off": [ids of the shipped entries that are to be off], "phrases": [the creator's own phrases]}.
            # Either may be left out and then stays as stored. The answer is the list as it then is.
            try:
                return self._json(wordlist.save(store, self._read_json()))
            except wordlist.Refused as exc:
                raise ApiError(400, exc.code, exc.message, exc.message_zh, extra=exc.extra)

        if route == ("GET", "/api/samples"):
            samples = store.list_samples()
            # stats: habits measured from the wording, so only from text the creator wrote or confirmed.
            stats = corpus.voice_stats(s["text"] for s in pipeline.confirmed_samples(samples) if not s["is_sponsored"])
            # measured: speaking rate from the timestamps of non-sponsored timed items, or null under a minute of them.
            # Transcripts waiting for confirmation count here: their timing does not depend on the wording.
            # An item that came from a posted script carries ``posted``: its days, its link and how its wording stands.
            return self._json({"samples": posted.for_page(store, samples), "stats": stats,
                               "measured": pipeline.measured_rate(samples)})
        if route == ("POST", "/api/samples"):
            body = self._read_json()
            text = str(body.get("text") or "")
            kind, measured_timing = "script", None
            if body.get("subtitle_text") is not None:        # the contents of an .srt or .vtt file
                try:
                    segments = timing.parse_subtitles(body["subtitle_text"]) if isinstance(body["subtitle_text"], str) else []
                except ValueError:
                    segments = []
                if not segments:
                    raise ApiError(400, "bad_subtitles")
                # The words exactly as the file has them, and how fast they were said, counted from its timestamps.
                text, kind, measured_timing = timing.transcript_text(segments), "subtitle", timing.timing_stats(segments)
            elif body.get("docx_base64"):
                try:
                    text = corpus.extract_docx_text(base64.b64decode(str(body["docx_base64"]), validate=True))
                except (binascii.Error, ValueError, corpus.ImportError_):
                    raise ApiError(400, "bad_file", "This file could not be read as a Word document (.docx)",
                                   "这个文件没法当作 Word 文档（.docx）读取")
            if not text.strip():
                raise ApiError(400, "empty_text")
            source_name = body.get("source_name")
            source_name = source_name if isinstance(source_name, str) else ""
            try:
                if body.get("attach_to") is not None:
                    # The subtitle file of a posted video, for the item its script already has: its words take
                    # the place of the script's text there, and no second item is made.
                    return self._json(posted.shown(store, _posting(lambda: posted.attach_transcript(
                        store, body["attach_to"], text=text, kind=kind, timing=measured_timing, source_name=source_name))))
                sample = store.create_sample(
                    title=str(body.get("title") or ""), text=text,
                    is_sponsored=bool(body.get("is_sponsored")), metrics=body, kind=kind, timing=measured_timing,
                    source_name=source_name)
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json(posted.shown(store, sample), 201)
        if route == ("POST", "/api/voice/learn"):
            self._read_json()
            self._require_ai()
            if not any(not s["is_sponsored"] and s["text"].strip() for s in pipeline.confirmed_samples(store.list_samples())):
                raise ApiError(400, "no_samples", "There is nothing of the creator's own to learn from yet. A video transcript "
                               "counts once it has been confirmed.", "还没有可以学习的自己的素材。视频转写要先确认过才算。")
            # A proposal: the active profile stays as it is until the creator accepts (POST /api/voice/approve).
            return self._json({"pending": pipeline.learn_voice(store, self.app.transport)})
        if route == ("GET", "/api/voice/pending"):
            # ``pending.created_at`` names this proposal: POST /api/voice/approve has to send it back.
            return self._json({"pending": pipeline.pending_voice(store)})
        if route == ("DELETE", "/api/voice/pending"):
            return self._json({"ok": True, "discarded": pipeline.discard_voice(store)})
        if route == ("POST", "/api/voice/approve"):
            # {"created_at": <pending.created_at as the page received it>,
            #  "rejected": [paths the creator unticked], "keep": [paths of unconfirmed items the creator ticked]}
            body = self._read_json()
            rejected, keep = body.get("rejected", []), body.get("keep", [])
            if not isinstance(rejected, list) or not all(isinstance(p, str) for p in rejected):
                raise ApiError(400, "bad_rejected", "`rejected` must be a list of item paths", "rejected 必须是一组条目路径")
            if not isinstance(keep, list) or not all(isinstance(p, str) for p in keep):
                raise ApiError(400, "bad_keep", "`keep` must be a list of item paths", "keep 必须是一组条目路径")
            try:
                dna = pipeline.approve_voice(store, rejected, created_at=body.get("created_at"), keep=keep)
            except pipeline.VoiceReviewError as exc:
                status, message, message_zh = {
                    "no_pending": (409, "There is no proposed voice profile waiting to be accepted.", "现在没有等你确认的声音档案。"),
                    "stale_proposal": (409, "This page is not showing the proposal that is stored now; a newer one may have "
                                       "replaced it. Nothing was accepted. Reload the page to see the current proposal.",
                                       "这一页显示的不是现在保存的那份提议，它可能已经被更新的一份替换了。"
                                       "这次没有采用任何内容。请刷新页面，看看现在的提议。"),
                    "no_samples": (400, "There is no non-sponsored material left to check the proposal against, so it was "
                                   "not accepted. Add or confirm some material first.",
                                   "素材库里已经没有可以用来核对这份提议的非商单素材了，所以没有采用。请先添加或确认一些素材。"),
                }.get(exc.code, (400, str(exc), ""))
                raise ApiError(status, exc.code, message, message_zh)
            return self._json({"dna": dna, "is_example": False})
        if route == ("PUT", "/api/voice/rate"):
            cpm = self._read_json().get("cpm")
            if not isinstance(cpm, (int, float)) or isinstance(cpm, bool) or not 100 <= cpm <= 500:
                raise ApiError(400, "bad_rate")
            try:
                return self._json({"dna": pipeline.set_speaking_rate(store, int(cpm)), "is_example": False})
            except ValueError:
                raise ApiError(400, "no_profile")
        if route == ("GET", "/api/voice/history"):
            return self._json({"history": setup_status.voice_history(store)})
        m = re.fullmatch(r"/api/voice/history/(%s)/restore" % HISTORY_ID, path)
        if m and method == "POST":
            # The creator's click in the app, like accepting a proposal. There is deliberately no MCP tool for it.
            dna = store.restore_dna(m.group(1))
            if dna is None:
                raise ApiError(404, "not_found", "That earlier voice profile is no longer there",
                               "那一份之前的声音档案已经不在了")
            return self._json({"dna": dna, "is_example": False})
        if route == ("GET", "/api/recommendations"):
            # ``discover``: the latest one-prompt run, running or finished, so the page can follow it or show how it ended.
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": store.load_recommendations(),
                               "discover": app.jobs.latest(DISCOVER_JOB)})
        if route == ("POST", "/api/recommendations"):
            focus = _focus(self._read_json())
            self._require_ai()
            if not any(s["text"].strip() for s in store.list_samples()):
                raise ApiError(400, "no_samples")
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": pipeline.recommend_topics(store, transport=self.app.transport, focus=focus)})
        if route == ("POST", "/api/discover"):
            # {"focus": what the creator typed or said, "lookup": false to leave out the web lookup,
            #  "found": the keywords a lookup by copy and paste brought in just before, which then stands in for it}
            # Answers at once with a job (kind DISCOVER_JOB); its result is what pipeline.discover returns.
            body = self._read_json()
            focus, lookup, pasted = _focus(body), body.get("lookup", True), _pasted_lookup(body)
            if not isinstance(lookup, bool):
                raise ApiError(400, "bad_lookup")
            found = pasted["keywords"] if pasted else None
            self._require_ai()
            with app.discover_lock:                 # two clicks must not start two runs
                running = app.jobs.running(DISCOVER_JOB)
                job = running[0] if running else app.jobs.start(
                    DISCOVER_JOB, lambda progress: pipeline.discover(store, focus, app.transport, progress,
                                                                     lookup=lookup, found=found),
                    label=focus)
            return self._json({"job": job}, 202)

        # ---- videos, transcribed on this computer ----
        if route == ("GET", "/api/transcriber"):
            return self._json(self._transcriber_payload())
        if route == ("POST", "/api/transcriber/install"):
            with app.install_lock:                  # two clicks must not start two installs
                running = app.jobs.running(INSTALL_JOB)
                job = running[0] if running else app.jobs.start(
                    INSTALL_JOB, lambda progress: _install_transcriber(app, progress))
            return self._json({"job": job}, 202)
        if route == ("DELETE", "/api/transcriber"):
            if any(app.jobs.running(kind) for kind in (INSTALL_JOB, TRANSCRIBE_JOB, DICTATION_JOB)):
                raise ApiError(409, "transcriber_busy", "An install or a transcription is still running",
                               "安装或转写还在进行中")
            try:
                app.transcriber.uninstall()
            except transcribe.TranscribeError as exc:
                raise ApiError(409, "transcriber_busy", str(exc), getattr(exc, "message_zh", ""))
            return self._json(self._transcriber_payload())
        if route == ("PUT", "/api/uploads"):
            return self._upload()
        if route == ("POST", "/api/videos"):
            return self._start_video(self._read_json())
        if route == ("POST", "/api/dictation"):
            return self._start_dictation(self._read_json())
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
        if m and method == "GET":
            job = app.jobs.get(m.group(1))
            if job is None:
                raise ApiError(404, "not_found")
            return self._json({"job": job})

        # ---- lessons from the creator's edits ----
        if route == ("GET", "/api/lessons"):
            return self._json(pipeline.lessons_payload(store))
        if route == ("POST", "/api/lessons"):
            body = self._read_json()
            try:
                lesson, created = pipeline.remember_lesson(store, script_id=body.get("script_id"), ai=body.get("ai"),
                                                           final=body.get("final"), note=body.get("note"))
            except ValueError as exc:
                raise ApiError(404 if str(exc) == "not_found" else 400, str(exc))
            return self._json(lesson, 201 if created else 200)
        m = re.fullmatch(r"/api/lessons/([0-9a-f]{12})", path)
        if m and method == "DELETE":
            if not store.delete_lesson(m.group(1)):
                raise ApiError(404, "not_found")
            return self._json({"ok": True})

        if route == ("GET", "/api/trends"):
            return self._json(trends.payload(store))
        if route == ("POST", "/api/trends"):
            items, skipped = trends.parse_keywords(str(self._read_json().get("text") or ""))
            if not items:
                raise ApiError(400, "no_keywords")
            added = store.upsert_trends(items, source="manual")
            return self._json(dict(trends.payload(store), added=added, skipped=skipped), 201)
        if route in (("POST", "/api/trends/import/preview"), ("POST", "/api/trends/import")):
            # A keyword export from an analytics tool: {"name", "text" or "file_base64"} and, once the creator has
            # picked them, "keyword_column" and "heat_column". The preview stores nothing. What is imported is the
            # creator's own entry ("manual"): she brought the file, no AI did.
            body = self._read_json()
            try:
                if path.endswith("/preview"):
                    return self._json(keyword_import.preview(body))
                items, counts = keyword_import.collect(body)
            except keyword_import.ImportRefused as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            added = store.upsert_trends(items, source="manual")
            return self._json(dict(trends.payload(store), added=added, **counts), 201)
        if route == ("PUT", "/api/trends/weight"):
            weight = self._read_json().get("weight")
            if weight not in trends.WEIGHTS:
                raise ApiError(400, "bad_weight")
            store.update_config(lambda config: config.update(trend_weight=weight))
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
            sample = store.get_sample(m.group(1))
            if sample is None:
                raise ApiError(404, "not_found")
            if method == "PUT":
                body = self._read_json()
                # Confirming a transcript is its own act: a real true or false, never a side effect of another edit.
                # The same act says of a posted script's text "this is what I said, in my words".
                if "needs_review" in body and not isinstance(body["needs_review"], bool):
                    raise ApiError(400, "bad_needs_review")
                try:
                    # For an item that came from a posted script, also ``posted_on``, ``metrics_on`` and ``note_url``;
                    # a number changed here takes today as the day it was read (``posted.update_item``).
                    updated = _posting(lambda: posted.update_item(store, sample, body))
                    # None: the item was removed in the same moment; answered with null, as before.
                    return self._json(posted.shown(store, updated) if updated is not None else None)
                except TextTooLong as exc:
                    raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            if method == "DELETE":
                store.delete_sample(m.group(1))
                return self._json({"ok": True})

        if route == ("GET", "/api/hooks"):
            # The creator's own openings with their videos' results. Counted in code; no model is asked.
            return self._json(hooks.library(store.list_samples()))

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/suggest", path)
        if m and method == "POST":
            # Proposals for one line (or a critic's read of all of them), each checked before it is shown.
            # Nothing is changed in the script: the page applies what the creator picks, through a normal save.
            body = self._read_json()
            if store.get_script(m.group(1)) is None:
                raise ApiError(404, "not_found")
            self._require_ai()
            return self._json(_suggesting(lambda: suggest.suggest(
                store, m.group(1), body.get("kind"), body.get("line_index"), body.get("instruction"),
                transport=self.app.transport)))

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/changes", path)
        if m and method == "GET":
            changes = pipeline.script_changes(store, m.group(1))
            if changes is None:
                raise ApiError(404, "not_found")
            return self._json(changes)

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/posted", path)
        if m and method in ("POST", "PUT"):
            # POST: mark the script as posted. It gets ONE library item, made from the script as it stands; when
            # it has one already, that item is changed and no second is made. PUT: change what was recorded
            # (the numbers and the day they were read, the day posted, the link, or with "refresh_text" the
            # text); refused when the item is gone. The fields are those of ``posted.mark``. Answers with the
            # script, as GET /api/scripts/<id> does.
            body = self._read_json()
            script, created = _posting(lambda: posted.mark(store, m.group(1), body, update_only=(method == "PUT")))
            return self._json(script, 201 if created else 200)

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/export/(\w+)/save", path)
        if m and method == "POST":
            # The file GET .../export/<kind> sends, put into the Downloads folder by this program, under a name
            # that is not taken; {"lang"} chooses the language of its name. The desktop app's page asks for this
            # in place of a download (why: ``studio.outside``). When Downloads cannot be written to, the file goes
            # into ``exports`` beside the data folder and ``in_downloads`` is false. Answers with where it is.
            filename, data, _ = self._export(m.group(1), m.group(2), self._lang(self._read_json()))
            downloads = app.outside.downloads_folder()
            try:
                saved = app.outside.save(filename, data, [downloads, Path(store.data_dir).resolve().parent / "exports"])
            except OSError as exc:
                raise ApiError(507, "export_not_saved", "The file could not be saved on this computer: %s" % exc,
                               "文件没能存到这台电脑上：%s" % exc)
            app.remember_saved(saved)
            return self._json({"name": saved.name, "folder": str(saved.parent), "path": str(saved),
                               "in_downloads": saved.parent == Path(downloads)}, 201)

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})(?:/export/(\w+))?", path)
        if m:
            script_id, export_kind = m.group(1), m.group(2)
            if export_kind and method == "GET":
                filename, data, content_type = self._export(script_id, export_kind, self._lang())
                disposition = "attachment; filename*=UTF-8''" + urllib.parse.quote(filename)
                return self._send(200, data, content_type, {"Content-Disposition": disposition})
            script = store.get_script(script_id)
            if script is None:
                raise ApiError(404, "not_found")
            if not export_kind and method == "GET":
                # ``posted``: null, or what was recorded when the script was marked as posted (``posted.for_script``).
                return self._json(posted.with_posted(store, script))
            if not export_kind and method == "PUT":
                body = self._read_json()
                try:    # base_updated_at: the version this save was made from; absent: save whatever is stored
                    saved = store.update_script(script_id, body, base_updated_at=body.get("base_updated_at"))
                    return self._json(posted.with_posted(store, saved) if saved is not None else saved)
                except StaleScript as exc:
                    raise _stale_script(exc)
                except ValueError as exc:
                    if str(exc) != "bad_base_updated_at":
                        raise
                    raise ApiError(400, "bad_base_updated_at", "base_updated_at has to be the updated_at of a script",
                                   "base_updated_at 必须是脚本的 updated_at")
            if not export_kind and method == "DELETE":
                store.delete_script(script_id)
                return self._json({"ok": True})
        raise ApiError(404, "not_found")


def _stale_script(exc: StaleScript) -> ApiError:
    """The refusal for a request based on an older version of a script than the one stored: a save, or marking it as posted."""
    return ApiError(409, "stale_script",
                    "This script was changed somewhere else after you opened it here: in another tab "
                    "or window, or by an AI app connected to the studio. Nothing was saved. Reload "
                    "it to see the newer version.",
                    "这份脚本在你打开之后，已经在别处被改过了：另一个标签页或窗口，或者连接到工作台的 AI 应用。"
                    "这次没有保存。请重新加载，查看较新的版本。",
                    extra={"updated_at": exc.updated_at})


def _posting(work: Any) -> Any:
    """Run a step about a posted script, turning its refusals into answers a page can show, in both languages."""
    try:
        return work()
    except posted.PostedError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
    except StaleScript as exc:
        raise _stale_script(exc)
    except TextTooLong as exc:
        raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})


_SUGGEST_REFUSALS = {
    "not_found": (404, "There is no such script.", "没有这份脚本。"),
    "bad_kind": (400, "kind has to be one of: hooks, rewrite, shorten, critic.", "kind 只能是 hooks、rewrite、shorten 或 critic。"),
    "bad_line": (400, "There is no such line in this script.", "这份脚本里没有这一句。"),
    "live_clip": (400, "This line is a live clip: somebody's recorded words, which are not rewritten.",
                  "这一句是现场原声，是别人当时说的话，不改写。"),
    "empty_line": (400, "There is nothing written here yet.", "这里还没有写内容。"),
}


def _suggesting(work: Any) -> Any:
    """Run a step of the line editor, turning its refusals (``suggest.SuggestError``) into answers a page can show."""
    try:
        return work()
    except suggest.SuggestError as exc:
        status, message, message_zh = _SUGGEST_REFUSALS[exc.code]
        raise ApiError(status, exc.code, message, message_zh)


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


def _focus(body: Dict[str, Any]) -> str:
    """What the creator asked for this time (``focus``), as one short line. Absent means nothing in particular."""
    value = body.get("focus")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, "bad_focus")
    return pipeline.clean_focus(value)


def _pasted_lookup(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The lookup done by copy and paste just before the ideas being asked for, or None when there was none.

    The page sends ``found``, the keywords that lookup's reply brought in (the ``found`` of the ``trends``
    reply). Only keywords that are tracked are kept from it (``pipeline.apply_ideas``), so nothing the page
    sends here can add a keyword or a link.
    """
    found = body.get("found")
    if found is None:
        return None
    if not isinstance(found, list) or not all(isinstance(keyword, str) for keyword in found):
        raise ApiError(400, "bad_found")
    return {"status": pipeline.LOOKUP_DONE, "keywords": found}


def _job_error(exc: Exception) -> JobError:
    """A transcriber failure as a job failure: its code and its messages, which are written to be shown (English and Chinese)."""
    return JobError(str(getattr(exc, "code", "") or "failed"), str(exc), str(getattr(exc, "message_zh", "") or ""))


def _transcribe_upload(app: App, upload: Dict[str, Any], title: str, is_sponsored: bool, progress: Progress,
                       attach_to: Optional[str] = None) -> Dict[str, Any]:
    """One transcription job: the upload becomes a ``video`` item in the library, text and measured timing.

    With ``attach_to``, the id of the library item of a posted script, no item is made: the transcript takes
    the place of the script's text in that item (``posted.attach_transcript``), which keeps its title, its
    numbers and its link to the script. ``title`` and ``is_sponsored`` are not used then.

    The item waits for the creator's confirmation (``needs_review``): its timing counts at once, its
    wording only after they have read it. ``timing.unclear_dropped`` is how many stretches the
    transcriber left out because it could not make them out, so the page can say so.

    The uploaded copy is deleted on the way out whatever happened. The creator
    is told that no copy of the video is kept, so that has to hold for a
    failure as much as for a success.
    """
    path = upload["path"]
    try:
        try:
            result = app.transcriber.transcribe_file(str(path), progress)
        except transcribe.TranscribeError as exc:
            raise _job_error(exc)
        result = result if isinstance(result, dict) else {}
        segments = result.get("segments") if isinstance(result.get("segments"), list) else []
        dropped = result.get("unclear_dropped")
        dropped = dropped if isinstance(dropped, int) and not isinstance(dropped, bool) and dropped > 0 else 0
        text = timing.transcript_text(segments)
        if not textstats.spoken_units(textstats.strip_cues(text)):
            if dropped:
                raise JobError("no_speech", "The speech in this file could not be made out clearly, so nothing was "
                                            "added to your material.", "这个文件里的话没能听清，所以没有加进你的素材。")
            raise JobError("no_speech", "No speech was found in this file, so nothing was added to your material.",
                           "这个文件里没有听到说话的声音，所以没有加进你的素材。")
        measured = dict(timing.timing_stats(segments), unclear_dropped=dropped)
        try:
            if attach_to is not None:
                sample = posted.attach_transcript(app.store, attach_to, text=text, kind="video", timing=measured,
                                                  source_name=upload["name"])
            else:
                sample = app.store.create_sample(title=title, text=text, is_sponsored=is_sponsored, kind="video",
                                                 timing=measured, source_name=upload["name"])
        except (TextTooLong, posted.PostedError) as exc:    # the library's length limit, or the item is gone: say so, in both languages
            raise JobError(exc.code, exc.message, exc.message_zh)
        return {"sample_id": sample["id"], "title": sample["title"]}
    finally:
        _remove(path)


def _dictate(app: App, upload: Dict[str, Any], progress: Progress) -> Dict[str, Any]:
    """One dictation job: a short spoken request becomes ``{"text"}``, one line of at most ``pipeline.MAX_FOCUS_CHARS``.

    It is written down by the same transcriber as a video, on this computer. Nothing is added to the
    library, and the text goes nowhere until the creator has seen it in the box and asked for ideas.
    The recording is deleted on the way out whatever happened, as every upload is.
    """
    path = upload["path"]
    try:
        try:
            result = app.transcriber.transcribe_file(str(path), progress)
        except transcribe.TranscribeError as exc:
            raise _job_error(exc)
        result = result if isinstance(result, dict) else {}
        segments = result.get("segments") if isinstance(result.get("segments"), list) else []
        text = pipeline.clean_focus(textstats.strip_cues(timing.transcript_text(segments)))
        if not textstats.spoken_units(text):
            raise JobError("no_speech", "No speech could be made out. Try again a little closer to the microphone, or type it.",
                           "没有听清说的话。请靠近麦克风再说一次，或者直接打字。")
        return {"text": text}
    finally:
        _remove(path)


def _install_transcriber(app: App, progress: Progress) -> None:
    try:
        app.transcriber.install(progress)
    except transcribe.TranscribeError as exc:
        raise _job_error(exc)


class StudioServer(ThreadingHTTPServer):
    """The HTTP server, plus what its request handlers share (``app``)."""

    app: Optional[App] = None
    # "Reuse the address" lets a server start again right after it was stopped. On Windows the same setting
    # means something else: a second program may listen on a port that is already taken, and each of the two
    # then gets some of the requests. There the port is asked for exclusively instead, so that a taken port
    # fails as it does everywhere else and ``main`` goes on to the next one.
    allow_reuse_address = os.name != "nt"
    # How many connections may wait to be taken up. Python's own choice is 5. A browser opens six connections to
    # one address at once, a page of this app is twelve files, and every request is a connection of its own; a
    # second window, or a script that tries the app, adds its own. When more arrive than may wait, the system
    # turns the rest away, and a page that loses one of its files stays empty or half-working until it is
    # loaded again. Measured on one Mac on 18 November 2026 with twelve connections opened at the same moment,
    # 150 times: with 5, 802 of 1,800 were lost, most of them reset; with 128, none.
    request_queue_size = 128

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)        # Windows only
        if os.name == "nt" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    def server_close(self) -> None:
        # Also reached from the constructor when the port is taken, before ``app`` exists.
        if self.app is not None:
            self.app.uploads.close()        # uploads still waiting are not kept once the app has closed
        super().server_close()


def make_server(port: int, store: Store, transport: Optional[llm.Transport] = None,
                transcriber: Any = None, outside_world: Any = None) -> StudioServer:
    """A server on 127.0.0.1. ``transport``, ``transcriber`` and ``outside_world`` stand in, in tests, for the
    model, for Whisper, and for the Downloads folder, the file manager and the browser."""
    server = StudioServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = App(store, transport, transcriber, outside_world)
    return server


def _stop_when_input_ends(server: StudioServer, stream: Any) -> None:
    """Stop ``server`` once ``stream`` has ended.

    The desktop app starts the core with its standard input held open, and never writes to it. However the
    app goes away (its window is closed, it crashes, the system ends it), its end of that pipe closes, the
    read below returns, and the core stops the way it does on Ctrl-C: uploads still waiting are removed and
    the port is free again. So the core never outlives the window it belongs to. Nothing read is used.
    """
    try:
        raw = getattr(stream, "buffer", stream)
        while raw.read(4096):
            pass
    except (AttributeError, OSError, ValueError):
        pass                              # no input at all, or it was closed under us: the same as its end
    server.shutdown()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Learn My Voice script studio (local web app)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="the first of ten ports to try; 0 takes any free port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--exit-with-parent", action="store_true",
                        help="stop when standard input ends. The desktop app starts the core like this, so that it never outlives its window")
    args = parser.parse_args(argv)

    # What is printed below is Chinese and English. Where the output cannot show Chinese (it is sent to a file or
    # another program on a Windows that uses an older text encoding), a character is replaced and the app still
    # starts; without this, printing the first line ended the app before it served a page.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

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
              % (args.port, args.port + 9, args.port, args.port + 9), file=sys.stderr, flush=True)
        return 1
    if os.environ.get(WINDOW_CHECK_ENV) == "1":
        server.app.start_window_check()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    # Sent at once also when the output is not a window: whoever started the app is waiting for this address.
    print("工作台已启动 / Studio running: %s\n关闭这个窗口即可退出。/ Close this window to quit." % url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    if args.exit_with_parent:
        threading.Thread(target=_stop_when_input_ends, args=(server, sys.stdin), name="exit-with-parent", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
