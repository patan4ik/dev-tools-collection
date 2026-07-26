"""
tests/test_project_context.py

Tests for cli.py v1.7.0
Run: pytest tests/cli.py -v
"""

import re
import subprocess
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).parent.parent / "src" / "dev_tools" / "project_context" / "cli.py"


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


def make_project_with_conventions(tmp_path: Path) -> Path:
    """Sample project with tests/, pyproject.toml with lint tools, a CI
    workflow, and a documented, snake_case-styled module — used to exercise
    detect_conventions() end to end."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        '"""Calc module."""\n\n'
        "def add_numbers(a, b):\n"
        '    """Add two numbers."""\n'
        "    return a + b\n\n"
        "def subtract_numbers(a, b):\n"
        '    """Subtract two numbers."""\n'
        "    return a - b\n"
    )
    (tmp_path / "src" / "uncovered.py").write_text("def orphan_function():\n    return None\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    assert True\n")

    (tmp_path / "pyproject.toml").write_text(
        "[tool.black]\n"
        "line-length = 88\n\n"
        "[tool.ruff]\n"
        "line-length = 88\n\n"
        "[tool.mypy]\n"
        "strict = true\n\n"
        "[tool.bandit]\n"
        'exclude_dirs = ["tests"]\n\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
    )

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\n"
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - run: pytest --cov\n"
        "      - run: mypy src\n"
        "      - run: bandit -r src\n"
    )

    (tmp_path / "requirements.txt").write_text("requests\n")
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
    (tmp_path / "a.py").write_text("from b import helper\ndef use():\n    return helper()\n")
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


# --------------------------------------------------------------------------- #
# PROJECT CONVENTIONS DETECTED — v1.7.0
# --------------------------------------------------------------------------- #


def test_conventions_section_present_by_default(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path)
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout


def test_conventions_section_absent_with_flag(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--no-conventions")
    assert "PROJECT CONVENTIONS DETECTED" not in result.stdout


def test_conventions_appear_in_tree_only_mode(tmp_path):
    """The whole point of the feature: conventions must not be skippable
    just because a lightweight mode is used."""
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--tree-only")
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout


def test_conventions_appear_in_signatures_only_mode(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--signatures-only")
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout


def test_conventions_appear_in_graph_mode(tmp_path):
    (tmp_path / "a.py").write_text("def use():\n    return 1\n")
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
    index_content = (tmp_path / "graph_out" / "index.md").read_text()
    assert "PROJECT CONVENTIONS DETECTED" in index_content


def test_conventions_detects_test_coverage_pairs(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "src/calc.py" in result.stdout
    assert "src/uncovered.py" in result.stdout
    assert "tests/test_<module>.py" in result.stdout


def test_conventions_detects_lint_tools(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "Black" in result.stdout
    assert "Ruff" in result.stdout
    assert "mypy" in result.stdout
    assert "Bandit" in result.stdout


def test_conventions_detects_ci_required_checks(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "pytest" in result.stdout
    assert "mypy" in result.stdout
    assert "bandit" in result.stdout
    assert "coverage" in result.stdout


def test_conventions_detects_dependency_files(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "requirements.txt" in result.stdout
    assert "pyproject.toml" in result.stdout


def test_conventions_detects_naming_and_docstring_style(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "snake_case" in result.stdout


def test_conventions_no_ci_workflow_reports_none_detected(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path)
    assert "No CI workflow detected" in result.stdout


def test_conventions_scoped_scan_still_sees_full_project(tmp_path):
    """Conventions must be detected from the WHOLE project, not just the
    subset of files matched by --grep/--changed-only for this run."""
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--grep", "add_numbers")
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout
    assert "src/uncovered.py" in result.stdout


def test_conventions_xml_format_includes_section(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--format", "xml")
    assert "<conventions>" in result.stdout
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout
