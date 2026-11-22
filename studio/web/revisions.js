'use strict';
/* Script revision checkpoints, breath-by-breath diffs, non-destructive restore, home search/filter, and A/B variants.
   Loaded after refs.js and before pages.js. Every dynamic string passes through esc(). Restoring never asks for a
   destructive modal confirmation because the server automatically snapshots `before_restore` first. */

Object.assign(I18N.zh, {
  rv_btn: '历史版本',
  rv_save_btn: '存为节点',
  rv_title: '历史版本与对比',
  rv_close: '收起',
  rv_sub: 'AI 初稿、审稿修改前、标记已发布前都会自动留档；恢复任意旧版本前也会自动保存当前内容，不会丢稿。',
  rv_label_ph: '节点备注（可选，例如：改开头前 / 版本 A 定稿）',
  rv_save_named: '保存节点',
  rv_saving: '保存中…',
  rv_loading: '正在读取历史版本…',
  rv_empty: '还没有历史节点。点上方「存为节点」即可随时记下当前版本。',
  rv_kind_ai_draft: 'AI 初稿',
  rv_kind_before_critic: 'AI 润色前',
  rv_kind_before_posted: '发布留档前',
  rv_kind_before_restore: '恢复前自动留档',
  rv_kind_checkpoint: '手动节点',
  rv_meta_counts: '{lines} 句 · {units} 字 · {sec}秒',
  rv_diff_loading: '正在对比差异…',
  rv_diff_same: '这个节点与当前脚本内容完全一致。',
  rv_diff_summary: '与当前脚本相比：新增 {add} 句 · 删去 {del} 句 · 改动 {chg} 句 · 未变 {eq} 句',
  rv_diff_title_chg: '标题：{from} → {to}',
  rv_diff_sec_chg: '目标时长：{from}秒 → {to}秒',
  rv_diff_post_chg: '发布文案已改动',
  rv_diff_kw_chg: '目标关键词已改动',
  rv_line_tag: '第 {n} 句',
  rv_line_added: '当前新增（第 {n} 句）',
  rv_line_removed: '当前已删（原第 {n} 句）',
  rv_restore_btn: '恢复此版本',
  rv_restoring: '恢复中…',
  rv_saved_toast: '已保存历史节点',
  rv_restored_toast: '已恢复到所选版本；恢复前的内容已自动存为历史节点，不会丢失。',
  hm_search_ph: '搜索脚本标题、台词、关键词…',
  hm_filter_all: '全部',
  hm_filter_draft: '草稿',
  hm_filter_posted: '已发布',
  hm_filter_sponsored: '商单',
  hm_filter_none: '没有符合筛选条件的脚本。',
  dup_btn: '复制改版',
  dup_done: '已复制新改版草稿',
  ln_split: '拆分',
  ln_merge: '合并下一行',
  bi_btn: '双语协作',
  bi_col_zh: '中文台词与花字',
  bi_col_en: '英文对照与画面提示',
  bi_missing: '缺英文对照 {n} 行',
  bi_missing_one: '缺英文对照 1 行',
  bi_complete: '英文对照已补齐',
});

