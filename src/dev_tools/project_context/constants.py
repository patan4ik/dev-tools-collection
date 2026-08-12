"""
constants.py

Shared regex patterns, default filter sets, and fixed filenames used
across the project_context package. No logic here -- pure data, so
every other module can import from one place without circular deps.
"""

from __future__ import annotations

import re

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

GENERATED_MARKER_FILENAME = ".project_context_generated"
ANALYSIS_OUTPUT_DIRNAME = "project_context_output"
INTERACTIVE_CONFIG_FILENAME = "project_context_config.json"

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

CLI_FLAG_MENTION_PATTERN = re.compile(r"--[a-zA-Z][a-zA-Z0-9\-]*")
ARGPARSE_ADD_ARGUMENT_PATTERN = re.compile(r'add_argument\(\s*["\'](--[a-zA-Z][a-zA-Z0-9\-]*)["\']')
CHANGELOG_ENTRY_PATTERN = re.compile(
    r"^\#\#\s*\[?([\d.]+|Unreleased)\]?[^\n]*\n(.*?)(?=^\#\#\s|\Z)", re.MULTILINE | re.DOTALL
)
