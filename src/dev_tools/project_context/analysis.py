"""
analysis.py

--analysis mode: deterministic candidate-abstraction extraction, real
import-graph relationships, README/CHANGELOG feature inventory with
documentation-vs-code drift checking, and the tutorial-writing
instruction block. Makes ZERO LLM calls itself.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import replace
from pathlib import Path

from .astutils import (
    build_dependency_graph,
    is_test_module,
    lang_for_highlight,  # add to existing import block
)
from .baseline import (
    collect_mandatory_baseline,
    select_reference_source_file,
    select_reference_test_file,
)
from .collectors import (
    collect_files,
    read_file_content,  # add to existing import block
    warn_if_full_dump_overload,
)
from .config import Config
from .constants import (
    ANALYSIS_OUTPUT_DIRNAME,
    CHANGELOG_ENTRY_PATTERN,
    CLI_FLAG_MENTION_PATTERN,
)
from .conventions import detect_conventions
from .render import render_markdown
from .utils import estimate_tokens, write_generated_marker

ABSTRACTION_ATTRIBUTION = (
    "Deterministic abstraction/relationship extraction below is inspired by "
    "PocketFlow-Tutorial-Codebase-Knowledge "
    "(https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge, "
    "MIT License), which performs the equivalent step with 2 sequential LLM "
    "calls. Here, abstraction descriptions come from real AST-extracted "
    "docstrings and relationships come from the real import graph -- no LLM "
    "call was made to produce this section."
)

FENCED_CODE_BLOCK_PATTERN = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
FLAG_TOKEN_PATTERN = re.compile(r"(--[a-zA-Z][a-zA-Z0-9-]*)")
ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.")

# ============================================================
# NEW: add these two functions to analysis.py
# ============================================================


def _resolve_key_files_for_full_dump(
    files: list[Path], root: Path, abstractions: list[dict]
) -> list[Path]:
    """Files whose complete source is worth embedding verbatim: anything
    backing a selected candidate abstraction (which already force-includes
    the detected CLI entry point -- see select_candidate_abstractions).
    Everything else stays visible via PROJECT TREE + SIGNATURES only."""
    key_rels: set[str] = set()
    for abst in abstractions:
        key_rels.update(abst["files"])
    return sorted(
        (f for f in files if f.relative_to(root).as_posix() in key_rels),
        key=lambda f: f.relative_to(root).as_posix(),
    )


def _render_key_file_contents_section(key_files: list[Path], root: Path, cfg: Config) -> str:
    """Verbatim source for files backing a candidate abstraction --
    the ONLY place in --analysis output where full function bodies for
    these files appear (the signatures-only architecture pass above does
    not repeat them)."""
    if not key_files:
        return (
            "\n## KEY FILE CONTENTS (full source for files backing the abstractions above)\n\n"
            "- No candidate-abstraction-backing files were resolved; see SIGNATURES above for "
            "every file's interface instead.\n"
        )
    parts = [
        "\n## KEY FILE CONTENTS (full source for files backing the abstractions above)\n",
        "_Every other project file is represented only via PROJECT TREE and SIGNATURES above -- "
        "full bodies are shown here once, only for files that back a CANDIDATE ABSTRACTIONS entry, "
        "to avoid dumping the entire project twice._\n",
    ]
    for f in key_files:
        rel = f.relative_to(root).as_posix()
        content = read_file_content(f, cfg)
        if content is None:
            continue
        lang = lang_for_highlight(f)
        parts.append(f"\n### `{rel}`\n\n```{lang}\n{content}\n```\n")
    return "\n".join(parts)


def extract_file_abstractions(path: Path) -> list[dict]:
    if path.suffix not in (".py", ".pyi"):
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError, ValueError):
        return []
    found: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            description = (
                " ".join(doc.strip().split("\n\n")[0].splitlines())
                if doc
                else "(no docstring available -- name only)"
            )
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            found.append({"name": node.name, "description": description, "kind": kind})
    return found


def _first_sentence(text: str, max_len: int = 200) -> str:
    """Cuts a description at the first REAL sentence boundary within
    max_len -- ignores periods inside common abbreviations (e.g., i.e.,
    etc.) and strips a leading numbered-list marker ("3. ") so its
    period isn't mistaken for a sentence boundary either."""
    text = re.sub(r"^\d+\.\s+", "", text.strip())
    truncated = text[:max_len]
    masked = truncated
    for abbr in ABBREVIATIONS:
        masked = masked.replace(abbr, abbr.replace(".", "\u0000"))
    match = re.search(r"^(.*?[.!?])(\s|$)", masked)
    if not match:
        return truncated
    return match.group(1).replace("\u0000", ".")


