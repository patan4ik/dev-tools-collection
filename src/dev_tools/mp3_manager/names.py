"""Reviewed, opt-in repair of common Russian filename and ID3 mojibake."""

from __future__ import annotations

import csv
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

HIGH_RUN = re.compile(r"[^\x00-\x7f\u0400-\u052f]+")
RUSSIAN_RUN = re.compile(r"[А-Яа-яЁё]{2,}")
VOWELS = set("АЕЁИОУЫЭЮЯаеёиоуыэюя")
TAG_FIELDS = ("TIT2", "TPE1", "TPE2", "TALB")
BACKUP_DIR = ".mp3-manager-backups"


def suggest_text(text: str) -> tuple[str | None, str]:
    """Return reversible candidates, never fill missing bytes or transliterate."""
    if any(
        ord(char) < 32 or unicodedata.category(char) in {"Cf", "Cs"} or char == "�" for char in text
    ):
        return None, "control/replacement characters: manual review"
    replacements: list[tuple[int, int, str]] = []
    strong = False
    for match in HIGH_RUN.finditer(text):
        token = match.group()
        candidates = set()
        for codec in ("latin1", "cp1252"):
            try:
                raw = token.encode(codec)
                restored = raw.decode("utf-8")
                if restored.encode("utf-8").decode(codec) == token and any(
                    "А" <= char <= "я" or char in "Ёё" for char in restored
                ):
                    candidates.add(restored)
            except UnicodeError:
                pass
        if len(candidates) == 1:
            replacements.append((match.start(), match.end(), candidates.pop()))
            strong = True
            continue
        # Broken UTF-8 byte patterns must not be misinterpreted as Windows-1251.
        if re.search(r"[ÐÑ][ÐÑ\x80-\xbf]", token) or len(candidates) > 1:
            return None, "damaged or ambiguous UTF-8 sequence: manual review"
        candidates = set()
        for codec in ("latin1", "cp1252"):
            try:
                restored = token.encode(codec).decode("cp1251")
                if restored.encode("cp1251").decode(codec) == token:
                    candidates.add(restored)
            except UnicodeError:
                pass
        if len(candidates) != 1:
            continue
        restored = candidates.pop()
        if restored == token:
            continue
        plausible = bool(RUSSIAN_RUN.search(restored)) and bool(set(restored) & VOWELS)
        # Isolated accented letters in Latin words (Beyonce, Motorhead, etc.) are not evidence.
        adjacent_latin = (
            match.start() > 0
            and text[match.start() - 1].isascii()
            and text[match.start() - 1].isalpha()
        ) or (
            match.end() < len(text) and text[match.end()].isascii() and text[match.end()].isalpha()
        )
        if plausible or (len(token) == 1 and not adjacent_latin):
            replacements.append((match.start(), match.end(), restored))
            strong = strong or plausible
    if not strong:
        return None, (
            "unrecognized control characters: manual review"
            if any(unicodedata.category(char) == "Cc" for char in text)
            else ""
        )
    result = text
    for start, end, restored in reversed(replacements):
        result = result[:start] + restored + result[end:]
    return (
        unicodedata.normalize("NFC", result),
        "reversible Cyrillic encoding candidate; review required",
    )


def invalid_filename(name: str) -> str:
    if not name or name in {".", ".."} or name.endswith((" ", ".")):
        return "empty name or trailing space/dot"
    if any(
        char in '<>:"/\\|?*' or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in name
    ):
        return "invalid Windows filename characters"
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", name.split(".")[0].rstrip()):
        return "reserved Windows device name"
    if len(name.encode("utf-16-le")) // 2 > 255:
        return "filename exceeds 255 UTF-16 units"
    if Path(name).suffix.lower() != ".mp3":
        return "MP3 extension required"
    return ""


@dataclass
class NameChange:
    path: Path
    field: str
    original: str
    proposed: str | None
    reason: str
    index: int = 0
    status: str = "proposed"
    backup: str = ""


