# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Focused tests for dynamic version ownership and release notes."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/scripts/version.py"


def _repository(path: Path, version: str = "1.2.3") -> None:
    (path / ".version").write_text(f"{version}\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\ndynamic = ["version"]\n'
        '[tool.setuptools.dynamic]\nversion = { file = ".version" }\n',
        encoding="utf-8",
    )
    (path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-08-30\n\n- Entry.\n",
        encoding="utf-8",
    )


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), "--root", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_version_rejects_static_metadata_and_non_increment(tmp_path: Path) -> None:
    _repository(tmp_path)
    assert _run(tmp_path, "check", "--base-version", "1.2.2").returncode == 0
    same = _run(tmp_path, "check", "--base-version", "1.2.3")
    assert same.returncode == 1 and "must be greater" in same.stderr
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n', encoding="utf-8"
    )
    drift = _run(tmp_path, "check")
    assert drift.returncode == 1 and "dynamic" in drift.stderr


def test_unpublished_recovery_and_exact_notes(tmp_path: Path) -> None:
    _repository(tmp_path)
    recovery = _run(
        tmp_path,
        "check",
        "--base-version",
        "1.2.3",
        "--unpublished-base-version",
        "1.2.2",
    )
    assert recovery.returncode == 0, recovery.stderr
    (tmp_path / ".version").write_text("1.2.4\n", encoding="utf-8")
    output = tmp_path / "notes.md"
    shutil.copy2(tmp_path / "CHANGELOG.md", tmp_path / "old-changelog")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.2.4] - 2026-08-30\n\n- New entry.\n\n"
        "## [1.2.3] - 2026-08-29\n\n- Old.\n",
        encoding="utf-8",
    )
    notes = _run(
        tmp_path,
        "notes",
        "--output",
        str(output),
        "--repository",
        "owner/project",
    )
    assert notes.returncode == 0, notes.stderr
    assert output.read_text(encoding="utf-8") == (
        "- New entry.\n\nFull changelog: "
        "https://github.com/owner/project/blob/v1.2.4/CHANGELOG.md\n"
    )
