"""Local file validation, durable replacement, and exclusive job ownership."""

import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

AUDIO_SUFFIXES = {
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".webm",
    ".mp4",
}


def local_path(value: str | Path) -> Path:
    text = str(value)
    if text.startswith(("\\\\", "//")) or "://" in text:
        raise ValueError("Use a local disk path, not a URL or UNC network share")
    return Path(value)


def child_file(folder: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or any(c in name for c in ("/", "\\", ":")):
        raise ValueError("Expected a simple filename")
    folder = local_path(folder).resolve()
    path = folder / name
    if path.is_symlink() or path.resolve().parent != folder or not path.is_file():
        raise ValueError(f"Missing or unsafe source file: {name}")
    return path


def finite_number(value: Any, label: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label} must be finite and >= {minimum}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path = local_path(path)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path: Path, data: Any) -> None:
    atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return value


@contextmanager
def job_lock(folder: Path) -> Iterator[None]:
    """OS lock releases after crashes; the harmless .job.lock file stays on disk."""
    handle = (folder / ".job.lock").open("a+b")
    acquired = False
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise RuntimeError("Another process owns this transcription output directory") from exc
        yield
    finally:
        if acquired:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def timestamp(seconds: float) -> str:
    milliseconds = round(finite_number(seconds, "timestamp") * 1000)
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{sec:02}.{ms:03}"
