"""
render.py

Output assembly: markdown/xml single-file rendering, the --graph
multi-file OKF-style renderer, and file/clipboard writing utilities.
"""

from __future__ import annotations

from pathlib import Path

from .astutils import (
    build_dependency_graph,
    detect_entry_points,
    extract_signatures,
    lang_for_highlight,
    module_id,
)
from .baseline import render_baseline_section, render_preflight_plan_gate
from .collectors import collect_all_project_files, read_file_content
from .config import Config
from .conventions import render_conventions_section
from .diagram import render_module_graph
from .tree import build_tree
from .utils import write_generated_marker


def render_markdown(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None,
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None = None,
) -> str:
    parts = [
        "# PROJECT CONTEXT\n",
        f"Project root: `{cfg.root.resolve()}`\n",
        f"Files included: {len(files)}\n",
    ]

    if conventions is not None:
        parts.append(render_conventions_section(conventions))

    if baseline is not None:
        parts.append(
            render_baseline_section(
                baseline, reference_test, reference_source, minimal_reference=cfg.tree_only
            )
        )
        if not cfg.no_plan_gate:
            parts.append(
                render_preflight_plan_gate(
                    baseline, reference_test, reference_source, cfg.integration_scope
                )
            )

    parts.append("\n## PROJECT TREE\n")
    parts.append("```\n" + build_tree(files, cfg.root) + "\n```\n")

    entry_points = detect_entry_points(collect_all_project_files(cfg), cfg.root)
    diagram_text = render_module_graph(files, cfg, entry_points)
    if diagram_text:
        parts.append(diagram_text)

    if cfg.tree_only:
        return "\n".join(parts)

    if cfg.signatures_only:
        parts.append("\n## SIGNATURES\n")
        for f in files:
            rel = f.relative_to(cfg.root).as_posix()
            sig = extract_signatures(f)
            if sig:
                parts.append(f"\n### `{rel}`\n```python\n{sig}\n```\n")
        return "\n".join(parts)

    parts.append("\n## FILE CONTENTS\n")
    for f in files:
        rel = f.relative_to(cfg.root).as_posix()
        content = read_file_content(f, cfg)
        parts.append(f"\n### `{rel}`\n")
        if content is None:
            parts.append("_[content not shown: binary/excluded file]_\n")
        else:
            parts.append(f"```{lang_for_highlight(f)}\n{content}\n```\n")
    return "\n".join(parts)


def render_xml(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None,
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None = None,
) -> str:
    parts = [
        "<project_context>",
        f"  <root>{cfg.root.resolve()}</root>",
        f"  <files_included>{len(files)}</files_included>",
    ]

    if conventions is not None:
        parts.append(
            f"  <conventions_detected><![CDATA[{render_conventions_section(conventions)}]]></conventions_detected>"
        )

    if baseline is not None:
        baseline_text = render_baseline_section(
            baseline, reference_test, reference_source, minimal_reference=cfg.tree_only
        )
        parts.append(f"  <mandatory_baseline><![CDATA[{baseline_text}]]></mandatory_baseline>")
        if not cfg.no_plan_gate:
            plan_text = render_preflight_plan_gate(
                baseline, reference_test, reference_source, cfg.integration_scope
            )
            parts.append(
                f"  <architecture_plan_gate><![CDATA[{plan_text}]]></architecture_plan_gate>"
            )

    parts.append(f"  <tree><![CDATA[{build_tree(files, cfg.root)}]]></tree>")

    entry_points = detect_entry_points(collect_all_project_files(cfg), cfg.root)
    diagram_text = render_module_graph(files, cfg, entry_points)
    if diagram_text:
        parts.append(f"  <module_graph><![CDATA[{diagram_text}]]></module_graph>")

    if cfg.tree_only:
        parts.append("</project_context>")
        return "\n".join(parts)

    if cfg.signatures_only:
        parts.append("  <signatures>")
        for f in files:
            rel = f.relative_to(cfg.root).as_posix()
            sig = extract_signatures(f)
            if sig:
                parts.append(f'    <file path="{rel}"><![CDATA[{sig}]]></file>')
        parts.append("  </signatures>")
        parts.append("</project_context>")
        return "\n".join(parts)

    parts.append("  <files>")
    for f in files:
        rel = f.relative_to(cfg.root).as_posix()
        content = read_file_content(f, cfg)
        if content is None:
            parts.append(f'    <file path="{rel}" skipped="true"></file>')
        else:
            parts.append(f'    <file path="{rel}"><![CDATA[{content}]]></file>')
    parts.append("  </files>")
    parts.append("</project_context>")
    return "\n".join(parts)


