#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import shutil
import sqlite3

# Required for FFmpeg; reviewed argument-list invocation below.
import subprocess  # nosec B404
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp3 import MP3, MPEGInfo

from .cache import AnalysisCache, file_stamp
from .dedup import QUARANTINE_DIR, deduplicate_files
from .names import BACKUP_DIR, repair_names

BITRATE_BUCKETS = (360, 256, 192, 160, 128, 64)
LOUDNESS_PATTERN = re.compile(r"\bI:\s*(-?(?:\d+(?:\.\d+)?|inf))\s+LUFS")


def default_workers() -> int:
    return min(4, os.cpu_count() or 1)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


@dataclass(frozen=True)
class Mp3Record:
    source_path: Path
    relative_path: Path
    bitrate_kbps: int
    duration_seconds: float
    loudness_lufs: float | None
    loudness_error: str | None
    bucket_kbps: int
    destination_path: Path | None = None
    volume_percent: float | None = None
    move_error: str | None = None
    original_path: Path | None = None
    name_status: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sort MP3 files into bitrate buckets and write a bitrate/loudness CSV report. "
            "FFmpeg with the ebur128 filter is required for loudness measurement."
        )
    )
    parser.add_argument("folder", type=Path, help="Folder to scan recursively for MP3 files.")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="CSV output path (default: <folder>/mp3_report.csv).",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="FFmpeg executable to use (default: ffmpeg from PATH).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and report without moving any files.",
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=default_workers(),
        help="Concurrent file analyses (default: up to 4 CPUs; use 1 for sequential analysis).",
    )
    parser.add_argument(
        "--skip-loudness",
        action="store_true",
        help="Read metadata only; omit LUFS/volume measurements and do not require FFmpeg.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=300,
        help="FFmpeg timeout per file in seconds (default: 300).",
    )
    parser.add_argument("--quiet", action="store_true", help="Hide progress and per-file results.")
    parser.add_argument(
        "--cache", type=Path, help="Cache database (default: <folder>/.mp3-manager-cache.sqlite3)."
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Do not read or write cached measurements."
    )
    parser.add_argument(
        "--refresh-cache", action="store_true", help="Reanalyze files and replace cached results."
    )
    parser.add_argument(
        "--fix-filenames",
        action="store_true",
        help="Preview and confirm batch repair of Russian filename mojibake.",
    )
    parser.add_argument(
        "--fix-tags",
        action="store_true",
        help="Preview and confirm repair of ID3 title/artist/album text; back up before editing.",
    )
    parser.add_argument(
        "--accept-name-fixes",
        action="store_true",
        help="Explicitly accept proposed name/tag repairs without prompting; dry runs never apply them.",
    )
    parser.add_argument(
        "--no-move",
        action="store_true",
        help="Analyze and optionally repair names/tags without moving to bitrate folders.",
    )
    parser.add_argument(
        "--include-sorted",
        action="store_true",
        help="Also scan existing bitrate folders (use --no-move for repairs in place).",
    )
    parser.add_argument(
        "--csv-encoding",
        choices=("utf-8-sig", "utf-8"),
        default="utf-8-sig",
        help="CSV encoding (default: UTF-8 with BOM for Excel).",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Review exact audio-payload duplicates and quarantine approved extras.",
    )
    parser.add_argument(
        "--accept-duplicates",
        action="store_true",
        help="Accept the duplicate plan without prompting; dry runs never apply it.",
    )
    args = parser.parse_args(argv)
    if args.accept_duplicates and not args.deduplicate:
        parser.error("--accept-duplicates requires --deduplicate")
    if args.accept_name_fixes and not (args.fix_filenames or args.fix_tags):
        parser.error("--accept-name-fixes requires --fix-filenames or --fix-tags")
    return args


def bucket_for_bitrate(bitrate_kbps: int) -> int:
    for bucket in BITRATE_BUCKETS:
        if bitrate_kbps >= bucket:
            return bucket
    return BITRATE_BUCKETS[-1]


