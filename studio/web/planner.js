'use strict';
/* The plan (#/plan): the videos the creator means to make, as a list, a week or a month (studio/planner.py).
   Loaded after discover.js and before pages.js; helpers from app.js and pages.js are used at call time.

   The page reads with GET /api/plans, which changes nothing. Every change is a request of its own, and its
   answer carries the page's data as it is afterwards (`planner`), so the page never guesses a state: whether a
   plan is in writing or posted is worked out by the core from the scripts and the library.

   What leaves the computer from here: nothing, except when the creator asks for a draft by AI. Then the draft
   prompt goes to the AI service chosen in Settings, with the plan's title, notes, reason and the viewers'
   questions it answers in it. In copy-and-paste mode the same prompt is shown through /api/manual/prompt and
   nothing is sent; pasting the reply into /api/manual/reply creates the script and links it back to the plan.

   Built to be used with the keyboard: a plan in the calendar is a button, a day is a button, and a plan's
   day is also an ordinary date field on its card and inside its edit form. No test presses a key, so that
   everything can be done that way is not shown.

   plFileToBase64 and the words pl_import_btn, pl_import_done, pl_stat_save, pl_add_btn and pl_added_toast are
   also used by rednote.js, pages.js and discover.js. */

Object.assign(I18N.zh, {
  nav_plan: '拍摄计划',
  pl_title: '拍摄计划',
  pl_sub: '接下来要拍的视频：哪天拍哪条，哪些还没定日子。',
  pl_stat_views: '平时的播放量', pl_stat_inter: '平时的互动', pl_stat_save: '平时的收藏率',
  pl_stat_note: '这三个数是你非商单视频的中位数。数据在素材库里填写，或从创作中心表格导入（会先预览再保存）。',
  pl_import_btn: '导入创作中心的数据表（.csv / .xlsx）',
  pl_import_done: '已更新 {u} 条视频的数据（新建 {c} 条）',
  pl_add_btn: '加入计划', pl_added_toast: '已加入计划', pl_added_n: '已加入 {n} 条计划',
  pl_new_title: '加一条计划',
  pl_title_label: '标题', pl_new_title_ph: '这条视频拍什么，例如：秋冬干皮晚上怎么护肤',
  pl_day: '日期', pl_no_day: '还没定日子', pl_clear_day: '取消日期',
  pl_notes: '备注', pl_notes_ph: '想拍的要点，或者想回答的观众问题（可不填）',
  pl_view: '怎么看', pl_view_list: '列表', pl_view_week: '按周', pl_view_month: '按月',
  pl_prev_week: '← 上一周', pl_next_week: '下一周 →', pl_prev_month: '← 上个月', pl_next_month: '下个月 →', pl_today: '今天',
  pl_weekdays: '周一,周二,周三,周四,周五,周六,周日', pl_today_mark: '今天',
  pl_all: '全部',
  pl_status_IDEA: '想法', pl_status_PLANNED: '已计划', pl_status_SCRIPTING: '写稿中', pl_status_READY: '脚本写好了',
  pl_status_POSTED: '已发布',
  pl_posted_on: '{d} 发布',
  pl_empty: '还没有计划。在上面加一条，或者从下面勾选几条。', pl_empty_filter: '没有这个状态的计划。',
  pl_unscheduled: '还没定日子的（{n}）', pl_unscheduled_none: '每条计划都定了日子。',
  pl_pick_hint: '已选「{t}」。点某一天的日期，就把它移到那天；也可以在下面改它的日期。',
  pl_pick_posted: '已选「{t}」。它已经发布了，排在发布的那一天，不能移动。',
  pl_day_hint: '点一条计划可以查看和修改。点某一天的日期，可以在那天加一条。',
  pl_move_to: '移到 {d}', pl_add_on: '在 {d} 加一条',
  pl_moved: '已移到 {d}', pl_day_cleared: '已取消日期',
  pl_edit: '修改', pl_length: '时长（秒）',
  pl_why: '为什么拍：', pl_answers: '要回答的观众提问：',
  pl_empty_script_btn: '建空白脚本', pl_open_script_btn: '打开脚本',
  pl_mark_ready: '脚本写好了', pl_mark_writing: '还在写', pl_mark_idea: '先当想法放着', pl_mark_planned: '定下来要拍',
  pl_confirm_delete: '删除计划「{t}」吗？', pl_confirm_delete_script: '从它建的脚本会留着。', pl_deleted: '已删除',
  pl_not_linked: '脚本建好了。不过这条计划刚刚在别处已经建过脚本，所以没有连到这一篇上。',
  pl_draft_hint: '「AI 帮我写初稿」会把这条计划的标题、备注、为什么拍和要回答的观众提问，连同你的个人资料和声音档案，发给设置里选的 AI 服务。',
  pl_mb_intro: '提示词里有这条计划的标题、备注、为什么拍和要回答的观众提问，还有你在设置里填的个人资料、你的声音档案和相关的脚本内容。这些会随提示词一起发给 ChatGPT。',
  pl_offer_title: '可以排进计划的',
  pl_offer_sub: '观众提问和存着的选题可以勾选几条一次加入计划，先不定日子。按词表或数据统计出的线索只描述数到了什么，请先写个视频标题再加入。',
  pl_offer_questions: '观众在评论区问的', pl_offer_ideas: '「选题建议」里存着的选题', pl_offer_opps: '从数据和评论里看出来的',
  pl_offer_add: '把勾选的 {n} 条加入计划', pl_offer_none: '先勾选要加的',
  pl_offer_empty: '这里现在是空的。在素材库里给视频贴上评论、填上数据，或者在「选题建议」里找过选题，这里就会列出来。',
  pl_basis_word_lists: '按词表统计', pl_basis_numbers: '按数据统计', pl_basis_model: 'AI 归纳（未核实）',
  pl_opp_write_btn: '写标题加入计划', pl_opp_edit_btn: '改标题加入',
  pl_opp_empty_numbers: '《{t}》的播放或收藏高于平时', pl_opp_empty_comments: '《{t}》评论区有观众反馈',
  pl_under: '《{t}》的评论区', pl_follows: '接着《{t}》',
  pl_kind_sentiment_followup: '来自评论', pl_kind_high_resonance_sequel: '来自数据', pl_kind_other: '线索',
  pl_sug_label: '推荐热词：', pl_sug_take: '采纳', pl_sug_dismiss: '忽略',
  pl_sug_taken: '已把热词加入这条计划', pl_sug_dismissed: '已忽略这条热词建议',
  pl_sug_part_title: '标题', pl_sug_part_notes: '备注', pl_sug_part_questions: '观众提问',
  pl_sug_fit: '匹配{where}：「{m}」', pl_sug_rising: '上升中',
});

