# Learn-My-Voice: agent team roadmap

For the people and agents who work on this repository. It is not part of the download
(`tools/build_release.py` leaves it out). It says where the work stands, what the review of round 1 found,
who does what in round 2, and the rules everybody works by.

The README says what the app does and what has and has not been verified. This file never says more than
the README would be allowed to say.

---

## 1. What must stay true

The tests enforce most of this (`python3 -m unittest discover -t . -s tests`). Read the test named beside a
rule before you work near it.

1. **Standard library only.** `studio/*.py` and `tools/*.py` import nothing that has to be installed.
   Transcription (`mlx-whisper`, `numpy`) lives in its own environment and runs in a sealed worker
   (`studio/transcribe.py`, `studio/whisper_worker.py`).
2. **The desktop shell gets no new abilities** (`tests/test_desktop.py`, `ShellFilesTest`). No new crate, no
   `tauri::command`, no plugin, no capability file. A feature is built in `studio/`, so the desktop app, the
   zip, the source and MCP all have it.
3. **The database is written the plain way a backup can check** (`studio/backup.py`, `tests/test_backup.py`).
   Column types `TEXT`, `INTEGER`, `REAL`; `PRIMARY KEY`, `NOT NULL`, `UNIQUE`, `DEFAULT` with a literal. No
   `CHECK`, no foreign key, no trigger, no view. A column added to a table that already shipped also goes
   into `store._ADDED_COLUMNS`. `backup.DATA_VERSION` names one layout; `LAYOUT_OF_DATA_VERSION` in the
   tests holds its fingerprint.
4. **The page talks to this app and to nothing else** (`tests/test_hardening.py`). No `fetch` to another
   address, no `<style>`, no handler written into markup, no `<form>`, no frame, no outside `href`. A link
   to another site is a button that calls `openOutside(url)`. A new page script is named in
   `studio/web/index.html` before `/pages.js` and in the two sorted lists of that test. Everything that
   comes from data goes through `esc()`.
5. **Text can be read in both appearances** (`tests/test_look.py`). A new colour needs a dark value and a
   place in `PAIRINGS`. Prefer the classes that are there.
6. **Chinese and English, both complete.** Every word on a page is in `I18N.zh` and `I18N.en`. Every refusal
   carries `(code, message, message_zh)`. English about the creator uses `{@name}`, `{@them}`, `{@their}`.
7. **Nothing is fetched from Xiaohongshu.** `xiaohongshu.com`, `rednote.com` and `xhslink.com` are refused by
   name (`docs/SECURITY_NOTES.md`). Outside the AI service the creator chose, a model server on this computer,
   and GitHub's list of this project's releases, optional Autopilot (`studio/trend_sources.py`, off by default)
   fetches only public `https://` pages the creator named and switched on, after checking global public IPs and
   `robots.txt`.
8. **A file, a pasted text and a model's reply are hostile input.** Sizes, rows and nesting are bounded
   before work is done on them. See `studio/keyword_import.py` and `studio/backup.py` for how.
9. **Nothing takes effect behind the creator's back.** By default (and at Autopilot `suggest`), a learned voice
   is a proposal, an import shows what it understood before storing anything, and plans and scripts are untouched.
   Only when the creator explicitly switches Autopilot to `auto` may bounded voice/lesson updates or candidate
   topic refreshes apply, each recorded in `autopilot_runs` with a one-click Undo. Nothing an MCP app can call
   switches Autopilot on or accepts something for the creator.
10. **Say only what was shown.** "Verified" means a test ran it. Code written from a vendor's documentation
    and never run against the service says so where it stands, and the README says so too.

---

## 2. Where the work stands

Round 1 (one session, 21 November 2026) added four things on top of `v0.2.0-beta.rc.19`. They are on the
branch `work/full-service`, with data version 3, which has not shipped: round 2 may still change it.

| Area | Round 1 built | Module |
| :--- | :--- | :--- |
| Updates | A look at GitHub's releases when the app starts, a prompt, a download checked against the published SHA-256, moving a zip's data to a newer zip's folder | `studio/updater.py`, `studio/web/updater.js` |
| Numbers and comments | Numbers kept by day (`metric_snapshots`), an import of a Creator Center table, pasted comments counted by word lists, a model's summary of them (`prompts/09`) | `studio/rednote_analytics.py`, `studio/web/rednote.js` |
| Plan | A list of planned videos with a day and a state, made from ideas, from comments or by hand (`video_plans`) | `studio/planner.py`, `studio/web/planner.js` |
| Tone | Counts of particles, connectors and doubled words, five scores made from them, loudness and pauses from a WAV | `studio/tone_analyzer.py`, `studio/web/tone.js` |
| Providers | Anthropic as an AI service, an optional temperature | `studio/llm.py` |

