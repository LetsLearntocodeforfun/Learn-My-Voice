'use strict';
/* One request, typed or spoken, becomes current keywords and topic ideas.
   Loaded after setup.js and before pages.js (which boots the app); helpers from all three are used at call time.

   What leaves the computer, and when:
   - Speaking: the recording goes to this app's own server on 127.0.0.1, is written down there by the local
     transcriber, and is deleted. The browser's built-in speech recognition is not used: Chrome's sends the audio
     to Google.
   - "Find ideas": two separate exchanges with the AI service chosen in settings. A web lookup that carries only
     the request, the niches, up to 20 titles and the tracked keywords; then the ideas prompt, without web search.
     The ideas prompt also carries, for a library item whose comments were pasted in, the summary of what viewers
     wrote and their questions, and the videos that are planned and not posted (pipeline.ideas_prompt lists all of it).
     The hint under the box (dvSentKey) says which of these will happen with the current settings. */

/* The numbers in dv_sent_* and mb_intro_ideas must match studio/pipeline.py: 20 titles (trends_prompt),
   RECOMMEND_MAX_VIDEOS 60, RECOMMEND_EXCERPT_CHARS 400, IDEAS_MAX_TRENDS 12, IDEAS_MAX_QUESTIONS 4, and
   PROMPT_MAX_PLANS 20 in studio/planner.py. tests/test_discover.py and tests/test_planner.py check them. */
