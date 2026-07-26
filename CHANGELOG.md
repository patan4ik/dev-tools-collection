# Changelog

## [1.7.0] - 2026-07-26

### Added
- `detect_conventions()`: automatically detects five categories of project convention from the repository itself, rather than relying on the LLM to infer them from raw file contents:
  1. **Test coverage convention** — pairs `<module>.py` with `tests/test_<module>.py`, lists covered and uncovered modules, and instructs the model to generate a matching test file for any new module, even if the prompt never mentions testing.
  2. **Lint / format / type / security gate** — parses `pyproject.toml` for `[tool.black]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.bandit]`, `[tool.pytest]` sections and checks for `.pre-commit-config.yaml`.
  3. **CI gate requirements** — reads `.github/workflows/*.yml` and detects, by regex, which checks (`pytest`, `mypy`, `bandit`, `coverage`) are actually executed in CI, not just present in a config file.
  4. **Dependency management** — identifies where dependencies are declared (`pyproject.toml` / `requirements.txt`) and requires new third-party imports to be listed there explicitly.
  5. **Code style conventions** — samples up to 50 `.py` files via AST to compute docstring coverage ratio and dominant naming convention (`snake_case` vs. mixed/camelCase).
- New `## PROJECT CONVENTIONS DETECTED` section rendered at the top of every output mode — including `--tree-only`, `--signatures-only`, `--graph`, and both `md`/`xml` formats — so no lightweight mode can silently omit the detected rules.
- `--no-conventions` flag: disables the new section entirely, for clean A/B baseline comparisons against pre-1.7.0 prompt behavior.
- `collect_all_project_files()`: convention detection always scans the *entire* project regardless of active `--grep`/`--changed-only`/`--signatures-only` filters for the current run, so scoped invocations don't produce incomplete convention data.

### Fixed
- `pyproject.toml` previously declared lint/security tools only as installable dependencies without any corresponding `[tool.*]` configuration sections — meaning `detect_lint_config()` would report "no lint tools detected" on the tool's own repository. Added minimal working `[tool.black]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.bandit]`, `[tool.pytest.ini_options]` sections.

### Rationale
- A token-budget experiment using an LLM-as-judge (four context modes: `--tree-only`, `--signatures-only`, `--graph`, full dump) found that no mode — including full dump, which contained the actual test file's full source — caused the executor model to generate a matching unit test for a newly requested tool. This held even though the model was explicitly cast as "an experienced Python developer" and the repository visibly followed a one-test-file-per-module pattern. The gap was not a context *volume* problem (it happened identically at 540 chars and 68,720 chars) but a context *interpretation* problem: structural facts were visible but never translated into an explicit instruction. `detect_conventions()` closes that gap by converting detected facts into imperative, unavoidable rules rather than leaving inference to the model.
- The same experiment found a "calibration inversion": full context produced the *lowest* Calibration score (2/5) of all four modes, with the judge noting the model "more confidently assert[ed] compliance with the project architecture" without sufficient justification, while `--graph` (least token-efficient of the three scoped modes) scored a perfect 5/5 by explicitly flagging its own assumptions. This suggests more context alone does not improve epistemic honesty and can actively reduce it — motivating the decision to make convention detection an explicit, structural feature of the tool rather than something left to emerge from raw context volume.

### Testing
- `tests/test_project_context.py` extended with 13 new tests: presence/absence of the conventions section, non-skippability across `--tree-only`/`--signatures-only`/`--graph`/`--format xml`, per-category detection (test pairs, lint tools, CI checks, dependency files, naming style) via a new `make_project_with_conventions()` fixture, and a scoping-correctness test confirming conventions are computed from the whole project even when `--grep` narrows the current run's file list.

## [1.6.0] - 2026-07-24

### Added
- Extracted `project_context.py` from `kraken-portfolio-tracker` into its own standalone repository, `dev-tools-collection`, preserving file history via `git filter-repo`. This repo is designed to hold a growing collection of independent developer CLI tools, each installable and buildable into a standalone binary.
- Restructured as an installable Python package: source moved to `src/dev_tools/project_context/cli.py` (renamed from `project_context.py`), with `pyproject.toml` defining a `project-context` console-script entry point.
- `[project.optional-dependencies]` extra (`report`) declaring `tiktoken` as an explicit, installable dependency for `--report` mode, replacing the previous implicit/undeclared dependency.
- `README.md`, `CONTRIBUTING.md`, and `LICENSE.md` (MIT) added at the repo root.
- GitHub Actions workflow `.github/workflows/tests.yml` — runs Black (format check), Ruff (lint), and the pytest suite with coverage, on every push and pull request to `main`, across Python 3.11–3.13.
- GitHub Actions workflow `.github/workflows/build-binaries.yml` — builds a standalone `project-context` binary per OS (Linux, Windows, macOS) via PyInstaller and publishes them as GitHub Release assets, triggered on version tag pushes (`v*`).

### Fixed
- `build-binaries.yml` initially failed release creation with a 403 ("Resource not accessible by integration") because the default `GITHUB_TOKEN` lacked write access. Fixed by adding an explicit `permissions: contents: write` block to the release job.
- Tool-specific `README.md` (usage guide) updated to reference the installed `project-context` command instead of the old `python tools/project_context.py` invocation, matching the new package structure.

