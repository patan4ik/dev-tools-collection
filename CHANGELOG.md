# Changelog

## [2.1.3] - 2026-08-19

- Renamed `--analysis` to `--docs` (old flag still works, deprecated).
- Renamed `--analysis-include-baseline` to `--docs-include-baseline` (old flag still works, deprecated).
- Replaced regex-based CLI flag detection with AST-based detection (fixes false-positive documentation-drift reports).
- Added `validate_tutorial_bundle()`: structural sanity check on generated `--docs` output.
- Added `security_check.yml` CI workflow (mypy + bandit), zero actionable findings.
- Renamed `--report`'s comparison-table label from `analysis:` to `docs:`.

## [2.1.2] and earlier

Full history available in the private repository. This public snapshot reflects v2.1.3 as the final open-source release.
