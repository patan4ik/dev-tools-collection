# Changelog

## [1.8.1] - 2026-07-28

### Fixed
- **`--no-plan-gate` was a non-functional stub.** In v1.8.0 the flag was parsed and stored on `Config` but never consulted by any renderer, so Step 1 (`ARCHITECTURE PLAN`) and Step 2 (`SELF-VALIDATION CHECKLIST`) still appeared in the output even with `--no-plan-gate` set. `render_markdown()`, `render_xml()`, and `render_graph()` now all guard the call to `render_preflight_plan_gate()` with `if not cfg.no_plan_gate:`, while still rendering `MANDATORY BASELINE FILES` unconditionally when baseline detection is enabled.
- **False-positive `.txt` dependency-manifest detection.** The `DEPENDENCY_MANIFEST_SIGNATURES[".txt"]` regex matched any bare word on its own line (e.g. a `readme.txt` containing only `"hello"`), incorrectly classifying arbitrary prose files as dependency manifests. The pattern now requires a real PEP 508 version specifier token (`==`, `>=`, `<=`, `~=`, `!=`, `>`, `<` followed by a digit). An unpinned-requirement fallback (`UNPINNED_REQUIREMENTS_NAME_HINT`) recognizes `requirements*.txt`-style filenames containing only unpinned package names, per pip's own documented naming convention, used only when the content signature does not already match.
- **`detect_dependency_files()` duplicated classification logic.** It previously ran its own copy of the manifest-signature check instead of calling `classify_file_role()`, risking drift between the `PROJECT CONVENTIONS DETECTED` section and the `MANDATORY BASELINE FILES` bundle. It now delegates to `classify_file_role()` so both stay consistent.

### Changed
- Version bumped to `1.8.1` (module docstring header and `VERSION` constant).

## [1.8.0] - 2026-07-27

### Added
- **Role-based baseline file classification** (`classify_file_role`, `collect_mandatory_baseline`): replaces all hardcoded filename assumptions (`pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows`) with detection by content signature and by the owning tool's own fixed path convention. A dependency manifest is now recognized by its structural shape (`DEPENDENCY_MANIFEST_SIGNATURES`: `[project]`/`[tool.poetry]` for TOML, pinned-package lines for `.txt`, `[options]` for `.cfg`, a `dependencies:` key for YAML), a CI config by the CI provider's own path convention (`CI_CONFIG_PATH_PATTERNS`: GitHub Actions, GitLab CI, Azure Pipelines, Jenkins, CircleCI), and a pre-commit config by pytest's/pre-commit's own `repos:` schema. This means the tool now works correctly on any project regardless of what it happens to name its config files.
- **AST/language-based test detection** (`is_test_module`, `_has_testcase_subclass`, `_has_pytest_decorator`, `_has_bare_assert`): a Python file is now classified as a test module using only stable, version-pinned facts of the Python language and its standard testing ecosystem — `import unittest`/`import pytest`, a class inheriting from `unittest.TestCase`, a function decorated with `pytest.fixture`/`pytest.mark.*`, or the presence of a bare `assert` statement. This completely replaces the previous `test_<name>.py` filename/`tests/` folder heuristic, so projects using `*_test.py`, co-located tests, or any other naming scheme are now detected correctly.
- **pytest config-driven test root discovery** (`find_pytest_test_roots`): reads the `testpaths` key from whichever file pytest itself would read (`pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`) — this is pytest's own documented schema, not a project-specific convention — instead of assuming a folder is called `tests/`.
- **`select_reference_test_file()`**: picks one real, representative test file (by AST-detected role, median length among candidates) and embeds it verbatim as a style/fixture/assertion-pattern exemplar, capped at `REFERENCE_TEST_MAX_CHARS` (4000 chars) to keep it cheap in every mode.
- **`MANDATORY BASELINE FILES` section** (`render_baseline_section`): injects the verbatim content of the detected dependency manifest, CI config, pre-commit config, and reference test file into every output mode, including `--tree-only`, so the executor model reads the project's actual binding contracts instead of a paraphrase of them.
- **Architecture Plan Gate** (`render_preflight_plan_gate`), a Plan-and-Solve style two-phase instruction block:
  - **Step 1 (pre-flight plan)**: forces the executor to commit, in writing, to target module path, dependency-manifest diff, test file plan, applicable CI/lint/type/security gates, and version/security constraints — referencing the real baseline files — *before* generating any code.
  - **Step 2 (self-validation checklist)**: forces the executor to re-read its own Step 1 plan after writing code and mark each item PASS/FAIL with a concrete artifact, so an incomplete implementation cannot be declared "done" silently.
  - This exploits models' recency bias: a plan the model just wrote is harder to silently drop mid-generation than an instruction stated once, far away, near the top of a long context.
- `--no-baseline` flag: disables both the `MANDATORY BASELINE FILES` bundle and the Architecture Plan Gate, for output that matches pre-1.8.0 behavior.
- `--no-plan-gate` flag: keeps the verbatim baseline files but disables only the Step 1/Step 2 planning instructions, for cases where the caller wants ground-truth file content without the imperative planning wrapper.

### Changed
- `detect_test_pairs()` no longer relies on filename prefixes (`test_`) or folder names (`tests/`) to identify test files or link them to source modules; it now uses `is_test_module()` and reports `test_roots` sourced from pytest's own configuration instead of a hardcoded `pattern` string.
- `detect_lint_config()` and `detect_dependency_files()` no longer look for a file literally named `pyproject.toml`; they scan all collected files and apply the same role/content-signature detection used by `collect_mandatory_baseline()`.
- `detect_ci_requirements()` now recognizes CI configuration by `CI_CONFIG_PATH_PATTERNS` (multi-provider) instead of a single hardcoded `.github/workflows` substring check.
- `render`, `render_markdown`, `render_xml`, `render_graph`, and `run_benchmark` all take new optional `baseline`/`reference_test` parameters so the mandatory baseline bundle and plan gate are threaded through every output mode consistently.
- All source comments and user-facing CLI strings translated from Russian to English for consistent LLM-facing terminology and reduced token overhead when this file itself is fed into a model's context.

### Rationale
- Hardcoding contract filenames (as in `MANDATORY_BASELINE_FILES = {"pyproject.toml", "setup.cfg", ...}`) silently breaks on any project that names its manifest, CI config, or test files differently — which is the norm, not the exception, across real-world Python projects. Classifying files by the stable, version-pinned rules of the Python language and its tooling ecosystem (AST node types, `unittest`/`pytest` public API, each CI provider's own fixed path convention) generalizes correctly without per-project configuration.
- The two-phase Plan-and-Solve structure (commit to a plan, then validate against that same plan) is a documented prompting pattern shown to reduce missing-step errors compared to asking a model to "just do it correctly," because it forces an explicit checkpoint before code generation and a second explicit checkpoint after, using the model's own stated plan as the object being validated.

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
