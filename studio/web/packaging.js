'use strict';
/* RedNote Cover, Title & First-3s Packaging Lab + Pre-Filming Readiness Gate (#/s/<id>).
   All display-width truncation, word-list / never-say checks, and corpus insights come from studio/packaging.py
   and are explicitly labelled as deterministic checks, never reach predictions. */

Object.assign(I18N.zh, {
  pk_ready_title: '拍摄前就绪检查',
  pk_ready_all: '全部就绪 ({p}/{n})',
  pk_ready_some: '{p}/{n} 项通过',
  pk_ready_jump: '封面与标题工坊 ↓',
  pk_lab_title: '小红书封面与标题工坊',
  pk_lab_sub: '双列信息流 3:4 封面与标题实时预览、备选标题切入角、置顶评论引导与词表预检。',
  pk_gen_btn: 'AI 一键生成包装',
  pk_gen_busy: '正在生成包装…',
  pk_manual_btn: '复制提示词 / 粘贴 JSON',
  pk_sim_label: '双列信息流 3:4 卡片模拟',
  pk_sim_cover_ph: '在右侧输入或选择 3:4 封面大字（<=10 字更清晰）',
  pk_sim_title_ph: '选定或输入小红书标题后在此预览折行与截断',
  pk_units_lbl: '{u} 单位',
  pk_st_ok: '单行安全',
  pk_st_wrap: '双行折行 (>20)',
  pk_st_truncate: '信息流截断 (>28)',
  pk_st_empty: '未填',
  pk_cover_ok: '3:4 清晰 (<=10)',
  pk_cover_dense: '字数偏密 (>10)',
  pk_sub_ok: '副标适中 (<=12)',
  pk_sub_dense: '副标偏长 (>12)',
  pk_titles_h: '备选标题（点 ★ 设为当前使用标题）',
  pk_title_ph: '输入小红书标题（建议 <= 20 显示单位）',
  pk_add_title: '+ 添加备选标题',
  pk_star_aria: '设为当前选定标题',
  pk_sync_title: '同步为脚本标题',
  pk_synced_toast: '已同步为脚本标题',
  pk_angle_curiosity_gap: '好奇留白',
  pk_angle_concrete_detail: '具体数字/细节',
  pk_angle_contrast: '反差对比',
  pk_angle_relatable_moment: '日常共鸣',
  pk_tweaks_h: '前 3 秒首句提速建议',
  pk_tweak_use: '套用到第 1 句',
  pk_tweak_applied: '已更新第 1 句开场',
  pk_cover_h: '3:4 竖版封面文案',
  pk_cover_picks: '点选生成的封面方案：',
  pk_cover_main_lbl: '封面主标题（建议 <= 10 字）',
  pk_cover_main_ph: '例如：周日超市必买清单',
  pk_cover_sub_lbl: '封面副标题（可选，建议 <= 12 字）',
  pk_cover_sub_ph: '例如：3 件不踩雷日常回购',
  pk_body_h: '小红书笔记正文',
  pk_body_ph: '写几句自然的补充文案（分段透气，不堆砌营销词）…',
  pk_tags_h: '话题标签 (#Hashtags)',
  pk_tags_ph: '#日常Vlog #周末超市 #好物分享',
  pk_pinned_h: '置顶评论引导（自然互动开场）',
  pk_pinned_picks: '点选置顶评论方案：',
  pk_pinned_ph: '例如：顺便问下大家平时去超市必拿哪一样？',
  pk_kw_h: '目标关键词覆盖（标题 / 封面 / 正文 / 话题）',
  pk_kw_ok: '包装已自然包含目标词',
  pk_kw_miss: '当前标题、封面与正文尚未包含任何目标关键词',
  pk_findings_h: '包装词表与禁说词核对',
  pk_findings_clean: '当前选定标题、封面、正文与置顶评论未命中词表风险或禁说词。',
  pk_insights_h: '你的过往高表现笔记对照',
  pk_mb_title: '复制提示词 · 生成小红书封面与标题包装',
  pk_mb_intro: '没有配置 API Key 也能完整使用：复制下方提示词发给任意聊天模型（ChatGPT / Claude / Gemini / DeepSeek 等），再把返回的 JSON 粘贴回来即可合并。',
  pk_mb_step1: '第 1 步：复制完整包装提示词',
  pk_mb_step2: '第 2 步：粘贴模型返回的 JSON',
  pk_mb_apply: '合并到当前包装',
  pk_generated_toast: '已更新封面与标题包装方案',
  pk_clean_badge: '通过预检',
  pk_flag_badge: '{n} 项提醒',
});

