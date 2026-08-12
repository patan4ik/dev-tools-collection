"""
diagram.py

--diagram module graph: deterministic, GitDiagram-parity relationship
detection rendered as text arrows or a Mermaid flowchart.
"""

from __future__ import annotations

import re
from pathlib import Path

from .astutils import build_dependency_graph, is_test_module
from .collectors import get_git_remote_url
from .config import Config
from .constants import (
    CI_BUILD_TOOL_PATTERN,
    CI_CONFIG_PATH_PATTERNS,
    CI_STEP_PATTERNS,
    NON_CODE_CONFIG_NAMES,
    NON_CODE_DOC_EXT,
)


def _module_role_label(rel: str, root: Path, path: Path) -> str:
    if path.suffix == ".py":
        if is_test_module(path):
            return "test"
        if path.name == "__init__.py":
            return "package-init"
        return "source"
    if any(p.search(rel) for p in CI_CONFIG_PATH_PATTERNS):
        return "ci"
    if path.name == "pyproject.toml":
        return "build-config"
    if path.name.lower() == "readme.md":
        return "docs"
    if path.suffix in NON_CODE_DOC_EXT:
        return "doc"
    if path.name in NON_CODE_CONFIG_NAMES or path.suffix in (
        ".cfg",
        ".ini",
        ".yaml",
        ".yml",
        ".json",
    ):
        return "other-config"
    return "other"


def _find_root_package_init(init_files: list[str]) -> str | None:
    if not init_files:
        return None
    return sorted(init_files, key=lambda r: r.count("/"))[0]


def _parent_package_init(rel: str, init_files: set[str]) -> str | None:
    parts = rel.split("/")[:-1]
    for depth in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:depth]) + "/__init__.py"
        if candidate in init_files:
            return candidate
    return None


def _test_referenced_modules(
    test_rel: str, test_content: str, source_files: list[str]
) -> list[str]:
    referenced = []
    for src_rel in source_files:
        stem = Path(src_rel).stem
        fname = Path(src_rel).name
        if re.search(rf"\b{re.escape(stem)}\b", test_content) or fname in test_content:
            referenced.append(src_rel)
    return referenced


