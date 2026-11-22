"""Tests for the 0.2.0 hardening of the server, the storage and the file readers.

Each class covers one fix, named for what the creator can rely on afterwards:

- library text has a length limit, on every way in;
- a model on this computer is never reached through a proxy;
- a hostile .docx neither holds the server nor fills its memory, and is refused as a bad file;
- a provider's reply is read up to a fixed size and refused beyond it;
- a file is written whole or not at all, a damaged profile file stops no page and is never thrown away,
  and two settings saves at once both survive;
- a save based on an older version of a script is refused instead of replacing newer text;
- the data folder and everything in it is closed to other accounts, also on an existing installation;
- what an AI reports never lands on a keyword the creator typed;
- no page may frame the app, and a content security policy allows the pages only what they use;
- NaN and Infinity are refused on the way in and never written or sent;
- a request states its size in digits, an idle connection is closed, and two slow patterns are linear;
- earlier voice profiles can be listed and one of them put back in use.

Timing tests use the same generous limit as tests/test_security.py: the fixed code needs milliseconds, the
code before the fix needed many seconds for the same input.
"""
import base64
import bz2
import contextlib
import http.client
import io
import json
import lzma
import os
import random
import re
import shutil
import socket
import sqlite3
import stat
import struct
import tempfile
import threading
import time
import tracemalloc
import unittest
import urllib.request
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from studio import corpus, llm, server, timing, trends
from studio import store as store_module
from studio.store import ROOT, Store

EXAMPLE = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
HER_SCRIPT = "\n".join(["今天去超市买菜 / 结果人特别多嘛 / 排队排了半小时啦"] * 12)
SLOW = 3.0              # seconds
LIBRARY_LIMIT = 500000  # characters; the number in contract 4
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; style-src-elem 'self'; "
          "style-src-attr 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
          "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MB = 1024 * 1024


def _not_json(name):
    raise ValueError("%s is not JSON" % name)


def strict_json(raw):
    """Read an answer the way a browser does: NaN and Infinity are not JSON."""
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw, parse_constant=_not_json)


def traced_peak(call):
    """``(what call() returned or raised, the most memory in bytes it held at one time)``."""
    was_tracing = tracemalloc.is_tracing()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        try:
            outcome = call()
        except Exception as exc:        # handed back, for the caller to judge
            outcome = exc
        return outcome, tracemalloc.get_traced_memory()[1] - before
    finally:
        if not was_tracing:
            tracemalloc.stop()


@contextlib.contextmanager
def loopback_only():
    """While this is active the process can connect to this computer only and look up no other name.

    Yields the list of attempts that were refused. Tests that exercise the real model client run inside it,
    so that a mistake in the client or in a test cannot send anything anywhere.
    """
    refused = []
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def local(host):
        host = str(host)
        return host in ("localhost", "::1") or host.startswith("127.")

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if not local(host):
            refused.append(("connect", address))
            raise OSError("test: only this computer may be reached")
        return real_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if host is not None and not local(host):
            refused.append(("lookup", host))
            raise socket.gaierror("test: only this computer may be looked up")
        return real_lookup(host, *args, **kwargs)

    with mock.patch.object(socket.socket, "connect", connect), mock.patch.object(socket, "getaddrinfo", lookup):
        yield refused


@contextlib.contextmanager
def proxy_environment(proxy_url):
    """The environment of a computer where an HTTP proxy is configured and nothing is exempt from it.

    urllib builds its default opener once and keeps the proxy settings it saw then, so that opener is put
    aside for the duration and restored afterwards: the setting is seen here and does not leak into other tests.
    """
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method")}
    env.update(http_proxy=proxy_url, HTTP_PROXY=proxy_url)
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(urllib.request, "_opener", None):
        yield


def json_answer(payload, status=200):
    def answer(handler):
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    return answer


class Tree:
    """Modes of everything under a folder."""

    @staticmethod
    def modes(root):
        out = {".": stat.S_IMODE(os.lstat(str(root)).st_mode)}
        for folder, folders, files in os.walk(str(root)):
            for name in folders + files:
                path = os.path.join(folder, name)
                if not os.path.islink(path):
                    out[os.path.relpath(path, str(root))] = stat.S_IMODE(os.lstat(path).st_mode)
        return out

    @staticmethod
    def open_to_others(root):
        return {name: oct(mode) for name, mode in Tree.modes(root).items() if mode & 0o077}


class Transcriber:
    """Stands in for studio.transcribe: local_tools/ is never touched."""

    def __init__(self):
        self.result = {"segments": [{"start": 0.0, "end": 12.0, "text": "今天去超市买菜结果人特别多排队排了半小时"}],
                       "unclear_dropped": 0}
        self.more_status = {}

    def status(self):
        return dict({"supported": True, "installed": True, "model_ready": True, "reason": None}, **self.more_status)

    def install(self, progress=None):
        pass

    def uninstall(self):
        pass

    def transcribe_file(self, path, progress=None):
        return self.result


