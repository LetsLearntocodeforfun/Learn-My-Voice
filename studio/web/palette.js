'use strict';
/* Global Command Palette (⌘K / Ctrl+K): instant client-side search and jump across scripts, plans, library
   material, tracked trends, all nine studio pages and common actions. Loaded before pages.js; uses api() only
   against local studio endpoints and renders every dynamic string through esc(). */

Object.assign(I18N.zh, {
  pal_title: '搜索与快捷跳转',
  pal_placeholder: '搜索脚本、计划、素材、热词或输入命令…',
  pal_results_label: '搜索结果与命令',
  pal_empty: '没有找到匹配的脚本、计划或命令。',
  pal_loading: '正在读取索引…',
  pal_cat_action: '操作',
  pal_cat_nav: '页面',
  pal_cat_script: '脚本',
  pal_cat_plan: '计划',
  pal_cat_sample: '素材',
  pal_cat_trend: '热点',
  pal_cmd_new_script: '新建脚本',
  pal_cmd_new_script_sub: '手写或用 AI 起草新视频脚本',
  pal_cmd_prompter: '打开提词器',
  pal_cmd_prompter_sub: '按你的语速逐句滚动当前脚本',
  pal_cmd_theme_light: '切换外观：浅色模式',
  pal_cmd_theme_dark: '切换外观：深色模式',
  pal_cmd_theme_sub: '立即切换工作台明暗外观',
  pal_cmd_lang: '切换语言：中英文切换',
  pal_cmd_lang_sub: '在中文与英文界面之间切换',
  pal_nav_home_sub: '查看和编辑全部视频脚本',
  pal_nav_start_sub: '五步引导设置与个人资料',
  pal_nav_plan_sub: '列表、按周和按月拍摄计划',
  pal_nav_ideas_sub: '从素材与数据生成选题建议',
  pal_nav_trends_sub: '跟踪热词与综合评分',
  pal_nav_library_sub: '管理脚本、字幕、视频与评论数据',
  pal_nav_voice_sub: '查看声音档案、语速与历史版本',
  pal_nav_autopilot_sub: '后台自动学习任务与一键撤销',
  pal_nav_settings_sub: '连接模型、明暗外观、词表与备份',
  pal_foot_move: '选择',
  pal_foot_run: '执行 / 跳转',
  pal_foot_close: '关闭',
});

Object.assign(I18N.en, {
  pal_title: 'Search and command palette',
  pal_placeholder: 'Search {@names} scripts, plans, library, trends or commands…',
  pal_results_label: 'Search results and commands',
  pal_empty: 'No matching scripts, plans or commands found.',
  pal_loading: 'Loading studio index…',
  pal_cat_action: 'Action',
  pal_cat_nav: 'Go to',
  pal_cat_script: 'Script',
  pal_cat_plan: 'Plan',
  pal_cat_sample: 'Library',
  pal_cat_trend: 'Trend',
  pal_cmd_new_script: 'New script',
  pal_cmd_new_script_sub: 'Draft a new script by hand or with AI',
  pal_cmd_prompter: 'Open Teleprompter',
  pal_cmd_prompter_sub: 'Read the active script at {@names} speaking pace',
  pal_cmd_theme_light: 'Switch theme: Light mode',
  pal_cmd_theme_dark: 'Switch theme: Dark mode',
  pal_cmd_theme_sub: 'Switch the studio between light and dark appearance',
  pal_cmd_lang: 'Switch language (Chinese / English)',
  pal_cmd_lang_sub: 'Toggle interface language between Chinese and English',
  pal_nav_home_sub: 'Browse and edit {@names} video scripts',
  pal_nav_start_sub: 'Five-step guided setup and creator profile',
  pal_nav_plan_sub: 'List, week and month video schedule',
  pal_nav_ideas_sub: 'Topic suggestions from {@their} library and metrics',
  pal_nav_trends_sub: 'Tracked trend keywords and scores',
  pal_nav_library_sub: 'Manage {@their} scripts, subtitles, videos and metrics',
  pal_nav_voice_sub: 'Inspect {@names} voice profile, rate and history',
  pal_nav_autopilot_sub: 'Bounded background tasks and run undo',
  pal_nav_settings_sub: 'AI connection, appearance, word list and backups',
  pal_foot_move: 'Navigate',
  pal_foot_run: 'Run / Jump',
  pal_foot_close: 'Close',
});

