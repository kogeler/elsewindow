#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Install the one validated Xpra release asset supplied in an image context."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

INSTALLER = Path("/usr/local/libexec/install_xpra_release.py")
RELEASE_JSON = Path("/tmp/xpra-release/release.json")
ARCHIVE = Path("/tmp/xpra-release/archive.tar")
INSTALLED_DESCRIPTOR = Path("/usr/share/elsewindow/xpra-release.json")


def load_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("xpra_release_installer", INSTALLER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the Xpra release installer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    installer = load_installer()
    try:
        target = installer.detect_system()
        raw: Any = json.loads(RELEASE_JSON.read_text(encoding="utf-8"))
        parsed = installer.parse_release(raw)
        if parsed is None:
            raise installer.ReleaseError("image release descriptor is not canonical")
        release, _published = parsed
        asset = release.asset(installer.ASSET_FOR_DISTRO[target.distro])
        complete = installer.validate_package_archive(
            ARCHIVE,
            release=release,
            asset=asset,
            distro=target.distro,
        )
        archive = installer.select_required_packages(complete)
        stage = Path(tempfile.mkdtemp(prefix="xpra-image-install-", dir="/var/tmp"))
        stage.chmod(0o700)
        try:
            paths = installer.extract_package_files(
                ARCHIVE, archive, stage / "packages"
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
        if before:
            raise installer.ReleaseError(
                "base image unexpectedly contained Xpra packages"
            )
        result = {
            "asset": {
                "id": asset.asset_id,
                "name": asset.name,
                "sha256": asset.sha256,
                "size": asset.size,
            },
            "distro": target.distro,
            "installed_packages": [package.package for package in installed],
            "release": release.descriptor(),
            "schema": 1,
        }
        INSTALLED_DESCRIPTOR.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        temporary = INSTALLED_DESCRIPTOR.with_suffix(".json.new")
        temporary.write_text(installer.canonical_json(result), encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(INSTALLED_DESCRIPTOR)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        installer.ReleaseError,
    ) as error:
        print(f"xpra image install: {error}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(RELEASE_JSON.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
