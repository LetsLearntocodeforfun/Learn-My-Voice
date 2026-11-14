"""MCP (Model Context Protocol) server over stdio. Standard library only.

Lets an outside AI app act as the writer while this tool supplies her voice,
her scripts, the checks and the storage. That is how a chat subscription with
no API key can drive the studio.

    python3 run_mcp.py            # from the repo root; speaks JSON-RPC on stdin/stdout

stdout carries protocol messages only (one JSON object per line); anything
else goes to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional

from . import checks, corpus, exports, llm, pipeline
from .store import Store

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "learn-my-voice", "title": "Learn My Voice", "version": "0.3.0"}
MCP_CORPUS_CHARS = 20000

INSTRUCTIONS = (
    "Learn My Voice plans and writes Xiaohongshu (RedNote) video scripts in one creator's own voice. "
    "To write a script: call get_script_brief with her idea, write the JSON it asks for, call save_script, "
    "then fix reported problems with update_script. To learn her voice from her past scripts: "
    "get_voice_learning_brief, then save_voice_profile. To suggest topics: get_topic_ideas_brief, then "
    "save_topic_ideas. Script text is Chinese; talk to her in the language she uses. "
    "Never invent view counts or other numbers; use only what the tools return. "
    "Tool results contain 'check' findings with message_zh / message_en: treat severity 'error' as must-fix "
    "and 'review' as a question to put to her. After saving, tell her the script is in the app under 我的脚本."
)


class ToolError(Exception):
    """Bad arguments or a failed step; the message is shown to the model."""


def _text(args: Dict[str, Any], key: str, required: bool = False, default: str = "") -> str:
    value = args.get(key, default)
    if not isinstance(value, str):
        raise ToolError("'%s' must be a string" % key)
    if required and not value.strip():
        raise ToolError("'%s' is required" % key)
    return value


def _int(args: Dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("'%s' must be a number" % key)
    return max(low, min(int(value), high))


def _obj(args: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = args.get(key)
    if isinstance(value, str):  # some clients pass nested JSON as a string
        try:
            value = llm.parse_json_text(value)
        except llm.LLMError:
            raise ToolError("'%s' must be a JSON object" % key)
    if not isinstance(value, dict):
        raise ToolError("'%s' must be a JSON object" % key)
    return value


def _lines(args: Dict[str, Any], key: str = "script_lines") -> List[Any]:
    value = args.get(key)
    if not isinstance(value, list) or not value:
        raise ToolError("'%s' must be a non-empty array of line objects" % key)
    return value


def _format(args: Dict[str, Any]) -> str:
    value = args.get("video_format") or pipeline.VIDEO_FORMATS[0]
    if value not in pipeline.VIDEO_FORMATS:
        raise ToolError("'video_format' must be one of: %s" % ", ".join(pipeline.VIDEO_FORMATS))
    return value


def _script(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    script = store.get_script(_text(args, "script_id", required=True).strip())
    if script is None:
        raise ToolError("No script with that script_id; call list_scripts to see ids")
    return script


def _checked(store: Store, script: Dict[str, Any]) -> Dict[str, Any]:
    check = checks.lint_payload(store, script["lines"], script["target_sec"])
    errors = sum(1 for f in check["findings"] if f["severity"] == "error")
    return {
        "script_id": script["id"], "title": script["title"], "target_duration_sec": script["target_sec"],
        "line_count": len(script["lines"]), "check": check,
        "next_step": ("Fix the %d error finding(s) and call update_script with the full corrected script_lines."
                      % errors) if errors else "No must-fix problems. Tell her it is saved in the app under 我的脚本.",
    }


# ---- tools ------------------------------------------------------------------
def t_get_script_brief(store: Store, args: Dict[str, Any]) -> str:
    system, user = pipeline.draft_prompt(
        store, notes=_text(args, "notes", required=True), video_format=_format(args),
        target_sec=_int(args, "target_duration_sec", 60, 5, 900))
    return ("%s\n\n%s\n\nNEXT STEP: do not show this JSON to her. Call save_script with `script` set to the JSON "
            "object, and the same notes, target_duration_sec and video_format.") % (system.strip(), user.strip())


def t_save_script(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    script = pipeline.apply_draft(
        store, _obj(args, "script"), notes=_text(args, "notes"), video_format=_format(args),
        target_sec=_int(args, "target_duration_sec", 60, 5, 900))
    return _checked(store, script)


def t_update_script(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    script = _script(store, args)
    changes: Dict[str, Any] = {"lines": _lines(args)}
    if _text(args, "title").strip():
        changes["title"] = _text(args, "title")
    return _checked(store, store.update_script(script["id"], changes))  # type: ignore[arg-type]


def t_check_script(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    return checks.lint_payload(store, _lines(args), _int(args, "target_duration_sec", 60, 5, 900))


def t_list_scripts(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"scripts": store.list_scripts()}


def t_get_script(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    script = _script(store, args)
    return {"script": script, "check": checks.lint_payload(store, script["lines"], script["target_sec"])}


def t_get_voice_profile(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    dna, is_example = store.load_dna()
    note = ("This is the bundled EXAMPLE profile, not hers. Suggest learning her voice first "
            "(get_voice_learning_brief).") if is_example else "This is her own learned profile."
    return {"is_example": is_example, "note": note, "profile": dna}


def t_list_library(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    samples = store.list_samples()
    return {
        "note": "Her own past scripts. Numbers were typed in by her; null means not entered.",
        "performance": corpus.performance(samples),
        "scripts": [{"title": s["title"], "is_sponsored": s["is_sponsored"], "characters": len(s["text"]),
                     "views": s["views"], "likes": s["likes"], "collects": s["collects"], "comments": s["comments"]}
                    for s in samples],
    }


def t_get_voice_learning_brief(store: Store, args: Dict[str, Any]) -> str:
    system, user = pipeline.voice_prompt(store, _int(args, "max_corpus_chars", MCP_CORPUS_CHARS, 2000, 200000))
    return ("%s\n\n%s\n\nNEXT STEP: do not show this JSON to her. Call save_voice_profile with `profile` set to "
            "the JSON object.") % (system.strip(), user.strip())


def t_save_voice_profile(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    dna = pipeline.apply_voice(store, _obj(args, "profile"), MCP_CORPUS_CHARS)
    return {
        "saved": True, "cadence_and_syntax": dna["cadence_and_syntax"], "source": dna["source_corpus_summary"],
        "note": "Clause lengths were replaced with values measured from her scripts. Speaking rate cannot be "
                "measured from text; unless 'speaking_rate_confirmed' is true it is a default she should correct "
                "in the app under 我的声音.",
    }


def t_get_topic_ideas_brief(store: Store, args: Dict[str, Any]) -> str:
    system, user = pipeline.ideas_prompt(store, _int(args, "idea_count", 6, 1, 12))
    return ("%s\n\n%s\n\nNEXT STEP: call save_topic_ideas with `ideas` set to the JSON object, then present the "
            "ideas to her and offer to draft one with get_script_brief.") % (system.strip(), user.strip())


def t_save_topic_ideas(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    return pipeline.apply_ideas(store, _obj(args, "ideas"))


def t_export_jianying_kit(store: Store, args: Dict[str, Any]) -> Dict[str, Any]:
    script = _script(store, args)
    dna, _ = store.load_dna()
    lang = "en" if args.get("language") == "en" else "zh"
    filename, data, _ctype = exports.export_file(script, "jianying", dna, lang)
    folder = store.data_dir / "exports"
    folder.mkdir(exist_ok=True)
    target = folder / re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", filename)
    target.write_bytes(data)
    return {
        "saved_to": str(target),
        "contains": "Subtitle .srt, on-screen text .srt (if any), voiceover text for 文稿匹配, shot list, instructions.",
        "caveat": "Subtitle timing is estimated from her speaking rate, not from a recording. Tell her to use "
                  "文稿匹配 in 剪映 for subtitles aligned to her audio, or adjust the imported subtitles by hand.",
    }


_FORMAT_PROP = {"type": "string", "enum": list(pipeline.VIDEO_FORMATS), "default": pipeline.VIDEO_FORMATS[0],
                "description": "VLOG_VOICEOVER daily vlog voiceover; COUPLE_VLOG couple vlog; FAVORITES_ROUNDUP "
                               "several products; SINGLE_PRODUCT_SHARE one product."}
_DURATION_PROP = {"type": "integer", "default": 60, "description": "Target video length in seconds, usually 30, 60 or 90."}
_ID_PROP = {"type": "string", "description": "Script id from save_script or list_scripts."}
_LINES_PROP = {"type": "array", "items": {"type": "object"},
               "description": "Full list of line objects in the script_lines shape from get_script_brief. "
                              "Each needs at least spoken_script_zh."}


def _tool(name: str, fn: Callable[[Store, Dict[str, Any]], Any], description: str, properties: Dict[str, Any],
          required: List[str], read_only: bool, destructive: bool = False) -> Dict[str, Any]:
    annotations: Dict[str, Any] = {"readOnlyHint": read_only, "openWorldHint": False}
    if not read_only:
        annotations["destructiveHint"] = destructive
    return {"fn": fn, "public": {
        "name": name, "description": description, "annotations": annotations,
        "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    }}


TOOLS: Dict[str, Dict[str, Any]] = {t["public"]["name"]: t for t in [
    _tool("get_script_brief", t_get_script_brief,
          "Use this when she wants a new video script written. Returns her voice profile, her own past scripts as "
          "examples, the writing rules and the exact JSON shape to produce. Always call this before writing a script; "
          "do not write one from memory.",
          {"notes": {"type": "string", "description": "Her idea or rough notes, in Chinese or English."},
           "target_duration_sec": _DURATION_PROP, "video_format": _FORMAT_PROP}, ["notes"], read_only=True),
    _tool("save_script", t_save_script,
          "Use this right after writing a script from get_script_brief. Saves it as a new script in her app and "
          "returns deterministic checks (length, her banned phrases, clichés, ad-rule wording).",
          {"script": {"type": "object", "description": "The JSON object produced from the brief (with script_lines)."},
           "notes": {"type": "string", "description": "The same notes given to get_script_brief."},
           "target_duration_sec": _DURATION_PROP, "video_format": _FORMAT_PROP}, ["script"], read_only=False),
    _tool("update_script", t_update_script,
          "Use this to fix or revise a saved script. Replaces all of its lines with the ones given, so always send "
          "the complete list, then returns fresh checks.",
          {"script_id": _ID_PROP, "script_lines": _LINES_PROP,
           "title": {"type": "string", "description": "Optional new title."}},
          ["script_id", "script_lines"], read_only=False, destructive=True),
    _tool("check_script", t_check_script,
          "Use this to check script lines against her voice profile without saving anything.",
          {"script_lines": _LINES_PROP, "target_duration_sec": _DURATION_PROP}, ["script_lines"], read_only=True),
    _tool("list_scripts", t_list_scripts, "Use this to see the scripts saved in her app, newest first.",
          {}, [], read_only=True),
    _tool("get_script", t_get_script, "Use this to read one saved script in full, with its current checks.",
          {"script_id": _ID_PROP}, ["script_id"], read_only=True),
    _tool("get_voice_profile", t_get_voice_profile,
          "Use this to read her current voice profile (how she talks, what she never says). Says whether it is still "
          "the bundled example.", {}, [], read_only=True),
    _tool("list_library", t_list_library,
          "Use this to see her past videos with the views, likes, collects and comments she entered, ranked by "
          "interactions. Use it for questions about what performed well.", {}, [], read_only=True),
    _tool("get_voice_learning_brief", t_get_voice_learning_brief,
          "Use this when she wants the tool to learn or refresh her voice from her own past scripts. Returns those "
          "scripts, measured statistics and the JSON shape of a voice profile.",
          {"max_corpus_chars": {"type": "integer", "default": MCP_CORPUS_CHARS,
                                "description": "How much of her script text to include."}}, [], read_only=True),
    _tool("save_voice_profile", t_save_voice_profile,
          "Use this right after building a profile from get_voice_learning_brief. Makes it her active profile; the "
          "previous one is kept as a backup file.",
          {"profile": {"type": "object", "description": "The voice profile JSON object."}}, ["profile"], read_only=False),
    _tool("get_topic_ideas_brief", t_get_topic_ideas_brief,
          "Use this when she asks what to film next. Returns her past videos with their numbers and the JSON shape "
          "for recommendations. Base ideas only on this data.",
          {"idea_count": {"type": "integer", "default": 6, "description": "How many ideas to suggest."}}, [], read_only=True),
    _tool("save_topic_ideas", t_save_topic_ideas,
          "Use this right after producing ideas from get_topic_ideas_brief. Saves them to the 选题建议 page; cited "
          "titles that are not her videos are removed.",
          {"ideas": {"type": "object", "description": "The recommendations JSON object (with an ideas array)."}},
          ["ideas"], read_only=False),
    _tool("export_jianying_kit", t_export_jianying_kit,
          "Use this when she is ready to edit a script's video in 剪映 / CapCut. Writes a zip with subtitle files, "
          "voiceover text and a shot list into the app's data folder and returns its path.",
          {"script_id": _ID_PROP, "language": {"type": "string", "enum": ["zh", "en"], "default": "zh",
                                               "description": "Language of file names and instructions."}},
          ["script_id"], read_only=False),
]}


# ---- json-rpc ---------------------------------------------------------------
def _result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def call_tool(store: Store, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run one tool and return an MCP tool result (never raises)."""
    try:
        output = TOOLS[name]["fn"](store, arguments)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, indent=1)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except (ToolError, llm.LLMError, pipeline.PromptError) as exc:
        return {"content": [{"type": "text", "text": "Error: %s" % exc}], "isError": True}
    except Exception:  # noqa: BLE001 - report to the client, keep the server alive
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": "Error: internal error in tool '%s'" % name}], "isError": True}


