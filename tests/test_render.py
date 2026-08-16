"""
tests/test_render.py

Unit tests for src/dev_tools/project_context/render.py -- direct
import against the REAL Config dataclass and collect_files(), no
subprocess. Covers render_markdown/render_xml (single-file output)
and render_graph (OKF-flavored multi-file output), independent of
conventions/baseline (disabled via cfg flags to isolate render.py's
own responsibility: assembling sections into final text/files).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "dev_tools"))

from project_context.collectors import collect_files
from project_context.config import Config
from project_context.render import (
    render_graph,
    render_markdown,
    render_xml,
    split_by_max_chars,
    write_output,
)


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
        no_conventions=True,
        no_baseline=True,  # isolate render.py from conventions/baseline
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_render_markdown_includes_tree_and_file_contents(tmp_path):
    (tmp_path / "app.py").write_text("def hello():\n    return 1\n")
    cfg = make_config(tmp_path)
    files = collect_files(cfg)

    text = render_markdown(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert "# PROJECT CONTEXT" in text
    assert "## PROJECT TREE" in text
    assert "## FILE CONTENTS" in text
    assert "def hello():" in text


def test_render_markdown_tree_only_omits_file_contents(tmp_path):
    (tmp_path / "app.py").write_text("def hello():\n    return 1\n")
    cfg = make_config(tmp_path, tree_only=True)
    files = collect_files(cfg)

    text = render_markdown(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert "## PROJECT TREE" in text
    assert "## FILE CONTENTS" not in text
    assert "def hello():" not in text


def test_render_markdown_signatures_only_omits_bodies(tmp_path):
    (tmp_path / "app.py").write_text("def hello(name):\n    return f'hi {name}'\n")
    cfg = make_config(tmp_path, signatures_only=True)
    files = collect_files(cfg)

    text = render_markdown(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert "## SIGNATURES" in text
    assert "def hello(name)" in text
    assert "return f'hi" not in text


def test_render_xml_produces_well_formed_wrapper(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cfg = make_config(tmp_path, output_format="xml")
    files = collect_files(cfg)

    text = render_xml(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert text.startswith("<project_context>")
    assert text.strip().endswith("</project_context>")
    assert "<tree><![CDATA[" in text


def test_render_graph_creates_depends_on_and_used_by_links(tmp_path):
    (tmp_path / "a.py").write_text("from b import helper\ndef use():\n    return helper()\n")
    (tmp_path / "b.py").write_text("def helper():\n    return 1\n")
    cfg = make_config(tmp_path, graph=True)
    files = collect_files(cfg)

    graph_files = render_graph(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert "index.md" in graph_files
    a_content = graph_files["a_py.md"]
    assert "depends_on: [b.py]" in a_content
    assert "[b.py](./b_py.md)" in a_content
    b_content = graph_files["b_py.md"]
    assert "used_by: [a.py]" in b_content


def test_render_graph_module_with_no_functions_reports_empty(tmp_path):
    (tmp_path / "empty.py").write_text("x = 1\n")
    cfg = make_config(tmp_path, graph=True)
    files = collect_files(cfg)

    graph_files = render_graph(files, cfg, conventions=None, baseline=None, reference_test=None)

    assert "_[no top-level functions/classes]_" in graph_files["empty_py.md"]


def test_split_by_max_chars_respects_limit():
    text = "a" * 100
    chunks = split_by_max_chars(text, 40)
    assert len(chunks) == 3
    assert all(len(c) <= 40 for c in chunks)
    assert "".join(chunks) == text


def test_split_by_max_chars_single_chunk_when_under_limit():
    text = "short"
    assert split_by_max_chars(text, 100) == ["short"]


def test_write_output_splits_into_numbered_part_files(tmp_path):
    cfg = make_config(tmp_path, output=str(tmp_path / "out.md"), max_chars=10)
    written = write_output("a" * 25, cfg)

    assert len(written) == 3
    assert written[0].name == "out_part1.md"
    assert written[1].name == "out_part2.md"
    assert all(p.exists() for p in written)


def test_write_output_stdout_mode_returns_no_paths(tmp_path, capsys):
    cfg = make_config(tmp_path, output=None)
    written = write_output("hello world", cfg)

    assert written == []
    captured = capsys.readouterr()
    assert "hello world" in captured.out


def test_write_output_creates_missing_parent_directory(tmp_path):
    cfg = make_config(tmp_path, output=str(tmp_path / "nested" / "sub" / "out.md"))
    written = write_output("hello world", cfg)
    assert written[0].exists()
    assert written[0].read_text() == "hello world"


def test_write_output_creates_missing_parent_directory_for_split_parts(tmp_path):
    cfg = make_config(tmp_path, output=str(tmp_path / "nested" / "out.md"), max_chars=5)
    written = write_output("hello world", cfg)
    assert len(written) > 1
    assert all(p.exists() for p in written)
