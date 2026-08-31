# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Focused evidence for archive, checksum, and standalone helpers."""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools.build_standalone import (
    StandaloneBuildError,
    standalone_architecture,
    standalone_name,
)
from tools.checksums import ChecksumError, expected_names, write
from tools.checksums import verify as verify_checksums
from tools.normalize_sdist import normalize as normalize_sdist
from tools.normalize_wheel import normalize as normalize_wheel


def _sdist(path: Path, *, timestamp: int, owner: int) -> None:
    with (
        path.open("wb") as raw,
        gzip.GzipFile(
            filename="source", mode="wb", fileobj=raw, mtime=timestamp
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target,
    ):
        directory = tarfile.TarInfo("package/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o775
        directory.mtime = timestamp
        directory.uid = directory.gid = owner
        directory.uname = directory.gname = str(owner)
        directory.pax_headers = {"mtime": f"{timestamp}.25"}
        target.addfile(directory)
        data = b"same contents\n"
        member = tarfile.TarInfo("package/module.py")
        member.mode = 0o664
        member.size = len(data)
        member.mtime = timestamp
        member.uid = member.gid = owner
        target.addfile(member, io.BytesIO(data))


def _wheel(
    path: Path, *, timestamp: tuple[int, int, int, int, int, int], mode: int
) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        item = zipfile.ZipInfo("package/module.py", timestamp)
        item.create_system = 3
        item.external_attr = mode << 16
        archive.writestr(item, b"same contents\n")


def test_normalization_makes_equivalent_archives_byte_identical(tmp_path: Path) -> None:
    epoch = 1_600_000_000
    first_sdist = tmp_path / "first.tar.gz"
    second_sdist = tmp_path / "second.tar.gz"
    _sdist(first_sdist, timestamp=1_700_000_001, owner=1000)
    _sdist(second_sdist, timestamp=1_800_000_002, owner=2000)
    normalize_sdist(first_sdist, epoch=epoch)
    normalize_sdist(second_sdist, epoch=epoch)
    assert first_sdist.read_bytes() == second_sdist.read_bytes()

    first_wheel = tmp_path / "first.whl"
    second_wheel = tmp_path / "second.whl"
    _wheel(first_wheel, timestamp=(2025, 1, 1, 0, 0, 0), mode=0o664)
    _wheel(second_wheel, timestamp=(2026, 2, 2, 2, 2, 2), mode=0o600)
    normalize_wheel(first_wheel, epoch=epoch)
    normalize_wheel(second_wheel, epoch=epoch)
    assert first_wheel.read_bytes() == second_wheel.read_bytes()


def test_normalizers_reject_pre_zip_epoch(tmp_path: Path) -> None:
    sdist = tmp_path / "package.tar.gz"
    wheel = tmp_path / "package.whl"
    _sdist(sdist, timestamp=1_700_000_001, owner=1000)
    _wheel(wheel, timestamp=(2025, 1, 1, 0, 0, 0), mode=0o644)
    with pytest.raises(ValueError, match="1980"):
        normalize_sdist(sdist, epoch=1)
    with pytest.raises(ValueError, match="1980"):
        normalize_wheel(wheel, epoch=1)


def test_checksum_inventory_is_exact_and_self_verifying(tmp_path: Path) -> None:
    version = "1.2.3"
    for name in expected_names(version):
        (tmp_path / name).write_bytes(name.encode())
    output = write(tmp_path, version=version)
    verify_checksums(tmp_path, version=version)
    first = output.read_bytes()
    write(tmp_path, version=version)
    assert output.read_bytes() == first
    (tmp_path / "unexpected").write_bytes(b"unexpected")
    with pytest.raises(ChecksumError, match="inventory"):
        write(tmp_path, version=version)


@pytest.mark.parametrize(
    ("machine", "expected"),
    (("x86_64", "amd64"), ("AMD64", "amd64"), ("aarch64", "arm64"), ("ARM64", "arm64")),
)
def test_standalone_architecture_and_linux_names(machine: str, expected: str) -> None:
    assert standalone_architecture(machine) == expected
    assert standalone_name("linux", machine) == f"elsewindow-linux-{expected}"


def test_standalone_rejects_foreign_platforms_and_architectures() -> None:
    with pytest.raises(StandaloneBuildError, match="unsupported standalone target"):
        standalone_name("win32", "x86_64")
    with pytest.raises(
        StandaloneBuildError, match="unsupported standalone architecture"
    ):
        standalone_architecture("mips64")