def plan_changes(paths: list[Path], filenames: bool, tags: bool) -> list[NameChange]:
    changes: list[NameChange] = []
    for path in paths:
        if filenames:
            proposed, reason = suggest_text(path.stem)
            if proposed is not None:
                proposed += path.suffix
                invalid = invalid_filename(proposed)
                changes.append(
                    NameChange(
                        path,
                        "filename",
                        path.name,
                        proposed,
                        invalid or reason,
                        status="blocked" if invalid else "proposed",
                    )
                )
            elif reason:
                changes.append(
                    NameChange(path, "filename", path.name, None, reason, status="unresolved")
                )
        if not tags:
            continue
        try:
            metadata = ID3(path, translate=False)
        except ID3NoHeaderError:
            continue
        except (MutagenError, OSError) as exc:
            changes.append(NameChange(path, "ID3", "", None, str(exc), status="unresolved"))
            continue
        for field in TAG_FIELDS:
            frame = metadata.get(field)
            for index, value in enumerate(getattr(frame, "text", [])):
                proposed, reason = suggest_text(str(value))
                if proposed is not None or reason:
                    supported = metadata.version[0] == 1 or metadata.version[1] in {3, 4}
                    if metadata.version[0] == 1:
                        reason += "; upgrade legacy ID3v1 to ID3v2.3 UTF-16"
                    elif not supported:
                        reason += "; unsupported ID3 version: manual review"
                    if proposed is not None and supported:
                        reason += "; remove legacy ID3v1 if present"
                    changes.append(
                        NameChange(
                            path,
                            field,
                            str(value),
                            proposed,
                            reason,
                            index,
                            ("proposed" if proposed is not None and supported else "unresolved"),
                        )
                    )
    # Reserve unique case-insensitive names before displaying the exact proposed names.
    occupied: dict[Path, set[str]] = {}
    for change in changes:
        if change.field == "filename" and change.status == "proposed" and change.proposed:
            parent = change.path.parent
            if parent not in occupied:
                occupied[parent] = {path.name.casefold() for path in parent.iterdir()}
            target = Path(change.proposed)
            candidate = change.proposed
            index = 1
            while candidate.casefold() in occupied[parent]:
                candidate = f"{target.stem} ({index}){target.suffix}"
                index += 1
            if candidate != change.proposed:
                change.reason += "; numbered suffix avoids an existing/planned name"
                change.proposed = candidate
            invalid = invalid_filename(candidate)
            if invalid:
                change.status, change.reason = "blocked", invalid
            else:
                occupied[parent].add(candidate.casefold())
    return changes


def write_audit(changes: list[NameChange], audit: Path, root: Path, mode: str = "w") -> None:
    def save(stream: IO[str]) -> None:
        writer = csv.writer(stream)
        writer.writerow(
            ["relative_path", "field", "original", "proposed", "status", "reason", "backup"]
        )
        for change in changes:
            writer.writerow(
                [
                    change.path.relative_to(root).as_posix(),
                    change.field,
                    change.original,
                    change.proposed or "",
                    change.status,
                    change.reason,
                    change.backup,
                ]
            )
        stream.flush()
        os.fsync(stream.fileno())

    if mode == "x":
        with audit.open("x", encoding="utf-8-sig", newline="") as stream:
            save(stream)
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=audit.parent,
            prefix=".name-audit-",
            suffix=".tmp",
            delete=False,
        ) as temporary_stream:
            temporary = Path(temporary_stream.name)
            save(temporary_stream.file)
        os.replace(temporary, audit)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def rename_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename fails if destination exists, including a racing new file.
        source.rename(destination)
    else:
        # POSIX rename would overwrite: reserve the new name atomically via a hard link.
        os.link(source, destination)
        try:
            source.unlink()
        except OSError:
            destination.unlink()
            raise