Object.assign(I18N.zh, {
  dv_label: '这次想拍什么？', dv_ph: '写一句或说一句就行，例如：秋冬护肤、周末带娃去哪儿。不填也可以。',
  dv_go: '✨ 找选题', dv_mic: '🎙 说一句', dv_mic_stop: '■ 说完了',
  dv_lookup_check: '先联网查一下最近的热词',
  dv_home_title: '一句话找选题',
  dv_mic_listening: '正在听… {s} 秒（最多 {max} 秒），说完点「说完了」。',
  dv_mic_writing: '正在这台电脑上把你说的写下来…', dv_mic_waiting: '前面还有一段视频在转写，排到了就开始…',
  dv_mic_done: '写好了。可以改一改，再点「找选题」。',
  dv_mic_privacy: '说的话只在这台电脑上转成文字，录音用完就删。',
  dv_mic_asking: '浏览器会问能不能用麦克风，请点「允许」。',
  dv_mic_denied: '用不了麦克风。请在浏览器里允许这个页面使用麦克风，或者直接打字。',
  dv_mic_no_browser: '这个浏览器不能录音，请直接打字。',
  dv_mic_unsupported: '这台电脑不能在本机把语音转成文字，请直接打字。',
  dv_mic_not_installed: '说话输入要先装好本机转写。', dv_mic_install_go: '去安装',
  dv_mic_failed: '没能写下来：', dv_mic_too_short: '没有录到声音，请再说一次。',
  dv_sent_search: '点「找选题」后分两步发给你在设置里选的 AI 服务。第一步联网查热词，只带：这句话、你的内容方向、最多 20 个非商单素材的标题、已经在跟踪的热词。第二步想选题，不联网，会带：你的个人资料、声音档案里的人设和段落写法、最多 60 篇素材的标题、数据和开头 400 个字、你贴过的观众评论的摘要（是按词表数出来的还是 AI 读的、概括、每篇最多 4 个观众提问的原话）、按数据算出的名次、中位数和收藏率、最多 12 个在跟踪的热词、最多 20 条还没发布的拍摄计划，还有这句话。',
  dv_sent_plain: '点「找选题」后发给你在设置里选的 AI 服务，不联网。会带：这句话、你的个人资料、声音档案里的人设和段落写法、最多 60 篇素材的标题、数据和开头 400 个字、你贴过的观众评论的摘要（是按词表数出来的还是 AI 读的、概括、每篇最多 4 个观众提问的原话）、按数据算出的名次、中位数和收藏率、最多 12 个在跟踪的热词、最多 20 条还没发布的拍摄计划。',
  dv_sent_local: '只发给这台电脑上的本地模型，内容不会离开这台电脑。本地模型不能联网，所以选题只参考已经在跟踪的热词。',
  dv_sent_manual: '现在是复制粘贴模式：点「找选题」后分两步，每一步都会先让你看到要发出去的提示词。第一步请能联网的 AI 查热词（可以跳过），第二步想选题。',
  dv_byhand: '用复制粘贴查热词', dv_found_waiting: '已经查到 {n} 个热词，下次点「找选题」会用上。',
  dv_stage_queued: '正在开始…', dv_stage_starting: '正在开始…',
  dv_stage_looking_up: '正在联网查最近的热词，查完接着想选题…', dv_stage_thinking: '正在结合你自己的数据想选题…',
  dv_running_hint: '可能要一两分钟。可以先去别的页面，回来还在。', dv_elapsed: '已经 {s} 秒',
  dv_patterns_title: '从你的数据里看出的规律',
  dv_patterns_hint: '这是 AI 从你自己的视频里总结的，每条下面标着它依据的是哪几条视频。没有标的，是它没有指出出处的说法。',
  dv_planned: '已加入计划',
  dv_for: '这次问的是：「{q}」', dv_ideas_title: '选题', dv_earlier_title: '之前的选题', dv_found_title: '这次查到的热词',
  dv_lookup_done: '联网查到 {n} 个热词', dv_lookup_gone: '这次联网查到的热词，现在都不在跟踪列表里了。',
  dv_lookup_unverified: '这些词是 AI 查到的，没有核实过：请点「来源」看一眼再用。',
  dv_lookup_failed: '这次没能联网查热词：{why} 选题只参考了已经在跟踪的热词。',
  dv_lookup_no_web: '当前的 AI 服务不能联网，这次没有查热词，选题只参考了已经在跟踪的热词。',
  dv_lookup_not_asked: '这次没有联网查热词，选题只参考了已经在跟踪的热词。',
  dv_pages: '查的时候引用的网页（{n}）',
  dv_ideas_failed: '这次没能给出选题：{why}', dv_ideas_no_samples: '素材库里还没有你的内容，所以给不出结合你自己数据的选题。',
  dv_kept_earlier: '下面是之前的选题。', dv_job_failed: '这次没有完成：',
  dv_empty: '写一句想拍的方向，点「找选题」。不写也可以，会按你自己的数据来想。',
  dv_step1_title: '第 1 步，共 2 步：请能联网的 AI 查热词', dv_step1_skip: '跳过，直接想选题', dv_step2_title: '第 2 步，共 2 步：想选题',
  mb_skip: '跳过',
  mb_intro_ideas: '提示词里有：你在设置里填的个人资料（名字、账号名、内容方向、自我介绍、一起出镜的人、不说的话）、声音档案里的人设和段落写法、最多 60 篇素材的标题、是否商单、数据和开头 400 个字（还没确认的视频转写不带文字）、你贴过的观众评论的摘要（是按词表数出来的还是 AI 读的、评论条数、概括、每篇最多 4 个观众提问的原话）、按数据算出的名次、中位数和收藏率、最多 12 个在跟踪的热词、最多 20 条还没发布的拍摄计划（标题、状态、日期、关键词、要回答的观众提问），以及你这次填的这句话。这些会随提示词一起发给 AI。',
  err_bad_focus: '这句话没法识别，请重新写一下。', err_upload_not_found: '录音没有传上来，请再说一次。',
  err_too_large: '太长了，一两句话就够。',
  dv_auto_wait_title: '自动学习刚刚刷新了一组候选选题（{n} 个）',
  dv_auto_wait_time: '刷新时间：{t}',
  dv_auto_wait_trends: '依据的顶部热词：',
  dv_auto_wait_note: '你当前保存的选题没有被覆盖。点击「换上这组选题」才会替换下方选题；点击「忽略」则保留原样。',
  dv_auto_take: '换上这组选题', dv_auto_dismiss: '忽略',
  dv_auto_taken: '已换上这组新选题', dv_auto_dismissed: '已忽略这组候选选题',
  dv_auto_in_use: '当前显示的是自动学习在 {t} 刷新的选题',
});
Object.assign(I18N.en, {
  dv_label: 'What should the next video be about?', dv_ph: 'Type or say one line, for example: autumn skincare, a weekend out with the kids. Leaving it empty is fine.',
  dv_go: '✨ Find ideas', dv_mic: '🎙 Say it', dv_mic_stop: '■ Done speaking',
  dv_lookup_check: 'Look up current keywords on the web first',
  dv_home_title: 'Ideas from one line',
  dv_mic_listening: 'Listening… {s} s (at most {max} s). Click "Done speaking" when finished.',
  dv_mic_writing: 'Writing down what was said, on this computer…', dv_mic_waiting: 'A video is still being transcribed; this starts as soon as it is done…',
  dv_mic_done: 'Written down. Edit it if needed, then click "Find ideas".',
  dv_mic_privacy: 'Speech is turned into text on this computer only, and the recording is deleted afterwards.',
  dv_mic_asking: 'The browser may ask whether this page can use the microphone. Choose "Allow".',
  dv_mic_denied: 'The microphone cannot be used. Allow this page to use it in the browser, or type instead.',
  dv_mic_no_browser: 'This browser cannot record sound. Type instead.',
  dv_mic_unsupported: 'This computer cannot turn speech into text on its own. Type instead.',
  dv_mic_not_installed: 'Speaking needs the on-device transcriber, which is not installed yet.', dv_mic_install_go: 'Install it',
  dv_mic_failed: 'Could not write that down: ', dv_mic_too_short: 'No sound was recorded. Please try again.',
  dv_sent_search: '"Find ideas" sends two things to the AI service chosen in Settings. Step 1 searches the web and carries only: this line, the niches from setup, the titles of up to 20 non-sponsored library items and the keywords already tracked. Step 2 suggests ideas without web search and carries: the creator profile, the persona summary and beat playbooks from the voice profile, for up to 60 library items the title, numbers and first 400 characters, the summary of the comments pasted for an item (whether it was counted by word lists or read by an AI, the summary, up to 4 questions in the words of the viewers), the ranking, medians and save rates worked out from the numbers, up to 12 tracked keywords, up to 20 planned videos that are not posted yet, and this line.',
  dv_sent_plain: '"Find ideas" sends this to the AI service chosen in Settings, without web search: this line, the creator profile, the persona summary and beat playbooks from the voice profile, for up to 60 library items the title, numbers and first 400 characters, the summary of the comments pasted for an item (whether it was counted by word lists or read by an AI, the summary, up to 4 questions in the words of the viewers), the ranking, medians and save rates worked out from the numbers, up to 12 tracked keywords, and up to 20 planned videos that are not posted yet.',
  dv_sent_local: 'Sent only to the local model on this computer; nothing leaves it. A local model cannot search the web, so the ideas use only the keywords already tracked.',
  dv_sent_manual: 'Copy-and-paste mode is on: "Find ideas" takes two steps, and each shows the prompt before anything is sent. Step 1 asks a web-searching AI for current keywords (it can be skipped); step 2 suggests ideas.',
  dv_byhand: 'Look up keywords by copy and paste', dv_found_waiting: '{n} keywords were found and will be used the next time you click "Find ideas".',
  dv_found_waiting_one: '1 keyword was found and will be used the next time you click "Find ideas".',
  dv_stage_queued: 'Starting…', dv_stage_starting: 'Starting…',
  dv_stage_looking_up: 'Looking up current keywords on the web; ideas come next…', dv_stage_thinking: 'Working out ideas from {@names} own numbers…',
  dv_running_hint: 'This can take a minute or two. You can open another page and come back.', dv_elapsed: '{s} s so far',
  dv_patterns_title: 'Patterns in {@names} numbers',
  dv_patterns_hint: 'An AI drew these from {@names} own videos. The videos each one rests on are named under it; one with none named is a claim it gave no source for.',
  dv_planned: 'In the plan',
  dv_for: 'Asked for: "{q}"', dv_ideas_title: 'Ideas', dv_earlier_title: 'Earlier ideas', dv_found_title: 'Keywords found this time',
  dv_lookup_done: 'The web lookup found {n} keywords', dv_lookup_done_one: 'The web lookup found 1 keyword',
  dv_lookup_gone: 'None of the keywords this lookup found is tracked any more.',
  dv_lookup_unverified: 'An AI found these and nobody has checked them: open "source" before relying on one.',
  dv_lookup_failed: 'The web lookup did not work this time: {why} The ideas used only the keywords already tracked.',
  dv_lookup_no_web: 'The current AI service cannot search the web, so no keywords were looked up; the ideas used only the keywords already tracked.',
  dv_lookup_not_asked: 'No web lookup was made this time; the ideas used only the keywords already tracked.',
  dv_pages: 'Pages cited by the lookup ({n})',
  dv_ideas_failed: 'No ideas this time: {why}', dv_ideas_no_samples: 'The library has none of {@names} material yet, so there is nothing to base ideas on.',
  dv_kept_earlier: 'The earlier ideas are below.', dv_job_failed: 'This run did not finish: ',
  dv_empty: 'Type what the video should be about and click "Find ideas". Leaving it empty works too: the ideas then follow {@names} own numbers.',
  dv_step1_title: 'Step 1 of 2: ask a web-searching AI for current keywords', dv_step1_skip: 'Skip, go straight to ideas', dv_step2_title: 'Step 2 of 2: suggest ideas',
  mb_skip: 'Skip',
  mb_intro_ideas: 'The prompt contains: the creator profile from setup (name, account name, niches, self-description, on-camera partner, refused phrases), the persona summary and beat playbooks from the voice profile, for up to 60 library items the title, the sponsored mark, the numbers and the first 400 characters (a video transcript not confirmed yet goes without its text), the summary of the comments pasted for an item (whether it was counted by word lists or read by an AI, how many comments, the summary, up to 4 questions in the words of the viewers), the ranking, medians and save rates worked out from the numbers, up to 12 tracked keywords, up to 20 planned videos that are not posted yet (title, state, day, keywords, the questions of viewers each answers), and the line typed here. All of it is sent to the AI with the prompt.',
  err_bad_focus: 'That line could not be read. Please type it again.', err_upload_not_found: 'The recording did not arrive. Please say it again.',
  err_too_large: 'That is too long. A sentence or two is enough.',
  dv_auto_wait_title: 'Autopilot refreshed {n} candidate topic idea(s)',
  dv_auto_wait_time: 'Refreshed: {t}',
  dv_auto_wait_trends: 'From top trends: ',
  dv_auto_wait_note: '{@Names} currently saved ideas were kept untouched. Choose "Use these ideas" to replace them, or "Dismiss" to leave them as they are.',
  dv_auto_take: 'Use these ideas', dv_auto_dismiss: 'Dismiss',
  dv_auto_taken: 'Replaced saved ideas with this refreshed set', dv_auto_dismissed: 'Dismissed candidate idea set',
  dv_auto_in_use: 'Showing topic ideas refreshed by Autopilot at {t}',
});

