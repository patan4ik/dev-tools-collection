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

# ============================================================
# NEW sanitize at LABEL-CONSTRUCTION time, not as a post-hoc regex pass over the finished markdown file.
# ============================================================


def _sanitize_mermaid_label_text(text: str) -> str:
    """Escapes characters that can break Mermaid flowchart node labels
    even inside double-quoted ["..."] syntax: literal double quotes
    (would prematurely close the label), and parentheses -- Mermaid
    reserves "(" ")" for alternate node shapes and can misinterpret
    them inside quoted label text on some renderer versions, per
    OpenDeepWiki's documented workaround for the same class of bug."""
    text = text.replace('"', "'")  # avoid premature label termination
    text = re.sub(r"[()]", "", text)  # drop parens entirely (OpenDeepWiki's approach)
    return text


def _render_node_shape(rel: str, role: str) -> str:
    """Builds a Mermaid node shape string for a single node, running
    the label text through _sanitize_mermaid_label_text() exactly once
    -- the single place every node shape (group-detail synthetic nodes,
    file-detail subgraph nodes, file-detail ungrouped nodes) is built,
    so no call site can accidentally skip sanitization."""
    safe_rel = _sanitize_mermaid_label_text(rel)
    if role == "actor":
        return f"(({safe_rel}))"
    return f'["{safe_rel}<br/>{role}"]'


def _group_key(rel: str, root: Path) -> str | None:
    """Deterministic grouping key from the file's own path -- no
    inference, no LLM. Returns None for synthetic nodes (actors, the
    build-artifact node) that don't correspond to a real on-disk path
    and must never be grouped into a subgraph."""
    if not (root / rel).exists():
        return None  # synthetic node (actor/artifact label, not a path)
    if rel.startswith(".github/workflows/"):
        return "CI"
    parts = rel.split("/")
    if parts[0] == "tests":
        return "tests"
    if parts[0] == "src" and len(parts) >= 3:
        return "/".join(parts[:3])
    if parts[0] == "src" and len(parts) == 2:
        return "/".join(parts[:2])
    if len(parts) == 1:
        return "root"
    return parts[0]


def _build_subgraphs(node_labels: dict[str, str], root: Path) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for rel in node_labels:
        key = _group_key(rel, root)
        if key is None:
            continue  # synthetic node -- rendered ungrouped, see caller
        groups.setdefault(key, []).append(rel)
    return groups


def _render_group_node_label(group_name: str, rels: list[str]) -> str:
    """Plain-text member list inside a collapsed group node -- still a
    real, verbatim fact (the actual filenames), just aggregated rather
    than drawn as separate connected nodes."""
    filenames = sorted(_sanitize_mermaid_label_text(Path(r).name) for r in rels)
    preview = ", ".join(filenames[:6])
    if len(filenames) > 6:
        preview += f", +{len(filenames) - 6} more"
    safe_group_name = _sanitize_mermaid_label_text(group_name)
    return f"{safe_group_name}<br/><i>{len(rels)} files: {preview}</i>"


def _aggregate_group_edges(
    edges: list[tuple[str, str, str, bool]],
    node_to_group: dict[str, str],
) -> list[tuple[str, str, str, bool]]:
    """Collapses many file-to-file edges into deduplicated group-to-group
    edges. Drops edges that stay inside one group (they'd be self-loops
    on the collapsed node) and any edge touching a node with no group
    (shouldn't happen for real files, but defensive)."""
    seen: set[tuple[str, str, str]] = set()
    aggregated: list[tuple[str, str, str, bool]] = []
    for src, dst, label, dashed in edges:
        src_group = node_to_group.get(src, src)  # synthetic nodes: use their own id as "group"
        dst_group = node_to_group.get(dst, dst)
        if src_group == dst_group:
            continue  # intra-group edge -- would be a self-loop, no info added
        key = (src_group, dst_group, label)
        if key in seen:
            continue
        seen.add(key)
        aggregated.append((src_group, dst_group, label, dashed))
    return aggregated


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
    seen: set[str] = set()
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


