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

from project_context.config import Config
from project_context.diagram import (
    detect_module_graph_edges,
    render_module_graph,
    render_module_graph_mermaid,
    render_module_graph_text,
)


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


# ============================================================
# 1. Config.resolved_diagram_mode() / resolved_diagram_detail()
#    -- the exact functions where the "dead code after return" bug
#    hid silently. Direct unit tests, no CLI/subprocess involved.
# ============================================================


def _make_config(tmp_path: Path, **overrides) -> Config:

    defaults = dict(
        root=tmp_path,
        output=None,
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


def test_resolved_diagram_mode_auto_resolves_text_for_tree_only(tmp_path):
    cfg = _make_config(tmp_path, tree_only=True, diagram_mode="auto")
    assert cfg.resolved_diagram_mode() == "text"


def test_resolved_diagram_mode_auto_resolves_none_when_not_scoped(tmp_path):
    cfg = _make_config(tmp_path, diagram_mode="auto")
    assert cfg.resolved_diagram_mode() == "none"


def test_resolved_diagram_mode_explicit_value_overrides_auto(tmp_path):
    cfg = _make_config(tmp_path, diagram_mode="mermaid")
    assert cfg.resolved_diagram_mode() == "mermaid"


def test_resolved_diagram_detail_auto_resolves_group_for_tree_only(tmp_path):
    cfg = _make_config(tmp_path, tree_only=True, diagram_detail="auto")
    assert cfg.resolved_diagram_detail() == "group"


def test_resolved_diagram_detail_auto_resolves_file_otherwise(tmp_path):
    cfg = _make_config(tmp_path, signatures_only=True, diagram_detail="auto")
    assert cfg.resolved_diagram_detail() == "file"


def test_resolved_diagram_detail_explicit_value_overrides_auto(tmp_path):
    cfg = _make_config(tmp_path, tree_only=True, diagram_detail="file")
    assert cfg.resolved_diagram_detail() == "file"


# ============================================================
# 2. render_module_graph() dispatcher -- ZERO prior coverage, despite
#    being the exact function where every missing-()/extra-() bug in
#    this session actually lived.
# ============================================================


def _make_two_file_project(tmp_path: Path) -> list[Path]:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("import b\n")
    b.write_text("def helper():\n    return 1\n")
    return [a, b]


def test_render_module_graph_dispatches_text_mode(tmp_path):

    files = _make_two_file_project(tmp_path)
    cfg = _make_config(tmp_path, tree_only=True, diagram_mode="text")
    result = render_module_graph(files, cfg, entrypoints={})
    assert "MODULE GRAPH" in result
    assert "```mermaid" not in result


def test_render_module_graph_dispatches_mermaid_mode(tmp_path):
    """Regression guard: this assertion is FALSE if resolved_diagram_mode()
    is ever called without parens again -- the dispatcher would silently
    fall through to `return ""` with no exception."""

    files = _make_two_file_project(tmp_path)
    cfg = _make_config(tmp_path, tree_only=True, diagram_mode="mermaid")
    result = render_module_graph(files, cfg, entrypoints={})
    assert "```mermaid" in result


def test_render_module_graph_returns_empty_string_when_mode_none(tmp_path):

    files = _make_two_file_project(tmp_path)
    cfg = _make_config(tmp_path, diagram_mode="none")
    assert render_module_graph(files, cfg, entrypoints={}) == ""


def test_render_module_graph_passes_diagram_detail_through_to_group_mode(tmp_path):
    """Regression guard for the bug where diagram_detail was computed
    correctly by Config but never threaded through render_module_graph()
    into render_module_graph_mermaid() -- output stayed in "file" detail
    regardless of --tree-only."""

    files = _make_two_file_project(tmp_path)
    cfg = _make_config(tmp_path, tree_only=True, diagram_mode="mermaid", diagram_detail="auto")
    result = render_module_graph(files, cfg, entrypoints={})
    assert "subgraph" not in result  # group mode never emits subgraph blocks
    assert "g_root" in result or "g_" in result  # collapsed group node prefix present


# ============================================================
# 3. --diagram-detail group: node/edge aggregation
#    (today's actual feature -- previously zero coverage)
# ============================================================


def test_group_detail_collapses_same_group_files_into_one_node(tmp_path):

    files = _make_two_file_project(tmp_path)  # both at root -> same "root" group
    result = render_module_graph_mermaid(files, tmp_path, entrypoints={}, diagram_detail="group")
    assert result.count('g_root["root') == 1
    assert "n_a_py" not in result  # per-file node id must not appear in group mode
    assert "n_b_py" not in result


def test_group_detail_drops_same_group_edges_as_self_loops(tmp_path):
    """The 'imports' edge between a.py and b.py (both in the same
    root-level group) must NOT be rendered -- it would be a self-loop
    on the single collapsed node, adding no information."""

    files = _make_two_file_project(tmp_path)
    result = render_module_graph_mermaid(files, tmp_path, entrypoints={}, diagram_detail="group")
    assert '"imports"' not in result


def test_group_detail_keeps_cross_group_edges_deduplicated(tmp_path):
    """Multiple underlying test->source edges between two DIFFERENT
    groups must collapse into exactly one rendered edge per relationship
    type, not one per underlying file pair."""

    (tmp_path / "tests").mkdir()
    src_a = tmp_path / "a.py"
    src_b = tmp_path / "b.py"
    src_a.write_text("def foo():\n    return 1\n")
    src_b.write_text("def bar():\n    return 2\n")
    test_1 = tmp_path / "tests" / "test_a.py"
    test_2 = tmp_path / "tests" / "test_b.py"
    test_1.write_text("import pytest\ndef test_foo():\n    assert a.foo() == 1\n")
    test_2.write_text("import pytest\ndef test_bar():\n    assert b.bar() == 2\n")

    files = [src_a, src_b, test_1, test_2]
    result = render_module_graph_mermaid(files, tmp_path, entrypoints={}, diagram_detail="group")
    # Exactly one "validates" edge between the two groups, regardless of
    # how many individual test<->source file pairs produced it.
    assert result.count('"validates"') == 1


def test_group_detail_never_groups_synthetic_nodes(tmp_path):
    """Regression guard for the earlier bug where 'Distribution / build
    artifact' (a plain-text label containing '/', not a real path) was
    mistakenly split on '/' and wrapped in its own bogus one-node
    subgraph. Synthetic nodes must always render standalone."""

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    ci_file = tmp_path / ".github" / "workflows" / "build.yml"
    ci_file.write_text("jobs:\n  build:\n    steps:\n      - run: pyinstaller app.py\n")
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("[project]\nname = 'x'\n")

    files = [ci_file, manifest]
    result = render_module_graph_mermaid(files, tmp_path, entrypoints={}, diagram_detail="group")
    assert "g_Distribution" not in result  # no bogus group for the synthetic node
    assert "Distribution / build artifact" in result  # still rendered, just standalone


# ============================================================
# 4. --diagram-imports collapsed/all (previously zero coverage)
# ============================================================


def test_diagram_imports_collapsed_hides_same_group_import_edges(tmp_path):

    files = _make_two_file_project(tmp_path)  # a.py imports b.py, same group
    result = render_module_graph_mermaid(
        files, tmp_path, entrypoints={}, diagram_imports="collapsed", diagram_detail="file"
    )
    assert (
        '|"imports"|' not in result
    )  # no rendered edge, only the summary comment mentions "imports"
    assert (
        "hidden for readability" in result
    )  # confirms suppression actually happened, not silently skipped


def test_diagram_imports_all_shows_same_group_import_edges(tmp_path):

    files = _make_two_file_project(tmp_path)
    result = render_module_graph_mermaid(
        files, tmp_path, entrypoints={}, diagram_imports="all", diagram_detail="file"
    )
    assert '"imports"' in result


# ============================================================
# 5. click-link correctness (regression guard for the missing "/" bug)
# ============================================================


def test_click_links_have_slash_between_remote_prefix_and_path(tmp_path, monkeypatch):
    from project_context import diagram as diagram_module

    monkeypatch.setattr(
        diagram_module,
        "get_git_remote_url",
        lambda root: "https://github.com/owner/repo/blob/main/",
    )
    files = _make_two_file_project(tmp_path)
    result = diagram_module.render_module_graph_mermaid(
        files, tmp_path, entrypoints={}, diagram_detail="file"
    )
    assert "/blob/main/a.py" in result
    assert "/blob/maina.py" not in result


def test_group_node_label_sanitizes_parens_in_filename(tmp_path):
    (tmp_path / "config (copy).py").write_text("x = 1\n")
    files = [tmp_path / "config (copy).py"]
    # entry_points must reference the file so detect_module_graph_edges()
    # produces at least one real edge -- otherwise render_module_graph_mermaid()
    # returns "" before reaching any label-construction code at all.
    entrypoints = {"my-tool": "config (copy).py"}
    result = render_module_graph_mermaid(
        files, tmp_path, entrypoints=entrypoints, diagram_detail="group"
    )
    assert result != ""  # sanity check: the function actually rendered something
    assert "config (copy).py" not in result
    assert "config copy.py" in result


def test_file_detail_node_label_sanitizes_parens_in_filename(tmp_path):
    (tmp_path / "config (copy).py").write_text("x = 1\n")
    files = [tmp_path / "config (copy).py"]
    entrypoints = {"my-tool": "config (copy).py"}
    result = render_module_graph_mermaid(
        files, tmp_path, entrypoints=entrypoints, diagram_detail="file"
    )
    assert result != ""
    assert "config (copy).py" not in result
    assert "config copy.py" in result


def test_sanitize_mermaid_label_text_strips_quotes_and_parens():
    from project_context.diagram import _sanitize_mermaid_label_text

    assert _sanitize_mermaid_label_text('some"text(with)parens"') == "some'textwithparens'"
