from argparse import Namespace
from importlib.resources import files
from pathlib import Path

import pytest

from dev_tools.meeting_context.cli import parser
from dev_tools.meeting_context.models import describe_model
from dev_tools.meeting_context.packaging import bundle, init_context, verify_bundle


def test_namespaced_entry_point_defaults() -> None:
    args = parser().parse_args(["transcribe", "meetings/trial", "--out", "meetings/text"])
    assert args.model == Path("models/small.en")
    assert args.language == "en"
    assert args.task == "transcribe"


def test_packaged_templates_are_available() -> None:
    templates = files("dev_tools.meeting_context").joinpath("templates")
    for name in (
        "MOM_RULES.md",
        "meeting_context.txt",
        "GENERATE_MINUTES_PROMPT.md",
        "REVIEW_MINUTES_PROMPT.md",
    ):
        assert templates.joinpath(name).read_text(encoding="utf-8").strip()


def test_pytest_bundle_round_trip(tmp_path: Path) -> None:
    transcript = tmp_path / "reviewed.txt"
    transcript.write_text("[S000001] We did not approve the deployment.\n", encoding="utf-8")
    context = tmp_path / "context.txt"
    init_context(Namespace(out=context))
    output = tmp_path / "bundle"
    bundle(Namespace(transcript=transcript, context=context, out=output))
    verify_bundle(Namespace(input=output))
    assert (output / "transcript.txt").read_bytes() == transcript.read_bytes()
    (output / "transcript.txt").write_text("Changed content", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_bundle(Namespace(input=output))


def test_missing_model_is_a_local_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Local model directory missing"):
        describe_model(tmp_path / "not-provisioned")
