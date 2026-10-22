#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Read the Xpra fork release pinned for every Elsewindow test gate."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import install_xpra_release as installer

PIN_FILE: Final = ROOT / "XPRA_TEST_RELEASE"
MAX_PIN_BYTES: Final = 4096


def pinned_release_name(path: Path = PIN_FILE) -> str:
    """Return the single validated release name or tag from the pin file."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise installer.ReleaseError(f"cannot read {path.name}") from error
    if len(data) > MAX_PIN_BYTES:
        raise installer.ReleaseError(f"{path.name} is too large")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise installer.ReleaseError(f"{path.name} is not UTF-8") from error
    values = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(values) != 1:
        raise installer.ReleaseError(
            f"{path.name} must contain exactly one release name or tag"
        )
    return installer.release_selector(values[0])


if __name__ == "__main__":
    try:
        print(pinned_release_name())
    except installer.ReleaseError as error:
        print(f"xpra test release: {error}", file=sys.stderr)
        raise SystemExit(2) from error
