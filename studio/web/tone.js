'use strict';
/* On the voice page (#/voice): counted wording habits from confirmed non-sponsored material and measured sound
   from non-sponsored recordings (studio/tone_analyzer.py, studio/soundstats.py). No personality scores. */

Object.assign(I18N.zh, {
  pl_tone_title: '语气与声音实测统计',
  pl_tone_sub: '只统计你已确认的非商单文字（{n} 篇，{b} 口气）和真实录音测量值；全部是字面计数或物理量，不是 AI 评分。',
  pl_tone_sub_sound_only: '素材库还没有已确认的非商单文字；以下来自非商单字幕或录音的时间戳与波形测量。',
  pl_tone_empty: '还没有可统计的内容。导入并确认非商单脚本或转写后，这里会列出语气词、连接词、叠词等计数；转写视频后还会列出停顿、响度跨度与音高估计。',
  pl_tone_thin: '当前只有 {b} 口气已确认的非商单文字（满 {need} 口气后才会在编辑器里与新脚本对照）。',
  pl_tone_breath: '每口气长度',
  pl_tone_breath_v: '大多 {p5}–{p95} 字（中间值 {med} 字）',
  pl_tone_particles: '句尾语气词与尾词',
  pl_tone_connectors_c: '口语连接词',
  pl_tone_connectors_b: '书面连接词',
  pl_tone_redup: '叠词（AABB / AA的）',
  pl_tone_hedges: '留白与限定说法',
  pl_tone_hardsell: '常见硬广夸张词',
  pl_tone_punct: '问号与感叹号',
  pl_tone_punct_v: '问句占 {q}% 气口 · 感叹占 {e}% 气口',
  pl_tone_rate_breaths: '占 {pct}% 气口',
  pl_tone_per100: '每百口气 {n} 处',
  pl_tone_range: '（单篇范围 {low}–{high}，共 {s} 篇）',
  pl_tone_none_found: '0 处',
  pl_tone_pace: '实测语速',
  pl_tone_pace_v: '{cpm} 字/分钟（{v} 条有时间戳素材，共 {sec} 秒口播）',
  pl_tone_pauses: '每百字停顿（≥0.3 秒）',
  pl_tone_pauses_v: '{n} 次',
  pl_tone_loudness: '录音响度跨度',
  pl_tone_loudness_v: '中间值 {med} dB（范围 {low}–{high} dB，{n} 条录音）',
  pl_tone_pitch: '声音基频估计（音高）',
  pl_tone_pitch_v: '中间值 {med} Hz（{low}–{high} Hz，起伏跨度中间值 {span} 半音，{n} 条录音）',
  pl_tone_cut_short: '注：有 {n} 条录音超过 10 分钟，只测量了前 10 分钟。',
  pl_tone_sound_note: '录音测量无法把背景音乐、环境声或另一位说话人与你的声音分开；词表为本项目固定整理，不代表脚本好坏。',
});

