# Pit Wall — notes for Claude

Local AI race engineer for Assetto Corsa Competizione (ACC). Full spec: [docs/PRD.md](docs/PRD.md). Read it before starting a new phase.

## Rules

- **GitHub account:** every push and merge uses `antoniowolkk`, no other account. Before any push or merge, run `gh api user --jq .login` and confirm it prints `antoniowolkk`; git identity is `Antonio <antonio@wolkk.com>`. If anything else is active, stop and ask.
- Work on a feature branch, open a PR to `main`, squash-merge. Never push straight to `main`.
- Run `uv run pytest` before every commit.
- v1 reads the **player car only**. No other cars' data (performance budget, see PRD).
- Dashboard stays lightweight: plain HTML/CSS/JS, system fonts, no images, simple fades only, every status shows icon + word (never colour alone), works at phone width.
- Thresholds live in `src/pitwall/thresholds.toml`, not in code.

## Commands

```bash
uv sync                                   # install (pulls pyaccsharedmemory on Windows only)
uv run pitwall                            # serve http://localhost:8765; ACC on Windows, mock elsewhere
uv run pitwall --source mock --mock-speed 4
uv run pitwall record recordings/x.jsonl  # Windows: record a real ACC session
uv run pitwall --replay recordings/x.jsonl
uv run pitwall --lan                      # allow phone/tablet on the network
uv run pitwall voices download            # Kokoro model + voices, 120 MB, to %LOCALAPPDATA%\PitWall\kokoro
uv run pitwall --voice female --volume 0.8 # male (default, bm_george), female (af_heart), off
uv run pitwall --engine piper             # fallback engine; run voices download --engine piper first
uv run pitwall lines generate             # ACC closed: qwen3:4b writes line variants (~5 min incl. voice)
uv run pitwall lines show                 # canned line (=) and AI variants (~) per call
uv run pytest
```

## Status (2026-10-27)

