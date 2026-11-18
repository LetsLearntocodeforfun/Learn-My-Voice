'use strict';
/* The teleprompter: one breath at a time, paced by the speaking rate in the voice profile.
   Loaded after discover.js and before pages.js (which boots the app); the editor in app.js calls openPrompter
   when its button is clicked.

   Nothing here listens. There is no microphone and no speech recognition: the pace is arithmetic on a rate the
   app already has (measured from the creator's own recordings, or typed in), and nothing is sent anywhere.

   The file has two parts. The first, between the two "tp-core" marks, is the prompter itself and uses nothing
   from the rest of the app. studio/exports.py copies that part as it stands into the single file made for a
   phone, so the phone paces exactly as this page does. The second part connects the prompter to the editor. */

/* tp-core:start
   Everything from here to the end mark also runs in the phone file, alone. So it must not use anything the
   other scripts define, and it must not contain two slashes in a row (write comments like this one): the phone
   file is checked to hold no address at all, and two slashes are how that check knows one. */
const TP_TEXT = {
  zh: {
    tp_start: '开始', tp_pause: '暂停', tp_key_space: '空格',
    tp_prev: '↑ 上一口气', tp_prev_name: '回到上一口气', tp_next: '↓ 下一口气', tp_next_name: '跳到下一口气',
    tp_restart: '重来', tp_restart_name: '从头再来，先倒数 3 秒',
    tp_slower: '慢一点', tp_slower_name: '语速慢一点', tp_faster: '快一点', tp_faster_name: '语速快一点',
    tp_smaller: '字小一点', tp_bigger: '字大一点',
    tp_mirror: '镜像', tp_mirror_name: '左右镜像，配合提词器的分光镜使用',
    tp_close: '关闭', tp_empty: '脚本还是空的。', tp_end: '— 完 —',
    tp_src_mine: '按你的语速：每分钟 {n} 字 × {x}',
    tp_src_default: '默认语速（还不知道你自己的语速）：每分钟 {n} 字 × {x}',
    tp_src_example: '示例声音档案的语速，不是你自己的：每分钟 {n} 字 × {x}',
    tp_src_plain: '语速：每分钟 {n} 字 × {x}',
  },
  en: {
    tp_start: 'Start', tp_pause: 'Pause', tp_key_space: 'Space',
    tp_prev: '↑ Back', tp_prev_name: 'Previous breath', tp_next: '↓ Next', tp_next_name: 'Next breath',
    tp_restart: 'Restart', tp_restart_name: 'Start again from the top, after a 3-second countdown',
    tp_slower: 'Slower', tp_slower_name: 'Slower pace', tp_faster: 'Faster', tp_faster_name: 'Faster pace',
    tp_smaller: 'Smaller text', tp_bigger: 'Bigger text',
    tp_mirror: 'Mirror', tp_mirror_name: 'Mirror the text left to right, for a teleprompter glass',
    tp_close: 'Close', tp_empty: 'The script is empty.', tp_end: '— End —',
    tp_src_mine: 'At {@names} rate: {n} units/min × {x}',
    tp_src_default: 'Default rate ({@names} own rate is not known yet): {n} units/min × {x}',
    tp_src_example: 'The example profile\'s rate, not {@names} own: {n} units/min × {x}',
    tp_src_plain: 'Rate: {n} units/min × {x}',
  },
};

/* The pace is a factor on the rate, counted in tenths so that ten steps are exactly 1.0 and nothing drifts. */
const TP_PACE = { least: 6, most: 16, usual: 10 };
const TP_SIZE = { least: 24, most: 120, step: 6, usual: 44 };     /* px */
const TP_COUNTDOWN_SEC = 3;
const TP_READ_AT = 0.4;         /* the reading line, as a share of the height of the text area, from the top */
const TP_EASE_SEC = 0.12;       /* how quickly the text catches up when the place being read moves */
const TP_KEYS = {
  ' ': 'toggle', Spacebar: 'toggle', ArrowUp: 'prev', ArrowLeft: 'prev', ArrowDown: 'next', ArrowRight: 'next',
  '-': 'slower', '[': 'slower', '=': 'faster', '+': 'faster', ']': 'faster', ',': 'smaller', '.': 'bigger',
  m: 'mirror', M: 'mirror', r: 'restart', R: 'restart', Escape: 'close',
};
/* A key held down repeats. That is welcome for these, and would flicker start, mirror and the rest. */
const TP_REPEATING = { prev: true, next: true, slower: true, faster: true, smaller: true, bigger: true };

