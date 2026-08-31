# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Lifecycle and exact-argv tests for remote Xpra sessions."""

from __future__ import annotations

import asyncio
import os
import shlex
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from ssh_wrapper.connection import ConnectionSpec
from ssh_wrapper.errors import SSHError

import elsewindow.session as session_module
from elsewindow.config import XpraConfig
from elsewindow.live_config import (
    DEFAULT_ENCODING_PROFILE,
    command_cli_options,
    encoding_profile_names,
    load_live_cli,
    load_network_profiles,
    network_profile,
    production_encoding,
    production_transport_options,
    static_cli_options,
)
from elsewindow.session import XpraSession, build_xpra_command_argv


def _config(
    tmp_path: Path,
) -> XpraConfig:
    executable = tmp_path / "executable"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return XpraConfig(
        connection=ConnectionSpec.from_alias("workstation"),
        application=("spotify", "--profile", "value with spaces", "semi;colon"),
        encoding_profile=DEFAULT_ENCODING_PROFILE,
        network_profile=load_network_profiles()[0],
        connect_timeout=2,
        ready_timeout=0.2,
        probe_timeout=0.1,
        poll_interval=0.01,
        heartbeat_interval=0.05,
        lease_timeout=0.2,
        grace_timeout=0.05,
        ssh_path=executable,
        false_path=executable,
        xpra_path=executable,
    )


def _native_video_profile() -> str:
    return next(
        name for name in encoding_profile_names() if production_encoding(name) == "h264"
    )


