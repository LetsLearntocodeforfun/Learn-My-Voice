"""Minimal stdlib clients for cloud providers and local model servers.

Providers:
- ``gemini`` / ``openai``: cloud, need an API key.
- ``local``: any OpenAI-compatible server on this machine (Ollama, LM Studio,
  llama.cpp, mlx-lm). No key; nothing leaves the computer.

Model names are never hard-coded: the settings screen lists what is available
and the user picks one. A ``transport`` callable can be injected for tests.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

Transport = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Dict[str, Any]]

OPENAI_BASE = "https://api.openai.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
SEARCH_PROVIDERS = ("gemini", "openai")     # the providers whose API can search the web while answering
GEMINI_API_REVISION = "2026-05-20"          # the revision of Gemini's Interactions API that ``search_json`` is written for
DEFAULT_LOCAL_BASE = "http://127.0.0.1:11434/v1"  # Ollama's OpenAI-compatible endpoint
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_NON_CHAT_HINTS = ("embedding", "whisper", "tts", "dall-e", "moderation", "image", "audio",
                   "realtime", "transcribe", "babbage", "davinci", "sora")
# How much of a provider's answer is read. A script or a voice profile is a few thousand bytes and the longest
# model list a few hundred thousand; a broken or hostile server can send without end.
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024         # of an error answer; 300 characters of it are shown
# Opens addresses on this computer with no proxy at all, whatever the environment or the system settings say,
# and intercepts all HTTP 3xx responses so ``_http`` decides whether a loopback redirect may be followed.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are refused", headers, fp)

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_MAX_LOCAL_REDIRECTS = 3
MAX_LOCAL_MODEL_CHARS = 500
MAX_CLOUD_MODEL_CHARS = 200
_SECRET_HEADERS = ("authorization", "x-goog-api-key", "x-api-key", "proxy-authorization")
_KEY_PATTERNS = (
    re.compile(r"sk-(?:proj|ant|svcacct|admin|live|test|or)-[A-Za-z0-9_-]{6,}"),
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{12,}"),
)


class LLMError(Exception):
    """An AI step failed.

    ``code`` is a stable identifier the UI translates (Chinese / English);
    the message is an English detail that is safe to show, and ``message_zh``
    the same in Chinese when the error was written with one.
    Codes: not_configured, manual_mode, local_address, bad_model, http, network,
    bad_response, bad_json, empty_result, no_samples, unknown_provider,
    cannot_browse, no_focus, too_large, other.
    """

    def __init__(self, message: str, code: str = "other", message_zh: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or not ch.isprintable() for ch in text)


def _gemini_model_id(model: str) -> str:
    """Validate a Gemini model identifier used as a URL path segment (accepting optional ``models/`` prefix)."""
    cleaned = (model or "").strip()
    if not cleaned:
        raise LLMError("Gemini model name cannot be empty", "bad_model", "Gemini 模型名称不能为空")
    if len(cleaned) > MAX_CLOUD_MODEL_CHARS:
        raise LLMError("Gemini model name is too long", "bad_model", "Gemini 模型名称太长")
    if _has_control_chars(cleaned) or any(ch.isspace() for ch in cleaned):
        raise LLMError("Gemini model name cannot contain spaces or control characters", "bad_model",
                       "Gemini 模型名称不能包含空格或控制字符")
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/"):]
    if not cleaned or cleaned in (".", "..") or ".." in cleaned or any(ch in "/?#\\" for ch in cleaned):
        raise LLMError("Invalid Gemini model name", "bad_model", "Gemini 模型名称无效")
    return cleaned


def _gemini_model_segment(model: str) -> str:
    return urllib.parse.quote(_gemini_model_id(model), safe="._-")


def validate_model_name(model: str, provider: str = "local") -> str:
    r"""Validate a model identifier according to how ``provider`` transmits it.

    - ``gemini``: placed into a URL path segment, so ``/``, ``?``, ``#``, ``\``, ``..`` and spaces are refused.
    - ``openai``: placed into a JSON request body; printable non-whitespace IDs up to 200 chars without
      ``?``, ``#``, ``\`` or ``..`` are accepted (including fine-tune IDs with colons and ``@``).
    - ``local`` (default): placed only into a JSON body sent to a loopback server; any printable non-control
      characters up to 500 chars are accepted so file paths (``../models/x.gguf``, ``C:\models\x.gguf``),
      spaces (``Llama 3 8B``), brackets, ``+``, and CJK names work as reported by the local server.
    """
    cleaned = (model or "").strip()
    if not cleaned:
        raise LLMError("Model name cannot be empty", "bad_model", "模型名称不能为空")
    if _has_control_chars(cleaned):
        raise LLMError("Model name cannot contain line breaks or control characters", "bad_model",
                       "模型名称不能包含换行或控制字符")
    if provider == "gemini":
        _gemini_model_id(cleaned)
        return cleaned
    if provider == "openai":
        if len(cleaned) > MAX_CLOUD_MODEL_CHARS:
            raise LLMError("Model name is too long (limit %d characters)" % MAX_CLOUD_MODEL_CHARS, "bad_model",
                           "模型名称太长（上限 %d 个字符）" % MAX_CLOUD_MODEL_CHARS)
        if any(ch.isspace() or ch in "?#\\" for ch in cleaned) or ".." in cleaned:
            raise LLMError("Invalid OpenAI model name", "bad_model", "OpenAI 模型名称格式不正确")
        return cleaned
    if len(cleaned) > MAX_LOCAL_MODEL_CHARS:
        raise LLMError("Model name is too long (limit %d characters)" % MAX_LOCAL_MODEL_CHARS, "bad_model",
                       "模型名称太长（上限 %d 个字符）" % MAX_LOCAL_MODEL_CHARS)
    return cleaned


def normalize_local_base(url: str) -> str:
    """Validate a local server URL: http(s) on a loopback host only, without credentials, query or fragment."""
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_LOCAL_BASE
    if _has_control_chars(raw) or any(ch.isspace() for ch in raw) or "\\" in raw:
        raise LLMError("Local model address cannot contain spaces, backslashes, or control characters", "local_address",
                       "本地模型地址不能包含空格、反斜杠或控制字符")
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        raise LLMError("Local model address is not a valid URL", "local_address", "本地模型地址格式不正确")
    if parts.scheme not in ("http", "https"):
        raise LLMError("Local model address must start with http:// or https://", "local_address",
                       "本地模型地址必须以 http:// 或 https:// 开头")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise LLMError("Local model address cannot include a username or password", "local_address",
                       "本地模型地址不能包含用户名或密码")
    try:
        port = parts.port
    except ValueError:
        raise LLMError("Local model address has an invalid port number (must be 1–65535)", "local_address",
                       "本地模型地址里的端口号无效（应在 1 到 65535 之间）")
    if parts.netloc.endswith(":") or (port is not None and not (1 <= port <= 65535)):
        raise LLMError("Local model address has an invalid port number (must be 1–65535)", "local_address",
                       "本地模型地址里的端口号无效（应在 1 到 65535 之间）")
    if parts.hostname not in _LOOPBACK_HOSTS:
        raise LLMError("Local model address must be on this computer (127.0.0.1, localhost, or [::1])", "local_address",
                       "本地模型地址必须在这台电脑上（127.0.0.1、localhost 或 [::1]）")
    if "?" in raw or "#" in raw or parts.query or parts.fragment:
        raise LLMError("Local model address cannot include a '?' query or '#' fragment", "local_address",
                       "本地模型地址不能包含 ? 查询参数或 # 片段")
    unquoted_segments = urllib.parse.unquote(parts.path).split("/")
    if ".." in unquoted_segments or "\\" in urllib.parse.unquote(parts.path):
        raise LLMError("Local model address cannot contain '..'", "local_address",
                       "本地模型地址不能包含 ..")
    return raw.rstrip("/")


class _NonFiniteJSON(ValueError):
    """Raised when JSON contains NaN, Infinity, -Infinity, or overflow floats."""


def _refuse_constant(name: str) -> Any:
    raise _NonFiniteJSON("%s is not valid JSON" % name)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _NonFiniteJSON("a number too large to read: %s" % text[:40])
    return value


_LOOSE_DECODER = json.JSONDecoder(parse_constant=_refuse_constant, parse_float=_finite_float, strict=False)
_EDGE_INVISIBLE = " \t\r\n\ufeff\u200b\u200c\u200d\u2060"
_INVISIBLE_OUTSIDE_STR = frozenset("\ufeff\u200b\u200c\u200d\u2060")
_ARRAY_TASK_KEYS = {
    "draft": "script_lines",
    "ideas": "ideas",
    "trends": "keywords",
    "suggest": "suggestions",
    "video_ingest": "beats",
}
_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
_SURROGATE_CHAR = re.compile(r"[\ud800-\udfff]")


def _has_surrogate(value: Any) -> bool:
    stack = [value]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            if _SURROGATE_CHAR.search(cur):
                return True
        elif isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str) and _SURROGATE_CHAR.search(k):
                    return True
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


MAX_JSON_DEPTH = 64


def _raw_json_too_deep(text: str, limit: int = MAX_JSON_DEPTH) -> bool:
    """Linear O(n) nesting check outside JSON string literals."""
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in ("{", "["):
                depth += 1
                if depth > limit:
                    return True
            elif ch in ("}", "]") and depth > 0:
                depth -= 1
    return False


def _value_too_deep(value: Any, limit: int = MAX_JSON_DEPTH) -> bool:
    """Iterative depth check on a decoded JSON value (no Python recursion)."""
    if not isinstance(value, (dict, list)):
        return False
    stack: List[Tuple[Any, int]] = [(value, 1)]
    while stack:
        cur, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
    return False


def loads_strict(text: str) -> Any:
    """Read JSON that arrives from outside: a model's reply, a provider's answer, a request body.

    Python's reader accepts ``NaN``, ``Infinity`` and ``-Infinity``, which are not JSON. Stored and sent back
    to the page they make an answer a browser cannot read, so the page shows nothing at all. Here they
    raise ``ValueError`` like any other invalid JSON, and so does a number written so large that Python
    would read it as Infinity (``1e999``), nested beyond ``MAX_JSON_DEPTH``, or holding an unpaired UTF-16
    surrogate that cannot be encoded as UTF-8.
    """
    if not isinstance(text, str):
        raise ValueError("JSON input must be text")
    if _raw_json_too_deep(text):
        raise ValueError("JSON nested too deeply")
    try:
        value = json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except RecursionError:
        raise ValueError("JSON nested too deeply")
    if _value_too_deep(value):
        raise ValueError("JSON nested too deeply")
    if _SURROGATE_CHAR.search(text) or (_SURROGATE_ESCAPE.search(text) and _has_surrogate(value)):
        raise ValueError("JSON contains an unpaired surrogate")
    return value


def _has_secret_header(headers: Optional[Dict[str, str]]) -> bool:
    for name, val in (headers or {}).items():
        if str(name).strip().lower() in _SECRET_HEADERS and str(val or "").strip():
            return True
    return False


def _redact_secrets(text: str, headers: Optional[Dict[str, str]] = None) -> str:
    """Scrub API keys from upstream error messages before returning or logging them.

    Only sensitive credential headers (``Authorization``, ``x-goog-api-key``, ``x-api-key``,
    ``Proxy-Authorization``) are treated as secrets so ordinary headers such as ``Api-Revision``
    never corrupt normal error text.
    """
    out = str(text or "")
    for header_name, header_val in (headers or {}).items():
        if str(header_name).strip().lower() not in _SECRET_HEADERS:
            continue
        raw = str(header_val or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if len(raw) >= 8:
            out = re.sub(re.escape(raw), "[REDACTED]", out, flags=re.IGNORECASE)
        elif len(raw) >= 3:
            out = out.replace(raw, "[REDACTED]")
    for pattern in _KEY_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _is_loopback_target(url: str) -> bool:
    """True when ``url`` is a clean http(s) loopback URL without credentials, fragments, or traversal."""
    if not url or _has_control_chars(url) or any(ch.isspace() for ch in url) or "\\" in url or "#" in url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if (parts.scheme not in ("http", "https")
            or parts.hostname not in _LOOPBACK_HOSTS
            or parts.username is not None or parts.password is not None
            or "@" in parts.netloc or parts.netloc.endswith(":")
            or (port is not None and not (1 <= port <= 65535))):
        return False
    unquoted = urllib.parse.unquote(parts.path)
    return ".." not in unquoted.split("/") and "\\" not in unquoted


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Open a request to a provider. An address on this computer is never reached through a proxy.

    ``urlopen`` follows the ``http_proxy`` variable and the system's proxy settings also for 127.0.0.1, which
    hands the whole prompt to the proxy. With a local model nothing may leave the computer, so those
    requests go straight to the address. Cloud providers keep using a configured proxy: on some networks
    it is the only way out. Automatic redirects are disabled on both openers; ``_http`` handles bounded
    loopback-to-loopback redirects explicitly.
    """
    try:
        local = urllib.parse.urlsplit(request.full_url).hostname in _LOOPBACK_HOSTS
    except ValueError:
        local = False
    if local:
        return _DIRECT.open(request, timeout=timeout)
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _refuse_redirect(code: int, raw_location: str, headers: Dict[str, str], too_many: bool = False) -> LLMError:
    cleaned = "".join(ch for ch in (raw_location or "").strip() if ch.isprintable())
    shown = _redact_secrets(cleaned, headers)[:200]
    if too_many:
        target = shown or "unknown"
        return LLMError(
            "HTTP %s: redirected more than %d times (last target: %s). Check the address in Settings."
            % (code, _MAX_LOCAL_REDIRECTS, target),
            "http",
            "HTTP %s：连续重定向超过 %d 次（最后指向 %s）。请到「设置」里检查地址。"
            % (code, _MAX_LOCAL_REDIRECTS, target),
        )
    if shown:
        return LLMError(
            "HTTP %s: the service answered with a redirect to %s, which was not followed. "
            "If that is the right address, put it into Settings." % (code, shown),
            "http",
            "HTTP %s：服务返回了指向 %s 的跳转（重定向），出于安全原因没有自动跳转。"
            "如果那是正确的地址，请直接填到「设置」里。" % (code, shown),
        )
    return LLMError(
        "HTTP %s: the service answered with a redirect without a target address, which was not followed." % code,
        "http",
        "HTTP %s：服务返回了没有目标地址的重定向，未自动跟随。" % code,
    )