/* What she chose last time (pace, text size, mirror), kept in this browser only. Reading or writing it can be
   refused, for a file opened from a phone's storage or in private browsing. The prompter then starts from the
   usual values, and what she changes lasts until the page is closed. */
function tpRecall(key, least, most, usual) {
  try {
    const raw = window.localStorage.getItem(key);
    const n = raw === null ? NaN : Number(raw);
    return Number.isInteger(n) && n >= least && n <= most ? n : usual;
  } catch (e) {
    return usual;
  }
}
function tpKeep(key, n) {
  try { window.localStorage.setItem(key, String(n)); } catch (e) { /* not kept: see tpRecall */ }
}

/* Whole seconds as m:ss. */
function tpClock(sec) {
  const s = Math.max(0, sec);
  return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + (s % 60);
}

/* Build the prompter inside `root` and run it. `opts`:
   - breaths: [{ text, units }], in reading order. A breath lasts units / (cpm × pace) × 60 seconds.
   - cpm: spoken units per minute. source: which tp_src_ sentence says where that number comes from.
   - say(key): the text for a key of TP_TEXT, in the language in use, with names filled in.
   - size: the text size to start from when she has not chosen one. onClose: called once it is closed; without
     it there is nothing to close (the phone file is the whole page) and no close button.
   Returns { close(), source(name) }. */
