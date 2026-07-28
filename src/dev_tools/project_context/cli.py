#!/usr/bin/env python3
"""
project_context.py

CLI tool that bundles a Python project's code into a single text file
suitable for feeding into an LLM's context window (ChatGPT, Claude,
Gemini, etc.).

Version: 1.8.1

Features:
- Recursive project traversal, respecting .gitignore
- Filtering by extension/filename (a "python" profile is the default)
- Excludes tooling directories (venv, __pycache__, .git, etc.)
- --tree-only mode: project tree only, no file contents
- --changed-only mode: only files changed relative to Git
  (working tree / staged)
- --signatures-only mode: only function/class signatures (AST),
  no bodies, in a single file
- --grep PATTERN mode: only files whose content matches a regex
- --graph mode: OKF-flavored output -- one markdown file per module with
  YAML frontmatter and explicit cross-file dependency (import graph) links
- --report mode: benchmark your own project -- compares token/char usage
  across modes
- PROJECT CONVENTIONS DETECTED: automatically detects and explicitly,
  imperatively states the project rules an LLM MUST follow -- tests,
  lint/format/type/security tooling, CI gate checks, dependency
  management, docstring/naming conventions. Injected at the start of the
  output in EVERY mode, including --tree-only and --graph, so the model
  cannot skip past it.
- MANDATORY BASELINE FILES (new in 1.8): verbatim content of the
  project's real contract files (dependency manifest, CI config,
  pre-commit config), detected by role/content signature -- never by a
  hardcoded filename -- plus one real reference test file, selected using
  only stable Python/pytest/unittest language rules (AST: `assert`,
  `unittest.TestCase`, `pytest` fixtures/imports; config: `[tool.pytest...]`
  / `testpaths` / `python_files` if declared). This bundle is attached in
  EVERY output mode, including --tree-only, at a bounded size.
- ARCHITECTURE PLAN GATE (new in 1.8): a Plan-and-Solve style two-phase
  instruction block. Phase 1 forces the executor to commit, in writing,
  to a concrete integration plan (target path, dependency diff, test
  file, CI gates, version/security constraints) referencing the
  MANDATORY BASELINE FILES *before* generating any code. Phase 2 forces
  a self-validation checklist against that same plan *after* the code is
  written. This exploits the model's own recency bias -- a plan it just
  committed to is harder to silently drop than an instruction buried at
  the top of a long context.
- Warning when full-dump mode is used on a large number of files
- Output size cap (--max-chars) with automatic splitting into parts
- Output to a file, stdout, or the clipboard (--clipboard)
- Output format: markdown (default) or xml-like blocks

Usage examples:
    python project_context.py --root . --output context.md
    python project_context.py --tree-only
    python project_context.py --changed-only --output diff_context.md
    python project_context.py --signatures-only --output signatures.md
    python project_context.py --grep "PortfolioSummary" --output portfolio_context.md
    python project_context.py --graph --output project_graph
    python project_context.py --max-chars 50000 --output context.md
    python project_context.py --report --grep "PortfolioSummary"
    python project_context.py --no-conventions --output context.md
    # disable the conventions section
    python project_context.py --no-baseline --output context.md
    # disable the mandatory baseline files bundle
    python project_context.py --no-plan-gate --output context.md
    # disable the architecture plan gate
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

VERSION = "1.8.1"

# --------------------------------------------------------------------------- #
# Filter profiles
# --------------------------------------------------------------------------- #

DEFAULT_INCLUDE_EXT = {
    ".py",
    ".pyi",
    ".md",
    ".rst",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".txt",
    ".env.example",
}

DEFAULT_INCLUDE_NAMES = {
    "Dockerfile",
    "Makefile",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    ".pre-commit-config.yaml",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "node_modules",
    "htmlcov",
    ".coverage",
    "site-packages",
    ".eggs",
    "*.egg-info",
}

DEFAULT_EXCLUDE_CONTENT_EXT = {
    ".csv",
    ".json.lock",
    ".lock",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".parquet",
    ".ipynb",
}

DEFAULT_EXCLUDE_FILES = {
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "yarn.lock",
}

MAX_FILE_SIZE_BYTES = 300_000
FULL_DUMP_FILE_WARNING_THRESHOLD = 40

# Size cap for the reference test file embedded in the baseline bundle.
# Kept separate from MAX_FILE_SIZE_BYTES because this content is injected
# into every mode, including --tree-only, so it must stay cheap.
REFERENCE_TEST_MAX_CHARS = 4000

# Known pyproject.toml dev-tool sections treated as CI "gate" checks
KNOWN_LINT_TOOLS = {
    "tool.black": "Black (code formatting)",
    "tool.ruff": "Ruff (linting)",
    "tool.mypy": "mypy (static typing)",
    "tool.bandit": "Bandit (security static analysis)",
    "tool.pytest": "pytest (test configuration)",
}

# Patterns used to recognize mandatory CI steps
CI_STEP_PATTERNS = {
    "black": re.compile(r"\bblack\b", re.IGNORECASE),
    "ruff": re.compile(r"\bruff\b", re.IGNORECASE),
    "mypy": re.compile(r"\bmypy\b", re.IGNORECASE),
    "bandit": re.compile(r"\bbandit\b", re.IGNORECASE),
    "pytest": re.compile(r"\bpytest\b", re.IGNORECASE),
    "coverage": re.compile(r"\bcov(erage)?\b", re.IGNORECASE),
}

# CI config locations across common CI providers. Detected by path
# pattern, since these are the provider's own fixed conventions, not
# something a given project invents.
CI_CONFIG_PATH_PATTERNS = (
    re.compile(r"(^|/)\.github/workflows/.+\.ya?ml$"),
    re.compile(r"(^|/)\.gitlab-ci\.ya?ml$"),
    re.compile(r"(^|/)azure-pipelines\.ya?ml$"),
    re.compile(r"(^|/)Jenkinsfile$"),
    re.compile(r"(^|/)\.circleci/config\.ya?ml$"),
)

# Structural signatures used to recognize a dependency manifest by its
# actual content shape, not by a fixed filename (a project may use
# pyproject.toml, Pipfile, environment.yml, poetry.toml, etc.).
DEPENDENCY_MANIFEST_SIGNATURES = {
    ".toml": re.compile(r"^\s*\[(project|tool\.poetry|build-system)\]", re.MULTILINE),
    ".txt": re.compile(r"^[A-Za-z0-9_.\-]+\s*[=<>!~]{0,2}=?\s*[\d.]*\s*$", re.MULTILINE),
    ".cfg": re.compile(r"^\s*\[options(\.\w+)?\]", re.MULTILINE),
    ".yml": re.compile(r"^\s*dependencies:\s*$", re.MULTILINE),
    ".yaml": re.compile(r"^\s*dependencies:\s*$", re.MULTILINE),
}

# A pre-commit config is recognized by pre-commit's own documented schema
# (a top-level `repos:` key), not by an assumed filename.
PRE_COMMIT_CONTENT_SIGNATURE = re.compile(r"^\s*repos:\s*$", re.MULTILINE)

# pytest test-discovery configuration keys, per pytest's own documented
# schema (pyproject.toml [tool.pytest.ini_options], pytest.ini, tox.ini,
# setup.cfg [tool:pytest]). Used to find the project's declared test
# root(s) without guessing folder names.
PYTEST_TESTPATHS_PATTERN = re.compile(r"testpaths\s*=\s*(.+)")
PYTEST_PYTHON_FILES_PATTERN = re.compile(r"python_files\s*=\s*(.+)")


@dataclass
class Config:
    root: Path
    output: str | None
    tree_only: bool
    changed_only: bool
    signatures_only: bool
    graph: bool
    grep_pattern: str | None
    max_chars: int | None
    output_format: str
    clipboard: bool
    report: bool
    no_conventions: bool = False
    no_baseline: bool = False
    no_plan_gate: bool = False
    include_ext: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_EXT))
    include_names: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_NAMES))
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_content_ext: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_CONTENT_EXT))
    exclude_files: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_FILES))
    use_gitignore: bool = True


# --------------------------------------------------------------------------- #
# .gitignore
# --------------------------------------------------------------------------- #


def load_gitignore_patterns(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns = []
    for line in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def is_gitignored(rel_path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(rel_path, pat_clean) or fnmatch.fnmatch(
            os.path.basename(rel_path), pat_clean
        ):
            return True
        if fnmatch.fnmatch(rel_path, f"{pat_clean}/*") or fnmatch.fnmatch(
            rel_path, f"*/{pat_clean}/*"
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Git changed-only
# --------------------------------------------------------------------------- #


def get_changed_files(root: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "Warning: git was not found or this is not a git repository. "
            "--changed-only is ignored.",
            file=sys.stderr,
        )
        return set()

    changed = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            path = parts[1].split(" -> ")[-1]
            changed.add(path)
    return changed


# --------------------------------------------------------------------------- #
# Relevance filter: --grep
# --------------------------------------------------------------------------- #


def matches_grep(path: Path, pattern: re.Pattern) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return pattern.search(content) is not None


# --------------------------------------------------------------------------- #
# Project traversal
# --------------------------------------------------------------------------- #


def should_skip_dir(dirname: str, cfg: Config) -> bool:
    for pattern in cfg.exclude_dirs:
        if fnmatch.fnmatch(dirname, pattern):
            return True
    return False


def should_include_file(path: Path, cfg: Config) -> bool:
    name = path.name
    if name in cfg.exclude_files:
        return False
    if name in cfg.include_names:
        return True
    return path.suffix in cfg.include_ext


def collect_files(cfg: Config) -> list[Path]:
    gitignore_patterns = load_gitignore_patterns(cfg.root) if cfg.use_gitignore else []
    changed_files = get_changed_files(cfg.root) if cfg.changed_only else None
    grep_re = re.compile(cfg.grep_pattern, re.IGNORECASE) if cfg.grep_pattern else None

    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d, cfg)]

        for filename in filenames:
            full_path = Path(dirpath) / filename
            rel_path = str(full_path.relative_to(cfg.root)).replace(os.sep, "/")

            if gitignore_patterns and is_gitignored(rel_path, gitignore_patterns):
                continue
            if not should_include_file(full_path, cfg):
                continue
            if changed_files is not None and rel_path not in changed_files:
                continue
            if grep_re is not None and not matches_grep(full_path, grep_re):
                continue

            collected.append(full_path)

    return sorted(collected)


# --------------------------------------------------------------------------- #
# collect_all_project_files: convention/baseline detection needs the full
# project file list (tests, dependency manifest, CI config), independent
# of any --grep/--changed-only/--signatures-only filters of the current run.
# --------------------------------------------------------------------------- #


def collect_all_project_files(cfg: Config) -> list[Path]:
    scan_cfg = replace(
        cfg,
        changed_only=False,
        grep_pattern=None,
        tree_only=False,
        signatures_only=False,
        graph=False,
    )
    return collect_files(scan_cfg)


# --------------------------------------------------------------------------- #
# AST: function/class signature extraction (--signatures-only, --graph)
# --------------------------------------------------------------------------- #


def extract_signatures(path: Path) -> str:
    if path.suffix not in (".py", ".pyi"):
        return ""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, OSError, ValueError):
        return ""

    lines: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"{prefix} {node.name}({args})")
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(b.id for b in node.bases if isinstance(b, ast.Name))
            suffix = f"({bases})" if bases else ""
            lines.append(f"class {node.name}{suffix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# AST: import extraction and dependency graph construction (--graph)
# --------------------------------------------------------------------------- #


def extract_imports(path: Path) -> list[str]:
    if path.suffix not in (".py", ".pyi"):
        return []
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, OSError, ValueError):
        return []

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module.split(".")[0])
    return modules


def build_dependency_graph(
    files: list[Path], root: Path
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]

    stem_index: dict[str, list[str]] = {}
    for f in py_files:
        rel = f.relative_to(root).as_posix()
        stem_index.setdefault(f.stem, []).append(rel)

    depends_on: dict[str, list[str]] = {}
    used_by: dict[str, list[str]] = {}

    for f in py_files:
        rel = f.relative_to(root).as_posix()
        imported_names = extract_imports(f)
        deps: list[str] = []
        for name in imported_names:
            candidates = stem_index.get(name, [])
            for cand in candidates:
                if cand != rel:
                    deps.append(cand)
        deps = sorted(set(deps))
        depends_on[rel] = deps
        for dep in deps:
            used_by.setdefault(dep, [])
            if rel not in used_by[dep]:
                used_by[dep].append(rel)

    for f in py_files:
        rel = f.relative_to(root).as_posix()
        used_by.setdefault(rel, [])
        used_by[rel] = sorted(set(used_by[rel]))

    return depends_on, used_by


def module_id(rel_path: str) -> str:
    return rel_path.replace("/", "_").replace("\\", "_").replace(".", "_") + ".md"


# --------------------------------------------------------------------------- #
# ROLE-BASED FILE CLASSIFICATION (new in 1.8)
#
# Rationale: a project's contract files (dependency manifest, CI config,
# pre-commit config, test suite) can have any name and live in any
# folder -- only their CONTENT SHAPE or their PATH CONVENTION mandated by
# an external tool (GitHub Actions, GitLab CI, pytest, unittest) is
# stable. Classifying files by hardcoded names (e.g. "pyproject.toml")
# breaks on any project that names things differently. Classifying by
# content signature and by the external tool's own fixed path/schema
# convention generalizes correctly across projects and does not depend
# on this repository's specific naming choices.
# --------------------------------------------------------------------------- #


def classify_file_role(path: Path, root: Path, content: str | None) -> str | None:
    """Determines a file's role from its path convention (owned by an
    external tool, e.g. GitHub Actions) or its content shape -- never
    from an assumed project-specific filename."""
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
    """Avoid classifying arbitrary one-word text files as requirements."""
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
    """Collects the project's contract files by ROLE, not by a fixed
    filename. Returns {relative_path: (role, content)}."""
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


# --------------------------------------------------------------------------- #
# TEST FILE DETECTION (new in 1.8) -- based on stable Python/pytest rules,
# never on folder or filename conventions.
#
# A file is treated as a test file if it exhibits the language-level
# signatures that make it executable as a test under the standard
# library's own `unittest` module or under `pytest` (both documented,
# version-pinned behaviors of the Python ecosystem, not project-specific
# conventions):
#   - it imports `unittest` or `pytest`
#   - it defines a class inheriting from `unittest.TestCase`
#   - it defines a function decorated with a `pytest.fixture` (or any
#     `@pytest.mark.*` decorator)
#   - it contains at least one bare `assert` statement at module/function
#     level (the fundamental unit of both unittest's assertion methods
#     conceptually and pytest's assert-rewriting mechanism)
# This works regardless of whether the project names its files
# `test_*.py`, `*_test.py`, or something else entirely, and regardless
# of whether tests live in a folder called `tests/`, `test/`, or are
# co-located with source modules.
# --------------------------------------------------------------------------- #


def _module_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _has_testcase_subclass(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = (
                    base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                )
                if base_name == "TestCase":
                    return True
    return False


def _has_pytest_decorator(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_src = ast.dump(dec)
                if "pytest" in dec_src and ("fixture" in dec_src or "mark" in dec_src):
                    return True
    return False


def _has_bare_assert(tree: ast.AST) -> bool:
    return any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def is_test_module(path: Path) -> bool:
    """Detects a test file using stable unittest/pytest language rules
    (imports, TestCase subclassing, pytest decorators, assert usage)
    instead of filename or folder heuristics."""
    if path.suffix != ".py":
        return False
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, OSError, ValueError):
        return False

    imports = _module_imports(tree)
    if "unittest" in imports or "pytest" in imports:
        return True
    if _has_testcase_subclass(tree):
        return True
    if _has_pytest_decorator(tree):
        return True
    if _has_bare_assert(tree):
        return True
    return False


def find_pytest_test_roots(files: list[Path], root: Path) -> list[str]:
    """Reads pytest's own documented configuration keys (`testpaths`)
    from whichever config file declares them (pyproject.toml, pytest.ini,
    tox.ini, setup.cfg) -- this is pytest's fixed schema, not a
    project-specific convention, so no filename needs to be assumed
    beyond "a file pytest itself would read"."""
    config_names = {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}
    roots: list[str] = []
    for f in files:
        if f.name not in config_names:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = PYTEST_TESTPATHS_PATTERN.search(content)
        if match:
            raw = match.group(1).strip().strip("\"'")
            roots.extend(part.strip() for part in re.split(r"[,\s]+", raw) if part.strip())
    return sorted(set(roots))


def select_reference_test_file(cfg: Config) -> tuple[str, str] | None:
    """Selects one real test file as a style/fixture/assertion-pattern
    exemplar for the executor to copy. Detection uses only stable
    Python-language and pytest/unittest rules (see is_test_module),
    never folder or filename heuristics. Among detected test modules,
    picks the median-length one as the most representative example
    (shortest ones are often trivial smoke tests, longest ones are often
    edge-case heavy and less representative of everyday style)."""
    all_files = collect_all_project_files(cfg)
    candidates = [f for f in all_files if is_test_module(f)]
    if not candidates:
        return None

    scored: list[tuple[int, str, str]] = []
    for f in candidates:
        content = read_file_content(f, cfg)
        if content:
            scored.append((len(content), f.relative_to(cfg.root).as_posix(), content))
    if not scored:
        return None

    scored.sort(key=lambda item: item[0])
    _, rel, content = scored[len(scored) // 2]
    if len(content) > REFERENCE_TEST_MAX_CHARS:
        content = content[:REFERENCE_TEST_MAX_CHARS] + "\n# [... truncated for context size ...]"
    return rel, content


# --------------------------------------------------------------------------- #
# PROJECT CONVENTIONS DETECTED
#
# Rationale: the tool should not just dump repository facts and hope the
# LLM draws the right architectural/process conclusions on its own.
# Practice shows (see the separate LLM-as-judge experiments) that models
# systematically ignore implicit project conventions -- they do not
# create tests by analogy, do not mentally run linters, do not add new
# dependencies to the manifest -- even when every fact needed to do so
# is present in the context. This section turns detected facts into
# explicit, imperative rules and is injected at the start of EVERY
# output mode, including --tree-only and --graph, so the model cannot
# skim past facts scattered across a file tree or a signature list.
# --------------------------------------------------------------------------- #


def detect_test_pairs(files: list[Path], root: Path) -> dict:
    """Finds 'implementation module <-> test file' pairs using the
    language-level test detector (is_test_module), not a fixed filename
    pattern, and reports which source modules currently lack a test."""
    py_files = [f for f in files if f.suffix == ".py"]
    test_files = [f for f in py_files if is_test_module(f)]
    test_stems = {f.stem for f in test_files}

    source_modules = [f for f in py_files if f not in test_files and f.stem != "__init__"]

    covered = []
    uncovered = []
    for f in source_modules:
        # A source module is considered covered if a test module's stem
        # references it (e.g. "test_<name>" or "<name>_test"), which is
        # the common naming link even when exact naming varies.
        if any(f.stem in stem for stem in test_stems):
            covered.append(f.relative_to(root).as_posix())
        else:
            uncovered.append(f.relative_to(root).as_posix())

    test_roots = find_pytest_test_roots(files, root)
    tests_detected = bool(test_files)

    return {
        "tests_detected": tests_detected,
        "test_files": sorted(f.relative_to(root).as_posix() for f in test_files),
        "test_roots": test_roots,
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
    }


def detect_lint_config(files: list[Path], root: Path) -> dict:
    """Scans any detected dependency manifest for lint/format/type/
    security tool configuration sections."""
    found_tools: list[str] = []
    pre_commit_found = False
    for f in files:
        content = f.read_text(encoding="utf-8", errors="ignore") if f.suffix == ".toml" else None
        if content is None:
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

    return {
        "tools": found_tools,
        "pre_commit_configured": pre_commit_found,
    }


def detect_ci_requirements(files: list[Path], root: Path) -> dict:
    """Recognizes CI config files by the CI provider's own fixed path
    convention (CI_CONFIG_PATH_PATTERNS) and extracts mandatory gate
    steps that generated code must pass before merge."""
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
    """Detects where the project declares dependencies by content
    signature (DEPENDENCY_MANIFEST_SIGNATURES), not by assumed filename,
    so any LLM-added package is pointed at the correct real file."""
    found = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        pattern = DEPENDENCY_MANIFEST_SIGNATURES.get(f.suffix)
        if pattern and pattern.search(content):
            found.append(f.relative_to(root).as_posix())
    return {"dependency_files": sorted(set(found))}


def detect_docstring_and_naming(files: list[Path], root: Path) -> dict:
    """Samples existing .py files to determine the share of functions
    with a docstring and the dominant naming convention (snake_case,
    etc.), using only Python's own AST -- a language-level fact."""
    py_files = [f for f in files if f.suffix == ".py"]
    total_funcs = 0
    documented_funcs = 0
    snake_case = 0
    other_case = 0
    snake_re = re.compile(r"^[a-z_][a-z0-9_]*$")

    for f in py_files[:50]:  # sample size cap for performance
        try:
            source = f.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
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
    naming_convention = "snake_case" if snake_case >= other_case else "mixed/camelCase"

    return {
        "sampled_functions": total_funcs,
        "docstring_ratio": (round(docstring_ratio, 2) if docstring_ratio is not None else None),
        "naming_convention": naming_convention,
    }


