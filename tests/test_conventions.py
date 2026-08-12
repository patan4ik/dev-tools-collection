"""
tests/test_conventions.py

Migrated from the pre-split tests/test_project_context.py -- covers
PROJECT CONVENTIONS DETECTED (v1.7.0+): test coverage pairing,
lint/CI/security tooling, dependency manifests, naming/docstring
style, and README/CHANGELOG presence. Runs via the CLI subprocess
(unchanged from the original) since these are inherently
whole-project, cross-file detections best verified end to end.
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


def make_project_with_conventions(tmp_path: Path) -> Path:
    """Sample project with tests/, pyproject.toml with lint tools, a CI
    workflow, and a documented, snake_case-styled module -- used to
    exercise detect_conventions() end to end."""
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


def make_project_with_nonstandard_names(tmp_path: Path) -> Path:
    """Sample project that deliberately avoids every conventional
    filename (no pyproject.toml, no tests/, no test_*.py, no
    .github/workflows) to verify that role/content-based detection
    does not depend on hardcoded names."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "calc2.py").write_text("def multiply(a, b):\n    return a * b\n")

    (tmp_path / "lib" / "verify_calc2.py").write_text(
        "import pytest\n\n"
        "from lib.calc2 import multiply\n\n"
        "def test_multiply():\n"
        "    assert multiply(2, 3) == 6\n"
    )

    (tmp_path / "app_manifest.toml").write_text(
        '[project]\nname = "nonstandard"\nversion = "1.0"\n'
    )

    (tmp_path / ".gitlab-ci.yml").write_text(
        "test:\n  script:\n    - pytest --cov\n    - mypy lib\n"
    )
    return tmp_path


def run_tool(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), "--root", str(tmp_path), "--output", "-", *args],
        capture_output=True,
        text=True,
    )


def test_conventions_section_present_by_default(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path)
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout


def test_conventions_section_absent_with_flag(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--no-conventions")
    assert "PROJECT CONVENTIONS DETECTED" not in result.stdout


def test_conventions_appear_in_tree_only_mode(tmp_path):
    """The whole point of the feature: conventions must not be
    skippable just because a lightweight mode is used."""
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
    assert "tests/test_calc.py" in result.stdout
    assert "by language-level signature, not filename" in result.stdout


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
    assert "No CI configuration detected" in result.stdout


def test_conventions_scoped_scan_still_sees_full_project(tmp_path):
    """Conventions must be detected from the WHOLE project, not just
    the subset of files matched by --grep/--changed-only for this run."""
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path, "--grep", "add_numbers")
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout
    assert "src/uncovered.py" in result.stdout


def test_conventions_xml_format_includes_section(tmp_path):
    make_sample_project(tmp_path)
    result = run_tool(tmp_path, "--format", "xml")
    assert "<project_context>" in result.stdout
    assert "PROJECT CONVENTIONS DETECTED" in result.stdout
    assert "<conventions_detected><![CDATA[" in result.stdout
    assert "<mandatory_baseline><![CDATA[" in result.stdout


def test_dependency_manifest_detected_by_content_not_filename(tmp_path):
    make_project_with_nonstandard_names(tmp_path)
    result = run_tool(tmp_path)
    assert "app_manifest.toml" in result.stdout
    assert "Dependency manifest" in result.stdout


def test_ci_config_detected_for_non_github_provider(tmp_path):
    make_project_with_nonstandard_names(tmp_path)
    result = run_tool(tmp_path)
    assert ".gitlab-ci.yml" in result.stdout
    assert "pytest" in result.stdout
    assert "mypy" in result.stdout


def test_test_module_detected_without_conventional_naming(tmp_path):
    """A file named verify_calc2.py, outside any 'tests/' folder, must
    still be recognized as a test module purely from its AST signature
    (pytest import + assert), proving detection is not
    filename/folder based."""
    make_project_with_nonstandard_names(tmp_path)
    result = run_tool(tmp_path)
    assert "verify_calc2.py" in result.stdout
    assert "by language-level signature, not filename" in result.stdout


def test_pytest_testpaths_respected(tmp_path):
    make_project_with_conventions(tmp_path)
    result = run_tool(tmp_path)
    assert "pytest is configured to look for tests in" in result.stdout