Object.assign(I18N.en, {
  rv_btn: 'History',
  rv_save_btn: 'Save checkpoint',
  rv_title: 'Version history & diff',
  rv_close: 'Close',
  rv_sub: 'Snapshots are saved automatically on AI drafts, before AI polish, and before marking posted. Restoring saves the current version first so nothing is lost.',
  rv_label_ph: 'Checkpoint note (optional, e.g. Before hook trim)',
  rv_save_named: 'Save checkpoint',
  rv_saving: 'Saving…',
  rv_loading: 'Loading version history…',
  rv_empty: 'No revisions yet. Use "Save checkpoint" to record the script as it stands.',
  rv_kind_ai_draft: 'AI draft',
  rv_kind_before_critic: 'Before AI polish',
  rv_kind_before_posted: 'Before posted',
  rv_kind_before_restore: 'Saved before restore',
  rv_kind_checkpoint: 'Checkpoint',
  rv_meta_counts: '{lines} lines · {units} units · {sec}s',
  rv_diff_loading: 'Computing breath-by-breath diff…',
  rv_diff_same: 'This revision is identical to the live script.',
  rv_diff_summary: 'Compared with live script: +{add} added · -{del} removed · {chg} changed · {eq} unchanged',
  rv_diff_title_chg: 'Title: {from} → {to}',
  rv_diff_sec_chg: 'Target length: {from}s → {to}s',
  rv_diff_post_chg: 'Post copy changed',
  rv_diff_kw_chg: 'Target keywords changed',
  rv_line_tag: 'Line {n}',
  rv_line_added: 'Added in live script (Line {n})',
  rv_line_removed: 'Removed from live script (was Line {n})',
  rv_restore_btn: 'Restore this version',
  rv_restoring: 'Restoring…',
  rv_saved_toast: 'Checkpoint saved',
  rv_restored_toast: 'Restored this revision. The previous state was saved as a checkpoint so nothing is lost.',
  hm_search_ph: 'Search titles, spoken lines, keywords…',
  hm_filter_all: 'All',
  hm_filter_draft: 'Drafts',
  hm_filter_posted: 'Posted',
  hm_filter_sponsored: 'Sponsored',
  hm_filter_none: 'No scripts match this search or filter.',
  dup_btn: 'Duplicate',
  dup_done: 'Created duplicate variant draft',
  ln_split: 'Split',
  ln_merge: 'Merge next',
  bi_btn: 'Partner view',
  bi_col_zh: 'Chinese spoken & on-screen text',
  bi_col_en: 'English gloss & shot direction',
  bi_missing: '{n} lines missing English gloss',
  bi_missing_one: '1 line missing English gloss',
  bi_complete: 'All spoken lines have an English gloss',
});

const rvState = {
  scriptId: '',
  open: false,
  loading: false,
  saving: false,
  restoring: false,
  diffLoading: false,
  items: [],
  selectedId: null,
  detail: null,
  label: '',
};

const homeListState = {
  query: '',
  filter: 'all',
  scripts: [],
  renderCard: null,
};

function rvResetForScript(scriptId) {
  if (rvState.scriptId === scriptId) return;
  rvState.scriptId = scriptId;
  rvState.open = false;
  rvState.loading = false;
  rvState.saving = false;
  rvState.restoring = false;
  rvState.diffLoading = false;
  rvState.items = [];
  rvState.selectedId = null;
  rvState.detail = null;
  rvState.label = '';
}

function rvKindLabel(kind) {
  const key = 'rv_kind_' + kind;
  return I18N[lang][key] ? t(key) : t('rv_kind_checkpoint');
}

function rvKindClass(kind) {
  if (kind === 'checkpoint') return 'ok';
  if (kind === 'before_restore' || kind === 'before_critic') return 'warn';
  return '';
}

async function rvLoadList(pickNewest) {
  if (!cur || !cur.script) return;
  const sid = cur.script.id;
  rvState.loading = true;
  rvPaint();
  try {
    const res = await api('GET', `/api/scripts/${sid}/revisions`);
    if (!cur || cur.script.id !== sid) return;
    rvState.items = Array.isArray(res.revisions) ? res.revisions : [];
    rvState.loading = false;
    if (pickNewest && rvState.items.length && !rvState.selectedId) {
      await rvSelectRevision(rvState.items[0].id);
      return;
    }
    rvPaint();
  } catch (e) {
    rvState.loading = false;
    rvPaint();
    showError(e);
  }
}

async function rvSelectRevision(revId) {
  if (!cur || !cur.script || !revId) return;
  const sid = cur.script.id;
  rvState.selectedId = revId;
  rvState.diffLoading = true;
  rvPaint();
  try {
    const res = await api('GET', `/api/scripts/${sid}/revisions/${revId}`);
    if (!cur || cur.script.id !== sid || rvState.selectedId !== revId) return;
    rvState.detail = res;
  } catch (e) {
    showError(e);
  } finally {
    rvState.diffLoading = false;
    rvPaint();
  }
}

