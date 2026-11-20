'use strict';
/* The creator's own material beside the script being edited: "参考 / References" (studio/refs.py). Loaded after
   wordlist.js and before pages.js, which boots the app; helpers from pages.js (tf, countKey, num) are used at call time.

   What this file puts on the script page, inside <div id="rf-box"> (app.js):
   - closed: one row with a button that opens the panel. Nothing is asked of the server, and the editor is as it was;
   - open: the panel. In a wide window it is a second column beside the lines and stays in view while they scroll;
     in a narrow one it is a section above the lines. Which of the two is decided in app.css alone.

   The panel lists, each part with one sentence that says why its items are there: what is pinned to this script,
   what its AI draft saw, what is on the same topic, and what a search of the library finds. The server chooses
   and orders the items by plain rules and says what it found in each; nothing here ranks, and no AI is asked.

   Pinning is a request of its own (PUT /api/scripts/<id>/pins). It does not save the script, does not touch what
   is being typed and does not move the script's updated_at, so the editor's next save is taken as always.

   Text whose wording is not confirmed as the creator's own (a machine transcript nobody has read, a posted script
   nobody has confirmed) is shown under a note that says so, and an opening an AI wrote is labelled. Nothing here
   calls such text the way the creator talks. Everything shown goes through esc(). */

