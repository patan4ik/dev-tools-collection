"""
tests/test_astutils.py

Unit tests for src/dev_tools/project_context/astutils.py -- direct
import, no subprocess. Covers AST-based signature extraction, import
graphs, test-module detection by language signature (not filename),
and PEP 621 [project.scripts] entry-point resolution, including the
v2.0.2 fix for standard src-layout dotted-path stripping.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "dev_tools"))

from project_context.astutils import (
    build_dependency_graph,
    detect_entry_points,
    extract_imports,
    extract_signatures,
    is_source_module,
    is_test_module,
    lang_for_highlight,
)


def test_extract_signatures_lists_functions_and_classes_without_bodies(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("def hello(name):\n    return f'hi {name}'\n\nclass Foo:\n    pass\n")

    sig = extract_signatures(f)

    assert "def hello(name)" in sig
    assert "class Foo" in sig
    assert "return f'hi" not in sig


def test_extract_signatures_returns_empty_for_non_python_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("def hello(): pass\n")
    assert extract_signatures(f) == ""


def test_extract_imports_collects_top_level_module_names(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("import os\nimport sys as _sys\nfrom pathlib import Path\n")

    imports = extract_imports(f)

    assert "os" in imports
    assert "sys" in imports
    assert "pathlib" in imports


def test_build_dependency_graph_detects_real_import_edge(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("from b import helper\ndef use():\n    return helper()\n")
    b.write_text("def helper():\n    return 1\n")

    depends_on, used_by = build_dependency_graph([a, b], tmp_path)

    assert depends_on["a.py"] == ["b.py"]
    assert used_by["b.py"] == ["a.py"]
    assert used_by["a.py"] == []


def test_build_dependency_graph_module_never_depends_on_itself(tmp_path):
    f = tmp_path / "self_ref.py"
    f.write_text("import self_ref\n")

    depends_on, _used_by = build_dependency_graph([f], tmp_path)

    assert depends_on["self_ref.py"] == []


def test_is_test_module_detects_by_pytest_import_not_filename(tmp_path):
    """Detection must be by language-level signature (pytest/unittest
    import, TestCase subclass, pytest decorator, or bare assert), never
    by a hardcoded 'test_*.py' filename pattern."""
    f = tmp_path / "verify_calc.py"
    f.write_text("import pytest\n\ndef test_multiply():\n    assert 2 * 3 == 6\n")

    assert is_test_module(f) is True


def test_is_test_module_detects_by_bare_assert_alone(tmp_path):
    f = tmp_path / "checks.py"
    f.write_text("def check():\n    assert True\n")
    assert is_test_module(f) is True


def test_is_test_module_false_for_plain_module(tmp_path):
    f = tmp_path / "lib.py"
    f.write_text("def helper():\n    return 1\n")
    assert is_test_module(f) is False


def test_is_test_module_false_for_non_python_file(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("assert this is markdown\n")
    assert is_test_module(f) is False


def test_is_source_module_excludes_tests_and_init(tmp_path):
    src = tmp_path / "app.py"
    src.write_text("def helper():\n    return 1\n")
    test_f = tmp_path / "test_app.py"
    test_f.write_text("import pytest\ndef test_x():\n    assert True\n")
    init_f = tmp_path / "__init__.py"
    init_f.write_text("")

    assert is_source_module(src) is True
    assert is_source_module(test_f) is False
    assert is_source_module(init_f) is False


def test_detect_entry_points_strips_leading_src_for_dotted_path(tmp_path):
    """v2.0.2 fix: a standard src-layout project declares
    'pkg.cli:main' in pyproject.toml, but the file physically lives at
    src/pkg/cli.py. The dotted-path lookup must strip the leading
    'src/' segment before matching, or entry points are silently never
    detected on any standard src-layout project."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    cli_file = tmp_path / "src" / "pkg" / "cli.py"
    cli_file.write_text("def main():\n    pass\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\n\n' "[project.scripts]\n" 'my-tool = "pkg.cli:main"\n'
    )

    mapping = detect_entry_points([pyproject, cli_file], tmp_path)

    assert mapping == {"my-tool": "src/pkg/cli.py"}


def test_detect_entry_points_returns_empty_without_pyproject(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("def main():\n    pass\n")
    assert detect_entry_points([f], tmp_path) == {}


def test_lang_for_highlight_maps_known_extensions():
    assert lang_for_highlight(Path("a.py")) == "python"
    assert lang_for_highlight(Path("a.md")) == "markdown"
    assert lang_for_highlight(Path("a.unknown")) == ""
