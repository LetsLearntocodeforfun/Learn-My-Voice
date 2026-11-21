/* Beside the library: the numbers of a posted video over time, and what its viewers wrote (studio/rednote_analytics.py).
   plFileToBase64 and the words shared with the plan page (pl_import_btn, pl_add_btn, ...) are in planner.js. */

Object.assign(I18N.zh, {
  pl_lib_panel_title: '小红书动态播放表现 & 评论区情绪分析',
  pl_lib_panel_sub: '批量同步创作服务平台数据，或粘贴单条笔记的评论区内容，自动提取情绪倾向、槽点与下期选题。',
  pl_pick_video: '选择要分析评论的笔记',
  pl_comments_ph: '直接粘贴小红书评论区文字（每行一条，支持带“赞 12”或地区时间后缀）…',
  pl_scan_local_btn: '分析评论情绪与提问',
  pl_scan_ai_btn: '✨ AI 深度提炼评论洞察',
  pl_quad_BREAKOUT_HIT: '爆款破圈（高播放+高收藏）',
  pl_quad_HIGH_SAVE_GEM: '高收藏干货（长尾潜力）',
  pl_quad_HIGH_DISCUSSION: '高讨论度（评论活跃）',
  pl_quad_BROAD_LOW_CONVERT: '高曝光低转化（开头强）',
  pl_quad_STEADY: '表现平稳',
  pl_quad_UNDERPERFORMING: '低于平时',
  pl_quad_UNMEASURED: '待录入数据',
  pl_vel_day: '日均 {v} 播放',
  pl_sent_pos: '正面 {n}%',
  pl_sent_neu: '中性 {n}%',
  pl_sent_neg: '顾虑/负面 {n}%',
});

Object.assign(I18N.en, {
  pl_lib_panel_title: 'RedNote Dynamic View Velocity & Comment Sentiment',
  pl_lib_panel_sub: 'Batch-sync Creator Center spreadsheets or paste viewer comments to extract sentiment, objections, and follow-up video angles.',
  pl_pick_video: 'Choose video to analyze comments',
  pl_comments_ph: 'Paste RedNote viewer comments here (one per line)…',
  pl_scan_local_btn: 'Analyze Sentiment & Questions',
  pl_scan_ai_btn: '✨ Deep AI Sentiment Synthesis',
  pl_quad_BREAKOUT_HIT: 'Breakout Hit (High Reach + Saves)',
  pl_quad_HIGH_SAVE_GEM: 'High-Save Gem (Evergreen)',
  pl_quad_HIGH_DISCUSSION: 'High Discussion',
  pl_quad_BROAD_LOW_CONVERT: 'Broad Reach / Low Save',
  pl_quad_STEADY: 'Steady Baseline',
  pl_quad_UNDERPERFORMING: 'Below Usual',
  pl_quad_UNMEASURED: 'No Metrics Yet',
  pl_vel_day: '{v} views/day',
  pl_sent_pos: 'Positive {n}%',
  pl_sent_neu: 'Neutral {n}%',
  pl_sent_neg: 'Critical {n}%',
});

const raUi = { selectedSampleId: '' };

