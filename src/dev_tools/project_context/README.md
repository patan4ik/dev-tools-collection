[![Security & Type Checks](https://github.com/patan4ik/dev-tools-collection/actions/workflows/security_check.yml/badge.svg)](https://github.com/patan4ik/dev-tools-collection/actions/workflows/security_check.yml)
[![Tests](https://github.com/patan4ik/dev-tools-collection/actions/workflows/tests.yml/badge.svg)](https://github.com/patan4ik/dev-tools-collection/actions/workflows/tests.yml)
[![Version](https://img.shields.io/badge/version-2.1.3-blue)](CHANGELOG.md)

# project-context

A CLI tool that compresses a codebase into a compact, structured context for LLMs — instead of pasting entire repositories into a chat window.

**Privacy-first. Zero LLM calls. Fully auditable.** Every mode below runs 100% locally via AST parsing, regex, and local git/CI-config inspection — no network access, no API keys, no telemetry, no code ever leaves your machine.

## What it does

| Mode | Purpose |
|---|---|
| `--tree-only` | Architecture overview, no file contents |
| `--signatures-only` | Function/class signatures without bodies |
| `--graph` | Per-module files with dependency links |
| `--docs <path>` | Deterministic tutorial-context bundle for a repo (candidate abstractions, import graph, doc-vs-code drift — zero LLM calls) |
| `--report` | Token/character comparison across all modes |

## Measured token reduction (self-analysis)

```
project-context --report --docs .
```

| Mode | Tokens | Reduction vs. full dump |
|---|---|---|
| full dump | 70,826 | baseline |
| `--tree-only` | 7,510 | 89.4% |
| `--signatures-only` | 10,769 | 84.8% |
| `--graph` | 12,424 | 82.5% |
| `--docs` | 19,636 | 72.3% |

## Status

This public repository is a proof-of-concept / demo snapshot at **v2.1.3**. Active development continues in a private repository.

## License

MIT — see [LICENSE.md](LICENSE.md).
