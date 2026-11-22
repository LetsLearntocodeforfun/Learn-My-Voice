"""Background trend sources and polite public HTTPS fetch layer for Autopilot.

Nothing here runs unless the creator switches on Autopilot, switches on ``trends.refresh`` (or watched-folder
keyword intake), and explicitly enables a source. With default settings (``ai_lookup=False``, ``pages=[]``),
no background lookup or web request is ever made. Direct Xiaohongshu scraping (``xiaohongshu.com``,
``rednote.com``, ``xhslink.com`` and their subdomains) is always refused by name before any DNS or socket call.

Standard library only.
"""
from __future__ import annotations

import codecs
import contextlib
import html.parser
import http.client
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from . import autopilot, creator, keyword_import, llm, pipeline, trends
from .store import MANUAL_PROVIDER, MAX_KEYWORD_CHARS, clean_file_name, clean_keyword, keyword_key

if TYPE_CHECKING:
    from .store import Store

SETTINGS_KEY = "sources"
TASK_NAME = "trends.refresh"
UNDO_KIND = "trends.added"
EVERY_SEC = 12 * 3600

MAX_PAGES = 5
MAX_URL_CHARS = 500
USER_AGENT = "Learn-My-Voice-Autopilot/0.2 (+https://github.com/LetsLearntocodeforfun/Learn-My-Voice)"
ROBOTS_MAX_BYTES = 64 * 1024
ROBOTS_TIMEOUT_SEC = 10.0
PAGE_MAX_BYTES = 512 * 1024
PAGE_TIMEOUT_SEC = 15.0
MAX_REDIRECTS = 3
MAX_PAGE_TEXT_CHARS = 16_000
MAX_PAGE_KEYWORDS = 12
MIN_PAGE_KEYWORD_CHARS = 2
MAX_PAGE_KEYWORD_CHARS = 10
STAGE10 = "10_trend_page_reader.md"

BLOCKED_DOMAINS = ("xiaohongshu.com", "rednote.com", "xhslink.com")
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain")
KEYWORD_FILE_EXTENSIONS = (".csv", ".tsv", ".xlsx")

# Optional test seams used when fetch_public_page is called without explicit opener/resolver (e.g. inside trends.refresh).
DEFAULT_OPENER: Any = None
DEFAULT_RESOLVER: Any = None

_REDIRECT_CODES = (301, 302, 303, 307, 308)
_READ_CHUNK = 16 * 1024
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SAFE_CHARSET_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$", re.ASCII)
_FORBIDDEN_CODECS = frozenset({
    "unicode_escape", "raw_unicode_escape", "punycode", "idna", "rot_13",
    "hex_codec", "base64_codec", "bz2_codec", "zlib_codec", "uu_codec", "quopri_codec",
})
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
})


class SourceError(Exception):
    """A trend-source setting, web page fetch, or keyword spreadsheet was refused."""

    def __init__(self, code: str, message: str, message_zh: str, status: int = 400,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.message_zh = str(message_zh)
        self.status = int(status)
        self.extra = dict(extra or {})


def _has_whitespace_or_control(text: str) -> bool:
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or not ch.isprintable() for ch in text)


