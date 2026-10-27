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
uv run pytest
```

## Status (2026-10-27)

- **Phase 1 merged** (PR #1): core service, ACC reader, mock + replay, 10 Hz WebSocket, Live HUD dashboard. Built and tested on macOS with mock data only.
- **Not yet done:** Phase 1 test on Windows with ACC running (checklist below). This is the Phase 1 gate.
- **Next:** Phase 2 — event engine, canned calls, Piper voice, subtitles and radio log, settings screen, Lite mode.

## Phase 1 Windows checklist

Run `uv run pitwall`, open http://localhost:8765, start ACC from Steam.

- [x] Status shows ACC **Waiting** before a session, **Connected** once on track. (2026-10-27, Ferrari 296 GT3, Nürburgring 24h practice)
- [x] Speed, gear, RPM, fuel, fuel laps, tyre temps and pressures, lap count, current/last/best lap match the game. Checked against raw shared memory dumps; last/best lap after a 2:22.202 at Spa.
- [x] Gear mapping right (ACC physics gear: 0 = R, 1 = N, 2 = 1st). Raw gear 1 shows N.
- [ ] Flags show (yellow, blue, etc.) when the game shows them. Green, chequered and a 0.7 s orange flag (Monza lap 2, cause unknown) seen in recordings; yellow and blue not seen yet.
- [x] Pause > 5 s: does it flip to Waiting? No: both packed_ids keep ticking while paused. But physics reads as zeros then, so `AccSource` now holds the last live values and the dashboard shows ACC **Paused**.
- [x] Quit ACC: back to Waiting within ~5 s. Restart: reconnects without restarting Pit Wall. ACC writes `ACC_OFF` on exit, so Waiting is instant; restart reconnected on its own once the session went live. Session change (Nürburgring → Spa) works too.
- [x] Record a ~10 min session with `pitwall record` for future replay tests. Local only (gitignored): `recordings/spa_race.jsonl`, 5-lap race at Spa, 12:49, fuel usage off; `recordings/monza_race_fuel.jsonl`, 3-lap race at Monza, 7:07, real fuel (~3.1 L/lap) and damage. Use the Monza one for fuel calls.
- [ ] Check FPS impact (game counter, with and without Pit Wall) and CPU/RAM in Task Manager. Targets in PRD "Performance budget". CPU/RAM done: 0.04% CPU, ~48 MB working set while connected. FPS still to check.
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
  poller.py            poll loop, connect/disconnect, sends only changes
  server.py            FastAPI: static dashboard, /ws, /api/status
  telemetry/acc.py     ACC shared memory (Windows)
  telemetry/mock.py    synthetic laps
  telemetry/replay.py  JSON Lines recorder / replayer
  web/                 index.html, styles.css, app.js
tests/                 pytest
docs/                  PRD, reference setup file
```

WebSocket messages: `config` (thresholds, once), `status` (acc/ai/voice), `telemetry` (`data` = changed fields only; full snapshot on connect).