### What the review of round 1 found

Round 1 was reported as built and verified. Reading it again the same day found the following. Each has an
owner in round 2. None of it has shipped.

| No. | What is wrong | Owner |
| :--- | :--- | :--- |
| U1 | Opening a page asked api.github.com for releases, also from the three tests that drive Chrome, and the error that followed was caught and dropped. A stopgap is in (`App.update_checks`, off unless `main` turns it on). To finish: the page is told whether checks are on, the trials of a package and an installer switch them off, and a test shows no request leaves without the switch. | A |
| U2 | `POST /api/update/migrate` replaces the database and the settings of the folder in use with those of another folder: no backup first, outside the gate every restore passes, allowed when the folder in use holds work, and a page that was open keeps writing. One click in Settings can lose everything. | A |
| U3 | `POST /api/update/apply-zip` unpacks into a folder that may exist and replaces its data. | A |
| U4 | A download is held whole in memory (up to 512 MB) and is saved also when no checksum was published. The checksum comes from the same place as the file: it shows a download is whole, not that a release is genuine. Nothing says so. | A |
| U5 | The transcription environment (about 2 GB) is copied inside a request. Whether a copied environment works is not shown. | A |
| U6 | Every look at the update state opens the databases of neighbouring folders. | A |
| R1 | Importing an `.xlsx` calls `keyword_import._xlsx_rows`, which does not exist. A catch-all turned that into "could not read". It never worked. Only `.csv` was tested. | B |
| R2 | A "发布时间" column is taken for the day the numbers were read, so views per day come out wrong. | B |
| R3 | A row is matched to a video by title, then by part of a title, and written at once. There is no preview, so the wrong video can get the numbers. | B |
| R4 | The word lists for comments count "不好用" as praise and complaint at once. The lists were never reviewed. | B |
| R5 | The notes said views count in `hooks.py` and `refs.py`. They do not. `corpus.performance` still orders by interactions alone. | B |
| P1 | `GET /api/plans` changes rows. A read is not counted as a change by the backup gate. | C |
| P2 | `prompts/06` was not told about the new inputs it receives, and `ideas_prompt` lost the sentence that lists what the prompt carries about the creator. The page that offers the prompt has to name all of it, the comment summaries included. | C |
| P3 | A plan's title and notes cannot be edited. | C |
| T1 | Loudness and pauses are measured from the uploaded file, which is a video or an `.m4a`, never a WAV. So nothing is measured for a real upload. Only a made-up WAV in a test went through. | D |
| T2 | The five tone scores are sums with weights nobody tuned. They are shown as if they measured something. | D |
| T3 | The smallest amount of material for a comparison was lowered from 10 breaths to 6 so that a test passed. | D |
| T4 | Every sample of up to ten minutes of sound is looped over in Python inside the upload. Not measured. | D |
| L1 | The Anthropic client is written from documentation and was never run against the service. It asks for 8,192 tokens, more than some models give. A temperature is passed to models that may refuse one. | F |
| L2 | Through MCP an AI app can now read viewers' comments in summary. No instruction says what that is. | F |
| G1 | `tests/test_full_service_gaps.py` is one file for four areas. | all |

---

## 3. Round 2: who does what

Six agents, each in a worktree of its own on a branch of its own, cut from `work/full-service`. The
integrator merges, reviews every diff, writes the README and the changelog, and makes the release candidate.

**A. Updates and releases.** `studio/updater.py`, `studio/web/updater.js`, the update routes in
`studio/server.py`, `tools/build_release.py`, `tools/try_release.py`, `desktop/try_installer.py`,
`tests/test_updater.py`. Fix U1 to U6. Then, if it can be done so that `tests/test_release.py` holds it to
the rules the other workflow keeps: a workflow that builds and attaches the packages when a final tag is
pushed. It must not change `release-candidate.yml`.

**B. Numbers and comments.** `studio/rednote_analytics.py`, `studio/web/rednote.js`, `prompts/09`,
`studio/corpus.py`, `studio/trends.py`, `studio/hooks.py`, the `/api/rednote/` and comment routes,
`tests/test_rednote_analytics.py`. Fix R1 to R5. Then: more columns of a Creator Center table (click rate,
watch time, completion, shares, new followers) where a real export has them, and comments read from a
screenshot by an AI service that takes pictures, with copy and paste as the other way.