def detect_conventions(cfg: Config) -> dict:
    """Aggregates all detected project conventions into a single
    structure used to render the mandatory PROJECT CONVENTIONS section."""
    all_files = collect_all_project_files(cfg)
    return {
        "tests": detect_test_pairs(all_files, cfg.root),
        "lint": detect_lint_config(all_files, cfg.root),
        "ci": detect_ci_requirements(all_files, cfg.root),
        "deps": detect_dependency_files(all_files, cfg.root),
        "style": detect_docstring_and_naming(all_files, cfg.root),
    }


def render_conventions_section(conv: dict) -> str:
    """Renders an explicit, imperative section of project rules. Wording
    is deliberately strong ("MUST") to counter the observed tendency of
    LLMs to ignore implicit conventions scattered across a file tree
    when they are not phrased as a direct requirement."""
    lines = []
    lines.append("## \u26a0\ufe0f PROJECT CONVENTIONS DETECTED (MANDATORY -- DO NOT SKIP)\n")
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
            f"- Existing test files detected (by language-level signature, not filename): "
            f"{', '.join(tests['test_files'])}."
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
                f"- \u26a0\ufe0f Modules WITHOUT an apparent test currently (do not treat as an "
                f"excuse to skip tests for new code): {', '.join(tests['uncovered'])}."
            )
        lines.append(
            "- **MUST**: if you generate a new tool/module, you MUST also "
            "generate a corresponding test module with equivalent style and "
            "coverage to the existing tests, even if the task prompt does not "
            "explicitly mention testing. See the REFERENCE TEST FILE below for "
            "the exact style/fixture/assertion pattern to follow.\n"
        )
    else:
        lines.append(
            "- No existing test file was detected in this context. If none "
            "exists yet, still generate a test module as a professional "
            "default unless explicitly told not to.\n"
        )

    lint = conv["lint"]
    lines.append("### 2. Lint / format / type / security gate")
    if lint["tools"]:
        lines.append(f"- This project enforces: {', '.join(lint['tools'])}.")
        lines.append(
            "- **MUST**: generated code MUST be written as if it will be "
            "run through Black formatting, Ruff linting, mypy type checking, "
            "and Bandit security scanning -- use type hints on all functions, "
            "avoid unused imports, and avoid patterns Bandit flags (e.g. "
            "`eval`, unsanitized `subprocess` calls, hardcoded secrets)."
        )
    if lint["pre_commit_configured"]:
        lines.append(
            "- Pre-commit hooks are configured -- assume every commit is "
            "checked automatically; do not generate code that would fail "
            "a pre-commit run.\n"
        )
    else:
        lines.append("")

    ci = conv["ci"]
    lines.append("### 3. CI gate requirements")
    if ci["required_checks"]:
        lines.append(
            f"- CI config file(s) {', '.join(ci['workflow_files'])} run: "
            f"{', '.join(ci['required_checks'])} on every push/PR."
        )
        lines.append(
            "- **MUST**: treat all of the above as non-negotiable gates. "
            "Code that would fail any of them is NOT considered complete.\n"
        )
    else:
        lines.append("- No CI configuration detected in this context.\n")

    deps = conv["deps"]
    lines.append("### 4. Dependency management")
    if deps["dependency_files"]:
        lines.append(f"- Dependencies are declared in: {', '.join(deps['dependency_files'])}.")
        lines.append(
            "- **MUST**: if your generated code imports any third-party "
            "package not already visible in this context, you MUST "
            "explicitly list it as a required addition to the dependency "
            "file(s) above -- do not silently assume it is installed.\n"
        )
    else:
        lines.append(
            "- No dependency declaration file detected -- explicitly list "
            "any third-party packages your code requires.\n"
        )

    style = conv["style"]
    lines.append("### 5. Code style conventions")
    if style["sampled_functions"]:
        lines.append(
            f"- Naming convention observed across {style['sampled_functions']} "
            f"sampled functions: **{style['naming_convention']}**."
        )
        if style["docstring_ratio"] is not None:
            lines.append(
                f"- Docstring coverage in existing code: "
                f"~{int(style['docstring_ratio'] * 100)}% of functions have "
                f"a docstring."
            )
        lines.append(
            "- **MUST**: match the observed naming convention and docstring "
            "practice for any new code, to remain stylistically consistent "
            "with the rest of the codebase.\n"
        )
    else:
        lines.append("- Not enough sampled code to infer a style convention.\n")

    lines.append(
        "### If context is insufficient\n"
        "If any of the above conventions are ambiguous or you cannot verify "
        "compliance with the information given, explicitly say so -- do not "
        "silently skip a convention without flagging it as an open question.\n"
    )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# MANDATORY BASELINE FILES + ARCHITECTURE PLAN GATE (new in 1.8)
