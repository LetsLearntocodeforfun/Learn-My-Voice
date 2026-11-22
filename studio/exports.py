"""Files she takes out of the studio: text lists, subtitle files, the 剪映 / CapCut kit, and a teleprompter for a phone.

Subtitle timing here is an ESTIMATE: clauses are laid back to back at her
speaking rate. Nothing listens to her recording. For subtitles aligned to the
real audio, the kit also contains plain voiceover text for 剪映's 文稿匹配.

The phone teleprompter is one page with everything inside it. It is paced by
arithmetic on the same speaking rate, it loads nothing and sends nothing, and
it is put together from the code and the styles of the app's own prompter
(``_shared``), so the two cannot drift apart.
"""
from __future__ import annotations

import html
import io
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xml.etree.ElementTree as ET

from . import creator, packaging, recording, textstats

Cue = Tuple[float, float, str]

DEFAULT_RATE = 240.0     # units per minute, used when the profile has no rate
MIN_CUE_SEC = 0.5
SILENT_FLOWER_SEC = 2.0  # on-screen text on a line with nothing spoken
KINDS = ("jianying", "srt", "flower_srt", "voiceover", "shots", "flower", "fcpxml", "prompter")
WEB_DIR = Path(__file__).with_name("web")
# Whose rate the teleprompter is paced by. The prompter has one sentence for each (tp_src_* in web/prompter.js).
SOURCE_MINE, SOURCE_DEFAULT, SOURCE_EXAMPLE = "mine", "default", "example"

_NAMES = {
    "zh": {"voiceover": "口播文稿.txt", "shots": "拍摄清单.txt", "flower": "花字清单.txt", "srt": "字幕.srt",
           "flower_srt": "花字.srt", "howto": "使用说明.txt", "jianying": "剪映素材包.zip", "fcpxml": "时间线.fcpxml",
           "prompter": "提词器.html", "post_and_cover": "发布文案与封面.txt"},
    "en": {"voiceover": "voiceover.txt", "shots": "shot_list.txt", "flower": "onscreen_text.txt", "srt": "subtitles.srt",
           "flower_srt": "onscreen_text.srt", "howto": "HOW_TO_USE.txt", "jianying": "capcut-kit.zip",
           "fcpxml": "timeline.fcpxml", "prompter": "teleprompter.html", "post_and_cover": "post_and_cover.txt"},
}


def _is_live(line: Dict[str, Any]) -> bool:
    return str(line.get("audio_mode", "")).strip().upper() == "LIVE_CLIP"


def post_and_cover_text(script: Dict[str, Any], lang: str = "zh") -> str:
    """Format RedNote titles, 3:4 cover callouts, caption, hashtags, and pinned CTA; empty when post has none."""
    raw_post = script.get("post")
    if not packaging.has_packaging_content(raw_post):
        return ""
    norm = packaging.normalize_post(raw_post)
    is_en = lang == "en"
    sections: List[str] = []

    active_title = packaging.active_title_of(norm)
    if active_title:
        sections.append(("%s\n%s" % ("Selected title" if is_en else "【选定标题】", active_title)))

    if norm["title_options"]:
        header = "Title options" if is_en else "【备选标题】"
        lines = []
        for idx, opt in enumerate(norm["title_options"]):
            star = "★ " if idx == norm["selected_title_idx"] else "  "
            en_part = (" (%s)" % opt["title_en"]) if is_en and opt.get("title_en") else ""
            lines.append("%s%d. %s%s" % (star, idx + 1, opt["title_zh"], en_part))
        sections.append(header + "\n" + "\n".join(lines))

    if norm["cover_text_zh"] or norm["cover_sub_zh"] or norm["cover_options"]:
        header = "3:4 Cover text" if is_en else "【3:4 封面文字】"
        c_lines: List[str] = []
        if norm["cover_text_zh"]:
            c_lines.append(("%s: %s" % ("Headline" if is_en else "主标题", norm["cover_text_zh"])))
        if norm["cover_sub_zh"]:
            c_lines.append(("%s: %s" % ("Subtitle" if is_en else "副标题", norm["cover_sub_zh"])))
        if len(norm["cover_options"]) > 1:
            c_lines.append("Options:" if is_en else "备选封面：")
            for idx, copt in enumerate(norm["cover_options"], start=1):
                pair = " / ".join(p for p in (copt["cover_text_zh"], copt["cover_sub_zh"]) if p)
                if pair:
                    c_lines.append("  %d. %s" % (idx, pair))
        sections.append(header + "\n" + "\n".join(c_lines))

    if norm["body_zh"]:
        header = "Note caption" if is_en else "【笔记正文】"
        sections.append(header + "\n" + norm["body_zh"])

    if norm["hashtags"]:
        header = "Hashtags" if is_en else "【话题标签】"
        sections.append(header + "\n" + " ".join(norm["hashtags"]))

    if norm["pinned_comment_zh"] or norm["pinned_options"]:
        header = "Pinned comment CTA" if is_en else "【置顶评论引导】"
        p_lines: List[str] = []
        if norm["pinned_comment_zh"]:
            p_lines.append(norm["pinned_comment_zh"])
        if norm["pinned_options"]:
            for idx, popt in enumerate(norm["pinned_options"], start=1):
                if popt["text_zh"] and popt["text_zh"] != norm["pinned_comment_zh"]:
                    p_lines.append(("  %d. %s" % (idx, popt["text_zh"])))
        if p_lines:
            sections.append(header + "\n" + "\n".join(p_lines))

    return ("\n\n".join(sections).strip() + "\n") if sections else ""


