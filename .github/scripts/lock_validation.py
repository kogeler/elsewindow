# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Validate every generated hash lock against its exact PEP 621 owner."""

from __future__ import annotations

import sys
from pathlib import Path

from dependency_snapshot import LOCK_NAMES, SnapshotError, build_manifests

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def validate(root: Path) -> dict[str, int]:
    manifests = build_manifests(root)
    if tuple(manifests) != LOCK_NAMES:
        raise SnapshotError("dependency manifest order or inventory differs")
    runtime = manifests["requirements.txt"]["resolved"]
    if not isinstance(runtime, dict):
        raise SnapshotError("runtime manifest is malformed")
    wrapper = runtime.get("ssh-wrapper")
    if (
        not isinstance(wrapper, dict)
        or wrapper.get("package_url") != "pkg:pypi/ssh-wrapper@0.1.0"
        or wrapper.get("relationship") != "direct"
        or wrapper.get("scope") != "runtime"
    ):
        raise SnapshotError("runtime lock must contain exact ssh-wrapper==0.1.0")
    for name in LOCK_NAMES[1:]:
        resolved = manifests[name]["resolved"]
        if not isinstance(resolved, dict) or not set(runtime).issubset(resolved):
            raise SnapshotError(f"runtime lock is not a subset of {name}")
        for package, dependency in runtime.items():
            if resolved[package]["package_url"] != dependency["package_url"]:
                raise SnapshotError(f"{name} changes runtime pin: {package}")
    return {
        name: len(manifest["resolved"])
        for name, manifest in manifests.items()
        if isinstance(manifest["resolved"], dict)
    }


def main() -> int:
    try:
        counts = validate(DEFAULT_ROOT)
    except SnapshotError as error:
        print(f"lock validation error: {error}", file=sys.stderr)
        return 1
    print(
        "locks validated: "
        + ", ".join(f"{name}={count}" for name, count in counts.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