# --------------------------------------------------------------------------- #


def render_baseline_section(
    bundle: dict[str, tuple[str, str]], reference_test: tuple[str, str] | None
) -> str:
    """Renders the verbatim contract-file bundle. These files are never
    truncated by mode scoping (--tree-only etc.) because they are
    structural facts, not exploratory content."""
    lines = ["## \U0001f4ce MANDATORY BASELINE FILES (verbatim -- read before anything else)\n"]
    lines.append(
        "These are this project's binding contracts, detected by role/content "
        "signature rather than by an assumed filename. Do not summarize or "
        "paraphrase them -- treat their exact content as ground truth for any "
        "new module you add.\n"
    )
    if not bundle and reference_test is None:
        lines.append(
            "- No baseline contract files or test exemplar were detected in this context.\n"
        )
        return "\n".join(lines)

    role_labels = {
        "dependency_manifest": "Dependency manifest",
        "ci_config": "CI configuration",
        "pre_commit_config": "Pre-commit configuration",
    }
    for rel, (role, content) in bundle.items():
        label = role_labels.get(role, role)
        lines.append(f"### {label}: `{rel}`\n```\n{content}\n```\n")

    if reference_test:
        rel, content = reference_test
        lines.append(
            f"### Reference test file (copy this exact style/fixtures/assert pattern): `{rel}`\n"
            f"```python\n{content}\n```\n"
        )
    return "\n".join(lines)