def voiceover_text(script: Dict[str, Any]) -> str:
    """One breath per line; live clips and [cues] left out."""
    out: List[str] = []
    for line in script["lines"]:
        if not _is_live(line):
            out.extend(textstats.split_clauses(line.get("spoken_script_zh", ""), punctuation_breaks=False))
    return "\n".join(out) + "\n"


def _numbered(script: Dict[str, Any], field: str) -> str:
    rows = ["%d. %s  %s" % (l["line_no"], l.get("time_range_sec", ""), l.get(field, ""))
            for l in script["lines"] if str(l.get(field, "")).strip()]
    return "\n".join(rows) + "\n"


def build_cues(lines: List[Dict[str, Any]], cpm: Any) -> Tuple[List[Cue], List[Cue], float]:
    """Return ``(subtitle_cues, flower_cues, total_seconds)`` with estimated timing."""
    rate = float(cpm) if isinstance(cpm, (int, float)) and not isinstance(cpm, bool) and cpm > 0 else DEFAULT_RATE
    clock = 0.0
    subtitles: List[Cue] = []
    flowers: List[Cue] = []
    for line in lines:
        start = clock
        text = str(line.get("spoken_script_zh", ""))
        if _is_live(line):
            span = textstats.parse_time_range(str(line.get("time_range_sec", "")))
            clock += (span[1] - span[0]) if span else textstats.spoken_units(textstats.strip_cues(text)) / rate * 60
        else:
            for clause in textstats.split_clauses(text, punctuation_breaks=False):
                units = textstats.spoken_units(clause)
                if units:
                    duration = max(units / rate * 60, MIN_CUE_SEC)
                    subtitles.append((clock, clock + duration, clause))
                    clock += duration
        flower = str(line.get("onscreen_flower_text_zh", "")).strip()
        if flower:
            if clock == start:
                clock += SILENT_FLOWER_SEC
            flowers.append((start, clock, flower))
    return subtitles, flowers, clock


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def to_srt(cues: List[Cue]) -> str:
    blocks = ["%d\n%s --> %s\n%s\n" % (i, _stamp(start), _stamp(end), text.replace("\n", " "))
              for i, (start, end, text) in enumerate(cues, start=1)]
    return "\n".join(blocks)