const DV_MAX_FOCUS = 500;         // must match MAX_FOCUS_CHARS in studio/pipeline.py
const DV_MAX_RECORD_SEC = 60;     // a spoken request, not a recording session
/* focus: the box, kept across pages. lookup: the "look up first" tick. found: keywords a copy-and-paste lookup
   brought in that the next run should use (null: none). autostart: the home page asked for a run. */
const discoverUi = { focus: '', lookup: true, found: null, autostart: false, timer: 0 };
/* state: idle | asking | listening | writing. note: a function that gives the line under the buttons as escaped HTML
   (called when painting, so it follows the interface language), or null for the line at rest.
   asked: names the microphone request being waited for. */
const dvMic = { state: 'idle', recorder: null, note: null, asked: null };

/* Why a step did not work out, in the interface language: the app's own wording for a reason it knows,
   else the message that came with it. */
function dvWhy(x) {
  const key = 'ai_' + x.reason;
  const own = (lang === 'zh' && x.message_zh) || x.message || '';
  if (I18N.zh[key] === undefined) return own || t('ai_other');
  return t(key) + (x.reason === 'http' || x.reason === 'other' ? own : '');
}

/* Which sentence under the box is true with the current settings. */
function dvSentKey() {
  const st = appState.settings;
  if (st.manual) return 'dv_sent_manual';
  if (st.provider === 'local') return 'dv_sent_local';
  return appState.lookup_available && discoverUi.lookup && !discoverUi.found ? 'dv_sent_search' : 'dv_sent_plain';
}

/* ---------- a spoken request ---------- */
function dvMicPaint() {
  const $btn = document.getElementById('dv-mic');
  const $note = document.getElementById('dv-mic-note');
  if (!$btn || !$note) return;
  $btn.textContent = t(dvMic.state === 'listening' ? 'dv_mic_stop' : 'dv_mic');
  $btn.classList.toggle('on', dvMic.state === 'listening');
  $btn.disabled = dvMic.state === 'writing';
  $note.innerHTML = dvMic.note ? dvMic.note() : esc(t('dv_mic_privacy'));
}
function dvMicSay(note, state) { dvMic.note = note; if (state) dvMic.state = state; dvMicPaint(); }
/* What went wrong with a spoken request, in the interface language. */
const dvMicWhy = (e) => (e && e.key ? t(e.key) : failText(e)) || t('err_default');

