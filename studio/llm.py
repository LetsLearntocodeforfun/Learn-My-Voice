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
from typing import Any, Callable, Dict, List, Optional

Transport = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Dict[str, Any]]

OPENAI_BASE = "https://api.openai.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_LOCAL_BASE = "http://127.0.0.1:11434/v1"  # Ollama's OpenAI-compatible endpoint
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_NON_CHAT_HINTS = ("embedding", "whisper", "tts", "dall-e", "moderation", "image", "audio",
                   "realtime", "transcribe", "babbage", "davinci", "sora")
# How much of a provider's answer is read. A script or a voice profile is a few thousand bytes and the longest
# model list a few hundred thousand; a broken or hostile server can send without end.
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024         # of an error answer; 300 characters of it are shown
# Opens addresses on this computer with no proxy at all, whatever the environment or the system settings say.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LLMError(Exception):
    """An AI step failed.

    ``code`` is a stable identifier the UI translates (Chinese / English);
    the message is an English detail that is safe to show, and ``message_zh``
    the same in Chinese when the error was written with one.
    Codes: not_configured, manual_mode, local_address, http, network,
    bad_response, bad_json, empty_result, no_samples, unknown_provider,
    cannot_browse, no_focus, too_large, other.
    """

    def __init__(self, message: str, code: str = "other", message_zh: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh


def normalize_local_base(url: str) -> str:
    """Validate a local server URL: http(s) on a loopback host only."""
    url = (url or "").strip().rstrip("/") or DEFAULT_LOCAL_BASE
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in _LOOPBACK_HOSTS:
        raise LLMError("Local model address must be on this computer (127.0.0.1 or localhost)", "local_address")
    return url


def _refuse_constant(name: str) -> Any:
    raise ValueError("%s is not valid JSON" % name)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("a number too large to read: %s" % text[:40])
    return value


def loads_strict(text: str) -> Any:
    """Read JSON that arrives from outside: a model's reply, a provider's answer, a request body.

    Python's reader accepts ``NaN``, ``Infinity`` and ``-Infinity``, which are not JSON. Stored and sent back
    to the page they make an answer a browser cannot read, so the page shows nothing at all. Here they
    raise ``ValueError`` like any other invalid JSON, and so does a number written so large that Python
    would read it as Infinity (``1e999``).
    """
    return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Open a request to a provider. An address on this computer is never reached through a proxy.

    ``urlopen`` follows the ``http_proxy`` variable and the system's proxy settings also for 127.0.0.1, which
    hands the whole prompt to the proxy. With a local model nothing may leave the computer, so those
    requests go straight to the address. Cloud providers keep using a configured proxy: on some networks
    it is the only way out.
    """
    try:
        local = urllib.parse.urlsplit(request.full_url).hostname in _LOOPBACK_HOSTS
    except ValueError:
        local = False
    return _DIRECT.open(request, timeout=timeout) if local else urllib.request.urlopen(request, timeout=timeout)


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
        raise LLMError("HTTP %s: %s" % (exc.code, str(detail)[:300]), "http")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError("Network error: %s" % exc, "network")
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


def parse_json_text(text: str) -> Dict[str, Any]:
    """Parse model text as a JSON object, tolerating ```json fences and stray prose.

    ``NaN`` and ``Infinity`` are refused like any other invalid JSON (see ``loads_strict``).
    """
    # Plain string work on purpose. A pattern did this before, and on a reply that was mostly white space
    # its running time grew with the square of the length: 128,000 spaces held the server for 18 seconds.
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.lstrip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    try:
        value = loads_strict(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model did not return valid JSON", "bad_json")
        try:
            value = loads_strict(cleaned[start:end + 1])
        except ValueError:
            raise LLMError("Model did not return valid JSON", "bad_json")
    if not isinstance(value, dict):
        raise LLMError("Model returned JSON that is not an object", "bad_json")
    return value


def complete_json(provider: str, api_key: str, model: str, system: str, user: str,
                  transport: Optional[Transport] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Send one system+user exchange and return the reply parsed as a JSON object."""
    call = transport or _http
    if provider == "gemini":
        data = call("POST", "%s/models/%s:generateContent" % (GEMINI_BASE, model), {"x-goog-api-key": api_key}, {
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
    data = call("POST", base + "/chat/completions", headers, {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    })
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("Unexpected response shape from %s" % provider, "bad_response")
    return parse_json_text(text)
