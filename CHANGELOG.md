# Changelog

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
- Note: these figures supersede the `1.0.3.0`/`1.0.4.0` changelog entries' numbers (73,694 / 4,717 / 8,984 baseline), which were measured on an earlier, smaller snapshot of the same codebase via manual `tiktoken` scripts rather than `--report`.

### Testing
- Added `test_report_prints_comparison_table`, `test_report_includes_grep_row_when_pattern_given`, and `test_report_does_not_write_output_file` to `tests/test_project_context.py` — the last of these directly guards against the premature-write bug fixed above.

## [1.4.0] - 2026-07-23

### Added
- `--graph` flag: OKF-flavored output mode. Splits signature extraction into one markdown file per module, with YAML frontmatter (`depends_on`, `used_by`) and cross-file markdown links reflecting the project's actual import graph. Writes to a directory (default: `project_graph/`) plus an `index.md` linking all modules.

### Benchmarked
- `--graph` measured ~2.8x more tokens than flat `--signatures-only` on a small test project, due to per-file frontmatter overhead. This is a navigability/precision tradeoff, not a token-savings mode — recommended for scoped, iterative exploration of specific modules and their direct dependencies, not as a replacement for `--signatures-only` when the goal is minimizing total context size.

## [1.3.0] - 2026-07-22
### Added
- --grep PATTERN flag for regex-based relevance filtering of file contents.
- --signatures-only flag using Python's ast module to extract function and class signatures without full implementation bodies.
- Runtime warning printed to stderr when full-dump mode risks LLM context overload (threshold: more than 40 files without a scoping flag).
- --version flag.
- Test suite (tests/test_project_context.py) covering exclusion rules, signature extraction, grep filtering, tree-only mode, and the new warning.

### Changed
- Internal Config dataclass extended with signatures_only and grep_pattern fields.
- render_markdown and render_xml updated to support the new signatures-only output branch.

### Rationale
- Benchmarking discussed in https://habr.com/ru/articles/1042880/ found that "read all files" context strategies for LLM agents correlate with degraded output quality and token counts an order of magnitude higher than scoped alternatives (e.g. symbol maps). This release brings an equivalent scoping option (--signatures-only) and a relevance filter (--grep) to project_context.py, plus a safeguard warning for unscoped full dumps on larger projects.

## [1.0.0] - 2026-07-21

### Added
- `tools/project_context.py` — standalone developer tool, unrelated to the trading/reporting functionality of this project. Recursively scans the repository and merges its structure and file contents into a single Markdown or XML-like document, optimized for pasting into LLM chat context (ChatGPT, Claude, Gemini). Respects `.gitignore`, filters out virtual envs, caches, and binaries by default, and supports `--tree-only`, `--changed-only` (git-diff-aware context updates), `--max-chars`
  (auto-splitting), and `--clipboard` output.

## [0.9.0] - 2026-07-20
- Initial public release
