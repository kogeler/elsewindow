#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Run destructive Xpra installer acceptance in two fresh Podman containers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pty
import re
import secrets
import selectors
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container_payload
from tools import install_xpra_release as installer

PODMAN: Final = os.environ.get("PODMAN", "podman")
OWNER_LABEL: Final = "io.elsewindow.owner"
OWNER_VALUE: Final = "xpra-installer-acceptance"
CONTAINERFILE: Final = ROOT / "containers/xpra-installer/Containerfile"
INSTALLER: Final = ROOT / "tools/install_xpra_release.py"
SYSTEMS: Final = (
    ("debian-13", "docker.io/library/debian:13"),
    ("ubuntu-26.04", "docker.io/library/ubuntu:26.04"),
)
BUILD_INPUTS: Final = (CONTAINERFILE, INSTALLER)
COMMAND_TIMEOUT: Final = 1800
INSTALLER_USER: Final = "xpra-installer"
PROMPT: Final = b"Purge this exact list and install the newest Xpra release? [y/N]: "
MAX_TERMINAL_OUTPUT: Final = 2 * 1024 * 1024


class AcceptanceError(RuntimeError):
    """Raised when installer acceptance cannot prove its contract."""


def run(
    arguments: Sequence[str],
    *,
    input_value: bytes | None = None,
    timeout: int = COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[bytes]:
    """Run one bounded host command without a shell."""
    try:
        completed = subprocess.run(
            tuple(arguments),
            input=input_value,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AcceptanceError(f"cannot run required command: {arguments[0]}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-8192:].decode(
            "utf-8", errors="replace"
        )
        raise AcceptanceError(
            f"command failed ({completed.returncode}): {arguments[0]}: {detail.strip()}"
        )
    return completed


def text(
    arguments: Sequence[str],
    *,
    input_value: bytes | None = None,
    timeout: int = COMMAND_TIMEOUT,
) -> str:
    value = run(arguments, input_value=input_value, timeout=timeout).stdout
    try:
        return value.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise AcceptanceError("command returned non-UTF-8 output") from error


def interactive_installer_command(container: str) -> tuple[str, ...]:
    """Run the exact local installer source through a human-style terminal."""
    pipeline = (
        "/usr/bin/cat /usr/local/libexec/install_xpra_release.py | /usr/bin/python3 -"
    )
    return (
        PODMAN,
        "exec",
        "--interactive",
        "--tty",
        "--user",
        INSTALLER_USER,
        "--env",
        "HOME=/home/xpra-installer",
        container,
        "/bin/bash",
        "-o",
        "pipefail",
        "-c",
        pipeline,
    )


def terminal_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    """Decode the bounded merged output returned through the pseudoterminal."""
    try:
        return (
            (completed.stdout + completed.stderr)
            .decode("utf-8", errors="strict")
            .replace("\r\n", "\n")
        )
    except UnicodeDecodeError as error:
        raise AcceptanceError(
            "interactive installer returned non-UTF-8 output"
        ) from error


def run_terminal_installer(
    arguments: Sequence[str], *, answer: bytes | None
) -> subprocess.CompletedProcess[bytes]:
    """Drive one pseudoterminal and answer only after its exact prompt."""
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            tuple(arguments),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
    except OSError as error:
        os.close(master)
        os.close(slave)
        raise AcceptanceError("cannot start interactive installer") from error
    os.close(slave)
    output = bytearray()
    answered = False
    prompt_seen = False
    deadline = time.monotonic() + COMMAND_TIMEOUT
    selector = selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, COMMAND_TIMEOUT)
            for key, _mask in selector.select(min(remaining, 1.0)):
                try:
                    block = os.read(key.fd, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    block = b""
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(block)
                if len(output) > MAX_TERMINAL_OUTPUT:
                    raise AcceptanceError(
                        "interactive installer output exceeded its bound"
                    )
                if not prompt_seen and PROMPT in output:
                    prompt_seen = True
                    if answer is None:
                        raise AcceptanceError(
                            "fresh installer unexpectedly requested purge confirmation"
                        )
                    os.write(master, answer)
                    answered = True
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, AcceptanceError) as error:
        process.kill()
        process.wait()
        if isinstance(error, AcceptanceError):
            raise
        raise AcceptanceError("interactive installer did not complete") from error
    finally:
        selector.close()
        os.close(master)
    if answer is not None and not answered:
        raise AcceptanceError("interactive installer exited before its purge prompt")
    return subprocess.CompletedProcess(tuple(arguments), returncode, bytes(output), b"")


def terminal_json_output(
    arguments: Sequence[str], *, answer: bytes | None
) -> dict[str, Any]:
    """Run one terminal-backed installer and extract its final JSON object."""
    completed = run_terminal_installer(arguments, answer=answer)
    output = terminal_text(completed)
    if completed.returncode != 0:
        raise AcceptanceError(
            f"interactive installer failed ({completed.returncode}): {output[-8192:].strip()}"
        )
    decoder = json.JSONDecoder()
    for offset, character in reversed(tuple(enumerate(output))):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(output[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not output[offset + end :].strip():
            return value
    raise AcceptanceError("interactive installer returned no final JSON object")


def input_digest(base_image_id: str, distro: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"xpra-installer-acceptance-image-v1\0")
    digest.update(base_image_id.encode("ascii"))
    digest.update(b"\0")
    digest.update(distro.encode("ascii"))
    for path in sorted(BUILD_INPUTS, key=lambda item: item.as_posix()):
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def require_image_id(value: str) -> str:
    """Require Podman's immutable lowercase hexadecimal image identity."""
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise AcceptanceError("Podman returned an invalid image ID")
    return value


def stream_failed(
    process: subprocess.Popen[bytes], purpose: str, error: BaseException
) -> NoReturn:
    """Close a failed producer and retain the receiver's bounded diagnostic."""
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
    raise AcceptanceError(f"{purpose}{suffix}") from error


def build_context(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is None:
        raise AcceptanceError("Podman build stdin is unavailable")
    try:
        container_payload.write_archive(
            process.stdin,
            (
                container_payload.PayloadEntry(
                    path, PurePosixPath(path.relative_to(ROOT).as_posix())
                )
                for path in BUILD_INPUTS
            ),
            max_members=len(BUILD_INPUTS),
            max_bytes=4 * 1024 * 1024,
        )
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT)
    except (
        OSError,
        container_payload.PayloadError,
        subprocess.TimeoutExpired,
    ) as error:
        stream_failed(process, "cannot stream the installer image context", error)
    if process.returncode != 0:
        detail = (stderr or stdout)[-8192:].decode("utf-8", errors="replace")
        raise AcceptanceError(f"installer image build failed: {detail.strip()}")


def image_labels(image: str) -> dict[str, str]:
    raw = text((PODMAN, "image", "inspect", "--format", "{{json .Labels}}", image))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AcceptanceError("Podman returned invalid image labels") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise AcceptanceError("Podman returned invalid image labels")
    return value


def prepare_image(distro: str, base: str) -> tuple[str, str, str]:
    print(f"xpra installer: refreshing {base}", file=sys.stderr)
    run((PODMAN, "pull", "--quiet", base))
    base_id = text((PODMAN, "image", "inspect", "--format", "{{.Id}}", base))
    base_id = require_image_id(base_id)
    key = input_digest(base_id, distro)
    tag = f"localhost/elsewindow-installer:{distro}-{key[:16]}"
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
    expected = {
        "io.elsewindow.installer.base-image-id": base_id,
        "io.elsewindow.installer.distro": distro,
        "io.elsewindow.installer.input-sha256": key,
    }
    if not exists:
        print(f"xpra installer: building {tag}", file=sys.stderr)
        arguments = [
            PODMAN,
            "build",
            "--quiet",
            "--pull=never",
            "--build-arg",
            f"BASE_IMAGE={base_id}",
            "--tag",
            tag,
            "--file",
            "containers/xpra-installer/Containerfile",
        ]
        for name, value in expected.items():
            arguments.extend(("--label", f"{name}={value}"))
        arguments.append("-")
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise AcceptanceError("cannot start the installer image build") from error
        build_context(process)
    labels = image_labels(tag)
    if any(labels.get(name) != value for name, value in expected.items()):
        raise AcceptanceError("cached installer image labels differ")
    image_id = text((PODMAN, "image", "inspect", "--format", "{{.Id}}", tag))
    image_id = require_image_id(image_id)
    return base_id, image_id, key


def expected_result(
    result: Mapping[str, Any], release: installer.ForkRelease, distro: str
) -> None:
    asset = release.asset(installer.ASSET_FOR_DISTRO[distro])
    expected = {
        "asset_id": asset.asset_id,
        "asset_name": asset.name,
        "asset_sha256": asset.sha256,
        "commit": release.commit,
        "distro": distro,
        "release_id": release.release_id,
        "tag": release.tag,
        "version": release.version,
    }
    if any(result.get(name) != value for name, value in expected.items()):
        raise AcceptanceError("installer result differs from the selected release")
    if result.get("installed_packages") != list(installer.REQUIRED_XPRA_PACKAGES):
        raise AcceptanceError("installer result has the wrong package set")


def require_pre_purge(value: Any, purpose: str) -> None:
    """Require one exact native-architecture Xpra package inventory."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AcceptanceError(f"{purpose} returned an invalid purge inventory")
    packages: list[str] = []
    for binary in value:
        package, separator, architecture = binary.partition(":")
        if separator and architecture != "amd64":
            raise AcceptanceError(f"{purpose} returned a foreign purge inventory")
        packages.append(package)
    if len(packages) != len(set(packages)) or sorted(packages) != sorted(
        installer.REQUIRED_XPRA_PACKAGES
    ):
        raise AcceptanceError(f"{purpose} did not purge the exact Xpra set")


def assert_no_owned_containers(label: str) -> None:
    names = text(
        (PODMAN, "ps", "--all", "--filter", f"label={label}", "--format", "{{.Names}}")
    )
    if names:
        raise AcceptanceError(f"existing runtime objects use label {label}: {names}")


def run_system(
    *,
    distro: str,
    base: str,
    latest: installer.ForkRelease,
    run_id: str,
) -> dict[str, Any]:
    base_id, image_id, image_input = prepare_image(distro, base)
    name = f"elsewindow-installer-{distro}-{run_id}"
    arguments = [
        PODMAN,
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        f"{OWNER_LABEL}={OWNER_VALUE}",
        "--label",
        f"io.elsewindow.run={run_id}",
        "--label",
        f"io.elsewindow.distro={distro}",
        "--userns=auto:size=4096",
        "--network=slirp4netns",
        "--pids-limit=2048",
        "--memory=6g",
        "--memory-swap=6g",
        "--log-driver=k8s-file",
        "--pull=never",
        "--unsetenv-all",
        "--env",
        "HOME=/root",
        "--env",
        "LANG=C.UTF-8",
        "--env",
        "LC_ALL=C.UTF-8",
        "--env",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    if os.environ.get("GITHUB_TOKEN"):
        arguments.extend(("--env", "GITHUB_TOKEN"))
    arguments.append(image_id)
    print(f"xpra installer: starting fresh {distro} container", file=sys.stderr)
    run(arguments)
    try:
        install_command = interactive_installer_command(name)
        initial = terminal_json_output(install_command, answer=None)
        expected_result(initial, latest, distro)
        if initial.get("pre_purge_packages") != []:
            raise AcceptanceError(
                "fresh container unexpectedly contained Xpra packages"
            )

        inventory_command = (
            PODMAN,
            "exec",
            name,
            installer.DPKG_QUERY,
            "--show",
            "--showformat=${binary:Package}\\t${Version}\\t${db:Status-Abbrev}\\n",
        )
        inventory_before_decline = text(inventory_command)
        cancelled = run_terminal_installer(install_command, answer=b"n\n")
        cancelled_output = terminal_text(cancelled)
        if cancelled.returncode != 1 or "no changes were made" not in cancelled_output:
            raise AcceptanceError("declined installer confirmation did not cancel")
        prompted = {
            binary.partition(":")[0]
            for binary in re.findall(r"^  - (\S+) ", cancelled_output, re.MULTILINE)
        }
        if prompted != set(installer.REQUIRED_XPRA_PACKAGES):
            raise AcceptanceError("installer prompt omitted an installed Xpra package")
        if text(inventory_command) != inventory_before_decline:
            raise AcceptanceError("declined installer confirmation changed packages")

        replacement = terminal_json_output(install_command, answer=b"yes\n")
        expected_result(replacement, latest, distro)
        require_pre_purge(replacement.get("pre_purge_packages"), "latest replacement")
        return {
            "base_image_id": base_id,
            "distro": distro,
            "image_id": image_id,
            "image_input_sha256": image_input,
            "initial_install": initial,
            "replacement_install": replacement,
        }
    finally:
        print(f"xpra installer: removing {name}", file=sys.stderr)
        run((PODMAN, "rm", "--force", "--time", "0", name))


def main() -> int:
    if os.geteuid() == 0:
        print("xpra installer acceptance must run as a rootless user", file=sys.stderr)
        return 2
    if shutil.which(PODMAN) is None:
        print("xpra installer acceptance requires Podman", file=sys.stderr)
        return 2
    try:
        assert_no_owned_containers(OWNER_LABEL)
        api = installer.GitHubApi(token=os.environ.get("GITHUB_TOKEN") or None)
        latest = installer.resolve_latest_release(api)
        run_id = secrets.token_hex(6)
        systems = [
            run_system(
                distro=distro,
                base=base,
                latest=latest,
                run_id=run_id,
            )
            for distro, base in SYSTEMS
        ]
        assert_no_owned_containers(OWNER_LABEL)
        result = {
            "latest_release": latest.descriptor(),
            "schema": 2,
            "systems": systems,
        }
        sys.stdout.write(installer.canonical_json(result))
    except (AcceptanceError, installer.ReleaseError) as error:
        print(f"xpra installer acceptance: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