- **Phase 1 merged** (PR #1): core service, ACC reader, mock + replay, 10 Hz WebSocket, Live HUD dashboard. Built and tested on macOS with mock data only.
- **Phase 1 Windows checks merged** (PR #3). Open: yellow/blue flag sighting, FPS comparison without Pit Wall.
- **Phase 2 in progress** (`feat/phase2-event-engine`): event engine, canned calls, radio queue, subtitle strip and radio log done; checked live in a Monza race (5 sane calls). Voice done: Kokoro `bm_george` (British male) by default, casual lines, radio-style loudness; Piper kept as `--engine piper`. Confirmed OK by the user in a live race (volume, voice, timing). Merged as PR #4.
- **Phase 2 settings** (`feat/phase2-settings`): Settings screen (voice, engine, volume, test voice, which calls, subtitles on/off and size), saved to `%LOCALAPPDATA%\PitWall\settings.json`, applied live. Merged as PR #5.
- **Phase 2 pit window** (`feat/phase2-pit-window`): calls for window open, closing (2 min before), missed, and stop done. Confirmed live in a 20-minute race with a 10-minute window: open and stop-done calls came at the exact moments. Merged as PR #6.
- **Phase 3 AI wording** (`feat/phase3-ai-wording`): AI-written line variants, made ahead of time with Ollama `qwen3:4b`, picked per call (never the same twice in a row); Lite mode = canned lines only; AI pill. 61 checked lines generated. Not yet heard in a race. Merged as PR #7.
- **Track map** (`feat/track-map`): player position, lap fraction and inputs read from ACC; circuit outline learned from the first full lap and saved to `%LOCALAPPDATA%\PitWall\tracks\<track>.json`; official turns per track in `tracks.toml` (Spa done), else turns found from speed dips; Live screen shows the map, car dot, current turn and next turn with distance. Next: per-turn tips, then official turns for Monza and Nurburgring (needs a lap there).

## Phase 1 Windows checklist

Run `uv run pitwall`, open http://localhost:8765, start ACC from Steam.

- [x] Status shows ACC **Waiting** before a session, **Connected** once on track. (2026-10-27, Ferrari 296 GT3, Nürburgring 24h practice)
- [x] Speed, gear, RPM, fuel, fuel laps, tyre temps and pressures, lap count, current/last/best lap match the game. Checked against raw shared memory dumps; last/best lap after a 2:22.202 at Spa.
- [x] Gear mapping right (ACC physics gear: 0 = R, 1 = N, 2 = 1st). Raw gear 1 shows N.
- [ ] Flags show (yellow, blue, etc.) when the game shows them. Green, chequered, white and a 0.7 s orange flag (Monza lap 2, cause unknown) seen and called; yellow and blue not seen yet.
- [x] Pause > 5 s: does it flip to Waiting? No: both packed_ids keep ticking while paused. But physics reads as zeros then, so `AccSource` now holds the last live values and the dashboard shows ACC **Paused**.
- [x] Quit ACC: back to Waiting within ~5 s. Restart: reconnects without restarting Pit Wall. ACC writes `ACC_OFF` on exit, so Waiting is instant; restart reconnected on its own once the session went live. Session change (Nürburgring → Spa) works too.
- [x] Record a ~10 min session with `pitwall record` for future replay tests. Local only (gitignored): `recordings/spa_race.jsonl`, 5-lap race at Spa, 12:49, fuel usage off; `recordings/monza_race_fuel.jsonl`, 3-lap race at Monza, 7:07, real fuel (~3.1 L/lap) and damage. Use the Monza one for fuel calls.
- [ ] Check FPS impact (game counter, with and without Pit Wall) and CPU/RAM in Task Manager. Targets in PRD "Performance budget". CPU/RAM done: 0.04% CPU, ~48 MB working set while connected. FPS: 65 with Pit Wall running (Monza); the without-Pit-Wall run is skipped for now.
- [x] Tune tyre temp and pressure windows in `thresholds.toml` for the Ferrari 296 GT3. Kept 80-90 °C and 26.0-27.0 psi after the Spa race; reasons in the file.
- [ ] Optional: open the dashboard from a phone with `--lan`.

## Things learned while building

- `pyaccsharedmemory` 1.0.0 opens the named maps with `ACCESS_WRITE`, so they exist (all zeros, status ACC_OFF) even when ACC is not running, and our open handle keeps the **last values alive after ACC exits**. On a clean quit ACC does write `ACC_OFF` first (verified); the 5 s stale check stays as the fallback for a crash. So `AccSource` treats frozen physics + graphics `packed_id` for 5 s as disconnected. Verified: both ids keep ticking while paused.
- While paused (`ACC_PAUSE`) ACC zeroes physics: fuel 0, gear R, rpm 0, tyres 0. `acc.hold_on_pause` keeps the last live snapshot with `status="pause"`, so later event rules must not see a fake empty tank. If Pit Wall starts while the game is paused there is nothing to hold, and zeros pass through until you resume.
- `AccSource` reads the maps with the module's `read_physic_map` / `read_graphics_map` / `read_static_map` directly, not `read_shared_memory()`, which returns None whenever physics is unchanged and deep-copies every call.
- ACC reports "no lap time" as INT32_MAX; `models.lap_ms` maps it to None.
- Before you click Drive (grid screen) ACC reports `ACC_LIVE` but physics is all zeros: fuel 0, gear R, tyres 0 psi (30 s at Monza). `acc.on_track` needs non-zero tyre pressures, else the source returns None (Waiting), so no fake empty tank reaches the event engine.
- In a race at Spa, ACC's `best_time` stayed on lap 1 (3:46) all race though every lap was valid; at Monza it updated normally. `acc.BestLap` tracks the best from completed valid laps and keeps the lower of the two.
- In that race fuel stayed at 56.0 L all race because fuel usage was off in the race settings (confirmed). So `spa_race.jsonl` is no good for testing fuel calls; record another session with fuel usage on for that.
- Voice choice (2026-10-27): Piper sounded too quiet and robotic in a race; after a listening test against Kokoro `af_heart`, `am_michael` and Piper amy, the pick was Kokoro `bm_george`. Kokoro int8 (`kokoro-onnx` 0.6.1) loads in ~3 s but renders a line in ~2.8 s even on all 8 cores (2 threads: ~3.2 s). So every fixed line plus the fuel warning for 1-3 laps is pre-rendered at startup (cached on disk after the first run), the subtitle is sent when the voice starts, not when the call fires, and the voice queue is urgent-first with non-urgent lines dropped after 15 s. Process: ~240 MB working set / 536 MB private, ~9% CPU while rendering. The fp32/fp16 Kokoro models are untried and may render faster on CPU.
- Loudness: TTS only normalises the peak, so speech was quiet under engine noise. `voice.radio_loud` soft-clips (tanh, drive 3): about +9.5 dB average level. Numbers go to the voice as words (`events.words`, `spoken_lap`: "one fifty point two"); the subtitle shows the same words.
- Piper (`piper-tts` 1.8, GPL-3.0, fine for personal use, check before distributing): model load ~10 s, so it runs in the voice thread at startup (pill shows Loading). `PiperVoice.load` uses default onnxruntime options (big memory arena, all cores); `voice.piper_synth` builds the session itself with the arena off and 2 threads: 164 MB working set / 365 MB private instead of 268 / 629, and a new line still renders in 0.4-0.6 s. Lines without placeholders are pre-rendered; every spoken line is cached as WAV in `%LOCALAPPDATA%\PitWall\cache`.
- Voice plays on the gaming PC (`winsound`, blocking, in the voice thread), not in the browser: no autoplay limits, and the driver hears it in the headset. A dashboard on a phone shows subtitles only.
- Pit window (2026-10-28, 5-minute Monza race, no mandatory stop set): `session_time_left` is **ms** (300000 at the start, 0 at the flag, frozen before the start). `pit_window_start`/`end` (static) are ms too and centred on half distance: 150500 / 149500, start + end = race length. start >= end means no window; that race also had `mandatory_pit_done` true and `missing_mandatory_pits` 0 throughout. So `events._pit_window` uses elapsed = start + end - time left (works when joining mid-race). Confirmed in a 20-minute race with a 10-minute window (`recordings/pit_stop_race.jsonl`): window 300000-900000 (minutes 5-15), start + end = 1200000 = race length; at the stop `missing_mandatory_pits` 1 -> 0 and `mandatory_pit_done` false -> true flip in the same frame, as the car leaves the box. `recordings/pit_window_test.jsonl` is the 5-minute race without a window.
- Phase 3 design (2026-10-28, user's choice): this PC is a GTX 1650 4 GB laptop (i5-9300H, 16 GB). With ACC open, 3.67 GB VRAM is used, so a live LLM would run on the CPU: ~3-5 s per call for qwen3:4b and CPU bursts mid-race. So the PRD's live rewording became pre-generation: `pitwall lines generate` (ACC closed, GPU free) asks Ollama for variants of every calls.toml line, filters them (`wording.check`), saves `%LOCALAPPDATA%\PitWall\lines.json` and pre-renders them with the voice. No AI runs during a race. qwen3:4b takes 3.5 GB with context, so ~66% GPU / 34% CPU; a run is ~2-3 min of AI plus ~3 min of Kokoro.
- qwen3:4b line quality: the first run invented facts ("take the win"), gave bad advice ("slow down", "fix it quickly"), went robotic ("problem detected") and emitted U+FFFD. The prompt now asks for the relaxed tone and no new facts or advice; `wording.check` rejects digits, non-ASCII, dashes, colons, a sentence starting with a placeholder, pronouns and a banned-word list. `load()` re-checks saved lines, so tightening the filter needs no regeneration. Second run: 61 of 66 kept. Read `pitwall lines show` after every generate.
- Track map (2026-10-28, Spa, Porsche 992 GT3 R, `recordings/spa_map.jsonl`): `normalized_car_position` is 0 at the start/finish line and is the same spline for every car, so turn positions in `tracks.toml` hold for any car. Player x/y = `car_coordinates[i]` x and z where `car_id[i] == player_car_id` (y is height). Plotted with SVG y = ACC z (no flip) the turns come out with the right hand (clockwise Spa, La Source a right). The outline from one 10 Hz lap measures 6.93 km (official 7.004). In the pit lane ACC still reports a lap fraction, so the map skips pit lane samples. ACC's `wheel_slip` is about 1 under normal hard braking and spikes to 3-5 on a lockup (front-right at Bruxelles on the out lap).
- Static strings (`car_model`, `track`) come NUL-padded to their buffer size; `acc._text` cuts at the first NUL.
- The dashboard renders via `requestAnimationFrame`, so a hidden tab or pane freezes the view until it is visible again. Data is still received; it catches up at once. Not a bug, but don't judge freshness from a background tab.
- `docs/reference/BGK_RACE.json` has **LF** line endings and tabs; the PRD says ACC setup files use CRLF. Check a file saved by ACC itself before writing the phase 5 setup writer.
- Mock track is tuned (ACCEL 3.0 m/s²) for ~1:40 laps; tests rely on 95–120 s lap times.

## Layout

```
src/pitwall/
  __main__.py          CLI (serve, record)
  config.py            Settings, thresholds loader
  models.py            Snapshot (player car), wire format, diff
  poller.py            poll loop, connect/disconnect, sends only changes, runs the radio
  track.py             track map: outline learned per track, official turns (tracks.toml) or found from speed dips
  events.py            event engine (fuel, flags, tyres, best lap) and Radio priority queue
  calls.toml           canned radio lines and priorities
  wording.py           AI line variants: Ollama prompt, checks, lines.json, generate/show CLI
  voice.py             TTS (Kokoro default, Piper fallback): model load, WAV cache, priority playback thread, downloads
  server.py            FastAPI: static dashboard, /ws, /api/status
  telemetry/acc.py     ACC shared memory (Windows)
  telemetry/mock.py    synthetic laps
  telemetry/replay.py  JSON Lines recorder / replayer
  web/                 index.html, styles.css, app.js
tests/                 pytest
docs/                  PRD, reference setup file
```

WebSocket messages: `config` (thresholds, once), `status` (acc/ai/voice; ai = ready, missing, lite), `telemetry` (`data` = changed fields only; full snapshot on connect), `radio` (one call: kind, priority, text, lap, time), `radio_log` (last 20 calls, on connect), `settings` (driver prefs + which voices are downloaded; on connect and after every change), `track` (track name, outline points or null while learning, turns with number/name/start/end, length_m, official; on connect and when the map changes).

HTTP: `GET /api/status`, `GET /api/settings`, `PUT /api/settings` (partial JSON, 400 on a bad value), `POST /api/voice/test` (radio check, 409 when the voice is off).

Settings: `config.Prefs` is the single source; `settings.json` is loaded at start and CLI flags (`--voice`, `--engine`, `--volume`) override it for that run only. Changing voice or engine builds a new `Voice` and swaps it into the poller; volume is applied at play time (cache stays full volume, no re-render); "which calls" filters by priority after the engine runs, so cooldowns stay right.

Radio rules: code decides when, `calls.toml` says what. Calls are silent while paused. Tyre calls need the problem to last `tyre_sustain_s`; every kind has a cooldown in `thresholds.toml [calls]`. Urgent (priority 1) skips the gap between calls; queued calls older than `expire_s` are dropped. The two race recordings give 5 calls each, a sane rate; replay them through `EventEngine` when changing rules.
