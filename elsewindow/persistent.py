# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Client-side control of an SSH-independent, owned user service."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections.abc import Awaitable, Callable
from importlib.resources import files
from typing import Any

from ssh_wrapper.errors import SSHError

from . import _persistent_agent as agent
from .journal import DEFAULT_LOG_LEVEL, PRIORITIES, Journal, remote_source

MuxCommand = Callable[[str, float], Awaitable[tuple[int, bytes, bytes]]]
InteractiveCommand = Callable[[str], Awaitable[None]]
LINGER_PROMPT = (
    "elsewindow: remote linger is disabled. Enabling it keeps this account's "
    "user services running after logout. Enable linger? [y/N] "
)
ERRORS = {
    "persistent_systemd_unavailable": "persistent sessions require a working remote systemd user manager and loginctl",
    "persistent_linger_failed": "could not enable remote linger; ask the remote administrator",
    "persistent_unsafe_state": "persistent session state has unsafe ownership, permissions or contents",
    "persistent_identity_mismatch": "the existing persistent service does not match its recorded ownership",
    "persistent_configuration_mismatch": "the existing session uses different server, clipboard or logging settings, or an older helper; use compatible settings or exit the application first",
    "persistent_environment_mismatch": "the existing session uses different application environment values; reconnect with its original --env values or exit the application first",
    "journal_unavailable": "the remote system journal is unavailable",
    "persistent_busy": "the persistent service is still starting or stopping; retry shortly",
    "persistent_start_failed": "could not start the persistent user service; retry to inspect its state",
    "invalid_application": "the remote application is not a valid executable or its arguments exceed the limits",
    "invalid_application_environment": "the application environment has invalid or reserved names, invalid values, or exceeds the limits",
}


def _server_template(server: tuple[str, ...]) -> list[str]:
    # The remote agent rebuilds this child from the separate application payload.
    child_option = "--start-child="
    return [child_option if arg.startswith(child_option) else arg for arg in server]


def open_terminal() -> int:
    try:
        return os.open("/dev/tty", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise SSHError(
            "persistent_linger_consent_required",
            "remote linger is disabled; run --persistent from a terminal to approve enabling it, or ask the remote administrator",
        ) from error


async def confirm_linger() -> None:
    """Read explicit terminal consent without blocking cancellation or stdin."""
    descriptor = open_terminal()
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bytes] = loop.create_future()
    pending = bytearray()

    def readable() -> None:
        try:
            chunk = os.read(descriptor, 128)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        pending.extend(chunk)
        if not chunk or b"\n" in pending or len(pending) >= 128:
            loop.remove_reader(descriptor)
            if not answer.done():
                answer.set_result(bytes(pending))

    try:
        os.write(descriptor, LINGER_PROMPT.encode())
        loop.add_reader(descriptor, readable)
        response = (await answer).strip()
        if response not in {b"y", b"yes"}:
            raise SSHError(
                "persistent_linger_declined",
                "remote linger was not enabled",
            )
    finally:
        loop.remove_reader(descriptor)
        os.close(descriptor)


