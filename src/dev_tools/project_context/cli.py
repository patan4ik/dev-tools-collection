#!/usr/bin/env python3
"""
project_context.py

CLI-утилита для объединения кода Python-проекта в один текстовый файл,
удобный для передачи в контекст LLM (ChatGPT, Claude, Gemini и т.д.).

Version: 1.7.0

Возможности:
- Рекурсивный обход проекта с учётом .gitignore
- Фильтр по расширениям/именам файлов (профиль "python" по умолчанию)
- Исключение служебных директорий (venv, __pycache__, .git и т.д.)
- Режим --tree-only: только дерево проекта без содержимого
- Режим --changed-only: только файлы, изменённые относительно Git
  (working tree / staged)
- Режим --signatures-only: только сигнатуры функций/классов (AST),
  без тела, в одном файле
- Режим --grep PATTERN: только файлы, содержимое которых matches regex
- Режим --graph: OKF-flavored вывод — один markdown-файл на модуль с YAML
  frontmatter и явными cross-file ссылками на зависимости (import graph)
- Режим --report: Benchmarking your own project — сравнение token/char usage режимов
- PROJECT CONVENTIONS DETECTED: автоматически обнаруживает и явно, императивно
  формулирует обязательные для соблюдения LLM-моделью правила проекта — тесты,
  lint/format/type/security тулчейн, CI gate-проверки, управление зависимостями,
  docstring/naming конвенции. Вставляется в начало вывода ВО ВСЕХ режимах,
  включая --tree-only и --graph, чтобы модель не могла её пропустить.
- Предупреждение при full-dump режиме на большом количестве файлов
- Ограничение размера вывода (--max-chars) с разбиением на части
- Вывод в файл, в stdout или в буфер обмена (--clipboard)
- Формат вывода: markdown (по умолчанию) или xml-like блоки

Пример использования:
    python project_context.py --root . --output context.md
    python project_context.py --tree-only
    python project_context.py --changed-only --output diff_context.md
    python project_context.py --signatures-only --output signatures.md
    python project_context.py --grep "PortfolioSummary" --output portfolio_context.md
    python project_context.py --graph --output project_graph
    python project_context.py --max-chars 50000 --output context.md
    python project_context.py --report --grep "PortfolioSummary"
    python project_context.py --no-conventions --output context.md
    # отключить секцию конвенций
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

VERSION = "1.7.0"

# --------------------------------------------------------------------------- #
# Профили фильтров
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

# Известные dev-tool секции pyproject.toml, которые считаются "gate" проверками
KNOWN_LINT_TOOLS = {
    "tool.black": "Black (форматирование кода)",
    "tool.ruff": "Ruff (линтинг)",
    "tool.mypy": "mypy (статическая типизация)",
    "tool.bandit": "Bandit (security static analysis)",
    "tool.pytest": "pytest (конфигурация тестов)",
}

# Паттерны для распознавания шагов CI, которые обязаны проходить
CI_STEP_PATTERNS = {
    "black": re.compile(r"\bblack\b", re.IGNORECASE),
    "ruff": re.compile(r"\bruff\b", re.IGNORECASE),
    "mypy": re.compile(r"\bmypy\b", re.IGNORECASE),
    "bandit": re.compile(r"\bbandit\b", re.IGNORECASE),
    "pytest": re.compile(r"\bpytest\b", re.IGNORECASE),
    "coverage": re.compile(r"\bcov(erage)?\b", re.IGNORECASE),
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
    no_conventions: bool = False
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
            "Предупреждение: git не найден или это не git-репозиторий. "
            "--changed-only игнорируется.",
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
# Обход проекта
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
# collect_files_unfiltered: для обнаружения конвенций нужен полный список файлов
# проекта (тесты, pyproject.toml, CI workflows), независимо от активных
# --grep/--changed-only/--signatures-only фильтров текущего запуска.
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
# AST: извлечение сигнатур функций/классов (--signatures-only, --graph)
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
# AST: извлечение импортов и построение графа зависимостей (--graph)
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
# PROJECT CONVENTIONS DETECTED
#
# Философия: инструмент не должен просто выгружать факты о репозитории и
# надеяться, что LLM сама сделает правильные архитектурные и процессные
# выводы. Практика показывает (см. отдельные эксперименты с LLM-as-judge),
# что модели систематически игнорируют неявные конвенции проекта — не
# создают тестов по аналогии, не запускают линтеры мысленно, не добавляют
# новые зависимости в pyproject.toml — даже когда все нужные для этого
# факты присутствуют в контексте. Эта секция превращает найденные факты
# в явные, императивные правила и вставляется в начало КАЖДОГО режима
# вывода, включая --tree-only и --graph, чтобы модель не могла их
# пропустить мимо взгляда, как это происходит с фактами, разбросанными
# по дереву файлов или сигнатурам.
# --------------------------------------------------------------------------- #


def detect_test_pairs(files: list[Path], root: Path) -> dict:
    """Ищет пары 'модуль реализации <-> тестовый файл' по стандартному
    паттерну tests/test_<stem>.py и определяет, есть ли модули без тестов."""
    py_files = [f for f in files if f.suffix == ".py"]
    test_files = {f.stem for f in py_files if f.stem.startswith("test_")}
    test_targets = {stem[len("test_") :] for stem in test_files}

    source_modules = [
        f
        for f in py_files
        if not f.stem.startswith("test_")
        and f.stem != "__init__"
        and "test" not in {p.lower() for p in f.parts}
    ]

    covered = []
    uncovered = []
    for f in source_modules:
        if f.stem in test_targets:
            covered.append(f.relative_to(root).as_posix())
        else:
            uncovered.append(f.relative_to(root).as_posix())

    tests_dir_exists = any("tests" in f.relative_to(root).parts for f in py_files)

    return {
        "tests_dir_exists": tests_dir_exists,
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "pattern": "tests/test_<module_name>.py",
    }


def detect_lint_config(files: list[Path], root: Path) -> dict:
    """Ищет секции конфигурации lint/format/type/security тулчейна
    в pyproject.toml и определяет, какие gate-инструменты активны."""
    pyproject = next((f for f in files if f.name == "pyproject.toml"), None)
    found_tools: list[str] = []
    if pyproject is not None:
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        for section_key, label in KNOWN_LINT_TOOLS.items():
            if f"[{section_key}" in content:
                found_tools.append(label)

    pre_commit = next((f for f in files if f.name == ".pre-commit-config.yaml"), None)
    return {
        "pyproject_found": pyproject is not None,
        "tools": found_tools,
        "pre_commit_configured": pre_commit is not None,
    }


def detect_ci_requirements(files: list[Path], root: Path) -> dict:
    """Читает .github/workflows/*.yml и распознаёт обязательные gate-шаги,
    которые сгенерированный код должен пройти перед merge."""
    workflow_files = [
        f
        for f in files
        if ".github/workflows" in f.relative_to(root).as_posix() and f.suffix in (".yml", ".yaml")
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
    """Определяет, где проект объявляет зависимости, чтобы явно указать
    LLM, куда добавлять новые пакеты, использованные в сгенерированном коде."""
    candidates = [
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "setup.py",
    ]
    found = [f.name for f in files if f.name in candidates]
    return {"dependency_files": sorted(set(found))}


def detect_docstring_and_naming(files: list[Path], root: Path) -> dict:
    """Сэмплирует существующие .py файлы, чтобы определить долю функций
    с docstring и доминирующую конвенцию именования (snake_case и т.п.)."""
    py_files = [f for f in files if f.suffix == ".py"]
    total_funcs = 0
    documented_funcs = 0
    snake_case = 0
    other_case = 0
    snake_re = re.compile(r"^[a-z_][a-z0-9_]*$")

    for f in py_files[:50]:  # ограничение сэмпла для производительности
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
    """Агрегирует все обнаруженные конвенции проекта в единую структуру,
    используемую для рендеринга обязательной секции PROJECT CONVENTIONS."""
    all_files = collect_all_project_files(cfg)
    return {
        "tests": detect_test_pairs(all_files, cfg.root),
        "lint": detect_lint_config(all_files, cfg.root),
        "ci": detect_ci_requirements(all_files, cfg.root),
        "deps": detect_dependency_files(all_files, cfg.root),
        "style": detect_docstring_and_naming(all_files, cfg.root),
    }


def render_conventions_section(conv: dict) -> str:
    """Формирует явную, императивную секцию правил проекта. Формулировки
    намеренно жёсткие ('MUST', 'ОБЯЗАТЕЛЬНО'), чтобы противодействовать
    наблюдаемой тенденции LLM игнорировать неявные конвенции, разбросанные
    по дереву файлов, если они не сформулированы как прямое требование."""
    lines = []
    lines.append("## ⚠️ PROJECT CONVENTIONS DETECTED (MANDATORY — DO NOT SKIP)\n")
    lines.append(
        "The following rules were automatically detected from this repository. "
        "Any code you generate for this project MUST comply with ALL of them. "
        "Failure to comply means the generated code will fail CI or be rejected "
        "in code review, even if it is functionally correct.\n"
    )

    tests = conv["tests"]
    lines.append("### 1. Test coverage convention")
    if tests["tests_dir_exists"]:
        lines.append(
            f"- This project follows the pattern `{tests['pattern']}` — "
            f"every module in `src/` has a matching test file."
        )
        if tests["covered"]:
            lines.append(f"- Modules WITH existing tests: {', '.join(tests['covered'])}.")
        if tests["uncovered"]:
            lines.append(
                f"- ⚠️ Modules WITHOUT tests currently (do not treat as an "
                f"excuse to skip tests for new code): {', '.join(tests['uncovered'])}."
            )
        lines.append(
            "- **MUST**: if you generate a new tool/module, you MUST also "
            "generate a corresponding `tests/test_<module>.py` file with "
            "equivalent style and coverage to existing tests, even if the "
            "task prompt does not explicitly mention testing.\n"
        )
    else:
        lines.append(
            "- No `tests/` directory detected. If none exists yet, still "
            "generate a `tests/test_<module>.py` file as a professional "
            "default unless explicitly told not to.\n"
        )

    lint = conv["lint"]
    lines.append("### 2. Lint / format / type / security gate")
    if lint["tools"]:
        lines.append(
            f"- This project enforces: {', '.join(lint['tools'])} "
            f"(configured in `pyproject.toml`)."
        )
        lines.append(
            "- **MUST**: generated code MUST be written as if it will be "
            "run through Black formatting, Ruff linting, mypy type checking, "
            "and Bandit security scanning — use type hints on all functions, "
            "avoid unused imports, and avoid patterns Bandit flags (e.g. "
            "`eval`, unsanitized `subprocess` calls, hardcoded secrets)."
        )
    if lint["pre_commit_configured"]:
        lines.append(
            "- Pre-commit hooks are configured — assume every commit is "
            "checked automatically; do not generate code that would fail "
            "a pre-commit run.\n"
        )
    else:
        lines.append("")

    ci = conv["ci"]
    lines.append("### 3. CI gate requirements")
    if ci["required_checks"]:
        lines.append(
            f"- CI workflow(s) {', '.join(ci['workflow_files'])} run: "
            f"{', '.join(ci['required_checks'])} on every push/PR."
        )
        lines.append(
            "- **MUST**: treat all of the above as non-negotiable gates. "
            "Code that would fail any of them is NOT considered complete.\n"
        )
    else:
        lines.append("- No CI workflow detected in this context.\n")

    deps = conv["deps"]
    lines.append("### 4. Dependency management")
    if deps["dependency_files"]:
        lines.append(f"- Dependencies are declared in: {', '.join(deps['dependency_files'])}.")
        lines.append(
            "- **MUST**: if your generated code imports any third-party "
            "package not already visible in this context, you MUST "
            "explicitly list it as a required addition to the dependency "
            "file(s) above — do not silently assume it is installed.\n"
        )
    else:
        lines.append(
            "- No dependency declaration file detected — explicitly list "
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
        "compliance with the information given, explicitly say so — do not "
        "silently skip a convention without flagging it as an open question.\n"
    )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Форматирование вывода
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
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{name}{'/' if is_dir else ''}")
            if is_dir:
                extension = "    " if i == len(entries) - 1 else "│   "
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
        return f"[файл пропущен: размер {size} байт превышает лимит {MAX_FILE_SIZE_BYTES}]"

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


def render_markdown(files: list[Path], cfg: Config, conventions: dict | None) -> str:
    parts = []
    parts.append("# PROJECT CONTEXT\n")
    parts.append(f"Корень проекта: `{cfg.root.resolve()}`\n")
    parts.append(f"Файлов включено: {len(files)}\n")

    if conventions is not None:
        parts.append(render_conventions_section(conventions))

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
            parts.append("_[содержимое не выводится: бинарный/исключённый файл]_\n")
        else:
            lang = lang_for_highlight(f)
            parts.append(f"```{lang}\n{content}\n```\n")

    return "\n".join(parts)


def render_xml(files: list[Path], cfg: Config, conventions: dict | None) -> str:
    parts = []
    parts.append("<project_context>")
    parts.append(f"  <root>{cfg.root.resolve()}</root>")
    parts.append(f"  <file_count>{len(files)}</file_count>")

    if conventions is not None:
        parts.append("  <conventions><![CDATA[")
        parts.append(render_conventions_section(conventions))
        parts.append("  ]]></conventions>")

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


def render(files: list[Path], cfg: Config, conventions: dict | None) -> str:
    if cfg.output_format == "xml":
        return render_xml(files, cfg, conventions)
    return render_markdown(files, cfg, conventions)


# --------------------------------------------------------------------------- #
# --graph: OKF-flavored многофайловый вывод
# --------------------------------------------------------------------------- #


def render_graph(files: list[Path], cfg: Config, conventions: dict | None) -> dict[str, str]:
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    depends_on, used_by = build_dependency_graph(py_files, cfg.root)

    output: dict[str, str] = {}
    index_lines = [
        "# PROJECT GRAPH INDEX\n",
        f"Корень проекта: `{cfg.root.resolve()}`\n",
    ]

    if conventions is not None:
        index_lines.append(render_conventions_section(conventions))

    index_lines.append(f"Модулей: {len(py_files)}\n")
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
            parts.append("_[нет функций/классов на верхнем уровне]_\n")

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
# Разбиение на части по лимиту символов
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
            "Модуль pyperclip не установлен. Установите: pip install pyperclip",
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

    full_files = collect_files(base_cfg)
    full_text = render(full_files, base_cfg, conventions)
    rows.append(measure("full", full_text))

    sig_cfg = replace(base_cfg, signatures_only=True)
    sig_files = collect_files(sig_cfg)
    sig_text = render(sig_files, sig_cfg, conventions)
    rows.append(measure("signatures-only", sig_text))

    if cfg.grep_pattern:
        grep_cfg = replace(base_cfg, grep_pattern=cfg.grep_pattern)
        grep_files = collect_files(grep_cfg)
        grep_text = render(grep_files, grep_cfg, conventions)
        rows.append(measure(f"grep:{cfg.grep_pattern}", grep_text))

    graph_cfg = replace(base_cfg, graph=True)
    graph_files = collect_files(graph_cfg)
    graph_dict = render_graph(graph_files, graph_cfg, conventions)
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
        description="Объединяет код Python-проекта в один файл для LLM-контекста."
    )
    parser.add_argument("--version", action="version", version=f"project_context.py {VERSION}")
    parser.add_argument("--root", type=str, default=".", help="Корневая директория проекта")
    parser.add_argument(
        "--output",
        type=str,
        default="project_context.md",
        help=("Путь к выходному файлу (или директории для --graph). " "Пусто/'-' для stdout"),
    )
    parser.add_argument(
        "--tree-only",
        action="store_true",
        help="Вывести только дерево проекта, без содержимого файлов",
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Включить только изменённые (git status) файлы",
    )
    parser.add_argument(
        "--signatures-only",
        action="store_true",
        help="Вывести только сигнатуры функций/классов (AST) в одном файле",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help=(
            "OKF-flavored вывод: один markdown-файл на модуль с YAML "
            "frontmatter и cross-file ссылками на import-зависимости, "
            "плюс index.md. --output трактуется как директория."
        ),
    )
    parser.add_argument(
        "--grep",
        type=str,
        default=None,
        dest="grep_pattern",
        help="Включать только файлы, содержимое которых matches regex-паттерн",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="Максимум символов на файл вывода, для разбиения на части",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["md", "xml"],
        default="md",
        help="Формат вывода: markdown или xml-like (игнорируется при --graph)",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help=(
            "Скопировать результат в буфер обмена (требует pyperclip, " "игнорируется при --graph)"
        ),
    )
    parser.add_argument(
        "--no-gitignore", action="store_true", help="Не учитывать правила .gitignore"
    )
    parser.add_argument(
        "--include-ext",
        type=str,
        default=None,
        help="Доп. расширения через запятую, напр: .env,.j2",
    )
    parser.add_argument(
        "--exclude-dir",
        type=str,
        default=None,
        help="Доп. директории для исключения через запятую",
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
            "Отключить автоматическое обнаружение и вставку секции "
            "PROJECT CONVENTIONS DETECTED (тесты, lint/CI gate, "
            "зависимости, стиль). Включена по умолчанию."
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
            f"[warning] Full-dump режим с {len(files)} файлами может перегрузить "
            "контекст LLM и снизить качество ответа. Рассмотрите --changed-only, "
            "--signatures-only, --graph или --grep для более точечного контекста.",
            file=sys.stderr,
        )


def main() -> None:
    cfg = parse_args()

    if not cfg.root.exists():
        print(f"Ошибка: директория {cfg.root} не найдена", file=sys.stderr)
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
        print("Не найдено ни одного файла, подходящего под фильтры.", file=sys.stderr)
        sys.exit(0)

    warn_if_full_dump_overload(files, cfg)

    conventions = None if cfg.no_conventions else detect_conventions(cfg)

    if cfg.graph:
        graph_files = render_graph(files, cfg, conventions)
        out_dir = write_graph_output(graph_files, cfg)
        total_chars = sum(len(c) for c in graph_files.values())
        print(
            f"Записано в {out_dir}: {len(graph_files)} файлов, "
            f"{total_chars} символов суммарно.",
            file=sys.stderr,
        )
        return

    text = render(files, cfg, conventions)

    written = write_output(text, cfg)
    if written:
        for p in written:
            print(
                f"Записано: {p} ({len(p.read_text(encoding='utf-8'))} символов)",
                file=sys.stderr,
            )

    if cfg.clipboard:
        if copy_to_clipboard(text):
            print("Результат скопирован в буфер обмена.", file=sys.stderr)


if __name__ == "__main__":
    main()
