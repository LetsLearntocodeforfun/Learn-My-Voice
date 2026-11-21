/* On the voice page: what was counted about tone in the creator's own material (studio/tone_analyzer.py). */

Object.assign(I18N.zh, {
  pl_tone_title: '声音与语气深度分析仪',
  pl_tone_sub: '从自然非商单素材与音频波形中提取的五维语气雷达、复合语气词、感官叠词与停顿密度。',
  pl_axis_warmth_intimacy: '亲切熟人感',
  pl_axis_playful_humor: '冷幽默吐槽感',
  pl_axis_restraint_authenticity: '克制真实度',
  pl_axis_spoken_naturalness: '口语自然度',
  pl_axis_pacing_energy: '语速与能量感',
  pl_tone_dominant: '主导语气画像',
  pl_tone_multi_p: '复合句尾语气词',
  pl_tone_sensory: '感官叠词习惯',
  pl_tone_pauses: '每百字气口停顿',
  pl_tone_prosody: '音频声学韵律（动态范围 / 能量曲线）',
});

Object.assign(I18N.en, {
  pl_tone_title: 'Voice & Tone Deep Analyzer',
  pl_tone_sub: '5-axis register profile, multi-char particles, sensory reduplications, pause density, and audio prosody.',
  pl_axis_warmth_intimacy: 'Warmth & Intimacy',
  pl_axis_playful_humor: 'Playful Dry Humor',
  pl_axis_restraint_authenticity: 'Restraint & Trust',
  pl_axis_spoken_naturalness: 'Spoken Naturalness',
  pl_axis_pacing_energy: 'Pacing & Vocal Energy',
  pl_tone_dominant: 'Dominant Tone Signature',
  pl_tone_multi_p: 'Multi-char Particles',
  pl_tone_sensory: 'Sensory Reduplications',
  pl_tone_pauses: 'Pauses per 100 units',
  pl_tone_prosody: 'Acoustic Prosody (Dynamic Range / Arc)',
});

async function plPaintToneCard(box) {
  if (!box) return;
  let report;
  try { report = await api('GET', '/api/tone'); } catch (e) { return; }
  const ling = report.linguistics || {};
  if (!ling.clauses) return;
  const axes = ling.tone_axes || {};
  const axisKeys = ['warmth_intimacy', 'playful_humor', 'restraint_authenticity', 'spoken_naturalness', 'pacing_energy'];
  const multiList = Object.keys((ling.particles && ling.particles.multi) || {});
  const sensoryList = ling.sensory_reduplications || [];
  const prosody = report.acoustic_prosody_samples || [];

  const axisRows = axisKeys.map((k) => {
    const val = axes[k] != null ? axes[k] : 50;
    return `<div class="row" style="margin:6px 0;gap:12px">
      <span style="min-width:130px">${esc(t('pl_axis_' + k))}</span>
      <div class="spacer" style="height:8px;background:var(--fill);border-radius:4px;overflow:hidden">
        <div style="width:${val}%;height:100%;background:var(--accent)"></div>
      </div>
      <span class="meta" style="min-width:34px;text-align:right">${val}</span>
    </div>`;
  }).join('');

  const prosodyHtml = prosody.length ? prosody.map((p) =>
    `<span class="tag">${esc(p.title)}: ${esc(lang === 'zh' ? p.delivery_style_zh : p.delivery_style_en)} (${p.dynamic_range_db} dB)</span>`
  ).join('') : '';

  box.innerHTML = `<h2 class="sec-h">${esc(t('pl_tone_title'))}</h2>
    <div class="card" id="tone-analyzer-card">
      <p class="hint" style="margin-top:0">${esc(t('pl_tone_sub'))}</p>
      <p><strong>${esc(t('pl_tone_dominant'))}：</strong>${esc(lang === 'zh' ? ling.dominant_tone_zh : ling.dominant_tone_en)}</p>
      <div style="margin:12px 0">${axisRows}</div>
      <dl class="kv">
        ${multiList.length ? `<dt>${esc(t('pl_tone_multi_p'))}</dt><dd>${multiList.map((m) => `<span class="tag">${esc(m)}</span>`).join('')}</dd>` : ''}
        ${sensoryList.length ? `<dt>${esc(t('pl_tone_sensory'))}</dt><dd>${sensoryList.map((s) => `<span class="tag">${esc(s)}</span>`).join('')}</dd>` : ''}
        ${report.pauses_per_100_units != null ? `<dt>${esc(t('pl_tone_pauses'))}</dt><dd>${esc(String(report.pauses_per_100_units))}</dd>` : ''}
        ${prosodyHtml ? `<dt>${esc(t('pl_tone_prosody'))}</dt><dd>${prosodyHtml}</dd>` : ''}
      </dl>
    </div>`;
}
