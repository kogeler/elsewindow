# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Run one real Xpra lifecycle case through production SSH and profile code."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import shlex
import signal
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ssh_wrapper.connection import ConnectionSpec
from ssh_wrapper.errors import SSHError

from elsewindow.config import DEFAULT_CLIPBOARD_POLICY, XpraConfig
from elsewindow.live_config import DEFAULT_ENCODING_PROFILE, load_network_profiles
from elsewindow.session import XpraSession, build_xpra_command_argv
from elsewindow.xpra_runtime import prepared_launcher
from tests.live_support.journal import LIVE_LOG_LEVELS
from tests.live_support.process import LIVE_EVIDENCE_PREFIX, TARGET_ALIAS
from tests.live_support.xpra_target import (
    ABRUPT_MARKER,
    OWNED_MARKER,
    OWNED_TITLE,
    PERSISTENT_DISCONNECTS,
    PERSISTENT_MARKER,
    REMOTE_APP,
)

ATTACH_TIMEOUT = 60.0
CLEANUP_TIMEOUT = 20.0
LIVE_DIAGNOSTIC_CHARACTERS = 8 * 1024


def verify_packaged_product() -> None:
    """Fail unless this live case imports the clean-installed wheel."""
    import elsewindow

    installed = Path(os.environ["ELSEWINDOW_LIVE_INSTALLED_ROOT"]).resolve()
    package = Path(elsewindow.__file__).resolve()
    if not package.is_relative_to(installed):
        raise RuntimeError("live case did not import the clean-installed distribution")
    if importlib.metadata.version("elsewindow") != elsewindow.__version__:
        raise RuntimeError("live package metadata and runtime version differ")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=("detach", "abrupt", "linger-declined", "linger", "persistent"),
        required=True,
    )
    parser.add_argument("--ssh-path", type=Path, required=True)
    parser.add_argument("--false-path", type=Path, required=True)
    parser.add_argument("--xpra-path", type=Path, required=True)
    parser.add_argument(
        "--target-ssh-port", type=int, choices=range(1, 65536), required=True
    )
    parser.add_argument("--verify-local-window", action="store_true")
    return parser.parse_args()


def session_config(arguments: argparse.Namespace) -> XpraConfig:
    marker = case_marker(arguments.case)
    return XpraConfig(
        connection=ConnectionSpec.from_alias(TARGET_ALIAS),
        application=(REMOTE_APP, marker, OWNED_TITLE),
        encoding_profile=DEFAULT_ENCODING_PROFILE,
        network_profile=load_network_profiles()[0],
        clipboard=DEFAULT_CLIPBOARD_POLICY,
        connect_timeout=30,
        ready_timeout=45,
        probe_timeout=8,
        poll_interval=1,
        heartbeat_interval=2,
        lease_timeout=8,
        grace_timeout=3,
        ssh_path=arguments.ssh_path,
        false_path=arguments.false_path,
        xpra_path=prepared_launcher(arguments.xpra_path),
        persistent=arguments.case not in {"detach", "abrupt"},
        log_level=LIVE_LOG_LEVELS[arguments.case],
    )


def case_marker(case: str) -> str:
    if case == "abrupt":
        return ABRUPT_MARKER
    return OWNED_MARKER if case == "detach" else PERSISTENT_MARKER


def print_session_diagnostics(session: XpraSession) -> None:
    """Print bounded local-only tails without exposing private runtime paths."""
    streams = {
        "client stderr": session.client_stderr.text(),
        "client stdout": session.client_stdout.text(),
    }
    if session.remote is not None:
        streams["remote stderr"] = session.remote.stderr_tail.text()
        streams["remote stdout"] = session.remote.stdout_tail.text()
    private_paths = tuple(
        str(path)
        for path in (
            session._wrapper,
            session.master.control_path,
            session.master.runtime_dir,
        )
        if path is not None
    )
    for label, raw in streams.items():
        if not raw:
            continue
        for private_path in private_paths:
            raw = raw.replace(private_path, "<private-path>")
        printable = "\n".join(
            "".join(character for character in line if character.isprintable())
            for line in raw.splitlines()
        )
        print(
            f"live Xpra {label} tail:\n{printable[-LIVE_DIAGNOSTIC_CHARACTERS:]}",
            file=sys.stderr,
        )


