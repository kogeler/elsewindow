# Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT.

"""Tests for the exact-lock audit and its pull-request baseline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/dependency_audit.py"
Vulnerabilities = dict[str, list[str]]


def report(path: Path, packages: Vulnerabilities) -> Path:
    dependencies = [
        {
            "name": name,
            "version": "1.0",
            "vulns": [{"id": identifier} for identifier in identifiers],
        }
        for name, identifiers in packages.items()
    ]
    path.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    return path


def audit(
    tmp_path: Path,
    head: Vulnerabilities,
    base: Vulnerabilities | None = None,
    exceptions: list[dict[str, object]] | None = None,
) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "exceptions.json"
    config.write_text(json.dumps({"exceptions": exceptions or []}), encoding="utf-8")
    arguments = [
        sys.executable,
        str(SCRIPT),
        "--exceptions",
        str(config),
        "--report",
        str(report(tmp_path / "head.json", head)),
    ]
    if base is not None:
        baseline = report(tmp_path / "base.json", base)
        arguments += ["--baseline-report", str(baseline)]
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def test_without_baseline_every_finding_fails(tmp_path: Path) -> None:
    result = audit(tmp_path, {"filelock": ["CVE-1"]})

    assert result.returncode == 1
    assert "filelock==1.0: CVE-1" in result.stderr


def test_baseline_reports_inherited_findings_without_failing(tmp_path: Path) -> None:
    result = audit(tmp_path, {"filelock": ["CVE-1"]}, {"filelock": ["CVE-1"]})

    assert result.returncode == 0, result.stderr
    assert "filelock==1.0: CVE-1" in result.stdout


def test_baseline_rejects_findings_the_change_adds(tmp_path: Path) -> None:
    result = audit(
        tmp_path,
        {"filelock": ["CVE-1", "CVE-2"], "msgpack": ["CVE-3"]},
        {"filelock": ["CVE-1"]},
    )

    assert result.returncode == 1
    assert "filelock==1.0: CVE-1" not in result.stderr
    assert "filelock==1.0: CVE-2" in result.stderr
    assert "msgpack==1.0: CVE-3" in result.stderr


def test_baseline_keeps_stale_exceptions_fatal(tmp_path: Path) -> None:
    exception: dict[str, object] = {
        "package": "filelock",
        "version": "1.0",
        "vulnerabilities": ["CVE-1"],
        "reason": "reviewed",
    }

    result = audit(tmp_path, {}, {}, [exception])

    assert result.returncode == 1
    assert "stale vulnerability exceptions" in result.stderr
