"""Resumable single-worker ASR with content-bound checkpoints and local models."""

import os
from argparse import Namespace
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import __version__
from .files import (
    AUDIO_SUFFIXES,
    atomic_text,
    child_file,
    finite_number,
    job_lock,
    local_path,
    read_json,
    save_json,
    sha256_file,
    timestamp,
)
from .models import describe_model

LIMITATIONS = (
    "ASR is unverified. Track labels are not speaker identities. Cross-track timing is approximate."
)


@dataclass(frozen=True)
class TranscriptionSettings:
    language: str = "en"
    task: str = "transcribe"
    hotwords: str = ""
    initial_prompt: str = ""
    vad: bool = True

    def validate(self) -> None:
        if self.task not in ("transcribe", "translate"):
            raise ValueError("task must be transcribe or translate")
        if self.language != "auto" and (
            not self.language.isalpha() or not 2 <= len(self.language) <= 3
        ):
            raise ValueError("language must be a language code such as en or auto")
        if len(self.hotwords) > 2000 or len(self.initial_prompt) > 2000:
            raise ValueError(
                "Vocabulary hints and initial prompt must each be at most 2000 characters"
            )


def engine_versions() -> dict[str, str]:
    result = {}
    for name in ("faster-whisper", "ctranslate2", "av", "tokenizers", "onnxruntime"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not installed"
    return result


def collect_sources(input_path: Path) -> tuple[Path, list[dict[str, Any]], list[str]]:
    input_path = local_path(input_path)
    warnings = []
    if input_path.is_dir():
        manifest = read_json(input_path / "recording.json")
        if manifest.get("version") not in (1, 2):
            raise ValueError("Unsupported recording manifest version")
        entries = manifest.get("files")
        if not isinstance(manifest.get("warnings", []), list):
            raise ValueError("Invalid recording warnings")
        warnings = list(manifest.get("warnings", []))
        if manifest.get("status") != "complete":
            warnings.append("Recording was not marked complete; check for missing audio.")
        base = input_path
    else:
        entries = [{"file": input_path.name, "track": "audio", "offset_seconds": 0}]
        base = input_path.parent
    if not isinstance(entries, list) or not entries:
        raise ValueError("No recorded audio files found")
    sources, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid recording source")
        name = entry.get("file")
        if not isinstance(name, str):
            raise ValueError("Audio filename must be text")
        path = child_file(base, name)
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            raise ValueError(f"Unsupported audio file extension: {path.suffix}")
        if path.name.casefold() in seen:
            raise ValueError("Duplicate audio file in recording manifest")
        seen.add(path.name.casefold())
        offset = finite_number(entry.get("offset_seconds"), "offset_seconds")
        track = entry.get("track")
        if not isinstance(track, str) or not track.strip() or len(track) > 100:
            raise ValueError("Invalid audio track label")
        digest = sha256_file(path)
        if entry.get("sha256") and entry["sha256"] != digest:
            raise ValueError(f"Audio hash mismatch: {path.name}")
        source: dict[str, Any] = {
            "file": path.name,
            "track": track,
            "offset_seconds": offset,
            "sha256": digest,
            "bytes": path.stat().st_size,
        }
        if not source["bytes"]:
            raise ValueError(f"Empty audio file: {path.name}")
        if "duration_seconds" in entry:
            source["duration_seconds"] = finite_number(
                entry["duration_seconds"], "duration_seconds"
            )
        sources.append(source)
    return base.resolve(), sources, warnings


def load_engine(model_path: Path) -> Any:
    # Set before importing the hub/client libraries. No model resolution network calls.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from faster_whisper import WhisperModel

    return WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)


def transcribe(args: Namespace) -> None:
    initial_prompt = (
        args.initial_prompt.read_text(encoding="utf-8-sig") if args.initial_prompt else ""
    )
    hotwords = args.hotwords.read_text(encoding="utf-8-sig") if args.hotwords else ""
    settings = TranscriptionSettings(
        args.language,
        args.task,
        hotwords.strip(),
        initial_prompt.strip(),
        not args.no_vad,
    )
    settings.validate()
    model_path = local_path(args.model).resolve()
    model_info = describe_model(model_path)
    base, sources, warnings = collect_sources(args.input)
    identity = {
        "schema": 2,
        "tool_version": __version__,
        "model": model_info,
        "engine_versions": engine_versions(),
        "settings": asdict(settings),
        "sources": sources,
    }
    args.out.mkdir(parents=True, exist_ok=args.resume)
    with job_lock(args.out):
        _run_job(args, base, sources, warnings, settings, model_path, identity)


