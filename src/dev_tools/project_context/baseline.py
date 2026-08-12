"""
baseline.py

MANDATORY BASELINE FILES: role-based classification of contract files,
reference test/source file selection with bounded KISS summaries, and
the ARCHITECTURE PLAN GATE (SENIOR-DEVELOPER MANDATE + Step 1/Step 2).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .astutils import _looks_like_cli_entrypoint, is_source_module, is_test_module
from .collectors import collect_all_project_files, read_file_content
from .config import Config
from .constants import (
    CI_CONFIG_PATH_PATTERNS,
    DEPENDENCY_MANIFEST_SIGNATURES,
    PRE_COMMIT_CONTENT_SIGNATURE,
)

# Size caps for the exemplar artifacts embedded in MANDATORY BASELINE FILES.
REFERENCE_TEST_MAX_CHARS = 4000
REFERENCE_SOURCE_MAX_CHARS = 6000


def classify_file_role(path: Path, root: Path, content: str | None) -> str | None:
    rel = path.relative_to(root).as_posix()
    if any(pattern.search(rel) for pattern in CI_CONFIG_PATH_PATTERNS):
        return "ci_config"
    if content is not None:
        if PRE_COMMIT_CONTENT_SIGNATURE.search(content) and "pre-commit" in rel.lower():
            return "pre_commit_config"
        pattern = DEPENDENCY_MANIFEST_SIGNATURES.get(path.suffix)
        if pattern and pattern.search(content):
            if path.suffix == ".txt" and not _is_text_dependency_manifest(path, content):
                return None
            return "dependency_manifest"
    return None


def _is_text_dependency_manifest(path: Path, content: str) -> bool:
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return False
    if path.name.startswith("requirements"):
        return True
    return any(re.search(r"[=<>!~]", line) for line in lines)


def collect_mandatory_baseline(cfg: Config) -> dict[str, tuple[str, str]]:
    all_files = collect_all_project_files(cfg)
    bundle: dict[str, tuple[str, str]] = {}
    for f in all_files:
        content = read_file_content(f, cfg)
        if content is None:
            continue
        role = classify_file_role(f, cfg.root, content)
        if role:
            rel = f.relative_to(cfg.root).as_posix()
            bundle[rel] = (role, content)
    return bundle


def _module_docstring_first_paragraph(source: str) -> str:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    first_para = doc.strip().split("\n\n")[0]
    return " ".join(line.strip() for line in first_para.splitlines())


def _module_top_level_imports(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    lines = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            lines.append("import " + ", ".join(a.name for a in node.names))
        elif isinstance(node, ast.ImportFrom):
            mod = "." * (node.level or 0) + (node.module or "")
            lines.append(f"from {mod} import " + ", ".join(a.name for a in node.names))
    return lines


def _extract_representative_function(source: str) -> tuple[str, str] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    candidates: list[tuple[int, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    fallback = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = (node.end_lineno or node.lineno) - node.lineno
            if fallback is None:
                fallback = node
            if node.name != "main" and body_lines >= 2:
                candidates.append((body_lines, node))
    chosen = None
    if candidates:
        candidates.sort(key=lambda item: item[0])
        chosen = candidates[len(candidates) // 2][1]
    elif fallback is not None:
        chosen = fallback
    if chosen is None:
        return None
    segment = ast.get_source_segment(source, chosen)
    if not segment:
        return None
    return chosen.name, segment


def render_kiss_reference_summary(
    label: str, rel: str, content: str, include_imports: bool = True
) -> str:
    doc = _module_docstring_first_paragraph(content)
    imports = _module_top_level_imports(content) if include_imports else []
    parts = [f"### {label} -- KISS summary, not the full file: `{rel}`\n"]
    if doc:
        parts.append(f"**Module purpose:** {doc}\n")
    if imports:
        parts.append("**Top-level imports:**\n```python\n" + "\n".join(imports) + "\n```\n")
    rep = _extract_representative_function(content)
    if rep:
        name, segment = rep
        parts.append(
            f"**One complete, representative function (`{name}`) -- copy this exact style/error-handling pattern verbatim, do not invent a different one:**\n```python\n{segment}\n```\n"
        )
    parts.append(
        "_Bounded summary, not the full file. Request it explicitly or use the default full-dump mode for the complete, byte-exact source._\n"
    )
    return "\n".join(parts)


def select_reference_test_file(cfg: Config) -> tuple[str, str] | None:
    all_files = collect_all_project_files(cfg)
    candidates = [f for f in all_files if is_test_module(f)]
    if not candidates:
        return None
    scored = []
    for f in candidates:
        content = read_file_content(f, cfg)
        if content:
            scored.append((len(content), f.relative_to(cfg.root).as_posix(), content))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    _, rel, content = scored[len(scored) // 2]
    return rel, content


def select_reference_source_file(cfg: Config) -> tuple[str, str] | None:
    all_files = collect_all_project_files(cfg)
    candidates = [f for f in all_files if is_source_module(f)]
    if not candidates:
        return None
    scored, entrypoints = [], []
    for f in candidates:
        content = read_file_content(f, cfg)
        if not content:
            continue
        rel = f.relative_to(cfg.root).as_posix()
        item = (len(content), rel, content)
        scored.append(item)
        if _looks_like_cli_entrypoint(content):
            entrypoints.append(item)
    pool = entrypoints if entrypoints else scored
    if not pool:
        return None
    pool.sort(key=lambda item: item[0])
    _, rel, content = pool[len(pool) // 2]
    return rel, content


def render_baseline_section(
    bundle: dict[str, tuple[str, str]],
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None = None,
    minimal_reference: bool = False,
) -> str:
    lines = ["## \U0001f4ce MANDATORY BASELINE FILES (verbatim -- read before anything else)\n"]
    lines.append(
        "These are this project's binding contracts and style exemplars, detected by role/content "
        "signature rather than by an assumed filename. Contract files (dependency manifest, CI config, "
        "pre-commit config) below are shown VERBATIM -- treat their exact content as ground truth. The "
        "reference source/test file are shown as a bounded KISS summary, not the full file (see note "
        "below each). Reference all of them by their short relative path shown here, not by any "
        "upload/storage URL that may appear elsewhere in this conversation.\n"
    )
    if not bundle and reference_test is None and reference_source is None:
        lines.append(
            "- No baseline contract files or code exemplars were detected in this context.\n"
        )
        return "\n".join(lines)

    role_labels = {
        "dependency_manifest": "Dependency manifest",
        "ci_config": "CI configuration",
        "pre_commit_config": "Pre-commit configuration",
    }
    for rel, (role, content) in bundle.items():
        lines.append(f"### {role_labels.get(role, role)}: `{rel}`\n```\n{content}\n```\n")

    include_imports = not minimal_reference
    if reference_source:
        rel, content = reference_source
        lines.append(
            render_kiss_reference_summary(
                "Reference source module (style exemplar)", rel, content, include_imports
            )
        )
    if reference_test:
        rel, content = reference_test
        lines.append(
            render_kiss_reference_summary(
                "Reference test file (fixture/assert style exemplar)", rel, content, include_imports
            )
        )
    return "\n".join(lines)


def render_preflight_plan_gate(
    bundle: dict[str, tuple[str, str]],
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None,
    integration_scope: str,
) -> str:
    roles_present = sorted({role for role, _ in bundle.values()})
    roles_text = ", ".join(roles_present) if roles_present else "none detected"
    scope_text = {
        "standalone": "STANDALONE -- add the requested functionality as a new, independently runnable module. Do NOT modify existing entry points, registries, or wiring unless the task explicitly asks for it.",
        "integrated": "INTEGRATED -- the new functionality MUST be wired into this project's existing entry points/CLI registry/registration pattern shown in the baseline files. Show the exact diff required to register it, not just the new file's content.",
    }.get(integration_scope, integration_scope)

    lines = [
        "\n## \U0001f6a8 SENIOR-DEVELOPER MANDATE (read before Step 1)\n",
        "You are acting as a senior Python developer, not a junior who does only the literal minimum listed in the prompt. You MUST see the big picture: project structure, tests, CI/CD, naming conventions, dependency and version constraints, known security risks, and documentation -- exactly like a human senior engineer would before opening a pull request.",
        "**Never respond with zero code.** If a genuinely ambiguous detail exists (e.g. an exact business formula or an unconfirmed target path), state the assumption explicitly, pick the most reasonable default consistent with the MANDATORY BASELINE FILES, and proceed to implement it anyway. Reserve 'insufficient context, do not guess' strictly for details that would silently corrupt behavior -- never as a reason to withhold an entire implementation.\n",
        f"**Integration scope for this task: {integration_scope}.** {scope_text}\n",
        "\n## \U0001f6a6 STEP 1 -- ARCHITECTURE PLAN (required before any code)\n",
        f"This project's detected contract file roles: {roles_text}.\n",
        "Before writing the requested module, produce a short 'Architecture Plan' section that explicitly answers, referencing the MANDATORY BASELINE FILES above:\n",
        "1. Target module path -- matching this project's existing source layout and the REFERENCE SOURCE MODULE's location pattern"
        + (
            ""
            if reference_source
            else " (state explicitly that no source exemplar was found and name the convention you inferred instead)"
        )
        + ".",
        "2. Dependency manifest change -- quote the exact diff, using the real detected file's syntax and the SAME version-pinning style already used in that file.",
        "3. Test file -- path/name and content, modeled on the REFERENCE TEST FILE above"
        + (
            ""
            if reference_test
            else " (state explicitly that none was found and that a new test module is required as a default)"
        )
        + ".",
        "4. CI/lint/type/security gates -- list exactly which detected checks your code must pass.",
        "5. Any dependency version constraints, known security advisories, or breaking-change risks you must respect.",
        "6. Documentation updates -- the exact README/CHANGELOG snippet you will add or state explicitly that none were detected and propose one.\n",
    ]
    lines.append(
        "\n## \U0001f6a6 STEP 2 -- SELF-VALIDATION CHECKLIST (required after code, before finishing)\n"
        "Re-read your own Step 1 plan. For each item, state PASS or FAIL with the concrete artifact produced "
        "(file name, diff line, or explicit justification for why it was skipped). A response with unresolved "
        "FAIL items or missing artifacts is INCOMPLETE per this project's contract.\n"
        "\n**Definition of Done:** finish with a single line `COMPLETION: N%` where N is your own honest "
        "estimate (0-100) of how much of this task is directly copy-paste-usable without further human rework "
        "-- code, tests, dependency diff, and docs all count against this number."
    )
    return "\n".join(lines)