Object.assign(I18N.en, {
  nav_plan: 'Plan',
  pl_title: 'Plan',
  pl_sub: 'The videos to make next: which one on which day, and which have no day yet.',
  pl_stat_views: 'Usual views', pl_stat_inter: 'Usual interactions', pl_stat_save: 'Usual save rate',
  pl_stat_note: 'Each is the middle value over {@names} non-sponsored videos. Numbers are typed in, or imported with a preview, in the Library.',
  pl_import_btn: 'Import a Creator Center table (.csv / .xlsx)',
  pl_import_done: 'Numbers updated for {u} videos ({c} added)',
  pl_add_btn: 'Add to the plan', pl_added_toast: 'Added to the plan', pl_added_n: 'Added {n} plans', pl_added_n_one: 'Added 1 plan',
  pl_new_title: 'Add a plan',
  pl_title_label: 'Title', pl_new_title_ph: 'What the video is about',
  pl_day: 'Day', pl_no_day: 'No day yet', pl_clear_day: 'Clear day',
  pl_notes: 'Notes', pl_notes_ph: 'Points to cover, or a question to answer (optional)',
  pl_view: 'View', pl_view_list: 'List', pl_view_week: 'Week', pl_view_month: 'Month',
  pl_prev_week: '← Week before', pl_next_week: 'Week after →', pl_prev_month: '← Month before', pl_next_month: 'Month after →', pl_today: 'Today',
  pl_weekdays: 'Mon,Tue,Wed,Thu,Fri,Sat,Sun', pl_today_mark: 'today',
  pl_all: 'All',
  pl_status_IDEA: 'Idea', pl_status_PLANNED: 'Planned', pl_status_SCRIPTING: 'Writing', pl_status_READY: 'Script ready',
  pl_status_POSTED: 'Posted',
  pl_posted_on: 'Posted {d}',
  pl_empty: 'Nothing is planned yet. Add a plan above, or tick a few below.', pl_empty_filter: 'No plan is in this state.',
  pl_unscheduled: 'No day yet ({n})', pl_unscheduled_none: 'Every plan has a day.',
  pl_pick_hint: 'Chosen: "{t}". Press a day to move it there, or change its day below.',
  pl_pick_posted: 'Chosen: "{t}". It is posted and stands on the day it went out, so it cannot be moved.',
  pl_day_hint: 'Press a plan to see and change it. Press a day to add a plan on that day.',
  pl_move_to: 'Move to {d}', pl_add_on: 'Add a plan on {d}',
  pl_moved: 'Moved to {d}', pl_day_cleared: 'The day was taken away',
  pl_edit: 'Change', pl_length: 'Length (seconds)',
  pl_why: 'Why: ', pl_answers: 'Answers the question: ',
  pl_empty_script_btn: 'Empty script', pl_open_script_btn: 'Open the script',
  pl_mark_ready: 'The script is ready', pl_mark_writing: 'Still writing', pl_mark_idea: 'Keep as an idea', pl_mark_planned: 'Decide to make it',
  pl_confirm_delete: 'Delete the plan "{t}"?', pl_confirm_delete_script: 'The script made from it stays.', pl_deleted: 'Deleted',
  pl_not_linked: 'The script was made. Another script was made for this plan a moment ago, though, so this one is not linked to it.',
  pl_draft_hint: '"Draft with AI" sends the plan\'s title, notes, reason and the questions it answers, together with the creator profile and {@names} voice profile, to the AI service chosen in Settings.',
  pl_mb_intro: 'The prompt contains this plan\'s title, notes, reason and the questions of viewers it answers, the creator profile from setup, {@names} voice profile and the relevant script text. All of it is sent to ChatGPT with the prompt.',
  pl_offer_title: 'What could go into the plan',
  pl_offer_sub: 'Tick viewers\' questions and saved ideas to add several in one step, without a day at first. Items counted by word lists or from numbers describe what was counted rather than a video title: write a title first to add one.',
  pl_offer_questions: 'Questions viewers asked', pl_offer_ideas: 'Saved ideas', pl_offer_opps: 'From the numbers and the comments',
  pl_offer_add: 'Add the {n} ticked to the plan', pl_offer_add_one: 'Add the 1 ticked to the plan', pl_offer_none: 'Tick what to add',
  pl_offer_empty: 'Nothing to offer yet. Paste comments and enter numbers for videos in the Library, or find ideas on the Ideas page, and they are listed here.',
  pl_basis_word_lists: 'Counted by word lists', pl_basis_numbers: 'Counted from numbers', pl_basis_model: 'Model reading (unverified)',
  pl_opp_write_btn: 'Write a title to plan', pl_opp_edit_btn: 'Edit title & add',
  pl_opp_empty_numbers: '"{t}" stood out in views or saves', pl_opp_empty_comments: 'Viewer comments under "{t}"',
  pl_under: 'under "{t}"', pl_follows: 'following "{t}"',
  pl_kind_sentiment_followup: 'from comments', pl_kind_high_resonance_sequel: 'from the numbers', pl_kind_other: 'noted',
  pl_sug_label: 'Suggested keywords:', pl_sug_take: 'Take', pl_sug_dismiss: 'Dismiss',
  pl_sug_taken: 'Keyword added to the plan', pl_sug_dismissed: 'Suggestion dismissed',
  pl_sug_part_title: 'title', pl_sug_part_notes: 'notes', pl_sug_part_questions: 'viewer questions',
  pl_sug_fit: 'Matched in {where}: "{m}"', pl_sug_rising: 'rising',
});

/* view: list | week | month. cursor: a day in the week or month the calendar shows ('' = today). picked: the plan
   chosen in the calendar. editing + editDraft: the plan whose fields are open and what is typed in them.
   oppOpen + oppTitle + oppNotes: an opportunity whose title form is open. ticked: what is ticked for batch add.
   The three new* hold the form for a new plan, so that drawing the page again does not empty it. */
const plUi = {
  view: 'list', filter: 'ALL', cursor: '', picked: null,
  editing: null, editDraft: null,
  oppOpen: null, oppTitle: '', oppNotes: '',
  ticked: {}, newTitle: '', newDay: '', newNotes: '',
};
let plData = null;        // what the core answered last (planner.payload)
const PL_STATES = ['IDEA', 'PLANNED', 'SCRIPTING', 'READY', 'POSTED'];
const PL_BASES = ['word_lists', 'numbers', 'model'];
const PL_TITLE_SHOWN = 80;      // a viewer's question as the title of a plan: the whole question stays in `questions`

/* Used by rednote.js: a chosen file as base64, for the import in the library. */
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

