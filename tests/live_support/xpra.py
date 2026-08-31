# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Project-owned Xpra lifecycle cases on the disposable SSH topology."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from elsewindow.session import build_xpra_command_argv
from tools.install_xpra_release import REQUIRED_XPRA_PACKAGES

from .connection import target_log
from .process import (
    LIVE_DRIVER,
    LiveFailure,
    LiveResources,
    checked,
    podman_exec,
    run_process,
)
from .xpra_target import (
    ABRUPT_MARKER,
    OWNED_MARKER,
    install_xpra_fixture,
    owned_state_absent,
    start_unrelated_resources,
    unrelated_diagnostics,
    unrelated_ready,
    unrelated_state,
    verify_unrelated,
)

CLIENT_DISPLAY = ":99"
CLIENT_RUNTIME = "/tmp/elsewindow-client-runtime"
WAIT_TIMEOUT = 60.0
XPRA_RELEASE_DESCRIPTOR = "/usr/share/elsewindow/xpra-release.json"


def _verify_xpra_install(
    resources: LiveResources,
    container: str,
    role: str,
    distro: str,
) -> dict[str, Any]:
    podman_exec(
        resources,
        container,
        "test",
        "!",
        "-e",
        "/etc/apt/sources.list.d/xpra.sources",
        purpose=f"checking the {role} Xpra repository isolation",
    )
    try:
        descriptor = json.loads(
            podman_exec(
                resources,
                container,
                "cat",
                XPRA_RELEASE_DESCRIPTOR,
                purpose=f"reading the {role} Xpra release descriptor",
            ).decode()
        )
        release = descriptor["release"]
        version = release["version"]
        packages = descriptor["installed_packages"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LiveFailure(f"the {role} Xpra release descriptor is invalid") from error
    if (
        descriptor.get("schema") != 1
        or descriptor.get("distro") != distro
        or packages != list(REQUIRED_XPRA_PACKAGES)
        or not isinstance(release, dict)
        or not isinstance(release.get("release_id"), int)
        or not isinstance(release.get("commit"), str)
        or len(release["commit"]) != 40
        or not isinstance(version, str)
        or not version
    ):
        raise LiveFailure(f"the {role} Xpra release descriptor differs")
    versions = {
        podman_exec(
            resources,
            container,
            "dpkg-query",
            "-W",
            "-f=${Version}",
            package,
            purpose=f"reading the {role} {package} package version",
        ).decode()
        for package in REQUIRED_XPRA_PACKAGES
    }
    if versions != {version}:
        raise LiveFailure(f"the {role} Xpra package set is not version-consistent")
    canonical_role = "server" if role == "target" else "client"
    podman_exec(
        resources,
        container,
        *build_xpra_command_argv(canonical_role, "version", "xpra"),
        purpose=f"running the canonical {role} Xpra version command",
    )
    return release


def wait_until(description: str, predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise LiveFailure(f"timed out waiting for {description}")


def start_client_display(resources: LiveResources, client: str) -> None:
    checked(
        [
            resources.podman,
            "exec",
            "--detach",
            client,
            "/bin/sh",
            "-ceu",
            (
                f"install -d -m 0700 {CLIENT_RUNTIME}; "
                f"exec Xvfb {CLIENT_DISPLAY} -screen 0 1280x720x24 -nolisten tcp"
            ),
        ],
        "starting the confined Xpra client display",
    )

    def display_ready() -> bool:
        completed = run_process(
            [
                resources.podman,
                "exec",
                "--env",
                f"DISPLAY={CLIENT_DISPLAY}",
                client,
                "xdpyinfo",
            ],
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0

    wait_until("the confined Xpra client display", display_ready)


def client_driver_command(client: str, case: str) -> tuple[list[str], dict[str, str]]:
    return (
        [
            "podman",
            "exec",
            "--env",
            f"DISPLAY={CLIENT_DISPLAY}",
            "--env",
            f"XDG_RUNTIME_DIR={CLIENT_RUNTIME}",
            "--env",
            "ELSEWINDOW_LIVE_INSTALLED_ROOT=/home/box/.local",
            client,
            "/usr/local/bin/python",
            "/work/src/tests/live_xpra_e2e.py",
            "--case",
            case,
            "--ssh-path",
            "/usr/bin/ssh",
            "--false-path",
            "/usr/bin/false",
            "--xpra-path",
            "/usr/bin/xpra",
            "--target-ssh-port",
            "22",
            "--verify-local-window",
        ],
        os.environ.copy(),
    )


def run_driver(resources: LiveResources, client: str, case: str) -> dict[str, object]:
    command, environment = client_driver_command(client, case)
    command[0] = resources.podman
    completed = run_process(
        command,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        if detail:
            print(detail, file=sys.stderr)
        raise LiveFailure(
            f"the {case} Xpra case failed with status {completed.returncode}"
        )
    try:
        evidence = json.loads(completed.stdout.strip())
    except (TypeError, ValueError) as error:
        raise LiveFailure(
            f"the {case} Xpra driver returned malformed evidence"
        ) from error
    if not isinstance(evidence, dict):
        raise LiveFailure(f"the {case} Xpra driver returned non-object evidence")
    return evidence


def run_xpra_matrix(
    resources: LiveResources,
    target: str,
    client: str,
) -> None:
    """Prove SSH-owned attach, detach, master loss, and selective cleanup."""
    if not LIVE_DRIVER.is_file():
        raise LiveFailure("the Xpra live driver is missing")
    target_release = _verify_xpra_install(resources, target, "target", "ubuntu-26.04")
    client_release = _verify_xpra_install(resources, client, "client", "debian-13")
    if client_release != target_release:
        raise LiveFailure("the Xpra target and client releases differ")
    install_xpra_fixture(resources, target)
    start_unrelated_resources(resources, target)
    try:
        wait_until(
            "the unrelated Xpra fixtures", lambda: unrelated_ready(resources, target)
        )
    except LiveFailure as error:
        diagnostics = unrelated_diagnostics(resources, target)
        suffix = f"; diagnostics={diagnostics}" if diagnostics else ""
        raise LiveFailure(
            f"{error}; readiness={unrelated_state(resources, target)}{suffix}"
        ) from error
    start_client_display(resources, client)

    for case, marker in (("detach", OWNED_MARKER), ("abrupt", ABRUPT_MARKER)):
        accepted_before = target_log(resources, target).count(
            "Accepted publickey for xpra-test"
        )
        evidence = run_driver(resources, client, case)
        display = evidence.get("display")
        if not (
            isinstance(display, str)
            and display.startswith("wayland-")
            and display[8:].isdigit()
        ):
            raise LiveFailure(f"the {case} Xpra display evidence is invalid")
        accepted_after = target_log(resources, target).count(
            "Accepted publickey for xpra-test"
        )
        if accepted_after != accepted_before + 1:
            raise LiveFailure("one Xpra run did not perform exactly one authentication")
        wait_until(
            f"owned Xpra cleanup on {display}",
            lambda display=display, marker=marker: owned_state_absent(
                resources, target, display, marker
            ),
        )
        verify_unrelated(resources, target)

    release = target_release
    print(
        "live: Xpra SSH lifecycle matrix passed with fork release "
        f"{release['release_id']} ({release['version']}, {release['commit']})",
        file=sys.stderr,
    )
