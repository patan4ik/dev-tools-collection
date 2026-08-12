"""
tests/test_diagram.py

Unit tests for src/dev_tools/project_context/diagram.py -- covers the
--diagram GitDiagram-parity edges (registers entry point, belongs to,
documents, packages, validates, runs, invokes) that were previously
only exercised end-to-end via subprocess, never at the function level.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "dev_tools"))

from project_context.diagram import detect_module_graph_edges, render_module_graph_text


def test_belongs_to_edge_for_nested_package(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sub").mkdir()
    root_init = tmp_path / "pkg" / "__init__.py"
    root_init.write_text("")
    sub_init = tmp_path / "pkg" / "sub" / "__init__.py"
    sub_init.write_text("")

    files = [root_init, sub_init]
    _labels, edges = detect_module_graph_edges(files, tmp_path, entry_points={})

    assert ("pkg/sub/__init__.py", "pkg/__init__.py", "belongs to", False) in edges


def test_invokes_edge_only_created_when_entry_point_exists(tmp_path):
    cli_file = tmp_path / "cli.py"
    cli_file.write_text("def main():\n    pass\n")

    labels, edges = detect_module_graph_edges(
        [cli_file], tmp_path, entry_points={"my-tool": "cli.py"}
    )

    assert labels.get("User / automation invoker") == "actor"
    assert ("User / automation invoker", "cli.py", "invokes", False) in edges


def test_no_actor_nodes_when_no_entry_points_detected(tmp_path):
    plain_file = tmp_path / "lib.py"
    plain_file.write_text("def helper():\n    pass\n")

    labels, _edges = detect_module_graph_edges([plain_file], tmp_path, entry_points={})

    assert "User / automation invoker" not in labels


def test_render_module_graph_text_lists_isolated_nodes(tmp_path):
    """Parity fix: text mode must list bare, edge-less nodes too, not
    just files that participate in a detected relationship."""
    isolated_file = tmp_path / "standalone.py"
    isolated_file.write_text("x = 1\n")

    result = render_module_graph_text([isolated_file], tmp_path, entry_points={})
    assert "no detected relationships" in result
    assert "standalone.py" in result


def test_render_module_graph_text_empty_when_no_edges_and_no_files():
    result = render_module_graph_text([], Path("."), entry_points={})
    assert result == ""
