# Changelog

## [2.1.3] - 2026-08-19

### Added
- `validate_tutorial_bundle()` in `analysis.py`: structural sanity check run automatically on every `--docs`/`--analysis` bundle before it is
  written or printed. Prints soft warnings to stderr (never fails the run) when the generated bundle exceeds 2000 lines, or is missing the expected `## CANDIDATE ABSTRACTIONS` or `## DOCUMENTED FEATURE INVENTORY` sections.

### Changed
- **`--analysis` renamed to `--docs`.** The old name still works identically (`--analysis` is now a deprecated alias sharing the same underlying option) and will be removed in a future major version. Using `--analysis` now prints a one-line migration warning to stderr.
- **`--analysis-include-baseline` renamed to `--docs-include-baseline`**, for consistency with the `--docs` rename above. `--analysis-include-baseline`
  remains available as a deprecated alias with the same migration warning.
- `extract_registered_cli_flags()` now uses AST-based detection (walks `ast.Call` nodes for `.add_argument(...)` calls with string-literal flag arguments) instead of a regular expression. This eliminates false positives from comments, docstrings, and string fixtures inside test files that merely construct a throwaway `ArgumentParser` to exercise unrelated code -- those were previously misreported as real, undocumented CLI flags in the `--docs` documentation-drift inventory.

### Fixed
- Documentation-drift detection no longer falsely reports flags found only inside test fixtures (e.g. `--verbose` in a test's own `ArgumentParser` setup) as "implemented but undocumented" CLI options.

## [2.1.2] - 2026-08-16

### Fixed
- **`--diagram mermaid` could emit syntactically fragile node labels when a real filename contained parentheses or double quotes** (e.g. `config (copy).py`), since filesystem-derived text was interpolated directly into Mermaid `["..."]`/`(("..."))` shape syntax with no escaping. Adapted the sanitization concept from OpenDeepWiki's `RepairMermaid` function (itself pure deterministic regex, despite its docstring's misleading "uses a large model" description — no LLM call was ever involved on their side either) to `project-context`'s architecture: sanitize at **label-construction time** via a new `_sanitize_mermaid_label_text()` helper, rather than as a post-hoc regex pass over the whole rendered markdown file. A new `_render_node_shape()` helper centralizes all three call sites that build a node shape string (group-detail synthetic nodes, file-detail subgraph nodes, file-detail ungrouped nodes), guaranteeing sanitization can't be accidentally skipped at any one of them. Also removed dead no-op code (`.replace("(", "\\u0028")...`) left over from an earlier draft of the sanitizer.

### Testing
- 3 new tests in `tests/test_diagram.py`: `test_group_node_label_sanitizes_parens_in_filename`, `test_file_detail_node_label_sanitizes_parens_in_filename`, `test_sanitize_mermaid_label_text_strips_quotes_and_parens` — all construct a real file with parentheses in its name and confirm the Mermaid output never contains the unescaped original text.


## [2.1.1] - 2026-08-13
### Added
--diagram-detail {auto, file, group} (default auto, resolving to group for --tree-only and file otherwise): collapses each subgraph (CI, tests, root, and each src/<package> directory) into a single Mermaid node listing member filenames as plain text, instead of one node per file. Cross-group edges (e.g. tests --validates--> src/dev_tools/project_context) are aggregated and de-duplicated by (group, group, label); same-group edges (including all imports edges, which are almost always intra-package) are dropped as self-loops, since they'd add no information on a single collapsed node. Reduces a real 39-node/51-edge --tree-only diagram on this repository to 10 nodes/10 edges — the previous per-file diagram was unreadable on any project past a handful of files. --diagram-detail file keeps the full per-file rendering (subgraph grouping + --diagram-imports) for --signatures-only or explicit deep-dive use.

--diagram-imports {collapsed, all} (default collapsed): in --diagram-detail file mode, hides imports edges whose source and target files live in the same subgraph group, printing a one-line count of how many were hidden and how to see them (--diagram-imports all). This was the first, smaller fix for the same underlying readability problem that --diagram-detail group fully addresses; kept as a lighter-weight option for users who want per-file nodes but not per-file import spaghetti.

Mermaid diagram nodes are now visually grouped into subgraph blocks by directory/role (CI, tests, root, each src/<package>) in --diagram-detail file mode — purely deterministic (path-string grouping, no inference), closing the biggest gap against GitDiagram-style layouts without reproducing GitDiagram's LLM-generated package-collapse labels (which this project has already declined to fake — see the v1.9.6 README note on hallucination risk).

12 new tests in tests/test_diagram.py covering: Config.resolved_diagram_mode()/resolved_diagram_detail() in isolation, the render_module_graph() dispatcher (previously completely untested — see Fixed section below), group-node aggregation and same-group edge suppression, synthetic-node exclusion from grouping, --diagram-imports collapse/all, and click-link path correctness.