def handle(store: Store, message: Any) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    method = message.get("method")
    if not isinstance(method, str) or "id" not in message:
        return None  # a notification, or a response to something we never asked
    msg_id = message["id"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(msg_id, {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [t["public"] for t in TOOLS.values()]})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name not in TOOLS:
            return _error(msg_id, -32602, "Unknown tool: %s" % name)
        if not isinstance(arguments, dict):
            return _error(msg_id, -32602, "Tool arguments must be an object")
        return _result(msg_id, call_tool(store, name, arguments))
    return _error(msg_id, -32601, "Method not found: %s" % method)


def serve(store: Store, stdin: BinaryIO, stdout: BinaryIO) -> None:
    """Read newline-delimited JSON-RPC from stdin until it closes."""
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        reply: Any
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            reply = _error(None, -32700, "Parse error")
        else:
            if isinstance(message, list):
                reply = [r for r in (handle(store, m) for m in message) if r is not None] or None
            else:
                reply = handle(store, message)
        if reply is not None:
            stdout.write(json.dumps(reply).encode("ascii") + b"\n")  # ASCII-escaped: always one line
            stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Learn My Voice MCP server (stdio)")
    parser.add_argument("--data-dir", type=Path, default=None, help="Defaults to ./data next to the app")
    args = parser.parse_args(argv)
    store = Store(args.data_dir)
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must never corrupt the protocol stream
    try:
        serve(store, sys.stdin.buffer, protocol_out)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