def test_exact_server_metadata_probe_and_mirrored_default_profiles(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    session = XpraSession(config)
    session._wrapper = Path("/private/xpra-ssh")
    session._remote_display = "wayland-3"

    server = session.server_argv()
    probe = session.readiness_probe()
    attach = session.attach_argv()

    assert server == (
        "env",
        f"XPRA_WAYLAND_GPU={session_module._gpu_mode(config.encoding_profile)}",
        "xpra",
        "seamless",
        *session_module._production_server_base_options(),
        f"--session-name={session.session_name}",
        f"--start-child-after-connect={shlex.join(session.config.application)}",
        *static_cli_options("server", "lifecycle"),
        *production_transport_options("server", config.encoding_profile),
        *session_module.SERVER_SECURITY_OPTIONS,
    )
    assert not any(option.startswith("--start-child=") for option in server)
    assert not any(value.startswith(":") for value in server)
    assert session_module.SESSION_METADATA_PROBE in probe
    probe_argv = shlex.split(probe)
    assert probe_argv[-4:-1] == ["ready", "wayland-3", session.session_name]
    assert len(probe_argv[-1]) == 64
    assert "xpra id" not in probe
    assert "subprocess" not in session_module.SESSION_METADATA_PROBE
    assert attach == [
        str(config.xpra_path),
        "attach",
        session._uri(),
        f"--ssh={session._wrapper}",
        *static_cli_options("client", "base"),
        *network_profile(config.network_profile).client_options(),
        *production_transport_options("client", config.encoding_profile),
        *session_module.CLIENT_SECURITY_OPTIONS,
    ]
    combined = " ".join((*server, probe, *attach))
    assert "--debug=" not in combined
    assert "15000" not in combined
    assert "bind-tcp" not in combined
    assert all(token not in combined for token in (" -L ", " -R ", " -D "))
    assert not set(static_cli_options("server", "diagnostics")) & set(server)
    assert not set(static_cli_options("client", "diagnostics")) & set(attach)


def test_every_canonical_auxiliary_command_flows_to_runtime_argv() -> None:
    configured = load_live_cli()
    display = "wayland-7"
    uri = "ssh://example/wayland-7"
    wrapper = Path("/private/xpra-ssh")
    xpra = Path("/usr/bin/xpra")

    assembled = {
        ("server", "version"): build_xpra_command_argv("server", "version", xpra),
        ("server", "info"): build_xpra_command_argv(
            "server", "info", xpra, display=display
        ),
        ("client", "version"): build_xpra_command_argv("client", "version", xpra),
        ("client", "detach"): build_xpra_command_argv(
            "client", "detach", xpra, uri=uri, ssh_wrapper=wrapper
        ),
    }
    assert set(assembled) == {
        (role, command)
        for role, blocks in configured.items()
        for command in blocks["commands"]
    }
    assert assembled[("server", "version")] == (
        str(xpra),
        *command_cli_options("server", "version"),
    )
    assert assembled[("client", "version")] == (
        str(xpra),
        *command_cli_options("client", "version"),
    )
    assert assembled[("client", "detach")] == (
        str(xpra),
        "detach",
        uri,
        f"--ssh={wrapper}",
        *command_cli_options("client", "detach"),
    )

    info = assembled[("server", "info")]
    canonical_info = command_cli_options("server", "info")
    assert info[:2] == (str(xpra), "info")
    assert len(info[2:]) == len(canonical_info)
    for canonical, production in zip(canonical_info, info[2:], strict=True):
        name = canonical.partition("=")[0]
        if name in session_module.SERVER_RUNTIME_PATHS:
            assert production == f"{name}={session_module.SERVER_RUNTIME_PATHS[name]}"
        elif not canonical.startswith("-"):
            assert production == display
        else:
            assert production == canonical


@pytest.mark.parametrize(
    ("role", "command", "context"),
    (
        ("server", "version", {"display": "wayland-1"}),
        ("server", "info", {}),
        ("client", "detach", {"uri": "ssh://example/wayland-1"}),
    ),
)
def test_canonical_auxiliary_commands_reject_incomplete_context(
    role: str, command: str, context: dict[str, str]
) -> None:
    with pytest.raises(RuntimeError, match="command context"):
        build_xpra_command_argv(role, command, "xpra", **context)


def test_h264_uses_the_mirrored_adaptive_alpha_and_selected_network_profile(
    tmp_path: Path,
) -> None:
    encoding_profile = _native_video_profile()
    default_network, profiles = load_network_profiles()
    selected_network = next(name for name in profiles if name != default_network)
    config = replace(
        _config(tmp_path),
        encoding_profile=encoding_profile,
        network_profile=selected_network,
    )
    session = XpraSession(config)
    session._wrapper = Path("/private/xpra-ssh")
    session._remote_display = "wayland-7"

    server = session.server_argv()
    attach = session.attach_argv()

    assert server == (
        "env",
        f"XPRA_WAYLAND_GPU={session_module._gpu_mode(encoding_profile)}",
        "xpra",
        "seamless",
        *session_module._production_server_base_options(),
        f"--session-name={session.session_name}",
        f"--start-child-after-connect={shlex.join(session.config.application)}",
        *static_cli_options("server", "lifecycle"),
        *production_transport_options("server", encoding_profile),
        *session_module.SERVER_SECURITY_OPTIONS,
    )
    assert attach == [
        str(config.xpra_path),
        "attach",
        session._uri(),
        f"--ssh={session._wrapper}",
        *static_cli_options("client", "base"),
        *network_profile(selected_network).client_options(),
        *production_transport_options("client", encoding_profile),
        *session_module.CLIENT_SECURITY_OPTIONS,
    ]


def test_session_name_uses_the_normalized_application_basename(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        application=("/opt/zed.app/libexec/Zed Editor", "--foreground"),
    )

    session = XpraSession(config)

    assert session.application_name == "zed-editor"
    assert session.session_name == "zed-editor"


@pytest.mark.parametrize("process_style", ("direct", "interpreter", "title"))
def test_session_metadata_probe_validates_identity_without_connecting(
    tmp_path: Path, process_style: str
) -> None:
    runtime = tmp_path / "runtime"
    session_dir = runtime / "xpra" / "wayland-3"
    process_dir = tmp_path / "proc" / "4242"
    session_dir.mkdir(parents=True)
    process_dir.mkdir(parents=True)
    session_dir.chmod(0o750)

    owned = XpraSession(_config(tmp_path))
    session_name = owned.session_name
    argv = owned.server_argv()[2:]
    owned._remote_display = "wayland-3"
    expected_argv_sha256 = shlex.split(owned.readiness_probe())[-1]
    files = {
        "config": (
            f"mode=seamless\nsession-name={session_name}\nbackend=wayland\n"
            "unrelated=first\nunrelated=second\n"
        ),
        "cmdline": "\n".join(argv) + "\n",
        "server.pid": "4242\n",
    }
    for name, content in files.items():
        path = session_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o640)
    process_argv = argv
    if process_style == "interpreter":
        process_argv = ("/usr/bin/python3", *argv)
    if process_style == "title":
        process_argv = (" ".join(argv),)
    (process_dir / "cmdline").write_bytes(
        b"\0".join(value.encode() for value in process_argv) + b"\0"
    )

    probe = session_module.SESSION_METADATA_PROBE.replace(
        'PROC_ROOT = "/proc"', f"PROC_ROOT = {str(tmp_path / 'proc')!r}", 1
    )
    environment = os.environ.copy()
    environment["XDG_RUNTIME_DIR"] = str(runtime)

    def run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            (sys.executable, "-c", probe, *arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=environment,
            check=False,
        )

    with socket.socket(socket.AF_UNIX) as server_socket:
        socket_path = session_dir / "socket"
        server_socket.bind(str(socket_path))
        socket_path.chmod(0o600)

        ready = run(
            "ready",
            "wayland-3",
            session_name,
            expected_argv_sha256,
        )
        mismatch = run(
            "ready",
            "wayland-3",
            "elsewindow-other",
            expected_argv_sha256,
        )
        (session_dir / "config").write_text(
            f"mode=seamless\nsession-name={session_name}\n"
            "backend=wayland\nbackend=wayland\n",
            encoding="utf-8",
        )
        (session_dir / "config").chmod(0o640)
        ambiguous = run(
            "ready",
            "wayland-3",
            session_name,
            expected_argv_sha256,
        )

    assert (ready.returncode, ready.stdout, ready.stderr) == (0, b"", b"")
    assert mismatch.returncode == 2
    assert ambiguous.returncode == 2
    assert ambiguous.stderr == b"metadata-duplicate-config-field\n"


