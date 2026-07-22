# project_context.py — Usage Guide

CLI utility that turns a Python repository into a single, LLM-ready context document. Designed to be dropped into a `tools/` folder of any Python project and reused across projects without modification.

## Modes and measured token cost

As of v1.0.3.0, this table is backed by a real benchmark run against a production codebase (Kraken portfolio tracker), measured with `tiktoken` (`cl100k_base` encoding) — not estimates.

| Flag | Purpose | Tokens (measured) | Savings vs. full dump |
|---|---|---|---|
| (default, no flags) | Full tree + full file contents | 73,694 | baseline |
| `--signatures-only` | Function/class signatures via AST, no bodies | 4,717 | 93.6% fewer tokens (15.6x smaller) |
| `--grep PATTERN` | Only files whose content matches a regex, full detail kept | 8,984 | 87.8% fewer tokens (8.2x smaller) |
| `--tree-only` | Architecture only, no file contents | not benchmarked, expect <1% of full dump | very low |

Note the difference between `--signatures-only` and `--grep`: signatures-only strips implementation bodies from every file (interface map only), while `--grep` keeps full implementation detail but narrows the file set to only what matches the pattern. Choose based on whether you need breadth (signatures) or depth on a specific area (grep).

Running the default full-dump mode on more than 40 files without any scoping flag prints a warning to stderr, since unscoped dumps have been shown to correlate with degraded LLM output quality and unnecessary token spend.

## Installation

No external dependencies required for core functionality. Optional:

```bash
pip install pyperclip   # only needed for --clipboard
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

Getting a fast interface map without full code — cheapest way to give an LLM architectural awareness of a large codebase (measured 15.6x token reduction):
```bash
python tools/project_context.py --signatures-only --output signatures.md
```

Splitting a large context into chunks under a model's context window:
```bash
python tools/project_context.py --max-chars 50000 --output context.md
```

Using XML-like output instead of Markdown:
```bash
python tools/project_context.py --format xml --output context.xml
```

## Recommended workflow

1. Start a new AI conversation with `--tree-only` so the model understands the architecture first.
2. If deep review is needed, follow up with `--signatures-only` to give the model an interface map at roughly 6% of the token cost of a full dump.
3. During iterative development, use `--changed-only --clipboard` to refresh the model with only what you've actually modified.
4. For focused debugging on one class or feature, use `--grep "ClassName"` — full implementation detail on relevant files only, at roughly 12% of the token cost of a full dump.
5. Reserve the unscoped full-dump mode for small projects or first-time full audits — expect the warning on repositories with 40+ files.

## Benchmarking your own project

Reproduce the comparison above on your own codebase:

```bash
python tools/project_context.py --output full.md
python tools/project_context.py --signatures-only --output sig.md
python tools/project_context.py --grep "YourClassName" --output grep.md
```

Each run prints the character count to stderr automatically. For a token
count closer to what an LLM actually charges, use `tiktoken`:

```bash
pip install tiktoken
python -c "import tiktoken; enc = tiktoken.get_encoding('cl100k_base'); [print(f, len(enc.encode(open(f, encoding='utf-8').read()))) for f in ['full.md','sig.md','grep.md']]"
```

Sample outputs generated for benchmarking (e.g. `full.md`, `sig.md`, `grep.md`, `test_context.md`) are disposable — they are not consumed by the tool, its tests, or CI, and should not be committed to version control. Add them to `.gitignore` if you regenerate them locally.

## Testing

```bash
pip install pytest
pytest tests/test_project_context.py -v
```

## Version history

See `CHANGELOG.md` for the full history of changes.