/* An AudioBuffer as a mono 16-bit WAV file at the buffer's own rate. The local transcriber resamples it. */
function dvWav(audio) {
  const n = audio.length;
  const mono = new Float32Array(n);
  for (let c = 0; c < audio.numberOfChannels; c++) {
    const data = audio.getChannelData(c);
    for (let i = 0; i < n; i++) mono[i] += data[i] / audio.numberOfChannels;
  }
  const view = new DataView(new ArrayBuffer(44 + n * 2));
  const ascii = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
  ascii(0, 'RIFF'); view.setUint32(4, 36 + n * 2, true); ascii(8, 'WAVE'); ascii(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, audio.sampleRate, true); view.setUint32(28, audio.sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data'); view.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, mono[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: 'audio/wav' });
}

/* Sends a recording to the local server and waits for the text. Throws an error that failText can show. */
async function dvDictate(wav) {
  const res = await fetch('/api/uploads?name=prompt.wav', {
    method: 'PUT',
    headers: studioHeaders({ 'Content-Type': 'application/octet-stream' }),
    body: wav,
  });
  const up = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(up.message || up.error || 'upload'), { message_zh: up.message_zh });
  let { job } = await api('POST', '/api/dictation', { upload_id: up.upload_id });
  while (job.status === 'running') {
    const key = job.stage === 'queued' ? 'dv_mic_waiting' : 'dv_mic_writing';
    dvMicSay(() => esc(t(key)));
    await new Promise((resolve) => setTimeout(resolve, 700));
    job = (await api('GET', '/api/jobs/' + job.id)).job;
  }
  if (job.status !== 'done') throw Object.assign(new Error((job.error && job.error.message) || 'failed'), { message_zh: job.error && job.error.message_zh });
  return String((job.result && job.result.text) || '');
}

function dvInsertSpoken(text) {
  const before = discoverUi.focus.trim();
  discoverUi.focus = ((before ? before + ' ' : '') + text).slice(0, DV_MAX_FOCUS);
  const $focus = document.getElementById('dv-focus');
  if ($focus) { $focus.value = discoverUi.focus; autoGrow($focus); $focus.focus(); }
}

async function dvMicToggle() {
  if (dvMic.state === 'listening') { dvMic.recorder.stop(); return; }
  /* A browser can leave its question about the microphone unanswered for ever. A second click gives up waiting. */
  if (dvMic.state === 'asking') { dvMic.asked = null; return dvMicSay(null, 'idle'); }
  if (dvMic.state !== 'idle') return;
  if (!navigator.mediaDevices || !window.MediaRecorder || !(window.AudioContext || window.webkitAudioContext)) {
    return dvMicSay(() => esc(t('dv_mic_no_browser')));
  }
  let tr;
  try { tr = await api('GET', '/api/transcriber'); } catch (e) { return dvMicSay(() => esc(t('dv_mic_failed') + dvMicWhy(e))); }
  if (!tr.supported) {
    return dvMicSay(() => esc(t('dv_mic_unsupported') + ' ' + ((lang === 'zh' ? tr.reason_zh : tr.reason_en) || tr.reason || '')));
  }
  if (!(tr.installed && tr.model_ready)) {
    return dvMicSay(() => `${esc(t('dv_mic_not_installed'))} <a href="#/start" id="dv-mic-install">${esc(t('dv_mic_install_go'))}</a>`);
  }
  let stream;
  const asked = {};
  dvMic.asked = asked;
  dvMicSay(() => esc(t('dv_mic_asking')), 'asking');               // the browser's own question can stay open for a while
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { return dvMic.asked === asked ? dvMicSay(() => esc(t('dv_mic_denied')), 'idle') : undefined; }
  if (dvMic.asked !== asked) { stream.getTracks().forEach((track) => track.stop()); return undefined; }   // given up on meanwhile
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  const started = Date.now();
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  const tick = setInterval(() => {
    const secs = Math.floor((Date.now() - started) / 1000);
    const gone = !document.getElementById('dv-mic');           // the page was left: stop listening, keep what was said
    if ((gone || secs >= DV_MAX_RECORD_SEC) && recorder.state === 'recording') recorder.stop();
    else dvMicSay(() => esc(tf('dv_mic_listening', { s: secs, max: DV_MAX_RECORD_SEC })));
  }, 250);
  recorder.onstop = async () => {
    clearInterval(tick);
    stream.getTracks().forEach((track) => track.stop());       // the browser's recording mark goes off
    dvMic.recorder = null;
    dvMicSay(() => esc(t('dv_mic_writing')), 'writing');
    try {
      const bytes = await new Blob(chunks).arrayBuffer();
      if (!bytes.byteLength) throw Object.assign(new Error('nothing recorded'), { key: 'dv_mic_too_short' });
      const context = new (window.AudioContext || window.webkitAudioContext)();
      let audio;
      try {
        audio = await new Promise((resolve, reject) => {
          /* Older browsers only call back; newer ones also return a promise, which fails with the same error. */
          const also = context.decodeAudioData(bytes, resolve, reject);
          if (also && also.catch) also.catch(() => {});
        });
      } catch (e) { throw Object.assign(new Error('recording could not be read'), { key: 'dv_mic_too_short' }); }
      finally { context.close(); }
      dvInsertSpoken(await dvDictate(dvWav(audio)));
      dvMicSay(() => esc(t('dv_mic_done')), 'idle');
    } catch (e) {
      dvMicSay(() => esc(t('dv_mic_failed') + dvMicWhy(e)), 'idle');
    }
  };
  dvMic.recorder = recorder;
  recorder.start();
  dvMicSay(() => esc(tf('dv_mic_listening', { s: 0, max: DV_MAX_RECORD_SEC })), 'listening');
}