const PAL_PAGES = [
  { hash: '#/', key: 'nav_scripts', sub: 'pal_nav_home_sub', aliases: 'home scripts 首页 我的脚本' },
  { hash: '#/ideas', key: 'nav_ideas', sub: 'pal_nav_ideas_sub', aliases: 'discover ideas topics 选题建议 发现 灵感' },
  { hash: '#/plan', key: 'nav_plan', sub: 'pal_nav_plan_sub', aliases: 'plan planner schedule calendar 拍摄计划 日历 排期' },
  { hash: '#/voice', key: 'nav_voice', sub: 'pal_nav_voice_sub', aliases: 'voice dna profile 我的声音 声音档案 语速' },
  { hash: '#/library', key: 'nav_library', sub: 'pal_nav_library_sub', aliases: 'library samples analytics numbers comments 素材库 数据 评论' },
  { hash: '#/trends', key: 'nav_trends', sub: 'pal_nav_trends_sub', aliases: 'trends keywords analytics 热点 热词 关键词' },
  { hash: '#/autopilot', key: 'nav_autopilot', sub: 'pal_nav_autopilot_sub', aliases: 'autopilot background auto 自动学习 后台' },
  { hash: '#/start', key: 'nav_start', sub: 'pal_nav_start_sub', aliases: 'start guide setup onboarding 开始 开始使用 指南 引导' },
  { hash: '#/settings', key: 'nav_settings', sub: 'pal_nav_settings_sub', aliases: 'settings config appearance backup 设置 外观 备份' },
];

const palState = {
  open: false,
  opener: null,
  query: '',
  activeIdx: 0,
  items: [],
  cache: null,
  seq: 0,
};

