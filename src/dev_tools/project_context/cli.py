#!/usr/bin/env python3
"""
project_context.py

CLI tool that bundles a Python project's code into a single text file
suitable for feeding into an LLM's context window (ChatGPT, Claude,
Gemini, etc.).

Version: 1.9.6

NEW IN 1.9.6 (fixes real duplication/formatting issues found in
production use of the 1.9.5 KISS reference summary):
- REMOVED the "All top-level signatures" block from the KISS reference
  summary entirely, in every mode. It was byte-for-byte duplicated by
  the "## SIGNATURES" section in --signatures-only mode (making
  --tree-only-with-reference and --signatures-only look nearly
  identical), and was unnecessary bulk in every other mode where the
  full file (or nothing) is shown elsewhere.
- --tree-only now renders a MINIMAL reference summary: only the
  module's one-line purpose plus ONE complete representative function
  body -- no import list. This preserves the original fix for the
  blind-judge "zero-code refusal" failure mode (--tree-only must still
  show at least one real, complete code example) while remaining
  clearly smaller than --signatures-only, which additionally shows the
  full top-level import list and the full "## SIGNATURES" listing for
  every collected file.
- --signatures-only (and the default full-dump mode) keep the import
  list in the KISS summary (it is NOT duplicated elsewhere -- neither
  "## SIGNATURES" nor "## FILE CONTENTS" separately calls out imports)
  plus the one representative function body.
- Fixed a section-heading formatting bug: the reference summary title
  previously rendered as "...(style exemplar: `path` (KISS summary --
  not the full file)" -- two open parentheses, never properly closed.
  Now renders as "...(style exemplar) -- KISS summary, not the full
  file: `path`".

Mode-size ordering is now strictly:
  --tree-only  <  --signatures-only  <  default full-dump
which was the original design goal restated by the user after testing
showed --tree-only and --signatures-only outputs had converged.

Inherited from 1.9.5/1.9.4/1.9.3/1.9.0/1.8.x -- see CHANGELOG.md for
the full history, including the GitDiagram-parity module graph edges
(imports, registers entry point, belongs to, documents, packages,
validates, runs, builds from, produces, publishes build, invokes),
all 100% deterministic (AST + regex + local git/CI-config parsing),
no LLM call, no network access.
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

VERSION = "1.9.6"

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
DEFAULT_EXCLUDE_FILES = {"poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock"}

MAX_FILE_SIZE_BYTES = 300_000
FULL_DUMP_FILE_WARNING_THRESHOLD = 40

KNOWN_LINT_TOOLS = {
    "tool.black": "Black (code formatting)",
    "tool.ruff": "Ruff (linting)",
    "tool.mypy": "mypy (static typing)",
    "tool.bandit": "Bandit (security static analysis)",
    "tool.pytest": "pytest (test configuration)",
}
CI_STEP_PATTERNS = {
    "black": re.compile(r"\bblack\b", re.IGNORECASE),
    "ruff": re.compile(r"\bruff\b", re.IGNORECASE),
    "mypy": re.compile(r"\bmypy\b", re.IGNORECASE),
    "bandit": re.compile(r"\bbandit\b", re.IGNORECASE),
    "pytest": re.compile(r"\bpytest\b", re.IGNORECASE),
    "coverage": re.compile(r"\bcov(erage)?\b", re.IGNORECASE),
}
CI_BUILD_TOOL_PATTERN = re.compile(
    r"\b(pyinstaller|python\s+-m\s+build|setup\.py\s+bdist|twine|nuitka|cx_freeze)\b",
    re.IGNORECASE,
)
CI_CONFIG_PATH_PATTERNS = (
    re.compile(r"(^|/)\.github/workflows/.+\.ya?ml$"),
    re.compile(r"(^|/)\.gitlab-ci\.ya?ml$"),
    re.compile(r"(^|/)azure-pipelines\.ya?ml$"),
    re.compile(r"(^|/)Jenkinsfile$"),
    re.compile(r"(^|/)\.circleci/config\.ya?ml$"),
)
DEPENDENCY_MANIFEST_SIGNATURES = {
    ".toml": re.compile(r"^\s*\[(project|tool\.poetry|build-system)\]", re.MULTILINE),
    ".txt": re.compile(r"^[A-Za-z0-9_.\-]+\s*[=<>!~]{0,2}=?\s*[\d.]*\s*$", re.MULTILINE),
    ".cfg": re.compile(r"^\s*\[options(\.\w+)?\]", re.MULTILINE),
    ".yml": re.compile(r"^\s*dependencies:\s*$", re.MULTILINE),
    ".yaml": re.compile(r"^\s*dependencies:\s*$", re.MULTILINE),
}
PRE_COMMIT_CONTENT_SIGNATURE = re.compile(r"^\s*repos:\s*$", re.MULTILINE)
PYTEST_TESTPATHS_PATTERN = re.compile(r"testpaths\s*=\s*(.+)")
PYTEST_PYTHON_FILES_PATTERN = re.compile(r"python_files\s*=\s*(.+)")
EXACT_PIN_PATTERN = re.compile(r"[A-Za-z0-9_.\-]+\s*==\s*[\d][\w.\-]*")
RANGE_PIN_PATTERN = re.compile(r"[A-Za-z0-9_.\-]+\s*(>=|<=|~=|>|<)\s*[\d]")
CHANGELOG_HEADER_PATTERN = re.compile(
    r"^\#{1,3}\s*\[?(Unreleased|\d+\.\d+(\.\d+)?)\]?", re.IGNORECASE | re.MULTILINE
)

INTEGRATION_SCOPES = ("standalone", "integrated")
DIAGRAM_MODES = ("auto", "none", "text", "mermaid")

ENTRY_POINT_PATTERN = re.compile(r"\[project\.scripts\]\s*\n(.+?)(?:\n\[|\Z)", re.DOTALL)
SCRIPT_TARGET_PATTERN = re.compile(
    r"^\s*([\w.\-]+)\s*=\s*[\"']([\w.]+)(?::(\w+))?[\"']", re.MULTILINE
)

NON_CODE_DOC_EXT = {".md", ".rst", ".txt"}
NON_CODE_CONFIG_NAMES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "pytest.ini",
    ".pre-commit-config.yaml",
    "pyvenv.cfg",
    "Makefile",
    "Dockerfile",
}


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
    integration_scope: str = "standalone"
    diagram_mode: str = "auto"
    no_conventions: bool = False
    no_baseline: bool = False
    no_plan_gate: bool = False
    include_ext: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_EXT))
    include_names: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_NAMES))
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_content_ext: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_CONTENT_EXT))
    exclude_files: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_FILES))
    use_gitignore: bool = True

    def resolved_diagram_mode(self) -> str:
        if self.diagram_mode != "auto":
            return self.diagram_mode
        return "text" if (self.tree_only or self.signatures_only) else "none"


_TIKTOKEN_ENCODER = None
_TIKTOKEN_AVAILABLE = importlib.util.find_spec("tiktoken") is not None


def estimate_tokens(text: str) -> tuple[int, str]:
    global _TIKTOKEN_ENCODER
    if _TIKTOKEN_AVAILABLE:
        if _TIKTOKEN_ENCODER is None:
            import tiktoken

            _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        return len(_TIKTOKEN_ENCODER.encode(text)), "tiktoken cl100k_base"
    return max(1, len(text) // 4), "approx, chars/4 -- install tiktoken for exact count"


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


def get_git_remote_url(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    url = result.stdout.strip()
    match = re.search(r"github\.com[:/]([\w.\-]+)/([\w.\-]+?)(?:\.git)?$", url)
    if not match:
        return None
    owner, repo = match.groups()
    branch = "main"
    try:
        branch_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        if branch_result.stdout.strip():
            branch = branch_result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return f"https://github.com/{owner}/{repo}/blob/{branch}"


def matches_grep(path: Path, pattern: re.Pattern) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return pattern.search(content) is not None


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
    return rel_path.replace("/", "_").replace("\\\\", "_").replace(".", "_") + ".md"


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
            names = ", ".join(a.name for a in node.names)
            lines.append(f"from {mod} import {names}")
    return lines


def _extract_representative_function(source: str) -> tuple[str, str] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    candidates: list[tuple[int, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    fallback: ast.FunctionDef | ast.AsyncFunctionDef | None = None
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
    """1.9.6: the 'All top-level signatures' block was removed entirely
    -- it duplicated '## SIGNATURES' byte-for-byte in --signatures-only
    mode, and added nothing of value elsewhere. include_imports=False
    (used only by --tree-only) additionally drops the import list,
    keeping --tree-only strictly smaller than --signatures-only, which
    still shows the import list plus the full '## SIGNATURES' listing
    for every collected file (not just this one representative file).
    """
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
            f"**One complete, representative function (`{name}`) -- copy this exact "
            f"style/error-handling pattern verbatim, do not invent a different one:**\n"
            f"```python\n{segment}\n```\n"
        )
    parts.append(
        "_Bounded summary, not the full file. Request it explicitly or use the "
        "default full-dump mode for the complete, byte-exact source._\n"
    )
    return "\n".join(parts)


def select_reference_test_file(cfg: Config) -> tuple[str, str] | None:
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
    return rel, content


def is_source_module(path: Path) -> bool:
    return path.suffix == ".py" and path.stem != "__init__" and not is_test_module(path)


def _looks_like_cli_entrypoint(content: str) -> bool:
    return bool(
        re.search(r"^\s*def\s+main\s*\(", content, re.MULTILINE)
        and re.search(r"__name__\s*==\s*[\"']__main__[\"']", content)
    )


def select_reference_source_file(cfg: Config) -> tuple[str, str] | None:
    all_files = collect_all_project_files(cfg)
    candidates = [f for f in all_files if is_source_module(f)]
    if not candidates:
        return None

    scored: list[tuple[int, str, str]] = []
    entrypoints: list[tuple[int, str, str]] = []
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


def detect_entry_points(files: list[Path], root: Path) -> dict[str, str]:
    pyproject = next((f for f in files if f.name == "pyproject.toml"), None)
    if pyproject is None:
        return {}
    try:
        content = pyproject.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    block_match = ENTRY_POINT_PATTERN.search(content)
    if not block_match:
        return {}

    module_to_path: dict[str, str] = {}
    for f in files:
        if f.suffix != ".py":
            continue
        rel = f.relative_to(root).as_posix()
        rel_for_dotted = rel[4:] if rel.startswith("src/") else rel
        dotted = rel_for_dotted[:-3].replace("/", ".")
        module_to_path[dotted] = rel

    mapping: dict[str, str] = {}
    for cmd_name, target_module, _func in SCRIPT_TARGET_PATTERN.findall(block_match.group(1)):
        if target_module in module_to_path:
            mapping[cmd_name] = module_to_path[target_module]
    return mapping


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
    by_depth = sorted(init_files, key=lambda r: r.count("/"))
    return by_depth[0]


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
        refs = _test_referenced_modules(t, test_contents.get(t, ""), source_rels)
        for src_rel in refs:
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
        invoker_node = "User / automation invoker"
        node_labels[invoker_node] = "actor"
        for _cmd_name, target_rel in sorted(entry_points.items()):
            edges.append((invoker_node, target_rel, "invokes", False))

    if root_init:
        importer_node = "External Python callers"
        node_labels[importer_node] = "actor"
        edges.append((importer_node, root_init, "imports", False))

    return node_labels, edges


def render_module_graph_text(files: list[Path], root: Path, entry_points: dict[str, str]) -> str:
    node_labels, edges = detect_module_graph_edges(files, root, entry_points)
    if not edges:
        return ""

    lines = ["\n## MODULE GRAPH (auto-generated, deterministic -- no LLM)\n"]
    seen = set()
    for src, dst, label, _dashed in edges:
        role = node_labels.get(src, "")
        role_tag = f" [{role}]" if role else ""
        lines.append(f"- `{src}`{role_tag} --{label}--> `{dst}`")
        seen.add(src)
        seen.add(dst)

    isolated = [r for r in node_labels if r not in seen]
    for rel in sorted(isolated):
        role = node_labels.get(rel, "")
        lines.append(f"- `{rel}` [{role}] (no detected relationships)")

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

    lines = ["\n## MODULE GRAPH (Mermaid, deterministic -- no LLM)\n"]
    lines.append("```mermaid")
    lines.append("flowchart TD")

    for rel in sorted(node_labels):
        role = node_labels[rel]
        nid = node_ids[rel]
        shape = f'(("{rel}"))' if role == "actor" else f'["{rel}<br/>[{role}]"]'
        lines.append(f"  {nid}{shape}")

    for src, dst, label, dashed in edges:
        if src not in node_ids or dst not in node_ids:
            continue
        arrow = "-.->" if dashed else "-->"
        lines.append(f'  {node_ids[src]} {arrow}|"{label}"| {node_ids[dst]}')

    for rel in sorted(node_labels):
        style = MERMAID_ROLE_STYLES.get(node_labels[rel], "toneNeutral")
        lines.append(f"  class {node_ids[rel]} {style}")

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


def detect_test_pairs(files: list[Path], root: Path) -> dict:
    py_files = [f for f in files if f.suffix == ".py"]
    test_files = [f for f in py_files if is_test_module(f)]
    test_stems = {f.stem for f in test_files}

    source_modules = [f for f in py_files if f not in test_files and f.stem != "__init__"]

    covered = []
    uncovered = []
    for f in source_modules:
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
    exact_hits = 0
    range_hits = 0
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
    total_funcs = 0
    documented_funcs = 0
    snake_case = 0
    other_case = 0
    snake_re = re.compile(r"^[a-z_][a-z0-9_]*$")

    for f in py_files[:50]:
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


def detect_docs_convention(files: list[Path], root: Path) -> dict:
    readme = next((f for f in files if f.name.lower() == "readme.md"), None)
    changelog = next((f for f in files if f.name.lower() == "changelog.md"), None)

    changelog_format = None
    if changelog is not None:
        try:
            content = changelog.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        if CHANGELOG_HEADER_PATTERN.search(content):
            changelog_format = "Keep a Changelog style ('## [Unreleased]' / '## [x.y.z]' headers)"
        else:
            changelog_format = "free-form (no standard version-header pattern detected)"

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
    else:
        lines.append("- No lint/type/security tooling detected in this context.\n")

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
        lines.append(f"- Observed version-pinning style: **{deps['pin_style']}**.")
        lines.append(
            "- **MUST**: if your generated code imports any third-party "
            "package not already visible in this context, you MUST "
            "explicitly list it as a required addition to the dependency "
            "file(s) above, using the SAME pinning style, and flag any "
            "known unmaintained/insecure package as an open risk -- do not "
            "silently assume it is installed.\n"
        )
    else:
        lines.append(
            "- No dependency declaration file detected -- explicitly list "
            "any third-party packages your code requires and propose where "
            "to declare them.\n"
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
            "with the rest of the codebase. See the REFERENCE SOURCE MODULE "
            "below for the exact architecture/style pattern to follow.\n"
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
                f"- CHANGELOG detected at `{docs['changelog_path']}` "
                f"(format: {docs['changelog_format']})."
            )
        lines.append(
            "- **MUST**: any new tool/feature/dependency you add MUST come "
            "with a matching README section (usage) and a new CHANGELOG "
            "entry in the same format as the existing entries. State the "
            "exact diff/snippet to add, do not just say 'update the docs'.\n"
        )
    else:
        lines.append(
            "- No README.md or CHANGELOG.md detected in this context -- "
            "still propose a short usage note for any new tool.\n"
        )

    lines.append(
        "### If context is insufficient\n"
        "If any of the above conventions are ambiguous or you cannot verify "
        "compliance with the information given, explicitly say so -- do not "
        "silently skip a convention without flagging it as an open question. "
        "This does NOT excuse you from still producing a best-effort "
        "implementation (see SENIOR-DEVELOPER MANDATE below).\n"
    )

    return "\n".join(lines)


def render_baseline_section(
    bundle: dict[str, tuple[str, str]],
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None = None,
    minimal_reference: bool = False,
) -> str:
    """minimal_reference=True (used only by --tree-only) drops the
    import-list block from the reference exemplars, keeping only the
    module purpose line and one complete representative function --
    strictly smaller than --signatures-only's reference summary, which
    keeps the import list (see render_kiss_reference_summary)."""
    lines = ["## \U0001f4ce MANDATORY BASELINE FILES (verbatim -- read before anything else)\n"]
    lines.append(
        "These are this project's binding contracts and style exemplars, "
        "detected by role/content signature rather than by an assumed "
        "filename. Contract files (dependency manifest, CI config, "
        "pre-commit config) below are shown VERBATIM -- treat their exact "
        "content as ground truth. The reference source/test file are shown "
        "as a bounded KISS summary, not the full file (see note below each). "
        "Reference all of them by their short relative path shown here, not "
        "by any upload/storage URL that may appear elsewhere in this "
        "conversation.\n"
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
        label = role_labels.get(role, role)
        lines.append(f"### {label}: `{rel}`\n```\n{content}\n```\n")

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
        "standalone": (
            "STANDALONE -- add the requested functionality as a new, "
            "independently runnable module. Do NOT modify existing entry "
            "points, registries, or wiring unless the task explicitly asks "
            "for it."
        ),
        "integrated": (
            "INTEGRATED -- the new functionality MUST be wired into this "
            "project's existing entry points/CLI registry/registration "
            "pattern shown in the baseline files. Show the exact diff "
            "required to register it, not just the new file's content."
        ),
    }.get(integration_scope, integration_scope)

    lines = [
        "\n## \U0001f6a8 SENIOR-DEVELOPER MANDATE (read before Step 1)\n",
        "You are acting as a senior Python developer, not a junior who does "
        "only the literal minimum listed in the prompt. You MUST see the big "
        "picture: project structure, tests, CI/CD, naming conventions, "
        "dependency and version constraints, known security risks, and "
        "documentation -- exactly like a human senior engineer would before "
        "opening a pull request.",
        "**Never respond with zero code.** If a genuinely ambiguous detail "
        "exists (e.g. an exact business formula or an unconfirmed target "
        "path), state the assumption explicitly, pick the most reasonable "
        "default consistent with the MANDATORY BASELINE FILES, and proceed "
        "to implement it anyway. Reserve 'insufficient context, do not "
        "guess' strictly for details that would silently corrupt behavior -- "
        "never as a reason to withhold an entire implementation.\n",
        f"**Integration scope for this task: {integration_scope}.** {scope_text}\n",
        "\n## \U0001f6a6 STEP 1 -- ARCHITECTURE PLAN (required before any code)\n",
        f"This project's detected contract file roles: {roles_text}.\n",
        "Before writing the requested module, produce a short 'Architecture Plan' "
        "section that explicitly answers, referencing the MANDATORY BASELINE FILES above:\n",
        "1. Target module path -- matching this project's existing source layout "
        "and the REFERENCE SOURCE MODULE's location pattern"
        + (
            ""
            if reference_source
            else " (state explicitly that no source exemplar was found and name the "
            "convention you inferred instead)"
        )
        + ".",
        "2. Dependency manifest change -- quote the exact diff, using the real "
        "detected file's syntax and the SAME version-pinning style already used "
        "in that file.",
        "3. Test file -- path/name and content, modeled on the REFERENCE TEST FILE "
        "above"
        + (
            ""
            if reference_test
            else " (state explicitly that none was found and that a new test module "
            "is required as a default)"
        )
        + ".",
        "4. CI/lint/type/security gates -- list exactly which detected checks your code must pass.",
        "5. Any dependency version constraints, known security advisories, or "
        "breaking-change risks you must respect.",
        "6. Documentation updates -- the exact README/CHANGELOG snippet you will "
        "add or state explicitly that none were detected and propose one.\n",
    ]
    lines.append(
        "\n## \U0001f6a6 STEP 2 -- SELF-VALIDATION CHECKLIST "
        "(required after code, before finishing)\n"
        "Re-read your own Step 1 plan. For each of the 6 items, state PASS or FAIL "
        "with the concrete artifact produced (file name, diff line, or explicit "
        "justification for why it was skipped). A response with unresolved FAIL "
        "items or missing artifacts is INCOMPLETE per this project's contract.\n"
        "\n**Definition of Done:** finish with a single line "
        "`COMPLETION: N%` where N is your own honest estimate (0-100) of how "
        "much of this task is directly copy-paste-usable without further "
        "human rework -- code, tests, dependency diff, and docs all count "
        "against this number."
    )
    return "\n".join(lines)


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
    reference_source: tuple[str, str] | None = None,
) -> str:
    parts = []
    parts.append("# PROJECT CONTEXT\n")
    parts.append(f"Project root: `{cfg.root.resolve()}`\n")
    parts.append(f"Files included: {len(files)}\n")

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
            lang = lang_for_highlight(f)
            parts.append(f"```{lang}\n{content}\n```\n")

    return "\n".join(parts)


def render_xml(
    files: list[Path],
    cfg: Config,
    conventions: dict | None,
    baseline: dict[str, tuple[str, str]] | None,
    reference_test: tuple[str, str] | None,
    reference_source: tuple[str, str] | None = None,
) -> str:
    parts = []
    parts.append("<project_context>")
    parts.append(f"  <root>{cfg.root.resolve()}</root>")
    parts.append(f"  <files_included>{len(files)}</files_included>")

    if conventions is not None:
        conv_text = render_conventions_section(conventions)
        parts.append(f"  <conventions_detected><![CDATA[{conv_text}]]></conventions_detected>")

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

    tree_text = build_tree(files, cfg.root)
    parts.append(f"  <tree><![CDATA[{tree_text}]]></tree>")

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
    index_lines = [
        "# PROJECT GRAPH INDEX\n",
        f"Project root: `{cfg.root.resolve()}`\n",
    ]

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


def run_benchmark(cfg: Config) -> list[dict]:
    try:
        import tiktoken
    except ImportError:
        print("--report requires tiktoken. Install with: pip install tiktoken", file=sys.stderr)
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
    reference_source = None if cfg.no_baseline else select_reference_source_file(base_cfg)

    full_files = collect_files(base_cfg)
    full_text = render(
        full_files, base_cfg, conventions, baseline, reference_test, reference_source
    )
    rows.append(measure("full", full_text))

    tree_cfg = replace(base_cfg, tree_only=True)
    tree_files = collect_files(tree_cfg)
    tree_text = render(
        tree_files, tree_cfg, conventions, baseline, reference_test, reference_source
    )
    rows.append(measure("tree-only", tree_text))

    sig_cfg = replace(base_cfg, signatures_only=True)
    sig_files = collect_files(sig_cfg)
    sig_text = render(sig_files, sig_cfg, conventions, baseline, reference_test, reference_source)
    rows.append(measure("signatures-only", sig_text))

    if cfg.grep_pattern:
        grep_cfg = replace(base_cfg, grep_pattern=cfg.grep_pattern)
        grep_files = collect_files(grep_cfg)
        grep_text = render(
            grep_files, grep_cfg, conventions, baseline, reference_test, reference_source
        )
        rows.append(measure(f"grep:{cfg.grep_pattern}", grep_text))

    graph_cfg = replace(base_cfg, graph=True)
    graph_files = collect_files(graph_cfg)
    graph_dict = render_graph(
        graph_files, graph_cfg, conventions, baseline, reference_test, reference_source
    )
    rows.append(measure("graph", graph_dict))

    baseline_tokens = rows[0]["tokens"]
    for row in rows:
        row["reduction_pct"] = round(100 * (1 - row["tokens"] / baseline_tokens), 1)
        row["multiplier"] = round(baseline_tokens / row["tokens"], 1) if row["tokens"] else 0.0

    return rows


def print_benchmark_table(rows: list[dict]) -> None:
    print(f"{'Mode':<20} {'Chars':>10} {'Tokens':>10} {'Reduction':>10} {'Smaller':>10}")
    print("-" * 62)
    for row in rows:
        print(
            f"{row['mode']:<20} {row['characters']:>10} {row['tokens']:>10} "
            f"{row['reduction_pct']:>9}% {row['multiplier']:>9}x"
        )


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
        help="Path to the output file (or directory for --graph). Empty/'-' for stdout",
    )
    parser.add_argument(
        "--tree-only",
        action="store_true",
        help="Output only the project tree, without file contents",
    )
    parser.add_argument(
        "--changed-only", action="store_true", help="Include only changed (git status) files"
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
        help="Copy the result to the clipboard (requires pyperclip, ignored with --graph)",
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
            "Run full, tree-only, signatures-only, graph (and grep, if --grep "
            "is set) modes against the same root and print a token/character "
            "comparison table using tiktoken (cl100k_base)."
        ),
    )
    parser.add_argument(
        "--integration-scope",
        type=str,
        choices=list(INTEGRATION_SCOPES),
        default="standalone",
        help=(
            "'standalone' (default): the requested task is a new, independent "
            "module -- do not touch existing wiring. 'integrated': the task "
            "REQUIRES wiring into the existing entry points/CLI registry, and "
            "the plan gate will demand an explicit diff for it."
        ),
    )
    parser.add_argument(
        "--diagram",
        type=str,
        choices=list(DIAGRAM_MODES),
        default="auto",
        dest="diagram_mode",
        help=(
            "'auto' (default): 'text' module-graph edges for --tree-only and "
            "--signatures-only, 'none' otherwise. 'none': never render a "
            "module graph. 'text': always render deterministic text-arrow "
            "edges below PROJECT TREE. 'mermaid': same edges as a Mermaid "
            "flowchart, with 'click' links if a github.com git remote is "
            "configured locally. Fully deterministic, no LLM call."
        ),
    )
    parser.add_argument(
        "--no-conventions",
        action="store_true",
        help=(
            "Disable automatic detection and injection of the "
            "PROJECT CONVENTIONS DETECTED section. Enabled by default."
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help=(
            "Disable the MANDATORY BASELINE FILES bundle and the "
            "ARCHITECTURE PLAN GATE entirely. Enabled by default."
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
        integration_scope=args.integration_scope,
        diagram_mode=args.diagram_mode,
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
            print("--report requires tiktoken. Install with: pip install tiktoken", file=sys.stderr)
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
    reference_source = None if cfg.no_baseline else select_reference_source_file(cfg)

    if cfg.graph:
        graph_files = render_graph(
            files, cfg, conventions, baseline, reference_test, reference_source
        )
        out_dir = write_graph_output(graph_files, cfg)
        total_chars = sum(len(c) for c in graph_files.values())
        total_tokens, method = estimate_tokens("".join(graph_files.values()))
        print(
            f"Written to {out_dir}: {len(graph_files)} files, "
            f"{total_chars} characters, ~{total_tokens} tokens total ({method}).",
            file=sys.stderr,
        )
        return

    text = render(files, cfg, conventions, baseline, reference_test, reference_source)

    written = write_output(text, cfg)
    if written:
        for p in written:
            written_text = p.read_text(encoding="utf-8")
            tok_count, method = estimate_tokens(written_text)
            print(
                f"Written: {p} ({len(written_text)} characters, ~{tok_count} tokens, {method})",
                file=sys.stderr,
            )

    if cfg.clipboard:
        if copy_to_clipboard(text):
            print("Result copied to clipboard.", file=sys.stderr)


if __name__ == "__main__":
    main()
