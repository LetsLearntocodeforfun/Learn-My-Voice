'use strict';
/* What the desktop app's own window does with the real page, found out by the page itself.

   People never get this file. The page loads it only when the core was started by the script that tries an
   installer (LEARN_MY_VOICE_WINDOW_CHECK=1, which makes /api/state say window_check). The desktop app shows the
   page in the system's web view, which is not the browser the pages are developed in, and nobody is at the
   screen of the machine that builds an installer. So, once that script says "start", the page uses itself the
   way a person would, through the same buttons: it opens a script in the editor, exports it through the menu,
   follows a link to another site, and deletes the script through the question the page asks. It writes down
   what happened at each step, what this web view offers, and every error the page logged, and sends that to the
   core, where the script reads it. During such a trial the core writes links and folders down without opening them.

   Nothing here waits for ever, and nothing here asks the system for anything a person would have to answer.
   The first time this ran in WebView2, on one of GitHub's Windows machines on 18 November 2026, the page never
   reported. The one thing in it without a time limit was a write to the clipboard without a click, done to see
   what the web view says to that. That this was where it stopped is concluded from reading this file, not seen:
   the log of that machine cannot be read without an account. Since then every step has a limit, the clipboard
   is only asked about and never written to, and after each step the core is told how far the check has come. */
