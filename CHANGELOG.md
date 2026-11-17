# Changelog

Each beta release is a tag on `main` and a pre-release on GitHub. Versions are `v0.<iteration>.0-beta`.
Before a beta is tagged, release candidates of it are built from a `beta/...` branch: every push there is
tested, packaged and published by GitHub Actions as a pre-release named `v<version>.rc.<number>`.
The README's Status section says what has and has not been verified; this file says what changed.

## v0.2.0-beta (not released yet)

In progress, written from the code on 16 November 2026. Release candidates are built from the branch
`beta/0.2.0`. The final tag has not been made; the date goes in when it is, and "State of this release" has to
be read again then.

**New**

- **Guided setup.** A Start page with five steps: who the creator is, their material, their voice, a first
  script, and what keeps learning. The prompts receive the creator profile and no longer assume one specific
  creator.
- **Subtitle files and videos as material.** `.srt` and `.vtt` files are read with their timing. On a Mac with
  an Apple chip a video can be transcribed on the computer; the uploaded copy is deleted afterwards. Speaking
  rate is measured from timed material once there is a minute of it. A transcript waits for the creator's
  confirmation before its wording is learned from.
- **A learned voice is a proposal.** Every phrase the AI attributes to the creator is looked up in their own
  material, the review shows what was and was not found, and nothing takes effect until the creator accepts
  it. Earlier profiles are kept and can be restored. This closes the item left open in v0.1.1-beta.
- **Lessons from edits.** The app shows what the creator changed in an AI draft. A change can be remembered
  and then goes into later draft prompts.
- **What a draft was based on.** Each AI draft lists which of the creator's scripts its prompt contained.
- **Phrases never to be used.** Typed in during setup and enforced by rule in every check.
- **One line to ideas.** On 选题建议, and on the home page once an AI is connected, the creator types one line
  about what they want to film, or leaves it empty, and clicks 找选题. With a Gemini or OpenAI key the app first
  asks that service to search the web for current keywords in the creator's niches, keeps those that come with
  a source link (marked as unverified), and then asks for ideas that answer the line. The page shows the ideas,
  the keywords found with their sources, and the pages the lookup cited. With a local model or in copy & paste
  mode the same two steps are offered by hand. When one step fails the other's result is still shown, and ideas
  saved earlier stay.
- **Saying the line.** On a Mac with the on-device transcriber the line can be spoken. The recording goes to
  the app on this computer, is turned into text there and deleted; the browser's own speech recognition is not
  used. The text lands in the box and can be corrected before anything is sent.

- **Voice match.** The editor shows how close a script is to the creator's own speech habits, as a number
  counted in code, not judged by a model: how long a breath is, how it ends, how it begins, and English mixed
  in. The number stands next to the range the creator's own scripts score in, and a section that opens on
  request shows what was counted on both sides. It says nothing about whether a script is good.
- **The creator's own openings.** The first breaths of each confirmed, non-sponsored item are shown next to
  that video's interactions as a multiple of the creator's usual, with labels given by plain rules. Openings
  from videos at or above the usual also go into the draft prompt, and a script records which ones its draft
  saw.
- **Proposals for a line.** Every spoken line offers "reword" and "shorten", the first also "openings", and
  the script an "AI read-through". A model writes the proposals; the app checks each with the same rules as
  any line and with the counted speech habits before showing it, orders them by those checks, and applies
  nothing until "use this" is pressed, which can be undone. A line taken from a proposal is shown as an AI
  proposal that was taken, not as the creator's edit, and is not learned from. Works with a connected AI
  service and in copy & paste mode.
- **A teleprompter paced by the speaking rate.** One breath at a time, each lasting its length over the
  speaking rate in the voice profile, with a pace control, a countdown, a mirror for a teleprompter glass and
  keys for everything. It says whose rate it uses, and calls it the creator's only when it was measured or
  typed in. It can be exported as one self-contained `.html` file. Nothing listens: there is no microphone
  and no speech recognition in it.
