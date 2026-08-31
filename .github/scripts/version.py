# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Validate and render stable release metadata owned by ``.version``."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPOSITORY = "kogeler/elsewindow"
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class VersionError(ValueError):
    """Release metadata violates the repository contract."""


@dataclass(frozen=True, order=True)
class Version:
    """A strict stable semantic version."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str, *, source: str) -> Version:
        """Parse one canonical X.Y.Z value."""
        match = VERSION_PATTERN.fullmatch(value.strip())
        if match is None:
            raise VersionError(
                f"{source} must contain exactly one stable X.Y.Z version"
            )
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def read_version(root: Path) -> Version:
    """Read the human-maintained version."""
    try:
        return Version.parse(
            (root / ".version").read_text(encoding="utf-8"), source=".version"
        )
    except OSError as error:
        raise VersionError(f"cannot read .version: {error}") from error


def require_dynamic_metadata(root: Path) -> None:
    """Require setuptools to read exactly the root version file."""
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = document["project"]
        dynamic = document["tool"]["setuptools"]["dynamic"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise VersionError(f"cannot read dynamic version metadata: {error}") from error
    if not isinstance(project, dict) or project.get("dynamic") != ["version"]:
        raise VersionError("pyproject.toml must declare only a dynamic project version")
    if "version" in project:
        raise VersionError("pyproject.toml must not contain a static project.version")
    if not isinstance(dynamic, dict) or dynamic.get("version") != {"file": ".version"}:
        raise VersionError("setuptools dynamic version must read only .version")


def changelog_body(root: Path, version: Version) -> str:
    """Extract exactly one non-empty dated changelog section."""
    try:
        content = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as error:
        raise VersionError(f"cannot read CHANGELOG.md: {error}") from error
    heading = re.compile(
        rf"^## \[{re.escape(str(version))}\] - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$",
        re.MULTILINE,
    )
    matches = list(heading.finditer(content))
    if len(matches) != 1:
        raise VersionError(f"CHANGELOG.md must contain one dated [{version}] section")
    start = matches[0].end()
    following = re.search(r"^## ", content[start:], re.MULTILINE)
    end = start + following.start() if following else len(content)
    body = content[start:end].strip()
    if not body or re.search(r"^- ", body, re.MULTILINE) is None:
        raise VersionError(f"CHANGELOG.md section {version} has no release entries")
    return body


def validate(root: Path) -> Version:
    """Validate dynamic ownership and the release-note source."""
    version = read_version(root)
    require_dynamic_metadata(root)
    changelog_body(root, version)
    return version


def require_increment(
    current: Version, base: Version, unpublished: Version | None
) -> None:
    """Require an increase while allowing recovery of one unpublished version."""
    effective = unpublished if current == base and unpublished is not None else base
    if current <= effective:
        raise VersionError(
            f".version {current} must be greater than base version {effective}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--base-version")
    check.add_argument("--unpublished-base-version")
    notes = commands.add_parser("notes")
    notes.add_argument("--output", type=Path, required=True)
    notes.add_argument("--repository", default=DEFAULT_REPOSITORY)
    commands.add_parser("current")
    return parser


def _run(arguments: argparse.Namespace) -> None:
    root = arguments.root.resolve()
    if arguments.command == "current":
        print(read_version(root))
        return
    version = validate(root)
    if arguments.command == "check":
        if arguments.unpublished_base_version and not arguments.base_version:
            raise VersionError("--unpublished-base-version requires --base-version")
        if arguments.base_version:
            base = Version.parse(arguments.base_version, source="--base-version")
            unpublished = (
                Version.parse(
                    arguments.unpublished_base_version,
                    source="--unpublished-base-version",
                )
                if arguments.unpublished_base_version
                else None
            )
            require_increment(version, base, unpublished)
        print(version)
        return
    if arguments.command == "notes":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", arguments.repository):
            raise VersionError("--repository must be an owner/name pair")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            f"{changelog_body(root, version)}\n\nFull changelog: "
            f"https://github.com/{arguments.repository}/blob/v{version}/CHANGELOG.md\n",
            encoding="utf-8",
        )
        print(version)
        return
    raise AssertionError(arguments.command)


def main() -> int:
    """Run the helper and return a process status."""
    try:
        _run(_parser().parse_args())
    except (OSError, VersionError) as error:
        print(f"version error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
