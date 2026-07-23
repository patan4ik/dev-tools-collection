"""
tests/test_project_context.py

Тесты для project_context.py v1.0.3.0
Запуск: pytest tests/test_project_context.py -v
"""

import subprocess
import sys
from pathlib import Path
import re

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


def test_graph_mode_creates_linked_files(tmp_path):
    (tmp_path / "a.py").write_text(
        "from b import helper\ndef use():\n    return helper()\n"
    )
    (tmp_path / "b.py").write_text("def helper():\n    return 1\n")
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--root",
            str(tmp_path),
            "--graph",
            "--output",
            str(tmp_path / "graph_out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    graph_dir = tmp_path / "graph_out"
    assert graph_dir.exists()
    a_content = (graph_dir / "a_py.md").read_text()
    assert "depends_on: [b.py]" in a_content
    assert "[b.py](./b_py.md)" in a_content


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
    source = TOOL_PATH.read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    assert match, "VERSION constant not found in project_context.py"
    expected_version = match.group(1)
    assert expected_version in result.stdout


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


def test_report_includes_grep_row_when_pattern_given(tmp_path):
    make_sample_project(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--root",
            str(tmp_path),
            "--report",
            "--grep",
            "hello",
        ],
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
