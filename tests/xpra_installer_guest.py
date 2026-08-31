#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Install one validated older release inside an installer acceptance image."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

INSTALLER = Path("/usr/local/libexec/install_xpra_release.py")


def load_installer() -> ModuleType:
    """Load the exact standalone installer shipped in the acceptance image."""
    spec = importlib.util.spec_from_file_location("xpra_release_installer", INSTALLER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the installed Xpra release helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--release-json", type=Path, required=True)
    value.add_argument("--archive", type=Path, required=True)
    value.add_argument("--expected-release-id", type=int, required=True)
    value.add_argument("--expected-commit", required=True)
    value.add_argument("--expected-version", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    installer = load_installer()
    try:
        target = installer.detect_system()
        raw: Any = json.loads(arguments.release_json.read_text(encoding="utf-8"))
        parsed = installer.parse_release(raw)
        if parsed is None:
            raise installer.ReleaseError("acceptance fixture is not canonical")
        release, _published = parsed
        if (
            release.release_id != arguments.expected_release_id
            or release.commit != arguments.expected_commit
            or release.version != arguments.expected_version
        ):
            raise installer.ReleaseError("acceptance fixture identity differs")
        asset = release.asset(installer.ASSET_FOR_DISTRO[target.distro])
        complete = installer.validate_package_archive(
            arguments.archive,
            release=release,
            asset=asset,
            distro=target.distro,
        )
        archive = installer.select_required_packages(complete)
        stage = Path(tempfile.mkdtemp(prefix="xpra-older-install-", dir="/var/tmp"))
        stage.chmod(0o700)
        try:
            paths = installer.extract_package_files(
                arguments.archive, archive, stage / "packages"
            )
            installer.verify_deb_metadata(archive, paths)
            installer.refresh_apt_metadata()
            before = installer.list_xpra_packages()
            installer.simulate_install(paths, release.version)
            installer.purge_xpra_packages(before)
            installer.install_local_packages(paths)
            installed = installer.verify_installed_system(
                release.version,
                target.distro,
            )
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        result = {
            "architecture": target.architecture,
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "asset_sha256": asset.sha256,
            "commit": release.commit,
            "distro": target.distro,
            "installed_packages": [package.package for package in installed],
            "pre_purge_packages": [package.binary for package in before],
            "release_id": release.release_id,
            "schema": 1,
            "tag": release.tag,
            "version": release.version,
        }
        sys.stdout.write(installer.canonical_json(result))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        installer.ReleaseError,
    ) as error:
        print(f"xpra installer acceptance: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