### Fixed
--analysis now ingests README.md/CHANGELOG.md as a "Documented Feature Inventory," cross-checks it against actually-registered CLI flags (documentation-vs-code drift detection), and forces the detected CLI entry point into the candidate list even when it scores zero on import-usage ranking. Remaining before this is considered done: re-run the full --analysis → tutorial-generation workflow on this repository and independently re-score Completeness/Actionability against the 2/5 baseline from the prior review.

Config.resolved_diagram_mode() / resolved_diagram_detail() had unreachable dead code. Both methods' second return statement was indented one level too deep (inside the preceding if block instead of after it), so whenever diagram_mode/diagram_detail was left at its default "auto" value, the method fell through with no explicit return and Python implicitly returned None. Comparisons like mode == "text" then silently evaluated False with no exception raised, disabling the module graph entirely in the default case. Caught only by adding direct unit tests against these two methods in isolation — no prior test called them at all.

render_module_graph()'s dispatcher called cfg.resolved_diagram_mode and cfg.resolved_diagram_detail as bare attributes, missing the () needed to actually invoke them as methods. Same failure mode as above: no exception, just a bound-method object compared against a string, always False, module graph silently omitted. This function had zero test coverage before this release despite being the single integration point between Config and both renderers.

render_module_graph_mermaid()'s _group_key() mis-grouped synthetic nodes. Labels like "Distribution / build artifact" contain a / character as part of their wording, not a path separator — _group_key() was splitting on it anyway and wrapping the synthetic node in its own bogus one-file subgraph. Fixed by checking real on-disk path existence ((root / rel).exists()) before attempting any path-based grouping; synthetic nodes (actors, the build-artifact node) now always render standalone, outside every subgraph, in both file and group detail modes.

Generated click links to GitHub were missing the / between the branch name and the file path (e.g. .../blob/mainCHANGELOG.md instead of .../blob/main/CHANGELOG.md), breaking every clickable link in --diagram mermaid output. Root cause: get_git_remote_url()'s returned prefix was missing its trailing /, and the concatenation site had no defensive separator.

### Changed
VERSION bumped to 2.1.1 (module docstring header and VERSION constant) — the actual functional content of this release (the four fixes and two new flags above), verified end-to-end against this repository before tagging.

- v2.1.1 — documentation-generation completeness fixes. --analysis now ingests README.md/CHANGELOG.md as a "Documented Feature Inventory," cross-checks it against actually-registered CLI flags (documentation-vs-code drift detection), and forces the detected CLI entry point into the candidate list even when it scores zero on import-usage ranking. Remaining before this is considered done: re-run the full --analysis → tutorial-generation workflow on this repository and independently re-score Completeness/Actionability against the 2/5 baseline from the prior review.

## [v2.1.0] — 2026-08-12
- v2.1.0 — package split. The single-file cli.py (~2400 lines) has been split into focused modules (config.py, constants.py, utils.py, collectors.py, astutils.py, conventions.py, baseline.py, diagram.py, tree.py, render.py, analysis.py, benchmark.py, cli.py), all living inside the existing src/dev_tools/project_context/ package directory — no new top-level folder, pyproject.toml entry point unchanged. cli.py now supports three equivalent invocation paths (installed console script, python -m dev_tools.project_context, and direct python cli.py for local testing) via a dynamic package re-import mechanism.
- Validated end-to-end against this repository itself: `--version` agrees across all three invocation paths, `--tree-only`/`--signatures-only`/`--diagram mermaid`/`--graph`/`--format xml` all produced well-formed output, `--analysis .` (self-analysis) completed with the expected full-dump-overload warning at 47 files, and the full pytest suite (80 collected tests) passed.

### Added
- `scripts/collect_graph_context.py` — merges a `--graph` output directory into a single markdown file (`index.md` first, then remaining modules alphabetically, each delimited by `--- FILE: <relative path> ---`), for pasting `--graph`'s per-module output into LLM chat UIs that only accept a single attachment. Documented in README under "Merging `--graph` output into one file".

### Clarified
 - `--report --analysis .` benchmarks self-analysis alongside `full`/`tree-only`/`signatures-only`/`graph` in the same table — this already worked per the v2.0.2 `--analysis`-aware `--report` change, but wasn't obvious without passing `--analysis` explicitly. Added to README's benchmarking section and Recommended workflow (item 13) as the standard pre-commit validation command. A default (flagless) self-analysis row is tracked as a follow-up under Roadmap, not included in this release.
- Remaining before this is considered fully done: none outstanding — module-layout test imports, `--report`/`--analysis`/`--graph` end-to-end parity, and the two documentation gaps above are all closed as of this entry.

## [2.0.2] - 2026-08-11