_HOWTO = {
    "zh": """剪映素材包 · {title}
预计时长：约 {sec} 秒（按语速 {rate} 字/分钟估算）

一、字幕（两种方法，任选其一）

方法 A（推荐）：文稿匹配，字幕会和你的录音自动对齐
  1. 把录好的口播或视频拖进剪映的时间线。
  2. 打开「文本」（新版本可能叫「字幕」）→「智能字幕」→「文稿匹配」。
  3. 把「{voiceover}」里的内容粘贴进去，开始匹配。

方法 B：导入字幕文件
  1. 打开「文本」（新版本可能叫「字幕」）→「本地字幕」→「导入」，选择「{srt}」。
  2. 把字幕添加到时间线。
  注意：这份字幕的时间是按语速估算的，不是按你的实际录音。导入后需要自己拖动微调。

二、花字
{flower_part}

三、拍摄清单
  「{shots}」是每一句对应的画面，拍摄和找素材时对照用。

说明：菜单名称可能因剪映版本不同而略有差异。国际版 CapCut 请在字幕（Captions）相关面板里找导入字幕文件的入口。
""",
    "en": """CapCut / Jianying kit · {title}
Estimated length: about {sec} s (at {rate} units per minute)

1. Subtitles (pick one method)

Method A (recommended): script matching, which aligns subtitles to your recording
  1. Drag your recorded voiceover or video onto the timeline.
  2. In Jianying open 文本 Text (called 字幕 Captions in newer versions) → 智能字幕 → 文稿匹配.
  3. Paste the contents of "{voiceover}" and start matching.

Method B: import the subtitle file
  1. Open 文本 Text (or 字幕 Captions) → 本地字幕 → 导入 and choose "{srt}".
  2. Add the subtitles to the timeline.
  Note: timing in this file is estimated from speaking rate, not taken from your recording. Adjust it after importing.

2. On-screen text (花字)
{flower_part}

3. Shot list
  "{shots}" lists what to film for each line.

Menu names can differ between Jianying versions. In CapCut, look for caption-file import in the Captions panel.
""",
}
_FLOWER_PART = {
    "zh": ("  用导入字幕的方法导入「{flower_srt}」，每条花字会出现在对应那句话的大概位置，再套上你喜欢的花字样式。",
           "  这篇脚本没有填花字。"),
    "en": ("  Import \"{flower_srt}\" the same way; each item lands near its line. Then apply the text style you like.",
           "  This script has no on-screen text."),
}


def _profile_rate(dna: Any) -> Optional[float]:
    """The speaking rate a voice profile holds, or None when it holds none that can be used."""
    # A stored profile may have the wrong shape (one saved by an early version from a wrong-shaped reply), and
    # its rate may not be a number: then the default rate is used, as when there is no profile.
    cadence = dna.get("cadence_and_syntax") if isinstance(dna, dict) else None
    rate = cadence.get("target_speaking_rate_cpm") if isinstance(cadence, dict) else None
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
        return None
    return rate


# ---- the teleprompter for a phone ------------------------------------------------------------------------
def prompter_breaths(script: Dict[str, Any]) -> List[Tuple[str, int]]:
    """``(text, spoken units)`` for each breath she reads, in order. Must match ``tpBreaths`` in web/prompter.js.

    Live clips and [cues] are left out, as in the voiceover text. A breath with nothing this app counts in it
    (only punctuation, say) still gets one unit, so the prompter shows it for a moment and never passes over it.
    """
    out: List[Tuple[str, int]] = []
    for line in script["lines"]:
        if not isinstance(line, dict) or _is_live(line):
            continue
        spoken = line.get("spoken_script_zh")
        for clause in textstats.split_clauses(spoken if isinstance(spoken, str) else "", punctuation_breaks=False):
            out.append((clause, max(1, textstats.spoken_units(clause))))
    return out


def prompter_pace(dna: Any, is_example: bool = False) -> Tuple[float, str]:
    """``(units per minute, whose rate that is)`` for the teleprompter.

    It is called hers only when it was measured from her recordings or typed in by her, on her own profile.
    The bundled example's rate is called the example's. Anything else is called the default: no usable rate
    at all (then DEFAULT_RATE), or the one a profile holds before either has happened. The voice page and the
    prompter in the app draw the same line.
    """
    rate = _profile_rate(dna)
    if rate is None:
        return DEFAULT_RATE, SOURCE_DEFAULT
    if is_example:
        return rate, SOURCE_EXAMPLE
    summary = dna.get("source_corpus_summary")
    summary = summary if isinstance(summary, dict) else {}
    hers = summary.get("speaking_rate_source") == "measured" or summary.get("speaking_rate_confirmed") is True
    return rate, SOURCE_MINE if hers else SOURCE_DEFAULT