Object.assign(I18N.zh, {
  rf_title: '参考', rf_open: '打开参考', rf_close: '收起',
  rf_closed_hint: '写的时候，把你自己的相关素材放在旁边看。',
  rf_about: '这里只列出工作台里已有的、你自己的素材。不调用 AI，内容不会离开这台电脑。',
  rf_loading: '正在读取…', rf_failed: '读取失败，请刷新页面再试。',
  rf_library_empty: '素材库还是空的。把以前的脚本、字幕文件或视频放进去，这里才有东西可看。', rf_go_library: '去素材库',
  rf_pinned: '钉在这篇上的', rf_pinned_count: '{k}/{n}',
  rf_pinned_why: '你钉在这篇脚本上的素材，会一直留在这里，最多 {n} 条。钉和取消都不会改动脚本。',
  rf_pinned_none: '还没有钉任何素材。在下面任何一条上点「钉在这里」。',
  rf_basis: '初稿看到的',
  rf_basis_why: '这篇是 AI 写的初稿。写的时候，提示词里放了下面这些素材；它们是按当时记下的标题找回来的。',
  rf_basis_none: '写这篇初稿时，提示词里没有放你的素材。',
  rf_role_example: '正文进了提示词', rf_role_opening: '开场进了提示词',
  rf_same_title: '现在有 {n} 条素材叫这个名字，这里显示其中一条；初稿看到的也可能是另一条。',
  rf_missing: '「{title}」现在不在素材库里了（删除了，或者改了名）。',
  rf_basis_left_out: '另有 {n} 条没有列出。',
  rf_related: '同主题的',
  rf_related_why: '提到这篇的目标关键词、或者标题里的词的素材。量法和「热点」页一样：这个词原样出现，或者它至少 {pct}% 的词片出现，才算。',
  rf_related_order: '排序：找到的关键词多的在前，然后是标题里的词、提到的次数、视频的互动数，最后是加入的时间。',
  rf_looked_for: '按这些词找：', rf_term_title: '{term}（标题）',
  rf_needs_terms: '这篇脚本还没有目标关键词，标题也看不出主题，所以这里不列素材。在上面加上目标关键词，或者起一个说明主题的标题。',
  rf_related_none: '素材库里没有提到这些词的素材。',
  rf_related_none_title: '素材库里没有提到这些词的素材。标题是按空格和标点分成词去找的；在上面加上目标关键词，找得更准。',
  rf_related_more: '还有 {n} 条相关的没有列出，用下面的搜索可以找到。',
  rf_related_above: '另有 {n} 条相关的已经列在上面。',
  rf_search: '搜索素材库',
  rf_search_why: '按标题和内容里的字找，找到的任何一条都可以钉上。照你打的字找，不拆词；不分大小写，空格不算。',
  rf_search_ph: '例如：面霜', rf_searching: '正在找…',
  rf_search_none: '没有找到「{q}」。', rf_search_found: '找到 {n} 条。',
  rf_search_more: '找到 {n} 条，显示前 {k} 条。多打几个字可以缩小范围。',
  rf_kind_script: '脚本', rf_kind_subtitle: '字幕文件', rf_kind_video: '视频',
  rf_from_script: '从工作台的脚本发布', rf_this_script: '这篇脚本发布的那一条',
  rf_sponsored: '商单', rf_organic: '非商单',
  rf_numbers_none: '没填数据', rf_interactions: '互动 {n}', rf_times: '是你平常的 {x} 倍',
  rf_read_after: '数据是发布 {n} 天后看的', rf_read_same: '数据是发布当天看的',
  rf_w_transcript: '机器转写，你还没确认：下面的字不一定是你的原话。',
  rf_w_posted: '从工作台的脚本发布的，文字还没确认是你自己的说法，里面可能有 AI 写的句子。',
  rf_w_tag: '文字未确认',
  rf_opening: '开场', rf_opening_ai: 'AI 写的，不算你的开场',
  rf_full: '看全文', rf_full_close: '收起全文', rf_full_more: '再看 {n} 字',
  rf_full_part: '显示了前 {k} 字，共 {n} 字。', rf_full_all: '全文共 {n} 字。', rf_full_failed: '全文读取失败，请再点一次。',
  rf_long: '这条很长：只在它的前 {n} 字里找过。',
  rf_pin: '钉在这里', rf_unpin: '取消钉住', rf_pin_full: '已经钉满 {n} 条',
  rf_pinned_done: '已钉上', rf_unpinned_done: '已取消',
  rf_to_library: '在素材库里打开',
  rf_why_count: '提到「{term}」{n} 次', rf_why_in_title: '，标题里有',
  rf_why_pieces: '有「{term}」里的 {k}/{m}：{list}',
  rf_why_title_count: '有这篇标题里的「{term}」（{n} 次）', rf_why_title_pieces: '有这篇标题里「{term}」的 {k}/{m}：{list}',
  rf_found_title: '标题里有', rf_found_count: '内容里出现 {n} 次',
  rf_usual: '你平常一条视频的互动：{n}（{v} 条有数据的非商单视频的中间值）。',
  rf_no_usual: '还没有填了数据的非商单视频，所以算不出「平常的几倍」。',
});
Object.assign(I18N.en, {
  rf_title: 'References', rf_open: 'Open references', rf_close: 'Close',
  rf_closed_hint: 'Keep {@names} own related material in view while writing.',
  rf_about: 'Only {@names} own material that is already in the studio is listed here. No AI is asked and nothing leaves this computer.',
  rf_loading: 'Reading…', rf_failed: 'This could not be read. Reload the page to try again.',
  rf_library_empty: 'The library is empty. Add past scripts, subtitle files or videos to have something to look at here.', rf_go_library: 'Open the library',
  rf_pinned: 'Pinned to this script', rf_pinned_count: '{k}/{n}',
  rf_pinned_why: 'Items {@name} pinned to this script. They stay here, up to {n}. Pinning and unpinning never change the script.',
  rf_pinned_none: 'Nothing is pinned yet. Use "Pin here" on any item below.',
  rf_basis: 'What the draft saw',
  rf_basis_why: 'This script began as an AI draft. Its prompt contained the items below; they were found again by the titles recorded at the time.',
  rf_basis_none: 'The prompt for this draft contained none of {@names} material.',
  rf_role_example: 'its text was in the prompt', rf_role_opening: 'its opening was in the prompt',
  rf_same_title: '{n} items carry this title now. One of them is shown; the draft may have seen another.',
  rf_missing: '"{title}" is no longer in the library (removed, or renamed since).',
  rf_basis_left_out: '{n} more are not listed.', rf_basis_left_out_one: '1 more is not listed.',
  rf_related: 'On the same topic',
  rf_related_why: 'Items that mention this script\'s target keywords or the words of its title. Measured as on the Trends page: the word appears as written, or at least {pct}% of its pieces do.',
  rf_related_order: 'Order: more keywords found first, then words of the title, then mentions, then the video\'s interactions, then the newer item.',
  rf_looked_for: 'Looked for: ', rf_term_title: '{term} (title)',
  rf_needs_terms: 'This script has no target keyword yet and its title names no topic, so nothing is listed here. Add target keywords above, or give it a title that says what it is about.',
  rf_related_none: 'Nothing in the library mentions these words.',
  rf_related_none_title: 'Nothing in the library mentions these words. The title is looked for in the words its spaces and punctuation divide it into; add target keywords above for a closer match.',
  rf_related_more: '{n} more are on the topic and not listed; the search below finds them.',
  rf_related_more_one: '1 more is on the topic and not listed; the search below finds it.',
  rf_related_above: '{n} more on the topic are listed above.', rf_related_above_one: '1 more on the topic is listed above.',
  rf_search: 'Search the library',
  rf_search_why: 'Finds items by the words in their title or text, so that any of them can be pinned. The text is looked for as typed and is not split into words; letter case and spaces make no difference.',
  rf_search_ph: 'e.g. 面霜', rf_searching: 'Searching…',
  rf_search_none: 'Nothing found for "{q}".', rf_search_found: '{n} items found.', rf_search_found_one: '1 item found.',
  rf_search_more: '{n} items found; the first {k} are shown. Type more to narrow it down.',
  rf_kind_script: 'Script', rf_kind_subtitle: 'Subtitle file', rf_kind_video: 'Video',
  rf_from_script: 'posted from a script here', rf_this_script: 'the item this script was posted as',
  rf_sponsored: 'Sponsored', rf_organic: 'Not sponsored',
  rf_numbers_none: 'No numbers entered', rf_interactions: '{n} interactions', rf_interactions_one: '1 interaction',
  rf_times: '{x}× {@their} usual',
  rf_read_after: 'numbers read {n} days after posting', rf_read_after_one: 'numbers read 1 day after posting',
  rf_read_same: 'numbers read the day it was posted',
  rf_w_transcript: 'Machine transcript, not confirmed: the words below may not be what {@name} said.',
  rf_w_posted: 'Posted from a script written here. Its wording is not confirmed as {@names} own and may hold sentences an AI wrote.',
  rf_w_tag: 'wording not confirmed',
  rf_opening: 'Opening', rf_opening_ai: 'written by AI, not {@names} own opening',
  rf_full: 'Read the whole text', rf_full_close: 'Fold the text', rf_full_more: 'Show the next {n} characters',
  rf_full_part: 'Showing the first {k} of {n} characters.', rf_full_all: '{n} characters in all.', rf_full_all_one: '1 character in all.',
  rf_full_failed: 'The text could not be read. Try once more.',
  rf_long: 'A long item: only its first {n} characters were looked through.',
  rf_pin: 'Pin here', rf_unpin: 'Unpin', rf_pin_full: 'All {n} pins are in use',
  rf_pinned_done: 'Pinned', rf_unpinned_done: 'Unpinned',
  rf_to_library: 'Open in the library',
  rf_why_count: 'mentions {term} {n} times', rf_why_count_one: 'mentions {term} once', rf_why_in_title: ', also in its title',
  rf_why_pieces: 'has {k} of the {m} pieces of {term}: {list}',
  rf_why_title_count: 'shares {term} with this script\'s title ({n} times)', rf_why_title_count_one: 'shares {term} with this script\'s title (once)',
  rf_why_title_pieces: 'has {k} of the {m} pieces of {term}, from this script\'s title: {list}',
  rf_found_title: 'in the title', rf_found_count: 'in the text {n} times', rf_found_count_one: 'in the text once',
  rf_usual: '{@Names} usual video gets {n} interactions (the middle value of {v} non-sponsored videos with numbers).',
  rf_usual_one: '{@Names} usual video gets {n} interactions (from the only non-sponsored video with numbers).',
  rf_no_usual: 'No non-sponsored video has numbers yet, so no result can be given as a multiple of the usual.',
});

