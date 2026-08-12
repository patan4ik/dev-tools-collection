"""
tests/test_benchmark.py

Migrated from the pre-split tests/test_project_context.py -- covers
--report: full/tree-only/signatures-only/graph (and grep, if set)
token/character comparison table via tiktoken (cl100k_base). Runs via
the CLI subprocess (unchanged from the original) since --report
exercises the full render pipeline end to end across every mode.
"""

import subprocess
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).parent.parent / "src" / "dev_tools" / "project_context" / "cli.py"


def make_sample_project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def hello(name):\n    return f'hi {name}'\n\nclass Foo:\n    pass\n"
    )
    (tmp_path / "src" / "other.py").write_text("y = 2\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.pyc").write_text("binary-ish")
    return tmp_path


def test_report_prints_comparison_table(tmp_path):
    make_sample_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--report"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Mode" in result.stdout
    assert "full" in result.stdout
    assert "signatures-only" in result.stdout
    assert "graph" in result.stdout


def test_report_includes_tree_only_row(tmp_path):
    """v1.9.1: --report previously omitted tree-only entirely, the
    smallest and most commonly used lightweight mode -- must be
    represented in the comparison table."""
    make_sample_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--report"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "tree-only" in result.stdout


def test_report_includes_grep_row_when_pattern_given(tmp_path):
    make_sample_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--report", "--grep", "hello"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "grep:hello" in result.stdout


def test_report_does_not_write_output_file(tmp_path):
    make_sample_project(tmp_path)
    subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--report"],
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / "project_context.md").exists()


def test_report_includes_analysis_row_only_when_target_given(tmp_path):
    """--report must add an analysis:<repo-name> row only when
    --analysis is explicitly passed -- there is no default second
    repository to benchmark, so nothing should be faked when absent."""
    make_sample_project(tmp_path)
    other_repo = tmp_path.parent / "other_repo_for_analysis"
    other_repo.mkdir(exist_ok=True)
    (other_repo / "lib.py").write_text('"""A tiny library."""\ndef helper():\n    return 1\n')

    result_without = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--report"],
        capture_output=True,
        text=True,
    )
    assert "analysis:" not in result_without.stdout

    result_with = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--root",
            str(tmp_path),
            "--report",
            "--analysis",
            str(other_repo),
        ],
        capture_output=True,
        text=True,
    )
    assert result_with.returncode == 0
    assert "analysis:other_repo_for_analysis" in result_with.stdout