def _shared(name: str, mark: str) -> str:
    """The part of a file of the interface between its ``<mark>:start`` and ``<mark>:end`` comments.

    The phone teleprompter runs the very code, and wears the very styles, of the prompter in the app. Those
    parts use nothing else from the app and name no address. tests/test_prompter.py checks both.
    """
    text = (WEB_DIR / name).read_text(encoding="utf-8")
    last = "/* %s:end */" % mark
    start = text.index("/* %s:start" % mark)
    return text[start:text.index(last, start) + len(last)] + "\n"


def _page_text(text: str) -> str:
    """Text for the markup of the phone page.

    "/" is written as a character reference as well. Nothing she typed (a web address in a title, say) can
    then put two slashes in a row into the file, which is checked to hold no address at all.
    """
    return html.escape(text, quote=True).replace("/", "&#47;")


_IN_SCRIPT = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029", "/": "\\/"}


def _page_json(value: Any) -> str:
    """JSON that is safe inside a script element of the phone page.

    "<", ">" and "&" are written as escapes, so a line that says ``</script>`` stays a line of the script and
    ends nothing. U+2028 and U+2029 are escaped because older browsers read them as line ends in code. "/" is
    escaped for the reason given in ``_page_text``. A JSON reader gets the same text back in every case.
    """
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return re.sub("[<>&/\u2028\u2029]", lambda found: _IN_SCRIPT[found.group()], text)


_PHONE_WORDS = {
    "zh": {"title": "提词器", "empty": "脚本还是空的。",
           "cannot_run": "这个打开方式不能运行提词器，所以文字不会自己走。下面是全文，可以用手滑动；"
                         "换用浏览器打开这个文件，它才会按语速走。"},
    "en": {"title": "Teleprompter", "empty": "The script is empty.",
           "cannot_run": "The prompter cannot run in this view, so the text will not move on its own. The whole "
                         "text is below and can be scrolled by hand; open this file in a browser to have it paced."},
}

# On top of the shared styles: the page is the prompter, the buttons sit under the thumb and are large enough
# to hit, keys are not named (a phone has none), and the text can be read and scrolled where code cannot run.
_PHONE_CSS = """html, body { height: 100%; margin: 0; background: #000; }
body { font: 16px/1.5 -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; -webkit-text-size-adjust: 100%; }
.tp { position: fixed; top: 0; right: 0; bottom: 0; left: 0; }
.tp-status { padding-top: calc(8px + env(safe-area-inset-top)); }
.tp-bar { order: 1; justify-content: center; padding-bottom: calc(10px + env(safe-area-inset-bottom)); }
.tp button { min-height: 44px; }
.tp-key { display: none; }
.tp-note { margin: 0; padding: 12px 16px; background: #111; color: #bbb; font-size: 14px; }
.tp-static { flex: 1; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 24px 9vw; text-align: center; font-size: 30px; font-weight: 600; }
.tp-static p { margin: 0 0 .9em; word-break: break-word; overflow-wrap: anywhere; }
"""

# Starts the shared prompter on the data written into the page. The text size it begins with suits a phone.
_PHONE_START = """(function () {
  var data = JSON.parse(document.getElementById('tp-data').textContent);
  var words = TP_TEXT[data.lang];
  tpMount(document.getElementById('tp'), {
    breaths: data.breaths.map(function (b) { return { text: b[0], units: b[1] }; }),
    cpm: data.cpm,
    source: data.source,
    size: 34,
    say: function (key) { return String(words[key]).split('{@names}').join(data.names); }
  });
}());
"""


