#!/usr/bin/env python3
"""
project_context.py

CLI-утилита для объединения кода Python-проекта в один текстовый файл,
удобный для передачи в контекст LLM (ChatGPT, Claude, Gemini и т.д.).

Возможности:
  - Рекурсивный обход проекта с учётом .gitignore
  - Фильтр по расширениям/именам файлов (профиль "python" по умолчанию)
  - Исключение служебных директорий (venv, __pycache__, .git и т.д.)
  - Режим --tree-only: только дерево проекта без содержимого
  - Режим --changed-only: только файлы, изменённые относительно Git (working tree / staged)
  - Ограничение размера вывода (--max-chars) с разбиением на части
  - Вывод в файл, в stdout или в буфер обмена (--clipboard)
  - Формат вывода: markdown (по умолчанию) или xml-like блоки

Пример использования:
    python project_context.py --root . --output context.md
    python project_context.py --tree-only
    python project_context.py --changed-only --output diff_context.md
    python project_context.py --max-chars 50000 --output context.md
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class Config:
    root: Path
    output: str | None
    tree_only: bool
    changed_only: bool
    max_chars: int | None
    output_format: str
    clipboard: bool
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

            collected.append(full_path)

    return sorted(collected)


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

    raw = None
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
    parts.append("  <tree><![CDATA[")
    parts.append(build_tree(files, cfg.root))
    parts.append("  ]]></tree>")

    if not cfg.tree_only:
        parts.append("  <files>")
        for f in files:
            rel = f.relative_to(cfg.root).as_posix()
            content = read_file_content(f, cfg)
            parts.append(f'    <file path="{rel}">')
            if content is None:
                parts.append("      <!-- содержимое не выводится -->")
            else:
                parts.append("      <![CDATA[")
                parts.append(content)
                parts.append("      ]]>")
            parts.append("    </file>")
        parts.append("  </files>")

    parts.append("</project_context>")
    return "\n".join(parts)


def render(files: list[Path], cfg: Config) -> str:
    if cfg.output_format == "xml":
        return render_xml(files, cfg)
    return render_markdown(files, cfg)


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
# CLI
# --------------------------------------------------------------------------- #


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Объединяет код Python-проекта в один файл для LLM-контекста."
    )
    parser.add_argument(
        "--root", type=str, default=".", help="Корневая директория проекта"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="project_context.md",
        help="Путь к выходному файлу. Пусто/'-' для stdout",
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
        help="Формат вывода: markdown или xml-like",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Скопировать результат в буфер обмена (требует pyperclip)",
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

    args = parser.parse_args()

    output = None if (args.output in ("-", "", None)) else args.output

    cfg = Config(
        root=Path(args.root).resolve(),
        output=output,
        tree_only=args.tree_only,
        changed_only=args.changed_only,
        max_chars=args.max_chars,
        output_format=args.format,
        clipboard=args.clipboard,
        use_gitignore=not args.no_gitignore,
    )

    if args.include_ext:
        cfg.include_ext |= {e.strip() for e in args.include_ext.split(",") if e.strip()}
    if args.exclude_dir:
        cfg.exclude_dirs |= {
            d.strip() for d in args.exclude_dir.split(",") if d.strip()
        }

    return cfg


def main() -> None:
    cfg = parse_args()

    if not cfg.root.exists():
        print(f"Ошибка: директория {cfg.root} не найдена", file=sys.stderr)
        sys.exit(1)

    files = collect_files(cfg)

    if not files:
        print("Не найдено ни одного файла, подходящего под фильтры.", file=sys.stderr)
        sys.exit(0)

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
