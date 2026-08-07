# Changelog

## [1.9.6] - 2026-08-07
## Fixed
Removed a byte-for-byte duplicate "All top-level signatures" block from the MANDATORY BASELINE FILES reference summary. It repeated ## SIGNATURES in --signatures-only mode and added no value elsewhere, and was the direct cause of --tree-only and --signatures-only output sizes converging.

- `--tree-only` now renders a strictly minimal reference summary (module purpose + one representative function only, no import list), restoring the intended --tree-only < --signatures-only < full-dump size ordering.

Fixed a malformed section heading in the reference summary.

## [1.9.5] - 2026-08-05
## Changed
Replaced the raw, character-truncated reference source/test file dump (which routinely cut off mid-function or mid-docstring, wasting tokens on an unusable fragment) with a bounded KISS summary: module purpose (docstring's first paragraph), full import list, full signature list, and exactly one complete, never-truncated representative function.

## Fixed
Non-Python files (CHANGELOG.md, LICENSE.md, pyvenv.cfg, etc.) were being labeled [source] and tinted the same Mermaid color as real Python modules in the MODULE GRAPH. Added dedicated doc and other-config/other roles with their own neutral/mint styling.

## [1.9.4] - 2026-08-03
## Added
Closed the GitDiagram-parity gap in --diagram: the module graph previously detected only imports and registers entry point edges. Added deterministic (AST/regex/local-git-derived, no LLM) detection for:

- belongs to — nested __init__.py linked to its parent package's __init__.py.
- documents — README.md linked to the nearest package __init__.py.
- packages — pyproject.toml linked to the root package __init__.py.
- validates — each test file linked individually to the source files its content actually references.

runs / builds from / produces / publishes build — CI workflows linked to the tests they run and, for build/packaging workflows (pyinstaller, python -m build, twine, etc.), to pyproject.toml and a synthetic "Distribution / build artifact" node.

invokes / imports — two synthetic actor nodes ("User / automation invoker", "External Python callers"), added only when a real target (a detected entry point, or a root package) exists.

## Fixed
Mermaid click links were being generated for synthetic nodes (actors, the "Distribution / build artifact" node) and pointed at nonexistent GitHub paths. Now restricted to real, on-disk repository files only.

## [1.9.3] - 2026-08-02
## Fixed
- Config.diagram_mode was referenced but never declared on the dataclass, causing an immediate AttributeError on every run.
- render_module_graph_text() / render_module_graph_mermaid() were called but never defined, causing a NameError.
- detect_entry_points() was left truncated mid-function (a syntax error) in an earlier hand-edited draft; completed the full [project.scripts] (PEP 621) parser.

Entry-point detection now strips a leading src/ path segment before building its dotted-module lookup, fixing silent zero-entry-point detection on any standard src-layout project.

The ## MODULE GRAPH block is now wired into --format xml and --graph (index.md) as well as default markdown, instead of only one of the three output paths.

## Added
- **`--diagram {auto,none,text,mermaid} flag`** (default auto, resolving to text for --tree-only/--signatures-only and none otherwise), rendering the project's real import graph, detected entry points, and file roles as either plain text arrows or a styled Mermaid flowchart TD block — fully deterministic (AST + regex + local git metadata), no LLM call, no network access.

## [1.9.2] - 2026-08-01 (draft, superseded by 1.9.3 fixes)
## Added (incomplete in this revision — see 1.9.3)
Initial attempt at GitDiagram-inspired module graph rendering for --tree-only: detect_entry_points(), render_module_graph_text()/_mermaid(). Shipped with several defects (missing Config field, undefined render functions, truncated entry-point parser) that were all fixed in 1.9.3.

## [1.9.1] - 2026-07-30
## Added
- **`--baseline-mode {auto,full,summary,off} (default auto)`**: resolves to summary for --tree-only/--signatures-only and full otherwise. summary mode lists each MANDATORY BASELINE FILES contract file's role, path, and byte size instead of embedding it verbatim — the direct fix for --tree-only and --signatures-only output sizes having nearly converged in real-world use.

- `--signatures-only` and `--graph` now also extract each module's top-level imports, module-level constants, and dataclass field definitions (still zero function/method bodies) — the most commonly requested missing piece of signature-only review.

Real-time Written: <file> (<N> characters, ~<M> tokens, <method>) reporting on every run (using tiktoken when installed, a documented chars/4 fallback otherwise), replacing the previous character-only confirmation message.

## Fixed
A source module is now considered "covered" by a test if the test's content references it (import or literal path string), not just if the test filename happens to contain the source's stem as a substring — fixed a false "uncovered" flag on modules referenced only via a computed path (e.g. TOOL_PATH = Path(...) / "cli.py").

Real per-project output (PROJECT TREE, then SIGNATURES where applicable) now renders immediately after the file count, before the PROJECT CONVENTIONS/MANDATORY BASELINE/ARCHITECTURE PLAN GATE instructional sections, not after them.

## [1.9.0] - 2026-07-29

### Added — Mandatory Reference Source Module, Integration Scope, Senior-Developer Mandate
- **Mandatory reference source module**: one real, verbatim, non-test source file is now selected (preferring a module with `def main(` + `if __name__ == "__main__":`) and embedded in EVERY output mode, including `--tree-only` and `--signatures-only`. Root-cause fix for the blind-judge finding that tree/signature modes scored Actionability 1/5 — models could see *where* files live or *what* is callable, but never *how* the project actually writes error handling, CLI parsing, or docstrings. Bounded by `REFERENCE_SOURCE_MAX_CHARS` (6,000 chars) to preserve token savings in scoped modes.
- **`--integration-scope {standalone,integrated}` flag** (default: `standalone`): removes guessing around whether a new module should be wired into existing entry points/CLI registries. `standalone` instructs the model not to touch existing wiring unless asked; `integrated` requires an explicit diff showing registration into the existing pattern.
- **SENIOR-DEVELOPER MANDATE**: new instruction block preceding the Architecture Plan Gate that explicitly forbids the "zero-code refusal" failure mode observed in blind judging (a model responding with only clarifying questions and no implementation). "State missing context, do not guess" is now scoped strictly to details that would silently corrupt behavior — never to withholding an entire implementation.
- **Documentation convention detection**: `detect_docs_convention()` finds README.md/CHANGELOG.md presence and observed changelog header format (e.g. Keep a Changelog style), added as category 6 of `PROJECT CONVENTIONS DETECTED`. Closes the "suggest updates for docs" gap.
- **Dependency version-pinning style detection**: reports whether the manifest uses exact (`==`) or range (`>=`, `~=`) pins, so new dependencies added by the model follow the same discipline instead of being guessed.
- **Definition of Done** self-validation gate expanded from 5 to 6 items (added "Documentation updates"), plus a mandatory self-reported `COMPLETION: N%` line for direct KPI tracking without manual judge estimation.
- New `--report` row for `tree-only`, previously missing from the benchmark table.

### Changed
- Plan gate instructions now direct the model to reference baseline files by their short relative path, not by re-pasting any upload/storage URL — reduces citation noise that was hurting Coherence scores in blind evaluation.
- `render_xml` now embeds the full rendered `PROJECT CONVENTIONS DETECTED` and `MANDATORY BASELINE FILES` text as CDATA blocks (`<conventions_detected>`, `<mandatory_baseline>`, `<architecture_plan_gate>`), fixing a regression where `--format xml` silently dropped this payload and only emitted boolean flags.
- "No baseline contract files or test exemplar were detected" message updated to "No baseline contract files or code exemplars were detected", reflecting the new reference-source-module exemplar alongside the reference test file.

### Known issue (tracked for v1.8.1)
- `detect_dependency_files()` (used by the `PROJECT CONVENTIONS DETECTED` section) uses a looser `.txt` heuristic than `_is_text_dependency_manifest()` (used by the baseline bundle classifier), so a plain-word `.txt` file can be misclassified as a dependency manifest in the conventions section while correctly excluded from the baseline bundle. Fix planned: reuse `_is_text_dependency_manifest()` in both code paths.

## [1.8.1] - 2026-07-28

### Fixed — Baseline detection fixes
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