Object.assign(I18N.en, {
  pk_ready_title: 'Pre-filming readiness',
  pk_ready_all: 'All ready ({p}/{n})',
  pk_ready_some: '{p}/{n} passed',
  pk_ready_jump: 'Packaging Lab ↓',
  pk_lab_title: 'RedNote Packaging Lab',
  pk_lab_sub: 'Live 3:4 mobile feed simulator, title angles, cover callouts, pinned comment starters, and deterministic word-list checks for {@name}.',
  pk_gen_btn: 'Generate packaging',
  pk_gen_busy: 'Generating…',
  pk_manual_btn: 'Copy prompt / Paste JSON',
  pk_sim_label: '2-column mobile feed 3:4 simulator',
  pk_sim_cover_ph: 'Type or pick a 3:4 cover headline (<=10 units stays crisp on phones)',
  pk_sim_title_ph: 'Select or type a RedNote title to preview 2-column wrapping & truncation',
  pk_units_lbl: '{u} u',
  pk_st_ok: '1-line safe',
  pk_st_wrap: '2-line wrap (>20)',
  pk_st_truncate: 'Truncates in feed (>28)',
  pk_st_empty: 'Empty',
  pk_cover_ok: '3:4 clear (<=10)',
  pk_cover_dense: 'Too dense (>10)',
  pk_sub_ok: 'Subtitle ok (<=12)',
  pk_sub_dense: 'Subtitle long (>12)',
  pk_titles_h: 'Title options (click ★ to set active title)',
  pk_title_ph: 'RedNote feed title (<= 20 display units ideal)',
  pk_add_title: '+ Add title option',
  pk_star_aria: 'Set as active title',
  pk_sync_title: 'Use as script title',
  pk_synced_toast: 'Updated script title',
  pk_angle_curiosity_gap: 'Curiosity gap',
  pk_angle_concrete_detail: 'Concrete detail',
  pk_angle_contrast: 'Contrast',
  pk_angle_relatable_moment: 'Relatable moment',
  pk_tweaks_h: 'First-3s opening hook tweaks',
  pk_tweak_use: 'Use as Line 1',
  pk_tweak_applied: 'Updated Line 1 opening',
  pk_cover_h: '3:4 vertical cover callout',
  pk_cover_picks: 'Quick-pick a generated cover:',
  pk_cover_main_lbl: 'Cover headline (<= 10 units ideal)',
  pk_cover_main_ph: 'e.g. Sunday grocery staples',
  pk_cover_sub_lbl: 'Cover subtitle (optional, <= 12 units ideal)',
  pk_cover_sub_ph: 'e.g. 3 everyday repeats',
  pk_body_h: 'Note caption',
  pk_body_ph: 'Short natural note caption in {@names} voice…',
  pk_tags_h: 'Hashtags',
  pk_tags_ph: '#vlog #groceries #favorites',
  pk_pinned_h: 'Pinned comment CTA',
  pk_pinned_picks: 'Quick-pick a pinned comment starter:',
  pk_pinned_ph: 'A natural conversation starter for the comment section…',
  pk_kw_h: 'Target keyword check (title / cover / caption / hashtags)',
  pk_kw_ok: 'Target keyword included in packaging',
  pk_kw_miss: 'No target keyword appears in active title, cover, or caption yet',
  pk_findings_h: 'Packaging word-list & never-say check',
  pk_findings_clean: 'No word-list or never-say findings in the active title, cover, caption, or pinned comment.',
  pk_insights_h: 'Comparison with {@names} past notes',
  pk_mb_title: 'Copy prompt · Generate RedNote packaging',
  pk_mb_intro: 'Full parity without an API key: copy the Stage 11 prompt below into any chat model, then paste its JSON reply back to merge.',
  pk_mb_step1: 'Step 1: Copy packaging prompt',
  pk_mb_step2: 'Step 2: Paste JSON reply',
  pk_mb_apply: 'Merge packaging',
  pk_generated_toast: 'Packaging options updated',
  pk_clean_badge: 'Clean',
  pk_flag_badge: '{n} flag(s)',
});

const pkUi = {
  scriptId: null,
  seq: 0,
  saveTimer: null,
  data: null, // { post, report, readiness, updated_at }
};

/* Must match display_units and truncate_for_feed in studio/packaging.py */
function pkCharUnits(ch) {
  if (ch === '\r' || ch === '\n') return 0;
  const code = ch.codePointAt(0);
  if (code < 32 || code === 127) return 0;
  if (code >= 0xFE00 && code <= 0xFE0F) return 0;
  if (code === 0x200B || code === 0x200C || code === 0x200D || code === 0xFEFF) return 0;
  if (code < 128) return 0.55;
  if (
    (code >= 0x1100 && code <= 0x115F) ||
    (code >= 0x2000) ||
    (code >= 0x3000 && code <= 0x9FFF) ||
    (code >= 0xF900 && code <= 0xFAFF) ||
    (code >= 0xFF01 && code <= 0xFF60)
  ) {
    return 1.0;
  }
  return 0.55;
}

function pkDisplayUnits(text) {
  if (typeof text !== 'string' || !text) return 0;
  let sum = 0;
  for (const ch of text.replace(/\r?\n/g, ' ')) sum += pkCharUnits(ch);
  return Math.round(sum * 100) / 100;
}