def detect_module_graph_edges(
    files: list[Path], root: Path, entry_points: dict[str, str]
) -> tuple[dict[str, str], list[tuple[str, str, str, bool]]]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    all_rels = sorted(f.relative_to(root).as_posix() for f in files)
    py_rels = sorted(f.relative_to(root).as_posix() for f in py_files)
    init_files = {r for r in py_rels if r.endswith("__init__.py")}
    source_rels = [
        r for r in py_rels if not r.endswith("__init__.py") and not is_test_module(root / r)
    ]
    test_rels = [r for r in py_rels if is_test_module(root / r)]

    node_labels: dict[str, str] = {r: _module_role_label(r, root, root / r) for r in all_rels}
    edges: list[tuple[str, str, str, bool]] = []

    depends_on, _used_by = build_dependency_graph(py_files, root)
    for rel in py_rels:
        for dep in depends_on.get(rel, []):
            edges.append((rel, dep, "imports", False))

    for cmd_name, target_rel in sorted(entry_points.items()):
        if "pyproject.toml" in node_labels and target_rel in node_labels:
            edges.append(("pyproject.toml", target_rel, f"registers {cmd_name}", True))

    for init_rel in sorted(init_files):
        parent = _parent_package_init(init_rel, init_files)
        if parent:
            edges.append((init_rel, parent, "belongs to", False))

    root_init = _find_root_package_init(sorted(init_files))
    readmes = [r for r in all_rels if Path(r).name.lower() == "readme.md"]
    for readme_rel in readmes:
        readme_dir = "/".join(readme_rel.split("/")[:-1])
        same_dir_init = f"{readme_dir}/__init__.py" if readme_dir else "__init__.py"
        target = same_dir_init if same_dir_init in init_files else root_init
        if target:
            edges.append((readme_rel, target, "documents", True))

    manifest_rel = next((r for r in all_rels if Path(r).name == "pyproject.toml"), None)
    if manifest_rel and root_init:
        edges.append((manifest_rel, root_init, "packages", False))

    test_contents: dict[str, str] = {}
    for t in test_rels:
        try:
            test_contents[t] = (root / t).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            test_contents[t] = ""
    for t in test_rels:
        for src_rel in _test_referenced_modules(t, test_contents.get(t, ""), source_rels):
            edges.append((t, src_rel, "validates", False))

    ci_workflow_rels = [r for r in all_rels if any(p.search(r) for p in CI_CONFIG_PATH_PATTERNS)]
    distribution_node = "Distribution / build artifact"
    build_ci_found = False
    for ci_rel in ci_workflow_rels:
        try:
            content = (root / ci_rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        runs_tests = bool(CI_STEP_PATTERNS["pytest"].search(content))
        is_build_ci = bool(CI_BUILD_TOOL_PATTERN.search(content))
        if runs_tests and test_rels:
            for t in test_rels:
                edges.append((ci_rel, t, "runs", False))
        if is_build_ci:
            build_ci_found = True
            if manifest_rel:
                edges.append((ci_rel, manifest_rel, "builds from", False))
            edges.append((manifest_rel or ci_rel, distribution_node, "produces", False))
            edges.append((ci_rel, distribution_node, "publishes build", False))
    if build_ci_found:
        node_labels[distribution_node] = "artifact"

    if entry_points:
        node_labels["User / automation invoker"] = "actor"
        for _cmd_name, target_rel in sorted(entry_points.items()):
            edges.append(("User / automation invoker", target_rel, "invokes", False))

    if root_init:
        node_labels["External Python callers"] = "actor"
        edges.append(("External Python callers", root_init, "imports", False))

    return node_labels, edges


def render_module_graph_text(files: list[Path], root: Path, entry_points: dict[str, str]) -> str:
    node_labels, edges = detect_module_graph_edges(files, root, entry_points)
    if not node_labels:
        return ""
    lines = ["\n## MODULE GRAPH (auto-generated, deterministic -- no LLM)\n"]
    seen = set()
    for src, dst, label, _dashed in edges:
        role = node_labels.get(src, "")
        lines.append(f"- `{src}`{f' [{role}]' if role else ''} --{label}--> `{dst}`")
        seen.update((src, dst))
    for rel in sorted(r for r in node_labels if r not in seen):
        lines.append(f"- `{rel}` [{node_labels.get(rel, '')}] (no detected relationships)")
    lines.append("")
    return "\n".join(lines)


MERMAID_ROLE_STYLES = {
    "source": "toneBlue",
    "package-init": "toneBlue",
    "test": "toneAmber",
    "ci": "toneAmber",
    "build-config": "toneAmber",
    "artifact": "toneAmber",
    "docs": "toneMint",
    "doc": "toneMint",
    "other-config": "toneNeutral",
    "other": "toneNeutral",
    "actor": "toneNeutral",
}


def _mermaid_node_id(rel: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", rel)


def render_module_graph_mermaid(files: list[Path], root: Path, entry_points: dict[str, str]) -> str:
    node_labels, edges = detect_module_graph_edges(files, root, entry_points)
    if not edges:
        return ""
    remote_prefix = get_git_remote_url(root)
    node_ids = {rel: _mermaid_node_id(rel) for rel in node_labels}

    lines = ["\n## MODULE GRAPH (Mermaid, deterministic -- no LLM)\n", "```mermaid", "flowchart TD"]
    for rel in sorted(node_labels):
        role = node_labels[rel]
        shape = f'(("{rel}"))' if role == "actor" else f'["{rel}<br/>[{role}]"]'
        lines.append(f"  {node_ids[rel]}{shape}")
    for src, dst, label, dashed in edges:
        if src not in node_ids or dst not in node_ids:
            continue
        arrow = "-.->" if dashed else "-->"
        lines.append(f'  {node_ids[src]} {arrow}|"{label}"| {node_ids[dst]}')
    for rel in sorted(node_labels):
        lines.append(
            f"  class {node_ids[rel]} {MERMAID_ROLE_STYLES.get(node_labels[rel], 'toneNeutral')}"
        )
    lines.append(
        "  classDef toneBlue fill:#dbeafe,stroke:#2563eb,color:#172554\n"
        "  classDef toneAmber fill:#fef3c7,stroke:#d97706,color:#78350f\n"
        "  classDef toneMint fill:#dcfce7,stroke:#16a34a,color:#14532d\n"
        "  classDef toneNeutral fill:#f8fafc,stroke:#334155,color:#0f172a"
    )
    if remote_prefix:
        lines.append("")
        for rel, nid in node_ids.items():
            if node_labels[rel] in ("actor", "artifact"):
                continue
            if (root / rel).exists():
                lines.append(f'  click {nid} "{remote_prefix}/{rel}"')
    lines.append("```\n")
    return "\n".join(lines)


def render_module_graph(files: list[Path], cfg: Config, entry_points: dict[str, str]) -> str:
    mode = cfg.resolved_diagram_mode()
    if mode == "text":
        return render_module_graph_text(files, cfg.root, entry_points)
    if mode == "mermaid":
        return render_module_graph_mermaid(files, cfg.root, entry_points)
    return ""