def extract_readme_flag_mentions(files: list[Path], root: Path) -> dict[str, str]:

    readme = next((f for f in files if f.name.lower() == "readme.md"), None)
    if readme is None:
        return {}
    try:
        content = readme.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    prose_only = FENCED_CODE_BLOCK_PATTERN.sub("", content)

    descriptions: dict[str, str] = {}
    from_table_row: set[str] = set()

    def _consider(flag: str, candidate_desc: str, *, is_table_row: bool = False) -> None:
        candidate_desc = _first_sentence(candidate_desc)
        existing = descriptions.get(flag)
        if existing is None:
            descriptions[flag] = candidate_desc
            if is_table_row:
                from_table_row.add(flag)
            return
        if flag in from_table_row and not is_table_row:
            descriptions[flag] = candidate_desc
            from_table_row.discard(flag)
            return
        if flag not in from_table_row and not is_table_row and len(candidate_desc) > len(existing):
            descriptions[flag] = candidate_desc

    # PASS 1: bullet-style flag definitions in prose (table rows
    # deprioritized, not competing purely on length).
    for line in prose_only.splitlines():
        flags_on_line = CLI_FLAG_MENTION_PATTERN.findall(line)
        if not flags_on_line:
            continue
        stripped = line.strip()
        is_table_row = stripped.startswith("|")
        cleaned = re.sub(r"^[-*]\s+", "", stripped).strip("`").strip()
        for flag in flags_on_line:
            per_flag = re.sub(rf"^`?{re.escape(flag)}`?\s*:?\s*", "", cleaned).strip()
            if per_flag and per_flag != flag:
                _consider(flag, per_flag, is_table_row=is_table_row)
            elif len(cleaned) > len(flag):
                _consider(flag, cleaned, is_table_row=is_table_row)

    # PASS 2: the sentence immediately preceding a ```bash example command
    # is this README's real description -- but ONLY when the command
    # inside the block is actually invoking project-context itself, not
    # a third-party tool (git, pip, python) whose OWN flags (e.g. git's
    # --depth) must never be attributed to this project.
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j < 0:
                continue
            preceding_prose = _first_sentence(lines[j].strip().rstrip(":"))
            block_end = i + 1
            while block_end < len(lines) and not lines[block_end].strip().startswith("```"):
                block_end += 1
            block_lines = lines[i + 1 : block_end]
            for block_line in block_lines:
                stripped_block_line = block_line.strip()
                if not stripped_block_line:
                    continue
                # Only attribute flags to project-context if THIS line
                # actually invokes project-context -- skip git/pip/python/
                # other-tool lines entirely, even inside the same fence.
                if not stripped_block_line.startswith("project-context"):
                    continue
                for flag in FLAG_TOKEN_PATTERN.findall(stripped_block_line):
                    _consider(flag, preceding_prose, is_table_row=False)

    return descriptions


def extract_changelog_highlights(
    files: list[Path], root: Path, max_entries: int = 3
) -> list[tuple[str, str]]:
    changelog = next((f for f in files if f.name.lower() == "changelog.md"), None)
    if changelog is None:
        return []
    try:
        content = changelog.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    entries = CHANGELOG_ENTRY_PATTERN.findall(content)
    highlights = []
    for version, body in entries[:max_entries]:
        trimmed = body.strip()
        if len(trimmed) > 1500:
            trimmed = trimmed[:1500] + "\n[... truncated for context size ...]"
        highlights.append((version, trimmed))
    return highlights