function palRoot() {
  let el = document.getElementById('palette');
  if (!el) {
    el = document.createElement('div');
    el.id = 'palette';
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

function palGoHash(targetHash) {
  if (location.hash === targetHash) {
    router();
  } else {
    location.hash = targetHash;
  }
}

function palOnScriptPage() {
  return /^#\/(?:s|script)\/[0-9a-f]{12}$/.test(location.hash || '')
    && typeof cur !== 'undefined' && Boolean(cur && cur.script)
    && typeof openPrompter === 'function';
}

function palBothLangs(key) {
  return [I18N.zh[key] || '', I18N.en[key] || ''].join(' ');
}

function palBuildAllItems() {
  const out = [];
  if (palOnScriptPage()) {
    out.push({
      key: 'cmd:prompter',
      kind: 'action',
      badge: t('pal_cat_action'),
      title: t('pal_cmd_prompter'),
      sub: t('pal_cmd_prompter_sub'),
      keywords: '',
      search: palBothLangs('pal_cmd_prompter') + ' teleprompter prompter read 提词器 打开提词器 念稿',
      run: () => { openPrompter(); },
    });
  }
  out.push({
    key: 'cmd:new-script',
    kind: 'action',
    badge: t('pal_cat_action'),
    title: t('pal_cmd_new_script'),
    sub: t('pal_cmd_new_script_sub'),
    keywords: '',
    search: palBothLangs('pal_cmd_new_script') + ' new create script draft 新建脚本 写新脚本 创建',
    run: () => palGoHash('#/new'),
  });
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const themeKey = isDark ? 'pal_cmd_theme_light' : 'pal_cmd_theme_dark';
  out.push({
    key: 'cmd:theme',
    kind: 'action',
    badge: t('pal_cat_action'),
    title: t(themeKey),
    sub: t('pal_cmd_theme_sub'),
    keywords: '',
    search: palBothLangs('pal_cmd_theme_light') + ' ' + palBothLangs('pal_cmd_theme_dark') + ' theme dark light mode 浅色 深色 明暗 外观',
    keepFocus: true,
    run: () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      setLook('theme', next);
      if (location.hash === '#/settings' && typeof renderSettings === 'function') renderSettings();
    },
  });
  out.push({
    key: 'cmd:lang',
    kind: 'action',
    badge: t('pal_cat_action'),
    title: t('pal_cmd_lang'),
    sub: t('pal_cmd_lang_sub'),
    keywords: '',
    search: palBothLangs('pal_cmd_lang') + ' language chinese english zh en 中文 英文 语言',
    run: () => {
      lang = lang === 'zh' ? 'en' : 'zh';
      remember('lang', lang);
      keepWithCore();
      router();
    },
  });

  PAL_PAGES.forEach((p) => {
    out.push({
      key: 'nav:' + p.hash,
      kind: 'nav',
      badge: t('pal_cat_nav'),
      title: t(p.key),
      sub: t(p.sub),
      keywords: '',
      search: palBothLangs(p.key) + ' ' + p.aliases + ' ' + p.hash,
      run: () => palGoHash(p.hash),
    });
  });

  const data = palState.cache;
  if (data) {
    (data.scripts || []).forEach((sc) => {
      const title = String(sc.title || '').trim() || t('untitled');
      const kws = Array.isArray(sc.keywords) ? sc.keywords.join(' · ') : '';
      const subParts = [kws, String(sc.preview || '').trim()].filter(Boolean);
      out.push({
        key: 'script:' + sc.id,
        kind: 'script',
        badge: t('pal_cat_script'),
        title,
        sub: subParts.join(' — '),
        keywords: kws,
        search: [title, kws, sc.preview || ''].join(' '),
        run: () => palGoHash('#/script/' + sc.id),
      });
    });

    (data.plans || []).forEach((p) => {
      const title = String(p.title || '').trim() || t('untitled');
      const day = String(p.day || p.scheduled_for || p.posted_on || '').trim();
      const kws = Array.isArray(p.keywords) ? p.keywords.join(' · ') : '';
      const stLabel = p.status ? t('pl_status_' + p.status) : '';
      const sub = [day, stLabel, kws].filter(Boolean).join(' · ');
      out.push({
        key: 'plan:' + p.id,
        kind: 'plan',
        badge: t('pal_cat_plan'),
        title,
        sub,
        keywords: [day, kws].filter(Boolean).join(' '),
        search: [title, day, p.scheduled_for || '', p.posted_on || '', p.notes || '', kws].join(' '),
        run: () => {
          if (typeof plUi !== 'undefined') {
            plUi.filter = 'ALL';
            if (plUi.view !== 'list') {
              plUi.picked = p.id;
              if (day) plUi.cursor = day;
            }
          }
          palGoHash('#/plan');
        },
      });
    });

    (data.samples || []).forEach((sm) => {
      const title = String(sm.title || '').trim() || t('untitled');
      const kindSlug = ['script', 'subtitle', 'video'].includes(sm.kind) ? sm.kind : 'script';
      const sub = t('mt_kind_' + kindSlug);
      out.push({
        key: 'sample:' + sm.id,
        kind: 'sample',
        badge: t('pal_cat_sample'),
        title,
        sub,
        keywords: '',
        search: [title, String(sm.text || '').slice(0, 160)].join(' '),
        run: () => palGoHash('#/library'),
      });
    });

    (data.trends || []).forEach((tr) => {
      const kw = String(tr.keyword || '').trim();
      if (!kw) return;
      const note = String(lang === 'zh' ? (tr.note_zh || '') : (tr.note_en || tr.note_zh || '')).trim();
      out.push({
        key: 'trend:' + tr.id,
        kind: 'trend',
        badge: t('pal_cat_trend'),
        title: kw,
        sub: note || (t('tr_score') + ' ' + String(tr.score ?? '')),
        keywords: kw,
        search: [kw, tr.note_zh || '', tr.note_en || ''].join(' '),
        run: () => palGoHash('#/trends'),
      });
    });
  }
  return out;
}

function palSubseq(needle, haystack) {
  if (needle.length < 2 || needle.length > haystack.length) return 0;
  let ni = 0;
  let first = -1;
  let last = -1;
  for (let hi = 0; hi < haystack.length && ni < needle.length; hi += 1) {
    if (haystack[hi] === needle[ni]) {
      if (first === -1) first = hi;
      last = hi;
      ni += 1;
    }
  }
  if (ni < needle.length) return 0;
  const span = Math.max(1, last - first + 1);
  return Math.max(8, 36 - Math.min(24, span - needle.length));
}

function palScoreItem(tokens, item) {
  const titleL = String(item.title || '').toLowerCase();
  const kwL = String(item.keywords || '').toLowerCase();
  const searchL = String(item.search || '').toLowerCase();
  let total = 0;
  for (let i = 0; i < tokens.length; i += 1) {
    const tok = tokens[i];
    let s = 0;
    if (titleL === tok) s = 220;
    else if (titleL.startsWith(tok)) s = 155;
    else if (titleL.includes(tok)) s = 115;
    else if (kwL && kwL.includes(tok)) s = 95;
    else if (searchL.includes(tok)) s = 65;
    else {
      s = palSubseq(tok, titleL) || palSubseq(tok, searchL);
    }
    if (!s) return 0;
    total += s;
  }
  if (item.kind === 'script') total += 10;
  else if (item.kind === 'plan') total += 6;
  return total;
}

function palFilterItems(rawQuery) {
  const all = palBuildAllItems();
  const tokens = String(rawQuery || '').toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return all.slice(0, 60);
  const scored = [];
  all.forEach((item, idx) => {
    const score = palScoreItem(tokens, item);
    if (score > 0) scored.push({ item, score, idx });
  });
  scored.sort((a, b) => (b.score - a.score) || (a.idx - b.idx));
  return scored.slice(0, 60).map((x) => x.item);
}

function palPaintList() {
  const $list = document.getElementById('palette-list');
  const $input = document.getElementById('palette-input');
  if (!$list || !$input) return;
  const items = palState.items;
  if (!items.length) {
    palState.activeIdx = 0;
    $input.removeAttribute('aria-activedescendant');
    $list.innerHTML = `<div class="pal-empty">${esc(t(palState.cache ? 'pal_empty' : 'pal_loading'))}</div>`;
    return;
  }
  if (palState.activeIdx < 0 || palState.activeIdx >= items.length) palState.activeIdx = 0;
  $list.innerHTML = items.map((item, i) => {
    const active = i === palState.activeIdx;
    return `<div id="pal-opt-${i}" class="pal-item${active ? ' on' : ''}" role="option"` +
      ` aria-selected="${active ? 'true' : 'false'}" data-pal-idx="${i}" data-pal-kind="${esc(item.kind)}">` +
      `<div class="pal-item-main"><div class="pal-item-title">${esc(item.title)}</div>` +
      `${item.sub ? `<div class="pal-item-sub">${esc(item.sub)}</div>` : ''}</div>` +
      `<span class="pal-badge">${esc(item.badge)}</span></div>`;
  }).join('');
  const activeId = 'pal-opt-' + palState.activeIdx;
  $input.setAttribute('aria-activedescendant', activeId);
  const activeEl = document.getElementById(activeId);
  if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: 'nearest' });
}