/* ---------- the box: on the ideas page, and a shorter one on the home page ---------- */
function dvPaintBox(el, home) {
  if (!el) return;
  const st = appState.settings;
  const canLook = !!appState.lookup_available;
  const byHand = !home && st.configured && !st.manual && !canLook
    ? `<button type="button" class="btn" data-dv-byhand>${esc(t('dv_byhand'))}</button>` : '';
  const n = discoverUi.found ? discoverUi.found.length : 0;
  el.innerHTML = `<div class="card dv-box">
    <label class="field" style="margin-top:0" for="dv-focus">${esc(t(home ? 'dv_home_title' : 'dv_label'))}</label>
    <textarea id="dv-focus" rows="2" maxlength="${DV_MAX_FOCUS}" placeholder="${esc(t('dv_ph'))}">${esc(discoverUi.focus)}</textarea>
    <div class="row">
      <button type="button" class="btn primary big" id="dv-go" ${st.configured ? '' : 'disabled'}>${esc(t('dv_go'))}</button>
      <button type="button" class="btn dv-mic" id="dv-mic"></button>
      ${canLook ? `<label class="check"><input type="checkbox" id="dv-lookup" ${discoverUi.lookup ? 'checked' : ''}> ${esc(t('dv_lookup_check'))}</label>` : ''}
      ${byHand}
    </div>
    <p class="hint" id="dv-mic-note" role="status" aria-live="polite"></p>
    ${n ? `<p class="hint">${esc(tf(countKey('dv_found_waiting', n), { n }))}</p>` : ''}
    ${st.configured ? `<p class="hint" id="dv-sent">${esc(t(dvSentKey()))}</p>`
      : `<p class="hint">${esc(t('no_ai'))} <a href="#/settings">${esc(t('go_settings'))}</a></p>`}
  </div>`;
  const $focus = document.getElementById('dv-focus');
  autoGrow($focus);
  $focus.oninput = () => { discoverUi.focus = $focus.value; autoGrow($focus); };
  const go = () => {
    if (!st.configured) return;
    if (home) { discoverUi.autostart = true; location.hash = '#/ideas'; } else dvGo();
  };
  $focus.onkeydown = (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return;   // Enter also confirms Chinese input
    e.preventDefault(); go();
  };
  document.getElementById('dv-go').onclick = go;
  document.getElementById('dv-mic').onclick = dvMicToggle;
  document.getElementById('dv-mic-note').onclick = (e) => {
    if (e.target.id === 'dv-mic-install') setupUi.open = 'material';      // the transcriber is installed in that step
  };
  const $lookup = document.getElementById('dv-lookup');
  if ($lookup) {
    $lookup.onchange = () => {
      discoverUi.lookup = $lookup.checked;
      const $sent = document.getElementById('dv-sent');
      if ($sent) $sent.textContent = t(dvSentKey());
    };
  }
  dvMicPaint();
}

/* ---------- running it ---------- */
function dvStopFollowing() { clearInterval(discoverUi.timer); discoverUi.timer = 0; }

function dvProgressText(job) {
  const key = 'dv_stage_' + job.stage;
  const secs = Math.max(0, Math.round(Date.now() / 1000 - job.started_at));
  return t(I18N.zh[key] !== undefined ? key : 'dv_stage_starting') + ' ' + tf('dv_elapsed', { s: secs });
}

/* Keeps the progress line current while a run is under way, and shows the outcome when it ends. */
function dvFollow(job) {
  dvStopFollowing();
  let latest = job;
  const paint = () => { const el = document.getElementById('dv-progress-text'); if (el) el.textContent = dvProgressText(latest); };
  const timer = setInterval(async () => {
    if (location.hash !== '#/ideas' || discoverUi.timer !== timer) return clearInterval(timer);
    try {
      latest = (await api('GET', '/api/jobs/' + job.id)).job;
      if (latest.status === 'running') return paint();
    } catch (e) {
      if (e.code !== 'not_found') return undefined;     // a hiccup: try again on the next tick
    }
    clearInterval(timer);
    if (discoverUi.timer === timer) discoverUi.timer = 0;
    return router();        // the outcome, and the app state with it (new keywords may be tracked now)
  }, 1500);
  discoverUi.timer = timer;
}

function dvAddedToast(out) {
  toast(tf('tr_added', { n: out.added })
    + (out.dropped_no_source ? tf('tr_dropped_source', { n: out.dropped_no_source }) : '')
    + (out.dropped_too_old ? tf('tr_dropped_old', { n: out.dropped_too_old }) : ''));
}

/* A lookup by copy and paste with a web-searching AI. What it finds is used by the next run. */
async function dvLookupByHand() {
  try {
    const out = await manualBridge('trends', { focus: discoverUi.focus.trim() }, { intro: t('tr_ai_hint') + ' ' + t('mb_intro_trends') });
    if (!out) return;
    discoverUi.found = out.found || [];
    appState.trend_count = out.trends.length;
    dvAddedToast(out);
    await renderIdeas();
  } catch (e) { showError(e); }
}