def extract_registered_cli_flags(files: list[Path]) -> set[str]:
    """Real argparse flag registrations only: walks the AST looking for
    Call nodes whose func is an attribute named exactly `add_argument`,
    and collects string-literal positional arguments that look like a
    CLI flag (start with `-`). Skips test modules entirely -- fixtures
    that construct a throwaway ArgumentParser to exercise unrelated
    code must never be attributed to this project's real CLI surface.
    """
    flags: set[str] = set()
    for f in files:
        if f.suffix != ".py" or is_test_module(f):
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith("-"):
                        flags.add(arg.value)
    return flags


def detect_documentation_drift(readme_flags: dict[str, str], code_flags: set[str]) -> dict:
    documented = set(readme_flags.keys())
    return {
        "documented_and_implemented": sorted(documented & code_flags),
        "documented_but_not_found_in_code": sorted(documented - code_flags),
        "implemented_but_undocumented": sorted(code_flags - documented),
    }


def render_documentation_inventory_section(
    readme_flags: dict[str, str], changelog_highlights: list[tuple[str, str]], drift: dict
) -> str:
    if not readme_flags and not changelog_highlights:
        return (
            "\n## DOCUMENTED FEATURE INVENTORY\n\n"
            "- No README.md or CHANGELOG.md was found in the target project. Feature-level coverage "
            "relies entirely on CANDIDATE ABSTRACTIONS above, which describe implementation, not "
            "user-facing intent.\n"
        )
    parts = ["\n## DOCUMENTED FEATURE INVENTORY (from README.md/CHANGELOG.md)\n"]
    parts.append(
        "_This section is ADDITIVE to CANDIDATE ABSTRACTIONS above, not a replacement. Code-derived "
        "abstractions describe HOW the project is implemented; this section describes WHAT it does and "
        "WHY, in the project author's own words. The tutorial MUST cover both, not just whichever list "
        "is more convenient._\n"
    )
    if readme_flags:
        parts.append("### CLI flags documented in README.md\n")
        parts.extend(f"- `{flag}` -- {desc}" for flag, desc in sorted(readme_flags.items()))
        parts.append("")
    if changelog_highlights:
        parts.append("### Recent CHANGELOG.md entries (author's own feature summaries)\n")
        parts.extend(f"#### {version}\n{body}\n" for version, body in changelog_highlights)
    if drift["documented_but_not_found_in_code"] or drift["implemented_but_undocumented"]:
        parts.append("### \u26a0\ufe0f Documentation-vs-code drift detected\n")
        parts.append(
            "_README content is a coverage HINT, not unconditional ground truth -- docs can fall behind "
            "the code they describe. Verify against the actual FILE CONTENTS before writing a chapter "
            "about any flag listed below._\n"
        )
        if drift["documented_but_not_found_in_code"]:
            parts.append(
                f"- Documented in README but NOT found registered in code: {', '.join(drift['documented_but_not_found_in_code'])}. Verify these still exist before writing a chapter -- they may be removed or renamed.\n"
            )
        if drift["implemented_but_undocumented"]:
            parts.append(
                f"- Implemented in code but NOT mentioned in README: {', '.join(drift['implemented_but_undocumented'])}. These still need tutorial coverage even though the README is silent on them.\n"
            )
    return "\n".join(parts)


def _intra_file_call_counts(path: Path) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError, ValueError):
        return {}
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            counts[node.id] = counts.get(node.id, 0) + 1
    return counts