def _is_blocked_domain(host: str) -> bool:
    cleaned = host.rstrip(".").lower()
    return any(cleaned == domain or cleaned.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def _xiaohongshu_refused() -> SourceError:
    return SourceError(
        "xiaohongshu_refused",
        "Direct Xiaohongshu scraping is not supported: URLs on xiaohongshu.com, rednote.com, and xhslink.com are refused.",
        "不支持直接抓取小红书：xiaohongshu.com、rednote.com 和 xhslink.com 的网址均被拒绝。",
        status=400,
    )


def _private_address_refused(detail: str = "") -> SourceError:
    suffix = (" (%s)" % detail) if detail else ""
    return SourceError(
        "private_address",
        "Only public internet hosts are allowed; localhost and private, loopback, link-local, or reserved IP addresses are refused%s." % suffix,
        "只允许访问公开互联网地址；localhost 以及内网、回环、链路本地或保留 IP 地址均被拒绝%s。" % suffix,
        status=400,
    )


def _is_public_ip(addr: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None and not _is_public_ip(addr.ipv4_mapped):
            return False
        if addr.sixtofour is not None and not _is_public_ip(addr.sixtofour):
            return False
        if addr.teredo is not None:
            server_ip, client_ip = addr.teredo
            if not (_is_public_ip(server_ip) and _is_public_ip(client_ip)):
                return False
    elif addr in _CGNAT_NET:
        return False
    return bool(
        addr.is_global
        and not addr.is_loopback
        and not addr.is_private
        and not addr.is_link_local
        and not addr.is_multicast
        and not addr.is_unspecified
        and not addr.is_reserved
    )


def _parse_literal_ip(host: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    if "%" in host:
        raise _private_address_refused("zone-scoped IPv6")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def validate_page_url(url: Any) -> str:
    """Validate a public ``https://`` trend-source URL without making any network call."""
    if not isinstance(url, str) or not url:
        raise SourceError(
            "sources_bad_url",
            "Each page URL must be a non-empty https:// string.",
            "每个网页地址都必须是非空的 https:// 网址。",
            status=400,
        )
    if len(url) > MAX_URL_CHARS:
        raise SourceError(
            "sources_bad_url",
            "Each page URL must be %d characters or fewer." % MAX_URL_CHARS,
            "每个网页地址不能超过 %d 个字符。" % MAX_URL_CHARS,
            status=400,
        )
    if _has_whitespace_or_control(url):
        raise SourceError(
            "sources_bad_url",
            "Page URLs cannot contain spaces or control characters.",
            "网页地址不能包含空格或控制字符。",
            status=400,
        )
    if url[:8].lower() != "https://":
        raise SourceError(
            "sources_bad_url",
            "Only https:// URLs are allowed for public trend pages.",
            "公开趋势网页只允许使用 https:// 网址。",
            status=400,
        )
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise SourceError(
            "sources_bad_url",
            "That URL is malformed and could not be parsed.",
            "这个网址格式有误，无法解析。",
            status=400,
        )
    if parts.scheme.lower() != "https" or port == 0:
        raise SourceError(
            "sources_bad_url",
            "Only valid https:// URLs are allowed.",
            "只允许有效的 https:// 网址。",
            status=400,
        )
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise SourceError(
            "sources_bad_url",
            "Page URLs cannot include embedded usernames or passwords.",
            "网页地址不能包含用户名或密码。",
            status=400,
        )
    host = parts.hostname or ""
    if not host:
        raise SourceError(
            "sources_bad_url",
            "Each page URL must include a valid host name.",
            "每个网页地址都必须包含有效的域名。",
            status=400,
        )
    if _is_blocked_domain(host):
        raise _xiaohongshu_refused()
    literal = _parse_literal_ip(host)
    if literal is not None:
        if not _is_public_ip(literal):
            raise _private_address_refused(str(literal))
        return url
    host_clean = host.rstrip(".").lower()
    if not host_clean or host_clean.startswith(".") or ".." in host_clean:
        raise SourceError(
            "sources_bad_url",
            "Each page URL must include a valid host name.",
            "每个网页地址都必须包含有效的域名。",
            status=400,
        )
    if host_clean == "localhost" or host_clean.endswith(".localhost") or host_clean.isdigit():
        raise _private_address_refused(host_clean)
    if all(part.isdigit() or part.lower().startswith("0x") for part in host_clean.split(".")):
        raise _private_address_refused(host_clean)
    return url


# ---- settings ---------------------------------------------------------------
def clean_source_settings(raw: Any) -> Dict[str, Any]:
    """Return cleaned source settings; damaged or hand-edited values fall back to safe defaults."""
    held = raw if isinstance(raw, dict) else {}
    ai_lookup = held.get("ai_lookup") is True
    keyword_files = held.get("keyword_files") if isinstance(held.get("keyword_files"), bool) else True
    pages: List[str] = []
    if isinstance(held.get("pages"), list):
        for entry in held["pages"]:
            if len(pages) >= MAX_PAGES:
                break
            try:
                valid = validate_page_url(entry)
            except SourceError:
                continue
            if valid not in pages:
                pages.append(valid)
    return {
        "ai_lookup": ai_lookup,
        "keyword_files": keyword_files,
        "pages": pages,
        "max_pages": MAX_PAGES,
    }


def get_source_settings(store: "Store") -> Dict[str, Any]:
    """Read and clean Autopilot trend-source settings from ``config.json``."""
    pilot_cfg = store.load_config().get(autopilot.KEY)
    raw = pilot_cfg.get(SETTINGS_KEY) if isinstance(pilot_cfg, dict) else None
    return clean_source_settings(raw)


def save_source_settings(store: "Store", body: Any) -> Dict[str, Any]:
    """Validate and save Autopilot trend-source settings; raises ``SourceError`` on invalid input."""
    if not isinstance(body, dict):
        raise SourceError(
            "sources_bad_body",
            "Trend source settings must be a JSON object.",
            "趋势来源设置必须是一个 JSON 对象。",
            status=400,
        )
    allowed = {"ai_lookup", "keyword_files", "pages", "max_pages"}
    for key in body:
        if key not in allowed:
            raise SourceError(
                "sources_bad_field",
                "Unknown trend source setting: %s." % str(key)[:60],
                "未知的趋势来源设置项：%s。" % str(key)[:60],
                status=400,
            )
    if "max_pages" in body and (isinstance(body["max_pages"], bool) or body["max_pages"] != MAX_PAGES):
        raise SourceError(
            "sources_bad_field",
            "max_pages is fixed at %d." % MAX_PAGES,
            "max_pages 固定为 %d。" % MAX_PAGES,
            status=400,
        )
    current = get_source_settings(store)
    ai_lookup = current["ai_lookup"]
    keyword_files = current["keyword_files"]
    pages = list(current["pages"])

    if "ai_lookup" in body:
        if not isinstance(body["ai_lookup"], bool):
            raise SourceError(
                "sources_bad_field",
                "ai_lookup must be true or false.",
                "ai_lookup 必须是 true 或 false。",
                status=400,
            )
        ai_lookup = body["ai_lookup"]

    if "keyword_files" in body:
        if not isinstance(body["keyword_files"], bool):
            raise SourceError(
                "sources_bad_field",
                "keyword_files must be true or false.",
                "keyword_files 必须是 true 或 false。",
                status=400,
            )
        keyword_files = body["keyword_files"]

    if "pages" in body:
        raw_pages = body["pages"]
        if not isinstance(raw_pages, list):
            raise SourceError(
                "sources_bad_field",
                "pages must be a list of https:// URLs.",
                "pages 必须是一个包含 https:// 网址的列表。",
                status=400,
            )
        if len(raw_pages) > MAX_PAGES:
            raise SourceError(
                "sources_too_many_pages",
                "At most %d public page URLs can be configured." % MAX_PAGES,
                "最多只能添加 %d 个公开网页地址。" % MAX_PAGES,
                status=400,
            )
        checked: List[str] = []
        for item in raw_pages:
            valid = validate_page_url(item)
            if valid not in checked:
                checked.append(valid)
        pages = checked

    autopilot.save_other_setting(store, SETTINGS_KEY, {
        "ai_lookup": ai_lookup,
        "keyword_files": keyword_files,
        "pages": pages,
    })
    return get_source_settings(store)


# ---- polite HTTPS fetch layer -----------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects TCP directly to a validated global IP while verifying TLS SNI and certificate for ``server_host``."""

    def __init__(self, server_host: str, pinned_ip: str, port: int, **kwargs: Any) -> None:
        super().__init__(server_host, port=port, **kwargs)
        self._server_host = server_host
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self._server_host)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, server_host: str, pinned_ip: str, port: int) -> None:
        self._ssl_ctx = ssl.create_default_context()
        super().__init__(context=self._ssl_ctx)
        self._server_host = server_host
        self._pinned_ip = pinned_ip
        self._port = port

    def https_open(self, req: urllib.request.Request) -> Any:
        def factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            kwargs.setdefault("context", self._ssl_ctx)
            return _PinnedHTTPSConnection(self._server_host, self._pinned_ip, self._port, **kwargs)

        return self.do_open(factory, req)


def _extract_ips(resolved: Any) -> List[str]:
    if isinstance(resolved, str):
        return [resolved]
    if not isinstance(resolved, Iterable):
        return []
    ips: List[str] = []
    for entry in resolved:
        if isinstance(entry, str):
            ips.append(entry)
        elif isinstance(entry, tuple) and len(entry) >= 5 and isinstance(entry[4], tuple) and entry[4]:
            ips.append(str(entry[4][0]))
        elif isinstance(entry, tuple) and len(entry) >= 1 and isinstance(entry[0], str):
            ips.append(entry[0])
    return ips


def _resolve_public_ips(host: str, port: int, resolver: Any = None) -> List[str]:
    literal = _parse_literal_ip(host)
    if literal is not None:
        if not _is_public_ip(literal):
            raise _private_address_refused(str(literal))
        return [str(literal)]

    active = socket.getaddrinfo if resolver is None else resolver
    try:
        if active is socket.getaddrinfo:
            raw = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        else:
            try:
                raw = active(host)
            except TypeError:
                raw = active(host, port)
    except SourceError:
        raise
    except OSError as exc:
        raise SourceError(
            "dns_failed",
            "Could not resolve host %s: %s." % (host, exc),
            "无法解析域名 %s：%s。" % (host, exc),
            status=502,
        )

    ips = _extract_ips(raw)
    if not ips:
        raise SourceError(
            "dns_failed",
            "Host %s did not resolve to any IP address." % host,
            "域名 %s 没有解析出任何 IP 地址。" % host,
            status=502,
        )

    checked: List[str] = []
    for raw_ip in ips:
        if not isinstance(raw_ip, str) or "%" in raw_ip:
            raise _private_address_refused(str(raw_ip))
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise _private_address_refused(raw_ip)
        if not _is_public_ip(addr):
            raise _private_address_refused(str(addr))
        if str(addr) not in checked:
            checked.append(str(addr))
    return checked


def _build_safe_opener(opener: Any, host: str, pinned_ip: str, port: int) -> Any:
    if opener is None:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _PinnedHTTPSHandler(host, pinned_ip, port),
            _NoRedirect(),
        )
    if isinstance(opener, urllib.request.OpenerDirector):
        builtin_skip = (
            urllib.request.HTTPRedirectHandler,
            urllib.request.HTTPCookieProcessor,
            urllib.request.ProxyHandler,
            urllib.request.UnknownHandler,
            urllib.request.HTTPDefaultErrorHandler,
            urllib.request.HTTPErrorProcessor,
            urllib.request.HTTPHandler,
            urllib.request.HTTPSHandler,
            urllib.request.FileHandler,
            urllib.request.FTPHandler,
            urllib.request.DataHandler,
        )
        custom = [h for h in opener.handlers if type(h) not in builtin_skip]
        return urllib.request.build_opener(*custom, urllib.request.ProxyHandler({}), _NoRedirect())
    return opener