/* Copy-and-paste mode: the same two steps, by hand. */
async function dvGoByHand(focus) {
  let found = discoverUi.found;
  if (!found) {
    const looked = await manualBridge('trends', { focus }, {
      title: t('dv_step1_title'), intro: t('tr_ai_hint') + ' ' + t('mb_intro_trends'), skip: t('dv_step1_skip') });
    if (looked === undefined) return;                       // cancelled
    if (!looked.skipped) {
      found = looked.found || [];
      discoverUi.found = found;                             // kept if the next step is cancelled, and used next time
      appState.trend_count = looked.trends.length;
      dvAddedToast(looked);
    }
  }
  const payload = found ? { focus, found } : { focus };
  let out;
  try { out = await manualBridge('ideas', payload, { title: t('dv_step2_title'), intro: t('mb_intro_ideas') }); }
  finally { if (!out) await renderIdeas(); }                // cancelled or refused: show what step 1 brought in
  if (!out) return;
  discoverUi.found = null;
  await renderIdeas();
}

async function dvGo() {
  const $go = document.getElementById('dv-go');
  if ($go) $go.disabled = true;
  const focus = discoverUi.focus.trim();
  try {
    if (appState.settings.manual) { await dvGoByHand(focus); return; }
    const body = { focus, lang };
    if (discoverUi.found) body.found = discoverUi.found;
    else if (appState.lookup_available) body.lookup = discoverUi.lookup;     // otherwise the server records why none was made
    await api('POST', '/api/discover', body);
    discoverUi.found = null;
    await renderIdeas();                                    // finds the run under way and follows it
  } catch (e) { showError(e); }
  finally { const $now = document.getElementById('dv-go'); if ($now && !discoverUi.timer) $now.disabled = !appState.settings.configured; }
}

/* ---------- what came back ---------- */
/* One keyword the lookup found, as it is tracked now: `x` is its entry from /api/trends. */
function dvKeywordHtml(x) {
  const tags = [];
  if (x.source === 'ai') {
    const link = /^https?:\/\//i.test(x.source_url)
      ? ` <a href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">${esc(t('tr_src_link'))}</a>` : '';
    tags.push(`<span class="tag warn">${esc(t('tr_src_ai'))}${link}</span>`);
  }
  if (x.flags.includes('voice')) tags.push(`<span class="tag no">${esc(t('tr_flag_voice'))}</span>`);
  if (x.flags.includes('compliance')) tags.push(`<span class="tag no">${esc(t('tr_flag_compliance'))}</span>`);
  let mine = t('tr_mine_none');
  if (x.lift_raw !== null) mine = tf(countKey('tr_mine_lift', x.her_videos_with_numbers), { n: x.her_videos_with_numbers, x: x.lift_raw });
  else if (x.her_videos) mine = tf(countKey('tr_mine_no_numbers', x.her_videos), { n: x.her_videos });
  const note = lang === 'zh' ? x.note_zh : (x.note_en || x.note_zh);
  return `<div class="card trend ${x.usable ? '' : 'off'}">
    <div class="row">
      <div class="score" title="${esc(t('tr_score'))}">${esc(x.score)}</div>
      <div class="name"><h3>${esc(x.keyword)}</h3>${tags.length ? `<div>${tags.join('')}</div>` : ''}</div>
      ${x.usable ? `<button type="button" class="btn" data-dv-use="${esc(x.keyword)}">${esc(t('tr_use'))}</button>` : ''}
    </div>
    ${note ? `<p class="meta">${esc(note)}</p>` : ''}
    <div class="facts"><span>${esc(mine)}</span></div>
  </div>`;
}

/* What the web lookup before a set of ideas did. `byKey` maps kwKey(keyword) to its /api/trends entry. */
function dvLookupHtml(lookup, byKey) {
  if (!lookup) return '';
  if (lookup.status === 'failed') {
    const byHand = appState.settings.manual ? '' : `<div class="row" style="margin-top:8px">
      <button type="button" class="btn" data-dv-byhand>${esc(t('dv_byhand'))}</button></div>`;
    return `<div class="banner">${esc(tf('dv_lookup_failed', { why: dvWhy(lookup) }))}${byHand}</div>`;
  }
  if (lookup.status !== 'done') {
    return `<p class="meta">${esc(t(lookup.reason === 'no_web_search' ? 'dv_lookup_no_web' : 'dv_lookup_not_asked'))}</p>`;
  }
  const found = (lookup.keywords || []).map((k) => byKey[kwKey(k)]).filter(Boolean);
  const pages = (lookup.pages || []).filter((p) => p && /^https?:\/\//i.test(p.url));
  const dropped = (lookup.dropped_no_source ? tf('tr_dropped_source', { n: lookup.dropped_no_source }) : '')
    + (lookup.dropped_too_old ? tf('tr_dropped_old', { n: lookup.dropped_too_old }) : '');
  const summary = found.length
    ? tf(countKey('dv_lookup_done', found.length), { n: found.length }) + dropped + (lang === 'zh' ? '。' : '. ') + t('dv_lookup_unverified')
    : t('dv_lookup_gone');
  /* Shown open: OpenAI asks that pages a web search cites are shown as prominent, clickable links. */
  const cited = pages.length ? `<details class="dv-pages" open><summary>${esc(tf('dv_pages', { n: pages.length }))}</summary><ul>${
    pages.map((p) => `<li><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title || p.url)}</a></li>`).join('')}</ul></details>` : '';
  return `<h2 class="dv-h">${esc(t('dv_found_title'))}</h2><p class="meta">${esc(summary)}</p>${found.map(dvKeywordHtml).join('')}${cited}`;
}