function pkTruncateFeed(text, maxUnits) {
  const s = String(text || '').trim().replace(/\s+/g, ' ');
  if (!s) return { preview: '', truncated: false };
  let used = 0;
  let out = '';
  for (const ch of s) {
    const w = pkCharUnits(ch);
    if (used + w > maxUnits && out) return { preview: out.trimEnd() + '…', truncated: true };
    out += ch;
    used += w;
  }
  return { preview: out, truncated: false };
}

function pkTitleBadgeInfo(text) {
  const clean = String(text || '').trim();
  const units = pkDisplayUnits(clean);
  if (!clean) return { units: 0, cls: '', label: t('pk_st_empty') };
  if (units <= 20) return { units, cls: 'ok', label: t('pk_st_ok') };
  if (units <= 28) return { units, cls: 'warn', label: t('pk_st_wrap') };
  return { units, cls: 'no', label: t('pk_st_truncate') };
}

function pkNormalizeLocalPost(raw) {
  const p = raw && typeof raw === 'object' ? raw : {};
  const str = (v) => (typeof v === 'string' ? v.trim() : '');
  const list = (v) => (Array.isArray(v) ? v : []);
  const titles = list(p.title_options).map((o) => (
    typeof o === 'string'
      ? { title_zh: str(o), title_en: '', angle: '', hook_logic: '' }
      : {
          title_zh: str(o && (o.title_zh || o.zh)),
          title_en: str(o && (o.title_en || o.en)),
          angle: str(o && o.angle),
          hook_logic: str(o && o.hook_logic),
        }
  )).filter((o) => o.title_zh);
  let idx = Number.isInteger(p.selected_title_idx) ? p.selected_title_idx : 0;
  if (!titles.length || idx < 0 || idx >= titles.length) idx = 0;

  const rawCovers = list(p.cover_options).length
    ? list(p.cover_options)
    : list(p.cover_thumbnail_text_options_zh);
  const coverOptions = rawCovers.map((c) => (
    typeof c === 'string'
      ? { cover_text_zh: str(c), cover_sub_zh: '', cover_text_en: '', cover_sub_en: '' }
      : {
          cover_text_zh: str(c && c.cover_text_zh),
          cover_sub_zh: str(c && c.cover_sub_zh),
          cover_text_en: str(c && c.cover_text_en),
          cover_sub_en: str(c && c.cover_sub_en),
        }
  )).filter((c) => c.cover_text_zh || c.cover_sub_zh);

  const coverText = Object.prototype.hasOwnProperty.call(p, 'cover_text_zh')
    ? String(p.cover_text_zh || '')
    : (coverOptions[0] ? coverOptions[0].cover_text_zh : '');
  const coverSub = Object.prototype.hasOwnProperty.call(p, 'cover_sub_zh')
    ? String(p.cover_sub_zh || '')
    : (!Object.prototype.hasOwnProperty.call(p, 'cover_text_zh') && coverOptions[0] ? coverOptions[0].cover_sub_zh : '');
  const bodyZh = Object.prototype.hasOwnProperty.call(p, 'body_zh') && typeof p.body_zh === 'string'
    ? p.body_zh
    : String(p.caption_body_zh || '');
  const tags = (list(p.hashtags).length ? list(p.hashtags) : list(p.hashtags_zh)).map(str).filter(Boolean);
  const pinnedOptions = list(p.pinned_options).map((x) => (
    typeof x === 'string' ? { text_zh: str(x), text_en: '' } : { text_zh: str(x && x.text_zh), text_en: str(x && x.text_en) }
  )).filter((x) => x.text_zh);
  const pinnedZh = String(p.pinned_comment_zh || '');
  const hookTweaks = list(p.opening_hook_tweaks).filter((x) => x && str(x.spoken_script_zh));

  return {
    title_options: titles,
    selected_title_idx: idx,
    cover_text_zh: coverText,
    cover_sub_zh: coverSub,
    cover_options: coverOptions,
    body_zh: bodyZh,
    hashtags: tags,
    pinned_comment_zh: pinnedZh,
    pinned_options: pinnedOptions,
    opening_hook_tweaks: hookTweaks,
  };
}

