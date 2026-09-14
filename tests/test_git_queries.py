"""Security and failure behavior of the fixed Git query boundary."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dev_tools.project_context.collectors import _run_git, get_changed_files, get_git_remote_url


def test_git_uses_absolute_paths_and_bounded_shell_free_execution(tmp_path: Path) -> None:
    executable = tmp_path / "Program Files" / "Git" / "git.exe"
    root = tmp_path / "repo with spaces & symbols"
    with (
        patch("dev_tools.project_context.collectors.shutil.which", return_value=str(executable)),
        patch("dev_tools.project_context.collectors.subprocess.run") as run,
    ):
        _run_git(root, ("status", "--porcelain"))
    run.assert_called_once_with(
        [str(executable.resolve()), "-C", str(root.resolve()), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
        timeout=15,
    )


def test_git_rejects_commands_outside_allowlist(tmp_path: Path) -> None:
    with patch("dev_tools.project_context.collectors.subprocess.run") as run:
        with pytest.raises(ValueError, match="Unsupported"):
            _run_git(tmp_path, ("push",))
        run.assert_not_called()


def test_git_missing_is_handled(tmp_path: Path) -> None:
    with patch("dev_tools.project_context.collectors.shutil.which", return_value=None):
        assert get_changed_files(tmp_path) == set()
        assert get_git_remote_url(tmp_path) is None


def test_git_timeout_is_handled(tmp_path: Path) -> None:
    with patch(
        "dev_tools.project_context.collectors._run_git",
        side_effect=subprocess.TimeoutExpired("git", 15),
    ):
        assert get_changed_files(tmp_path) == set()
        assert get_git_remote_url(tmp_path) is None
