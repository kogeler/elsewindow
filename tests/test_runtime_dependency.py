# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Runtime identity follows the requirements input, not a copied version."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools import runtime_dependency


def test_runtime_identity_reads_changed_input_without_a_cache(tmp_path: Path) -> None:
    source = tmp_path / "requirements.in"
    for version in ("42.3.7", "43.0rc1"):
        source.write_text(f"# Sole runtime input.\n\nssh-wrapper=={version}\n")
        assert runtime_dependency.runtime_version(tmp_path) == version


@pytest.mark.parametrize(
    "content",
    (
        "",
        "# comment\n",
        "ssh-wrapper>=42.3.7\n",
        "ssh-wrapper==42.*\n",
        "-r another.in\n",
        "another-package==42.3.7\n",
        "ssh-wrapper==42.3.7\nssh-wrapper==43.0\n",
        "ssh-wrapper==42.3.7\nanother-package==43.0\n",
        "ssh-wrapper @ https://example.invalid/package.whl\n",
    ),
)
def test_runtime_identity_rejects_nonexclusive_or_nonexact_inputs(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "requirements.in").write_text(content)
    with pytest.raises(ValueError, match="one exact"):
        runtime_dependency.runtime_version(tmp_path)


def test_installed_identity_is_checked_against_current_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "requirements.in"
    source.write_text("ssh-wrapper==42.3.7\n")
    imported: list[str] = []
    monkeypatch.setattr(
        runtime_dependency.importlib.metadata,
        "version",
        lambda name: "42.3.7" if name == "ssh-wrapper" else None,
    )
    monkeypatch.setattr(runtime_dependency.importlib, "import_module", imported.append)
    runtime_dependency.check_installed(tmp_path)
    assert imported == ["ssh_wrapper"]
    source.write_text("ssh-wrapper==43.0\n")
    with pytest.raises(ValueError, match="differs from requirements.in"):
        runtime_dependency.check_installed(tmp_path)


def test_runtime_identity_cli_is_standard_library_only_and_cwd_independent(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.in").write_text("ssh-wrapper==42.3.7\n")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            runtime_dependency.__file__,
            "--root",
            str(tmp_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "42.3.7\n"


def test_runtime_identity_cli_rejects_missing_input_without_private_paths(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            runtime_dependency.__file__,
            "--root",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "requirements.in" in result.stderr
    assert str(tmp_path) not in result.stderr