def render_preflight_plan_gate(
    bundle: dict[str, tuple[str, str]], reference_test: tuple[str, str] | None
) -> str:
    """Plan-and-Solve style gate: forces the executor to commit to an
    explicit integration plan referencing the real baseline files BEFORE
    writing code (Phase 1), then forces it to validate its own output
    against that same plan AFTER writing code (Phase 2). Committing to a
    plan as its own recent output makes it harder for the model to
    silently drift away from it during code generation than a rule
    stated only once, far away, at the top of a long context."""
    roles_present = sorted({role for role, _ in bundle.values()})
    roles_text = ", ".join(roles_present) if roles_present else "none detected"

    lines = [
        "\n## \U0001f6a6 STEP 1 -- ARCHITECTURE PLAN (required before any code)\n",
        f"This project's detected contract file roles: {roles_text}.\n",
        "Before writing the requested module, produce a short 'Architecture Plan' "
        "section that explicitly answers, referencing the MANDATORY BASELINE FILES above:\n",
        "1. Target module path -- matching this project's existing source layout.",
        "2. Dependency manifest change -- quote the exact diff, using the real "
        "detected file's syntax.",
        "3. Test file -- path/name and content, modeled on the REFERENCE TEST FILE "
        "above"
        + (
            ""
            if reference_test
            else (
                " (state explicitly that none was found and that a new test module "
                "is required as a default)"
            )
        )
        + ".",
        "4. CI/lint/type/security gates -- list exactly which detected checks your code must pass.",
        "5. Any dependency version constraints, known security advisories, or "
        "breaking-change risks you must respect.\n",
    ]
    lines.append(
        "\n## \U0001f6a6 STEP 2 -- SELF-VALIDATION CHECKLIST "
        "(required after code, before finishing)\n"
        "Re-read your own Step 1 plan. For each of the 5 items, state PASS or FAIL "
        "with the concrete artifact produced (file name, diff line, or explicit "
        "justification for why it was skipped). A response with unresolved FAIL "
        "items or missing artifacts is INCOMPLETE per this project's contract."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Output formatting
# --------------------------------------------------------------------------- #


def build_tree(files: list[Path], root: Path) -> str:
    tree: dict = {}
    for f in files:
        parts = f.relative_to(root).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1])

    lines = []

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


