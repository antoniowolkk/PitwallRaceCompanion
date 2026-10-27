# Pitwall Race Companion — PRD

Oct 26, 2026 · @Antonio

## Overview

Pitwall Race Companion (called Pit Wall in the rest of this document) is a companion dashboard that acts as an AI race engineer for Assetto Corsa Competizione (ACC): it reads live telemetry, runs a local AI model, and speaks short radio calls to the driver.

**Problem:** a solo sim racer has no pit wall. Nobody watches fuel, tyre temperatures and gaps, or makes strategy calls.

**Pitch:** real-time, fully local, no cloud and no per-token cost. Plain code detects events fast; a small local model turns them into natural engineer talk and answers questions.

## Repository

Code lives at [github.com/antoniowolkk/PitwallRaceCompanion](https://github.com/antoniowolkk/PitwallRaceCompanion). It is public, uses the `main` branch, and currently holds only a README.

```powershell
git clone https://github.com/antoniowolkk/PitwallRaceCompanion.git
```

**Account rule:** every push and every merge uses the GitHub account `antoniowolkk`, and no other account.

- Before any push or merge, confirm the active account is `antoniowolkk` (for example with `gh auth status`) and that `git config user.name` and `git config user.email` in the repo are that account's identity.
- Pull requests are merged by `antoniowolkk`.
- Claude Code, Cowork and any other build tool follow the same rule. If a different account is active, stop and ask instead of pushing.

## Goals and non-goals

v1 proves one thing: a local AI engineer can give useful, timely calls during a real ACC session without hurting game performance. Everything in v1 reads the player car only.

**Goals**

- Read live player-car data from ACC and show it on a lightweight dashboard
- Detect key events in code: fuel, tyre temps and pressures, flags, pit window
- Speak short radio calls and show each one as a subtitle
- Let the driver pick a female or male English voice
- Coach driving style with plain tips after laps and stints
- Propose setup changes from driving style; the driver always decides
- Run all AI fully local through Ollama
- Auto-connect when ACC starts
- Dark mode with a maroon accent

**Non-goals for v1**

- Launching ACC or managing Steam (the dashboard only detects the game)
- Other cars' data such as gaps and positions: too much data, hurts performance (see Ideas backlog)
- Cloud AI, accounts, or multiplayer sync
- Other sims, consoles, or languages besides English
- Setup changes without the driver's approval

## User and context

Primary user is one solo sim racer playing ACC on a Windows PC with the game started from Steam.

- **Hands busy, eyes on track:** the driver cannot read a screen mid-race, so voice is the main output and the dashboard is for before, after and pit moments.
- **GPU shared with the game:** ACC already loads the GPU hard, so the AI must be small.
- **Dashboard placement:** same PC second monitor, or a tablet or phone on the local network (open decision).

## Core features

MVP is live data, event calls, voice and subtitles. The coach and setup advisor come after the base works. Phases match the Milestones section.

| Feature | What it does | Phase |
| --- | --- | --- |
| Connection status | Shows ACC waiting or connected, AI ready, voice ready; auto-connects when the game starts | 1 |
| Installer and setup wizard | Windows installer, then a first-run wizard where the user picks the AI model and voices to download | 1 to 3 |
| Lite mode | No AI model: canned calls, coach rules and setup rules only, lowest memory use | 2 |
| Live telemetry panel | Speed, gear, fuel, tyre temps and pressures, lap times, player car only | 1 |
| Mock mode | Replays sample telemetry so the app can be tested without the game | 1 |
| Event engine | Code rules detect low fuel, hot tyres, flags, pit window, penalties | 2 |
| Canned calls | Fixed lines for time-critical events, no AI delay | 2 |
| Voice out | Local text-to-speech, female or male English voice, call queue and cooldowns | 2 |
| Subtitles in dashboard | Every radio call shown as large text | 2 |
| AI radio calls | Local model words each event as one short engineer sentence | 3 |
| Settings | Voice, volume, call frequency, subtitle on or off, units | 2 |
| Driving coach | Plain tips about driving style per stint | 4 |
| Setup advisor | Per-lap draft setup, versioned setup file only after approval | 5 |
| Subtitle overlay | Subtitles top-left over the game | 6 |
| Driver questions | Push-to-talk, speech-to-text, engineer answers | 6 |
| Strategy suggestions | Pit window and fuel plan from player data | Later |
| Session log | Calls, tips and key moments saved for debrief | Later |

## Pit wall roles: GT3, not F1

An F1 pit wall and a GT3 pit wall do different jobs, so Pit Wall is modeled on GT3 racing in ACC. The F1 version has several specialists plus factory support ([F1 overview](https://www.formula1.com/en/latest/article/the-insiders-guide-to-the-pit-wall.CFwHwjTcLNO5Gf4B3Dy4o)); GT3 is simpler and rule-driven.

| Topic | F1 pit wall | GT3 pit wall (what Pit Wall copies) |
| --- | --- | --- |
| Main decisions | Tyre compounds, undercut and overcut, engine and energy modes | Mandatory driver change in a pit window, fuel, tyre set, stint length |
| Pit rules | Free choice of stop timing | Pit stop only counts inside the window; one driver change; a missed stop means a stop-and-go ([GT World Challenge regs](https://asiaregs.sporting.gt-world-challenge.com/20.Mandatory%20Pit%20Stops/)) |
| Safety periods | Safety car, virtual safety car, red flag | Full Course Yellow: no overtaking, reduced speed, and the pit window may be delayed or extended (same regs, [GT World Challenge Europe briefing](https://www.gt-world-challenge-europe.com/documents/notice/4629/Drivers+and+competitors+Briefing+Notes.pdf)) |
| Car adjustments | Engineer sets engine and energy modes | Driver sets electronics: traction control, ABS and engine map ([Coach Dave](https://coachdaveacademy.com/tutorials/the-ultimate-acc-car-setup-guide/)) |
| Fairness system | None | Balance of Performance (BoP) per car and track; the setup file carries a `trackBopType` value |

Series rules differ per event, so the real pit window and driver-change rules must come from the ACC session data, not from fixed numbers in the code.

| GT3 role | Job | Pit Wall module | Phase |
| --- | --- | --- | --- |
| Race engineer | The one voice on the radio: hazards, flags, fuel, tyre feedback | Radio voice | 2 and 3 |
| Data engineer | Watches telemetry | Event engine and dashboard | 1 and 2 |
| Performance engineer | Reads driving style, proposes setup changes | Driving coach and setup advisor | 4 and 5 |
| Strategist | Pit window, fuel and tyre plan, reaction to Full Course Yellow | Strategy suggestions from player data | Later |
| Sporting director | Rules and race control contact | Not built; ACC session data covers the rules | Out |

## Driving style coach

Pit Wall reads how you drive and gives plain tips. Tips come after a lap or a stint, never in the middle of a corner. Phase 4.

| What it watches | Signal | Example tip |
| --- | --- | --- |
| Braking | Brake input, wheel slip while braking | Front tyres lock under braking. Brake a little earlier and release smoother. |
| Throttle | Throttle input against rear wheel slip | Rear tyres spin on exit. Apply the throttle more gradually. |
| Steering | Steering corrections per corner | Many small corrections mid-corner. Smoother steering keeps the car settled. |
| Tyre use | Tyre pressures and temperature spread | Fronts run much hotter than rears. Ease the steering input in long corners. |
| Consistency | Lap time spread over clean laps | Lap times vary a lot. Repeat the same braking points. |

- Tips come from code rules; the local model only rewords them. Thresholds live in a config file and are tuned in testing.
- Maximum one or two tips per stint, and praise a clean lap when it happens.
- No tip without enough clean laps (default 3, configurable).
- A short driver style summary per session (for example: brakes late, smooth on throttle, rear unstable on exit) feeds the setup advisor. It describes driving in the sim only and stays on the local machine.
- The driver can mute the coach.

## Setup advisor

The advisor turns driving symptoms into small setup proposals. The driver always decides. Phase 5.

**Design decision:** the draft setup updates every lap, but an ACC setup file is written only when the driver approves. One lap is not a trend, and a new file every lap would flood the setup folder.

1. **Base:** the setup file the driver picks. It is never modified.
2. **Every valid lap:** collect lap metrics and update a rolling window of the last clean laps (default 3).
3. **Symptoms:** understeer, oversteer or unstable, per corner phase (entry, mid, exit).
4. **Draft:** rules map the symptom to one candidate change on top of the base. The draft is rewritten every lap in Pit Wall's own drafts folder, not in the ACC setups folder.
5. **Show:** the dashboard shows the current draft: symptom, change, before and after value, and how many laps back it.
6. **Approve:** Apply writes a new versioned file (for example `BGK_RACE_pw_v2.json`) into the ACC setups folder. Reject hides that change for a number of laps.
7. **Load:** the driver loads the file from the garage. What ACC allows to change on track or in the pits must be verified.
8. **Check:** the next stint shows whether the symptom improved. If it got worse, the advisor suggests going back.

| Symptom | Candidate change | Source |
| --- | --- | --- |
| Oversteer on corner entry, unstable | Add rear wing, or lower rear ride height | [Coach Dave](https://coachdaveacademy.com/tutorials/how-to-fix-oversteer-and-understeer-in-acc/) |
| Understeer in fast corners | Lower rear wing | Coach Dave |
| Understeer over the whole lap | Raise rear ride height and lower rear wing | Coach Dave |
| Rear spins on exit | Raise traction control one step | [Driver61](https://driver61.com/sim-racing/assetto-corsa-competizione-setup-guide/), to verify |

These are starter rules; each one must be validated per car in testing.

**Rules**

- One change at a time, one click step, then wait a few laps before the next proposal.
- Driver approves every change. No silent edits.
- No proposal without enough clean laps.
- Change only click values. Static camber, toe out linear and rod length look game-computed, so never hand-edit them (to verify).
- Stay inside each car's valid range; ranges differ per car and BoP (to verify).
- Original file is never overwritten.

**File format**

ACC setups are JSON files in `Documents\Assetto Corsa Competizione\Setups\<car>\<track>` ([install guide](https://simracingsetup.com/support/how-to-install-assetto-corsa-competizione-setups/)). The reference is the Ferrari 296 GT3 file `BGK_RACE`. Top-level keys: `carName`, `basicSetup` (tyres, alignment, electronics, strategy), `advancedSetup` (mechanicalBalance, dampers, aeroBalance, drivetrain), `trackBopType`. The file uses tabs and Windows line breaks; the writer must keep that layout.

Demo output `BGK_RACE_pitwall_v2.json`: same file, one change, rear wing 12 to 13, as a test for entry instability. Not yet tested in the game.

## System architecture

&#91;embedded content: architecture · 9 parts, 3 outputs\]

Only the player car is read. Time-critical calls skip the AI and use fixed lines; other calls get natural wording from the local model. The lap analyser runs once per lap, shows tips and a setup draft on the dashboard, and writes a setup file only after the driver approves.

## Data sources

v1 reads only ACC shared memory, and only the player car. The broadcast feed with other cars is out of v1 because it adds data and load. Shared memory is Windows only.

| Block | Gives | Used by |
| --- | --- | --- |
| Physics | Player car: gas, brake, steering, speed, gear, rpm, fuel, G forces, wheel slip, tyre pressures | Event engine, coach, advisor |
| Graphics | Session status, flags, penalties, pit lane flag, tyre set, rain and track grip | Event engine |
| Static | Values fixed for the session, such as game and session info; car and track fields to verify | Setup file choice |

Field names above follow the [pyaccsharedmemory docs](https://pypi.org/project/pyaccsharedmemory), which copy ACC's own shared memory documentation: physics fields refer to the player car; graphics fields mostly do too, except car coordinates and car IDs for other cars, which Pit Wall ignores.

- **Polling:** 10 times per second by default (configurable), plus a summary at the end of every lap.
- **Library:** Python `pyaccsharedmemory` (`pip install pyaccsharedmemory`). Alternatives: a [Rust crate](https://docs.rs/acc_shared_memory_rs) and the [Node wrapper](https://github.com/TFarish/acc-node-wrapper).
- **Later, not v1:** the broadcast API for rivals needs `broadcasting.json` in `Documents\Assetto Corsa Competizione\Config` ([RaceLab setup guide](https://docs.racelab.app/docs/first-steps/acc)). An earlier draft of this PRD wrote `broadcast.json`; the RaceLab guide uses `broadcasting.json`.

Sources: pages linked above, read from search results. Exact field lists still need to be checked against ACC's official shared memory documentation before the build.

## Local AI and voice

Code decides when to speak; the AI only decides how to say it. Voice runs on the CPU so the GPU stays free for the game. The reasons behind each choice are in the ADRs tab.

- **Runtime:** Ollama, which serves a local API on `localhost:11434`. Keep the model loaded between calls so the first call is not slow.
- **Model:** Qwen3 4B (`qwen3:4b`, about 3 GB) as the default test model, with `gemma3:4b` as the alternative. A test set picks the winner. Tags and sizes come from [this Ollama model list](https://docs.screenpi.pe/ollama); check them on ollama.com/library at download time. Final size depends on the GPU.
- **Thinking mode off:** Qwen3 can write out reasoning before answering, which would break the latency budget. Turn it off for radio calls with Ollama's `think` option (to verify in the Ollama docs).
- **Prompt:** a short system prompt sets the engineer persona. Input is a small event summary, not raw telemetry. Output is one sentence of at most about 15 words. A filter cuts anything longer.
- **Fallback:** if the model has not answered within the time limit, a canned line is spoken or the call is dropped.
- **Latency tiers:** canned calls play right away; AI calls get about 1.5 seconds. A late AI call is dropped, not spoken. These are targets to measure.
- **Voice engine:** Piper by default: CPU only and fast ([comparison](https://www.promptquorum.com/power-local-llm/piper-vs-kokoro-tts)). Kokoro is the optional higher-quality engine; it is heavier, with a measured first audio around 0.9 s on one Mac benchmark ([CodeSOTA](https://www.codesota.com/text-to-speech/piper-vs-kokoro)).
- **Voice setting:** Engineer voice = Female or Male, English only. Piper voices: female `en_US-amy-medium`, male `en_US-lessac-medium`. A short listening test confirms the choice. Kokoro has male and female English voices too, for example `am_michael`.
- **Canned audio cache:** fixed lines are rendered once per voice and reused, so they play instantly.
- **Call discipline:** one sentence per call, a cooldown per call type, and a priority queue so low-priority talk never blocks urgent calls.
- **Speech to text (phase 6):** local, push-to-talk only, so the microphone is never always listening.

## Dashboard UX

The dashboard is lightweight, glanceable, dark, with a maroon accent. It is a companion, not a launcher: the user starts ACC from Steam as usual and Pit Wall wakes up when game data appears.

**Principles**

- **Dark only in v1.** No light theme; less glare in a dim sim room, less to build.
- **Few elements, big numbers.** The driver looks for one second at most. No small text on the live screen.
- **Lightweight by design:** plain HTML, CSS and a little JavaScript, no heavy UI framework, system fonts, no images or video, simple fades only, updates at most 10 times per second.
- **Status first:** ACC waiting or connected, AI ready or loading, voice on or muted, always visible at the top.
- **Colour never works alone:** every status also has an icon and a word.
- **Respect reduced motion** and keep full keyboard use for settings.

**Screens**

| Screen | Holds |
| --- | --- |
| Live (default) | Status bar, four large tiles (fuel, tyres, last lap, best lap), subtitle strip, radio log |
| Coach | Latest tips, driver style summary of the session |
| Setup | Current draft: symptom, change, before and after value, laps of evidence, Apply and Reject; list of saved versions |
| Settings | Engineer voice (Female or Male), volume, call frequency, subtitles on or off, subtitle size and position, model, units, thresholds |

**Visual tokens (proposal)**

| Token | Value | Use |
| --- | --- | --- |
| Background | `#0F0D0E` | Page |
| Surface | `#181416` | Cards and tiles |
| Surface raised | `#211B1E` | Menus, selected rows |
| Border | `#2E2427` | Dividers |
| Text primary | `#F2ECEA` | Numbers and body |
| Text secondary | `#A69A9C` | Labels |
| Maroon | `#8B1E2D` | Primary buttons, active tab, tile accents |
| Maroon hover | `#A32638` | Hover and pressed |
| Maroon tint | `#E0707F` | Icons and highlights on dark |
| Good, warning, critical | `#4CAF7A`, `#E0A030`, `#E5484D` | Status, always with icon and text |

Maroon `#8B1E2D` with white text reads at about 9:1 contrast, but maroon alone on the dark page is only about 2:1, so it is used for fills and never for text; the lighter tint carries text and icons. These contrast values are my estimates and must be checked in the real UI.

**Type:** system font stack, tabular numerals for live numbers, large numbers about 48 to 64 px, labels 14 px, subtitle 28 to 32 px.

## Subtitles while driving

Showing each radio call as text at the top left of the screen is possible, in two stages.

1. **Stage 1, MVP:** a large subtitle strip in the dashboard. It works on a second monitor, phone or tablet. No game integration needed.
2. **Stage 2, phase 6:** a small overlay window at the top left over the game: transparent, always on top, and click-through. It is a separate lightweight process.

**Conditions for the overlay**

- ACC must not run in exclusive fullscreen. Windows does not let other windows draw over an exclusive fullscreen game ([explained here](https://www.th.gl/faq/overlay-not-visible-fullscreen)). RaceLab's ACC guide says to set Fullscreen to Disabled in the video options ([RaceLab](https://docs.racelab.app/docs/first-steps/acc)); the game still fills the screen.
- VR is not supported in v1; overlays reportedly do not show there, and the dashboard strip or the voice is the fallback.
- Tauri's click-through option has had problems on some Windows setups ([example issue](https://github.com/tauri-apps/tauri/issues/11461)), so phase 6 starts with a short test of the overlay in ACC. If it fails, the dashboard strip on a second screen stays.

**Behavior**

- Text appears when the voice starts and fades after about 4 seconds.
- Maximum two lines, high contrast on a semi-transparent dark maroon backing.
- Settings: on or off, size, opacity, position (top left by default).
- The text matches the spoken sentence exactly.

## Performance budget

Lightweight is a requirement, not a nice-to-have. The numbers below are starting targets, measured in phases 1 and 3.

| Area | Rule | Starting target |
| --- | --- | --- |
| Telemetry | Player car only, 10 reads per second, skip other cars' fields | No extra reads |
| UI updates | Send only changed values, at most 10 per second | One small WebSocket message per update |
| AI | Only on events, never while idle; small model; model stays loaded | No AI call at idle |
| Voice | CPU only, canned audio cached | First audio under 1 second for AI calls |
| GPU | The model must fit in the VRAM the game leaves free | Average ACC FPS drops 3% or less with Pit Wall running |
| Core process | No per-frame logging; one draft file write per lap | Under 5% total CPU and under 300 MB RAM, not counting Ollama |

**How to measure:** drive the same lap with the same graphics settings with and without Pit Wall, and compare average FPS from the game's FPS counter or any FPS tool, plus CPU and RAM from Windows Task Manager. Repeat with the AI model loaded.

## Memory plan

The AI model is the biggest memory user, so Pit Wall treats memory as a feature: pick small, load late, unload early, and show the numbers.

| Part | Memory use | Plan |
| --- | --- | --- |
| AI model | Largest. In GPU memory if the PC has a usable GPU, otherwise in RAM | Wizard picks the size from the PC check. The model loads when ACC connects and unloads when ACC closes. Ollama keeps a model loaded for 5 minutes by default; the `keep_alive` setting changes that, and 0 unloads at once ([example](https://oneuptime.com/blog/post/2026-01-27-ollama-local-llm-inference/view)). |
| Voice | Small; voice files are about 60 MB on disk | Load only the chosen voice. Canned audio is cached on disk, not kept in memory. |
| Core service | Small | Keep only the current lap and a rolling window of clean laps, never a full telemetry history. |
| App window | The system web view | Closing the window frees it; the core keeps running from the tray (to verify). |
| ACC | Large | Pit Wall must never compete with it. Weak PCs use Lite mode. |

**Memory modes**

- **Lite:** no AI model. Canned calls, coach rules and setup rules. Lowest memory use.
- **Standard:** the model loads when ACC connects and unloads when it closes. Default.
- **Always ready:** the model stays loaded between sessions for the fastest first call.

**Starting targets, to measure:** the app window, core and one voice together stay under 400 MB of RAM, not counting the AI model. The wizard shows each model's size before download. Settings shows live memory for the app, the core and Ollama, so a user can see where memory goes. Measure with Windows Task Manager, Details tab.

## Recommended stack

A Python core, a Tauri app shell that shows the web dashboard, and a Windows installer with a setup wizard. Python is the one language with ready libraries for ACC shared memory, Ollama and local voice. Tauri gives a real Windows app that uses the system web view instead of bundling a browser. Reasons and alternatives are in the ADRs tab.

| Layer | Choice | Why | Alternative |
| --- | --- | --- | --- |
| Telemetry | Python `pyaccsharedmemory` | One pip install, documented fields | [Node wrapper](https://github.com/TFarish/acc-node-wrapper), [Rust crate](https://docs.rs/acc_shared_memory_rs) |
| Core service | Python 3.11 or newer, FastAPI, WebSocket, frozen with PyInstaller as a sidecar | One process for telemetry, rules, AI, voice and files; the user needs no Python | Node |
| Event engine | Plain Python rules, thresholds in a config file | Fast, testable, no AI needed | None |
| AI | Ollama with Qwen3 4B, installed by the wizard | Easy local API on port 11434 | `gemma3:4b`, other small models |
| Voice | Piper (default), Kokoro (option), downloaded by the wizard | CPU only, female and male English voices | Windows built-in voices |
| Dashboard | HTML, CSS, vanilla JavaScript, shown in the app window | Light; the app window uses the system WebView2, which example projects report is preinstalled on Windows 10 and 11 | Svelte if the UI grows |
| Overlay | Second window of the same Tauri app, phase 6 | Transparent, always-on-top, click-through; no extra process | Electron, which bundles Chromium and Node ([example](https://github.com/eson39/talking-point)) |
| Packaging | Tauri bundle with the Python sidecar (`externalBin`) and an NSIS installer, per user, no admin rights | Documented pattern: [Tauri sidecar docs](https://v2.tauri.app/develop/sidecar/), [Python sidecar example](https://github.com/dieharders/example-tauri-v2-python-server-sidecar), [per-user installer example](https://github.com/SpaceSquare640/PokeTrack-App/wiki/Desktop-Shell) | Zip with `start.bat` for early tests |
| Tests | `pytest` and mock mode with recorded sample laps | Test without driving | None |

**Build notes**

- Build the sidecar with PyInstaller in onedir mode, not one-file. With one-file the shell only knows the launcher process, so it cannot stop the real child ([example notes](https://github.com/dieharders/example-tauri-v2-python-server-sidecar)).
- A Tauri NSIS setup once kept an old sidecar after a reinstall ([issue](https://github.com/tauri-apps/tauri/issues/15134)), so the installer test must include an upgrade.
- The developer machine needs Rust, Node and the Microsoft C++ build tools; the end user needs none of these ([example project](https://github.com/rusenbb/eduport)).
- Opening the dashboard from another device on the network is an optional setting, off by default.

## Installer and setup wizard

Pit Wall installs like a normal Windows program. After the install, a first-run wizard lets the user choose which AI model and which voices to download, with sizes shown, so nobody opens a terminal.

**Two stages**

1. **Install Pit Wall:** a standard Windows installer (NSIS) for the current user, no admin rights, with install folder and shortcuts.
2. **First-run wizard:** inside the app, in seven steps (below). It can be left and resumed, and re-opened later from Settings.

| Step | Screen | What the app does |
| --- | --- | --- |
| 1. System check | Welcome, what this PC offers | Reads Windows version, free disk, RAM, GPU name and VRAM (method to choose), whether the ACC Documents folder exists, and whether Ollama already answers on port 11434. Shows a Recommended choice. |
| 2. AI engine | Install Ollama for me, I already have it, or Skip AI | Downloads the official Ollama installer from ollama.com and runs it. Ollama's installer needs no admin rights, installs to the user's home folder by default, and accepts an install folder with the `/DIR` flag ([Ollama Windows docs](https://docs.ollama.com/windows)). Waits until port 11434 answers. |
| 3. AI model | Model cards | Cards show size on disk, memory use and speed. Starting candidates: a Light card (about 2 GB class, for example `ministral-3`, to test), Standard `qwen3:4b` (about 3 GB), and `gemma3:4b` (about 3 GB), sizes per [this list](https://docs.screenpi.pe/ollama). Optional model folder through the `OLLAMA_MODELS` setting from the Ollama docs. |
| 4. Voices | Female, Male or both, with a preview button | Downloads Piper voice files, about 60 MB each ([voice sizes](https://www.mintlify.com/yocxy2/RCLI/models/tts-models)). A More voices area holds Kokoro, with a note about its size and extra install on Windows ([setup snag](https://aivideosensei.com/guides/kokoro-tts-local-setup-guide)). |
| 5. Download | Progress, pause, cancel | Per-item and total progress, speed and time left. Model download uses Ollama's pull API, which streams progress per layer, so the app adds the layers up ([API notes](https://pkg.go.dev/github.com/richardwooding/file-search-on@v0.70.1/internal/embed)). Checks free disk first, resumes after a break, retries with a plain message. |
| 6. Test | Play and test | Plays a sample call in the chosen voice, runs one AI wording call and shows the reply time, and shows ACC as waiting. |
| 7. Finish | Summary | Lists what was installed with sizes. Options: start with Windows (off by default), open the dashboard. |

**UX rules**

- The Recommended path takes three clicks to start the downloads. Starting target: first test call heard within 10 minutes of launching the installer, not counting download time.
- Sizes and sources are shown before anything downloads. Nothing downloads without a click.
- Official sources only: ollama.com and the Piper voices repository ([voice list source](https://docs.sunfounder.com/projects/fusion-hat/en/latest/_modules/sunfounder_voice_assistant/tts/piper.html)). The source is named on screen.
- Plain words. An error says what happened and which button fixes it.
- Existing installs are detected and skipped.
- Skip AI is allowed: the app runs in Lite mode with canned calls. Skip voices is allowed: subtitles only.
- Same dark and maroon look as the dashboard, keyboard friendly, back and next on every step.

**After setup**

Settings has an AI and voices page: add or remove models and voices, see disk used per item, switch Lite mode, change the model folder. The uninstaller asks whether to remove downloaded voices. Ollama is uninstalled separately from Windows Apps.

**Known gaps**

- Silent-install flags for the Ollama installer are not verified; the fallback is to run its normal installer window.
- The first installers are unsigned, so Windows SmartScreen may warn. Code signing is a later task.
- Ollama also offers a standalone zip for embedding it in other apps ([docs](https://docs.ollama.com/windows)). Whether Pit Wall should manage Ollama itself with it is an open question.

## Risks and constraints

| Risk | Impact | Mitigation |
| --- | --- | --- |
| AI competes with ACC for the GPU | Frame drops during a race | Small model, test with the game running, option to run the AI on a second PC |
| AI latency | Late calls, wrong timing | Canned lines for time-critical calls, drop late AI calls, thinking mode off |
| Too much talking | Driver distraction, user mutes everything | One sentence calls, cooldowns, priority queue, call frequency setting |
| Raw telemetry sent to the AI | Slow and wasteful | Code detects events; the AI only sees a short event summary |
| Player car only | No gap or rival calls | Accepted for v1; rivals are in the Ideas backlog |
| Wrong setup advice | Slower car, lost trust | Human approval, one change at a time, check next stint, suggest reverting |
| ACC rejects or misreads a generated setup file | Setup unusable | Never overwrite the original, keep exact file layout, test per car, check value ranges |
| Per-lap draft is noisy | Flip-flopping proposals | Rolling window of clean laps, wait after each applied change |
| Overlay does not show | No top-left subtitles | Needs borderless or non-fullscreen mode; dashboard strip and voice as fallback; no VR in v1 |
| Voice quality or speed disappoints | Users switch it off | Piper default, Kokoro option, cached canned audio, listening test |
| Windows only shared memory | Dashboard on another device needs a relay | Core runs on the gaming PC and serves the dashboard over the local network |
| Voice engine licence | Piper's current engine repo is GPL-3.0 | Fine for personal testing; check before any distribution |
| Existing callout tools overlap | Less differentiation | Focus on conversation, driving coach and setup advisor; check existing tools before build |

## Milestones

&#91;embedded content: roadmap · 6 phases, 6 gates\]

Each phase ends at a gate; the next phase starts only when it passes. Phases 1 to 3 are the MVP. Gates are proposed exit criteria and no dates are set yet. The install and run guide tab shows how to test each gate.

## Ideas backlog

Not in v1, collected so they are not lost.

- **Session debrief:** after the session, a short summary of laps, tips and key moments
- **Tyre trends:** temperature and pressure trends per stint, with a warning before they go wrong
- **Weather alerts:** rain intensity and track grip are in the graphics data, so rain calls are possible
- **Fuel and pit planner:** fuel per lap, laps left, pit window timing
- **Lap delta and consistency chart:** against your best lap
- **Voice commands:** push-to-talk questions like how many laps of fuel are left
- **Personality and jokes:** only in calm moments, never on urgent calls, with a personality setting and a joke cooldown
- **Setup library:** all approved setup versions per car and track, with notes on what each fixed
- **Rival calls:** gaps and positions from the broadcast feed, once the player-car version runs cleanly
- **Stream mode:** radio calls and subtitles ready for streaming
- **Real-life use:** track day or karting with phone sensors or an OBD2 adapter; track only, never on public roads
- **Practice trainer:** pit stop and driver change practice with timing feedback

## Open questions

- [ ] What GPU and how much VRAM? The wizard detects it, but the model cards need real test values.
- [ ] Which ACC car and track first? The reference file is the Ferrari 296 GT3.
- [ ] Does the user play on a monitor or in VR? Overlay subtitles need a non-fullscreen monitor setup.
- [ ] Dashboard: app window on the gaming PC, second monitor, or another device on the network?
- [ ] Piper or Kokoro for the default voice? A short listening test decides.
- [ ] Exact ACC shared memory fields: verify against the official documentation.
- [ ] Valid setup value ranges for the Ferrari 296 GT3 and how ACC treats the derived values in a setup file.
- [ ] What can be changed on track or in the pits in ACC, and what needs the garage?
- [ ] Ollama: use its normal installer, or manage it inside Pit Wall with the standalone zip? Are silent-install flags available?
- [ ] Which Light model passes the AI test set?
- [ ] Check trademarks, domains and app stores for the name Pitwall Race Companion.