async function rvSaveCheckpoint(customLabel) {
  if (!cur || !cur.script || rvState.saving) return;
  const sid = cur.script.id;
  const labelInput = document.getElementById('rv-label-input');
  const labelText = typeof customLabel === 'string'
    ? customLabel
    : (labelInput ? labelInput.value : rvState.label);
  rvState.saving = true;
  rvPaint();
  try {
    await flushSave();
    const res = await api('POST', `/api/scripts/${sid}/revisions`, { label: String(labelText || '').trim() });
    if (!cur || cur.script.id !== sid) return;
    rvState.label = '';
    rvState.open = true;
    rvState.items = Array.isArray(res.revisions) ? res.revisions : rvState.items;
    rvState.saving = false;
    toast(t('rv_saved_toast'));
    if (res.revision && res.revision.id) {
      await rvSelectRevision(res.revision.id);
    } else {
      rvPaint();
    }
  } catch (e) {
    rvState.saving = false;
    rvPaint();
    showError(e);
  }
}

async function rvRestore(revId) {
  if (!cur || !cur.script || !revId || rvState.restoring) return;
  const sid = cur.script.id;
  rvState.restoring = true;
  rvPaint();
  try {
    await flushSave();
    const res = await api('POST', `/api/scripts/${sid}/revisions/${revId}/restore`, {});
    if (!cur || cur.script.id !== sid) return;
    cur.script = res.script;
    if (res.check) {
      cur.findings = res.check.findings || [];
      cur.leftOut = res.check.findings_left_out || 0;
      cur.stats = res.check.stats || {};
      cur.coverage = res.check.keyword_coverage || [];
      cur.voice = res.check.voice_match || null;
      cur.wordlist = res.check.wordlist || null;
    }
    rvState.items = Array.isArray(res.revisions) ? res.revisions : [];
    rvState.restoring = false;
    rvState.selectedId = revId;
    rvState.detail = null;
    paintScript();
    toast(t('rv_restored_toast'));
    await rvSelectRevision(revId);
  } catch (e) {
    rvState.restoring = false;
    rvPaint();
    showError(e);
  }
}

async function rvDuplicateScript(scriptId, btn) {
  if (!scriptId) return;
  if (btn) btn.disabled = true;
  try {
    if (cur && cur.script && cur.script.id === scriptId) await flushSave();
    const res = await api('POST', `/api/scripts/${scriptId}/duplicate`, { lang });
    if (res && res.script && res.script.id) {
      toast(t('dup_done'));
      location.hash = '#/s/' + res.script.id;
    }
  } catch (e) {
    if (btn) btn.disabled = false;
    showError(e);
  }
}

function rvBreathsHtml(breaths) {
  if (!Array.isArray(breaths) || !breaths.length) return '';
  return `<div class="rv-breaths">${breaths.map((b) => {
    if (b.op === 'added') return `<span class="rv-breath add">+ ${esc(b.text)}</span>`;
    if (b.op === 'removed') return `<span class="rv-breath del">− ${esc(b.text)}</span>`;
    return `<span class="rv-breath eq">${esc(b.text)}</span>`;
  }).join('')}</div>`;
}