def test_wayland_displayfd_selects_the_actual_session_uri(tmp_path: Path) -> None:
    session = XpraSession(_config(tmp_path))
    session._wrapper = Path("/private/xpra-ssh")
    tail = session_module.BoundedTail()
    remote = type(
        "Remote",
        (),
        {
            "returncode": None,
            "stdout_head": b"wayland-3\napplication output\n",
            "stdout_tail": tail,
        },
    )()
    session.remote = remote  # type: ignore[assignment]

    session._capture_remote_display()

    readiness = session.readiness_probe()
    assert " ready wayland-3 " in readiness
    assert f" {session.session_name} " in readiness
    assert session.attach_argv()[2] == "ssh://workstation/wayland-3"


@pytest.mark.asyncio
async def test_readiness_uses_remote_metadata_without_an_xpra_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = XpraSession(_config(tmp_path))
    session._wrapper = tmp_path / "wrapper"
    session.remote = type(
        "Remote",
        (),
        {
            "returncode": None,
            "stdout_head": b"wayland-3\n",
            "stdout_tail": session_module.BoundedTail(),
        },
    )()  # type: ignore[assignment]

    programs: list[str] = []

    async def probe(program: str, _timeout: float) -> tuple[int, bytes, bytes]:
        programs.append(program)
        return 0, b"", b""

    async def local(_argv: list[str], _timeout: float) -> tuple[int, bytes, bytes]:
        raise AssertionError("readiness must not start an Xpra client")

    monkeypatch.setattr(session, "_run_mux", probe)
    monkeypatch.setattr(session, "_run_local", local)
    await session._wait_ready()
    assert len(programs) == 1
    assert session_module.SESSION_METADATA_PROBE in programs[0]

    async def wrong(_program: str, _timeout: float) -> tuple[int, bytes, bytes]:
        return 2, b"", b""

    monkeypatch.setattr(session, "_run_mux", wrong)
    with pytest.raises(SSHError, match="metadata does not match"):
        await session._wait_ready()


