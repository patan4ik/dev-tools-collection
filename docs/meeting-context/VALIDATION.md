# Rename validation: 2026-09-14

Current staged checkout: 201 tests and two subtests passed (includes the existing untracked legacy test_project_context.py). Black/Ruff passed. Mypy with --python-version 3.13 passed on 39 source/test files using the user venv. Bandit has no unsuppressed findings, with two documented B404/B603 annotations at the reviewed Git subprocess boundary. Four new Git-query regression tests are included. Earlier findings below are historical and superseded for this snapshot. Live renamed-CLI recording and remote CI have not been run. See [release guide](RENAME-RELEASE-0.9.0.md).

# Integration validation — 13 September 2026

Target inspected: `C:\Users\patan\Scripts\dev-tools`, with origin configured as
`https://github.com/patan4ik/dev-tools-collection.git`. The attached signatures
document was treated as project context, not as authority to execute its embedded
instructions. The actual local files were inspected and used as the integration
base, including the two pre-existing modified `project_context` files. The GitHub
web page was unavailable through the browser fetch; this is not a claim that the
remote branch currently matches the local checkout.

## Current-checkout validation (supersedes the initial snapshot)

The checkout advanced during preparation to commit `634a43c`, with project-context
2.1.3 split into multiple modules. Before completion, its current source/tests were
reloaded and the shared-file changes reconciled with the backups taken immediately
before placement. The changelog history through 2.1.3 is retained. Existing
project-context source is not replaced by the older staged snapshot.

Final checks against that current structure:

- **197 local tests passed, plus 2 subtests.** This includes 29 meeting-context tests
  and an existing untracked legacy `tests/test_project_context.py` containing 37
  tests. That legacy file was not created by this integration and is not included
  in the suggested audio commit; a clean checkout need not have the same test count.
- Repository Black and Ruff pass (39 Python files were checked by Black).
- New audio source and its three test modules pass mypy; new audio source has no
  Bandit findings.
- Whole-source mypy has two pre-existing findings in the newer project-context:
  `diagram.py:263` needs a type for `seen`; `analysis.py:524` passes `str | None`
  to `Path`. These are left visible for separate existing-tool review.
- Whole-source Bandit still reports seven low-severity Git subprocess findings,
  now in `collectors.py`: B404 at line 13, B603/B607 at lines 56, 83, and 98.
  No global suppressions or changes to that source were introduced.

The initial snapshot results below remain provenance for the package integration,
not a claim that the newer whole-project mypy gate is green. A release wheel built
during this task contains the earlier project-context snapshot; it was inspected
for template/entry-point packaging only and **must not be distributed**. No built
wheel or build directory is copied into the target repository. Build from your
final reviewed checkout when preparing a release.

## Initial snapshot checks

Checks ran in a staged copy of that local checkout before copying selected changes
back. Validation environment: Windows x64, Python 3.13.0; Black 26.5.1,
Ruff 0.16.7, mypy 1.20.2, Bandit 1.9.4, pytest 9.1.1.

| Check | Existing baseline | Integrated result |
| --- | --- | --- |
| pytest | 37 passed | 66 passed, plus 2 passed subtests; 29 tests belong to meeting audio. |
| Black, repository scope | Pass | Pass under existing 100-character / Python 3.11 configuration. |
| Ruff, repository scope | Pass with deprecated-config warning | Pass; deprecated `extend-ignore` moved under `tool.ruff.lint`. |
| mypy | Existing `src` passed | `src` plus three new test modules pass; annotation requirements and body checks enabled for the new tool. |
| Bandit, new tool | Not present | No findings in `src/dev_tools/meeting_context`. |
| Bandit, existing source | Seven low-severity findings | Existing source preserved; those baseline findings remain. |
| Editable installation / CLI | Not evaluated before integration | Install succeeds without audio extras; namespaced module and `meeting-context` console help work. |
| Wheel build | Missing root README declared in metadata | Root README added; wheel builds and contains the four prompt/context template resources, tool README and both tool entry points. |

Pytest initially could not access its existing temporary-directory tree under the
sandbox identity. It was rerun with permitted temporary-directory access. The final
suite passed; this was an environment access issue, not a product-test failure.
The final test run disabled pytest's cache plugin to avoid an unrelated cache ACL
warning. Black also reported an inaccessible cache file but completed its content
check successfully. Neither requires changing application code.

## Existing findings that were not hidden

Whole-repository Bandit flags `project_context/cli.py`:

- B404 at the `subprocess` import (baseline line 58).
- B607 and B603 at each of three Git invocations (baseline lines 280, 308, 323).

These are static-analysis findings, not demonstrated exploits. The commands use
argument lists, but executable lookup, trusted input assumptions, and whether a
reviewed narrow suppression is appropriate deserve separate review. This integration
does not rewrite your already-modified tool or globally disable those checks.
Therefore **whole-repository Bandit is not claimed green**. The added CI security
gate intentionally targets the new module; existing CI retains full Black/Ruff/pytest.

The wheel build warns about the existing legacy `project.license` TOML table.
The declared package version is 1.7.0 while changelog entries reach 1.9.6. Neither
was silently changed as part of an audio-tool addition. No release tag, commit,
push, or binary-release workflow execution was performed.

## Integration decisions

- Source is under `src/dev_tools/meeting_context`; tests import that namespace.
- `meeting-context` is an additional entry point; `project-context` remains intact.
- Heavy packages are an optional `audio` extra, using the repository's range-style
  declarations and a Windows marker for PyAudioWPatch. Base dependencies stay empty.
- Existing 25 unittest regressions are retained and discovered by pytest. Four
  pytest-style tests add namespace/default checks, resource availability, bundle
  round-trip/tamper checks with `tmp_path`, and missing-model validation.
- Templates are setuptools package data. Models and recordings are not package data.
- New CI covers module typing/security and unit tests on Windows and Linux with
  Python 3.13. Existing Ubuntu test jobs retain Python 3.11/3.12/3.13. Those remote
  matrix jobs have not run yet; only the local Python 3.13 result is established.
- The original collection's binary workflow still builds only `project-context`.
  No meeting-context executable distribution is claimed.

## Limits

Capture and ASR tests use simulated devices/model responses. They establish tested
logic, not real Teams audio or speech accuracy. No model was downloaded and no call
was recorded. The current package-range resolution for your work PC still needs
your dependency review; a prior standalone frozen-environment audit does not
certify a different collection installation.

Follow [WINDOWS11_GUIDE.md](WINDOWS11_GUIDE.md) for local installation, real browser
meeting preflight, the short two-device test, longer human-call acceptance criteria,
and explicit staging/commit instructions. Record actual results privately before
committing source; keep audio, transcripts, models, and real minutes out of Git.