def read_file_content(path: Path, cfg: Config) -> str | None:
    if path.suffix in cfg.exclude_content_ext:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_SIZE_BYTES:
        return f"[file skipped: size {size} bytes exceeds the {MAX_FILE_SIZE_BYTES} limit]"

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"

    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def lang_for_highlight(path: Path) -> str:
    mapping = {
        ".py": "python",
        ".pyi": "python",
        ".md": "markdown",
        ".rst": "rst",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".ini": "ini",
        ".cfg": "ini",
        ".sql": "sql",
        ".sh": "bash",
        ".txt": "text",
    }
    return mapping.get(path.suffix, "")


def render_markdown(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None,
    reference_test: tuple[str, str] | None,
) -> str:
    parts = []
    parts.append("# PROJECT CONTEXT\n")
    parts.append(f"Project root: `{cfg.root.resolve()}`\n")
    parts.append(f"Files included: {len(files)}\n")

    if conventions is not None:
        parts.append(render_conventions_section(conventions))

    if baseline is not None:
        parts.append(render_baseline_section(baseline, reference_test))
        if not cfg.no_plan_gate:
            parts.append(render_preflight_plan_gate(baseline, reference_test))

    parts.append("\n## PROJECT TREE\n")
    parts.append("```\n" + build_tree(files, cfg.root) + "\n```\n")

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
            lang = lang_for_highlight(f)
            parts.append(f"```{lang}\n{content}\n```\n")

    return "\n".join(parts)


