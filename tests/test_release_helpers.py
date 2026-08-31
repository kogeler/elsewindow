# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Exact PyPI equality and GitHub partial-recovery policy tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"
VERSION = "1.2.3"


def _module(name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _pypi_fixture(root: Path) -> tuple[Path, Path]:
    dist = root / "dist"
    dist.mkdir(parents=True)
    urls: list[dict[str, Any]] = []
    for name, package_type in (
        (f"elsewindow-{VERSION}-py3-none-any.whl", "bdist_wheel"),
        (f"elsewindow-{VERSION}.tar.gz", "sdist"),
    ):
        path = dist / name
        path.write_bytes(f"contents:{name}".encode())
        urls.append(
            {
                "filename": name,
                "packagetype": package_type,
                "size": path.stat().st_size,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "yanked": False,
            }
        )
    metadata = root / "pypi.json"
    metadata.write_text(
        json.dumps({"info": {"name": "elsewindow", "version": VERSION}, "urls": urls}),
        encoding="utf-8",
    )
    return dist, metadata


def _verify_pypi(dist: Path, metadata: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_pypi_release.py"),
            "--version",
            VERSION,
            "--dist-dir",
            str(dist),
            "--metadata-file",
            str(metadata),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_pypi_verifier_accepts_exact_complete_state_only(tmp_path: Path) -> None:
    dist, metadata = _pypi_fixture(tmp_path)
    exact = _verify_pypi(dist, metadata)
    assert exact.returncode == 0, exact.stderr
    assert exact.stdout.count(" matches PyPI (") == 2

    (dist / f"elsewindow-{VERSION}.tar.gz").write_bytes(b"conflict")
    conflict = _verify_pypi(dist, metadata)
    assert conflict.returncode == 1
    assert "does not match PyPI" in conflict.stderr

    dist, metadata = _pypi_fixture(tmp_path / "yanked")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["urls"][0]["yanked"] = True
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    yanked = _verify_pypi(dist, metadata)
    assert yanked.returncode == 1
    assert "is yanked" in yanked.stderr


def test_release_recovery_handles_absent_partial_complete_and_conflict() -> None:
    helper = _module("release_inventory")
    local = {"a": (1, "a" * 64), "b": (2, "b" * 64)}
    assert helper.recovery_plan(local, None, published=False) == ("a", "b")
    assert helper.recovery_plan(local, {"a": local["a"]}, published=False) == ("b",)
    assert helper.recovery_plan(local, local.copy(), published=False) == ()
    assert helper.recovery_plan(local, local.copy(), published=True) == ()
    with pytest.raises(helper.ReleaseInventoryError, match="incomplete"):
        helper.recovery_plan(local, {"a": local["a"]}, published=True)
    with pytest.raises(helper.ReleaseInventoryError, match="conflicts"):
        helper.recovery_plan(local, {"a": (99, "c" * 64)}, published=False)
