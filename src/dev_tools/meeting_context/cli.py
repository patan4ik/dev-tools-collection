"""Record locally, transcribe offline, and prepare evidence for reviewed minutes."""

import argparse
import math
from pathlib import Path

from .files import local_path
from .models import download_model
from .packaging import bundle, init_context, verify_bundle
from .recording import devices, record
from .review import review
from .transcription import transcribe


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def chunk_duration(value: str) -> float:
    number = positive(value)
    if number < 1 or number > 600:
        raise argparse.ArgumentTypeError("must be between 1 and 600 seconds")
    return number


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="List Windows recording endpoints").set_defaults(func=devices)
    p = sub.add_parser("record", help="Capture selected output and microphone")
    p.add_argument("--out", type=local_path, required=True)
    p.add_argument("--loopback", type=int, help="Device index; default Windows output if omitted")
    mic = p.add_mutually_exclusive_group()
    mic.add_argument(
        "--mic",
        nargs="?",
        const="default",
        default="default",
        help="Microphone index; defaults to Windows microphone",
    )
    mic.add_argument("--no-mic", dest="mic", action="store_const", const=None)
    p.add_argument("--seconds", type=positive, help="Stop automatically; otherwise Ctrl+C")
    p.add_argument("--chunk-seconds", type=chunk_duration, default=300)
    p.add_argument(
        "--min-free-mb",
        type=positive,
        default=512,
        help="Stop when free disk falls below this reserve (MiB)",
    )
    p.set_defaults(func=record)
    p = sub.add_parser("download-model", help="Explicit ONLINE provisioning; reads no meeting data")
    p.add_argument("--repo", default="Systran/faster-whisper-small.en")
    p.add_argument("--revision", required=True, help="Reviewed model commit: full 40-character SHA")
    p.add_argument("--out", type=local_path, required=True)
    p.set_defaults(func=download_model)
    p = sub.add_parser("transcribe", help="OFFLINE ASR from a recording directory or audio file")
    p.add_argument("input", type=local_path)
    p.add_argument("--out", type=local_path, required=True)
    p.add_argument(
        "--model",
        type=local_path,
        default=Path("models/small.en"),
        help="Local CTranslate2 model directory",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Compatibility flag: transcription is always offline",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed files if audio/model/settings match",
    )
    p.add_argument(
        "--language",
        default="en",
        help="Source language code or auto (multilingual model needed)",
    )
    p.add_argument(
        "--task",
        choices=("transcribe", "translate"),
        default="transcribe",
        help="translate produces English from another language",
    )
    p.add_argument(
        "--hotwords",
        type=local_path,
        help="UTF-8 file of domain terms; max 2000 characters",
    )
    p.add_argument(
        "--initial-prompt",
        type=local_path,
        help="UTF-8 ASR context hint file; max 2000 characters",
    )
    p.add_argument(
        "--no-vad",
        action="store_true",
        help="Disable silence filtering when investigating missed speech",
    )
    p.set_defaults(func=transcribe)
    p = sub.add_parser("review", help="Make an offline HTML transcript with source playback links")
    p.add_argument(
        "--transcript",
        type=local_path,
        required=True,
        help="transcript.json from this tool",
    )
    p.add_argument("--audio-dir", type=local_path, required=True)
    p.add_argument(
        "--out",
        type=local_path,
        required=True,
        help="New directory; includes copies of source audio",
    )
    p.set_defaults(func=review)
    p = sub.add_parser("init-context")
    p.add_argument("--out", type=local_path, required=True)
    p.set_defaults(func=init_context)
    p = sub.add_parser(
        "bundle", help="Prepare local files for manual attachment to your approved LLM"
    )
    p.add_argument("--transcript", type=local_path, required=True)
    p.add_argument("--context", type=local_path, required=True)
    p.add_argument("--out", type=local_path, required=True)
    p.set_defaults(func=bundle)
    p = sub.add_parser("verify-bundle", help="Check bundle files against their recorded hashes")
    p.add_argument("input", type=local_path)
    p.set_defaults(func=verify_bundle)
    return parser


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        cli.exit(
            130,
            "Interrupted. Completed transcription files can be reused with --resume.\n",
        )
    except (OSError, ValueError, RuntimeError, ImportError, LookupError) as exc:
        cli.exit(1, f"Error: {exc}\n")
