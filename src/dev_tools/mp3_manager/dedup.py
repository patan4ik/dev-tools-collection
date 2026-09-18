"""Exact encoded-payload deduplication with reviewed, recoverable removals."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

from .cache import file_stamp
from .names import invalid_filename, rename_no_replace, suggest_text

QUARANTINE_DIR = ".mp3-manager-duplicates"


def payload_fingerprint(path: Path) -> tuple[int, str, str]:
    """Hash all bytes except validated leading ID3v2 and trailing ID3v1 blocks.

    Other trailers are deliberately retained: ambiguous layouts may miss matches,
    rather than treating differently encoded tracks as interchangeable.
    The CLI supplies files already validated as MP3 by the metadata reader.
    """
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular non-symlink file")
    before = file_stamp(path)
    with path.open("rb") as stream:
        end = stream.seek(0, os.SEEK_END)
        stream.seek(0)
        header = stream.read(10)
        start = 0
        if header.startswith(b"ID3"):
            if len(header) != 10 or header[3] not in {2, 3, 4} or any(b & 128 for b in header[6:]):
                raise ValueError("invalid ID3 header")
            tag_size = 0
            for byte in header[6:]:
                tag_size = (tag_size << 7) | byte
            start = 10 + tag_size
            if header[3] == 4 and header[5] & 0x10:
                stream.seek(start)
                if stream.read(10) != b"3DI" + header[3:]:
                    raise ValueError("invalid ID3v2 footer")
                start += 10
        if end >= 128:
            stream.seek(end - 128)
            if stream.read(3) == b"TAG":
                end -= 128
        if start >= end:
            raise ValueError("empty or invalid MP3 payload bounds")
        stream.seek(start)
        digest = hashlib.sha256()
        remaining = end - start
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("file truncated during hashing")
            digest.update(chunk)
            remaining -= len(chunk)
    if before != file_stamp(path):
        raise ValueError("file changed during hashing")
    return end - start, digest.hexdigest(), before


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def clean_tag(value: str) -> str:
    corrected, reason = suggest_text(value)
    if corrected is not None:
        value = corrected
    elif reason:
        return ""
    return value.strip()


def tag_details(path: Path) -> tuple[str, str, int]:
    try:
        tags = ID3(path, translate=False)
    except ID3NoHeaderError:
        return "", "", 0
    artist = clean_tag(str(tags.get("TPE1", "")))
    title = clean_tag(str(tags.get("TIT2", "")))
    richness = sum(
        bool(str(tags.get(field, ""))) for field in ("TPE1", "TIT2", "TALB", "TPE2", "TRCK", "TDRC")
    )
    richness += bool(tags.getall("APIC"))
    return artist, title, richness


def has_artist_title(filename: str, artist: str, title: str) -> bool:
    name = f" {normalized(Path(filename).stem)} "
    return bool(
        artist and title and f" {normalized(artist)} " in name and f" {normalized(title)} " in name
    )


def filename_from_tags(artist: str, title: str, suffix: str = ".mp3") -> str | None:
    if not artist or not title:
        return None
    stem = f"{artist} - {title}"
    stem = re.sub(r'[<>:"/\\|?*]', " - ", stem)
    stem = " ".join(stem.split()).strip(" .")
    candidate = unicodedata.normalize("NFC", stem) + suffix
    return None if invalid_filename(candidate) else candidate


@dataclass(frozen=True)
class Member:
    path: Path
    length: int
    digest: str
    stamp: str
    artist: str
    title: str
    richness: int


@dataclass
class DuplicateGroup:
    keeper: Member
    duplicates: list[Member]
    target: Path
    reason: str


def keeper_key(member: Member) -> tuple:
    stem = re.sub(r"\s*\(\d+\)$", "", member.path.stem)
    return (
        -int(has_artist_title(member.path.name, member.artist, member.title)),
        -len(normalized(stem)),
        -member.richness,
        bool(re.search(r"\(\d+\)$", member.path.stem)),
        str(member.path).casefold(),
        str(member.path),
    )


def find_duplicates(paths: list[Path], quiet: bool = False) -> tuple[list[DuplicateGroup], bool]:
    by_payload: dict[tuple[int, str], list[Member]] = {}
    failed = False
    last_update = time.monotonic()
    for index, path in enumerate(paths, 1):
        try:
            length, digest, stamp = payload_fingerprint(path)
            artist, title, richness = tag_details(path)
            by_payload.setdefault((length, digest), []).append(
                Member(path, length, digest, stamp, artist, title, richness)
            )
        except (OSError, ValueError, MutagenError) as exc:
            failed = True
            print(f"Warning: deduplication skipped {path}: {exc}", file=sys.stderr)
        if not quiet and time.monotonic() - last_update >= 1:
            print(
                f"Duplicate check: hashed {index}/{len(paths)} files", file=sys.stderr, flush=True
            )
            last_update = time.monotonic()
    groups = []
    occupied: dict[Path, set[str]] = {}
    for members in by_payload.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=keeper_key)
        keeper = ordered[0]
        target = keeper.path
        reason = "identical SHA-256 and length of encoded payload; prefer tagged artist/title and descriptive name"
        pairs = {(m.artist, m.title) for m in members if m.artist and m.title}
        if len(pairs) == 1:
            artist, title = next(iter(pairs))
            if not has_artist_title(keeper.path.name, artist, title):
                suggested = filename_from_tags(artist, title, keeper.path.suffix)
                if suggested:
                    parent = keeper.path.parent
                    if parent not in occupied:
                        occupied[parent] = {p.name.casefold() for p in parent.iterdir()}
                    candidate = Path(suggested)
                    number = 1
                    while candidate.name.casefold() in occupied[parent]:
                        candidate = Path(f"{Path(suggested).stem} ({number}){keeper.path.suffix}")
                        number += 1
                    if not invalid_filename(candidate.name):
                        target = parent / candidate.name
                        occupied[parent].add(candidate.name.casefold())
                        reason += "; keeper filename completed from artist/title tags"
        elif len(pairs) > 1:
            reason += "; conflicting tags: keeper filename left unchanged"
        groups.append(DuplicateGroup(keeper, ordered[1:], target, reason))
    return groups, failed


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=path.parent,
            prefix=".dedup-audit-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def deduplicate_files(
    paths: list[Path],
    root: Path,
    report: Path,
    *,
    dry_run: bool,
    accept: bool,
    quiet: bool,
) -> tuple[dict[Path, Path], set[Path], bool]:
    groups, failed = find_duplicates(paths, quiet)
    if not groups:
        print("Deduplication: no exact encoded-payload duplicates found.")
        return {}, set(), failed
    run_id = uuid.uuid4().hex
    quarantine = root / QUARANTINE_DIR / run_id
    audit = report.with_name(f"{report.stem}.duplicates-{run_id}.csv")
    rows: list[dict[str, str]] = []
    for group_index, group in enumerate(groups, 1):
        print(f"\nGroup {group_index}: KEEP {group.keeper.path.relative_to(root)}")
        if group.target != group.keeper.path:
            print(f"  Rename kept file to: {group.target.name}")
        print(f"  {group.reason}")
        for duplicate in group.duplicates:
            target = quarantine / duplicate.path.relative_to(root)
            print(f"  QUARANTINE {duplicate.path.relative_to(root)}")
            rows.append(
                {
                    "group": str(group_index),
                    "keeper_original": str(group.keeper.path),
                    "keeper_proposed": str(group.target),
                    "duplicate_original": str(duplicate.path),
                    "quarantine_path": str(target),
                    "payload_sha256": duplicate.digest,
                    "payload_bytes": str(duplicate.length),
                    "status": "proposed",
                    "keeper_rename_status": (
                        "proposed" if group.target != group.keeper.path else "unchanged"
                    ),
                    "reason": group.reason,
                }
            )
    write_manifest(audit, rows)
    print(
        f"\nDuplicate review: {len(rows)} extra files in {len(groups)} groups. Nothing is permanently deleted."
    )
    approved = accept and not dry_run
    if not dry_run and not accept:
        if sys.stdin.isatty():
            try:
                approved = input(
                    "Apply this deduplication/keeper-name plan and quarantine duplicates? [y/N] "
                ).strip().lower() in {"y", "yes"}
            except EOFError:
                approved = False
        else:
            print(
                "Noninteractive run: duplicates retained. Use --accept-duplicates after reviewing the plan."
            )
    for row in rows:
        row["status"] = "accepted" if approved else "dry-run" if dry_run else "declined"
        if row["keeper_rename_status"] == "proposed" and not approved:
            row["keeper_rename_status"] = row["status"]
    write_manifest(audit, rows)
    removed: set[Path] = set()
    renamed: dict[Path, Path] = {}
    if approved:
        if (root / QUARANTINE_DIR).is_symlink() or not quarantine.resolve().is_relative_to(root):
            raise OSError("unsafe quarantine directory")
        quarantine.mkdir(parents=True, exist_ok=False)
        for index, group in enumerate(groups, 1):
            group_rows = [row for row in rows if row["group"] == str(index)]
            try:
                # Rehash after approval; size/tags/stat-only equality never authorizes removal.
                for member in [group.keeper, *group.duplicates]:
                    if payload_fingerprint(member.path) != (
                        member.length,
                        member.digest,
                        member.stamp,
                    ):
                        raise ValueError(f"file changed since preview: {member.path}")
            except (OSError, ValueError) as exc:
                failed = True
                for row in group_rows:
                    row["status"], row["reason"] = "failed", str(exc)
                    row["keeper_rename_status"] = "not applied"
                write_manifest(audit, rows)
                continue
            for member, row in zip(group.duplicates, group_rows, strict=True):
                try:
                    if (
                        file_stamp(group.keeper.path) != group.keeper.stamp
                        or file_stamp(member.path) != member.stamp
                    ):
                        raise ValueError("file changed after verification")
                    destination = Path(row["quarantine_path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    rename_no_replace(member.path, destination)
                    removed.add(member.path)
                    row["status"] = "quarantined"
                except (OSError, ValueError) as exc:
                    row["status"], row["reason"] = "failed", str(exc)
                    failed = True
                write_manifest(audit, rows)
            if group.target != group.keeper.path:
                try:
                    if file_stamp(group.keeper.path) != group.keeper.stamp:
                        raise ValueError("keeper changed after verification")
                    if any(
                        p.name.casefold() == group.target.name.casefold()
                        for p in group.target.parent.iterdir()
                    ):
                        raise FileExistsError(f"keeper target already exists: {group.target}")
                    rename_no_replace(group.keeper.path, group.target)
                    renamed[group.keeper.path] = group.target
                    for row in group_rows:
                        row["keeper_rename_status"] = "applied"
                except (OSError, ValueError) as exc:
                    failed = True
                    for row in group_rows:
                        row["keeper_rename_status"] = f"failed: {exc}"
                write_manifest(audit, rows)
    print(f"Duplicate audit: {audit}")
    print(f"Deduplication: {len(removed)} quarantined; {len(groups)} keeper(s) retained.")
    return renamed, removed, failed