def render_xml(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None,
    reference_test: tuple[str, str] | None,
) -> str:
    parts = []
    parts.append("<project_context>")
    parts.append(f"  <root>{cfg.root.resolve()}</root>")
    parts.append(f"  <file_count>{len(files)}</file_count>")

    if conventions is not None:
        parts.append("  <conventions><![CDATA[")
        parts.append(render_conventions_section(conventions))
        parts.append("  ]]></conventions>")

    if baseline is not None:
        parts.append("  <mandatory_baseline><![CDATA[")
        parts.append(render_baseline_section(baseline, reference_test))
        if not cfg.no_plan_gate:
            parts.append(render_preflight_plan_gate(baseline, reference_test))
        parts.append("  ]]></mandatory_baseline>")

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
            parts.append(f'    <file path="{rel}" skipped="true"/>')
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
) -> str:
    if cfg.output_format == "xml":
        return render_xml(files, cfg, conventions, baseline, reference_test)
    return render_markdown(files, cfg, conventions, baseline, reference_test)


# --------------------------------------------------------------------------- #
# --graph: OKF-flavored multi-file output
# --------------------------------------------------------------------------- #


def render_graph(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None = None,
    reference_test: tuple[str, str] | None = None,
) -> dict[str, str]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    depends_on, used_by = build_dependency_graph(py_files, cfg.root)

    output: dict[str, str] = {}
    index_lines = [
        "# PROJECT GRAPH INDEX\n",
        f"Project root: `{cfg.root.resolve()}`\n",
    ]

    if conventions is not None:
        index_lines.append(render_conventions_section(conventions))

    if baseline is not None:
        index_lines.append(render_baseline_section(baseline, reference_test))
        if not cfg.no_plan_gate:
            index_lines.append(render_preflight_plan_gate(baseline, reference_test))

    index_lines.append(f"Modules: {len(py_files)}\n")
    index_lines.append("\n## PROJECT TREE\n")
    index_lines.append("```\n" + build_tree(files, cfg.root) + "\n```\n")
    index_lines.append("\n## MODULES\n")

    for f in sorted(py_files, key=lambda p: p.relative_to(cfg.root).as_posix()):
        rel = f.relative_to(cfg.root).as_posix()
        fname = module_id(rel)
        deps = depends_on.get(rel, [])
        users = used_by.get(rel, [])
        sig = extract_signatures(f)

        fm_deps = ", ".join(deps) if deps else ""
        fm_users = ", ".join(users) if users else ""

        parts = []
        parts.append("---")
        parts.append("type: module")
        parts.append(f"path: {rel}")
        parts.append(f"depends_on: [{fm_deps}]")
        parts.append(f"used_by: [{fm_users}]")
        parts.append("---\n")
        parts.append(f"# `{rel}`\n")

        if sig:
            parts.append("## Signatures\n")
            parts.append(f"```python\n{sig}\n```\n")
        else:
            parts.append("_[no top-level functions/classes]_\n")

        if deps:
            parts.append("## Dependencies\n")
            for dep in deps:
                dep_fname = module_id(dep)
                parts.append(f"- [{dep}](./{dep_fname})")
            parts.append("")

        if users:
            parts.append("## Used by\n")
            for user in users:
                user_fname = module_id(user)
                parts.append(f"- [{user}](./{user_fname})")
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
    return out_dir