const RF_METRICS = ['views', 'likes', 'collects', 'comments'];      // must match METRIC_FIELDS in studio/store.py
const RF_KINDS = { script: 'rf_kind_script', subtitle: 'rf_kind_subtitle', video: 'rf_kind_video' };   // how an item came in
const RF_WIDE = '(min-width: 1280px)';       // must match the media query of this panel in app.css
const RF_STORED = 'rf-open';                 // kept between runs (KEPT in app.js): '1' when the panel was opened last, '0' when it was closed

/* What the panel shows, kept here and not read off the page: adding, deleting or moving a line draws the whole
   editor again, and the panel has to come back as it was.
   scriptId: the script all of this belongs to. page: the `cur` it was last drawn for; a new one means the script
   page was opened again, and the lists are asked for again. auto: whether the panel is open while nothing was
   chosen in this browser yet, decided each time the script page is opened. data: the last answer of /refs. sig:
   the title and keywords that answer is known to be for, or '' when that is not known. seq and searchSeq name the
   newest request of each kind, so that the answer to an older one is dropped. query, found, searchError: the
   search box, its results, a refusal. full: the whole texts that were opened, by the key of their card, each with
   how far it is scrolled. scroll: how far the panel is scrolled. */
const rfUi = { scriptId: null, page: null, auto: false, data: null, loading: false, failed: false, sig: '', seq: 0, busy: false,
  query: '', found: null, searching: false, searchError: '', searchSeq: 0, full: {}, scroll: 0 };
let rfSearchTimer = null;

const rfSep = () => (lang === 'zh' ? '、' : ', ');
/* What the server said about a refused request, in the interface language. */
const rfSay = (e) => (lang === 'zh' ? e.message_zh : e.detail) || e.detail || t('err_default');
const rfSig = (s) => JSON.stringify([s.title, s.keywords || []]);
const rfWide = () => window.matchMedia(RF_WIDE).matches;

/* Open or closed. What was chosen last holds, also the next time the app is opened (recall, in app.js). Before any choice, the panel is open where it has
   a column of its own and there is something in the library to show, and closed otherwise (rfUi.auto): that is
   decided when the script page is opened and then stays, so that a window made wider or narrower does not open
   or close the panel the next time a line is moved. */
function rfIsOpen() {
  const kept = recall(RF_STORED);
  if (kept === '1' || kept === '0') return kept === '1';
  return rfUi.auto;
}