def _detect_entry_point_name(files: list[Path]) -> str | None:
    for f in files:
        if f.suffix != ".py":
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(
            r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:\s*\n\s*(?:raise\s+SystemExit\(|sys\.exit\()?(\w+)\(",
            content,
        )
        if match:
            return match.group(1)
    return None


def select_candidate_abstractions(
    files: list[Path], root: Path, max_abstractions: int
) -> list[dict]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi") and not is_test_module(f)]
    _depends_on, used_by = build_dependency_graph(py_files, root)

    total_cross_file_usage = sum(len(v) for v in used_by.values())
    intra_file_counts_by_file: dict[str, dict[str, int]] = {}
    if total_cross_file_usage == 0:
        for f in py_files:
            intra_file_counts_by_file[f.relative_to(root).as_posix()] = _intra_file_call_counts(f)

    candidates: dict[str, dict] = {}
    for f in py_files:
        rel = f.relative_to(root).as_posix()
        usage_count = len(used_by.get(rel, []))
        intra_counts = intra_file_counts_by_file.get(rel, {}) if total_cross_file_usage == 0 else {}
        for item in extract_file_abstractions(f):
            key = item["name"]
            effective_usage = (
                max(0, intra_counts.get(key, 1) - 1) if total_cross_file_usage == 0 else usage_count
            )
            if key not in candidates:
                candidates[key] = {
                    "name": item["name"],
                    "description": item["description"],
                    "kind": item["kind"],
                    "files": [rel],
                    "has_docstring": item["description"] != "(no docstring available -- name only)",
                    "usage_count": effective_usage,
                }
            else:
                candidates[key]["files"].append(rel)
                candidates[key]["usage_count"] += effective_usage

    ranked = sorted(
        candidates.values(), key=lambda c: (c["has_docstring"], c["usage_count"]), reverse=True
    )
    top = ranked[:max_abstractions]

    entry_point_name = _detect_entry_point_name(py_files)
    if (
        entry_point_name
        and entry_point_name in candidates
        and not any(c["name"] == entry_point_name for c in top)
    ):
        top = (top[:-1] if top else []) + [candidates[entry_point_name]]

    return top


def render_tutorial_bundle(
    files: list[Path],
    root: Path,
    project_name: str,
    abstractions: list[dict],
    language: str,
) -> str:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    depends_on, _used_by = build_dependency_graph(py_files, root)

    readme_flags = extract_readme_flag_mentions(files, root)
    changelog_highlights = extract_changelog_highlights(files, root)
    code_flags = extract_registered_cli_flags(py_files)
    drift = detect_documentation_drift(readme_flags, code_flags)

    parts: list[str] = [f"# TUTORIAL CONTEXT: `{project_name}`\n"]
    target_license = next(
        (f for f in files if f.name.upper() in ("LICENSE", "LICENSE.md", "LICENSE.txt")), None
    )
    if target_license:
        parts.append(
            f"_\u26a0\ufe0f This target project has its own license file: `{target_license.name}`. If you publish "
            f"a tutorial containing code extracted from this project, that publication is governed by ITS "
            f"license terms, not this tool's or PocketFlow's. Review `{target_license.name}` before publishing._\n"
        )
    parts.append(f"Analyzed root: `{root.resolve()}`\n")
    parts.append(f"Files scanned: {len(files)} | Candidate abstractions: {len(abstractions)}\n")
    parts.append(f"_{ABSTRACTION_ATTRIBUTION}_\n")

    parts.append("\n## CANDIDATE ABSTRACTIONS\n")
    for i, abst in enumerate(abstractions):
        file_list = ", ".join(f"`{f}`" for f in sorted(set(abst["files"])))
        parts.append(
            f"{i}. **{abst['name']}** ({abst['kind']}) -- {abst['description']}\n   Files: {file_list} | Referenced by {abst['usage_count']} other module(s)\n"
        )

    parts.append(render_documentation_inventory_section(readme_flags, changelog_highlights, drift))

    parts.append(
        "\n## RELATIONSHIPS\n\n"
        "The real, verified import graph for this project is already "
        "rendered above under `## MODULE GRAPH` (deterministic, extracted "
        "from actual `import` statements -- not LLM-inferred). Use that "
        "section as your source for module dependency facts; it is not "
        "repeated here to avoid duplicating the same ~50 edges twice in "
        "one document.\n"
    )

    #    parts.append("\n## RELATIONSHIPS (real import graph, not LLM-inferred)\n")
    #    any_edges = False
    #    for f in sorted(py_files, key=lambda p: p.relative_to(root).as_posix()):
    #        rel = f.relative_to(root).as_posix()
    #        for dep in depends_on.get(rel, []):
    #            any_edges = True
    #            parts.append(f"- `{rel}` --imports--> `{dep}`")
    #    if not any_edges:
    #        parts.append("- No cross-module imports detected.")

    parts.append(
        "\n## INSTRUCTIONS FOR THE TUTORIAL-WRITING LLM\n"
        "_The following two steps are adapted from PocketFlow-Tutorial-Codebase-Knowledge's OrderChapters "
        "and WriteChapters prompts (MIT License), modified to consume the CANDIDATE ABSTRACTIONS, "
        "DOCUMENTED FEATURE INVENTORY, and RELATIONSHIPS above as verified ground truth instead of "
        "re-deriving them from raw code._\n"
    )
    parts.append(
        "**Coverage requirement:** your chapter list MUST cover BOTH the CANDIDATE ABSTRACTIONS list AND "
        "every entry in the DOCUMENTED FEATURE INVENTORY above. Do not exclude a documented feature (e.g. "
        "a --flag, a rendering mode, the CLI entry point itself) just because it did not rank highly by "
        "code-usage alone -- usage-based ranking measures implementation centrality, not user-facing "
        "importance, and the two are NOT the same thing.\n"
    )
    parts.append(
        f"**Step 1 -- Order the chapters.** Given the abstractions, documented features, and relationships "
        f"above for `{project_name}`, decide the best order to explain them, from first to last. Prefer "
        f"foundational/user-facing concepts first (how to invoke the tool, what each major mode/flag does), "
        f"then lower-level implementation details. Output a numbered list of chapter topics in your chosen "
        f"order, and for each one state explicitly whether it comes from CANDIDATE ABSTRACTIONS, DOCUMENTED "
        f"FEATURE INVENTORY, or both.\n"
    )
    parts.append(
        f"**Step 2 -- Write one beginner-friendly chapter per topic, in {language}.** For each chapter: "
        "start with a clear heading; explain what problem the topic solves with a concrete use case; keep "
        "code blocks under 10 lines each with a beginner-friendly explanation after each one; use a simple "
        "diagram (mermaid) for any non-trivial internal flow; link to other chapters by name where "
        "relevant; end with a brief summary and a transition to the next chapter. If a chapter covers a "
        "DOCUMENTED FEATURE INVENTORY item, quote the README's own description as your starting point and "
        "verify it against FILE CONTENTS before writing.\n"
    )
    return "\n".join(parts)


def default_analysis_output_path(target_root: Path) -> Path:
    return target_root / ANALYSIS_OUTPUT_DIRNAME / "tutorial_context.md"


MAX_TUTORIAL_BUNDLE_LINES = 2000  # soft budget -- see run_analysis_mode()


def validate_tutorial_bundle(text: str, max_lines: int = MAX_TUTORIAL_BUNDLE_LINES) -> list[str]:
    """Structural sanity check on the generated --docs/--analysis bundle
    BEFORE it is written to disk or printed. Catches regressions where a
    future refactor of render_tutorial_bundle()/run_analysis_mode()
    silently drops a required section, or where an unusually large
    repository produces a bundle too big to be a useful LLM context --
    without failing the run, since these are soft warnings, not errors."""
    warnings: list[str] = []
    line_count = len(text.splitlines())
    if line_count > max_lines:
        warnings.append(f"body: {line_count} lines > {max_lines} (soft budget for LLM context)")
    if "## CANDIDATE ABSTRACTIONS" not in text:
        warnings.append("missing CANDIDATE ABSTRACTIONS section")
    if "## DOCUMENTED FEATURE INVENTORY" not in text:
        warnings.append("missing DOCUMENTED FEATURE INVENTORY section")
    return warnings


def run_analysis_mode(cfg: Config, write_files: bool = True) -> str:
    if not cfg.analysis_target:
        print("Error: --docs/--analysis requires a target path.", file=sys.stderr)
        sys.exit(1)
    target_root = Path(cfg.analysis_target).expanduser().resolve()
    if not target_root.exists() or not target_root.is_dir():
        print(
            f"Error: --analysis target does not exist or is not a directory: {target_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    target_cfg = replace(cfg, root=target_root, analysis_target=None)
    files = collect_files(target_cfg)
    if not files:
        print(f"No files matching the filters were found under: {target_root}", file=sys.stderr)
        sys.exit(0)

    warn_if_full_dump_overload(files, target_cfg)

    project_name = target_root.name
    # --analysis's own tutorial-writing instructions explicitly tell the
    # reader to ignore PROJECT CONVENTIONS DETECTED / MANDATORY BASELINE
    # FILES -- so unless the caller explicitly asks to KEEP them (a new
    # --analysis-include-baseline flag, see below), skip generating them
    # at all. This is the single largest size reduction available: those
    # sections embed verbatim contract files (CI config, dependency
    # manifest) that a beginner tutorial never references.
    include_baseline_in_analysis = getattr(cfg, "analysis_include_baseline", False)
    conventions = (
        detect_conventions(target_cfg)
        if include_baseline_in_analysis and not cfg.no_conventions
        else None
    )
    baseline = (
        collect_mandatory_baseline(target_cfg)
        if include_baseline_in_analysis and not cfg.no_baseline
        else None
    )
    reference_test = (
        select_reference_test_file(target_cfg)
        if include_baseline_in_analysis and not cfg.no_baseline
        else None
    )
    reference_source = (
        select_reference_source_file(target_cfg)
        if include_baseline_in_analysis and not cfg.no_baseline
        else None
    )

    # Compute abstractions ONCE, reused for both the full-dump scoping
    # decision below and the CANDIDATE ABSTRACTIONS section inside
    # render_tutorial_bundle -- guarantees the two never disagree.
    abstractions = select_candidate_abstractions(files, target_root, cfg.analysis_max_abstractions)
    key_files = _resolve_key_files_for_full_dump(files, target_root, abstractions)

    # CHANGED: architecture/conventions/baseline pass now runs
    # signatures-only (cheap: tree + signatures + module graph, no
    # bodies) instead of a full dump of every file's complete source.
    # --analysis's architecture pass is ALWAYS meant to be a cheap
    # overview -- force the compact group-detail diagram regardless of
    # whether --tree-only was also passed, instead of silently falling
    # back to expensive per-file diagram detail.
    architecture_cfg = replace(
        target_cfg, signatures_only=True, diagram_mode="mermaid", diagram_detail="group"
    )
    parts = [
        render_markdown(
            files, architecture_cfg, conventions, baseline, reference_test, reference_source
        )
    ]
    # NEW: full verbatim source, once, only for abstraction-backing files.
    parts.append(_render_key_file_contents_section(key_files, target_root, target_cfg))
    parts.append(
        render_tutorial_bundle(
            files, target_root, project_name, abstractions, cfg.analysis_language
        )
    )
    text = "\n\n".join(parts)
    for warning in validate_tutorial_bundle(text):
        print(f"Warning: {warning}", file=sys.stderr)

    if write_files:
        if cfg.output is None:
            print(text)
        else:
            out_path = (
                Path(cfg.output)
                if cfg.output != "project_context.md"
                else default_analysis_output_path(target_root)
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            write_generated_marker(out_path.parent)
            tok_count, method = estimate_tokens(text)
            print(
                f"Written: {out_path} ({len(text)} characters, ~{tok_count} tokens, {method})",
                file=sys.stderr,
            )
        if cfg.clipboard:
            from .render import copy_to_clipboard

            if copy_to_clipboard(text):
                print("Result copied to clipboard.", file=sys.stderr)

    return text