class PersistentSession:
    """Observe a service; closing this connection never stops that service."""

    def __init__(
        self,
        mux: MuxCommand,
        interactive: InteractiveCommand,
        poll_interval: float,
        *,
        log_level: str = DEFAULT_LOG_LEVEL,
        journal: Journal | None = None,
        application_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._mux = mux
        self._interactive = interactive
        self._poll_interval = poll_interval
        self.log_level = log_level
        self.application_environment = dict(application_environment)
        self.journal = journal or Journal(log_level, "client")
        self._source = remote_source(
            files("elsewindow").joinpath("_persistent_agent.py").read_text()
        )
        self.record: dict[str, Any] = {}
        self.identity: dict[str, str] = {}
        self._prepared = False

    def command(self, action: str, request: dict[str, Any]) -> str:
        return shlex.join(
            (
                "python3",
                "-c",
                agent.LOADER,
                self._source,
                action,
                agent.encode(
                    request
                    | {
                        "log_level": self.log_level,
                        "log_session": self.journal.session,
                        "log_deferred": self.journal.deferred,
                    }
                ),
            )
        )

    async def call(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        code, stdout, _stderr = await self._mux(
            self.command(action, request), agent.COMMAND_TIMEOUT * 2
        )
        try:
            if len(stdout) > agent.MAX_BYTES:
                raise ValueError("oversized response")
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise TypeError("invalid response")
            error = result.get("error")
            if isinstance(error, str) and error in ERRORS:
                raise SSHError(error, ERRORS[error])
            if code or error:
                raise ValueError("failed operation")
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            raise SSHError(
                "persistent_operation_failed", "the remote persistent operation failed"
            ) from error
        return result

    async def identify(self, application: tuple[str, ...]) -> str:
        result = await self.call(
            "identify",
            {
                "application": list(application),
                "application_environment": self.application_environment,
            },
        )
        for name in ("key", "log_session"):
            if (
                not isinstance(result.get(name), str)
                or agent.IDENTITY.fullmatch(result[name]) is None
            ):
                raise SSHError(
                    "persistent_operation_failed", "invalid persistent log session"
                )
        self.identity = {name: result[name] for name in ("key", "log_session")}
        return self.identity["log_session"]

    async def _probe_prerequisites(self) -> dict[str, Any]:
        probe = await self.call("probe", {})
        if set(probe) != {"linger", "manager"} or any(
            type(value) is not bool for value in probe.values()
        ):
            raise SSHError(
                "persistent_operation_failed", "invalid persistent prerequisite reply"
            )
        return probe

    async def _check_prerequisites(self) -> None:
        probe = await self._probe_prerequisites()
        if probe.get("linger") is False:
            self.journal.emit(PRIORITIES["info"], "remote linger consent is required")
            try:
                await confirm_linger()
            except SSHError:
                self.journal.emit(
                    PRIORITIES["warning"],
                    "remote linger was not enabled; consent was not granted",
                )
                raise
            await self._interactive(self.command("enable-linger", {}))
            self.journal.emit(
                PRIORITIES["info"], "remote linger was enabled with consent"
            )
            probe = await self._probe_prerequisites()
        if probe.get("linger") is not True or probe.get("manager") is not True:
            raise SSHError(
                "persistent_systemd_unavailable",
                ERRORS["persistent_systemd_unavailable"],
            )
        self._prepared = True

    async def prepare(self, application: tuple[str, ...]) -> bool:
        """Disable persistence only before creation and without recorded state."""
        try:
            await self._check_prerequisites()
        except SSHError as error:
            if error.code not in {
                "persistent_systemd_unavailable",
                "persistent_linger_failed",
                "persistent_linger_declined",
                "persistent_linger_consent_required",
            }:
                raise
            fallback = await self.call(
                "ordinary-fallback",
                {
                    "application": list(application),
                    "application_environment": self.application_environment,
                },
            )
            if fallback.get("allowed") is not True:
                self.journal.emit(
                    PRIORITIES["warning"],
                    "Persistence is unavailable, but recorded session state prevents an "
                    "ordinary fallback. Restore the remote systemd user manager and linger "
                    "to resume the existing session; no duplicate application was started.",
                )
                raise
            self.journal.emit(
                PRIORITIES["warning"],
                "Persistence disabled for this session: remote systemd or linger is "
                "unavailable or consent was not granted. On Debian/Ubuntu, install "
                "systemd libpam-systemd on the remote host, enable its user manager, "
                "and approve linger when prompted. Continuing as an ordinary session: "
                "disconnecting the client or SSH will stop the application.",
            )
            return False
        return True

    async def start(
        self,
        application: tuple[str, ...],
        server: tuple[str, ...],
        *,
        requested_server: tuple[str, ...] | None = None,
        server_features: dict[str, Any] | None = None,
    ) -> None:
        if not self._prepared:
            await self._check_prerequisites()
        self._record(
            await self.call(
                "ensure",
                {
                    "application": list(application),
                    "application_environment": self.application_environment,
                    "server": _server_template(server),
                    "requested_server": _server_template(requested_server or server),
                    "server_features": server_features,
                },
            )
        )
        state = "started" if self.record["created"] else "resuming"
        self.journal.emit(
            PRIORITIES["info"],
            f"{state} persistent session; disconnecting will leave it running",
        )

    def _record(self, result: dict[str, Any]) -> None:
        for name, pattern in (
            ("key", agent.IDENTITY),
            ("log_session", agent.IDENTITY),
            ("token", agent.TOKEN),
            ("argv_sha256", agent.IDENTITY),
        ):
            if (
                not isinstance(result.get(name), str)
                or pattern.fullmatch(result[name]) is None
            ):
                raise SSHError(
                    "persistent_operation_failed", "invalid persistent session identity"
                )
        display = result.get("display")
        if (
            display is not None
            and (
                not isinstance(display, str) or agent.DISPLAY.fullmatch(display) is None
            )
            or result.get("session_name") != f"elsewindow-{result['key'][:16]}"
            or not isinstance(result.get("created"), bool)
        ):
            raise SSHError(
                "persistent_operation_failed", "invalid persistent session metadata"
            )
        if (
            (
                result["log_session"] != self.journal.session
                or any(result[name] != value for name, value in self.identity.items())
            )
            or self.record
            and any(
                self.record.get(name) != result.get(name)
                for name in (
                    "key",
                    "log_session",
                    "token",
                    "argv_sha256",
                    "server_features",
                )
            )
        ):
            raise SSHError(
                "persistent_identity_mismatch", ERRORS["persistent_identity_mismatch"]
            )
        self.record = result

    async def refresh(self) -> bool:
        result = await self.call(
            "status", {name: self.record[name] for name in ("key", "token")}
        )
        if result.get("ended") is True:
            return False
        if result.get("ended") is not False:
            raise SSHError(
                "persistent_operation_failed", "invalid persistent session status"
            )
        self._record(result)
        return True

    async def wait(self) -> int:
        while await self.refresh():
            await asyncio.sleep(max(self._poll_interval, 1.0))
        return 0
