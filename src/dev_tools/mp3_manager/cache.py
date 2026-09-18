"""Disposable SQLite cache; only the coordinator thread accesses the connection."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

from mutagen import version_string

# Bump whenever metadata interpretation or FFmpeg analysis settings change.
ANALYSIS_VERSION = "mp3-v1:ebur128-peak-none:first-audio:full-track"


def file_stamp(path: Path) -> str:
    stat = path.stat()
    return json.dumps([stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino])


class AnalysisCache:
    def __init__(self, path: Path, executable: str):
        self.connection: sqlite3.Connection | None = None
        self.signature = json.dumps(
            [
                ANALYSIS_VERSION,
                version_string,
                str(Path(executable).resolve()),
                file_stamp(Path(executable)),
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=1)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if tables and "mp3_analysis_v1" not in tables:
                raise sqlite3.DatabaseError("not an MP3 manager cache database")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mp3_analysis_v1 ("
                "path TEXT PRIMARY KEY, stamp TEXT NOT NULL, signature TEXT NOT NULL, "
                "bitrate INTEGER NOT NULL, duration REAL NOT NULL, loudness REAL NOT NULL)"
            )
            connection.commit()
        except BaseException:
            connection.close()
            raise
        self.connection = connection

    def disable(self, exc: Exception) -> None:
        print(f"Warning: cache disabled; analysis continues without it: {exc}", file=sys.stderr)
        self.close()

    def get(self, path: Path, stamp: str) -> tuple[int, float, float] | None:
        if self.connection is None:
            return None
        try:
            row = self.connection.execute(
                "SELECT bitrate, duration, loudness FROM mp3_analysis_v1 "
                "WHERE path=? AND stamp=? AND signature=?",
                (str(path), stamp, self.signature),
            ).fetchone()
            if row is None:
                return None
            bitrate, duration, loudness = row
            if (
                not isinstance(bitrate, int)
                or bitrate <= 0
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration < 0
                or not isinstance(loudness, (int, float))
                or not math.isfinite(loudness)
            ):
                return None
            return bitrate, float(duration), float(loudness)
        except sqlite3.Error as exc:
            self.disable(exc)
            return None

    def put(self, path: Path, stamp: str, bitrate: int, duration: float, loudness: float) -> None:
        if self.connection is None:
            return
        try:
            self.connection.execute(
                "INSERT OR REPLACE INTO mp3_analysis_v1 "
                "(path, stamp, signature, bitrate, duration, loudness) VALUES (?, ?, ?, ?, ?, ?)",
                (str(path), stamp, self.signature, bitrate, duration, loudness),
            )
            # Checkpoint each completed file so an interrupted run can reuse it.
            self.connection.commit()
        except sqlite3.Error as exc:
            self.disable(exc)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