@pytest.mark.asyncio
async def test_capability_checks_use_public_cli_instead_of_version_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = XpraSession(_config(tmp_path))
    local_help = " ".join(session._local_required_options()).encode()

    local_calls: list[list[str]] = []

    async def local(argv: list[str], _timeout: float) -> tuple[int, bytes, bytes]:
        local_calls.append(argv)
        return 0, local_help, b""

    monkeypatch.setattr(session, "_run_local", local)
    await session._check_local_capabilities()

    remote_calls: list[str] = []

    async def remote(program: str, _timeout: float = 15) -> tuple[int, bytes, bytes]:
        remote_calls.append(program)
        return 0, " ".join(session._remote_required_options()).encode(), b""

    monkeypatch.setattr(session, "_run_mux", remote)
    await session._check_remote_capabilities()

    assert local_calls == [[str(session.config.xpra_path), "attach", "--help"]]
    assert remote_calls == ["command -v python3 >/dev/null && xpra seamless --help"]


@pytest.mark.asyncio
async def test_h264_capabilities_add_the_public_local_opengl_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoding_profile = _native_video_profile()
    session = XpraSession(replace(_config(tmp_path), encoding_profile=encoding_profile))
    local_help = " ".join(session._local_required_options()).encode()
    local_calls: list[list[str]] = []
    local_responses = iter(
        (
            (0, local_help, b""),
            (0, b"success=True\nsafe=True\n", b""),
        )
    )

    async def local(argv: list[str], _timeout: float) -> tuple[int, bytes, bytes]:
        local_calls.append(argv)
        return next(local_responses)

    monkeypatch.setattr(session, "_run_local", local)
    await session._check_local_capabilities()

    assert local_calls == [
        [str(session.config.xpra_path), "attach", "--help"],
        [str(session.config.xpra_path), "opengl"],
    ]

    remote_calls: list[str] = []

    async def remote(program: str, _timeout: float = 15) -> tuple[int, bytes, bytes]:
        remote_calls.append(program)
        return 0, " ".join(session._remote_required_options()).encode(), b""

    monkeypatch.setattr(session, "_run_mux", remote)
    await session._check_remote_capabilities()

    assert remote_calls == ["command -v python3 >/dev/null && xpra seamless --help"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_output",
    (
        b"success=False\nsafe=True\n",
        b"success=True\nsafe=False\n",
        b"unrelated=value\n",
    ),
)
async def test_native_video_local_opengl_probe_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_output: bytes,
) -> None:
    session = XpraSession(
        replace(_config(tmp_path), encoding_profile=_native_video_profile())
    )
    responses = iter(
        (
            (0, " ".join(session._local_required_options()).encode(), b""),
            (0, probe_output, b"private diagnostic"),
        )
    )

    async def local(_argv: list[str], _timeout: float) -> tuple[int, bytes, bytes]:
        return next(responses)

    monkeypatch.setattr(session, "_run_local", local)
    with pytest.raises(SSHError) as raised:
        await session._check_local_capabilities()

    assert raised.value.code == "xpra_incompatible"
    assert raised.value.message == "local Xpra OpenGL renderer is unavailable or unsafe"
    assert "private" not in raised.value.message


class _WaitProcess:
    def __init__(self, status: int, delay: float) -> None:
        self.status = status
        self.delay = delay

    async def wait(self) -> int:
        await asyncio.sleep(self.delay)
        return self.status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "remote", "master", "message"),
    [
        ((0, 0), (0, 1), (0, 1), None),
        ((23, 1), (0, 0), (0, 1), None),
        ((0, 1), (23, 0), (0, 1), "remote Xpra exited"),
        ((0, 1), (0, 1), (255, 0), "SSH master was lost"),
    ],
)
async def test_first_lifecycle_exit_controls_result(
    tmp_path: Path,
    client: tuple[int, float],
    remote: tuple[int, float],
    master: tuple[int, float],
    message: str | None,
) -> None:
    session = XpraSession(_config(tmp_path))
    session.client = _WaitProcess(*client)  # type: ignore[assignment]
    session.remote = _WaitProcess(*remote)  # type: ignore[assignment]
    session.master.process = _WaitProcess(*master)  # type: ignore[assignment]

    if message is None:
        assert await session._wait_lifecycle() == 0
    else:
        with pytest.raises(SSHError, match=message):
            await session._wait_lifecycle()