(function () {
  const report = { agent: navigator.userAgent, facts: {}, steps: [], errors: [], saved: null, href: '' };
  window.addEventListener('error', (e) => report.errors.push(String(e.message || e)));
  window.addEventListener('unhandledrejection', (e) => report.errors.push('promise: ' + String((e.reason && e.reason.message) || e.reason)));

  const STEP_MS = 30000;        // the most one step may take; each takes a second or two when all is well
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  /* `promise`, or an error when it has not settled after `ms`: a web view can leave a request of a page unanswered. */
  function within(ms, what, promise) {
    let timer = null;
    const late = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error('no answer within ' + (ms / 1000) + ' s: ' + what)), ms);
    });
    return Promise.race([promise, late]).then(
      (value) => { clearTimeout(timer); return value; },
      (problem) => { clearTimeout(timer); throw problem; });
  }
  /* Tells the core how far the check has come. Should the page stop for good after this, the trial can still say where. */
  async function soFar() {
    try { await within(5000, 'the core', api('POST', '/api/window-check', Object.assign({ unfinished: true }, report))); }
    catch (e) { /* only a trace: the report at the end is what counts, and that is tried several times */ }
  }
  async function until(what, test, ms) {
    const end = Date.now() + (ms || 20000);
    for (;;) {
      let found = null;
      try { found = await test(); } catch (e) { found = null; }
      if (found) return found;
      if (Date.now() > end) throw new Error('waited in vain for ' + what);
      await sleep(60);
    }
  }
  async function step(name, run) {
    try { report.steps.push({ name, ok: true, said: String((await within(STEP_MS, name, run())) || '') }); }
    catch (e) { report.steps.push({ name, ok: false, said: String((e && e.message) || e) }); }
    await soFar();
  }
  /* Still this app's page, with its parts in place: what a download or a link must never change. */
  function stillTheApp(after) {
    if (location.origin !== report.origin || !document.getElementById('view') || !document.getElementById('nav')) {
      throw new Error('after ' + after + ' the window no longer shows the app: ' + location.href);
    }
  }

  async function run() {
    report.origin = location.origin;
    report.facts = {
      secure_context: !!window.isSecureContext,
      clipboard: !!(navigator.clipboard && navigator.clipboard.writeText),
      microphone_api: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      recorder: !!window.MediaRecorder,
      audio: !!(window.AudioContext || window.webkitAudioContext),
      wake_lock: !!navigator.wakeLock,
      browser_confirm: typeof window.confirm,
      blur_behind: !!(window.CSS && (CSS.supports('backdrop-filter', 'blur(2px)') || CSS.supports('-webkit-backdrop-filter', 'blur(2px)'))),
      dark_wanted: !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches),
      width: window.innerWidth, height: window.innerHeight, pixel_ratio: window.devicePixelRatio,
      language: navigator.language, desktop: !!appState.desktop,
    };
    /* Whether a page may write to the clipboard here without a click. Asked about, never tried: the app itself
       copies only when a button is clicked, and a web view may answer a try with a question on the screen. */
    try {
      const answer = await within(3000, 'the clipboard permission', navigator.permissions.query({ name: 'clipboard-write' }));
      report.facts.clipboard_write = String(answer && answer.state);
    } catch (e) {
      report.facts.clipboard_write = 'not told (' + String((e && e.name) || e) + ')';
    }
    await soFar();              // "begun": from here on the trial knows that the page's scripts run in this window

    let id = '';
    await step('a script opens in the editor', async () => {
      const made = await api('POST', '/api/scripts', { title: 'window check 窗口检查', lines: [{ spoken_script_zh: '今天去超市啦 / 买了好多东西' }] });
      id = made.id;
      location.hash = '#/s/' + id;
      await until('the editor', () => document.getElementById('export-btn') && document.getElementById('title'));
      return document.getElementById('title').value;
    });

    await step('an export through the menu is saved and the window still shows the editor', async () => {
      lastExport = null;
      document.getElementById('export-btn').click();
      const item = await until('the export menu', () => {
        const pop = document.getElementById('export-pop');
        return pop && !pop.hidden && pop.querySelector('[data-export="voiceover"]');
      });
      item.click();
      report.saved = await until('the core saying where the file went', () => lastExport);
      await until('the page saying where the file went',
        () => $toast.classList.contains('show') && $toast.textContent.indexOf(report.saved.name) !== -1);
      stillTheApp('the export');
      if (!document.getElementById('export-btn')) throw new Error('after the export the editor is gone');
      return $toast.textContent;
    });

    await step('the saved file can be shown in its folder', async () => {
      const button = $toast.querySelector('button');
      if (!button || !report.saved) throw new Error('the message about the saved file has no button');
      button.click();
      await until('the core being asked to show it', async () => (await api('GET', '/api/window-check')).shown.indexOf(report.saved.path) !== -1);
      return button.textContent;
    });

    await step('a link to another site goes to the system browser and the window stays', async () => {
      const url = ['https:', '', 'example.com', 'window-check'].join('/');
      const a = document.createElement('a');
      a.setAttribute('href', url);
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = 'window check';
      $view.appendChild(a);
      a.click();
      a.remove();
      await until('the core being asked to open it', async () => (await api('GET', '/api/window-check')).opened.indexOf(url) !== -1);
      await sleep(300);
      stillTheApp('the link');
      return url;
    });

    await step('a script is deleted through the question the page asks', async () => {
      const button = await until('the delete button', () => document.getElementById('delete-btn'));
      button.click();
      const yes = await until('the question', () => {
        const ask = document.getElementById('ask');
        return ask && !ask.hidden && document.getElementById('ask-yes');
      });
      const asked = document.getElementById('ask-text').textContent;
      yes.click();
      await until('the script being gone', async () => {
        try { await api('GET', '/api/scripts/' + id); return false; } catch (e) { return e.code === 'not_found'; }
      });
      await until('the editor closing', () => !document.getElementById('delete-btn'));
      stillTheApp('deleting');
      return asked;
    });

    report.href = location.href;
  }

  (async function () {
    for (;;) {
      try {
        const check = await api('GET', '/api/window-check');
        if (check.start && !check.report) break;
        if (check.report) return;                           // done already, in this run of the app
      } catch (e) { /* the core is not answering: ask again */ }
      await sleep(500);
    }
    try { await run(); } catch (e) { report.errors.push('the check itself: ' + String((e && e.message) || e)); }
    for (let tries = 0; tries < 5; tries++) {
      try { await within(10000, 'the core', api('POST', '/api/window-check', report)); return; } catch (e) { await sleep(500); }
    }
  })();
})();