### Testing
- `tests/test_project_context.py` updated: `TOOL_PATH` now points to `src/dev_tools/project_context/cli.py` instead of the old flat `project_context.py` path.

## [1.5.0] - 2026-07-23

### Added
- `--report` flag: runs `full`, `--signatures-only`, `--graph` (and `--grep`, if `--grep PATTERN` is also passed) against the same project root in a single command, and prints a comparison table with character counts, `tiktoken` (`cl100k_base`) token counts, percentage reduction vs. the full dump, and the multiplier (e.g. "17.6x smaller"). Replaces the previous manual three-script benchmarking workflow with one reproducible built-in command.

### Fixed
- `Config` dataclass was missing the `report` field despite `--report` being wired into the argument parser, causing `AttributeError: 'Config' object has no attribute 'report'` at runtime.
- `parse_args()` was not passing `args.report` into the `Config` constructor.
- `--report` check was located at the end of `main()`, after the full-dump write path had already executed — this caused an unwanted `project_context.md` to be written to disk before the tool crashed on the two bugs above. `--report` now short-circuits at the top of `main()`, immediately after the root-directory existence check, before `collect_files()` or any write logic runs.

### Changed
- `tiktoken` import moved to module level with a soft dependency check via `importlib.util.find_spec("tiktoken")` — `--report` fails with a clear install message (`pip install tiktoken`) rather than a raw `ImportError` if the package is missing. Core tool functionality (default mode, `--tree-only`, `--signatures-only`, `--grep`, `--graph`) remains dependency-free.

### Benchmarked
- Measured on Kraken portfolio tracker (production codebase) via `--report --grep "PortfolioSummary"`:
  - Full dump: 330,126 chars / 81,325 tokens (baseline)
  - `--signatures-only`: 18,786 chars / 4,630 tokens — 94.3% fewer tokens (17.6x smaller)
  - `--grep "PortfolioSummary"`: 101,550 chars / 24,355 tokens — 70.1% fewer tokens (3.3x smaller)
  - `--graph`: 31,519 chars / 8,250 tokens — 89.9% fewer tokens (9.9x smaller)
- Note: these figures supersede the `1.3.0`/`1.4.0` changelog entries' numbers (73,694 / 4,717 / 8,984 baseline), which were measured on an earlier, smaller snapshot of the same codebase via manual `tiktoken` scripts rather than `--report`.

### Testing
- Added `test_report_prints_comparison_table`, `test_report_includes_grep_row_when_pattern_given`, and `test_report_does_not_write_output_file` to `tests/test_project_context.py` — the last of these directly guards against the premature-write bug fixed above.

## [1.4.0] - 2026-07-23

### Added
- `--graph` flag: OKF-flavored output mode. Splits signature extraction into one markdown file per module, with YAML frontmatter (`depends_on`, `used_by`) and cross-file markdown links reflecting the project's actual import graph. Writes to a directory (default: `project_graph/`) plus an `index.md` linking all modules.

### Benchmarked
- `--graph` measured ~2.8x more tokens than flat `--signatures-only` on a small test project, due to per-file frontmatter overhead. This is a navigability/precision tradeoff, not a token-savings mode — recommended for scoped, iterative exploration of specific modules and their direct dependencies, not as a replacement for `--signatures-only` when the goal is minimizing total context size.

## [1.3.0] - 2026-07-22
### Added
- `--grep` flag: for regex-based relevance filtering of file contents.
- `--signatures-only` flag: using Python's ast module to extract function and class signatures without full implementation bodies.
- Runtime warning printed to stderr when full-dump mode risks LLM context overload (threshold: more than 40 files without a scoping flag).
- `--version` flag.
- Test suite (tests/test_project_context.py) covering exclusion rules, signature extraction, grep filtering, tree-only mode, and the new warning.

### Changed
- Internal Config dataclass extended with signatures_only and grep_pattern fields.
- render_markdown and render_xml updated to support the new signatures-only output branch.

### Rationale
- Benchmarking discussed in https://habr.com/ru/articles/1042880/ found that "read all files" context strategies for LLM agents correlate with degraded output quality and token counts an order of magnitude higher than scoped alternatives (e.g. symbol maps). This release brings an equivalent scoping option (--signatures-only) and a relevance filter (--grep) to sli.py, plus a safeguard warning for unscoped full dumps on larger projects.

## [1.2.0] - 2026-07-21
### Added
- `--changed-only` flag:  Mid-refactor update — only files you just edited, git-diff-aware context updates
- `--clipboard` flag: to copy output into clipboard

## [1.1.0] - 2026-07-20
### Added
- `--tree-only` flag: Architecture-only review (e.g. onboarding a new AI session)
- `--max-chars` flag: Splitting a large context into chunks (auto-splitting)
- `--output context.xml` support: Using XML-like output instead of Markdown

## [1.0.0] - 2026-07-18
### Added
- `project_context.py` — standalone developer CLI tool. Recursively scans a repository and merges its structure and file contents into a single Markdown or XML-like document, optimized for pasting into LLM chat context (ChatGPT, Claude, Gemini). Respects `.gitignore`, filters out virtual envs, caches, and binaries by default, and supports Markdown output.


## [0.9.0] - 2026-07-16
- Initial public release
