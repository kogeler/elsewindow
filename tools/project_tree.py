# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Enumerate maintained files from the current filesystem tree."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_GENERATED_DIRECTORIES = frozenset(
    {
        ".artifacts",
        ".agents",
        ".codex",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "htmlcov",
        "site",
        "tmp",
        "venv-docs",
        "venv-package",
        "venv-quality",
        "venv-runtime",
        "venv-standalone",
        "venv-test",
    }
)
GENERATED_DIRECTORY_NAMES = frozenset({"__pycache__"})
ROOT_GENERATED_FILES = frozenset({".coverage", "coverage.xml"})
GENERATED_SUFFIXES = frozenset({".pyc", ".pyo"})


class ProjectTreeError(ValueError):
    """The project tree contains an unsafe maintained entry."""


def _excluded_directory(relative: Path) -> bool:
    return (
        relative.name in GENERATED_DIRECTORY_NAMES
        or (len(relative.parts) == 1 and relative.name in ROOT_GENERATED_DIRECTORIES)
        or relative.name.endswith(".egg-info")
    )


def _excluded_file(relative: Path) -> bool:
    return (
        (len(relative.parts) == 1 and relative.name in ROOT_GENERATED_FILES)
        or relative.name.startswith(".coverage.")
        or relative.suffix in GENERATED_SUFFIXES
    )


def project_files(root: Path) -> tuple[Path, ...]:
    """Return every maintained regular file without consulting Git metadata."""

    root = root.resolve()
    if not root.is_dir():
        raise ProjectTreeError(f"project root is not a directory: {root}")
    selected: list[Path] = []
    for directory, names, filenames in os.walk(root, topdown=True):
        current = Path(directory)
        kept_directories: list[str] = []
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root)
            if _excluded_directory(relative):
                continue
            if path.is_symlink():
                raise ProjectTreeError(f"maintained directory is a symlink: {relative}")
            kept_directories.append(name)
        names[:] = kept_directories
        for name in sorted(filenames):
            path = current / name
            relative = path.relative_to(root)
            if _excluded_file(relative):
                continue
            if path.is_symlink():
                raise ProjectTreeError(f"maintained file is a symlink: {relative}")
            if not path.is_file():
                raise ProjectTreeError(f"maintained entry is not a file: {relative}")
            selected.append(path)
    return tuple(sorted(selected, key=lambda path: path.relative_to(root).as_posix()))
