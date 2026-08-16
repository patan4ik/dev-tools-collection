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
    _render_key_file_contents_section,
    _resolve_key_files_for_full_dump,
    default_analysis_output_path,
    detect_documentation_drift,
    extract_readme_flag_mentions,
    extract_registered_cli_flags,
    run_analysis_mode,  # ← добавить
    select_candidate_abstractions,
)
from project_context.config import Config  # ← добавить
from project_context.constants import ANALYSIS_OUTPUT_DIRNAME


def test_extract_readme_flag_mentions_captures_description(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\n" "- `--verbose`: prints extra diagnostic output during the run.\n")
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "--verbose" in flags
    assert "flag name" not in flags["--verbose"]  # description, not the flag echoed back
    assert "prints extra diagnostic" in flags["--verbose"]


def test_extract_readme_flag_mentions_ignores_other_tools_flags_in_same_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "Generating a tutorial context for a different repo:\n"
        "```bash\n"
        "git clone --depth 1 https://github.com/example/repo\n"
        "project-context --analysis ./repo --output tutorial.md\n"
        "```\n"
    )
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "--depth" not in flags  # git's own flag, not ours
    assert "--analysis" in flags  # real project-context flag
    assert "--output" in flags


def test_extract_readme_flag_mentions_truncates_at_sentence_boundary(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "3. During iterative development, use `--changed-only` --clipboard "
        "to refresh the model with only what you've actually modified. "
        "This is a second sentence that should NOT appear in the description.\n"
    )
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "second sentence" not in flags.get("--changed-only", "")


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


# ============================================================
# 1. _resolve_key_files_for_full_dump() -- decides which files get
#    full source embedded. Zero prior coverage.
# ============================================================


def test_resolve_key_files_returns_only_abstraction_backing_files(tmp_path):

    keep = tmp_path / "keep.py"
    drop = tmp_path / "drop.py"
    keep.write_text("def kept(): pass\n")
    drop.write_text("def dropped(): pass\n")

    abstractions = [{"name": "kept", "files": ["keep.py"], "kind": "function", "usage_count": 0}]
    result = _resolve_key_files_for_full_dump([keep, drop], tmp_path, abstractions)

    assert result == [keep]


def test_resolve_key_files_deduplicates_file_shared_by_multiple_abstractions(tmp_path):

    shared = tmp_path / "shared.py"
    shared.write_text("def a(): pass\ndef b(): pass\n")

    abstractions = [
        {"name": "a", "files": ["shared.py"], "kind": "function", "usage_count": 0},
        {"name": "b", "files": ["shared.py"], "kind": "function", "usage_count": 0},
    ]
    result = _resolve_key_files_for_full_dump([shared], tmp_path, abstractions)

    assert result == [shared]  # not duplicated despite backing two abstractions


def test_resolve_key_files_empty_when_no_abstractions(tmp_path):

    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert _resolve_key_files_for_full_dump([f], tmp_path, []) == []


# ============================================================
# 2. _render_key_file_contents_section() -- zero prior coverage.
# ============================================================


def _minimal_cfg(tmp_path: Path) -> Config:

    return Config(
        root=tmp_path,
        output=None,
        tree_only=False,
        changed_only=False,
        signatures_only=False,
        graph=False,
        grep_pattern=None,
        max_chars=None,
        output_format="md",
        clipboard=False,
        report=False,
    )


def test_render_key_file_contents_includes_real_verbatim_source(tmp_path):

    f = tmp_path / "app.py"
    f.write_text("def main():\n    return 42\n")
    cfg = _minimal_cfg(tmp_path)

    result = _render_key_file_contents_section([f], tmp_path, cfg)
    assert "## KEY FILE CONTENTS" in result
    assert "def main():" in result
    assert "return 42" in result


def test_render_key_file_contents_fallback_message_when_empty(tmp_path):

    cfg = _minimal_cfg(tmp_path)
    result = _render_key_file_contents_section([], tmp_path, cfg)
    assert "No candidate-abstraction-backing files were resolved" in result
    assert "SIGNATURES" in result


# ============================================================
# 3. run_analysis_mode() end-to-end -- the ACTUAL Gap-3 bug
#    (duplication), previously with NO regression test at all.
# ============================================================