class StudioCase(unittest.TestCase):
    """A running studio on a temporary data folder, with stand-ins for the model and the transcriber."""

    use_real_model_client = False       # True: no stand-in transport, so studio.llm's own client is exercised

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.data = self.tmp / "data"
        self.store = Store(self.data)
        self.provider_calls = []
        self.transcriber = Transcriber()
        self.httpd = server.make_server(0, self.store, None if self.use_real_model_client else self._transport,
                                        self.transcriber)
        self.port = self.httpd.server_address[1]
        # A short poll interval: the default makes every test wait up to half a second for the server to stop.
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.listeners = []

    def _stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        for listener in self.listeners:
            listener.shutdown()
            listener.server_close()

    def _transport(self, method, url, headers, body):
        self.provider_calls.append((method, url))
        return {}

    def listen(self, answer):
        """A stand-in HTTP server on a free port of this computer. Returns ``(base address, requests seen)``."""
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                seen.append((self.command, self.path, self.rfile.read(length) if length else b""))
                answer(self)

            do_GET = do_POST = _any

        listener = server.StudioServer(("127.0.0.1", 0), Handler)
        listener.daemon_threads = True
        threading.Thread(target=listener.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.listeners.append(listener)
        return "http://127.0.0.1:%d" % listener.server_address[1], seen

    def send(self, method, path, payload=None, body=None, headers=None, timeout=60):
        """One request. ``payload`` goes as JSON, ``body`` as it is. Returns ``(status, answer, headers)``.

        A JSON answer is read strictly (``strict_json``), so an answer a browser could not read fails the test.
        """
        head = {"Host": "127.0.0.1:%d" % self.port}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        head.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=head)
            res = conn.getresponse()
            raw = res.read()
        finally:
            conn.close()
        if "json" in (res.getheader("Content-Type") or ""):
            return res.status, strict_json(raw), res.headers
        return res.status, raw, res.headers

    def raw(self, request, timeout=4.0):
        """Send bytes as they are and read until the server closes. Returns ``(status, answer, raw head)``."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        try:
            sock.sendall(request)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            sock.close()
        head, _, payload = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), (strict_json(payload) if payload else None), head

    def request_head(self, method, path, *lines):
        return ("%s %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % (method, path, self.port)).encode("latin-1") \
            + b"".join(line + b"\r\n" for line in lines) + b"\r\n"

    def titles(self):
        return sorted(item["title"] for item in self.send("GET", "/api/samples")[1]["samples"])

    def files(self, folder=None):
        folder = folder or self.data
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def assert_both_languages(self, body):
        self.assertTrue(body.get("message") and body.get("message_zh"), body)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", body["message_zh"]), body)
        self.assertFalse(re.search(r"[\u4e00-\u9fff]", body["message"]), body)


# ---------------------------------------------------------------------------------------------- 1. library text limit
class LibraryTextLimitTest(StudioCase):
    def test_text_over_the_limit_is_refused_and_text_at_the_limit_is_kept(self):
        status, body, _ = self.send("POST", "/api/samples", {"title": "too long", "text": "好" * (LIBRARY_LIMIT + 1)})
        self.assertEqual((status, body.get("error"), body.get("limit")), (413, "text_too_long", LIBRARY_LIMIT),
                         {key: value for key, value in body.items() if key != "text"})
        self.assert_both_languages(body)
        self.assertIn("500,000", body["message"])
        self.assertIn("500,000", body["message_zh"])
        self.assertEqual(self.titles(), [])
        status, kept, _ = self.send("POST", "/api/samples", {"title": "longest", "text": "好" * LIBRARY_LIMIT})
        self.assertEqual((status, len(kept["text"])), (201, LIBRARY_LIMIT))
        self.assertEqual(store_module.MAX_SAMPLE_CHARS, LIBRARY_LIMIT)

    def test_the_limit_holds_for_every_kind_of_item(self):
        with mock.patch.object(store_module, "MAX_SAMPLE_CHARS", 2000):
            long_text = "今天去超市买菜" * 300                                   # 2,100 characters
            cues = "\n".join("%d\n00:00:%02d,000 --> 00:00:%02d,900\n%s\n" % (n + 1, n, n, "今天去超市买菜" * 15) for n in range(30))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", "<w:p ><w:r><w:t>%s</w:t></w:r></w:p>" % long_text)
            for label, item in (("typed", {"text": long_text}), ("subtitles", {"subtitle_text": cues}),
                                ("docx", {"docx_base64": base64.b64encode(buffer.getvalue()).decode()})):
                status, body, _ = self.send("POST", "/api/samples", dict(item, title=label))
                self.assertEqual((status, body.get("error"), body.get("limit")), (413, "text_too_long", 2000), label)
                self.assert_both_languages(body)
                self.assertIn("2,000", body["message"])
            self.assertEqual(self.titles(), [])
            with self.assertRaises(store_module.TextTooLong) as refused:         # and for any caller of the store
                self.store.create_sample(title="direct", text=long_text)
            self.assertEqual((refused.exception.code, refused.exception.limit, refused.exception.length),
                             ("text_too_long", 2000, 2100))
            self.assertIsInstance(refused.exception, ValueError)

    def test_editing_cannot_take_an_item_over_the_limit(self):
        _, made, _ = self.send("POST", "/api/samples", {"title": "mine", "text": "今天去超市买菜"})
        with mock.patch.object(store_module, "MAX_SAMPLE_CHARS", 2000):
            status, body, _ = self.send("PUT", "/api/samples/" + made["id"], {"title": "changed", "text": "好" * 2001})
            self.assertEqual((status, body.get("error")), (413, "text_too_long"))
            self.assert_both_languages(body)
            now = self.store.get_sample(made["id"])
            self.assertEqual((now["title"], now["text"]), ("mine", "今天去超市买菜"))      # none of the edit was applied
            status, body, _ = self.send("PUT", "/api/samples/" + made["id"], {"text": "好" * 2000})
            self.assertEqual((status, len(body["text"])), (200, 2000))

    def test_a_transcript_over_the_limit_fails_its_job_and_says_why(self):
        self.transcriber.result = {"segments": [{"start": float(n), "end": n + 0.9, "text": "今天去超市买菜" * 15}
                                                for n in range(30)], "unclear_dropped": 0}
        with mock.patch.object(store_module, "MAX_SAMPLE_CHARS", 2000):
            status, upload, _ = self.send("PUT", "/api/uploads?name=long.mp4", body=b"video bytes",
                                          headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(status, 201, upload)
            status, started, _ = self.send("POST", "/api/videos", {"upload_id": upload["upload_id"]})
            self.assertEqual(status, 202, started)
            job = self.httpd.app.jobs.wait(started["job"]["id"], 20)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "text_too_long"), job)
        self.assert_both_languages(job["error"])
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.files(self.data / "uploads"), [])                  # and the copy of the video is gone


# ---------------------------------------------------------------------------------------------- 2. no proxy for a local model
class LocalModelProxyTest(StudioCase):
    use_real_model_client = True
    REPLY = {"choices": [{"message": {"content": json.dumps({"script_lines": [{"spoken_script_zh": "今天去超市买菜"}]})}}],
             "data": [{"id": "local-model"}]}

    def setUp(self):
        super().setUp()
        self.model, self.model_seen = self.listen(json_answer(self.REPLY))
        self.proxy, self.proxy_seen = self.listen(json_answer({"error": "this is the proxy"}, 502))

    def test_a_local_model_is_reached_directly_when_a_proxy_is_configured(self):
        with loopback_only() as refused, proxy_environment(self.proxy):
            reply = llm.complete_json("local", "", "local-model", "SYSTEM", "the creator's notes and scripts",
                                      base_url=self.model + "/v1")
            models = llm.list_models("local", base_url=self.model.replace("127.0.0.1", "localhost") + "/v1")
        self.assertEqual(reply, {"script_lines": [{"spoken_script_zh": "今天去超市买菜"}]})
        self.assertEqual(models, ["local-model"])
        self.assertEqual(self.proxy_seen, [])                                    # the proxy was handed nothing
        self.assertEqual([(method, path) for method, path, _ in self.model_seen],
                         [("POST", "/v1/chat/completions"), ("GET", "/v1/models")])
        self.assertIn("the creator's notes and scripts", self.model_seen[0][2].decode("utf-8"))
        self.assertEqual(refused, [])

    def test_the_studio_reaches_a_local_model_directly_too(self):
        with loopback_only() as refused, proxy_environment(self.proxy):
            status, body, _ = self.send("POST", "/api/settings/models", {"provider": "local", "base_url": self.model + "/v1"})
        self.assertEqual((status, body.get("models")), (200, ["local-model"]), body)
        self.assertEqual(self.proxy_seen, [])
        self.assertEqual(refused, [])

    def test_other_addresses_still_go_through_the_proxy(self):
        """On some networks the proxy is the only way out, so a cloud provider must keep using it."""
        with loopback_only() as refused, proxy_environment(self.proxy):
            with self.assertRaises(llm.LLMError) as failed:
                llm._http("GET", "http://provider.invalid/v1/models", {}, None)
        self.assertEqual(failed.exception.code, "http")                          # the proxy's own 502
        self.assertEqual([(method, path) for method, path, _ in self.proxy_seen], [("GET", "http://provider.invalid/v1/models")])
        self.assertEqual(refused, [])                                            # and nothing tried to leave this computer


# ---------------------------------------------------------------------------------------------- 3. the .docx reader
def docx(xml, method=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def raw_zip(stream, method, declared_size, crc, flags=0, version_needed=20, name="word/document.xml"):
    """A zip with one entry whose headers say what the test chooses, whatever the stream really holds."""
    encoded = name.encode()
    local = struct.pack("<IHHHHHIIIHH", 0x04034b50, version_needed, flags, method, 0, 0x21, crc, len(stream), declared_size,
                        len(encoded), 0) + encoded
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, version_needed, version_needed, flags, method, 0, 0x21, crc,
                          len(stream), declared_size, len(encoded), 0, 0, 0, 0, 0, 0) + encoded
    body = local + stream
    return body + central + struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, 1, 1, len(central), len(body), 0)


def deflated_zeros(megabytes):
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    chunk = b"\0" * (4 * MB)
    return b"".join([packer.compress(chunk) for _ in range(megabytes // 4)] + [packer.flush()])


class DocxTest(StudioCase):
    GOOD = '<w:document><w:body><w:p w:x="1"><w:r><w:t>今天去超市买菜</w:t></w:r></w:p></w:body></w:document>'.encode("utf-8")

    def add_docx(self, data):
        return self.send("POST", "/api/samples", {"title": "t", "docx_base64": base64.b64encode(data).decode()})

    def test_ordinary_documents_read_as_before(self):
        def para(text, attrs=' w:rsidR="1"'):
            return ('<w:p%s><w:pPr><w:jc w:val="left"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                    % (attrs, text))
        cases = [
            ("<w:document><w:body>%s%s%s</w:body></w:document>" % (para("周日去超市 &amp; 补货"), para(""), para("结果某人又走不动道哈")),
             "周日去超市 & 补货\n\n结果某人又走不动道哈"),
            ("<w:body>" + para("第一行<w:br/>") + '<w:p><w:r><w:t>无属性段落</w:t><w:tab/><w:t>制表后</w:t></w:r><w:r>'
             '<w:br w:type="page"/><w:t>换页后</w:t></w:r></w:p></w:body>', "第一行\n\n无属性段落制表后换页后"),
            ("<w:body><w:p ><w:r><w:t>a</w:t></w:r><w:tbl><w:tr><w:tc><w:p ><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body>",
             "acell"),
            ("<w:body><w:p ><w:r><w:t>unclosed paragraph at the end", ""),
            ("<w:body><w:pPr/><w:p><w:r><w:t>only this</w:t></w:r></w:p><w:pict/></w:body>", "only this"),
            ("<w:body>" + "".join(para("第%d句" % n) for n in range(300)) + "</w:body>", "\n".join("第%d句" % n for n in range(300))),
        ]
        for xml, expected in cases:
            for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
                self.assertEqual(corpus.extract_docx_text(docx(xml, method)), expected, xml[:60])
        status, item, _ = self.add_docx(docx(self.GOOD))
        self.assertEqual((status, item["text"]), (201, "今天去超市买菜"))

    def test_tags_that_are_never_closed_do_not_hold_the_server(self):
        hostile = {"paragraphs": "<w:p >" * 32000,
                   "runs": "<w:p >" + "<w:t>" * 16000 + "</w:p>",
                   "line breaks": "<w:p >" + "<w:br " * 32000 + "</w:p>",
                   "tabs": "<w:p >" + "<w:tab " * 32000 + "</w:p>",
                   "run attributes": "<w:p >" + "<w:t " * 32000 + "</w:p>"}
        for label, xml in hostile.items():
            data = docx(xml)
            self.assertLess(len(data), 2000, label)                              # a file of a few hundred bytes
            started = time.time()
            self.assertEqual(corpus.extract_docx_text(data), "", label)
            self.assertLess(time.time() - started, SLOW, label)
        started = time.time()                                                    # as large as the reader accepts at all
        self.assertEqual(corpus.extract_docx_text(docx("<w:p >" * (corpus.MAX_DOCX_XML_BYTES // 6 - 10))), "")
        self.assertLess(time.time() - started, SLOW)
        started = time.time()
        status, body, _ = self.add_docx(docx("<w:p >" * 32000))
        self.assertEqual((status, body.get("error")), (400, "empty_text"))
        self.assertLess(time.time() - started, SLOW)

    def test_an_archive_that_lies_about_its_size_is_not_expanded(self):
        """The entry really holds 128 MB; its header says 100 bytes, under the 20 MB the reader accepts."""
        data = raw_zip(deflated_zeros(128), zipfile.ZIP_DEFLATED, 100, zlib.crc32(b"\0" * 100))
        self.assertLess(len(data), MB)
        outcome, peak = traced_peak(lambda: corpus.extract_docx_text(data))
        self.assertTrue(outcome == "" or isinstance(outcome, corpus.ImportError_), repr(outcome)[:80])
        self.assertLess(peak, 3 * corpus.MAX_DOCX_XML_BYTES, "memory used while reading: %d MB" % (peak // MB))
        status, body, _ = self.add_docx(data)
        self.assertEqual(status, 400, body)
        honest = raw_zip(deflated_zeros(64), zipfile.ZIP_DEFLATED, 64 * MB, 0)   # says 64 MB: refused unread, as before
        self.assertEqual(self.add_docx(honest)[1].get("error"), "bad_file")

    def test_an_archive_packed_another_way_is_refused_unread(self):
        """bzip2 and LZMA entries expand with no ceiling at all, and Word never writes them."""
        packed = bz2.compress(b"\0" * (64 * MB), 9)
        lying = raw_zip(packed, zipfile.ZIP_BZIP2, 100, zlib.crc32(b"\0" * 100), version_needed=46)
        outcome, peak = traced_peak(lambda: corpus.extract_docx_text(lying))
        self.assertIsInstance(outcome, corpus.ImportError_)
        self.assertLess(peak, 8 * MB, "memory used while refusing: %d MB" % (peak // MB))
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            status, body, _ = self.add_docx(docx(self.GOOD, method))
            self.assertEqual((status, body.get("error")), (400, "bad_file"), method)

    def test_whatever_is_wrong_with_the_archive_it_is_a_bad_file_not_a_server_error(self):
        good, crc = self.GOOD, zlib.crc32(self.GOOD)
        cases = {
            "unknown compression method": raw_zip(good, 99, len(good), crc),
            "entry marked as encrypted": raw_zip(good, zipfile.ZIP_STORED, len(good), crc, flags=1),
            "damaged deflate stream": raw_zip(b"\xff" * 64, zipfile.ZIP_DEFLATED, 100, 0),
            "deflate stream cut short": raw_zip(zlib.compress(good * 50)[2:20], zipfile.ZIP_DEFLATED, len(good) * 50, 0),
            "wrong checksum": raw_zip(good, zipfile.ZIP_STORED, len(good), 12345),
            "needs a newer zip version": raw_zip(good, zipfile.ZIP_STORED, len(good), crc, version_needed=990),
            "no document inside": raw_zip(good, zipfile.ZIP_STORED, len(good), crc, name="word/other.xml"),
            "not a zip": b"plain text",
            "empty": b"",
        }
        for label, data in cases.items():
            with self.assertRaises(corpus.ImportError_, msg=label):
                corpus.extract_docx_text(data)
            if data:
                status, body, _ = self.add_docx(data)
                self.assertEqual((status, body.get("error")), (400, "bad_file"), label)
                self.assert_both_languages(body)
        status, body, _ = self.send("POST", "/api/samples", {"title": "t", "docx_base64": "!!!not base64!!!"})
        self.assertEqual((status, body.get("error")), (400, "bad_file"))
        self.assertEqual(self.titles(), [])


# ---------------------------------------------------------------------------------------------- 4. size of a provider's reply
class ReplySizeTest(StudioCase):
    use_real_model_client = True

    def endless(self, status=200, megabytes=48):
        """A provider that keeps sending: up to ``megabytes`` of white space, or until the reader hangs up."""
        sent = [0]

        def answer(handler):
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            chunk = b" " * MB
            try:
                for _ in range(megabytes):
                    handler.wfile.write(chunk)
                    sent[0] += len(chunk)
            except OSError:
                pass

        return self.listen(answer)[0], sent

    def test_a_reply_without_end_is_cut_off_and_refused(self):
        address, sent = self.endless()
        started = time.time()
        with loopback_only(), self.assertRaises(llm.LLMError) as failed:
            llm._http("GET", address + "/v1/models", {}, None)
        time.sleep(0.2)                                                          # let the stand-in notice the hang-up
        self.assertLess(sent[0], 24 * MB, "the stand-in got to send %d MB" % (sent[0] // MB))
        self.assertEqual(failed.exception.code, "too_large")
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", failed.exception.message_zh))
        self.assertLess(time.time() - started, SLOW)
        self.assertLessEqual(llm.MAX_REPLY_BYTES, 8 * MB)

    def test_the_page_is_told_in_both_languages(self):
        address, _ = self.endless()
        with loopback_only():
            status, body, _ = self.send("POST", "/api/settings/models", {"provider": "local", "base_url": address + "/v1"})
        self.assertEqual((status, body.get("error"), body.get("reason")), (502, "ai_failed", "too_large"), body)
        self.assert_both_languages(body)
        self.assertEqual(self.store.public_settings()["local_base_url"], "")     # an address that failed is not remembered

    def test_an_error_reply_without_end_is_cut_off_too(self):
        address, sent = self.endless(status=500)
        started = time.time()
        with loopback_only(), self.assertRaises(llm.LLMError) as failed:
            llm._http("GET", address + "/v1/models", {}, None)
        self.assertEqual(failed.exception.code, "http")
        self.assertLess(len(str(failed.exception)), 400)
        self.assertLess(time.time() - started, SLOW)
        time.sleep(0.2)
        self.assertLess(sent[0], 16 * MB, "the stand-in got to send %d MB" % (sent[0] // MB))

    def test_replies_of_an_ordinary_size_are_read_whole(self):
        models = {"data": [{"id": "model-%04d" % n, "object": "model", "owned_by": "x" * 400} for n in range(4000)]}
        address, _ = self.listen(json_answer(models))                            # about 2 MB
        with loopback_only():
            self.assertEqual(len(llm.list_models("local", base_url=address + "/v1")), 4000)


# ---------------------------------------------------------------------------------------------- 5. files written whole; damaged files; settings
class FileWriteTest(StudioCase):
    def leftovers(self):
        return [name for name in self.files() if name.endswith(".tmp")]

    def test_a_write_that_fails_leaves_the_old_file_and_nothing_else(self):
        self.store.save_creator({"name": "first"})
        before = self.store.creator_path.read_bytes()
        with mock.patch.object(os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                self.store.save_creator({"name": "second"})
        self.assertEqual(self.store.creator_path.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])
        with mock.patch.object(os, "fsync", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                self.store.save_creator({"name": "third"})
        self.assertEqual(self.store.creator_path.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])
        self.store.save_creator({"name": "fourth"})                              # and saving works again afterwards
        self.assertEqual(self.store.load_creator(), {"name": "fourth"})

    def test_the_contents_are_on_the_disk_before_the_file_is_put_in_place(self):
        events = []
        real_sync, real_replace = os.fsync, os.replace

        def sync(fd):
            events.append(("sync", stat.S_ISREG(os.fstat(fd).st_mode), os.fstat(fd).st_size))
            return real_sync(fd)

        def replace(source, target, **kwargs):
            events.append(("replace", os.path.basename(str(target))))
            return real_replace(source, target, **kwargs)

        with mock.patch.object(os, "fsync", sync), mock.patch.object(os, "replace", replace):
            self.store.save_creator({"name": "written and synced"})
        size = self.store.creator_path.stat().st_size
        self.assertIn(("sync", True, size), events)                              # the whole file, synced
        self.assertLess(events.index(("sync", True, size)), events.index(("replace", "creator.json")))

    def test_a_stray_temporary_file_can_neither_break_a_save_nor_loosen_the_file(self):
        """What a crash of an earlier version could leave behind: ``config.json.tmp``, readable by everyone."""
        stray = self.data / "config.json.tmp"
        stray.write_text("left behind")
        os.chmod(str(stray), 0o644)
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "openai", "model": "m", "key": "sk-TEST-NOT-REAL"})[0], 200)
        if os.name == "posix":            # Windows has no such modes: there the folder decides who may read the file
            self.assertEqual(stat.S_IMODE(self.store.config_path.stat().st_mode), 0o600)
        self.assertEqual(self.store.load_config()["keys"], {"openai": "sk-TEST-NOT-REAL"})
        self.assertEqual(stray.read_text(), "left behind")                       # not this save's file: left alone

    def test_saves_at_the_same_moment_do_not_trip_over_each_other(self):
        errors = []

        def save(worker):
            try:
                for n in range(60):
                    self.store.save_recommendations({"worker": worker, "n": n, "padding": "x" * 2000})
            except Exception as exc:                                             # the point of the test
                errors.append(repr(exc))

        threads = [threading.Thread(target=save, args=(worker,)) for worker in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.store.load_recommendations()["n"], 59)             # a whole file, from one of the last saves
        self.assertEqual(self.leftovers(), [])

    def test_a_profile_that_cannot_be_stored_touches_nothing(self):
        self.store.save_dna(dict(EXAMPLE, profile_version="mine"))
        before = self.store.dna_path.read_bytes()
        for unstorable in ({"note": "\ud800"}, {"rate": float("nan")}):
            with self.assertRaises(ValueError):                                  # UnicodeEncodeError is one
                self.store.save_dna(dict(EXAMPLE, **unstorable))
            self.assertEqual(self.store.dna_path.read_bytes(), before)
            self.assertEqual(self.files(), ["studio.db", "voice_dna.json"])      # no history copy of a save that did not happen


class DamagedProfileTest(StudioCase):
    DAMAGED = {"empty": b"", "cut short": b'{"persona_summary": {"one_line_essence_zh": "\xe6\x9d\xbe', "a list": b"[]",
               "a word": b'"profile"', "not text": b"\xff\xfe\x00\x01\x02"}
    PAGES = (("GET", "/api/state", None), ("GET", "/api/dna", None), ("GET", "/api/setup", None), ("GET", "/api/samples", None),
             ("GET", "/api/trends", None), ("GET", "/api/voice/history", None),
             ("POST", "/api/lint", {"lines": [{"line_no": 1, "spoken_script_zh": "今天去超市买菜"}], "target_sec": 60}))

    def test_no_page_fails_and_the_creator_is_told(self):
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        for label, content in self.DAMAGED.items():
            self.store.dna_path.write_bytes(content)
            for method, path, payload in self.PAGES:
                status, body, _ = self.send(method, path, payload)
                self.assertEqual(status, 200, "%s: %s %s -> %r" % (label, method, path, body))
            state, dna, setup = (self.send("GET", path)[1] for path in ("/api/state", "/api/dna", "/api/setup"))
            self.assertEqual((state["dna_is_example"], state.get("dna_unreadable")), (True, True), label)
            self.assertEqual((dna["is_example"], dna.get("unreadable"), dna["dna"]), (True, True, EXAMPLE), label)
            self.assertEqual((setup["voice"]["state"], setup["voice"].get("unreadable")), ("example", True), label)
            self.assertEqual(self.store.dna_path.read_bytes(), content, label)   # the file was not touched
            self.assertEqual(self.files(), ["studio.db", "voice_dna.json"], label)

    def test_the_flag_is_absent_when_nothing_is_wrong(self):
        for expected_example in (True, False):
            state, dna, setup = (self.send("GET", path)[1] for path in ("/api/state", "/api/dna", "/api/setup"))
            self.assertEqual((dna["is_example"], "unreadable" in dna, "dna_unreadable" in state, "unreadable" in setup["voice"]),
                             (expected_example, False, False, False))
            self.store.save_dna(dict(EXAMPLE, profile_version="mine"))

    def test_a_damaged_file_is_kept_when_a_new_profile_replaces_it(self):
        content = self.DAMAGED["cut short"]
        self.store.dna_path.write_bytes(content)
        self.store.save_dna(dict(EXAMPLE, profile_version="learned again"))
        self.assertEqual(self.send("GET", "/api/dna")[1], {"dna": dict(EXAMPLE, profile_version="learned again"), "is_example": False})
        kept = self.files(self.store.dna_history_dir)
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].endswith(".unreadable"), kept)
        self.assertEqual((self.store.dna_history_dir / kept[0]).read_bytes(), content)
        self.assertEqual(self.send("GET", "/api/voice/history")[1], {"history": []})     # and never offered as a profile


class SettingsSaveTest(StudioCase):
    def test_two_saves_at_the_same_moment_both_survive(self):
        """One request has read the settings and not yet saved; another saves in between."""
        real_load, holding, release, first = self.store.load_config, threading.Event(), threading.Event(), [True]

        def load_and_wait():
            config = real_load()
            if first[0] and threading.current_thread() is not threading.main_thread():
                first[0] = False
                holding.set()
                release.wait(10)
            return config

        self.store.load_config = load_and_wait
        self.addCleanup(setattr, self.store, "load_config", real_load)
        slow = threading.Thread(target=self.send, args=("PUT", "/api/trends/weight", {"weight": "mine"}))
        slow.start()
        self.assertTrue(holding.wait(10))
        other = threading.Thread(target=self.send, args=("POST", "/api/settings", {"provider": "manual"}))
        other.start()
        other.join(1.0)                 # unprotected, it has saved by now; protected, it waits its turn
        release.set()
        slow.join(10)
        other.join(10)
        config = real_load()
        self.assertEqual((config["provider"], config["trend_weight"]), ("manual", "mine"))

    def test_each_route_changes_only_its_own_settings(self):
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "openai", "model": "m", "key": "sk-TEST-NOT-REAL"})[0], 200)
        self.assertEqual(self.send("PUT", "/api/trends/weight", {"weight": "trend"})[0], 200)
        self.assertEqual(self.send("POST", "/api/settings/models", {"provider": "gemini", "key": "g-TEST-NOT-REAL"})[0], 200)
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "local", "model": "l", "base_url": "http://localhost:11434/v1/"})[0], 200)
        config = self.store.load_config()
        self.assertEqual(config, {"provider": "local", "model": "l", "trend_weight": "trend", "local_base_url": "http://localhost:11434/v1",
                                  "keys": {"openai": "sk-TEST-NOT-REAL", "gemini": "g-TEST-NOT-REAL"}})
        status, body, _ = self.send("POST", "/api/settings", {"provider": "nobody", "model": "x"})
        self.assertEqual((status, body.get("error")), (400, "bad_provider"))
        self.assertEqual(self.store.load_config(), config)                       # a refused save changes nothing
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "manual"})[0], 200)
        self.assertEqual(self.store.load_config(), dict(config, provider="manual"))


# ---------------------------------------------------------------------------------------------- 6. two tabs on one script
class StaleScriptTest(StudioCase):
    def setUp(self):
        super().setUp()
        _, made, _ = self.send("POST", "/api/scripts", {"title": "t", "lines": [{"spoken_script_zh": "第一版"}]})
        self.sid = made["id"]
        self.path = "/api/scripts/" + self.sid

    def test_the_older_tab_cannot_overwrite_newer_text(self):
        tab_a = self.send("GET", self.path)[1]                                   # tab A opens the script
        status, tab_b, _ = self.send("PUT", self.path, {                         # tab B, opened at the same version, adds a paragraph
            "lines": tab_a["lines"] + [{"spoken_script_zh": "另一个标签页写的一整段"}], "base_updated_at": tab_a["updated_at"]})
        self.assertEqual(status, 200, tab_b)
        self.assertGreater(tab_b["updated_at"], tab_a["updated_at"])

        status, body, _ = self.send("PUT", self.path, {                          # tab A saves what it has
            "lines": tab_a["lines"], "title": "edited in tab A", "base_updated_at": tab_a["updated_at"]})
        self.assertEqual((status, body.get("error"), body.get("updated_at")), (409, "stale_script", tab_b["updated_at"]), body)
        self.assert_both_languages(body)
        now = self.send("GET", self.path)[1]
        self.assertEqual(now, tab_b)                                             # nothing changed, the paragraph is still there

        status, saved, _ = self.send("PUT", self.path, {"title": "after reloading", "base_updated_at": now["updated_at"]})
        self.assertEqual((status, saved["title"], len(saved["lines"])), (200, "after reloading", 2))

    def test_a_save_that_names_no_version_works_as_before(self):
        first = self.send("GET", self.path)[1]
        self.send("PUT", self.path, {"title": "second"})
        status, body, _ = self.send("PUT", self.path, {"title": "third"})        # what MCP and older pages send
        self.assertEqual((status, body["title"]), (200, "third"))
        self.assertEqual(self.store.update_script(self.sid, {"title": "fourth"})["title"], "fourth")
        with self.assertRaises(store_module.StaleScript) as stale:               # the rule lives in the store, for every caller
            self.store.update_script(self.sid, {"title": "fifth"}, base_updated_at=first["updated_at"])
        self.assertEqual(stale.exception.updated_at, self.store.get_script(self.sid)["updated_at"])
        self.assertEqual(self.store.get_script(self.sid)["title"], "fourth")

    def test_a_base_that_is_not_a_version_is_refused(self):
        before = self.send("GET", self.path)[1]
        for bad in ("yesterday", True, [before["updated_at"]], {"at": 1}, 10 ** 400):
            status, body, _ = self.send("PUT", self.path, {"title": "changed", "base_updated_at": bad})
            self.assertEqual((status, body.get("error")), (400, "bad_base_updated_at"), bad)
            self.assert_both_languages(body)
        self.assertEqual(self.send("GET", self.path)[1], before)

    def test_every_script_answer_says_when_it_was_last_changed(self):
        status, made, _ = self.send("POST", "/api/scripts", {"title": "new"})
        fetched = self.send("GET", "/api/scripts/" + made["id"])[1]
        changed = self.send("PUT", "/api/scripts/" + made["id"], {"title": "newer"})[1]
        listed = self.send("GET", "/api/scripts")[1]["scripts"]
        for answer in [made, fetched, changed] + listed:
            self.assertIsInstance(answer["updated_at"], float)
        self.assertEqual(made["updated_at"], fetched["updated_at"])
        self.assertGreater(changed["updated_at"], fetched["updated_at"])

    def test_a_newer_version_never_carries_an_older_time(self):
        """The computer's clock can be set back between two saves."""
        before = self.store.get_script(self.sid)["updated_at"]
        with mock.patch.object(store_module.time, "time", return_value=before - 3600):
            after = self.store.update_script(self.sid, {"title": "saved an hour earlier by the clock"})["updated_at"]
            self.assertGreater(after, before)
            with self.assertRaises(store_module.StaleScript):
                self.store.update_script(self.sid, {"title": "from the older version"}, base_updated_at=before)


