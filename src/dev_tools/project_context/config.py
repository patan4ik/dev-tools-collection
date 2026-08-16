"""
config.py

The Config dataclass shared across every mode, plus the frozen-exe
interactive path prompt.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .constants import (
    DEFAULT_EXCLUDE_CONTENT_EXT,
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_EXCLUDE_FILES,
    DEFAULT_INCLUDE_EXT,
    DEFAULT_INCLUDE_NAMES,
    INTERACTIVE_CONFIG_FILENAME,
)


@dataclass
class Config:
    root: Path
    output: str | None
    tree_only: bool
    changed_only: bool
    signatures_only: bool
    graph: bool
    grep_pattern: str | None
    max_chars: int | None
    output_format: str
    clipboard: bool
    report: bool
    integration_scope: str = "standalone"
    diagram_mode: str = "auto"
    diagram_detail: str = "auto"  # {"auto", "file", "group"}
    diagram_imports: str = "collapsed"  # {"collapsed", "all"}
    no_conventions: bool = False
    no_baseline: bool = False
    no_plan_gate: bool = False
    analysis_target: str | None = None
    analysis_max_abstractions: int = 10
    analysis_language: str = "english"
    analysis_include_baseline: bool = False
    include_ext: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_EXT))
    include_names: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_NAMES))
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_content_ext: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_CONTENT_EXT))
    exclude_files: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_FILES))
    use_gitignore: bool = True

    def resolved_diagram_mode(self) -> str:
        if self.diagram_mode != "auto":
            return self.diagram_mode
        return "text" if (self.tree_only or self.signatures_only) else "none"

    def resolved_diagram_detail(self) -> str:
        if self.diagram_detail != "auto":
            return self.diagram_detail
        return "group" if self.tree_only else "file"


def resolve_interactive_path(exe_dir: Path) -> Path:
    config_path = exe_dir / INTERACTIVE_CONFIG_FILENAME
    saved: dict = {}
    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            saved = {}
    default = saved.get("last_path")

    prompt = f"Path to analyze [{default or 'none saved'}]: "
    entered = input(prompt).strip()
    chosen = entered or default
    if not chosen:
        print("No path provided and no saved default. Exiting.")
        sys.exit(1)

    try:
        config_path.write_text(json.dumps({"last_path": chosen}), encoding="utf-8")
    except OSError:
        pass

    return Path(chosen)


def running_as_frozen_exe() -> bool:
    return getattr(sys, "frozen", False)
