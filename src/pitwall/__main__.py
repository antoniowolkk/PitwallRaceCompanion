"""Command line entry point.

    pitwall                         serve the dashboard (ACC on Windows, mock elsewhere)
    pitwall --source mock           serve with synthetic telemetry
    pitwall --source replay --replay laps.jsonl
    pitwall record laps.jsonl       record a real ACC session (Windows)
    pitwall --voice female          engineer voice: male (default), female, off
    pitwall --engine piper          faster, flatter voice engine (default kokoro)
    pitwall voices download         fetch the voice model (Kokoro 120 MB, Piper 63 MB a voice)
    pitwall lines generate          AI-write varied radio lines with Ollama (run when not racing)
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import Prefs, Settings, data_dir


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .server import create_app

    # Saved Settings-screen choices, with any flag given on the command line on top for this run.
    prefs_path = data_dir() / "settings.json"
    flags = {"voice": args.voice, "volume": args.volume, "engine": args.engine}
    prefs = Prefs.load(prefs_path).updated({k: v for k, v in flags.items() if v is not None})
    from . import wording
    from .events import load_calls

    variants = wording.load(wording.lines_path(), load_calls())
    settings = Settings(
        poll_hz=args.hz,
        port=args.port,
        host="0.0.0.0" if args.lan else "127.0.0.1",
        mock_speed=args.mock_speed,
        replay_path=args.replay,
        prefs=prefs,
        prefs_path=prefs_path,
        variants=variants,
        tracks_dir=data_dir() / "tracks",
    )
    if args.source:
        settings.source = args.source
    elif args.replay:
        settings.source = "replay"
    app = create_app(settings)
    print(f"Pit Wall dashboard: http://{'localhost' if not args.lan else settings.host}:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


def _record(args: argparse.Namespace) -> None:
    from .telemetry.acc import AccSource
    from .telemetry.replay import Recorder

    source = AccSource()
    period = 1.0 / args.hz
    frames = 0
    print(f"Recording to {args.out}. Waiting for ACC... Ctrl+C to stop.")
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            rec = Recorder(f)
            while True:
                snap = source.read()
                if snap is not None:
                    rec.write(snap)
                    frames += 1
                time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        source.close()
        print(f"Saved {frames} frames.")


def _voices(args: argparse.Namespace) -> None:
    from .voice import VOICES, download, model_files

    for choice in VOICES[args.engine] if args.which == "all" else [args.which]:
        files = model_files(args.engine, choice)
        if all(f.exists() for f in files):
            print(f"{args.engine} {choice}: already in {files[0].parent}")
            continue
        print(f"{args.engine} {choice}: downloading {VOICES[args.engine][choice]} ...")
        print(f"{args.engine} {choice}: saved to {download(args.engine, choice)[0].parent}")


def _lines(args: argparse.Namespace) -> None:
    from . import wording
    from .events import load_calls
    from .server import warm_lines
    from .voice import Voice

    calls = load_calls()
    path = wording.lines_path()
    if args.action == "show":
        variants = wording.load(path, calls)
        for kind, line in calls.items():
            print(f"{kind}:\n  = {line['text']}")
            for text in variants.get(kind, []):
                print(f"  ~ {text}")
        return

    models = wording.ollama_models()
    if models is None:
        raise SystemExit("Ollama is not running. Start it from the Start menu (or `ollama serve`) and try again.")
    if not any(m == args.model or m == f"{args.model}:latest" for m in models):
        raise SystemExit(f"Model {args.model} is not installed. Run: ollama pull {args.model}")
    print(f"Writing {args.count} variants of {len(calls)} radio calls with {args.model} ...")
    data = wording.generate(calls, n=args.count, model=args.model)
    wording.unload(args.model)  # give the memory back before anything else runs
    wording.save(data, path)
    total = sum(len(v) for v in data["variants"].values())
    print(f"Saved {total} lines to {path}")

    prefs = Prefs.load(data_dir() / "settings.json")
    voice = Voice(prefs.voice, prefs.volume, engine=prefs.engine)
    if voice.status != "loading":
        print(f"Voice is {voice.status}; skipping pre-render.")
        return
    lines = warm_lines(Settings(prefs=prefs), wording.load(path, calls))
    print(f"Pre-rendering {len(lines)} lines with {voice.engine} {voice.voice_id} (new ones take a few seconds each) ...")
    new = voice.render_all(lines, lambda done, n: print(f"  {done}/{n}", end="\r", flush=True))
    print(f"\nDone: {new} new voice lines cached.")


def _tracks(args: argparse.Namespace) -> None:
    from .track import download_lovely

    root = data_dir() / "tracks"
    print("Downloading turn names for every ACC track from Lovely Track Data (CC BY-NC-SA 4.0, lsr.gg) ...")
    print(f"Saved {download_lovely(root)} tracks to {root / 'lovely'}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="pitwall", description="Pit Wall race engineer core")
    p.add_argument("--source", choices=["acc", "mock", "replay"])
    p.add_argument("--replay", type=Path, help="recording for --source replay")
    p.add_argument("--mock-speed", type=float, default=1.0, help="time multiplier for mock and replay")
    p.add_argument("--hz", type=float, default=10.0, help="telemetry polls per second")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lan", action="store_true", help="allow other devices on the network")
    p.add_argument("--voice", choices=["female", "male", "off"], help="engineer voice (default: saved setting, male)")
    p.add_argument("--engine", choices=["kokoro", "piper"], help="voice engine (default: saved setting, kokoro)")
    p.add_argument("--volume", type=float, help="voice volume, 0 to 1 (default: saved setting, 1)")
    p.set_defaults(func=_serve)

    sub = p.add_subparsers()
    r = sub.add_parser("record", help="record ACC telemetry to a JSON Lines file")
    r.add_argument("out", type=Path)
    r.add_argument("--hz", type=float, default=10.0)
    r.set_defaults(func=_record)

    v = sub.add_parser("voices", help="manage the engineer voices")
    v.add_argument("action", choices=["download"])
    v.add_argument("which", choices=["female", "male", "all"], nargs="?", default="all")
    v.add_argument("--engine", choices=["kokoro", "piper"], default="kokoro")
    v.set_defaults(func=_voices)

    w = sub.add_parser("lines", help="AI-written variants of the radio lines (needs Ollama)")
    w.add_argument("action", choices=["generate", "show"])
    w.add_argument("--model", default="qwen3:4b")
    w.add_argument("--count", type=int, default=5, help="variants per call")
    w.set_defaults(func=_lines)

    t = sub.add_parser("tracks", help="turn names for every ACC track")
    t.add_argument("action", choices=["download"])
    t.set_defaults(func=_tracks)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
