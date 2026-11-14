"""Guardrail results phrased for people, in Chinese and English."""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import guardrails, textstats
from .store import Store

_MESSAGES = {
    (guardrails.CADENCE_TOO_LONG, "error"): ("这一句有 {n} 个字，一口气念不完，建议断开", "This clause is {n} units; too long for one breath"),
    (guardrails.GENERIC_XHS_SLOP, "error"): ("「{m}」是营销号常用说法，不像你的口吻", "\"{m}\" is generic influencer wording, not her voice"),
    (guardrails.GENERIC_XHS_SLOP, "review"): ("「{m}」你平时很少这么说，确认一下", "\"{m}\" is not something she usually says; please check"),
    (guardrails.BOOKISH_MANDARIN, "error"): ("「{m}」太书面了，口语里不这么说", "\"{m}\" is written-style Chinese, not spoken"),
    (guardrails.TRANSLATIONESE, "error"): ("「{m}」有点翻译腔", "\"{m}\" reads like translated English"),
    (guardrails.TRANSLATIONESE, "review"): ("「{m}」偏书面，确认一下", "\"{m}\" may be too formal; please check"),
    (guardrails.UNNATURAL_CODESWITCH, "review"): ("英文词「{m}」你平时不常夹在中文里，确认一下", "English word \"{m}\" is not in her usual code-switching; please check"),
    (guardrails.COMPLIANCE_RISK, "error"): ("「{m}」可能违规（极限词或功效词），建议换个说法", "\"{m}\" may break ad rules (absolute or efficacy claim)"),
    (guardrails.COMPLIANCE_RISK, "review"): ("「{m}」可能被当成极限词，确认一下", "\"{m}\" may be read as an absolute claim; please check"),
}


def describe(finding: guardrails.Finding, stats: Dict[str, Any]) -> Dict[str, Any]:
    if finding.category == guardrails.DURATION_OFF_BUDGET:
        over = stats["budget"] is not None and stats["units"] > stats["budget"]
        zh = "全篇 %s 字，这个时长大约念 %s 字，%s" % (stats["units"], stats["budget"], "偏长了" if over else "偏短了")
        en = "%s units against a budget of about %s: %s" % (stats["units"], stats["budget"], "too long" if over else "too short")
    else:
        zh, en = _MESSAGES.get((finding.category, finding.severity), ("「{m}」需要确认", "\"{m}\" needs a check"))
        n = textstats.spoken_units(finding.matched)
        zh, en = zh.format(m=finding.matched, n=n), en.format(m=finding.matched, n=n)
    return {"line_no": finding.line_no, "category": finding.category, "severity": finding.severity,
            "matched": finding.matched, "message_zh": zh, "message_en": en}


def lint_payload(store: Store, lines: Any, target_sec: Optional[float]) -> Dict[str, Any]:
    """Stats plus bilingual findings for a list of script lines, against the active voice profile."""
    dna, is_example = store.load_dna()
    script = {"script_lines": lines if isinstance(lines, list) else []}
    stats = guardrails.script_stats(script, dna, target_sec)
    findings = guardrails.lint_script(script, dna, target_duration_sec=target_sec)
    return {"stats": stats, "findings": [describe(f, stats) for f in findings], "dna_is_example": is_example}