# --------------------------------------------------------------------------- #
# Splitting output by a character limit
# --------------------------------------------------------------------------- #


def split_by_max_chars(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


def write_output(text: str, cfg: Config) -> list[Path]:
    written_paths: list[Path] = []

    if cfg.max_chars:
        chunks = split_by_max_chars(text, cfg.max_chars)
    else:
        chunks = [text]

    if cfg.output is None:
        for chunk in chunks:
            print(chunk)
        return written_paths

    base = Path(cfg.output)
    if len(chunks) == 1:
        base.write_text(text, encoding="utf-8")
        written_paths.append(base)
    else:
        stem, suffix = base.stem, base.suffix or ".md"
        for i, chunk in enumerate(chunks, start=1):
            part_path = base.with_name(f"{stem}_part{i}{suffix}")
            part_path.write_text(chunk, encoding="utf-8")
            written_paths.append(part_path)

    return written_paths


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except ImportError:
        print(
            "The pyperclip module is not installed. Install it with: pip install pyperclip",
            file=sys.stderr,
        )
        return False


# --------------------------------------------------------------------------- #
# Benchmarks
# --------------------------------------------------------------------------- #


def run_benchmark(cfg: Config) -> list[dict]:
    try:
        import tiktoken
    except ImportError:
        print(
            "--report requires tiktoken. Install with: pip install tiktoken",
            file=sys.stderr,
        )
        sys.exit(1)

    enc = tiktoken.get_encoding("cl100k_base")
    rows = []

    def measure(label: str, text_or_texts) -> dict:
        if isinstance(text_or_texts, dict):
            chars = sum(len(t) for t in text_or_texts.values())
            tokens = sum(len(enc.encode(t)) for t in text_or_texts.values())
        else:
            chars = len(text_or_texts)
            tokens = len(enc.encode(text_or_texts))
        return {"mode": label, "characters": chars, "tokens": tokens}

    base_cfg = replace(cfg, tree_only=False, signatures_only=False, graph=False, grep_pattern=None)
    conventions = None if cfg.no_conventions else detect_conventions(base_cfg)
    baseline = None if cfg.no_baseline else collect_mandatory_baseline(base_cfg)
    reference_test = None if cfg.no_baseline else select_reference_test_file(base_cfg)

    full_files = collect_files(base_cfg)
    full_text = render(full_files, base_cfg, conventions, baseline, reference_test)
    rows.append(measure("full", full_text))

    sig_cfg = replace(base_cfg, signatures_only=True)
    sig_files = collect_files(sig_cfg)
    sig_text = render(sig_files, sig_cfg, conventions, baseline, reference_test)
    rows.append(measure("signatures-only", sig_text))

    if cfg.grep_pattern:
        grep_cfg = replace(base_cfg, grep_pattern=cfg.grep_pattern)
        grep_files = collect_files(grep_cfg)
        grep_text = render(grep_files, grep_cfg, conventions, baseline, reference_test)
        rows.append(measure(f"grep:{cfg.grep_pattern}", grep_text))

    graph_cfg = replace(base_cfg, graph=True)
    graph_files = collect_files(graph_cfg)
    graph_dict = render_graph(graph_files, graph_cfg, conventions, baseline, reference_test)
    rows.append(measure("graph", graph_dict))

    baseline_tokens = rows[0]["tokens"]
    for row in rows:
        row["reduction_pct"] = round(100 * (1 - row["tokens"] / baseline_tokens), 1)
        row["multiplier"] = round(baseline_tokens / row["tokens"], 1)

    return rows


def print_benchmark_table(rows: list[dict]) -> None:
    print(f"{'Mode':<20} {'Chars':>10} {'Tokens':>10} {'Reduction':>10} {'Smaller':>10}")
    print("-" * 62)
    for row in rows:
        print(
            f"{row['mode']:<20} {row['characters']:>10} {row['tokens']:>10} "
            f"{row['reduction_pct']:>9}% {row['multiplier']:>9}x"
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Bundles a Python project's code into a single file for LLM context."
    )
    parser.add_argument("--version", action="version", version=f"project_context.py {VERSION}")
    parser.add_argument("--root", type=str, default=".", help="Project root directory")
    parser.add_argument(
        "--output",
        type=str,
        default="project_context.md",
        help=("Path to the output file (or directory for --graph). Empty/'-' for stdout"),
    )
    parser.add_argument(
        "--tree-only",
        action="store_true",
        help="Output only the project tree, without file contents",
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Include only changed (git status) files",
    )
    parser.add_argument(
        "--signatures-only",
        action="store_true",
        help="Output only function/class signatures (AST) in a single file",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help=(
            "OKF-flavored output: one markdown file per module with YAML "
            "frontmatter and cross-file import-dependency links, plus "
            "index.md. --output is treated as a directory."
        ),
    )
    parser.add_argument(
        "--grep",
        type=str,
        default=None,
        dest="grep_pattern",
        help="Include only files whose content matches a regex pattern",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="Max characters per output file, to split into parts",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["md", "xml"],
        default="md",
        help="Output format: markdown or xml-like (ignored with --graph)",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help=("Copy the result to the clipboard (requires pyperclip, ignored with --graph)"),
    )
    parser.add_argument("--no-gitignore", action="store_true", help="Ignore .gitignore rules")
    parser.add_argument(
        "--include-ext",
        type=str,
        default=None,
        help="Extra extensions, comma-separated, e.g.: .env,.j2",
    )
    parser.add_argument(
        "--exclude-dir",
        type=str,
        default=None,
        help="Extra directories to exclude, comma-separated",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "Run full, signatures-only, graph (and grep, if --grep is set) "
            "modes against the same root and print a token/character "
            "comparison table using tiktoken (cl100k_base)."
        ),
    )
    parser.add_argument(
        "--no-conventions",
        action="store_true",
        help=(
            "Disable automatic detection and injection of the "
            "PROJECT CONVENTIONS DETECTED section (tests, lint/CI gate, "
            "dependencies, style). Enabled by default."
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help=(
            "Disable the MANDATORY BASELINE FILES bundle and the "
            "ARCHITECTURE PLAN GATE (verbatim dependency manifest/CI "
            "config/pre-commit config plus a reference test file, and the "
            "pre-flight plan/self-validation instructions). Enabled by "
            "default."
        ),
    )
    parser.add_argument(
        "--no-plan-gate",
        action="store_true",
        help=(
            "Keep the MANDATORY BASELINE FILES bundle but disable only the "
            "ARCHITECTURE PLAN GATE (Step 1/Step 2 planning instructions)."
        ),
    )
    args = parser.parse_args()

    output = None if (args.output in ("-", "", None)) else args.output

    cfg = Config(
        root=Path(args.root).resolve(),
        output=output,
        tree_only=args.tree_only,
        changed_only=args.changed_only,
        signatures_only=args.signatures_only,
        graph=args.graph,
        grep_pattern=args.grep_pattern,
        max_chars=args.max_chars,
        output_format=args.format,
        clipboard=args.clipboard,
        report=args.report,
        no_conventions=args.no_conventions,
        no_baseline=args.no_baseline,
        no_plan_gate=args.no_plan_gate,
        use_gitignore=not args.no_gitignore,
    )
    if args.include_ext:
        cfg.include_ext |= {e.strip() for e in args.include_ext.split(",") if e.strip()}
    if args.exclude_dir:
        cfg.exclude_dirs |= {d.strip() for d in args.exclude_dir.split(",") if d.strip()}

    if cfg.graph and cfg.output == "project_context.md":
        cfg.output = "project_graph"

    return cfg


def warn_if_full_dump_overload(files: list[Path], cfg: Config) -> None:
    is_scoped = (
        cfg.tree_only
        or cfg.changed_only
        or cfg.signatures_only
        or cfg.graph
        or cfg.grep_pattern is not None
    )
    if not is_scoped and len(files) > FULL_DUMP_FILE_WARNING_THRESHOLD:
        print(
            f"[warning] Full-dump mode with {len(files)} files may overload "
            "the LLM's context and reduce answer quality. Consider "
            "--changed-only, --signatures-only, --graph, or --grep for a "
            "more targeted context.",
            file=sys.stderr,
        )


def main() -> None:
    cfg = parse_args()

    if not cfg.root.exists():
        print(f"Error: directory {cfg.root} was not found", file=sys.stderr)
        sys.exit(1)

    if cfg.report:
        if importlib.util.find_spec("tiktoken") is None:
            print(
                "--report requires tiktoken. Install with: pip install tiktoken",
                file=sys.stderr,
            )
            sys.exit(1)
        rows = run_benchmark(cfg)
        print_benchmark_table(rows)
        return

    files = collect_files(cfg)

    if not files:
        print("No files matching the filters were found.", file=sys.stderr)
        sys.exit(0)

    warn_if_full_dump_overload(files, cfg)

    conventions = None if cfg.no_conventions else detect_conventions(cfg)
    baseline = None if cfg.no_baseline else collect_mandatory_baseline(cfg)
    reference_test = None if cfg.no_baseline else select_reference_test_file(cfg)

    if cfg.graph:
        graph_files = render_graph(files, cfg, conventions, baseline, reference_test)
        out_dir = write_graph_output(graph_files, cfg)
        total_chars = sum(len(c) for c in graph_files.values())
        print(
            f"Written to {out_dir}: {len(graph_files)} files, " f"{total_chars} characters total.",
            file=sys.stderr,
        )
        return

    text = render(files, cfg, conventions, baseline, reference_test)

    written = write_output(text, cfg)
    if written:
        for p in written:
            print(
                f"Written: {p} ({len(p.read_text(encoding='utf-8'))} characters)",
                file=sys.stderr,
            )

    if cfg.clipboard:
        if copy_to_clipboard(text):
            print("Result copied to clipboard.", file=sys.stderr)


if __name__ == "__main__":
    main()
