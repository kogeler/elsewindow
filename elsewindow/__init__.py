# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Xpra application runner over one owned SSH master."""

from __future__ import annotations


def _resolve_version() -> str:
    """Resolve the source-tree or installed distribution version."""
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        if (root / "pyproject.toml").is_file():
            return (root / ".version").read_text(encoding="utf-8").strip()
        bundled = Path(__file__).resolve().parent / ".version"
        if bundled.is_file():
            return bundled.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("elsewindow")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()

__all__ = ["__version__"]