/* ---------- one item ---------- */
function rfWhyText(w) {
  const title = w.from === 'title';
  if (w.count) {
    const key = title ? countKey('rf_why_title_count', w.count) : countKey('rf_why_count', w.count);
    return tf(key, { term: w.term, n: w.count }) + (w.in_title && !title ? t('rf_why_in_title') : '');
  }
  return tf(title ? 'rf_why_title_pieces' : 'rf_why_pieces', { term: w.term, k: w.pieces_found, m: w.of, list: (w.pieces || []).join(rfSep()) });
}

function rfNumbersText(x) {
  if (!x.has_numbers) return t('rf_numbers_none');
  const said = [];
  if (x.interactions !== null) said.push(tf(countKey('rf_interactions', x.interactions), { n: num(x.interactions) }));
  if (x.times_usual !== null) said.push(tf('rf_times', { x: x.times_usual }));
  RF_METRICS.filter((f) => x[f] !== null && x[f] !== undefined).forEach((f) => said.push(`${t('m_' + f)} ${num(x[f])}`));
  const days = x.posted ? x.posted.read_after_days : null;
  if (typeof days === 'number') said.push(days === 0 ? t('rf_read_same') : tf(countKey('rf_read_after', days), { n: days }));
  return said.join(' · ');
}

/* The start of an item's text, with what was found in it marked. Three pieces of text, each through esc().
   The server names the place of the mark in characters as it counts them, where an emoji is one. Here an emoji
   counts as two, so the text is cut by characters (Array.from) and never by its own count. */
function rfExcerptHtml(ex) {
  const chars = Array.from(String(ex.text || ''));
  const piece = (from, to) => chars.slice(from, to).join('');
  const mark = Array.isArray(ex.mark) ? ex.mark : null;
  const body = mark
    ? `${esc(piece(0, mark[0]))}<mark>${esc(piece(mark[0], mark[1]))}</mark>${esc(piece(mark[1]))}`
    : esc(piece(0));
  return (ex.cut_before ? '…' : '') + body + (ex.cut_after ? '…' : '');
}

function rfFullHtml(key, st) {
  const n = st.chars || 0;
  const shown = st.next === null ? n : st.next;      // how far the server has handed the text out, in characters as it counts them
  const limit = (rfUi.data && rfUi.data.limits.text_part_chars) || 0;
  const first = st.busy && !st.text;                 // the first part is on its way: there is nothing to say about lengths yet
  let state = '';
  if (st.failed) state = t('rf_full_failed');
  else if (!first) state = st.next !== null ? tf('rf_full_part', { k: num(shown), n: num(n) }) : tf(countKey('rf_full_all', n), { n: num(n) });
  const body = first ? t('rf_loading') : st.text;
  const next = st.text ? tf('rf_full_more', { n: num(Math.min(limit, Math.max(0, n - shown)) || limit) }) : t('rf_full');
  const more = st.next !== null && !st.busy
    ? `<button type="button" class="btn" data-rf="more" data-rf-key="${esc(key)}">${esc(next)}</button>` : '';
  return `<div class="rf-text whole" data-rf-whole="${esc(key)}">${esc(body)}</div>
    <div class="rf-actions"><span class="meta" data-rf-full-state>${esc(state)}</span>${more}
      <button type="button" class="btn ghost" data-rf="fold" data-rf-key="${esc(key)}">${esc(t('rf_full_close'))}</button></div>`;
}

/* `part` names the list the card stands in ('pinned', 'basis', 'related', 'found'): the same item can stand in
   two lists, and each card opens and folds its whole text by itself. */