def find_mp3_files(folder: Path, include_sorted: bool = False) -> list[Path]:
    root = folder.resolve()
    bucket_names = {str(bucket) for bucket in BITRATE_BUCKETS}
    found: list[Path] = []
    for directory, dirs, files in os.walk(folder, followlinks=False):
        current = Path(directory)
        # Prune buckets before walking their contents, and do not traverse links/junctions.
        dirs[:] = [
            name
            for name in dirs
            if not (
                current == folder
                and (
                    name in {BACKUP_DIR, QUARANTINE_DIR}
                    or (not include_sorted and name in bucket_names)
                )
            )
            and not (current / name).is_symlink()
            and (current / name).resolve() == (current / name).absolute()
            and (current / name).resolve().is_relative_to(root)
        ]
        for name in files:
            path = current / name
            if (
                path.suffix.lower() == ".mp3"
                and not path.is_symlink()
                and path.is_file()
                and path.resolve().is_relative_to(root)
            ):
                found.append(path)
    return sorted(found)


def read_mp3_metadata(path: Path) -> tuple[int, float]:
    try:
        audio = MP3(path)
    except (MutagenError, OSError) as exc:
        raise ValueError(f"cannot read MP3 metadata: {exc}") from exc

    info = audio.info
    if not isinstance(info, MPEGInfo):
        raise ValueError("MP3 metadata does not contain MPEG stream information")
    if info.bitrate is None or info.bitrate <= 0:
        raise ValueError("MP3 metadata does not contain a positive bitrate")
    if not math.isfinite(info.length) or info.length < 0:
        raise ValueError("MP3 metadata contains an invalid duration")
    return round(info.bitrate / 1000), float(info.length)


def measure_loudness(
    path: Path,
    ffmpeg_bin: str,
    timeout: int = 300,
) -> tuple[float | None, str | None]:
    command = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-i",
        str(path.resolve()),
        "-map",
        "0:a:0",
        "-af",
        "ebur128=peak=none:framelog=verbose",
        "-threads",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        # Trusted user-selected executable, separate arguments, no shell, bounded runtime.
        result = subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None, f"FFmpeg timed out after {timeout} seconds"
    except FileNotFoundError:
        return None, f"FFmpeg executable not found: {ffmpeg_bin}"
    except OSError as exc:
        return None, f"could not run FFmpeg: {exc}"

    output = f"{result.stdout}\n{result.stderr}"
    matches = LOUDNESS_PATTERN.findall(output)
    if result.returncode != 0:
        return None, f"FFmpeg failed with exit code {result.returncode}"
    if not matches:
        return None, "FFmpeg did not return an integrated loudness measurement"
    if matches[-1] == "-inf":
        return None, "silence (-inf LUFS)"
    value = float(matches[-1])
    if not math.isfinite(value):
        return None, "FFmpeg returned non-finite loudness"
    return value, None


def calculate_volume_percent(records: list[Mp3Record]) -> list[Mp3Record]:
    measured = [record.loudness_lufs for record in records if record.loudness_lufs is not None]
    if not measured:
        return records

    quietest = min(measured)
    loudest = max(measured)
    adjusted: list[Mp3Record] = []
    for record in records:
        if record.loudness_lufs is None:
            adjusted.append(record)
        elif loudest == quietest:
            adjusted.append(replace(record, volume_percent=100.0))
        else:
            percent = (record.loudness_lufs - quietest) / (loudest - quietest) * 100
            adjusted.append(replace(record, volume_percent=round(percent, 2)))
    return adjusted


