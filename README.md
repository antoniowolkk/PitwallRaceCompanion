# Pitwall Race Companion

Local AI race engineer for Assetto Corsa Competizione (ACC). Reads live player-car telemetry, shows a lightweight dark dashboard, and (from phase 2) speaks short radio calls. Everything runs on your PC; no cloud.

Product spec: [docs/PRD.md](docs/PRD.md).

## Status

Phase 1 done: core service, ACC shared memory reader, mock and replay modes, Live dashboard.
Phase 2 done: event engine, casual radio calls with subtitles and radio log, the Kokoro engineer voice, a Settings screen and pit window calls.
Phase 3 in progress: AI-written variants of every radio line, made ahead of time with a local [Ollama](https://ollama.com) model so nothing AI runs during a race; Lite mode keeps the fixed lines.

## Run

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pitwall voices download      # engineer voice model, 120 MB (once)
uv run pitwall                      # ACC on Windows, mock telemetry elsewhere
```

Open http://localhost:8765. Start ACC from Steam as usual; Pit Wall connects when a session goes live. Voice, volume, which calls you hear and subtitles are on the Settings tab and are remembered.

Options:

```bash
uv run pitwall --source mock --mock-speed 4      # synthetic laps, 4x time
uv run pitwall record recordings/barcelona.jsonl # Windows: record a real session
uv run pitwall --replay recordings/barcelona.jsonl
uv run pitwall --lan                             # let a tablet or phone on the network connect
uv run pitwall --voice female --volume 0.8       # voice: male (default), female, off
uv run pitwall --engine piper                    # lighter, faster, flatter voice engine
uv run pitwall lines generate                    # with ACC closed: AI-write varied radio lines (needs Ollama + qwen3:4b)
```

The voice is [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (`bm_george` male, `af_heart` female), run on the CPU and played on the PC. [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0) is the lighter fallback.

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