function rfItemHtml(x, part) {
  const key = `${part}:${x.id}`;
  const d = rfUi.data;
  const kind = Object.keys(RF_KINDS).includes(x.kind) ? x.kind : 'script';
  const tags = [`<span class="tag ${kind === 'script' ? '' : 'ok'}" data-rf-kind="${kind}">${esc(t(RF_KINDS[kind]))}</span>`,
    `<span class="tag ${x.is_sponsored ? 'warn' : ''}" data-rf-sponsored="${x.is_sponsored ? 1 : 0}">${esc(t(x.is_sponsored ? 'rf_sponsored' : 'rf_organic'))}</span>`];
  if (x.of_this_script) tags.push(`<span class="tag ok">${esc(t('rf_this_script'))}</span>`);
  else if (x.posted) tags.push(`<span class="tag">${esc(t('rf_from_script'))}</span>`);
  const why = [];
  (x.roles || []).forEach((r) => why.push(`<span class="tag" data-rf-role="${esc(r)}">${esc(t(r === 'opening' ? 'rf_role_opening' : 'rf_role_example'))}</span>`));
  if (x.found) {
    if (x.found.in_title) why.push(`<span class="tag ok" data-rf-why>${esc(t('rf_found_title'))}</span>`);
    if (x.found.count) why.push(`<span class="tag ok" data-rf-why>${esc(tf(countKey('rf_found_count', x.found.count), { n: x.found.count }))}</span>`);
  } else {
    (x.why || []).forEach((w) => why.push(`<span class="tag ok" data-rf-why>${esc(rfWhyText(w))}</span>`));
  }
  const waits = x.wording === 'posted_waiting' || x.wording === 'transcript_waiting';
  const note = waits
    ? `<div class="rf-note" data-rf-wording="${esc(x.wording)}">${esc(t(x.wording === 'posted_waiting' ? 'rf_w_posted' : 'rf_w_transcript'))}</div>` : '';
  const openingTag = x.opening_by_ai ? `<span class="tag warn" data-rf-ai-opening>${esc(t('rf_opening_ai'))}</span>`
    : (waits ? `<span class="tag warn">${esc(t('rf_w_tag'))}</span>` : '');
  const opening = x.opening
    ? `<div class="rf-opening" data-rf-opening><span class="meta">${esc(t('rf_opening'))}</span> ${esc(x.opening)} ${openingTag}</div>` : '';
  const st = rfUi.full[key];
  const reading = st && st.open ? rfFullHtml(key, st)
    : `<div class="rf-text" data-rf-excerpt>${rfExcerptHtml(x.excerpt || {})}</div>`;
  const pins = d ? d.pinned.length : 0;
  const full = !!d && !x.pinned && pins >= d.limits.pins;
  const pinLabel = full ? tf('rf_pin_full', { n: d.limits.pins }) : t('rf_pin');
  const pin = x.pinned
    ? `<button type="button" class="btn" data-rf="unpin" data-rf-item="${esc(x.id)}">${esc(t('rf_unpin'))}</button>`
    : `<button type="button" class="btn" data-rf="pin" data-rf-item="${esc(x.id)}" ${full ? 'disabled' : ''}>${esc(pinLabel)}</button>`;
  const same = x.same_title > 1 ? `<div class="meta" data-rf-same-title>${esc(tf('rf_same_title', { n: x.same_title }))}</div>` : '';
  const long = x.long && d ? `<div class="meta" data-rf-long>${esc(tf('rf_long', { n: num(d.limits.scan_chars) }))}</div>` : '';
  const whole = st && st.open ? ''
    : `<button type="button" class="btn ghost" data-rf="full" data-rf-key="${esc(key)}" data-rf-item="${esc(x.id)}">${esc(t('rf_full'))}</button>`;
  return `<div class="rf-item" data-rf-card="${esc(key)}">
    <div class="rf-name" data-rf-name>${esc(x.title + (x.title_cut ? '…' : ''))}</div>
    <div>${tags.join('')}${why.join('')}</div>${same}
    <div class="meta rf-nums" data-rf-numbers>${esc(rfNumbersText(x))}</div>
    ${note}${opening}${reading}${long}
    <div class="rf-actions">${pin}${whole}
      <a href="#/library" data-rf="library" data-rf-item="${esc(x.id)}">${esc(t('rf_to_library'))}</a></div>
  </div>`;
}

/* ---------- the lists ---------- */
/* One part of the panel. `why` is the sentence that says why its items are there, as text; `body` is markup. */
function rfPartHtml(part, title, count, why, body) {
  const counted = count ? ` <span class="meta">${esc(count)}</span>` : '';
  return `<section class="rf-sec" data-rf-part="${part}"><h4>${esc(title)}${counted}</h4>
    <p class="hint">${esc(why)}</p>${body}</section>`;
}