def _send_raw(method: str, url: str, headers: Dict[str, str], data: Optional[bytes],
              content_type: Optional[str]) -> Dict[str, Any]:
    cur_method = method
    cur_url = url
    cur_data = data
    cur_ctype = content_type
    allow_local_redirect = _is_loopback_target(url) and not _has_secret_header(headers)
    redirects = 0

    while True:
        req_headers = dict(headers)
        if cur_data is not None and cur_ctype:
            req_headers["Content-Type"] = cur_ctype
        request = urllib.request.Request(cur_url, data=cur_data, method=cur_method, headers=req_headers)
        try:
            with _open(request, 600) as response:
                raw = response.read(MAX_REPLY_BYTES + 1)      # never more than this, however much is sent
            if len(raw) > MAX_REPLY_BYTES:
                size = "%d MB" % (MAX_REPLY_BYTES // (1024 * 1024))
                raise LLMError("The AI service sent back far more than an answer needs (over %s), so it was not read. "
                               "Try again. If it keeps happening, check the AI service's address in Settings." % size,
                               "too_large",
                               "AI 服务返回的内容远远超过一个回答该有的大小（超过 %s），所以没有读取。请再试一次；"
                               "如果总是这样，请到“设置”里检查 AI 服务的地址。" % size)
            return loads_strict(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                raw_loc = (exc.headers.get("Location") or "").strip() if exc.headers is not None else ""
                exc.close()
                next_url = urllib.parse.urljoin(cur_url, raw_loc) if raw_loc else ""
                if allow_local_redirect and next_url and _is_loopback_target(next_url):
                    if redirects >= _MAX_LOCAL_REDIRECTS:
                        raise _refuse_redirect(exc.code, next_url, headers, too_many=True)
                    redirects += 1
                    cur_url = next_url
                    if exc.code == 303 or (exc.code in (301, 302) and cur_method == "POST"):
                        cur_method, cur_data, cur_ctype = "GET", None, None
                    continue
                raise _refuse_redirect(exc.code, next_url or raw_loc, headers)
            try:
                detail = exc.read(MAX_ERROR_BYTES).decode("utf-8", "replace")
            finally:
                exc.close()                 # whatever else the server goes on sending is not waited for
            try:
                error = json.loads(detail).get("error", detail)
                detail = error.get("message", detail) if isinstance(error, dict) else error
            except (ValueError, AttributeError):
                pass
            safe_detail = _redact_secrets(str(detail) or exc.reason, headers)[:300]
            raise LLMError("HTTP %s: %s" % (exc.code, safe_detail), "http")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError("Network error: %s" % _redact_secrets(str(exc), headers)[:300], "network")
        except ValueError:
            raise LLMError("Provider returned a non-JSON response", "bad_response")


def _http(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    return _send_raw(method, url, headers, data, "application/json" if data is not None else None)


def _quote_part(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def post_multipart_json(
    url: str,
    headers: Dict[str, str],
    fields: Dict[str, str],
    file_field: Tuple[str, str, str, bytes],
    transport: Optional[Transport] = None,
) -> Dict[str, Any]:
    """Send a stdlib ``multipart/form-data`` upload (e.g. OpenAI audio transcription) and parse JSON."""
    field_name, file_name, mime_type, file_bytes = file_field
    if transport is not None:
        return transport("POST", url, headers, {
            "_multipart": dict(fields),
            "file_field": field_name,
            "file_name": file_name,
            "file_mime": mime_type,
            "file_size": len(file_bytes),
        })
    boundary = "----StudioBoundary" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for key, val in fields.items():
        chunks.append(("--%s\r\n" % boundary).encode("ascii"))
        chunks.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % _quote_part(str(key))).encode("utf-8"))
        chunks.append(str(val).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(("--%s\r\n" % boundary).encode("ascii"))
    chunks.append((
        'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
        'Content-Type: %s\r\n\r\n'
        % (_quote_part(field_name), _quote_part(file_name), _quote_part(mime_type))
    ).encode("utf-8"))
    chunks.append(file_bytes)
    chunks.append(("\r\n--%s--\r\n" % boundary).encode("ascii"))
    payload = b"".join(chunks)
    return _send_raw("POST", url, headers, payload, "multipart/form-data; boundary=" + boundary)


def _openai_style(provider: str, api_key: str, base_url: Optional[str]):
    if provider == "openai":
        return OPENAI_BASE, {"Authorization": "Bearer " + api_key}
    if provider == "local":
        return normalize_local_base(base_url or ""), {}
    raise LLMError("Unknown provider: %s" % provider, "unknown_provider")


def list_models(provider: str, api_key: str = "", transport: Optional[Transport] = None,
                base_url: Optional[str] = None) -> List[str]:
    """Text-generation model ids available to this key or local server."""
    call = transport or _http
    if provider == "gemini":
        data = call("GET", GEMINI_BASE + "/models?pageSize=200", {"x-goog-api-key": api_key}, None)
        if not isinstance(data, dict) or not isinstance(data.get("models", []), list):
            raise LLMError("Unexpected Gemini model list shape", "bad_response")
        out: List[str] = []
        for m in data.get("models", []):
            if not isinstance(m, dict):
                continue
            name, methods = m.get("name"), m.get("supportedGenerationMethods")
            if isinstance(name, str) and name and isinstance(methods, list) and "generateContent" in methods:
                out.append(name.split("/", 1)[-1])
        return sorted(out)
    if provider == "anthropic":
        data = call("GET", ANTHROPIC_BASE + "/models?limit=100",
                    {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}, None)
        if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
            raise LLMError("Unexpected Anthropic model list shape", "bad_response")
        ids = [m["id"] for m in (data.get("data") or []) if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
        return sorted(ids)
    base, headers = _openai_style(provider, api_key, base_url)
    data = call("GET", base + "/models", headers, None)
    if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
        raise LLMError("Unexpected model list shape from %s" % provider, "bad_response")
    ids = [m["id"] for m in (data.get("data") or []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if provider == "local":
        return sorted(i for i in ids if i)
    return sorted(i for i in ids if i and not any(h in i for h in _NON_CHAT_HINTS))


def can_search(provider: str) -> bool:
    """Whether this provider's API can search the web while it answers (see ``search_json``)."""
    return provider in SEARCH_PROVIDERS


def _cited_pages(blocks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The pages a reply cites, in order and without repeats: ``[{"url", "title"}]``.

    Both providers mark them the same way: ``annotations`` on a block of text, each of type ``url_citation``.
    """
    pages: List[Dict[str, str]] = []
    seen = set()
    for block in blocks:
        notes = block.get("annotations")
        for note in notes if isinstance(notes, list) else []:
            if not isinstance(note, dict) or note.get("type") != "url_citation":
                continue
            url = note.get("url")
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                pages.append({"url": url, "title": str(note.get("title") or "")})
    return pages


def search_json(provider: str, api_key: str, model: str, prompt: str,
                transport: Optional[Transport] = None) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """One exchange with the provider's own web search switched on.

    Returns ``(the reply parsed as a JSON object, the pages the reply cites)``. ``prompt`` is one text: the
    instructions and the material together, as ``pipeline.single_prompt`` builds it.

    The requests and the reply shapes are taken from the providers' documentation as it read on
    16 November 2026, and have NOT been run against either service:

    - ``gemini``: the Interactions API with the ``google_search`` tool
      (ai.google.dev/gemini-api/docs/google-search). The text is in ``steps[]`` of type ``model_output``.
    - ``openai``: the Responses API with the ``web_search`` tool
      (developers.openai.com/api/docs/guides/tools-web-search). The text is in ``output[]`` of type ``message``.

    A model that does not offer web search answers with an HTTP error, which is raised as ``LLMError("http")``
    with the service's own message. A provider with no web search at all raises ``LLMError("cannot_browse")``.
    """
    call = transport or _http
    if provider == "gemini":
        safe_model = _gemini_model_id(model)
        data = call("POST", GEMINI_BASE + "/interactions",
                    {"x-goog-api-key": api_key, "Api-Revision": GEMINI_API_REVISION},
                    {"model": safe_model, "input": prompt, "tools": [{"type": "google_search"}]})
        steps = data.get("steps") if isinstance(data, dict) else None
        holders = [s for s in (steps if isinstance(steps, list) else [])
                   if isinstance(s, dict) and s.get("type") == "model_output"]
        kind = "text"
    elif provider == "openai":
        safe_model = validate_model_name(model, "openai")
        data = call("POST", OPENAI_BASE + "/responses", {"Authorization": "Bearer " + api_key},
                    {"model": safe_model, "input": prompt, "tools": [{"type": "web_search"}]})
        output = data.get("output") if isinstance(data, dict) else None
        holders = [o for o in (output if isinstance(output, list) else [])
                   if isinstance(o, dict) and o.get("type") == "message"]
        kind = "output_text"
    else:
        raise LLMError("This AI service cannot search the web", "cannot_browse",
                       "这个 AI 服务不能联网搜索")
    blocks = [block for holder in holders
              for block in (holder.get("content") if isinstance(holder.get("content"), list) else [])
              if isinstance(block, dict) and block.get("type") == kind]
    text = "".join(block["text"] for block in blocks if isinstance(block.get("text"), str))
    if not text.strip():
        raise LLMError("Unexpected response shape from %s: the reply holds no text" % provider, "bad_response")
    return parse_json_text(text), _cited_pages(blocks)


def _pop_trailing_commas(out: List[str]) -> None:
    """Remove trailing commas (and only commas) before a closing ``}`` or ``]`` in ``out``."""
    ws: List[str] = []
    while out and out[-1].strip() == "":
        ws.append(out.pop())
    removed = False
    while out and out[-1] == ",":
        out.pop()
        removed = True
        while out and out[-1].strip() == "":
            ws.append(out.pop())
    if ws:
        out.extend(reversed(ws))
    elif removed:
        pass


def _repair_json(text: str, smart_quotes: bool = False) -> str:
    """Single-pass linear O(n) repair for comments, trailing commas, BOM, and macOS smart quotes."""
    n = len(text)
    out: List[str] = []
    i = 0
    quote_kind = ""  # "", "ascii", or "smart"
    la_pos = -1
    la_char = ""

    def next_sig(pos: int) -> str:
        nonlocal la_pos, la_char
        if pos <= la_pos:
            return la_char
        j = pos
        while j < n:
            c = text[j]
            if c in " \t\r\n" or c in _INVISIBLE_OUTSIDE_STR:
                j += 1
                continue
            if c == "/" and j + 1 < n:
                nxt = text[j + 1]
                if nxt == "/":
                    nl = text.find("\n", j + 2)
                    j = n if nl == -1 else nl + 1
                    continue
                if nxt == "*":
                    end_c = text.find("*/", j + 2)
                    j = n if end_c == -1 else end_c + 2
                    continue
            break
        la_pos = j
        la_char = text[j] if j < n else ""
        return la_char

    while i < n:
        ch = text[i]
        if not quote_kind:
            if ch in _INVISIBLE_OUTSIDE_STR:
                i += 1
                continue
            if ch == "/" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == "/":
                    nl = text.find("\n", i + 2)
                    i = n if nl == -1 else nl
                    continue
                if nxt == "*":
                    end_c = text.find("*/", i + 2)
                    i = n if end_c == -1 else end_c + 2
                    continue
            if ch in ("}", "]"):
                _pop_trailing_commas(out)
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                quote_kind = "ascii"
                out.append('"')
                i += 1
                continue
            if smart_quotes and ch in ("\u201c", "\u201d"):
                quote_kind = "smart"
                out.append('"')
                i += 1
                continue
            out.append(ch)
            i += 1
        elif quote_kind == "ascii":
            if ch == "\\":
                if i + 1 < n:
                    esc_ch = text[i + 1]
                    if smart_quotes and esc_ch in ("\u201c", "\u201d"):
                        out.append('\\"')
                    else:
                        out.append(text[i:i + 2])
                    i += 2
                else:
                    out.append(ch)
                    i += 1
                continue
            if ch == '"':
                quote_kind = ""
                out.append('"')
                i += 1
                continue
            if smart_quotes and ch == "\u201d":
                # Handle macOS TextEdit turning only the closing quote of an ASCII-opened string into ”
                nl = text.find("\n", i + 1)
                line_end = n if nl == -1 else nl
                if '"' not in text[i + 1:line_end] and next_sig(i + 1) in (":", ",", "}", "]", ""):
                    quote_kind = ""
                    out.append('"')
                    i += 1
                    continue
            out.append(ch)
            i += 1
        else:
            # quote_kind == "smart"
            if ch == "\\":
                if i + 1 < n:
                    esc_ch = text[i + 1]
                    if esc_ch in ("\u201c", "\u201d"):
                        out.append('\\"')
                    else:
                        out.append(text[i:i + 2])
                    i += 2
                else:
                    out.append(ch)
                    i += 1
                continue
            if ch in ('"', "\u201d", "\u201c"):
                if next_sig(i + 1) in (":", ",", "}", "]", ""):
                    quote_kind = ""
                    out.append('"')
                elif ch == '"':
                    out.append('\\"')
                else:
                    out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
    return "".join(out)


def _strip_outer_fence(text: str) -> Optional[str]:
    """If ``text`` starts and ends with triple backticks, return the inner text."""
    if not (text.startswith("```") and text.endswith("```") and len(text) >= 6):
        return None
    inner = text[3:-3]
    nl = inner.find("\n")
    first_line = inner[:nl] if nl != -1 else inner
    tag = first_line.strip()
    if nl != -1 and (not tag or (tag.isalnum() and len(tag) <= 16)):
        return inner[nl + 1:].strip(_EDGE_INVISIBLE)
    stripped = inner.lstrip(_EDGE_INVISIBLE)
    if stripped[:4].lower() == "json" and (len(stripped) == 4 or not stripped[4].isalnum()):
        stripped = stripped[4:].lstrip(_EDGE_INVISIBLE)
    return stripped.rstrip(_EDGE_INVISIBLE)


def _extract_fenced_blocks(text: str) -> List[Tuple[bool, str]]:
    """Extract markdown ``` code blocks in O(n) without regex backtracking.

    Returns ``[(is_json_tagged, content), ...]``. Multi-line fences require closing ``` on their
    own line so ``"```not a fence```"`` inside a JSON string never terminates the block early.
    """
    blocks: List[Tuple[bool, str]] = []
    n = len(text)
    pos = 0
    while pos < n:
        at = text.find("```", pos)
        if at == -1:
            break
        line_start = text.rfind("\n", 0, at) + 1
        if text[line_start:at].strip(" \t\r") == "":
            nl = text.find("\n", at + 3)
            if nl != -1:
                header = text[at + 3:nl].strip(" \t\r")
                if not header or (len(header) <= 20 and all(c.isalnum() or c in "_-" for c in header)):
                    # Search for a closing fence on its own line
                    search_from = nl + 1
                    closed = -1
                    after_close = -1
                    while search_from <= n:
                        cand = text.find("```", search_from)
                        if cand == -1:
                            break
                        c_line_start = text.rfind("\n", nl, cand) + 1
                        if c_line_start > nl and text[c_line_start:cand].strip(" \t\r") == "":
                            c_line_end = text.find("\n", cand + 3)
                            end_idx = n if c_line_end == -1 else c_line_end
                            if text[cand + 3:end_idx].strip(" \t\r") == "":
                                closed = c_line_start - 1
                                after_close = end_idx
                                break
                        search_from = cand + 3
                    if closed != -1:
                        is_json = header.lower() in ("json", "jsonc", "json5")
                        blocks.append((is_json, text[nl + 1:closed].strip(_EDGE_INVISIBLE)))
                        pos = after_close
                        continue
        # Single-line fence fallback: ```json { ... } ```
        close_inline = text.find("```", at + 3)
        if close_inline == -1:
            break
        inner = text[at + 3:close_inline].strip(_EDGE_INVISIBLE)
        is_json = False
        if inner[:4].lower() == "json" and (len(inner) == 4 or not inner[4].isalnum()):
            is_json = True
            inner = inner[4:].strip(_EDGE_INVISIBLE)
        if inner:
            blocks.append((is_json, inner))
        pos = close_inline + 3
    return blocks


def _decode_whole(candidate: str) -> Tuple[bool, Any]:
    s = candidate.strip(_EDGE_INVISIBLE)
    if not s or s[0] not in ("{", "["):
        return False, None
    if _raw_json_too_deep(s):
        raise LLMError("Model did not return valid JSON", "bad_json")
    try:
        val, end = _LOOSE_DECODER.raw_decode(s, 0)
    except _NonFiniteJSON:
        raise
    except RecursionError:
        raise LLMError("Model did not return valid JSON", "bad_json")
    except ValueError:
        return False, None
    if s[end:].strip(_EDGE_INVISIBLE) == "":
        if _value_too_deep(val):
            raise LLMError("Model did not return valid JSON", "bad_json")
        if (_SURROGATE_CHAR.search(s) or _SURROGATE_ESCAPE.search(s)) and _has_surrogate(val):
            raise _NonFiniteJSON("JSON contains an unpaired surrogate")
        return True, val
    return False, None


def _is_property_value(text: str, pos: int) -> bool:
    """True if ``text[pos]`` is preceded by ``"key":`` (a nested value inside an unclosed outer object)."""
    k = pos - 1
    while k >= 0 and text[k] in _EDGE_INVISIBLE:
        k -= 1
    if k < 0 or text[k] != ":":
        return False
    m = k - 1
    while m >= 0 and text[m] in _EDGE_INVISIBLE:
        m -= 1
    return m >= 0 and text[m] in ('"', "\u201d", "\u201c")


def _scan_top_level(text: str, allow_array: bool) -> Tuple[bool, Any]:
    """Scan ``text`` for top-level JSON objects (or arrays if ``allow_array``) using ``raw_decode``."""
    n = len(text)
    pos = 0
    matches: List[Tuple[int, int, Any]] = []  # (is_dict_rank, span_length, value)
    check_surrogates = bool(_SURROGATE_CHAR.search(text) or _SURROGATE_ESCAPE.search(text))
    while pos < n:
        next_obj = text.find("{", pos)
        next_arr = text.find("[", pos) if allow_array else -1
        if next_obj == -1 and next_arr == -1:
            break
        if next_arr == -1 or (next_obj != -1 and next_obj < next_arr):
            at = next_obj
        else:
            at = next_arr
        if _is_property_value(text, at):
            pos = at + 1
            continue
        try:
            val, end = _LOOSE_DECODER.raw_decode(text, at)
        except _NonFiniteJSON:
            raise
        except RecursionError:
            raise LLMError("Model did not return valid JSON", "bad_json")
        except ValueError:
            pos = at + 1
            continue
        if _value_too_deep(val):
            raise LLMError("Model did not return valid JSON", "bad_json")
        if check_surrogates and _has_surrogate(val):
            raise _NonFiniteJSON("JSON contains an unpaired surrogate")
        if isinstance(val, dict):
            matches.append((1, end - at, val))
            pos = end
        elif allow_array and isinstance(val, list):
            matches.append((0, end - at, val))
            pos = end
        else:
            pos = at + 1
    if not matches:
        return False, None
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return True, matches[0][2]


def _try_candidate(candidate: str, allow_array: bool, scan: bool = False) -> Tuple[bool, Any]:
    has_brace = ("{" in candidate) or (allow_array and "[" in candidate)
    if not has_brace:
        return False, None
    ok, val = _decode_whole(candidate)
    if ok:
        return True, val
    repaired = _repair_json(candidate, smart_quotes=False)
    ok, val = _decode_whole(repaired)
    if ok:
        return True, val
    has_smart = ("\u201c" in candidate) or ("\u201d" in candidate)
    smart_repaired = _repair_json(candidate, smart_quotes=True) if has_smart else None
    if smart_repaired is not None:
        ok, val = _decode_whole(smart_repaired)
        if ok:
            return True, val
    if scan:
        for variant in (candidate, repaired, smart_repaired):
            if variant is not None:
                ok, val = _scan_top_level(variant, allow_array=allow_array)
                if ok:
                    return True, val
    return False, None


def _looks_truncated_json(cleaned: str) -> bool:
    first_brace = cleaned.find("{")
    if first_brace == -1 or ":" not in cleaned[first_brace:]:
        return False
    # Check if braces are unbalanced in repaired text
    repaired = _repair_json(cleaned, smart_quotes=("\u201c" in cleaned or "\u201d" in cleaned))
    depth = 0
    in_str = False
    i = 0
    n = len(repaired)
    saw_open = False
    while i < n:
        ch = repaired[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
                saw_open = True
            elif ch == "}":
                depth -= 1
        i += 1
    return saw_open and (depth > 0 or in_str)


def parse_json_any(text: str, allow_array: bool = False) -> Any:
    """Parse model or file text into a JSON value (object, or array if ``allow_array``).

    Tolerates UTF-8 BOM, zero-width spaces, markdown code fences anywhere in prose,
    ``//`` and ``/* */`` comments, trailing commas, literal newlines/tabs in strings,
    and macOS TextEdit smart quotes. Refuses ``NaN`` and ``Infinity`` strictly.
    """
    # Plain linear string passes only: never catastrophic backtracking regexes.
    cleaned = (text or "").strip(_EDGE_INVISIBLE)
    if not cleaned or (("{" not in cleaned) and not (allow_array and "[" in cleaned)):
        raise LLMError("Model did not return valid JSON", "bad_json")
    try:
        # 1. Whole text directly (or repaired whole text if there are no fences)
        ok, val = _decode_whole(cleaned)
        if ok:
            return val

        # 2. Outer fence wrapper (handles single-line fences containing inner triple backticks)
        outer = _strip_outer_fence(cleaned)
        if outer is not None:
            ok, val = _try_candidate(outer, allow_array=allow_array, scan=True)
            if ok:
                return val

        # 3. Markdown fenced blocks anywhere in the text (json-tagged blocks tried first)
        fenced = _extract_fenced_blocks(cleaned)
        if fenced:
            ordered = [b for is_j, b in fenced if is_j] + [b for is_j, b in fenced if not is_j]
            for block in ordered:
                ok, val = _try_candidate(block, allow_array=allow_array, scan=True)
                if ok:
                    return val

        # 4. Whole text repair + top-level raw_decode scan across prose
        ok, val = _try_candidate(cleaned, allow_array=allow_array, scan=True)
        if ok:
            return val
    except _NonFiniteJSON:
        raise LLMError("Model did not return valid JSON", "bad_json")

    if _looks_truncated_json(cleaned):
        raise LLMError(
            "The pasted JSON looks incomplete (cut off before the closing '}'). Copy the full reply again, or choose the .json file directly.",
            "bad_json",
            "粘贴的 JSON 似乎没有复制完整（结尾缺少闭合括号）。请回到 ChatGPT 重新完整复制，或直接点「选择 .json 文件」导入。",
        )
    raise LLMError("Model did not return valid JSON", "bad_json")


def parse_json_text(text: str) -> Dict[str, Any]:
    """Parse model text as a JSON object, tolerating ```json fences and stray prose.

    ``NaN`` and ``Infinity`` are refused like any other invalid JSON (see ``loads_strict``).
    """
    value = parse_json_any(text, allow_array=False)
    if not isinstance(value, dict):
        raise LLMError("Model returned JSON that is not an object", "bad_json")
    return value


def parse_manual_reply(text: str, task: str) -> Dict[str, Any]:
    """Parse a manually pasted or imported JSON reply for ``task``, wrapping top-level arrays of items."""
    key = _ARRAY_TASK_KEYS.get(task)
    value = parse_json_any(text, allow_array=bool(key))
    if isinstance(value, dict):
        return value
    if key and isinstance(value, list) and any(isinstance(item, dict) for item in value):
        return {key: value}
    raise LLMError(
        "Model returned JSON that is not an object",
        "bad_json",
        "粘贴的 JSON 不是对象格式（最外层应为 { ... }），请确认复制了完整回复。",
    )


def complete_json(provider: str, api_key: str, model: str, system: str, user: str,
                  transport: Optional[Transport] = None, base_url: Optional[str] = None,
                  temperature: Optional[float] = None) -> Dict[str, Any]:
    """Send one system+user exchange and return the reply parsed as a JSON object."""
    call = transport or _http
    temp_val: Optional[float] = None
    if temperature is not None and not isinstance(temperature, bool) and isinstance(temperature, (int, float)):
        if math.isfinite(float(temperature)):
            temp_val = max(0.0, min(1.0, float(temperature)))
    if provider == "gemini":
        safe_model = _gemini_model_segment(model)
        gen_cfg: Dict[str, Any] = {"responseMimeType": "application/json"}
        if temp_val is not None:
            gen_cfg["temperature"] = temp_val
        data = call("POST", "%s/models/%s:generateContent" % (GEMINI_BASE, safe_model), {"x-goog-api-key": api_key}, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
        })
        try:
            if not isinstance(data, dict):
                raise TypeError("response is not an object")
            parts = data["candidates"][0]["content"]["parts"]
            if not isinstance(parts, list):
                raise TypeError("parts is not a list")
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text", ""), str))
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("Unexpected Gemini response shape (the reply may have been blocked)", "bad_response")
        return parse_json_text(text)

    if provider == "anthropic":
        safe_model = validate_model_name(model, "anthropic")
        req_body: Dict[str, Any] = {
            "model": safe_model,
            "max_tokens": 8192,
            "system": system + "\n\nReturn ONLY a single valid JSON object with no surrounding prose.",
            "messages": [{"role": "user", "content": user}],
        }
        if temp_val is not None:
            req_body["temperature"] = temp_val
        data = call(
            "POST",
            ANTHROPIC_BASE + "/messages",
            {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
            req_body,
        )
        try:
            if not isinstance(data, dict):
                raise TypeError("response is not an object")
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise TypeError("content is not a list")
            text = "".join(
                b.get("text", "") for b in blocks
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text", ""), str)
            )
            if not text.strip():
                raise ValueError("empty content")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise LLMError("Unexpected Anthropic response shape", "bad_response")
        return parse_json_text(text)

    base, headers = _openai_style(provider, api_key, base_url)
    safe_model = validate_model_name(model, provider)
    oai_body: Dict[str, Any] = {
        "model": safe_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    if temp_val is not None:
        oai_body["temperature"] = temp_val
    data = call("POST", base + "/chat/completions", headers, oai_body)
    try:
        if not isinstance(data, dict):
            raise TypeError("response is not an object")
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("content is not text")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMError("Unexpected response shape from %s" % provider, "bad_response")
    return parse_json_text(text)