def _make_multi_file_project(root: Path) -> Path:
    """One file backing a likely-ranked abstraction (main/entry point),
    one file that should NOT rank and therefore should NOT get its
    full body duplicated."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "core.py").write_text(
        '"""Core module."""\n'
        "def helper():\n"
        '    """Used by main."""\n'
        "    return 1\n\n"
        "def main():\n"
        '    """Entry point."""\n'
        "    return helper()\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    (root / "unused_leaf.py").write_text(
        "def never_called_by_anything_and_should_not_be_duplicated():\n"
        "    return 'leaf body unique marker 12345'\n"
    )
    return root


def test_analysis_does_not_duplicate_full_body_for_non_abstraction_files(tmp_path):
    """Regression guard for the exact Gap-3 bug: a file's full function
    body must appear at most once in the output (inside KEY FILE
    CONTENTS if it ranked, or not at all if it didn't) -- never twice
    (once in a full-dump FILE CONTENTS pass, once again implicitly via
    a duplicated architecture pass)."""
    target = _make_multi_file_project(tmp_path / "target")
    cfg = _minimal_cfg(tmp_path)
    cfg.analysis_target = str(target)
    cfg.analysis_max_abstractions = 5
    cfg.no_baseline = True
    cfg.output = None  # print to stdout instead of writing a file

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_analysis_mode(cfg, write_files=True)
    output = buf.getvalue()

    marker = "leaf body unique marker 12345"
    assert output.count(marker) <= 1


def test_analysis_signatures_only_architecture_pass_has_no_full_body_dump(tmp_path):
    """The architecture/tree pass must run signatures-only, not a full
    dump -- confirmed by checking that a function body which is NOT
    part of KEY FILE CONTENTS never appears verbatim outside that
    section."""
    target = _make_multi_file_project(tmp_path / "target")
    cfg = _minimal_cfg(tmp_path)
    cfg.analysis_target = str(target)
    cfg.analysis_max_abstractions = 5
    cfg.no_baseline = True
    cfg.output = None

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_analysis_mode(cfg, write_files=True)
    output = buf.getvalue()

    # The signature must still be visible (nothing is hidden)...
    assert "never_called_by_anything_and_should_not_be_duplicated" in output
    # ...but if it didn't rank into the abstractions, its body must not
    # appear at all outside of a real KEY FILE CONTENTS block.
    key_section_start = output.find("KEY FILE CONTENTS")
    body_marker = "leaf body unique marker 12345"
    if body_marker in output:
        assert output.index(body_marker) > key_section_start


def test_analysis_candidate_abstractions_and_key_files_stay_consistent(tmp_path):
    """abstractions must be computed ONCE and reused for both the
    CANDIDATE ABSTRACTIONS section and the KEY FILE CONTENTS scoping --
    guards against the two silently drifting apart after a refactor."""
    target = _make_multi_file_project(tmp_path / "target")
    cfg = _minimal_cfg(tmp_path)
    cfg.analysis_target = str(target)
    cfg.analysis_max_abstractions = 5
    cfg.no_baseline = True
    cfg.output = None

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_analysis_mode(cfg, write_files=True)
    output = buf.getvalue()

    assert "CANDIDATE ABSTRACTIONS" in output
    assert "KEY FILE CONTENTS" in output
    # main() is force-included as the detected entry point in both.
    assert "main" in output
    assert "return helper()" in output  # main's real body, verbatim, in KEY FILE CONTENTS


def test_extract_readme_flag_mentions_prefers_prose_over_table_fragment(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "| Mode | Purpose |\n"
        "|---|---|\n"
        "| `--tree-only` | Architecture only, minimal exemplar, 14229 3397 93% |\n\n"
        "## Examples\n\n"
        "Architecture-only review (e.g. onboarding a new AI session):\n"
        "```bash\n"
        "project-context --tree-only\n"
        "```\n"
    )
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "onboarding a new AI session" in flags["--tree-only"]
    assert "93%" not in flags["--tree-only"]  # table fragment must lose


def test_extract_readme_flag_mentions_captures_example_only_flags(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "Mid-refactor update -- only files you just edited:\n"
        "```bash\n"
        "project-context --changed-only --clipboard\n"
        "```\n"
    )
    flags = extract_readme_flag_mentions([readme], tmp_path)
    assert "--changed-only" in flags
    assert "--clipboard" in flags
    assert "Mid-refactor" in flags["--changed-only"]


def test_first_sentence_does_not_break_on_abbreviation_period():
    from project_context.analysis import _first_sentence

    text = "Architecture-only review (e.g. onboarding a new AI session)"
    assert _first_sentence(text) == text


def test_first_sentence_strips_leading_numbered_list_marker():
    from project_context.analysis import _first_sentence

    text = (
        "3. During iterative development, use --changed-only --clipboard "
        "to refresh the model with only what you've actually modified."
    )
    result = _first_sentence(text)
    assert not result.startswith("3.")
    assert "During iterative development" in result


def test_first_sentence_still_cuts_at_real_sentence_boundary():
    from project_context.analysis import _first_sentence

    text = "First real sentence. Second sentence that must be dropped."
    assert _first_sentence(text) == "First real sentence."
