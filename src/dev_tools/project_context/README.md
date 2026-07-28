# project-context — Usage Guide

CLI utility that turns a Python repository into a single, LLM-ready context document. Part of the [dev-tools-collection](https://github.com/patan4ik/dev-tools-collection) toolbox — installable as a standalone command or usable as a frozen binary.

## Modes and measured token cost

As of v1.6.0, this table is generated directly by the tool's built-in `--report` command, run against this repository itself, measured with `tiktoken` (`cl100k_base` encoding).

| Mode | Purpose | Chars | Tokens | Reduction vs. full | Smaller |
|---|---|---|---|---|---|
| `--report` full dump | Full tree + full file contents | 53,941 | 13,079 | baseline | 1.0x |
| `--signatures-only` | Function/class signatures via AST, no bodies | 2,184 | 568 | 95.7% fewer | 23.0x |
| `--graph` | OKF-flavored per-module files with dependency links | 3,027 | 812 | 93.8% fewer | 16.1x |
| `--tree-only` | Architecture only, no file contents | not in `--report` | expect <1% of full dump | very low | — |

Note on `--graph`: it costs more tokens than `--signatures-only`, because each module carries its own YAML frontmatter and dependency links. The tradeoff isn't token savings — it's navigability. Use it when you want to open one module and immediately see its exact dependencies without loading the entire signature map at once.

Note on `--grep`: results vary heavily depending on how common the search pattern is in the codebase. A narrow class name matches few files (high reduction); a common term matches many files (lower reduction, as seen here: 70.1% vs. an earlier run's 87.8% on the same project with a different pattern set).

Running the default full-dump mode on more than 40 files without any scoping flag prints a warning to stderr, since unscoped dumps have been shown to correlate with degraded LLM output quality and unnecessary token spend.

## Installation

Install the whole collection in editable mode from the repo root:

```bash
pip install -e .
```

Optional extras:

```bash
pip install pyperclip   # only needed for --clipboard
pip install -e ".[report]"   # installs tiktoken, needed for --report
```

Once installed, the tool is available as the `project-context` command anywhere in your shell — no need to reference the file path.

## Examples

Starting a new LLM chat with full context:
```bash
project-context --output context.md
```

Mid-refactor update — only files you just edited:
```bash
project-context --changed-only --clipboard
```

Architecture-only review (e.g. onboarding a new AI session):
```bash
project-context --tree-only
```

Reviewing only logic related to a specific class or feature, with full detail:
```bash
project-context --grep "PortfolioSummary" --output portfolio_context.md
```

Getting a fast interface map without full code — cheapest way to give an LLM architectural awareness of a large codebase (measured 17.6x token reduction):
```bash
project-context --signatures-only --output signatures.md
```

OKF-flavored dependency graph — one markdown file per module with explicit import links, useful for scoped, iterative exploration:
```bash
project-context --graph --output project_graph
```

Splitting a large context into chunks under a model's context window:
```bash
project-context --max-chars 50000 --output context.md
```

Using XML-like output instead of Markdown:
```bash
project-context --format xml --output context.xml
```

Benchmarking all modes at once (requires `tiktoken`):
```bash
project-context --report --grep "YourClassName"
```

Disabling automatic convention detection (e.g. for a clean baseline comparison):
```bash
project-context --no-conventions --output context.md
```

Disabling the mandatory baseline files bundle and architecture plan gate entirely:
```bash
project-context --no-baseline --output context.md
```

Keeping the baseline files (dependency manifest, CI config, reference test) but dropping only the Step 1/Step 2 planning instructions:
```bash
project-context --no-plan-gate --output context.md
```

## Recommended workflow

1. Start a new AI conversation with `--tree-only` so the model understands the architecture first.
2. If deep review is needed, follow up with `--signatures-only` to give the model an interface map at roughly 6% of the token cost of a full dump.
3. During iterative development, use `--changed-only --clipboard` to refresh the model with only what you've actually modified.
4. For focused debugging on one class or feature, use `--grep "ClassName"` — full implementation detail on relevant files only, at a token cost that depends heavily on how common the pattern is in your codebase.
5. For scoped, navigable exploration of one module and its dependencies, use `--graph` — it costs more tokens than `--signatures-only`, but structures the output as linked per-module files instead of one flat block.
6. Reserve the unscoped full-dump mode for small projects or first-time full audits — expect the warning on repositories with 40+ files.
7. Leave `PROJECT CONVENTIONS DETECTED` enabled by default for any code-generation task — it costs a small, fixed number of tokens per run and is the single change most likely to prevent an LLM from silently skipping your test suite, lint config, or dependency file.
8. Leave `MANDATORY BASELINE FILES` and the `ARCHITECTURE PLAN GATE` enabled for any task that adds real code — the forced pre-commitment plan is the single change most likely to catch a missing test file or a silently-skipped dependency update before the model finishes responding. Use `--no-plan-gate` alone if you want the baseline facts without the two-phase planning overhead.

## PROJECT CONVENTIONS DETECTED (new in v1.7.0)

Earlier versions of this tool dumped facts about the repository — file trees, signatures, dependency graphs — and left it to the LLM to infer what those facts implied. Real end-to-end testing showed this doesn't work reliably: models consistently skipped unstated professional norms (e.g. "write a test for your new module by analogy") even when the evidence for that norm was sitting in plain text in the context, in every mode, including full dumps that literally contained the existing test file.

As of v1.7.0, `project-context` closes that gap. It detects five categories of project convention and renders them as an explicit, imperative section — **`PROJECT CONVENTIONS DETECTED`** — inserted at the top of every output mode, including `--tree-only` and `--graph`, so there is no lightweight mode where a convention can be silently dropped.

The five categories detected:

1. **Test coverage convention** — scans for `<module>.py` ↔ `tests/test_<module>.py` pairs, explicitly lists which modules already have tests and which don't, and instructs the model: if you generate a new tool, generate a matching test file too, even if the prompt never mentions testing.
2. **Lint / format / type / security gate** — parses `pyproject.toml` for `[tool.black]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.bandit]`, `[tool.pytest]` sections, and checks for `.pre-commit-config.yaml`. If found, the model is told to write code as if it will actually be run through all of them — type hints on every function, no Bandit antipatterns like `eval` or unsanitized `subprocess` calls, etc.
3. **CI gate requirements** — reads `.github/workflows/*.yml` and detects, by regex, which checks (`pytest`, `mypy`, `bandit`, `coverage`) are actually *executed* in CI — not just configured somewhere, but treated as a merge-blocking gate.
4. **Dependency management** — identifies where dependencies are declared (`pyproject.toml` / `requirements.txt`) and requires that any new third-party import used by generated code be explicitly called out as a needed addition to that file, rather than silently assumed to be installed.
5. **Code style conventions** — samples up to 50 `.py` files via AST, computes the docstring coverage ratio across sampled functions, and determines the dominant naming convention (`snake_case` vs. mixed/camelCase), so new code doesn't stylistically stand out.

Disable this section — for example, to run a clean A/B baseline against an older prompt style — with:

```bash
project-context --no-conventions --output context.md
```

## MANDATORY BASELINE FILES + ARCHITECTURE PLAN GATE (new in v1.8.0, fixed in v1.8.1)

`PROJECT CONVENTIONS DETECTED` tells the model *what the rules are*. As of v1.8.0, `project-context` goes one step further and forces the model to *commit to a plan* before writing any code, and to *self-audit* against that plan afterward.

**MANDATORY BASELINE FILES** embeds, verbatim, the project's real contract files — dependency manifest, CI config, pre-commit config — detected by role/content signature rather than by a hardcoded filename, plus one real reference test file selected using stable Python/pytest/unittest rules (AST `assert`, `unittest.TestCase`, pytest fixtures/imports; `testpaths` from `pyproject.toml`/`pytest.ini`/`tox.ini`/`setup.cfg` if declared). This bundle is attached in every output mode, including `--tree-only`.

**ARCHITECTURE PLAN GATE** is a Plan-and-Solve style two-phase instruction block:
- **Step 1 — Architecture Plan (before code):** the model must state the target module path, the exact dependency-manifest diff, the test file it will add (modeled on the reference test file), which CI/lint/type/ security gates apply, and any version/security constraints.
- **Step 2 — Self-Validation Checklist (after code):** the model must re-read its own Step 1 plan and mark each item PASS/FAIL with a concrete artifact, so an incomplete response can't slip through unnoticed.

Disable both the bundle and the gate:
```bash
project-context --no-baseline --output context.md
```

Keep the baseline bundle but disable only the plan gate:
```bash
project-context --no-plan-gate --output context.md
```

**v1.8.1 fixes:** the `.txt` dependency-manifest detector previously flagged any plain-text file as a manifest on a bare word match; it now requires a real PEP 508 version specifier (with an unpinned-`requirements*.txt` name-based fallback). `--no-plan-gate` was a non-functional stub in v1.8.0 — it now actually suppresses the Step 1/Step 2 instructions in every output mode (`--format md`, `--format xml`, `--graph`) while keeping the baseline files intact.


## Benchmarking your own project

The tool has a built-in benchmark command — you no longer need to run separate scripts:

```bash
pip install tiktoken
project-context --report
project-context --report --grep "YourClassName"
```

This runs full, `--signatures-only`, `--graph` (and `--grep`, if provided) against the same root, measures both character and `cl100k_base` token counts for each, and prints a single comparison table.

Manual, single-mode runs are still available if you want the actual output file rather than just the metrics:

```bash
project-context --version
project-context --output full.md
project-context --signatures-only --output sig.md
project-context --grep "YourClassName" --output grep.md
project-context --graph --output project_graph
```

Sample outputs generated for benchmarking (e.g. `full.md`, `sig.md`, `grep.md`, `test_context.md`, `project_graph/`) are disposable — they are not consumed by the tool, its tests, or CI, and should not be committed to version control. Add them to `.gitignore` if you regenerate them locally.

## Running from source (without installing)

If you're developing the tool itself and want to run it directly from source without reinstalling:

```bash
python src/dev_tools/project_context/cli.py --tree-only
```

## Testing

```bash
pip install pytest
pytest tests/test_project_context.py -v
```

## Version history

See the root [`CHANGELOG.md`](../../../CHANGELOG.md) for the full history of changes.
