"""
astutils.py

Pure AST-based code analysis: signature extraction, import graphs,
test-module/source-module/entry-point detection.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .constants import ENTRY_POINT_PATTERN, PYTEST_TESTPATHS_PATTERN, SCRIPT_TARGET_PATTERN


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


def is_source_module(path: Path) -> bool:
    return path.suffix == ".py" and path.stem != "__init__" and not is_test_module(path)


def _looks_like_cli_entrypoint(content: str) -> bool:
    return bool(
        re.search(r"^\s*def\s+main\s*\(", content, re.MULTILINE)
        and re.search(r"__name__\s*==\s*[\"']__main__[\"']", content)
    )


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