def render_module_graph_mermaid(
    files: list[Path],
    root: Path,
    entrypoints: dict[str, str],
    diagram_imports: str = "collapsed",
    diagram_detail: str = "file",
) -> str:
    node_labels, edges = detect_module_graph_edges(files, root, entrypoints)
    if not edges:
        return ""

    remote_prefix = get_git_remote_url(root)
    node_ids = {rel: _mermaid_node_id(rel) for rel in node_labels}
    detail = diagram_detail  # passed in from cfg.resolved_diagram_detail()
    groups = _build_subgraphs(node_labels, root)
    node_to_group = {rel: g for g, rels in groups.items() for rel in rels}

    lines = ["## MODULE GRAPH (Mermaid, deterministic -- no LLM)\n", "```mermaid", "flowchart TD"]

    # Actor/synthetic nodes (no real path) render outside any subgraph.
    # Synthetic nodes (actors, build-artifact) are never grouped --
    # role check kept as a defensive second signal alongside the
    # path-existence check already applied inside _group_key/_build_subgraphs.
    synthetic = {
        rel
        for rel in node_labels
        if node_labels[rel] in ("actor", "artifact") or rel not in node_to_group
    }

    if detail == "group":
        # One node per group -- member filenames listed as plain text.
        group_node_id = {g: "g_" + re.sub(r"[^A-Za-z0-9]", "_", g) for g in groups}
        for group_name, rels in sorted(groups.items()):
            if not rels:
                continue
            group_label = _render_group_node_label(group_name, rels)
            lines.append(f'  {group_node_id[group_name]}["{group_label}"]')
        for rel in sorted(synthetic):
            role = node_labels[rel]
            lines.append(f"  {node_ids[rel]}{_render_node_shape(rel, role)}")

        node_to_group_for_agg = {rel: node_to_group.get(rel, rel) for rel in node_labels}
        agg_edges = _aggregate_group_edges(edges, node_to_group_for_agg)
        for src_group, dst_group, edge_label, dashed in agg_edges:
            src_id = group_node_id.get(src_group, node_ids.get(src_group))
            dst_id = group_node_id.get(dst_group, node_ids.get(dst_group))
            if not src_id or not dst_id:
                continue
            arrow = "-.->" if dashed else "-->"
            lines.append(f'  {src_id} {arrow}|"{edge_label}"| {dst_id}')

        for group_name, rels in sorted(groups.items()):
            if not rels:
                continue
            roles = [node_labels[r] for r in rels]
            dominant = max(set(roles), key=roles.count)
            lines.append(
                f"  class {group_node_id[group_name]} "
                f"{MERMAID_ROLE_STYLES.get(dominant, 'toneNeutral')}"
            )
        for rel in sorted(synthetic):
            lines.append(
                f"  class {node_ids[rel]} "
                f"{MERMAID_ROLE_STYLES.get(node_labels[rel], 'toneNeutral')}"
            )
        lines.append("  classDef toneBlue fill:#dbeafe,stroke:#2563eb,color:#172554")
        lines.append("  classDef toneAmber fill:#fef3c7,stroke:#d97706,color:#78350f")
        lines.append("  classDef toneMint fill:#dcfce7,stroke:#16a34a,color:#14532d")
        lines.append("  classDef toneNeutral fill:#f8fafc,stroke:#334155,color:#0f172a")
        # No click links in group mode -- a collapsed node has no single
        # real on-disk path to link to; per-file exploration belongs to
        # --diagram-detail file (or --graph, which is already file-scoped).
        lines.append("```")
        return "\n".join(lines)

    # detail == "file": original per-file rendering path (subgraph
    # blocks + --diagram-imports collapsed/all + click links), unchanged.
    ungrouped = [rel for rel in node_labels if rel in synthetic]
    grouped_only = {g: [r for r in rels if r not in synthetic] for g, rels in groups.items()}

    for group_name, rels in sorted(grouped_only.items()):
        if not rels:
            continue
        safe_group_id = re.sub(r"[^A-Za-z0-9]", "_", group_name)
        lines.append(f'  subgraph {safe_group_id}["{group_name}"]')
        for rel in sorted(rels):
            role = node_labels[rel]
            lines.append(f"    {node_ids[rel]}{_render_node_shape(rel, role)}")
        lines.append("  end")

    for rel in sorted(ungrouped):
        role = node_labels[rel]
        lines.append(f"    {node_ids[rel]}{_render_node_shape(rel, role)}")

    edges_rendered = 0
    for src, dst, label, dashed in edges:
        if src not in node_ids or dst not in node_ids:
            continue
        if (
            diagram_imports == "collapsed"
            and label == "imports"
            and node_to_group.get(src) == node_to_group.get(dst)
            and node_to_group.get(src) is not None
        ):
            continue  # same-package import edge suppressed for readability
        arrow = "-.->" if dashed else "-->"
        lines.append(f'  {node_ids[src]} {arrow}|"{label}"| {node_ids[dst]}')
        edges_rendered += 1

    if diagram_imports == "collapsed":
        suppressed = sum(
            1
            for src, dst, label, _ in edges
            if label == "imports"
            and node_to_group.get(src) == node_to_group.get(dst)
            and node_to_group.get(src) is not None
        )
        if suppressed:
            lines.append(
                f'  %% {suppressed} same-package "imports" edge(s) hidden for readability '
                f"-- rerun with --diagram-imports all to see every edge"
            )

    for rel in sorted(node_labels):
        lines.append(
            f"  class {node_ids[rel]} "
            f"{MERMAID_ROLE_STYLES.get(node_labels[rel], 'toneNeutral')}"
        )
    lines.append("  classDef toneBlue fill:#dbeafe,stroke:#2563eb,color:#172554")
    lines.append("  classDef toneAmber fill:#fef3c7,stroke:#d97706,color:#78350f")
    lines.append("  classDef toneMint fill:#dcfce7,stroke:#16a34a,color:#14532d")
    lines.append("  classDef toneNeutral fill:#f8fafc,stroke:#334155,color:#0f172a")

    if remote_prefix:
        lines.append("")
        for rel, nid in node_ids.items():
            if node_labels[rel] in ("actor", "artifact"):
                continue
            if (root / rel).exists():
                lines.append(f'  click {nid} "{remote_prefix}{rel}"')

    lines.append("```")
    return "\n".join(lines)


def render_module_graph(files: list[Path], cfg: Config, entrypoints: dict[str, str]) -> str:
    mode = cfg.resolved_diagram_mode()
    if mode == "text":
        return render_module_graph_text(files, cfg.root, entrypoints)
    if mode == "mermaid":
        return render_module_graph_mermaid(
            files,
            cfg.root,
            entrypoints,
            diagram_imports=cfg.diagram_imports,
            diagram_detail=cfg.resolved_diagram_detail(),
        )
    return ""
