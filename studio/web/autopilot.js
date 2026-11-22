'use strict';
/* Autopilot (#/autopilot): what the app does by itself while it is open, within what the creator chose
   (studio/autopilot.py, studio/autosuggest.py). Loaded before pages.js; every value from data goes through esc(). */

Object.assign(I18N.zh, {
  nav_autopilot: '自动学习',
  ap_title: '自动学习与后台助手',
  ap_sub: '在应用打开期间按你设定的边界自动整理热词、素材草稿、拍摄计划关键词建议和选题，每一步都留有记录并可一键撤销。',
  ap_off_banner: '这次运行已关闭后台自动任务：应用是用 LEARN_MY_VOICE_NO_AUTOPILOT=1 启动的，或者是由测试 / 试运行脚本启动的。当前会话不会自动运行任何任务。',
  ap_master_title: '总开关与运行模式',
  ap_enable_label: '开启自动学习（仅在本应用打开期间运行）',
  ap_status_active: '运行中',
  ap_status_paused: '已暂停',
  ap_level_label: '自动程度',
  ap_level_suggest: '只给建议',
  ap_level_auto: '在安全边界内自动应用',
  ap_level_suggest_d: '所有结果都先作为待确认建议展示（计划关键词建议、候选选题、声音档案提案），点确认后才生效。',
  ap_level_auto_d: '在安全边界内自动落盘（如自动刷新上次也是自动生成的选题、采纳重复出现的改稿教训），绝不自动发布、绝不直接启用未审核的声音档案、也绝不覆盖你手写或改过的脚本与选题。',
  ap_cap_label: '每日自动模型调用上限',
  ap_cap_spent: '今日已用 {s} / {m} 次（手动点「立即运行」不计入上限）',
  ap_cap_save: '保存上限',
  ap_tasks_title: '自动任务开关',
  ap_tasks_sub: '每个任务都可以单独开关；只有总开关和该任务同时开启时才会按周期运行。',
  ap_tasks_empty: '当前没有已注册的后台任务。',
  ap_task_calls_model: '会调用模型',
  ap_task_local_only: '纯本地比对（不调模型）',
  ap_task_every_min: '每 {n} 分钟检查',
  ap_task_every_hour: '每 {n} 小时检查',
  ap_task_every_day: '每 {n} 天检查',
  ap_task_last: '上次：{s} · {t}',
  ap_task_never: '还没有运行过',
  ap_task_next: '下次最早：{t}',
  ap_run_now: '立即运行',
  ap_running_now: '运行中…',
  ap_task_trends_refresh_title: '刷新热词观察（trends.refresh）',
  ap_task_trends_refresh_d: '按下方勾选的热词来源整理最近出现的热词与上升趋势。',
  ap_task_voice_ingest_folder_title: '导入监控文件夹素材（voice.ingest_folder）',
  ap_task_voice_ingest_folder_d: '把监控文件夹里的新视频、音频、字幕或文稿导入素材库等待你确认。',
  ap_task_voice_relearn_title: '更新声音档案提案（voice.relearn）',
  ap_task_voice_relearn_d: '当你确认过的新非商单素材积累到一定数量时，生成一份新的声音档案提案供你在「我的声音」里核对。',
  ap_task_lessons_accept_recurring_title: '整理常改口癖（lessons.accept_recurring）',
  ap_task_lessons_accept_recurring_d: '把你在多篇脚本里反复改掉的 AI 书面词整理成改稿教训。',
  ap_task_plans_suggest_title: '为近期计划匹配热词建议（plans.suggest）',
  ap_task_plans_suggest_d: '纯本地比对未来 14 天内或未定日期的计划，把匹配的跟踪热词挂在计划卡片旁供你采纳或忽略，绝不自动改动计划。',
  ap_task_ideas_refresh_title: '刷新候选选题（ideas.refresh）',
  ap_task_ideas_refresh_d: '当顶部热词变化或距上次刷新超过数天时生成一组新选题，绝不覆盖你自己保存的选题。',
  ap_voice_title: '监控文件夹与语音转写',
  ap_voice_sub: '指定一个本地文件夹，放入其中的视频、字幕或文稿可由导入任务读取；放在 sponsored/ 子文件夹里的内容会自动标为商单，不混入日常声音学习。',
  ap_watch_dir_label: '监控文件夹路径',
  ap_watch_dir_ph: '例如：/Users/name/Movies/RedNoteDrafts（留空则不监控）',
  ap_folder_ok: '文件夹已找到',
  ap_folder_missing: '路径尚未创建或不存在',
  ap_route_label: '音视频转写方式',
  ap_route_off: '关闭音视频转写（只读字幕与文稿）',
  ap_route_local: '本机转写（文字与声音都不离开这台电脑）',
  ap_route_cloud: '云端 AI 转写（仅在选此项时才会把音视频上传到设置里选的 AI 服务）',
  ap_voice_counts: '待处理 {r} · 已导入 {i} · 等待确认 {w} · 失败 {f}',
  ap_voice_save: '保存文件夹设置',
  ap_sources_title: '热词来源设置',
  ap_sources_sub: '只使用你明确开启的来源。本应用绝不会抓取或访问 xiaohongshu.com / rednote.com 页面。',
  ap_src_ai_label: '允许通过设置里支持联网的 AI 服务查公开热词',
  ap_src_files_label: '读取监控文件夹里的热词表格（.csv / .tsv / .xlsx）',
  ap_src_pages_label: '公开 HTTPS 热词参考网页（每行一个，最多 {max} 个，不含小红书域名）',
  ap_src_pages_ph: '每行填写一个公开的 https 网页地址（不要填 xiaohongshu.com 或 rednote.com）',
  ap_sources_save: '保存热词来源',
  ap_runs_title: '运行记录与一键撤销',
  ap_runs_sub: '每次自动或手动运行的耗时、模型调用次数与改动摘要都在这里；凡是可逆的改动都可以一键撤销。',
  ap_runs_empty: '还没有运行记录。',
  ap_run_status_done: '完成',
  ap_run_status_failed: '失败',
  ap_run_status_skipped: '已跳过',
  ap_run_status_running: '运行中',
  ap_run_status_interrupted: '已中断',
  ap_run_forced: '手动触发',
  ap_run_calls: '模型调用 {n} 次',
  ap_run_undone: '已撤销',
  ap_undo_btn: '撤销本次更改',
  ap_undone_toast: '已撤销这次运行的更改',
  ap_saved_toast: '自动学习设置已保存',
  ap_started_toast: '任务已开始运行',
  ap_sum_plans: '检查了 {p} 条计划，新增 {a} 条热词建议',
  ap_sum_ideas_placed: '生成了 {n} 个选题并更新到选题页',
  ap_sum_ideas_waiting: '生成了 {n} 个候选选题，正在选题页等待你确认',
  ap_sum_daily_limit: '已达到今日自动模型调用上限，本次未调用模型',
});