function palSetActive(nextIdx) {
  const n = palState.items.length;
  if (!n) return;
  palState.activeIdx = ((nextIdx % n) + n) % n;
  const $list = document.getElementById('palette-list');
  const $input = document.getElementById('palette-input');
  if (!$list || !$input) return;
  Array.from($list.querySelectorAll('[role="option"]')).forEach((el, i) => {
    const on = i === palState.activeIdx;
    el.classList.toggle('on', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const activeId = 'pal-opt-' + palState.activeIdx;
  $input.setAttribute('aria-activedescendant', activeId);
  const activeEl = document.getElementById(activeId);
  if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: 'nearest' });
}

function palRefresh(preserveKey) {
  const prevKey = preserveKey && palState.items[palState.activeIdx] ? palState.items[palState.activeIdx].key : '';
  palState.items = palFilterItems(palState.query);
  if (prevKey) {
    const found = palState.items.findIndex((x) => x.key === prevKey);
    palState.activeIdx = found >= 0 ? found : 0;
  } else {
    palState.activeIdx = 0;
  }
  palPaintList();
}

function palExecute(idx) {
  const item = palState.items[idx];
  if (!item) return;
  const shouldRestore = Boolean(item.keepFocus);
  closePalette(shouldRestore);
  item.run();
}

async function palLoadIndex(seq) {
  const [scriptsRes, plansRes, samplesRes, trendsRes] = await Promise.all([
    api('GET', '/api/scripts').catch(() => ({ scripts: [] })),
    api('GET', '/api/plans').catch(() => ({ plans: [] })),
    api('GET', '/api/samples').catch(() => ({ samples: [] })),
    api('GET', '/api/trends').catch(() => ({ trends: [] })),
  ]);
  if (!palState.open || palState.seq !== seq) return;
  palState.cache = {
    scripts: Array.isArray(scriptsRes.scripts) ? scriptsRes.scripts : [],
    plans: Array.isArray(plansRes.plans) ? plansRes.plans : [],
    samples: Array.isArray(samplesRes.samples) ? samplesRes.samples : [],
    trends: Array.isArray(trendsRes.trends) ? trendsRes.trends : [],
  };
  const $pal = palRoot();
  $pal.setAttribute('data-pal-loaded', '1');
  palRefresh(Boolean(palState.query));
}

function openPalette() {
  const $ask = document.getElementById('ask');
  if ($ask && !$ask.hidden) return;
  if (typeof tpOpen !== 'undefined' && tpOpen) tpOpen.close();
  const $pal = palRoot();
  if (!palState.open) {
    palState.opener = document.activeElement;
  }
  palState.open = true;
  palState.query = '';
  palState.activeIdx = 0;
  palState.cache = null;
  palState.seq += 1;
  const seq = palState.seq;
  $pal.removeAttribute('data-pal-loaded');
  $pal.innerHTML = `<div class="pal-box" id="palette-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('pal_title'))}">` +
    `<div class="pal-head">${icon('search')}` +
    `<input type="text" id="palette-input" class="pal-input" role="combobox" aria-autocomplete="list"` +
    ` aria-expanded="true" aria-controls="palette-list" aria-label="${esc(t('pal_placeholder'))}"` +
    ` placeholder="${esc(t('pal_placeholder'))}" autocomplete="off" spellcheck="false">` +
    `<button type="button" class="btn ghost pal-close" id="palette-close" aria-label="${esc(t('close'))}">Esc</button></div>` +
    `<div id="palette-list" class="pal-list" role="listbox" aria-label="${esc(t('pal_results_label'))}"></div>` +
    `<div class="pal-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> ${esc(t('pal_foot_move'))}</span>` +
    `<span><kbd>↵</kbd> ${esc(t('pal_foot_run'))}</span>` +
    `<span><kbd>Esc</kbd> ${esc(t('pal_foot_close'))}</span></div></div>`;
  $pal.hidden = false;

  const $input = document.getElementById('palette-input');
  const $close = document.getElementById('palette-close');
  const $list = document.getElementById('palette-list');

  $input.oninput = () => {
    palState.query = $input.value;
    palRefresh(false);
  };
  $close.onclick = () => closePalette(true);
  $pal.onclick = (e) => {
    if (e.target === $pal) closePalette(true);
  };
  $list.onclick = (e) => {
    const opt = e.target.closest('[data-pal-idx]');
    if (opt) palExecute(Number(opt.dataset.palIdx));
  };

  palRefresh(false);
  $input.focus();
  palLoadIndex(seq);
}

function closePalette(restoreFocus) {
  const $pal = document.getElementById('palette');
  const wasOpen = palState.open;
  const before = palState.opener;
  palState.open = false;
  palState.opener = null;
  palState.cache = null;
  palState.items = [];
  palState.query = '';
  if ($pal) {
    $pal.hidden = true;
    $pal.removeAttribute('data-pal-loaded');
    $pal.innerHTML = '';
  }
  if (wasOpen && restoreFocus !== false && before && before.focus && document.contains(before)) {
    before.focus();
  }
}

document.addEventListener('keydown', (e) => {
  const keyLower = typeof e.key === 'string' ? e.key.toLowerCase() : '';
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && keyLower === 'k') {
    const $ask = document.getElementById('ask');
    if ($ask && !$ask.hidden) return;
    e.preventDefault();
    e.stopPropagation();
    if (palState.open) closePalette(true);
    else openPalette();
    return;
  }
  if (!palState.open) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    closePalette(true);
  } else if (e.key === 'ArrowDown' && !e.altKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    e.stopPropagation();
    palSetActive(palState.activeIdx + 1);
  } else if (e.key === 'ArrowUp' && !e.altKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    e.stopPropagation();
    palSetActive(palState.activeIdx - 1);
  } else if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) {
    const $close = document.getElementById('palette-close');
    if (document.activeElement === $close) {
      e.preventDefault();
      e.stopPropagation();
      closePalette(true);
      return;
    }
    if (palState.items.length) {
      e.preventDefault();
      e.stopPropagation();
      palExecute(palState.activeIdx);
    }
  } else if (e.key === 'Tab') {
    const $input = document.getElementById('palette-input');
    const $close = document.getElementById('palette-close');
    if (!$input || !$close) return;
    e.preventDefault();
    if (document.activeElement === $input) $close.focus();
    else $input.focus();
  }
}, true);