### Added
- **Interactive path prompt for frozen `.exe` builds.** When run with zero CLI arguments in an interactive terminal (e.g. double-clicking the packaged executable), the tool now prompts `Path to analyze [<last saved path>]:` and remembers the answer in `project_context_config.json`, written next to the executable, as the default for next time. Triggered only when all three are true: running as a frozen build, zero `argv`, and `stdin` is a real terminal — any scripted/CI invocation that passes even one flag, or has piped/redirected stdin, is completely unaffected.
- **Marker-file self-tagging replaces name/content-pattern exclusion heuristics.** Every directory this tool creates (`--graph` output, `--analysis` output) now contains a hidden sentinel file, `.project_context_generated`. Any directory containing that sentinel is automatically excluded from all future scans, regardless of its name. This replaces a considered-but-rejected approach of excluding by folder name (`output/`, `dist/`, `_site/`) or by content shape (folder contains only `.md` files) — both of those are heuristics that can misfire on a real source project's own `docs/` folder; a self-written marker has zero false positives and zero false negatives for the tool's own output.
- **`--analysis` output isolation.** `--analysis <path>` no longer writes to the bare `--output` path in the current working directory. It now defaults to `<target_root>/project_context_output/tutorial_context.md`, tagged with the marker above, so re-running `--analysis` against the same target repository never re-ingests its own prior tutorial output as source material. (This closes the gap explicitly flagged as "not yet implemented" in the 2.0.1 CHANGELOG.)
- **`--report` now benchmarks `--analysis` when a target is supplied.** `project-context --report --analysis <path>` adds an `analysis:<repo-name>` row to the comparison table, measuring the deterministic tutorial bundle's token cost the same way `full`/`tree-only`/`signatures-only`/`graph` are measured. Without `--analysis`, no such row appears — there is no default second repository to benchmark, so nothing is faked.

### Fixed
- `VERSION` constant now correctly reads `"2.0.2"` (2.0.1 shipped with the module docstring one version ahead of the actual constant — verified before tagging this release).
- `--report`'s table column width increased to accommodate the new, longer `analysis:<repo-name>` row label without misaligning existing columns.

## [2.0.1] - 2026-08-11

### Fixed
- `VERSION` constant was left at `"2.0.0"` while the module docstring already read `Version: 2.0.1` — bumped to match, so `--version` output and the module header agree.
- `--analysis` previously never called the full-dump overload check that every other mode already had, so a large cloned repository could silently produce a `tutorial_context.md` far beyond a typical chat UI's practical paste limit with no warning at all. `--analysis` now calls `warn_if_full_dump_overload()` exactly like the default full-dump mode.

### Added
- `--analysis <path>` now detects a `LICENSE`/`LICENSE.md`/`LICENSE.txt` file in the **target** project and, if found, prepends an explicit warning to the generated bundle: any tutorial content derived from that project's code is governed by *that project's* license terms, not this tool's MIT license and not PocketFlow's. This is a real, per-target risk (GPL share-alike obligations, proprietary/NDA'd code, etc.) that the tool can partially flag but never fully resolve — the warning exists so a user publishing a generated tutorial doesn't miss the question entirely.

### Known gap (planned for 2.0.2, not yet in this release)
- `--analysis` output still writes directly to the path given by `--output` (or the CLI default), not to a dedicated `<target>/project_context_output/` subfolder. Re-running `--analysis` against the same target repo without moving the prior output elsewhere risks the tool ingesting its own previous tutorial output as if it were source code on the next run. Do not run `--analysis` twice against the same target without manually relocating or deleting the prior output first.
- The frozen `.exe` build does not yet prompt interactively for a target path when run with no arguments and no piped input; it currently requires explicit flags (`--root`, `--analysis`, etc.) every time.

## [2.0.0] - 2026-08-10

### Added — `--analysis` mode: deterministic tutorial-context generation for third-party repositories
- **`--analysis <path>`**: points the tool's existing detection pipeline (PROJECT CONVENTIONS DETECTED, MANDATORY BASELINE FILES, module graph) at a *different*, already-cloned local repository instead of this tool's own `--root`, then appends a new `CANDIDATE ABSTRACTIONS` + `RELATIONSHIPS` + tutorial-writing instruction bundle. Inspired by [PocketFlow-Tutorial-Codebase-Knowledge](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge) (MIT License), which performs the equivalent step via 4 sequential LLM calls inside its own tool. This mode makes **zero LLM calls itself** — the tool only prepares deterministic context; a human still pastes the output into their own LLM chat to do the actual writing. This preserves the project's founding design principle across every mode, including this new one.
- `extract_file_abstractions()` / `select_candidate_abstractions()`: AST-derived candidate abstractions (top-level classes/functions), ranked by real docstring presence + import-usage count — a deterministic substitute for PocketFlow's `IdentifyAbstractions` LLM call.
- `render_tutorial_bundle()`: renders the real import graph (already-built machinery shared with `--diagram`) as the relationship data, plus an attributed instruction block adapted from PocketFlow's own `OrderChapters`/`WriteChapters` prompt structure — explicitly reusing their prompt *shape*, not their code, and crediting the source project by name and license.
- `--max-abstractions N` (default 10) and `--tutorial-language` flags to control the size and language of the `--analysis` bundle.

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
