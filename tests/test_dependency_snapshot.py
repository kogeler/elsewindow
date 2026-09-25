# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Regression tests for the exact GitHub dependency snapshot."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from tools.runtime_dependency import runtime_version

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_SCRIPT = ROOT / ".github/scripts/dependency_snapshot.py"
AUDIENCES = ("quality", "test", "package", "standalone", "docs")
LOCKS = (
    "requirements.txt",
    *(f"requirements-{name}.txt" for name in AUDIENCES),
    "elsewindow/requirements-xpra.txt",
    "elsewindow/requirements-xpra-build.txt",
)
INPUTS = (
    "requirements.in",
    *(f"requirements-{name}.in" for name in AUDIENCES),
    "elsewindow/requirements-xpra.in",
    "elsewindow/requirements-xpra-build.in",
)


def _run_snapshot(root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SNAPSHOT_SCRIPT),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _copy_inputs(destination: Path) -> None:
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    for name in INPUTS + LOCKS:
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination / name)


def _replace_runtime_dependency(path: Path, replacement: str) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(
        index
        for index, line in enumerate(lines)
        if line and not line.startswith(("#", "-r "))
    )
    current = lines[index]
    name, separator, version = current.partition("==")
    assert separator
    lines[index] = replacement
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return name, version


def test_snapshot_contains_all_exact_lock_graphs(tmp_path: Path) -> None:
    output = tmp_path / "nested/snapshot.json"
    result = _run_snapshot(ROOT, output)

    assert result.returncode == 0, result.stderr
    manifests = json.loads(output.read_text(encoding="utf-8"))["manifests"]
    assert set(manifests) == set(LOCKS)
    for name, manifest in manifests.items():
        assert manifest["name"] == name
        assert manifest["file"] == {"source_location": name}
        assert manifest["resolved"]

    runtime = manifests["requirements.txt"]["resolved"]
    graphics = manifests["elsewindow/requirements-xpra.txt"]["resolved"]
    assert set(graphics) == {"pyopengl", "pyopengl-accelerate"}
    assert {item["scope"] for item in graphics.values()} == {"runtime"}
    builder = manifests["elsewindow/requirements-xpra-build.txt"]["resolved"]
    assert set(builder) == {"cython", "numpy", "setuptools"}
    assert {item["scope"] for item in builder.values()} == {"development"}
    assert runtime == {
        "ssh-wrapper": {
            "package_url": f"pkg:pypi/ssh-wrapper@{quote(runtime_version(ROOT), safe='')}",
            "relationship": "direct",
            "scope": "runtime",
        }
    }
    expected = {
        "quality": ("bandit", "ast-serialize"),
        "test": ("pytest", "coverage"),
        "package": ("setuptools", "packaging"),
        "standalone": ("pyinstaller", "altgraph"),
        "docs": ("mkdocs-material", "babel"),
    }
    for audience, (direct, indirect) in expected.items():
        resolved = manifests[f"requirements-{audience}.txt"]["resolved"]
        assert resolved["ssh-wrapper"]["relationship"] == "direct"
        assert resolved[direct]["relationship"] == "direct"
        assert resolved[indirect]["relationship"] == "indirect"
        assert {item["scope"] for item in resolved.values()} == {"development"}


def test_snapshot_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_result = _run_snapshot(ROOT, first)
    second_result = _run_snapshot(ROOT, second)

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first.read_bytes() == second.read_bytes()


def test_lock_validator_follows_a_changed_runtime_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SNAPSHOT_SCRIPT.parent))
    from dependency_snapshot import SnapshotError
    from lock_validation import validate

    _copy_inputs(tmp_path)
    current = runtime_version(ROOT)
    # These are inert test fixtures, never edits to maintained generated locks.
    for version in ("42.3.7", "43.0rc1"):
        (tmp_path / "requirements.in").write_text(f"ssh-wrapper=={version}\n")
        for name in LOCKS:
            path = tmp_path / name
            path.write_text(
                path.read_text().replace(
                    f"ssh-wrapper=={current} \\", f"ssh-wrapper=={version} \\"
                )
            )
        assert validate(tmp_path)["requirements.txt"] == 1
        current = version
    (tmp_path / "requirements.in").write_text("ssh-wrapper==44.0\n")
    with pytest.raises(SnapshotError, match="versions differ"):
        validate(tmp_path)


def test_snapshot_rejects_unrecognized_lock_content(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    runtime = tmp_path / "requirements.txt"
    runtime.write_text(
        runtime.read_text(encoding="utf-8") + "--index-url example.invalid\n",
        encoding="utf-8",
    )

    result = _run_snapshot(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "unsupported lock entry" in result.stderr


def test_snapshot_rejects_missing_and_mismatched_direct_dependency(
    tmp_path: Path,
) -> None:
    _copy_inputs(tmp_path)
    runtime_input = tmp_path / "requirements.in"
    name, locked_version = _replace_runtime_dependency(
        runtime_input, "missing-package==1.0.0"
    )
    missing = _run_snapshot(tmp_path, tmp_path / "missing.json")
    assert missing.returncode == 1
    assert "direct dependencies missing from lock: missing-package" in missing.stderr

    replacement_version = "0.0.1" if locked_version == "0.0.0" else "0.0.0"
    _replace_runtime_dependency(runtime_input, f"{name}=={replacement_version}")
    mismatch = _run_snapshot(tmp_path, tmp_path / "mismatch.json")
    assert mismatch.returncode == 1
    assert (
        f"{name}=={replacement_version} (lock has {locked_version})" in mismatch.stderr
    )


def test_snapshot_rejects_hashless_and_duplicate_hash_entries(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    quality = tmp_path / "requirements-quality.txt"
    quality.write_text(
        "# This file is autogenerated by pip-compile\n"
        "fixture-package==1.0.0 \\\n"
        "    # via test\n",
        encoding="utf-8",
    )
    hashless = _run_snapshot(tmp_path, tmp_path / "hashless.json")
    assert hashless.returncode == 1
    assert "fixture-package==1.0.0 has no SHA-256 hash" in hashless.stderr

    digest = "0" * 64
    quality.write_text(
        "# This file is autogenerated by pip-compile\n"
        "fixture-package==1.0.0 \\\n"
        f"    --hash=sha256:{digest} \\\n"
        f"    --hash=sha256:{digest}\n",
        encoding="utf-8",
    )
    duplicate = _run_snapshot(tmp_path, tmp_path / "duplicate.json")
    assert duplicate.returncode == 1
    assert "duplicate SHA-256 hash" in duplicate.stderr


def test_snapshot_rejects_noncanonical_runtime_include(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    quality_input = tmp_path / "requirements-quality.in"
    quality_input.write_text(
        quality_input.read_text(encoding="utf-8").replace(
            "-r requirements.in", "-r unexpected.in"
        ),
        encoding="utf-8",
    )

    result = _run_snapshot(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "must include exactly '-r requirements.in' once" in result.stderr


def test_snapshot_rejects_project_dependency_version_duplication(
    tmp_path: Path,
) -> None:
    _copy_inputs(tmp_path)
    project = tmp_path / "pyproject.toml"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            'dynamic = ["version", "dependencies"]',
            f'dynamic = ["version"]\ndependencies = ["ssh-wrapper=={runtime_version(ROOT)}"]',
        ),
        encoding="utf-8",
    )

    result = _run_snapshot(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "[project].dynamic" in result.stderr