Object.assign(I18N.en, {
  nav_autopilot: 'Autopilot',
  ap_title: 'Autopilot',
  ap_sub: 'While the app is open, run bounded background helpers for {@names} keywords, folder drafts, plan keyword suggestions and topic ideas — every run is logged and reversible.',
  ap_off_banner: 'Background runs are switched off for this session: the app was started with LEARN_MY_VOICE_NO_AUTOPILOT=1, or by a test or trial script. Nothing runs automatically in this session.',
  ap_master_title: 'Master switch & mode',
  ap_enable_label: 'Enable Autopilot (runs only while this app is open)',
  ap_status_active: 'Active',
  ap_status_paused: 'Paused',
  ap_level_label: 'Mode',
  ap_level_suggest: 'Suggest only',
  ap_level_auto: 'Apply within safety bounds',
  ap_level_suggest_d: 'Keep every output waiting as a suggestion (plan keyword chips, waiting topic ideas, voice profile proposals) until {@name} accepts it.',
  ap_level_auto_d: 'Apply safe updates automatically (such as replacing an earlier automatic idea set or recording recurring edit lessons); never publishes, never activates an unreviewed voice profile, and never overwrites {@names} own scripts or saved ideas.',
  ap_cap_label: 'Daily automatic model call limit',
  ap_cap_spent: 'Spent today: {s} / {m} (manual "Run now" does not count against the daily cap)',
  ap_cap_save: 'Save limit',
  ap_tasks_title: 'Tasks',
  ap_tasks_sub: 'Each task has its own switch and runs on schedule only when both Autopilot and that task are switched on.',
  ap_tasks_empty: 'No background tasks are registered.',
  ap_task_calls_model: 'Calls AI model',
  ap_task_local_only: 'Local matching only (no model call)',
  ap_task_every_min: 'Every {n} min',
  ap_task_every_hour: 'Every {n} hr',
  ap_task_every_day: 'Every {n} d',
  ap_task_last: 'Last run: {s} · {t}',
  ap_task_never: 'Never run yet',
  ap_task_next: 'Earliest next check: {t}',
  ap_run_now: 'Run now',
  ap_running_now: 'Running…',
  ap_task_trends_refresh_title: 'Refresh trend sightings (trends.refresh)',
  ap_task_trends_refresh_d: 'Collect recent trend sightings from the sources switched on below.',
  ap_task_voice_ingest_folder_title: 'Import watched folder drafts (voice.ingest_folder)',
  ap_task_voice_ingest_folder_d: 'Bring new media, subtitles or scripts from the watched folder into {@names} library for confirmation.',
  ap_task_voice_relearn_title: 'Propose updated voice profile (voice.relearn)',
  ap_task_voice_relearn_d: 'Draft a new voice profile proposal for review once enough confirmed organic material accumulates.',
  ap_task_lessons_accept_recurring_title: 'Record recurring edit lessons (lessons.accept_recurring)',
  ap_task_lessons_accept_recurring_d: 'Keep phrases {@name} repeatedly edits out across scripts as writing lessons.',
  ap_task_plans_suggest_title: 'Suggest trend keywords for upcoming plans (plans.suggest)',
  ap_task_plans_suggest_d: 'Match tracked keywords locally against unscheduled or next-14-day plans without ever modifying {@names} plans.',
  ap_task_ideas_refresh_title: 'Refresh topic ideas (ideas.refresh)',
  ap_task_ideas_refresh_d: 'Generate a fresh set of topic ideas when top trends shift, without overwriting ideas {@name} saved.',
  ap_voice_title: 'Watched folder & voice learning',
  ap_voice_sub: 'Point to a local folder of videos, subtitles or scripts; files inside the sponsored/ subfolder are marked sponsored automatically so they stay separate from {@names} organic voice.',
  ap_watch_dir_label: 'Watched folder path',
  ap_watch_dir_ph: 'e.g. /Users/name/Movies/RedNoteDrafts (leave empty to watch nothing)',
  ap_folder_ok: 'Folder exists',
  ap_folder_missing: 'Folder not found yet',
  ap_route_label: 'Media transcription route',
  ap_route_off: 'Off (import subtitle and text files only)',
  ap_route_local: 'On-device transcription (audio stays on this computer)',
  ap_route_cloud: 'Cloud AI transcription (uploads media to the AI service in Settings only when this option is chosen)',
  ap_voice_counts: 'Ready {r} · Imported {i} · Waiting review {w} · Failed {f}',
  ap_voice_save: 'Save folder settings',
  ap_sources_title: 'Trend sources',
  ap_sources_sub: 'Only sources switched on below are used. Pages on xiaohongshu.com and rednote.com are never scraped or fetched.',
  ap_src_ai_label: 'Allow web trend lookup via the web-capable AI service in Settings',
  ap_src_files_label: 'Import keyword tables (.csv / .tsv / .xlsx) found in the watched folder',
  ap_src_pages_label: 'Public HTTPS trend page URLs (one per line, up to {max}; Xiaohongshu / RedNote domains are refused)',
  ap_src_pages_ph: 'One public https URL per line (never xiaohongshu.com or rednote.com)',
  ap_sources_save: 'Save trend sources',
  ap_runs_title: 'Run log & Undo',
  ap_runs_sub: 'Every background or manual run is listed with its timestamp, model calls and summary; reversible runs offer one-click Undo.',
  ap_runs_empty: 'No runs recorded yet.',
  ap_run_status_done: 'Done',
  ap_run_status_failed: 'Failed',
  ap_run_status_skipped: 'Skipped',
  ap_run_status_running: 'Running',
  ap_run_status_interrupted: 'Interrupted',
  ap_run_forced: 'Manual',
  ap_run_calls: '{n} model call(s)',
  ap_run_undone: 'Undone',
  ap_undo_btn: 'Undo',
  ap_undone_toast: 'Undid the changes from that run',
  ap_saved_toast: 'Autopilot settings saved',
  ap_started_toast: 'Task started',
  ap_sum_plans: 'Checked {p} plan(s), suggested {a} keyword(s)',
  ap_sum_ideas_placed: 'Generated {n} topic idea(s) and updated the Ideas page',
  ap_sum_ideas_waiting: 'Generated {n} topic idea(s), waiting for confirmation on the Ideas page',
  ap_sum_daily_limit: 'Daily automatic model call limit reached; no model call made',
});

