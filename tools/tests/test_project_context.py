"""
tests/test_project_context.py

Тесты для project_context.py v1.0.3.0
Запуск: pytest tests/test_project_context.py -v
"""

import subprocess
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).parent.parent / "project_context.py"


def make_sample_project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def hello(name):\n" "    return f'hi {name}'\n\n" "class Foo:\n" "    pass\n"
    )
    (tmp_path / "src" / "other.py").write_text("y = 2\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.pyc").write_text("binary-ish")
    return tmp_path


def run_tool(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--root",
            str(tmp_path),
            "--output",
            "-",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def test_venv_excluded(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path)
    assert ".venv" not in result.stdout
    assert "__pycache__" not in result.stdout


def test_signatures_only_extracts_defs_without_body(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--signatures-only")
    assert "def hello(name)" in result.stdout
    assert "class Foo" in result.stdout
    assert "return f'hi" not in result.stdout


def test_grep_filters_irrelevant_files(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--grep", "hello")
    assert "app.py" in result.stdout
    assert "other.py" not in result.stdout


def test_tree_only_has_no_file_contents(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--tree-only")
    assert "PROJECT TREE" in result.stdout
    assert "def hello" not in result.stdout


def test_full_dump_warning_triggered_above_threshold(tmp_path):
    make_sample_project(tmp_path)
    for i in range(45):
        (tmp_path / "src" / f"m{i}.py").write_text("pass\n")
    result = run_tool(tmp_path)
    assert "[warning]" in result.stderr
    assert "Full-dump" in result.stderr


def test_no_warning_when_scoped_with_signatures_only(tmp_path):
    make_sample_project(tmp_path)
    for i in range(45):
        (tmp_path / "src" / f"m{i}.py").write_text("pass\n")
    result = run_tool(tmp_path, "--signatures-only")
    assert "[warning]" not in result.stderr


def test_no_warning_when_scoped_with_grep(tmp_path):
    make_sample_project(tmp_path)
    for i in range(45):
        (tmp_path / "src" / f"m{i}.py").write_text("pass\n")
    result = run_tool(tmp_path, "--grep", "hello")
    assert "[warning]" not in result.stderr


def test_version_flag(tmp_path):
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--version"],
        capture_output=True,
        text=True,
    )
    assert "1.0.3.0" in result.stdout