# ---------------------------------------------------------------------------------------------- 7. the data folder is the owner's only
@unittest.skipUnless(os.name == "posix", "Windows has no file modes. There the app does not close its data folder to other accounts: "
                                         "the folder it is in decides who may read it, as the README says")
class OwnerOnlyTest(StudioCase):
    def setUp(self):
        previous = os.umask(0o022)      # the usual setting, under which a new file is readable by everyone
        self.addCleanup(os.umask, previous)
        super().setUp()

    def use_everything(self):
        """Make the app write every kind of file it has."""
        self.assertEqual(self.send("POST", "/api/settings", {"provider": "openai", "model": "m", "key": "sk-TEST-NOT-REAL"})[0], 200)
        self.assertEqual(self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})[0], 201)
        self.assertEqual(self.send("POST", "/api/scripts", {"title": "t"})[0], 201)
        self.store.save_creator({"name": "小满"})
        self.store.save_dna(dict(EXAMPLE, profile_version="one"))
        self.store.save_dna(dict(EXAMPLE, profile_version="two"))                # the first goes to the history folder
        self.store.save_pending({"dna": EXAMPLE, "report": {}, "created_at": 1.0, "based_on": 1})
        self.store.save_recommendations({"ideas": []})
        status, _, _ = self.send("PUT", "/api/uploads?name=clip.mp4", body=b"video bytes", headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 201)
        expected = {".", "studio.db", "config.json", "creator.json", "voice_dna.json", "voice_pending.json", "recommendations.json",
                    "voice_dna_history", "uploads"}
        self.assertTrue(expected <= set(Tree.modes(self.data)), Tree.modes(self.data))

    def test_a_new_installation_is_closed_to_other_accounts(self):
        self.assertEqual(Tree.modes(self.data)["."], 0o700)
        self.use_everything()
        self.assertEqual(Tree.open_to_others(self.data), {})
        modes = Tree.modes(self.data)
        self.assertEqual((modes["."], modes["voice_dna_history"], modes["uploads"]), (0o700, 0o700, 0o700))
        self.assertEqual({mode for name, mode in modes.items() if os.path.isfile(str(self.data / name))}, {0o600})

    def test_an_installation_that_already_exists_is_closed_when_it_is_opened(self):
        self.use_everything()
        (self.data / "exports").mkdir()
        (self.data / "exports" / "kit.zip").write_bytes(b"made by an earlier version")
        (self.data / "notes.txt").write_text("something the creator put here")
        for folder, folders, files in os.walk(str(self.data)):                  # as an earlier version left it
            os.chmod(folder, 0o755)
            for name in files:
                os.chmod(os.path.join(folder, name), 0o644)
        os.chmod(str(self.data / "notes.txt"), 0o444)                            # read-only for its owner: that stays
        self.assertGreater(len(Tree.open_to_others(self.data)), 10)
        Store(self.data)                                                         # the next start
        self.assertEqual(Tree.open_to_others(self.data), {})
        self.assertEqual(Tree.modes(self.data)["notes.txt"], 0o400)
        self.assertEqual((self.data / "notes.txt").read_text(), "something the creator put here")

    def test_links_out_of_the_folder_are_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "theirs.txt").write_text("not the app's file")
        os.chmod(str(outside), 0o755)
        os.chmod(str(outside / "theirs.txt"), 0o644)
        os.symlink(str(outside / "theirs.txt"), str(self.data / "link-to-file"))
        os.symlink(str(outside), str(self.data / "link-to-folder"))
        Store(self.data)
        self.assertEqual((Tree.modes(outside)["."], Tree.modes(outside)["theirs.txt"]), (0o755, 0o644))