def _read_bounded(stream: Any, limit: int) -> Tuple[bytes, bool]:
    """Read up to ``limit + 1`` bytes; returns ``(bytes_up_to_limit, exceeded)``."""
    chunks: List[bytes] = []
    total = 0
    while total <= limit:
        piece = stream.read(min(_READ_CHUNK, limit + 1 - total))
        if not piece:
            break
        chunks.append(piece)
        total += len(piece)
    data = b"".join(chunks)
    if len(data) > limit:
        return data[:limit], True
    return data, False


def _validate_same_site_redirect(current_url: str, location: Optional[str], orig_host: str, orig_port: int) -> str:
    if not isinstance(location, str) or not location or _has_whitespace_or_control(location):
        raise SourceError(
            "bad_redirect",
            "Redirect target is missing or invalid.",
            "网页重定向目标缺失或无效。",
            status=502,
        )
    target = urllib.parse.urljoin(current_url, location)
    parts = urllib.parse.urlsplit(target)
    target_host = (parts.hostname or "").rstrip(".").lower()
    target_port = parts.port or 443
    if parts.scheme.lower() != "https" or target_host != orig_host or target_port != orig_port:
        raise SourceError(
            "bad_redirect",
            "Cross-site or non-HTTPS redirect from %s to %s was refused." % (orig_host, target_host or parts.scheme),
            "已拒绝从 %s 跳转到其他站点或非 HTTPS 地址（%s）。" % (orig_host, target_host or parts.scheme),
            status=502,
        )
    validate_page_url(target)
    return target


