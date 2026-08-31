# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Static evidence for Elsewindow's public package identity."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import elsewindow

ROOT = Path(__file__).resolve().parents[1]


def test_public_metadata_has_one_exact_elsewindow_identity() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = document["project"]

    assert project["name"] == "elsewindow"
    assert project["dynamic"] == ["version"]
    assert "version" not in project
    assert project["description"] == (
        "Run one remote Linux GUI application through Xpra over one owned "
        "OpenSSH master"
    )
    assert project["readme"] == {
        "file": "README.md",
        "content-type": "text/markdown",
    }
    assert project["requires-python"] == ">=3.13,<3.15"
    assert project["authors"] == project["maintainers"] == [{"name": "kogeler"}]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["dependencies"] == ["ssh-wrapper==0.1.0"]
    assert project["scripts"] == {"elsewindow": "elsewindow.cli:main"}
    assert project["urls"] == {
        "Homepage": "https://kogeler.github.io/elsewindow/",
        "Documentation": "https://kogeler.github.io/elsewindow/",
        "Repository": "https://github.com/kogeler/elsewindow",
        "Issues": "https://github.com/kogeler/elsewindow/issues",
        "Changelog": "https://github.com/kogeler/elsewindow/blob/main/CHANGELOG.md",
    }


def test_linux_python_typing_and_package_data_are_exact() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = document["project"]["classifiers"]

    assert "Operating System :: POSIX :: Linux" in classifiers
    assert "Programming Language :: Python :: 3.13" in classifiers
    assert "Programming Language :: Python :: 3.14" in classifiers
    assert "Programming Language :: Python :: Implementation :: CPython" in classifiers
    assert "Typing :: Typed" in classifiers
    assert not any(
        platform in classifier.casefold()
        for classifier in classifiers
        for platform in ("windows", "macos")
    )
    assert document["tool"]["setuptools"]["packages"]["find"] == {
        "include": ["elsewindow*"]
    }
    assert document["tool"]["setuptools"]["package-data"] == {
        "elsewindow": ["live-cli.yml", "profiles.yml", "py.typed"]
    }
    assert document["tool"]["setuptools"]["dynamic"]["version"] == {"file": ".version"}
    assert (ROOT / "elsewindow/py.typed").read_bytes() in {b"", b"\n"}


def test_version_resolves_from_the_single_source() -> None:
    version = (ROOT / ".version").read_text(encoding="utf-8").strip()

    assert re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    assert version == "0.1.0"
    assert elsewindow.__version__ == version
    assert f"## [{version}] - " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_sdist_manifest_excludes_nonproduct_trees() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    for directory in (
        ".github",
        "bin",
        "containers",
        "doc",
        "tests",
        "tmp",
        "tools",
    ):
        assert f"prune {directory}\n" in manifest
    assert "recursive-include elsewindow *.py *.yml py.typed" in manifest
