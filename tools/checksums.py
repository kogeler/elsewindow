# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Write and verify the exact SHA-256 inventory for one release."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"[0-9a-f]{64}")


class ChecksumError(ValueError):
    """A checksum inventory is missing, malformed, or unexpected."""


def expected_names(version: str) -> set[str]:
    """Return the complete GitHub Release artifact inventory."""
    return {
        f"elsewindow-{version}-py3-none-any.whl",
        f"elsewindow-{version}.tar.gz",
        "elsewindow-linux-amd64",
        "elsewindow-linux-arm64",
    }


def write(directory: Path, *, version: str) -> Path:
    """Write checksums for exactly the expected release artifacts."""
    names = expected_names(version)
    actual = {
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    if actual != names:
        raise ChecksumError(f"release inventory {sorted(actual)} != {sorted(names)}")
    output = directory / "SHA256SUMS.txt"
    output.write_text(
        "".join(
            f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            for name in sorted(names)
        ),
        encoding="ascii",
    )
    output.chmod(0o644)
    return output


def verify(directory: Path, *, version: str) -> None:
    """Verify the checksum file and complete local release inventory."""
    path = directory / "SHA256SUMS.txt"
    expected = expected_names(version)
    actual = {
        candidate.name for candidate in directory.iterdir() if candidate.is_file()
    }
    if actual != expected | {path.name}:
        raise ChecksumError(
            f"release inventory {sorted(actual)} != {sorted(expected | {path.name})}"
        )
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or SHA256.fullmatch(digest) is None or not name:
            raise ChecksumError(f"malformed checksum line: {line!r}")
        if name in parsed:
            raise ChecksumError(f"duplicate checksum entry: {name}")
        parsed[name] = digest
    if set(parsed) != expected:
        raise ChecksumError("checksum entries differ from the release inventory")
    for name, digest in parsed.items():
        candidate = directory / name
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            raise ChecksumError(f"checksum mismatch for {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    try:
        version = (arguments.root / ".version").read_text(encoding="utf-8").strip()
        output = write(arguments.directory.resolve(), version=version)
        verify(arguments.directory.resolve(), version=version)
    except (ChecksumError, OSError) as error:
        print(f"checksum error: {error}", file=sys.stderr)
        return 1
    print(output.read_text(encoding="ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