function tpMount(root, opts) {
  const say = opts.say;
  const breaths = opts.breaths;
  /* Seconds each breath takes at pace 1.0, and when each starts. The pace is applied to the clock as it runs and
     not stored in these, so changing it in the middle of a breath keeps her place. */
  const lasts = breaths.map((b) => b.units * 60 / opts.cpm);
  const starts = [0];
  lasts.forEach((sec, i) => starts.push(starts[i] + sec));
  const whole = starts[lasts.length];
  const calm = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };

  let state = 'idle';           /* idle (not started since opening or restart) | counting | running | paused | ended */
  let at = 0;                   /* the breath being read; breaths.length once the last one has ended */
  let into = 0;                 /* seconds into that breath, at pace 1.0 */
  let countdown = 0;
  let tenths = tpRecall('tp-pace', TP_PACE.least, TP_PACE.most, TP_PACE.usual);
  let size = tpRecall('tp-size', TP_SIZE.least, TP_SIZE.most, opts.size || TP_SIZE.usual);
  let mirror = tpRecall('tp-mirror', 0, 1, 0) === 1;
  let source = opts.source;
  let shown = null;             /* where the text is drawn: px from its top edge to the reading line */
  let lineHeight = 0;
  let gap = 0;
  let last = 0;
  let frameId = 0;
  let closed = false;
  let lock = null;
  let lockWanted = false;
  let watcher = null;

  /* Text is always set with textContent, so nothing from a script is ever read as markup. */
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  const buttons = {};
  /* `hint` is the key that does the same, shown small beside the label. `name` is a fuller description for a
     label that is an arrow or a single word; a screen reader says it, and it shows when the pointer rests there. */
  const button = (act, label, hint, shortcut, name) => {
    const b = make('button');
    b.type = 'button';
    b.setAttribute('data-tp', act);
    b.appendChild(make('span', '', say(label)));
    if (hint) b.appendChild(make('span', 'tp-key', hint)).setAttribute('aria-hidden', 'true');
    if (shortcut) b.setAttribute('aria-keyshortcuts', shortcut);
    if (name) { b.setAttribute('aria-label', say(name)); b.title = say(name); }
    buttons[act] = b;
    return b;
  };
  const group = (nodes) => {
    const g = make('div', 'tp-group');
    nodes.forEach((n) => g.appendChild(n));
    return g;
  };

  const bar = make('div', 'tp-bar');
  const paceNow = make('span', 'tp-pace');
  bar.appendChild(button('toggle', 'tp_start', say('tp_key_space'), 'Space')).className = 'tp-go';
  bar.appendChild(group([button('prev', 'tp_prev', '', 'ArrowUp', 'tp_prev_name'), button('next', 'tp_next', '', 'ArrowDown', 'tp_next_name')]));
  bar.appendChild(button('restart', 'tp_restart', 'R', 'R', 'tp_restart_name'));
  bar.appendChild(group([button('slower', 'tp_slower', '-', '-', 'tp_slower_name'), paceNow, button('faster', 'tp_faster', '=', '=', 'tp_faster_name')]));
  bar.appendChild(group([button('smaller', 'tp_smaller', ',', ','), button('bigger', 'tp_bigger', '.', '.')]));
  bar.appendChild(button('mirror', 'tp_mirror', 'M', 'M', 'tp_mirror_name'));
  if (opts.onClose) {
    bar.appendChild(make('span', 'tp-spacer'));
    bar.appendChild(button('close', 'tp_close', 'Esc', 'Escape'));
  }

  const status = make('div', 'tp-status');
  const clock = status.appendChild(make('span', 'tp-time'));
  const from = status.appendChild(make('span', 'tp-from'));
  const progress = make('div', 'tp-progress');
  progress.setAttribute('role', 'progressbar');
  progress.setAttribute('aria-valuemin', '0');
  progress.setAttribute('aria-valuemax', '100');
  const done = progress.appendChild(make('i'));

  const stage = make('div', 'tp-stage');
  const text = make('div', 'tp-text');
  const items = (breaths.length ? breaths.map((b) => b.text) : [say('tp_empty')]).map((words) => text.appendChild(make('p', '', words)));
  const endMark = text.appendChild(make('p', 'tp-end', say('tp_end')));
  endMark.hidden = true;
  stage.appendChild(make('div', 'tp-flip')).appendChild(text);
  stage.appendChild(make('div', 'tp-line'));
  const count = stage.appendChild(make('div', 'tp-count'));
  count.setAttribute('role', 'status');
  count.hidden = true;

  root.textContent = '';
  root.classList.add('tp');
  [bar, status, progress, stage].forEach((n) => root.appendChild(n));

  const pace = () => tenths / 10;
  /* A control at its limit is marked, not switched off: a switched-off button that has the focus swallows the keys. */
  const limit = (b, reached) => { if (reached) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled'); };

  const paintSource = () => {
    setText(from, say('tp_src_' + source).replace('{n}', String(Math.round(opts.cpm))).replace('{x}', pace().toFixed(1)));
  };
  /* Where she is and how long the whole script takes, both at the pace chosen now. */
  const paintClock = () => {
    const ended = state === 'ended';
    const place = ended ? whole : starts[at] + into;
    const total = Math.round(whole / pace());
    setText(clock, tpClock(ended ? total : Math.min(total, Math.floor(place / pace()))) + ' / ' + tpClock(total));
    const share = whole > 0 ? Math.min(1, place / whole) : 0;
    done.style.width = (share * 100).toFixed(2) + '%';
    const percent = String(Math.round(share * 100));
    if (progress.getAttribute('aria-valuenow') !== percent) progress.setAttribute('aria-valuenow', percent);
  };
  const paintPace = () => {
    setText(paceNow, pace().toFixed(1) + '×');
    limit(buttons.slower, tenths <= TP_PACE.least);
    limit(buttons.faster, tenths >= TP_PACE.most);
    paintSource();
    paintClock();
  };
  const paintBreaths = () => {
    const ended = state === 'ended';
    items.forEach((p, i) => {
      const now = !ended && i === at;
      p.classList.toggle('tp-now', now);
      p.classList.toggle('tp-past', ended || i < at);
      if (now) p.setAttribute('aria-current', 'true'); else p.removeAttribute('aria-current');
    });
    endMark.hidden = !ended;
  };
  const paintState = () => {
    setText(buttons.toggle.firstChild, say(state === 'running' ? 'tp_pause' : 'tp_start'));
    /* The style sheet reads this: the marks at the reading line light up while the text is moving. */
    if (root.getAttribute('data-state') !== state) root.setAttribute('data-state', state);
    const n = state === 'counting' ? Math.max(1, Math.ceil(countdown)) : 0;
    setText(count, n ? String(n) : '');
    count.hidden = !n;
  };

  /* Keep the screen lit while she reads: a phone that dims in the middle of a take is worse than no prompter.
     It needs no permission and sends nothing. Where the browser has no such thing, or refuses, nothing happens. */
  const hold = (on) => {
    lockWanted = on;
    if (!on) {
      if (lock) { lock.release().catch(() => {}); lock = null; }
      return;
    }
    if (lock || !navigator.wakeLock) return;
    try {
      navigator.wakeLock.request('screen').then((got) => {
        if (!lockWanted || lock || closed) { got.release().catch(() => {}); return; }
        lock = got;
        got.addEventListener('release', () => { if (lock === got) lock = null; });    /* the browser lets go when the page is hidden */
      }).catch(() => {});
    } catch (e) { /* refused: the screen follows the device's own setting */ }
  };

  const measure = () => {
    const style = window.getComputedStyle(items[0]);
    lineHeight = parseFloat(style.lineHeight);
    if (!(lineHeight > 0)) lineHeight = size * 1.5;
    gap = parseFloat(style.paddingBottom);
    if (!(gap >= 0)) gap = 0;
  };
  /* The point of the text that belongs on the reading line, in px from the top of the text: the middle of the
     line being read. A breath that wraps onto several lines is read down through them over its time. With
     reduced motion the text only moves when the breath changes. */
  const readingPoint = () => {
    const p = state === 'ended' ? endMark : items[Math.min(at, items.length - 1)];
    const through = state !== 'ended' && !calm.matches && lasts[at] > 0 ? Math.min(1, into / lasts[at]) : 0;
    return p.offsetTop + lineHeight / 2 + through * Math.max(0, p.offsetHeight - gap - lineHeight);
  };
  /* Move the text toward where it belongs; true once it is there. It eases over `dt` seconds of real time, so
     the motion looks the same at any frame rate. With reduced motion, or when `jump` is set, it goes there at once. */
  const draw = (dt, jump) => {
    const want = readingPoint();
    if (shown === null || jump || calm.matches) shown = want;
    else {
      shown += (want - shown) * (1 - Math.exp(-dt / TP_EASE_SEC));
      if (Math.abs(want - shown) < 0.5) shown = want;
    }
    text.style.transform = 'translateY(' + (stage.clientHeight * TP_READ_AT - shown).toFixed(1) + 'px)';
    return shown === want;
  };

  /* One frame. Time is taken from the clock and not counted in frames or pixels, so a breath lasts what the
     rate says at any text size, on any screen, and also when a frame is late. */
  const frame = (now) => {
    frameId = 0;
    if (closed) return;
    const dt = Math.max(0, (now - last) / 1000);
    last = now;
    const was = at;
    if (state === 'counting') {
      countdown -= dt;
      if (countdown <= 0) state = 'running';
      paintState();
    } else if (state === 'running') {
      into += dt * pace();
      while (at < lasts.length && into >= lasts[at]) { into -= lasts[at]; at += 1; }
      if (at >= lasts.length) {           /* the last breath has ended: stop there, and do not start over */
        into = 0;
        state = 'ended';
        hold(false);
        paintState();
      }
    }
    if (at !== was) paintBreaths();
    paintClock();
    const there = draw(dt, false);
    if (state === 'running' || state === 'counting' || !there) frameId = window.requestAnimationFrame(frame);
  };
  const wake = () => {
    if (frameId || closed) return;
    last = window.performance.now();
    frameId = window.requestAnimationFrame(frame);
  };
  const refresh = () => {
    hold(state === 'running' || state === 'counting');
    paintState();
    paintBreaths();
    paintClock();
    draw(0, false);
    wake();
  };

  const begin = () => {
    state = 'counting';
    countdown = TP_COUNTDOWN_SEC;
  };
  const toggle = () => {
    if (!breaths.length) return;
    if (state === 'running') state = 'paused';
    else if (state === 'paused' || state === 'counting') state = 'running';     /* during the countdown: start now */
    else {
      if (state === 'ended') { at = 0; into = 0; }
      begin();
    }
    refresh();
  };
  const restart = () => {
    if (!breaths.length) return;
    at = 0;
    into = 0;
    begin();
    refresh();
  };
  /* To the start of the breath before or after. Past the last one is the end. */
  const step = (by) => {
    if (!breaths.length) return;
    const to = Math.max(0, (state === 'ended' ? breaths.length : at) + by);
    if (to >= breaths.length) {
      if (state === 'ended') return;
      at = breaths.length;
      state = 'ended';
    } else {
      at = to;
      if (state === 'ended') state = 'paused';
    }
    into = 0;
    refresh();
  };
  const paintSize = () => {
    text.style.fontSize = size + 'px';
    limit(buttons.smaller, size <= TP_SIZE.least);
    limit(buttons.bigger, size >= TP_SIZE.most);
    measure();
    draw(0, true);
  };
  const paintMirror = () => {
    root.classList.toggle('tp-mirror', mirror);
    buttons.mirror.setAttribute('aria-pressed', String(mirror));
  };
  const setPace = (by) => {
    tenths = Math.max(TP_PACE.least, Math.min(TP_PACE.most, tenths + by));
    tpKeep('tp-pace', tenths);
    paintPace();
  };
  const setSize = (by) => {
    size = Math.max(TP_SIZE.least, Math.min(TP_SIZE.most, size + by * TP_SIZE.step));
    tpKeep('tp-size', size);
    paintSize();
  };
  const setMirror = () => {
    mirror = !mirror;
    tpKeep('tp-mirror', mirror ? 1 : 0);
    paintMirror();
  };

  const close = () => {
    if (closed) return;
    closed = true;
    if (frameId) window.cancelAnimationFrame(frameId);
    hold(false);
    document.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('keyup', onKeyUp);
    document.removeEventListener('visibilitychange', onHidden);
    window.removeEventListener('resize', onResize);
    if (watcher) watcher.disconnect();
    root.removeEventListener('click', onClick);
    root.textContent = '';
    root.classList.remove('tp', 'tp-mirror');
    root.removeAttribute('data-state');
    if (opts.onClose) opts.onClose();
  };
  const acts = {
    toggle, restart, prev: () => step(-1), next: () => step(1), slower: () => setPace(-1), faster: () => setPace(1),
    smaller: () => setSize(-1), bigger: () => setSize(1), mirror: setMirror, close: () => { if (opts.onClose) close(); },
  };

  function onClick(e) {
    const b = e.target.closest('[data-tp]');
    if (b) acts[b.getAttribute('data-tp')]();
    else if (stage.contains(e.target)) toggle();      /* a tap anywhere on the text starts and pauses */
  }
  function onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey || !Object.prototype.hasOwnProperty.call(TP_KEYS, e.key)) return;
    const act = TP_KEYS[e.key];
    e.preventDefault();         /* also keeps the button that has the focus from taking Space as a click of its own */
    if (!e.repeat || TP_REPEATING[act]) acts[act]();
  }
  function onKeyUp(e) {
    if (e.key === ' ' || e.key === 'Spacebar') e.preventDefault();      /* some browsers click the button on release */
  }
  /* A page nobody is looking at stops, so that coming back to it does not find the text minutes further on. */
  function onHidden() {
    if (!document.hidden || (state !== 'running' && state !== 'counting')) return;
    state = state === 'counting' ? 'idle' : 'paused';
    refresh();
  }
  /* The text area changed size: the window did, the phone was turned, or a line above it wrapped. The reading
     line has moved with it, so the text is put back on it. */
  function onResize() {
    if (closed) return;
    measure();
    draw(0, true);
  }
  root.addEventListener('click', onClick);
  document.addEventListener('keydown', onKeyDown);
  document.addEventListener('keyup', onKeyUp);
  document.addEventListener('visibilitychange', onHidden);
  window.addEventListener('resize', onResize);
  if (window.ResizeObserver) {
    watcher = new window.ResizeObserver(onResize);
    watcher.observe(stage);
  }

  if (!breaths.length) ['toggle', 'prev', 'next', 'restart'].forEach((act) => limit(buttons[act], true));
  paintMirror();
  paintPace();
  paintState();
  paintBreaths();
  paintSize();          /* last: it places the text, and the bars above must have their words by then */

  return { close, source: (name) => { source = name; paintSource(); } };
}
/* tp-core:end */