/* ---------- what is offered, as the body of a new plan (planner._new_plan) ---------- */
/* A saved idea. The core reads the idea's own names too; they are spelled out here so that the page says what it sends. */
function plFromIdea(idea) {
  return {
    title: idea.title_zh, video_format: idea.video_format, target_sec: idea.target_duration_sec,
    notes: idea.script_notes_zh || '', rationale_zh: idea.why_zh || '', rationale_en: idea.why_en || '',
    keywords: (idea.trend_keywords || []).slice(0, MAX_KEYWORDS), source_kind: 'idea', basis: 'model',
  };
}
/* A question a viewer asked. The plan is named after the question and keeps it whole as the question it answers.
   Where it came from is kept in both languages, because a plan is shown in either. */
function plFromQuestion(q) {
  const asked = String(q.question_zh || '').replace(/\s+/g, ' ').trim();
  const video = String(q.video_title || '').trim();
  const qBasis = q && q.basis === 'model' ? 'model' : 'word_lists';
  return {
    title: asked.length > PL_TITLE_SHOWN ? asked.slice(0, PL_TITLE_SHOWN - 1) + '…' : asked,
    questions: [asked],
    rationale_zh: video ? `《${video}》的评论区有人问了这个问题` : '评论区有人问了这个问题',
    rationale_en: video ? `A viewer asked this under "${video}"` : 'A viewer asked this in the comments',
    source_kind: 'viewer_question',
    basis: qBasis,
  };
}
const plBasis = (o) => (o && PL_BASES.includes(o.basis) ? o.basis : 'word_lists');
/* Only a model reading with a non-empty title has a ready-made plan title. For word-list counts and numbers,
   title_zh / title_en describe what was counted, so a plan is made only once the creator writes `writtenTitle`. */
function plFromOpportunity(o, writtenTitle, writtenNotes) {
  const basis = plBasis(o);
  const title = writtenTitle !== undefined ? String(writtenTitle).trim() : (basis === 'model' ? String(o.plan_title || '').trim() : '');
  const whyZh = String(o.rationale_zh || (basis !== 'model' ? o.title_zh : '') || '').trim();
  const whyEn = String(o.rationale_en || (basis !== 'model' ? o.title_en : '') || '').trim();
  return {
    title,
    video_format: o.video_format,
    target_sec: o.target_sec,
    notes: writtenNotes !== undefined ? writtenNotes : (o.notes || ''),
    rationale_zh: whyZh,
    rationale_en: whyEn,
    source_kind: o.kind || 'opportunity',
    basis,
  };
}