def prompter_page(script: Dict[str, Any], dna: Any = None, lang: str = "zh", dna_is_example: bool = False,
                  who: Any = None) -> str:
    """The teleprompter as one page that needs nothing else: styles, code and the script are all inside it.

    Her text is in the page twice. Once as JSON, which the prompter reads (``_page_json``). And once as plain
    paragraphs, which are what shows where code is not run at all (a file preview on a phone, for one) and
    which the prompter replaces where it does run. ``who`` is the creator profile: English text names her.
    """
    lang = "en" if lang == "en" else "zh"
    words = _PHONE_WORDS[lang]
    breaths = prompter_breaths(script)
    rate, source = prompter_pace(dna, dna_is_example)
    data = {"lang": lang, "cpm": rate, "source": source, "names": creator.english_name(who) + "'s",
            "breaths": [[text, units] for text, units in breaths]}
    plain = "".join("<p>%s</p>" % _page_text(text) for text, _ in breaths) or "<p>%s</p>" % _page_text(words["empty"])
    return "".join((
        '<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n' % ("en" if lang == "en" else "zh-CN"),
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n',
        "<title>%s</title>\n" % _page_text("%s · %s" % (script["title"], words["title"])),
        "<style>\n", _shared("app.css", "tp-look"), _PHONE_CSS, "</style>\n</head>\n<body>\n",
        '<div id="tp" class="tp"><noscript><p class="tp-note">%s</p></noscript><div class="tp-static">%s</div></div>\n'
        % (_page_text(words["cannot_run"]), plain),
        '<script type="application/json" id="tp-data">', _page_json(data), "</script>\n",
        "<script>\n'use strict';\n", _shared("prompter.js", "tp-core"), _PHONE_START, "</script>\n</body>\n</html>\n",
    ))


_ALIGN_MODE_TEXT = {
    "zh": {
        "whisper_aligned": "录音词级对齐（Whisper 本地转写时间轴对齐，非按语速估算）",
        "subtitle_aligned": "字幕轴对齐（按上传字幕的时间轴对齐，非按语速估算）",
        "pause_aligned": "录音停顿对齐（按检测到的说话/停顿区间与气口字数分配时间，未做语音识别，非按固定语速估算）",
    },
    "en": {
        "whisper_aligned": "Whisper audio-aligned (matched to on-device transcript timestamps, not estimated from speaking rate)",
        "subtitle_aligned": "Subtitle-cue aligned (matched to uploaded subtitle timestamps, not estimated from speaking rate)",
        "pause_aligned": "Speech-pause aligned (clauses distributed across detected speech/pause islands by spoken unit weight without speech recognition, not estimated from a fixed speaking rate)",
    },
}

_HOWTO_ALIGNED = {
    "zh": """剪映素材包 · {title}
对齐时长：约 {sec} 秒{rate_note}
时间轴来源：{mode_text}（{mode_code}）

一、字幕与时间线
  1. 「{srt}」的时间轴已按当前激活的录音/字幕对齐记录生成（来源：{mode_code}）。
  2. 打开剪映「文本」（或「字幕」）→「本地字幕」→「导入」，选择「{srt}」即可放到时间线上。
  3. 包内同时附带「{fcpxml}」（FCPXML 1.9 竖屏 1080x1920 30p 时间线），可直接导入 Final Cut Pro 或 DaVinci Resolve，包含双轨道字幕/花字与分镜标记。
  4. 如需在剪映重新做文稿匹配，也可使用「{voiceover}」。

二、花字
{flower_part}

三、拍摄清单
  「{shots}」是每一句对应的画面，拍摄和找素材时对照用。
""",
    "en": """CapCut / Jianying kit · {title}
Aligned length: about {sec} s{rate_note}
Timing source: {mode_text} ({mode_code})

1. Subtitles and timeline
  1. "{srt}" is aligned from the active take ({mode_code}).
  2. In Jianying / CapCut open Captions → Import local subtitles and choose "{srt}".
  3. "{fcpxml}" (FCPXML 1.9, 1080x1920 30p vertical timeline) is included for Final Cut Pro and DaVinci Resolve, with lane 1 subtitles, lane 2 on-screen text, and shot markers.
  4. "{voiceover}" is also included if you want plain voiceover text for script matching.

2. On-screen text (花字)
{flower_part}

3. Shot list
  "{shots}" lists what to film for each line.
""",
}