def verify_master_argv(session: XpraSession) -> None:
    process = session.master.process
    if process is None or process.returncode is not None:
        raise RuntimeError("the owned SSH master is not running")
    argv = Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")
    if any(item.startswith((b"-L", b"-R", b"-D")) for item in argv):
        raise RuntimeError("the owned SSH master contains a forwarding option")
    for required in (
        b"ClearAllForwardings=yes",
        b"ForwardAgent=no",
        b"ForwardX11=no",
    ):
        if required not in argv:
            raise RuntimeError("the owned SSH master lacks an isolation option")


async def remote_identity_ready(
    session: XpraSession,
    marker: str,
    expected_display: str,
    *,
    diagnostic: dict[str, bool | int | str] | None = None,
) -> bool:
    command = shlex.join(
        (
            "python3",
            "-c",
            """\
import os
import sys

marker, expected_display = sys.argv[1:]
try:
    fields = open(marker, encoding="ascii").read().split()
    pid_text, recorded_start, display = fields
    pid = int(pid_text)
    stat_text = open(f"/proc/{pid}/stat", encoding="ascii").read()
    closing = stat_text.rfind(")")
    actual_start = stat_text[closing + 2:].split()[19]
    command = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\\0", 1)[0]
except (FileNotFoundError, OSError, ValueError, IndexError) as error:
    print(type(error).__name__)
    raise SystemExit(1)
print(f"display={display}, start-match={recorded_start == actual_start}, command={os.path.basename(os.fsdecode(command))}")
raise SystemExit(
    0 if display == expected_display and recorded_start == actual_start
    and os.path.basename(os.fsdecode(command)) == "python3" else 1
)
""",
            marker,
            expected_display,
        )
    )
    status, stdout, _stderr = await session._run_mux(command)
    if diagnostic is not None:
        diagnostic["remote_identity"] = stdout.decode(
            "utf-8", errors="replace"
        ).strip()[:256]
    return status == 0


async def verify_no_remote_tcp_listener(
    session: XpraSession, target_ssh_port: int
) -> None:
    status, stdout, _stderr = await session._run_mux("ss -Hltn")
    if status != 0:
        raise RuntimeError("cannot inspect remote TCP listeners")
    listeners = stdout.decode("utf-8", errors="replace")
    expected_suffix = f":{target_ssh_port} "
    unexpected = [
        line for line in listeners.splitlines() if expected_suffix not in f"{line} "
    ]
    if unexpected or "15000" in listeners:
        raise RuntimeError("the Xpra target exposes an unexpected TCP listener")


async def local_window_state(session: XpraSession) -> tuple[bool, str]:
    status, stdout, _stderr = await session._run_local(
        ["xwininfo", "-root", "-tree"], session.config.probe_timeout
    )
    text = stdout.decode("utf-8", errors="replace")
    named_windows = " | ".join(
        line.strip() for line in text.splitlines() if '"' in line
    )[-2048:]
    return status == 0 and OWNED_TITLE in text, named_windows


def window_pixels_sent(output: bytes) -> bool:
    for raw_line in output.decode("utf-8", errors="replace").splitlines():
        key, separator, value = raw_line.partition("=")
        if not separator or not key.endswith(".damage.packets_sent"):
            continue
        try:
            if int(value) > 0:
                return True
        except ValueError:
            continue
    return False


def window_title_present(output: bytes) -> bool:
    return any(
        separator and key.endswith(".title") and value == OWNED_TITLE
        for raw_line in output.decode("utf-8", errors="replace").splitlines()
        for key, separator, value in (raw_line.partition("="),)
    )


async def wait_for_attach(
    session: XpraSession,
    task: asyncio.Task[int],
    *,
    marker: str,
    verify_local_window: bool,
    target_ssh_port: int,
) -> None:
    deadline = asyncio.get_running_loop().time() + ATTACH_TIMEOUT
    state: dict[str, bool | int | str] = {
        "client_running": False,
        "local_window": False,
        "remote_application": False,
    }
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            await task
            raise RuntimeError("the Xpra session exited before attach verification")
        client = session.client
        state["client_running"] = client is not None and client.returncode is None
        display = session._remote_display or ""
        if state["client_running"] and display:
            state["remote_application"] = await remote_identity_ready(
                session, marker, display, diagnostic=state
            )
        if state["client_running"] and state["remote_application"]:
            wrapper = session._wrapper
            if wrapper is None:
                raise RuntimeError("the Xpra session did not create its mux wrapper")
            status, stdout, _stderr = await session._run_mux(
                shlex.join(
                    build_xpra_command_argv(
                        "server",
                        "info",
                        "xpra",
                        display=display,
                    )
                ),
                session.config.probe_timeout,
            )
            state["info_status"] = status
            state["info_title"] = window_title_present(stdout)
            state["pixels_sent"] = window_pixels_sent(stdout)
            local_window, local_tree = await local_window_state(session)
            state["local_window"] = not verify_local_window or local_window
            if verify_local_window:
                state["local_tree"] = local_tree
            if (
                status == 0
                and state["info_title"]
                and state["pixels_sent"]
                and state["local_window"]
            ):
                verify_master_argv(session)
                await verify_no_remote_tcp_listener(session, target_ssh_port)
                return
        await asyncio.sleep(0.2)
    raise RuntimeError(
        "timed out waiting for the Xpra client on "
        f"{session._remote_display or 'unknown display'}: "
        f"{json.dumps(state, sort_keys=True)}"
    )


