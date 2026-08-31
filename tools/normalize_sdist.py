# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Normalize an sdist into a deterministic safe gzip tar archive."""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe sdist member: {name}")


def normalize(path: Path, *, epoch: int) -> None:
    """Rewrite one sdist with stable order, metadata, modes, and gzip header."""
    if epoch < 315_532_800:
        raise ValueError("epoch must be at or after 1980-01-01")
    with tarfile.open(path, mode="r:gz") as source:
        members = source.getmembers()
        names: set[str] = set()
        contents: dict[str, bytes] = {}
        for member in members:
            _safe_name(member.name)
            if member.name in names:
                raise ValueError(f"duplicate sdist member: {member.name}")
            names.add(member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsupported sdist member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"unsupported sdist member type: {member.name}")
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read sdist member: {member.name}")
                contents[member.name] = extracted.read()

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as raw:
            temporary = Path(raw.name)
            with (
                gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9
                ) as compressed,
                tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as target,
            ):
                for original in sorted(members, key=lambda item: item.name):
                    member = copy.copy(original)
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.mtime = epoch
                    member.mode = 0o755 if member.isdir() else 0o644
                    member.pax_headers = {
                        key: value
                        for key, value in member.pax_headers.items()
                        if key not in {"atime", "ctime", "mtime"}
                    }
                    data = (
                        io.BytesIO(contents[member.name]) if member.isfile() else None
                    )
                    target.addfile(member, data)
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
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"sdist normalization failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