_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def _xml_clean(text: Any) -> str:
    return _XML_ILLEGAL.sub("", str(text or ""))


def _fcpxml_frames(seconds: float) -> int:
    if not math.isfinite(seconds) or seconds <= 0.0:
        return 0
    return int(round(seconds * 30.0))


def _fcpxml_time(frames: int) -> str:
    return "%d/30s" % max(0, int(frames))


def cues_from_take(script: Dict[str, Any], active_take: Dict[str, Any]) -> Tuple[List[Cue], List[Cue], float]:
    """Convert a stored ``script_takes`` row into ``(subtitle_cues, flower_cues, total_sec)``."""
    raw_cues = active_take.get("cues") if isinstance(active_take.get("cues"), list) else []
    subtitles: List[Cue] = []
    for item in raw_cues:
        if not isinstance(item, dict):
            continue
        txt = str(item.get("text") or "").strip()
        if not txt:
            continue
        s_val = float(item.get("start_sec") or 0.0)
        e_val = max(s_val, float(item.get("end_sec") or s_val))
        subtitles.append((s_val, e_val, txt))
    dur = float(active_take.get("duration_sec") or 0.0)
    flower_dicts, total = recording.build_aligned_flower_cues(script.get("lines", []), raw_cues, dur)
    flowers: List[Cue] = [(float(f["start_sec"]), float(f["end_sec"]), str(f["text"])) for f in flower_dicts]
    max_sub = max((e for _, e, _ in subtitles), default=0.0)
    max_flw = max((e for _, e, _ in flowers), default=0.0)
    return subtitles, flowers, max(total, dur, max_sub, max_flw)


def _shot_markers(lines: List[Dict[str, Any]], subtitles: List[Cue], total_sec: float) -> List[Tuple[float, str]]:
    """Compute ``(start_sec, shot_note)`` for every line with ``visual_broll_en_zh``."""
    markers: List[Tuple[float, str]] = []
    sub_idx = 0
    clock = 0.0
    for idx, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        start_at = clock
        if _is_live(line):
            span = textstats.parse_time_range(str(line.get("time_range_sec") or ""))
            if span and span[1] >= span[0]:
                clock += (span[1] - span[0])
        else:
            clauses = [c for c in textstats.split_clauses(str(line.get("spoken_script_zh") or ""), punctuation_breaks=False)
                       if textstats.spoken_units(c)]
            if clauses and sub_idx < len(subtitles):
                start_at = subtitles[sub_idx][0]
                end_idx = min(len(subtitles), sub_idx + len(clauses)) - 1
                clock = max(clock, subtitles[end_idx][1])
                sub_idx = min(len(subtitles), sub_idx + len(clauses))
        visual = _xml_clean(line.get("visual_broll_en_zh", "")).strip()
        if visual:
            lno = line.get("line_no") or (idx + 1)
            clamped = max(0.0, min(start_at, max(0.0, total_sec)))
            markers.append((clamped, "%s. %s" % (lno, visual)))
    return markers


