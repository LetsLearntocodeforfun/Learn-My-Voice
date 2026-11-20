#!/usr/bin/env python3
"""Fuzz every place where the app reads something it did not write itself.

    python3 tools/fuzz.py --list
    nice -n 10 python3 tools/fuzz.py --seconds 1800 --out fuzz-out     # the long run; the time is shared out over the targets
    python3 tools/fuzz.py --cases 200 --seed 7 --targets docx,subtitles
    python3 tools/fuzz.py --replay fuzz-out/findings/docx-1.json

A target is one way in: a .docx, a subtitle file, a keyword spreadsheet, pasted trend lines, a model's reply, a
provider's answer, a request, an MCP message, a stored profile, a link, script lines, the word list, a recording.
For each target, inputs are made three ways: by a generator that knows the format, by changing a valid example,
and from the shapes that froze or broke the app before (very many opening brackets, long runs of one character
or of white space, deep nesting, an archive that unpacks to far more than its size, text that is not UTF-8,
byte-order marks, NUL, direction marks, combining characters, full-width forms, half a character in JSON,
numbers such as 1e999 and NaN).

What counts as a fault, checked for every input:

- anything raised that is not the refusal the parser is documented to give;
- no answer within the time limit. The target runs in a child process, which is ended; the input is then tried
  once more in a new child, and only a second silence is reported. Apart from that, each target has shapes that
  are run at sizes n, 2n and 4n: work that grows with the square of the size shows as a factor near four for
  each doubling of the processor time, whatever the speed of the machine;
- an answer far larger than the input;
- an answer that cannot be written as JSON, holds NaN or Infinity, or cannot be written as UTF-8;
- memory far beyond the size of the input (the peak of the child process; not measured on Windows).

A run is repeatable: input number i of a target is made from ``seed:target:i`` and nothing else, and a finding is
written down with the input itself. What is generated stays bounded: an input is a few hundred kilobytes at
most (a few megabytes for the shapes run at 4n), one working copy of an invented data folder exists at a time,
and everything is removed at the end. No model, no network beyond this computer, no material of a real creator.

What this cannot find is said in the report that came with it: wrong answers that look right, faults that need
two requests at the same moment, and anything behind an input no generator here makes.

Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import copy
import html
import io
import json
import math
import os
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import warnings
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio import checks, corpus, creator, exports, guardrails, hooks, keyword_import, learning, llm    # noqa: E402
from studio import mcp_server, outside, pipeline, posted, server, suggest, timing, transcribe, trends     # noqa: E402
from studio import voicematch, whisper_worker, wordlist                                                   # noqa: E402
from studio import setup as setup_status                                                                  # noqa: E402
from studio.store import StaleScript, Store, TextTooLong, clean_file_name                                 # noqa: E402

try:
    import resource                     # not on Windows: the memory of the child is then not measured
except ImportError:                     # pragma: no cover
    resource = None                     # type: ignore[assignment]

LIMIT_SEC = 10.0                        # an input with no answer after this long ends the child process
ANSWER_FLOOR = 512 * 1024               # an answer smaller than this is never "far larger than the input"
ANSWER_RATIO = 20                       # ... and above it, it is when it is this many times the input
MEMORY_FLOOR = 128 * 1024 * 1024        # growth of the child's peak memory below this is not looked at
MEMORY_RATIO = 50                       # ... and above it, it is a fault at this many times the input
MOST_CHARS = 20000                      # the usual ceiling for one generated piece of text
LARGE_CHARS = 300000                    # the ceiling for the one input in fifty that is large
BATCH = 20                              # inputs asked of the child at a time
MB = 1024 * 1024


# ---- inputs as they are written down ----------------------------------------------------------------------
class Raw:
    """A piece of JSON text that is written as it stands: ``NaN``, ``1e999``, a number of 5,000 digits, half a character."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return "Raw(%r)" % (self.text if len(self.text) < 60 else self.text[:57] + "...")


