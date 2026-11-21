/* The plan of upcoming videos (#/plan). The numbers and comments beside the library are in rednote.js,
   the tone card on the voice page in tone.js. */

Object.assign(I18N.zh, {
  nav_plan: '拍摄计划',
  pl_title: '小红书动态洞察与拍摄计划',
  pl_sub: '综合小红书播放量增速、高收藏转化与评论区情绪提问，无缝规划并起草下一期视频。',
  pl_stat_views: '自然笔记中位播放',
  pl_stat_inter: '中位互动量',
  pl_stat_save: '中位收藏率',
  pl_import_btn: '导入小红书创作中心数据 (.csv/.xlsx)',
  pl_import_create: '未匹配标题自动新建素材条目',
  pl_import_done: '已同步 {u} 条视频数据（新建 {c} 条）',
  pl_opp_title: '基于播放表现与评论情绪的选题机会',
  pl_opp_empty: '在「素材库」录入播放量或粘贴评论后，这里会自动浮现高潜续集与评论区追问选题。',
  pl_add_btn: '＋ 加入拍摄计划',
  pl_added_toast: '已加入拍摄计划',
  pl_queue_title: '近期拍摄排期队列',
  pl_new_title_ph: '新视频标题或主题，例如：秋冬干皮晚间护肤实话实说',
  pl_new_date_ph: '计划发布日 YYYY-MM-DD',
  pl_new_notes_ph: '拍摄要点或想回应的观众问题（可选）',
  pl_queue_empty: '排期队列还是空的。可以从上方选题机会、「选题建议」一键添加，或在上方直接新建。',
  pl_status_IDEA: '灵感池',
  pl_status_PLANNED: '已排期',
  pl_status_SCRIPTING: '写稿中',
  pl_status_READY: '待发布',
  pl_status_POSTED: '已发布',
  pl_ai_draft_btn: '✨ AI 写脚本',
  pl_self_draft_btn: '新建空白脚本',
  pl_open_script_btn: '打开脚本 →',
  pl_questions_title: '全站评论区高频追问（可直接拍成新视频）',
  pl_patterns_title: '高表现内容规律总结',
});

Object.assign(I18N.en, {
  nav_plan: 'Video Plan',
  pl_title: 'RedNote Dynamic Insights & Video Planner',
  pl_sub: 'Turn dynamic RedNote view velocity, save rates, and comment sentiment seamlessly into planned upcoming videos.',
  pl_stat_views: 'Median organic views',
  pl_stat_inter: 'Median interactions',
  pl_stat_save: 'Median save rate',
  pl_import_btn: 'Import RedNote Creator CSV/XLSX',
  pl_import_create: 'Create library stubs for unmatched titles',
  pl_import_done: 'Synced {u} videos ({c} created)',
  pl_opp_title: 'Opportunities from View Lift & Viewer Sentiment',
  pl_opp_empty: 'Add view counts or paste viewer comments in Library to surface sequel & Q&A video opportunities automatically.',
  pl_add_btn: '+ Add to Plan',
  pl_added_toast: 'Added to Video Plan',
  pl_queue_title: 'Upcoming Video Queue',
  pl_new_title_ph: 'Planned video title or hook…',
  pl_new_date_ph: 'Target date YYYY-MM-DD',
  pl_new_notes_ph: 'Key points or viewer questions to answer (optional)',
  pl_queue_empty: 'No videos in the planning queue yet. Add from Opportunities, Topic Ideas, or create one above.',
  pl_status_IDEA: 'Idea',
  pl_status_PLANNED: 'Planned',
  pl_status_SCRIPTING: 'Scripting',
  pl_status_READY: 'Ready',
  pl_status_POSTED: 'Posted',
  pl_ai_draft_btn: '✨ AI Draft',
  pl_self_draft_btn: 'Blank Script',
  pl_open_script_btn: 'Open Script →',
  pl_questions_title: 'Top Viewer Questions Across Comments',
  pl_patterns_title: 'Winning Content Patterns',
});

const plUi = {
  filter: 'ALL',
};

function plFileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || '');
      const comma = s.indexOf(',');
      resolve(comma >= 0 ? s.slice(comma + 1) : s);
    };
    reader.onerror = () => reject(new Error('read_failed'));
    reader.readAsDataURL(file);
  });
}