async def disconnect(session: XpraSession, task: asyncio.Task[int], mode: str) -> None:
    """Exercise the same disconnect operations in both lifetime modes."""
    if mode == "detach":
        if session._wrapper is None:
            raise RuntimeError("the detach case has no mux wrapper")
        status, _stdout, _stderr = await session._run_local(
            list(
                build_xpra_command_argv(
                    "client",
                    "detach",
                    session.config.xpra_path,
                    uri=session._uri(),
                    ssh_wrapper=session._wrapper,
                )
            ),
            session.config.probe_timeout,
        )
        if status != 0 or await asyncio.wait_for(task, CLEANUP_TIMEOUT) != 0:
            raise RuntimeError("the real Xpra client could not detach cleanly")
        return
    if mode == "cancel":
        task.cancel()
        try:
            await asyncio.wait_for(task, CLEANUP_TIMEOUT)
        except asyncio.CancelledError:
            return
        raise RuntimeError("session cancellation was ignored")
    if mode == "master-close":
        await session.master.close()
    else:
        process = session.client if mode == "client-kill" else session.master.process
        if process is None:
            raise RuntimeError("the loss case has no owned process")
        os.killpg(process.pid, signal.SIGKILL)
    try:
        await asyncio.wait_for(task, CLEANUP_TIMEOUT)
    except SSHError as error:
        if error.code not in {
            "connection_lost",
            "xpra_client_failed",
            "xpra_server_exited",
            "persistent_operation_failed",
        }:
            raise
    else:
        raise RuntimeError("process loss did not fail the local session")


async def persistent_identity(session: XpraSession, marker: str) -> dict[str, object]:
    persistent = session.persistent
    if persistent is None or not await persistent.refresh():
        raise RuntimeError("the persistent user service is not running")
    status, stdout, _stderr = await session._run_mux(shlex.join(("cat", marker)))
    if status:
        raise RuntimeError("the persistent application marker is missing")
    record = persistent.record
    app_pid = stdout.decode().split()[0]
    unit = f"elsewindow-{record['key']}.service"
    for pid in (app_pid, str(record["xpra_pid"])):
        status, cgroup, _stderr = await session._run_mux(
            shlex.join(("cat", f"/proc/{pid}/cgroup"))
        )
        if status or unit not in cgroup.decode():
            raise RuntimeError(
                "the persistent application is outside its owned user-service cgroup"
            )
    return {
        "application": stdout.decode().strip(),
        **{
            name: record[name]
            for name in ("key", "token", "xpra_pid", "xpra_start", "display")
        },
    }


async def exit_application(
    session: XpraSession, task: asyncio.Task[int], marker: str, code: int
) -> None:
    if not await remote_identity_ready(session, marker, session._remote_display or ""):
        raise RuntimeError("refusing to signal an unverified fixture")
    status, _stdout, _stderr = await session._run_mux(
        shlex.join(
            (
                "python3",
                "-c",
                "import sys;open(sys.argv[1], 'x').write(sys.argv[2]+'\\n')",
                f"{marker}.exit",
                str(code),
            )
        )
    )
    if status or await asyncio.wait_for(task, CLEANUP_TIMEOUT) != 0:
        raise RuntimeError("application exit did not close the persistent session")


async def cancel_driver(task: asyncio.Task[int]) -> None:
    if not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, SSHError):
            pass


async def concurrent_session(
    config: XpraConfig, arguments: argparse.Namespace
) -> tuple[str, str]:
    """Reuse the same fixture and production lifecycle alongside the first copy."""
    marker = case_marker(arguments.case) + "-concurrent"
    session = XpraSession(
        replace(config, application=(REMOTE_APP, marker, OWNED_TITLE))
    )
    task = asyncio.create_task(session.run())
    try:
        await wait_for_attach(
            session,
            task,
            marker=marker,
            verify_local_window=arguments.verify_local_window,
            target_ssh_port=arguments.target_ssh_port,
        )
        display = session._remote_display
        if display is None:
            raise RuntimeError("concurrent session did not publish its display")
        await exit_application(session, task, marker, 0)
        return session.session_id, display
    except Exception:
        print_session_diagnostics(session)
        raise
    finally:
        await cancel_driver(task)


