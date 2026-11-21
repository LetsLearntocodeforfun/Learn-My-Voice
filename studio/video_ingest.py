"""Stage 1 RedNote video & audio ingestion: verbatim speech, timing, tone, and on-screen 花字.

Supports three routes that all end at the same library invariant (``kind="video"``,
``needs_review=True`` until the creator checks the words, timing counted at once):

1. Multimodal Gemini (``gemini``): sends the video/audio inline with the Stage 1B
   blueprint (``prompts/01_verbatim_transcription_and_visual_ingest.md``) to extract
   verbatim Mandarin–English speech, beat timestamps, vocal energy, and burned-in 花字.
2. OpenAI Whisper (``openai``): sends the media file to ``/v1/audio/transcriptions``
   (``whisper-1``, ``verbose_json``) with the Stage 1A code-switching prompt.
3. No-key copy & paste (``video_ingest`` manual task): builds the Stage 1B prompt so
   the creator can attach their RedNote video in Gemini or ChatGPT and paste the JSON
   reply back.

Uploaded temporary media files are always deleted on the way out, whether the call
succeeds or fails.
"""
from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import llm, pipeline, posted, textstats, timing
from .jobs import JobError, Progress
from .store import Store, TextTooLong, clean_file_name

STAGE1 = "01_verbatim_transcription_and_visual_ingest.md"
AI_VIDEO_PROVIDERS = ("gemini", "openai")
DEFAULT_GEMINI_VIDEO_MODEL = "gemini-2.5-pro"
OPENAI_TRANSCRIBE_MODEL = "whisper-1"

MAX_GEMINI_VIDEO_BYTES = 25 * 1024 * 1024
MAX_OPENAI_AUDIO_BYTES = 25 * 1024 * 1024
MAX_FLOWER_TEXTS = 30
MAX_FLOWER_CHARS = 120
MAX_ENERGY_CHARS = 240
MAX_FORMAT_CHARS = 60

# Stage 1A (prompts/01_verbatim_transcription_and_visual_ingest.md): guides Whisper
# to preserve sentence-final particles and Mandarin-English code-switching verbatim.
VERBATIM_MANDARIN_CODESWITCH_PROMPT = (
    "以下是小红书日常Vlog旁白与好物分享原声逐字稿，使用简体中文，包含自然的中英文混说（code-switching）。"
    "请严格逐字记录口语原话，保留所有句尾语气词（如：啦、嘛、哈、喔、呐、诶、呗、呀）和口头连接词"
    "（如：其实、就是说、跟你们讲、说实话），不要把口语改成书面语。"
    "Example: 今天weekend出门逛了一圈，其实这个cream我用了一周啦，真的蛮舒服的哈。"
)

MEDIA_MIME_TYPES: Dict[str, str] = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "m4v": "video/x-m4v",
    "3gp": "video/3gpp",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "aac": "audio/aac",
    "aif": "audio/aiff",
    "aiff": "audio/aiff",
    "caf": "audio/x-caf",
    "flac": "audio/flac",
}

_TEMPLATE_ENERGY_HINT = "string describing warmth, pitch variation"
_TEMPLATE_FLOWER_HINT = "exact on-screen aside"


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    return fval if math.isfinite(fval) else None


def _clean_one_line(value: Any, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split()).strip()
    return cleaned[:max_chars].strip()


def _metric_str(value: Any) -> str:
    num = _finite_number(value)
    if num is None or num < 0:
        return "null"
    return str(int(num))


def media_mime_type(filename: str) -> str:
    ext = clean_file_name(filename).rsplit(".", 1)[-1].lower() if "." in clean_file_name(filename) else ""
    return MEDIA_MIME_TYPES.get(ext, "video/mp4")


def ai_video_info(store: Store) -> Dict[str, Any]:
    """Whether the AI provider currently selected in Settings can ingest video/audio directly."""
    config = store.load_config()
    provider = str(config.get("provider") or "")
    capable = provider in AI_VIDEO_PROVIDERS and bool(config["keys"].get(provider))
    return {
        "ai_video_capable": capable,
        "ai_video_provider": provider if capable else "",
    }


