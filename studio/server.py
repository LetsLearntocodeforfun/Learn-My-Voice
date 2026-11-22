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

The two writes that are not JSON are the video upload (``PUT /api/uploads``) and
a backup file that is to be restored (``PUT /api/restore/file``), each with the
raw file as the body. They are covered by 1 and 3, and must also declare
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
import hmac
import html
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import socket
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import checks, corpus, creator, exports, keyword_import, llm, pipeline, textstats, timing, transcribe, trends
from . import buildinfo, hooks, outside, posted, refs, suggest, video_ingest, wordlist
from . import planner, rednote_analytics, soundstats, sponsored, tone_analyzer, updater
from . import autosuggest     # registers autopilot's tasks plans.suggest and ideas.refresh: on a list, nothing is started
from . import setup as setup_status
from . import autopilot, autovoice, backup, trend_sources
from .jobs import JobError, Jobs, Progress
from .store import (
    HISTORY_ID,
    MANUAL_PROVIDER,
    PROVIDERS,
    ROOT,
    StaleScript,
    Store,
    TextTooLong,
    _owner_only,
    _replace,
    clean_file_name,
    kept_value,
)

WEB_DIR = Path(__file__).with_name("web")
SERVER_TOKEN_FILE = "server-token"
SESSION_COOKIE_NAME = "studio_session"
TOKEN_HEADER = "X-Studio-Token"
MAX_BODY = 12 * 1024 * 1024  # room for a base64 .docx
MAX_REMEMBERED_FILES = 200   # exported files of this run that can still be shown in the file manager
MAX_WINDOW_REPORT = 200_000  # characters of what the window check may report
# Set to 1 by the script that tries an installer of the desktop app: see ``App.start_window_check``.
WINDOW_CHECK_ENV = "LEARN_MY_VOICE_WINDOW_CHECK"
# Set to 1 by anything that starts the app without a person in front of it: no question is put to GitHub then.
NO_UPDATE_CHECK_ENV = "LEARN_MY_VOICE_NO_UPDATE_CHECK"
# Set to 1 the same way: the app then runs no task by itself, whatever its settings say (studio/autopilot.py).
NO_AUTOPILOT_ENV = autopilot.NO_AUTOPILOT_ENV
DEFAULT_PORT = 8765
UNTITLED = {"zh": "未命名脚本", "en": "Untitled script"}
MANUAL_TASKS = ("draft", "voice", "ideas", "trends", "suggest", "video_ingest", "sentiment", "comment_ocr")
# "sponsor": a brand's brief read into plain words, requirements and ways in; "sponsor_draft": a draft from what the
# creator chose of that (studio/sponsored.py).
MANUAL_TASKS += ("sponsor", "sponsor_draft")
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
    "img-src 'self' blob: data:",
    "media-src 'self' blob:",
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

_PUBLIC_STATIC_SUFFIXES = (".css", ".js", ".svg", ".png", ".ico", ".woff2")

# How the app's page (web/index.html) begins. ``page_with_keeps`` writes what the core keeps for the page onto it.
PAGE_OPENING = b'<html lang="zh-CN">'

UNAUTHORIZED_PAGE = (
    '<!doctype html>\n'
    '<html lang="zh-CN">\n'
    '<head>\n'
    '  <meta charset="utf-8">\n'
    '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '  <title>脚本工作台 · Learn My Voice</title>\n'
    '  <link rel="stylesheet" href="/app.css">\n'
    '</head>\n'
    '<body>\n'
    '  <main id="view">\n'
    '    <div class="wrap" style="max-width:640px;padding-top:36px">\n'
    '      <div class="card">\n'
    '        <h1 style="font-size:22px;margin-bottom:10px">这个页面缺少本次启动的访问钥匙</h1>\n'
    '        <p style="margin:0 0 10px">每次启动工作台都会生成一把新的访问钥匙，防止这台电脑上的其他程序或其他账号读取你的脚本。'
    '书签、历史记录或上次留下的标签页里不会带有这把钥匙。</p>\n'
    '        <p style="margin:0"><strong>怎么打开：</strong>请从启动程序或桌面应用打开工作台，或使用启动窗口里显示的「安全访问链接」；'
    '如果在桌面应用里看到这一页，请先退出应用再重新打开。</p>\n'
    '      </div>\n'
    '      <div class="card">\n'
    '        <h2 style="font-size:18px;margin:0 0 8px">This page was opened without the studio&#39;s launch key</h2>\n'
    '        <p class="meta" style="font-size:14.5px;margin:0 0 10px">Each start of the studio creates a new key so other programs '
    'and other accounts on this computer cannot read your scripts. A bookmark or an old tab does not carry it.</p>\n'
    '        <p class="meta" style="font-size:14.5px;margin:0"><strong>What to do:</strong> open the studio from its launcher or '
    'the desktop app, or follow the launch link printed in the launcher window. In the desktop app, quit Learn My Voice and open it again.</p>\n'
    '      </div>\n'
    '    </div>\n'
    '  </main>\n'
    '</body>\n'
    '</html>\n'
).encode("utf-8")


def _tokens_equal(candidate: Any, expected: str) -> bool:
    """Constant-time UTF-8 byte comparison; returns False on non-strings, oversized inputs, or surrogates."""
    if not isinstance(candidate, str) or not expected or len(candidate) > 1024:
        return False
    try:
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False

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


