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
from .journal import start_client_journal, verify_journals
from .process import (
    CLIENT_XPRA_VENV,
    LIVE_DRIVER,
    LIVE_EVIDENCE_PREFIX,
    LiveFailure,
    LiveResources,
    checked,
    podman_exec,
    run_process,
)
from .xpra_target import (
    ABRUPT_MARKER,
    OWNED_MARKER,
    PERSISTENT_CONNECTIONS,
    PERSISTENT_MARKER,
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
        *build_xpra_command_argv(
            canonical_role,
            "version",
            f"{CLIENT_XPRA_VENV}/bin/xpra" if role == "client" else "xpra",
        ),
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
            "/usr/bin/dbus-run-session",
            "--",
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
    answer = None
    if case in {"linger", "linger-declined"}:
        command[2:2] = ["--interactive", "--tty"]
        answer = "yes\n" if case == "linger" else "no\n"
    completed = run_process(
        command,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        input=answer,
    )
    if completed.returncode != 0:
        for stream in (completed.stdout, completed.stderr):
            notifications = "\n".join(
                line for line in stream.splitlines() if "notif" in line.lower()
            )[-8192:]
            if notifications:
                print(notifications, file=sys.stderr)
            detail = stream[-4096:].strip()
            if detail:
                print(detail, file=sys.stderr)
        raise LiveFailure(
            f"the {case} Xpra case failed with status {completed.returncode}"
        )
    try:
        line = completed.stdout.strip().splitlines()[-1]
        if not line.startswith(LIVE_EVIDENCE_PREFIX):
            raise ValueError("missing evidence prefix")
        evidence = json.loads(line.removeprefix(LIVE_EVIDENCE_PREFIX))
    except (IndexError, TypeError, ValueError) as error:
        raise LiveFailure(
            f"the {case} Xpra driver returned malformed evidence"
        ) from error
    if not isinstance(evidence, dict):
        raise LiveFailure(f"the {case} Xpra driver returned non-object evidence")
    assert resources.target_name is not None
    verify_journals(
        resources,
        resources.target_name,
        client,
        evidence,
        completed.stdout,
        completed.stderr,
        terminal_merged=answer is not None,
    )
    return evidence


def run_authenticated_driver(
    resources: LiveResources, target: str, client: str, case: str
) -> dict[str, object]:
    """Check every case against the same authoritative SSH journal."""
    before = target_log(resources, target).count("Accepted publickey for xpra-test")
    evidence = run_driver(resources, client, case)
    after = target_log(resources, target).count("Accepted publickey for xpra-test")
    expected = (
        PERSISTENT_CONNECTIONS if case == "persistent" else 2 if case == "detach" else 1
    )
    if after != before + expected:
        raise LiveFailure(
            "one Xpra invocation did not perform exactly one authentication"
        )
    return evidence


def verify_case_cleanup(
    resources: LiveResources, target: str, evidence: dict[str, object], marker: str
) -> None:
    displays = evidence.get("displays")
    if (
        not isinstance(displays, list)
        or not displays
        or any(
            not isinstance(display, str)
            or not display.startswith("wayland-")
            or not display[8:].isdigit()
            for display in displays
        )
    ):
        raise LiveFailure("the Xpra display evidence is invalid")
    for display in set(displays):
        wait_until(
            f"owned Xpra cleanup on {display}",
            lambda display=display: owned_state_absent(
                resources, target, display, marker
            ),
        )
    buses = evidence.get("buses")
    if not isinstance(buses, list) or not buses:
        raise LiveFailure("the private bus lifecycle evidence is missing")
    for bus in buses:
        if (
            not isinstance(bus, dict)
            or not isinstance(bus.get("pid"), int)
            or not str(bus.get("start", "")).isdigit()
        ):
            raise LiveFailure("the private bus process identity is invalid")

        def ended(bus: dict = bus) -> bool:
            output = run_process(
                [resources.podman, "exec", target, "cat", f"/proc/{bus['pid']}/stat"],
                capture_output=True,
                check=False,
            )
            if output.returncode:
                return True
            fields = output.stdout.rsplit(b")", 1)[1].split()
            return fields[19].decode() != bus["start"] or fields[0] == b"Z"

        wait_until("owned notification bus cleanup", ended)


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
    start_client_journal(resources, client)
    start_client_display(resources, client)
    graphics = podman_exec(
        resources,
        client,
        "env",
        f"DISPLAY={CLIENT_DISPLAY}",
        f"{CLIENT_XPRA_VENV}/bin/xpra",
        "opengl",
        purpose="checking the prepared Xpra client's public OpenGL properties",
    ).decode()
    properties = dict(
        line.split("=", 1) for line in graphics.splitlines() if "=" in line
    )
    if (
        properties.get("zerocopy") != "True"
        or not properties.get("accelerate")
        or properties.get("accelerate") != properties.get("pyopengl")
    ):
        raise LiveFailure(
            "the prepared Xpra client did not load its matched OpenGL accelerator"
        )
    print(
        "live: prepared Xpra client reports matched PyOpenGL and zerocopy=True",
        file=sys.stderr,
    )
    for case in ("linger-declined", "linger"):
        evidence = run_authenticated_driver(resources, target, client, case)
        if case == "linger-declined" and evidence.get("declined") is not True:
            raise LiveFailure("the linger refusal case did not refuse")
        if case == "linger":
            verify_case_cleanup(resources, target, evidence, PERSISTENT_MARKER)
        podman_exec(
            resources,
            target,
            "test",
            *(() if case == "linger" else ("!",)),
            "-e",
            "/var/lib/systemd/linger/xpra-test",
            purpose="checking fixture linger",
        )
        print(f"live: {case} lifecycle and journals verified", file=sys.stderr)
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
    for case, marker in (
        ("detach", OWNED_MARKER),
        ("abrupt", ABRUPT_MARKER),
        ("persistent", PERSISTENT_MARKER),
    ):
        evidence = run_authenticated_driver(resources, target, client, case)
        verify_case_cleanup(resources, target, evidence, marker)
        verify_unrelated(resources, target)
        if case == "persistent":
            podman_exec(
                resources,
                target,
                "sh",
                "-ceu",
                "test -z \"$(find /run/user/1001/elsewindow-persistent -name '*.json' -print)\"; "
                "test -z \"$(runuser -u xpra-test -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user --no-legend list-units 'elsewindow-*.service')\"",
                purpose="verifying persistent registry and unit cleanup",
            )
        print(f"live: {case} lifecycle and journals verified", file=sys.stderr)

    release = target_release
    print(
        "live: Xpra SSH lifecycle and dual-host journal matrix passed with fork release "
        f"{release['release_id']} ({release['version']}, {release['commit']})",
        file=sys.stderr,
    )