const apUi = {
  status: null,
  voice: null,
  sources: null,
  timer: 0,
};

function apStopTimer() {
  clearInterval(apUi.timer);
  apUi.timer = 0;
}

function apTaskKey(name) {
  return String(name || '').replace(/[^a-z0-9]+/gi, '_');
}

function apTaskTitle(name) {
  const key = 'ap_task_' + apTaskKey(name) + '_title';
  return I18N.zh[key] !== undefined ? t(key) : String(name || '');
}

function apTaskDesc(name) {
  const key = 'ap_task_' + apTaskKey(name) + '_d';
  return I18N.zh[key] !== undefined ? t(key) : '';
}

function apCadenceText(everySec) {
  const sec = Math.max(60, Number(everySec) || 600);
  if (sec >= 86400 && sec % 86400 === 0) return tf('ap_task_every_day', { n: Math.round(sec / 86400) });
  if (sec >= 3600 && sec % 3600 === 0) return tf('ap_task_every_hour', { n: Math.round(sec / 3600) });
  return tf('ap_task_every_min', { n: Math.max(1, Math.round(sec / 60)) });
}

function apRunStatusLabel(status) {
  const key = 'ap_run_status_' + status;
  return I18N.zh[key] !== undefined ? t(key) : String(status || '');
}