@pytest.mark.asyncio
async def test_client_failure_reports_bounded_redacted_diagnostic(
    tmp_path: Path,
) -> None:
    session = XpraSession(_config(tmp_path))
    private_runtime = tmp_path / "private-runtime"
    session.master.runtime_dir = private_runtime
    session.master.control_path = private_runtime / "mux.sock"
    session._wrapper = private_runtime / "xpra-ssh"
    session.client = _WaitProcess(18, 0)  # type: ignore[assignment]
    session.remote = _WaitProcess(0, 1)  # type: ignore[assignment]
    session.master.process = _WaitProcess(0, 1)  # type: ignore[assignment]
    session.client_stderr.append(
        (
            "\x1b[31mnoise without a useful marker\x1b[0m\n"
            f"Warning: socket {session.master.control_path} disconnected\n"
            f"Error: connection failed through {session._wrapper}\n"
        ).encode()
    )

    with pytest.raises(SSHError) as raised:
        await session._wait_lifecycle()

    assert raised.value.code == "xpra_client_failed"
    assert raised.value.message == (
        "local Xpra client exited with status 18: "
        "Warning: socket <private-path> disconnected | "
        "Error: connection failed through <private-path>"
    )
    assert str(private_runtime) not in raised.value.message
    assert "\x1b" not in raised.value.message


@pytest.mark.asyncio
async def test_early_remote_failure_reports_bounded_diagnostic(
    tmp_path: Path,
) -> None:
    session = XpraSession(_config(tmp_path))
    session.remote = type(
        "Remote",
        (),
        {
            "returncode": 1,
            "stderr_tail": session_module.BoundedTail(
                data=b"noise\nError: failed to create wayland GPU renderer\n"
            ),
        },
    )()  # type: ignore[assignment]

    with pytest.raises(SSHError) as raised:
        await session._wait_ready()

    assert raised.value.code == "xpra_start_failed"
    assert raised.value.message == (
        "remote Xpra exited before becoming ready: "
        "Error: failed to create wayland GPU renderer"
    )


@pytest.mark.asyncio
async def test_run_cleans_remote_and_master_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = XpraSession(_config(tmp_path))
    events: list[str] = []

    async def record(name: str) -> None:
        events.append(name)

    monkeypatch.setattr(session, "_check_local_capabilities", lambda: record("local"))
    monkeypatch.setattr(session.master, "start", lambda: record("master-start"))
    monkeypatch.setattr(
        session.master,
        "create_mux_wrapper",
        lambda _name: tmp_path / "wrapper",
    )
    monkeypatch.setattr(
        session, "_check_remote_capabilities", lambda: record("remote-check")
    )
    monkeypatch.setattr(session, "_wait_ready", lambda: record("ready"))
    monkeypatch.setattr(session, "_start_client", lambda: record("client"))

    async def lifecycle() -> int:
        events.append("lifecycle")
        return 0

    monkeypatch.setattr(session, "_wait_lifecycle", lifecycle)
    monkeypatch.setattr(session.master, "close", lambda: record("master-close"))

    class FakeRemote:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return

        async def start(self) -> None:
            events.append("remote-start")

        async def close(self) -> None:
            events.append("remote-close")

    monkeypatch.setattr(session_module, "OwnedRemoteProcess", FakeRemote)

    assert await session.run() == 0
    assert events == [
        "local",
        "master-start",
        "remote-check",
        "remote-start",
        "ready",
        "client",
        "lifecycle",
        "remote-close",
        "master-close",
    ]