def unique_destination(destination_dir: Path, filename: str) -> Path:
    candidate = destination_dir / filename
    if not candidate.exists() and not candidate.is_symlink():
        return candidate

    source = Path(filename)
    index = 1
    while True:
        candidate = destination_dir / f"{source.stem} ({index}){source.suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def move_to_bucket(record: Mp3Record, root: Path) -> Mp3Record:
    destination_dir = root / str(record.bucket_kbps)
    if destination_dir.is_symlink() or destination_dir.resolve().parent != root.resolve():
        raise OSError(f"bucket directory must be directly inside the scan root: {destination_dir}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    if record.source_path.parent == destination_dir:
        return replace(record, destination_path=record.source_path)
    destination = unique_destination(destination_dir, record.source_path.name)
    shutil.move(str(record.source_path), str(destination))
    return replace(record, destination_path=destination)


def write_report(records: list[Mp3Record], report_path: Path, encoding: str = "utf-8-sig") -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        records,
        key=lambda record: (
            -record.bitrate_kbps,
            -(record.volume_percent if record.volume_percent is not None else -1),
            record.relative_path.as_posix().lower(),
        ),
    )
    name_columns = (
        ("original_relative_path", "name_repair_status")
        if any(record.name_status for record in records)
        else ()
    )
    with report_path.open("w", newline="", encoding=encoding) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=(
                "file_name",
                "relative_path",
                "destination_path",
                "bitrate_kbps",
                "bitrate_bucket_kbps",
                "duration_seconds",
                "integrated_loudness_lufs",
                "volume_percent",
                "analysis_status",
            )
            + name_columns,
        )
        writer.writeheader()
        for record in ordered:
            writer.writerow(
                {
                    "file_name": record.source_path.name,
                    "relative_path": record.relative_path.as_posix(),
                    "destination_path": (
                        record.destination_path.relative_to(report_path.parent).as_posix()
                        if record.destination_path is not None
                        and record.destination_path.is_relative_to(report_path.parent)
                        else str(record.destination_path or "")
                    ),
                    "bitrate_kbps": record.bitrate_kbps,
                    "bitrate_bucket_kbps": record.bucket_kbps,
                    "duration_seconds": f"{record.duration_seconds:.2f}",
                    "integrated_loudness_lufs": (
                        f"{record.loudness_lufs:.2f}" if record.loudness_lufs is not None else ""
                    ),
                    "volume_percent": (
                        f"{record.volume_percent:.2f}" if record.volume_percent is not None else ""
                    ),
                    "analysis_status": "; ".join(
                        error for error in (record.loudness_error, record.move_error) if error
                    )
                    or "ok",
                    **(
                        {
                            "original_relative_path": (
                                record.original_path or record.relative_path
                            ).as_posix(),
                            "name_repair_status": record.name_status,
                        }
                        if name_columns
                        else {}
                    ),
                }
            )


def print_results(records: list[Mp3Record]) -> None:
    for record in sorted(
        records, key=lambda item: (-item.bitrate_kbps, item.relative_path.as_posix())
    ):
        loudness = (
            f"{record.loudness_lufs:.2f} LUFS / {record.volume_percent:.2f}%"
            if record.loudness_lufs is not None and record.volume_percent is not None
            else record.loudness_error or "not measured"
        )
        print(
            f"{record.bitrate_kbps:>3} kbps -> {record.bucket_kbps:>3} | "
            f"{loudness} | {record.relative_path.as_posix()}"
        )


def analyze_file(
    path: Path,
    root: Path,
    executable: str,
    skip_loudness: bool,
    timeout: int,
) -> Mp3Record:
    bitrate, duration = read_mp3_metadata(path)
    loudness, error = (
        (None, "skipped (--skip-loudness)")
        if skip_loudness
        else measure_loudness(path, executable, timeout)
    )
    return Mp3Record(
        path,
        path.relative_to(root),
        bitrate,
        duration,
        loudness,
        error,
        bucket_for_bitrate(bitrate),
    )