function apRunStatusTagClass(status) {
  if (status === 'done') return 'ok';
  if (status === 'failed') return 'no';
  if (status === 'skipped' || status === 'interrupted') return 'warn';
  return '';
}

function apRunSummaryText(run) {
  const sum = (run && run.summary && typeof run.summary === 'object') ? run.summary : {};
  if (sum.reason === 'daily_limit') return t('ap_sum_daily_limit');
  if (typeof sum.plans_looked_at === 'number' && typeof sum.added === 'number') {
    const named = Array.isArray(sum.suggested)
      ? sum.suggested.map((x) => (x && x.keyword ? String(x.keyword) : '')).filter(Boolean).join('、')
      : '';
    return tf('ap_sum_plans', { p: sum.plans_looked_at, a: sum.added }) + (named ? `（${named}）` : '');
  }
  if (sum.refreshed === true && typeof sum.ideas === 'number') {
    return tf(sum.in_place ? 'ap_sum_ideas_placed' : 'ap_sum_ideas_waiting', { n: sum.ideas });
  }
  if (run && run.detail) return String(run.detail);
  const keys = Object.keys(sum).filter((k) => k !== 'seen');
  if (!keys.length) return '';
  return keys.slice(0, 4).map((k) => `${k}: ${typeof sum[k] === 'object' ? JSON.stringify(sum[k]) : String(sum[k])}`).join(' · ');
}

function apTaskRowHtml(task, st) {
  const desc = apTaskDesc(task.name);
  const last = task.last_run;
  const lastLine = last
    ? tf('ap_task_last', { s: apRunStatusLabel(last.status), t: last.started_at ? fmtDate(last.started_at) : '' })
    : t('ap_task_never');
  const nextLine = typeof task.next_run_at === 'number'
    ? ' · ' + tf('ap_task_next', { t: fmtDate(task.next_run_at) })
    : '';
  const isRunningThis = Boolean(st.running && st.running.task === task.name);
  const canRunNow = Boolean(st.on && st.enabled && task.on && !st.running);
  return `<div class="card soft" data-ap-task="${esc(task.name)}" style="margin-top:10px">
    <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <label class="check spacer" style="font-weight:600">
        <input type="checkbox" data-ap-task-toggle="${esc(task.name)}" ${task.on ? 'checked' : ''}>
        <span>${esc(apTaskTitle(task.name))}</span>
      </label>
      <span class="tag">${esc(apCadenceText(task.every_sec))}</span>
      <span class="tag ${task.calls_model ? 'warn' : ''}">${esc(t(task.calls_model ? 'ap_task_calls_model' : 'ap_task_local_only'))}</span>
      <button type="button" class="btn ${canRunNow ? 'primary' : ''}" data-ap-run-now="${esc(task.name)}" ${canRunNow ? '' : 'disabled'}>
        ${esc(t(isRunningThis ? 'ap_running_now' : 'ap_run_now'))}
      </button>
    </div>
    ${desc ? `<p class="hint" style="margin:6px 0 0">${esc(desc)}</p>` : ''}
    <p class="meta" style="margin:6px 0 0">${esc(lastLine + nextLine)}</p>
  </div>`;
}

