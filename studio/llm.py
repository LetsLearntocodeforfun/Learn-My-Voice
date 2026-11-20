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
from typing import Any, Callable, Dict, List, Optional, Tuple

Transport = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Dict[str, Any]]

OPENAI_BASE = "https://api.openai.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
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
# and refuses HTTP redirects so neither a local prompt nor an API key header can be redirected elsewhere.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are refused", headers, fp)


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")
_KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{12,}"),
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


def validate_model_name(model: str) -> str:
    """Validate a model identifier before placing it into a URL path or request body."""
    cleaned = (model or "").strip()
    if (not cleaned or not _MODEL_NAME.fullmatch(cleaned)
            or ".." in cleaned or "//" in cleaned or "\\" in cleaned):
        raise LLMError("Invalid model name", "bad_model", "模型名称无效")
    return cleaned


def _gemini_model_id(model: str) -> str:
    cleaned = validate_model_name(model)
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/"):]
    if not cleaned or "/" in cleaned or cleaned in (".", ".."):
        raise LLMError("Invalid Gemini model name", "bad_model", "Gemini 模型名称无效")
    return cleaned


def _gemini_model_segment(model: str) -> str:
    return urllib.parse.quote(_gemini_model_id(model), safe="._-")


def normalize_local_base(url: str) -> str:
    """Validate a local server URL: http(s) on a loopback host only, without credentials, query or fragment."""
    url = (url or "").strip().rstrip("/") or DEFAULT_LOCAL_BASE
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise LLMError("Local model address must be on this computer (127.0.0.1 or localhost)", "local_address")
    if (parts.scheme not in ("http", "https")
            or parts.hostname not in _LOOPBACK_HOSTS
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment
            or (port is not None and not (1 <= port <= 65535))):
        raise LLMError("Local model address must be on this computer (127.0.0.1 or localhost)", "local_address")
    return url


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
}


def loads_strict(text: str) -> Any:
    """Read JSON that arrives from outside: a model's reply, a provider's answer, a request body.

    Python's reader accepts ``NaN``, ``Infinity`` and ``-Infinity``, which are not JSON. Stored and sent back
    to the page they make an answer a browser cannot read, so the page shows nothing at all. Here they
    raise ``ValueError`` like any other invalid JSON, and so does a number written so large that Python
    would read it as Infinity (``1e999``) or nested so deeply that Python raises ``RecursionError``.
    """
    try:
        return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except RecursionError:
        raise ValueError("JSON nested too deeply")


def _redact_secrets(text: str, headers: Optional[Dict[str, str]] = None) -> str:
    """Scrub API keys from upstream error messages before returning or logging them."""
    out = str(text or "")
    for header_val in (headers or {}).values():
        raw = str(header_val or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if len(raw) >= 4:
            out = out.replace(raw, "[REDACTED]")
    for pattern in _KEY_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Open a request to a provider. An address on this computer is never reached through a proxy.

    ``urlopen`` follows the ``http_proxy`` variable and the system's proxy settings also for 127.0.0.1, which
    hands the whole prompt to the proxy. With a local model nothing may leave the computer, so those
    requests go straight to the address. Cloud providers keep using a configured proxy: on some networks
    it is the only way out. Neither path follows HTTP redirects.
    """
    try:
        local = urllib.parse.urlsplit(request.full_url).hostname in _LOOPBACK_HOSTS
    except ValueError:
        local = False
    if local:
        return _DIRECT.open(request, timeout=timeout)
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _http(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    if data is not None:
        request.add_header("Content-Type", "application/json")
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
        return sorted(
            m.get("name", "").split("/", 1)[-1] for m in data.get("models", [])
            if "generateContent" in (m.get("supportedGenerationMethods") or []) and m.get("name")
        )
    base, headers = _openai_style(provider, api_key, base_url)
    data = call("GET", base + "/models", headers, None)
    ids = [m.get("id", "") for m in (data.get("data") or []) if isinstance(m, dict)]
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
        safe_model = validate_model_name(model)
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
    try:
        val, end = _LOOSE_DECODER.raw_decode(s, 0)
    except _NonFiniteJSON:
        raise
    except RecursionError:
        raise LLMError("Model did not return valid JSON", "bad_json")
    except ValueError:
        return False, None
    if s[end:].strip(_EDGE_INVISIBLE) == "":
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
                  transport: Optional[Transport] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Send one system+user exchange and return the reply parsed as a JSON object."""
    call = transport or _http
    if provider == "gemini":
        safe_model = _gemini_model_segment(model)
        data = call("POST", "%s/models/%s:generateContent" % (GEMINI_BASE, safe_model), {"x-goog-api-key": api_key}, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        })
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected Gemini response shape (the reply may have been blocked)", "bad_response")
        return parse_json_text(text)

    base, headers = _openai_style(provider, api_key, base_url)
    safe_model = validate_model_name(model)
    data = call("POST", base + "/chat/completions", headers, {
        "model": safe_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    })
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("Unexpected response shape from %s" % provider, "bad_response")
    return parse_json_text(text)