# ---------------------------------------------------------------------------------------------- 8. keywords the creator typed
class CreatorKeywordTest(StudioCase):
    DAY = 86400.0
    FOUND = {"keyword": "#秋冬 护肤", "source_url": "https://example.com/?creator=x&titles=y", "note_zh": "模型写的一句话",
             "note_en": "A sentence the model wrote"}

    def row(self, keyword):
        return {t["keyword"]: t for t in self.store.list_trends()}[keyword]

    def test_what_an_ai_reports_does_not_land_on_a_keyword_the_creator_typed(self):
        typed_at = time.time() - 40 * self.DAY
        self.store.upsert_trends([{"keyword": "秋冬护肤", "heat": 500, "seen_at": typed_at}], source="manual")
        before = self.row("秋冬护肤")
        scored_before = {t["keyword"]: t for t in self.send("GET", "/api/trends")[1]["trends"]}["秋冬护肤"]
        added = self.store.upsert_trends([dict(self.FOUND, seen_at=time.time(), heat=99999), {"keyword": "围炉煮茶",
                                          "source_url": "https://example.com/b", "seen_at": time.time()}], source="ai")
        self.assertEqual(added, 1)                                               # only the new keyword was added
        self.assertEqual(self.row("秋冬护肤"), before)                             # source, link, notes, heat and date: all hers
        self.assertEqual((before["source"], before["source_url"], before["note_zh"], before["updated_at"]), ("manual", "", "", typed_at))
        scored = {t["keyword"]: t for t in self.send("GET", "/api/trends")[1]["trends"]}
        self.assertEqual({k: scored["秋冬护肤"][k] for k in ("freshness", "stale", "source", "note_zh", "source_url")},
                         {k: scored_before[k] for k in ("freshness", "stale", "source", "note_zh", "source_url")})
        self.assertTrue(scored["秋冬护肤"]["stale"])                               # still shown as possibly out of date
        self.assertEqual((scored["围炉煮茶"]["source"], scored["围炉煮茶"]["source_url"]), ("ai", "https://example.com/b"))

    def test_the_same_holds_for_a_pasted_reply(self):
        typed_at = time.time() - 40 * self.DAY
        self.store.upsert_trends([{"keyword": "秋冬护肤", "seen_at": typed_at}], source="manual")
        reply = {"keywords": [{"keyword": "秋冬护肤", "source_url": "https://example.com/a", "why_zh": "模型写的一句话",
                               "source_date": time.strftime("%Y-%m-%d")}]}
        status, body, _ = self.send("POST", "/api/manual/reply", {"task": "trends", "reply": json.dumps(reply, ensure_ascii=False)})
        self.assertEqual((status, body.get("added")), (200, 0), body)
        row = self.row("秋冬护肤")
        self.assertEqual((row["source"], row["source_url"], row["note_zh"], row["updated_at"]), ("manual", "", "", typed_at))

    def test_a_keyword_the_creator_types_carries_nothing_an_ai_wrote(self):
        self.store.upsert_trends([dict(self.FOUND, seen_at=time.time() - 10 * self.DAY)], source="ai")
        self.assertEqual((self.row("秋冬 护肤")["source"], self.row("秋冬 护肤")["note_zh"]), ("ai", "模型写的一句话"))
        status, body, _ = self.send("POST", "/api/trends", {"text": "秋冬护肤, 1200"})
        self.assertEqual((status, body.get("added")), (201, 1), body)
        row = self.row("秋冬 护肤")
        self.assertEqual((row["source"], row["heat"], row["source_url"], row["note_zh"], row["note_en"]), ("manual", 1200, "", "", ""))
        self.assertLess(time.time() - row["updated_at"], 60)                     # she saw it today

    def test_an_ai_can_still_refresh_what_an_ai_found(self):
        self.store.upsert_trends([dict(self.FOUND, seen_at=time.time() - 10 * self.DAY)], source="ai")
        now = time.time()
        self.assertEqual(self.store.upsert_trends([{"keyword": "秋冬护肤", "source_url": "https://example.com/newer", "seen_at": now}], source="ai"), 1)
        row = self.row("秋冬 护肤")
        self.assertEqual((row["source"], row["source_url"], row["note_zh"], row["updated_at"]),
                         ("ai", "https://example.com/newer", "模型写的一句话", now))

    def held_for_writing(self):
        """True while some connection holds the database for writing."""
        probe = sqlite3.connect(str(self.data / "studio.db"), timeout=0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
            return False
        except sqlite3.OperationalError:
            return True
        finally:
            probe.close()

    def test_two_saves_of_one_new_keyword_at_the_same_moment_both_succeed(self):
        # The creator types a keyword at the moment an AI app reports the same one. The AI's save is placed
        # exactly between the creator's lookup ("not tracked yet") and her insert. It runs to the end there
        # when the database lets it; when her save already holds the database, it is left waiting its turn.
        real_uuid4, other_store, other, outcome = store_module.uuid.uuid4, Store(self.data), [], {}

        def ai_save():
            try:
                outcome["added"] = other_store.upsert_trends([dict(self.FOUND, seen_at=time.time(), heat=99999)], source="ai")
            except Exception as exc:        # handed to the assertions below
                outcome["error"] = exc

        def between_lookup_and_insert():
            if not other:
                other.append(threading.Thread(target=ai_save, daemon=True))
                other[0].start()
                deadline = time.time() + 30
                while other[0].is_alive() and not self.held_for_writing() and time.time() < deadline:
                    other[0].join(0.005)
            return real_uuid4()

        with mock.patch.object(store_module.uuid, "uuid4", between_lookup_and_insert):
            added = self.store.upsert_trends([{"keyword": "秋冬护肤", "heat": 1200}], source="manual")
            other[0].join(30)
        self.assertFalse(other[0].is_alive())
        self.assertEqual((added, outcome), (1, {"added": 0}))                    # hers was saved; the AI's changed nothing on it
        rows = self.store.list_trends()
        self.assertEqual([(r["keyword"], r["source"], r["heat"], r["source_url"], r["note_zh"], r["note_en"]) for r in rows],
                         [("秋冬护肤", "manual", 1200, "", "", "")])                # one keyword, and all of it is hers


# ---------------------------------------------------------------------------------------------- 9. framing and the content security policy
class PageProtectionTest(StudioCase):
    def test_every_answer_forbids_framing_and_carries_the_policy(self):
        answers = [self.send("GET", "/"), self.send("GET", "/app.js"), self.send("GET", "/app.css"), self.send("GET", "/api/state"),
                   self.send("GET", "/nothing-here"), self.send("GET", "/api/nothing-here"),
                   self.send("GET", "/api/state", headers={"Host": "evil.example"}),
                   self.send("POST", "/api/scripts", body=b"title=x", headers={"Content-Type": "application/x-www-form-urlencoded"}),
                   self.send("POST", "/api/scripts", {"title": "t"})]
        self.assertEqual([status for status, _, _ in answers], [200, 200, 200, 200, 404, 404, 403, 415, 201])
        for status, _, headers in answers:
            self.assertEqual(headers.get("X-Frame-Options"), "DENY", status)
            self.assertEqual(headers.get("Content-Security-Policy"), POLICY, status)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff", status)
        _, script, _ = self.send("POST", "/api/scripts", {"title": "t", "lines": [{"spoken_script_zh": "今天去超市买菜"}]})
        _, _, headers = self.send("GET", "/api/scripts/%s/export/srt" % script["id"])
        self.assertEqual((headers.get("X-Frame-Options"), headers.get("Content-Security-Policy")), ("DENY", POLICY))

    def test_the_policy_allows_nothing_it_does_not_need(self):
        policy = dict(part.split(" ", 1) for part in POLICY.split("; "))
        self.assertEqual(policy["default-src"], "'none'")
        self.assertEqual(policy["script-src"], "'self'")                         # no inline script, no eval, no other address
        self.assertEqual(policy["frame-ancestors"], "'none'")
        self.assertEqual(policy["img-src"], "'self' blob: data:")
        self.assertEqual(policy["media-src"], "'self' blob:")
        for directive, value in policy.items():
            self.assertNotIn("*", value, directive)
            self.assertNotIn("http", value, directive)
            if directive != "img-src":
                self.assertNotIn("data:", value, directive)
            if directive not in ("style-src", "style-src-attr"):
                self.assertNotIn("unsafe", value, directive)

    def test_the_pages_use_nothing_the_policy_forbids(self):
        """If a page starts to need more, this fails here and not silently in the creator's browser."""
        web = Path(server.WEB_DIR)
        index = (web / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", index, re.S)
        self.assertEqual(sorted(re.search(r'src="([^"]+)"', attrs).group(1) for attrs, _ in scripts),
                         ["/app.js", "/autopilot.js", "/backup.js", "/craft.js", "/discover.js", "/kwimport.js", "/pages.js", "/planner.js", "/posted.js",
                          "/prompter.js", "/recording.js", "/rednote.js", "/refs.js", "/setup.js", "/sponsored.js", "/tone.js",
                          "/updater.js", "/wordlist.js"])
        self.assertEqual([body.strip() for _, body in scripts], [""] * len(scripts))   # none is written into the page
        self.assertEqual(re.findall(r'<link\b[^>]*href="([^"]+)"', index), ["/app.css"])
        sources = {path.name: path.read_text(encoding="utf-8") for path in web.iterdir() if path.suffix in (".html", ".js", ".css")}
        # windowcheck.js is the one file the page does not name: it is loaded only during a trial of an installer.
        self.assertEqual(sorted(sources), ["app.css", "app.js", "autopilot.js", "backup.js", "craft.js", "discover.js", "index.html", "kwimport.js", "pages.js",
                                           "planner.js", "posted.js", "prompter.js", "recording.js", "rednote.js", "refs.js", "setup.js", "sponsored.js",
                                           "tone.js", "updater.js", "windowcheck.js", "wordlist.js"])
        forbidden = {
            "a <style> element": r"<style\b",
            "an event handler written into markup": r"<[A-Za-z][^<>]*\son[a-z]+\s*=",
            "a javascript: address": r"javascript:",
            "code built from text": r"\beval\s*\(|new\s+Function\s*\(|set(?:Timeout|Interval)\s*\(\s*['\"`]",
            "something loaded from another address": r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|@import|url\(\s*["']?(?:https?:|//|data:)""",
            "an embedded page or plug-in": r"<(?:iframe|object|embed|base|form)\b",
            "an external source, picture or font": r"<(?:source|picture)\b|@font-face|new\s+Image\s*\(",
            "a connection other than a request to this server": r"\b(?:WebSocket|EventSource|sendBeacon|importScripts|Worker)\b",
        }
        for name, text in sources.items():
            for what, pattern in forbidden.items():
                self.assertIsNone(re.search(pattern, text), "%s contains %s" % (name, what))
            for address in re.findall(r"fetch\(\s*([^,)]+)", text):
                self.assertNotRegex(address, r"https?:|//", name)


# ---------------------------------------------------------------------------------------------- 10. NaN and Infinity
class FiniteNumbersTest(StudioCase):
    NOT_NUMBERS = ("NaN", "Infinity", "-Infinity", "1e999", "-1e999")

    def test_a_reply_that_contains_one_is_not_valid_json(self):
        for word in self.NOT_NUMBERS:
            for reply in ('{"a": %s}' % word, '```json\n{"a": [1, {"b": %s}]}\n```' % word, 'Here you go: {"a": %s} Anything else?' % word):
                with self.assertRaises(llm.LLMError) as refused:
                    llm.parse_json_text(reply)
                self.assertEqual(refused.exception.code, "bad_json", reply)
        self.assertEqual(llm.parse_json_text('{"a": 1e3, "b": -0.5, "c": "NaN", "d": "Infinity", "e": 12345678901234567890}'),
                         {"a": 1000.0, "b": -0.5, "c": "NaN", "d": "Infinity", "e": 12345678901234567890})

    def test_pasted_replies_that_contain_one_are_refused_and_nothing_is_stored(self):
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT, "likes": 5})
        self.send("POST", "/api/settings", {"provider": "manual"})
        voice = json.dumps(EXAMPLE, ensure_ascii=False)[:-1] + ', "energy": NaN}'
        self.assertEqual(json.loads(voice)["persona_summary"], EXAMPLE["persona_summary"])    # a whole profile, plus the one NaN
        replies = {
            "voice": voice,
            "draft": '{"script_lines": [{"spoken_script_zh": "今天去超市买菜"}], "rednote_post_package": {"score": NaN}}',
            "ideas": '{"ideas": [{"title_zh": "早餐", "why_zh": "w", "target_duration_sec": Infinity}]}',
            "trends": '{"keywords": [{"keyword": "露营", "source_url": "https://example.com/a", "heat": 1e999}]}',
        }
        for task, reply in replies.items():
            status, body, _ = self.send("POST", "/api/manual/reply", {"task": task, "notes": "去超市", "reply": reply})
            self.assertEqual((status, body.get("error"), body.get("reason")), (502, "ai_failed", "bad_json"), task)
        self.assertIsNone(self.store.load_pending())
        self.assertIsNone(self.store.load_recommendations())
        self.assertEqual((self.store.list_scripts(), self.store.list_trends()), ([], []))
        self.assertEqual(self.files(), ["config.json", "studio.db"])

    def test_a_request_that_contains_one_is_refused(self):
        _, script, _ = self.send("POST", "/api/scripts", {"title": "t", "lines": [{"spoken_script_zh": "今天去超市买菜"}]})
        before = self.send("GET", "/api/scripts/" + script["id"])[1]
        requests = [("PUT", "/api/scripts/" + script["id"], '{"post": {"score": %s}}'), ("POST", "/api/scripts", '{"title": "x", "target_sec": %s}'),
                    ("POST", "/api/samples", '{"title": "s", "text": "今天去超市买菜", "views": %s}'), ("PUT", "/api/voice/rate", '{"cpm": %s}'),
                    ("PUT", "/api/scripts/" + script["id"], '{"title": "x", "base_updated_at": %s}')]
        for method, path, template in requests:
            for word in self.NOT_NUMBERS:
                status, body, _ = self.send(method, path, body=(template % word).encode(), headers={"Content-Type": "application/json"})
                self.assertEqual((status, body.get("error")), (400, "bad_json"), "%s %s %s" % (method, path, word))
        self.assertEqual(self.send("GET", "/api/scripts/" + script["id"])[1], before)
        self.assertEqual((len(self.store.list_scripts()), self.store.list_samples()), (1, []))
        # numbers that are merely large or given as text are not refused, and do not end in a server error
        for body in ({"title": "s", "text": "今天去超市买菜", "views": "Infinity"}, {"title": "s", "text": "今天去超市买菜", "views": 10 ** 23},
                     {"title": "s", "text": "今天去超市买菜", "views": "nan", "likes": 1e300}):
            status, item, _ = self.send("POST", "/api/samples", body)
            self.assertEqual((status, item["views"]), (201, None), body)

    def test_a_provider_answer_that_contains_one_is_refused(self):
        def bare(handler):
            body = b'{"choices": [{"message": {"content": "{}"}}], "usage": {"cost": NaN}}'
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        address, _ = self.listen(bare)
        with loopback_only(), self.assertRaises(llm.LLMError) as refused:
            llm._http("GET", address + "/v1/models", {}, None)
        self.assertEqual(refused.exception.code, "bad_response")

    def test_the_store_never_writes_one(self):
        script = self.store.create_script(title="t", lines=[{"spoken_script_zh": "今天去超市买菜"}], post={"score": 1})
        sample = self.store.create_sample(title="s", text="今天去超市买菜")
        self.store.save_dna(dict(EXAMPLE, profile_version="mine"))
        for bad in (float("nan"), float("inf"), float("-inf")):
            writes = {
                "profile": lambda: self.store.save_dna({"x": [bad]}), "proposal": lambda: self.store.save_pending({"dna": {"x": bad}}),
                "ideas": lambda: self.store.save_recommendations({"ideas": [{"score": bad}]}), "creator": lambda: self.store.save_creator({"x": bad}),
                "settings": lambda: self.store.save_config({"x": bad}), "new script": lambda: self.store.create_script(title="n", post={"score": bad}),
                "script": lambda: self.store.update_script(script["id"], {"title": "changed", "post": {"score": bad}}),
                "timing": lambda: self.store.create_sample(title="v", text="今天去超市买菜", kind="video", timing={"spoken_sec": bad}),
                "prompt record": lambda: self.store.save_brief("draft", {"basis": {"x": bad}}),
            }
            for label, write in writes.items():
                with self.assertRaises(ValueError, msg=label):
                    write()
        self.assertEqual(self.store.get_script(script["id"]), script)
        self.assertEqual((len(self.store.list_scripts()), [s["id"] for s in self.store.list_samples()]), (1, [sample["id"]]))
        self.assertEqual(self.store.load_dna()[0]["profile_version"], "mine")
        self.assertEqual(self.files(), ["studio.db", "voice_dna.json"])
        self.assertIsNone(self.store.find_brief("draft"))

    def test_what_an_earlier_version_stored_can_be_shown_and_removed_again(self):
        """0.1.x accepted NaN in a reply and stored it; the page then could not read the answer at all."""
        self.send("POST", "/api/samples", {"title": "超市那天", "text": HER_SCRIPT})
        script = self.store.create_script(title="cannot be opened", lines=[{"spoken_script_zh": "今天去超市买菜"}])
        db = sqlite3.connect(str(self.store.db_path))
        db.execute("UPDATE scripts SET post_json = ?, basis_json = ? WHERE id = ?",
                   ('{"score": NaN, "title_options": [{"title_zh": "标题", "weight": Infinity}]}', '{"lessons_used": -Infinity}', script["id"]))
        db.commit()
        db.close()
        with_nan = (json.dumps(dict(EXAMPLE, profile_version="stored by 0.1.0"), ensure_ascii=False)[:-1]
                    + ', "energy": NaN, "pause_ratio": 1e999}')
        self.store.dna_path.write_text(with_nan, encoding="utf-8")
        self.store.pending_path.write_text('{"dna": %s, "report": {"items": []}, "created_at": 5.0, "based_on": 1}' % with_nan, encoding="utf-8")
        self.store.recommendations_path.write_text('{"ideas": [{"title_zh": "早餐", "score": Infinity}], "generated_at": NaN}', encoding="utf-8")

        for path in ("/api/state", "/api/setup", "/api/dna", "/api/voice/pending", "/api/recommendations", "/api/scripts",
                     "/api/scripts/" + script["id"], "/api/trends", "/api/samples"):
            status, body, _ = self.send("GET", path)                             # send() reads the answer as a browser would
            self.assertEqual(status, 200, "%s -> %r" % (path, body))
        dna = self.send("GET", "/api/dna")[1]
        self.assertEqual((dna["is_example"], dna["dna"]["energy"], dna["dna"]["pause_ratio"], dna["dna"]["profile_version"]),
                         (False, None, None, "stored by 0.1.0"))
        opened = self.send("GET", "/api/scripts/" + script["id"])[1]
        self.assertEqual((opened["post"], opened["basis"]), ({"score": None, "title_options": [{"title_zh": "标题", "weight": None}]},
                                                             {"lessons_used": None}))
        self.assertEqual(self.send("GET", "/api/recommendations")[1]["result"], {"ideas": [{"title_zh": "早餐", "score": None}], "generated_at": None})
        self.assertEqual(self.send("DELETE", "/api/voice/pending")[0], 200)      # the proposal can be discarded
        self.assertEqual(self.send("DELETE", "/api/scripts/" + script["id"])[0], 200)    # and the script deleted
        self.assertEqual(self.store.dna_path.read_text(encoding="utf-8"), with_nan)      # reading changed nothing on disk

    def test_no_answer_carries_one_whatever_computed_it(self):
        self.transcriber.more_status = {"progress": float("nan"), "sizes": [1.5, float("inf"), {"free": float("-inf")}], "note": "NaN"}
        status, body, _ = self.send("GET", "/api/transcriber")
        self.assertEqual(status, 200)
        self.assertEqual((body["progress"], body["sizes"], body["note"]), (None, [1.5, None, {"free": None}], "NaN"))


# ---------------------------------------------------------------------------------------------- 11. request size, idle connections, slow patterns
class RequestSizeTest(StudioCase):
    BODY = b'{"title": "planted", "text": "not added by the creator"}'

    def test_a_size_that_is_not_plain_digits_is_refused(self):
        for stated in (b"-1", b"abc", b"1e3", b"+56", b"0x38", b"56, 56", b"5 6", b"", b"\xb2", b"-0"):
            status, body, _ = self.raw(self.request_head("POST", "/api/samples", b"Content-Type: application/json",
                                                         b"Content-Length: " + stated) + self.BODY)
            self.assertEqual((status, body.get("error")), (400, "bad_content_length"), stated)
            self.assert_both_languages(body)
        self.assertEqual(self.titles(), [])
        for stated in (b"%d" % len(self.BODY), b"  %d  " % len(self.BODY), b"000%d" % len(self.BODY)):
            status, body, _ = self.raw(self.request_head("POST", "/api/samples", b"Content-Type: application/json",
                                                         b"Content-Length: " + stated) + self.BODY)
            self.assertEqual(status, 201, stated)
        self.assertEqual(self.titles(), ["planted"] * 3)

    def test_the_size_limit_cannot_be_passed_by_stating_no_real_size(self):
        too_much = b'{"title": "big", "text": "' + b"x" * (server.MAX_BODY + 1024) + b'"}'
        for stated, expected in ((b"-1", (400, "bad_content_length")), (b"9" * 5000, (413, "too_large")),
                                 (b"%d" % len(too_much), (413, "too_large"))):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            try:
                sock.sendall(self.request_head("POST", "/api/samples", b"Content-Type: application/json", b"Content-Length: " + stated))
                try:
                    sock.sendall(too_much)
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass                                                         # refused before the body was read: that is the point
                answer = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        answer += chunk
                except OSError:
                    pass
            finally:
                sock.close()
            # The answer can be lost, or cut short, when the server stops reading a body that is still being
            # sent. It is checked when all of it arrived; that nothing was stored is checked every time.
            head, _, payload = answer.partition(b"\r\n\r\n")
            size = re.search(br"(?i)\r\nContent-Length: *(\d+)", head)
            if size and len(payload) == int(size.group(1)):
                self.assertEqual((int(head.split()[1]), strict_json(payload).get("error")), expected, stated[:12])
            self.assertEqual(self.titles(), [], stated[:12])

    def test_an_upload_must_state_its_size_in_digits_too(self):
        for stated, expected in ((b"abc", (400, "bad_content_length")), (b"-1", (400, "bad_content_length")), (b"\xb2", (400, "bad_content_length"))):
            status, body, _ = self.raw(self.request_head("PUT", "/api/uploads?name=clip.mp4", b"Content-Type: application/octet-stream",
                                                         b"Content-Length: " + stated) + b"video bytes")
            self.assertEqual((status, body.get("error")), expected, stated)
        status, body, _ = self.raw(self.request_head("PUT", "/api/uploads?name=clip.mp4", b"Content-Type: application/octet-stream"))
        self.assertEqual((status, body.get("error")), (411, "length_required"))
        self.assertEqual(self.files(self.data / "uploads"), [])


class IdleConnectionTest(StudioCase):
    def read_until_closed(self, sock):
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return data
            data += chunk

    def test_a_connection_that_sends_nothing_is_closed(self):
        self.assertIsNotNone(server.Handler.timeout)                             # without one, such a connection is held for ever
        self.assertLessEqual(server.Handler.timeout, 60)
        with mock.patch.object(server.Handler, "timeout", 0.3):
            for sent in (b"", b"GET /api/sta", b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % self.port):
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                try:
                    sock.sendall(sent)
                    started = time.time()
                    self.assertEqual(self.read_until_closed(sock), b"", sent)
                    self.assertLess(time.time() - started, 3, sent)
                finally:
                    sock.close()
            self.assertEqual(self.send("GET", "/api/state")[0], 200)             # and the server still answers

    def test_a_body_that_stops_arriving_gets_an_answer(self):
        with mock.patch.object(server.Handler, "timeout", 0.3):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                sock.sendall(self.request_head("POST", "/api/samples", b"Content-Type: application/json", b"Content-Length: 500")
                             + b'{"title": "half')
                answer = self.read_until_closed(sock)
            finally:
                sock.close()
        head, _, payload = answer.partition(b"\r\n\r\n")
        body = strict_json(payload)
        self.assertEqual((int(head.split()[1]), body.get("error")), (408, "request_timeout"))
        self.assert_both_languages(body)
        self.assertEqual(self.titles(), [])

    def test_an_answer_that_takes_long_to_work_out_is_still_sent(self):
        """The limit is on a connection that sends nothing while it is being read, not on the work."""
        def slow_status():
            time.sleep(0.7)
            return {"supported": True, "installed": True}

        self.transcriber.status = slow_status
        with mock.patch.object(server.Handler, "timeout", 0.2):
            status, body, _ = self.send("GET", "/api/transcriber")
        self.assertEqual((status, body["installed"]), (200, True))


# The pattern studio/trends.py used before, kept here to compare the new reading with.
OLD_TRAILING_HEAT = re.compile(r"^(.+?)(\s*[\t,，;；|｜]\s*|\s+)(\d[\d,]*(?:\.\d+)?)\s*(万|亿|[wWkK])?\+?\s*$")


class BurstTest(StudioCase):
    """Connections that arrive at the same moment. A browser opens six to one address at once, a page of the
    app is twelve files, and a second window or a script that tries the app adds its own."""

    def test_more_connections_may_wait_than_python_allows_by_itself(self):
        self.assertGreaterEqual(server.StudioServer.request_queue_size, 64)        # Python's own choice is 5
        self.assertEqual(self.httpd.request_queue_size, server.StudioServer.request_queue_size)

    def test_connections_made_at_the_same_moment_are_all_answered(self):
        """With Python's own queue of 5, close to half of twelve such connections were lost on one Mac."""
        at_once, rounds, lost = 24, 6, []
        for _ in range(rounds):
            gate = threading.Barrier(at_once)

            def one():
                try:
                    gate.wait(timeout=30)
                    status, body, _ = self.send("GET", "/app.css", timeout=30)
                    if status != 200 or not body:
                        lost.append("answered %s with %d bytes" % (status, len(body)))
                except Exception as problem:                 # noqa: BLE001 - every way of not being answered counts
                    lost.append("%s: %s" % (type(problem).__name__, problem))

            threads = [threading.Thread(target=one) for _ in range(at_once)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual(lost, [])


class SlowPatternTest(unittest.TestCase):
    def test_a_timestamp_cannot_carry_a_number_that_takes_seconds_to_read(self):
        digits = "9" * 2000000
        started = time.time()
        with self.assertRaises(ValueError):                                      # not a timestamp: there is no cue in this file
            timing.parse_subtitles("1\n%s:00:01,000 --> %s:00:02,000\n今天去超市买菜\n" % (digits, digits))
        self.assertLess(time.time() - started, SLOW)
        started = time.time()
        cues = timing.parse_subtitles("%s\n00:00:01,000 --> 00:00:02,000\n今天去超市买菜\n\n2\n00:00:03,000 --> 00:00:04,000\n%s\n%s\n"
                                      "00:00:05,000 --> 00:00:06,000\n结果人特别多\n" % (digits, digits, digits))
        self.assertLess(time.time() - started, SLOW)
        self.assertEqual([(c["start"], c["text"] if len(c["text"]) < 100 else len(c["text"])) for c in cues],
                         [(1.0, "今天去超市买菜"), (3.0, 4000001), (5.0, "结果人特别多")])

    def test_real_timestamps_and_cue_numbers_read_as_before(self):
        text = ("1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n2\n1:02:03.250 --> 1:02:04\n第二句\n3\n9999:59:58,000 --> 9999:59:59,999\n第三句\n"
                "\n4\n02:05 --> 02:06\n第四句\n٥\n00:03:00,000 --> 00:03:01,000\n第五句\n\n6\n00:04:00,000 --> 00:04:01,000\n第六句\n")
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in timing.parse_subtitles(text)],
                         [(1.0, 2.5, "第一句"), (125.0, 126.0, "第四句"), (180.0, 181.0, "第五句"), (240.0, 241.0, "第六句"),
                          (3723.25, 3724.0, "第二句"), (35999998.0, 35999999.999, "第三句")])
        # A line that only looks like a number stays part of the words; it used to stop the whole file being read.
        odd = timing.parse_subtitles("1\n00:00:01,000 --> 00:00:02,000\n面积是 5 m\n²\n00:00:03,000 --> 00:00:04,000\n第二句\n")
        self.assertEqual([c["text"] for c in odd], ["面积是 5 m ²", "第二句"])
        with self.assertRaises(ValueError):
            timing.parse_subtitles("10000:00:01,000 --> 10000:00:02,000\n五位数的小时不是时间\n")

    def test_pasted_keyword_lines_are_read_in_one_pass(self):
        hostile = ["a" + " " * 32000 + "b", "a," + "1," * 16000 + "x", "a" + " " * 2000000 + "b", "a," + "1," * 1000000 + "x",
                   "1" * 2000000, "a " * 1000000 + "5", "a" + ", " * 1000000 + "5", "护肤, " + "9" * 2000000, "k+" * 1000000,
                   "1." * 1000000, " " * 2000000 + "护肤 12.5万"]
        for line in hostile:
            started = time.time()
            items, skipped = trends.parse_keywords(line)
            self.assertLess(time.time() - started, SLOW, line[:12])
            self.assertEqual(len(items) + skipped, 1, line[:12])
        self.assertEqual(trends.parse_keywords(hostile[-1]), ([{"keyword": "护肤", "heat": 125000}], 0))
        self.assertEqual(trends.parse_keywords(hostile[7]), ([{"keyword": "护肤", "heat": None}], 0))

    def test_keyword_lines_are_read_as_before(self):
        lines = {"秋冬护肤, 125000": ("秋冬护肤", 125000), "秋冬护肤\t80": ("秋冬护肤", 80), "秋冬护肤 12.5万": ("秋冬护肤", 125000),
                 "秋冬护肤 12,500": ("秋冬护肤", 12500), "iPhone 15": ("iPhone 15", None), "#露营 3k+": ("露营", 3000), "护肤,1,234": ("护肤", 1234),
                 "1. 围炉煮茶；2亿": ("围炉煮茶", 200000000), "a, b 5": ("a, b 5", None), "a, b, 5": ("a, b", 5), "早餐 | 1.5w+": ("早餐", 15000),
                 "- city walk 999": ("city walk 999", None), "city walk 1000": ("city walk", 1000), "护肤, 99999999999999": ("护肤", None)}
        for line, (keyword, heat) in lines.items():
            self.assertEqual(trends.parse_keywords(line), ([{"keyword": keyword, "heat": heat}], 0), line)

        def old(line):
            match = OLD_TRAILING_HEAT.match(line)
            return (match.group(1), match.group(2), match.group(3), match.group(4)) if match else None

        alphabet = ["a", "b", "护", "肤", "1", "2", "0", "9", ",", ".", " ", " ", "\t", "，", ";", "|", "万", "亿", "w", "K", "k", "+", "#", "-",
                    "\u3000", "５"]
        chooser = random.Random(20261115)
        compared = matched = 0
        for length in range(1, 13):
            for _ in range(2500):
                line = "".join(chooser.choice(alphabet) for _ in range(length)).strip()
                if line:
                    self.assertEqual(trends._trailing_heat(line), old(line), repr(line))
                    compared += 1
                    matched += old(line) is not None
        self.assertGreater(compared, 25000)
        self.assertGreater(matched, 1500)                                        # the comparison is not of misses only


# ---------------------------------------------------------------------------------------------- 12. earlier voice profiles
def profile(name, learned_at, based_on):
    persona = dict(EXAMPLE["persona_summary"], one_line_essence_zh=name + "（中文）", one_line_essence_en=name + " (English)")
    return dict(EXAMPLE, persona_summary=persona, source_corpus_summary={"learned_at": learned_at, "based_on": based_on})


class VoiceHistoryTest(StudioCase):
    A, B, C = profile("A", "2026-11-01 10:00", 3), profile("B", "2026-11-02 11:00", 4), profile("C", "2026-11-03 12:00", 5)

    def history(self):
        status, body, _ = self.send("GET", "/api/voice/history")
        self.assertEqual((status, list(body)), (200, ["history"]))
        return body["history"]

    def names(self):
        return [entry["essence_en"] for entry in self.history()]

    def restore(self, history_id):
        return self.send("POST", "/api/voice/history/%s/restore" % history_id, {})

    def test_earlier_profiles_are_listed_newest_first(self):
        self.assertEqual(self.history(), [])
        self.store.save_dna(self.A)
        self.assertEqual(self.history(), [])                                     # the profile in use is not an earlier one
        self.store.save_dna(self.B)
        self.store.save_dna(self.C)
        history = self.history()
        self.assertEqual([set(entry) for entry in history], [{"id", "saved_at", "learned_at", "based_on", "essence_zh", "essence_en"}] * 2)
        self.assertEqual([(e["essence_zh"], e["essence_en"], e["learned_at"], e["based_on"]) for e in history],
                         [("B（中文）", "B (English)", "2026-11-02 11:00", 4), ("A（中文）", "A (English)", "2026-11-01 10:00", 3)])
        for entry in history:
            self.assertRegex(entry["id"], r"^[0-9]{8}-[0-9]{6}(-[0-9]+)?$")
            self.assertLess(abs(time.time() - entry["saved_at"]), 10)
        self.assertEqual(len({entry["id"] for entry in history}), 2)

    def test_an_earlier_profile_can_be_put_back_in_use(self):
        for dna in (self.A, self.B, self.C):
            self.store.save_dna(dna)
        oldest = self.history()[-1]
        status, body, _ = self.restore(oldest["id"])
        self.assertEqual((status, body), (200, {"dna": self.A, "is_example": False}))
        self.assertEqual(self.send("GET", "/api/dna")[1], {"dna": self.A, "is_example": False})
        self.assertEqual(self.names(), ["C (English)", "B (English)"])           # the one it replaced joined the earlier ones
        status, body, _ = self.restore(self.history()[0]["id"])                  # and back again
        self.assertEqual((status, body["dna"]), (200, self.C))
        self.assertEqual(self.names(), ["A (English)", "B (English)"])
        self.assertEqual(self.restore(oldest["id"])[0], 404)                     # that copy is the one that went back into use
        if os.name == "posix":            # Windows has no such modes: there the folder decides who may read the files
            self.assertEqual(Tree.open_to_others(self.data), {})

    def test_a_profile_is_restored_byte_for_byte(self):
        self.store.save_dna(self.A)
        kept = self.store.dna_path.read_bytes()
        self.store.save_dna(self.B)
        self.assertEqual(self.restore(self.history()[0]["id"])[0], 200)
        self.assertEqual(self.store.dna_path.read_bytes(), kept)

    def test_an_id_that_names_no_earlier_profile_is_not_found(self):
        for dna in (self.A, self.B):
            self.store.save_dna(dna)
        before = (self.store.dna_path.read_bytes(), self.files(self.store.dna_history_dir), self.files())
        (self.data / "config.json").write_text('{"provider": "manual"}')
        (self.store.dna_history_dir / "voice_dna-20200101-000000.json").write_text("{ cut short")
        (self.store.dna_history_dir / "voice_dna-20200101-000001.json").write_text("[]")
        (self.store.dna_history_dir / "notes.json").write_text(json.dumps(self.C))
        os.symlink(str(self.data / "config.json"), str(self.store.dna_history_dir / "voice_dna-20200101-000002.json"))
        self.assertEqual(self.names(), ["A (English)"])                          # none of those four is offered
        for history_id in ("20990101-000000", "20200101-000000", "20200101-000001", "20200101-000002", "notes", "abc", "..%2F..%2Fconfig",
                           "../../config", "20200101-000000%00", "20200101-0000000", ""):
            status, body, _ = self.send("POST", "/api/voice/history/%s/restore" % history_id, {})
            self.assertEqual(status, 404, history_id)
        for history_id in ("../../config", "２０２００１０１-００００００", "20200101-000002", "20200101-000000\n", None, 20200101, b"20200101-000000"):
            self.assertIsNone(self.store.restore_dna(history_id), history_id)
        self.assertEqual(self.store.dna_path.read_bytes(), before[0])
        self.assertEqual(self.send("GET", "/api/dna")[1]["dna"], self.B)
        self.assertEqual(len(self.files(self.store.dna_history_dir)), len(before[1]) + 4)   # nothing was removed or added

    def test_restoring_is_a_write_like_any_other(self):
        for dna in (self.A, self.B):
            self.store.save_dna(dna)
        path = "/api/voice/history/%s/restore" % self.history()[0]["id"]
        self.assertEqual(self.send("GET", path)[0], 404)                         # reading never changes anything
        self.assertEqual(self.send("POST", path, body=b"", headers={"Content-Type": "text/plain"})[0], 415)
        status, body, _ = self.send("POST", path, {}, headers={"Origin": "https://attacker.example"})
        self.assertEqual((status, body.get("error")), (403, "bad_origin"))
        self.assertEqual(self.send("GET", "/api/dna")[1]["dna"], self.B)
        self.assertEqual(self.names(), ["A (English)"])

    def test_going_back_also_works_when_the_file_in_use_is_missing_or_damaged(self):
        for dna in (self.A, self.B):
            self.store.save_dna(dna)
        self.store.dna_path.write_bytes(b'{"cut short')
        self.assertTrue(self.send("GET", "/api/dna")[1].get("unreadable"))
        self.assertEqual(self.restore(self.history()[0]["id"])[0], 200)
        self.assertEqual(self.send("GET", "/api/dna")[1], {"dna": self.A, "is_example": False})
        self.assertEqual([name.rsplit(".", 1)[1] for name in self.files(self.store.dna_history_dir)], ["unreadable"])   # kept, not listed
        self.store.save_dna(self.B)
        self.store.dna_path.unlink()
        self.assertEqual(self.restore(self.history()[0]["id"])[0], 200)
        self.assertEqual(self.send("GET", "/api/dna")[1]["dna"], self.A)

    def test_profiles_of_an_unexpected_shape_are_listed_without_failing(self):
        odd = [{"persona_summary": "松弛", "source_corpus_summary": ["x"]}, {"persona_summary": {"one_line_essence_zh": 7},
               "source_corpus_summary": {"learned_at": 5, "based_on": "many", "organic_scripts_analyzed": 9}}, {}]
        for dna in odd + [self.A]:
            self.store.save_dna(dna)
        self.assertEqual([(e["learned_at"], e["based_on"], e["essence_zh"], e["essence_en"]) for e in self.history()],
                         [(None, 0, "", ""), (None, 0, "", ""), (None, 0, "", "")])

    def test_no_mcp_tool_can_restore(self):
        from studio import mcp_server
        self.assertNotIn("restore_dna", Path(mcp_server.__file__).read_text(encoding="utf-8"))
        for name in mcp_server.TOOLS:
            self.assertIsNone(re.search(r"restor|histor|roll_?back", name), name)


if __name__ == "__main__":
    unittest.main()
