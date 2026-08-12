"""
tests/test_analysis.py

Unit tests for src/dev_tools/project_context/analysis.py -- covers the
v2.0.3 documentation-completeness fixes: README/CHANGELOG feature
inventory, documentation-vs-code drift detection, and forced inclusion
of the detected CLI entry point regardless of import-usage ranking.
None of this was covered by the pre-split test suite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "dev_tools"))

from project_context.analysis import (
    default_analysis_output_path,
    detect_documentation_drift,
    extract_readme_flag_mentions,
    extract_registered_cli_flags,
    select_candidate_abstractions,
)
from project_context.constants import ANALYSIS_OUTPUT_DIRNAME


def test_extract_readme_flag_mentions_captures_description(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\n" "- `--verbose`: prints extra diagnostic output during the run.\n")
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "--verbose" in flags
    assert "flag name" not in flags["--verbose"]  # description, not the flag echoed back
    assert "prints extra diagnostic" in flags["--verbose"]


def test_extract_registered_cli_flags_finds_add_argument_calls(tmp_path):
    src = tmp_path / "cli.py"
    src.write_text(
        'parser.add_argument("--verbose", action="store_true")\n'
        'parser.add_argument("--output", type=str)\n'
    )
    flags = extract_registered_cli_flags([src])
    assert flags == {"--verbose", "--output"}


def test_documentation_drift_flags_both_directions():
    """README content is a coverage HINT, not ground truth -- a flag
    documented but removed from code, and a flag implemented but never
    documented, must both be surfaced explicitly, not silently trusted
    or silently dropped."""
    readme_flags = {"--verbose": "desc", "--removed-flag": "desc"}
    code_flags = {"--verbose", "--new-flag"}

    drift = detect_documentation_drift(readme_flags, code_flags)

    assert drift["documented_and_implemented"] == ["--verbose"]
    assert drift["documented_but_not_found_in_code"] == ["--removed-flag"]
    assert drift["implemented_but_undocumented"] == ["--new-flag"]


def test_entry_point_force_included_despite_zero_usage(tmp_path):
    """Root-cause fix: for a single-file target, the cross-file import
    graph is empty, so every candidate ties at usage_count=0 and main()
    -- called only via the __main__ guard, never imported -- previously
    never made the ranked shortlist. It must now always be included."""
    src = tmp_path / "app.py"
    src.write_text(
        '"""Demo app."""\n'
        "def helper():\n"
        '    """Does something."""\n'
        "    return 1\n\n"
        "def main():\n"
        '    """Entry point."""\n'
        "    helper()\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    abstractions = select_candidate_abstractions([src], tmp_path, max_abstractions=1)
    names = {a["name"] for a in abstractions}
    assert "main" in names, "entry point must be force-included even when max_abstractions is tiny"


def test_default_analysis_output_path_is_isolated_subfolder(tmp_path):
    """--analysis output must never land directly in the target repo
    root -- it must be isolated into its own tagged subfolder so a
    second run against the same target never re-ingests prior output."""
    out_path = default_analysis_output_path(tmp_path)
    assert out_path.parent.name == ANALYSIS_OUTPUT_DIRNAME
    assert out_path.name == "tutorial_context.md"
    assert out_path.parent.parent == tmp_path
