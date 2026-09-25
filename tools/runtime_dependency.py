# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Read the runtime dependency identity from its only maintained version input."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PIN = re.compile(r"ssh-wrapper==([0-9][A-Za-z0-9.!+_-]*)")


def runtime_version(root: Path = ROOT) -> str:
    """Read current bytes, without caching or consulting generated metadata."""
    try:
        lines = (root / "requirements.in").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError("cannot read the runtime requirements input") from error
    pins = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    match = RUNTIME_PIN.fullmatch(pins[0]) if len(pins) == 1 else None
    if match is None:
        raise ValueError("requirements.in must contain one exact ssh-wrapper pin")
    return match.group(1)


def check_installed(root: Path = ROOT) -> None:
    """Check only the dependency in the invoking prepared interpreter."""
    expected = runtime_version(root)
    if importlib.metadata.version("ssh-wrapper") != expected:
        raise ValueError("installed ssh-wrapper differs from requirements.in")
    importlib.import_module("ssh_wrapper")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check-installed", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.check_installed:
            check_installed(arguments.root)
        else:
            print(runtime_version(arguments.root))
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        print(
            "runtime dependency check failed; inspect requirements.in and the prepared environment",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
