# project_context.py — Usage Guide

CLI utility that turns a Python repository into a single, LLM-ready context document. Designed to be dropped into a `tools/` folder of any Python project and reused across projects without modification.

## Modes and measured token cost

As of v1.0.5.0, this table is generated directly by the tool's built-in `--report` command against a production codebase (Kraken portfolio tracker), measured with `tiktoken` (`cl100k_base` encoding) — not estimates, not manual scripts.

| Mode | Purpose | Chars | Tokens | Reduction vs. full | Smaller |
|---|---|---|---|---|---|
| `--report` full dump | Full tree + full file contents | 330,126 | 81,325 | baseline | 1.0x |
| `--signatures-only` | Function/class signatures via AST, no bodies | 18,786 | 4,630 | 94.3% fewer | 17.6x |
| `--grep "PortfolioSummary"` | Only files matching a regex, full detail kept | 101,550 | 24,355 | 70.1% fewer | 3.3x |
| `--graph` | OKF-flavored per-module files with dependency links | 31,519 | 8,250 | 89.9% fewer | 9.9x |
| `--tree-only` | Architecture only, no file contents | not in `--report` | expect <1% of full dump | very low | — |

Note on `--graph`: it costs more tokens than `--signatures-only`, because each module carries its own YAML frontmatter and dependency links. The tradeoff isn't token savings — it's navigability. Use it when you want to open one module and immediately see its exact dependencies without loading the entire signature map at once.

Note on `--grep`: results vary heavily depending on how common the search pattern is in the codebase. A narrow class name matches few files (high reduction); a common term matches many files (lower reduction, as seen here: 70.1% vs. an earlier run's 87.8% on the same project with a different pattern set).

Running the default full-dump mode on more than 40 files without any scoping flag prints a warning to stderr, since unscoped dumps have been shown to correlate with degraded LLM output quality and unnecessary token spend.

## Installation

No external dependencies required for core functionality. Optional:

```bash
pip install pyperclip   # only needed for --clipboard
pip install tiktoken    # only needed for --report
```

## Examples

Starting a new LLM chat with full context:
```bash
python tools/project_context.py --output context.md
```

Mid-refactor update — only files you just edited:
```bash
python tools/project_context.py --changed-only --clipboard
```

Architecture-only review (e.g. onboarding a new AI session):
```bash
python tools/project_context.py --tree-only
```

Reviewing only logic related to a specific class or feature, with full detail:
```bash
python tools/project_context.py --grep "PortfolioSummary" --output portfolio_context.md
```

Getting a fast interface map without full code — cheapest way to give an LLM architectural awareness of a large codebase (measured 17.6x token reduction):
```bash
python tools/project_context.py --signatures-only --output signatures.md
```

OKF-flavored dependency graph — one markdown file per module with explicit import links, useful for scoped, iterative exploration:
```bash
python tools/project_context.py --graph --output project_graph
```

Splitting a large context into chunks under a model's context window:
```bash
python tools/project_context.py --max-chars 50000 --output context.md
```

Using XML-like output instead of Markdown:
```bash
python tools/project_context.py --format xml --output context.xml
```

Benchmarking all modes at once (requires `tiktoken`):
```bash
python tools/project_context.py --report --grep "YourClassName"
```

## Recommended workflow

1. Start a new AI conversation with `--tree-only` so the model understands the architecture first.
2. If deep review is needed, follow up with `--signatures-only` to give the model an interface map at roughly 6% of the token cost of a full dump.
3. During iterative development, use `--changed-only --clipboard` to refresh the model with only what you've actually modified.
4. For focused debugging on one class or feature, use `--grep "ClassName"` — full implementation detail on relevant files only, at a token cost that depends heavily on how common the pattern is in your codebase.
5. For scoped, navigable exploration of one module and its dependencies, use `--graph` — it costs more tokens than `--signatures-only`, but structures the output as linked per-module files instead of one flat block.
6. Reserve the unscoped full-dump mode for small projects or first-time full audits — expect the warning on repositories with 40+ files.

## Benchmarking your own project

The tool has a built-in benchmark command — you no longer need to run separate scripts:

```bash
pip install tiktoken
python tools/project_context.py --report
python tools/project_context.py --report --grep "YourClassName"
```

This runs full, `--signatures-only`, `--graph` (and `--grep`, if provided) against the same root, measures both character and `cl100k_base` token counts for each, and prints a single comparison table.

Manual, single-mode runs are still available if you want the actual output file rather than just the metrics:

```bash
python tools/project_context.py --output full.md
python tools/project_context.py --signatures-only --output sig.md
python tools/project_context.py --grep "YourClassName" --output grep.md
python tools/project_context.py --graph --output project_graph
```

Sample outputs generated for benchmarking (e.g. `full.md`, `sig.md`, `grep.md`, `test_context.md`, `project_graph/`) are disposable — they are not consumed by the tool, its tests, or CI, and should not be committed to version control. Add them to `.gitignore` if you regenerate them locally.

## Testing

```bash
pip install pytest
pytest tests/test_project_context.py -v
```

## Version history

See `CHANGELOG.md` for the full history of changes.
