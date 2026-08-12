"""
tree.py

Project-tree ASCII rendering.
"""

from __future__ import annotations

from pathlib import Path


def build_tree(files: list[Path], root: Path) -> str:
    tree: dict = {}
    for f in files:
        parts = f.relative_to(root).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1])

    lines: list[str] = []

    def walk(node: dict, prefix: str = ""):
        dirs = sorted(k for k in node.keys() if k != "__files__")
        files_here = sorted(node.get("__files__", []))
        entries = [(d, True) for d in dirs] + [(f, False) for f in files_here]
        for i, (name, is_dir) in enumerate(entries):
            connector = "\u2514\u2500\u2500 " if i == len(entries) - 1 else "\u251c\u2500\u2500 "
            lines.append(f"{prefix}{connector}{name}{'/' if is_dir else ''}")
            if is_dir:
                extension = "    " if i == len(entries) - 1 else "\u2502   "
                walk(node[name], prefix + extension)

    lines.append(root.name + "/")
    walk(tree)
    return "\n".join(lines)