/* ---------- days ---------- */
/* A day is 'YYYY-MM-DD' everywhere. It is reckoned in UTC so that no time zone can move it to its neighbour. */
const plIsDay = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ''));
const plDate = (day) => new Date(day + 'T00:00:00Z');
const plAddDays = (day, n) => { const d = plDate(day); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const plMonday = (day) => plAddDays(day, -((plDate(day).getUTCDay() + 6) % 7));
const plShiftMonth = (day, n) => { const d = plDate(day.slice(0, 8) + '01'); d.setUTCMonth(d.getUTCMonth() + n); return d.toISOString().slice(0, 10); };
const plSay = (day, how) => plDate(day).toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', Object.assign({ timeZone: 'UTC' }, how));
const plShortDay = (day) => plSay(day, { month: 'short', day: 'numeric' });
const plLongDay = (day) => plSay(day, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
/* The days a month view shows: whole weeks, Monday first, from the week of the 1st to the week of the last day. */
function plMonthDays(cursor) {
  const month = cursor.slice(0, 7);
  const days = [];
  for (let day = plMonday(month + '-01'); days.length < 42; day = plAddDays(day, 1)) {
    if (days.length >= 28 && days.length % 7 === 0 && day.slice(0, 7) !== month) break;
    days.push(day);
  }
  return days;
}

/* ---------- drawing ---------- */
const plKey = (kind, item) => kind + '|' + (kind === 'q' ? (item.sample_id || '') + '|' + item.question_zh
  : kind === 'i' ? item.title_zh : item.plan_title);
const plWhy = (x) => (lang === 'zh' ? (x.rationale_zh || x.rationale_en) : (x.rationale_en || x.rationale_zh)) || '';
function plDayText(p) {
  if (p.status === 'POSTED') return p.posted_on && plIsDay(p.posted_on) ? tf('pl_posted_on', { d: plShortDay(p.posted_on) }) : t('pl_status_POSTED');
  return plIsDay(p.scheduled_for) ? plShortDay(p.scheduled_for) : t('pl_no_day');
}
function plOppHeading(o, basis) {
  const own = String(lang === 'zh' ? (o.title_zh || o.title_en || '') : (o.title_en || o.title_zh || '')).trim();
  if (own) return { text: own, usedWhy: false };
  const src = String(o.source_title || '').trim();
  if (src) {
    if (basis === 'numbers' || o.kind === 'high_resonance_sequel') return { text: tf('pl_opp_empty_numbers', { t: src }), usedWhy: false };
    if (basis === 'word_lists' || o.kind === 'sentiment_followup') return { text: tf('pl_opp_empty_comments', { t: src }), usedWhy: false };
    return { text: tf('pl_follows', { t: src }), usedWhy: false };
  }
  const why = plWhy(o).trim();
  if (why) return { text: why, usedWhy: true };
  return { text: t('pl_basis_' + basis), usedWhy: false };
}

function plOpenEdit(p) {
  plUi.editing = p.id;
  plUi.editDraft = {
    title: p.title, notes: p.notes || '',
    scheduled_for: plIsDay(p.scheduled_for) ? p.scheduled_for : '',
    target_sec: String(Number(p.target_sec) || 60),
    video_format: p.video_format,
  };
}

function plCardHtml(p) {
  const posted = p.status === 'POSTED';
  const why = plWhy(p);
  const questions = (p.questions || []).length
    ? `<p class="meta" style="margin:6px 0 0">${esc(t('pl_answers'))}${p.questions.map((q) => `「${esc(q)}」`).join(' ')}</p>` : '';
  let acts;
  if (p.script_id) {
    acts = `<a class="btn primary" href="#/s/${esc(p.script_id)}">${esc(t('pl_open_script_btn'))}</a>`
      + (p.status === 'SCRIPTING' ? `<button type="button" class="btn" data-pl-mark="${esc(p.id)}" data-state="READY">${esc(t('pl_mark_ready'))}</button>` : '')
      + (p.status === 'READY' ? `<button type="button" class="btn" data-pl-mark="${esc(p.id)}" data-state="SCRIPTING">${esc(t('pl_mark_writing'))}</button>` : '');
  } else {
    const st = appState.settings;
    acts = (st.configured ? `<button type="button" class="btn primary" data-pl-draft="${esc(p.id)}" data-how="${st.manual ? 'paste' : 'ai'}">${esc(t(st.manual ? 'ai_write_manual' : 'ai_write'))}</button>` : '')
      + `<button type="button" class="btn" data-pl-draft="${esc(p.id)}" data-how="empty">${esc(t('pl_empty_script_btn'))}</button>`
      + (p.status === 'IDEA' ? `<button type="button" class="btn" data-pl-mark="${esc(p.id)}" data-state="PLANNED">${esc(t('pl_mark_planned'))}</button>`
        : `<button type="button" class="btn" data-pl-mark="${esc(p.id)}" data-state="IDEA">${esc(t('pl_mark_idea'))}</button>`);
  }
  const lim = (plData && plData.limits) || {};
  const editing = plUi.editing === p.id;
  const ed = editing && plUi.editDraft ? plUi.editDraft : {
    title: p.title, notes: p.notes || '', scheduled_for: plIsDay(p.scheduled_for) ? p.scheduled_for : '',
    target_sec: String(Number(p.target_sec) || 60), video_format: p.video_format,
  };
  const form = editing ? `<div class="card soft" style="margin-top:12px">
      <label class="field" style="margin-top:0" for="pl-edit-title">${esc(t('pl_title_label'))}</label>
      <input type="text" id="pl-edit-title" maxlength="${Number(lim.title) || 300}" value="${esc(ed.title)}">
      <label class="field" for="pl-edit-notes">${esc(t('pl_notes'))}</label>
      <textarea id="pl-edit-notes" rows="3" maxlength="${Number(lim.notes) || 4000}" placeholder="${esc(t('pl_notes_ph'))}">${esc(ed.notes)}</textarea>
      <div class="row" style="flex-wrap:wrap;gap:14px;margin-top:10px">
        ${posted ? '' : `<label class="meta" for="pl-edit-date">${esc(t('pl_day'))}</label>
        <input type="date" id="pl-edit-date" value="${esc(ed.scheduled_for)}" style="width:auto">`}
        <label class="meta" for="pl-edit-sec">${esc(t('pl_length'))}</label>
        <input type="number" id="pl-edit-sec" min="${Number(lim.min_sec) || 15}" max="${Number(lim.max_sec) || 900}" step="1" value="${esc(ed.target_sec)}" style="width:96px">
        <label class="meta" for="pl-edit-format">${esc(t('format'))}</label>
        <select id="pl-edit-format" style="width:auto">${formatChoices(ed.video_format).map((v) =>
          `<option value="${esc(v)}" ${v === ed.video_format ? 'selected' : ''}>${esc(formatLabel(v))}</option>`).join('')}</select>
      </div>
      <div class="row" style="margin-top:12px;gap:8px">
        <button type="button" class="btn primary" data-pl-save="${esc(p.id)}">${esc(t('save'))}</button>
        <button type="button" class="btn" data-pl-cancel="1">${esc(t('ask_cancel'))}</button>
      </div>
    </div>` : '';
  const hasDay = plIsDay(p.scheduled_for);
  const quickDay = posted ? `<span class="meta">${esc(plDayText(p))}</span>`
    : (editing ? `<span class="meta">${esc(plDayText(p))}</span>`
      : `<label class="meta" for="pl-date-${esc(p.id)}">${esc(t('pl_day'))}</label>
         <input type="date" id="pl-date-${esc(p.id)}" data-pl-date="${esc(p.id)}" value="${esc(hasDay ? p.scheduled_for : '')}" style="width:auto">
         ${hasDay ? `<button type="button" class="btn ghost" data-pl-clear-day="${esc(p.id)}">${esc(t('pl_clear_day'))}</button>` : ''}`);
  const planBasisBadge = (p.basis === 'model' || p.basis === 'word_lists')
    ? `<span class="tag" style="margin:0" data-plan-basis="${esc(p.basis)}">${esc(t('pl_basis_' + p.basis))}</span>` : '';
  const sugs = !posted && Array.isArray(p.suggestions) ? p.suggestions : [];
  const sugHtml = sugs.length ? `<div class="card soft" data-pl-suggestions="${esc(p.id)}" style="margin-top:10px;padding:10px 12px">
    <div class="meta" style="margin-bottom:6px">${esc(t('pl_sug_label'))}</div>
    ${sugs.map((s) => {
      const sep = lang === 'zh' ? '、' : ', ';
      const where = (s.matched_in || []).map((part) => (I18N.zh['pl_sug_part_' + part] ? t('pl_sug_part_' + part) : part)).join(sep);
      const fitWhy = tf('pl_sug_fit', { where: where || t('pl_sug_part_title'), m: s.matched || s.keyword });
      return `<div class="row" data-pl-sug="${esc(s.id)}" style="flex-wrap:wrap;gap:8px;align-items:center;margin-top:4px">
        <span class="tag ok" style="margin:0">${esc(s.keyword)}</span>
        ${s.rising ? `<span class="tag" style="margin:0">${esc(t('pl_sug_rising'))}</span>` : ''}
        ${s.source === 'ai' ? `<span class="tag warn" style="margin:0">${esc(t('tr_src_ai'))}</span>` : ''}
        <span class="meta spacer">${esc(fitWhy)}</span>
        <button type="button" class="btn primary" data-pl-sug-take="${esc(s.id)}" data-plan-id="${esc(p.id)}">${esc(t('pl_sug_take'))}</button>
        <button type="button" class="btn" data-pl-sug-dismiss="${esc(s.id)}" data-plan-id="${esc(p.id)}">${esc(t('pl_sug_dismiss'))}</button>
      </div>`;
    }).join('')}
  </div>` : '';
  return `<div class="card pl-plan" data-plan-card="${esc(p.id)}">
    <div class="row" style="flex-wrap:wrap;gap:8px 10px">
      <h3 style="margin:0">${esc(p.title)}</h3>
      <span class="tag ${posted ? 'ok' : ''}" style="margin:0" data-pl-state="${esc(p.status)}">${esc(t('pl_status_' + p.status))}</span>
      ${planBasisBadge}
      ${(p.keywords || []).map((k) => `<span class="tag" style="margin:0">${esc(k)}</span>`).join('')}
      <span class="spacer"></span>
      ${quickDay}
    </div>
    ${why ? `<p class="meta" style="margin:8px 0 0">${esc(t('pl_why'))}${esc(why)}</p>` : ''}
    ${questions}
    ${p.notes && !editing ? `<p class="hint" style="margin:6px 0 0;white-space:pre-wrap">${esc(p.notes)}</p>` : ''}
    ${sugHtml}
    ${form}
    <div class="row" style="margin-top:12px;flex-wrap:wrap;gap:8px">
      ${acts}
      <span class="meta">${esc(formatLabel(p.video_format))} · ${Number(p.target_sec) || 60}${esc(t('sec'))}</span>
      <span class="spacer"></span>
      ${editing ? '' : `<button type="button" class="btn" data-pl-edit="${esc(p.id)}">${esc(t('pl_edit'))}</button>`}
      <button type="button" class="btn ghost" data-pl-del="${esc(p.id)}">${esc(t('delete'))}</button>
    </div>
  </div>`;
}

const plChipHtml = (p) => `<button type="button" class="pl-chip${p.status === 'POSTED' ? ' posted' : ''}${p.id === plUi.picked ? ' on' : ''}"
  data-pl-chip="${esc(p.id)}" aria-pressed="${p.id === plUi.picked ? 'true' : 'false'}" title="${esc(p.title + ' · ' + t('pl_status_' + p.status))}">${esc(p.title)}</button>`;

/* A week or a month of days, the plans without a day, and the card of the plan that is chosen. */
function plCalendarHtml(plans, today) {
  const month = plUi.view === 'month';
  const cursor = plIsDay(plUi.cursor) ? plUi.cursor : today;
  const days = month ? plMonthDays(cursor) : Array.from({ length: 7 }, (_, i) => plAddDays(plMonday(cursor), i));
  const picked = plans.find((p) => p.id === plUi.picked) || null;
  const movable = picked && picked.status !== 'POSTED';
  const heading = month ? plSay(cursor, { year: 'numeric', month: 'long' }) : `${plShortDay(days[0])} – ${plShortDay(days[6])}`;
  const cells = days.map((day) => {
    const act = tf(movable ? 'pl_move_to' : 'pl_add_on', { d: plLongDay(day) });
    const isToday = day === today;
    const number = !month || day.slice(8) === '01' ? plShortDay(day) : String(Number(day.slice(8)));
    return `<div class="pl-day${isToday ? ' today' : ''}${month && day.slice(0, 7) !== cursor.slice(0, 7) ? ' out' : ''}" data-pl-day="${day}">
      <button type="button" class="pl-num" data-pl-daybtn="${day}" aria-label="${esc(act)}" title="${esc(act)}"${isToday ? ' aria-current="date"' : ''}>${esc(number)}${isToday ? ' · ' + esc(t('pl_today_mark')) : ''}</button>
      ${plans.filter((p) => p.day === day).map(plChipHtml).join('')}
    </div>`;
  }).join('');
  const loose = plans.filter((p) => !plIsDay(p.day));
  const hint = picked ? tf(movable ? 'pl_pick_hint' : 'pl_pick_posted', { t: picked.title }) : t('pl_day_hint');
  return `<div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:8px">
      <button type="button" class="btn" data-pl-nav="-1">${esc(t(month ? 'pl_prev_month' : 'pl_prev_week'))}</button>
      <button type="button" class="btn" data-pl-nav="0">${esc(t('pl_today'))}</button>
      <button type="button" class="btn" data-pl-nav="1">${esc(t(month ? 'pl_next_month' : 'pl_next_week'))}</button>
      <strong id="pl-cal-title" style="margin-left:6px">${esc(heading)}</strong>
    </div>
    <p class="hint" id="pl-cal-hint" role="status" aria-live="polite" style="margin:0 0 8px">${esc(hint)}</p>
    <div class="pl-scroll"><div class="pl-cal ${month ? 'month' : 'week'}" id="pl-cal">
      ${t('pl_weekdays').split(',').map((name) => `<div class="pl-wd">${esc(name)}</div>`).join('')}${cells}
    </div></div>
    <div class="card" id="pl-loose" style="margin-top:14px">
      <h3>${esc(tf('pl_unscheduled', { n: loose.length }))}</h3>
      ${loose.length ? `<div class="pl-loose">${loose.map(plChipHtml).join('')}</div>` : `<p class="meta" style="margin:0">${esc(t('pl_unscheduled_none'))}</p>`}
    </div>
    <div id="pl-picked">${picked ? plCardHtml(picked) : ''}</div>`;
}

function plListHtml(plans, counts) {
  const shown = plUi.filter === 'ALL' ? plans : plans.filter((p) => p.status === plUi.filter);
  const chips = ['ALL'].concat(PL_STATES).map((state) => {
    const label = state === 'ALL' ? `${t('pl_all')} (${plans.length})` : `${t('pl_status_' + state)} (${(counts && counts[state]) || 0})`;
    return `<button type="button" class="chip ${plUi.filter === state ? 'on' : ''}" data-pl-filter="${state}" aria-pressed="${plUi.filter === state ? 'true' : 'false'}">${esc(label)}</button>`;
  }).join('');
  return `<div class="chips" style="margin-bottom:14px">${chips}</div>
    <div id="pl-list">${shown.length ? shown.map(plCardHtml).join('') : `<div class="empty">${esc(t(plans.length ? 'pl_empty_filter' : 'pl_empty'))}</div>`}</div>`;
}

/* What could go into the plan: viewers' questions, saved ideas, and what the numbers and comments suggest. */
function plOfferHtml(data) {
  const lim = data.limits || {};
  const row = (kind, item, title, small, extraHtml) => {
    const key = plKey(kind, item);
    return `<label class="pl-pick"><input type="checkbox" data-pl-tick="${esc(key)}" ${plUi.ticked[key] ? 'checked' : ''}>
      <span><span>${esc(title)}</span>${extraHtml || ''}${small ? `<br><span class="meta">${esc(small)}</span>` : ''}</span></label>`;
  };
  const group = (name, rows) => (rows.length ? `<h3 style="margin-top:14px">${esc(t(name))}</h3>${rows.join('')}` : '');
  const questions = (data.top_audience_questions || []).map((q) => row('q', q,
    `「${(lang === 'zh' ? q.question_zh : (q.question_en || q.question_zh))}」`, q.video_title ? tf('pl_under', { t: q.video_title }) : ''));
  const ideas = (data.ideas || []).map((i) => row('i', i, lang === 'zh' ? i.title_zh : (i.title_en || i.title_zh),
    (lang === 'zh' ? i.why_zh : (i.why_en || i.why_zh)) || ''));
  const opps = (data.opportunities || []).map((o, idx) => {
    const basis = plBasis(o);
    const badge = ` <span class="tag" style="margin:0 0 0 6px" data-pl-basis="${esc(basis)}">${esc(t('pl_basis_' + basis))}</span>`;
    const head = plOppHeading(o, basis);
    const kind = I18N.zh['pl_kind_' + o.kind] !== undefined ? t('pl_kind_' + o.kind) : t('pl_kind_other');
    const from = o.source_title ? tf('pl_follows', { t: o.source_title }) : '';
    const detail = [kind, from, head.usedWhy ? '' : plWhy(o)].filter(Boolean).join(' · ');
    const readyModel = basis === 'model' && Boolean(String(o.plan_title || '').trim());
    const open = plUi.oppOpen === idx;
    const writer = open ? `<div class="card soft" style="margin-top:8px" data-pl-opp-form="${idx}">
        <label class="field" style="margin-top:0" for="pl-opp-title">${esc(t('pl_title_label'))}</label>
        <input type="text" id="pl-opp-title" maxlength="${Number(lim.title) || 300}" placeholder="${esc(t('pl_new_title_ph'))}" value="${esc(plUi.oppTitle)}">
        <label class="field" for="pl-opp-notes">${esc(t('pl_notes'))}</label>
        <textarea id="pl-opp-notes" rows="2" maxlength="${Number(lim.notes) || 4000}" placeholder="${esc(t('pl_notes_ph'))}">${esc(plUi.oppNotes)}</textarea>
        <div class="row" style="margin-top:10px;gap:8px">
          <button type="button" class="btn primary" id="pl-opp-save" data-pl-opp-save="${idx}" ${plUi.oppTitle.trim() ? '' : 'disabled'}>${esc(t('pl_add_btn'))}</button>
          <button type="button" class="btn" data-pl-opp-cancel="1">${esc(t('ask_cancel'))}</button>
        </div>
      </div>` : '';
    if (readyModel) {
      const key = plKey('o', o);
      return `<div data-pl-opp="${idx}" style="padding:4px 0">
        <div class="row" style="align-items:flex-start;gap:10px">
          <label class="pl-pick spacer" style="padding:3px 0"><input type="checkbox" data-pl-tick="${esc(key)}" ${plUi.ticked[key] ? 'checked' : ''}>
            <span><span>${esc(head.text)}</span>${badge}${detail ? `<br><span class="meta">${esc(detail)}</span>` : ''}</span></label>
          ${open ? '' : `<button type="button" class="btn" data-pl-opp-open="${idx}">${esc(t('pl_opp_edit_btn'))}</button>`}
        </div>${writer}</div>`;
    }
    return `<div data-pl-opp="${idx}" style="padding:7px 0">
      <div class="row" style="align-items:flex-start;gap:10px">
        <div class="spacer"><span>${esc(head.text)}</span>${badge}${detail ? `<br><span class="meta">${esc(detail)}</span>` : ''}</div>
        ${open ? '' : `<button type="button" class="btn" data-pl-opp-open="${idx}">${esc(t('pl_opp_write_btn'))}</button>`}
      </div>${writer}</div>`;
  });
  const tickableCount = questions.length + ideas.length
    + (data.opportunities || []).filter((o) => plBasis(o) === 'model' && String(o.plan_title || '').trim()).length;
  const n = Object.keys(plUi.ticked).length;
  const any = questions.length + ideas.length + opps.length;
  return `<h2 class="sec-h">${esc(t('pl_offer_title'))}</h2>
    ${any ? `<div class="card" id="pl-offer"><p class="hint" style="margin-top:0">${esc(t('pl_offer_sub'))}</p>
      ${group('pl_offer_questions', questions)}${group('pl_offer_ideas', ideas)}${group('pl_offer_opps', opps)}
      ${tickableCount ? `<div class="row" style="margin-top:14px"><button type="button" class="btn primary" id="pl-offer-add" ${n ? '' : 'disabled'}>${esc(plOfferLabel(n))}</button></div>` : ''}
    </div>` : `<div class="card meta" id="pl-offer-empty">${esc(t('pl_offer_empty'))} <a href="#/library">${esc(t('go_library'))}</a></div>`}`;
}
const plOfferLabel = (n) => (n ? tf(countKey('pl_offer_add', n), { n }) : t('pl_offer_none'));

/* Draws the page from plData. `focus` is a selector: where the keyboard is afterwards, so that an action taken
   with the keyboard does not send it back to the top of the page. */
function plPaint(focus) {
  const data = plData;
  const plans = data.plans || [];
  const today = plIsDay(data.today) ? data.today : new Date().toISOString().slice(0, 10);
  const lim = data.limits || {};
  const stat = (key, value) => `<div><div class="meta">${esc(t(key))}</div><strong>${value}</strong></div>`;
  const views = ['list', 'week', 'month'].map((v) =>
    `<button type="button" class="${plUi.view === v ? 'on' : ''}" data-pl-view="${v}" aria-pressed="${plUi.view === v ? 'true' : 'false'}">${esc(t('pl_view_' + v))}</button>`).join('');
  const canDraft = appState.settings.configured && !appState.settings.manual && plans.some((p) => !p.script_id);
  $view.innerHTML = `<div class="wrap" id="pl-root">
    <h1>${esc(t('pl_title'))}</h1><p class="sub">${esc(t('pl_sub'))}</p>
    <div class="card" id="pl-new">
      <label class="field" style="margin-top:0" for="pl-new-title">${esc(t('pl_new_title'))}</label>
      <div class="row" style="flex-wrap:wrap;gap:10px">
        <input type="text" id="pl-new-title" class="spacer" maxlength="${Number(lim.title) || 300}" placeholder="${esc(t('pl_new_title_ph'))}" value="${esc(plUi.newTitle)}">
        <label class="meta" for="pl-new-date">${esc(t('pl_day'))}</label>
        <input type="date" id="pl-new-date" style="width:auto" value="${esc(plUi.newDay)}">
        <button type="button" class="btn primary" id="pl-create-btn">${esc(t('pl_add_btn'))}</button>
      </div>
      <textarea id="pl-new-notes" rows="2" style="margin-top:8px" maxlength="${Number(lim.notes) || 4000}" aria-label="${esc(t('pl_notes'))}" placeholder="${esc(t('pl_notes_ph'))}">${esc(plUi.newNotes)}</textarea>
    </div>
    <div class="row" style="margin:18px 0 12px;gap:10px"><span class="meta" id="pl-view-label">${esc(t('pl_view'))}</span>
      <div class="seg" role="group" aria-labelledby="pl-view-label">${views}</div></div>
    ${plUi.view === 'list' ? plListHtml(plans, data.counts) : plCalendarHtml(plans, today)}
    ${canDraft ? `<p class="hint" id="pl-draft-hint">${esc(t('pl_draft_hint'))}</p>` : ''}
    ${plOfferHtml(data)}
    <div class="card" id="pl-stats" style="margin-top:28px">
      <div class="row" style="flex-wrap:wrap;gap:28px">
        ${stat('pl_stat_views', data.median_views != null ? esc(num(Math.round(data.median_views))) : '–')}
        ${stat('pl_stat_inter', data.median_interactions != null ? esc(num(Math.round(data.median_interactions))) : '–')}
        ${stat('pl_stat_save', data.median_save_rate_pct != null ? esc(data.median_save_rate_pct + '%') : '–')}
      </div>
      <p class="hint" style="margin-bottom:0">${esc(t('pl_stat_note'))} <a href="#/library">${esc(t('go_library'))}</a></p>
    </div>
  </div>`;
  const root = document.getElementById('pl-root');
  root.onclick = plClicked;
  root.onchange = plChanged;
  root.oninput = (e) => {
    const id = e.target.id;
    if (id === 'pl-new-title') plUi.newTitle = e.target.value;
    else if (id === 'pl-new-notes') plUi.newNotes = e.target.value;
    else if (id === 'pl-edit-title' && plUi.editDraft) plUi.editDraft.title = e.target.value;
    else if (id === 'pl-edit-notes' && plUi.editDraft) plUi.editDraft.notes = e.target.value;
    else if (id === 'pl-edit-sec' && plUi.editDraft) plUi.editDraft.target_sec = e.target.value;
    else if (id === 'pl-opp-title') {
      plUi.oppTitle = e.target.value;
      const saveBtn = document.getElementById('pl-opp-save');
      if (saveBtn) saveBtn.disabled = !plUi.oppTitle.trim();
    } else if (id === 'pl-opp-notes') plUi.oppNotes = e.target.value;
  };
  root.onkeydown = (e) => {      // Enter in the title of a new plan adds it; Enter also confirms Chinese input, which must not
    if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
    if (e.target.id === 'pl-new-title') { e.preventDefault(); plCreate(); }
    else if (e.target.id === 'pl-opp-title' && plUi.oppTitle.trim() && plUi.oppOpen !== null) {
      e.preventDefault();
      plSaveOpportunity(plUi.oppOpen);
    }
  };
  const el = focus ? root.querySelector(focus) : null;
  if (el) el.focus();
}

/* ---------- doing ---------- */
/* What the core answered after a change: the page's data as it is now, and the mark beside "Plan" in the sidebar. */
function plTake(data) {
  plData = data;
  appState.plan_count = (data.plans || []).filter((p) => p.status !== 'POSTED').length;
  renderNav('plan');
}
/* Sends one change, takes the answer, draws the page again. Resolves with the answer, or null when it was refused. */
async function plSend(method, path, body, focus) {
  try {
    const answer = await api(method, path, body);
    if (answer.planner) plTake(answer.planner);
    plPaint(focus);
    return answer;
  } catch (e) {
    showError(e);
    plPaint(focus);         // a field that was refused shows what is stored again
    return null;
  }
}
const plFind = (id) => ((plData && plData.plans) || []).find((p) => p.id === id) || null;

async function plCreate() {
  const body = { title: plUi.newTitle, scheduled_for: plUi.newDay, notes: plUi.newNotes, source_kind: 'manual' };
  try {
    const answer = await api('POST', '/api/plans', body);
    plUi.newTitle = ''; plUi.newDay = ''; plUi.newNotes = '';
    if (plUi.view !== 'list') { plUi.picked = answer.plan.id; if (plIsDay(answer.plan.day)) plUi.cursor = answer.plan.day; }
    plTake(answer.planner);
    toast(t('pl_added_toast'));
    plPaint('#pl-new-title');
  } catch (e) { showError(e); }
}

async function plSaveOpportunity(idx) {
  const opps = (plData && plData.opportunities) || [];
  const o = opps[idx];
  if (!o) return;
  const body = plFromOpportunity(o, plUi.oppTitle, plUi.oppNotes);
  try {
    const answer = await api('POST', '/api/plans', body);
    plUi.oppOpen = null; plUi.oppTitle = ''; plUi.oppNotes = '';
    if (plUi.view !== 'list') plUi.picked = answer.plan.id;
    plTake(answer.planner);
    toast(t('pl_added_toast'));
    plPaint('#pl-new-title');
  } catch (e) { showError(e); }
}

async function plMove(plan, day, focus) {
  const answer = await plSend('PUT', '/api/plans/' + plan.id, { scheduled_for: day }, focus);
  if (answer) toast(day ? tf('pl_moved', { d: plLongDay(day) }) : t('pl_day_cleared'));
}

async function plMakeScript(plan, how, btn) {
  const path = '/api/plans/' + plan.id;
  try {
    let made;
    if (how === 'paste') {
      /* Copy and paste: /api/manual/prompt builds the plan's draft prompt, and /api/manual/reply creates the script
         and links it back to this plan. */
      made = await manualBridge('draft', { plan_id: plan.id, lang }, { intro: t('pl_mb_intro') });
      if (!made) return;                    // the dialog was closed: nothing was made
    } else {
      btn.disabled = true;
      if (how === 'ai') btn.textContent = t('writing');
      made = await api('POST', path + '/draft', { use_ai: how === 'ai', lang });
    }
    if (made.planner) plTake(made.planner);
    if (made.linked === false) toast(t('pl_not_linked'));
    const scriptId = (made.script && made.script.id) || made.id;
    location.hash = '#/s/' + scriptId;
  } catch (e) {
    showError(e);
    if (location.hash === '#/plan') await renderPlan();     // what stands now: another window may have made the script
  }
}

async function plAddTicked() {
  const data = plData;
  const readyModelOpps = (data.opportunities || []).filter((o) => plBasis(o) === 'model' && String(o.plan_title || '').trim());
  const offered = [].concat(
    (data.top_audience_questions || []).map((q) => [plKey('q', q), plFromQuestion(q)]),
    (data.ideas || []).map((i) => [plKey('i', i), plFromIdea(i)]),
    readyModelOpps.map((o) => [plKey('o', o), plFromOpportunity(o)]));
  const plans = offered.filter(([key]) => plUi.ticked[key]).map(([, body]) => body);
  if (!plans.length) { plUi.ticked = {}; plPaint(); return; }
  try {
    const answer = await api('POST', '/api/plans/batch', { plans });
    plUi.ticked = {};
    plTake(answer.planner);
    toast(tf(countKey('pl_added_n', answer.plans.length), { n: answer.plans.length }));
    plPaint('#pl-new-title');
  } catch (e) { showError(e); }
}

async function plClicked(e) {
  const hit = (name) => e.target.closest('[' + name + ']');
  let el;
  if ((el = hit('data-pl-view'))) {
    plUi.view = el.dataset.plView; plUi.editing = null; plUi.editDraft = null;
    return plPaint(`[data-pl-view="${plUi.view}"]`);
  }
  if ((el = hit('data-pl-filter'))) { plUi.filter = el.dataset.plFilter; return plPaint(`[data-pl-filter="${plUi.filter}"]`); }
  if ((el = hit('data-pl-nav'))) {
    const step = Number(el.dataset.plNav);
    const today = plIsDay(plData.today) ? plData.today : new Date().toISOString().slice(0, 10);
    const from = plIsDay(plUi.cursor) ? plUi.cursor : today;
    plUi.cursor = step === 0 ? today : (plUi.view === 'month' ? plShiftMonth(from, step) : plAddDays(from, 7 * step));
    return plPaint(`[data-pl-nav="${step}"]`);
  }
  if ((el = hit('data-pl-chip'))) {
    const id = el.dataset.plChip;
    plUi.picked = plUi.picked === id ? null : id;
    plUi.editing = null; plUi.editDraft = null;
    return plPaint(`[data-pl-chip="${id}"]`);
  }
  if ((el = hit('data-pl-daybtn'))) {
    const day = el.dataset.plDaybtn;
    const picked = plFind(plUi.picked);
    if (picked && picked.status !== 'POSTED') return plMove(picked, day, `[data-pl-daybtn="${day}"]`);
    plUi.newDay = day;                       // nothing to move: the day goes into the form for a new plan
    return plPaint('#pl-new-title');
  }
  if (e.target.closest('#pl-create-btn')) return plCreate();
  if (e.target.closest('#pl-offer-add')) return plAddTicked();
  if ((el = hit('data-pl-opp-open'))) {
    const idx = Number(el.dataset.plOppOpen);
    const o = ((plData && plData.opportunities) || [])[idx];
    if (!o) return undefined;
    plUi.oppOpen = idx;
    /* Only a model reading has a suggested title to edit; counts and numbers start empty so the creator writes the title. */
    plUi.oppTitle = plBasis(o) === 'model' ? String(o.plan_title || '').trim() : '';
    plUi.oppNotes = String(o.notes || '');
    return plPaint('#pl-opp-title');
  }
  if (hit('data-pl-opp-cancel')) {
    const idx = plUi.oppOpen;
    plUi.oppOpen = null; plUi.oppTitle = ''; plUi.oppNotes = '';
    return plPaint(idx !== null ? `[data-pl-opp-open="${idx}"]` : '#pl-new-title');
  }
  if ((el = hit('data-pl-opp-save'))) return plSaveOpportunity(Number(el.dataset.plOppSave));
  if ((el = hit('data-pl-clear-day'))) {
    const plan = plFind(el.dataset.plClearDay);
    if (plan) return plMove(plan, '', `#pl-date-${plan.id}`);
    return undefined;
  }
  if ((el = hit('data-pl-edit'))) {
    const plan = plFind(el.dataset.plEdit);
    if (plan) { plOpenEdit(plan); return plPaint('#pl-edit-title'); }
    return undefined;
  }
  if (hit('data-pl-cancel')) {
    const id = plUi.editing;
    plUi.editing = null; plUi.editDraft = null;
    return plPaint(`[data-pl-edit="${id}"]`);
  }
  if ((el = hit('data-pl-save'))) {
    const id = el.dataset.plSave;
    const plan = plFind(id);
    const ed = plUi.editDraft || {};
    const seconds = Number(document.getElementById('pl-edit-sec').value);
    const body = {
      title: document.getElementById('pl-edit-title').value,
      notes: document.getElementById('pl-edit-notes').value,
      target_sec: Number.isFinite(seconds) ? seconds : 0,
      video_format: document.getElementById('pl-edit-format').value,
    };
    const dateEl = document.getElementById('pl-edit-date');
    if (dateEl && (!plan || plan.status !== 'POSTED')) {
      body.scheduled_for = dateEl.value;
      ed.scheduled_for = dateEl.value;
    }
    try {
      const answer = await api('PUT', '/api/plans/' + id, body);
      if (plUi.view !== 'list' && body.scheduled_for) plUi.cursor = body.scheduled_for;
      plUi.editing = null; plUi.editDraft = null;
      plTake(answer.planner);
      toast(t('saved'));
      plPaint(`[data-pl-edit="${id}"]`);
    } catch (err) { showError(err); }          // the fields stay open with what was typed
    return undefined;
  }
  if ((el = hit('data-pl-mark'))) return plSend('PUT', '/api/plans/' + el.dataset.plMark, { status: el.dataset.state }, `[data-plan-card="${el.dataset.plMark}"] [data-pl-edit]`);
  if ((el = hit('data-pl-del'))) {
    const plan = plFind(el.dataset.plDel);
    if (!plan) return undefined;
    const question = tf('pl_confirm_delete', { t: plan.title }) + (plan.script_id ? ' ' + t('pl_confirm_delete_script') : '');
    if (!(await askConfirm(question, { ok: t('delete'), danger: true }))) return undefined;
    if (plUi.picked === plan.id) plUi.picked = null;
    if (plUi.editing === plan.id) { plUi.editing = null; plUi.editDraft = null; }
    if (await plSend('DELETE', '/api/plans/' + plan.id, undefined, '#pl-new-title')) toast(t('pl_deleted'));
    return undefined;
  }
  if ((el = hit('data-pl-sug-take'))) {
    const pid = el.dataset.planId;
    const sid = el.dataset.plSugTake;
    const ans = await plSend('POST', `/api/plans/${pid}/suggestions/${sid}/take`, {}, `[data-plan-card="${pid}"] [data-pl-edit]`);
    if (ans) toast(t('pl_sug_taken'));
    return undefined;
  }
  if ((el = hit('data-pl-sug-dismiss'))) {
    const pid = el.dataset.planId;
    const sid = el.dataset.plSugDismiss;
    const ans = await plSend('POST', `/api/plans/${pid}/suggestions/${sid}/dismiss`, {}, `[data-plan-card="${pid}"] [data-pl-edit]`);
    if (ans) toast(t('pl_sug_dismissed'));
    return undefined;
  }
  if ((el = hit('data-pl-draft'))) {
    const plan = plFind(el.dataset.plDraft);
    if (plan) return plMakeScript(plan, el.dataset.how, el);
  }
  return undefined;
}

function plChanged(e) {
  const el = e.target;
  if (el.id === 'pl-new-date') { plUi.newDay = el.value; return; }
  if (el.id === 'pl-edit-date' && plUi.editDraft) { plUi.editDraft.scheduled_for = el.value; return; }
  if (el.id === 'pl-edit-format' && plUi.editDraft) { plUi.editDraft.video_format = el.value; return; }
  if (el.dataset.plTick !== undefined) {
    if (el.checked) plUi.ticked[el.dataset.plTick] = true; else delete plUi.ticked[el.dataset.plTick];
    const n = Object.keys(plUi.ticked).length;
    const add = document.getElementById('pl-offer-add');
    if (add) { add.disabled = !n; add.textContent = plOfferLabel(n); }
    return;
  }
  if (el.dataset.plDate !== undefined) {
    const plan = plFind(el.dataset.plDate);
    if (plan && el.value !== (plan.scheduled_for || '')) {
      if (plUi.view !== 'list' && el.value) plUi.cursor = el.value;         // the calendar follows the plan to its new day
      plMove(plan, el.value, `#pl-date-${plan.id}`);
    }
  }
}

async function renderPlan() {
  plData = await api('GET', '/api/plans');
  /* What is ticked, chosen or open may be gone since the page was last shown. */
  if (!plFind(plUi.picked)) plUi.picked = null;
  if (!plFind(plUi.editing)) { plUi.editing = null; plUi.editDraft = null; }
  plUi.oppOpen = null; plUi.oppTitle = ''; plUi.oppNotes = '';
  plUi.ticked = {};
  plPaint();
}