def repair_names(
    paths: list[Path],
    root: Path,
    report: Path,
    *,
    filenames: bool,
    tags: bool,
    dry_run: bool,
    accept: bool,
) -> tuple[dict[Path, Path], dict[Path, str], bool]:
    changes = plan_changes(paths, filenames, tags)
    if not changes:
        print("Name review: no supported encoding issues detected.")
        return {}, {}, False
    audit = report.with_name(f"{report.stem}.name-fixes-{uuid.uuid4().hex[:12]}.csv")
    # Durable original/proposed mapping exists before any changes.
    write_audit(changes, audit, root, "x")
    print("\nFile | Field | Original | Proposed | Status | Reason")
    for change in changes:
        cells = [
            change.path.relative_to(root).as_posix(),
            change.field,
            change.original,
            change.proposed or "(manual review)",
            change.status,
            change.reason,
        ]
        print(
            " | ".join(
                "".join(
                    (
                        f"\\u{ord(char):04x}"
                        if unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                        else char
                    )
                    for char in cell
                ).replace("|", r"\|")
                for cell in cells
            )
        )
    applicable = [change for change in changes if change.status == "proposed"]
    approved = accept and not dry_run
    if applicable and not dry_run and not accept:
        if sys.stdin.isatty():
            try:
                approved = input(
                    f"Apply {len(applicable)} proposed filename/tag changes? [y/N] "
                ).strip().lower() in {"y", "yes"}
            except EOFError:
                approved = False
        else:
            print(
                "No interactive input: changes declined. Use --accept-name-fixes after reviewing a dry run."
            )
    renamed: dict[Path, Path] = {}
    statuses: dict[Path, str] = {}
    failed = False
    for change in applicable:
        change.status = "accepted" if approved else "dry-run" if dry_run else "declined"
    write_audit(changes, audit, root)
    if approved:
        by_path: dict[Path, list[NameChange]] = {}
        for change in applicable:
            by_path.setdefault(change.path, []).append(change)
        backup_root = root / BACKUP_DIR / uuid.uuid4().hex
        for path, group in by_path.items():
            tag_changes = [change for change in group if change.field != "filename"]
            name_changes = [change for change in group if change.field == "filename"]
            if tag_changes:
                backup = backup_root / path.relative_to(root)
                saved = False
                try:
                    metadata = ID3(path, translate=False)
                    for change in tag_changes:
                        if str(metadata[change.field].text[change.index]) != change.original:
                            raise ValueError("tag changed since preview")
                    # Never place backups outside the scan root through an existing link.
                    if (root / BACKUP_DIR).is_symlink() or not backup.resolve().is_relative_to(
                        root
                    ):
                        raise OSError("unsafe backup directory")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)
                    saved = True
                    target_version = 3 if metadata.version[0] == 1 else metadata.version[1]
                    if target_version == 3:
                        metadata.update_to_v23()
                    for change in tag_changes:
                        metadata[change.field].text[change.index] = change.proposed
                        metadata[change.field].encoding = 1 if target_version == 3 else 3
                        change.backup = str(backup)
                    write_audit(changes, audit, root)
                    metadata.save(path, v2_version=target_version, v1=0)
                    for change in tag_changes:
                        change.status = "applied"
                except (MutagenError, OSError, ValueError, KeyError, IndexError) as exc:
                    if saved:
                        shutil.copy2(backup, path)
                    for change in tag_changes:
                        change.status, change.reason = "failed", str(exc)
                    failed = True
            for change in name_changes:
                if change.proposed is None:
                    change.status, change.reason = "failed", "missing proposed filename"
                    failed = True
                    continue
                destination = path.with_name(change.proposed)
                try:
                    if any(
                        p.name.casefold() == destination.name.casefold()
                        for p in path.parent.iterdir()
                    ):
                        raise FileExistsError(f"destination already exists: {destination.name}")
                    rename_no_replace(path, destination)
                    renamed[path] = destination
                    change.status = "applied"
                except OSError as exc:
                    change.status, change.reason = "failed", str(exc)
                    failed = True
            write_audit(changes, audit, root)
    for change in changes:
        status = f"{change.field}: {change.status}"
        statuses[change.path] = "; ".join(filter(None, [statuses.get(change.path), status]))
    print(f"Name-change audit: {audit}")
    for status in sorted({change.status for change in changes}):
        print(f"Name review {status}: {sum(change.status == status for change in changes)}")
    return renamed, statuses, failed