- **Importing a keyword spreadsheet.** On 热点, a keyword list saved from an analytics tool as `.csv`, `.tsv`,
  `.txt` or `.xlsx` can be imported. The app reads the file on this computer, shows which columns it took for
  the keywords and for the number, how the first rows read and how many rows are left out and why, and stores
  nothing until Import is clicked. What is imported counts as the creator's own entry, like a paste. A file is
  treated as hostile input: sizes, rows, columns and unpacking are capped, a workbook is never written to
  disk, formulas are never worked out, and a workbook that declares entities is refused.
- **Mark as posted.** A script written here and then posted becomes one library item, with the day it was
  posted, an optional link kept as text, and its numbers, so everything that ranks by the creator's own
  results sees what was made in the app. The day the numbers were read is kept beside them and shown as
  days after posting; nothing is adjusted or projected. The wording waits: it is not learned from until the
  creator confirms it, or until the words of the posted video take its place from a subtitle file or a
  transcript. The page says how many lines are still exactly as an AI wrote them, counted in code. An
  opening that is still an AI's sentence is kept apart from the creator's own openings and never reaches a
  prompt as the creator's habit.
- **A word list that says what it rests on.** A finding about advertising wording now says what its entry
  rests on: the article of the Advertising Law that was read (as published on Wikisource, read on 16
  November 2026) and whether the wording is named there or listed by analogy, or that no source was checked.
  The list has more entries than before. On Settings any entry can be switched off and the creator can add
  phrases of their own, which are looked for exactly as written. Every page that lists such findings says
  once that the list is this project's own, not legal advice and not a statement of any platform's rules,
  the day it was reviewed, and how many entries are switched off.
- **Hints that a line sounds written.** A new kind of finding, drawn apart and labelled "hint only", for
  wording that tends to make a spoken line sound written or machine-made. The clues follow the Humanizer-zh
  checklist (MIT), which says of itself that it gives clues and not a blacklist. A hint is never an error,
  is not counted among the things to fix, and stops nothing.
- **Launchers for Windows and Linux.** `Start Learn My Voice.bat` and `start-learn-my-voice.sh` start the app
  with the Python the system has, and say what to install when there is none.
- **A package to download, and the app says which build it is.** A release candidate comes with one zip that
  holds what is needed to run the app and nothing else, built so that the same commit always gives the same
  bytes, with its SHA-256 published next to it. Settings shows the version and commit of the build in use.
- **A desktop app, with installers.** The same app in a window of its own, installed like any other program:
  a `.dmg` for Macs with an Apple chip, one for Macs with an Intel chip, and a setup program for 64-bit
  Windows. It needs no Python on the computer: Python 3.14 is inside it. What it keeps (scripts, library,
  settings) is in the system's folder for application data, so installing a newer version keeps it. The
  window is a small program written with Tauri 2 that starts the app on this computer, shows it, and ends it
  when the window is closed; it gives the page no abilities of its own. The installers are not signed,
  because this project has no paid certificates: the system has to be told once that the app may run, and
  each release candidate's notes say how, in English and Chinese. Transcribing videos is not in the desktop
  app yet; the zip does that.

**Changed**

- Through MCP, `save_voice_profile` stores a proposal only, and `update_script` only revises an AI draft the
  creator has not edited.
- A save based on an older version of a script is refused instead of replacing the newer one.
- A draft prompt now also holds the openings of the creator's best-performing videos (see above).
- The message the app prints when it starts is sent at once also when its output goes to a file, and no
  longer ends the app where that output cannot show Chinese.
- With a Gemini or OpenAI key, 找选题 now sends a web lookup to that service unless the tick for it is removed.
  Before, the app never started a web lookup itself. The lookup prompt is the one already used by copy and
  paste: niches, up to 20 titles, tracked keywords and the line typed, and nothing else about the creator.
- Through MCP, `get_topic_ideas_brief` and `save_topic_ideas` take an optional `focus`.
- A script with no finding now says "These checks found nothing" where it said "No issues found" with a
  thumbs-up, and through MCP a saved script with no must-fix finding is no longer called free of problems:
  the checks are this project's own lists, and passing them does not make wording allowed.

**Fixed**