/* The latest run when it did not end in saved ideas. `latest` is its result, or { crashed: error }. */
function dvLatestHtml(latest, byKey, hasEarlier) {
  if (!latest) return '';
  if (latest.crashed) return `<div class="banner">${esc(t('dv_job_failed') + (failText(latest.crashed) || t('err_default')))}</div>`;
  const asked = latest.focus ? esc(tf('dv_for', { q: latest.focus })) + ' ' : '';
  const why = latest.ideas.reason === 'no_samples'
    ? `${esc(t('dv_ideas_no_samples'))} <a href="#/library">${esc(t('go_library'))}</a>`
    : esc(tf('dv_ideas_failed', { why: dvWhy(latest.ideas) }));
  return `<div class="banner">${asked}${why}${hasEarlier ? ' ' + esc(t('dv_kept_earlier')) : ''}</div>${dvLookupHtml(latest.lookup, byKey)}`;
}

async function renderIdeas() {
  dvStopFollowing();
  const data = await api('GET', '/api/recommendations');
  const { performance, result } = data;
  const job = data.discover || null;
  const running = !!(job && job.status === 'running');
  const ideas = result && Array.isArray(result.ideas) ? result.ideas : [];
  const savedAt = result && typeof result.generated_at === 'number' ? result.generated_at : 0;

  /* The latest run, when it ended without saving ideas: its lookup and the reason are shown above any earlier ideas. */
  let latest = null;
  if (job && !running && (job.finished_at || 0) > savedAt) {
    if (job.status === 'failed') latest = { crashed: job.error || {} };
    else if (job.result && job.result.ideas && job.result.ideas.status !== 'done') latest = job.result;
  }
  /* Keywords a copy-and-paste lookup brought in and no run has used yet are shown the same way. */
  const waiting = !running && discoverUi.found && discoverUi.found.length
    ? { status: 'done', keywords: discoverUi.found, pages: [] } : null;
  const shown = [latest && latest.lookup, waiting, result && result.lookup]
    .filter((x) => x && x.status === 'done' && (x.keywords || []).length);
  const byKey = {};
  let listNote = '';      // wordlist.js: said once on this page when a keyword is shown as the word list marks it
  if (shown.length) {
    try {
      const tracked = await api('GET', '/api/trends');
      tracked.trends.forEach((x) => { byKey[kwKey(x.keyword)] = x; });
      if (shown.some((x) => x.keywords.some((k) => byKey[kwKey(k)]))) listNote = wlNoteHtml(tracked.wordlist);
    }
    catch (e) { /* the keywords are optional here; the ideas still show */ }
  }

  const few = appState.sample_count > 0 && performance.with_metrics < 8
    ? `<p class="hint">${esc(tf('ideas_few_metrics', { n: performance.with_metrics }))}</p>` : '';
  const top = performance.rows.slice(0, 5);
  const table = top.length ? `<div class="card"><h3>${esc(t('top_title'))}</h3><table class="perf">
      <tr><th></th><th>${esc(t('m_views'))}</th><th>${esc(t('interactions'))}</th><th>${esc(t('rate'))}</th></tr>
      ${top.map((r) => `<tr><td>${esc(r.title)}${r.is_sponsored ? ` <span class="tag">${esc(t('sponsored'))}</span>` : ''}</td>
        <td>${num(r.views)}</td><td>${num(r.interactions)}</td>
        <td>${r.interaction_rate_pct === null ? '–' : r.interaction_rate_pct + '%'}</td></tr>`).join('')}</table>${few}</div>` : '';
  const progress = running ? `<div class="card dv-progress" role="status" aria-live="polite"><i class="dv-spin"></i>
      <div><div id="dv-progress-text">${esc(dvProgressText(job))}</div>
        <div class="hint">${job.label ? esc(tf('dv_for', { q: job.label })) + ' ' : ''}${esc(t('dv_running_hint'))}</div></div></div>` : '';
  const note = result ? (lang === 'zh' ? result.data_note_zh : (result.data_note_en || result.data_note_zh)) : '';
  /* A lookup that failed or was left out is said above the ideas; the keywords of one that worked follow them. */
  const looked = result ? dvLookupHtml(result.lookup, byKey) : '';
  const lookedFirst = !!(result && result.lookup && result.lookup.status !== 'done');
  /* What the model says it sees in the numbers (pipeline._checked_patterns): each with the creator's videos it cited. */
  const seen = (result && Array.isArray(result.patterns) ? result.patterns : []).filter((p) => p && p.pattern_zh);
  const patterns = seen.length
    ? `<div class="card" id="dv-patterns"><h3>${esc(t('dv_patterns_title'))}</h3><p class="hint">${esc(t('dv_patterns_hint'))}</p>` +
      seen.map((p) => `<p>${esc(lang === 'zh' ? p.pattern_zh : (p.pattern_en || p.pattern_zh))}</p>` +
        ((p.evidence_titles || []).length ? `<div class="meta">${esc(t('evidence'))}：${
          p.evidence_titles.map((x) => `<span class="tag">${esc(x)}</span>`).join('')}</div>` : '')).join('') +
      `</div>` : '';
  const autoState = (data.automatic && typeof data.automatic === 'object') ? data.automatic : {};
  const autoWait = autoState.waiting && autoState.waiting.result ? autoState.waiting : null;
  const autoInUse = autoState.in_use || null;
  const waitIdeas = autoWait && Array.isArray(autoWait.result.ideas) ? autoWait.result.ideas : [];
  const waitTrends = autoWait && Array.isArray(autoWait.trends) ? autoWait.trends : [];
  const autoBanner = autoWait ? `<div class="banner" id="dv-auto-waiting">
    <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <div class="spacer">
        <strong>${esc(tf('dv_auto_wait_title', { n: waitIdeas.length }))}</strong>
        ${autoWait.made_at ? `<span class="meta"> · ${esc(tf('dv_auto_wait_time', { t: fmtDate(autoWait.made_at) }))}</span>` : ''}
      </div>
      <button type="button" class="btn primary" id="dv-auto-take" data-auto-take="${esc(autoWait.set_id)}">${esc(t('dv_auto_take'))}</button>
      <button type="button" class="btn" id="dv-auto-dismiss" data-auto-dismiss="${esc(autoWait.set_id)}">${esc(t('dv_auto_dismiss'))}</button>
    </div>
    ${waitTrends.length ? `<div class="meta" style="margin-top:6px">${esc(t('dv_auto_wait_trends'))}${waitTrends.map((k) => `<span class="tag">${esc(k)}</span>`).join('')}</div>` : ''}
    ${waitIdeas.length ? `<div class="meta" style="margin-top:6px">${waitIdeas.slice(0, 4).map((x) => `「${esc(lang === 'zh' ? x.title_zh : (x.title_en || x.title_zh))}」`).join(' ')}</div>` : ''}
    <div class="hint" style="margin-top:6px">${esc(t('dv_auto_wait_note'))}</div>
  </div>` : '';
  const inUseNote = autoInUse && autoInUse.made_at
    ? `<p class="meta" id="dv-auto-in-use">${esc(tf('dv_auto_in_use', { t: fmtDate(autoInUse.made_at) }))}${(autoInUse.trends || []).length ? ' · ' + (autoInUse.trends || []).map((k) => `<span class="tag">${esc(k)}</span>`).join('') : ''}</p>`
    : '';
  const saved = result ? `<h2 class="dv-h">${esc(t(latest ? 'dv_earlier_title' : 'dv_ideas_title'))}</h2>
      <p class="meta">${result.focus ? esc(tf('dv_for', { q: result.focus })) + ' · ' : ''}${savedAt ? esc(t('generated_at')) + ' ' + esc(fmtDate(savedAt)) : ''}</p>
      ${inUseNote}
      ${lookedFirst ? looked : ''}
      ${note ? `<p class="meta">${esc(note)}</p>` : ''}
      ${patterns}
      <div id="ideas">${ideas.map(ideaHtml).join('')}</div>
      ${lookedFirst ? '' : looked}` : '';
  const empty = !result && !autoWait && !latest && !waiting && !running && appState.settings.configured
    ? `<div class="empty">${esc(t('dv_empty'))}</div>` : '';
  $view.innerHTML = `<div class="wrap"><h1>${esc(t('ideas_title'))}</h1><p class="sub">${esc(t('ideas_sub'))}</p>
    <div id="dv-box"></div>
    ${progress}
    ${autoBanner}
    ${dvLatestHtml(latest, byKey, !!result)}
    ${waiting ? dvLookupHtml(waiting, byKey) : ''}
    ${saved}${empty}
    ${listNote}
    ${table || few}</div>`;
  dvPaintBox(document.getElementById('dv-box'), false);

  $view.querySelectorAll('[data-dv-byhand]').forEach((b) => { b.onclick = dvLookupByHand; });
  const $autoTake = document.getElementById('dv-auto-take');
  if ($autoTake) {
    $autoTake.onclick = async () => {
      $autoTake.disabled = true;
      try {
        await api('POST', '/api/recommendations/automatic/' + $autoTake.dataset.autoTake + '/take', {});
        toast(t('dv_auto_taken'));
        await renderIdeas();
      } catch (err) { $autoTake.disabled = false; showError(err); }
    };
  }
  const $autoDismiss = document.getElementById('dv-auto-dismiss');
  if ($autoDismiss) {
    $autoDismiss.onclick = async () => {
      $autoDismiss.disabled = true;
      try {
        await api('POST', '/api/recommendations/automatic/' + $autoDismiss.dataset.autoDismiss + '/dismiss', {});
        toast(t('dv_auto_dismissed'));
        await renderIdeas();
      } catch (err) { $autoDismiss.disabled = false; showError(err); }
    };
  }
  $view.querySelectorAll('[data-dv-use]').forEach((b) => {
    b.onclick = () => {
      const keyword = b.dataset.dvUse;
      if (!draft.keywords.some((k) => kwKey(k) === kwKey(keyword)) && draft.keywords.length < MAX_KEYWORDS) draft.keywords.push(keyword);
      if (!draft.notes.trim() && discoverUi.focus.trim()) draft.notes = discoverUi.focus.trim();
      location.hash = '#/new';
    };
  });
  const $ideas = document.getElementById('ideas');
  if ($ideas) {
    $ideas.onclick = async (e) => {
      const planBtn = e.target.closest('[data-plan-idea]');
      if (planBtn) {
        const idea = ideas[Number(planBtn.dataset.planIdea)];
        if (!idea) return;
        planBtn.disabled = true;
        try {
          await api('POST', '/api/plans', plFromIdea(idea));      // planner.js: an idea as a plan
          planBtn.textContent = t('dv_planned');                   // it stays off: the same idea is not planned twice from here
          toast(t('pl_added_toast'));
          appState.plan_count = (appState.plan_count || 0) + 1;
          renderNav('ideas');
        } catch (err) { planBtn.disabled = false; showError(err); }
        return;
      }
      const btn = e.target.closest('[data-idea]');
      if (!btn) return;
      const idea = ideas[Number(btn.dataset.idea)];
      draft.notes = idea.title_zh + '\n' + idea.script_notes_zh;
      draft.target_sec = idea.target_duration_sec;
      draft.video_format = idea.video_format;
      draft.keywords = (idea.trend_keywords || []).slice(0, MAX_KEYWORDS);
      location.hash = '#/new';
    };
  }
  if (running) {
    const $go = document.getElementById('dv-go');
    if ($go) $go.disabled = true;
    dvFollow(job);
  } else if (discoverUi.autostart) {
    discoverUi.autostart = false;
    dvGo();
  }
}
