# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Remote-target fixtures and evidence for the Xpra live test."""

from __future__ import annotations

from pathlib import Path

from elsewindow.config import DEFAULT_CLIPBOARD_POLICY
from elsewindow.live_config import DEFAULT_ENCODING_PROFILE
from elsewindow.session import build_server_argv

from .process import LiveFailure, LiveResources, checked, podman_exec, run_process

OWNED_TITLE = "Elsewindow Live Test"
APPLICATION_ENVIRONMENT = (
    ("EW_LIVE_EMPTY", ""),
    ("EW_LIVE_VALUE", "one two = '$HOME'; literal\nsecond line \u2603"),
)
FIXTURE_HOME = "/home/xpra-test"
FIXTURE_DIRECTORY = ".local/elsewindow-live"
REMOTE_APP = f"{FIXTURE_HOME}/{FIXTURE_DIRECTORY}/app"
AGENT_CASE = "agent-notification"
# Relative to the remote home, like an operator's `.local/<app>/...` command.
AGENT_PROBE = f"{FIXTURE_DIRECTORY}/agent-probe"
AGENT_MARKER = "/tmp/elsewindow-live-agent"
OWNED_MARKER = "/tmp/elsewindow-live-owned"
ABRUPT_MARKER = "/tmp/elsewindow-live-abrupt"
PERSISTENT_MARKER = "/tmp/elsewindow-live-persistent"
PERSISTENT_DISCONNECTS = ("detach", "client-kill", "master-close", "abrupt", "cancel")
PERSISTENT_CONNECTIONS = len(PERSISTENT_DISCONNECTS) + 2
UNRELATED_PROCESS_MARKER = "/tmp/elsewindow-live-unrelated-process"

FIXTURE_SOURCES = {
    "app": "gui_app.py",
    "agent-probe": "agent_probe.py",
    "remote_fixture.py": "remote_fixture.py",
}


def _target_user_argv(target: str, *arguments: str) -> list[str]:
    return [
        "exec",
        target,
        "runuser",
        "-u",
        "xpra-test",
        "--",
        "env",
        "HOME=/home/xpra-test",
        *arguments,
    ]


def _target_user(
    resources: LiveResources,
    target: str,
    *arguments: str,
    purpose: str,
    input_data: bytes | None = None,
) -> bytes:
    return podman_exec(
        resources,
        target,
        "runuser",
        "-u",
        "xpra-test",
        "--",
        "env",
        f"HOME={FIXTURE_HOME}",
        *arguments,
        purpose=purpose,
        input_data=input_data,
    )


def _process_start_time(stat_text: str) -> str:
    closing = stat_text.rfind(")")
    fields = stat_text[closing + 2 :].split()
    if closing < 0 or len(fields) < 20:
        raise ValueError("malformed process stat")
    return fields[19]


