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
    TARGET_ALIAS,
    LiveFailure,
    LiveResources,
    checked,
    podman_exec,
    run_process,
)
from .xpra_target import (
    ABRUPT_MARKER,
    AGENT_CASE,
    AGENT_MARKER,
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

LINGER_CASES = ("linger-declined", "linger")
LIFECYCLE_CASES = (
    ("detach", OWNED_MARKER),
    ("abrupt", ABRUPT_MARKER),
    ("persistent", PERSISTENT_MARKER),
    (AGENT_CASE, AGENT_MARKER),
)
ABRUPT_REPETITIONS = 4
LIVE_CASES = (*LINGER_CASES, *(case for case, _marker in LIFECYCLE_CASES))
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
            diagnostic = "\n".join(
                line[:180] + (" ... " + line[-80:] if len(line) > 180 else "")
                for line in stream.splitlines()
                if any(
                    word in line.lower()
                    for word in (
                        "portal",
                        "permission",
                        "started command",
                        "warning",
                        "failed",
                        "traceback",
                    )
                )
            )[-16384:]
            if diagnostic:
                print(diagnostic, file=sys.stderr)
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
        PERSISTENT_CONNECTIONS
        if case == "persistent"
        else 2
        if case in {"detach", AGENT_CASE}
        else 1
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
        try:
            wait_until(
                f"owned Xpra cleanup on {display}",
                lambda display=display: owned_state_absent(
                    resources, target, display, marker
                ),
            )
        except LiveFailure as error:
            details = run_process(
                [
                    resources.podman,
                    "exec",
                    target,
                    "sh",
                    "-c",
                    'ps -eo pid,ppid,pgid,stat,comm; find "/run/user/1001/xpra/$1" -maxdepth 2 -printf "%y %f\\n" 2>/dev/null',
                    "elsewindow-cleanup-diagnostic",
                    display,
                ],
                capture_output=True,
                check=False,
            )
            records = run_process(
                [
                    resources.podman,
                    "exec",
                    target,
                    "journalctl",
                    "--no-pager",
                    "--all",
                    "--output=short-monotonic",
                    "--lines=160",
                    *(f"ELSEWINDOW_SESSION={value}" for value in evidence["sessions"]),
                ],
                capture_output=True,
                check=False,
            )
            timeline = "\n".join(
                line[:400]
                for line in records.stdout.decode(errors="replace").splitlines()
            )[-16000:]
            raise LiveFailure(
                f"{error}; process and owned-path evidence:\n{details.stdout.decode(errors='replace')[-4096:]}"
                f"\nowned session shutdown timeline:\n{timeline}"
            ) from error
    buses = evidence.get("buses")
    if not isinstance(buses, list) or not buses:
        raise LiveFailure("the private bus lifecycle evidence is missing")
    owners = []
    for bus in buses:
        if not isinstance(bus, dict) or not isinstance(bus.get("services"), list):
            raise LiveFailure(
                "the private desktop service lifecycle evidence is missing"
            )
        owners.extend((bus, *bus["services"]))
    for bus in owners:
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


def resumed_cases(from_case: str | None) -> tuple[str, ...]:
    """Select the ordered matrix tail that starts at a failed case."""
    if from_case is None:
        return LIVE_CASES
    if from_case not in LIVE_CASES:
        raise LiveFailure(f"unknown live case to resume from: {from_case}")
    return LIVE_CASES[LIVE_CASES.index(from_case) :]


def run_xpra_matrix(
    resources: LiveResources,
    target: str,
    client: str,
    from_case: str | None = None,
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
    cases = resumed_cases(from_case)
    if from_case is not None:
        print(
            f"live: resuming the matrix at {from_case}; earlier cases are skipped, "
            "so finish with one complete run",
            file=sys.stderr,
        )
    for case in LINGER_CASES:
        if case not in cases:
            continue
        evidence = run_authenticated_driver(resources, target, client, case)
        if case == "linger-declined" and evidence.get("declined") is not True:
            raise LiveFailure("the linger refusal case did not refuse")
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
    if "linger" not in cases:
        # Leave the state the skipped consent case establishes, through the same
        # logind session and sudo rule that the product's consent uses.
        podman_exec(
            resources,
            client,
            "ssh",
            TARGET_ALIAS,
            "/usr/bin/sudo -n /usr/bin/loginctl enable-linger 1001",
            purpose="enabling fixture linger for a resumed matrix",
        )
        podman_exec(
            resources,
            target,
            "test",
            "-e",
            "/var/lib/systemd/linger/xpra-test",
            purpose="checking resumed fixture linger",
        )
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
        (selected, selected_marker)
        for selected, selected_marker in LIFECYCLE_CASES
        if selected in cases
        for _attempt in range(ABRUPT_REPETITIONS if selected == "abrupt" else 1)
    ):
        print(f"live: checking {case} lifecycle", file=sys.stderr)
        evidence = run_authenticated_driver(resources, target, client, case)
        verify_case_cleanup(resources, target, evidence, marker)
        verify_unrelated(resources, target)
        if case == AGENT_CASE:
            print(
                "live: agent notification windows "
                f"{json.dumps(evidence.get('notifications'), sort_keys=True)}",
                file=sys.stderr,
            )
        if case in {"persistent", AGENT_CASE}:
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
    scope = (
        "matrix"
        if from_case is None
        else f"matrix tail from {from_case} (not a complete run)"
    )
    print(
        f"live: Xpra SSH lifecycle and dual-host journal {scope} passed with fork "
        f"release {release['release_id']} ({release['version']}, {release['commit']})",
        file=sys.stderr,
    )
