"""Files she takes out of the studio: text lists, subtitle files, and the 剪映 / CapCut kit.

Subtitle timing here is an ESTIMATE: clauses are laid back to back at her
speaking rate. Nothing listens to her recording. For subtitles aligned to the
real audio, the kit also contains plain voiceover text for 剪映's 文稿匹配.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from . import textstats

Cue = Tuple[float, float, str]

DEFAULT_RATE = 240.0     # units per minute, used when the profile has no rate
MIN_CUE_SEC = 0.5
SILENT_FLOWER_SEC = 2.0  # on-screen text on a line with nothing spoken
KINDS = ("jianying", "srt", "flower_srt", "voiceover", "shots", "flower")

_NAMES = {
    "zh": {"voiceover": "口播文稿.txt", "shots": "拍摄清单.txt", "flower": "花字清单.txt", "srt": "字幕.srt",
           "flower_srt": "花字.srt", "howto": "使用说明.txt", "jianying": "剪映素材包.zip"},
    "en": {"voiceover": "voiceover.txt", "shots": "shot_list.txt", "flower": "onscreen_text.txt", "srt": "subtitles.srt",
           "flower_srt": "onscreen_text.srt", "howto": "HOW_TO_USE.txt", "jianying": "capcut-kit.zip"},
}


def _is_live(line: Dict[str, Any]) -> bool:
    return str(line.get("audio_mode", "")).strip().upper() == "LIVE_CLIP"


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


def export_file(script: Dict[str, Any], kind: str, dna: Optional[Dict[str, Any]] = None,
                lang: str = "zh") -> Tuple[str, bytes, str]:
    """Return ``(filename, body, content_type)``; raises KeyError for an unknown kind."""
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

    rate = ((dna or {}).get("cadence_and_syntax") or {}).get("target_speaking_rate_cpm")
    subtitles, flowers, total = build_cues(script["lines"], rate)
    if kind == "srt":
        return filename, to_srt(subtitles).encode("utf-8"), "application/x-subrip; charset=utf-8"
    if kind == "flower_srt":
        return filename, to_srt(flowers).encode("utf-8"), "application/x-subrip; charset=utf-8"

    shown_rate = int(rate) if isinstance(rate, (int, float)) and rate > 0 else int(DEFAULT_RATE)
    flower_part = _FLOWER_PART[lang][0 if flowers else 1].format(flower_srt=names["flower_srt"])
    howto = _HOWTO[lang].format(title=title, sec=int(round(total)), rate=shown_rate, voiceover=names["voiceover"],
                                srt=names["srt"], shots=names["shots"], flower_part=flower_part)
    files = [(names["howto"], howto), (names["srt"], to_srt(subtitles)), (names["voiceover"], voiceover_text(script))]
    if flowers:
        files.append((names["flower_srt"], to_srt(flowers)))
    shots = _numbered(script, "visual_broll_en_zh")
    if shots.strip():
        files.append((names["shots"], shots))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files:
            archive.writestr(name, text.encode("utf-8"))
    return filename, buffer.getvalue(), "application/zip"