function rfListsHtml() {
  const d = rfUi.data;
  if (!d) return `<p class="hint" id="${rfUi.failed ? 'rf-failed' : 'rf-loading'}">${esc(t(rfUi.failed ? 'rf_failed' : 'rf_loading'))}</p>`;
  if (!d.library) {
    return `<p class="hint" id="rf-empty">${esc(t('rf_library_empty'))} <a href="#/library">${esc(t('rf_go_library'))}</a></p>`;
  }
  const items = (list, part) => list.map((x) => rfItemHtml(x, part)).join('');
  const parts = [rfPartHtml('pinned', t('rf_pinned'), tf('rf_pinned_count', { k: d.pinned.length, n: d.limits.pins }),
    tf('rf_pinned_why', { n: d.limits.pins }),
    items(d.pinned, 'pinned') || `<p class="hint" id="rf-pinned-none">${esc(t('rf_pinned_none'))}</p>`)];
  if (d.basis) {
    const b = d.basis;
    const missing = b.missing.map((m) => `<p class="rf-note" data-rf-missing>${esc(tf('rf_missing', { title: m.title }))}</p>`).join('');
    const more = b.left_out ? `<p class="hint">${esc(tf(countKey('rf_basis_left_out', b.left_out), { n: b.left_out }))}</p>` : '';
    parts.push(rfPartHtml('basis', t('rf_basis'), '', t('rf_basis_why'),
      (items(b.items, 'basis') + missing + more) || `<p class="hint" id="rf-basis-none">${esc(t('rf_basis_none'))}</p>`));
  }
  const r = d.related;
  let body;
  if (r.needs_terms) body = `<div class="banner" id="rf-needs-terms">${esc(t('rf_needs_terms'))}</div>`;
  else {
    const named = (x) => (x.from === 'title' ? tf('rf_term_title', { term: x.text }) : x.text);
    const looked = r.terms.map((x) => `<span class="tag" data-rf-term="${esc(x.from)}">${esc(named(x))}</span>`).join('');
    const hidden = r.found - r.items.length;
    /* Nothing found at all is said only when that is so: items that stand above (pinned, or seen by the draft) are
       on the topic too. Looked for by the title alone, it also says how a title is read and what finds more. */
    const none = t(r.terms.every((x) => x.from === 'title') ? 'rf_related_none_title' : 'rf_related_none');
    const nothing = r.found || r.listed_above ? '' : `<p class="hint" id="rf-related-none">${esc(none)}</p>`;
    const more = hidden > 0 ? `<p class="hint" id="rf-related-more">${esc(tf(countKey('rf_related_more', hidden), { n: hidden }))}</p>` : '';
    const above = r.listed_above
      ? `<p class="hint" id="rf-related-above">${esc(tf(countKey('rf_related_above', r.listed_above), { n: r.listed_above }))}</p>` : '';
    body = `<p class="hint" id="rf-terms">${esc(t('rf_looked_for'))}${looked}</p>${items(r.items, 'related')}${nothing}${more}${above}`;
  }
  parts.push(rfPartHtml('related', t('rf_related'), '',
    tf('rf_related_why', { pct: Math.round(d.limits.relevant_at * 100) }) + (lang === 'zh' ? '' : ' ') + t('rf_related_order'), body));
  return parts.join('');
}

function rfFoundHtml() {
  const f = rfUi.found;
  if (rfUi.searchError) return `<p class="rf-note" id="rf-search-error" role="alert">${esc(rfUi.searchError)}</p>`;
  if (rfUi.searching && !f) return `<p class="hint" id="rf-searching">${esc(t('rf_searching'))}</p>`;
  if (!f) return '';
  if (!f.found) return `<p class="hint" id="rf-search-none">${esc(tf('rf_search_none', { q: f.query }))}</p>`;
  const count = f.found > f.items.length ? tf('rf_search_more', { n: f.found, k: f.items.length })
    : tf(countKey('rf_search_found', f.found), { n: f.found });
  return `<p class="hint" id="rf-search-count">${esc(count)}</p>${f.items.map((x) => rfItemHtml(x, 'found')).join('')}`;
}

function rfFootHtml() {
  const d = rfUi.data;
  if (!d || !d.library) return '';
  const usual = d.usual_interactions
    ? tf(countKey('rf_usual', d.videos_with_numbers), { n: num(Math.round(d.usual_interactions)), v: d.videos_with_numbers })
    : t('rf_no_usual');
  return `<p class="hint" id="rf-usual">${esc(usual)}</p>`;
}

/* An open text is put back where it was being read. The lists are drawn again on every answer, and a text that
   jumped back to its first line each time its next part arrived could not be read through. */
function rfKeepPlaces() {
  document.querySelectorAll('#rf-body [data-rf-whole]').forEach((el) => {
    const st = rfUi.full[el.dataset.rfWhole];
    if (st && st.scroll) el.scrollTop = st.scroll;
  });
}

/* The lists, the results and the note under them are drawn again; the search box is not, so that an answer that
   arrives while something is being typed there does not take the cursor away. */
function rfPaintLists() {
  const lists = document.getElementById('rf-lists');
  if (!lists) return;
  const d = rfUi.data;
  lists.innerHTML = rfListsHtml();
  const search = document.getElementById('rf-search');
  if (d && d.library && !search.firstChild) {
    search.innerHTML = `<section class="rf-sec" data-rf-part="search"><h4><label for="rf-q">${esc(t('rf_search'))}</label></h4>
      <p class="hint">${esc(t('rf_search_why'))}</p>
      <input type="text" id="rf-q" data-rf-q maxlength="${Number(d.limits.query_chars)}" autocomplete="off" spellcheck="false"
        placeholder="${esc(t('rf_search_ph'))}" value="${esc(rfUi.query)}">
      <div id="rf-found"></div></section>`;
  }
  rfPaintFound();
  document.getElementById('rf-foot').innerHTML = rfFootHtml();
}

function rfPaintFound() {
  const el = document.getElementById('rf-found');
  if (el) el.innerHTML = rfFoundHtml();
  rfKeepPlaces();
}

