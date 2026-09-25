# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Persistent ownership, inert argv transport, consent and service policy."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pty
import shlex
import signal
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from ssh_wrapper.errors import SSHError

from elsewindow import _persistent_agent as agent
from elsewindow import persistent
from elsewindow.config import DEFAULT_CLIPBOARD_POLICY, MAX_APPLICATION_BYTES
from elsewindow.desktop import (
    MAX_APPLICATION_ENVIRONMENT_BYTES,
    SYSTEM_PYTHON,
    application_argv,
)
from elsewindow.journal import remote_source
from elsewindow.live_config import DEFAULT_ENCODING_PROFILE
from elsewindow.session import build_server_argv


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    tmp_path.chmod(0o700)
    monkeypatch.setattr(agent, "state_root", lambda: tmp_path)
    monkeypatch.setattr(agent, "unit_state", lambda _key: {})
    # Registry tests must not depend on the test container having booted systemd.
    monkeypatch.setattr(
        agent, "log_session", lambda key: agent.digest(["test-machine", key])
    )
    previous = signal.getsignal(signal.SIGHUP)
    yield tmp_path
    signal.signal(signal.SIGHUP, previous)


def request(*arguments: str) -> dict[str, Any]:
    application = (sys.executable, *arguments)
    return {
        "application": list(application),
        "server": list(
            build_server_argv(
                application,
                "fixture",
                DEFAULT_ENCODING_PROFILE,
                DEFAULT_CLIPBOARD_POLICY,
                persistent=True,
            )
        ),
    }


