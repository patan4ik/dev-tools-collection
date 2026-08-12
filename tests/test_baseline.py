"""
tests/test_baseline.py

Migrated from the pre-split tests/test_project_context.py -- covers
MANDATORY BASELINE FILES + ARCHITECTURE PLAN GATE (v1.8.0+): verbatim
contract-file embedding, reference test/source file selection, the
SENIOR-DEVELOPER MANDATE + Step 1/Step 2 plan gate, and the "none
detected" fallback wording. Runs via the CLI subprocess (unchanged
from the original).
"""

import subprocess
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).parent.parent / "src" / "dev_tools" / "project_context" / "cli.py"


def make_project_with_conventions(tmp_path: Path) -> Path:
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
        "[project]\n"
        'name = "sample"\n'
        'version = "0.1.0"\n\n'
        "[tool.black]\n"
        "line-length = 88\n\n"
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest --cov\n"
    )
    (tmp_path / "requirements.txt").write_text("requests\n")
    return tmp_path


def run_tool(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--output", "-", *args],
        capture_output=True,
        text=True,
    )


def test_baseline_section_present_by_default(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "MANDATORY BASELINE FILES" in result.stdout
    assert "[tool.black]" in result.stdout  # verbatim pyproject.toml content
    assert "requests" in result.stdout  # verbatim requirements.txt content


def test_baseline_section_absent_with_no_baseline_flag(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--no-baseline")
    assert "MANDATORY BASELINE FILES" not in result.stdout
    assert "ARCHITECTURE PLAN" not in result.stdout


def test_baseline_appears_in_tree_only_mode(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--tree-only")
    assert "MANDATORY BASELINE FILES" in result.stdout


def test_plan_gate_present_by_default(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "STEP 1" in result.stdout
    assert "ARCHITECTURE PLAN" in result.stdout
    assert "STEP 2" in result.stdout
    assert "SELF-VALIDATION CHECKLIST" in result.stdout


def test_plan_gate_absent_with_no_plan_gate_flag_but_baseline_kept(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--no-plan-gate")
    assert "MANDATORY BASELINE FILES" in result.stdout
    assert "STEP 1" not in result.stdout
    assert "SELF-VALIDATION CHECKLIST" not in result.stdout


def test_reference_test_file_embedded_verbatim(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "Reference test file" in result.stdout
    assert "def test_add():" in result.stdout


def test_baseline_reports_none_detected_when_project_is_empty(tmp_path):
    (tmp_path / "readme.txt").write_text("hello\n")
    result = run_tool(tmp_path)
    assert "No baseline contract files or code exemplars were detected" in result.stdout


def test_integration_scope_standalone_is_default(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "standalone" in result.stdout
    assert "STANDALONE" in result.stdout


def test_integration_scope_integrated_changes_plan_wording(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--integration-scope", "integrated")
    assert "integrated" in result.stdout
    assert "INTEGRATED" in result.stdout