function apRunRowHtml(run, st) {
  const sumText = apRunSummaryText(run);
  const detailText = run.detail && run.detail !== sumText ? String(run.detail) : '';
  const canUndo = Boolean(run.can_undo && !st.running);
  return `<div class="card soft" data-ap-run="${esc(run.id)}" style="margin-top:10px">
    <div class="row" style="flex-wrap:wrap;gap:8px;align-items:center">
      <strong>${esc(apTaskTitle(run.task))}</strong>
      <span class="tag ${apRunStatusTagClass(run.status)}" data-ap-run-status="${esc(run.status)}">${esc(apRunStatusLabel(run.status))}</span>
      ${run.forced ? `<span class="tag">${esc(t('ap_run_forced'))}</span>` : ''}
      ${run.model_calls ? `<span class="tag">${esc(tf('ap_run_calls', { n: run.model_calls }))}</span>` : ''}
      ${run.undone_at ? `<span class="tag warn" data-ap-undone="1">${esc(t('ap_run_undone'))}</span>` : ''}
      <span class="spacer"></span>
      <span class="meta">${run.started_at ? esc(fmtDate(run.started_at)) : ''}</span>
      ${run.can_undo ? `<button type="button" class="btn" data-ap-undo="${esc(run.id)}" ${canUndo ? '' : 'disabled'}>${esc(t('ap_undo_btn'))}</button>` : ''}
    </div>
    ${sumText ? `<p class="meta" style="margin:6px 0 0">${esc(sumText)}</p>` : ''}
    ${detailText ? `<p class="hint" style="margin:4px 0 0">${esc(detailText)}</p>` : ''}
  </div>`;
}

