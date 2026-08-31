# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Normalize a pure-Python wheel's order, timestamps, modes, and headers."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe wheel member: {name}")


def normalize(path: Path, *, epoch: int) -> None:
    """Rewrite one wheel with canonical regular-file ZIP metadata."""
    if epoch < 315_532_800:
        raise ValueError("epoch must be at or after 1980-01-01")
    timestamp = dt.datetime.fromtimestamp(epoch - (epoch % 2), tz=dt.UTC).timetuple()[
        :6
    ]
    with zipfile.ZipFile(path) as source:
        names: set[str] = set()
        contents: dict[str, bytes] = {}
        for item in source.infolist():
            _safe_name(item.filename)
            if item.is_dir():
                raise ValueError(f"wheel contains a directory entry: {item.filename}")
            if item.filename in names:
                raise ValueError(f"duplicate wheel member: {item.filename}")
            if item.flag_bits & 0x1:
                raise ValueError(f"encrypted wheel member: {item.filename}")
            names.add(item.filename)
            contents[item.filename] = source.read(item)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as raw:
            temporary = Path(raw.name)
        with zipfile.ZipFile(
            temporary, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as target:
            for name in sorted(contents):
                item = zipfile.ZipInfo(name, timestamp)
                item.compress_type = zipfile.ZIP_DEFLATED
                item.create_system = 3
                item.external_attr = 0o100644 << 16
                target.writestr(item, contents[name])
        os.chmod(temporary, 0o644)
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("archive", type=Path)
    arguments = parser.parse_args()
    try:
        normalize(arguments.archive, epoch=arguments.epoch)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"wheel normalization failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
