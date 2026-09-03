# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Require equivalent clean source trees to produce byte-identical archives."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

if __package__:
    from .build_distributions import BuildError, build
else:
    from build_distributions import BuildError, build

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    ".version",
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "pyproject.toml",
    "requirements.in",
)


class ReproducibilityError(ValueError):
    """Equivalent clean trees produced different release bytes."""


def _copy_source(source: Path, destination: Path, *, mtime: int) -> None:
    destination.mkdir()
    for name in ROOT_FILES:
        shutil.copy2(source / name, destination / name)
    shutil.copytree(source / "elsewindow", destination / "elsewindow")
    for path in (destination, *destination.rglob("*")):
        os.utime(path, (mtime, mtime), follow_symlinks=False)


def verify(*, root: Path, python: Path, epoch: int) -> dict[str, str]:
    """Return exact output digests after two independent clean builds."""
    with tempfile.TemporaryDirectory(prefix="elsewindow-reproducible-") as raw:
        base = Path(raw)
        trees = (base / "first", base / "second")
        _copy_source(root, trees[0], mtime=epoch + 101)
        _copy_source(root, trees[1], mtime=epoch + 202)
        outputs: list[dict[str, bytes]] = []
        for tree in trees:
            wheel, sdist = build(
                root=tree, python=python, output=tree / "dist", epoch=epoch
            )
            outputs.append(
                {wheel.name: wheel.read_bytes(), sdist.name: sdist.read_bytes()}
            )
        if outputs[0] != outputs[1]:
            changed = sorted(
                name
                for name in set(outputs[0]) | set(outputs[1])
                if outputs[0].get(name) != outputs[1].get(name)
            )
            raise ReproducibilityError(
                "equivalent builds differ: " + ", ".join(changed)
            )
        return {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(outputs[0].items())
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    try:
        digests = verify(
            root=arguments.root.resolve(),
            python=arguments.python.absolute(),
            epoch=arguments.epoch,
        )
    except (BuildError, OSError, ReproducibilityError) as error:
        print(f"reproducibility check failed: {error}", file=sys.stderr)
        return 1
    for name, digest in digests.items():
        print(f"{digest}  {name}")
    print("Equivalent clean source trees produced byte-identical distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
