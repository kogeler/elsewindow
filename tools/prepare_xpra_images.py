#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Build and verify both Xpra images from the newest owned fork release."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container_payload
from tools import install_xpra_release as installer

PODMAN: Final = os.environ.get("PODMAN", "podman")
DESCRIPTOR: Final = ROOT / ".artifacts/xpra-images/current.json"
OWNER_LABEL: Final = "io.elsewindow.owner"
OWNER_VALUE: Final = "xpra-image-verification"
COMMAND_TIMEOUT: Final = 1800
IMAGE_LABEL_INPUT: Final = f"{installer.IMAGE_LABEL_PREFIX}.input-sha256"
IMAGE_LABEL_ROLE: Final = f"{installer.IMAGE_LABEL_PREFIX}.role"
VERIFY_PROGRAM: Final = r"""
import importlib.util
import json
import pathlib
import sys

path = pathlib.Path('/usr/local/libexec/install_xpra_release.py')
spec = importlib.util.spec_from_file_location('xpra_release_installer', path)
if spec is None or spec.loader is None:
    raise SystemExit('cannot load image release verifier')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
packages = module.verify_installed_system(sys.argv[1], sys.argv[2])
descriptor = json.loads(
    pathlib.Path('/usr/share/elsewindow/xpra-release.json').read_text(
        encoding='utf-8'
    )
)
print(module.canonical_json({
    'descriptor': descriptor,
    'packages': [package.package for package in packages],
}), end='')
"""


class ImageError(RuntimeError):
    """Raised when release-backed image preparation cannot prove its result."""


@dataclasses.dataclass(frozen=True, slots=True)
class ImageSpec:
    role: str
    distro: str
    stage: str
    containerfile: Path
    inputs: tuple[Path, ...]


COMMON_INPUTS: Final = (
    ROOT / "containers/xpra/install-release.py",
    ROOT / "tools/install_xpra_release.py",
)
SPECS: Final = (
    ImageSpec(
        "target",
        "ubuntu-26.04",
        "xpra-target",
        ROOT / "containers/live-target/Containerfile",
        (
            ROOT / "containers/live-target/Containerfile",
            ROOT / "containers/live-target/install-base.sh",
            ROOT / "containers/live-target/entrypoint.sh",
            ROOT / "containers/live-target/sshd.conf",
            *COMMON_INPUTS,
        ),
    ),
    ImageSpec(
        "client",
        "debian-13",
        "xpra-client",
        ROOT / "containers/toolbox/Containerfile",
        (
            ROOT / "containers/toolbox/Containerfile",
            ROOT / "containers/toolbox/entrypoint.sh",
            ROOT / "requirements.txt",
            ROOT / "tools/container_payload.py",
            *COMMON_INPUTS,
        ),
    ),
)


class StaticApi:
    def __init__(self, values: Sequence[Any], live: installer.GitHubApi) -> None:
        self.values = tuple(values)
        self.live = live

    def releases(self) -> tuple[Any, ...]:
        return self.values

    def verify_develop_commit(self, commit: str) -> None:
        self.live.verify_develop_commit(commit)