async function plPaintLibraryTools(box, samples) {
  if (!box || !samples || !samples.length) return;
  let overview;
  try { overview = await api('GET', '/api/rednote/overview'); } catch (e) { return; }
  const videos = overview.videos || [];
  if (!raUi.selectedSampleId || !samples.some((s) => s.id === raUi.selectedSampleId)) {
    raUi.selectedSampleId = samples[0].id;
  }

  const draw = async () => {
    let detail = null;
    if (raUi.selectedSampleId) {
      try { detail = await api('GET', '/api/samples/' + raUi.selectedSampleId + '/comments'); } catch (e) { detail = null; }
    }
    const curVid = videos.find((v) => v.id === raUi.selectedSampleId) || {};
    const sent = detail && detail.sentiment;
    const pct = (sent && sent.sentiment_breakdown_pct) || { positive: 0, neutral: 0, negative: 0 };
    const qs = (sent && sent.audience_questions) || [];
    const angles = (sent && sent.followup_video_angles) || [];
    const quadKey = 'pl_quad_' + (curVid.quadrant || 'UNMEASURED');
    const velLine = curVid.velocity && curVid.velocity.views_per_day != null
      ? ' · ' + tf('pl_vel_day', { v: num(curVid.velocity.views_per_day) }) : '';

    box.innerHTML = `<details class="card" id="rn-analytics-drawer" style="margin-bottom:16px">
      <summary><strong>${esc(t('pl_lib_panel_title'))}</strong></summary>
      <p class="hint">${esc(t('pl_lib_panel_sub'))}</p>
      <div class="row" style="flex-wrap:wrap;gap:10px;margin-bottom:12px">
        <label for="rn-sample-sel" class="meta">${esc(t('pl_pick_video'))}</label>
        <select id="rn-sample-sel" class="spacer">${samples.map((s) =>
          `<option value="${esc(s.id)}" ${s.id === raUi.selectedSampleId ? 'selected' : ''}>${esc(s.title)}</option>`).join('')}</select>
        <input type="file" id="rn-lib-csv" accept=".csv,.tsv,.txt,.xlsx" hidden>
        <button type="button" class="btn" id="rn-lib-csv-btn">${esc(t('pl_import_btn'))}</button>
      </div>
      <div class="meta" style="margin-bottom:10px">
        <span class="tag">${esc(t(quadKey))}</span>${esc(velLine)}
        ${curVid.save_rate_pct != null ? ` · ${esc(t('pl_stat_save'))}: ${esc(curVid.save_rate_pct + '%')}` : ''}
      </div>
      <textarea id="rn-comments-in" rows="4" placeholder="${esc(t('pl_comments_ph'))}"></textarea>
      <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:8px">
        <button type="button" class="btn primary" id="rn-scan-local">${esc(t('pl_scan_local_btn'))}</button>
        ${(detail && detail.comments && detail.comments.length) ? `<button type="button" class="btn" id="rn-scan-ai">${esc(t('pl_scan_ai_btn'))}</button>` : ''}
      </div>
      ${sent && sent.comment_count > 0 ? `<div class="card soft" style="margin-top:14px">
        <div class="row" style="flex-wrap:wrap;gap:8px">
          <span class="tag ok">${esc(tf('pl_sent_pos', { n: pct.positive }))}</span>
          <span class="tag">${esc(tf('pl_sent_neu', { n: pct.neutral }))}</span>
          <span class="tag ${pct.negative > 20 ? 'no' : ''}">${esc(tf('pl_sent_neg', { n: pct.negative }))}</span>
        </div>
        <p style="margin:8px 0">${esc(lang === 'zh' ? sent.summary_zh : (sent.summary_en || sent.summary_zh))}</p>
        ${qs.map((q) => `<div class="meta">❓ ${esc(q.question_zh)}</div>`).join('')}
        ${angles.map((a, idx) => `<div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">
          <span class="spacer"><strong>💡 ${esc(lang === 'zh' ? a.title_zh : (a.title_en || a.title_zh))}</strong></span>
          <button type="button" class="btn" data-rn-angle="${idx}">${esc(t('pl_add_btn'))}</button>
        </div>`).join('')}
      </div>` : ''}
    </details>`;

    const $sel = box.querySelector('#rn-sample-sel');
    if ($sel) {
      $sel.onchange = () => {
        raUi.selectedSampleId = $sel.value;
        draw().then(() => {
          const d = box.querySelector('#rn-analytics-drawer');
          if (d) d.open = true;
        });
      };
    }
    const $csv = box.querySelector('#rn-lib-csv');
    const $csvBtn = box.querySelector('#rn-lib-csv-btn');
    if ($csv && $csvBtn) {
      $csvBtn.onclick = () => $csv.click();
      $csv.onchange = async () => {
        const f = $csv.files && $csv.files[0];
        if (!f) return;
        try {
          const b64 = await plFileToBase64(f);
          const res = await api('POST', '/api/rednote/import', { filename: f.name, data_base64: b64, create_missing: false });
          toast(tf('pl_import_done', { u: res.updated, c: res.created }));
          renderLibrary();
        } catch (e) { showError(e); }
      };
    }
    const $scanLocal = box.querySelector('#rn-scan-local');
    if ($scanLocal) {
      $scanLocal.onclick = async () => {
        const txt = box.querySelector('#rn-comments-in').value;
        try {
          await api('POST', '/api/samples/' + raUi.selectedSampleId + '/comments', { text: txt, replace: false });
          await draw();
          const d = box.querySelector('#rn-analytics-drawer');
          if (d) d.open = true;
        } catch (e) { showError(e); }
      };
    }
    const $scanAi = box.querySelector('#rn-scan-ai');
    if ($scanAi) {
      $scanAi.onclick = async () => {
        try {
          if (!appState.settings.configured || appState.settings.manual) {
            const out = await manualBridge('sentiment', { sample_id: raUi.selectedSampleId });
            if (!out) return;
          } else {
            $scanAi.disabled = true;
            await api('POST', '/api/samples/' + raUi.selectedSampleId + '/sentiment/ai', {});
          }
          await draw();
          const d = box.querySelector('#rn-analytics-drawer');
          if (d) d.open = true;
        } catch (e) {
          $scanAi.disabled = false;
          showError(e);
        }
      };
    }
    box.querySelectorAll('[data-rn-angle]').forEach((b) => {
      b.onclick = async () => {
        const a = angles[Number(b.dataset.rnAngle)];
        if (!a) return;
        try {
          await api('POST', '/api/plans', {
            title: a.title_zh,
            video_format: a.video_format,
            target_sec: a.target_duration_sec,
            notes: a.script_notes_zh,
            rationale_zh: a.rationale_zh,
            rationale_en: a.rationale_en,
            source_kind: 'sentiment_followup',
          });
          toast(t('pl_added_toast'));
        } catch (e) { showError(e); }
      };
    });
  };
  await draw();
}
