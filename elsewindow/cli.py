# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Command-line interface for one owned remote Xpra application."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from contextlib import suppress
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from importlib.resources import files

from ssh_wrapper.errors import SSHError

from . import __version__
from .config import (
    DEFAULT_CLIPBOARD_POLICY,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_ENCODING_PROFILE,
    DEFAULT_GRACE_TIMEOUT,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_LEASE_TIMEOUT,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_READY_TIMEOUT,
    SUPPORTED_CLIPBOARD_POLICIES,
    SUPPORTED_ENCODING_PROFILES,
    SUPPORTED_NETWORK_PROFILES,
    XpraConfig,
)
from .desktop import NOTIFICATION_PACKAGES, probe_argv, probe_result
from .journal import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    PRIORITIES,
    Journal,
    JournalError,
    xpra_environment,
)
from .session import ReportedSSHError, XpraSession
from .xpra_runtime import XpraRuntimeError, prepare, prepared_launcher


def build_parser() -> argparse.ArgumentParser:
    """Build the public command-line contract."""
    parser = argparse.ArgumentParser(
        prog="elsewindow",
        description="Run or resume a remote GUI application through Xpra over owned SSH.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument(
        "--diagnose",
        action="store_true",
        help="report bundled versions, profile digests, and local prerequisites",
    )
    setup.add_argument(
        "--prepare-xpra",
        action="store_true",
        help="prepare the isolated local Xpra Python environment without opening SSH",
    )
    authority = parser.add_mutually_exclusive_group()
    authority.add_argument("--ssh-alias", help="trusted OpenSSH host alias")
    authority.add_argument("--host", help="direct SSH host or address")
    parser.add_argument("--user", help="remote user required with --host")
    parser.add_argument("--port", type=int, default=22, help="direct SSH port")
    parser.add_argument(
        "--env",
        action="append",
        metavar="NAME[=VALUE]",
        help="set an application variable, or copy a named local variable; repeat for multiple variables",
    )
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
        "--clipboard",
        choices=SUPPORTED_CLIPBOARD_POLICIES,
        default=DEFAULT_CLIPBOARD_POLICY,
        help="clipboard synchronization policy (default: %(default)s)",
    )
    parser.add_argument(
        "--persistent",
        action="store_true",
        help="request persistence and resume by argv; warn and use ordinary lifetime if unavailable",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=DEFAULT_LOG_LEVEL,
        help="Elsewindow and Xpra journal level on both hosts (default: %(default)s)",
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
        ("_persistent_agent.py", files("elsewindow").joinpath("_persistent_agent.py")),
        ("journal.py", files("elsewindow").joinpath("journal.py")),
        ("session_bus.py", files("elsewindow").joinpath("session_bus.py")),
        ("desktop.py", files("elsewindow").joinpath("desktop.py")),
        ("log_transport.py", files("elsewindow").joinpath("log_transport.py")),
        (
            "requirements-xpra.txt",
            files("elsewindow").joinpath("requirements-xpra.txt"),
        ),
        (
            "requirements-xpra-build.txt",
            files("elsewindow").joinpath("requirements-xpra-build.txt"),
        ),
    ):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            print(
                f"elsewindow: invalid_resource: {label}: not readable", file=sys.stderr
            )
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
            if command == "xpra":
                from pathlib import Path

                try:
                    prepared_launcher(Path(resolved))
                except XpraRuntimeError as error:
                    print(
                        f"elsewindow: xpra_environment_unprepared: {error}",
                        file=sys.stderr,
                    )
                    failed = True
                else:
                    print("xpra-environment: verified")
    if not failed:
        _diagnose_optional()
    return int(failed)


def _diagnose_optional() -> None:
    try:
        result = subprocess.run(
            probe_argv("client"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=xpra_environment(DEFAULT_LOG_LEVEL),
            timeout=15,
            check=False,
        )
        capabilities = probe_result(result.returncode, result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        capabilities = {"notifications": False, "render": False}
    for feature, available in capabilities.items():
        print(
            f"optional-local-{feature}: {'available' if available else 'unavailable'}"
        )
    if not capabilities["notifications"]:
        print(
            "elsewindow: warning: local notification delivery is unavailable. "
            f"On Debian/Ubuntu, install {NOTIFICATION_PACKAGES} and run a desktop "
            "notification service (for example dunst). Sessions continue without delivery.",
            file=sys.stderr,
        )
    if not capabilities["render"]:
        print(
            "elsewindow: warning: no accessible local DRM render device; H.264 "
            "connections fall back to RGB. Check GPU permissions and the system "
            "VA-API driver. Ordinary RGB sessions remain available.",
            file=sys.stderr,
        )
    journal = Journal(DEFAULT_LOG_LEVEL, "client")
    try:
        journal.open()
    except (OSError, JournalError):
        print(
            "elsewindow: warning: local journald is unavailable. Install systemd "
            "and enable systemd-journald; terminal logging remains available.",
            file=sys.stderr,
        )
    finally:
        journal.close()
    print(
        "optional-remote-features: checked after SSH authentication and display startup"
    )


async def _run_with_signals(config: XpraConfig) -> int:
    session = XpraSession(config)
    loop = asyncio.get_running_loop()
    interrupted: asyncio.Future[int] = loop.create_future()

    def stop(exit_code: int) -> None:
        if not interrupted.done():
            interrupted.set_result(exit_code)

    installed: list[signal.Signals] = []
    for selected in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
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
    if args.diagnose or args.prepare_xpra:
        if (
            args.ssh_alias is not None
            or args.host is not None
            or args.user is not None
            or args.port != 22
            or args.application
            or args.env
        ):
            parser.error(
                "setup and diagnosis cannot be combined with session or application inputs"
            )
        if args.diagnose:
            return _diagnose()
        from pathlib import Path

        try:
            xpra = shutil.which("xpra")
            if xpra is None:
                raise XpraRuntimeError(
                    "install the supported system Xpra packages first"
                )
            prepare(Path(xpra))
        except (OSError, ValueError, XpraRuntimeError) as error:
            detail = (
                str(error)
                if isinstance(error, XpraRuntimeError)
                else "cannot prepare the local Xpra environment"
            )
            print(f"elsewindow: xpra_environment_unprepared: {detail}", file=sys.stderr)
            return 1
        print("Local Xpra Python environment is ready.")
        return 0
    if args.ssh_alias is None and args.host is None:
        parser.error("provide either --ssh-alias or --host")
    try:
        config = XpraConfig.from_namespace(args)
    except SSHError as error:
        journal = Journal(args.log_level, "client")
        journal.emit(PRIORITIES["error"], f"{error.code}: {error.message}")
        journal.close()
        parser.error(error.message)

    try:
        return asyncio.run(_run_with_signals(config))
    except KeyboardInterrupt:
        return 130
    except ReportedSSHError:
        return 1
    except SSHError as error:
        journal = Journal(config.log_level, "client")
        journal.emit(PRIORITIES["error"], f"{error.code}: {error.message}")
        journal.close()
        return 1
    except Exception as error:  # noqa: BLE001 - public diagnostics stay sanitized.
        journal = Journal(config.log_level, "client")
        journal.emit(PRIORITIES["error"], f"session_failed: {type(error).__name__}")
        journal.close()
        return 1