def render(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None = None,
    reference_test: tuple[str, str] | None = None,
    reference_source: tuple[str, str] | None = None,
) -> str:
    if cfg.output_format == "xml":
        return render_xml(files, cfg, conventions, baseline, reference_test, reference_source)
    return render_markdown(files, cfg, conventions, baseline, reference_test, reference_source)


def render_graph(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None = None,
    reference_test: tuple[str, str] | None = None,
    reference_source: tuple[str, str] | None = None,
) -> dict[str, str]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    depends_on, used_by = build_dependency_graph(py_files, cfg.root)

    output: dict[str, str] = {}
    index_lines = ["# PROJECT GRAPH INDEX\n", f"Project root: `{cfg.root.resolve()}`\n"]

    if conventions is not None:
        index_lines.append(render_conventions_section(conventions))
    if baseline is not None:
        index_lines.append(
            render_baseline_section(
                baseline, reference_test, reference_source, minimal_reference=False
            )
        )
        if not cfg.no_plan_gate:
            index_lines.append(
                render_preflight_plan_gate(
                    baseline, reference_test, reference_source, cfg.integration_scope
                )
            )

    index_lines.append(f"Modules: {len(py_files)}\n")
    index_lines.append("\n## PROJECT TREE\n")
    index_lines.append("```\n" + build_tree(files, cfg.root) + "\n```\n")
    index_lines.append("\n## MODULES\n")

    for f in sorted(py_files, key=lambda p: p.relative_to(cfg.root).as_posix()):
        rel = f.relative_to(cfg.root).as_posix()
        fname = module_id(rel)
        deps, users = depends_on.get(rel, []), used_by.get(rel, [])
        sig = extract_signatures(f)

        parts = [
            "---",
            "type: module",
            f"path: {rel}",
            f"depends_on: [{', '.join(deps)}]",
            f"used_by: [{', '.join(users)}]",
            "---\n",
            f"# `{rel}`\n",
        ]
        parts.append(
            f"## Signatures\n```python\n{sig}\n```\n"
            if sig
            else "_[no top-level functions/classes]_\n"
        )
        if deps:
            parts.append("## Dependencies\n")
            parts.extend(f"- [{dep}](./{module_id(dep)})" for dep in deps)
            parts.append("")
        if users:
            parts.append("## Used by\n")
            parts.extend(f"- [{user}](./{module_id(user)})" for user in users)
            parts.append("")

        output[fname] = "\n".join(parts)
        index_lines.append(f"- [{rel}](./{fname})")

    output["index.md"] = "\n".join(index_lines)
    return output


def write_graph_output(graph_files: dict[str, str], cfg: Config) -> Path:
    out_dir = Path(cfg.output) if cfg.output else Path("project_graph")
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in graph_files.items():
        (out_dir / fname).write_text(content, encoding="utf-8")
    write_generated_marker(out_dir)
    return out_dir


def split_by_max_chars(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


def write_output(text: str, cfg: Config) -> list[Path]:
    written_paths: list[Path] = []
    chunks = split_by_max_chars(text, cfg.max_chars) if cfg.max_chars else [text]

    if cfg.output is None:
        for chunk in chunks:
            print(chunk)
        return written_paths

    base_path = Path(cfg.output)
    base_path.parent.mkdir(parents=True, exist_ok=True)  # <-- ADD THIS LINE
    if len(chunks) == 1:
        base_path.write_text(text, encoding="utf-8")
        written_paths.append(base_path)
    else:
        stem, suffix = base_path.stem, base_path.suffix or ".md"
        for i, chunk in enumerate(chunks, start=1):
            part_path = base_path.with_name(f"{stem}_part{i}{suffix}")
            part_path.parent.mkdir(parents=True, exist_ok=True)  # <-- ADD THIS LINE too
            part_path.write_text(chunk, encoding="utf-8")
            written_paths.append(part_path)
    return written_paths


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except ImportError:
        import sys

        print(
            "The pyperclip module is not installed. Install it with: pip install pyperclip",
            file=sys.stderr,
        )
        return False