- Saving the settings, the creator profile or a voice profile would have failed on Windows with a Python older
  than 3.13. Found by reading the code. The tests now pass on GitHub's Windows machines with Python 3.9.
- On Windows, two saves at the same moment could fail with "Access is denied", and a second copy of the app
  could start on a port the first was already using. Both were found by the tests on GitHub's Windows
  machines on 17 November 2026. A save is now tried again for a moment where Windows refuses it, and the port
  is asked for exclusively there.
- On Windows the pages' scripts could be sent as plain text, which leaves the page empty. Also found by
  reading the code.
- A `.docx` of 437 bytes held the server for 10 seconds, and one pasted trend line of 32,000 spaces for 7.5.
- A check of one very long line that is flagged at every character took four times as long for each doubling
  of its length: 5 seconds for 20,000 characters, which makes about two minutes for 100,000. It now grows with
  the length. A check also lists at most 500 findings about lines and says how many more there were, so that
  the answer to a small request stays small.
- A pasted trend lookup in which one source link had a stray bracket (`http://[...`) failed with "internal
  error". Such a keyword now counts as one without a source.
- Answers and stored files never contain `NaN` or `Infinity`, which used to make a page show nothing.
- Files are written whole and flushed to disk before they replace the old one. On macOS and Linux the data
  folder is closed to the other accounts on the computer.
- Every answer carries a content security policy and forbids framing. At most 4 MB of a provider's answer is
  read. A request to a local model never goes through a proxy.
- Text from the library can no longer close a block of a prompt.

**State of this release**

- The automated tests pass on one Mac with Python 3.9.6, and on GitHub's machines on macOS, Windows and Linux
  with Python 3.9, 3.14 and 3.15. They first passed on all three systems on 17 November 2026; that morning 18
  of 642 failed on Windows, two of them the faults listed under "Fixed". One test transcribes real audio and
  only runs where the transcriber is installed. Each release candidate's own notes say what GitHub's machines
  found for that build.
- Windows and Linux: no person has used the app there. On GitHub's machines the package starts with its
  launcher and the app answers on all three systems.
- The desktop app: no person has installed it. On GitHub's machines each of the three installers was
  installed and the app opened, used and ended, first on 17 November 2026. The installers are not signed. On
  a computer managed by an employer or a school the app may be refused whatever is clicked. Transcription
  does not work inside the desktop app yet, a microphone has not been tried in it, and there is no build for
  Linux.
- On Windows the app does not close its data folder to the other accounts on the computer. Who may read it is
  decided by the folder the app was unpacked in; the desktop app keeps its data in the user's own folder for
  application data.
- A release candidate is published only when the tests pass on macOS, Linux and Windows, the package, started
  with its launcher, works on macOS, and there is an installer of the desktop app for macOS and one for
  Windows that passed its own trial. A trial of the package on Windows and Linux (unpack, start with the
  launcher, use, stop) runs as well; when it fails the release candidate is still published and its notes say
  so with what that job reported. An installer whose trial did not pass is never attached; of the two for
  macOS one is enough for the candidate to go out.
- The Rust packages the desktop app's window is compiled from are the versions listed in the source
  (`desktop/src-tauri/Cargo.lock`, taken from the first build that worked), so two builds of one commit
  compile the same code. The Python inside it is downloaded by its SHA-256.
- Voice match, openings, proposals for a line and the teleprompter were run in headless Chrome against the
  real server with stand-in AI replies and invented material. None has been tried on the creator's real
  material, and no real model has written a proposal. The teleprompter file has not been opened on a phone;
  on an iPhone it may only show its text.
- Importing a keyword spreadsheet: tested with made-up files, and the page was run in headless Chrome against
  the real server. No real export from 新红, 千瓜 or 灰豚 and no workbook saved by Excel, WPS or Numbers was
  available, so the header names it looks for are guesses.
- One line to ideas: the pages were run together with the server in headless Chrome, against stand-in AI
  replies. The web lookup through a Gemini or OpenAI key is written from those services' documentation as it
  read on 16 November 2026 and has not been run against either. Google's terms for showing search-grounded
  results have not been read.