def _run_job(
    args: Namespace,
    base: Path,
    sources: list[dict[str, Any]],
    warnings: list[str],
    settings: TranscriptionSettings,
    model_path: Path,
    identity: dict[str, Any],
) -> None:
    job_file = args.out / "transcription-job.json"
    if args.resume:
        if not job_file.is_file():
            raise ValueError("No checkpoint to resume in this directory")
        job = read_json(job_file)
        if job.get("identity") != identity:
            raise ValueError(
                "Cannot resume: source audio, model, ASR settings, or engine versions changed. Use a new output directory."
            )
    else:
        job = {
            "identity": identity,
            "status": "pending",
            "completed": {},
            "started_utc": datetime.now(UTC).isoformat(),
        }
    job["status"] = "processing"
    job.pop("error", None)
    save_json(job_file, job)
    engine = None
    try:
        for source in sources:
            name = source["file"]
            if name in job["completed"]:
                print(f"Resume: reusing {name}", flush=True)
                continue
            if engine is None:
                engine = load_engine(model_path)
                if not engine.model.is_multilingual and (
                    settings.language != "en" or settings.task != "transcribe"
                ):
                    raise ValueError(
                        "This English-only model requires --language en --task transcribe. Use a multilingual model for other languages or translation."
                    )
            print(f"Transcribing {name}...", flush=True)
            path = child_file(base, name)
            segments, info = engine.transcribe(
                str(path),
                language=None if settings.language == "auto" else settings.language,
                task=settings.task,
                beam_size=5,
                vad_filter=settings.vad,
                condition_on_previous_text=False,
                hotwords=settings.hotwords or None,
                initial_prompt=settings.initial_prompt or None,
            )
            rows = []
            for segment in segments:
                start = finite_number(segment.start, "segment start")
                end = finite_number(segment.end, "segment end", start)
                if not isinstance(segment.text, str):
                    raise ValueError("ASR returned non-text segment")
                if segment.text.strip():
                    rows.append(
                        {
                            "start": source["offset_seconds"] + start,
                            "end": source["offset_seconds"] + end,
                            "source_start": start,
                            "source_end": end,
                            "track": source["track"],
                            "source": name,
                            "text": segment.text.strip(),
                        }
                    )
            if sha256_file(path) != source["sha256"]:
                raise ValueError(f"Source changed during transcription: {name}")
            job["completed"][name] = {
                "segments": rows,
                "detected_language": info.language,
            }
            # Only completed source files are checkpointed. Retry interrupted files from their start.
            save_json(job_file, job)
        rows = [row for source in sources for row in job["completed"][source["file"]]["segments"]]
        rows.sort(key=lambda row: (row["start"], row["track"]))
        for i, row in enumerate(rows, 1):
            row["id"] = f"S{i:06}"
        for source in sources:
            if not job["completed"][source["file"]]["segments"]:
                warnings.append(
                    f"{source['file']}: no speech recognized; inspect the source audio."
                )
        result = {
            "schema": 2,
            "status": "complete",
            "settings": asdict(settings),
            "warnings": warnings,
            "segments": rows,
            "sources": sources,
            "model": identity["model"],
            "engine_versions": identity["engine_versions"],
            "limitations": LIMITATIONS,
            "languages": {
                name: part["detected_language"] for name, part in job["completed"].items()
            },
        }
        save_json(args.out / "transcript.json", result)
        lines = [
            "UNVERIFIED AUTOMATIC TRANSCRIPT — review against audio.",
            LIMITATIONS,
            f"Task: {settings.task}; source language setting: {settings.language}.",
        ]
        if settings.task == "translate":
            lines.append(
                "TRANSLATION TO ENGLISH — this is not a verbatim source-language transcript."
            )
        lines.extend(f"WARNING: {w}" for w in warnings)
        lines.extend(
            f"[{s['id']}] [{timestamp(s['start'])}–{timestamp(s['end'])}] [{s['track']}] {s['text']}"
            for s in rows
        )
        atomic_text(args.out / "transcript.txt", "\n".join(lines) + "\n")
        job["status"] = "complete"
        job["artifacts"] = {
            name: sha256_file(args.out / name) for name in ("transcript.txt", "transcript.json")
        }
        job["completed_utc"] = datetime.now(UTC).isoformat()
        save_json(job_file, job)
    except BaseException as exc:
        job["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        # Keep provider/library exception text out of persistent logs; it can include recognized speech.
        job["error"] = type(exc).__name__
        save_json(job_file, job)
        raise
    print(f"Transcript saved to {args.out}")
