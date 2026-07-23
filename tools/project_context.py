#!/usr/bin/env python3
"""
project_context.py

CLI-утилита для объединения кода Python-проекта в один текстовый файл,
удобный для передачи в контекст LLM (ChatGPT, Claude, Gemini и т.д.).

Version: 1.0.5.0

Возможности:
- Рекурсивный обход проекта с учётом .gitignore
- Фильтр по расширениям/именам файлов (профиль "python" по умолчанию)
- Исключение служебных директорий (venv, __pycache__, .git и т.д.)
- Режим --tree-only: только дерево проекта без содержимого
- Режим --changed-only: только файлы, изменённые относительно Git (working tree / staged)
- Режим --signatures-only: только сигнатуры функций/классов (AST), без тела, в одном файле
- Режим --grep PATTERN: только файлы, содержимое которых matches regex
- Режим --graph: OKF-flavored вывод — один markdown-файл на модуль с YAML
- Режим --report: Benchmarks вывод — Benchmarking your own project
  frontmatter и явными cross-file ссылками на зависимости (import graph)
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
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
import tiktoken
import importlib.util
from dataclasses import replace

VERSION = "1.0.5.0"

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
    # файлы, которые остаются в дереве, но содержимое не выводится
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

MAX_FILE_SIZE_BYTES = 300_000  # файлы больше этого лимита не выводятся целиком
FULL_DUMP_FILE_WARNING_THRESHOLD = 40  # порог для предупреждения о перегрузке контекста


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
    include_ext: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_EXT))
    include_names: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDE_NAMES))
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_content_ext: set[str] = field(
        default_factory=lambda: set(DEFAULT_EXCLUDE_CONTENT_EXT)
    )
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
    """Возвращает список имён модулей, импортируемых в файле (top-level)."""
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
    """
    Строит граф внутренних зависимостей проекта на основе import-выражений.

    Возвращает (depends_on, used_by):
      depends_on[rel_path] -> список rel_path модулей, от которых зависит файл
      used_by[rel_path]    -> список rel_path модулей, которые зависят от файла
    """
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]

    # Индекс: имя модуля (stem файла) -> список rel_path с таким stem
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
    """Преобразует относительный путь в безопасное имя файла для --graph."""
    return rel_path.replace("/", "_").replace("\\", "_").replace(".", "_") + ".md"


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
        return (
            f"[файл пропущен: размер {size} байт превышает лимит {MAX_FILE_SIZE_BYTES}]"
        )

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    # Определяем кодировку по BOM, иначе пробуем utf-8, затем cp1251/latin-1 как fallback
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


def render_markdown(files: list[Path], cfg: Config) -> str:
    parts = []
    parts.append("# PROJECT CONTEXT\n")
    parts.append(f"Корень проекта: `{cfg.root.resolve()}`\n")
    parts.append(f"Файлов включено: {len(files)}\n")

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


def render_xml(files: list[Path], cfg: Config) -> str:
    parts = []
    parts.append("<project_context>")
    parts.append(f"  <root>{cfg.root.resolve()}</root>")
    parts.append(f"  <file_count>{len(files)}</file_count>")
    parts.append(f"  <tree><![CDATA[\n{build_tree(files, cfg.root)}\n]]></tree>")

    if cfg.tree_only:
        parts.append("</project_context>")
        return "\n".join(parts)

    if cfg.signatures_only:
        parts.append("  <signatures>")
        for f in files:
            rel = f.relative_to(cfg.root).as_posix()
            sig = extract_signatures(f)
            if sig:
                parts.append(f'    <file path="{rel}"><![CDATA[\n{sig}\n]]></file>')
        parts.append("  </signatures>")
        parts.append("</project_context>")
        return "\n".join(parts)

    parts.append("  <files>")
    for f in files:
        rel = f.relative_to(cfg.root).as_posix()
        content = read_file_content(f, cfg)
        if content is None:
            parts.append(f'    <file path="{rel}" hidden="true"/>')
        else:
            parts.append(f'    <file path="{rel}"><![CDATA[\n{content}\n]]></file>')
    parts.append("  </files>")
    parts.append("</project_context>")
    return "\n".join(parts)


def render(files: list[Path], cfg: Config) -> str:
    if cfg.output_format == "xml":
        return render_xml(files, cfg)
    return render_markdown(files, cfg)


# --------------------------------------------------------------------------- #
# --graph: OKF-flavored многофайловый вывод
# --------------------------------------------------------------------------- #


def render_graph(files: list[Path], cfg: Config) -> dict[str, str]:
    """
    Генерирует OKF-flavored вывод: один markdown-файл на модуль с YAML
    frontmatter (path, depends_on, used_by) и сигнатурами. Плюс index.md
    со ссылками на все модули.

    Возвращает словарь {имя_файла: содержимое}, который затем пишется
    на диск функцией write_graph_output().
    """
    py_files = [f for f in files if f.suffix in (".py", ".pyi")]
    depends_on, used_by = build_dependency_graph(py_files, cfg.root)

    output: dict[str, str] = {}
    index_lines = [
        "# PROJECT GRAPH INDEX\n",
        f"Корень проекта: `{cfg.root.resolve()}`\n",
    ]
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
    """
    Executes full, signatures-only, and graph modes against the same
    project root, measures tiktoken cl100k_base token counts and raw
    character counts for each, and returns comparison rows.

    If cfg.grep_pattern is set, also benchmarks the --grep mode using
    that pattern.
    """

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

    base_cfg = replace(
        cfg, tree_only=False, signatures_only=False, graph=False, grep_pattern=None
    )
    full_files = collect_files(base_cfg)
    full_text = render(full_files, base_cfg)
    rows.append(measure("full", full_text))

    sig_cfg = replace(base_cfg, signatures_only=True)
    sig_files = collect_files(sig_cfg)
    sig_text = render(sig_files, sig_cfg)
    rows.append(measure("signatures-only", sig_text))

    if cfg.grep_pattern:
        grep_cfg = replace(base_cfg, grep_pattern=cfg.grep_pattern)
        grep_files = collect_files(grep_cfg)
        grep_text = render(grep_files, grep_cfg)
        rows.append(measure(f"grep:{cfg.grep_pattern}", grep_text))

    graph_cfg = replace(base_cfg, graph=True)
    graph_files = collect_files(graph_cfg)
    graph_dict = render_graph(graph_files, graph_cfg)
    rows.append(measure("graph", graph_dict))

    baseline_tokens = rows[0]["tokens"]
    for row in rows:
        row["reduction_pct"] = round(100 * (1 - row["tokens"] / baseline_tokens), 1)
        row["multiplier"] = round(baseline_tokens / row["tokens"], 1)

    return rows


def print_benchmark_table(rows: list[dict]) -> None:
    print(
        f"{'Mode':<20} {'Chars':>10} {'Tokens':>10} {'Reduction':>10} {'Smaller':>10}"
    )
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
    parser.add_argument(
        "--version", action="version", version=f"project_context.py {VERSION}"
    )
    parser.add_argument(
        "--root", type=str, default=".", help="Корневая директория проекта"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="project_context.md",
        help="Путь к выходному файлу (или директории для --graph). Пусто/'-' для stdout",
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
        help="Скопировать результат в буфер обмена (требует pyperclip, игнорируется при --graph)",
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
        use_gitignore=not args.no_gitignore,
    )
    if args.include_ext:
        cfg.include_ext |= {e.strip() for e in args.include_ext.split(",") if e.strip()}
    if args.exclude_dir:
        cfg.exclude_dirs |= {
            d.strip() for d in args.exclude_dir.split(",") if d.strip()
        }

    if cfg.graph and cfg.output == "project_context.md":
        # если пользователь не переопределил --output явно, используем
        # разумное имя директории по умолчанию для --graph
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

    if cfg.graph:
        graph_files = render_graph(files, cfg)
        out_dir = write_graph_output(graph_files, cfg)
        total_chars = sum(len(c) for c in graph_files.values())
        print(
            f"Записано в {out_dir}: {len(graph_files)} файлов, "
            f"{total_chars} символов суммарно.",
            file=sys.stderr,
        )
        return

    text = render(files, cfg)

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