- Saying the line: tested with the Whisper on one Mac and a sound stream in place of the microphone. It wrote
  an English sentence correctly and got one word of a Chinese sentence wrong (秋冬 as 邱中, synthetic voice). A
  real microphone has not been tried.
- On-device transcription has been set up and used on one Mac. Whisper gets some sentence-final particles
  wrong.
- AI steps were tested against stand-in model replies only. Output quality with real models has not been
  evaluated.
- Importing the exported files into 剪映 or CapCut has not been verified.
- The MCP server was tested at protocol level, not with ChatGPT, Codex or Claude Desktop.
- Sponsored-brief handling and a second-pass critic are designed but not built.

## v0.1.1-beta (15 November 2026)

Security and reliability fixes for v0.1.0-beta. Nothing else changes. **Anyone using v0.1.0-beta should update.**

**Fixed**

- **Requests from other websites are refused.** In v0.1.0-beta a web page open in the same browser could send
  writes to the studio while it was running: change the AI provider and key, replace the voice profile, add
  library items and scripts. It could not read anything back, and could not edit or delete existing items.
  The server now refuses any write that names another site as its origin, and accepts a request body only when
  its type is exactly `application/json`.
- **A wrong-shaped voice profile no longer breaks script checking.** A model reply with a field of the wrong
  kind was saved as the profile, and every check then failed with "internal error" until `data/voice_dna.json`
  was deleted by hand. The checks now skip what they cannot use. If this happened to you, updating is enough.
- **A voice profile that cannot be saved no longer replaces the one in use.** The previous profile used to be
  moved to the history folder before the new one was written, so a failed write left the studio on the example
  profile.
- **Two inputs that froze the studio.** A library item with very many opening brackets made the library slow to
  load and held every other request meanwhile (25 seconds for 250,000 of them, and four times longer each time
  the length doubled). A pasted reply that was mostly white space held the server in the same way.

**State of this release**

- 92 earlier tests and the new ones in `tests/test_security.py` pass. The cross-site fix was also checked in
  headless Chrome from a page on another origin.
- Still open in this version, to be fixed in v0.2.0-beta: a voice reply from the model takes effect as soon as
  it arrives, with no review step.
- Everything under "State of this release" for v0.1.0-beta still applies.

## v0.1.0-beta (15 November 2026)

**Superseded by v0.1.1-beta, which fixes security problems in this release. Please update.**

First beta. A local, bilingual (中文 / English) script studio for one Xiaohongshu creator, with nothing to
install except Python.

**In this release**

- Script editor: one card per spoken line, live character count and read time, autosave, teleprompter.
- Rule-based checks in Chinese and English: lines too long for one breath, phrases the creator never uses,
  influencer clichés, translated-sounding Chinese, risky advertising wording, length against the target duration.
- Voice learning from the creator's own scripts. Breath-length limits are measured in code, not taken from the model.
- Drafting anchored on the creator's own best-performing scripts.
- Topic ideas from past performance. Citations that do not match a real video title are removed.
- Trend keywords: paste keywords, rank them by heat, freshness and how the creator's own videos on the topic
  performed. Target keywords on a script, with a check of where each one ended up.
- The title options, cover text, caption and hashtags drafted with a script are shown and can be copied.
- Exports: 剪映 / CapCut kit, `.srt` with estimated timing, voiceover text, shot list, on-screen text list.
- Four ways to provide the AI: copy and paste with a chat subscription (no key), an MCP server, the Gemini or
  OpenAI API, or a local model through Ollama or LM Studio.

**State of this release**

- 92 automated tests pass. The interface was exercised in headless Chrome on macOS.
- AI steps were tested against stand-in model replies only. Output quality with real models has not been evaluated.
- Importing the exported files into 剪映 or CapCut has not been verified.
- The MCP server was tested at protocol level, not with ChatGPT, Codex or Claude Desktop.
- Developed on macOS with Python 3.9. Windows and Linux are untested.
- Prompts and English wording in this release assume one specific creator ("her", with an English-speaking husband).