/* The whole of #rf-box: the row that opens the panel, or the panel. */
function rfPaint() {
  const box = document.getElementById('rf-box');
  if (!box) return;
  const open = rfIsOpen();
  const wrap = box.closest('.wrap');
  if (wrap) wrap.classList.toggle('rf-on', open);     // app.css gives the panel its own column where the window is wide
  if (!open) {
    box.innerHTML = `<div class="rf-bar"><button type="button" class="btn" data-rf="open" aria-expanded="false">${esc(t('rf_open'))} ▸</button>
      <span class="hint">${esc(t('rf_closed_hint'))}</span></div>`;
    return;
  }
  box.innerHTML = `<aside class="rf-panel" id="rf-panel" aria-label="${esc(t('rf_title'))}">
    <div class="rf-head"><b>${esc(t('rf_title'))}</b><span class="spacer"></span>
      <button type="button" class="btn ghost" data-rf="close" aria-expanded="true">${esc(t('rf_close'))}</button></div>
    <div class="rf-body" id="rf-body"><p class="hint" id="rf-about">${esc(t('rf_about'))}</p>
      <div id="rf-lists"></div><div id="rf-search"></div><div id="rf-foot"></div></div></aside>`;
  rfPaintLists();
  const body = document.getElementById('rf-body');
  body.scrollTop = rfUi.scroll;
  body.onscroll = () => { rfUi.scroll = body.scrollTop; };
}

/* ---------- asking the server ---------- */
/* What the lists are given (the last answer of /refs, or of a pin): what a search found says the same about its pins. */
function rfTake(data) {
  rfUi.data = data;
  const now = new Set(data.pinned.map((x) => x.id));
  if (rfUi.found) rfUi.found.items.forEach((x) => { x.pinned = now.has(x.id); });
}

/* `inStep`: the script on the server is known to be the one on this page right now (it was just loaded, or just
   saved with nothing typed since). Only then is the answer known to be for this title and these keywords. Asked
   at any other moment (a save may be on its way), that is not known, and the next save asks again. */
async function rfLoad(inStep) {
  const s = cur && cur.script;
  if (!s) return;
  const seq = ++rfUi.seq;
  const sig = inStep ? rfSig(s) : '';
  rfUi.loading = true;
  let data = null;
  try { data = await api('GET', `/api/scripts/${s.id}/refs`); } catch (e) { /* said below; what was shown before stays */ }
  if (seq !== rfUi.seq || !cur || cur.script.id !== s.id) return;
  rfUi.loading = false;
  rfUi.failed = !data;
  if (data) { rfTake(data); rfUi.sig = sig; }
  rfPaintLists();
}

/* Called at the end of paintScript (app.js): the editor was drawn again, so the panel is put back as it was. */
function rfAttach() {
  if (!cur || !document.getElementById('rf-box')) return;
  const s = cur.script;
  if (rfUi.scriptId !== s.id) {
    clearTimeout(rfSearchTimer);
    Object.assign(rfUi, { scriptId: s.id, data: null, loading: false, failed: false, sig: '', query: '', found: null,
      searching: false, searchError: '', full: {}, scroll: 0 });
    rfUi.seq += 1; rfUi.searchSeq += 1;       // answers still on their way belong to another script
  }
  const reopened = rfUi.page !== cur;         // the script page was opened or loaded again, not only drawn again
  rfUi.page = cur;
  if (reopened) rfUi.auto = rfWide() && appState.sample_count > 0;
  rfPaint();
  if (!rfIsOpen()) return;
  if (reopened || !rfUi.data) rfLoad(reopened);
  if (reopened && rfUi.query.trim()) rfSearch();      // the library may have changed while she was on another page
}

/* Called from saveNow (app.js) after a save: a new title or keyword changes what is on the same topic.
   When something was typed after this save was sent, the server does not hold what the page holds: the lists
   would be asked for by the older title and then taken for the newer one. The save that follows comes here again. */
function rfAfterSave(s) {
  if (!cur || cur.script !== s || dirty) return;
  if (!rfIsOpen() || !document.getElementById('rf-lists')) return;
  if (rfSig(s) !== rfUi.sig) rfLoad(true);
}

async function rfPin(id, pinned) {
  const s = cur && cur.script;
  if (!s || rfUi.busy) return;
  rfUi.busy = true;
  const asked = rfUi.seq;
  try {
    /* Only the pin is sent. The script is not saved here and what is being typed is not touched. */
    const data = await api('PUT', `/api/scripts/${s.id}/pins`, { sample_id: id, pinned });
    if (!cur || cur.script.id !== s.id) return;
    /* A read of the lists that was on its way, or was sent while this pin was, may be for a newer title than this
       answer and would be dropped here: the pin is shown at once, and the lists are asked for once more. */
    const again = rfUi.loading || rfUi.seq !== asked;
    rfUi.seq += 1;
    rfTake(data);
    Object.assign(rfUi, { loading: false, failed: false });
    rfPaintLists();
    toast(t(pinned ? 'rf_pinned_done' : 'rf_unpinned_done'));
    if (again) rfLoad(false);
  } catch (e) {
    toast(rfSay(e));
    /* The item went away, or the last free place was taken, somewhere else: show the lists as they are now. */
    if (e.code === 'no_item' || e.code === 'too_many_pins') rfLoad(false);
  } finally {
    rfUi.busy = false;
  }
}