async def run_case(arguments: argparse.Namespace) -> dict[str, object]:
    config = session_config(arguments)
    marker = case_marker(arguments.case)
    sessions = []
    provisional_sessions = []
    if arguments.case == "linger-declined":
        session = XpraSession(config)
        initial_id = session.session_id
        try:
            await session.run()
        except SSHError as error:
            if error.code == "persistent_linger_declined":
                return {
                    "declined": True,
                    "sessions": [session.session_id],
                    "provisional_sessions": [initial_id]
                    if initial_id != session.session_id
                    else [],
                    "log_level": config.log_level,
                }
            raise
        raise RuntimeError("declining linger unexpectedly started a session")
    modes = (
        (*PERSISTENT_DISCONNECTS, "exit-zero", "exit-error")
        if arguments.case == "persistent"
        else ("exit-zero",)
        if arguments.case == "linger"
        else (arguments.case,)
    )
    identity: dict[str, object] | None = None
    displays = []
    for mode in modes:
        session = XpraSession(config)
        initial_id = session.session_id
        task = asyncio.create_task(session.run())
        try:
            await wait_for_attach(
                session,
                task,
                marker=marker,
                verify_local_window=arguments.verify_local_window,
                target_ssh_port=arguments.target_ssh_port,
            )
            display = session._remote_display
            if display is None:
                raise RuntimeError("the Xpra server did not publish its display")
            displays.append(display)
            if session.session_id != initial_id:
                provisional_sessions.append(initial_id)
            if config.persistent and sessions and session.session_id != sessions[0]:
                raise RuntimeError(
                    "persistent reconnect changed the shared log identity"
                )
            sessions.append(session.session_id)
            if arguments.case == "detach":
                other_id, other_display = await concurrent_session(config, arguments)
                if other_id == session.session_id or other_display == display:
                    raise RuntimeError(
                        "concurrent sessions share a log or display identity"
                    )
                if task.done() or not await remote_identity_ready(
                    session, marker, display
                ):
                    raise RuntimeError(
                        "closing the other session affected the first copy"
                    )
                sessions.append(other_id)
                displays.append(other_display)
            if config.persistent:
                current = await persistent_identity(session, marker)
                if identity is not None:
                    if mode == "exit-error":
                        if current["token"] == identity["token"]:
                            raise RuntimeError("a completed session was reused")
                    elif current != identity:
                        raise RuntimeError(
                            "reconnect replaced the running application or server"
                        )
                identity = current
                if arguments.case == "persistent" and mode == PERSISTENT_DISCONNECTS[0]:
                    channel = session._remote_logs[-1]
                    if channel.process is None:
                        raise RuntimeError("the persistent log channel was not started")
                    channel.process.terminate()
                    await asyncio.wait_for(channel.process.wait(), 5)
                    if channel.reader is not None:
                        await asyncio.wait_for(channel.reader, 5)
                    if (
                        task.done()
                        or await persistent_identity(session, marker) != current
                    ):
                        raise RuntimeError(
                            "log channel loss changed the persistent application"
                        )
            if mode.startswith("exit-"):
                await exit_application(
                    session, task, marker, 0 if mode == "exit-zero" else 23
                )
            else:
                await disconnect(session, task, mode)
            if config.persistent:
                # Exceed the ordinary ownership lease with every SSH channel gone.
                await asyncio.sleep(config.lease_timeout + config.grace_timeout + 1)
        except Exception:
            print_session_diagnostics(session)
            raise
        finally:
            await cancel_driver(task)
    return {
        "display": displays[-1],
        "displays": displays,
        "identity": identity,
        "connections": len(modes) + (arguments.case == "detach"),
        "sessions": sessions,
        "provisional_sessions": provisional_sessions,
        "log_level": config.log_level,
    }


def main() -> int:
    try:
        verify_packaged_product()
        arguments = parse_arguments()
        evidence = asyncio.run(run_case(arguments))
        print("\n" + LIVE_EVIDENCE_PREFIX + json.dumps(evidence, sort_keys=True))
    except (OSError, RuntimeError, SSHError, ValueError) as error:
        print(f"live Xpra case failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