def ai_video_refusal_messages(store: Store) -> Tuple[str, str]:
    """Explain in English and Chinese why direct AI video/audio upload is unavailable for the current provider."""
    config = store.load_config()
    provider = str(config.get("provider") or "")
    if provider == "anthropic":
        return (
            "Anthropic Claude does not take video or audio files directly here. Transcribe offline on this computer, "
            "export .srt subtitles from CapCut/Jianying, use copy-and-paste, or switch to Gemini or OpenAI in Settings.",
            "Anthropic Claude 在这里不直接接收音视频文件。请使用本机离线转写、从剪映导出 .srt 字幕、"
            "使用复制粘贴方式，或到「设置」切换到 Gemini 或 OpenAI。",
        )
    if provider == "local":
        return (
            "The local text model does not take video or audio files directly. Transcribe offline on this computer, "
            "export .srt subtitles from CapCut/Jianying, use copy-and-paste, or connect Gemini or OpenAI in Settings.",
            "本地文本模型不直接接收音视频文件。请使用本机离线转写、从剪映导出 .srt 字幕、"
            "使用复制粘贴方式，或到「设置」连接 Gemini 或 OpenAI。",
        )
    return (
        "Connect a Gemini or OpenAI API key in Settings first, or use the copy-and-paste button.",
        "请先到「设置」连接 Gemini 或 OpenAI 密钥，或使用复制粘贴方式。",
    )


def video_ingest_prompt(
    title: str = "",
    caption: str = "",
    duration_sec: Optional[float] = None,
    raw_segments: Optional[List[Dict[str, Any]]] = None,
    *,
    note_id: str = "",
    likes: Optional[int] = None,
    collects: Optional[int] = None,
    comments: Optional[int] = None,
) -> Tuple[str, str]:
    """Build ``(system, user)`` from Stage 1B (``01_verbatim_transcription_and_visual_ingest.md``)."""
    dur = _finite_number(duration_sec)
    dur_str = "%.1f" % dur if (dur is not None and dur > 0) else "unknown"
    if raw_segments:
        segments_text = pipeline._dump(raw_segments)
    else:
        segments_text = (
            "[]  (no pre-run Whisper segments provided; listen to the attached video/audio directly "
            "and transcribe every spoken clause verbatim, keeping all modal particles 啦/嘛/哈/喔/诶/呗 "
            "and English code-switched words)"
        )
    return pipeline._blueprint(STAGE1, {
        "note_id": pipeline._inline((note_id or "").strip() or "local_upload"),
        "post_title": pipeline._inline((title or "").strip()),
        "post_caption": pipeline._inline((caption or "").strip()),
        "likes": _metric_str(likes),
        "collects": _metric_str(collects),
        "comments": _metric_str(comments),
        "duration_sec": dur_str,
        "whisper_timestamped_json": segments_text,
    })


