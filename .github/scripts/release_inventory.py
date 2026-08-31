# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Validate local release bytes and fail-closed partial recovery decisions."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA256 = re.compile(r"[0-9a-f]{64}")
ArtifactFact = tuple[int, str]


class ReleaseInventoryError(ValueError):
    """A local or existing release inventory is conflicting."""


def expected_names(version: str) -> set[str]:
    return {
        f"elsewindow-{version}-py3-none-any.whl",
        f"elsewindow-{version}.tar.gz",
        "elsewindow-linux-amd64",
        "elsewindow-linux-arm64",
        "SHA256SUMS.txt",
    }


def recovery_plan(
    local: dict[str, ArtifactFact],
    existing: dict[str, ArtifactFact] | None,
    *,
    published: bool,
) -> tuple[str, ...]:
    """Return missing draft assets or reject any conflicting state."""
    if existing is None:
        if published:
            raise ReleaseInventoryError("published release metadata is missing")
        return tuple(sorted(local))
    unexpected = set(existing) - set(local)
    if unexpected:
        raise ReleaseInventoryError(
            f"existing release has unexpected assets: {sorted(unexpected)}"
        )
    for name, fact in existing.items():
        if local[name] != fact:
            raise ReleaseInventoryError(f"existing release asset conflicts: {name}")
    missing = tuple(sorted(set(local) - set(existing)))
    if published and missing:
        raise ReleaseInventoryError(
            f"published release is incomplete: {', '.join(missing)}"
        )
    return missing


def local_facts(directory: Path, *, version: str) -> dict[str, ArtifactFact]:
    """Validate the complete release inventory and checksum contents."""
    expected = expected_names(version)
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected:
        raise ReleaseInventoryError(
            f"local release inventory {sorted(actual)} != {sorted(expected)}"
        )
    facts = {
        name: (
            (directory / name).stat().st_size,
            hashlib.sha256((directory / name).read_bytes()).hexdigest(),
        )
        for name in expected
    }
    if any(size <= 0 for size, _digest in facts.values()):
        raise ReleaseInventoryError("local release contains an empty artifact")
    checksums: dict[str, str] = {}
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or SHA256.fullmatch(digest) is None
            or name in checksums
            or name == "SHA256SUMS.txt"
        ):
            raise ReleaseInventoryError(f"malformed checksum line: {line!r}")
        checksums[name] = digest
    release_files = expected - {"SHA256SUMS.txt"}
    if set(checksums) != release_files:
        raise ReleaseInventoryError("checksum inventory differs from release files")
    for name in release_files:
        if checksums[name] != facts[name][1]:
            raise ReleaseInventoryError(f"checksum mismatch for {name}")
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--directory", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        version = (arguments.root / ".version").read_text(encoding="utf-8").strip()
        facts = local_facts(arguments.directory.resolve(), version=version)
    except (OSError, UnicodeError, ReleaseInventoryError) as error:
        print(f"release inventory failed: {error}", file=sys.stderr)
        return 1
    for name, (size, digest) in sorted(facts.items()):
        print(f"{digest}  {size}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
