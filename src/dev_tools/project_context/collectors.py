"""
collectors.py

File discovery: .gitignore parsing, git changed-file detection, and the
main collect_files()/collect_all_project_files() pipeline.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import Config
from .constants import (
    FULL_DUMP_FILE_WARNING_THRESHOLD,
    GENERATED_MARKER_FILENAME,
    MAX_FILE_SIZE_BYTES,
)
from .utils import dir_has_generated_marker


def load_gitignore_patterns(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns = []
    for line in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def is_gitignored(rel_path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(rel_path, pat_clean) or fnmatch.fnmatch(
            os.path.basename(rel_path), pat_clean
        ):
            return True
        if fnmatch.fnmatch(rel_path, f"{pat_clean}/*") or fnmatch.fnmatch(
            rel_path, f"*/{pat_clean}/*"
        ):
            return True
    return False


def get_changed_files(root: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "Warning: git was not found or this is not a git repository. --changed-only is ignored.",
            file=sys.stderr,
        )
        return set()

    changed = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            path = parts[1].split(" -> ")[-1]
            changed.add(path)
    return changed


def get_git_remote_url(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    url = result.stdout.strip()
    match = re.search(r"github\.com[:/]([\w.\-]+)/([\w.\-]+?)(?:\.git)?$", url)
    if not match:
        return None
    owner, repo = match.groups()
    branch = "main"
    try:
        branch_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        if branch_result.stdout.strip():
            branch = branch_result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return f"https://github.com/{owner}/{repo}/blob/{branch}"


def matches_grep(path: Path, pattern: re.Pattern) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return pattern.search(content) is not None


def should_skip_dir(dirname: str, cfg: Config, parent: Path | None = None) -> bool:
    for pattern in cfg.exclude_dirs:
        if fnmatch.fnmatch(dirname, pattern):
            return True
    if parent is not None and dir_has_generated_marker(parent / dirname):
        return True
    return False


def should_include_file(path: Path, cfg: Config) -> bool:
    name = path.name
    if name == GENERATED_MARKER_FILENAME:
        return False
    if name in cfg.exclude_files:
        return False
    if name in cfg.include_names:
        return True
    return path.suffix in cfg.include_ext


def collect_files(cfg: Config) -> list[Path]:
    gitignore_patterns = load_gitignore_patterns(cfg.root) if cfg.use_gitignore else []
    changed_files = get_changed_files(cfg.root) if cfg.changed_only else None
    grep_re = re.compile(cfg.grep_pattern, re.IGNORECASE) if cfg.grep_pattern else None

    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        parent = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d, cfg, parent)]

        for filename in filenames:
            full_path = parent / filename
            rel_path = str(full_path.relative_to(cfg.root)).replace(os.sep, "/")

            if gitignore_patterns and is_gitignored(rel_path, gitignore_patterns):
                continue
            if not should_include_file(full_path, cfg):
                continue
            if changed_files is not None and rel_path not in changed_files:
                continue
            if grep_re is not None and not matches_grep(full_path, grep_re):
                continue

            collected.append(full_path)

    return sorted(collected)


def collect_all_project_files(cfg: Config) -> list[Path]:
    scan_cfg = replace(
        cfg,
        changed_only=False,
        grep_pattern=None,
        tree_only=False,
        signatures_only=False,
        graph=False,
    )
    return collect_files(scan_cfg)


def read_file_content(path: Path, cfg: Config) -> str | None:
    if path.suffix in cfg.exclude_content_ext:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_SIZE_BYTES:
        return f"[file skipped: size {size} bytes exceeds the {MAX_FILE_SIZE_BYTES} limit]"

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"

    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def warn_if_full_dump_overload(files: list[Path], cfg: Config) -> None:
    is_scoped = (
        cfg.tree_only
        or cfg.changed_only
        or cfg.signatures_only
        or cfg.graph
        or cfg.grep_pattern is not None
    )
    if not is_scoped and len(files) > FULL_DUMP_FILE_WARNING_THRESHOLD:
        print(
            f"[warning] Full-dump mode with {len(files)} files may overload "
            "the LLM's context and reduce answer quality. Consider "
            "--changed-only, --signatures-only, --graph, or --grep for a "
            "more targeted context.",
            file=sys.stderr,
        )