async function renderPlan() {
  const data = await api('GET', '/api/plans');
  const plans = data.plans || [];
  const opps = data.opportunities || [];
  const questions = data.top_audience_questions || [];
  const statuses = ['IDEA', 'PLANNED', 'SCRIPTING', 'READY', 'POSTED'];

  const statSummary = `<div class="card" style="margin-bottom:18px">
    <div class="row" style="flex-wrap:wrap;gap:20px">
      <div><div class="meta">${esc(t('pl_stat_views'))}</div><strong>${data.median_views != null ? esc(num(Math.round(data.median_views))) : '—'}</strong></div>
      <div><div class="meta">${esc(t('pl_stat_inter'))}</div><strong>${data.median_interactions != null ? esc(num(Math.round(data.median_interactions))) : '—'}</strong></div>
      <div><div class="meta">${esc(t('pl_stat_save'))}</div><strong>${data.median_save_rate_pct != null ? esc(data.median_save_rate_pct + '%') : '—'}</strong></div>
      <span class="spacer"></span>
      <label class="row meta" style="gap:6px;cursor:pointer">
        <input type="checkbox" id="pl-imp-create">
        <span>${esc(t('pl_import_create'))}</span>
      </label>
      <input type="file" id="pl-imp-file" accept=".csv,.tsv,.txt,.xlsx" hidden>
      <button type="button" class="btn" id="pl-imp-btn">${esc(t('pl_import_btn'))}</button>
    </div>
  </div>`;

  const oppCards = opps.length ? opps.map((o, idx) => `<div class="card">
    <div class="row">
      <strong>${esc(lang === 'zh' ? o.title_zh : (o.title_en || o.title_zh))}</strong>
      <span class="spacer"></span>
      <button type="button" class="btn primary" data-pl-opp="${idx}">${esc(t('pl_add_btn'))}</button>
    </div>
    <p class="meta" style="margin:6px 0 0">${esc(lang === 'zh' ? o.rationale_zh : (o.rationale_en || o.rationale_zh))}</p>
  </div>`).join('') : `<div class="card meta">${esc(t('pl_opp_empty'))}</div>`;

  const qHtml = questions.length ? `<h2 class="sec-h">${esc(t('pl_questions_title'))}</h2>
    <div class="card">${questions.slice(0, 6).map((q, idx) => `<div class="row" style="padding:6px 0;gap:10px;flex-wrap:wrap">
      <div class="spacer"><strong>「${esc(q.question_zh)}」</strong> <span class="meta">· ${esc(q.video_title)}</span></div>
      <button type="button" class="btn" data-pl-q="${idx}">${esc(t('pl_add_btn'))}</button>
    </div>`).join('')}</div>` : '';

  const shownPlans = plUi.filter === 'ALL' ? plans : plans.filter((p) => p.status === plUi.filter);
  const filterChips = ['ALL'].concat(statuses).map((st) => {
    const label = st === 'ALL' ? (lang === 'zh' ? '全部' : 'All') + ` (${plans.length})` : `${t('pl_status_' + st)} (${(data.counts && data.counts[st]) || 0})`;
    return `<button type="button" class="chip ${plUi.filter === st ? 'on' : ''}" data-pl-filter="${st}">${esc(label)}</button>`;
  }).join('');

  const planCards = shownPlans.length ? shownPlans.map((p) => {
    const statusOpts = statuses.map((st) => `<option value="${st}" ${p.status === st ? 'selected' : ''}>${esc(t('pl_status_' + st))}</option>`).join('');
    const kwTags = (p.keywords || []).map((k) => `<span class="tag ok">${esc(k)}</span>`).join('');
    const actionBtns = p.script_id
      ? `<a class="btn primary" href="#/s/${esc(p.script_id)}">${esc(t('pl_open_script_btn'))}</a>`
      : `${appState.settings.configured ? `<button type="button" class="btn primary" data-pl-draft="${esc(p.id)}" data-ai="1">${esc(t('pl_ai_draft_btn'))}</button>` : ''}
         <button type="button" class="btn" data-pl-draft="${esc(p.id)}" data-ai="0">${esc(t('pl_self_draft_btn'))}</button>`;
    return `<div class="card" data-plan-card="${esc(p.id)}">
      <div class="row" style="flex-wrap:wrap;gap:10px">
        <h3 style="margin:0">${esc(p.title)}</h3>
        ${kwTags}
        <span class="spacer"></span>
        <input type="date" data-pl-date="${esc(p.id)}" value="${esc(p.scheduled_for || '')}" aria-label="Target date" style="width:auto">
        <select data-pl-status="${esc(p.id)}" style="width:auto">${statusOpts}</select>
      </div>
      ${p.rationale_zh || p.rationale_en ? `<p class="meta" style="margin:8px 0 0">${esc(lang === 'zh' ? p.rationale_zh : (p.rationale_en || p.rationale_zh))}</p>` : ''}
      ${p.notes ? `<p class="hint" style="margin:6px 0 0">${esc(p.notes)}</p>` : ''}
      <div class="row" style="margin-top:12px;flex-wrap:wrap;gap:8px">
        ${actionBtns}
        <span class="meta">${esc(formatLabel(p.video_format))} · ${p.target_sec}${esc(t('sec'))}</span>
        <span class="spacer"></span>
        <button type="button" class="btn" data-pl-del="${esc(p.id)}">${esc(t('delete'))}</button>
      </div>
    </div>`;
  }).join('') : `<div class="empty">${esc(t('pl_queue_empty'))}</div>`;

  $view.innerHTML = `<div class="wrap">
    <h1>${esc(t('pl_title'))}</h1><p class="sub">${esc(t('pl_sub'))}</p>
    ${statSummary}
    <h2 class="sec-h">${esc(t('pl_opp_title'))}</h2>
    ${oppCards}
    ${qHtml}
    <h2 class="sec-h">${esc(t('pl_queue_title'))}</h2>
    <div class="card" style="margin-bottom:14px">
      <div class="row" style="flex-wrap:wrap;gap:10px">
        <input type="text" id="pl-new-title" class="spacer" placeholder="${esc(t('pl_new_title_ph'))}">
        <input type="date" id="pl-new-date" style="width:auto" title="${esc(t('pl_new_date_ph'))}">
        <button type="button" class="btn primary" id="pl-create-btn">${esc(t('pl_add_btn'))}</button>
      </div>
      <textarea id="pl-new-notes" rows="2" style="margin-top:8px" placeholder="${esc(t('pl_new_notes_ph'))}"></textarea>
    </div>
    <div class="chips" style="margin-bottom:14px">${filterChips}</div>
    <div id="pl-list">${planCards}</div>
  </div>`;

  const $impFile = document.getElementById('pl-imp-file');
  document.getElementById('pl-imp-btn').onclick = () => $impFile.click();
  $impFile.onchange = async () => {
    const file = $impFile.files && $impFile.files[0];
    if (!file) return;
    try {
      const b64 = await plFileToBase64(file);
      const createMissing = !!document.getElementById('pl-imp-create').checked;
      const res = await api('POST', '/api/rednote/import', { filename: file.name, data_base64: b64, create_missing: createMissing });
      toast(tf('pl_import_done', { u: res.updated, c: res.created }));
      await router();
    } catch (e) { showError(e); }
  };

  document.getElementById('pl-create-btn').onclick = async () => {
    const title = document.getElementById('pl-new-title').value.trim();
    const scheduled_for = document.getElementById('pl-new-date').value.trim();
    const notes = document.getElementById('pl-new-notes').value.trim();
    try {
      await api('POST', '/api/plans', { title, scheduled_for, notes, source_kind: 'manual' });
      toast(t('pl_added_toast'));
      await router();
    } catch (e) { showError(e); }
  };

  $view.querySelectorAll('[data-pl-filter]').forEach((b) => {
    b.onclick = () => { plUi.filter = b.dataset.plFilter; renderPlan(); };
  });

  $view.querySelectorAll('[data-pl-opp]').forEach((b) => {
    b.onclick = async () => {
      const opp = opps[Number(b.dataset.plOpp)];
      if (!opp) return;
      try {
        await api('POST', '/api/plans', {
          title: opp.title_zh,
          video_format: opp.video_format,
          target_sec: opp.target_sec,
          notes: opp.notes,
          rationale_zh: opp.rationale_zh,
          rationale_en: opp.rationale_en,
          source_kind: opp.kind,
        });
        toast(t('pl_added_toast'));
        await router();
      } catch (e) { showError(e); }
    };
  });

  $view.querySelectorAll('[data-pl-q]').forEach((b) => {
    b.onclick = async () => {
      const q = questions[Number(b.dataset.plQ)];
      if (!q) return;
      try {
        await api('POST', '/api/plans', {
          title: '回应评论区提问｜' + q.question_zh.slice(0, 26),
          notes: '本条专门解答《' + q.video_title + '》评论区观众追问：' + q.question_zh,
          questions: [q.question_zh],
          rationale_zh: '来自《' + q.video_title + '》评论区真实提问',
          rationale_en: 'From viewer question on "' + q.video_title + '"',
          source_kind: 'viewer_question',
        });
        toast(t('pl_added_toast'));
        await router();
      } catch (e) { showError(e); }
    };
  });

  $view.querySelectorAll('[data-pl-status]').forEach((sel) => {
    sel.onchange = async () => {
      try {
        await api('PUT', '/api/plans/' + sel.dataset.plStatus, { status: sel.value });
        toast(t('saved'));
        await router();
      } catch (e) { showError(e); }
    };
  });

  $view.querySelectorAll('[data-pl-date]').forEach((inp) => {
    inp.onchange = async () => {
      try {
        await api('PUT', '/api/plans/' + inp.dataset.plDate, { scheduled_for: inp.value });
        toast(t('saved'));
      } catch (e) { showError(e); }
    };
  });

  $view.querySelectorAll('[data-pl-del]').forEach((b) => {
    b.onclick = async () => {
      try {
        await api('DELETE', '/api/plans/' + b.dataset.plDel);
        await router();
      } catch (e) { showError(e); }
    };
  });

  $view.querySelectorAll('[data-pl-draft]').forEach((b) => {
    b.onclick = async () => {
      const pid = b.dataset.plDraft;
      const useAi = b.dataset.ai === '1';
      if (useAi && appState.settings.manual) {
        const plan = plans.find((x) => x.id === pid);
        if (!plan) return;
        draft.notes = plan.title + (plan.notes ? '\n' + plan.notes : '');
        draft.target_sec = plan.target_sec;
        draft.video_format = plan.video_format;
        draft.keywords = (plan.keywords || []).slice();
        location.hash = '#/new';
        return;
      }
      b.disabled = true;
      if (useAi) b.textContent = t('writing');
      try {
        const res = await api('POST', '/api/plans/' + pid + '/draft', { use_ai: useAi, lang });
        location.hash = '#/s/' + res.script.id;
      } catch (e) {
        b.disabled = false;
        showError(e);
      }
    };
  });
}
