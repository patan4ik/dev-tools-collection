"""
tests/test_collectors.py

Unit tests for src/dev_tools/project_context/collectors.py -- direct
import, no subprocess. Covers the v2.0.2 marker-file self-exclusion
mechanism, which was NOT covered by the pre-split test suite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "dev_tools"))

from project_context.collectors import (
    collect_files,
    dir_has_generated_marker,
)
from project_context.config import Config
from project_context.constants import GENERATED_MARKER_FILENAME


def make_config(root: Path, **overrides) -> Config:
    defaults = dict(
        root=root,
        output="-",
        tree_only=False,
        changed_only=False,
        signatures_only=False,
        graph=False,
        grep_pattern=None,
        max_chars=None,
        output_format="md",
        clipboard=False,
        report=False,
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_marker_file_excludes_own_generated_output(tmp_path):
    """v2.0.2: a directory tagged with .project_context_generated must be
    skipped on every future scan, regardless of its name -- this is what
    prevents --analysis from re-ingesting its own prior tutorial output."""
    (tmp_path / "src.py").write_text("x = 1\n")
    generated_dir = tmp_path / "project_context_output"
    generated_dir.mkdir()
    (generated_dir / GENERATED_MARKER_FILENAME).write_text("marker")
    (generated_dir / "tutorial_context.md").write_text("stale output")

    cfg = make_config(tmp_path)
    files = collect_files(cfg)
    rel_paths = {f.relative_to(tmp_path).as_posix() for f in files}

    assert "src.py" in rel_paths
    assert not any("project_context_output" in p for p in rel_paths)


def test_marker_file_not_excluded_without_sentinel(tmp_path):
    """A same-named folder WITHOUT the marker file must still be scanned
    normally -- exclusion is based on the sentinel, not the folder name."""
    (tmp_path / "src.py").write_text("x = 1\n")
    other_dir = tmp_path / "project_context_output"
    other_dir.mkdir()
    (other_dir / "real_docs.md").write_text("legit content")

    cfg = make_config(tmp_path)
    files = collect_files(cfg)
    rel_paths = {f.relative_to(tmp_path).as_posix() for f in files}

    assert "project_context_output/real_docs.md" in rel_paths


def test_dir_has_generated_marker_detects_sentinel(tmp_path):
    marked = tmp_path / "marked"
    marked.mkdir()
    assert dir_has_generated_marker(marked) is False
    (marked / GENERATED_MARKER_FILENAME).write_text("marker")
    assert dir_has_generated_marker(marked) is True


def test_generated_marker_file_itself_never_included(tmp_path):
    """The sentinel file must never appear in the collected file list --
    it's tool metadata, not project source content."""
    (tmp_path / GENERATED_MARKER_FILENAME).write_text("marker")
    (tmp_path / "real.py").write_text("x = 1\n")

    cfg = make_config(tmp_path)
    files = collect_files(cfg)
    names = {f.name for f in files}

    assert GENERATED_MARKER_FILENAME not in names
    assert "real.py" in names