def live_identity(
    resources: LiveResources,
    target: str,
    marker: str,
    expected_command: bytes,
) -> tuple[int, str] | None:
    marker_result = run_process(
        [resources.podman, "exec", target, "cat", marker],
        capture_output=True,
        check=False,
    )
    if marker_result.returncode != 0:
        return None
    try:
        fields = marker_result.stdout.decode("ascii").split()
        pid = int(fields[0])
        recorded_start = fields[1]
    except (IndexError, UnicodeDecodeError, ValueError):
        return None

    stat_result = run_process(
        [resources.podman, "exec", target, "cat", f"/proc/{pid}/stat"],
        capture_output=True,
        check=False,
    )
    command_result = run_process(
        [resources.podman, "exec", target, "cat", f"/proc/{pid}/cmdline"],
        capture_output=True,
        check=False,
    )
    if stat_result.returncode != 0 or command_result.returncode != 0:
        return None
    try:
        actual_start = _process_start_time(stat_result.stdout.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    command = command_result.stdout.split(b"\0", maxsplit=1)[0].rsplit(b"/", 1)[-1]
    if actual_start != recorded_start or command != expected_command:
        return None
    return pid, recorded_start


def xpra_id(resources: LiveResources, target: str, display: str) -> bool:
    completed = run_process(
        [resources.podman, *_target_user_argv(target, "xpra", "id", display)],
        capture_output=True,
        check=False,
    )
    fields = set(completed.stdout.splitlines())
    return (
        completed.returncode == 0
        and b"session-type=wayland" in fields
        and any(field.startswith(b"pid=") for field in fields)
    )


def unrelated_display(resources: LiveResources, target: str) -> str | None:
    completed = run_process(
        [
            resources.podman,
            "exec",
            target,
            "runuser",
            "-u",
            "xpra-test",
            "--",
            "bash",
            "-ceu",
            r"""
for config in /run/user/1001/xpra/wayland-*/config; do
    test -f "$config" || continue
    grep -Eq '^session[-_]name[[:space:]]*=[[:space:]]*elsewindow-unrelated[[:space:]]*$' "$config" || continue
    basename "$(dirname "$config")"
done
""",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    displays = completed.stdout.split() if completed.returncode == 0 else []
    display = displays[0] if len(displays) == 1 else ""
    return display if display.startswith("wayland-") and display[8:].isdigit() else None


def install_xpra_fixture(resources: LiveResources, target: str) -> None:
    directory = f"{FIXTURE_HOME}/{FIXTURE_DIRECTORY}"
    _target_user(
        resources,
        target,
        "install",
        "-d",
        "-m",
        "0700",
        directory,
        purpose="creating the Xpra live fixture directory",
    )
    # Every fixture imports the shared helpers from its own directory.
    for name, source in FIXTURE_SOURCES.items():
        _target_user(
            resources,
            target,
            "sh",
            "-ceu",
            'umask 077; cat > "$1"; chmod 0700 "$1"',
            "elsewindow-live-fixture",
            f"{directory}/{name}",
            purpose=f"installing the Xpra live fixture {name}",
            input_data=Path(__file__).with_name(source).read_bytes(),
        )
    podman_exec(
        resources,
        target,
        "sh",
        "-ceu",
        "umask 077; cat > /etc/sudoers.d/elsewindow-live-linger; chmod 0440 /etc/sudoers.d/elsewindow-live-linger; visudo -cf /etc/sudoers.d/elsewindow-live-linger",
        purpose="allowing only fixture-account linger activation",
        input_data=b"xpra-test ALL=(root) NOPASSWD: /usr/bin/loginctl enable-linger 1001\n",
    )


def start_unrelated_resources(resources: LiveResources, target: str) -> None:
    server = (
        "dbus-run-session",
        "--",
        *build_server_argv(
            ("/bin/sleep", "3600"),
            "elsewindow-unrelated",
            DEFAULT_ENCODING_PROFILE,
            DEFAULT_CLIPBOARD_POLICY,
        ),
    )
    checked(
        [
            resources.podman,
            "exec",
            "--detach",
            target,
            "runuser",
            "-u",
            "xpra-test",
            "--",
            "bash",
            "-ceu",
            'exec "$@" >/tmp/elsewindow-unrelated.log 2>&1',
            "elsewindow-unrelated",
            "env",
            "HOME=/home/xpra-test",
            "XDG_RUNTIME_DIR=/run/user/1001",
            *server,
        ],
        "starting the unrelated Xpra session",
    )
    _target_user(
        resources,
        target,
        "/bin/sh",
        "-ceu",
        "umask 077; nohup sleep 3600 >/dev/null 2>&1 & child=$!; "
        "start=$(awk '{print $22}' \"/proc/$child/stat\"); "
        'printf \'%s %s\\n\' "$child" "$start" > "$1"',
        "elsewindow-unrelated-process",
        UNRELATED_PROCESS_MARKER,
        purpose="starting the unrelated remote process",
    )


def unrelated_state(resources: LiveResources, target: str) -> dict[str, bool]:
    display = unrelated_display(resources, target)
    return {
        "process": live_identity(resources, target, UNRELATED_PROCESS_MARKER, b"sleep")
        is not None,
        "session": display is not None and xpra_id(resources, target, display),
    }


def unrelated_ready(resources: LiveResources, target: str) -> bool:
    return all(unrelated_state(resources, target).values())


def unrelated_diagnostics(resources: LiveResources, target: str) -> str:
    completed = run_process(
        [
            resources.podman,
            "exec",
            target,
            "bash",
            "-ceu",
            r"""
printf 'processes='
ps -eo user=,pid=,args= | grep '[x]pra' | tail -n 12 | tr '\n' '|' || true
printf '\nruntime='
find /run/user/1001/xpra /home/xpra-test/.xpra -maxdepth 3 \
    -user xpra-test -printf '%p|' 2>/dev/null | head -c 2048 || true
printf '\n'
if test -f /tmp/elsewindow-unrelated.log; then
    printf 'launcher-log-tail='
    tail -n 20 /tmp/elsewindow-unrelated.log | tr '\n' '|'
fi
for log in /run/user/1001/xpra/wayland-*/server.log; do
    test -f "$log" || continue
    printf 'server-log-tail='
    tail -n 12 "$log" | tr '\n' '|'
done
for log in /home/xpra-test/.xpra/*/server.log /home/xpra-test/.xpra/*/server.log.*; do
    test -f "$log" || continue
    printf 'home-server-log-tail='
    tail -n 12 "$log" | tr '\n' '|'
done
""",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return (completed.stdout + completed.stderr).strip()[-2048:]


def verify_unrelated(resources: LiveResources, target: str) -> None:
    if not unrelated_ready(resources, target):
        raise LiveFailure("an unrelated remote process or Xpra session was changed")


def owned_state_absent(
    resources: LiveResources, target: str, display: str, marker: str
) -> bool:
    if live_identity(resources, target, marker, b"python3") is not None:
        return False
    if xpra_id(resources, target, display):
        return False
    cleanup_check = (
        'display=$1; test ! -e "/tmp/xpra/$display"; '
        'test ! -e "/run/user/1001/xpra/$display"; '
        'test ! -S "/run/user/1001/$display"; '
        'for path in /home/xpra-test/.xpra/*-"$display" '
        '/run/user/1001/xpra/*-"$display"; do '
        'test ! -e "$path" && test ! -S "$path"; done'
    )
    completed = run_process(
        [
            resources.podman,
            "exec",
            target,
            "bash",
            "-ceu",
            cleanup_check,
            "elsewindow-cleanup-check",
            display,
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0