def to_fcpxml(script: Dict[str, Any], subtitles: List[Cue], flowers: List[Cue],
              total_sec: float, lang: str = "zh") -> str:
    """Build a valid Final Cut Pro FCPXML 1.9 vertical (1080x1920 30p) timeline XML string."""
    max_end = max([float(total_sec or 0.0)] + [e for _, e, _ in subtitles] + [e for _, e, _ in flowers])
    total_frames = max(30, _fcpxml_frames(max_end))
    title_clean = _xml_clean(script.get("title") or ("Untitled" if lang == "en" else "未命名脚本")).strip() or "Timeline"
    event_name = "Learn My Voice" if lang == "en" else "脚本工作台"

    root = ET.Element("fcpxml", {"version": "1.9"})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": "FFVideoFormatRateUndefined",
        "frameDuration": "100/3000s",
        "width": "1080",
        "height": "1920",
        "colorSpace": "1-1-1 (Rec. 709)",
    })
    ET.SubElement(resources, "effect", {
        "id": "r2",
        "name": "Basic Title",
        "uid": ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti",
    })

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": title_clean})
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": _fcpxml_time(total_frames),
        "tcStart": "0/30s",
        "tcFormat": "NDF",
        "audioLayout": "stereo",
        "audioRate": "48k",
    })
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(spine, "gap", {
        "name": "Gap",
        "offset": "0/30s",
        "start": "0/30s",
        "duration": _fcpxml_time(total_frames),
    })

    # Lane 1: spoken breath subtitles
    prev_sub_end = 0
    for idx, (s_sec, e_sec, raw_txt) in enumerate(subtitles, start=1):
        txt = _xml_clean(raw_txt).replace("\n", " ").strip()
        if not txt:
            continue
        s_frames = max(prev_sub_end, _fcpxml_frames(s_sec))
        d_frames = max(1, _fcpxml_frames(e_sec) - s_frames)
        prev_sub_end = s_frames + d_frames
        if prev_sub_end > total_frames:
            total_frames = prev_sub_end
            gap.set("duration", _fcpxml_time(total_frames))
            sequence.set("duration", _fcpxml_time(total_frames))
        style_id = "ts_sub_%d" % idx
        t_el = ET.SubElement(gap, "title", {
            "ref": "r2",
            "lane": "1",
            "name": txt[:40],
            "offset": _fcpxml_time(s_frames),
            "start": "0/30s",
            "duration": _fcpxml_time(d_frames),
        })
        text_el = ET.SubElement(t_el, "text")
        ts_ref = ET.SubElement(text_el, "text-style", {"ref": style_id})
        ts_ref.text = txt
        ts_def = ET.SubElement(t_el, "text-style-def", {"id": style_id})
        ET.SubElement(ts_def, "text-style", {
            "font": "PingFang SC",
            "fontSize": "52",
            "fontColor": "1 1 1 1",
            "alignment": "center",
        })

    # Lane 2: on-screen flower text overlays
    prev_flw_end = 0
    for idx, (s_sec, e_sec, raw_txt) in enumerate(flowers, start=1):
        txt = _xml_clean(raw_txt).replace("\n", " ").strip()
        if not txt:
            continue
        s_frames = max(prev_flw_end, _fcpxml_frames(s_sec))
        d_frames = max(1, _fcpxml_frames(e_sec) - s_frames)
        prev_flw_end = s_frames + d_frames
        if prev_flw_end > total_frames:
            total_frames = prev_flw_end
            gap.set("duration", _fcpxml_time(total_frames))
            sequence.set("duration", _fcpxml_time(total_frames))
        style_id = "ts_flw_%d" % idx
        t_el = ET.SubElement(gap, "title", {
            "ref": "r2",
            "lane": "2",
            "name": txt[:40],
            "offset": _fcpxml_time(s_frames),
            "start": "0/30s",
            "duration": _fcpxml_time(d_frames),
        })
        text_el = ET.SubElement(t_el, "text")
        ts_ref = ET.SubElement(text_el, "text-style", {"ref": style_id})
        ts_ref.text = txt
        ts_def = ET.SubElement(t_el, "text-style-def", {"id": style_id})
        ET.SubElement(ts_def, "text-style", {
            "font": "PingFang SC",
            "fontSize": "64",
            "fontColor": "1 0.92 0.35 1",
            "bold": "1",
            "alignment": "center",
        })

    # Shot markers for visual_broll_en_zh
    for m_sec, m_val in _shot_markers(script.get("lines", []), subtitles, total_frames / 30.0):
        m_frames = min(max(0, _fcpxml_frames(m_sec)), max(0, total_frames - 1))
        ET.SubElement(gap, "marker", {
            "start": _fcpxml_time(m_frames),
            "duration": "1/30s",
            "value": m_val,
        })

    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def export_file(script: Dict[str, Any], kind: str, dna: Optional[Dict[str, Any]] = None,
                lang: str = "zh", dna_is_example: bool = False, who: Any = None,
                active_take: Optional[Dict[str, Any]] = None) -> Tuple[str, bytes, str]:
    """Return ``(filename, body, content_type)``; raises KeyError for an unknown kind.

    ``dna_is_example`` and ``who`` (the creator profile) matter to the phone teleprompter only, which says
    whose rate it is paced by. When ``active_take`` is provided, ``srt``, ``flower_srt``, ``fcpxml`` and
    ``jianying`` use the take's aligned cues and state the exact alignment source; when ``None``, behavior
    is identical to rate-estimated timing.
    """
    if kind not in KINDS:
        raise KeyError(kind)
    names = _NAMES["en" if lang == "en" else "zh"]
    lang = "en" if lang == "en" else "zh"
    title = script["title"]
    filename = "%s-%s" % (title, names[kind])
    text_type = "text/plain; charset=utf-8"
    if kind == "voiceover":
        return filename, voiceover_text(script).encode("utf-8"), text_type
    if kind == "shots":
        return filename, _numbered(script, "visual_broll_en_zh").encode("utf-8"), text_type
    if kind == "flower":
        return filename, _numbered(script, "onscreen_flower_text_zh").encode("utf-8"), text_type
    if kind == "prompter":
        page = prompter_page(script, dna, lang, dna_is_example, who)
        return filename, page.encode("utf-8"), "text/html; charset=utf-8"

    rate = _profile_rate(dna)
    if active_take is not None:
        subtitles, flowers, total = cues_from_take(script, active_take)
    else:
        subtitles, flowers, total = build_cues(script["lines"], rate)

    if kind == "srt":
        return filename, to_srt(subtitles).encode("utf-8"), "application/x-subrip; charset=utf-8"
    if kind == "flower_srt":
        return filename, to_srt(flowers).encode("utf-8"), "application/x-subrip; charset=utf-8"
    if kind == "fcpxml":
        return filename, to_fcpxml(script, subtitles, flowers, total, lang).encode("utf-8"), "application/xml; charset=utf-8"

    flower_part = _FLOWER_PART[lang][0 if flowers else 1].format(flower_srt=names["flower_srt"])
    if active_take is not None:
        mode_code = str(active_take.get("alignment_mode") or "pause_aligned")
        mode_text = _ALIGN_MODE_TEXT[lang].get(mode_code, _ALIGN_MODE_TEXT[lang]["pause_aligned"])
        mcpm = float(active_take.get("measured_cpm") or 0.0)
        if mcpm > 0:
            rate_note = ("（实测语速约 %d 字/分钟）" % int(round(mcpm))) if lang == "zh" else (" (measured ~%d units/min)" % int(round(mcpm)))
        else:
            rate_note = ""
        howto = _HOWTO_ALIGNED[lang].format(
            title=title, sec=int(round(total)), rate_note=rate_note, mode_text=mode_text, mode_code=mode_code,
            voiceover=names["voiceover"], srt=names["srt"], fcpxml=names["fcpxml"],
            shots=names["shots"], flower_part=flower_part,
        )
    else:
        shown_rate = int(rate) if rate is not None else int(DEFAULT_RATE)
        howto = _HOWTO[lang].format(title=title, sec=int(round(total)), rate=shown_rate, voiceover=names["voiceover"],
                                    srt=names["srt"], shots=names["shots"], flower_part=flower_part)

    files = [(names["howto"], howto), (names["srt"], to_srt(subtitles)), (names["voiceover"], voiceover_text(script))]
    if flowers:
        files.append((names["flower_srt"], to_srt(flowers)))
    shots = _numbered(script, "visual_broll_en_zh")
    if shots.strip():
        files.append((names["shots"], shots))
    if active_take is not None:
        files.append((names["fcpxml"], to_fcpxml(script, subtitles, flowers, total, lang)))
    post_copy = post_and_cover_text(script, lang)
    if post_copy.strip():
        files.append((names["post_and_cover"], post_copy))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files:
            archive.writestr(name, text.encode("utf-8"))
    return filename, buffer.getvalue(), "application/zip"