def dumps(value: Any) -> str:
    """JSON text for ``value``. A ``Raw`` is written as it stands; text that is not UTF-8 is written with escapes."""
    if isinstance(value, Raw):
        return value.text
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return json.dumps(value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{" + ",".join(dumps(str(key)) + ":" + dumps(inner) for key, inner in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(inner) for inner in value) + "]"
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if value != value else ("Infinity" if value > 0 else "-Infinity")
    return json.dumps(value)


def pack(value: Any) -> Any:
    """An input as it is sent to the child and kept in a finding: bytes and ``Raw`` become small objects."""
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Raw):
        return {"$raw": value.text}
    if isinstance(value, dict):
        return {str(key): pack(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [pack(inner) for inner in value]
    return value


def unpack(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            return base64.b64decode(value["$bytes"])
        if set(value) == {"$raw"}:
            return Raw(value["$raw"])
        return {key: unpack(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [unpack(inner) for inner in value]
    return value


def as_bytes(value: Any) -> bytes:
    """Text or bytes as the bytes of a file. Half a character, which UTF-8 cannot hold, is written the way a broken writer would."""
    return value.encode("utf-8", "surrogatepass") if isinstance(value, str) else bytes(value)


def encoded(text: str, codec: str) -> bytes:
    """Text as the bytes of a file in ``codec``. What the codec cannot hold is written as a broken writer would
    write it where that is possible (half a character in UTF-8, -16 and -32), and as a question mark elsewhere."""
    try:
        return text.encode(codec, "surrogatepass")
    except UnicodeError:
        return text.encode(codec, "replace")


# ---- pieces every generator uses --------------------------------------------------------------------------
def pick(rng: random.Random, options: Sequence[Any]) -> Any:
    return options[rng.randrange(len(options))]


def some(rng: random.Random, most: int) -> int:
    """A size from 1 to ``most``, small ones as likely as large ones (even on a log scale)."""
    return int(2 ** rng.uniform(0, math.log2(max(most, 2))))


def ceiling(rng: random.Random) -> int:
    return LARGE_CHARS if rng.random() < 0.02 else MOST_CHARS


# One of these, repeated, is "a long run of one character": each froze or bloated something once, or could.
RUNS = ["[", "]", "【", "［", "(", "（", "<", ">", "{", "}", "&", "#", ";", " ", "\t", "\n", "\r\n", "\r", "\u3000", "/",
        "…", "...", "哈", "啊", "最", "a", "A", "9", "0", ".", ",", "，", "-", '"', "'", "\\", "`", "=", "+", "|", "%", "_",
        "~", "？", "！", "。", "\u200b", "\u200d", "\ufeff", "\u202e", "\u200f", "\u0301", "\u20dd", "\u3164", "\u2800",
        "\x00", "\x1e", "\x7f", "ａ", "１", "Ａ", "🙂", "\ufe0f", "XX", "&#", "&#x", "&amp;", "<w:p ", "<w:t>", "<br>",
        "{\\an8}", "-->", '""', "1,", "a,", "万", "%s", "{m}", "{@name}", "宝.子.们.", "哈…", "a ", "哈/", "[轻笑]"]
WORDS = ["今天", "去超市", "买菜", "结果", "人特别多嘛", "排队", "半小时啦", "其实", "然后", "我老公", "Costco", "weekend",
         "toner", "面霜", "最好", "第一", "国家级", "根治", "宝子们", "家人们", "绝绝子", "综上所述", "值得一提的是", "[轻笑]",
         "【停顿0.5s】", "`live`", "12.5万", "1,234", "3k+", "2亿", "00:01", "2026-11-18", "https://example.com/a",
         "http://[", "javascript:alert(1)", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "' OR 1=1 --",
         "../../etc/passwd", "NaN", "Infinity", "null", "true", "-0", "1e999", "İ", "ß", "ǅ", "ﬃ", "Ⅻ", "㍿", "ｶﾞ", "ё",
         "а", "ο", "\u0131", "\ud800", "\udc00", "e\u0301\u0301\u0301", "👨\u200d👩\u200d👧", "\u202eexe.txt", "/", "，", "。", "？", " "]


def words(rng: random.Random, most: int = 200) -> str:
    """Ordinary and odd words side by side: what a creator, a model or a file might really hold."""
    out, used = [], 0
    while used < some(rng, most):
        piece = pick(rng, WORDS)
        out.append(piece)
        used += len(piece)
    return pick(rng, ("", " ", " / ", "，", "\n")).join(out)


def run_of(rng: random.Random, most: int = MOST_CHARS) -> str:
    """One piece repeated many times."""
    piece = pick(rng, RUNS)
    return piece * max(1, some(rng, most) // len(piece))


def nasty(rng: random.Random, most: Optional[int] = None) -> str:
    """Text made to hurt: a run, a run between two words, words, or several of those."""
    most = ceiling(rng) if most is None else most
    kind = rng.random()
    if kind < 0.35:
        return run_of(rng, most)
    if kind < 0.6:
        return words(rng, 30) + run_of(rng, most) + words(rng, 30)
    if kind < 0.85:
        return words(rng, min(most, 2000))
    return "".join(pick(rng, (run_of, words))(rng, max(2, most // 4)) for _ in range(pick(rng, (2, 3, 4))))


# ---- changing a valid example -----------------------------------------------------------------------------
SCALARS: List[Any] = [None, True, False, 0, -1, 1, 2 ** 63, -(2 ** 63) - 1, 0.5, -0.0, 1e308, "", "0", " ", [], {}, [[]],
                      [None], {"a": 1}]
RAWS = ["NaN", "Infinity", "-Infinity", "1e999", "-1e999", "1E400", "1" + "0" * 400, "-" + "9" * 400, "9" * 5000,
        '"\\ud800"', '"\\udc00x"', '"a\\ud83d"', '"\\u0000"', "1e308", "4e-324", "0.1e-999", "0." + "9" * 3000]
DEPTHS = (20, 100, 500, 900, 990, 1100, 5000, 100000)


def nest(rng: random.Random, depth: Optional[int] = None) -> Raw:
    """Values inside values, ``depth`` deep, written as text so that making it needs no deep call stack."""
    depth = depth or pick(rng, DEPTHS)
    opening, closing = pick(rng, (("[", "]"), ('{"a":', "}"), ('[{"a":', "}]")))
    return Raw(opening * depth + pick(rng, ("1", '"x"', "null", "")) + closing * depth)


def hostile_value(rng: random.Random) -> Any:
    kind = rng.random()
    if kind < 0.35:
        return copy.deepcopy(pick(rng, SCALARS))
    if kind < 0.6:
        return Raw(pick(rng, RAWS))
    if kind < 0.85:
        return nasty(rng)
    if kind < 0.93:
        return nest(rng)
    return [nasty(rng, 40)] * some(rng, 3000)


def spots(value: Any) -> List[Tuple[Any, Any]]:
    """Every place in a value where something stands: ``(the list or object, the position or key)``."""
    found, stack = [], [value]
    while stack:
        node = stack.pop()
        items = list(node.items()) if isinstance(node, dict) else list(enumerate(node)) if isinstance(node, list) else []
        for key, inner in items:
            found.append((node, key))
            stack.append(inner)
    return found


def mutated(rng: random.Random, value: Any) -> Any:
    """A copy of a valid example with one to three things wrong with it."""
    value = copy.deepcopy(value)
    for _ in range(pick(rng, (1, 1, 1, 2, 3))):
        places = spots(value)
        if not places or rng.random() < 0.03:
            return hostile_value(rng)
        node, key = pick(rng, places)
        old, how = node[key], rng.random()
        if how < 0.45:
            node[key] = hostile_value(rng)
        elif how < 0.6 and isinstance(old, str):
            node[key] = nasty(rng)
        elif how < 0.7 and isinstance(old, list) and old:           # one of its items, very many times (but not megabytes of it)
            item = pick(rng, old)
            node[key] = [copy.deepcopy(item) for _ in range(some(rng, min(3000, max(2, LARGE_CHARS // weight(item)))))]
        elif how < 0.8 and isinstance(node, dict):
            del node[key]
        elif how < 0.9 and isinstance(old, str):
            node[key] = old + nasty(rng, 200)
        elif isinstance(old, (int, float)) and not isinstance(old, bool):
            node[key] = pick(rng, (-old, old * 1000, old + 0.5, 0, Raw(pick(rng, RAWS))))
        else:
            node[key] = [old] if rng.random() < 0.5 else {"value": old}
    return value


class Shut(Exception):
    """The app's reader of outside JSON refused an input before it reached the parser that is being tried."""


def through_the_door(value: Any) -> Any:
    """``value`` as it is once the app's own reader of outside JSON has read it (``llm.loads_strict``).

    Text reaches most parsers inside a request, so what they are handed is what that reader lets through.
    Raises ``Shut`` when the reader refuses it, which is an answer and not a fault.
    """
    try:
        return llm.loads_strict(dumps(value))
    except ValueError as exc:
        raise Shut(str(exc))


# ---- archives ---------------------------------------------------------------------------------------------
METHODS = (zipfile.ZIP_DEFLATED,) * 12 + (zipfile.ZIP_STORED,) * 4 + (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)


def archive(entries: Sequence[Sequence[Any]], cut: Optional[int] = None, flips: Sequence[Sequence[int]] = ()) -> bytes:
    """A zip of ``[name, content, method]`` entries, then cut short at ``cut`` and with the bytes in ``flips`` changed."""
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                 # two entries of one name are made on purpose
        with zipfile.ZipFile(buffer, "w") as packed:
            for name, content, method in entries:
                info = zipfile.ZipInfo(str(name))
                info.compress_type = method
                if isinstance(content, dict):           # {"run": piece, "times": n}: written piece by piece,
                    piece = as_bytes(content.get("run", "")) * 4096         # so that it is never held whole here
                    with packed.open(info, "w") as entry:
                        for _ in range(max(0, int(content.get("times", 0))) // 4096):
                            entry.write(piece)
                else:
                    packed.writestr(info, as_bytes(content))
    data = bytearray(buffer.getvalue())
    for at, byte in flips:
        if data:
            data[at % len(data)] = byte % 256
    return bytes(data if cut is None else data[:max(0, cut) % (len(data) + 1)])


class Unmade(Exception):
    """An input describes a file that cannot be written (a name the archive format has no way to hold): not a fault of the app."""


def packed(payload: Dict[str, Any]) -> bytes:
    """The archive an input describes with ``entries``, ``cut`` and ``flips``."""
    try:
        return archive(payload.get("entries") or [], payload.get("cut"), payload.get("flips") or ())
    except (ValueError, TypeError, struct.error, OverflowError, zipfile.LargeZipFile) as exc:
        raise Unmade(str(exc))


def damage(rng: random.Random) -> Dict[str, Any]:
    """How an archive is damaged after it was made: not at all (mostly), cut short, or with a few bytes changed."""
    kind = rng.random()
    if kind < 0.84:
        return {}
    if kind < 0.92:
        return {"cut": rng.randrange(1 << 20)}
    return {"flips": [[rng.randrange(1 << 20), rng.randrange(256)] for _ in range(pick(rng, (1, 2, 8)))]}


# ---- valid examples, all invented -------------------------------------------------------------------------
EXAMPLE_DNA = json.loads((ROOT / "schemas" / "voice_dna.example.json").read_text(encoding="utf-8"))
OWN = [
    "周末去了趟花市嘛 / 结果人多得走不动哈 / 然后我挑了两盆绿萝 / 其实家里已经放不下啦",
    "入秋以后嘴唇老是干嘛 / 所以我换了一支润唇膏 / 结果一天要涂好几次哈 / 其实多喝水更管用",
    "昨天试着做了南瓜汤啦 / 然后加了一点 cream / 我老公说像餐厅的嘛 / 结果他喝了两碗哈",
    "最近迷上了早起散步嘛 / 所以每天六点就出门 / 结果遇到好多遛狗的哈 / 然后我也想养一只啦",
    "这周收拾了一下衣柜啦 / 结果翻出三件没剪吊牌的 / 其实都是打折买的嘛 / 所以这个月不买衣服哈",
    "今天去 farmers market 买菜嘛 / 然后带回来一大把香菜 / 我老公不吃香菜哈 / 所以全是我的啦",
]
AI_LINES = [{"spoken_script_zh": text, "time_range_sec": "00:%02d-00:%02d" % (4 * at, 4 * at + 4)} for at, text in enumerate((
    "综上所述这款面霜的质地非常轻盈 / 与此同时它的吸收速度令人印象深刻", "因此我强烈推荐给大家",
    "总而言之这是一个值得入手的选择", "然而价格略高需要理性消费"))]
EDITED_LINES = [dict(AI_LINES[0], spoken_script_zh="入冬以后我换了一款面霜嘛 / 结果脸真的不干了哈"), AI_LINES[1],
                dict(AI_LINES[2], spoken_script_zh="所以我真的很推荐嘛"), AI_LINES[3]]
HEALTH_LINES = [{"spoken_script_zh": "这周又去花市啦 / 然后买了两盆绿萝 / 宝子们", "time_range_sec": "00:00-00:05"},
                {"spoken_script_zh": "结果我老公说放不下哈 / 其实阳台还有地方嘛 / 这是最好的", "audio_mode": "VOICEOVER"}]
SRT = "1\n00:00:01,000 --> 00:00:04,000\n今天去花市买花嘛\n\n2\n00:00:04,500 --> 00:00:09,000\n结果人多得走不动哈\n\n" \
      "3\n00:00:09,200 --> 00:00:15,000\n然后我挑了两盆绿萝回家啦\n"
try:
    SCRIPT_REPLY = json.loads((ROOT / "tests" / "fixtures" / "worked_example_script.json").read_text(encoding="utf-8"))
except (OSError, ValueError):               # the fixture is not part of a release package
    SCRIPT_REPLY = {"script_lines": [dict(line, line_no=at + 1, audio_mode="VOICEOVER") for at, line in enumerate(AI_LINES)]}
SCRIPT_REPLY = dict(SCRIPT_REPLY, rednote_post_package={"title_options": [{"title_zh": "入冬面霜"}], "caption_body_zh": "面霜分享",
                                                        "hashtags_zh": ["#面霜", "#秋冬护肤"]})
IDEAS_REPLY = {"ideas": [{"title_zh": "花市买花", "title_en": "Flower market", "kind": "BUILD_ON_WINNER", "why_zh": "上次效果好",
                          "why_en": "Did well", "confidence": "medium", "script_notes_zh": "周末去花市", "video_format": "VLOG_VOICEOVER",
                          "target_duration_sec": 60, "evidence_titles": ["第1条"], "trend_keywords": ["面霜"]}],
               "data_note_zh": "数据不多", "data_note_en": "Little data"}
TRENDS_REPLY = {"cannot_browse": False, "keywords": [{"keyword": "露营", "source_url": "https://example.com/camping",
                                                     "source_date": time.strftime("%Y-%m-%d"), "why_zh": "最近很多", "why_en": "Many lately"}]}
SUGGEST_REPLY = {"suggestions": [{"line_no": 1, "spoken_script_zh": "入冬以后我真的离不开这罐面霜嘛", "why_zh": "更口语",
                                  "english_partner_translation": "I cannot do without this cream", "why_en": "More spoken"},
                                 {"line_no": 2, "spoken_script_zh": "所以想推荐给你们哈", "why_zh": "短一点", "why_en": "Shorter"}]}
CREATOR = {"name": "小满", "pronoun": "she", "channel": "小满的日常", "niches": ["护肤", "探店"], "about": "住在海边的上班族",
           "partner": {"called": "我老公", "language": "English"}, "never": ["宝子们"]}


def seed(folder: Path) -> None:
    """An invented creator's data folder: a profile, a small library with numbers, an accepted voice profile,
    tracked keywords, an AI draft the creator edited and posted, a script typed by hand, and an AI service that
    is "set up" with a key that is not one."""
    store = Store(folder)
    store.save_creator(creator.normalize(CREATOR))
    for at, text in enumerate(OWN):
        store.create_sample(title="第%d条" % (at + 1), text=text,
                            metrics={"views": 1000 * (at + 1), "likes": 100 + 10 * at, "collects": 20 + at, "comments": 5 + at})
    store.create_sample(title="合作", text="这次是品牌合作嘛 / 面霜是他们送的哈", is_sponsored=True, metrics={"likes": 40})
    cues = timing.parse_subtitles(SRT)
    store.create_sample(title="花市字幕", text=timing.transcript_text(cues), kind="subtitle", timing=timing.timing_stats(cues),
                        source_name="花市.srt")
    dna = copy.deepcopy(EXAMPLE_DNA)
    dna["source_corpus_summary"] = {"based_on": 6, "learned_at": "2026-11-01 10:00", "clause_lengths_measured": True,
                                    "speaking_rate_source": "default", "material_as_of": time.time() - 86400}
    store.save_dna(dna)
    store.upsert_trends([{"keyword": "秋冬护肤", "heat": 125000}, {"keyword": "面霜"}], source="manual")
    store.upsert_trends([{"keyword": "露营", "source_url": "https://example.com/a"}], source="ai")
    script = store.create_script(title="面霜", notes="入冬换面霜", lines=AI_LINES, from_ai=True, keywords=["面霜"],
                                 post=SCRIPT_REPLY["rednote_post_package"])
    store.update_script(script["id"], {"lines": EDITED_LINES})
    store.add_ai_suggestions(script["id"], ["所以我真的很推荐嘛"])
    store.add_lesson(script_id=script["id"], ai=AI_LINES[0]["spoken_script_zh"], final=EDITED_LINES[0]["spoken_script_zh"])
    posted.mark(store, script["id"], {"views": 1200, "likes": 150})
    store.create_script(title="手写", lines=[{"spoken_script_zh": "这是我自己写的嘛 / 没有让 AI 帮忙哈"}])
    store.update_config(lambda config: config.update(provider="gemini", model="fuzz-model",
                                                     keys={"gemini": "not-a-key", "openai": "not-a-key"}))


class Env:
    """What the targets share inside the child: the invented data folder, one working copy of it at a time, and
    (made when first asked for) the app's server and a stand-in for a model server, both on free ports of this computer."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lmv-fuzz-"))
        self.template = self.root / "template"
        seed(self.template)
        self.work = self.root / "work"
        self.httpd: Any = None
        self.listener: Any = None
        self.said: Dict[str, Any] = {}              # what the stand-ins answer for the input being tried
        self.raised: List[BaseException] = []       # what the app wrote down as "internal error" while an input was tried
        # The app prints what it did not expect and answers "internal error". Here the printing is replaced by
        # keeping the exception, so that such an answer can be reported with the place it came from. The
        # waiting times of the server are shortened: an input that sends half a request is answered at once
        # instead of after half a minute. All of it is put back by close().
        self._kept = (traceback.print_exc, server.Handler.timeout, server.DRAIN_TIMEOUT_SEC, server.UPLOAD_IDLE_SEC,
                      whisper_worker.block_network, whisper_worker.network_denied_by_system)
        traceback.print_exc = lambda *args, **more: self.raised.append(sys.exc_info()[1])     # type: ignore[assignment]
        server.Handler.timeout, server.DRAIN_TIMEOUT_SEC, server.UPLOAD_IDLE_SEC = 3, 0.5, 1
        # The Whisper worker cuts this whole process off from the network before it works, and for good. Its
        # reading of what the engine returns is tried here without that: the stand-ins need this computer's own address.
        whisper_worker.block_network = lambda: None
        whisper_worker.network_denied_by_system = lambda: True
        self.usual_health = len(dumps_plain(health(self.fresh())))

    def fresh(self) -> Store:
        """A new copy of the invented data folder, in place of the one before it."""
        if self.httpd is not None:
            deadline = time.time() + 1.5
            while any(j["status"] == "running" for j in self.httpd.app.jobs._jobs.values()) and time.time() < deadline:
                time.sleep(0.02)
        for _ in range(3):
            shutil.rmtree(str(self.work), ignore_errors=True)
            if not self.work.exists():
                break
            time.sleep(0.02)
        shutil.copytree(str(self.template), str(self.work), dirs_exist_ok=True)
        return Store(self.work)

    def close(self) -> None:
        for running in (self.httpd, self.listener):
            if running is not None:
                running.shutdown()
                running.server_close()
        if self.httpd is not None:
            self.httpd.app.uploads.close()
        (traceback.print_exc, server.Handler.timeout, server.DRAIN_TIMEOUT_SEC, server.UPLOAD_IDLE_SEC,
         whisper_worker.block_network, whisper_worker.network_denied_by_system) = self._kept
        shutil.rmtree(str(self.root), ignore_errors=True)


def dumps_plain(value: Any) -> str:
    """``value`` as the app sends an answer: JSON in which NaN and Infinity are an error."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Broke(Exception):
    """A reader of stored data raised after an input was taken in: ``reader`` names which."""

    def __init__(self, reader: str, cause: BaseException) -> None:
        super().__init__("%s: %s: %s" % (reader, type(cause).__name__, cause))
        self.reader, self.cause = reader, cause


def health(store: Store) -> Dict[str, Any]:
    """What every page reads, read once more: a profile of the wrong shape once broke every check for good.

    Refusals a reader is documented to give (no material, no such line) are answers. Anything else raised is
    reported with the reader's name (``Broke``).
    """
    scripts = store.list_scripts()
    first = scripts[-1]["id"] if scripts else ""
    readers: List[Tuple[str, Callable[[], Any]]] = [
        ("lint", lambda: checks.lint_payload(store, HEALTH_LINES, 60, ["面霜"], {})),
        ("setup", lambda: setup_status.status(store)),
        ("trends", lambda: trends.payload(store)),
        ("hooks", lambda: hooks.library(store.list_samples())),
        ("lessons", lambda: pipeline.lessons_payload(store)),
        ("library", lambda: posted.for_page(store, store.list_samples())),
        ("scripts", lambda: posted.with_posted_dates(store, scripts)),
        ("voice", lambda: [store.load_dna(), pipeline.pending_voice(store), setup_status.voice_history(store)]),
        ("creator", lambda: [creator.for_page(store.load_creator()), store.public_settings()]),
        ("wordlist", lambda: wordlist.payload(store)),
        ("ideas", lambda: [store.load_recommendations(), corpus.performance(store.list_samples())]),
        ("script", lambda: [posted.with_posted(store, store.get_script(first)), pipeline.script_changes(store, first)] if first else None),
        ("exports", lambda: [len(exports.export_file(store.get_script(first), kind, store.load_dna()[0], "zh")[1])
                             for kind in exports.KINDS] if first else None),
        ("draft prompt", lambda: len(pipeline.draft_brief(store, notes="花市", target_sec=60, video_format=pipeline.VIDEO_FORMATS[0],
                                                           keywords=["面霜"])["user"])),
        ("ideas prompt", lambda: len(pipeline.ideas_prompt(store)[1])),
        ("lookup prompt", lambda: len(pipeline.trends_prompt(store)[1])),
        ("voice prompt", lambda: len(pipeline.voice_prompt(store)[1])),
        ("line prompt", lambda: len(suggest.suggest_prompt(store, first, "rewrite", 0)[1])),
        ("mcp", lambda: [mcp_server.t_get_voice_profile(store, {}), mcp_server.t_list_library(store, {}),
                         mcp_server.t_list_trends(store, {})]),
    ]
    out: Dict[str, Any] = {}
    for name, read in readers:
        try:
            out[name] = read()
        except (llm.LLMError, suggest.SuggestError) as refusal:
            out[name] = "refused: %s" % getattr(refusal, "code", "")
        except Exception as exc:        # noqa: BLE001 - named and reported as a fault of this input
            raise Broke(name, exc)
    return out


# ---- a target, and what is checked for every input --------------------------------------------------------
class Fault(Exception):
    """A fault a target saw for itself, such as an answer "internal error": ``kind`` names the sort of fault,
    ``where`` the place it is filed under, so that one fault met by a thousand inputs is one finding."""

    def __init__(self, kind: str, where: str, detail: str = "") -> None:
        super().__init__("%s at %s: %s" % (kind, where, detail))
        self.kind, self.where, self.detail = kind, where, detail


class Target:
    """One way in. ``make(rng)`` gives an input, ``run(env, input)`` hands it to the app and gives the answer.

    ``refused`` is the refusal the parser is documented to give: exception types, or a function that says
    whether an exception is that refusal. ``ready(input)`` turns an input into what the app is handed (packs an
    archive, sends the text through the app's reader of JSON); the time and memory of that are this tool's own
    and are not counted, and an input it cannot make ready (``Unmade``, ``Shut``) is not tried. ``usual`` says that the answer holds the usual answer of ``health``
    once, which is then not counted as part of the answer. ``named`` are the shapes that are run at three
    sizes, ``(name, largest size, build(size))``; ``slots`` are the places text can stand in an input,
    ``(name, largest size, put(text))``, into which patterns of repeated pieces are put at three sizes.
    """

    def __init__(self, name: str, about: str, make: Callable[[random.Random], Any], run: Callable[["Env", Any], Any],
                 refused: Any = (), ready: Optional[Callable[[Any], Any]] = None, floor: int = ANSWER_FLOOR, ratio: int = ANSWER_RATIO, usual: bool = False,
                 memory_floor: int = MEMORY_FLOOR, named: Sequence[Tuple[str, int, Callable[[int], Any]]] = (),
                 slots: Sequence[Tuple[str, int, Callable[[str], Any]]] = (), planted: bool = False) -> None:
        self.name, self.about, self.make, self.run = name, about, make, run
        self.ready = ready or (lambda payload: payload)
        self.refused = refused if callable(refused) and not isinstance(refused, type) else _is_one_of(refused)
        self.floor, self.ratio, self.usual, self.memory_floor = floor, ratio, usual, memory_floor
        self.named, self.slots, self.planted = list(named), list(slots), planted


def _is_one_of(kinds: Any) -> Callable[[BaseException], bool]:
    kinds = tuple(kinds) if isinstance(kinds, (tuple, list)) else (kinds,)
    return lambda exc: isinstance(exc, kinds)


TARGETS: Dict[str, Target] = {}


def add(name: str, about: str, make: Callable[[random.Random], Any], run: Callable[["Env", Any], Any], **more: Any) -> None:
    TARGETS[name] = Target(name, about, make, run, **more)


def case(seed: Any, target: str, index: Any) -> random.Random:
    """The source of chance for one input: the same seed, target and number give the same input on any computer."""
    return random.Random("%s:%s:%s" % (seed, target, index))


def weight(payload: Any) -> int:
    """How large an input is, in bytes as it is written down."""
    return len(json.dumps(pack(payload), ensure_ascii=False).encode("utf-8", "surrogatepass"))


def where(exc: BaseException) -> str:
    """The innermost place inside the app an exception came through, as ``file:function``. No line number:
    a finding has to keep its name when a line is added above it."""
    place = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename)
        if path.parent.name == "studio":
            place = "%s:%s" % (path.name, frame.name)
    return place or "outside studio/"


def peak_memory() -> int:
    """The most memory this process has held so far, in bytes; 0 where the system does not say (Windows)."""
    if resource is None:
        return 0
    used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return used if sys.platform == "darwin" else used * 1024


def short(text: Any, most: int = 300) -> str:
    text = str(text)
    return text if len(text) <= most else text[:most] + "… (%d characters)" % len(text)


def examine(target: Target, env: "Env", payload: Any) -> Dict[str, Any]:
    """Hand one input to a target and check everything that can be checked from inside the process.

    The answer says how large the input was, how much processor time went by, and under ``found`` what was
    wrong, if anything: ``{"kind", "where", "detail"}``. (No answer at all is seen by the process that waits.)
    """
    size = weight(payload)
    found: Optional[Dict[str, str]] = None
    answer: Any = None
    try:
        ready = target.ready(payload)
    except (Unmade, Shut):
        return {"bytes": size, "cpu": 0.0, "grown": 0, "found": None, "tried": False}
    except Exception as exc:            # noqa: BLE001 - the app's own reader of JSON raised something that is not its refusal
        return {"bytes": size, "cpu": 0.0, "grown": 0, "found": {"kind": "raised", "where": "%s in %s" % (type(exc).__name__, where(exc)),
                                                                   "detail": short(exc)}}
    before, started = peak_memory(), time.process_time()
    try:
        answer = target.run(env, ready)
    except Fault as fault:
        found = {"kind": fault.kind, "where": fault.where, "detail": short(fault.detail)}
    except Broke as broke:
        found = {"kind": "broke a page", "where": "%s <- %s in %s" % (broke.reader, type(broke.cause).__name__, where(broke.cause)),
                 "detail": short(broke.cause)}
    except Exception as exc:            # noqa: BLE001 - everything raised is looked at
        if not target.refused(exc):
            found = {"kind": "raised", "where": "%s in %s" % (type(exc).__name__, where(exc)), "detail": short(exc)}
    spent = time.process_time() - started
    if found is None:
        try:
            written = len(dumps_plain(answer).encode("utf-8"))
        except (ValueError, TypeError, RecursionError) as exc:
            found = {"kind": "answer is not JSON", "where": type(exc).__name__, "detail": short(exc)}
        else:
            written -= env.usual_health if target.usual else 0
            if written > target.floor and written > target.ratio * size:
                found = {"kind": "answer far larger than the input", "where": target.name,
                         "detail": "%d bytes were answered to %d" % (written, size)}
    grown = peak_memory() - before
    if found is None and grown > target.memory_floor and grown > MEMORY_RATIO * size:
        found = {"kind": "memory far beyond the input", "where": target.name,
                 "detail": "the process grew by %d bytes for an input of %d" % (grown, size)}
    return {"bytes": size, "cpu": round(spent, 4), "grown": grown, "found": found}


# ---- patterns of repeated pieces, for the runs at three sizes ----------------------------------------------
def pattern(rng: random.Random) -> Tuple[str, Callable[[int], str]]:
    """A way to make text of any size from one or two pieces: ``(what it is, build(size))``.

    A run of one piece, two pieces taking turns, a run with something else at one end, short runs each closed
    by another piece, and lines of a run each: between them these are what made patterns search again and
    again, and what made comparisons of every line with every other line slow.
    """
    a, b = pick(rng, RUNS), pick(rng, RUNS + ["嘿", "x", "\n", " / ", "1"])
    k = pick(rng, (1, 2, 3, 8, 40, 300))
    kind = rng.randrange(7)
    if kind == 0:
        return "%r repeated" % a, lambda n: a * (n // len(a))
    if kind == 1:
        return "%r and %r taking turns" % (a, b), lambda n: (a + b) * (n // len(a + b))
    if kind == 2:
        return "%r repeated, then %r" % (a, b), lambda n: a * (n // len(a)) + b
    if kind == 3:
        return "%r, then %r repeated" % (b, a), lambda n: b + a * (n // len(a))
    if kind == 4:
        return "%d times %r then %r, repeated" % (k, a, b), lambda n: (a * k + b) * (n // len(a * k + b))
    if kind == 5:
        return "lines of %d times %r" % (k, a), lambda n: (a * k + "\n") * (n // (len(a) * k + 1))
    return "breaths of %d times %r" % (k, a), lambda n: (a * k + " / ") * (n // (len(a) * k + 3))


def shape(target: Target, seed: Any, index: int) -> Tuple[str, int, Callable[[int], Any]]:
    """Shape number ``index`` of a target: its named shapes first, then patterns put into its slots."""
    if index < len(target.named):
        return target.named[index]
    rng = case(seed, target.name, "shape %d" % index)
    slot, most, put = pick(rng, target.slots)
    what, build = pattern(rng)
    return "%s: %s" % (slot, what), most, lambda n: put(build(n))


# ---- target: a .docx ---------------------------------------------------------------------------------------
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_DOCX_PIECES = ["<w:p>", "<w:p ", "</w:p>", "<w:t>", "<w:t ", "</w:t>", '<w:t xml:space="preserve">', "<w:br/>", "<w:tab/>", "<w:br ",
                "<w:cr/>", "<w:tab ", "&#", "&#x", "&amp;", "&lt;", "&", ";", "<", ">", "x", "哈", "<w:tbl>", "<!--", "]]>", "<![CDATA[",
                "&#9;", "&#x1F600;", "&#xD800;", "&#1114112;", "&nbsp;", "&notit;", "&#0;", "\x00"]


def docx_of(xml: Any, method: int = zipfile.ZIP_DEFLATED) -> Dict[str, Any]:
    return {"entries": [["[Content_Types].xml", "<Types/>", method], ["word/document.xml", xml, method]]}


def _docx_body(text: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><w:document %s><w:body>%s</w:body></w:document>' % (_W, text)


def make_docx(rng: random.Random) -> Dict[str, Any]:
    kind, method = rng.random(), pick(rng, METHODS)
    if kind < 0.3:                      # a document as Word writes one, with odd text in it
        xml = _docx_body("".join('<w:p><w:r><w:t%s>%s</w:t></w:r></w:p>' % (pick(rng, ("", ' xml:space="preserve"')), html.escape(words(rng)))
                                 for _ in range(some(rng, 60))))
    elif kind < 0.6:                    # the reader's own vocabulary in any order, mostly never closed
        unit = "".join(pick(rng, _DOCX_PIECES) for _ in range(pick(rng, (1, 2, 3, 5))))
        xml = _docx_body(unit * (some(rng, ceiling(rng)) // len(unit) + 1))
    elif kind < 0.75:                   # a character reference of many digits, or many of them
        digits = pick(rng, "0123456789abcdefADg") * some(rng, ceiling(rng))
        xml = _docx_body("<w:p><w:t>%s</w:t></w:p>" % (pick(rng, ("&#%s;", "&#%s", "&#x%s;", "&%s;", "&#0%s;")) % digits) * pick(rng, (1, 1, 2, 50)))
    elif kind < 0.85:                   # text that is not XML at all, or not UTF-8
        xml = pick(rng, (nasty(rng), encoded(nasty(rng), "utf-16"), encoded(nasty(rng), "gb18030"),
                         bytes(rng.randrange(256) for _ in range(some(rng, 4000)))))
    elif kind < 0.88:                   # an archive that unpacks to far more than its size
        xml = {"run": pick(rng, ("<w:p><w:t>哈</w:t></w:p>", "<w:p>", " ", "&#9;")), "times": pick(rng, (1, 2, 21, 24)) * MB}
        method = zipfile.ZIP_DEFLATED
    else:
        xml = _docx_body("<w:p><w:t>花市</w:t></w:p>")
    entries = docx_of(xml, method)["entries"]
    extra = rng.random()
    if extra < 0.1:                     # very many entries, or the same name twice
        entries = [["word/%d.xml" % at, "x", zipfile.ZIP_STORED] for at in range(some(rng, 5000))] + entries
    elif extra < 0.2:
        entries = entries + [["word/document.xml", nasty(rng, 2000), pick(rng, METHODS)]]
    elif extra < 0.25:
        entries = [entry for entry in entries if entry[0] != "word/document.xml"] + [[nasty(rng, 300), "x", method]]
    return dict({"entries": entries}, **damage(rng))


def run_docx(env: "Env", data: bytes) -> Any:
    text = corpus.extract_docx_text(data)
    if len(text) > corpus.MAX_DOCX_XML_BYTES:
        raise Fault("answer far larger than the input", "corpus.py:extract_docx_text",
                    "%d characters, above the %d the reader says it stops at" % (len(text), corpus.MAX_DOCX_XML_BYTES))
    return {"characters": len(text), "start": text[:2000], "end": text[-2000:], "clauses": corpus.voice_stats([text[:200000]])["clauses"]}


add("docx", "a .docx file", make_docx, run_docx, refused=corpus.ImportError_, ready=packed,
    named=[("a character reference of n digits", 400000, lambda n: docx_of(_docx_body("<w:p><w:t>&#" + "9" * n + ";</w:t></w:p>"))),
           ("a hexadecimal character reference of n digits", 400000, lambda n: docx_of(_docx_body("<w:p><w:t>&#x" + "f" * n + "</w:t></w:p>"))),
           ("n paragraphs never closed", 400000, lambda n: docx_of(_docx_body("<w:p>" * (n // 5)))),
           ("n text runs never closed in one paragraph", 400000, lambda n: docx_of(_docx_body("<w:p>" + "<w:t>" * (n // 5) + "</w:p>"))),
           ("n ampersands", 400000, lambda n: docx_of(_docx_body("<w:p><w:t>" + "&" * n + "</w:t></w:p>"))),
           ("n entries in the archive", 20000, lambda n: {"entries": [["w/%d" % at, "", zipfile.ZIP_STORED] for at in range(n)]
                                                          + docx_of(_docx_body("<w:p><w:t>x</w:t></w:p>"))["entries"]})],
    slots=[("the text of a paragraph", 400000, lambda text: docx_of(_docx_body("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % text))),
           ("the document part", 400000, docx_of)])


# ---- target: a subtitle file -------------------------------------------------------------------------------
_STAMPS = ["00:00:01,000", "00:00:02.500", "0:1", "1:02:03,4", "99:59:59,999", "9999:99:99,999", "00:00:00,000", "12345:00:00,000",
           "00:00:01", "１:２", "00：00：01", "-1:00", "1e3:00", "00:00:01,0000", ":", "0:0:0:0", "٠:٠١"]
_SUB_TEXTS = ["<i>", "</i>", "<b>", '<font color="red">', "<c.yellow>", "<v 小满>", "<00:00:01.500>", "{\\an8}", "{\\", "<br>", "<br/>",
              "&lt;", "&gt;", "&nbsp;", "&amp;", "&lrm;", "<", "{", "}", ">", "<1", "<a", "NOTE", "STYLE", "REGION", "WEBVTT", "-->", "1", "42"]


def _timing_line(rng: random.Random) -> str:
    arrow = pick(rng, (" --> ", "-->", " -->  ", " -> ", " --> " + " " * some(rng, 50), "\t-->\t"))
    return (pick(rng, ("", "", " ", "\t", " " * some(rng, 2000))) + pick(rng, _STAMPS) + arrow + pick(rng, _STAMPS)
            + pick(rng, ("", "", " align:start position:10%", " " + run_of(rng, 2000), "x")))


def make_subtitles(rng: random.Random) -> Dict[str, Any]:
    kind = rng.random()
    if kind < 0.25:
        lines = mutated(rng, SRT.split("\n"))
        text = "\n".join(part if isinstance(part, str) else dumps(part) for part in lines) if isinstance(lines, list) else nasty(rng)
    elif kind < 0.75:
        lines = [pick(rng, ("", "WEBVTT", "WEBVTT - 花市", "\ufeffWEBVTT", "\ufeff"))]
        for at in range(some(rng, 400)):
            block = rng.random()
            if block < 0.6:
                lines += [pick(rng, (str(at + 1), "", str(rng.randrange(10 ** 12)), "9" * some(rng, 3000), "cue-%d" % at))] * (rng.random() < 0.7)
                lines.append(_timing_line(rng))
                lines += ["".join(pick(rng, _SUB_TEXTS + WORDS) for _ in range(some(rng, 12))) for _ in range(pick(rng, (0, 1, 1, 2, 5)))]
            elif block < 0.75:
                lines += [pick(rng, ("NOTE", "STYLE", "REGION", "NOTE " + words(rng, 40))), words(rng, 80)]
            elif block < 0.9:
                lines.append(nasty(rng, 3000))
            lines += [""] * pick(rng, (0, 1, 1, 1, 2, 30))
        text = pick(rng, ("\n", "\n", "\r\n", "\r", "\n\r", "\u2028")).join(lines)
    else:
        text = nasty(rng)
    how = rng.random()
    if how < 0.6:
        return {"file": text}
    if how < 0.8:
        return {"file": encoded(text, pick(rng, ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "big5", "utf-32")))}
    data = bytearray(encoded(text, "utf-8"))
    for _ in range(pick(rng, (1, 3, 30))):
        if data:
            data[rng.randrange(len(data))] = rng.randrange(256)
    return {"file": bytes(data[:rng.randrange(len(data) + 1)] if rng.random() < 0.3 else data)}


def run_subtitles(env: "Env", data: Any) -> Any:
    cues = timing.parse_subtitles(data)
    stats = timing.timing_stats(cues)
    return {"cues": len(cues), "text": timing.transcript_text(cues), "timing": stats, "sample_timing": timing.combine_stats([stats, stats])
            if hasattr(timing, "combine_stats") else None}


def _cues(count: int, text: str = "哈") -> str:
    return "".join("%d\n00:00:%02d,000 --> 00:00:%02d,500\n%s\n\n" % (at + 1, at % 60, at % 60, text) for at in range(count))


add("subtitles", "a subtitle file (.srt, .vtt)", make_subtitles, run_subtitles,
    refused=lambda exc: isinstance(exc, ValueError) and str(exc) == "no_cues",
    ready=lambda payload: through_the_door(payload.get("file")) if isinstance(payload.get("file"), str) else payload.get("file"),
    named=[("n cues", 300000, lambda n: {"file": _cues(n // 40)}),
           ("n cues with no blank line between them", 300000, lambda n: {"file": _cues(n // 40).replace("\n\n", "\n")}),
           ("one cue, then n lines of bare numbers", 300000, lambda n: {"file": _cues(1) + "7\n" * (n // 2)}),
           ("one cue of n lines of text", 300000, lambda n: {"file": _cues(1).rstrip("\n") + "\n" + "哈哈\n" * (n // 3)}),
           ("one cue, then n blocks of loose text", 300000, lambda n: {"file": _cues(1) + "哈哈\n\n" * (n // 4)}),
           ("a timing line of n digits of hours", 300000, lambda n: {"file": "1\n" + "9" * n + ":00:01,000 --> 00:00:02,000\n哈\n"}),
           ("a file of n bytes that is no text", 300000, lambda n: {"file": bytes(range(256)) * (n // 256)})],
    slots=[("the whole file", 300000, lambda text: {"file": text}),
           ("the text of a cue", 300000, lambda text: {"file": "1\n00:00:01,000 --> 00:00:02,000\n" + text.replace("\n\n", "\n") + "\n"}),
           ("after the times of a cue", 300000, lambda text: {"file": "1\n00:00:01,000 --> 00:00:02,000 " + text.replace("\n", " ") + "\n哈\n"}),
           ("before the times of a cue", 300000, lambda text: {"file": text.replace("\n", " ") + "00:00:01,000 --> 00:00:02,000\n哈\n"}),
           ("the bytes of a file", 300000, lambda text: {"file": encoded(text, "utf-16")})])


# ---- target: a keyword spreadsheet -------------------------------------------------------------------------
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_LINKS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_LAUGHS = ('<!DOCTYPE lolz [<!ENTITY lol "lol">'
           + "".join('<!ENTITY lol%d "%s">' % (n, ("&lol%s;" % (n - 1 if n > 1 else "")) * 10) for n in range(1, 10)) + "]>")
_CELLS = ["关键词", "热度", "秋冬护肤", "面霜", "keyword", "heat", "搜索指数", "12.5万", "1,234", "3k+", "2亿", "1e5", "-3", "", " ", "0",
          "=SUM(A1:A9)", "=cmd|' /C calc'!A0", "@x", "+1", "\t", "NaN", "Infinity", "1e999", "９９", "１２万", "9" * 400, "万", "亿" * 30]


_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml(rng: random.Random, value: str) -> str:
    """``value`` as text inside XML. Characters XML cannot hold make a whole part unreadable, which is one
    refusal and no more: they are left in only once in thirty times, so that most inputs get past that."""
    return html.escape(value if rng.random() < 0.03 else _NOT_XML.sub("", value))


def _letters(column: int) -> str:
    out, column = "", column + 1
    while column:
        column, rest = divmod(column - 1, 26)
        out = chr(65 + rest) + out
    return out


def _sheet_cell(rng: random.Random, value: str, row: int, column: int, wild: bool) -> str:
    """One cell of a sheet. Only a ``wild`` sheet has cells that say they are somewhere else, or that are not
    well-formed XML: one of those makes the whole sheet unreadable, so most sheets have none."""
    at = pick(rng, (' r="%s%d"' % (_letters(column), row), "", ' r="%s%d"' % (_letters(rng.randrange(20000)), rng.randrange(1, 10 ** 7)),
                    ' r="%s"' % ("A" * some(rng, 3000)), ' r="ZZZZZZZZ1"', ' r="A%s"' % ("9" * some(rng, 3000)))) if wild and rng.random() < 0.2 \
        else ' r="%s%d"' % (_letters(column), row)
    kind = rng.random() * (1.0 if wild else 0.84)
    if kind < 0.5:
        return '<c%s t="inlineStr"><is><t>%s</t></is></c>' % (at, _xml(rng, value))
    if kind < 0.7:
        return '<c%s><v>%s</v></c>' % (at, _xml(rng, value))
    if kind < 0.85:
        return '<c%s t="s"><v>%s</v></c>' % (at, pick(rng, ("0", "1", "2", "-1", "99999999", "1e3", "x", "9" * 3000, " 1 ") if wild else ("0", "0", "1", "2")))
    return pick(rng, ('<c%s/>', '<c%s t="b"><v>1</v></c>', '<c%s t="e"><v>#N/A</v></c>', '<c%s t="str"><f>A1</f><v>x</v></c>',
                      '<c%s t="inlineStr"><is><r><t>a</t></r><r><t>b</t></r><rPh><t>no</t></rPh></is></c>', "<c%s><c%s><c%s>")).replace("%s", at)


def workbook_of(sheet: Any, strings: Any = None, more: Optional[Dict[str, Any]] = None, method: int = zipfile.ZIP_DEFLATED) -> Dict[str, Any]:
    """An input that is an .xlsx with one sheet: the parts as text, packed when the input is tried."""
    parts: Dict[str, Any] = {
        "[Content_Types].xml": _HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": '%s<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="热词" sheetId="1" r:id="rId1"/></sheets></workbook>' % (_HEAD, _MAIN, _LINKS),
        "xl/_rels/workbook.xml.rels": ('%s<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>'
                                       '<Relationship Id="rId900" Type="%s/sharedStrings" Target="sharedStrings.xml"/></Relationships>') % (_HEAD, _LINKS, _LINKS),
        "xl/worksheets/sheet1.xml": sheet,
    }
    if strings is not None:
        parts["xl/sharedStrings.xml"] = strings
    parts.update(more or {})
    return {"name": "热词.xlsx", "entries": [[name, part, method] for name, part in parts.items() if part is not None]}


def _sheet(rows: str, doctype: str = "", before: str = "") -> str:
    return '%s%s<worksheet xmlns="%s">%s<sheetData>%s</sheetData></worksheet>' % (_HEAD, doctype, _MAIN, before, rows)


def _strings(texts: Sequence[str], doctype: str = "") -> str:
    return '%s%s<sst xmlns="%s">%s</sst>' % (_HEAD, doctype, _MAIN, "".join("<si><t>%s</t></si>" % html.escape(text) for text in texts))


def _table_text(rng: random.Random) -> str:
    gap = pick(rng, (",", ",", "\t", ";", "，", "|", " ", ",,"))
    rows = []
    most_rows = pick(rng, (30, 300, 6000))
    for _ in range(some(rng, most_rows)):
        cells = [pick(rng, _CELLS + WORDS) if rng.random() < 0.9 else nasty(rng, 400 if most_rows < 6000 else 40)
                 for _ in range(pick(rng, (1, 2, 2, 3, 5, 60) if most_rows < 6000 else (1, 2, 3)))]
        rows.append(gap.join('"%s"' % cell.replace('"', pick(rng, ('""', '"'))) if rng.random() < 0.15 else cell for cell in cells))
    return pick(rng, ("", "", "\ufeff", "sep=,\n")) + pick(rng, ("\n", "\n", "\r\n", "\r")).join(rows)


def make_keywords_file(rng: random.Random) -> Dict[str, Any]:
    kind = rng.random()
    if kind < 0.3:
        payload: Dict[str, Any] = {"name": pick(rng, ("热词.csv", "a.tsv", "a.txt", "a.CSV", "a", "a.xlsx", "a.xls", "a.docx")), "text": _table_text(rng)}
    elif kind < 0.4:
        payload = {"name": pick(rng, ("a.csv", "a.txt", "a.tsv")), "text": nasty(rng)}
    elif kind < 0.55:
        text = _table_text(rng) if rng.random() < 0.7 else nasty(rng)
        payload = {"name": pick(rng, ("a.csv", "a.txt", "a.tsv", "a.xlsx")),
                   "bytes": pick(rng, (encoded(text, pick(rng, ("utf-8-sig", "utf-16", "gb18030", "big5", "utf-16-be", "utf-32"))),
                                       b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + encoded(text, "utf-8"), b"PK\x03\x04" + encoded(text, "utf-8"),
                                       bytes(rng.randrange(256) for _ in range(some(rng, 4000)))))}
    else:
        most_rows, wild = pick(rng, (20, 200, 6000)), rng.random() < 0.2
        rows = "".join('<row r="%s">%s</row>' % (pick(rng, (row, row, row, 1, 10 ** 9, "x", -1)) if wild and rng.random() < 0.1 else row,
                                                 "".join(_sheet_cell(rng, pick(rng, _CELLS + WORDS), row, column, wild)
                                                         for column in range(pick(rng, (1, 2, 2, 3, 70) if most_rows < 6000 else (1, 2, 3)))))
                       for row in range(1, some(rng, most_rows) + 1))
        odd = rng.random()
        sheet: Any = _sheet(rows, doctype=_LAUGHS if odd < 0.05 else "", before="<dimension ref=\"A1:XFD1048576\"/>" if odd > 0.9 else "")
        strings: Any = _strings([_NOT_XML.sub("", pick(rng, _CELLS + WORDS)) for _ in range(2 + some(rng, 300))], doctype=_LAUGHS if 0.05 < odd < 0.1 else "")
        if 0.1 < odd < 0.2:
            sheet = pick(rng, (nasty(rng), _sheet("<row>" * some(rng, 20000)), _sheet("<row><c><v>" + "&lol9;" * 3 + "</v></c></row>", _LAUGHS),
                               "<a>" * some(rng, 60000), _sheet('<row><c t="inlineStr"><is>' + "<t>哈</t>" * some(rng, 30000) + "</is></c></row>"),
                               {"run": "<row><c><v>1</v></c></row>", "times": 2 * MB}, encoded(_sheet(rows), "utf-16")))
        elif 0.2 < odd < 0.3:
            strings = pick(rng, (nasty(rng), _strings(["x"]) * 2, "<sst>" + "<si>" * some(rng, 60000), {"run": "<si><t>哈</t></si>", "times": 3 * MB},
                                 _strings([run_of(rng, ceiling(rng))]), None))
        payload = workbook_of(sheet, strings, method=pick(rng, METHODS))
        if 0.3 < odd < 0.4:
            payload["entries"] = [entry for entry in payload["entries"] if entry[0] != pick(rng, [part[0] for part in payload["entries"]])]
        elif 0.4 < odd < 0.5:
            payload["entries"] += [[pick(rng, ("xl/worksheets/sheet1.xml", "../x", "/etc/passwd", "xl/worksheets/sheet%d.xml" % rng.randrange(99))), nasty(rng, 40),
                                    pick(rng, METHODS)] for _ in range(some(rng, 2000))]
        payload["name"] = pick(rng, ("热词.xlsx",) * 6 + ("a.XLSX", "a.csv", "a.xlsm"))
        payload.update(damage(rng))
    if rng.random() < 0.5:
        payload["keyword_column"] = pick(rng, (0,) * 12 + (1,) * 6 + (2, 49, 50, -1, "0", None, 0.5, True, Raw("1e999"), Raw("9" * 400), [0], 10 ** 30))
    if rng.random() < 0.3:
        payload["heat_column"] = pick(rng, (1,) * 10 + (0, None, None, 2, "1", -1, 10 ** 9, {}, Raw("NaN"), 1.0))
    if rng.random() < 0.05:
        payload["name"] = hostile_value(rng)
    return payload


def ready_keywords_file(payload: Dict[str, Any]) -> Any:
    body = {key: payload[key] for key in ("name", "text", "keyword_column", "heat_column") if key in payload}
    if "entries" in payload:
        body["file_base64"] = base64.b64encode(packed(payload)).decode("ascii")
    elif "bytes" in payload:
        body["file_base64"] = base64.b64encode(as_bytes(payload["bytes"])).decode("ascii")
    return through_the_door(body)


def run_keywords_file(env: "Env", body: Any) -> Any:
    out: Dict[str, Any] = {}
    try:
        out["preview"] = keyword_import.preview(body)
    except keyword_import.ImportRefused as refusal:
        out["preview"] = [refusal.code, refusal.message, refusal.message_zh, refusal.status]
    try:
        out["import"] = keyword_import.collect(body)
    except keyword_import.ImportRefused as refusal:
        out["import"] = [refusal.code, refusal.message, refusal.message_zh, refusal.status]
    return out


def _wide_rows(count: int, cell: str = '<c t="inlineStr"><is><t>面霜</t></is></c>') -> str:
    return "".join("<row>%s</row>" % cell for _ in range(count))


add("keywords_file", "a keyword spreadsheet (.csv, .tsv, .txt, .xlsx)", make_keywords_file, run_keywords_file, ready=ready_keywords_file,
    named=[("n rows of one keyword in a text file", 400000, lambda n: {"name": "a.csv", "text": "面霜,12\n" * (n // 6)}),
           ("n different keywords in a text file", 400000, lambda n: {"name": "a.csv", "text": "".join("词%d,%d\n" % (at, at) for at in range(n // 8))}),
           ("one row of n cells", 400000, lambda n: {"name": "a.csv", "text": "面霜," * (n // 3)}),
           ("one quoted cell never closed, of n lines", 400000, lambda n: {"name": "a.csv", "text": '"' + "面霜\n" * (n // 3)}),
           ("n quotes", 400000, lambda n: {"name": "a.csv", "text": '"' * n}),
           ("a number of n digits beside a keyword", 400000, lambda n: {"name": "a.csv", "text": "面霜," + "9" * n}),
           ("n rows in a workbook", 400000, lambda n: workbook_of(_sheet(_wide_rows(n // 50)))),
           ("n rows that share one text in a workbook", 400000, lambda n: workbook_of(_sheet(_wide_rows(n // 30, '<c t="s"><v>0</v></c>')), _strings(["面霜"]))),
           ("one cell of n pieces of text in a workbook", 400000, lambda n: workbook_of(_sheet('<row><c t="inlineStr"><is>' + "<t>哈</t>" * (n // 9) + "</is></c></row>"))),
           ("n elements inside one another in a workbook", 400000, lambda n: workbook_of(_sheet("<row>" * (n // 5)))),
           ("a cell far to the right and far down in a workbook", 400000, lambda n: workbook_of(_sheet('<row r="%d"><c r="%s%d"><v>1</v></c></row>' % (n, _letters(n), n)))),
           ("n entries in the archive of a workbook", 20000, lambda n: dict(workbook_of(_sheet(_wide_rows(2))), entries=workbook_of(_sheet(_wide_rows(2)))["entries"]
                                                                              + [["x/%d" % at, "", zipfile.ZIP_STORED] for at in range(n)]))],
    slots=[("a text file", 400000, lambda text: {"name": "a.csv", "text": text}),
           ("a tab-separated file", 400000, lambda text: {"name": "a.tsv", "text": "关键词\t热度\n" + text}),
           ("a keyword cell of a text file", 400000, lambda text: {"name": "a.csv", "text": "关键词,热度\n" + text.replace("\n", " ") + ",12\n", "keyword_column": 0}),
           ("a heat cell of a text file", 400000, lambda text: {"name": "a.csv", "text": "关键词,热度\n面霜," + text.replace("\n", " ") + "\n", "keyword_column": 0, "heat_column": 1}),
           ("the bytes of a text file", 400000, lambda text: {"name": "a.txt", "bytes": encoded(text, "utf-16")}),
           ("a cell of a workbook", 400000, lambda text: workbook_of(_sheet('<row><c t="inlineStr"><is><t>%s</t></is></c></row>' % html.escape(text)))),
           ("a shared text of a workbook", 400000, lambda text: workbook_of(_sheet(_wide_rows(3, '<c t="s"><v>0</v></c>')), _strings([text]))),
           ("the sheet part of a workbook", 400000, lambda text: workbook_of(text))])


# ---- target: pasted trend lines ----------------------------------------------------------------------------
_HEATS = ["", "12.5万", "1,234", "3k+", "2亿", "１２万", "1e5", "-3", "0", "9" * 30, "9" * 400, "1.2.3", "12 万", "热度12万", "(12万)", "12w", "NaN", "∞", "１,２３４", "٣٤"]


def make_trend_lines(rng: random.Random) -> Dict[str, Any]:
    kind = rng.random()
    if kind < 0.6:
        gap = pick(rng, ("\t", ",", "，", " ", "  ", " - ", "：", ":", "|", "\u3000"))
        rows = [pick(rng, ("", "", "%d. " % at, "#", "• ", "%d、" % at)) + pick(rng, WORDS + _CELLS) + gap + pick(rng, _HEATS) if rng.random() < 0.9
                else nasty(rng, 300) for at in range(some(rng, pick(rng, (10, 300, 3000))))]
        text = pick(rng, ("\n", "\n", "\r\n", "\r", "\n\n")).join(rows)
    else:
        text = nasty(rng)
    lines = mutated(rng, AI_LINES) if rng.random() < 0.3 else AI_LINES
    post = mutated(rng, SCRIPT_REPLY["rednote_post_package"]) if rng.random() < 0.3 else SCRIPT_REPLY["rednote_post_package"]
    return {"text": text if rng.random() < 0.97 else hostile_value(rng), "lines": lines, "post": post}


def run_trend_lines(env: "Env", payload: Any) -> Any:
    payload = payload if isinstance(payload, dict) else {}
    store = env.fresh()
    items, skipped = trends.parse_keywords(str(payload.get("text") or ""))     # as POST /api/trends hands it over
    added = store.upsert_trends(items, source="manual")
    return {"items": items, "skipped": skipped, "added": added, "page": trends.payload(store),
            "coverage": trends.coverage([item["keyword"] for item in items][:60], payload.get("lines"), payload.get("post")),
            "health": health(store)}


add("trend_lines", "pasted trend lines", make_trend_lines, run_trend_lines, ready=through_the_door, usual=True,
    named=[("n lines of one keyword", 400000, lambda n: {"text": "面霜 12万\n" * (n // 7)}),
           ("n different keywords", 400000, lambda n: {"text": "".join("词%d\t%d\n" % (at, at) for at in range(n // 8))}),
           ("one keyword and a number of n digits", 400000, lambda n: {"text": "面霜\t" + "9" * n}),
           ("one keyword and n commas of a number", 400000, lambda n: {"text": "面霜\t1" + ",234" * (n // 4)}),
           ("the script of n lines that is looked through for 60 keywords", 400000,
            lambda n: {"text": "".join("词%d\n" % at for at in range(60)), "lines": [{"spoken_script_zh": "这是一行台词嘛"}] * (n // 8)})],
    slots=[("the pasted text", 400000, lambda text: {"text": text}),
           ("a keyword of the pasted text", 400000, lambda text: {"text": text.replace("\n", " ") + "\t12万"}),
           ("the number beside a keyword", 400000, lambda text: {"text": "面霜\t" + text.replace("\n", " ")}),
           ("a script line looked through for keywords", 400000, lambda text: {"text": "面霜\n露营", "lines": [{"spoken_script_zh": text}]})])


# ---- target: text that should be JSON ----------------------------------------------------------------------
_TOKENS = ["{", "}", "[", "]", ":", ",", '"', "\\", "\\u", "d800", "\\ud83d", "\\ude00", "null", "true", "false", "NaN", "Infinity", "1", "-", "e", "E", "+",
           ".", "9" * 50, " ", "\n", '"a"', '"a":', "/*", "//", "'", "\ufeff", "\x00", "```", "json", "0x1F", "1_000", "\t", "\\\"", "undefined", "１", "{}", "[]"]
_REPLIES = [SCRIPT_REPLY, IDEAS_REPLY, TRENDS_REPLY, SUGGEST_REPLY, EXAMPLE_DNA, CREATOR]


def json_soup(rng: random.Random) -> str:
    """Text that may or may not be JSON: a changed example, values inside values, loose tokens, or anything."""
    kind = rng.random()
    if kind < 0.35:
        text = dumps(mutated(rng, pick(rng, _REPLIES)))
    elif kind < 0.5:
        text = nest(rng).text
    elif kind < 0.6:
        text = dumps(hostile_value(rng))
    elif kind < 0.85:
        text = "".join(pick(rng, _TOKENS) for _ in range(some(rng, 400)))
    else:
        text = nasty(rng)
    wrap = rng.random()
    if wrap < 0.15:
        return "```json\n" + text + "\n```"
    if wrap < 0.25:
        return words(rng, 60) + text + words(rng, 60)
    if wrap < 0.3:
        return pick(rng, ("```", "```JSON", "\ufeff", " " * some(rng, MOST_CHARS), "{", "}{")) + text + pick(rng, ("", "```", "}", " " * some(rng, MOST_CHARS)))
    return text


def run_json_text(env: "Env", payload: Dict[str, Any]) -> Any:
    text, out = payload.get("text"), {}                 # type: Any, Dict[str, Any]
    try:
        out["strict"] = llm.loads_strict(text)
    except ValueError:
        out["strict"] = "refused"
    try:
        out["reply"] = llm.parse_json_text(text)
    except llm.LLMError as refusal:
        out["reply"] = "refused: %s" % refusal.code
    return out


add("json_text", "text that should be JSON (a request body, a model's reply)", lambda rng: {"text": json_soup(rng)}, run_json_text,
    named=[("n values inside one another", 400000, lambda n: {"text": "[" * n + "]" * n}),
           ("n objects inside one another", 400000, lambda n: {"text": '{"a":' * (n // 5) + "1" + "}" * (n // 5)}),
           ("n opening brackets", 400000, lambda n: {"text": "[" * n}),
           ("a whole number of n digits", 400000, lambda n: {"text": "9" * n}),
           ("a number of n digits after the point", 400000, lambda n: {"text": "0." + "9" * n}),
           ("an exponent of n digits", 400000, lambda n: {"text": "1e-" + "9" * n}),
           ("n spaces around an object", 400000, lambda n: {"text": " " * n + "{}" + " " * n}),
           ("prose with n opening braces before an object", 400000, lambda n: {"text": "x" + "{" * n + '{"a":1}'}),
           ("n small values in a list", 400000, lambda n: {"text": "[" + "1," * (n // 2) + "1]"}),
           ("one text of n escapes", 400000, lambda n: {"text": '"' + "\\ud83d\\ude00" * (n // 12) + '"'}),
           ("n keys that are the same", 400000, lambda n: {"text": "{" + '"a":1,' * (n // 6) + '"a":1}'})],
    slots=[("the text", 400000, lambda text: {"text": text}),
           ("a text value in an object", 400000, lambda text: {"text": dumps({"a": text})}),
           ("prose around an object", 400000, lambda text: {"text": text + '{"a":1}' + text}),
           ("inside a fence", 400000, lambda text: {"text": "```json\n" + text + "\n```"})])


# ---- target: a model's reply, at every step that reads one -------------------------------------------------
_TASKS = {"draft": SCRIPT_REPLY, "voice": EXAMPLE_DNA, "ideas": IDEAS_REPLY, "trends": TRENDS_REPLY, "suggest": SUGGEST_REPLY}


def make_model_replies(rng: random.Random) -> Dict[str, Any]:
    task = pick(rng, sorted(_TASKS))
    kind = rng.random()
    if kind < 0.75:
        reply: Any = mutated(rng, _TASKS[task])
    elif kind < 0.85:
        reply = mutated(rng, _TASKS[pick(rng, sorted(_TASKS))])        # the reply to another step
    elif kind < 0.92:
        reply = copy.deepcopy(_TASKS[task])
    else:
        reply = Raw(json_soup(rng))
    payload: Dict[str, Any] = {"task": task, "reply": reply}
    if rng.random() < 0.3:
        payload.update(pick(rng, ({"keywords": hostile_value(rng)}, {"notes": nasty(rng, 3000)}, {"focus": hostile_value(rng)}, {"lookup": hostile_value(rng)},
                                  {"lookup": {"keywords": ["露营"] * some(rng, 300), "pages": [{"url": "https://example.com/" + nasty(rng, 200), "title": nasty(rng, 200)}]}},
                                  {"rejected": hostile_value(rng)}, {"keep": hostile_value(rng)}, {"kind": pick(rng, suggest.KINDS + ("", "x", None, 1))},
                                  {"line_index": pick(rng, (0, 1, 3, 4, -1, "0", None, 1.5, 10 ** 30, [0]))},
                                  {"rejected": ["lexical_fingerprint.discourse_connectors.high_frequency[0]", "x"] * some(rng, 300)})))
    return payload


def ready_model_replies(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The reply as the app has it once it was read: what is not JSON at all is refused there, and not tried here."""
    ready = through_the_door({key: value for key, value in payload.items() if key != "reply"})
    try:
        ready["result"] = llm.parse_json_text(dumps(payload.get("reply")))
    except llm.LLMError as refusal:
        raise Shut(str(refusal))
    return ready


def run_model_replies(env: "Env", ready: Dict[str, Any]) -> Any:
    store, task, result = env.fresh(), ready.get("task"), ready["result"]
    if task == "draft":
        answer: Any = pipeline.apply_draft(store, result, notes=str(ready.get("notes") or "花市"), target_sec=60, video_format=pipeline.VIDEO_FORMATS[0],
                                           keywords=ready.get("keywords"))
    elif task == "voice":
        pending = pipeline.propose_voice(store, result)
        answer = [pending, pipeline.pending_voice(store)]
        try:
            answer.append(pipeline.approve_voice(store, ready.get("rejected"), created_at=pending.get("created_at"), keep=ready.get("keep")))
        except pipeline.VoiceReviewError as refusal:
            answer.append("refused: %s" % refusal.code)
    elif task == "ideas":
        answer = [pipeline.apply_ideas(store, result, focus=ready.get("focus") or "", lookup=ready.get("lookup")), corpus.performance(store.list_samples())]
    elif task == "trends":
        answer = [pipeline.store_trends(store, result), trends.payload(store)]
    else:
        drafted = [script["id"] for script in store.list_scripts() if script.get("title") == "面霜"][0]
        try:
            answer = suggest.apply_suggest(store, result, drafted, ready.get("kind", suggest.REWRITE), ready.get("line_index", 0))
        except suggest.SuggestError as refusal:
            answer = "refused: %s" % refusal.code
    return {"answer": answer, "health": health(store)}


def _reply(task: str, **changes: Any) -> Dict[str, Any]:
    return {"task": task, "reply": dict(copy.deepcopy(_TASKS[task]), **changes)}


def _voice(path: Sequence[str], value: Any) -> Dict[str, Any]:
    reply = copy.deepcopy(EXAMPLE_DNA)
    node = reply
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return {"task": "voice", "reply": reply}


_LINE = SCRIPT_REPLY["script_lines"][0]
_POST_TEXT = [key for key, value in SCRIPT_REPLY["rednote_post_package"].items() if isinstance(value, str)][0]
_OFTEN = ["lexical_fingerprint", "discourse_connectors", "high_frequency"]
_BANNED = ["anti_voice_blacklist", "banned_phrases_zh"]

add("model_replies", "a model's reply: a draft, a voice profile, ideas, a web lookup, proposals for a line", make_model_replies, run_model_replies,
    ready=ready_model_replies, refused=llm.LLMError, usual=True,
    named=[("a draft of n lines", 300000, lambda n: _reply("draft", script_lines=[_LINE] * (n // 100))),
           ("a draft of n different lines", 300000, lambda n: _reply("draft", script_lines=[dict(_LINE, spoken_script_zh="第%d行台词嘛" % at) for at in range(n // 100)])),
           ("a voice profile with n words said often", 300000, lambda n: _voice(_OFTEN, ["口头禅%d" % at for at in range(n // 8)])),
           ("a voice profile with n banned phrases", 300000, lambda n: _voice(_BANNED, ["禁词%d" % at for at in range(n // 8)])),
           ("a voice profile with n openers", 300000, lambda n: _voice(["lexical_fingerprint", "signature_openers"],
                                                                      [{"pattern_zh": "开场%d嘛" % at, "when_used_en": "x"} for at in range(n // 40)])),
           ("a voice profile with n keys that are not part of one", 300000, lambda n: _voice(["extra"], {"k%d" % at: at for at in range(n // 8)})),
           ("n ideas", 300000, lambda n: _reply("ideas", recommendations=IDEAS_REPLY["recommendations"] * (n // 300))),
           ("n keywords of a web lookup", 300000, lambda n: _reply("trends", keywords=[dict(TRENDS_REPLY["keywords"][0], keyword="词%d" % at) for at in range(n // 100)])),
           ("n proposals for a line", 300000, lambda n: _reply("suggest", suggestions=SUGGEST_REPLY["suggestions"] * (n // 200))),
           ("n proposals for a line, each for another line", 300000,
            lambda n: _reply("suggest", suggestions=[dict(SUGGEST_REPLY["suggestions"][0], line_no=at) for at in range(n // 100)]))],
    slots=[("a line of a draft", 300000, lambda text: _reply("draft", script_lines=[dict(_LINE, spoken_script_zh=text)])),
           ("a text of the post that comes with a draft", 300000,
            lambda text: _reply("draft", rednote_post_package=dict(SCRIPT_REPLY["rednote_post_package"], **{_POST_TEXT: text}))),
           ("a word said often, in a voice profile", 300000, lambda text: _voice(_OFTEN, [text])),
           ("a banned phrase of a voice profile", 300000, lambda text: _voice(_BANNED, [text])),
           ("an opener of a voice profile", 300000, lambda text: _voice(["lexical_fingerprint", "signature_openers"], [{"pattern_zh": text, "when_used_en": "x"}])),
           ("the sentence about the creator in a voice profile", 300000, lambda text: _voice(["persona_summary", "one_line_essence_zh"], text)),
           ("the notes of an idea", 300000, lambda text: _reply("ideas", recommendations=[dict(IDEAS_REPLY["recommendations"][0], script_notes_zh=text, title_zh=text)])),
           ("a keyword of a web lookup", 300000, lambda text: _reply("trends", keywords=[dict(TRENDS_REPLY["keywords"][0], keyword=text)])),
           ("the link of a web lookup", 300000, lambda text: _reply("trends", keywords=[dict(TRENDS_REPLY["keywords"][0], source_url="https://example.com/" + text)])),
           ("a proposal for a line", 300000, lambda text: _reply("suggest", suggestions=[dict(SUGGEST_REPLY["suggestions"][0], spoken_script_zh=text)]))])


# ---- target: a provider's answer ---------------------------------------------------------------------------
def _answers(text: str) -> Dict[str, Any]:
    """What each provider answers when all is well, for each kind of call, with ``text`` as the model's reply."""
    cited = [{"type": "url_citation", "url": "https://example.com/camping", "title": "露营"}]
    return {
        "complete gemini": {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]},
        "complete openai": {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]},
        "complete local": {"choices": [{"message": {"role": "assistant", "content": text}}]},
        "models gemini": {"models": [{"name": "models/gemini-fuzz", "supportedGenerationMethods": ["generateContent"]}, {"name": "models/embed", "supportedGenerationMethods": []}]},
        "models openai": {"data": [{"id": "gpt-fuzz"}, {"id": "text-embedding-fuzz"}]},
        "models local": {"data": [{"id": "llama-fuzz"}]},
        "search gemini": {"steps": [{"type": "model_output", "content": [{"type": "text", "text": text, "annotations": cited}]}]},
        "search openai": {"output": [{"type": "message", "content": [{"type": "output_text", "text": text, "annotations": cited}]}]},
        "script gemini": {"candidates": [{"content": {"parts": [{"text": text}]}}]},
    }


class _ModelServer(BaseHTTPRequestHandler):
    """A stand-in for a model server on this computer: it answers every request with what the input says."""

    def _answer(self) -> None:
        self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16 * MB))
        said = self.server.said                                     # type: ignore[attr-defined]
        body = said.get("body", b"{}")
        self.send_response(int(said.get("status", 200)))
        self.send_header("Content-Type", said.get("type", "application/json"))
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except OSError:                 # the app stopped reading, as it should when far too much is sent
            pass

    do_GET = do_POST = _answer

    def log_message(self, *args: Any) -> None:
        pass


def model_base(env: "Env", said: Dict[str, Any]) -> str:
    """The address of the stand-in model server, started when first asked for. It is on this computer only."""
    if env.listener is None:
        env.listener = server.StudioServer(("127.0.0.1", 0), _ModelServer)
        env.listener.daemon_threads = True
        threading.Thread(target=env.listener.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    env.listener.said = said
    return "http://127.0.0.1:%d/v1" % env.listener.server_address[1]


def make_provider_answers(rng: random.Random) -> Dict[str, Any]:
    reply = dumps(mutated(rng, SCRIPT_REPLY)) if rng.random() < 0.2 else dumps(SCRIPT_REPLY) if rng.random() < 0.7 else json_soup(rng)
    usual = _answers(reply)
    which = pick(rng, sorted(usual))
    kind = rng.random()
    answer: Any = mutated(rng, usual[which]) if kind < 0.75 else usual[which] if kind < 0.85 else hostile_value(rng) if kind < 0.95 else Raw(json_soup(rng))
    payload: Dict[str, Any] = {"call": which.split()[0], "provider": which.split()[1], "answer": answer}
    if payload["provider"] == "local" or (payload["call"] in ("complete", "models") and rng.random() < 0.2):
        payload["provider"] = "local"
        payload["http"] = {"status": pick(rng, (200,) * 8 + (400, 401, 404, 429, 500, 503, 204, 418))}
        how = rng.random()
        if how < 0.1:
            payload["http"]["body"] = encoded(dumps(answer), pick(rng, ("utf-16", "utf-8-sig", "gb18030", "utf-32")))
        elif how < 0.2:
            payload["http"]["body"] = bytes(rng.randrange(256) for _ in range(some(rng, 4000)))
        elif how < 0.3:
            payload["answer"] = {"error": pick(rng, ("no", 5, None, [], {"message": nasty(rng, 2000)}, {"message": {"a": 1}}, {"message": None}, [1, 2], hostile_value(rng)))}
        elif how < 0.34:
            payload["http"]["run"] = pick(rng, (5, 70)) * MB       # more than an answer may be, written piece by piece
    return payload


def ready_provider_answers(payload: Dict[str, Any]) -> Dict[str, Any]:
    ready = dict(payload)
    if "http" in payload:
        http = dict(payload["http"])
        http.setdefault("body", as_bytes(dumps(payload.get("answer"))))
        if http.get("run"):
            http["body"] = b'{"data": [' + b'{"id": "m"},' * (int(http["run"]) // 12) + b'{"id": "m"}]}'
        ready["http"] = http
    else:
        ready["answer"] = through_the_door(payload.get("answer"))  # the app's own transport hands on what its reader of JSON read
    return ready


def run_provider_answers(env: "Env", ready: Dict[str, Any]) -> Any:
    call, provider, base = ready.get("call"), ready.get("provider"), None
    transport: Any = lambda method, url, headers, body: ready["answer"]
    if "http" in ready:
        transport, base = None, model_base(env, ready["http"])     # never the address the app would use when none is given
    if call == "models":
        return llm.list_models(provider, "not-a-key", transport, base)
    if call == "search":
        return llm.search_json(provider, "not-a-key", "fuzz-model", "prompt", transport)
    if call == "script":
        store = env.fresh()
        return [pipeline.generate_script(store, notes="花市", target_sec=60, video_format=pipeline.VIDEO_FORMATS[0], transport=transport), health(store)]
    return llm.complete_json(provider, "not-a-key", "fuzz-model", "system", "user", transport, base)


def _said(which: str, change: Callable[[Dict[str, Any]], Any], text: str = '{"a": 1}') -> Dict[str, Any]:
    answer = _answers(text)[which]
    change(answer)
    return {"call": which.split()[0], "provider": which.split()[1], "answer": answer}


add("provider_answers", "the answer of an AI service: a reply, a list of models, a web lookup", make_provider_answers, run_provider_answers,
    ready=ready_provider_answers, refused=llm.LLMError,
    named=[("n models in a list", 300000, lambda n: _said("models openai", lambda answer: answer.update(data=[{"id": "gpt-%d" % at} for at in range(n // 16)]))),
           ("n models of Gemini", 300000, lambda n: _said("models gemini", lambda answer: answer.update(
               models=[{"name": "models/g-%d" % at, "supportedGenerationMethods": ["generateContent"]} for at in range(n // 70)]))),
           ("n parts of a reply", 300000, lambda n: _said("complete gemini", lambda answer: answer["candidates"][0]["content"].update(parts=[{"text": " "}] * (n // 14)))),
           ("n pages cited", 300000, lambda n: _said("search openai", lambda answer: answer["output"][0]["content"][0].update(
               annotations=[{"type": "url_citation", "url": "https://example.com/%d" % at, "title": "t"} for at in range(n // 70)]))),
           ("n blocks of a looked-up reply", 300000, lambda n: _said("search gemini", lambda answer: answer["steps"][0].update(
               content=[{"type": "text", "text": " "}] * (n // 30)))),
           ("an answer of n bytes over HTTP", 300000, lambda n: {"call": "models", "provider": "local", "answer": {"data": [{"id": "m" * 20}] * (n // 34)}, "http": {"status": 200}}),
           ("an error of n bytes over HTTP", 300000, lambda n: {"call": "complete", "provider": "local", "answer": {"error": {"message": "x" * n}}, "http": {"status": 500}})],
    slots=[("the reply of a model inside the answer", 300000, lambda text: {"call": "complete", "provider": "openai", "answer": _answers(text)["complete openai"]}),
           ("the reply of Gemini inside the answer", 300000, lambda text: {"call": "complete", "provider": "gemini", "answer": _answers(text)["complete gemini"]}),
           ("the name of a model", 300000, lambda text: {"call": "models", "provider": "openai", "answer": {"data": [{"id": text}]}}),
           ("the reply of a web lookup", 300000, lambda text: {"call": "search", "provider": "openai", "answer": _answers(text)["search openai"]}),
           ("the body of an answer over HTTP", 300000, lambda text: {"call": "complete", "provider": "local", "answer": None,
                                                                    "http": {"status": 200, "body": encoded(text, "utf-8")}}),
           ("the body of an error over HTTP", 300000, lambda text: {"call": "complete", "provider": "local", "answer": None,
                                                                   "http": {"status": 500, "body": encoded(text, "utf-8")}})])


# ---- stand-ins the server is given: no model, no Whisper, no Downloads folder, no browser ------------------
_WORKER_LOCK = threading.Lock()
_WORKER_PARTS = ("_run", "_sealed", "_venv_python", "_model_dir", "_worker_env")


def worker_reply(said: Any) -> Dict[str, Any]:
    """What the app makes of what the Whisper worker printed. The app's own reader of that is used
    (``transcribe._run_worker``); only the starting of the worker is replaced, by what the input says it printed."""
    said = said if isinstance(said, dict) else {}
    with _WORKER_LOCK:
        kept = [getattr(transcribe, name) for name in _WORKER_PARTS]
        transcribe._run = lambda *args, **more: (said.get("code", 0), str(said.get("out", "")), str(said.get("err", "")))
        transcribe._sealed = lambda command: command
        transcribe._venv_python = transcribe._model_dir = lambda: "stand-in"
        transcribe._worker_env = lambda: {}
        try:
            return transcribe._run_worker(Path("recording.wav"), 1.0)
        finally:
            for name, old in zip(_WORKER_PARTS, kept):
                setattr(transcribe, name, old)


class _Transcriber:
    """In place of Whisper: it "hears" what the input says the worker printed."""

    def __init__(self, env: "Env") -> None:
        self.env = env

    def status(self) -> Dict[str, Any]:
        return {"supported": True, "installed": True, "model_ready": True, "reason": None, "model": "stand-in"}

    def install(self, progress: Any = None) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def transcribe_file(self, path: Any, progress: Any = None) -> Dict[str, Any]:
        return worker_reply(self.env.said.get("worker"))


class _Outside:
    """In place of the Downloads folder, the file manager and the browser: files go into a folder of this run, and
    what would be opened is written down. The app's own checks of a name and of a link are used as they are."""

    def __init__(self, folder: Path) -> None:
        self.folder, self.opened = folder, []           # type: Path, List[str]

    def downloads_folder(self) -> Path:
        return self.folder

    def save(self, name: Any, data: bytes, folders: Any = None) -> Path:
        return outside.save(name, data, [self.folder])

    def link_problem(self, url: Any) -> str:
        return outside.link_problem(url)

    def open_link(self, url: Any) -> bool:
        self.opened.append(str(url))
        return True

    def reveal(self, path: Any) -> bool:
        self.opened.append(str(path))
        return True


def _model_says(env: "Env") -> Callable[..., Any]:
    """The stand-in for every AI service the server may call: it answers with the reply the input carries."""
    def transport(method: str, url: str, headers: Any, body: Any) -> Any:
        usual = _answers(dumps(env.said.get("reply")))
        if "/interactions" in url or url.endswith("/responses"):
            return through_the_door(usual["search gemini" if "/interactions" in url else "search openai"])
        if "/models" in url and method == "GET":
            return through_the_door(usual["models gemini" if "pageSize" in url else "models openai"])
        return through_the_door(usual["complete gemini" if "generateContent" in url else "complete openai"])
    return transport


def app_server(env: "Env", store: Store) -> int:
    """The app's own server on a free port of this computer, working on ``store`` from now on. Returns the port."""
    downloads = env.root / "downloads"
    shutil.rmtree(str(downloads), ignore_errors=True)
    downloads.mkdir()
    if env.httpd is None:
        env.httpd = server.make_server(0, store, _model_says(env), _Transcriber(env), _Outside(downloads), require_token=True)
        threading.Thread(target=env.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    else:
        env.httpd.app.uploads.close()
        env.httpd.app.remove_token_file()
        env.httpd.app = server.App(store, _model_says(env), _Transcriber(env), _Outside(downloads), require_token=True)
    env.httpd.app.max_upload_bytes = 2 * MB
    return env.httpd.server_address[1]


def send(port: int, method: str, path: str, headers: Sequence[Tuple[str, str]], body: bytes) -> Tuple[int, str, bytes]:
    """One request over a connection of its own: ``(status, media type, body)`` of the answer, status 0 when none came.

    The sending end is closed once everything is sent, so a request that states more than it sends is seen
    as cut short at once. No more than 64 MB of an answer is read.
    """
    head = "%s %s HTTP/1.1\r\n%s\r\n" % (method, path, "".join("%s: %s\r\n" % pair for pair in headers))
    received: List[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=600) as connection:
        try:
            connection.sendall(head.encode("utf-8", "surrogatepass") + body)
            connection.shutdown(socket.SHUT_WR)
        except OSError:                 # refused before all of it was read; the reason may still be there to read
            pass
        try:
            while sum(map(len, received)) < 64 * MB:
                chunk = connection.recv(1 << 16)
                if not chunk:
                    break
                received.append(chunk)
        except OSError:
            pass
    top, _, rest = b"".join(received).partition(b"\r\n\r\n")
    lines = top.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1]) if lines[0].startswith("HTTP/") and lines[0].split(" ")[1:2] and lines[0].split(" ")[1].isdigit() else 0
    kind = next((line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("content-type:")), "")
    return status, kind, rest


# ---- target: requests to the app's own server --------------------------------------------------------------
_POST = SCRIPT_REPLY["rednote_post_package"]
_WAV = (b"RIFF" + struct.pack("<I", 36 + 3200) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data" + struct.pack("<I", 3200) + b"\x00" * 3200)
_HEARD = {"code": 0, "out": json.dumps({"segments": [{"start": 0.0, "end": 1.5, "text": "周末去了趟花市嘛"}, {"start": 1.9, "end": 3.0, "text": "人多得走不动哈"}],
                                        "language": "zh", "duration_sec": 3.2, "model": "stand-in", "unclear_dropped": 0}, ensure_ascii=False)}
# Every route with a request that is right: (method, path, body, what the model then says). ``{script}`` and the
# like stand for something that exists in the invented data folder, and are filled in when the request is sent.
_REQUESTS: List[Tuple[str, str, Any, Any]] = [
    ("GET", "/api/state", None, None), ("GET", "/api/setup", None, None), ("GET", "/api/creator", None, None), ("PUT", "/api/creator", CREATOR, None),
    ("GET", "/api/dna", None, None), ("GET", "/api/scripts", None, None), ("GET", "/api/samples", None, None), ("GET", "/api/hooks", None, None),
    ("GET", "/api/trends", None, None), ("GET", "/api/lessons", None, None), ("GET", "/api/wordlist", None, None), ("GET", "/api/recommendations", None, None),
    ("GET", "/api/voice/pending", None, None), ("GET", "/api/voice/history", None, None), ("GET", "/api/transcriber", None, None),
    ("GET", "/", None, None), ("GET", "/app.js", None, None), ("GET", "/../studio/server.py", None, None), ("GET", "/api/nothing", None, None),
    ("POST", "/api/scripts", {"title": "新脚本", "notes": "花市", "target_sec": 60, "video_format": "VLOG_VOICEOVER", "lines": AI_LINES, "keywords": ["面霜"], "post": _POST}, None),
    ("POST", "/api/lint", {"lines": AI_LINES, "target_sec": 60, "keywords": ["面霜"], "post": _POST}, None),
    ("POST", "/api/generate", {"notes": "入冬换面霜", "target_sec": 60, "video_format": "VLOG_VOICEOVER", "keywords": ["面霜"], "lang": "zh"}, SCRIPT_REPLY),
    ("POST", "/api/manual/prompt", {"task": "draft", "notes": "花市", "target_sec": 60, "keywords": ["面霜"]}, None),
    ("POST", "/api/manual/prompt", {"task": "suggest", "script_id": "{script}", "kind": "rewrite", "line_index": 0, "instruction": "短一点"}, None),
    ("POST", "/api/manual/prompt", {"task": "voice"}, None), ("POST", "/api/manual/prompt", {"task": "trends", "focus": "秋冬"}, None),
    ("POST", "/api/manual/prompt", {"task": "ideas", "focus": "护肤"}, None),
    ("POST", "/api/manual/reply", {"task": "draft", "reply": json.dumps(SCRIPT_REPLY, ensure_ascii=False), "notes": "花市", "target_sec": 60}, None),
    ("POST", "/api/manual/reply", {"task": "voice", "reply": json.dumps(EXAMPLE_DNA, ensure_ascii=False)}, None),
    ("POST", "/api/manual/reply", {"task": "ideas", "reply": json.dumps(IDEAS_REPLY, ensure_ascii=False), "focus": "护肤",
                                   "lookup": {"keywords": ["露营"], "pages": [{"url": "https://example.com/camping", "title": "露营"}]}}, None),
    ("POST", "/api/manual/reply", {"task": "trends", "reply": json.dumps(TRENDS_REPLY, ensure_ascii=False)}, None),
    ("POST", "/api/manual/reply", {"task": "suggest", "reply": json.dumps(SUGGEST_REPLY, ensure_ascii=False), "script_id": "{script}", "kind": "rewrite", "line_index": 0}, None),
    ("POST", "/api/settings/models", {"provider": "gemini", "key": "not-a-key"}, None),
    ("POST", "/api/settings", {"provider": "gemini", "model": "fuzz-model", "key": "not-a-key"}, None),
    ("POST", "/api/settings", {"provider": "local", "model": "llama", "base_url": "http://127.0.0.1:1/v1"}, None), ("POST", "/api/settings", {"provider": "manual"}, None),
    ("PUT", "/api/wordlist", {"off": [], "phrases": ["我真的会谢", "家人们谁懂啊"]}, None),
    ("POST", "/api/samples", {"title": "花市", "text": OWN[0], "is_sponsored": False, "metrics": {"views": 1200, "likes": 80, "collects": 9, "comments": 3}}, None),
    ("POST", "/api/samples", {"title": "字幕", "subtitle_text": SRT, "source_name": "花市.srt"}, None),
    ("POST", "/api/samples", {"title": "文档", "docx_base64": base64.b64encode(archive(docx_of(_docx_body("<w:p><w:r><w:t>周末去了趟花市嘛</w:t></w:r></w:p>"))["entries"])).decode("ascii"),
                              "source_name": "花市.docx"}, None),
    ("PUT", "/api/samples/{sample}", {"title": "改过", "text": OWN[1], "metrics": {"views": "1.2万", "likes": 5}, "is_sponsored": True, "needs_review": False}, None),
    ("DELETE", "/api/samples/{sample}", None, None),
    ("POST", "/api/voice/learn", {}, EXAMPLE_DNA), ("DELETE", "/api/voice/pending", None, None),
    ("POST", "/api/voice/approve", {"created_at": "{pending}", "rejected": [], "keep": []}, None), ("PUT", "/api/voice/rate", {"cpm": 240}, None),
    ("POST", "/api/voice/history/{history}/restore", {}, None),
    ("POST", "/api/recommendations", {"focus": "护肤", "lang": "zh"}, IDEAS_REPLY), ("POST", "/api/discover", {"focus": "秋冬", "lang": "zh"}, TRENDS_REPLY),
    ("POST", "/api/lessons", {"script_id": "{script}", "ai": AI_LINES[1]["spoken_script_zh"], "final": "所以想推荐给你们哈", "note": "口语一点"}, None),
    ("DELETE", "/api/lessons/{lesson}", None, None),
    ("POST", "/api/trends", {"text": "秋冬护肤 12.5万\n露营\t3k"}, None),
    ("POST", "/api/trends/import/preview", {"name": "热词.csv", "text": "关键词,热度\n面霜,12\n露营,3k"}, None),
    ("POST", "/api/trends/import", {"name": "热词.csv", "text": "关键词,热度\n面霜,12\n露营,3k", "keyword_column": 0, "heat_column": 1}, None),
    ("PUT", "/api/trends/weight", {"weight": "balanced"}, None), ("PUT", "/api/trends/{trend}", {"heat": 5000}, None), ("DELETE", "/api/trends/{trend}", None, None),
    ("POST", "/api/scripts/{script}/suggest", {"kind": "rewrite", "line_index": 0, "instruction": "短一点", "lang": "zh"}, SUGGEST_REPLY),
    ("GET", "/api/scripts/{script}/changes", None, None), ("GET", "/api/scripts/{script}", None, None), ("GET", "/api/scripts/{own}", None, None),
    ("POST", "/api/scripts/{own}/posted", {"posted_on": time.strftime("%Y-%m-%d"), "note_url": "https://www.xiaohongshu.com/explore/abc", "views": 900, "likes": 70,
                                           "collects": 8, "comments": 2, "is_sponsored": False}, None),
    ("PUT", "/api/scripts/{script}/posted", {"views": "2.4万", "likes": 300, "metrics_on": time.strftime("%Y-%m-%d"), "refresh_text": True}, None),
    ("GET", "/api/scripts/{script}/export/{kind}", None, None), ("POST", "/api/scripts/{script}/export/{kind}/save", {"lang": "zh"}, None),
    ("PUT", "/api/scripts/{script}", {"title": "面霜二稿", "lines": EDITED_LINES, "notes": "入冬", "keywords": ["面霜"], "post": _POST, "base_updated_at": "{updated}"}, None),
    ("DELETE", "/api/scripts/{own}", None, None),
    ("POST", "/api/outside/open", {"url": "https://example.com/camping"}, None), ("POST", "/api/outside/show", {"path": "/tmp/x"}, None),
    ("PUT", "/api/uploads?name=recording.wav", {"$upload": True}, None),
    ("POST", "/api/videos", {"upload_id": "{upload}", "title": "花市录音", "is_sponsored": False}, None),
    ("POST", "/api/dictation", {"upload_id": "{upload}"}, None), ("GET", "/api/jobs/{job}", None, None),
    ("GET", "/api/scripts/{script}/refs", None, None),
    ("GET", "/api/scripts/{script}/refs/search?q=%E8%8A%B1%E5%B8%82", None, None),
    ("PUT", "/api/scripts/{script}/pins", {"sample_id": "{sample}", "pinned": True}, None),
]
_AFTER_UPLOAD = [request for request in _REQUESTS if request[1] in ("/api/videos", "/api/dictation")]
_HEADERS = [{"Content-Length": "abc"}, {"Content-Length": "-1"}, {"Content-Length": "0"}, {"Content-Length": "99999999999"}, {"Content-Length": "1e3"},
            {"Content-Length": "+5"}, {"Content-Length": "５"}, {"Content-Type": "text/plain"}, {"Content-Type": "text/plain;application/json"},
            {"Content-Type": "application/json; charset=utf-16"}, {"Content-Type": "APPLICATION/JSON"}, {"Host": "evil.example"}, {"Host": "localhost"},
            {"Origin": "http://evil.example"}, {"Origin": "null"}, {"Transfer-Encoding": "chunked"}, {"Content-Type": "application/octet-stream"},
            {"Expect": "100-continue"}, {"X-Long": "x" * 60000}, {"Content-Length": "5, 5"}]


def make_request_bodies(rng: random.Random) -> Dict[str, Any]:
    requests: List[Dict[str, Any]] = []
    payload: Dict[str, Any] = {"requests": requests}
    for _ in range(pick(rng, (1, 1, 1, 2, 2, 3, 4))):
        method, path, body, says = pick(rng, _REQUESTS)
        request: Dict[str, Any] = {"method": method, "path": path}
        if isinstance(body, dict) and "$upload" in body:
            request.update(raw=pick(rng, (_WAV, _WAV[:rng.randrange(len(_WAV))], bytes(rng.randrange(256) for _ in range(some(rng, 4000))), b"", _WAV * 40)),
                           headers={"Content-Type": "application/octet-stream"})
            request["path"] = "/api/uploads?name=" + pick(rng, ("recording.wav", "a.mp4", "a.MOV", "a.txt", "a", "%E8%8A%B1.m4a", "..%2F..%2Fa.wav", "a.wav%00.txt",
                                                                 "a.wav&name=b.exe", "x" * 3000 + ".wav", ".wav"))
            requests.append(request)
            payload["worker"] = pick(rng, (_HEARD, _HEARD, mutated(rng, _HEARD), {"code": pick(rng, (0, 1, None)), "out": json_soup(rng)},
                                           {"code": 0, "out": dumps({"segments": mutated(rng, json.loads(_HEARD["out"])["segments"])})}))
            method, path, body, says = pick(rng, _AFTER_UPLOAD)
            request = {"method": method, "path": path}
        if body is not None:
            request["body"] = mutated(rng, body) if rng.random() < 0.65 else copy.deepcopy(body)
        if says is not None:
            payload["reply"] = mutated(rng, says) if rng.random() < 0.5 else says
        odd = rng.random()
        if odd < 0.03:
            request["method"] = pick(rng, ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "get", "X" * 100))
        elif odd < 0.07:
            request["path"] = path + pick(rng, ("?lang=en", "?lang=" + "x" * some(rng, 3000), "/", "//", "/../", "%00", "?" + "a=1&" * some(rng, 3000), "#x", "?name=%ff"))
        elif odd < 0.11:
            request["headers"] = pick(rng, _HEADERS)
        elif odd < 0.16:
            text = json_soup(rng)
            request["raw"] = pick(rng, (encoded(text, "utf-8"), encoded(text, "utf-16"), encoded(text, "utf-8-sig"), bytes(rng.randrange(256) for _ in range(some(rng, 4000))),
                                        encoded(dumps(request.get("body")), "utf-8")[:rng.randrange(1, 200)]))
        elif odd < 0.2:
            request["path"] = re.sub(r"\{\w+\}", lambda found: pick(rng, ("0" * 12, "{sample}", "{script}", "{own}", "zzzzzzzzzzzz", "{trend}", "F" * 12, "", "x")), path, count=1)
        requests.append(request)
    return payload


def _filled(value: Any, known: Dict[str, Any]) -> Any:
    """``value`` with ``{script}`` and the like replaced by what they stand for in the data folder of this input."""
    if isinstance(value, str):
        if value in known:
            return known[value]
        for name, real in known.items():
            if name in value:
                value = value.replace(name, str(real))
        return value
    if isinstance(value, dict):
        return {key: _filled(inner, known) for key, inner in value.items()}
    if isinstance(value, list):
        return [_filled(inner, known) for inner in value]
    return value


def _known(store: Store) -> Dict[str, Any]:
    scripts = {script.get("title"): script for script in store.list_scripts()}
    samples, lessons, history, pending = store.list_samples(), store.list_lessons(), store.list_dna_history(), store.load_pending()
    return {"{script}": scripts["面霜"]["id"], "{own}": scripts["手写"]["id"], "{updated}": scripts["面霜"].get("updated_at"),
            "{sample}": samples[0]["id"] if samples else "0" * 12, "{trend}": (store.list_trends() or [{"id": "0" * 12}])[0]["id"],
            "{lesson}": lessons[0]["id"] if lessons else "0" * 12, "{history}": history[0]["id"] if history else "0",
            "{pending}": pending.get("created_at") if pending else 0, "{kind}": sorted(exports.KINDS)[0], "{upload}": "0" * 12, "{job}": "0" * 12}
def run_request_bodies(env: "Env", payload: Dict[str, Any]) -> Any:
    store = env.fresh()
    port = app_server(env, store)
    token = env.httpd.app.token
    known = _known(store)
    env.said = {"reply": payload.get("reply", SCRIPT_REPLY), "worker": payload.get("worker", _HEARD)}
    env.raised.clear()
    statuses: List[int] = []
    for req in payload.get("requests") or []:
        method = str(req.get("method") or "GET")
        path = str(_filled(req.get("path") or "/", known))
        # Strip CR/LF/NUL from method and path so HTTP framing stays valid single-request framing.
        method = re.sub(r"[\r\n\x00 ]+", "", method) or "GET"
        path = re.sub(r"[\r\n\x00 ]+", "", path) or "/"
        if not path.startswith("/"):
            path = "/" + path
        extra_headers = req.get("headers") if isinstance(req.get("headers"), dict) else {}
        if "raw" in req:
            raw_body = as_bytes(req["raw"])
        elif "body" in req and req["body"] is not None:
            raw_body = encoded(dumps(_filled(req["body"], known)), "utf-8")
        else:
            raw_body = b""
        hdr_map: Dict[str, str] = {"Host": "127.0.0.1:%d" % port, "Connection": "close"}
        if token:
            hdr_map[server.TOKEN_HEADER] = token
        if raw_body or "body" in req:
            hdr_map["Content-Type"] = "application/json"
            hdr_map["Content-Length"] = str(len(raw_body))
        for hk, hv in extra_headers.items():
            clean_k = re.sub(r"[\r\n\x00:]+", "", str(hk)) or "X-Fuzz"
            clean_v = re.sub(r"[\r\n\x00]+", "", str(hv))
            hdr_map[clean_k] = clean_v
        status, media, body_bytes = send(port, method, path, list(hdr_map.items()), raw_body)
        statuses.append(status)
        if env.raised:
            exc = env.raised.pop(0)
            raise Fault("raised", "%s in %s" % (type(exc).__name__, where(exc)), short(exc))
        if "json" in media and body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
            except Exception as exc:    # noqa: BLE001
                raise Fault("answer is not JSON", "server.py:%s" % path.split("?")[0], short(exc))
            if status >= 500 and isinstance(parsed, dict) and parsed.get("error") == "internal":
                raise Fault("internal error", "server.py:%s %s" % (method, path.split("?")[0]), short(parsed))
            if isinstance(parsed, dict):
                if isinstance(parsed.get("upload_id"), str):
                    known["{upload}"] = parsed["upload_id"]
                if isinstance(parsed.get("job_id"), str):
                    known["{job}"] = parsed["job_id"]
    # Wait briefly for any background job started during this sequence before checking health.
    deadline = time.time() + 1.5
    while any(j["status"] == "running" for j in env.httpd.app.jobs._jobs.values()) and time.time() < deadline:
        time.sleep(0.02)
    if env.raised:
        exc = env.raised.pop(0)
        raise Fault("raised", "%s in %s" % (type(exc).__name__, where(exc)), short(exc))
    return {"statuses": statuses, "health": health(store)}


add("request_bodies", "requests to the app's own HTTP server", make_request_bodies, run_request_bodies, usual=True,
    named=[("a script of n lines sent to /api/lint", 200000,
            lambda n: {"requests": [{"method": "POST", "path": "/api/lint",
                                     "body": {"lines": [{"spoken_script_zh": "今天去花市嘛"}] * max(1, n // 40), "target_sec": 60}}]}),
           ("n trend lines sent to /api/trends", 200000,
            lambda n: {"requests": [{"method": "POST", "path": "/api/trends", "body": {"text": "面霜 12万\n" * max(1, n // 10)}}]})],
    slots=[("the notes of /api/manual/prompt", 200000,
            lambda text: {"requests": [{"method": "POST", "path": "/api/manual/prompt", "body": {"task": "draft", "notes": text}}]}),
           ("a pasted reply to /api/manual/reply", 200000,
            lambda text: {"requests": [{"method": "POST", "path": "/api/manual/reply", "body": {"task": "trends", "reply": text}}]})])


# ---- target: MCP JSON-RPC messages -------------------------------------------------------------------------
_MCP_CALLS: List[Tuple[str, Dict[str, Any]]] = [
    ("get_creator_profile", {}),
    ("get_voice_profile", {}),
    ("list_library", {"limit": 5}),
    ("list_trends", {}),
    ("list_scripts", {}),
    ("get_draft_brief", {"notes": "周末花市", "target_sec": 60, "video_format": "VLOG_VOICEOVER", "keywords": ["面霜"]}),
    ("lint_script", {"lines": AI_LINES, "target_sec": 60, "keywords": ["面霜"]}),
    ("save_draft", {"title": "测试稿", "lines": AI_LINES, "target_sec": 60}),
]


def make_mcp(rng: random.Random) -> Dict[str, Any]:
    lines: List[str] = []
    for _ in range(pick(rng, (1, 1, 2, 3))):
        kind = rng.random()
        if kind < 0.55:
            tname, targs = pick(rng, _MCP_CALLS)
            msg: Any = {"jsonrpc": "2.0", "id": pick(rng, (1, "req-1", 0, None)),
                        "method": "tools/call",
                        "params": {"name": tname, "arguments": mutated(rng, targs) if rng.random() < 0.75 else copy.deepcopy(targs)}}
            if rng.random() < 0.25:
                msg = mutated(rng, msg)
            lines.append(dumps(msg))
        elif kind < 0.75:
            method = pick(rng, ("initialize", "notifications/initialized", "tools/list", "ping", "unknown/method", ""))
            msg = {"jsonrpc": "2.0", "id": pick(rng, (1, 2, "x", None)), "method": method, "params": hostile_value(rng) if rng.random() < 0.5 else {}}
            lines.append(dumps(msg))
        elif kind < 0.88:
            batch = [{"jsonrpc": "2.0", "id": at, "method": "tools/list"} for at in range(some(rng, 40))]
            lines.append(dumps(mutated(rng, batch) if rng.random() < 0.5 else batch))
        else:
            lines.append(json_soup(rng).replace("\n", " "))
    return {"stdin": "\n".join(lines) + "\n"}


def run_mcp(env: "Env", payload: Dict[str, Any]) -> Any:
    store = env.fresh()
    inp = io.BytesIO(encoded(str(payload.get("stdin") or ""), "utf-8"))
    out = io.BytesIO()
    mcp_server.serve(store, inp, out)
    responses = []
    for raw_line in out.getvalue().decode("utf-8", "replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            parsed = llm.loads_strict(raw_line)
        except ValueError as exc:
            raise Fault("answer is not JSON", "mcp_server.py:serve", short(exc))
        text = json.dumps(parsed, ensure_ascii=False)
        if "Internal error in tool" in text or "internal error in tool" in text.lower():
            raise Fault("internal error", "mcp_server.py:call_tool", short(text))
        responses.append(parsed)
    return {"responses": len(responses), "health": health(store)}


add("mcp", "MCP JSON-RPC messages on stdin", make_mcp, run_mcp, usual=True,
    named=[("a batch of n tools/list messages", 20000,
            lambda n: {"stdin": dumps([{"jsonrpc": "2.0", "id": at, "method": "tools/list"} for at in range(max(1, n // 45))]) + "\n"})],
    slots=[("a line sent to MCP", 200000, lambda text: {"stdin": text.replace("\n", " ") + "\n"}),
           ("notes in get_draft_brief", 200000,
            lambda text: {"stdin": dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "get_draft_brief", "arguments": {"notes": text}}}) + "\n"})])


# ---- target: stored profiles, config & keystore tokens -----------------------------------------------------
from studio import evidence, keystore, refs as refs_mod  # noqa: E402
from studio import store as store_mod                    # noqa: E402


def make_stored_profiles(rng: random.Random) -> Dict[str, Any]:
    return {
        "creator": mutated(rng, CREATOR) if rng.random() < 0.8 else hostile_value(rng),
        "dna": mutated(rng, EXAMPLE_DNA) if rng.random() < 0.8 else hostile_value(rng),
        "token": pick(rng, ("v1:" + nasty(rng, 200), "dpapi:" + nasty(rng, 200), nasty(rng, 300), "", None, 123, ["x"])),
        "kept_name": pick(rng, ("lang", "theme", "accent", "unknown", "", None)),
        "kept_val": hostile_value(rng),
        "stored_json": json_soup(rng),
    }


def ready_stored_profiles(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "creator": through_the_door(payload.get("creator")),
        "dna": through_the_door(payload.get("dna")),
        "token": through_the_door(payload.get("token")),
        "kept_name": through_the_door(payload.get("kept_name")),
        "kept_val": through_the_door(payload.get("kept_val")),
        "stored_json": str(payload.get("stored_json") or ""),
    }


def run_stored_profiles(env: "Env", ready: Dict[str, Any]) -> Any:
    out: Dict[str, Any] = {}
    try:
        norm = creator.normalize(ready["creator"])
        out["creator_norm"] = creator.for_page(norm)
    except ValueError as refusal:
        out["creator_norm"] = "refused: %s" % refusal
    out["creator_page"] = creator.for_page(ready["creator"])
    out["creator_prompt"] = creator.prompt_block(ready["creator"])
    shaped = evidence.shape_profile(ready["dna"])
    samples = [{"title": "样本", "text": OWN[0], "is_sponsored": False}]
    rep = evidence.verify_profile(ready["dna"], samples)
    applied = evidence.apply_decisions(shaped, rep, [], keep_paths=[])
    rules = guardrails.compile_rules(ready["dna"])
    out["rules_banned"] = len(rules.bans)
    out["applied_keys"] = sorted(applied.keys())
    out["unseal"] = keystore.unseal(ready["token"])
    out["kept"] = store_mod.kept_value(str(ready["kept_name"] or ""), ready["kept_val"])
    try:
        out["loads_stored"] = type(store_mod.loads_stored(ready["stored_json"])).__name__
    except ValueError:
        out["loads_stored"] = "refused"
    return out


add("stored_profiles", "creator profile, voice DNA, sealed key tokens and stored JSON", make_stored_profiles, run_stored_profiles,
    ready=ready_stored_profiles,
    slots=[("a sealed keystore token", 100000, lambda text: {"creator": CREATOR, "dna": EXAMPLE_DNA, "token": text, "stored_json": "{}"}),
           ("stored JSON text", 100000, lambda text: {"creator": CREATOR, "dna": EXAMPLE_DNA, "token": "", "stored_json": text})])


# ---- target: links, model names, file names ----------------------------------------------------------------
def make_links(rng: random.Random) -> Dict[str, Any]:
    url = pick(rng, (
        "https://www.xiaohongshu.com/explore/" + nasty(rng, 200),
        "http://127.0.0.1:11434/v1" + nasty(rng, 100),
        "http://[" + nasty(rng, 80),
        "javascript:" + nasty(rng, 80),
        nasty(rng, 400),
    ))
    return {"url": url, "name": nasty(rng, 300), "model": nasty(rng, 200)}


def run_links(env: "Env", payload: Dict[str, Any]) -> Any:
    url = payload.get("url")
    name = payload.get("name")
    model = payload.get("model")
    out: Dict[str, Any] = {
        "link_problem": outside.link_problem(url),
        "safe_name": outside.safe_name(str(name or "")),
        "clean_file": clean_file_name(str(name or "")),
    }
    try:
        out["posted_link"] = posted.clean_link(url)
    except posted.PostedError as refusal:
        out["posted_link"] = "refused: %s" % refusal.code
    try:
        out["local_base"] = llm.normalize_local_base(str(url or ""))
    except llm.LLMError as refusal:
        out["local_base"] = "refused: %s" % refusal.code
    try:
        out["model"] = llm.validate_model_name(str(model or ""))
    except llm.LLMError as refusal:
        out["model"] = "refused: %s" % refusal.code
    return out


add("links", "URLs, local server addresses, model names and file names", make_links, run_links, ready=through_the_door,
    slots=[("a URL", 100000, lambda text: {"url": text, "name": "a.txt", "model": "gpt-4o"}),
           ("a file name", 100000, lambda text: {"url": "https://example.com", "name": text, "model": "gpt-4o"}),
           ("a model name", 100000, lambda text: {"url": "https://example.com", "name": "a.txt", "model": text})])


# ---- target: script lines, lint, diffs and references ------------------------------------------------------
def make_script_lines(rng: random.Random) -> Dict[str, Any]:
    count = some(rng, 40)
    lines = [{"spoken_script_zh": nasty(rng, 600), "audio_mode": pick(rng, ("VOICEOVER", "LIVE_CLIP", "", "other"))}
             for _ in range(count)]
    edited = [dict(line, spoken_script_zh=nasty(rng, 600) if rng.random() < 0.5 else line["spoken_script_zh"]) for line in lines]
    return {"lines": lines, "edited": edited, "query": nasty(rng, 200), "start": pick(rng, (0, -5, 100, 10 ** 9, "0", None))}


def run_script_lines(env: "Env", payload: Dict[str, Any]) -> Any:
    store = env.fresh()
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    edited = payload.get("edited") if isinstance(payload.get("edited"), list) else []
    linted = checks.lint_payload(store, lines, 60, ["面霜"], _POST)
    changes = learning.line_changes(lines[:30], edited[:30])
    hook = hooks.describe(str((lines[0] if lines else {}).get("spoken_script_zh") or ""))
    scripts = store.list_scripts()
    sid = scripts[0]["id"]
    samples = store.list_samples()
    ref_out: Dict[str, Any] = {"for_script": len(refs_mod.for_script(store, sid)["pinned"])}
    try:
        ref_out["search"] = len(refs_mod.search(store, sid, payload.get("query"))["items"])
    except refs_mod.RefsError as refusal:
        ref_out["search"] = "refused: %s" % refusal.code
    try:
        ref_out["part"] = len(refs_mod.text_part(store, samples[0]["id"], payload.get("start"))["text"])
    except refs_mod.RefsError as refusal:
        ref_out["part"] = "refused: %s" % refusal.code
    return {"findings": len(linted["findings"]), "changes": len(changes), "patterns": hook["patterns"], "refs": ref_out}


add("script_lines", "script lines through lint, voice match, edit-diff learner, hook classifier and references",
    make_script_lines, run_script_lines, ready=through_the_door,
    slots=[("a spoken script line", 100000, lambda text: {"lines": [{"spoken_script_zh": text}], "edited": [{"spoken_script_zh": "好"}], "query": "花市", "start": 0}),
           ("a reference search query", 100000, lambda text: {"lines": AI_LINES, "edited": EDITED_LINES, "query": text, "start": 0})])


# ---- target: word list -------------------------------------------------------------------------------------
def make_wordlist(rng: random.Random) -> Dict[str, Any]:
    if rng.random() < 0.7:
        return {"off": [nasty(rng, 60) for _ in range(some(rng, 30))],
                "phrases": [nasty(rng, 80) for _ in range(some(rng, 60))]}
    return {"raw": hostile_value(rng)}


def run_wordlist(env: "Env", payload: Dict[str, Any]) -> Any:
    store = env.fresh()
    body = payload.get("raw") if "raw" in payload else payload
    try:
        saved = wordlist.save(store, body)
    except wordlist.Refused as refusal:
        saved = {"refused": refusal.code}
    cfg_check = wordlist.from_config({"wordlist": body if isinstance(body, dict) else {}})
    return {"saved": bool(saved), "own_count": len(cfg_check.get("own_phrases") or []), "page": len(wordlist.payload(store)["entries"])}


add("wordlist", "the compliance and style word list", make_wordlist, run_wordlist, ready=through_the_door,
    slots=[("a custom phrase in the word list", 50000, lambda text: {"off": [], "phrases": [text]})])


# ---- target: recordings & Whisper worker output ------------------------------------------------------------
def make_recordings(rng: random.Random) -> Dict[str, Any]:
    if rng.random() < 0.5:
        wav = _WAV[:rng.randrange(len(_WAV) + 1)] if rng.random() < 0.5 else bytes(rng.randrange(256) for _ in range(some(rng, 2000)))
    else:
        wav = b"RIFF" + struct.pack("<I", rng.randrange(1 << 31)) + b"WAVEfmt " + bytes(rng.randrange(256) for _ in range(40))
    worker = pick(rng, (
        mutated(rng, _HEARD),
        {"code": pick(rng, (0, 1, 2, -9)), "out": json_soup(rng), "err": nasty(rng, 400)},
        {"code": 0, "out": dumps({"segments": [{"start": pick(rng, (0.0, -1.0, 1e9, Raw("NaN"), Raw("Infinity"))),
                                                "end": pick(rng, (1.0, 0.5, Raw("Infinity"))),
                                                "text": nasty(rng, 300)}]})},
    ))
    return {"wav": wav, "worker": worker}


def run_recordings(env: "Env", payload: Dict[str, Any]) -> Any:
    wav_path = env.root / "probe.wav"
    wav_path.write_bytes(as_bytes(payload.get("wav") or b""))
    secs = transcribe._wav_seconds(wav_path)
    out: Dict[str, Any] = {"wav_secs": secs if secs is None or math.isfinite(secs) else "non-finite"}
    try:
        heard = worker_reply(payload.get("worker"))
        out["heard"] = len(heard.get("segments") or [])
        out["breaths"] = len(whisper_worker.breath_groups(heard.get("segments") or []))
    except transcribe.TranscribeError as refusal:
        out["heard"] = "refused: %s" % refusal.code
    return out


add("recordings", "WAV headers and Whisper worker output", make_recordings, run_recordings,
    slots=[("Whisper worker stdout", 100000, lambda text: {"wav": _WAV, "worker": {"code": 0, "out": text, "err": ""}})])


# ---- child worker & parent driver --------------------------------------------------------------------------
def _worker_loop() -> int:
    env = Env()
    try:
        sys.stdout.write('{"ready":true}\n')
        sys.stdout.flush()
        for raw_line in sys.stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            msg = json.loads(raw_line)
            if msg.get("cmd") == "quit":
                break
            target = TARGETS[msg["target"]]
            payload = unpack(msg["payload"])
            result = examine(target, env, payload)
            sys.stdout.write(dumps(result) + "\n")
            sys.stdout.flush()
    finally:
        env.close()
    return 0


class _Child:
    """Subprocess running ``_worker_loop`` with bounded per-case timeout across macOS, Linux and Windows."""

    def __init__(self, scratch_env: Path) -> None:
        child_env = dict(os.environ)
        data_dir = scratch_env / "child-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        child_env.setdefault("STUDIO_DATA_DIR", str(data_dir))
        child_env.setdefault("STUDIO_KEYSTORE_FILE", str(scratch_env / "keystore.key"))
        child_env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=child_env,
        )
        self._answers: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        try:
            ready = self._answers.get(timeout=60.0)
            if not ready or not json.loads(ready).get("ready"):
                self.close()
        except Exception:       # noqa: BLE001
            self.close()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._answers.put(line)
        except Exception:       # noqa: BLE001
            pass
        finally:
            self._answers.put(None)

    def ask(self, target_name: str, payload: Any, limit: float) -> Optional[Dict[str, Any]]:
        if self.proc.poll() is not None or self.proc.stdin is None:
            return None
        try:
            line = dumps({"target": target_name, "payload": pack(payload)})
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except Exception:       # noqa: BLE001
            return None
        try:
            got = self._answers.get(timeout=limit)
        except queue.Empty:
            return None
        if not got:
            return None
        try:
            return json.loads(got)
        except ValueError:
            return None

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write('{"cmd":"quit"}\n')
                    self.proc.stdin.flush()
                    self.proc.stdin.close()
                self.proc.wait(timeout=2.0)
            except Exception:   # noqa: BLE001
                self.proc.kill()
                try:
                    self.proc.wait(timeout=2.0)
                except Exception:   # noqa: BLE001
                    pass
        for pipe in (self.proc.stdout, self.proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:   # noqa: BLE001
                    pass


def run_suite(target_names: Sequence[str], *, cases: int = 50, seconds: float = 0.0, seed_val: str = "1",
              limit: float = LIMIT_SEC, out_dir: Optional[Path] = None, quiet: bool = False) -> Dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix="lmv-fuzz-driver-"))
    child: Optional[_Child] = None
    findings: List[Dict[str, Any]] = []
    seen_faults = set()
    stats: Dict[str, Dict[str, Any]] = {name: {"inputs": 0, "tried": 0, "faults": 0} for name in target_names}

    def ensure_child() -> _Child:
        nonlocal child
        if child is None or child.proc.poll() is not None:
            if child is not None:
                child.close()
            child = _Child(scratch)
        return child

    try:
        per_target_sec = (seconds / max(1, len(target_names))) if seconds > 0 else 0.0
        for name in target_names:
            target = TARGETS[name]
            t_start = time.monotonic()
            idx = 0
            while True:
                if seconds > 0:
                    if time.monotonic() - t_start >= per_target_sec and idx > 0:
                        break
                elif idx >= cases:
                    break
                rng = case(seed_val, name, idx)
                payload = target.make(rng)
                res = ensure_child().ask(name, payload, limit)
                if res is None:
                    # Child froze or died: confirm once in a brand-new child process.
                    if child is not None:
                        child.close()
                        child = None
                    retry = ensure_child().ask(name, payload, limit)
                    if retry is None:
                        if child is not None:
                            child.close()
                            child = None
                        res = {"bytes": weight(payload), "cpu": limit, "grown": 0,
                               "found": {"kind": "no answer within limit", "where": name,
                                         "detail": "exceeded %.1fs twice in fresh worker" % limit}}
                    else:
                        res = retry
                stats[name]["inputs"] += 1
                if res.get("tried", True):
                    stats[name]["tried"] += 1
                found = res.get("found")
                if found:
                    stats[name]["faults"] += 1
                    sig = (name, found["kind"], found["where"])
                    if sig not in seen_faults:
                        seen_faults.add(sig)
                        record = {
                            "target": name,
                            "seed": str(seed_val),
                            "index": idx,
                            "found": found,
                            "payload": pack(payload),
                        }
                        findings.append(record)
                        if out_dir is not None:
                            fdir = out_dir / "findings"
                            fdir.mkdir(parents=True, exist_ok=True)
                            (fdir / ("%s-%d.json" % (name, len(findings)))).write_text(
                                dumps(record), encoding="utf-8"
                            )
                idx += 1
            if not quiet:
                st = stats[name]
                print("%-18s %5d inputs (%5d reached parser), %d faults" % (name, st["inputs"], st["tried"], st["faults"]))
    finally:
        if child is not None:
            child.close()
        shutil.rmtree(str(scratch), ignore_errors=True)

    summary = {"seed": str(seed_val), "targets": stats, "findings": findings}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.json").write_text(
            json.dumps(dict({k: v for k, v in summary.items() if k != "findings"}, findings_count=len(findings)),
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return summary


def replay_finding(path: Path, limit: float = LIMIT_SEC) -> Dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    scratch = Path(tempfile.mkdtemp(prefix="lmv-fuzz-replay-"))
    worker = _Child(scratch)
    try:
        res = worker.ask(record["target"], unpack(record["payload"]), limit)
        if res is None:
            return {"found": {"kind": "no answer within limit", "where": record["target"], "detail": "timed out"}}
        return res
    finally:
        worker.close()
        shutil.rmtree(str(scratch), ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fuzz studio parsers and HTTP/MCP boundaries.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--list", action="store_true", help="List available fuzz targets and exit.")
    parser.add_argument("--cases", type=int, default=50, help="Cases per target when --seconds is not given.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Total wall-clock budget shared across targets.")
    parser.add_argument("--seed", default="1", help="Deterministic RNG seed.")
    parser.add_argument("--targets", default="", help="Comma-separated target names (default: all non-planted).")
    parser.add_argument("--limit", type=float, default=LIMIT_SEC, help="Per-input timeout in seconds.")
    parser.add_argument("--out", default="", help="Optional output folder for summary and findings.")
    parser.add_argument("--replay", default="", help="Replay a saved finding JSON file.")
    args = parser.parse_args(argv)

    if args.worker:
        return _worker_loop()
    if args.list:
        for name, target in TARGETS.items():
            if not target.planted:
                print("%-18s %s" % (name, target.about))
        return 0
    if args.replay:
        res = replay_finding(Path(args.replay), limit=args.limit)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res.get("found") else 0

    if args.targets:
        chosen = [t.strip() for t in args.targets.split(",") if t.strip()]
        unknown = [t for t in chosen if t not in TARGETS]
        if unknown:
            sys.stderr.write("Unknown target(s): %s\n" % ", ".join(unknown))
            return 2
    else:
        chosen = [name for name, t in TARGETS.items() if not t.planted]

    out_dir = Path(args.out) if args.out else None
    summary = run_suite(chosen, cases=args.cases, seconds=args.seconds, seed_val=str(args.seed),
                        limit=args.limit, out_dir=out_dir)
    if summary["findings"]:
        print("\n%d distinct finding(s):" % len(summary["findings"]))
        for f in summary["findings"]:
            info = f["found"]
            print("  - [%s #%d] %s at %s: %s" % (f["target"], f["index"], info["kind"], info["where"], info["detail"]))
        return 1
    print("\n0 faults across %d target(s)." % len(chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