Object.assign(I18N.en, {
  pl_tone_title: 'Counted wording & measured sound',
  pl_tone_sub: 'Counted from {n} confirmed non-sponsored item(s) ({b} breaths) that {@name} wrote or confirmed, plus sound measurements from recordings. Plain counts and physical units only, never an AI score.',
  pl_tone_sub_sound_only: 'No confirmed non-sponsored text yet; showing timestamp and waveform measurements from non-sponsored recordings.',
  pl_tone_empty: 'Nothing to count yet. Once {@name} adds and confirms non-sponsored scripts or transcripts, particle, connector and reduplication counts appear here; transcribing a video adds pause, loudness and pitch measurements.',
  pl_tone_thin: 'Only {b} breath(s) of confirmed non-sponsored text so far (at least {need} are needed before scripts are compared in the editor).',
  pl_tone_breath: 'Breath length',
  pl_tone_breath_v: 'Mostly {p5}–{p95} units (median {med})',
  pl_tone_particles: 'Sentence-final particles & tags',
  pl_tone_connectors_c: 'Spoken connectors',
  pl_tone_connectors_b: 'Formal written connectors',
  pl_tone_redup: 'Reduplicated words',
  pl_tone_hedges: 'Hedges & caveats',
  pl_tone_hardsell: 'Hard-sell phrases',
  pl_tone_punct: 'Questions & exclamations',
  pl_tone_punct_v: 'Questions in {q}% of breaths · exclamations in {e}%',
  pl_tone_rate_breaths: '{pct}% of breaths',
  pl_tone_per100: '{n} per 100 breaths',
  pl_tone_range: ' ({@names} scripts: {low}–{high} across {s})',
  pl_tone_none_found: '0 found',
  pl_tone_pace: 'Measured speaking pace',
  pl_tone_pace_v: '{cpm} units/min ({v} timed item(s), {sec}s of speech)',
  pl_tone_pauses: 'Pauses per 100 units (≥0.3s)',
  pl_tone_pauses_v: '{n}',
  pl_tone_loudness: 'Loudness range',
  pl_tone_loudness_v: 'Median {med} dB ({low}–{high} dB across {n} recording(s))',
  pl_tone_pitch: 'Estimated voice pitch',
  pl_tone_pitch_v: 'Median {med} Hz ({low}–{high} Hz, span median {span} semitones across {n} recording(s))',
  pl_tone_cut_short: 'Note: {n} recording(s) exceeded 10 minutes; only the first 10 minutes were measured.',
  pl_tone_sound_note: 'Sound measurements cannot separate background music, noise or another speaker from {@names} voice. Word lists are this project\'s own fixed lists and say nothing about whether a script is good.',
});

function plTagCountsHtml(countsObj, cls) {
  const entries = Object.entries(countsObj || {});
  if (!entries.length) return '';
  return ' ' + entries.map(([word, count]) => `<span class="tag ${cls || ''}">${esc(word)} ×${esc(count)}</span>`).join(' ');
}

function plRangeText(rangeObj) {
  if (!rangeObj || !rangeObj.scripts) return '';
  return tf('pl_tone_range', { low: rangeObj.low, high: rangeObj.high, s: rangeObj.scripts });
}