def page_with_keeps(page: bytes, kept: Dict[str, str], token: str = "") -> bytes:
    """The app's page with what the core keeps for it (``Store.load_page_keeps``) written onto its ``<html>`` element.

    ``data-kept`` holds all of it as JSON; app.js reads it before it draws anything, so the language and the
    look are the chosen ones from the first moment, also at an address the page has never been opened at
    (inside the desktop app the port differs at every start). The look is written out as ``data-theme`` and
    ``data-accent`` too, which the stylesheet reads: the window is not white for a moment for somebody who chose
    dark. When ``token`` is given, it is written onto ``data-token`` so the page can send ``X-Studio-Token`` on
    API requests. With nothing kept and no token, or a page that does not begin as expected, the page is sent as it is.
    """
    if (not kept and not token) or page.count(PAGE_OPENING) != 1:
        return page
    opening = '<html lang="%s"' % ("en" if kept.get("lang") == "en" else "zh-CN")
    if kept:
        opening += ' data-kept="%s"' % html.escape(json.dumps(kept, ensure_ascii=True, sort_keys=True), quote=True)
        if kept.get("theme") in ("light", "dark"):
            opening += ' data-theme="%s"' % kept["theme"]
        if kept.get("accent", "blue") != "blue":
            opening += ' data-accent="%s"' % html.escape(kept["accent"], quote=True)
    if token:
        opening += ' data-token="%s"' % html.escape(token, quote=True)
    return page.replace(PAGE_OPENING, (opening + ">").encode("ascii"))


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
                 outside_world: Any = None, require_token: bool = False) -> None:
        self.store = store
        self.transport = transport
        self.update_transport: Optional[updater.UpdateTransport] = None
        # Whether this run may ask GitHub which releases there are (studio/updater.py). Off unless ``main`` turns
        # it on: a server made for a test, or for a trial of a package, never reaches out by itself.
        self.update_checks = False
        self.transcriber = transcriber if transcriber is not None else transcribe
        self.outside = outside_world if outside_world is not None else outside
        self.require_token = bool(require_token)
        self.token = secrets.token_urlsafe(32)
        self.session_cookie = secrets.token_urlsafe(32)
        self.token_path = Path(store.data_dir) / SERVER_TOKEN_FILE
        if self.require_token:
            self._write_token_file()
        # Files this run of the app put into the Downloads folder. Only these can be shown in the file manager
        # on request, so the page cannot make the app point at any other place on the computer.
        self.saved_files: List[str] = []
        # None, or what a trial of the desktop app is told and tells: see ``start_window_check``.
        self.window_check: Optional[Dict[str, Any]] = None
        self.jobs = Jobs()
        # Backing up everything and restoring a backup (studio/backup.py). Made here, at the start: it first finishes
        # undoing a restore the app was closed in the middle of. Every request to the API passes through it
        # (``Handler._gated_api``).
        self.backups = backup.Backups(store, self.jobs)
        # What the app does by itself, within what the creator chose (studio/autopilot.py). Made after ``backups``, so
        # a restore the app was closed in the middle of is put back first; a run is one of ``jobs`` and begins inside
        # the gate of ``backups``. It runs nothing unless ``autopilot_on`` is set, and only ``main`` sets it.
        self.autopilot = autopilot.Autopilot(store, self.jobs, self.backups, transport_of=lambda: self.transport,
                                             transcriber_of=lambda: self.transcriber)
        self.max_upload_bytes = MAX_UPLOAD_BYTES
        self.install_lock = threading.Lock()
        self.discover_lock = threading.Lock()
        self.uploads = Uploads(store.uploads_dir)
        self.uploads.clear_leftovers()

    @property
    def autopilot_on(self) -> bool:
        """Whether this run of the app may run tasks by itself at all (``Autopilot.on``; the creator's own switch is
        in the settings). Off unless ``main`` turns it on: a server made for a test, or for a trial of a package or
        of an installer, never changes anything by itself."""
        return self.autopilot.on

    @autopilot_on.setter
    def autopilot_on(self, value: bool) -> None:
        self.autopilot.on = bool(value)

    def _write_token_file(self) -> None:
        folder = Path(self.store.data_dir)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        _owner_only(folder)
        if self.token_path.is_symlink():
            raise OSError("Refusing to write server-token through a symbolic link: %s" % self.token_path)
        tmp = folder / ("%s.%s.tmp" % (SERVER_TOKEN_FILE, uuid.uuid4().hex))
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            set_mode = getattr(os, "fchmod", None)
            if set_mode is not None:
                set_mode(fd, 0o600)
            os.write(fd, (self.token + "\n").encode("ascii"))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            _remove(tmp)
            raise
        os.close(fd)
        try:
            if os.name != "nt":
                os.chmod(str(tmp), 0o600)
            _replace(str(tmp), str(self.token_path))
        except BaseException:
            _remove(tmp)
            raise
        _owner_only(self.token_path)

    def remove_token_file(self) -> None:
        """Remove ``data/server-token`` only when it still holds this server instance's own token."""
        if not self.require_token:
            return
        try:
            if self.token_path.is_symlink():
                return
            current = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if _tokens_equal(current, self.token):
            _remove(self.token_path)

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

    def _head(self, status: int, length: int, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        """Send the status line and the headers every answer carries. The body, ``length`` bytes of it, follows."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")                         # no page may show this app inside a frame
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        # Which data this answer is about. It changes when a backup is restored; a page that was open before then
        # learns here that what it shows is no longer what is stored (studio/backup.py, studio/web/backup.js).
        self.send_header(backup.EPOCH_HEADER, self.app.backups.epoch)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self._head(status, len(body), content_type, extra)
        self.wfile.write(body)

    def _send_file(self, path: Path, name: str) -> None:
        """Send the file at ``path`` as a download called ``name``, a piece at a time: it is never held in memory whole."""
        try:
            source = open(str(path), "rb")
        except OSError:
            raise ApiError(404, "not_found")
        with source:
            self._head(200, os.fstat(source.fileno()).st_size, "application/octet-stream",
                       {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
            shutil.copyfileobj(source, self.wfile, UPLOAD_CHUNK)

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

    def _cookie_name(self) -> str:
        return "%s_%d" % (SESSION_COOKIE_NAME, self.server.server_address[1])

    def _has_valid_header_token(self) -> Optional[bool]:
        vals = self.headers.get_all(TOKEN_HEADER)
        if not vals:
            return None
        if len(vals) != 1:
            return False
        return _tokens_equal(vals[0].strip(), self.app.token)

    def _has_valid_session_cookie(self) -> bool:
        """Linear O(n) Cookie splitter ignoring neighbor 127.0.0.1 cookies of any syntax."""
        raw_headers = self.headers.get_all("Cookie")
        if not raw_headers:
            return False
        raw = "; ".join(raw_headers)[:16384]
        wanted = self._cookie_name()
        for part in raw.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip() != wanted:
                continue
            cleaned = value.strip()
            if len(cleaned) >= 2 and len(cleaned) <= 256 and cleaned[0] == '"' and cleaned[-1] == '"' and "\\" not in cleaned:
                cleaned = cleaned[1:-1]
            if _tokens_equal(cleaned, self.app.session_cookie):
                return True
        return False

    def _is_public_static(self, method: str, path: str) -> bool:
        """True only for published static assets inside ``studio/web/`` (never ``/``, ``index.html``, or ``/api/*``)."""
        if method != "GET" or path in ("/", "", "/index.html") or path.startswith("/api/"):
            return False
        target = (WEB_DIR / path.lstrip("/")).resolve()
        return (
            WEB_DIR.resolve() in target.parents
            and target.is_file()
            and target.name != "index.html"
            and target.suffix.lower() in _PUBLIC_STATIC_SUFFIXES
        )

    def _send_unauthorized_page(self) -> None:
        self._drain_unread()
        body = page_with_keeps(UNAUTHORIZED_PAGE, self.app.store.load_page_keeps(), token="")
        self._send(401, body, "text/html; charset=utf-8")

    def _check_auth(self, method: str, path: str, raw_query: str = "") -> bool:
        """Enforce capability token & port-scoped session cookie; returns True when response was already sent."""
        header_ok = self._has_valid_header_token()
        if header_ok is False:
            raise ApiError(401, "unauthorized", "Missing or invalid studio token", "缺少或无效的工作台口令")
        if not self.app.require_token:
            return False
        if path.startswith("/api/"):
            if method != "GET":
                if header_ok is not True:
                    raise ApiError(401, "unauthorized", "Missing studio token", "缺少工作台口令")
            elif header_ok is not True and not self._has_valid_session_cookie():
                raise ApiError(401, "unauthorized", "Missing studio token", "缺少工作台口令")
            return False

        token_params = [v for k, v in urllib.parse.parse_qsl(raw_query, keep_blank_values=True) if k == "token"]
        if token_params:
            if method == "GET" and path in ("/", "") and len(token_params) == 1 and _tokens_equal(token_params[0], self.app.token):
                cookie = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (self._cookie_name(), self.app.session_cookie)
                self._static("/", extra_headers={"Set-Cookie": cookie})
                return True
            if method == "GET" and path in ("/", "", "/index.html") and header_ok is None:
                self._send_unauthorized_page()
                return True
            raise ApiError(401, "unauthorized", "Invalid launch token", "启动口令无效")

        if self._is_public_static(method, path):
            return False
        if method != "GET":
            if header_ok is True:
                return False
            raise ApiError(401, "unauthorized", "Missing studio token", "缺少工作台口令")

        # Subresource probes (fetch/iframe/script) from another port or site cannot read GET / using only the cookie.
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        fetch_mode = (self.headers.get("Sec-Fetch-Mode") or "").strip().lower()
        if header_ok is not True and fetch_site and fetch_site not in ("same-origin", "none") and fetch_mode not in ("", "navigate"):
            raise ApiError(403, "bad_origin")

        if header_ok is True or self._has_valid_session_cookie():
            return False
        if path in ("/", "", "/index.html") and header_ok is None:
            self._send_unauthorized_page()
            return True
        raise ApiError(401, "unauthorized", "Open Studio using the launch link printed at startup.",
                       "请使用启动时显示的访问链接打开工作台。")

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
                fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
                if fetch_site and fetch_site not in ("same-origin", "none"):
                    if method != "GET" or split.path.startswith("/api/"):
                        raise ApiError(403, "bad_origin")
                if self._check_auth(method, split.path, split.query):
                    return
                if split.path.startswith("/api/"):
                    self._gated_api(method, split.path)
                elif method == "GET":
                    self._static(split.path)
                else:
                    raise ApiError(405, "method_not_allowed")
            except TextTooLong as exc:
                self._drain_unread()
                payload = {"error": exc.code, "message": exc.message, "limit": exc.limit}
                if exc.message_zh:
                    payload["message_zh"] = exc.message_zh
                self._json(payload, 413)
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

    def _start_ai_video(self, body: Dict[str, Any]) -> None:
        """``POST /api/videos/ai``: ingest an uploaded video/audio file via Gemini multimodal or OpenAI Whisper."""
        app = self.app
        upload_id, title, caption = body.get("upload_id"), body.get("title"), body.get("caption")
        if not isinstance(upload_id, str) or not _UPLOAD_ID.fullmatch(upload_id):
            raise ApiError(400, "bad_upload_id")
        if title is not None and not isinstance(title, str):
            raise ApiError(400, "bad_title")
        if caption is not None and not isinstance(caption, str):
            raise ApiError(400, "bad_caption")
        raw_dur = body.get("duration_sec")
        duration_sec: Optional[float] = None
        if raw_dur is not None:
            if isinstance(raw_dur, bool) or not isinstance(raw_dur, (int, float)) or not math.isfinite(raw_dur) or raw_dur < 0:
                raise ApiError(400, "bad_duration")
            duration_sec = float(raw_dur)
        attach_to = body.get("attach_to")
        if attach_to is not None:
            _posting(lambda: posted.attachable(app.store, attach_to))
        app.uploads.drop_stale()
        upload = app.uploads.claim(upload_id)
        if upload is None:
            raise ApiError(404, "upload_not_found")
        clean_title = (title or "").strip() or upload["name"].rsplit(".", 1)[0]
        clean_caption = (caption or "").strip()[:2000]
        sponsored = body.get("is_sponsored") if isinstance(body.get("is_sponsored"), bool) else None
        try:
            if not video_ingest.ai_video_info(app.store)["ai_video_capable"]:
                msg_en, msg_zh = video_ingest.ai_video_refusal_messages(app.store)
                raise ApiError(400, "ai_video_not_configured", msg_en, msg_zh)
            job = app.jobs.start(
                TRANSCRIBE_JOB,
                lambda progress: video_ingest.transcribe_via_ai(
                    app.store,
                    upload,
                    title=clean_title,
                    caption=clean_caption,
                    is_sponsored=sponsored,
                    duration_sec=duration_sec,
                    attach_to=attach_to,
                    transport=app.transport,
                    progress=progress,
                ),
                label=upload["name"],
            )
        except BaseException:
            _remove(upload["path"])
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
                    upload={"max_bytes": app.max_upload_bytes, "extensions": list(MEDIA_EXTENSIONS)},
                    **video_ingest.ai_video_info(app.store))

    def _static(self, path: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "not_found")
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None:                       # a kind of file not named there: what the system says, as before
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
        body = target.read_bytes()
        if name == "index.html":
            body = page_with_keeps(
                body,
                self.app.store.load_page_keeps(),
                token=self.app.token if self.app.require_token else "",
            )
        self._send(200, body, ctype, extra=extra_headers)

    # ---- copy-and-paste mode ------------------------------------------------
    def _manual_prompt(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """``{"prompt"}``, plus ``brief_id`` for a draft: the record of what that prompt contains."""
        store, task = self.app.store, _task(body)
        if task == "sponsor":
            # The whole brief is in this prompt, with the creator profile and the voice profile. It goes wherever the
            # creator pastes it.
            system, user = _sponsoring(lambda: sponsored.read_prompt(store, body.get("sponsor_id")))
            return {"prompt": pipeline.single_prompt(system, user)}
        if task == "sponsor_draft":
            # What the creator chose goes into this prompt; the brief itself does not.
            built = _sponsoring(lambda: sponsored.draft_brief(
                store, body.get("sponsor_id"), target_sec=_target_sec(body), video_format=_video_format(body),
                replace=body.get("replace")))
            return {"prompt": pipeline.single_prompt(built["system"], built["user"]), "brief_id": built["brief_id"]}
        if task == "draft":
            if body.get("plan_id") is not None:
                try:
                    return planner.script_prompt(store, body.get("plan_id"))
                except planner.PlannerError as exc:
                    raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
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
        elif task == "video_ingest":
            raw_dur = body.get("duration_sec")
            dur = float(raw_dur) if (isinstance(raw_dur, (int, float)) and not isinstance(raw_dur, bool)
                                     and math.isfinite(raw_dur) and raw_dur > 0) else None
            system, user = video_ingest.video_ingest_prompt(
                title=str(body.get("title") or "")[:300],
                caption=str(body.get("caption") or "")[:2000],
                duration_sec=dur,
            )
        elif task == "sentiment":
            try:
                system, user, brief_id = rednote_analytics.build_sentiment_prompt(store, str(body.get("sample_id") or ""))
            except rednote_analytics.AnalyticsError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            return {"prompt": pipeline.single_prompt(system, user), "brief_id": brief_id}
        elif task == "comment_ocr":
            system, user = rednote_analytics.build_comment_screenshot_prompt()
            return {"prompt": pipeline.single_prompt(system, user)}
        else:
            system, user = pipeline.ideas_prompt(store, focus=_focus(body))
        return {"prompt": pipeline.single_prompt(system, user)}

    def _manual_reply(self, body: Dict[str, Any]) -> None:
        store, task = self.app.store, _task(body)
        if task == "comment_ocr":
            return self._json(_counting(lambda: rednote_analytics.apply_screenshot_ocr_reply(str(body.get("reply") or ""))))
        if task == "draft" and body.get("plan_id") is not None:
            # Copy and paste only. Nothing but the pasted reply and the record it answers is handed on, so this
            # route can neither ask the AI service for a draft (``use_ai``) nor make an empty script (no ``reply``),
            # whatever else the body holds. A missing or blank reply is refused (``reply_required``).
            pasted = {"reply": body.get("reply"), "brief_id": body.get("brief_id")}
            try:
                made = planner.make_script(store, body.get("plan_id"), pasted, default_title=UNTITLED[self._lang(body)])
            except planner.PlannerError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json(dict(made["script"], script=made["script"], plan=made["plan"], linked=made["linked"],
                                   planner=planner.payload(store)), 201)
        # For ideas: what was asked, and the lookup that came before. Malformed ones are refused before the reply is read.
        focus, lookup = (_focus(body), _pasted_lookup(body)) if task == "ideas" else ("", None)
        # A draft from a brief is read as leniently as any draft: a bare list of lines counts as its script_lines.
        result = llm.parse_manual_reply(str(body.get("reply") or ""), "draft" if task == "sponsor_draft" else task)
        if task == "sponsor":     # a reading, as on the direct route: nothing is chosen and nothing is drafted
            return self._json(_sponsoring(lambda: sponsored.propose(store, body.get("sponsor_id"), result)))
        if task == "sponsor_draft":
            script = _sponsoring(lambda: sponsored.apply_draft(
                store, body.get("sponsor_id"), result, target_sec=_target_sec(body), video_format=_video_format(body),
                default_title=UNTITLED[self._lang(body)], record_id=body.get("brief_id"), replace=body.get("replace")))
            return self._json(posted.with_posted(store, script), 201)
        if task == "draft":
            brief_id = body.get("brief_id")
            try:
                script = pipeline.apply_draft(
                    store, result, notes=str(body.get("notes") or "").strip(), target_sec=_target_sec(body),
                    video_format=_video_format(body), default_title=UNTITLED[self._lang(body)],
                    keywords=body.get("keywords"), brief_id=brief_id if isinstance(brief_id, str) else None)
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
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
        if task == "video_ingest":
            raw_dur = body.get("duration_sec")
            dur = float(raw_dur) if (isinstance(raw_dur, (int, float)) and not isinstance(raw_dur, bool)
                                     and math.isfinite(raw_dur) and raw_dur > 0) else None
            sponsored_override = body.get("is_sponsored") if isinstance(body.get("is_sponsored"), bool) else None
            source_name = body.get("source_name") if isinstance(body.get("source_name"), str) else ""
            try:
                sample = video_ingest.parse_stage1_reply(
                    store,
                    result,
                    title=str(body.get("title") or ""),
                    source_name=source_name,
                    is_sponsored_override=sponsored_override,
                    fallback_duration_sec=dur,
                )
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json({"sample": store.attach_beats_one(posted.shown(store, sample))}, 201)
        if task == "sentiment":
            try:
                saved = rednote_analytics.apply_sentiment_reply(store, str(body.get("sample_id") or ""), result, source="manual")
            except rednote_analytics.AnalyticsError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            return self._json(saved)
        ideas = pipeline.apply_ideas(store, result, focus=focus, lookup=lookup)
        return self._json({"performance": corpus.performance(store.list_samples()), "result": ideas})

    # ---- the plan (studio/planner.py) ------------------------------------------
    def _plans_api(self, method: str, path: str) -> None:
        """The plan of videos to make. Each route hands over to ``studio/planner.py``; ``planner.PlannerError`` is
        turned into the answer by the caller.

        - ``GET /api/plans``: everything the plan page shows (``planner.payload``). It changes nothing.
        - ``POST /api/plans``: add one plan. ``POST /api/plans/batch`` ``{"plans": [...]}``: add several, all or none.
        - ``PUT /api/plans/<id>``: change what the request names. ``DELETE /api/plans/<id>``: remove the plan.
        - ``POST /api/plans/<id>/prompt``: the draft prompt for the plan, for copy and paste. Nothing is sent.
        - ``POST /api/plans/<id>/draft``: make the plan's script and link the two: ``{"use_ai": true}`` through
          the AI service in Settings, ``{"reply", "brief_id"}`` from a pasted reply, or empty.
        - ``POST /api/plans/<id>/suggestions/<suggestion id>/take``: the creator takes a trend keyword that was
          suggested for the plan; it becomes one of the plan's keywords and nothing else about the plan changes.
          ``.../dismiss``: the creator dismisses it; the plan is not changed. Both hand over to
          ``studio/autosuggest.py``. A suggestion is made by autopilot; only these two requests decide one.

        Every answer that follows a change carries ``planner``: the page's data as it is afterwards.
        """
        store, app = self.app.store, self.app
        route = (method, path)
        if route == ("GET", "/api/plans"):
            return self._json(planner.payload(store))
        if route == ("POST", "/api/plans"):
            created = planner.create_plan_item(store, self._read_json())
            return self._json({"plan": created, "planner": planner.payload(store)}, 201)
        if route == ("POST", "/api/plans/batch"):
            created = planner.create_many(store, self._read_json())
            return self._json({"plans": created, "planner": planner.payload(store)}, 201)
        m = re.fullmatch(r"/api/plans/([0-9a-f]{12})", path)
        if m and method == "PUT":
            changed = planner.change(store, m.group(1), self._read_json())
            return self._json({"plan": changed, "planner": planner.payload(store)})
        if m and method == "DELETE":
            planner.delete(store, m.group(1))
            return self._json({"ok": True, "planner": planner.payload(store)})
        m = re.fullmatch(r"/api/plans/([0-9a-f]{12})/(prompt|draft)", path)
        if m and method == "POST":
            body = self._read_json()
            if m.group(2) == "prompt":
                return self._json(planner.script_prompt(store, m.group(1)))
            if body.get("use_ai") is True:
                self._require_ai()
            try:
                made = planner.make_script(store, m.group(1), body, transport=app.transport,
                                           default_title=UNTITLED[self._lang(body)])
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json(dict(made, planner=planner.payload(store)), 201)
        m = re.fullmatch(r"/api/plans/([0-9a-f]{12})/suggestions/([0-9a-f]{12})/(take|dismiss)", path)
        if m and method == "POST":
            self._read_json()
            decide = autosuggest.take if m.group(3) == "take" else autosuggest.dismiss
            return self._json({"plan": decide(store, m.group(1), m.group(2)), "planner": planner.payload(store)})
        raise ApiError(404, "not_found")
    # ---- a brand's brief for a sponsored video (studio/sponsored.py) ---------
    def _sponsored_api(self, method: str, path: str) -> None:
        """``/api/sponsored`` and what hangs below it, and ``/api/scripts/<id>/sponsor``. Each hands over to ``studio.sponsored``.

        - ``GET /api/sponsored``: the briefs that are kept, without their text. ``POST {"text"}`` or ``{"name",
          "file_base64"}`` (a .txt, .md or .docx file), with an optional ``title``: keep a brief. Nothing is sent.
        - ``GET /api/sponsored/<id>``: one brief with what the word list marks in it, what a model made of it and
          what the creator decided. ``DELETE``: delete it with all of that; answers with the list.
        - ``POST /api/sponsored/<id>/read {"service": {"provider", "model"}}``: send the brief, the creator profile
          and the voice profile to the AI service in settings and store the reply as a reading. ``service`` is the
          one the page named to the creator before sending; when the settings name another by now, nothing is sent.
        - ``PUT /api/sponsored/<id>/choice {"reading_at", "way", "requirements", "own"}``: the creator's decision.
          Nothing is sent and nothing is drafted.
        - ``POST /api/sponsored/<id>/draft {"service", "target_sec", "video_format", "replace", "lang"}``: write a
          draft from that decision as a new script, in sponsored mode; answers with the script (201).
        - ``GET /api/scripts/<id>/sponsor``: the list beside a script drafted from a brief, checked by rule.
          ``PUT /api/scripts/<id>/sponsor/ticks {"id", "ticked"}``: the creator's own tick on what no rule can check.

        Each is the creator's click in the app. There is deliberately no MCP tool for any of them. Every answer is
        bounded in size by the limits of ``studio.sponsored``, whatever a brief or a reply held.
        """
        store = self.app.store
        route = (method, path)
        if route == ("GET", "/api/sponsored"):
            return self._json(sponsored.listing(store))
        if route == ("POST", "/api/sponsored"):
            body = self._read_json()
            return self._json(_sponsoring(lambda: sponsored.add(store, body)), 201)
        m = re.fullmatch(r"/api/sponsored/([0-9a-f]{12})", path)
        if m and method == "GET":
            return self._json(_sponsoring(lambda: sponsored.payload(store, m.group(1))))
        if m and method == "DELETE":
            return self._json(_sponsoring(lambda: sponsored.remove(store, m.group(1))))
        m = re.fullmatch(r"/api/sponsored/([0-9a-f]{12})/(read|choice|draft)", path)
        if m and (method, m.group(2)) == ("POST", "read"):
            body = self._read_json()
            self._require_ai()
            return self._json(_sponsoring(lambda: sponsored.read(store, m.group(1), body.get("service"), self.app.transport)))
        if m and (method, m.group(2)) == ("PUT", "choice"):
            body = self._read_json()
            return self._json(_sponsoring(lambda: sponsored.choose(store, m.group(1), body)))
        if m and (method, m.group(2)) == ("POST", "draft"):
            body = self._read_json()
            self._require_ai()
            script = _sponsoring(lambda: sponsored.generate_draft(
                store, m.group(1), body.get("service"), self.app.transport, target_sec=_target_sec(body),
                video_format=_video_format(body), default_title=UNTITLED[self._lang(body)], replace=body.get("replace")))
            return self._json(posted.with_posted(store, script), 201)
        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/sponsor(/ticks)?", path)
        if m and method == "GET" and not m.group(2):
            return self._json(_sponsoring(lambda: sponsored.for_script(store, m.group(1))))
        if m and method == "PUT" and m.group(2):
            body = self._read_json()
            return self._json(_sponsoring(lambda: sponsored.tick(store, m.group(1), body)))
        raise ApiError(405 if m else 404, "method_not_allowed" if m else "not_found")

    # ---- backing up everything, and restoring a backup (studio/backup.py) ------
    def _gated_api(self, method: str, path: str) -> None:
        """Every request to ``/api/`` runs inside ``Backups.passage``.

        That is what lets a restore replace the data in one step: for that moment requests wait here. It is also
        where a change sent by a page that was open before a restore is refused: such a page names, in
        ``backup.EPOCH_HEADER``, data that is no longer there. A request that names none is served as it always was.
        """
        try:
            with self.app.backups.passage(method != "GET", self.headers.get(backup.EPOCH_HEADER)):
                self._api(method, path)
        except backup.Refused as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)

    def _receive_backup(self) -> Path:
        """Write the body of ``PUT /api/restore/file`` to a file in the app's backup folder and return where it is.

        Like a video it is written as it arrives and never held in memory, under a name made here. It has to
        state its size, and one over ``backup.MAX_BACKUP_BYTES`` is refused before a byte of it is read.
        """
        app = self.app
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            raise ApiError(415, "octet_stream_required")
        length = _stated_length(self.headers.get("Content-Length"))
        if self.headers.get("Transfer-Encoding") or length is None:
            raise ApiError(411, "length_required", "The upload must state its size", "上传时必须说明文件大小")
        if length == 0:
            raise ApiError(400, "empty_upload", "The file is empty", "这个文件是空的")
        if length > backup.MAX_BACKUP_BYTES:
            limit = "%d MB" % (backup.MAX_BACKUP_BYTES // (1024 * 1024))
            raise ApiError(413, "backup_too_large", "This file is larger than a backup can be (%s), so it was not read." % limit,
                           "这个文件超过了备份的大小上限（%s），所以没有读取。" % limit, extra={"limit": backup.MAX_BACKUP_BYTES})
        try:
            free = shutil.disk_usage(str(app.store.data_dir)).free
        except OSError:
            free = None
        if free is not None and length > free:
            raise ApiError(507, "disk_full", "There is not enough free space on this computer for the file",
                           "这台电脑的剩余空间放不下这个文件")
        target = app.backups.arriving()
        self.connection.settimeout(UPLOAD_IDLE_SEC)
        self._body_read = True
        received, whole = 0, False
        try:
            with os.fdopen(os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
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
            whole = True
        except OSError:
            raise ApiError(507, "upload_not_saved", "The file could not be saved on this computer",
                           "文件没能保存到这台电脑上")
        finally:
            if not whole:
                _remove(target)
        return target

    def _backup_api(self, method: str, path: str) -> None:
        """One file that holds everything the creator made or taught the app, and restoring such a file.

        - ``GET /api/backup``: what a backup made now would hold, the automatic backups, what was last restored.
        - ``POST /api/backup``: make a backup. The answer names it (``id``); ``GET /api/backup/<id>/file`` then
          sends it as a download. That is what a browser does.
        - ``POST /api/backup/save``: make a backup and put it where exports are put, the way they are: into the
          Downloads folder, under a name that is not taken, or beside the data folder when Downloads cannot be
          written to. The desktop app's page asks for this in place of a download. Answers with where it is.
        - ``PUT /api/restore/file``: the backup file is the body. It is checked as hostile input and nothing is
          replaced. Answers with ``restore_id``, what the file holds and what is here now.
        - ``POST /api/restore/automatic`` ``{"name"}``: the same for one of the automatic backups.
        - ``POST /api/restore`` ``{"restore_id"}``: replace everything with the checked backup, all of it or none.
        - ``DELETE /api/restore/<id>``: forget a checked backup that is not going to be restored.

        Each is the creator's click in the app. There is deliberately no MCP tool for any of them.
        """
        app = self.app
        backups = app.backups
        route = (method, path)
        if route == ("GET", "/api/backup"):
            return self._json(backups.info())
        if route == ("POST", "/api/backup"):
            return self._json(backups.make(), 201)
        if route == ("POST", "/api/backup/save"):
            made = backups.make()
            downloads = app.outside.downloads_folder()
            try:
                # The name is taken first, with nothing in the file, and the backup is then copied into it a piece
                # at a time: it may be large, and what puts a file into Downloads takes its content whole.
                saved = app.outside.save(made["name"], b"", [downloads, Path(app.store.data_dir).resolve().parent / "exports"])
                try:
                    shutil.copyfile(str(backups.made(made["id"])["path"]), str(saved))
                    if os.name == "posix":
                        os.chmod(str(saved), 0o600)         # the creator's whole material: not for the other accounts
                except BaseException:
                    _remove(saved)
                    raise
            except (OSError, TypeError) as exc:
                raise ApiError(507, "export_not_saved", "The file could not be saved on this computer: %s" % exc,
                               "文件没能存到这台电脑上：%s" % exc)
            finally:
                backups.forget_made(made["id"])
            app.remember_saved(saved)
            return self._json(dict(made, name=saved.name, folder=str(saved.parent), path=str(saved),
                                   in_downloads=saved.parent == Path(downloads)), 201)
        m = re.fullmatch(r"/api/backup/([0-9a-f]{12})/file", path)
        if m and method == "GET":
            found = backups.made(m.group(1))
            if found is None:
                raise ApiError(404, "not_found", "That backup is no longer waiting to be fetched. Make it again.",
                               "那份备份已经不在等待下载了，请重新备份一次。")
            return self._send_file(found["path"], found["name"])
        if route == ("PUT", "/api/restore/file"):
            return self._json(backups.stage(self._receive_backup()))
        if route == ("POST", "/api/restore/automatic"):
            return self._json(backups.stage_automatic(self._read_json().get("name")))
        if route == ("POST", "/api/restore"):
            return self._json(backups.restore(self._read_json().get("restore_id")))
        m = re.fullmatch(r"/api/restore/([0-9a-f]{12})", path)
        if m and method == "DELETE":
            return self._json({"ok": True, "discarded": backups.discard(m.group(1))})
        raise ApiError(404, "not_found")

    # ---- autopilot: what the app does by itself (studio/autopilot.py) ---------
    def _autopilot_api(self, method: str, path: str) -> None:
        """What the app does by itself while it is open, within what the creator chose. Each line hands over at once.

        - ``GET /api/autopilot``: both switches, the level, every task with its last and its next run, today's model
          calls and their limit, the run under way, the newest lines of the log (``Autopilot.status``).
        - ``POST /api/autopilot/settings`` ``{"enabled", "level", "daily_model_calls", "tasks": {name: bool}}``, any
          of them: change the creator's settings. Answers with the state as it then is.
        - ``GET /api/autopilot/sources`` / ``POST /api/autopilot/sources``: read or update background trend-source
          settings (``studio/trend_sources.py``).
        - ``POST /api/autopilot/run`` ``{"task"}``: run one task now. Answers at once (202) with ``{"run", "job"}``;
          the job is followed like any other (``GET /api/jobs/<id>``). Refused while either switch is off.
        - ``POST /api/autopilot/runs/<id>/undo``: reverse what one run changed, once.

        Each is the creator's click in the app. A refusal has a code and a sentence in English and in Chinese.
        """
        pilot = self.app.autopilot
        route = (method, path)
        try:
            if route == ("GET", "/api/autopilot"):
                return self._json(pilot.status())
            if route == ("POST", "/api/autopilot/settings"):
                return self._json(pilot.save_settings(self._read_json()))
            if route == ("GET", "/api/autopilot/sources"):
                return self._json(trend_sources.get_source_settings(self.app.store))
            if route == ("POST", "/api/autopilot/sources"):
                res = trend_sources.save_source_settings(self.app.store, self._read_json())
                pilot._wake.set()
                return self._json(res)
            if route == ("GET", "/api/autopilot/voice"):
                return self._json(autovoice.voice_payload(self.app.store, now=pilot.clock()))
            if route == ("POST", "/api/autopilot/voice"):
                res = autovoice.save_voice_settings(self.app.store, self._read_json(), now=pilot.clock())
                pilot._wake.set()
                return self._json(res)
            if route == ("POST", "/api/autopilot/run"):
                return self._json(pilot.run_now(self._read_json().get("task")), 202)
            m = re.fullmatch(r"/api/autopilot/runs/([0-9a-f]{12})/undo", path)
            if m and method == "POST":
                return self._json(pilot.undo(m.group(1)))
        except (autopilot.AutopilotError, trend_sources.SourceError, autovoice.VoiceAutoError) as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
        raise ApiError(404, "not_found")

    # ---- api ----------------------------------------------------------------
    def _api(self, method: str, path: str) -> None:
        app = self.app
        store = app.store
        route = (method, path)
        if method == "POST":
            self._read_json()       # every POST must be JSON, also one that needs nothing from its body
        if path == "/api/backup" or path.startswith(("/api/backup/", "/api/restore")):
            return self._backup_api(method, path)       # one file that holds everything, and restoring one
        if path == "/api/autopilot" or path.startswith("/api/autopilot/"):
            return self._autopilot_api(method, path)    # what the app does by itself, and taking it back
        if path == "/api/sponsored" or path.startswith("/api/sponsored/") or re.fullmatch(r"/api/scripts/[0-9a-f]{12}/sponsor(/ticks)?", path):
            return self._sponsored_api(method, path)    # a brand's brief, and the list beside a script drafted from it

        if route == ("GET", "/api/state"):
            _, is_example = store.load_dna()
            state = {
                "settings": store.public_settings(), "dna_is_example": is_example,
                "creator": creator.for_page(store.load_creator()),
                "video_formats": list(pipeline.VIDEO_FORMATS), "sample_count": store.samples_count(),
                "trend_count": store.trends_count(), "plan_count": store.plans_count(),
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

        if route == ("POST", "/api/kept"):
            # {name: value, ...}: what the page wants kept until the next run (``store.PAGE_WORDS``, ``PAGE_NUMBERS``).
            # One name or value the page may not keep refuses the whole request, and nothing is kept.
            changes: Dict[str, str] = {}
            for name, value in self._read_json().items():
                text = kept_value(name, value)
                if text is None:
                    raise ApiError(400, "bad_value", "This cannot be kept: %s" % str(name)[:40])
                changes[name] = text
            return self._json({"kept": store.keep_for_page(changes) if changes else store.load_page_keeps()})

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
            try:
                script = pipeline.generate_script(
                    store, notes=notes, target_sec=_target_sec(body), video_format=_video_format(body),
                    transport=self.app.transport, default_title=UNTITLED[self._lang(body)],
                    keywords=body.get("keywords"))
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json(script, 201)
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
                raw_model = str(body.get("model") or "").strip()
                try:
                    model = llm.validate_model_name(raw_model, provider) if raw_model else ""
                except llm.LLMError as exc:
                    raise ApiError(400, exc.code, str(exc), exc.message_zh)

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
            return self._json({"samples": store.attach_beats(posted.for_page(store, samples)), "stats": stats,
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
                    return self._json(store.attach_beats_one(posted.shown(store, _posting(lambda: posted.attach_transcript(
                        store, body["attach_to"], text=text, kind=kind, timing=measured_timing, source_name=source_name)))))
                sample = store.create_sample(
                    title=str(body.get("title") or ""), text=text,
                    is_sponsored=bool(body.get("is_sponsored")), metrics=body, kind=kind, timing=measured_timing,
                    source_name=source_name)
            except TextTooLong as exc:
                raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            return self._json(store.attach_beats_one(posted.shown(store, sample)), 201)
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
            # ``automatic``: what autopilot's refresh did (autosuggest.ideas_state): whether the saved ideas are a set
            # it made, a set that waits for the creator, and when it last made ideas and from which trends.
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": store.load_recommendations(),
                               "discover": app.jobs.latest(DISCOVER_JOB),
                               "automatic": autosuggest.ideas_state(store)})
        m = re.fullmatch(r"/api/recommendations/automatic/([0-9a-f]{12})/(take|dismiss)", path)
        if m and method == "POST":
            # The creator's click on a set of ideas an automatic refresh left waiting. ``take`` makes it the saved
            # ideas, in place of those saved now, as "Find ideas" does; ``dismiss`` leaves the saved ideas alone.
            self._read_json()
            try:
                state = (autosuggest.take_ideas if m.group(2) == "take" else autosuggest.dismiss_ideas)(store, m.group(1))
            except autosuggest.Refused as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            return self._json({"performance": corpus.performance(store.list_samples()),
                               "result": store.load_recommendations(),
                               "discover": app.jobs.latest(DISCOVER_JOB), "automatic": state})
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
        if route == ("POST", "/api/videos/ai"):
            return self._start_ai_video(self._read_json())
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
                if str(exc) == "sponsored_script":
                    raise ApiError(409, "sponsored_script",
                                   "This script was drafted from a brand's brief, so a change made in it is not remembered "
                                   "as a lesson about how the creator words things: wording a brand asked for would then go "
                                   "into every later draft.",
                                   "这篇脚本是照品牌 Brief 写的，所以这里的改法不会记成你自己的用词习惯：不然品牌要求的说法"
                                   "会被带进以后的每一篇初稿。")
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
                    return self._json(store.attach_beats_one(posted.shown(store, updated)) if updated is not None else None)
                except TextTooLong as exc:
                    raise ApiError(413, exc.code, exc.message, exc.message_zh, extra={"limit": exc.limit})
            if method == "DELETE":
                store.delete_sample(m.group(1))
                return self._json({"ok": True})

        if route == ("GET", "/api/hooks"):
            # The creator's own openings with their videos' results. Counted in code; no model is asked.
            return self._json(hooks.library(store.list_samples()))

        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/refs(/search)?", path)
        if m and method == "GET":
            # The creator's own material beside this script: what is pinned to it, what its draft saw and what is
            # on the same topic (``refs.for_script``); under /search, with ?q=, what a search of the library finds.
            # Reading only. Everything is counted in code: no model is asked, and each answer is bounded in size.
            if m.group(2):
                return self._json(_referring(lambda: refs.search(store, m.group(1), (self.query.get("q") or [""])[0])))
            return self._json(_referring(lambda: refs.for_script(store, m.group(1))))
        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/pins", path)
        if m and method == "PUT":
            # {"sample_id": the id of a library item, "pinned": true or false}. Answers with what the panel shows,
            # as the GET above does. The script itself is not written to, so its updated_at stays as it is and a
            # save made from the version before the pin is not refused.
            body = self._read_json()
            return self._json(_referring(lambda: refs.set_pin(store, m.group(1), body.get("sample_id"), body.get("pinned"))))
        m = re.fullmatch(r"/api/samples/([0-9a-f]{12})/text", path)
        if m and method == "GET":
            # One part of an item's whole text, from ?start= (a count of characters), to be read beside a script.
            return self._json(_referring(lambda: refs.text_part(store, m.group(1), (self.query.get("start") or ["0"])[0])))

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

        # ---- auto-updater & cross-version settings persistence --------------
        # ``app.update_checks`` goes with every call: the module itself asks GitHub nothing while it is off, and the
        # answer tells the page so (``checks.on``).
        if route == ("GET", "/api/update"):
            force = self.query.get("force") == ["1"]
            scan = self.query.get("scan") == ["1"]
            try:
                if force or self.query.get("check") == ["1"]:
                    return self._json(updater.check_for_updates(
                        store, checks_on=app.update_checks, force=force, scan_previous=scan,
                        transport=app.update_transport))
                return self._json(updater.status_payload(store, checks_on=app.update_checks, scan_previous=scan))
            except updater.UpdateError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
        if route == ("POST", "/api/update/settings"):
            try:
                return self._json(updater.save_update_settings(store, self._read_json(), checks_on=app.update_checks))
            except updater.UpdateError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
        if route == ("POST", "/api/update/download"):
            try:
                res = updater.download_release_asset(store, app.outside, checks_on=app.update_checks,
                                                     transport=app.update_transport)
            except updater.UpdateError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
            app.remember_saved(Path(res["saved"]["path"]))
            return self._json(res, 201)
        if route == ("POST", "/api/update/migrate"):
            try:
                res = updater.migrate_from_named_folder(store, self._read_json().get("source_path"),
                                                        backups=app.backups, jobs=app.jobs)
            except updater.UpdateError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
            return self._json(res)
        if route == ("POST", "/api/update/apply-zip"):
            raw_zip = self._read_json().get("zip_path")
            wanted_zip = raw_zip.strip() if isinstance(raw_zip, str) else ""
            if not wanted_zip or wanted_zip not in app.saved_files:
                raise ApiError(404, "not_found", "Download the update package in the app first.",
                               "请先在应用内下载新版压缩包。")
            try:
                res = updater.unpack_and_migrate_zip(store, Path(wanted_zip), backups=app.backups, jobs=app.jobs)
            except updater.UpdateError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
            return self._json(res, 201)

        # ---- numbers by day, a table from the Creator Center, comments (studio/rednote_analytics.py) ----
        if route == ("GET", "/api/rednote/overview"):
            return self._json(rednote_analytics.analytics_overview(store))
        if route == ("POST", "/api/rednote/import/preview"):
            # A table the creator saved from the Creator Center: {"name", "text" or "file_base64"} and, once the
            # creator has picked them, "fields". Looking stores nothing.
            return self._json(_counting(lambda: rednote_analytics.preview(store, self._read_json())))
        if route == ("POST", "/api/rednote/import"):
            # The same file, the columns, and the "fingerprint" of the preview: exactly what that preview showed is stored.
            return self._json(_counting(lambda: rednote_analytics.bring_in(store, self._read_json())), 201)
        if route == ("POST", "/api/rednote/comments/ocr"):
            self._require_ai()
            return self._json(_counting(lambda: rednote_analytics.read_comments_from_screenshots_ai(
                store, self._read_json(), transport=app.transport)))

        m = re.fullmatch(r"/api/samples/([0-9a-f]{12})/posted-on", path)
        if m and method == "PUT":
            body = self._read_json()
            return self._json(_counting(lambda: rednote_analytics.set_sample_posted_on(store, m.group(1), body.get("posted_on"))))

        m = re.fullmatch(r"/api/samples/([0-9a-f]{12})/comments", path)
        if m:
            sid = m.group(1)
            sample = store.get_sample(sid)
            if sample is None:
                raise ApiError(404, "not_found")
            if method == "GET":
                comments = store.list_sample_comments(sid)
                sent = store.get_sample_sentiment(sid)
                snaps = store.list_metric_snapshots(sid)
                return self._json({
                    "sample_id": sid,
                    "posted_on": sample.get("posted_on"),
                    "from_script": sample.get("origin") == rednote_analytics.ORIGIN_SCRIPT,
                    "comments": comments,
                    "sentiment": sent["summary"] if sent else rednote_analytics.build_local_sentiment_summary(sample, comments),
                    "sentiment_source": sent["source"] if sent else "local",
                    "per_day": rednote_analytics.views_per_day(sample, snaps),
                })
            if method == "POST":
                body = self._read_json()
                try:
                    out = rednote_analytics.ingest_comments_for_sample(
                        store, sid, str(body.get("text") or ""), replace=bool(body.get("replace"))
                    )
                except rednote_analytics.AnalyticsError as exc:
                    raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
                return self._json(out, 201)
            if method == "DELETE":
                removed = store.clear_sample_comments(sid)
                return self._json({"ok": True, "removed": removed})

        m = re.fullmatch(r"/api/samples/([0-9a-f]{12})/sentiment/ai", path)
        if m and method == "POST":
            sid = m.group(1)
            self._require_ai()
            try:
                out = rednote_analytics.analyze_sample_sentiment_ai(store, sid, transport=app.transport)
            except rednote_analytics.AnalyticsError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)
            return self._json(out)

        # ---- the plan: videos the creator means to make (studio/planner.py) ----
        # GET /api/plans reads and writes nothing. Every refusal is the planner's, in both languages.
        if path == "/api/plans" or path.startswith("/api/plans/"):
            try:
                return self._plans_api(method, path)
            except planner.PlannerError as exc:
                raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)

        # ---- full-service voice & tone analyzer -----------------------------
        if route == ("GET", "/api/tone"):
            return self._json(tone_analyzer.corpus_tone_profile(store))
        m = re.fullmatch(r"/api/scripts/([0-9a-f]{12})/tone", path)
        if m and method == "GET":
            script = store.get_script(m.group(1))
            if script is None:
                raise ApiError(404, "not_found")
            return self._json(tone_analyzer.compare_script_tone(store, script))

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


def _counting(work: Any) -> Any:
    """Run a step about numbers or comments, turning its refusals (``rednote_analytics.AnalyticsError``) into answers
    a page can show, in both languages."""
    try:
        return work()
    except rednote_analytics.AnalyticsError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)


def _referring(work: Any) -> Any:
    """Run a step about the references beside a script, turning its refusals (``refs.RefsError``) into answers a
    page can show, in both languages."""
    try:
        return work()
    except refs.RefsError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.message_zh)


def _sponsoring(work: Any) -> Any:
    """Run a step about a brand's brief, turning its refusals (``sponsored.SponsorError``) into answers a page can
    show, in both languages."""
    try:
        return work()
    except sponsored.SponsorError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.message_zh, extra=exc.extra)
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
    transcriber left out because it could not make them out, so the page can say so. ``timing.sound`` is
    what the transcriber measured from the sound it extracted (``soundstats.measure``: pauses, loudness
    range, pitch), checked before it is kept; the upload itself is a video or a compressed recording and
    is never measured here.

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
        sound = soundstats.clean(result.get("sound"))
        if sound is not None:
            measured["sound"] = sound
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
        addr = getattr(self, "server_address", None)
        if isinstance(addr, tuple) and len(addr) >= 2 and addr[1] == 0:
            # Port 0 asks the kernel for a free ephemeral port; without SO_REUSEADDR it avoids ports still in TIME_WAIT.
            self.allow_reuse_address = False
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)        # Windows only
        if os.name == "nt" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        if ThreadingHTTPServer.server_bind is not HTTPServer.server_bind:
            super().server_bind()
            return
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host or "127.0.0.1")
        self.server_port = int(port)

    def server_close(self) -> None:
        # Also reached from the constructor when the port is taken, before ``app`` exists.
        if self.app is not None:
            self.app.autopilot.stop()       # the scheduler starts nothing more
            self.app.uploads.close()        # uploads still waiting are not kept once the app has closed
            self.app.backups.close()        # nor is a backup that was made or checked and then not fetched or restored
            self.app.remove_token_file()
        super().server_close()


def make_server(port: int, store: Store, transport: Optional[llm.Transport] = None,
                transcriber: Any = None, outside_world: Any = None,
                require_token: bool = False) -> StudioServer:
    """A server on 127.0.0.1. ``transport``, ``transcriber`` and ``outside_world`` stand in, in tests, for the
    model, for Whisper, and for the Downloads folder, the file manager and the browser."""
    server = StudioServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    try:
        server.app = App(store, transport, transcriber, outside_world, require_token=require_token)
    except BaseException:
        server.server_close()
        raise
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
    parser.add_argument("--no-auth-token", action="store_true",
                        help="disable per-launch local capability token check")
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
            candidate = StudioServer(("127.0.0.1", port), Handler)
            candidate.daemon_threads = True
            server = candidate
            break
        except OSError:
            continue
    if server is None:
        print("无法启动：端口 %d–%d 都被占用。/ Could not start: ports %d-%d are all in use."
              % (args.port, args.port + 9, args.port, args.port + 9), file=sys.stderr, flush=True)
        return 1
    try:
        server.app = App(store, require_token=not args.no_auth_token)
    except OSError as exc:
        server.server_close()
        print("无法启动：无法写入工作台数据文件（%s）。/ Could not start: could not write studio data file (%s)."
              % (exc, exc), file=sys.stderr, flush=True)
        return 1
    if os.environ.get(WINDOW_CHECK_ENV) == "1":
        server.app.start_window_check()
    # Asking GitHub for newer releases is for a run a person started: not for a trial of a package or of an
    # installer (which sets NO_UPDATE_CHECK_ENV or the window check), and never for a server a test made.
    server.app.update_checks = os.environ.get(NO_UPDATE_CHECK_ENV) != "1" and server.app.window_check is None
    # The same for what the app does by itself (studio/autopilot.py): only in a run a person started, and then only
    # what the creator switched on in the settings. The scheduler is started here and nowhere else.
    server.app.autopilot_on = os.environ.get(NO_AUTOPILOT_ENV) != "1" and server.app.window_check is None
    server.app.autopilot.start()            # starts nothing when the line above said no
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    launch_url = "%s?token=%s" % (url, server.app.token) if server.app.require_token else url
    # Sent at once also when the output is not a window: whoever started the app is waiting for this address
    # (line 1 keeps the exact ``http://127.0.0.1:<port>/`` format expected by the desktop shell).
    print("工作台已启动 / Studio running: %s\n安全访问链接 / Launch link: %s\n关闭这个窗口即可退出。/ Close this window to quit."
          % (url, launch_url), flush=True)
    if not args.no_browser:
        webbrowser.open(launch_url)
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