function rvDiffHtml() {
  if (rvState.diffLoading) return `<p class="hint">${esc(t('rv_diff_loading'))}</p>`;
  if (!rvState.detail || !rvState.detail.diff) return '';
  const { revision, diff } = rvState.detail;
  const counts = diff.counts || { added: 0, removed: 0, changed: 0, unchanged: 0 };
  const metaTags = [];
  if (diff.title && diff.title.changed) {
    metaTags.push(`<span class="tag warn">${esc(tf('rv_diff_title_chg', { from: diff.title.before, to: diff.title.after }))}</span>`);
  }
  if (diff.target_sec && diff.target_sec.changed) {
    metaTags.push(`<span class="tag warn">${esc(tf('rv_diff_sec_chg', { from: diff.target_sec.before, to: diff.target_sec.after }))}</span>`);
  }
  if (diff.post_changed) metaTags.push(`<span class="tag">${esc(t('rv_diff_post_chg'))}</span>`);
  if (diff.keywords_changed) metaTags.push(`<span class="tag">${esc(t('rv_diff_kw_chg'))}</span>`);

  const changedEntries = (diff.entries || []).filter((e) => e.status !== 'unchanged');
  const linesHtml = diff.identical
    ? `<p class="hint" data-rv-identical>${esc(t('rv_diff_same'))}</p>`
    : changedEntries.map((entry) => {
      let where = '';
      if (entry.status === 'added') where = tf('rv_line_added', { n: entry.cur_line_no });
      else if (entry.status === 'removed') where = tf('rv_line_removed', { n: entry.rev_line_no });
      else where = tf('rv_line_tag', { n: entry.cur_line_no || entry.rev_line_no });
      return `<div class="rv-diff-row" data-diff-status="${esc(entry.status)}">
        <div class="meta">${esc(where)}</div>
        ${rvBreathsHtml(entry.breaths)}
      </div>`;
    }).join('');

  return `<div class="rv-diff" data-rv-diff>
    <div class="row">
      <span class="meta">${esc(tf('rv_diff_summary', { add: counts.added, del: counts.removed, chg: counts.changed, eq: counts.unchanged }))}</span>
      <span class="spacer"></span>
      <button type="button" class="btn primary" id="rv-restore-btn" data-rv-restore="${revision.id}" ${rvState.restoring ? 'disabled' : ''}>
        ${esc(t(rvState.restoring ? 'rv_restoring' : 'rv_restore_btn'))}
      </button>
    </div>
    ${metaTags.length ? `<div class="rv-meta-tags">${metaTags.join('')}</div>` : ''}
    ${linesHtml}
  </div>`;
}

function rvPaint() {
  const box = document.getElementById('rv-box');
  if (!box || !cur || !cur.script) return;
  if (!rvState.open) {
    box.innerHTML = '';
    return;
  }
  const listHtml = rvState.loading
    ? `<p class="hint">${esc(t('rv_loading'))}</p>`
    : (!rvState.items.length
      ? `<p class="hint">${esc(t('rv_empty'))}</p>`
      : `<div class="rv-list" data-rv-list>${rvState.items.map((item) => `
          <button type="button" class="rv-item ${item.id === rvState.selectedId ? 'on' : ''}" data-rv-select="${item.id}">
            <div class="row">
              <span class="tag ${rvKindClass(item.kind)}">${esc(rvKindLabel(item.kind))}</span>
              ${item.label ? `<b>${esc(item.label)}</b>` : `<span>${esc(item.title)}</span>`}
              <span class="spacer"></span>
              <span class="meta">${esc(item.created_at)}</span>
            </div>
            <div class="meta">${esc(tf('rv_meta_counts', { lines: item.line_count, units: item.spoken_units, sec: item.target_sec }))}</div>
          </button>`).join('')}</div>`);

  box.innerHTML = `<div class="card rv-panel" id="rv-panel">
    <div class="row">
      <b>${esc(t('rv_title'))}</b>
      <span class="spacer"></span>
      <button type="button" class="btn ghost" data-rv-close>${esc(t('rv_close'))}</button>
    </div>
    <p class="hint">${esc(t('rv_sub'))}</p>
    <div class="row rv-save-row">
      <input type="text" id="rv-label-input" maxlength="80" placeholder="${esc(t('rv_label_ph'))}" value="${esc(rvState.label)}" ${rvState.saving ? 'disabled' : ''}>
      <button type="button" class="btn" id="rv-checkpoint-btn" data-rv-save-named ${rvState.saving ? 'disabled' : ''}>
        ${esc(t(rvState.saving ? 'rv_saving' : 'rv_save_named'))}
      </button>
    </div>
    ${listHtml}
    ${rvDiffHtml()}
  </div>`;
}