Object.assign(I18N.zh, TP_TEXT.zh, {
  tp_exp: '提词器文件（.html）', tp_exp_d: '一个文件，用浏览器打开就能用，不用联网。在 iPhone 上可能只显示文字',
});
Object.assign(I18N.en, TP_TEXT.en, {
  tp_exp: 'Teleprompter file (.html)', tp_exp_d: 'One file that runs in a browser with no connection. On an iPhone it may only show the text',
});

const TP_DEFAULT_CPM = 240;     /* used when the voice profile has no rate; must match DEFAULT_RATE in studio/exports.py */
const TP_CUE = /\[[^\[\]]*\]|［[^［］]*］|【[^【】]*】/g;      /* must match _BRACKET_CUE in studio/textstats.py */
let tpOpen = null;              /* the prompter on screen, or null */

/* The breaths she reads, in order, as { text, units }. Must match prompter_breaths in studio/exports.py, which
   makes the same list for the phone file. A line played from the footage (LIVE_CLIP) is not hers to read, a
   [cue] is a stage direction, and "/" and line breaks separate breaths. A breath with nothing this app counts
   in it (only punctuation, say) still gets one unit, so it is shown for a moment and never passed over unseen. */
function tpBreaths(lines) {
  const out = [];
  (lines || []).forEach((line) => {
    if (!line || String(line.audio_mode || '').trim().toUpperCase() === 'LIVE_CLIP') return;
    String(line.spoken_script_zh || '').replace(TP_CUE, '').replace(/`/g, '').split(/[\/\n]/).forEach((piece) => {
      const text = piece.trim();
      if (text) out.push({ text, units: Math.max(1, spokenUnits(text)) });
    });
  });
  return out;
}

function openPrompter() {
  if (tpOpen) tpOpen.close();
  const breaths = tpBreaths(cur.script.lines);
  const given = cur.stats ? cur.stats.speaking_rate_cpm : null;
  const known = typeof given === 'number' && Number.isFinite(given) && given > 0;
  $prompter.hidden = false;       /* before it is built: the text is measured as it is laid out */
  $prompter.setAttribute('role', 'dialog');
  $prompter.setAttribute('aria-modal', 'true');
  $prompter.setAttribute('aria-label', t('prompter'));
  /* While it is open, Tab goes round its own buttons and does not reach the page underneath. */
  const onTab = (e) => {
    if (e.key !== 'Tab') return;
    const stops = Array.from($prompter.querySelectorAll('button'));
    const inside = $prompter.contains(document.activeElement);
    if (!inside || (!e.shiftKey && document.activeElement === stops[stops.length - 1])) { e.preventDefault(); stops[0].focus(); }
    else if (e.shiftKey && document.activeElement === stops[0]) { e.preventDefault(); stops[stops.length - 1].focus(); }
  };
  const leave = () => view.close();
  const view = tpMount($prompter, {
    breaths,
    cpm: known ? given : TP_DEFAULT_CPM,
    /* Which sentence says where the pace comes from. With her own profile it starts as the plain one and is
       replaced below, once the profile says whether that rate is really hers. */
    source: !known ? 'default' : (appState.dna_is_example ? 'example' : 'plain'),
    say: t,
    onClose: () => {
      $prompter.hidden = true;
      document.removeEventListener('keydown', onTab);
      window.removeEventListener('hashchange', leave);
      if (tpOpen === view) tpOpen = null;
      keepWithCore();     /* app.js: the pace, the size and the mirror as she left them are kept by the core as well */
      /* Back to the button that opens it. It is looked up now: a click does not give a button the focus in
         every browser, and the editor may have drawn its buttons anew since. */
      const opener = document.getElementById('prompter-btn');
      if (opener) opener.focus();
    },
  });
  tpOpen = view;
  document.addEventListener('keydown', onTab);
  window.addEventListener('hashchange', leave);     /* going to another page of the app closes it */
  $prompter.querySelector(breaths.length ? '[data-tp="toggle"]' : '[data-tp="close"]').focus();
  if (!known || appState.dna_is_example) return;
  /* "Your rate" is only said of a rate measured from her recordings or typed in by her. A profile learned
     before either holds the default, and the voice page calls it that too. */
  api('GET', '/api/dna').then((answer) => {
    if (tpOpen !== view) return;
    const summary = (answer.dna && answer.dna.source_corpus_summary) || {};
    const hers = summary.speaking_rate_source === 'measured' || summary.speaking_rate_confirmed === true;
    view.source(answer.is_example ? 'example' : (hers ? 'mine' : 'default'));
  }).catch(() => {});             /* not answered: the plain sentence stays, which claims nothing */
}