function pkPaintReadiness() {
  const box = document.getElementById('pk-ready-box');
  if (!box || !cur || !cur.script) return;
  const rd = pkUi.data && pkUi.data.readiness;
  if (!rd || !Array.isArray(rd.items)) {
    box.innerHTML = '';
    return;
  }
  const summaryKey = rd.ready ? 'pk_ready_all' : 'pk_ready_some';
  const summaryCls = rd.ready ? 'ok' : 'warn';
  const pills = rd.items.map((item) => {
    const label = lang === 'zh' ? item.label_zh : (item.label_en || item.label_zh);
    const detail = lang === 'zh' ? item.detail_zh : (item.detail_en || item.detail_zh);
    const mark = item.ok ? '✓ ' : '! ';
    return `<span class="pk-pill ${item.ok ? 'pass' : 'warn'}" data-pk-ready-item="${esc(item.id)}" title="${esc(detail)}">
      <b>${esc(mark + label)}</b><span class="pk-pill-detail">${esc(detail)}</span></span>`;
  }).join('');

  box.innerHTML = `<div class="card pk-ready" id="pk-readiness-bar">
    <div class="row pk-ready-head">
      <b>${esc(t('pk_ready_title'))}</b>
      <span class="tag ${summaryCls}">${esc(tf(summaryKey, { p: rd.passed_count, n: rd.total_count }))}</span>
      <span class="spacer"></span>
      <button type="button" class="btn ghost pk-jump-btn" data-pk-jump="1">${esc(t('pk_ready_jump'))}</button>
    </div>
    <div class="pk-pills">${pills}</div>
  </div>`;
  const jumpBtn = box.querySelector('[data-pk-jump]');
  if (jumpBtn) {
    jumpBtn.onclick = () => {
      const target = document.getElementById('post-box');
      if (target && typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  }
}

function pkSimulatorHtml(post, report) {
  const activeTitle = post.title_options[post.selected_title_idx]
    ? post.title_options[post.selected_title_idx].title_zh
    : '';
  const feed = pkTruncateFeed(activeTitle, 28);
  const titleBadge = pkTitleBadgeInfo(activeTitle);
  const coverUnits = pkDisplayUnits(post.cover_text_zh);
  const subUnits = pkDisplayUnits(post.cover_sub_zh);
  const handle = (report && report.author_handle) || '创作者';
  const initial = Array.from(handle)[0] || '小';
  const coverCls = !post.cover_text_zh.trim() ? '' : (coverUnits <= 10 ? 'ok' : 'warn');
  const coverLabel = !post.cover_text_zh.trim() ? t('pk_st_empty') : (coverUnits <= 10 ? t('pk_cover_ok') : t('pk_cover_dense'));

  return `<div class="pk-sim-col">
    <div class="meta pk-sim-cap">${esc(t('pk_sim_label'))}</div>
    <div class="pk-feed-card" id="pk-feed-simulator">
      <div class="pk-cover-canvas">
        <div class="pk-cover-top"><span class="pk-cover-play" aria-hidden="true">▶</span></div>
        <div class="pk-cover-overlay">
          ${post.cover_text_zh.trim()
            ? `<div class="pk-cover-main" id="pk-sim-cover-main">${esc(post.cover_text_zh)}</div>`
            : `<div class="pk-cover-empty" id="pk-sim-cover-main">${esc(t('pk_sim_cover_ph'))}</div>`}
          ${post.cover_sub_zh.trim()
            ? `<div class="pk-cover-sub" id="pk-sim-cover-sub">${esc(post.cover_sub_zh)}</div>`
            : `<div class="pk-cover-sub" id="pk-sim-cover-sub" hidden></div>`}
        </div>
      </div>
      <div class="pk-feed-foot">
        <div class="pk-feed-title ${activeTitle ? '' : 'empty'}" id="pk-sim-title">${esc(activeTitle ? feed.preview : t('pk_sim_title_ph'))}</div>
        <div class="pk-feed-author">
          <span class="pk-feed-avatar" aria-hidden="true">${esc(initial)}</span>
          <span class="pk-feed-handle">@${esc(handle)}</span>
          <span class="spacer"></span>
          <span class="pk-feed-like" aria-hidden="true">♡</span>
        </div>
      </div>
    </div>
    <div class="pk-sim-badges" id="pk-sim-badges">
      <span class="tag ${titleBadge.cls}">${esc(tf('pk_units_lbl', { u: titleBadge.units }) + ' · ' + titleBadge.label)}</span>
      <span class="tag ${coverCls}">${esc(tf('pk_units_lbl', { u: coverUnits }) + ' · ' + coverLabel)}</span>
      ${post.cover_sub_zh.trim() ? `<span class="tag ${subUnits <= 12 ? 'ok' : 'warn'}">${esc(tf('pk_units_lbl', { u: subUnits }) + ' · ' + t(subUnits <= 12 ? 'pk_sub_ok' : 'pk_sub_dense'))}</span>` : ''}
    </div>
  </div>`;
}

function pkUpdateLiveSimulator() {
  if (!cur || !cur.script) return;
  const post = pkNormalizeLocalPost(cur.script.post);
  const report = pkUi.data ? pkUi.data.report : null;
  const $simWrap = document.querySelector('#post-box .pk-sim-col');
  if ($simWrap) {
    const temp = document.createElement('div');
    temp.innerHTML = pkSimulatorHtml(post, report);
    if (temp.firstElementChild) $simWrap.replaceWith(temp.firstElementChild);
  }
}

async function pkSavePatch(patch, opts) {
  if (!cur || !cur.script) return undefined;
  const scriptId = cur.script.id;
  clearTimeout(pkUi.saveTimer);
  try {
    const out = await api('POST', `/api/scripts/${scriptId}/packaging`, patch);
    if (!cur || cur.script.id !== scriptId) return undefined;
    pkUi.data = out;
    cur.script.post = out.post;
    if (typeof out.updated_at === 'number') cur.script.updated_at = out.updated_at;
    if (Array.isArray(out.lines)) cur.script.lines = out.lines;
    if (typeof out.title === 'string') cur.script.title = out.title;
    pkPaintReadiness();
    if (!(opts && opts.keepFocus)) pkPaintLab();
    if (typeof spAfterSave === 'function') spAfterSave(cur.script);
    return out;
  } catch (err) {
    showError(err);
    return undefined;
  }
}

function pkSchedulePatch(patchBuilder) {
  clearTimeout(pkUi.saveTimer);
  pkUi.saveTimer = setTimeout(() => {
    if (!cur || !cur.script) return;
    pkSavePatch(patchBuilder(), { keepFocus: true });
  }, 450);
}

async function pkOpenCopyPasteModal() {
  if (!cur || !cur.script) return;
  await flushSave();
  const scriptId = cur.script.id;
  let built;
  try {
    built = await api('POST', `/api/scripts/${scriptId}/packaging/generate`, { action: 'prompt' });
  } catch (err) {
    showError(err);
    return;
  }
  const promptText = built.prompt || '';
  const $modal = document.getElementById('modal');
  if (!$modal) return;

  $modal.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">
    <h2>${esc(t('pk_mb_title'))}</h2>
    <p class="hint">${esc(t('pk_mb_intro'))}</p>
    <label class="field" for="pk-mb-prompt">${esc(t('pk_mb_step1'))}</label>
    <textarea id="pk-mb-prompt" class="mono" rows="5" readonly></textarea>
    <div class="row">
      <button type="button" class="btn primary" id="pk-mb-copy">${esc(t('mb_copy'))}</button>
      <span class="meta">${esc(tf('mb_size', { n: promptText.length.toLocaleString() }))}</span>
    </div>
    <label class="field" for="pk-mb-reply">${esc(t('pk_mb_step2'))}</label>
    <textarea id="pk-mb-reply" class="mono" rows="6" placeholder="${esc(t('mb_reply_ph'))}"></textarea>
    <div class="banner danger" id="pk-mb-err" role="alert" hidden></div>
    <div class="row">
      <button type="button" class="btn primary big" id="pk-mb-apply">${esc(t('pk_mb_apply'))}</button>
      <button type="button" class="btn" id="pk-mb-cancel">${esc(t('mb_cancel'))}</button>
    </div>
  </div>`;
  $modal.hidden = false;

  const $prompt = document.getElementById('pk-mb-prompt');
  const $reply = document.getElementById('pk-mb-reply');
  const $err = document.getElementById('pk-mb-err');
  $prompt.value = promptText;

  const close = () => {
    $modal.hidden = true;
    $modal.innerHTML = '';
  };

  document.getElementById('pk-mb-copy').onclick = async () => {
    toast(t((await copyText(promptText, $prompt)) ? 'mb_copied' : 'mb_copy_failed'));
  };
  document.getElementById('pk-mb-cancel').onclick = close;
  document.getElementById('pk-mb-apply').onclick = async (e) => {
    const raw = $reply.value;
    if (!raw.trim()) {
      $err.textContent = t('mb_empty_reply');
      $err.hidden = false;
      return;
    }
    const btn = e.currentTarget;
    btn.disabled = true;
    $err.hidden = true;
    try {
      const out = await api('POST', `/api/scripts/${scriptId}/packaging/apply-reply`, { reply: raw });
      if (cur && cur.script.id === scriptId) {
        pkUi.data = out;
        cur.script.post = out.post;
        if (typeof out.updated_at === 'number') cur.script.updated_at = out.updated_at;
        pkPaintReadiness();
        pkPaintLab();
      }
      close();
      toast(t('pk_generated_toast'));
    } catch (err) {
      $err.textContent = errorMessage(err);
      $err.hidden = false;
      btn.disabled = false;
    }
  };
}

function pkPaintLab() {
  const el = document.getElementById('post-box');
  if (!el || !cur || !cur.script) return;
  const post = pkNormalizeLocalPost((pkUi.data && pkUi.data.post) || cur.script.post);
  const report = (pkUi.data && pkUi.data.report) || null;
  const titleChecks = (report && report.title_checks) || [];
  const configured = appState && appState.settings && appState.settings.configured;
  const isManual = appState && appState.settings && appState.settings.manual;

  const shownTitles = post.title_options.length
    ? post.title_options
    : [{ title_zh: '', title_en: '', angle: '', hook_logic: '' }];
  const titlesHtml = shownTitles.map((opt, i) => {
    const tc = titleChecks[i] || null;
    const wInfo = pkTitleBadgeInfo(opt.title_zh);
    const flagCount = tc ? (tc.error_count + tc.review_count) : 0;
    const angleKey = opt.angle ? 'pk_angle_' + opt.angle : '';
    const angleTxt = angleKey && I18N[lang][angleKey] ? t(angleKey) : '';
    const selected = i === post.selected_title_idx;
    return `<div class="pk-title-row ${selected ? 'selected' : ''}" data-pk-title-row="${i}">
      <button type="button" class="pk-star ${selected ? 'on' : ''}" data-pk-star="${i}" aria-label="${esc(t('pk_star_aria'))}" aria-pressed="${selected ? 'true' : 'false'}">${selected ? '★' : '☆'}</button>
      <div class="pk-title-main">
        <input type="text" data-pk-title-input="${i}" data-post-kind="title" data-post-idx="${i}" value="${esc(opt.title_zh)}" placeholder="${esc(t('pk_title_ph'))}">
        <div class="pk-title-meta">
          <span class="tag ${wInfo.cls}" data-pk-title-units="${i}">${esc(tf('pk_units_lbl', { u: wInfo.units }) + ' · ' + wInfo.label)}</span>
          ${angleTxt ? `<span class="tag">${esc(angleTxt)}</span>` : ''}
          ${tc ? `<span class="tag ${flagCount ? 'no' : 'ok'}">${esc(flagCount ? tf('pk_flag_badge', { n: flagCount }) : t('pk_clean_badge'))}</span>` : ''}
          ${lang === 'en' && opt.title_en ? `<span class="meta">${esc(opt.title_en)}</span>` : ''}
        </div>
      </div>
      <button type="button" class="btn ghost" data-pk-copy-val="title:${i}">${esc(t('post_copy'))}</button>
    </div>`;
  }).join('');

  const tweaks = post.opening_hook_tweaks || [];
  const tweaksHtml = tweaks.length ? `<div class="pk-sec">
    <h4>${esc(t('pk_tweaks_h'))}</h4>
    ${tweaks.map((tw, idx) => {
      const why = lang === 'zh' ? (tw.why_zh || tw.why_en) : (tw.why_en || tw.why_zh);
      return `<div class="pk-tweak-row">
        <div class="spacer">
          <div><b>${esc(tw.spoken_script_zh)}</b></div>
          ${why ? `<div class="meta">${esc(why)}</div>` : ''}
        </div>
        <button type="button" class="btn" data-pk-apply-tweak="${idx}">${esc(t('pk_tweak_use'))}</button>
      </div>`;
    }).join('')}
  </div>` : '';

  const coverChips = post.cover_options.length ? `<div class="pk-quick-row">
    <span class="hint">${esc(t('pk_cover_picks'))}</span>
    <div class="chips">${post.cover_options.map((c, idx) => {
      const label = [c.cover_text_zh, c.cover_sub_zh].filter(Boolean).join(' · ');
      const on = c.cover_text_zh === post.cover_text_zh && c.cover_sub_zh === post.cover_sub_zh;
      return `<button type="button" class="chip ${on ? 'on' : ''}" data-pk-cover-pick="${idx}">${esc(label)}</button>`;
    }).join('')}</div>
  </div>` : '';

  const pinnedChips = post.pinned_options.length ? `<div class="pk-quick-row">
    <span class="hint">${esc(t('pk_pinned_picks'))}</span>
    <div class="chips">${post.pinned_options.map((p, idx) => {
      const on = p.text_zh === post.pinned_comment_zh;
      return `<button type="button" class="chip ${on ? 'on' : ''}" data-pk-pin-pick="${idx}">${esc(p.text_zh)}</button>`;
    }).join('')}</div>
  </div>` : '';

  const kwCheck = report && report.keyword_check;
  const kwHtml = kwCheck && Array.isArray(kwCheck.items) && kwCheck.items.length ? `<div class="pk-sec">
    <h4>${esc(t('pk_kw_h'))}</h4>
    <div class="chips">
      ${kwCheck.items.map((k) => {
        const places = [
          k.in_title ? t('kw_in_title') : '',
          k.in_cover ? '3:4' : '',
          k.in_caption ? t('kw_in_caption') : '',
          k.in_hashtags ? t('kw_in_hashtags') : '',
        ].filter(Boolean).join(' · ');
        return `<span class="tag ${k.in_packaging ? 'ok' : 'warn'}">${esc(k.keyword + (places ? ' (' + places + ')' : ''))}</span>`;
      }).join('')}
    </div>
    <p class="hint">${esc(t(kwCheck.any_in_packaging ? 'pk_kw_ok' : 'pk_kw_miss'))}</p>
  </div>` : '';

  const activeFindings = (report && report.active_findings) || [];
  const findingsHtml = `<div class="pk-sec">
    <h4>${esc(t('pk_findings_h'))}</h4>
    ${activeFindings.length
      ? `<div class="issues">${activeFindings.map(wlIssueHtml).join('')}</div>`
      : `<p class="hint">${esc(t('pk_findings_clean'))}</p>`}
  </div>`;

  const observations = (report && report.corpus_insights && report.corpus_insights.observations) || [];
  const insightsHtml = observations.length ? `<div class="pk-sec">
    <h4>${esc(t('pk_insights_h'))}</h4>
    <ul class="pk-obs-list">${observations.map((o) => `<li>${esc(lang === 'zh' ? o.zh : (o.en || o.zh))}</li>`).join('')}</ul>
  </div>` : '';

  const honesty = report
    ? (lang === 'zh' ? report.honesty_zh : (report.honesty_en || report.honesty_zh))
    : '';

  el.innerHTML = `<div class="card post pk-lab" id="pk-packaging-lab">
    <div class="row pk-lab-head">
      <div>
        <h3>${esc(t('pk_lab_title'))}</h3>
        <p class="hint">${esc(t('pk_lab_sub'))}</p>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn primary" id="pk-gen-btn" ${configured ? '' : 'disabled'}>${esc(t('pk_gen_btn'))}</button>
      <button type="button" class="btn" id="pk-manual-btn">${esc(t('pk_manual_btn'))}</button>
    </div>

    <div class="pk-grid">
      ${pkSimulatorHtml(post, report)}
      <div class="pk-edit-col">
        <div class="pk-sec pk-sec-first">
          <div class="row">
            <h4>${esc(t('pk_titles_h'))}</h4>
            <span class="spacer"></span>
            ${post.title_options.length ? `<button type="button" class="btn ghost" id="pk-sync-title">${esc(t('pk_sync_title'))}</button>` : ''}
            <button type="button" class="btn ghost" id="pk-add-title">${esc(t('pk_add_title'))}</button>
          </div>
          <div id="pk-titles-list">${titlesHtml}</div>
        </div>

        ${tweaksHtml}

        <div class="pk-sec">
          <h4>${esc(t('pk_cover_h'))}</h4>
          ${coverChips}
          <div class="pk-cover-inputs">
            <div>
              <label class="hint" for="pk-cover-text">${esc(t('pk_cover_main_lbl'))}</label>
              <input type="text" id="pk-cover-text" data-post-kind="cover" data-post-idx="0" value="${esc(post.cover_text_zh)}" placeholder="${esc(t('pk_cover_main_ph'))}">
            </div>
            <div>
              <label class="hint" for="pk-cover-sub">${esc(t('pk_cover_sub_lbl'))}</label>
              <input type="text" id="pk-cover-sub" value="${esc(post.cover_sub_zh)}" placeholder="${esc(t('pk_cover_sub_ph'))}">
            </div>
          </div>
        </div>

        <div class="pk-sec">
          <div class="row">
            <h4>${esc(t('pk_body_h'))}</h4>
            <span class="spacer"></span>
            <button type="button" class="btn ghost" data-pk-copy-id="pk-body">${esc(t('post_copy'))}</button>
          </div>
          <textarea id="pk-body" data-post-kind="body" data-post-idx="0" rows="3" placeholder="${esc(t('pk_body_ph'))}">${esc(post.body_zh)}</textarea>
        </div>

        <div class="pk-sec">
          <div class="row">
            <h4>${esc(t('pk_tags_h'))}</h4>
            <span class="spacer"></span>
            <button type="button" class="btn ghost" data-pk-copy-id="pk-hashtags">${esc(t('post_copy'))}</button>
          </div>
          <input type="text" id="pk-hashtags" data-post-kind="hashtags" data-post-idx="0" value="${esc(post.hashtags.join(' '))}" placeholder="${esc(t('pk_tags_ph'))}">
        </div>

        <div class="pk-sec">
          <div class="row">
            <h4>${esc(t('pk_pinned_h'))}</h4>
            <span class="spacer"></span>
            <button type="button" class="btn ghost" data-pk-copy-id="pk-pinned">${esc(t('post_copy'))}</button>
          </div>
          ${pinnedChips}
          <input type="text" id="pk-pinned" value="${esc(post.pinned_comment_zh)}" placeholder="${esc(t('pk_pinned_ph'))}">
        </div>

        ${kwHtml}
        ${findingsHtml}
        ${insightsHtml}
        ${honesty ? `<p class="hint pk-honesty">${esc(honesty)}</p>` : ''}
      </div>
    </div>
  </div>`;

  el.querySelectorAll('textarea').forEach(autoGrow);

  const $genBtn = document.getElementById('pk-gen-btn');
  if ($genBtn) {
    $genBtn.onclick = (e) => {
      if (isManual) {
        pkOpenCopyPasteModal();
        return;
      }
      busy(e.currentTarget, t('pk_gen_busy'), async () => {
        await flushSave();
        const out = await api('POST', `/api/scripts/${cur.script.id}/packaging/generate`, {});
        if (!cur) return;
        pkUi.data = out;
        cur.script.post = out.post;
        if (typeof out.updated_at === 'number') cur.script.updated_at = out.updated_at;
        pkPaintReadiness();
        pkPaintLab();
        toast(t('pk_generated_toast'));
      });
    };
  }

  const $manualBtn = document.getElementById('pk-manual-btn');
  if ($manualBtn) $manualBtn.onclick = pkOpenCopyPasteModal;

  const $addTitle = document.getElementById('pk-add-title');
  if ($addTitle) {
    $addTitle.onclick = async () => {
      const next = pkNormalizeLocalPost(cur.script.post);
      const placeholder = cur.script.title || '新备选标题';
      next.title_options.push({ title_zh: placeholder, title_en: '', angle: '', hook_logic: '' });
      next.selected_title_idx = next.title_options.length - 1;
      await pkSavePatch({ title_options: next.title_options, selected_title_idx: next.selected_title_idx });
    };
  }

  const $syncTitle = document.getElementById('pk-sync-title');
  if ($syncTitle) {
    $syncTitle.onclick = async () => {
      const out = await pkSavePatch({ sync_script_title: true });
      if (out && out.title) {
        const $mainTitle = document.getElementById('title');
        if ($mainTitle) $mainTitle.value = out.title;
        toast(t('pk_synced_toast'));
      }
    };
  }

  el.oninput = (e) => {
    if (!cur || !cur.script) return;
    const target = e.target;
    if (target.tagName === 'TEXTAREA') autoGrow(target);
    const local = pkNormalizeLocalPost(cur.script.post);

    if (target.dataset.pkTitleInput !== undefined) {
      const idx = Number(target.dataset.pkTitleInput);
      while (local.title_options.length <= idx) {
        local.title_options.push({ title_zh: '', title_en: '', angle: '', hook_logic: '' });
      }
      local.title_options[idx].title_zh = target.value;
      const badgeEl = el.querySelector(`[data-pk-title-units="${idx}"]`);
      if (badgeEl) {
        const info = pkTitleBadgeInfo(target.value);
        badgeEl.className = 'tag ' + info.cls;
        badgeEl.textContent = tf('pk_units_lbl', { u: info.units }) + ' · ' + info.label;
      }
    } else if (target.id === 'pk-cover-text') {
      local.cover_text_zh = target.value;
    } else if (target.id === 'pk-cover-sub') {
      local.cover_sub_zh = target.value;
    } else if (target.id === 'pk-body') {
      local.body_zh = target.value;
    } else if (target.id === 'pk-hashtags') {
      local.hashtags = target.value.split(/\s+/).map((x) => x.trim()).filter(Boolean);
    } else if (target.id === 'pk-pinned') {
      local.pinned_comment_zh = target.value;
    } else {
      return;
    }

    cur.script.post = Object.assign({}, cur.script.post || {}, local, {
      caption_body_zh: local.body_zh,
      hashtags_zh: local.hashtags,
    });
    pkUpdateLiveSimulator();
    scheduleSave();
  };

  el.onclick = async (e) => {
    const starBtn = e.target.closest('[data-pk-star]');
    if (starBtn) {
      await pkSavePatch({ selected_title_idx: Number(starBtn.dataset.pkStar) });
      return;
    }
    const coverPick = e.target.closest('[data-pk-cover-pick]');
    if (coverPick) {
      await pkSavePatch({ select_cover_idx: Number(coverPick.dataset.pkCoverPick) });
      return;
    }
    const pinPick = e.target.closest('[data-pk-pin-pick]');
    if (pinPick) {
      await pkSavePatch({ select_pinned_idx: Number(pinPick.dataset.pkPinPick) });
      return;
    }
    const tweakBtn = e.target.closest('[data-pk-apply-tweak]');
    if (tweakBtn) {
      const out = await pkSavePatch({ apply_hook_tweak_idx: Number(tweakBtn.dataset.pkApplyTweak) });
      if (out && Array.isArray(out.lines)) {
        paintScript();
        toast(t('pk_tweak_applied'));
      }
      return;
    }
    const copyIdBtn = e.target.closest('[data-pk-copy-id]');
    if (copyIdBtn) {
      const field = document.getElementById(copyIdBtn.dataset.pkCopyId);
      toast(t((await copyPlain(field ? field.value : '')) ? 'mb_copied' : 'post_copy_failed'));
      return;
    }
    const copyValBtn = e.target.closest('[data-pk-copy-val]');
    if (copyValBtn) {
      const [kind, rawIdx] = String(copyValBtn.dataset.pkCopyVal).split(':');
      if (kind === 'title') {
        const input = el.querySelector(`[data-pk-title-input="${Number(rawIdx)}"]`);
        toast(t((await copyPlain(input ? input.value : '')) ? 'mb_copied' : 'post_copy_failed'));
      }
    }
  };
}

async function pkLoadForScript(scriptId) {
  const seq = ++pkUi.seq;
  try {
    const data = await api('GET', `/api/scripts/${scriptId}/packaging`);
    if (seq !== pkUi.seq || !cur || cur.script.id !== scriptId) return;
    pkUi.data = data;
    pkPaintReadiness();
    const el = document.getElementById('post-box');
    if (!el || !el.contains(document.activeElement)) pkPaintLab();
  } catch (err) {
    /* Optional panel refresh; ignore transient navigation cancels */
  }
}

function pkAttach() {
  if (!cur || !cur.script) return;
  if (pkUi.scriptId !== cur.script.id) {
    pkUi.scriptId = cur.script.id;
    pkUi.data = null;
  }
  pkPaintReadiness();
  pkPaintLab();
  pkLoadForScript(cur.script.id);
}

function pkAfterSave(script) {
  if (!script || !cur || cur.script.id !== script.id) return;
  pkLoadForScript(script.id);
}
