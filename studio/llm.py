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


class LLMError(Exception):
    """An AI step failed.

    ``code`` is a stable identifier the UI translates (Chinese / English);
    the message is an English detail that is safe to show.
    Codes: not_configured, manual_mode, local_address, http, network,
    bad_response, bad_json, empty_result, no_samples, unknown_provider,
    cannot_browse, no_focus, other.
    """

    def __init__(self, message: str, code: str = "other") -> None:
        super().__init__(message)
        self.code = code


def normalize_local_base(url: str) -> str:
    """Validate a local server URL: http(s) on a loopback host only."""
    url = (url or "").strip().rstrip("/") or DEFAULT_LOCAL_BASE
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in _LOOPBACK_HOSTS:
        raise LLMError("Local model address must be on this computer (127.0.0.1 or localhost)", "local_address")
    return url


def _http(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
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
    """Parse model text as a JSON object, tolerating ```json fences and stray prose."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    try:
        value = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model did not return valid JSON", "bad_json")
        try:
            value = json.loads(cleaned[start:end + 1])
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
