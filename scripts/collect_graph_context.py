"""
scripts/collect_graph_context.py

Merges all markdown files produced by `project-context --graph` into a
single markdown document, ready to paste/attach into an LLM chat.

Usage:
    python scripts/collect_graph_context.py --input project_graph --output graph_context.md
"""

import argparse
from pathlib import Path


def collect_graph_files(graph_dir: Path) -> str:
    if not graph_dir.exists():
        raise FileNotFoundError(f"Directory not found: {graph_dir}")

    md_files = sorted(graph_dir.rglob("*.md"))
    if not md_files:
        raise ValueError(f"No .md files found in {graph_dir}")

    parts = [f"# GRAPH CONTEXT — {len(md_files)} files from {graph_dir}\n"]

    index_file = graph_dir / "index.md"
    if index_file in md_files:
        ordered_files = [index_file] + [f for f in md_files if f != index_file]
    else:
        ordered_files = md_files

    for f in ordered_files:
        rel = f.relative_to(graph_dir).as_posix()
        content = f.read_text(encoding="utf-8")
        parts.append(f"\n--- FILE: {rel} ---\n")
        parts.append(content)

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Merge --graph mode output directory into a single prompt-ready file."
    )
    parser.add_argument(
        "--input", type=str, default="project_graph", help="Path to the --graph output directory"
    )
    parser.add_argument(
        "--output", type=str, default="graph_context.md", help="Path to write the merged file"
    )
    args = parser.parse_args()

    merged = collect_graph_files(Path(args.input))
    Path(args.output).write_text(merged, encoding="utf-8")
    print(f"Written {args.output} ({len(merged)} chars, from {args.input})")


if __name__ == "__main__":
    main()