async function rfSearch() {
  const s = cur && cur.script;
  if (!s) return;
  const q = rfUi.query.trim();
  const seq = ++rfUi.searchSeq;
  rfUi.searchError = '';
  if (!q) { rfUi.found = null; rfUi.searching = false; rfPaintFound(); return; }
  rfUi.searching = true;
  rfPaintFound();
  try {
    const found = await api('GET', `/api/scripts/${s.id}/refs/search?q=${encodeURIComponent(q)}`);
    if (seq !== rfUi.searchSeq) return;
    rfUi.found = found;
  } catch (e) {
    if (seq !== rfUi.searchSeq) return;
    rfUi.found = null;
    rfUi.searchError = rfSay(e);
  }
  rfUi.searching = false;
  rfPaintFound();
}

/* The whole text of one item, a part at a time: each request brings at most what the server says in `limit`. */
async function rfMore(key, id) {
  const st = rfUi.full[key];
  if (!st || st.busy || st.next === null) return;
  st.busy = true;
  rfPaintLists();
  try {
    const part = await api('GET', `/api/samples/${id}/text?start=${st.next}`);
    Object.assign(st, { text: st.text + part.text, chars: part.chars, next: part.next, failed: false });
  } catch (e) { st.failed = true; }
  st.busy = false;
  rfPaintLists();
}

/* The link into the library goes there by itself; this brings the item into view once the library is drawn. */
function rfShowInLibrary(id) {
  if (!/^[0-9a-f]{12}$/.test(String(id))) return;
  const until = Date.now() + 6000;
  const look = () => {
    const card = location.hash === '#/library' ? document.querySelector(`.sample[data-id="${id}"]`) : null;
    if (card) {
      const text = card.querySelector('details');
      if (text) text.open = true;
      card.scrollIntoView({ block: 'center' });
    } else if (Date.now() < until) setTimeout(look, 120);
  };
  setTimeout(look, 0);
}

/* ---------- what the buttons and the search box do ---------- */
function rfAct(what, el) {
  const id = el.dataset.rfItem;
  const key = el.dataset.rfKey;
  if (what === 'open' || what === 'close') {
    remember(RF_STORED, what === 'open' ? '1' : '0');
    keepWithCore();       // app.js: the core keeps it too, for the desktop app, whose page has another address at every start
    rfPaint();
    if (what === 'open') rfLoad(false);
  } else if (what === 'pin' || what === 'unpin') {
    rfPin(id, what === 'pin');
  } else if (what === 'full') {
    const st = rfUi.full[key] || (rfUi.full[key] = { id, open: false, text: '', chars: 0, next: 0, busy: false, failed: false, scroll: 0 });
    st.open = true;
    if (st.text) rfPaintLists(); else rfMore(key, id);
  } else if (what === 'more') {
    if (rfUi.full[key]) rfMore(key, rfUi.full[key].id);
  } else if (what === 'fold') {
    if (rfUi.full[key]) rfUi.full[key].open = false;
    rfPaintLists();
  }
}

document.addEventListener('click', (e) => {
  const el = e.target instanceof Element ? e.target.closest('[data-rf]') : null;
  if (!el) return;
  if (el.dataset.rf === 'library') { rfShowInLibrary(el.dataset.rfItem); return; }      // the link itself goes there
  e.preventDefault();
  rfAct(el.dataset.rf, el);
});

/* How far an open text is scrolled is written down as it happens (a scroll is not passed up to the document, so it
   is caught on its way down): by the time the lists are drawn again, the text that was scrolled is gone. */
document.addEventListener('scroll', (e) => {
  const el = e.target instanceof Element ? e.target : null;
  const st = el && el.dataset.rfWhole ? rfUi.full[el.dataset.rfWhole] : null;
  if (st) st.scroll = el.scrollTop;
}, true);

const rfTyped = (e) => {
  const el = e.target instanceof Element ? e.target.closest('[data-rf-q]') : null;
  if (!el) return;
  rfUi.query = el.value;
  if (e.isComposing) return;                  // Chinese is still being composed: the letters typed so far are not the query
  clearTimeout(rfSearchTimer);
  rfSearchTimer = setTimeout(rfSearch, 250);
};
document.addEventListener('input', rfTyped);
document.addEventListener('compositionend', rfTyped);
document.addEventListener('keydown', (e) => {
  const el = e.target instanceof Element ? e.target.closest('[data-rf-q]') : null;
  if (!el || e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;     // Enter also confirms Chinese input
  e.preventDefault();
  clearTimeout(rfSearchTimer);
  rfUi.query = el.value;
  rfSearch();
});
