"""CLI entry point for the disposable remote Xpra Podman topology."""

# ruff: noqa: I001

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.live_support.application import main


if __name__ == "__main__":
    raise SystemExit(main())