def run(
    arguments: Sequence[str], *, timeout: int = COMMAND_TIMEOUT
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            tuple(arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ImageError(f"cannot run required command: {arguments[0]}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-8192:].decode(
            "utf-8", errors="replace"
        )
        raise ImageError(
            f"command failed ({completed.returncode}): {arguments[0]}: {detail.strip()}"
        )
    return completed


def text(arguments: Sequence[str], *, timeout: int = COMMAND_TIMEOUT) -> str:
    try:
        return (
            run(arguments, timeout=timeout)
            .stdout.decode("utf-8", errors="strict")
            .strip()
        )
    except UnicodeDecodeError as error:
        raise ImageError("command returned non-UTF-8 output") from error


def require_image_id(value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ImageError("Podman returned an invalid image ID")
    return value


def assert_no_runtime(label: str) -> None:
    names = text(
        (PODMAN, "ps", "--all", "--filter", f"label={label}", "--format", "{{.Names}}")
    )
    if names:
        raise ImageError(f"existing runtime objects use label {label}: {names}")


def resolve_release() -> tuple[
    installer.GitHubApi, installer.ForkRelease, Mapping[str, Any]
]:
    api = installer.GitHubApi(token=os.environ.get("GITHUB_TOKEN") or None)
    values = api.releases()
    release = installer.resolve_latest_release(StaticApi(values, api))  # type: ignore[arg-type]
    raw = next(
        (
            value
            for value in values
            if isinstance(value, Mapping) and value.get("id") == release.release_id
        ),
        None,
    )
    if raw is None:
        raise ImageError("selected release disappeared from its frozen collection")
    return api, release, raw


def image_input_digest(
    spec: ImageSpec,
    release_json: bytes,
    asset: installer.ReleaseAsset,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"elsewindow-image-v1\0")
    digest.update(spec.role.encode("ascii"))
    digest.update(b"\0")
    digest.update(spec.distro.encode("ascii"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(release_json).digest())
    digest.update(bytes.fromhex(asset.sha256))
    for path in sorted(spec.inputs, key=lambda item: item.as_posix()):
        content = path.read_bytes()
        digest.update(b"\0")
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def stream_failed(
    process: subprocess.Popen[bytes], purpose: str, error: BaseException
) -> NoReturn:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
        process.stdin = None
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    detail = (stderr or stdout)[-8192:].decode("utf-8", errors="replace").strip()
    suffix = f": {detail}" if detail else ""
    raise ImageError(f"{purpose}{suffix}") from error


def build_context(
    process: subprocess.Popen[bytes],
    spec: ImageSpec,
    release_path: Path,
    archive_path: Path,
    asset: installer.ReleaseAsset,
) -> None:
    if process.stdin is None:
        raise ImageError("Podman build stdin is unavailable")
    entries = [
        container_payload.PayloadEntry(
            path, PurePosixPath(path.relative_to(ROOT).as_posix())
        )
        for path in spec.inputs
    ]
    entries.extend(
        (
            container_payload.PayloadEntry(
                release_path, PurePosixPath("xpra-release/release.json")
            ),
            container_payload.PayloadEntry(
                archive_path, PurePosixPath("xpra-release/archive.tar")
            ),
        )
    )
    try:
        container_payload.write_archive(
            process.stdin,
            entries,
            max_members=len(entries),
            max_bytes=asset.size + 8 * 1024 * 1024,
        )
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT)
    except (
        OSError,
        container_payload.PayloadError,
        subprocess.TimeoutExpired,
    ) as error:
        stream_failed(process, "cannot stream the Xpra image context", error)
    if process.returncode != 0:
        detail = (stderr or stdout)[-8192:].decode("utf-8", errors="replace")
        raise ImageError(f"Xpra image build failed: {detail.strip()}")


def inspect_labels(image: str) -> dict[str, str]:
    raw = text((PODMAN, "image", "inspect", "--format", "{{json .Labels}}", image))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ImageError("Podman returned invalid image labels") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ImageError("Podman returned invalid image labels")
    return value


def expected_labels(
    spec: ImageSpec,
    release: installer.ForkRelease,
    asset: installer.ReleaseAsset,
    image_input: str,
) -> dict[str, str]:
    values = installer.image_release_labels(release, asset, spec.distro)
    values[IMAGE_LABEL_INPUT] = image_input
    values[IMAGE_LABEL_ROLE] = spec.role
    return values


def build_image(
    spec: ImageSpec,
    release: installer.ForkRelease,
    asset: installer.ReleaseAsset,
    release_path: Path,
    archive_path: Path,
    image_input: str,
) -> tuple[str, str]:
    tag = f"localhost/elsewindow-{spec.role}:{release.release_id}-{image_input[:16]}"
    labels = expected_labels(spec, release, asset, image_input)
    exists = (
        subprocess.run(
            (PODMAN, "image", "exists", tag),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    if not exists:
        print(f"xpra images: building {spec.role} image", file=sys.stderr)
        arguments = [
            PODMAN,
            "build",
            "--quiet",
            "--pull=missing",
            "--tag",
            tag,
            "--target",
            spec.stage,
            "--file",
            spec.containerfile.relative_to(ROOT).as_posix(),
        ]
        for name, value in labels.items():
            arguments.extend(("--label", f"{name}={value}"))
        arguments.append("-")
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
            )
        except OSError as error:
            raise ImageError("cannot start the Xpra image build") from error
        build_context(process, spec, release_path, archive_path, asset)
    actual_labels = inspect_labels(tag)
    installer.verify_image_release_labels(actual_labels, release, asset, spec.distro)
    if any(actual_labels.get(name) != value for name, value in labels.items()):
        raise ImageError("cached Xpra image build labels differ")
    image_id = require_image_id(
        text((PODMAN, "image", "inspect", "--format", "{{.Id}}", tag))
    )
    return tag, image_id


def verify_image(
    spec: ImageSpec,
    release: installer.ForkRelease,
    asset: installer.ReleaseAsset,
    image_id: str,
) -> dict[str, Any]:
    name = f"elsewindow-verify-{spec.role}-{secrets.token_hex(5)}"
    arguments = (
        PODMAN,
        "run",
        "--name",
        name,
        "--label",
        f"{OWNER_LABEL}={OWNER_VALUE}",
        "--network=none",
        "--userns=auto:size=4096",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        "--read-only",
        "--read-only-tmpfs=false",
        "--pids-limit=512",
        "--memory=1g",
        "--memory-swap=1g",
        "--pull=never",
        "--unsetenv-all",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=128m,mode=1777",
        "--user=0",
        "--entrypoint=/usr/bin/python3",
        image_id,
        "-c",
        VERIFY_PROGRAM,
        release.version,
        spec.distro,
    )
    try:
        raw = text(arguments)
    finally:
        subprocess.run(
            (PODMAN, "rm", "--force", "--time", "0", name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ImageError("image verification returned invalid JSON") from error
    if not isinstance(value, dict):
        raise ImageError("image verification returned a non-object result")
    descriptor = value.get("descriptor")
    if not isinstance(descriptor, dict):
        raise ImageError("installed image descriptor is invalid")
    expected_descriptor = {
        "asset": {
            "id": asset.asset_id,
            "name": asset.name,
            "sha256": asset.sha256,
            "size": asset.size,
        },
        "distro": spec.distro,
        "installed_packages": list(installer.REQUIRED_XPRA_PACKAGES),
        "release": release.descriptor(),
        "schema": 1,
    }
    if descriptor != expected_descriptor or value.get("packages") != list(
        installer.REQUIRED_XPRA_PACKAGES
    ):
        raise ImageError("installed image package or release state differs")
    return descriptor


def prepare_one(
    spec: ImageSpec,
    release: installer.ForkRelease,
    release_path: Path,
    stage: Path,
) -> dict[str, Any]:
    asset = release.asset(installer.ASSET_FOR_DISTRO[spec.distro])
    archive_path = stage / asset.name
    installer.download_asset(asset, archive_path)
    complete = installer.validate_package_archive(
        archive_path,
        release=release,
        asset=asset,
        distro=spec.distro,
    )
    selected = installer.select_required_packages(complete)
    if tuple(package.package for package in selected.packages) != (
        installer.REQUIRED_XPRA_PACKAGES
    ):
        raise ImageError("release image package set differs")
    release_bytes = release_path.read_bytes()
    image_input = image_input_digest(spec, release_bytes, asset)
    tag, image_id = build_image(
        spec,
        release,
        asset,
        release_path,
        archive_path,
        image_input,
    )
    installed = verify_image(spec, release, asset, image_id)
    return {
        "asset": installed["asset"],
        "distro": spec.distro,
        "image_id": image_id,
        "image_input_sha256": image_input,
        "role": spec.role,
        "tag": tag,
    }


def publish(value: Mapping[str, Any]) -> None:
    artifact_root = DESCRIPTOR.parents[1]
    if artifact_root.exists():
        details = artifact_root.lstat()
        if not stat.S_ISDIR(details.st_mode) or artifact_root.is_symlink():
            raise ImageError("artifact root is unsafe")
    else:
        artifact_root.mkdir(mode=0o700)
    directory = DESCRIPTOR.parent
    if directory.exists():
        details = directory.lstat()
        if not stat.S_ISDIR(details.st_mode) or directory.is_symlink():
            raise ImageError("Xpra image descriptor directory is unsafe")
    else:
        directory.mkdir(mode=0o700)
    if DESCRIPTOR.is_symlink() or (DESCRIPTOR.exists() and not DESCRIPTOR.is_file()):
        raise ImageError("Xpra image descriptor path is unsafe")
    temporary = directory / ".current.json.new"
    if temporary.exists() or temporary.is_symlink():
        raise ImageError("Xpra image descriptor temporary path exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(installer.canonical_json(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, DESCRIPTOR)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def prepare() -> dict[str, Any]:
    if os.geteuid() == 0:
        raise ImageError("Xpra image preparation requires rootless Podman")
    assert_no_runtime(OWNER_LABEL)
    api, release, raw = resolve_release()
    with tempfile.TemporaryDirectory(prefix="elsewindow-images-") as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        release_path = stage / "release.json"
        release_path.write_text(installer.canonical_json(raw), encoding="utf-8")
        release_path.chmod(0o600)
        images = [prepare_one(spec, release, release_path, stage) for spec in SPECS]
    installer.verify_release_is_current(release, api)
    assert_no_runtime(OWNER_LABEL)
    result = {
        "images": {image["role"]: image for image in images},
        "release": release.descriptor(),
        "schema": 1,
    }
    publish(result)
    return result


def read_image(role: str) -> str:
    try:
        details = DESCRIPTOR.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise ImageError("Xpra image descriptor identity or mode is unsafe")
        value = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        image = value["images"][role]
        image_id = image["image_id"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ImageError("Xpra image descriptor is invalid") from error
    if not isinstance(image_id, str):
        raise ImageError("Xpra image descriptor has no image ID")
    return require_image_id(image_id)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    image = commands.add_parser("image")
    image.add_argument(
        "--role", choices=tuple(spec.role for spec in SPECS), required=True
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            result: Any = prepare()
        else:
            result = read_image(arguments.role)
        if isinstance(result, str):
            print(result)
        else:
            sys.stdout.write(installer.canonical_json(result))
    except (ImageError, installer.ReleaseError) as error:
        print(f"xpra images: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
