# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Validated immutable configuration for one remote Xpra session."""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ssh_wrapper.connection import ConnectionSpec, resolve_program
from ssh_wrapper.errors import SSHError

from . import live_config
from .desktop import validate_application_environment
from .journal import DEFAULT_LOG_LEVEL, LOG_LEVELS
from .xpra_runtime import XpraRuntimeError, prepared_launcher

DEFAULT_CONNECT_TIMEOUT = 120.0
DEFAULT_READY_TIMEOUT = 45.0
DEFAULT_PROBE_TIMEOUT = 8.0
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_HEARTBEAT_INTERVAL = 10.0
DEFAULT_LEASE_TIMEOUT = 45.0
DEFAULT_GRACE_TIMEOUT = 5.0
MAX_TIMEOUT = 900.0
MAX_APPLICATION_ARGUMENTS = 256
MAX_APPLICATION_BYTES = 16 * 1024
DEFAULT_CLIPBOARD_POLICY = "both"
SUPPORTED_CLIPBOARD_POLICIES = ("off", "to-server", "both")
DEFAULT_ENCODING_PROFILE = live_config.DEFAULT_ENCODING_PROFILE
DEFAULT_NETWORK_PROFILE = live_config.load_network_profiles()[0]
SUPPORTED_ENCODING_PROFILES = live_config.encoding_profile_names()
SUPPORTED_NETWORK_PROFILES = live_config.network_profile_names()


def _bounded_float(name: str, value: float, minimum: float = 0.1) -> float:
    if not math.isfinite(value) or not minimum <= value <= MAX_TIMEOUT:
        raise SSHError(
            "invalid_configuration",
            f"{name} must be between {minimum:g} and {MAX_TIMEOUT:g} seconds",
        )
    return value


def _local_xpra() -> Path:
    try:
        return prepared_launcher(resolve_program("xpra"))
    except XpraRuntimeError as error:
        raise SSHError("xpra_environment_unprepared", str(error)) from error


def _application_environment(entries: list[str] | None) -> tuple[tuple[str, str], ...]:
    values: dict[str, str] = {}
    try:
        for entry in entries or ():
            name, separator, value = entry.partition("=")
            validate_application_environment({name: value})
            if not separator:
                if name not in os.environ:
                    raise SSHError(
                        "invalid_application_environment",
                        f"local environment variable {name} is not set",
                    )
                value = os.environ[name]
            values[name] = value
        return tuple(validate_application_environment(values).items())
    except ValueError as error:
        raise SSHError("invalid_application_environment", str(error)) from error


@dataclass(frozen=True, slots=True)
class XpraConfig:
    """All startup policy for one independently owned GUI session."""

    connection: ConnectionSpec
    application: tuple[str, ...]
    encoding_profile: str
    network_profile: str
    clipboard: str
    connect_timeout: float
    ready_timeout: float
    probe_timeout: float
    poll_interval: float
    heartbeat_interval: float
    lease_timeout: float
    grace_timeout: float
    ssh_path: Path
    false_path: Path
    xpra_path: Path
    log_level: str = DEFAULT_LOG_LEVEL
    persistent: bool = False
    application_environment: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> XpraConfig:
        """Validate parsed arguments and resolve required local executables."""
        if args.ssh_alias is not None:
            if args.host is not None or args.user is not None or args.port != 22:
                raise SSHError(
                    "invalid_connection",
                    "ssh alias cannot be combined with host, user, or a direct port",
                )
            connection = ConnectionSpec.from_alias(args.ssh_alias)
        else:
            if args.host is None or args.user is None:
                raise SSHError(
                    "invalid_connection",
                    "provide either ssh alias or both host and user",
                )
            connection = ConnectionSpec.from_direct(args.host, args.user, args.port)

        application = tuple(args.application)
        if application and application[0] == "--":
            application = application[1:]
        if not application or not application[0]:
            raise SSHError(
                "invalid_application", "provide an application argv after --"
            )
        if len(application) > MAX_APPLICATION_ARGUMENTS:
            raise SSHError(
                "invalid_application",
                f"application argv is limited to {MAX_APPLICATION_ARGUMENTS} items",
            )
        if any("\x00" in item for item in application):
            raise SSHError("invalid_application", "application argv contains a NUL")
        if (
            sum(len(item.encode("utf-8")) for item in application)
            > MAX_APPLICATION_BYTES
        ):
            raise SSHError(
                "invalid_application",
                f"application argv is limited to {MAX_APPLICATION_BYTES} UTF-8 bytes",
            )
        application_environment = _application_environment(args.env)
        if args.encoding_profile not in SUPPORTED_ENCODING_PROFILES:
            raise SSHError(
                "invalid_configuration",
                "encoding profile must be one of: "
                f"{', '.join(SUPPORTED_ENCODING_PROFILES)}",
            )
        if args.network_profile not in SUPPORTED_NETWORK_PROFILES:
            raise SSHError(
                "invalid_configuration",
                "network profile must be one of: "
                f"{', '.join(SUPPORTED_NETWORK_PROFILES)}",
            )
        if args.clipboard not in SUPPORTED_CLIPBOARD_POLICIES:
            raise SSHError(
                "invalid_configuration",
                "clipboard policy must be one of: "
                f"{', '.join(SUPPORTED_CLIPBOARD_POLICIES)}",
            )
        if args.log_level not in LOG_LEVELS:
            raise SSHError("invalid_configuration", "the log level is invalid")
        heartbeat_interval = _bounded_float(
            "heartbeat interval", args.heartbeat_interval
        )
        lease_timeout = _bounded_float("lease timeout", args.lease_timeout)
        if lease_timeout <= heartbeat_interval * 2:
            raise SSHError(
                "invalid_configuration",
                "lease timeout must be greater than twice the heartbeat interval",
            )
        return cls(
            connection=connection,
            application=application,
            encoding_profile=args.encoding_profile,
            network_profile=args.network_profile,
            clipboard=args.clipboard,
            connect_timeout=_bounded_float("connect timeout", args.connect_timeout),
            ready_timeout=_bounded_float("ready timeout", args.ready_timeout),
            probe_timeout=_bounded_float("probe timeout", args.probe_timeout),
            poll_interval=_bounded_float("poll interval", args.poll_interval),
            heartbeat_interval=heartbeat_interval,
            lease_timeout=lease_timeout,
            grace_timeout=_bounded_float("cleanup grace", args.cleanup_grace),
            ssh_path=resolve_program("ssh"),
            false_path=resolve_program("false"),
            xpra_path=_local_xpra(),
            log_level=args.log_level,
            persistent=args.persistent,
            application_environment=application_environment,
        )

    @property
    def authority_uri(self) -> str:
        """Return the SSH URI prefix consumed by the local Xpra client."""
        connection = self.connection
        if connection.ssh_alias is not None:
            authority = connection.ssh_alias
        else:
            assert connection.host is not None
            assert connection.user is not None
            assert connection.port is not None
            host = f"[{connection.host}]" if ":" in connection.host else connection.host
            authority = f"{connection.user}@{host}:{connection.port}"
        return f"ssh://{authority}"