def extract_visual_notes(result: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize Stage 1B visual & vocal delivery observations for storage in ``timing['visual_ingest']``."""
    out: Dict[str, Any] = {}
    delivery = result.get("delivery_observations")
    if isinstance(delivery, dict):
        energy = _clean_one_line(delivery.get("vocal_energy"), MAX_ENERGY_CHARS)
        if energy and _TEMPLATE_ENERGY_HINT not in energy.lower():
            out["vocal_energy"] = energy

    raw_flowers = result.get("onscreen_flower_texts")
    if isinstance(raw_flowers, list):
        flowers: List[str] = []
        seen = set()
        for item in raw_flowers:
            text_val = item.get("text") if isinstance(item, dict) else item
            cleaned = _clean_one_line(text_val, MAX_FLOWER_CHARS)
            if not cleaned or _TEMPLATE_FLOWER_HINT in cleaned.lower() or cleaned in seen:
                continue
            seen.add(cleaned)
            flowers.append(cleaned)
            if len(flowers) >= MAX_FLOWER_TEXTS:
                break
        if flowers:
            out["onscreen_flower_texts"] = flowers

    classification = result.get("content_classification")
    if isinstance(classification, dict):
        fmt = _clean_one_line(classification.get("video_primary_format"), MAX_FORMAT_CHARS)
        if fmt and "|" not in fmt:
            out["video_primary_format"] = fmt

    return out


def _normalize_beat_line(raw: Any, audio_mode: str) -> str:
    parts = [part.strip() for part in str(raw or "").replace("\r\n", "\n").split("\n") if part.strip()]
    line = " / ".join(parts).strip()
    if not line:
        return ""
    if audio_mode == "LIVE_CLIP" and not line.upper().startswith("[LIVE_CLIP"):
        line = "[LIVE_CLIP] " + line
    return line


def parse_stage1_reply(
    store: Store,
    result: Any,
    *,
    title: str = "",
    source_name: str = "",
    is_sponsored_override: Optional[bool] = None,
    fallback_duration_sec: Optional[float] = None,
    attach_to: Any = None,
) -> Dict[str, Any]:
    """Validate a Stage 1B (or Whisper segments) JSON reply and store it as a ``video`` library item."""
    if isinstance(result, str):
        result = llm.parse_json_text(result)
    if not isinstance(result, dict):
        raise llm.LLMError("Model reply is not a JSON object", "bad_json", "回复不是有效的 JSON 对象")

    lines: List[str] = []
    segments: List[Dict[str, Any]] = []

    raw_beats = result.get("beats")
    raw_segments = result.get("segments")
    if isinstance(raw_beats, list) and raw_beats:
        for beat in raw_beats:
            if not isinstance(beat, dict):
                continue
            mode = str(beat.get("audio_mode") or "").strip().upper()
            raw_line = (
                beat.get("verbatim_transcript_zh_en")
                if beat.get("verbatim_transcript_zh_en") is not None
                else (
                    beat.get("spoken_script_zh")
                    if beat.get("spoken_script_zh") is not None
                    else (beat.get("spoken_zh") if beat.get("spoken_zh") is not None else beat.get("text"))
                )
            )
            line = _normalize_beat_line(raw_line, mode)
            if not line:
                continue
            lines.append(line)
            start = _finite_number(beat.get("start_sec", beat.get("start")))
            end = _finite_number(beat.get("end_sec", beat.get("end")))
            if start is not None and end is not None and start >= 0 and end > start:
                segments.append({"start": start, "end": end, "text": line})
    elif isinstance(raw_segments, list) and raw_segments:
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            line = " ".join(str(seg.get("text") or "").splitlines()).strip()
            if not line:
                continue
            lines.append(line)
            start = _finite_number(seg.get("start", seg.get("start_sec")))
            end = _finite_number(seg.get("end", seg.get("end_sec")))
            if start is not None and end is not None and start >= 0 and end > start:
                segments.append({"start": start, "end": end, "text": line})
    else:
        plain = result.get("transcript") if isinstance(result.get("transcript"), str) else result.get("text")
        if isinstance(plain, str):
            lines = [ln.strip() for ln in plain.replace("\r\n", "\n").split("\n") if ln.strip()]

    text = "\n".join(lines).strip()
    if not textstats.spoken_units(textstats.strip_cues(text)):
        raise llm.LLMError(
            "No spoken words were found in the reply",
            "empty_result",
            "回复里没有识别出可用的口播文字",
        )

    dur = _finite_number(fallback_duration_sec)
    if segments:
        measured: Dict[str, Any] = timing.timing_stats(segments)
    elif dur is not None and dur >= 1.0:
        measured = timing.timing_stats([{"start": 0.0, "end": dur, "text": text}])
    else:
        measured = {}

    visual_notes = extract_visual_notes(result)
    if visual_notes:
        measured = dict(measured, visual_ingest=visual_notes)

    if isinstance(is_sponsored_override, bool):
        sponsored = is_sponsored_override
    else:
        classification = result.get("content_classification")
        if isinstance(classification, dict) and "is_sponsored" in classification:
            sponsored = bool(classification.get("is_sponsored"))
        else:
            sponsored = bool(result.get("is_sponsored"))

    clean_title = (title or "").strip() or (clean_file_name(source_name).rsplit(".", 1)[0] if source_name else "")
    timing_arg = measured if measured else None
    if attach_to is not None:
        return posted.attach_transcript(
            store, attach_to, text=text, kind="video", timing=timing_arg, source_name=source_name
        )
    return store.create_sample(
        title=clean_title,
        text=text,
        is_sponsored=sponsored,
        kind="video",
        timing=timing_arg,
        source_name=source_name,
    )


def transcribe_via_ai(
    store: Store,
    upload: Dict[str, Any],
    *,
    title: str = "",
    caption: str = "",
    is_sponsored: Optional[bool] = None,
    duration_sec: Optional[float] = None,
    attach_to: Any = None,
    transport: Optional[llm.Transport] = None,
    progress: Optional[Progress] = None,
) -> Dict[str, Any]:
    """Ingest an uploaded video/audio file through Gemini multimodal or OpenAI Whisper.

    Always deletes ``upload['path']`` on exit, whether transcription succeeds or fails.
    """
    path = Path(upload["path"])
    try:
        config = store.load_config()
        provider = str(config.get("provider") or "")
        api_key = str(config["keys"].get(provider) or "")
        if provider not in AI_VIDEO_PROVIDERS or not api_key:
            msg_en, msg_zh = ai_video_refusal_messages(store)
            raise JobError("not_configured", msg_en, msg_zh)

        try:
            size = path.stat().st_size
        except OSError:
            raise JobError("upload_missing", "The uploaded file could not be read.", "读取不到刚刚上传的文件。")

        limit = MAX_GEMINI_VIDEO_BYTES if provider == "gemini" else MAX_OPENAI_AUDIO_BYTES
        if size > limit:
            mb = limit // (1024 * 1024)
            raise JobError(
                "too_large",
                "This file is over %d MB, which exceeds %s's direct upload limit. Trim/compress the clip, "
                "export .srt subtitles from CapCut/Jianying, or use the copy-and-paste button to attach the video in Gemini/ChatGPT." % (mb, provider.title()),
                "这个文件超过 %d MB，超出了 %s 接口单次上传上限。可以先压缩/截取片段、从剪映导出 .srt 字幕，或用「复制粘贴」在网页版上传原视频。" % (mb, provider.title()),
            )

        if progress:
            progress("ai_upload", 0.25)
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            raise JobError("upload_missing", "The uploaded file could not be read.", "读取不到刚刚上传的文件。")

        source_name = clean_file_name(upload.get("name") or path.name) or "video.mp4"
        mime_type = media_mime_type(source_name)

        try:
            if provider == "gemini":
                model = str(config.get("model") or "").strip() or DEFAULT_GEMINI_VIDEO_MODEL
                safe_model = llm._gemini_model_segment(model)
                system, user = video_ingest_prompt(
                    title=title, caption=caption, duration_sec=duration_sec
                )
                b64_data = base64.b64encode(raw_bytes).decode("ascii")
                if progress:
                    progress("ai_analyze", 0.55)
                call = transport or llm._http
                data = call(
                    "POST",
                    "%s/models/%s:generateContent" % (llm.GEMINI_BASE, safe_model),
                    {"x-goog-api-key": api_key},
                    {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{
                            "role": "user",
                            "parts": [
                                {"inlineData": {"mimeType": mime_type, "data": b64_data}},
                                {"text": user},
                            ],
                        }],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                )
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                    reply_text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                except (KeyError, IndexError, TypeError):
                    raise llm.LLMError(
                        "Unexpected Gemini response shape (the reply may have been blocked)",
                        "bad_response",
                        "Gemini 返回的内容无法读取（可能被安全策略拦截）",
                    )
                parsed = llm.parse_json_text(reply_text)
                fallback_dur = duration_sec
            else:
                if progress:
                    progress("ai_analyze", 0.55)
                data = llm.post_multipart_json(
                    llm.OPENAI_BASE + "/audio/transcriptions",
                    {"Authorization": "Bearer " + api_key},
                    {
                        "model": OPENAI_TRANSCRIBE_MODEL,
                        "response_format": "verbose_json",
                        "prompt": VERBATIM_MANDARIN_CODESWITCH_PROMPT,
                    },
                    ("file", source_name, mime_type, raw_bytes),
                    transport=transport,
                )
                if not isinstance(data, dict):
                    raise llm.LLMError("Unexpected OpenAI transcription response", "bad_response")
                parsed = {
                    "segments": data.get("segments") if isinstance(data.get("segments"), list) else [],
                    "text": data.get("text") if isinstance(data.get("text"), str) else "",
                }
                api_dur = _finite_number(data.get("duration"))
                fallback_dur = api_dur if (api_dur is not None and api_dur > 0) else duration_sec

            sample = parse_stage1_reply(
                store,
                parsed,
                title=title,
                source_name=source_name,
                is_sponsored_override=is_sponsored,
                fallback_duration_sec=fallback_dur,
                attach_to=attach_to,
            )
        except llm.LLMError as exc:
            raise JobError(exc.code or "ai_failed", str(exc), exc.message_zh or "")
        except (TextTooLong, posted.PostedError) as exc:
            raise JobError(exc.code, exc.message, exc.message_zh)

        if progress:
            progress("done", 1.0)
        return {"sample_id": sample["id"], "title": sample["title"]}
    finally:
        _unlink_quietly(path)
