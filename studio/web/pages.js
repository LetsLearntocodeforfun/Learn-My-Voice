'use strict';
/* Pages added after the first version: settings (with local models), library, ideas, voice actions.
   Loaded after app.js; boots the app at the end. */

Object.assign(I18N.zh, {
  nav_ideas: '选题建议', nav_library: '素材库',
  lib_title: '素材库', lib_sub: '把以前手写的脚本放进来，AI 会照着你的习惯写。填上数据，还能帮你分析选题。',
  upload: '上传脚本文件', upload_hint: '支持 .txt、.md、.docx，可以一次选多个',
  paste_title: '或者直接粘贴一篇', paste_ph: '把脚本内容粘贴到这里', add_to_lib: '加入素材库',
  lib_empty: '素材库还是空的。', imported_n: '已加入 {n} 篇', import_failed: '「{name}」读取失败',
  m_views: '播放', m_likes: '点赞', m_collects: '收藏', m_comments: '评论', sponsored: '商单',
  show_text: '查看 / 修改内容', lib_stats: '共 {n} 篇', confirm_delete_sample: '确定从素材库删除这一篇吗？', title_ph: '视频标题',
  learn_btn: '✨ 从素材库学习我的声音', learn_again: '重新学习', learning: '正在学习，可能要一两分钟…', learned: '学好了！',
  learn_need_samples: '先到「素材库」放几篇你写的脚本（建议 5 篇以上），再回来学习。',
  learn_source: '根据 {n} 篇脚本学习', rate_label: '我的语速（字/分钟）',
  rate_hint: '语速没法从文字里量出来。念一段一分钟的口播，数一下字数，填在这里。',
  rate_unconfirmed: '现在是默认值，还没确认', rate_saved: '语速已更新',
  ideas_title: '选题建议', ideas_sub: '根据你以前视频的内容和数据，建议接下来拍什么。',
  ideas_btn: '✨ 帮我想选题', ideas_again: '重新生成', thinking: '正在分析，可能要一两分钟…',
  ideas_need_samples: '先到「素材库」放几篇脚本，并填上播放、点赞等数据。', go_library: '去素材库',
  ideas_few_metrics: '只有 {n} 篇填了数据，建议会比较粗略。数据越多越准。',
  top_title: '表现最好的视频', interactions: '互动', rate: '互动率', use_idea: '用这个写脚本', evidence: '依据',
  kind_BUILD_ON_WINNER: '延续表现好的', kind_ADJACENT_EXPERIMENT: '新尝试',
  conf_low: '把握：低', conf_medium: '把握：中', conf_high: '把握：高', generated_at: '生成于',
  local_models: '本地模型', local_url: '本地模型地址',
  local_hint: '需要先在这台电脑上打开 Ollama 或 LM Studio，并下载好模型。内容不会离开这台电脑。',
  err_no_samples: '素材库里还没有可用的脚本。', err_bad_file: '这个文件读不了，请换成 .txt 或 .docx。',
  err_empty_text: '内容是空的。', err_bad_rate: '语速请填 100 到 500 之间的数字。', err_no_profile: '请先学习你的声音。',
});
Object.assign(I18N.en, {
  nav_ideas: 'Ideas', nav_library: 'Library',
  lib_title: 'Library', lib_sub: 'Add scripts she wrote by hand so AI writes in her habits. Add numbers to get topic analysis.',
  upload: 'Upload script files', upload_hint: '.txt, .md, .docx; several at once is fine',
  paste_title: 'Or paste one', paste_ph: 'Paste the script here', add_to_lib: 'Add to library',
  lib_empty: 'The library is empty.', imported_n: 'Added {n}', import_failed: 'Could not read "{name}"',
  m_views: 'Views', m_likes: 'Likes', m_collects: 'Collects', m_comments: 'Comments', sponsored: 'Sponsored',
  show_text: 'View / edit text', lib_stats: '{n} scripts', confirm_delete_sample: 'Remove this script from the library?', title_ph: 'Video title',
  learn_btn: '✨ Learn her voice from the library', learn_again: 'Learn again', learning: 'Learning, this can take a minute or two…', learned: 'Done!',
  learn_need_samples: 'Add a few of her scripts to the Library first (5 or more is better).',
  learn_source: 'Learned from {n} scripts', rate_label: 'Speaking rate (units/min)',
  rate_hint: 'Rate cannot be measured from text. Read one minute of voiceover aloud, count the characters, and enter it here.',
  rate_unconfirmed: 'Currently a default, not confirmed', rate_saved: 'Rate updated',
  ideas_title: 'Ideas', ideas_sub: 'What to film next, based on the content and numbers of her past videos.',
  ideas_btn: '✨ Suggest topics', ideas_again: 'Regenerate', thinking: 'Analysing, this can take a minute or two…',
  ideas_need_samples: 'Add scripts to the Library first, with views and likes filled in.', go_library: 'Open library',
  ideas_few_metrics: 'Only {n} videos have numbers, so suggestions will be rough. More data gives better results.',
  top_title: 'Best-performing videos', interactions: 'Interactions', rate: 'Rate', use_idea: 'Write this script', evidence: 'Based on',
  kind_BUILD_ON_WINNER: 'Builds on a winner', kind_ADJACENT_EXPERIMENT: 'Experiment',
  conf_low: 'Confidence: low', conf_medium: 'Confidence: medium', conf_high: 'Confidence: high', generated_at: 'Generated',
  local_models: 'Local model', local_url: 'Local server address',
  local_hint: 'Ollama or LM Studio must be running on this computer with a model downloaded. Nothing leaves the computer.',
  err_no_samples: 'There are no usable scripts in the library yet.', err_bad_file: 'This file could not be read. Use .txt or .docx.',
  err_empty_text: 'The text is empty.', err_bad_rate: 'Enter a rate between 100 and 500.', err_no_profile: 'Learn her voice first.',
});