def analyze_files(
    paths: list[Path],
    root: Path,
    executable: str,
    workers: int,
    skip_loudness: bool,
    timeout: int,
    quiet: bool,
    cache: AnalysisCache | None = None,
    refresh_cache: bool = False,
) -> tuple[list[Mp3Record], bool]:
    records: list[Mp3Record] = []
    failed = False
    started = time.monotonic()
    last_update = started
    completed = 0
    cache_hits = 0
    remaining = iter(paths)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Keep only a bounded set of futures; no thousand-process burst or unbounded queue.
        pending = {}

        def submit_next() -> None:
            nonlocal completed, cache_hits
            while (path := next(remaining, None)) is not None:
                stamp = None
                if cache is not None:
                    try:
                        stamp = file_stamp(path)
                    except OSError:
                        pass  # The normal metadata reader will report the unreadable file.
                saved = cache.get(path, stamp) if cache and stamp and not refresh_cache else None
                if saved is not None:
                    bitrate, duration, loudness = saved
                    records.append(
                        Mp3Record(
                            path,
                            path.relative_to(root),
                            bitrate,
                            duration,
                            loudness,
                            None,
                            bucket_for_bitrate(bitrate),
                        )
                    )
                    completed += 1
                    cache_hits += 1
                    continue
                future = pool.submit(analyze_file, path, root, executable, skip_loudness, timeout)
                pending[future] = (path, stamp)
                break

        for _ in range(min(len(paths), workers * 2)):
            submit_next()
        try:
            while pending:
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    path, stamp = pending.pop(future)
                    try:
                        record = future.result()
                    except ValueError as exc:
                        failed = True
                        print(f"Warning: skipped {path}: {exc}", file=sys.stderr)
                    else:
                        records.append(record)
                        failed = failed or (not skip_loudness and record.loudness_error is not None)
                        if (
                            cache
                            and stamp
                            and record.loudness_error is None
                            and record.loudness_lufs is not None
                        ):
                            try:
                                unchanged = file_stamp(path) == stamp
                            except OSError:
                                unchanged = False
                            if unchanged:
                                cache.put(
                                    path,
                                    stamp,
                                    record.bitrate_kbps,
                                    record.duration_seconds,
                                    record.loudness_lufs,
                                )
                    completed += 1
                    submit_next()
                now = time.monotonic()
                if not quiet and (now - last_update >= 1 or not pending):
                    print(
                        f"Analyzed {completed}/{len(paths)} files in {now - started:.1f}s "
                        f"({workers} workers)",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_update = now
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    # Worker completion order must not change duplicate-name allocation during moves.
    records.sort(key=lambda record: record.source_path)
    if cache is not None:
        print(f"Cache: {cache_hits} reused; {len(paths) - cache_hits} analyzed.")
    return records, failed


def run(
    folder: Path,
    report_path: Path,
    ffmpeg_bin: str,
    dry_run: bool,
    *,
    workers: int | None = None,
    skip_loudness: bool = False,
    timeout: int = 300,
    quiet: bool = False,
    cache_path: Path | None = None,
    no_cache: bool = False,
    refresh_cache: bool = False,
    fix_filenames: bool = False,
    fix_tags: bool = False,
    accept_name_fixes: bool = False,
    no_move: bool = False,
    include_sorted: bool = False,
    csv_encoding: str = "utf-8-sig",
    deduplicate: bool = False,
    accept_duplicates: bool = False,
) -> int:
    workers = default_workers() if workers is None else workers
    if workers < 1 or timeout < 1:
        print("Error: workers and timeout must be positive", file=sys.stderr)
        return 2
    root = folder.resolve()
    if not root.is_dir():
        print(f"Error: folder does not exist or is not a directory: {root}", file=sys.stderr)
        return 2

    if report_path.suffix.lower() == ".mp3" or report_path.is_symlink():
        print("Error: report must not be an MP3 file or symbolic link", file=sys.stderr)
        return 2
    report_path = report_path.resolve()
    if report_path.exists() and not report_path.is_file():
        print("Error: report path is not a regular file", file=sys.stderr)
        return 2
    cache_path = cache_path or root / ".mp3-manager-cache.sqlite3"
    if not no_cache and not skip_loudness:
        if (
            cache_path.is_symlink()
            or cache_path.suffix.lower() != ".sqlite3"
            or cache_path.resolve() == report_path
        ):
            print(
                "Error: cache must be a separate .sqlite3 file, not a symlink or report",
                file=sys.stderr,
            )
            return 2
    executable = "" if skip_loudness else shutil.which(ffmpeg_bin)
    if not skip_loudness and (
        executable is None or Path(executable).suffix.lower() in {".bat", ".cmd"}
    ):
        print(
            f"Error: FFmpeg executable not found or not a native executable: {ffmpeg_bin}",
            file=sys.stderr,
        )
        return 2
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Error: cannot create report directory: {exc}", file=sys.stderr)
        return 2

    started = time.monotonic()
    paths = find_mp3_files(root, include_sorted)
    if not quiet:
        print(
            f"Found {len(paths)} MP3 files; analyzing with {workers} workers"
            f"{' (metadata only)' if skip_loudness else ''}...",
            file=sys.stderr,
            flush=True,
        )
    cache = None
    if not no_cache and not skip_loudness:
        try:
            cache = AnalysisCache(cache_path.resolve(), executable or "")
        except (OSError, sqlite3.Error) as exc:
            print(f"Warning: cache unavailable; analyzing without it: {exc}", file=sys.stderr)
    try:
        records, failed = analyze_files(
            paths,
            root,
            executable or "",
            workers,
            skip_loudness,
            timeout,
            quiet,
            cache,
            refresh_cache,
        )
    finally:
        if cache is not None:
            cache.close()

    if fix_filenames or fix_tags:
        try:
            renamed, statuses, repair_failed = repair_names(
                [record.source_path for record in records],
                root,
                report_path,
                filenames=fix_filenames,
                tags=fix_tags,
                dry_run=dry_run,
                accept=accept_name_fixes,
            )
        except (OSError, MutagenError) as exc:
            print(f"Error: name repair stopped before segregation: {exc}", file=sys.stderr)
            return 1
        records = [
            replace(
                record,
                source_path=renamed.get(record.source_path, record.source_path),
                relative_path=renamed.get(record.source_path, record.source_path).relative_to(root),
                original_path=record.relative_path,
                name_status=statuses.get(record.source_path, "unchanged"),
            )
            for record in records
        ]
        failed = failed or repair_failed
    if deduplicate:
        try:
            renamed, removed, dedup_failed = deduplicate_files(
                [record.source_path for record in records],
                root,
                report_path,
                dry_run=dry_run,
                accept=accept_duplicates,
                quiet=quiet,
            )
        except (OSError, ValueError) as exc:
            print(f"Error: deduplication stopped before segregation: {exc}", file=sys.stderr)
            return 1
        records = [
            (
                replace(
                    record,
                    source_path=renamed[record.source_path],
                    relative_path=renamed[record.source_path].relative_to(root),
                    original_path=record.original_path or record.relative_path,
                    name_status=(record.name_status + "; deduplication keeper renamed").strip("; "),
                )
                if record.source_path in renamed
                else record
            )
            for record in records
            if record.source_path not in removed
        ]
        failed = failed or dedup_failed
    records = calculate_volume_percent(records)
    if not dry_run and not no_move:
        moved = []
        for record in records:
            try:
                moved.append(move_to_bucket(record, root))
            except OSError as exc:
                failed = True
                error = f"move failed: {exc}"
                print(f"Warning: {record.source_path}: {error}", file=sys.stderr)
                moved.append(replace(record, move_error=error))
        records = moved

    try:
        write_report(records, report_path, csv_encoding)
    except OSError as exc:
        print(f"Error: cannot write report: {exc}", file=sys.stderr)
        return 1
    if not quiet:
        print_results(records)
    print(f"Processed {len(paths)} files in {time.monotonic() - started:.1f}s.")
    print(f"Wrote report: {report_path}")
    if dry_run:
        print("Dry run: no MP3 files were moved.")
    elif no_move:
        print("No bitrate segregation performed.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report_path = args.report or args.folder / "mp3_report.csv"
    return run(
        args.folder,
        report_path,
        args.ffmpeg_bin,
        args.dry_run,
        workers=args.workers,
        skip_loudness=args.skip_loudness,
        timeout=args.timeout,
        quiet=args.quiet,
        cache_path=args.cache,
        no_cache=args.no_cache,
        refresh_cache=args.refresh_cache,
        fix_filenames=args.fix_filenames,
        fix_tags=args.fix_tags,
        accept_name_fixes=args.accept_name_fixes,
        no_move=args.no_move,
        include_sorted=args.include_sorted,
        csv_encoding=args.csv_encoding,
        deduplicate=args.deduplicate,
        accept_duplicates=args.accept_duplicates,
    )


if __name__ == "__main__":
    raise SystemExit(main())