function rvAttach() {
  if (!cur || !cur.script) return;
  rvResetForScript(cur.script.id);
  const openBtn = document.getElementById('rev-open-btn');
  if (openBtn) {
    openBtn.onclick = () => {
      rvState.open = !rvState.open;
      if (rvState.open) rvLoadList(true);
      else rvPaint();
    };
  }
  const quickSaveBtn = document.getElementById('rev-save-btn');
  if (quickSaveBtn) {
    quickSaveBtn.onclick = () => rvSaveCheckpoint();
  }
  const dupBtn = document.getElementById('dup-script-btn');
  if (dupBtn) {
    dupBtn.onclick = (e) => rvDuplicateScript(cur.script.id, e.currentTarget);
  }
  const box = document.getElementById('rv-box');
  if (box) {
    box.oninput = (e) => {
      if (e.target && e.target.id === 'rv-label-input') rvState.label = e.target.value;
    };
    box.onclick = (e) => {
      if (e.target.closest('[data-rv-close]')) {
        rvState.open = false;
        rvPaint();
        return;
      }
      if (e.target.closest('[data-rv-save-named]')) {
        rvSaveCheckpoint();
        return;
      }
      const sel = e.target.closest('[data-rv-select]');
      if (sel) {
        rvSelectRevision(Number(sel.dataset.rvSelect));
        return;
      }
      const rest = e.target.closest('[data-rv-restore]');
      if (rest) {
        rvRestore(Number(rest.dataset.rvRestore));
      }
    };
  }
  rvPaint();
}

function rvAfterSave(script) {
  if (!rvState.open || !script || script.id !== rvState.scriptId) return;
  if (rvState.selectedId) rvSelectRevision(rvState.selectedId);
  else rvLoadList(false);
}

/* ---------- Home (#/) script search, filter pills & A/B duplicate action ---------- */
function rvMatchesHomeScript(s) {
  if (homeListState.filter === 'draft' && s.posted) return false;
  if (homeListState.filter === 'posted' && !s.posted) return false;
  if (homeListState.filter === 'sponsored' && s.mode !== 'SPONSORED') return false;
  const q = homeListState.query.trim().toLowerCase();
  if (!q) return true;
  const kw = Array.isArray(s.keywords) ? s.keywords.join(' ') : '';
  const hay = `${s.title || ''} ${s.spoken_text || ''} ${s.preview || ''} ${kw}`.toLowerCase();
  return hay.includes(q);
}

function rvPaintHomeCards() {
  const listEl = document.getElementById('home-cards');
  if (!listEl) return;
  const filtered = homeListState.scripts.filter(rvMatchesHomeScript);
  if (!filtered.length) {
    listEl.innerHTML = `<div class="empty" data-script-filter-empty>${esc(t('hm_filter_none'))}</div>`;
    return;
  }
  listEl.innerHTML = filtered.map((s) => homeListState.renderCard(s)).join('');
}

function rvPaintHomePills() {
  const pillsEl = document.getElementById('home-filter-pills');
  if (!pillsEl) return;
  const filters = ['all', 'draft', 'posted', 'sponsored'];
  pillsEl.innerHTML = filters.map((f) =>
    `<button type="button" class="chip ${homeListState.filter === f ? 'on' : ''}" data-script-filter="${f}">${esc(t('hm_filter_' + f))}</button>`
  ).join('');
}

function rvHomeListHtml(scripts) {
  homeListState.scripts = Array.isArray(scripts) ? scripts : [];
  if (!homeListState.scripts.length) {
    return `<div class="empty">${esc(t('empty'))}</div>`;
  }
  return `<div class="home-bar" id="home-bar">
      <input type="search" data-script-search placeholder="${esc(t('hm_search_ph'))}" value="${esc(homeListState.query)}">
      <div class="chips" id="home-filter-pills"></div>
    </div>
    <div id="home-cards"></div>`;
}

function rvAttachHome(renderCard) {
  homeListState.renderCard = renderCard;
  if (!homeListState.scripts.length) return;
  rvPaintHomePills();
  rvPaintHomeCards();
  const bar = document.getElementById('home-bar');
  if (bar) {
    const searchInput = bar.querySelector('[data-script-search]');
    if (searchInput) {
      searchInput.oninput = (e) => {
        homeListState.query = e.target.value;
        rvPaintHomeCards();
      };
    }
    bar.onclick = (e) => {
      const btn = e.target.closest('[data-script-filter]');
      if (!btn) return;
      homeListState.filter = btn.dataset.scriptFilter;
      rvPaintHomePills();
      rvPaintHomeCards();
    };
  }
  const cards = document.getElementById('home-cards');
  if (cards) {
    cards.onclick = (e) => {
      const dup = e.target.closest('[data-dup-script]');
      if (!dup) return;
      e.preventDefault();
      rvDuplicateScript(dup.dataset.dupScript, dup);
    };
  }
}