def _fetch_robots_parser(safe_opener: Any, orig_host: str, orig_port: int, netloc: str) -> urllib.robotparser.RobotFileParser:
    robots_url = "https://%s/robots.txt" % netloc
    current = robots_url
    for hop in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(
            current,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/plain, */*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
            method="GET",
        )
        try:
            resp = safe_opener.open(req, timeout=ROBOTS_TIMEOUT_SEC) if hasattr(safe_opener, "open") else safe_opener(req, timeout=ROBOTS_TIMEOUT_SEC)
            with contextlib.closing(resp):
                status = int(getattr(resp, "status", None) or resp.getcode() or 200)
                headers = getattr(resp, "headers", None) or resp.info() or {}
                if status in _REDIRECT_CODES:
                    if hop >= MAX_REDIRECTS:
                        raise SourceError("too_many_redirects", "Too many redirects while fetching robots.txt.",
                                          "读取 robots.txt 时重定向次数过多。", status=502)
                    current = _validate_same_site_redirect(current, headers.get("Location"), orig_host, orig_port)
                    continue
                raw, _exceeded = _read_bounded(resp, ROBOTS_MAX_BYTES)
        except urllib.error.HTTPError as err:
            with contextlib.closing(err):
                status = int(err.code)
                headers = err.headers or {}
                if status in _REDIRECT_CODES:
                    if hop >= MAX_REDIRECTS:
                        raise SourceError("too_many_redirects", "Too many redirects while fetching robots.txt.",
                                          "读取 robots.txt 时重定向次数过多。", status=502)
                    current = _validate_same_site_redirect(current, headers.get("Location"), orig_host, orig_port)
                    continue
                raw = b""
        except SourceError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise SourceError(
                "fetch_failed",
                "Could not check robots.txt on %s: %s." % (orig_host, exc),
                "无法读取 %s 的 robots.txt：%s。" % (orig_host, exc),
                status=502,
            )

        rp = urllib.robotparser.RobotFileParser()
        if status in (401, 403):
            rp.disallow_all = True
            return rp
        if 400 <= status < 500 and status != 429:
            rp.allow_all = True
            return rp
        if status != 200:
            raise SourceError(
                "fetch_failed",
                "robots.txt on %s returned HTTP %d." % (orig_host, status),
                "%s 的 robots.txt 返回了 HTTP %d。" % (orig_host, status),
                status=502,
            )
        text = raw.decode("utf-8", "replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        rp.parse(text.splitlines())
        return rp
    raise SourceError("too_many_redirects", "Too many redirects while fetching robots.txt.",
                      "读取 robots.txt 时重定向次数过多。", status=502)


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Linear stdlib HTML-to-text stripper ignoring <script>, <style>, <noscript>, <template>, and comments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._muted: Optional[str] = None
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        lower = tag.lower()
        if lower in ("script", "style"):
            self._muted = lower
        elif lower in ("noscript", "template"):
            self._skip_depth += 1
        elif lower in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if self._muted == lower:
            self._muted = None
        elif lower in ("noscript", "template") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif lower in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._muted is None and self._skip_depth == 0 and data:
            self._parts.append(data)

    def handle_comment(self, data: str) -> None:
        pass

    def text(self) -> str:
        return " ".join("".join(self._parts).split())[:MAX_PAGE_TEXT_CHARS]


def _decode_body(raw: bytes, content_type_header: str) -> str:
    charset = ""
    for param in content_type_header.split(";")[1:]:
        name, sep, val = param.partition("=")
        if sep and name.strip().lower() == "charset":
            charset = val.strip().strip('"\'')
            break
    if charset and _SAFE_CHARSET_RE.fullmatch(charset):
        try:
            info = codecs.lookup(charset)
            if info.name not in _FORBIDDEN_CODECS and getattr(info, "_is_text_encoding", True):
                return raw.decode(info.name, "replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", "replace")


def html_to_text(raw_html: str) -> str:
    """Strip <script>, <style>, comments, and tags, collapsing whitespace to <= 16,000 chars."""
    extractor = _HTMLTextExtractor()
    extractor.feed(raw_html)
    extractor.close()
    return extractor.text()


def _robots_refused(url: str) -> SourceError:
    return SourceError(
        "robots_disallowed",
        "Fetching %s is disallowed by that site's robots.txt." % url,
        "该网站的 robots.txt 不允许抓取 %s。" % url,
        status=403,
    )


def fetch_public_page(url: str, *, opener: Any = None, resolver: Any = None) -> Dict[str, Any]:
    """Politely fetch a public ``https://`` page after validating host, DNS IPs, and ``robots.txt``.

    Returns ``{"url", "text", "content_type", "bytes"}`` or raises ``SourceError``.
    """
    validate_page_url(url)
    parts = urllib.parse.urlsplit(url)
    orig_host_raw = parts.hostname or ""
    orig_host = orig_host_raw.rstrip(".").lower()
    orig_port = parts.port or 443
    netloc = parts.netloc

    active_opener = DEFAULT_OPENER if opener is None else opener
    active_resolver = DEFAULT_RESOLVER if resolver is None else resolver

    ips = _resolve_public_ips(orig_host_raw, orig_port, active_resolver)
    safe_opener = _build_safe_opener(active_opener, orig_host_raw, ips[0], orig_port)

    rp = _fetch_robots_parser(safe_opener, orig_host, orig_port, netloc)
    if not rp.can_fetch(USER_AGENT, url):
        raise _robots_refused(url)

    current = url
    for hop in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(
            current,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html, text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
            method="GET",
        )
        try:
            resp = safe_opener.open(req, timeout=PAGE_TIMEOUT_SEC) if hasattr(safe_opener, "open") else safe_opener(req, timeout=PAGE_TIMEOUT_SEC)
        except urllib.error.HTTPError as err:
            with contextlib.closing(err):
                if int(err.code) in _REDIRECT_CODES:
                    if hop >= MAX_REDIRECTS:
                        raise SourceError(
                            "too_many_redirects",
                            "Page redirected more than %d times." % MAX_REDIRECTS,
                            "网页重定向超过了 %d 次上限。" % MAX_REDIRECTS,
                            status=502,
                        )
                    headers = err.headers or {}
                    current = _validate_same_site_redirect(current, headers.get("Location"), orig_host, orig_port)
                    if not rp.can_fetch(USER_AGENT, current):
                        raise _robots_refused(current)
                    continue
                raise SourceError(
                    "fetch_failed",
                    "Fetching %s failed with HTTP %d." % (current, int(err.code)),
                    "读取 %s 失败（HTTP %d）。" % (current, int(err.code)),
                    status=502,
                )
        except SourceError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise SourceError(
                "fetch_failed",
                "Could not fetch %s: %s." % (current, exc),
                "无法读取 %s：%s。" % (current, exc),
                status=502,
            )

        with contextlib.closing(resp):
            status = int(getattr(resp, "status", None) or resp.getcode() or 200)
            headers = getattr(resp, "headers", None) or resp.info() or {}
            if status in _REDIRECT_CODES:
                if hop >= MAX_REDIRECTS:
                    raise SourceError(
                        "too_many_redirects",
                        "Page redirected more than %d times." % MAX_REDIRECTS,
                        "网页重定向超过了 %d 次上限。" % MAX_REDIRECTS,
                        status=502,
                    )
                current = _validate_same_site_redirect(current, headers.get("Location"), orig_host, orig_port)
                if not rp.can_fetch(USER_AGENT, current):
                    raise _robots_refused(current)
                continue
            if status != 200:
                raise SourceError(
                    "fetch_failed",
                    "Fetching %s failed with HTTP %d." % (current, status),
                    "读取 %s 失败（HTTP %d）。" % (current, status),
                    status=502,
                )

            raw_ct = str(headers.get("Content-Type") or "").strip()
            mime = raw_ct.split(";", 1)[0].strip().lower()
            if mime not in ALLOWED_CONTENT_TYPES:
                raise SourceError(
                    "unsupported_content_type",
                    "Unsupported Content-Type %r; only text/html and text/plain pages are read." % (mime or "missing"),
                    "不支持的内容类型 %r；只读取 text/html 或 text/plain 网页。" % (mime or "missing"),
                    status=415,
                )

            cl_raw = str(headers.get("Content-Length") or "").strip()
            if cl_raw.isascii() and cl_raw.isdigit() and int(cl_raw) > PAGE_MAX_BYTES:
                raise SourceError(
                    "page_too_large",
                    "Page exceeds the %d KB size limit." % (PAGE_MAX_BYTES // 1024),
                    "网页体积超过了 %d KB 的上限。" % (PAGE_MAX_BYTES // 1024),
                    status=413,
                )

            body, exceeded = _read_bounded(resp, PAGE_MAX_BYTES)
            if exceeded:
                raise SourceError(
                    "page_too_large",
                    "Page exceeds the %d KB size limit." % (PAGE_MAX_BYTES // 1024),
                    "网页体积超过了 %d KB 的上限。" % (PAGE_MAX_BYTES // 1024),
                    status=413,
                )

            decoded = _decode_body(body, raw_ct)
            text = html_to_text(decoded) if mime == "text/html" else " ".join(decoded.split())[:MAX_PAGE_TEXT_CHARS]
            return {
                "url": current,
                "text": text,
                "content_type": mime,
                "bytes": len(body),
            }

    raise SourceError(
        "too_many_redirects",
        "Page redirected more than %d times." % MAX_REDIRECTS,
        "网页重定向超过了 %d 次上限。" % MAX_REDIRECTS,
        status=502,
    )


# ---- Stage 10 page reader prompt & parser -----------------------------------
def page_reader_prompt(store: "Store", url: str, page_text: str, keyword_count: int = MAX_PAGE_KEYWORDS) -> Tuple[str, str]:
    """Build the XML-fenced Stage 10 prompt with ``<`` and ``>`` escaped via ``pipeline._no_tags``."""
    titles = [s["title"] for s in store.list_samples() if not s.get("is_sponsored")][:pipeline.LOOKUP_MAX_TITLES]
    niches = creator.prompt_block(store.load_creator())["niches"]
    return pipeline._blueprint(STAGE10, {
        "page_url_json": pipeline._inline(str(url)[:MAX_URL_CHARS]),
        "creator_niches_json": pipeline._dump({"niches": niches, "creator_video_titles": titles}),
        "page_text_json": pipeline._inline(str(page_text)[:MAX_PAGE_TEXT_CHARS]),
        "keyword_count": str(max(1, min(int(keyword_count), MAX_PAGE_KEYWORDS))),
    })


def _optional_heat(value: Any) -> Optional[int]:
    if isinstance(value, str):
        return keyword_import.read_heat(value)
    return trends._whole(value, 1, 10 ** 13 - 1)


def _parse_page_keywords(result: Any, *, source_url: str, now: float) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    raw_list = result.get("keywords")
    if not isinstance(raw_list, list):
        return []
    items: List[Dict[str, Any]] = []
    seen_keys = set()
    for raw in raw_list:
        if len(items) >= MAX_PAGE_KEYWORDS:
            break
        if not isinstance(raw, dict) or not isinstance(raw.get("keyword"), str):
            continue
        keyword = clean_keyword(raw["keyword"])
        key = keyword_key(keyword)
        if not key or key in seen_keys:
            continue
        if not (MIN_PAGE_KEYWORD_CHARS <= len(keyword) <= MAX_PAGE_KEYWORD_CHARS) or not _CJK_RE.search(keyword):
            continue
        seen_keys.add(key)
        items.append({
            "keyword": keyword,
            "source_url": source_url,
            "seen_at": now,
            "heat": _optional_heat(raw.get("heat")),
            "rank": trends._whole(raw.get("rank"), 1, trends.MAX_RANK),
            "note_zh": str(raw.get("note_zh") or "")[:200],
            "note_en": str(raw.get("note_en") or "")[:200],
        })
    return items


# ---- scheduled Stage 7 lookup helper ----------------------------------------
def _stage7_items(result: Dict[str, Any], now: float) -> Tuple[List[Dict[str, Any]], int, int]:
    items: List[Dict[str, Any]] = []
    no_source = too_old = 0
    for raw in result.get("keywords") if isinstance(result.get("keywords"), list) else []:
        if not isinstance(raw, dict):
            continue
        keyword = clean_keyword(raw.get("keyword"))
        url = str(raw.get("source_url") or "").strip()
        if not keyword or len(keyword) > MAX_KEYWORD_CHARS:
            continue
        if not pipeline._is_web_link(url):
            no_source += 1
            continue
        seen = pipeline._source_time(raw.get("source_date"), now)
        if seen is None:
            too_old += 1
            continue
        items.append({
            "keyword": keyword,
            "source_url": url,
            "seen_at": seen,
            "note_zh": str(raw.get("why_zh") or "")[:200],
            "note_en": str(raw.get("why_en") or "")[:200],
        })
    return items, no_source, too_old


def _skipped(reason: str, message: str, message_zh: str) -> Dict[str, Any]:
    return {"status": "skipped", "reason": reason, "message": message, "message_zh": message_zh}


def _run_ai_lookup(context: autopilot.Context) -> Tuple[Dict[str, Any], int, int]:
    store = context.store
    config = store.load_config()
    provider = config.get("provider") or ""
    model = config.get("model") or ""
    if not llm.can_search(provider):
        return _skipped(
            "cannot_browse",
            "The AI service chosen in Settings cannot search the live web, so scheduled AI lookup was skipped.",
            "设置里选的 AI 服务不支持联网搜索，已跳过定时 AI 热词搜索。",
        ), 0, 0
    api_key = (config.get("keys") or {}).get(provider) or ""
    if not model or not api_key:
        return _skipped(
            "not_configured",
            "No model or API key is configured for the chosen AI service, so scheduled AI lookup was skipped.",
            "当前选择的 AI 服务尚未配置模型或密钥，已跳过定时 AI 热词搜索。",
        ), 0, 0
    try:
        system, user = pipeline.trends_prompt(store)
    except llm.LLMError as exc:
        if exc.code == "no_focus":
            return _skipped(
                "no_focus",
                "No niches or library videos are set yet, so scheduled AI lookup was skipped.",
                "还没有填写赛道或添加作品素材，已跳过定时 AI 热词搜索。",
            ), 0, 0
        raise

    day = trends._date(context.now)
    if day is not None:
        user = re.sub(r"<today>[^<]*</today>", "<today>%s</today>" % day.isoformat(), user, count=1)

    if not context.spend(1):
        return _skipped(
            autopilot.DAILY_CAP,
            "Daily automatic model call limit reached; scheduled AI lookup was skipped.",
            "已达到今日自动模型调用上限，已跳过定时 AI 热词搜索。",
        ), 0, 0

    try:
        result, _pages = llm.search_json(
            provider, api_key, model, pipeline.single_prompt(system, user), transport=context.transport
        )
    except llm.LLMError as exc:
        if exc.code == "cannot_browse":
            return _skipped(
                "cannot_browse",
                "The AI service reported that it could not search the web, so scheduled AI lookup was skipped.",
                "AI 服务返回无法联网搜索，已跳过定时 AI 热词搜索。",
            ), 0, 0
        raise

    if not isinstance(result, dict) or result.get("cannot_browse") is True:
        return _skipped(
            "cannot_browse",
            "The AI service reported that it could not search the web, so scheduled AI lookup was skipped.",
            "AI 服务返回无法联网搜索，已跳过定时 AI 热词搜索。",
        ), 0, 0

    items, no_source, too_old = _stage7_items(result, context.now)
    if not items:
        return {
            "status": "done",
            "found": 0,
            "added": 0,
            "seen_again": 0,
            "dropped_no_source": no_source,
            "dropped_too_old": too_old,
        }, 0, 0

    prepared = trends.prepare_found(items, row_source=trends.AI, seen_by="ai", now=context.now)
    if not prepared["rows"]:
        return {"status": "done", "found": 0, "added": 0, "seen_again": 0}, 0, 0
    context.record_undo(UNDO_KIND, prepared["undo"])
    stored = trends.store_found(store, prepared)
    sightings_added = stored["added"] + stored["seen_again"]
    return {
        "status": "done",
        "found": stored["found"],
        "added": stored["added"],
        "seen_again": stored["seen_again"],
        "dropped_no_source": no_source,
        "dropped_too_old": too_old,
        "keywords": stored["keywords"][:12],
    }, stored["added"], sightings_added


def _model_ready_for_pages(store: "Store") -> Optional[Dict[str, Any]]:
    config = store.load_config()
    provider = config.get("provider") or ""
    model = config.get("model") or ""
    if provider == MANUAL_PROVIDER:
        return _skipped(
            "manual_mode",
            "Copy-and-paste mode is on; public trend pages require a configured model to extract keywords.",
            "当前为复制粘贴模式，读取公开趋势网页需要先配置可直接调用的模型。",
        )
    api_key = (config.get("keys") or {}).get(provider) or ""
    base_url = config.get("local_base_url") or None
    if not model or (provider == "local" and not base_url) or (provider != "local" and not api_key):
        return _skipped(
            "not_configured",
            "AI is not configured yet, so public trend page extraction was skipped.",
            "尚未配置 AI 模型，已跳过公开趋势网页热词提取。",
        )
    return None


def _run_one_page(context: autopilot.Context, url: str) -> Tuple[Dict[str, Any], int, int]:
    try:
        fetched = fetch_public_page(url)
    except SourceError as exc:
        return {
            "url": url,
            "status": "refused",
            "reason": exc.code,
            "message": exc.message,
            "message_zh": exc.message_zh,
        }, 0, 0

    if not fetched["text"]:
        return {"url": url, "status": "done", "found": 0, "added": 0, "seen_again": 0}, 0, 0

    not_ready = _model_ready_for_pages(context.store)
    if not_ready is not None:
        return dict(not_ready, url=url), 0, 0

    if not context.spend(1):
        return dict(
            _skipped(
                autopilot.DAILY_CAP,
                "Daily automatic model call limit reached; skipped reading %s." % url,
                "已达到今日自动模型调用上限，跳过读取 %s。" % url,
            ),
            url=url,
        ), 0, 0

    system, user = page_reader_prompt(context.store, url, fetched["text"])
    temperature = pipeline.STAGE_TEMPERATURES.get("trend_page", 0.2)
    reply = pipeline.complete(context.store, system, user, context.transport, temperature=temperature)
    items = _parse_page_keywords(reply, source_url=url, now=context.now)
    if not items:
        return {"url": url, "status": "done", "found": 0, "added": 0, "seen_again": 0}, 0, 0

    prepared = trends.prepare_found(
        items, row_source=trends.WEB_PAGE, seen_by="page", now=context.now, origin=url, link=url
    )
    if not prepared["rows"]:
        return {"url": url, "status": "done", "found": 0, "added": 0, "seen_again": 0}, 0, 0
    context.record_undo(UNDO_KIND, prepared["undo"])
    stored = trends.store_found(context.store, prepared)
    sightings_added = stored["added"] + stored["seen_again"]
    return {
        "url": url,
        "status": "done",
        "found": stored["found"],
        "added": stored["added"],
        "seen_again": stored["seen_again"],
        "keywords": stored["keywords"][:12],
    }, stored["added"], sightings_added


def run_refresh(context: autopilot.Context) -> Dict[str, Any]:
    """Autopilot ``trends.refresh`` task: runs enabled Source A (Stage 7 lookup) and Source B (public pages)."""
    cfg = clean_source_settings(context.settings.get(SETTINGS_KEY))
    total_added = 0
    total_sightings = 0

    if cfg["ai_lookup"]:
        context.progress("ai_lookup", 0.1)
        ai_summary, added, sighted = _run_ai_lookup(context)
        total_added += added
        total_sightings += sighted
    else:
        ai_summary = {"status": "off"}

    page_summaries: List[Dict[str, Any]] = []
    pages = cfg["pages"]
    for idx, url in enumerate(pages):
        context.progress("reading_page", (idx + 1) / float(len(pages) + 1))
        one, added, sighted = _run_one_page(context, url)
        page_summaries.append(one)
        total_added += added
        total_sightings += sighted

    return {
        "ai_lookup": ai_summary,
        "pages": page_summaries,
        "added": total_added,
        "sightings": total_sightings,
    }


# ---- watched-folder keyword spreadsheet helpers -----------------------------
def matches_keyword_file(path: Union[str, Path], store: Optional[Any] = None,
                         settings: Optional[Mapping[str, Any]] = None) -> bool:
    """True when ``path`` is a ``.csv``, ``.tsv``, or ``.xlsx`` keyword file and ``keyword_files`` is enabled."""
    if store is not None and hasattr(store, "settings") and hasattr(store, "store"):
        settings = store.settings if settings is None else settings
        store = None
    if store is not None and not get_source_settings(store)["keyword_files"]:
        return False
    if settings is not None and not clean_source_settings(settings.get(SETTINGS_KEY))["keyword_files"]:
        return False
    p = Path(path)
    name = p.name
    if not name or name.startswith(".") or name.startswith("~$"):
        return False
    return p.suffix.lower() in KEYWORD_FILE_EXTENSIONS


def take_in_keyword_file(context: autopilot.Context, path: Path) -> Dict[str, Any]:
    """Parse a watched-folder keyword spreadsheet via ``keyword_import`` and store unverified trends + sightings."""
    p = Path(path)
    label = clean_file_name(p.name)
    if not clean_source_settings(context.settings.get(SETTINGS_KEY))["keyword_files"]:
        return {
            "status": "skipped",
            "reason": "source_off",
            "file": label,
            "added": 0,
            "sightings": 0,
        }
    if not matches_keyword_file(p):
        raise SourceError(
            "unsupported_keyword_file",
            "Only .csv, .tsv, and .xlsx keyword files are accepted here.",
            "这里只支持导入 .csv、.tsv 和 .xlsx 关键词表格文件。",
            status=415,
        )
    if p.is_symlink():
        raise SourceError(
            "symlink_refused",
            "Symbolic links in the watched folder are refused.",
            "监控文件夹中的符号链接已被拒绝读取。",
            status=400,
        )
    try:
        with open(str(p), "rb") as handle:
            data = handle.read(keyword_import.MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise SourceError(
            "file_unreadable",
            "Could not read keyword file %s: %s." % (label, exc),
            "无法读取关键词文件 %s：%s。" % (label, exc),
            status=400,
        )

    try:
        table = keyword_import.read_table(label, data=data)
        detected = keyword_import.detect(table["rows"])
        keyword_col = detected["keyword_column"]
        if keyword_col is None:
            raise keyword_import._no_keyword_column()
        items, _shown, _counts = keyword_import.plan(
            table["rows"], detected["header_row"], keyword_col, detected["heat_column"]
        )
        if not items:
            raise keyword_import._no_keywords()
    except keyword_import.ImportRefused as exc:
        raise SourceError(exc.code, exc.message, exc.message_zh, status=exc.status)

    prepared = trends.prepare_found(
        items, row_source=trends.FILE, seen_by="file", now=context.now, origin=label
    )
    if not prepared["rows"]:
        return {"status": "done", "file": label, "found": 0, "added": 0, "seen_again": 0, "sightings": 0}
    context.record_undo(UNDO_KIND, prepared["undo"])
    stored = trends.store_found(context.store, prepared)
    return {
        "status": "done",
        "file": label,
        "found": stored["found"],
        "added": stored["added"],
        "seen_again": stored["seen_again"],
        "left_out_full": stored["left_out_full"],
        "sightings": stored["added"] + stored["seen_again"],
        "keywords": stored["keywords"][:24],
    }


def _undo_trends_added(store: "Store", payload: Any) -> None:
    trends.remove_found(store, payload)


autopilot.register_undo(UNDO_KIND, _undo_trends_added)
TASK = autopilot.register_task(TASK_NAME, run_refresh, every_sec=EVERY_SEC, calls_model=True)
