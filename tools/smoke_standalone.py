# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Exercise a standalone executable outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.smoke_xpra_runtime import smoke_xpra_runtime


class StandaloneSmokeError(RuntimeError):
    """A standalone command behavior differs from the public contract."""


def _run(
    command: list[str], *, cwd: Path, environment: dict[str, str], expected: int
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != expected:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise StandaloneSmokeError(
            f"command returned {completed.returncode}, expected {expected}: {detail}"
        )
    return completed


def smoke(artifact: Path, *, root: Path, wheels: Path) -> None:
    """Run public standalone routes with hostile cwd and import state."""
    artifact = artifact.resolve()
    if not artifact.is_file() or not os.access(artifact, os.X_OK):
        raise StandaloneSmokeError(f"standalone is not executable: {artifact}")
    version = (root / ".version").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(prefix="elsewindow-standalone-smoke-") as raw:
        temporary = Path(raw)
        hostile = temporary / "hostile"
        hostile.mkdir()
        (hostile / "elsewindow.py").write_text(
            "raise RuntimeError('hostile module imported')\n", encoding="utf-8"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(hostile),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        reported = _run(
            [str(artifact), "--version"],
            cwd=temporary,
            environment=environment,
            expected=0,
        )
        if reported.stdout.strip() != f"elsewindow {version}":
            raise StandaloneSmokeError("standalone reports the wrong version")
        help_output = _run(
            [str(artifact), "--help"],
            cwd=temporary,
            environment=environment,
            expected=0,
        )
        for option in (
            "--ssh-alias",
            "--host",
            "--encoding-profile",
            "--network-profile",
            "--persistent",
            "--log-level",
            "--diagnose",
            "--prepare-xpra",
        ):
            if option not in help_output.stdout:
                raise StandaloneSmokeError(f"standalone help is missing {option}")
        invalid = _run(
            [str(artifact), "--encoding-profile", "unreviewed"],
            cwd=temporary,
            environment=environment,
            expected=2,
        )
        if "elsewindow: error:" not in invalid.stderr:
            raise StandaloneSmokeError("standalone error prefix differs")
        missing = temporary / "missing-path"
        missing.mkdir()
        missing_environment = environment.copy()
        missing_environment["PATH"] = str(missing)
        diagnosis = _run(
            [str(artifact), "--diagnose"],
            cwd=temporary,
            environment=missing_environment,
            expected=1,
        )
        if "ssh-wrapper: 0.1.0" not in diagnosis.stdout:
            raise StandaloneSmokeError("bundled ssh-wrapper identity differs")
        for resource in (
            "live-cli.yml",
            "profiles.yml",
            "_persistent_agent.py",
            "journal.py",
            "log_transport.py",
            "requirements-xpra.txt",
            "requirements-xpra-build.txt",
        ):
            digest = hashlib.sha256(
                (root / "elsewindow" / resource).read_bytes()
            ).hexdigest()
            if f"{resource}: sha256:{digest}" not in diagnosis.stdout:
                raise StandaloneSmokeError(f"bundled {resource} differs")
        for command in ("ssh", "false", "xpra"):
            expected_error = (
                "elsewindow: missing_dependency: "
                f"required command not found on PATH: {command}"
            )
            if expected_error not in diagnosis.stderr:
                raise StandaloneSmokeError(f"missing {command} diagnostic differs")
        smoke_xpra_runtime(
            [str(artifact)], root=temporary, wheels=wheels, environment=environment
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dependency-dist", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        smoke(
            arguments.artifact,
            root=arguments.root.resolve(),
            wheels=arguments.dependency_dist.resolve(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"standalone smoke failed: {error}", file=sys.stderr)
        return 1
    print(f"Standalone smoke passed: {arguments.artifact.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
