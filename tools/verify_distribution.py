# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Verify exact wheel and sdist inventories, metadata, and normalized bytes."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import io
import os
import re
import struct
import sys
import tarfile
import tomllib
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "live-cli.yml",
    "live_config.py",
    "profiles.yml",
    "py.typed",
    "session.py",
}
PROJECT_URLS = {
    "Homepage, https://kogeler.github.io/elsewindow/",
    "Documentation, https://kogeler.github.io/elsewindow/",
    "Repository, https://github.com/kogeler/elsewindow",
    "Issues, https://github.com/kogeler/elsewindow/issues",
    "Changelog, https://github.com/kogeler/elsewindow/blob/main/CHANGELOG.md",
}
EXTRAS = {"quality", "test", "package", "standalone", "docs"}
FORBIDDEN_BYTES = (
    b"remote" + b"_xpra",
    b"remote-" + b"xpra-run",
    b"joplin_" + b"md_sync",
    b"joplin-" + b"md-sync",
)


class DistributionError(ValueError):
    """A built archive violates the public distribution contract."""


def safe_parts(name: str) -> tuple[str, ...]:
    """Return safe relative POSIX path parts."""
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DistributionError(f"unsafe archive member: {name}")
    return path.parts


def _requirement_key(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _metadata(
    data: bytes, *, version: str, readme: str, project: dict[str, object]
) -> Message:
    message = BytesParser(policy=policy.default).parsebytes(data)
    expected = {
        "Metadata-Version": "2.4",
        "Name": "elsewindow",
        "Version": version,
        "Summary": (
            "Run one remote Linux GUI application through Xpra over one owned "
            "OpenSSH master"
        ),
        "Author": "kogeler",
        "Maintainer": "kogeler",
        "License-Expression": "MIT",
        "Requires-Python": "<3.15,>=3.13",
        "Description-Content-Type": "text/markdown",
        "License-File": "LICENSE",
    }
    for key, value in expected.items():
        if message.get(key) != value:
            raise DistributionError(
                f"metadata {key}={message.get(key)!r}, expected {value!r}"
            )
    if set(message.get_all("Project-URL", [])) != PROJECT_URLS:
        raise DistributionError("metadata project URLs differ")
    keywords = project.get("keywords")
    classifiers = project.get("classifiers")
    if not isinstance(keywords, list) or not all(
        isinstance(item, str) for item in keywords
    ):
        raise DistributionError("source keywords are invalid")
    if not isinstance(classifiers, list) or not all(
        isinstance(item, str) for item in classifiers
    ):
        raise DistributionError("source classifiers are invalid")
    if message.get("Keywords") != ",".join(keywords):
        raise DistributionError("metadata keywords differ")
    if message.get_all("Classifier", []) != classifiers:
        raise DistributionError("metadata classifiers differ")
    if set(message.get_all("Provides-Extra", [])) != EXTRAS:
        raise DistributionError("metadata optional dependency audiences differ")
    dependencies = project.get("dependencies")
    optional = project.get("optional-dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(requirement, str) for requirement in dependencies
    ):
        raise DistributionError("source runtime dependencies are invalid")
    if not isinstance(optional, dict) or set(optional) != EXTRAS:
        raise DistributionError("source optional dependencies are invalid")
    expected_requirements = list(dependencies)
    for extra, requirements in optional.items():
        if (
            not isinstance(extra, str)
            or not isinstance(requirements, list)
            or not all(isinstance(requirement, str) for requirement in requirements)
        ):
            raise DistributionError("source optional dependencies are invalid")
        expected_requirements.extend(
            f'{requirement}; extra == "{extra}"' for requirement in requirements
        )
    actual_requirements = message.get_all("Requires-Dist", [])
    if len(actual_requirements) != len(expected_requirements) or {
        _requirement_key(requirement) for requirement in actual_requirements
    } != {_requirement_key(requirement) for requirement in expected_requirements}:
        raise DistributionError("metadata requirements differ")
    if message.get_all("Dynamic", []) != ["license-file"]:
        raise DistributionError("metadata dynamic fields differ")
    payload = message.get_payload()
    if not isinstance(payload, str) or payload.strip() != readme.strip():
        raise DistributionError("metadata long description differs from README.md")
    return message


def _forbidden(data: bytes, *, root: Path, source: str) -> None:
    for marker in (*FORBIDDEN_BYTES, os.fsencode(root.resolve())):
        if marker and marker in data:
            raise DistributionError(f"private or foreign marker appears in {source}")


def verify_wheel(path: Path, *, root: Path, version: str, epoch: int) -> None:
    """Verify the pure wheel, metadata, resources, typing, and RECORD."""
    dist_info = f"elsewindow-{version}.dist-info"
    expected_files = {
        *(f"elsewindow/{name}" for name in PACKAGE_FILES),
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/top_level.txt",
        f"{dist_info}/RECORD",
        f"{dist_info}/licenses/LICENSE",
    }
    expected_timestamp = dt.datetime.fromtimestamp(
        epoch - (epoch % 2), tz=dt.UTC
    ).timetuple()[:6]
    with zipfile.ZipFile(path) as archive:
        items = archive.infolist()
        names = [item.filename for item in items]
        if len(names) != len(set(names)) or names != sorted(names):
            raise DistributionError("wheel members are duplicate or not sorted")
        if set(names) != expected_files:
            raise DistributionError(f"wheel inventory differs: {sorted(names)}")
        for item in items:
            safe_parts(item.filename)
            if item.is_dir() or item.create_system != 3:
                raise DistributionError(f"wheel member type differs: {item.filename}")
            if (item.external_attr >> 16) & 0o777 != 0o644:
                raise DistributionError(f"wheel mode differs: {item.filename}")
            if item.date_time != expected_timestamp:
                raise DistributionError(f"wheel timestamp differs: {item.filename}")
            _forbidden(archive.read(item), root=root, source=item.filename)
        project_document = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = project_document.get("project")
        if not isinstance(project, dict):
            raise DistributionError("source project metadata is missing")
        _metadata(
            archive.read(f"{dist_info}/METADATA"),
            version=version,
            readme=(root / "README.md").read_text(encoding="utf-8"),
            project=project,
        )
        for resource in ("live-cli.yml", "profiles.yml"):
            if (
                archive.read(f"elsewindow/{resource}")
                != (root / "elsewindow" / resource).read_bytes()
            ):
                raise DistributionError(f"wheel {resource} differs from source")
        if archive.read("elsewindow/py.typed") not in {b"", b"\n"}:
            raise DistributionError("py.typed must be empty")
        if (
            archive.read(f"{dist_info}/licenses/LICENSE")
            != (root / "LICENSE").read_bytes()
        ):
            raise DistributionError("wheel license differs")
        if archive.read(f"{dist_info}/top_level.txt") != b"elsewindow\n":
            raise DistributionError("wheel top-level package differs")
        if archive.read(f"{dist_info}/entry_points.txt") != (
            b"[console_scripts]\nelsewindow = elsewindow.cli:main\n"
        ):
            raise DistributionError("wheel console entry point differs")
        build_requires = project_document.get("build-system", {}).get("requires")
        setuptools = (
            next(
                (
                    requirement.partition("==")[2]
                    for requirement in build_requires
                    if isinstance(requirement, str)
                    and requirement.startswith("setuptools==")
                ),
                None,
            )
            if isinstance(build_requires, list)
            else None
        )
        expected_wheel = (
            "Wheel-Version: 1.0\n"
            f"Generator: setuptools ({setuptools})\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n\n"
        )
        if (
            setuptools is None
            or archive.read(f"{dist_info}/WHEEL").decode() != expected_wheel
        ):
            raise DistributionError("wheel generator or tag differs")
        record_path = f"{dist_info}/RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_path).decode())))
        if (
            len(rows) != len(expected_files)
            or {row[0] for row in rows} != expected_files
        ):
            raise DistributionError("wheel RECORD inventory differs")
        for name, digest, size in rows:
            if name == record_path:
                if digest or size:
                    raise DistributionError("wheel RECORD self-entry is hashed")
                continue
            data = archive.read(name)
            encoded = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            if digest != f"sha256={encoded}" or size != str(len(data)):
                raise DistributionError(f"wheel RECORD mismatch for {name}")


def verify_sdist(path: Path, *, root: Path, version: str, epoch: int) -> None:
    """Verify the normalized source archive and exact file inventory."""
    archive_root = f"elsewindow-{version}"
    egg_info = {
        "elsewindow.egg-info/PKG-INFO",
        "elsewindow.egg-info/SOURCES.txt",
        "elsewindow.egg-info/dependency_links.txt",
        "elsewindow.egg-info/entry_points.txt",
        "elsewindow.egg-info/requires.txt",
        "elsewindow.egg-info/top_level.txt",
    }
    expected_files = {
        ".version",
        "CHANGELOG.md",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
        "setup.cfg",
        *(f"elsewindow/{name}" for name in PACKAGE_FILES),
        *egg_info,
    }
    raw = path.read_bytes()
    if (
        len(raw) < 10
        or raw[:2] != b"\x1f\x8b"
        or struct.unpack("<I", raw[4:8])[0] != epoch
    ):
        raise DistributionError("sdist gzip timestamp differs")
    if raw[3] & 0x08:
        raise DistributionError("sdist gzip header embeds a filename")
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or names != sorted(names):
            raise DistributionError("sdist members are duplicate or not sorted")
        files: dict[str, bytes] = {}
        directories: set[str] = set()
        for member in members:
            parts = safe_parts(member.name)
            if (
                parts[0] != archive_root
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise DistributionError(f"sdist member is unsafe: {member.name}")
            if member.uid or member.gid or member.uname or member.gname:
                raise DistributionError(f"sdist ownership differs: {member.name}")
            if member.mtime != epoch or any(
                key in member.pax_headers for key in ("atime", "ctime", "mtime")
            ):
                raise DistributionError(f"sdist timestamp differs: {member.name}")
            relative = "/".join(parts[1:])
            if member.isdir():
                if member.mode != 0o755:
                    raise DistributionError(
                        f"sdist directory mode differs: {member.name}"
                    )
                directories.add(relative)
            elif member.isfile():
                if member.mode != 0o644:
                    raise DistributionError(f"sdist file mode differs: {member.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise DistributionError(f"cannot read sdist member: {member.name}")
                files[relative] = stream.read()
            else:
                raise DistributionError(f"sdist member type differs: {member.name}")
        if set(files) != expected_files:
            raise DistributionError(f"sdist inventory differs: {sorted(files)}")
        if directories != {"", "elsewindow", "elsewindow.egg-info"}:
            raise DistributionError(
                f"sdist directory inventory differs: {sorted(directories)}"
            )
        for relative, data in files.items():
            _forbidden(data, root=root, source=relative)
        for name in (
            ".version",
            "CHANGELOG.md",
            "LICENSE",
            "MANIFEST.in",
            "README.md",
            "pyproject.toml",
        ):
            if files[name] != (root / name).read_bytes():
                raise DistributionError(f"sdist {name} differs from source")
        for name in PACKAGE_FILES:
            if files[f"elsewindow/{name}"] != (root / "elsewindow" / name).read_bytes():
                raise DistributionError(f"sdist package file differs: {name}")
        if files["PKG-INFO"] != files["elsewindow.egg-info/PKG-INFO"]:
            raise DistributionError("sdist PKG-INFO copies differ")
        if files["elsewindow.egg-info/dependency_links.txt"] != b"\n":
            raise DistributionError("sdist dependency links differ")
        if files["elsewindow.egg-info/top_level.txt"] != b"elsewindow\n":
            raise DistributionError("sdist top-level package differs")
        if (
            files["elsewindow.egg-info/entry_points.txt"]
            != b"[console_scripts]\nelsewindow = elsewindow.cli:main\n"
        ):
            raise DistributionError("sdist console entry point differs")
        if files["setup.cfg"] != b"[egg_info]\ntag_build = \ntag_date = 0\n\n":
            raise DistributionError("sdist setup.cfg differs")
        actual_sources = files["elsewindow.egg-info/SOURCES.txt"].decode().splitlines()
        if len(actual_sources) != len(set(actual_sources)) or set(
            actual_sources
        ) != expected_files - {"PKG-INFO", "setup.cfg"}:
            raise DistributionError("sdist SOURCES.txt inventory differs")
        project = tomllib.loads(files["pyproject.toml"].decode()).get("project")
        if not isinstance(project, dict):
            raise DistributionError("sdist project metadata is missing")
        _metadata(
            files["PKG-INFO"],
            version=version,
            readme=(root / "README.md").read_text(encoding="utf-8"),
            project=project,
        )


def verify(directory: Path, *, root: Path, epoch: int) -> tuple[Path, Path]:
    """Verify exactly one wheel and one sdist."""
    version = (root / ".version").read_text(encoding="utf-8").strip()
    wheel = directory / f"elsewindow-{version}-py3-none-any.whl"
    sdist = directory / f"elsewindow-{version}.tar.gz"
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != {wheel.name, sdist.name}:
        raise DistributionError(f"distribution inventory differs: {sorted(actual)}")
    for path in (wheel, sdist):
        if not path.is_file() or path.stat().st_size <= 0:
            raise DistributionError(f"distribution is missing or empty: {path.name}")
        if path.stat().st_mode & 0o777 != 0o644:
            raise DistributionError(f"distribution mode differs: {path.name}")
    verify_wheel(wheel, root=root, version=version, epoch=epoch)
    verify_sdist(sdist, root=root, version=version, epoch=epoch)
    return wheel, sdist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    try:
        wheel, sdist = verify(
            arguments.directory.resolve(),
            root=arguments.root.resolve(),
            epoch=arguments.epoch,
        )
    except (OSError, tarfile.TarError, zipfile.BadZipFile, DistributionError) as error:
        print(f"distribution error: {error}", file=sys.stderr)
        return 1
    print(f"Verified {wheel.name} and {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