function apPaint() {
  const st = apUi.status;
  if (!st) return;
  const runs = Array.isArray(st.runs) ? st.runs : [];
  const runsCard = `<div class="card" id="ap-runs-card" style="margin-top:18px">
    <h2 style="margin-top:0">${esc(t('ap_runs_title'))}</h2>
    <p class="hint">${esc(t('ap_runs_sub'))}</p>
    ${runs.length ? runs.map((r) => apRunRowHtml(r, st)).join('') : `<div class="empty">${esc(t('ap_runs_empty'))}</div>`}
  </div>`;

  if (!st.on) {
    $view.innerHTML = `<div class="wrap" id="ap-root">
      <h1>${esc(t('ap_title'))}</h1>
      <p class="sub">${esc(t('ap_sub'))}</p>
      <div class="banner" id="ap-off">${esc(t('ap_off_banner'))}</div>
      ${runs.length ? runsCard : ''}
    </div>`;
    const rootOff = document.getElementById('ap-root');
    rootOff.onclick = apClicked;
    return;
  }

  const cap = st.daily_model_calls || { limit: 6, spent: 0, max: 50 };
  const tasks = Array.isArray(st.tasks) ? st.tasks : [];
  const vc = apUi.voice || { watch_dir: '', transcribe_route: 'off', folder_exists: false, counts: {} };
  const counts = vc.counts || {};
  const sc = apUi.sources || { ai_lookup: false, keyword_files: false, pages: [], max_pages: 5 };
  const routes = ['off', 'local', 'cloud'];

  $view.innerHTML = `<div class="wrap" id="ap-root">
    <h1>${esc(t('ap_title'))}</h1>
    <p class="sub">${esc(t('ap_sub'))}</p>

    <div class="card" id="ap-master-card">
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <h2 style="margin:0" class="spacer">${esc(t('ap_master_title'))}</h2>
        <span class="tag ${st.active ? 'ok' : ''}" id="ap-active-badge">${esc(t(st.active ? 'ap_status_active' : 'ap_status_paused'))}</span>
      </div>
      <label class="check" style="margin-top:12px">
        <input type="checkbox" id="ap-enabled" ${st.enabled ? 'checked' : ''}>
        <span>${esc(t('ap_enable_label'))}</span>
      </label>
      <label class="field">${esc(t('ap_level_label'))}</label>
      <div class="seg" role="group" aria-label="${esc(t('ap_level_label'))}">
        <button type="button" class="${st.level === 'suggest' ? 'on' : ''}" data-ap-level="suggest" aria-pressed="${st.level === 'suggest' ? 'true' : 'false'}">${esc(t('ap_level_suggest'))}</button>
        <button type="button" class="${st.level === 'auto' ? 'on' : ''}" data-ap-level="auto" aria-pressed="${st.level === 'auto' ? 'true' : 'false'}">${esc(t('ap_level_auto'))}</button>
      </div>
      <p class="hint" id="ap-level-desc">${esc(t(st.level === 'auto' ? 'ap_level_auto_d' : 'ap_level_suggest_d'))}</p>
      <label class="field" for="ap-daily-limit">${esc(t('ap_cap_label'))}</label>
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <input type="number" id="ap-daily-limit" min="0" max="${Number(cap.max) || 50}" step="1" value="${esc(cap.limit)}" style="width:110px">
        <button type="button" class="btn" id="ap-save-limit">${esc(t('ap_cap_save'))}</button>
        <span class="meta" id="ap-spent">${esc(tf('ap_cap_spent', { s: Number(cap.spent) || 0, m: Number(cap.limit) || 0 }))}</span>
      </div>
    </div>

    <div class="card" id="ap-tasks-card" style="margin-top:18px">
      <h2 style="margin-top:0">${esc(t('ap_tasks_title'))}</h2>
      <p class="hint">${esc(t('ap_tasks_sub'))}</p>
      ${tasks.length ? tasks.map((task) => apTaskRowHtml(task, st)).join('') : `<div class="empty">${esc(t('ap_tasks_empty'))}</div>`}
    </div>

    <div class="card" id="ap-voice-card" style="margin-top:18px">
      <h2 style="margin-top:0">${esc(t('ap_voice_title'))}</h2>
      <p class="hint">${esc(t('ap_voice_sub'))}</p>
      <label class="field" for="ap-watch-dir">${esc(t('ap_watch_dir_label'))}</label>
      <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
        <input type="text" id="ap-watch-dir" class="spacer" maxlength="1000" placeholder="${esc(t('ap_watch_dir_ph'))}" value="${esc(vc.watch_dir || '')}">
        ${vc.watch_dir ? `<span class="tag ${vc.folder_exists ? 'ok' : 'warn'}" id="ap-folder-status">${esc(t(vc.folder_exists ? 'ap_folder_ok' : 'ap_folder_missing'))}</span>` : ''}
      </div>
      <label class="field" for="ap-transcribe-route">${esc(t('ap_route_label'))}</label>
      <select id="ap-transcribe-route">
        ${routes.map((r) => `<option value="${esc(r)}" ${vc.transcribe_route === r ? 'selected' : ''}>${esc(t('ap_route_' + r))}</option>`).join('')}
      </select>
      <div class="row" style="margin-top:12px;flex-wrap:wrap;gap:10px;align-items:center">
        <button type="button" class="btn primary" id="ap-save-voice">${esc(t('ap_voice_save'))}</button>
        <span class="meta">${esc(tf('ap_voice_counts', {
          r: Number(counts.ready) || 0,
          i: Number(counts.ingested) || 0,
          w: Number(counts.waiting) || 0,
          f: Number(counts.failed) || 0,
        }))}</span>
      </div>
    </div>

    <div class="card" id="ap-sources-card" style="margin-top:18px">
      <h2 style="margin-top:0">${esc(t('ap_sources_title'))}</h2>
      <p class="hint">${esc(t('ap_sources_sub'))}</p>
      <label class="check" style="margin-top:8px">
        <input type="checkbox" id="ap-src-ai" ${sc.ai_lookup ? 'checked' : ''}>
        <span>${esc(t('ap_src_ai_label'))}</span>
      </label>
      <label class="check" style="margin-top:8px">
        <input type="checkbox" id="ap-src-files" ${sc.keyword_files ? 'checked' : ''}>
        <span>${esc(t('ap_src_files_label'))}</span>
      </label>
      <label class="field" for="ap-src-pages">${esc(tf('ap_src_pages_label', { max: Number(sc.max_pages) || 5 }))}</label>
      <textarea id="ap-src-pages" rows="3" placeholder="${esc(t('ap_src_pages_ph'))}">${esc((sc.pages || []).join('\n'))}</textarea>
      <div class="row" style="margin-top:12px">
        <button type="button" class="btn primary" id="ap-save-sources">${esc(t('ap_sources_save'))}</button>
      </div>
    </div>

    ${runsCard}
  </div>`;

  const root = document.getElementById('ap-root');
  root.onclick = apClicked;
  root.onchange = apChanged;
}

