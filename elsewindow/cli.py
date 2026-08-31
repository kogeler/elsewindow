# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Command-line interface for one owned remote Xpra application."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import signal
import sys
from collections.abc import Sequence
from contextlib import suppress
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

from ssh_wrapper.errors import SSHError

from . import __version__
from .config import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_ENCODING_PROFILE,
    DEFAULT_GRACE_TIMEOUT,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_LEASE_TIMEOUT,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_READY_TIMEOUT,
    SUPPORTED_ENCODING_PROFILES,
    SUPPORTED_NETWORK_PROFILES,
    XpraConfig,
)
from .session import XpraSession


def build_parser() -> argparse.ArgumentParser:
    """Build the public command-line contract."""
    parser = argparse.ArgumentParser(
        prog="elsewindow",
        description="Run one new remote GUI application through Xpra over owned SSH.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="report bundled versions, profile digests, and local prerequisites",
    )
    authority = parser.add_mutually_exclusive_group()
    authority.add_argument("--ssh-alias", help="trusted OpenSSH host alias")
    authority.add_argument("--host", help="direct SSH host or address")
    parser.add_argument("--user", help="remote user required with --host")
    parser.add_argument("--port", type=int, default=22, help="direct SSH port")
    parser.add_argument(
        "--encoding-profile",
        choices=SUPPORTED_ENCODING_PROFILES,
        default=DEFAULT_ENCODING_PROFILE,
        help=(
            "reviewed pixel transport profile; h264 uses native VA-API, "
            "libyuv, OpenGL, and alpha-capable fallback"
        ),
    )
    parser.add_argument(
        "--network-profile",
        choices=SUPPORTED_NETWORK_PROFILES,
        default=DEFAULT_NETWORK_PROFILE,
        help="reviewed client quality/network profile (default: %(default)s)",
    )
    parser.add_argument(
        "--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT
    )
    parser.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT)
    parser.add_argument("--probe-timeout", type=float, default=DEFAULT_PROBE_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument(
        "--heartbeat-interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL
    )
    parser.add_argument("--lease-timeout", type=float, default=DEFAULT_LEASE_TIMEOUT)
    parser.add_argument("--cleanup-grace", type=float, default=DEFAULT_GRACE_TIMEOUT)
    parser.add_argument(
        "application",
        nargs=argparse.REMAINDER,
        metavar="APP [ARG ...]",
        help="application argv after --",
    )
    return parser


def _diagnose() -> int:
    """Report immutable bundled inputs and actionable host prerequisites."""
    from .live_config import LIVE_CLI_PATH, NETWORK_PROFILES_PATH

    failed = False
    print(f"elsewindow: {__version__}")
    try:
        wrapper_version = distribution_version("ssh-wrapper")
    except PackageNotFoundError:
        wrapper_version = "missing"
        failed = True
    print(f"ssh-wrapper: {wrapper_version}")
    for label, path in (
        ("live-cli.yml", LIVE_CLI_PATH),
        ("profiles.yml", NETWORK_PROFILES_PATH),
    ):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            print(f"elsewindow: invalid_resource: {label}: {error}", file=sys.stderr)
            failed = True
        else:
            print(f"{label}: sha256:{digest}")
    if not sys.platform.startswith("linux"):
        print(
            f"elsewindow: unsupported_platform: Linux is required, found {sys.platform}",
            file=sys.stderr,
        )
        failed = True
    for command in ("ssh", "false", "xpra"):
        resolved = shutil.which(command)
        if resolved is None:
            print(
                "elsewindow: missing_dependency: "
                f"required command not found on PATH: {command}",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"{command}: {resolved}")
    return int(failed)


async def _run_with_signals(config: XpraConfig) -> int:
    session = XpraSession(config)
    loop = asyncio.get_running_loop()
    interrupted: asyncio.Future[int] = loop.create_future()

    def stop(exit_code: int) -> None:
        if not interrupted.done():
            interrupted.set_result(exit_code)

    installed: list[signal.Signals] = []
    for selected in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(selected, stop, 128 + selected)
        except NotImplementedError:
            continue
        installed.append(selected)

    run_task = asyncio.create_task(session.run())
    try:
        done, _pending = await asyncio.wait(
            {run_task, interrupted}, return_when=asyncio.FIRST_COMPLETED
        )
        if interrupted in done:
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task
            return interrupted.result()
        return run_task.result()
    finally:
        if not interrupted.done():
            interrupted.cancel()
        for selected in installed:
            loop.remove_signal_handler(selected)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration, run one session, and return a stable exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.diagnose:
        if (
            args.ssh_alias is not None
            or args.host is not None
            or args.user is not None
            or args.port != 22
            or args.application
        ):
            parser.error("--diagnose cannot be combined with a session authority")
        return _diagnose()
    if args.ssh_alias is None and args.host is None:
        parser.error("provide either --ssh-alias or --host")
    try:
        config = XpraConfig.from_namespace(args)
    except SSHError as error:
        parser.error(error.message)

    try:
        return asyncio.run(_run_with_signals(config))
    except KeyboardInterrupt:
        return 130
    except SSHError as error:
        print(f"elsewindow: {error.code}: {error.message}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - public diagnostics stay sanitized.
        print(
            f"elsewindow: session_failed: {type(error).__name__}",
            file=sys.stderr,
        )
        return 1
