"""
conventions.py

PROJECT CONVENTIONS DETECTED: test coverage pairing, lint/CI/security
tooling, dependency manifests, naming/docstring style, README/CHANGELOG
presence -- rendered as an explicit, imperative instruction block.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .astutils import find_pytest_test_roots, is_test_module
from .collectors import collect_all_project_files
from .config import Config
from .constants import (
    CHANGELOG_HEADER_PATTERN,
    CI_CONFIG_PATH_PATTERNS,
    CI_STEP_PATTERNS,
    DEPENDENCY_MANIFEST_SIGNATURES,
    EXACT_PIN_PATTERN,
    KNOWN_LINT_TOOLS,
    PRE_COMMIT_CONTENT_SIGNATURE,
    RANGE_PIN_PATTERN,
)


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


def detect_test_pairs(files: list[Path], root: Path) -> dict:
    py_files = [f for f in files if f.suffix == ".py"]
    test_files = [f for f in py_files if is_test_module(f)]
    test_stems = {f.stem for f in test_files}
    source_modules = [f for f in py_files if f not in test_files and f.stem != "__init__"]

    covered, uncovered = [], []
    for f in source_modules:
        if any(f.stem in stem for stem in test_stems):
            covered.append(f.relative_to(root).as_posix())
        else:
            uncovered.append(f.relative_to(root).as_posix())

    return {
        "tests_detected": bool(test_files),
        "test_files": sorted(f.relative_to(root).as_posix() for f in test_files),
        "test_roots": find_pytest_test_roots(files, root),
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
    }


def detect_lint_config(files: list[Path], root: Path) -> dict:
    found_tools: list[str] = []
    pre_commit_found = False
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        if f.suffix == ".toml":
            for section_key, label in KNOWN_LINT_TOOLS.items():
                if f"[{section_key}" in content and label not in found_tools:
                    found_tools.append(label)
        if PRE_COMMIT_CONTENT_SIGNATURE.search(content) and "pre-commit" in f.name.lower():
            pre_commit_found = True
    return {"tools": found_tools, "pre_commit_configured": pre_commit_found}


def detect_ci_requirements(files: list[Path], root: Path) -> dict:
    workflow_files = [
        f
        for f in files
        if any(p.search(f.relative_to(root).as_posix()) for p in CI_CONFIG_PATH_PATTERNS)
    ]
    required_checks: set[str] = set()
    for wf in workflow_files:
        try:
            content = wf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for check_name, pattern in CI_STEP_PATTERNS.items():
            if pattern.search(content):
                required_checks.add(check_name)
    return {
        "workflow_files": [f.relative_to(root).as_posix() for f in workflow_files],
        "required_checks": sorted(required_checks),
    }


def detect_dependency_files(files: list[Path], root: Path) -> dict:
    found = []
    exact_hits = range_hits = 0
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        pattern = DEPENDENCY_MANIFEST_SIGNATURES.get(f.suffix)
        if pattern and pattern.search(content):
            if f.suffix == ".txt" and not _is_text_dependency_manifest(f, content):
                continue
            found.append(f.relative_to(root).as_posix())
            exact_hits += len(EXACT_PIN_PATTERN.findall(content))
            range_hits += len(RANGE_PIN_PATTERN.findall(content))
    if exact_hits == 0 and range_hits == 0:
        pin_style = "unpinned or undetermined"
    elif exact_hits >= range_hits:
        pin_style = "exact pins (==)"
    else:
        pin_style = "range/minimum pins (>=, ~=, etc.)"
    return {"dependency_files": sorted(set(found)), "pin_style": pin_style}


def detect_docstring_and_naming(files: list[Path], root: Path) -> dict:
    py_files = [f for f in files if f.suffix == ".py"]
    total_funcs = documented_funcs = snake_case = other_case = 0
    snake_re = re.compile(r"^[a-z_][a-z0-9_]*$")
    for f in py_files[:50]:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_funcs += 1
                if ast.get_docstring(node):
                    documented_funcs += 1
                if snake_re.match(node.name):
                    snake_case += 1
                else:
                    other_case += 1
    docstring_ratio = (documented_funcs / total_funcs) if total_funcs else None
    return {
        "sampled_functions": total_funcs,
        "docstring_ratio": round(docstring_ratio, 2) if docstring_ratio is not None else None,
        "naming_convention": "snake_case" if snake_case >= other_case else "mixed/camelCase",
    }


def detect_docs_convention(files: list[Path], root: Path) -> dict:
    readme = next((f for f in files if f.name.lower() == "readme.md"), None)
    changelog = next((f for f in files if f.name.lower() == "changelog.md"), None)
    changelog_format = None
    if changelog is not None:
        try:
            content = changelog.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        changelog_format = (
            "Keep a Changelog style ('## [Unreleased]' / '## [x.y.z]' headers)"
            if CHANGELOG_HEADER_PATTERN.search(content)
            else "free-form (no standard version-header pattern detected)"
        )
    return {
        "readme_present": readme is not None,
        "readme_path": readme.relative_to(root).as_posix() if readme else None,
        "changelog_present": changelog is not None,
        "changelog_path": changelog.relative_to(root).as_posix() if changelog else None,
        "changelog_format": changelog_format,
    }


def detect_conventions(cfg: Config) -> dict:
    all_files = collect_all_project_files(cfg)
    return {
        "tests": detect_test_pairs(all_files, cfg.root),
        "lint": detect_lint_config(all_files, cfg.root),
        "ci": detect_ci_requirements(all_files, cfg.root),
        "deps": detect_dependency_files(all_files, cfg.root),
        "style": detect_docstring_and_naming(all_files, cfg.root),
        "docs": detect_docs_convention(all_files, cfg.root),
    }


def render_conventions_section(conv: dict) -> str:
    lines = ["## \u26a0\ufe0f PROJECT CONVENTIONS DETECTED (MANDATORY -- DO NOT SKIP)\n"]
    lines.append(
        "The following rules were automatically detected from this repository. "
        "Any code you generate for this project MUST comply with ALL of them. "
        "Failure to comply means the generated code will fail CI or be rejected "
        "in code review, even if it is functionally correct.\n"
    )

    tests = conv["tests"]
    lines.append("### 1. Test coverage convention")
    if tests["tests_detected"]:
        if tests["test_roots"]:
            lines.append(
                f"- pytest is configured to look for tests in: {', '.join(tests['test_roots'])}."
            )
        lines.append(
            f"- Existing test files detected (by language-level signature, not filename): {', '.join(tests['test_files'])}."
        )
        if tests["covered"]:
            lines.append(
                f"- Modules WITH an apparent matching test: {', '.join(tests['covered'])}."
            )
        if tests["uncovered"]:
            lines.append(
                f"- \u26a0\ufe0f Modules WITHOUT an apparent test currently (do not treat as an excuse to skip tests for new code): {', '.join(tests['uncovered'])}."
            )
        lines.append(
            "- **MUST**: if you generate a new tool/module, you MUST also generate a corresponding test module with equivalent style and coverage to the existing tests, even if the task prompt does not explicitly mention testing. See the REFERENCE TEST FILE below for the exact style/fixture/assertion pattern to follow.\n"
        )
    else:
        lines.append(
            "- No existing test file was detected in this context. If none exists yet, still generate a test module as a professional default unless explicitly told not to.\n"
        )

    lint = conv["lint"]
    lines.append("### 2. Lint / format / type / security gate")
    if lint["tools"]:
        lines.append(f"- This project enforces: {', '.join(lint['tools'])}.")
        lines.append(
            "- **MUST**: generated code MUST be written as if it will be run through Black formatting, Ruff linting, mypy type checking, and Bandit security scanning -- use type hints on all functions, avoid unused imports, and avoid patterns Bandit flags (e.g. `eval`, unsanitized `subprocess` calls, hardcoded secrets)."
        )
        lines.append(
            "- Pre-commit hooks are configured -- assume every commit is checked automatically; do not generate code that would fail a pre-commit run.\n"
            if lint["pre_commit_configured"]
            else ""
        )
    else:
        lines.append("- No lint/type/security tooling detected in this context.\n")

    ci = conv["ci"]
    lines.append("### 3. CI gate requirements")
    if ci["required_checks"]:
        lines.append(
            f"- CI config file(s) {', '.join(ci['workflow_files'])} run: {', '.join(ci['required_checks'])} on every push/PR."
        )
        lines.append(
            "- **MUST**: treat all of the above as non-negotiable gates. Code that would fail any of them is NOT considered complete.\n"
        )
    else:
        lines.append("- No CI configuration detected in this context.\n")

    deps = conv["deps"]
    lines.append("### 4. Dependency management")
    if deps["dependency_files"]:
        lines.append(f"- Dependencies are declared in: {', '.join(deps['dependency_files'])}.")
        lines.append(f"- Observed version-pinning style: **{deps['pin_style']}**.")
        lines.append(
            "- **MUST**: if your generated code imports any third-party package not already visible in this context, you MUST explicitly list it as a required addition to the dependency file(s) above, using the SAME pinning style, and flag any known unmaintained/insecure package as an open risk -- do not silently assume it is installed.\n"
        )
    else:
        lines.append(
            "- No dependency declaration file detected -- explicitly list any third-party packages your code requires and propose where to declare them.\n"
        )

    style = conv["style"]
    lines.append("### 5. Code style conventions")
    if style["sampled_functions"]:
        lines.append(
            f"- Naming convention observed across {style['sampled_functions']} sampled functions: **{style['naming_convention']}**."
        )
        if style["docstring_ratio"] is not None:
            lines.append(
                f"- Docstring coverage in existing code: ~{int(style['docstring_ratio'] * 100)}% of functions have a docstring."
            )
        lines.append(
            "- **MUST**: match the observed naming convention and docstring practice for any new code, to remain stylistically consistent with the rest of the codebase. See the REFERENCE SOURCE MODULE below for the exact architecture/style pattern to follow.\n"
        )
    else:
        lines.append("- Not enough sampled code to infer a style convention.\n")

    docs = conv["docs"]
    lines.append("### 6. Documentation convention")
    if docs["readme_present"] or docs["changelog_present"]:
        if docs["readme_present"]:
            lines.append(f"- README detected at `{docs['readme_path']}`.")
        if docs["changelog_present"]:
            lines.append(
                f"- CHANGELOG detected at `{docs['changelog_path']}` (format: {docs['changelog_format']})."
            )
        lines.append(
            "- **MUST**: any new tool/feature/dependency you add MUST come with a matching README section (usage) and a new CHANGELOG entry in the same format as the existing entries. State the exact diff/snippet to add, do not just say 'update the docs'.\n"
        )
    else:
        lines.append(
            "- No README.md or CHANGELOG.md detected in this context -- still propose a short usage note for any new tool.\n"
        )

    lines.append(
        "### If context is insufficient\n"
        "If any of the above conventions are ambiguous or you cannot verify compliance with the information given, explicitly say so -- do not silently skip a convention without flagging it as an open question. This does NOT excuse you from still producing a best-effort implementation (see SENIOR-DEVELOPER MANDATE below).\n"
    )
    return "\n".join(lines)
