"""LLM attachment preparation: explicit input, no uploads, verifiable copies."""

import shutil
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from .files import child_file, read_json, save_json, sha256_file

TEMPLATES = Path(__file__).parent / "templates"
ATTACHMENTS = (
    "transcript.txt",
    "meeting_context.txt",
    "MOM_RULES.md",
    "GENERATE_MINUTES_PROMPT.md",
    "REVIEW_MINUTES_PROMPT.md",
)


def init_context(args: Namespace) -> None:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write((TEMPLATES / "meeting_context.txt").read_text(encoding="utf-8"))


def bundle(args: Namespace) -> None:
    transcript = args.transcript.read_text(encoding="utf-8-sig")
    context = args.context.read_text(encoding="utf-8-sig")
    if not transcript.strip() or not context.strip():
        raise ValueError("Transcript and context must not be empty")
    job_file = args.transcript.parent / "transcription-job.json"
    if job_file.exists():
        job = read_json(job_file)
        if job.get("status") != "complete":
            raise ValueError("Transcription job is incomplete; resume it before bundling")
        expected = job.get("artifacts", {}).get(args.transcript.name)
        if expected and expected != sha256_file(args.transcript):
            raise ValueError(
                "Transcript was edited after generation. Keep edits as a separately named reviewed transcript with documented corrections."
            )
    args.out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.transcript, args.out / "transcript.txt")
    shutil.copyfile(args.context, args.out / "meeting_context.txt")
    for name in ATTACHMENTS[2:]:
        shutil.copyfile(TEMPLATES / name, args.out / name)
    hashes = {name: sha256_file(args.out / name) for name in ATTACHMENTS}
    save_json(
        args.out / "bundle.json",
        {
            "schema": 1,
            "sha256": hashes,
            "created_utc": datetime.now(UTC).isoformat(),
            "external_transcript": not job_file.exists(),
            "notice": "No upload performed. Review content and destination approval before manually attaching files. Hashes are not signatures.",
        },
    )
    print(f"Attachments and prompts saved to {args.out}; nothing uploaded.")


def verify_bundle(args: Namespace) -> None:
    record = read_json(child_file(args.input, "bundle.json"))
    hashes = record.get("sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(ATTACHMENTS):
        raise ValueError("Invalid bundle manifest or unexpected attachment list")
    for name, expected in hashes.items():
        if sha256_file(child_file(args.input, name)) != expected:
            raise ValueError(f"Bundle hash mismatch: {name}")
    print(
        "All five attachment hashes match. This verifies consistency, not publisher authenticity."
    )
