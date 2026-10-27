# Pitwall Race Companion

Local AI race engineer for Assetto Corsa Competizione (ACC). Reads live player-car telemetry, shows a lightweight dark dashboard, and (from phase 2) speaks short radio calls. Everything runs on your PC; no cloud.

Product spec: [docs/PRD.md](docs/PRD.md).

## Status

Phase 1 in progress: core service, ACC shared memory reader, mock and replay modes, Live dashboard.

## Run

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pitwall                    # ACC on Windows, mock telemetry elsewhere
```

Open http://localhost:8765. Start ACC from Steam as usual; Pit Wall connects when a session goes live.

Options:

```bash
uv run pitwall --source mock --mock-speed 4      # synthetic laps, 4x time
uv run pitwall record recordings/barcelona.jsonl # Windows: record a real session
uv run pitwall --replay recordings/barcelona.jsonl
uv run pitwall --lan                             # let a tablet or phone on the network connect
```

## Test

```bash
uv run pytest
```

## Layout

```
src/pitwall/
  telemetry/acc.py     ACC shared memory (pyaccsharedmemory), Windows only
  telemetry/mock.py    synthetic laps
  telemetry/replay.py  JSON Lines recorder and replayer
  poller.py            10 Hz poll, connect/disconnect, sends only changed values
  server.py            FastAPI: dashboard + /ws WebSocket + /api/status
  thresholds.toml      tyre and fuel windows (to tune)
  web/                 plain HTML, CSS, JS dashboard
docs/
  PRD.md
  reference/BGK_RACE.json   Ferrari 296 GT3 reference setup
```