def test_creation_uses_owned_cgroup_and_lossless_child_argv(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(agent, "run", run)
    payload = request(
        "-c", "print('test')", "one two", "", "line\nvalue", "$literal;value"
    )
    public = agent.ensure(payload, "encoded-source")
    record = agent.read_record(registry, public["key"])
    assert record is not None
    assert public["created"]
    assert public["key"] == agent.digest(
        agent.canonical_application(payload["application"])
    )
    assert record["server"] != payload["server"]
    child = next(
        arg.partition("=")[2]
        for arg in record["server"]
        if arg.startswith("--start-child=")
    )
    argv = shlex.split(child)
    assert argv[:4] == [SYSTEM_PYTHON, "-I", "-c", agent.APP_LOADER]
    assert json.loads(base64.b64decode(argv[-2])) == {
        "argv": agent.canonical_application(payload["application"]),
        "environment": {},
    }
    command = commands[0]
    for option in (
        "--user",
        "--collect",
        "--service-type=exec",
        "--expand-environment=no",
        "--property=Restart=no",
        "--property=KillMode=control-group",
    ):
        assert option in command
    assert "--scope" not in command
    assert command[-3:] == (
        "encoded-source",
        "serve",
        agent.encode(
            {
                "key": public["key"],
                "token": public["token"],
                "log_session": public["log_session"],
                "log_level": record["log_level"],
            }
        ),
    )
    assert (registry / f"{public['key']}.json").stat().st_mode & 0o077 == 0
    assert "--property=StandardOutput=journal" in command
    assert "--property=StandardError=journal" in command
    assert record["log_level"] == "warning"


def test_identity_preserves_argument_boundaries_and_executable_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(sys.executable)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert agent.canonical_application(["alias", "a"]) == [str(alias), "a"]
    assert agent.canonical_application(["./alias", "a"]) == [str(alias), "a"]
    assert agent.canonical_application(
        [str(alias), "a"]
    ) != agent.canonical_application([sys.executable, "a"])
    assert agent.digest([sys.executable, "a b"]) != agent.digest(
        [sys.executable, "a", "b"]
    )
    assert agent.digest([sys.executable, ""]) != agent.digest([sys.executable])


def test_explicit_application_path_controls_remote_executable_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = tmp_path / "environment-fixture"
    application.symlink_to(sys.executable)
    monkeypatch.setenv("PATH", "/missing")
    assert agent.canonical_application(
        [application.name, "argument"], {"PATH": str(tmp_path)}
    ) == [str(application), "argument"]
    with pytest.raises(agent.AgentError, match="not found"):
        agent.canonical_application([application.name])


def test_persistent_environment_is_applied_and_must_match_on_resume(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(agent, "run", run)
    payload = request(
        "-c",
        "import json,os;print(json.dumps({k:os.environ[k] for k in ('APP_MODE','EMPTY')}))",
    )
    values = {"APP_MODE": "literal $HOME; value=one\nnext", "EMPTY": ""}
    payload["application_environment"] = values
    created = agent.ensure(payload, "source")
    record = agent.read_record(registry, created["key"])
    assert record is not None
    assert record["application_environment"] == values
    assert "APP_MODE" not in record["environment"]
    assert "application_environment" not in created
    child = next(
        arg.partition("=")[2]
        for arg in record["server"]
        if arg.startswith("--start-child=")
    )
    result = subprocess.run(
        shlex.split(child), capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == values

    state = {"ActiveState": "active", "Description": agent.description(record)}
    monkeypatch.setattr(agent, "unit_state", lambda _key: state)
    reordered = dict(reversed(tuple(values.items())))
    assert agent.ensure(
        payload | {"application_environment": reordered}, "source"
    ) == created | {"created": False}
    for changed in ({}, values | {"APP_MODE": "changed"}):
        with pytest.raises(agent.AgentError) as raised:
            agent.ensure(payload | {"application_environment": changed}, "source")
        assert raised.value.code == "persistent_environment_mismatch"
    assert len(commands) == 1
    assert agent.read_record(registry, created["key"]) == record


def test_child_launcher_preserves_the_selected_virtual_environment() -> None:
    application = agent.canonical_application(
        [sys.executable, "-c", "import sys;print(sys.prefix)"]
    )
    completed = subprocess.run(
        application_argv(tuple(application)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == sys.prefix


@pytest.mark.parametrize(
    "value",
    [
        [],
        ["/missing-application"],
        [""],
        ["bad\0value"],
        [sys.executable, 2],
        [sys.executable, "x" * 17000],
    ],
)
def test_invalid_application_is_rejected(value: Any) -> None:
    with pytest.raises(agent.AgentError, match="application"):
        agent.canonical_application(value)


def test_reuse_mismatch_stale_and_foreign_unit(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(agent, "run", run)
    payload = request("-c", "pass")
    created = agent.ensure(payload, "source")
    record = agent.read_record(registry, created["key"])
    assert record is not None
    state = {"ActiveState": "active", "Description": agent.description(record)}
    monkeypatch.setattr(agent, "unit_state", lambda _key: state)
    reused = agent.ensure(payload, "source")
    assert reused == created | {"created": False}
    assert len(commands) == 1
    assert agent.status(created)["ended"] is False
    assert agent.status(created | {"token": "changed"}) == {"ended": True}
    with pytest.raises(agent.AgentError, match="different server"):
        agent.ensure(
            payload | {"server": [*payload["server"], "--new-project-policy=value"]},
            "source",
        )
    with pytest.raises(agent.AgentError, match="logging settings"):
        agent.ensure(payload | {"log_level": "debug"}, "source")
    state["ActiveState"] = "deactivating"
    with pytest.raises(agent.AgentError, match="still stopping"):
        agent.ensure(payload, "source")
    state["Description"] = "unrelated user service"
    with pytest.raises(agent.AgentError, match="does not match"):
        agent.ensure(payload, "source")
    assert len(commands) == 1
    monkeypatch.setattr(agent, "unit_state", lambda _key: {})
    restarted = agent.ensure(payload, "source")
    assert restarted["key"] == created["key"]
    assert restarted["token"] != created["token"]


def test_resume_retains_effective_features_when_prerequisites_change(
    registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent, "run", lambda *args: subprocess.CompletedProcess(args, 0, "", "")
    )
    payload = request("-c", "pass")
    requested_server = payload["server"]
    original_features = {
        "notifications": False,
        "encoding_profile": DEFAULT_ENCODING_PROFILE,
    }
    payload.update(
        requested_server=requested_server,
        server=[
            arg.replace("--notifications=yes", "--notifications=no")
            for arg in requested_server
        ],
        server_features=original_features,
    )
    created = agent.ensure(payload, "source")
    record = agent.read_record(registry, created["key"])
    assert record is not None
    monkeypatch.setattr(
        agent,
        "unit_state",
        lambda _key: {
            "ActiveState": "active",
            "Description": agent.description(record),
        },
    )
    repaired = payload | {
        "server": requested_server,
        "server_features": original_features | {"notifications": True},
    }
    resumed = agent.ensure(repaired, "source")
    assert resumed == created | {"created": False}
    assert resumed["server_features"] == original_features
    with pytest.raises(agent.AgentError, match="different server"):
        agent.ensure(
            repaired
            | {"requested_server": [*requested_server, "--new-project-policy=value"]},
            "source",
        )


def test_simultaneous_creators_submit_exactly_one_service(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, str] = {}
    launches = []
    monkeypatch.setattr(agent.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(agent, "unit_state", lambda _key: state)

    def run(*command: str) -> subprocess.CompletedProcess[str]:
        launches.append(command)
        record = agent.read_record(
            registry,
            agent.digest(agent.canonical_application(request()["application"])),
        )
        assert record is not None
        state.update(ActiveState="active", Description=agent.description(record))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent, "run", run)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _index: agent.ensure(request(), "source"), range(4))
        )
    assert len(launches) == 1
    assert sum(result["created"] for result in results) == 1
    assert len({result["token"] for result in results}) == 1


def test_pid_reuse_is_not_owned() -> None:
    record = {
        "key": "a" * 64,
        "token": "b" * 32,
        "worker_pid": os.getpid(),
        "worker_start": "0",
        "invocation": "c" * 32,
    }
    state = {
        "Description": agent.description(record),
        "MainPID": str(os.getpid()),
        "InvocationID": record["invocation"],
    }
    with pytest.raises(agent.AgentError, match="identity changed"):
        agent.verify_unit(record, state)
    record["worker_start"] = agent.process_start(os.getpid())
    agent.verify_unit(record, state)


def test_log_identity_is_stable_and_separates_hosts_accounts_and_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "a" * 64
    monkeypatch.setattr(Path, "read_text", lambda _path: "b" * 32)
    monkeypatch.setattr(agent.os, "getuid", lambda: 1000)
    first = agent.log_session(key)
    assert first == agent.log_session(key)
    assert first != agent.log_session("c" * 64)
    monkeypatch.setattr(agent.os, "getuid", lambda: 1001)
    account = agent.log_session(key)
    monkeypatch.setattr(Path, "read_text", lambda _path: "d" * 32)
    host = agent.log_session(key)
    assert len({first, account, host}) == 3
    monkeypatch.setattr(Path, "read_text", lambda _path: "invalid")
    with pytest.raises(agent.AgentError, match="machine identity"):
        agent.log_session(key)


def test_state_symlinks_and_permissions_are_rejected(registry: Path) -> None:
    key = "a" * 64
    path = registry / f"{key}.lock"
    path.symlink_to(registry / "other")
    with pytest.raises(OSError), agent.locked(registry, key):
        pytest.fail("symlink lock accepted")
    with pytest.raises(agent.AgentError), agent.locked(registry, "../unsafe"):
        pytest.fail("path traversal accepted")
    registry.chmod(0o755)
    with pytest.raises(agent.AgentError, match="permissions"):
        agent.secure_directory(registry)


@pytest.mark.parametrize(
    "answer,accepted",
    [
        (b"y\n", True),
        (b"yes\n", True),
        (b"\n", False),
        (b"Y\n", False),
        (b"no\n", False),
        (b"yes please\n", False),
    ],
)
def test_terminal_consent(
    answer: bytes, accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        reader, writer = pty.openpty()
        monkeypatch.setattr(persistent, "open_terminal", lambda: os.dup(writer))
        try:
            task = asyncio.create_task(persistent.confirm_linger())
            await asyncio.sleep(0)
            assert persistent.LINGER_PROMPT.encode() in os.read(reader, 1024)
            os.write(reader, answer)
            if accepted:
                await asyncio.wait_for(task, 1)
            else:
                with pytest.raises(SSHError, match="not enabled"):
                    await asyncio.wait_for(task, 1)
        finally:
            os.close(reader)
            os.close(writer)

    asyncio.run(scenario())


def test_source_payload_runs_as_standard_library_only() -> None:
    source = remote_source(Path(agent.__file__).read_text())
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            agent.LOADER,
            source,
            "invalid-action",
            agent.encode({}),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["error"] == "persistent_operation_failed"
    assert not completed.stderr


def test_controller_rechecks_linger_and_never_emits_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    record = {
        "key": "a" * 64,
        "log_session": "d" * 64,
        "token": "b" * 32,
        "argv_sha256": "c" * 64,
        "session_name": "elsewindow-" + "a" * 16,
        "display": None,
        "created": True,
    }

    async def mux(command: str, _timeout: float) -> tuple[int, bytes, bytes]:
        action = shlex.split(command)[-2]
        payload = json.loads(base64.b64decode(shlex.split(command)[-1]))
        if "application" in payload:
            assert payload["application_environment"] == {"APP_MODE": "explicit"}
        calls.append(action)
        result = (
            {"linger": "enable-linger" in calls, "manager": True}
            if action == "probe"
            else record
            if action in {"ensure", "identify"}
            else {"ended": True}
        )
        return 0, json.dumps(result).encode(), b""

    async def interactive(command: str) -> None:
        calls.append(shlex.split(command)[-2])

    async def confirm() -> None:
        calls.append("consent")

    monkeypatch.setattr(persistent, "confirm_linger", confirm)

    async def scenario() -> None:
        controller = persistent.PersistentSession(
            mux, interactive, 0.01, application_environment=(("APP_MODE", "explicit"),)
        )
        session = await controller.identify(("application",))
        assert session == record["log_session"]
        controller.journal.flush(session)
        await controller.start(("application",), ("server",))
        assert await controller.wait() == 0

    asyncio.run(scenario())
    assert calls == [
        "identify",
        "probe",
        "consent",
        "enable-linger",
        "probe",
        "ensure",
        "status",
    ]


@pytest.mark.asyncio
async def test_large_launch_payload_is_sent_once_and_rebuilt_remotely(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = (
        sys.executable,
        "\n" * (MAX_APPLICATION_BYTES - len(sys.executable.encode())),
    )
    name = "APP_VALUE"
    values = ((name, "\n" * (MAX_APPLICATION_ENVIRONMENT_BYTES - len(name) - 2)),)
    server = build_server_argv(
        application,
        "fixture",
        DEFAULT_ENCODING_PROFILE,
        DEFAULT_CLIPBOARD_POLICY,
        persistent=True,
        application_environment=values,
    )
    monkeypatch.setattr(
        agent, "run", lambda *argv: subprocess.CompletedProcess(argv, 0, "", "")
    )

    async def mux(command: str, _timeout: float) -> tuple[int, bytes, bytes]:
        # SSH and the remote shell must each be able to pass this as one argument.
        process = await asyncio.create_subprocess_exec("/usr/bin/true", command)
        assert await asyncio.wait_for(process.wait(), 5) == 0
        source, action, encoded = shlex.split(command)[-3:]
        assert action == "ensure"
        payload = json.loads(base64.b64decode(encoded))
        for field in ("server", "requested_server"):
            assert "--start-child=" in payload[field]
        result = agent.ensure(payload, source)
        return 0, json.dumps(result).encode(), b""

    async def interactive(_command: str) -> None:
        raise AssertionError("unexpected interactive command")

    controller = persistent.PersistentSession(
        mux, interactive, 0.01, application_environment=values
    )
    controller._prepared = True
    controller.journal.flush(agent.log_session(agent.digest(list(application))))
    try:
        await controller.start(application, server, requested_server=server)
        record = agent.read_record(registry, controller.record["key"])
        assert record is not None
        child = next(
            arg.partition("=")[2]
            for arg in record["server"]
            if arg.startswith("--start-child=")
        )
        assert json.loads(base64.b64decode(shlex.split(child)[-2])) == {
            "argv": list(application),
            "environment": dict(values),
        }
    finally:
        controller.journal.close()


@pytest.mark.parametrize("exit_code", [0, 23, None])
def test_supervisor_reaps_notification_bus_and_cleans_after_exit_or_bus_failure(
    registry: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int | None
) -> None:
    key, token = "a" * 64, "b" * 32
    record = {
        "schema": agent.SCHEMA,
        "key": key,
        "log_session": "d" * 64,
        "token": token,
        "cwd": str(registry),
        "environment": {},
        "server": [
            sys.executable,
            "-c",
            (
                "import os,sys,time;from pathlib import Path;"
                "Path('bus-pid').write_text(os.environ.get('DBUS_SESSION_BUS_PID','0'));"
                f"print('wayland-4',flush=True);time.sleep(0.1);sys.exit({exit_code})"
            ),
        ],
    }
    agent.write_record(registry, record)
    writes: list[dict[str, Any]] = []
    original = agent.write_record

    def capture(root: Path, value: dict[str, Any]) -> None:
        writes.append(dict(value))
        original(root, value)

    monkeypatch.setattr(agent, "write_record", capture)
    monkeypatch.setenv("INVOCATION_ID", "c" * 32)
    monkeypatch.chdir(registry)
    if exit_code is None:
        monkeypatch.setattr(
            "elsewindow.session_bus.DBUS_DAEMON", str(registry / "missing-daemon")
        )
    signals = {
        selected: signal.getsignal(selected)
        for selected in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    }
    try:
        agent.serve({"key": key, "token": token})
    finally:
        for selected, handler in signals.items():
            signal.signal(selected, handler)
    assert agent.read_record(registry, key) is None
    assert writes[-1]["display"] == "wayland-4"
    assert writes[-1]["worker_start"] == agent.process_start(os.getpid())
    assert writes[-1]["invocation"] == "c" * 32
    assert agent.process_start(writes[-1]["xpra_pid"]) == ""
    bus_pid = int((registry / "bus-pid").read_text())
    if exit_code is None:
        assert bus_pid == 0
    else:
        assert bus_pid > 0 and agent.process_start(bus_pid) == ""


def test_linger_queries_and_activation_are_uid_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    enabled = False

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        nonlocal enabled
        commands.append(argv)
        if "enable-linger" in argv:
            enabled = True
        return subprocess.CompletedProcess(argv, 0, "yes\n" if enabled else "no\n", "")

    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(agent.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(agent, "runtime_directory", lambda: Path("/private/runtime"))
    assert agent.prerequisites() == {"linger": False, "manager": False}
    assert agent.enable_linger() == {"linger": True}
    assert agent.prerequisites() == {"linger": True, "manager": True}
    assert (
        "/usr/bin/loginctl",
        "--no-ask-password",
        "enable-linger",
        str(os.getuid()),
    ) in commands
    assert commands[-1] == ("/usr/bin/systemctl", "--user", "show-environment")


def test_linger_sudo_fallback_does_not_capture_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter((False, True))
    monkeypatch.setattr(agent, "linger_enabled", lambda: next(values))
    monkeypatch.setattr(agent.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(
        agent, "run", lambda *argv: subprocess.CompletedProcess(argv, 1, "", "")
    )
    calls = []

    def sudo(
        command: tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent.subprocess, "run", sudo)
    assert agent.enable_linger() == {"linger": True}
    assert calls == [
        (
            ("/usr/bin/sudo", "/usr/bin/loginctl", "enable-linger", str(os.getuid())),
            {"check": False},
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"not-json",
        b'{"error":"unknown","message":"private secret"}',
        b'{"error":"persistent_identity_mismatch","message":"private secret"}',
    ],
)
def test_controller_rejects_invalid_replies_without_echoing_remote_data(
    payload: bytes,
) -> None:
    async def mux(_command: str, _timeout: float) -> tuple[int, bytes, bytes]:
        return 1, payload, b"private stderr"

    async def interactive(_command: str) -> None:
        pytest.fail("unexpected interactive mutation")

    async def scenario() -> None:
        with pytest.raises(SSHError) as raised:
            await persistent.PersistentSession(mux, interactive, 1).call("probe", {})
        assert "private" not in str(raised.value)

    asyncio.run(scenario())


def test_no_terminal_cannot_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: Any) -> int:
        raise OSError("no controlling terminal")

    monkeypatch.setattr(persistent.os, "open", unavailable)
    with pytest.raises(SSHError) as raised:
        persistent.open_terminal()
    assert raised.value.code == "persistent_linger_consent_required"


def test_probe_fails_closed_without_systemd(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agent.os, "access", lambda _path, _mode: False)
    assert agent.main(["source", "probe", agent.encode({})]) == 1
    assert (
        json.loads(capsys.readouterr().out)["error"] == "persistent_systemd_unavailable"
    )
    monkeypatch.setattr(agent.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(
        agent,
        "run",
        lambda *argv: subprocess.CompletedProcess(argv, 1, "", "private diagnostic"),
    )
    with pytest.raises(agent.AgentError, match="cannot query"):
        agent.linger_enabled()
    with pytest.raises(agent.AgentError, match="cannot inspect"):
        agent.unit_state("a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", (False, True))
async def test_missing_persistence_only_falls_back_without_recorded_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], recorded: bool
) -> None:
    async def mux(_command: str, _timeout: float) -> tuple[int, bytes, bytes]:
        pytest.fail("unexpected SSH command")

    async def interactive(_command: str) -> None:
        pytest.fail("missing prerequisites never grant consent")

    controller = persistent.PersistentSession(
        mux, interactive, 1, application_environment=(("PATH", "/application-bin"),)
    )
    calls = []

    async def call(action: str, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(action)
        if action == "probe":
            raise SSHError("persistent_systemd_unavailable", "missing")
        assert action == "ordinary-fallback" and request["application"] == [
            "application"
        ]
        assert request["application_environment"] == {"PATH": "/application-bin"}
        return {"allowed": not recorded}

    monkeypatch.setattr(controller, "call", call)
    try:
        if recorded:
            with pytest.raises(SSHError, match="missing"):
                await controller.prepare(("application",))
        else:
            assert await controller.prepare(("application",)) is False
        assert calls == ["probe", "ordinary-fallback"]
        output = capsys.readouterr().err
        assert ("no duplicate" if recorded else "systemd libpam-systemd") in output
        if not recorded:
            assert "disconnecting the client or SSH will stop" in output
    finally:
        controller.journal.close()


def test_ordinary_fallback_checks_current_secure_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "elsewindow-persistent"
    root.mkdir(mode=0o700)
    original = agent.secure_directory
    monkeypatch.setattr(
        agent,
        "secure_directory",
        lambda path: original(root if path.name == root.name else tmp_path),
    )
    application = [sys.executable, "-c", "pass"]
    payload = {"application": application}
    key = agent.digest(agent.canonical_application(application))
    assert agent.ordinary_fallback(payload) == {"allowed": True}
    agent.write_record(root, {"schema": agent.SCHEMA, "key": key, "token": "a" * 32})
    assert agent.ordinary_fallback(payload) == {"allowed": False}
    (root / f"{key}.json").chmod(0o644)
    with pytest.raises(agent.AgentError, match="unsafe"):
        agent.ordinary_fallback(payload)
