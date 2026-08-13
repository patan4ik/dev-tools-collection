# project-context — Usage Guide

CLI utility that turns a Python repository into a single, LLM-ready context document. Part of the [dev-tools-collection](https://github.com/patan4ik/dev-tools-collection) toolbox — installable as a standalone command or usable as a frozen binary.

## Modes and measured token cost

As of v1.6.0, this table is generated directly by the tool's built-in `--report` command, run against this repository itself, measured with `tiktoken` (`cl100k_base` encoding).

| Mode | Purpose | Chars | Tokens | Reduction vs. full | Smaller |
|---|---|---|---|---|---|
| `--report` full dump | Full tree + full file contents | 205,609 | 48,790 | baseline | 1.0x |
| `--tree-only` | Architecture only + minimal KISS code exemplar + module graph | 14,229 | 3,397 | 93.0% fewer | 14.4x |
| `--signatures-only` | Function/class signatures via AST, no bodies, + module graph | 19,841 | 4,631 | 90.5% fewer | 10.5x |
| `--graph` | OKF-flavored per-module files with dependency links | 19,251 | 4,472 | 90.8% fewer | 10.9x |

**Note (v1.9.0/1.9.6):** every mode embeds a bounded KISS reference summary (module purpose + one complete, never-truncated representative function), not the raw file — this fixed an earlier issue where truncation cut a reference file off mid-function, wasting tokens on an unusable fragment. `--tree-only` uses a strictly minimal version of this summary (no import list) so it stays smaller than `--signatures-only`, which additionally lists every collected file's full signatures. Disable the whole baseline bundle with `--no-baseline` if you want the old, code-free lightweight behavior.

Note on `--graph`: it costs more tokens than `--signatures-only`, because each module carries its own YAML frontmatter and dependency links. The tradeoff isn't token savings — it's navigability. Use it when you want to open one module and immediately see its exact dependencies without loading the entire signature map at once. **`--graph` writes a directory, not a single file** — if you need to paste its output into an LLM chat UI that only accepts one attachment, merge it first with `scripts/collect_graph_context.py` (see "Merging `--graph` output into one file" below).

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

Merging `--graph` output into a single markdown file for LLM chat UIs that only accept one attachment:
```bash
project-context --graph --output project_graph
python scripts/collect_graph_context.py --input project_graph --output graph_context.md
```
`graph_context.md` concatenates every per-module file, `index.md` first (as the entry-point/map), in a `--- FILE: <relative path> ---` delimited format the LLM can still navigate by module.

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

Benchmarking the deterministic tutorial-context bundle alongside every other mode:
```bash
project-context --report --analysis /path/to/cloned/other-repo
```
Adds an `analysis:<repo-name>` row to the comparison table. This row only appears when `--analysis` is explicitly passed — there is no default second repository to benchmark. To benchmark self-analysis of the current project alongside `full`/`tree-only`/`signatures-only`/`graph`, pass the current directory explicitly:
```bash
project-context --report --analysis .
```
This adds an `analysis:<current-dir-name>` row measuring the tutorial-context bundle's cost when the tool analyzes itself — useful as a stable pre-commit sanity check, since it exercises the same code path as analyzing a third-party repository without needing one cloned locally.

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

Declaring that a new module must be wired into existing entry points/CLI registries, not left standalone (new in v1.9.0):
```bash
project-context --integration-scope integrated --output context.md
```

Adding a deterministic, GitDiagram-style architecture diagram to `--tree-only` or `--signatures-only` output — plain text arrows by default, or a Mermaid flowchart for GitHub/Markdown rendering:
```bash
project-context --tree-only --diagram text --output tree.md
project-context --tree-only --diagram mermaid --output tree.md
```

Generating a deterministic, zero-LLM-call tutorial context bundle for a DIFFERENT, already-cloned repository — for writing beginner documentation with your own LLM chat:
```bash
git clone --depth 1 https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge
project-context --analysis ./PocketFlow-Tutorial-Codebase-Knowledge --signatures-only --output preview.md
project-context --analysis ./PocketFlow-Tutorial-Codebase-Knowledge --grep "FetchRepo|WriteChapters" --max-abstractions 8 --output tutorial_context.md
```

The first run is a cheap reconnaissance pass (signatures only, no full bodies) to see which files hold the highest-ranked abstractions. The second run scopes `--grep` to just those files, so the full-content bundle stays small enough for a typical chat UI's context window, even on a large target repository.

Both are 100% deterministic (AST + regex + local git/CI-config parsing) — no LLM call, no network access, no GitHub API/token required. Detected relationships include `imports`, `registers entry point`, `belongs to`, `documents`, `packages`, `validates`, `runs`, `builds from`, `produces`, `publishes build`, and `invokes`. Disable with `--diagram none`.

## Merging `--graph` output into one file

`--graph` intentionally writes one markdown file per module (plus `index.md`) rather than one flat file — that's what makes it navigable for scoped, iterative exploration. When you instead need to feed the whole graph to an LLM as a single attachment (most chat UIs accept one file, not a directory), use the bundled merge script:

```bash
python scripts/collect_graph_context.py --input project_graph --output graph_context.md
```

It concatenates every `.md` file under the `--graph` output directory into one document, placing `index.md` first (since it's the entry point/map) and every other module after it in alphabetical order, each delimited by a `--- FILE: <relative path> ---` header so per-module boundaries stay visible to the model. This is a thin post-processing step over `--graph`'s own deterministic output — it adds no new detection logic and makes no additional claims about the codebase.

## Recommended workflow

1. Start a new AI conversation with `--tree-only` so the model understands the architecture first.
2. If deep review is needed, follow up with `--signatures-only` to give the model an interface map at roughly 6% of the token cost of a full dump.
3. During iterative development, use `--changed-only --clipboard` to refresh the model with only what you've actually modified.
4. For focused debugging on one class or feature, use `--grep "ClassName"` — full implementation detail on relevant files only, at a token cost that depends heavily on how common the pattern is in your codebase.
5. For scoped, navigable exploration of one module and its dependencies, use `--graph` — it costs more tokens than `--signatures-only`, but structures the output as linked per-module files instead of one flat block. Merge it into one file with `scripts/collect_graph_context.py` if your target LLM chat UI only accepts a single attachment.
6. Reserve the unscoped full-dump mode for small projects or first-time full audits — expect the warning on repositories with 40+ files.
7. Leave `PROJECT CONVENTIONS DETECTED` enabled by default for any code-generation task — it costs a small, fixed number of tokens per run and is the single change most likely to prevent an LLM from silently skipping your test suite, lint config, or dependency file.
8. Leave `MANDATORY BASELINE FILES` and the `ARCHITECTURE PLAN GATE` enabled for any task that adds real code — the forced pre-commitment plan is the single change most likely to catch a missing test file or a silently-skipped dependency update before the model finishes responding. Use `--no-plan-gate` alone if you want the baseline facts without the two-phase planning overhead.
9. Set `--integration-scope integrated` whenever the task explicitly requires wiring a new module into existing code (entry points, CLI registries, existing classes) — the default `standalone` scope tells the model to leave existing wiring untouched, which is the safer default for most "add a new tool" tasks but the wrong one for "add a new command to the existing CLI" tasks.
10. Add `--diagram mermaid` to `--tree-only` output when you want a paste-ready architecture diagram for a README, PR description, or design doc — it renders natively on GitHub. Use `--diagram text` (the default) when the output is only going to an LLM, since Mermaid's syntax overhead adds tokens an LLM doesn't need.
11. Use `--analysis <path>` only against repositories you have the right to extract and publish code from — check the target's own `LICENSE` file (now surfaced automatically in the output if present) before publishing any tutorial chapters derived from its code.
12. Never delete the `.project_context_generated` marker file inside a `project_context_output/` or `project_graph/` folder — removing it (without deleting the whole folder) will cause the next scan to re-ingest that folder's contents as if it were source code.
13. Before committing a release, run `--report --analysis .` (self-analysis) alongside the other modes as a pre-commit sanity check — it exercises the `--analysis` code path against a known-good target (the tool's own repository) without needing a separately cloned test repository.
14. Use --diagram-detail group (the default for --tree-only) for a quick architectural glance at a project's package structure; switch to --diagram-detail file only when you need to trace exactly which file imports which, since per-file detail re-introduces the node/edge density that group mode exists to avoid.

## PROJECT CONVENTIONS DETECTED (v1.7)

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

## MANDATORY BASELINE FILES + ARCHITECTURE PLAN GATE (v1.8)

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

## MANDATORY REFERENCE SOURCE MODULE + SENIOR-DEVELOPER MANDATE (v1.9)

Blind LLM-judge evaluation of v1.8.1 across all four output modes surfaced a consistent failure mode in the lightweight modes: given only a file tree or a signature list — but no real implementation — executor models frequently responded with clarifying questions and zero code, scoring Actionability 1/5 despite otherwise-honest Calibration.

v1.9.0 closes this gap with two changes that work together:

- **Mandatory reference source module**: one real, bounded, verbatim non-test source file is now selected and embedded in every mode, including `--tree-only` and `--signatures-only`. Selection prefers a module that looks like a CLI entry point (`def main(` plus an `if __name__ == "__main__":` guard); otherwise it falls back to the median-length source module, using the same representativeness logic as the existing reference-test-file selector. This gives the model a concrete example of the project's actual error handling, argument-parsing style, and docstring conventions — not just a shape of what's callable.
- **SENIOR-DEVELOPER MANDATE**: an explicit instruction block, injected immediately before the Architecture Plan Gate, that forbids responding with zero code. Ambiguous details must be resolved with a stated, reasonable assumption and an implementation delivered anyway; "insufficient context, do not guess" is now reserved strictly for details that would silently corrupt behavior (e.g. an unconfirmed business formula), never as grounds to withhold an entire response.

A companion flag, `--integration-scope {standalone,integrated}` (default `standalone`), removes a second common source of guessing: whether a new module should be wired into the project's existing entry points/CLI registry. Set `integrated` when the task explicitly requires touching existing code; the plan gate will then demand an explicit registration diff rather than just a new file.

Both additions are covered by the existing baseline toggle:
```bash
project-context --no-baseline --output context.md
```

## MODULE GRAPH / --diagram (Mermaid, deterministic -- no LLM)

`--tree-only` and `--signatures-only` can render the project's real dependency and wiring structure as an architecture diagram, in the spirit of tools like [GitDiagram](https://gitdiagram.com/) — but computed entirely offline from AST, regex, and local git/CI-config parsing, with no LLM call and no GitHub API access.

```bash
project-context --tree-only --diagram text # plain arrow list (default for --tree-only)
project-context --tree-only --diagram mermaid # Mermaid flowchart block
project-context --diagram none # suppress the module graph entirely
```

```mermaid
flowchart TD
  g_CI["CI<br/><i>2 files: build-binaries.yml, tests.yml</i>"]
  g_root["root<br/><i>5 files: CHANGELOG.md, CONTRIBUTING.md, LICENSE.md, diagnose_module_graph.py, pyproject.toml</i>"]
  g_scripts["scripts<br/><i>1 files: collect_graph_context.py</i>"]
  g_src_dev_tools___init___py["src/dev_tools/__init__.py<br/><i>1 files: __init__.py</i>"]
  g_src_dev_tools_project_context["src/dev_tools/project_context<br/><i>16 files: README.md, __init__.py, __main__.py, analysis.py, astutils.py, baseline.py, +10 more</i>"]
  g_tests["tests<br/><i>9 files: test_analysis.py, test_astutils.py, test_baseline.py, test_benchmark.py, test_cli.py, test_collectors.py, +3 more</i>"]
  g_venv_devtools["venv-devtools<br/><i>1 files: pyvenv.cfg</i>"]
  n_Distribution___build_artifact["Distribution / build artifact<br/>artifact"]
  n_External_Python_callers((External Python callers))
  n_User___automation_invoker((User / automation invoker))
  g_root -.->|"registers project-context"| g_src_dev_tools_project_context
  g_src_dev_tools_project_context -->|"belongs to"| g_src_dev_tools___init___py
  g_root -->|"packages"| g_src_dev_tools___init___py
  g_tests -->|"validates"| g_src_dev_tools_project_context
  g_CI -->|"builds from"| g_root
  g_root -->|"produces"| n_Distribution___build_artifact
  g_CI -->|"publishes build"| n_Distribution___build_artifact
  g_CI -->|"runs"| g_tests
  n_User___automation_invoker -->|"invokes"| g_src_dev_tools_project_context
  n_External_Python_callers -->|"imports"| g_src_dev_tools___init___py
  class g_CI toneAmber
  class g_root toneMint
  class g_scripts toneBlue
  class g_src_dev_tools___init___py toneBlue
  class g_src_dev_tools_project_context toneBlue
  class g_tests toneAmber
  class g_venv_devtools toneNeutral
  class n_Distribution___build_artifact toneAmber
  class n_External_Python_callers toneNeutral
  class n_User___automation_invoker toneNeutral
  classDef toneBlue fill:#dbeafe,stroke:#2563eb,color:#172554
  classDef toneAmber fill:#fef3c7,stroke:#d97706,color:#78350f
  classDef toneMint fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef toneNeutral fill:#f8fafc,stroke:#334155,color:#0f172a
```

Detected relationships: `imports` (real Python import edges), `registers entry point` (from `pyproject.toml`'s `[project.scripts]`), `belongs to` (package hierarchy), `documents` (README → package), `packages` (manifest → root package), `validates` (test → the source files it actually references), `runs`/`builds from`/`produces`/`publishes build` (CI workflow ↔ tests ↔ manifest ↔ build artifact), and `invokes`/`imports` for two synthetic actor nodes added only when a real target exists.

One capability is intentionally **not** reproduced: GitDiagram's LLM-generated semantic descriptions (e.g. turning `__init__.py` into "Project context API / feature package"). That requires summarizing README/docstrings — inference, not extraction — and was left out to avoid presenting a hallucinated label as a detected fact.

# Node granularity (new in v2.1.1) -- controls how much detail --diagram mermaid shows:
project-context --tree-only --diagram mermaid --diagram-detail group # one node per package/group (default for --tree-only)
project-context --diagram mermaid --diagram-detail file # one node per file, grouped into labeled subgraph blocks (default otherwise)

# Edge density in --diagram-detail file mode only (new in v2.1.1):
project-context --diagram mermaid --diagram-detail file --diagram-imports collapsed # hide same-package import edges (default)
project-context --diagram mermaid --diagram-detail file --diagram-imports all # show every detected edge

Then ADD this new paragraph immediately after that block:

--diagram-detail (v2.1.1): auto (the default) resolves to group for --tree-only and file otherwise. group collapses each detected package/directory (and tests/, CI configs, root-level files) into a single node listing its member filenames as plain text, with cross-group relationships (e.g. "tests validates the project_context package") shown as one deduplicated edge per relationship type -- regardless of how many individual file pairs produced it. This is the direct fix for per-file diagrams becoming unreadable on any project past a handful of files: measured on this repository, --tree-only's default group diagram is 10 nodes/10 edges, versus 39 nodes/51 edges in file detail. Use --diagram-detail file when you need to see exactly which file imports which -- e.g. for --signatures-only, where per-file detail is already the point.

--diagram-imports (v2.1.1): only affects --diagram-detail file mode (in group mode, same-package edges are dropped entirely as self-loops on the collapsed node, so this flag has nothing left to control). collapsed (the default) hides imports edges whose source and target live in the same subgraph group, since those are almost always intra-package plumbing that clutters the diagram without adding architectural insight; a comment in the output states how many were hidden. all renders every detected edge.

## MODULE ANALYSIS: deterministic tutorial-writing context (v2.0)

`--analysis <path>` points every existing detector at a *different* local repository (not this tool's own `--root`) and appends a `CANDIDATE ABSTRACTIONS` + `RELATIONSHIPS` bundle designed to feed a beginner-tutorial-writing LLM chat. It is inspired by [PocketFlow-Tutorial-Codebase-Knowledge](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge) (MIT License), which performs the equivalent step with 2 sequential LLM calls inside its own tool — this mode reproduces the same output *shape* with **zero LLM calls**, preserving this project's core design principle: the tool prepares context, a human's own LLM chat does the reasoning.

```bash
project-context --analysis /path/to/cloned/other-repo --output tutorial_context.md --max-abstractions 8
# If you analyze your own repository (self-analysis)
project-context --analysis . --no-baseline --max-abstractions 12
```

- Candidate abstractions are ranked by real AST-extracted docstrings + import-usage count, not by an LLM's judgment — treat the ranking as a starting shortlist, not a final answer.
- Relationships are the real import graph, already computed for `--diagram` — genuinely verified, not inferred.
- If the target repository has its own `LICENSE` file, `--analysis` now surfaces it explicitly in the output: any tutorial content you publish using code extracted from that target is governed by *that project's* license, not this tool's MIT license.

### Recommended prompt for chapter-by-chapter tutorial generation

Paste this prompt first, then attach/paste the generated `tutorial_context.md`:

> You are writing a beginner-friendly tutorial for the codebase described in the attached context. Ignore the `PROJECT CONVENTIONS DETECTED` and `MANDATORY BASELINE FILES` sections entirely — they exist for code-integration tasks, not tutorial writing. Use `FILE CONTENTS`/`SIGNATURES` as your only source of real code, and treat `CANDIDATE ABSTRACTIONS` as a shortlist to confirm or re-rank after reading the actual code, not a final answer.
>
> **Step 1** — propose your final chapter order (name, one-line reason, source file) and stop. Wait for me to approve or adjust before writing anything.
>
> **Step 2** — after I approve, write ONE chapter at a time, only when I explicitly ask for the next one. Each chapter: a clear heading, a concrete analogy, one real code snippet under 10 lines quoted verbatim from the context, a plain-English walkthrough, and a one-line transition to the next chapter.

### Running the packaged `.exe` with no arguments

If you build `project-context` as a standalone executable (see the `build-binaries.yml` workflow) and double-click it with no arguments, it now prompts interactively:

**Why one chapter per turn, not all at once:** most chat UIs cap the length of a single response well below their input context window. Asking for 8 full chapters in one reply risks silent truncation partway through — PocketFlow's own tool avoids this internally by calling its `WriteChapters` step once per chapter rather than once for the whole tutorial; the prompt above reproduces that same discipline manually.

**Output isolation (v2.0.2):** `--analysis` output now defaults to `<target>/project_context_output/tutorial_context.md`, tagged with a hidden marker file (`.project_context_generated`). Any directory carrying that marker — including this one — is automatically excluded from every future scan, so re-running `--analysis` against the same target repository is always safe and never re-ingests its own prior output. Pass an explicit `--output` path to override this default location.

## Benchmarking your own project

The tool has a built-in benchmark command — you no longer need to run separate scripts:

```bash
pip install tiktoken
project-context --report
project-context --report --grep "YourClassName"
project-context --report --analysis .
```

This runs full, `--signatures-only`, `--graph` (and `--grep`/`--analysis`, if provided) against the same root, measures both character and `cl100k_base` token counts for each, and prints a single comparison table. Passing `--analysis .` benchmarks self-analysis (the tool analyzing its own repository) — useful as a repeatable pre-commit check that exercises the `--analysis` code path without needing a separately cloned repository.

Manual, single-mode runs are still available if you want the actual output file rather than just the metrics:

```bash
project-context --version
project-context --output full.md
project-context --signatures-only --output sig.md
project-context --grep "YourClassName" --output grep.md
project-context --graph --output project_graph
python scripts/collect_graph_context.py --input project_graph --output graph_context.md
```

Sample outputs generated for benchmarking (e.g. `full.md`, `sig.md`, `grep.md`, `test_context.md`, `project_graph/`, `graph_context.md`) are disposable — they are not consumed by the tool, its tests, or CI, and should not be committed to version control. Add them to `.gitignore` if you regenerate them locally.

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
