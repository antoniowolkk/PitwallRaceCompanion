"""Command line entry point.

    pitwall                         serve the dashboard (ACC on Windows, mock elsewhere)
    pitwall --source mock           serve with synthetic telemetry
    pitwall --source replay --replay laps.jsonl
    pitwall record laps.jsonl       record a real ACC session (Windows)
    pitwall --voice male            speak calls with the male voice (female, male, off)
    pitwall voices download all     fetch the Piper voices (~63 MB each)
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import Settings


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .server import create_app

    settings = Settings(
        poll_hz=args.hz,
        port=args.port,
        host="0.0.0.0" if args.lan else "127.0.0.1",
        mock_speed=args.mock_speed,
        replay_path=args.replay,
        voice=args.voice,
        volume=args.volume,
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
    from .voice import VOICES, download, model_path

    for choice in VOICES if args.which == "all" else [args.which]:
        if model_path(choice).exists():
            print(f"{choice}: already at {model_path(choice)}")
            continue
        print(f"{choice}: downloading {VOICES[choice]} (~63 MB) from huggingface.co/rhasspy/piper-voices ...")
        print(f"{choice}: saved {download(choice)}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="pitwall", description="Pit Wall race engineer core")
    p.add_argument("--source", choices=["acc", "mock", "replay"])
    p.add_argument("--replay", type=Path, help="recording for --source replay")
    p.add_argument("--mock-speed", type=float, default=1.0, help="time multiplier for mock and replay")
    p.add_argument("--hz", type=float, default=10.0, help="telemetry polls per second")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lan", action="store_true", help="allow other devices on the network")
    p.add_argument("--voice", choices=["female", "male", "off"], default="female", help="engineer voice")
    p.add_argument("--volume", type=float, default=0.8, help="voice volume, 0 to 1")
    p.set_defaults(func=_serve)

    sub = p.add_subparsers()
    r = sub.add_parser("record", help="record ACC telemetry to a JSON Lines file")
    r.add_argument("out", type=Path)
    r.add_argument("--hz", type=float, default=10.0)
    r.set_defaults(func=_record)

    v = sub.add_parser("voices", help="manage the Piper voices")
    v.add_argument("action", choices=["download"])
    v.add_argument("which", choices=["female", "male", "all"], nargs="?", default="all")
    v.set_defaults(func=_voices)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