const tf = (key, vars) => t(key).replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : ''));
const num = (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US'));

async function busy(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true; btn.textContent = label;
  try { return await fn(); } catch (e) { showError(e); return undefined; }
  finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = original; } }
}

/* ---------- settings ---------- */
const LOCAL_PRESETS = { Ollama: 'http://127.0.0.1:11434/v1', 'LM Studio': 'http://127.0.0.1:1234/v1' };
const settingsForm = { provider: '', models: [], model: '', localUrl: '' };

function renderSettings() {
  const st = appState.settings;
  if (!settingsForm.provider) { settingsForm.provider = st.provider || 'gemini'; settingsForm.model = st.model || ''; }
  if (!settingsForm.localUrl) settingsForm.localUrl = st.local_base_url || LOCAL_PRESETS.Ollama;
  const p = settingsForm.provider;
  const isLocal = p === 'local';
  const names = { gemini: 'Google Gemini', openai: 'OpenAI', local: t('local_models') };
  const models = settingsForm.models.length ? settingsForm.models : (st.provider === p && st.model ? [st.model] : []);
  const access = isLocal
    ? `<label class="field" for="key">${esc(t('local_url'))}</label>
       <input type="text" id="key" autocomplete="off" spellcheck="false" value="${esc(settingsForm.localUrl)}">
       <div class="chips" style="margin-top:8px">${Object.keys(LOCAL_PRESETS).map((k) =>
         `<button type="button" class="chip" data-preset="${esc(k)}">${esc(k)}</button>`).join('')}</div>
       <p class="hint">${esc(t('local_hint'))}</p>`
    : `<label class="field" for="key">${esc(t('api_key'))}</label>
       <input type="password" id="key" autocomplete="off" placeholder="${esc(t(st.keys[p] ? 'key_saved_ph' : 'key_ph'))}">`;
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('settings_title'))}</h1><p class="sub">${esc(t('settings_sub'))}</p>
    ${st.configured ? `<div class="card meta">${esc(t('current'))}: ${esc(names[st.provider])} · ${esc(st.model)}</div>` : ''}
    <label class="field">${esc(t('provider'))}</label>
    <div class="chips">${Object.keys(names).map((k) =>
      `<button type="button" class="chip ${k === p ? 'on' : ''}" data-provider="${k}">${esc(names[k])}
       <span class="hint">· ${esc(t(st.keys[k] ? 'connected' : 'not_connected'))}</span></button>`).join('')}</div>
    ${access}
    <div class="row" style="margin-top:14px"><button class="btn" id="connect">${esc(t('connect'))}</button></div>
    <label class="field" for="model">${esc(t('model'))}</label>
    <select id="model" ${models.length ? '' : 'disabled'}>
      <option value="">${esc(t('pick_model'))}</option>
      ${models.map((m) => `<option value="${esc(m)}" ${m === settingsForm.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}
    </select>
    <div class="row" style="margin-top:24px"><button class="btn primary big" id="save">${esc(t('save'))}</button></div></div>`;

  const $key = document.getElementById('key');
  const access_body = () => (isLocal ? { provider: p, base_url: $key.value.trim() } : { provider: p, key: $key.value });
  if (isLocal) $key.oninput = () => { settingsForm.localUrl = $key.value; };
  $view.querySelectorAll('[data-preset]').forEach((b) => {
    b.onclick = () => { settingsForm.localUrl = LOCAL_PRESETS[b.dataset.preset]; settingsForm.models = []; renderSettings(); };
  });
  $view.querySelectorAll('[data-provider]').forEach((b) => {
    b.onclick = () => { settingsForm.provider = b.dataset.provider; settingsForm.models = []; settingsForm.model = ''; renderSettings(); };
  });
  const $model = document.getElementById('model');
  $model.onchange = () => { settingsForm.model = $model.value; };
  document.getElementById('connect').onclick = async (e) => {
    e.currentTarget.disabled = true;
    try {
      const out = await api('POST', '/api/settings/models', access_body());
      appState.settings = out.settings; settingsForm.models = out.models;
      if (!out.models.includes(settingsForm.model)) settingsForm.model = out.models.length === 1 ? out.models[0] : '';
    } catch (err) { showError(err); }
    renderSettings();
  };
  document.getElementById('save').onclick = async () => {
    if (!settingsForm.model) return toast(t('pick_model'));
    try {
      const out = await api('POST', '/api/settings', Object.assign(access_body(), { model: settingsForm.model }));
      appState.settings = out.settings; toast(t('settings_saved')); renderSettings();
    } catch (err) { showError(err); }
  };
}

/* ---------- library ---------- */
async function readTextFile(file) {
  const bytes = await file.arrayBuffer();
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch (e) { return new TextDecoder('gb18030').decode(bytes); }   // older Chinese-encoded .txt
}

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

async function importFiles(files) {
  let added = 0;
  for (const file of files) {
    const title = file.name.replace(/\.[^.]+$/, '');
    try {
      const body = /\.docx$/i.test(file.name)
        ? { title, docx_base64: await fileToBase64(file) }
        : { title, text: await readTextFile(file) };
      await api('POST', '/api/samples', body);
      added += 1;
    } catch (e) { toast(tf('import_failed', { name: file.name })); }
  }
  return added;
}

function sampleHtml(s) {
  const metric = (f) => `<label>${esc(t('m_' + f))}<input type="number" min="0" inputmode="numeric" data-f="${f}" value="${s[f] === null ? '' : esc(s[f])}"></label>`;
  return `<div class="card sample" data-id="${esc(s.id)}">
    <div class="row">
      <input type="text" class="sample-title" data-f="title" value="${esc(s.title)}" placeholder="${esc(t('title_ph'))}">
      <label class="check"><input type="checkbox" data-f="is_sponsored" ${s.is_sponsored ? 'checked' : ''}> ${esc(t('sponsored'))}</label>
      <button class="btn ghost" data-del aria-label="${esc(t('delete'))}">✕</button>
    </div>
    <div class="metrics">${['views', 'likes', 'collects', 'comments'].map(metric).join('')}</div>
    <details><summary>${esc(t('show_text'))} · ${spokenUnits(s.text)}${esc(t('chars'))}</summary>
      <textarea data-f="text" rows="8">${esc(s.text)}</textarea></details>
  </div>`;
}

async function renderLibrary() {
  const { samples } = await api('GET', '/api/samples');
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('lib_title'))}</h1><p class="sub">${esc(t('lib_sub'))}</p>
    <div class="card">
      <div class="row"><label class="btn primary" for="files">${esc(t('upload'))}</label>
        <input type="file" id="files" multiple accept=".txt,.md,.docx,text/plain" hidden>
        <span class="hint">${esc(t('upload_hint'))}</span></div>
      <label class="field" for="paste">${esc(t('paste_title'))}</label>
      <textarea id="paste" rows="4" placeholder="${esc(t('paste_ph'))}"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn" id="paste-add">${esc(t('add_to_lib'))}</button></div>
    </div>
    <p class="meta">${samples.length ? esc(tf('lib_stats', { n: samples.length })) : ''}</p>
    <div id="samples">${samples.map(sampleHtml).join('') || `<div class="empty">${esc(t('lib_empty'))}</div>`}</div></div>`;

  document.getElementById('files').onchange = async (e) => {
    const added = await importFiles(Array.from(e.target.files));
    if (added) toast(tf('imported_n', { n: added }));
    renderLibrary();
  };
  const $paste = document.getElementById('paste');
  $paste.oninput = () => autoGrow($paste);
  document.getElementById('paste-add').onclick = async () => {
    try {
      await api('POST', '/api/samples', { text: $paste.value });
      toast(tf('imported_n', { n: 1 }));
      renderLibrary();
    } catch (err) { showError(err); }
  };
  const $list = document.getElementById('samples');
  $list.onchange = async (e) => {
    const card = e.target.closest('.sample');
    const field = e.target.dataset.f;
    if (!card || !field) return;
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    try { await api('PUT', '/api/samples/' + card.dataset.id, { [field]: value }); toast(t('saved')); }
    catch (err) { showError(err); }
  };
  $list.onclick = async (e) => {
    if (!e.target.closest('[data-del]')) return;
    if (!confirm(t('confirm_delete_sample'))) return;
    await api('DELETE', '/api/samples/' + e.target.closest('.sample').dataset.id);
    renderLibrary();
  };
}

/* ---------- voice actions ---------- */
function paintVoiceActions(dna, isExample) {
  const el = document.getElementById('voice-actions');
  if (!el) return;
  const ready = appState.settings.configured && appState.sample_count > 0;
  const source = (dna && dna.source_corpus_summary) || {};
  const rate = dna && dna.cadence_and_syntax ? dna.cadence_and_syntax.target_speaking_rate_cpm : '';
  let hint = '';
  if (!appState.sample_count) hint = `${esc(t('learn_need_samples'))} <a href="#/library">${esc(t('go_library'))}</a>`;
  else if (!appState.settings.configured) hint = `${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a>`;
  const rateBox = isExample ? '' : `<div class="card">
      <label class="field" style="margin-top:0" for="rate">${esc(t('rate_label'))}
        ${source.speaking_rate_confirmed ? '' : `<span class="hint">· ${esc(t('rate_unconfirmed'))}</span>`}</label>
      <div class="row"><input type="number" id="rate" min="100" max="500" style="width:140px" value="${esc(rate)}">
        <button class="btn" id="rate-save">${esc(t('save'))}</button></div>
      <p class="hint">${esc(t('rate_hint'))}</p></div>`;
  el.innerHTML = `${rateBox}
    <div class="row"><button class="btn primary big" id="learn" ${ready ? '' : 'disabled'}>${esc(t(isExample ? 'learn_btn' : 'learn_again'))}</button>
      ${!isExample && source.organic_scripts_analyzed ? `<span class="meta">${esc(tf('learn_source', { n: source.organic_scripts_analyzed }))} · ${esc(source.learned_at || '')}</span>` : ''}</div>
    ${hint ? `<p class="hint">${hint}</p>` : ''}`;
  document.getElementById('learn').onclick = (e) => busy(e.currentTarget, t('learning'), async () => {
    await api('POST', '/api/voice/learn', {});
    toast(t('learned'));
    await router();
  });
  const $save = document.getElementById('rate-save');
  if ($save) $save.onclick = async () => {
    try {
      await api('PUT', '/api/voice/rate', { cpm: Number(document.getElementById('rate').value) });
      toast(t('rate_saved'));
      await router();
    } catch (err) { showError(err); }
  };
}

/* ---------- ideas ---------- */
function ideaHtml(idea, i) {
  const evidence = idea.evidence_titles.length
    ? `<div class="meta">${esc(t('evidence'))}：${idea.evidence_titles.map((x) => `<span class="tag">${esc(x)}</span>`).join('')}</div>` : '';
  const label = (prefix, value) => {
    if (!value) return '';
    const key = prefix + value;
    return `<span class="tag">${esc(t(key) === key ? value : t(key))}</span>`;
  };
  const why = lang === 'zh' ? idea.why_zh : (idea.why_en || idea.why_zh);
  return `<div class="card idea">
    <div class="row"><h3>${esc(lang === 'zh' ? idea.title_zh : (idea.title_en || idea.title_zh))}</h3><span class="spacer"></span>
      ${label('kind_', idea.kind)}${label('conf_', idea.confidence)}</div>
    ${why ? `<p>${esc(why)}</p>` : ''}
    ${evidence}
    <div class="row" style="margin-top:12px"><button class="btn primary" data-idea="${i}">${esc(t('use_idea'))}</button>
      <span class="meta">${esc(t('fmt_' + idea.video_format))} · ${idea.target_duration_sec}${esc(t('sec'))}</span></div>
  </div>`;
}

async function renderIdeas() {
  const { performance, result } = await api('GET', '/api/recommendations');
  const top = performance.rows.slice(0, 5);
  const table = top.length ? `<div class="card"><h3>${esc(t('top_title'))}</h3><table class="perf">
      <tr><th></th><th>${esc(t('m_views'))}</th><th>${esc(t('interactions'))}</th><th>${esc(t('rate'))}</th></tr>
      ${top.map((r) => `<tr><td>${esc(r.title)}${r.is_sponsored ? ` <span class="tag">${esc(t('sponsored'))}</span>` : ''}</td>
        <td>${num(r.views)}</td><td>${num(r.interactions)}</td>
        <td>${r.interaction_rate_pct === null ? '–' : r.interaction_rate_pct + '%'}</td></tr>`).join('')}</table></div>` : '';
  const hasSamples = appState.sample_count > 0;
  const ready = appState.settings.configured && hasSamples;
  let hint = '';
  if (!hasSamples) hint = `${esc(t('ideas_need_samples'))} <a href="#/library">${esc(t('go_library'))}</a>`;
  else if (!appState.settings.configured) hint = `${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a>`;
  else if (performance.with_metrics < 8) hint = esc(tf('ideas_few_metrics', { n: performance.with_metrics }));
  const ideas = result ? result.ideas : [];
  const note = result ? (lang === 'zh' ? result.data_note_zh : (result.data_note_en || result.data_note_zh)) : '';
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('ideas_title'))}</h1><p class="sub">${esc(t('ideas_sub'))}</p>
    ${hint ? `<div class="banner">${hint}</div>` : ''}
    ${table}
    <div class="row" style="margin:18px 0"><button class="btn primary big" id="ideas-btn" ${ready ? '' : 'disabled'}>${esc(t(result ? 'ideas_again' : 'ideas_btn'))}</button>
      ${result ? `<span class="meta">${esc(t('generated_at'))} ${esc(fmtDate(result.generated_at))}</span>` : ''}</div>
    ${note ? `<p class="meta">${esc(note)}</p>` : ''}
    <div id="ideas">${ideas.map(ideaHtml).join('')}</div></div>`;
  document.getElementById('ideas-btn').onclick = (e) => busy(e.currentTarget, t('thinking'), async () => {
    await api('POST', '/api/recommendations', {});
    await renderIdeas();
  });
  document.getElementById('ideas').onclick = (e) => {
    const btn = e.target.closest('[data-idea]');
    if (!btn) return;
    const idea = ideas[Number(btn.dataset.idea)];
    draft.notes = idea.title_zh + '\n' + idea.script_notes_zh;
    draft.target_sec = idea.target_duration_sec;
    draft.video_format = idea.video_format;
    location.hash = '#/new';
  };
}

window.addEventListener('hashchange', router);
window.addEventListener('beforeunload', () => { if (dirty) saveNow(); });
router();