**C. Plan and ideas.** `studio/planner.py`, `studio/web/planner.js`, `studio/web/discover.js`,
`prompts/06`, the ideas part of `studio/pipeline.py`, the `/api/plans` routes, `tests/test_planner.py`. Fix
P1 to P3. Then: a week and a month view of the plan, and planning several videos at once from the
questions viewers asked.

**D. Voice and tone.** `studio/tone_analyzer.py`, `studio/web/tone.js`, `studio/voicematch.py`,
`studio/transcribe.py`, `studio/whisper_worker.py`, `studio/web/craft.js`, the tone routes,
`tests/test_tone_analyzer.py`. Fix T1 to T4. Then: pitch, measured in a way the tests can run without
anything installed, and the tone of a script beside the creator's own in the editor.

**E. Sponsored briefs.** New: `studio/sponsored.py`, `studio/web/sponsored.js`, `tests/test_sponsored.py`;
`prompts/03`, the sponsored part of `prompts/04` and of `studio/pipeline.py`. An earlier session left this
unfinished on the branch `subagent-Sponsored-brief-flow-finisher-self-ed3a560e` (one commit on `a2e77d7`,
"inherited and not yet reviewed"). Read it as a stranger's work: keep what is sound, and bring that to the
code as it is now.

**F. AI services, beats, MCP.** `studio/llm.py`, `studio/video_ingest.py`, `studio/mcp_server.py`,
`studio/refs.py`, `studio/web/refs.js`, `tests/test_providers.py`, `tests/test_beats.py`. Fix L1 and L2.
Then: keep the beats a video was read into (kind, how it is spoken, meaning, what is seen) in a table of
their own, and use them where a draft is given examples.

### Left out on purpose

- **Fetching links, numbers or comments from Xiaohongshu**, also through a helper on this computer. Rule 7.
  The owner was asked and has not decided.
- **Signing the installers.** Needs certificates the project does not have.
- **Transcription on a Python other than 3.9.** Needs a checked list of package digests for each version.
- **Larger videos through Gemini's file upload, and Whisper's words handed to Gemini.** Neither can be run
  against the service from here, and both change how long an upload is kept.
- **Running the web lookup, or Anthropic, against the real services.** No key is used in this work.

---

## 4. How round 2 works

**Your worktree, your branch.** Commit there. Do not push, do not tag, do not touch `main`, another
worktree, `data/`, `local_tools/` or anything outside the repository. Install nothing. Use no real key.
No test may open a connection to another computer.

**Read before you write.** `studio/posted.py`, `studio/trends.py` and `studio/keyword_import.py` with their
tests show how this project writes: a docstring that says in plain words what is done and what is not,
limits with their reasons, refusals in two languages, tests named after what a person can rely on. Round 1
did not write that way. Bring what you own up to it.

**Start with the review.** Read the round 1 code you own as a stranger's. The table above is what one
quick reading found; expect more. Fix what is wrong before you add anything.

**The data version stays 3.** It has not shipped. When you change `store._SCHEMA`: keep to rule 3, put the
new fingerprint under key 3 in `tests/test_backup.py`, name a new table in the lists there and in
`tests/test_refs.py`, give it a row in `fill()`, and have `backup._try_out` read it. Two agents will change
those same lines; the integrator settles them.

**Shared files.** `studio/server.py`, `studio/store.py`, `studio/pipeline.py`, `studio/mcp_server.py`,
`studio/web/app.js`, `studio/web/pages.js`. Change only what your area needs, where your area already
stands in the file, and list every such change in your report. A route hands over to your module at once.

**Tests.** Yours go into the file named for your area. Leave `tests/test_full_service_gaps.py` alone unless
your change makes one of its checks wrong; then change only that check. A new MCP tool changes the counts
in `tests/test_setup.py` and `tests/test_integrations.py` and the two sets in the latter. Before your last
commit the whole suite passes.

**Not yours.** `README.md`, `README.zh-CN.md`, `CHANGELOG.md`, `docs/`. The integrator writes them from
your report.

**Your report**, when you are done:

1. What a creator can do now that they could not before, in two or three sentences.
2. What you found wrong in round 1 and what you did about each, by number, and what you found that the
   table does not have.
3. What leaves the computer that did not before, to where, and what it contains.
4. What is not verified: never run against a real service, a real file from the platform, a real person.
5. What you left out and why.
6. Changes to shared files.
7. The tests you added, the count of the whole suite, and your commits.
