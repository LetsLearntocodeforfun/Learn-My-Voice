"""Leave-one-out voice calibration holdout benchmark against synthetic generic AI templates.

What this module measures, in deterministic Python (no model calls):

- When a store holds at least ``MIN_CALIBRATION_SAMPLES`` (3) confirmed non-sponsored items with spoken breaths,
  each item is held out one at a time and scored against the remaining ``N - 1`` items on:
  1. ``voicematch`` speech-habit counts (breath length span, sentence-final particles, breath-initial spoken
     connectors, and English code-switching); and
  2. ``tone_analyzer`` surface wording rates (colloquial vs. formal bookish connectors, particle density,
     hard-sell hype density, and exclamation rate), compared with the same one-standard-deviation chance
     allowance used in ``voicematch``.
- A fixed bank of synthetic generic AI-slop and marketing-brochure scripts (``SYNTHETIC_SLOP_SCRIPTS``) is
  scored against the same organic reference corpus and checked through ``checks.lint_payload`` (which applies
  the creator's effective word list and never-say phrases).
- Held-out creator items are also checked through ``checks.lint_payload`` to measure ``own_guardrail_clean_rate``
  (the fraction of real creator items that trigger zero ``COMPLIANCE_RISK`` false positives).

To keep ``#/voice`` responsive on multi-year libraries (hundreds of items), at most ``MAX_HOLDOUT_SAMPLES`` (50)
evenly spaced confirmed non-sponsored items are evaluated per holdout pass, and ``capped`` reports when that cap
took effect.

Honesty boundary: these metrics measure surface-habit separation between the creator's confirmed non-sponsored
texts and fixed generic AI/marketing templates. They do NOT evaluate live LLM draft quality, factual accuracy,
regulatory compliance, or whether a human listener prefers a draft.

Usage::

    python3 -m studio.benchmark
    python3 -m studio.benchmark --data-dir data --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import checks, guardrails, pipeline, tone_analyzer, voicematch
from .store import EXAMPLE_DNA_PATH, Store

MIN_CALIBRATION_SAMPLES = 3
CALIBRATED_MIN_SAMPLES = 5
CALIBRATED_MIN_MARGIN = 12.0
CALIBRATED_MIN_SEPARATION = 0.75
MAX_HOLDOUT_SAMPLES = 50
MAX_SHOWN_HOLDOUT_ITEMS = 12

TIER_INSUFFICIENT = "insufficient_samples"
TIER_CALIBRATING = "calibrating"
TIER_CALIBRATED = "calibrated"

_VM_WEIGHT = 0.65
_TONE_WEIGHT = 0.35

_FLAGGED_SLOP_CATEGORIES = frozenset((
    guardrails.COMPLIANCE_RISK,
    guardrails.GENERIC_XHS_SLOP,
    guardrails.BOOKISH_MANDARIN,
    guardrails.TRANSLATIONESE,
))

HONESTY_NOTE = {
    "zh": (
        "这项自检只用留一法比较你已确认的非商单原话与固定通用 AI／营销模板在句长、语气词、连接词、夹英文和词表命中上的字面差异；"
        "它不调用大模型，不代表真实观众的盲测听感，也不能证明脚本合规或一定会火。"
    ),
    "en": (
        "This check runs a deterministic leave-one-out comparison between confirmed non-sponsored material and fixed "
        "generic AI/marketing templates on breath length, particles, connectors, code-switching and word-list hits. "
        "It makes no model calls and does not measure live AI output quality, legal compliance, or human listener preference."
    ),
}

# Fixed bank of generic influencer hype, stiff marketing brochure prose, translationese and compliance-risk templates.
SYNTHETIC_SLOP_SCRIPTS: Tuple[Dict[str, str], ...] = (
    {
        "id": "slop_hype_hard_sell",
        "kind": "hard_sell_cliche",
        "title": "全网最低绝绝子硬广模板",
        "text": (
            "家人们谁懂啊！这款神仙面霜真的绝绝子！\n"
            "全网最低价100%有效，闭眼入不踩雷！\n"
            "赶紧下单冲就完了，买它绝对不后悔！\n"
            "坚持3天见效，一夜回春立竿见影！"
        ),
    },
    {
        "id": "slop_stiff_brochure",
        "kind": "bookish_brochure",
        "title": "书面说明书式公关稿",
        "text": (
            "综上所述，这款精华液不仅能够深层赋能肌底状态，而且吸收表现十分令人印象深刻。\n"
            "与此同时，它的修护抗衰体系也值得一提的是行业天花板水准。\n"
            "毫无疑问，这是每一位精致消费者必不可少的理想之选。\n"
            "让我们一起来看看它的核心成分矩阵与使用步骤吧。"
        ),
    },
    {
        "id": "slop_translationese_pitch",
        "kind": "translationese_pitch",
        "title": "翻译腔痛点带货稿",
        "text": (
            "哈喽大家好我是小美，今天给大家带来一款改变游戏规则的护肤单品！\n"
            "它完美地解决了我在换季干燥期面临的所有肌肤痛点！\n"
            "当涉及到长效保湿的时候，没有任何同类产品能够秒杀它的极致表现！\n"
            "宝子们赶紧点击下方购买或者私信发链接领取专属福利吧！"
        ),
    },
    {
        "id": "slop_fake_codeswitch",
        "kind": "unnatural_codeswitch_hype",
        "title": "生硬中英夹杂种草模板",
        "text": (
            "集美们看过来！这个 weekend 的 morning routine 真的让我 feel 超级 healing 哒！\n"
            "满满的高级感直接拉满，真的是销量第一的宝藏好物捏！\n"
            "按头安利给所有老婆们，无脑入绝对不会出错鸭！\n"
            "开启元气满满的一天就靠这瓶顶级神仙水吼！"
        ),
    },
    {
        "id": "slop_medical_efficacy",
        "kind": "compliance_efficacy_claim",
        "title": "违规功效极限词模板",
        "text": (
            "众所周知，换季泛红敏感一定要用这款国家级配方的修护乳霜！\n"
            "它可以快速消炎杀菌排毒，根治反复长痘和顽固祛斑问题！\n"
            "百分之百温和无刺激，用完效果简直逆天炸裂封神！\n"
            "最后3件限时疯抢，赶紧加微私信领内部最低价！"
        ),
    },
    {
        "id": "slop_robotic_listicle",
        "kind": "formal_ai_listicle",
        "title": "机械总分总清单稿",
        "text": (
            "首先，我们需要对日常家居收纳逻辑进行全面系统的梳理与升级。\n"
            "其次，这款收纳盒在空间利用率方面展现出了卓越非凡的综合水准。\n"
            "此外，其耐用程度更是达到了令人惊叹的极致标准。\n"
            "总而言之，强烈推荐大家闭眼入这款万年回购的必买神器。"
        ),
    },
)

# Bundled self-check corpus matching schemas/voice_dna.example.json for CLI verification (`python3 -m studio.benchmark`).
BUNDLED_EXAMPLE_CORPUS: Tuple[Dict[str, str], ...] = (
    {
        "title": "周末 Costco 补货碎片",
        "text": (
            "上周末跟我老公去 Costco 补货嘛 / 本来只打算买两袋咖啡豆\n"
            "结果某人站在零食货架前面研究了十分钟哈 / 最后还是拿了原味薯片\n"
            "然后我们又顺手抱了四盒小笼包啦 / 其实家里冷冻室还有两盒嘛"
        ),
    },
    {
        "title": "入冬干皮面霜实话",
        "text": (
            "一入冬吹暖气我这张干皮真的服了 / 所以最近洗完澡赶紧厚涂一层嘛\n"
            "推开水水的 / 一点不糊脸啦 / 后续上妆不打架哈\n"
            "唯一的小缺点就是瓶子稍微有点沉 / 出差带不太方便嘛"
        ),
    },
    {
        "title": "周六 Trader Joe's 买花",
        "text": (
            "周六早上去练完 pilates 顺路拐去 Trader Joe's 买束花嘛\n"
            "结果结账的时候看到季节限定南瓜饼干哈 / 我老公眼神一下就亮了\n"
            "不过说实话这款稍微有点甜 / 配黑咖啡刚刚好啦"
        ),
    },
    {
        "title": "穿了三次的软糯开衫",
        "text": (
            "最近评论区问得最多的就是我身上这件开衫啦 / 这周已经穿了三次嘛\n"
            "摸起来软软糯糯的 / 随便抓一件就能出门巨省心哈\n"
            "洗了两次也没变形 / 但如果你怕热室内穿可能会觉得稍微厚一点啦"
        ),
    },
    {
        "title": "周日早餐与某人日常",
        "text": (
            "周日早上趁某人还没醒 / 先给自己煮杯热咖啡嘛\n"
            "然后煎了两个鸡蛋做 brunch / 结果他闻到香味五分钟就出来了哈\n"
            "最搞笑的是他还问今天有没有培根 / 吃完去 gym 走一圈清清爽爽啦"
        ),
    },
    {
        "title": "空瓶第二罐的晚间护肤",
        "text": (
            "这罐晚霜我已经空瓶第二罐了嘛 / 连某人都顺手拿去涂手哈\n"
            "它不是那种一夜惊艳型 / 就是脸摸着软软的很稳啦\n"
            "前两天去 Sephora 顺便补了一罐 / 算下来真的蛮舒服的诶"
        ),
    },
)


def _text_to_lines(text: str) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        cleaned = raw_line.strip()
        if cleaned:
            lines.append({
                "line_no": len(lines) + 1,
                "audio_mode": "VOICEOVER",
                "spoken_script_zh": cleaned,
            })
    return lines


def _lint_texts_batched(store: Store, texts: Sequence[str]) -> Tuple[List[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    """Check ``texts`` via ``checks.lint_payload`` in bounded batches separated by inert ``LIVE_CLIP`` barriers.

    Each item's lines keep unique ``line_no`` indices, separated by an alphanumeric ``LIVE_CLIP`` barrier line so
    cross-line ban matching cannot bridge two distinct items. If a batch ever hits ``checks.MAX_FINDINGS``
    (``findings_left_out > 0``), it splits recursively so zero findings are truncated.
    """
    if not texts:
        empty = checks.lint_payload(store, [], None)
        return [], empty.get("wordlist")

    combined_lines: List[Dict[str, Any]] = []
    owner_by_lineno: Dict[int, int] = {}
    next_no = 1
    for idx, text in enumerate(texts):
        if idx > 0:
            combined_lines.append({
                "line_no": next_no,
                "audio_mode": "LIVE_CLIP",
                "spoken_script_zh": "0000000000",
            })
            next_no += 1
        item_lines = _text_to_lines(text)
        for line in item_lines:
            owner_by_lineno[next_no] = idx
            combined_lines.append({
                "line_no": next_no,
                "audio_mode": "VOICEOVER",
                "spoken_script_zh": line["spoken_script_zh"],
            })
            next_no += 1

    lint_out = checks.lint_payload(store, combined_lines, None)
    if (lint_out.get("findings_left_out") or 0) > 0 and len(texts) > 1:
        mid = len(texts) // 2
        left_findings, wl_notice = _lint_texts_batched(store, texts[:mid])
        right_findings, _ = _lint_texts_batched(store, texts[mid:])
        return left_findings + right_findings, wl_notice

    grouped: List[List[Dict[str, Any]]] = [[] for _ in texts]
    for finding in lint_out.get("findings") or []:
        lno = finding.get("line_no")
        if isinstance(lno, int) and lno in owner_by_lineno:
            grouped[owner_by_lineno[lno]].append(finding)
    return grouped, lint_out.get("wordlist")


def _tone_counts(ling: Dict[str, Any]) -> Dict[str, int]:
    return {
        "breaths": int(ling.get("breaths") or 0),
        "particle": int((ling.get("particles") or {}).get("particle_breaths") or 0),
        "colloquial": int((ling.get("connectors") or {}).get("colloquial_breaths") or 0),
        "bookish": int((ling.get("connectors") or {}).get("bookish_breaths") or 0),
        "hard_sell": int((ling.get("hard_sell") or {}).get("count") or 0),
        "exclamation": int((ling.get("punctuation") or {}).get("exclamation_breaths") or 0),
    }


def _add_tone_counts(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    return {key: a[key] + b[key] for key in a}


def _sub_tone_counts(whole: Dict[str, int], part: Dict[str, int]) -> Dict[str, int]:
    return {key: max(0, whole[key] - part[key]) for key in whole}


def _tone_similarity(cand: Dict[str, int], ref: Dict[str, int]) -> float:
    """0..100 surface-tone closeness across five counted wording rates."""
    cand_b = cand["breaths"]
    ref_b = ref["breaths"]
    if cand_b <= 0 or ref_b <= 0:
        return 0.0
    keys = ("particle", "colloquial", "bookish", "hard_sell", "exclamation")
    parts = [
        voicematch._closeness(
            min(1.0, cand[k] / cand_b),
            min(1.0, ref[k] / ref_b),
            cand_b,
        )
        for k in keys
    ]
    return round(100.0 * sum(parts) / len(parts), 1)


def _voicematch_similarity(cand: voicematch.Counts, ref: voicematch.Counts) -> float:
    """0..100 four-habit ``voicematch`` score against ``ref``."""
    if cand.breaths <= 0 or ref.breaths <= 0:
        return 0.0
    parts = (
        voicematch._breath(cand, ref)["score"],
        voicematch._habit(cand, ref, "endings")["score"],
        voicematch._habit(cand, ref, "starts")["score"],
        voicematch._english(cand, ref)["score"],
    )
    return round(100.0 * sum(parts) / len(parts), 1)


def _combined_score(vm_score: float, tone_score: float) -> float:
    return round(_VM_WEIGHT * vm_score + _TONE_WEIGHT * tone_score, 1)


def _band(scores: Sequence[float]) -> Optional[Dict[str, float]]:
    if not scores:
        return None
    ordered = sorted(scores)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 == 1 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "low": round(ordered[0], 1),
        "median": round(median, 1),
        "high": round(ordered[-1], 1),
    }


def _select_bounded_samples(eligible: List[Dict[str, Any]], max_samples: int) -> Tuple[List[Dict[str, Any]], bool]:
    cap = max(MIN_CALIBRATION_SAMPLES, int(max_samples))
    if len(eligible) <= cap:
        return eligible, False
    indices = [int(i * len(eligible) / cap) for i in range(cap)]
    return [eligible[idx] for idx in indices], True


def _cache_signature(store: Store, max_samples: int) -> Tuple[Any, ...]:
    dna, is_example = store.load_dna()
    who = store.load_creator()
    cfg = store.load_config()
    blob = json.dumps(
        [is_example, dna, who, cfg.get("wordlist")],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8", "replace")
    digest = hashlib.sha256(blob).hexdigest()[:16]
    return (store.samples_version(), digest, int(max_samples))


def evaluate_store(store: Store, *, max_samples: int = MAX_HOLDOUT_SAMPLES) -> Dict[str, Any]:
    """Run deterministic leave-one-out voice calibration on ``store``."""
    sig = _cache_signature(store, max_samples)
    cached: Optional[Tuple[Tuple[Any, ...], Dict[str, Any]]] = getattr(store, "_voice_calibration", None)
    if cached is not None and cached[0] == sig:
        return json.loads(json.dumps(cached[1], ensure_ascii=False))

    _, is_example = store.load_dna()
    all_samples = store.list_samples()
    confirmed_organic = [
        s for s in pipeline.confirmed_samples(all_samples)
        if not s.get("is_sponsored") and isinstance(s.get("text"), str) and s["text"].strip()
    ]

    # Only items with at least one spoken breath can participate in speech-habit counting.
    eligible: List[Tuple[Dict[str, Any], voicematch.Counts]] = []
    for sample in confirmed_organic:
        cnt = voicematch.Counts().add_text(sample["text"])
        if cnt.breaths > 0:
            eligible.append((sample, cnt))

    total_eligible = len(eligible)

    # Evaluate guardrail detection on synthetic slop bank via checks.lint_payload (always respects effective word list).
    slop_findings_list, wordlist_notice = _lint_texts_batched(store, [item["text"] for item in SYNTHETIC_SLOP_SCRIPTS])
    slop_lints: List[Tuple[Dict[str, str], voicematch.Counts, Dict[str, int], List[Dict[str, Any]], bool]] = []
    for item, item_findings in zip(SYNTHETIC_SLOP_SCRIPTS, slop_findings_list):
        flagged = any(f.get("category") in _FLAGGED_SLOP_CATEGORIES for f in item_findings)
        s_counts = voicematch.Counts().add_text(item["text"])
        s_tone = _tone_counts(tone_analyzer.analyze_text_linguistics([item["text"]]))
        slop_lints.append((item, s_counts, s_tone, item_findings, flagged))

    slop_flagged_count = sum(1 for _, _, _, _, flagged in slop_lints if flagged)
    slop_flagged_rate = round(slop_flagged_count / len(slop_lints), 4) if slop_lints else 1.0

    if total_eligible < MIN_CALIBRATION_SAMPLES:
        total_breaths = sum(cnt.breaths for _, cnt in eligible)
        result: Dict[str, Any] = {
            "readiness_tier": TIER_INSUFFICIENT,
            "organic_samples_total": total_eligible,
            "samples_evaluated": 0,
            "sample_cap": max(MIN_CALIBRATION_SAMPLES, int(max_samples)),
            "capped": False,
            "min_samples_required": MIN_CALIBRATION_SAMPLES,
            "calibrated_samples_target": CALIBRATED_MIN_SAMPLES,
            "margin_target": CALIBRATED_MIN_MARGIN,
            "dna_is_example": is_example,
            "reference_breaths": total_breaths,
            "reference_breaths_sufficient": total_breaths >= voicematch.MIN_REFERENCE_BREATHS,
            "synthetic_slop_count": len(slop_lints),
            "own_mean_score": None,
            "slop_mean_score": None,
            "margin": None,
            "pairwise_separation_rate": None,
            "own_guardrail_clean_rate": None,
            "own_compliance_flagged_count": 0,
            "slop_flagged_rate": slop_flagged_rate,
            "slop_flagged_count": slop_flagged_count,
            "own_score_band": None,
            "slop_score_band": None,
            "holdout_samples": [],
            "wordlist": wordlist_notice,
            "honesty_note": dict(HONESTY_NOTE),
        }
        store._voice_calibration = (sig, result)
        return json.loads(json.dumps(result, ensure_ascii=False))

    selected_samples, capped = _select_bounded_samples([s for s, _ in eligible], max_samples)
    selected_ids = {s["id"] for s in selected_samples}
    counts_by_id = {s["id"]: cnt for s, cnt in eligible if s["id"] in selected_ids}

    item_records: List[Tuple[Dict[str, Any], voicematch.Counts, Dict[str, int]]] = []
    whole_vm = voicematch.Counts()
    whole_tone = {"breaths": 0, "particle": 0, "colloquial": 0, "bookish": 0, "hard_sell": 0, "exclamation": 0}

    for sample in selected_samples:
        vm_cnt = counts_by_id[sample["id"]]
        t_cnt = _tone_counts(tone_analyzer.analyze_text_linguistics([sample["text"]]))
        whole_vm = whole_vm.plus(vm_cnt)
        whole_tone = _add_tone_counts(whole_tone, t_cnt)
        item_records.append((sample, vm_cnt, t_cnt))

    own_scores: List[float] = []
    holdout_rows: List[Dict[str, Any]] = []
    own_clean_count = 0
    own_findings_list, _ = _lint_texts_batched(store, [sample["text"] for sample, _, _ in item_records])

    for (sample, vm_cnt, t_cnt), sample_findings in zip(item_records, own_findings_list):
        rest_vm = whole_vm.without(vm_cnt)
        rest_tone = _sub_tone_counts(whole_tone, t_cnt)
        vm_s = _voicematch_similarity(vm_cnt, rest_vm)
        tone_s = _tone_similarity(t_cnt, rest_tone)
        combined = _combined_score(vm_s, tone_s)
        own_scores.append(combined)

        comp_matches = [
            str(f.get("matched") or "")
            for f in sample_findings
            if f.get("category") == guardrails.COMPLIANCE_RISK
        ]
        is_clean = len(comp_matches) == 0
        if is_clean:
            own_clean_count += 1
        holdout_rows.append({
            "id": sample["id"],
            "title": str(sample.get("title") or "")[:120],
            "breaths": vm_cnt.breaths,
            "score": combined,
            "voicematch_score": vm_s,
            "tone_similarity": tone_s,
            "compliance_clean": is_clean,
            "compliance_matches": comp_matches[:5],
        })

    slop_scores: List[float] = []
    for _item, s_vm, s_tone, _lint_out, _flagged in slop_lints:
        s_vm_score = _voicematch_similarity(s_vm, whole_vm)
        s_tone_score = _tone_similarity(s_tone, whole_tone)
        slop_scores.append(_combined_score(s_vm_score, s_tone_score))

    own_mean = round(sum(own_scores) / len(own_scores), 1)
    slop_mean = round(sum(slop_scores) / len(slop_scores), 1) if slop_scores else 0.0
    margin = round(own_mean - slop_mean, 1)

    total_pairs = len(own_scores) * len(slop_scores)
    wins = sum(1 for o_val in own_scores for s_val in slop_scores if o_val > s_val)
    pairwise_sep = round(wins / total_pairs, 4) if total_pairs else 1.0
    own_clean_rate = round(own_clean_count / len(item_records), 4)

    if (
        total_eligible >= CALIBRATED_MIN_SAMPLES
        and margin >= CALIBRATED_MIN_MARGIN
        and pairwise_sep >= CALIBRATED_MIN_SEPARATION
    ):
        tier = TIER_CALIBRATED
    else:
        tier = TIER_CALIBRATING

    result = {
        "readiness_tier": tier,
        "organic_samples_total": total_eligible,
        "samples_evaluated": len(item_records),
        "sample_cap": max(MIN_CALIBRATION_SAMPLES, int(max_samples)),
        "capped": capped,
        "min_samples_required": MIN_CALIBRATION_SAMPLES,
        "calibrated_samples_target": CALIBRATED_MIN_SAMPLES,
        "margin_target": CALIBRATED_MIN_MARGIN,
        "dna_is_example": is_example,
        "reference_breaths": whole_vm.breaths,
        "reference_breaths_sufficient": whole_vm.breaths >= voicematch.MIN_REFERENCE_BREATHS,
        "synthetic_slop_count": len(slop_lints),
        "own_mean_score": own_mean,
        "slop_mean_score": slop_mean,
        "margin": margin,
        "pairwise_separation_rate": pairwise_sep,
        "own_guardrail_clean_rate": own_clean_rate,
        "own_compliance_flagged_count": len(item_records) - own_clean_count,
        "slop_flagged_rate": slop_flagged_rate,
        "slop_flagged_count": slop_flagged_count,
        "own_score_band": _band(own_scores),
        "slop_score_band": _band(slop_scores),
        "holdout_samples": holdout_rows[:MAX_SHOWN_HOLDOUT_ITEMS],
        "wordlist": wordlist_notice,
        "honesty_note": dict(HONESTY_NOTE),
    }
    store._voice_calibration = (sig, result)
    return json.loads(json.dumps(result, ensure_ascii=False))


def run_bundled_benchmark(corpus_items: Iterable[Dict[str, str]] = BUNDLED_EXAMPLE_CORPUS) -> Dict[str, Any]:
    """Run the leave-one-out holdout benchmark on the bundled example profile and corpus in a throwaway folder."""
    tmp = Path(tempfile.mkdtemp(prefix="lmv-bench-"))
    try:
        store = Store(tmp / "data")
        example_dna = json.loads(EXAMPLE_DNA_PATH.read_text(encoding="utf-8"))
        store.save_dna(example_dna)
        for item in corpus_items:
            store.create_sample(title=item["title"], text=item["text"], is_sponsored=False)
        report = evaluate_store(store)
        report["bundled_self_check"] = True
        return report
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic leave-one-out voice calibration holdout benchmark."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Evaluate an existing studio data folder (runs bundled example corpus when omitted).",
    )
    parser.add_argument("--max-samples", type=int, default=MAX_HOLDOUT_SAMPLES, help="Maximum organic samples evaluated.")
    parser.add_argument("--json", action="store_true", help="Emit full calibration report as JSON.")
    args = parser.parse_args(argv)

    if args.data_dir is not None:
        report = evaluate_store(Store(args.data_dir), max_samples=args.max_samples)
    else:
        report = run_bundled_benchmark()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Voice calibration tier : %s" % report["readiness_tier"])
        print("Confirmed organic items: %d (evaluated: %d%s, breaths: %d)" % (
            report["organic_samples_total"],
            report["samples_evaluated"],
            ", capped" if report["capped"] else "",
            report["reference_breaths"],
        ))
        if report["own_mean_score"] is not None:
            print("Held-out own mean      : %.1f / 100" % report["own_mean_score"])
            print("Synthetic AI-slop mean : %.1f / 100 (margin: %+.1f)" % (
                report["slop_mean_score"], report["margin"]
            ))
            print("Pairwise separation    : %.1f%%" % (100.0 * report["pairwise_separation_rate"]))
            print("Own compliance-clean   : %.1f%%" % (100.0 * report["own_guardrail_clean_rate"]))
        print("Synthetic slop flagged : %.1f%% (%d/%d)" % (
            100.0 * report["slop_flagged_rate"],
            report["slop_flagged_count"],
            report["synthetic_slop_count"],
        ))
        print("Note: " + report["honesty_note"]["en"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