async function plPaintToneCard(box) {
  if (!box) return;
  let report;
  try { report = await api('GET', '/api/tone'); } catch (e) { return; }
  if (!box.isConnected) return;
  const ling = report.linguistics || {};
  const ranges = report.reference_ranges || {};
  const sound = report.sound || {};
  const measuredRate = report.measured_speaking_rate;
  const hasText = (ling.breaths || 0) > 0;
  const hasDelivery = Boolean(measuredRate || report.pauses_per_100_units !== null || (sound && sound.recordings > 0));

  if (!hasText && !hasDelivery) {
    box.innerHTML = `<h2 class="sec-h">${esc(t('pl_tone_title'))}</h2>
      <div class="card" id="tone-analyzer-card"><p class="hint" style="margin:0">${esc(t('pl_tone_empty'))}</p></div>`;
    return;
  }

  const rows = [];
  const addRow = (labelKey, bodyHtml) => {
    if (bodyHtml) rows.push(`<dt>${esc(t(labelKey))}</dt><dd>${bodyHtml}</dd>`);
  };

  if (hasText) {
    const bl = ling.breath_length || {};
    addRow('pl_tone_breath', esc(tf('pl_tone_breath_v', { p5: bl.p5, p95: bl.p95, med: bl.median }) + plRangeText(ranges.breath_median_units)));

    const p = ling.particles || {};
    const pTags = plTagCountsHtml(p.multi, '') + plTagCountsHtml(p.single, '');
    addRow('pl_tone_particles', esc(tf('pl_tone_rate_breaths', { pct: p.particle_rate_pct }) + plRangeText(ranges.particle_rate_pct)) + pTags);

    const c = ling.connectors || {};
    addRow('pl_tone_connectors_c', esc(tf('pl_tone_rate_breaths', { pct: c.colloquial_rate_pct }) + plRangeText(ranges.colloquial_rate_pct)) + plTagCountsHtml(c.colloquial, ''));
    addRow('pl_tone_connectors_b', esc(tf('pl_tone_rate_breaths', { pct: c.bookish_rate_pct }) + plRangeText(ranges.bookish_rate_pct)) + plTagCountsHtml(c.bookish, 'warn'));

    const red = ling.reduplications || {};
    addRow('pl_tone_redup', esc(tf('pl_tone_per100', { n: red.per_100_breaths }) + plRangeText(ranges.reduplications_per_100_breaths)) + plTagCountsHtml(red.words, ''));

    const hd = ling.hedges || {};
    addRow('pl_tone_hedges', esc(tf('pl_tone_per100', { n: hd.per_100_breaths }) + plRangeText(ranges.hedges_per_100_breaths)) + plTagCountsHtml(hd.phrases, ''));

    const hs = ling.hard_sell || {};
    addRow('pl_tone_hardsell', esc(hs.count ? (tf('pl_tone_per100', { n: hs.per_100_breaths }) + plRangeText(ranges.hard_sell_per_100_breaths)) : t('pl_tone_none_found')) + plTagCountsHtml(hs.phrases, 'warn'));

    const pu = ling.punctuation || {};
    addRow('pl_tone_punct', esc(tf('pl_tone_punct_v', { q: pu.question_rate_pct, e: pu.exclamation_rate_pct })));
  }

  if (measuredRate) {
    addRow('pl_tone_pace', esc(tf('pl_tone_pace_v', { cpm: measuredRate.speaking_rate_cpm, v: measuredRate.videos, sec: Math.round(measuredRate.spoken_sec) })));
  }
  if (report.pauses_per_100_units !== null && report.pauses_per_100_units !== undefined) {
    addRow('pl_tone_pauses', esc(tf('pl_tone_pauses_v', { n: report.pauses_per_100_units })));
  }
  if (sound && sound.loudness_range_db) {
    const lr = sound.loudness_range_db;
    addRow('pl_tone_loudness', esc(tf('pl_tone_loudness_v', { med: lr.median, low: lr.low, high: lr.high, n: lr.recordings })));
  }
  if (sound && sound.pitch_hz) {
    const ph = sound.pitch_hz;
    const sp = sound.pitch_span_semitones;
    addRow('pl_tone_pitch', esc(tf('pl_tone_pitch_v', { med: ph.median, low: ph.low, high: ph.high, span: sp ? sp.median : 0, n: ph.recordings })));
  }

  const subText = hasText
    ? tf('pl_tone_sub', { n: report.organic_items_analyzed, b: ling.breaths })
    : t('pl_tone_sub_sound_only');
  const thinBanner = hasText && !report.ready
    ? `<p class="hint">${esc(tf('pl_tone_thin', { b: ling.breaths, need: report.needed_breaths }))}</p>`
    : '';
  const cutNote = sound && sound.cut_short
    ? `<p class="hint">${esc(tf('pl_tone_cut_short', { n: sound.cut_short }))}</p>`
    : '';
  const wlNote = report.wordlist_note && report.wordlist_note[lang] ? report.wordlist_note[lang] : t('pl_tone_sound_note');

  box.innerHTML = `<h2 class="sec-h">${esc(t('pl_tone_title'))}</h2>
    <div class="card" id="tone-analyzer-card">
      <p class="hint" style="margin-top:0">${esc(subText)}</p>
      ${thinBanner}
      <dl class="kv">${rows.join('')}</dl>
      ${cutNote}
      <p class="hint">${esc(wlNote)}</p>
      <p class="hint">${esc(t('pl_tone_sound_note'))}</p>
    </div>`;
}