async function apSaveSettings(changes) {
  try {
    apUi.status = await api('POST', '/api/autopilot/settings', changes);
    toast(t('ap_saved_toast'));
    apPaint();
  } catch (e) {
    showError(e);
    apPaint();
  }
}

function apFollowJob(jobId) {
  apStopTimer();
  const timer = setInterval(async () => {
    if (location.hash !== '#/autopilot' || apUi.timer !== timer) {
      clearInterval(timer);
      return;
    }
    try {
      const { job } = await api('GET', '/api/jobs/' + jobId);
      if (job && job.status === 'running') return;
    } catch (e) {
      if (e.code !== 'not_found') return;
    }
    clearInterval(timer);
    if (apUi.timer === timer) apUi.timer = 0;
    if (location.hash === '#/autopilot') await renderAutopilot();
  }, 400);
  apUi.timer = timer;
}

async function apClicked(e) {
  const hit = (attr) => e.target.closest('[' + attr + ']');
  let el;
  if ((el = hit('data-ap-level'))) {
    const wanted = el.dataset.apLevel;
    if (apUi.status && wanted !== apUi.status.level) await apSaveSettings({ level: wanted });
    return;
  }
  if (e.target.closest('#ap-save-limit')) {
    const raw = Number(document.getElementById('ap-daily-limit').value);
    await apSaveSettings({ daily_model_calls: Number.isFinite(raw) ? Math.round(raw) : -1 });
    return;
  }
  if (e.target.closest('#ap-save-voice')) {
    const watch_dir = document.getElementById('ap-watch-dir').value.trim();
    const transcribe_route = document.getElementById('ap-transcribe-route').value;
    try {
      apUi.voice = await api('POST', '/api/autopilot/voice', { watch_dir, transcribe_route });
      toast(t('ap_saved_toast'));
      apPaint();
    } catch (err) { showError(err); }
    return;
  }
  if (e.target.closest('#ap-save-sources')) {
    const ai_lookup = document.getElementById('ap-src-ai').checked;
    const keyword_files = document.getElementById('ap-src-files').checked;
    const pages = document.getElementById('ap-src-pages').value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    try {
      apUi.sources = await api('POST', '/api/autopilot/sources', { ai_lookup, keyword_files, pages });
      toast(t('ap_saved_toast'));
      apPaint();
    } catch (err) { showError(err); }
    return;
  }
  if ((el = hit('data-ap-run-now'))) {
    const taskName = el.dataset.apRunNow;
    el.disabled = true;
    try {
      const started = await api('POST', '/api/autopilot/run', { task: taskName });
      toast(t('ap_started_toast'));
      apUi.status = await api('GET', '/api/autopilot');
      apPaint();
      if (started.job && started.job.id && apUi.status.running) apFollowJob(started.job.id);
    } catch (err) {
      showError(err);
      await renderAutopilot();
    }
    return;
  }
  if ((el = hit('data-ap-undo'))) {
    const runId = el.dataset.apUndo;
    el.disabled = true;
    try {
      await api('POST', '/api/autopilot/runs/' + runId + '/undo', {});
      toast(t('ap_undone_toast'));
      await renderAutopilot();
    } catch (err) {
      showError(err);
      await renderAutopilot();
    }
  }
}

async function apChanged(e) {
  const el = e.target;
  if (el.id === 'ap-enabled') {
    await apSaveSettings({ enabled: el.checked });
    return;
  }
  if (el.dataset.apTaskToggle !== undefined) {
    const name = el.dataset.apTaskToggle;
    await apSaveSettings({ tasks: { [name]: el.checked } });
  }
}

async function renderAutopilot() {
  apStopTimer();
  const st = await api('GET', '/api/autopilot');
  apUi.status = st;
  if (st.on) {
    const [voice, sources] = await Promise.all([
      api('GET', '/api/autopilot/voice'),
      api('GET', '/api/autopilot/sources'),
    ]);
    apUi.voice = voice;
    apUi.sources = sources;
  }
  apPaint();
}
