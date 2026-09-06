# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Owned Xpra server, client, and SSH lifecycle orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
from pathlib import Path

from ssh_wrapper.connection import OpenSSHMaster, SSHMasterSettings
from ssh_wrapper.errors import SSHError
from ssh_wrapper.remote_process import BoundedTail, OwnedRemoteProcess

from .config import SUPPORTED_CLIPBOARD_POLICIES, XpraConfig
from .journal import (
    PRIORITIES,
    Journal,
    JournalError,
    XpraLogStream,
    remote_argv,
    xpra_environment,
)
from .live_config import (
    command_cli_options,
    load_live_cli,
    network_profile,
    production_encoding,
    production_transport_options,
    static_cli_options,
)
from .log_transport import LogTransportError, RemoteLogChannel
from .persistent import PersistentSession, open_terminal

LOCAL_PROCESS_STOP_TIMEOUT = 5.0
CAPABILITY_TIMEOUT = 15.0
OPENGL_CAPABILITY_TIMEOUT = 45.0
CLIENT_DIAGNOSTIC_LINES = 8
CLIENT_DIAGNOSTIC_CHARACTERS = 2 * 1024
CLIENT_DIAGNOSTIC_MARKERS = (
    "drm",
    "error",
    "failed",
    "failure",
    "connection",
    "disconnect",
    "handshake",
    "invalid",
    "metadata",
    "protocol",
    "renderer",
    "segmentation",
    "socket",
    "timeout",
    "unsupported",
    "warning",
    "wayland",
)
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
DISPLAY_PATTERN = re.compile(r"wayland-[0-9]+")
REMOTE_SESSIONS_DIR = "$XDG_RUNTIME_DIR/xpra"
REMOTE_SOCKET_DIR = "$XDG_RUNTIME_DIR/xpra"
SERVER_RUNTIME_PATHS = {
    "--socket-dir": REMOTE_SOCKET_DIR,
    "--socket-dirs": REMOTE_SOCKET_DIR,
    "--sessions-dir": REMOTE_SESSIONS_DIR,
}
# The canonical live base is intentionally minimal. Restore ordinary GUI
# semantics without changing its transport policy or unrelated auxiliary I/O.
SERVER_GUI_OPTIONS = (
    "--cursors=yes",
    "--dpi=0",
    "--notifications=yes",
)
CLIENT_GUI_OPTIONS = (
    *SERVER_GUI_OPTIONS,
    "--mousewheel=on",
    "--keyboard-sync=yes",
    "--modal-windows=yes",
    "--desktop-scaling=on",
    # The fork's notification presenter uses a helper from this client module.
    # Server-side tray forwarding remains explicitly disabled below.
    "--system-tray=yes",
)
NOTIFICATION_BUS_OPTIONS = (
    "--dbus=keep",
    "--dbus-launch=no",
    "--dbus-control=no",
)
SERVER_SECURITY_OPTIONS = (
    "--start-new-commands=no",
    *NOTIFICATION_BUS_OPTIONS,
    "--system-tray=no",
    "--mdns=no",
    "--ssh-upgrade=no",
    "--audio=no",
    "--webcam=no",
    "--printing=no",
    "--file-transfer=no",
    "--open-files=no",
    "--open-url=no",
)
CLIENT_SECURITY_OPTIONS = (
    *NOTIFICATION_BUS_OPTIONS,
    "--title=@title@",
    "--remote-logging=no",
    "--tray=no",
    "--splash=no",
    "--audio=no",
    "--webcam=no",
    "--printing=no",
    "--file-transfer=no",
    "--open-files=no",
    "--open-url=no",
)


def clipboard_options(policy: str) -> tuple[str, ...]:
    """Return the explicit Xpra options for one public clipboard policy."""
    if policy not in SUPPORTED_CLIPBOARD_POLICIES:
        raise RuntimeError("the clipboard policy is invalid")
    # The fork's client clipboard gate also isolates unrelated X11 features.
    # Apply only the symmetric server policy, not those test-only workarounds.
    return tuple(load_live_cli()["server"]["clipboard"][policy])


def _translate_server_runtime_options(
    options: tuple[str, ...],
) -> tuple[tuple[str, ...], frozenset[str]]:
    """Translate canonical container-private paths without copying their values."""
    translated: list[str] = []
    replaced: set[str] = set()
    for option in options:
        name, separator, _value = option.partition("=")
        replacement = SERVER_RUNTIME_PATHS.get(name)
        if replacement is None:
            translated.append(option)
            continue
        if not separator or name in replaced:
            raise RuntimeError("the mirrored server runtime-path contract changed")
        translated.append(f"{name}={replacement}")
        replaced.add(name)
    return tuple(translated), frozenset(replaced)


def _production_server_base_options() -> tuple[str, ...]:
    """Translate only the fork's container-private paths to the owned runtime."""
    translated, replaced = _translate_server_runtime_options(
        static_cli_options("server", "base")
    )
    if replaced != set(SERVER_RUNTIME_PATHS):
        raise RuntimeError("the mirrored server runtime-path contract changed")
    return translated


def _production_server_info_options(
    display: str, options: tuple[str, ...]
) -> tuple[str, ...]:
    """Translate the canonical server-info target to the owned live session."""
    if DISPLAY_PATTERN.fullmatch(display) is None:
        raise RuntimeError("the remote Xpra display is invalid")
    translated_options, replaced = _translate_server_runtime_options(options)
    positional = tuple(
        index
        for index, option in enumerate(translated_options)
        if not option.startswith("-")
    )
    if len(positional) != 1 or not replaced:
        raise RuntimeError("the mirrored server info command contract changed")
    translated = list(translated_options)
    translated[positional[0]] = display
    return tuple(translated)


def build_xpra_command_argv(
    role: str,
    command: str,
    xpra_path: str | Path,
    *,
    display: str | None = None,
    uri: str | None = None,
    ssh_wrapper: str | Path | None = None,
) -> tuple[str, ...]:
    """Build one canonical auxiliary Xpra command for production or live use."""
    options = command_cli_options(role, command)
    if command == "version" and display is None and uri is None and ssh_wrapper is None:
        return (str(xpra_path), *options)
    if (
        role == "server"
        and command == "info"
        and display is not None
        and uri is None
        and ssh_wrapper is None
    ):
        return (
            str(xpra_path),
            command,
            *_production_server_info_options(display, options),
        )
    if (
        role == "client"
        and command == "detach"
        and display is None
        and uri is not None
        and ssh_wrapper is not None
    ):
        return (
            str(xpra_path),
            command,
            uri,
            f"--ssh={ssh_wrapper}",
            *options,
        )
    raise RuntimeError("the mirrored auxiliary Xpra command context is invalid")


def _gpu_mode(profile: str) -> str:
    return "yes" if production_encoding(profile) == "h264" else "auto"


def _option_names(*blocks: tuple[str, ...]) -> tuple[str, ...]:
    """Return unique long-option names in source order, without copying tables."""
    names: list[str] = []
    for option in (value for block in blocks for value in block):
        if not option.startswith("--"):
            continue
        name = option.partition("=")[0].replace("_", "-")
        if name not in names:
            names.append(name)
    return tuple(names)


def build_server_argv(
    application: tuple[str, ...],
    session_name: str,
    encoding_profile: str,
    clipboard: str,
    *,
    persistent: bool = False,
) -> tuple[str, ...]:
    """Build one production Wayland server command from the mirrored profile."""
    return (
        "env",
        f"XPRA_WAYLAND_GPU={_gpu_mode(encoding_profile)}",
        "xpra",
        "seamless",
        *_production_server_base_options(),
        f"--session-name={session_name}",
        f"{'--start-child' if persistent else '--start-child-after-connect'}={shlex.join(application)}",
        *static_cli_options("server", "lifecycle"),
        *production_transport_options("server", encoding_profile),
        *clipboard_options(clipboard),
        *SERVER_GUI_OPTIONS,
        *SERVER_SECURITY_OPTIONS,
    )


SESSION_METADATA_PROBE = r"""\
import hashlib
import json
import os
import stat
import sys

NOT_FOUND = 1
MISMATCH = 2
INVALID_RUNTIME = 3
MAX_FILE_SIZE = 65536
PROC_ROOT = "/proc"

def fail(status, reason):
    if status != NOT_FOUND:
        print(f"metadata-{reason}", file=sys.stderr)
    raise SystemExit(status)

def owned_stat(path, kind):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        fail(MISMATCH, "stat-failed")
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        fail(MISMATCH, "unsafe-owner-or-mode")
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        fail(MISMATCH, "not-directory")
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        fail(MISMATCH, "not-file")
    if kind == "socket" and not stat.S_ISSOCK(info.st_mode):
        fail(MISMATCH, "not-socket")
    return info

def read_file(path):
    if owned_stat(path, "file") is None:
        return None
    try:
        with open(path, "rb") as source:
            value = source.read(MAX_FILE_SIZE + 1)
    except OSError:
        fail(MISMATCH, "read-failed")
    if len(value) > MAX_FILE_SIZE or b"\0" in value:
        fail(MISMATCH, "invalid-file-content")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(MISMATCH, "invalid-file-encoding")

def load_session(directory):
    if owned_stat(directory, "directory") is None:
        return None
    config_text = read_file(os.path.join(directory, "config"))
    command_text = read_file(os.path.join(directory, "cmdline"))
    pid_text = read_file(os.path.join(directory, "server.pid"))
    socket_info = owned_stat(os.path.join(directory, "socket"), "socket")
    if None in (config_text, command_text, pid_text, socket_info):
        return None
    try:
        pid = int(pid_text.strip())
    except ValueError:
        fail(MISMATCH, "invalid-pid")
    if pid <= 0:
        fail(MISMATCH, "invalid-pid")
    process_dir = os.path.join(PROC_ROOT, str(pid))
    try:
        process_info = os.stat(process_dir)
        with open(os.path.join(process_dir, "cmdline"), "rb") as source:
            process_command = source.read(MAX_FILE_SIZE + 1)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError:
        fail(MISMATCH, "process-read-failed")
    if process_info.st_uid != os.getuid() or len(process_command) > MAX_FILE_SIZE:
        fail(MISMATCH, "process-owner-or-size")
    recorded_argv = tuple(command_text.splitlines())
    try:
        process_argv = tuple(
            value.decode("utf-8", errors="strict")
            for value in process_command.rstrip(b"\0").split(b"\0")
        )
    except UnicodeDecodeError:
        fail(MISMATCH, "invalid-process-encoding")
    interpreter_prefix = (
        len(process_argv) == len(recorded_argv) + 1
        and os.path.basename(process_argv[0]).startswith("python")
        and process_argv[1:] == recorded_argv
    )
    same_process = (
        recorded_argv == process_argv
        or process_argv == (" ".join(recorded_argv),)
        or interpreter_prefix
    )
    if not same_process or len(recorded_argv) < 2:
        fail(MISMATCH, "process-arguments")
    if os.path.basename(recorded_argv[0]) != "xpra":
        fail(MISMATCH, "unexpected-program")
    if recorded_argv[1] != "seamless":
        fail(MISMATCH, "unexpected-mode-argument")
    fields = {}
    identity_fields = {"mode", "session-name", "backend"}
    for line in config_text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            key = key.strip().replace("_", "-")
            if key in fields and key in identity_fields:
                fail(MISMATCH, "duplicate-config-field")
            fields[key] = value.strip()
    if fields.get("mode") != "seamless":
        fail(MISMATCH, "unexpected-config-mode")
    return fields, recorded_argv

def sessions_root():
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime:
        runtime = f"/run/user/{os.getuid()}"
    if not os.path.isabs(runtime):
        fail(INVALID_RUNTIME, "relative-runtime")
    try:
        runtime_info = os.lstat(runtime)
    except FileNotFoundError:
        fail(INVALID_RUNTIME, "missing-runtime")
    except OSError:
        fail(INVALID_RUNTIME, "runtime-stat-failed")
    if not stat.S_ISDIR(runtime_info.st_mode) or runtime_info.st_uid != os.getuid():
        fail(INVALID_RUNTIME, "unsafe-runtime")
    return os.path.join(runtime, "xpra")

operation = sys.argv[1]
root = sessions_root()
if operation == "ready" and len(sys.argv) == 5:
    actual_display, session_name, expected_argv_sha256 = sys.argv[2:]
    component = actual_display
    if (
        not component.startswith("wayland-")
        or not component[8:].isdigit()
        or "/" in component
    ):
        fail(MISMATCH, "invalid-display")
    session = load_session(os.path.join(root, component))
    if session is None:
        fail(NOT_FOUND, "session-not-ready")
    fields, argv = session
    positionals = tuple(value for value in argv[2:] if not value.startswith("--"))
    if positionals:
        fail(MISMATCH, "unexpected-display-argument")
    normalized_argv = (
        "xpra",
        *argv[1:],
    )
    argv_sha256 = hashlib.sha256(
        json.dumps(
            list(normalized_argv),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if argv_sha256 != expected_argv_sha256:
        fail(MISMATCH, "arguments")
    if fields.get("session-name") != session_name:
        fail(MISMATCH, "session-name")
    if fields.get("backend") != "wayland":
        fail(MISMATCH, "backend")
    raise SystemExit(0)
fail(MISMATCH, "invalid-operation")
"""


async def _terminate_local(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=LOCAL_PROCESS_STOP_TIMEOUT)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


class ReportedSSHError(SSHError):
    """A runtime failure already sent to the journal and filtered terminal sink."""


class XpraSession:
    """Own one new remote Xpra session and one local attaching client."""

    def __init__(self, config: XpraConfig) -> None:
        self.config = config
        self.session_id = secrets.token_hex(16)
        self.journal = Journal(
            config.log_level, "client", self.session_id, deferred=config.persistent
        )
        executable_name = config.application[0].rsplit("/", maxsplit=1)[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", executable_name).strip(".-")
        self.application_name = (safe_name[:64] or "application").lower()
        self.session_name = self.application_name
        self.master = OpenSSHMaster(
            SSHMasterSettings(
                ssh_path=config.ssh_path,
                false_path=config.false_path,
                connect_timeout=config.connect_timeout,
                runtime_prefix="elsewindow",
            ),
            config.connection,
        )
        self.remote: OwnedRemoteProcess | None = None
        self.persistent: PersistentSession | None = None
        self.client: asyncio.subprocess.Process | None = None
        self.client_stdout = BoundedTail()
        self.client_stderr = BoundedTail()
        self._client_drains: tuple[asyncio.Task[None], ...] = ()
        self._wrapper: Path | None = None
        self._remote_display: str | None = None
        self._remote_logs: list[RemoteLogChannel] = []

    async def _subscribe_logs(self, session: str) -> None:
        channel = RemoteLogChannel(self.master, self.journal, session)
        self._remote_logs.append(channel)
        try:
            await channel.start()
        except (OSError, LogTransportError) as error:
            raise SSHError(
                "remote_log_unavailable", "cannot subscribe to the remote session logs"
            ) from error

    @staticmethod
    def _require_options(output: bytes, options: tuple[str, ...], side: str) -> None:
        normalized = output.decode("utf-8", errors="replace").replace("_", "-")
        missing = [option for option in options if option not in normalized]
        if missing:
            raise SSHError(
                "xpra_incompatible",
                f"{side} Xpra does not expose the required lifecycle options",
            )

    def _local_required_options(self) -> tuple[str, ...]:
        return _option_names(
            ("--ssh=owned",),
            static_cli_options("client", "base"),
            network_profile(self.config.network_profile).client_options(),
            production_transport_options("client", self.config.encoding_profile),
            clipboard_options(self.config.clipboard),
            CLIENT_GUI_OPTIONS,
            CLIENT_SECURITY_OPTIONS,
        )

    def _remote_required_options(self) -> tuple[str, ...]:
        return _option_names(
            _production_server_base_options(),
            (
                "--session-name=owned",
                f"{'--start-child' if self.config.persistent else '--start-child-after-connect'}=owned",
            ),
            static_cli_options("server", "lifecycle"),
            production_transport_options("server", self.config.encoding_profile),
            clipboard_options(self.config.clipboard),
            SERVER_GUI_OPTIONS,
            SERVER_SECURITY_OPTIONS,
        )

    async def _run_local(
        self, argv: list[str], timeout: float
    ) -> tuple[int, bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=xpra_environment(self.config.log_level),
            )
        except OSError as error:
            raise SSHError(
                "xpra_start_failed", "cannot start the local Xpra client"
            ) from error
        assert process.stdout is not None and process.stderr is not None
        output = (bytearray(), bytearray())

        async def capture(stream: asyncio.StreamReader, index: int) -> None:
            log = XpraLogStream(self.journal, stderr=bool(index), pid=process.pid)
            try:
                while chunk := await stream.read(4096):
                    output[index].extend(
                        chunk[: max(0, 128 * 1024 - len(output[index]))]
                    )
                    log.feed(chunk)
            finally:
                log.finish()

        drains = (
            asyncio.create_task(capture(process.stdout, 0)),
            asyncio.create_task(capture(process.stderr, 1)),
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            await _terminate_local(process)
            return 124, bytes(output[0]), bytes(output[1])
        except asyncio.CancelledError:
            await _terminate_local(process)
            raise
        finally:
            await asyncio.gather(*drains)
        return process.returncode or 0, bytes(output[0]), bytes(output[1])

    async def _run_mux(
        self, remote_program: str, timeout: float = CAPABILITY_TIMEOUT
    ) -> tuple[int, bytes, bytes]:
        await self.master.ensure_ready()
        process = await asyncio.create_subprocess_exec(
            *self.master.command_argv(remote_program),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError:
            await _terminate_local(process)
            raise SSHError(
                "xpra_probe_timeout", "remote Xpra probe timed out"
            ) from None
        except asyncio.CancelledError:
            await _terminate_local(process)
            raise
        return process.returncode or 0, stdout, stderr

    async def _run_mux_interactive(self, remote_program: str) -> None:
        """Give remote sudo a terminal on the same no-fallback SSH master."""
        await self.master.ensure_ready()
        descriptor = open_terminal()
        process = None
        try:
            os.set_blocking(descriptor, True)
            process = await asyncio.create_subprocess_exec(
                *self.master.mux_transport_argv(),
                "-tt",
                "--",
                self.config.connection.destination,
                remote_program,
                stdin=descriptor,
                stdout=descriptor,
                stderr=descriptor,
                start_new_session=True,
            )
            if await process.wait() != 0:
                raise SSHError(
                    "persistent_linger_failed",
                    "could not enable remote linger; ask the remote administrator",
                )
        finally:
            await _terminate_local(process)
            os.close(descriptor)

    async def _check_local_capabilities(self) -> None:
        help_status, stdout, stderr = await self._run_local(
            [str(self.config.xpra_path), "attach", "--help"], CAPABILITY_TIMEOUT
        )
        if help_status != 0:
            raise SSHError("xpra_incompatible", "local Xpra capability probe failed")
        self._require_options(stdout + stderr, self._local_required_options(), "local")
        if production_encoding(self.config.encoding_profile) == "h264":
            opengl_status, stdout, _stderr = await self._run_local(
                [str(self.config.xpra_path), "opengl"],
                OPENGL_CAPABILITY_TIMEOUT,
            )
            properties = {}
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    properties[key.strip()] = value.strip().lower()
            if (
                opengl_status != 0
                or properties.get("success") != "true"
                or properties.get("safe") != "true"
            ):
                raise SSHError(
                    "xpra_incompatible",
                    "local Xpra OpenGL renderer is unavailable or unsafe",
                )

    async def _check_remote_capabilities(self) -> None:
        status, stdout, stderr = await self._run_mux(
            shlex.join(
                remote_argv(
                    ("xpra", "seamless", "--help"),
                    self.config.log_level,
                    self.session_id,
                )
            )
        )
        if status != 0:
            detail = self._diagnostic(stderr.decode("utf-8", errors="replace"))
            raise SSHError(
                "xpra_incompatible",
                "remote Xpra capability probe failed"
                + (f": {detail}" if detail else ""),
            )
        self._require_options(
            stdout + stderr, self._remote_required_options(), "remote"
        )

    def server_argv(self) -> tuple[str, ...]:
        """Return the exact foreground remote Xpra server argv."""
        return build_server_argv(
            self.config.application,
            self.session_name,
            self.config.encoding_profile,
            self.config.clipboard,
            persistent=self.config.persistent,
        )

    def _capture_remote_display(self) -> None:
        """Capture the selected display published by Xpra."""
        if self._remote_display is not None:
            return
        remote = self.remote
        if remote is None:
            return
        raw = getattr(remote, "stdout_head", b"") or remote.stdout_tail.data
        for line in raw.decode("utf-8", errors="replace").splitlines():
            candidate = line.strip()
            if DISPLAY_PATTERN.fullmatch(candidate):
                self._remote_display = candidate
                return

    def _uri(self) -> str:
        """Return the owned session URI after its actual display is known."""
        display = self._remote_display
        if display is None:
            raise RuntimeError("remote Xpra display is not initialized")
        return f"{self.config.authority_uri}/{display}"

    def readiness_probe(self) -> str:
        """Return the non-connecting remote session-readiness probe."""
        display = self._remote_display
        if display is None:
            raise RuntimeError("remote Xpra display is not initialized")
        server = self.server_argv()
        normalized_argv = ("xpra", *server[3:])
        argv_sha256 = hashlib.sha256(
            json.dumps(
                list(normalized_argv),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.persistent is not None:
            argv_sha256 = self.persistent.record["argv_sha256"]
        return shlex.join(
            (
                "python3",
                "-c",
                SESSION_METADATA_PROBE,
                "ready",
                display,
                self.session_name,
                argv_sha256,
            )
        )

    def attach_argv(self) -> list[str]:
        """Return the exact mirrored production attach profile."""
        if self._wrapper is None:
            raise RuntimeError("SSH wrapper is not initialized")
        return [
            str(self.config.xpra_path),
            "attach",
            self._uri(),
            f"--ssh={self._wrapper}",
            *static_cli_options("client", "base"),
            *network_profile(self.config.network_profile).client_options(),
            *production_transport_options("client", self.config.encoding_profile),
            *clipboard_options(self.config.clipboard),
            *CLIENT_GUI_OPTIONS,
            *CLIENT_SECURITY_OPTIONS,
        ]

    async def _wait_ready(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.config.ready_timeout
        while asyncio.get_running_loop().time() < deadline:
            remote = self.remote
            if self.persistent is not None:
                running = await self.persistent.refresh()
                self._remote_display = self.persistent.record["display"]
            else:
                running = remote is not None and remote.returncode is None
            if not running:
                diagnostic = self._remote_diagnostic()
                message = "remote Xpra exited before becoming ready"
                if diagnostic:
                    message = f"{message}: {diagnostic}"
                raise SSHError(
                    "xpra_start_failed",
                    message,
                )
            self._capture_remote_display()
            if self._remote_display is None:
                await asyncio.sleep(self.config.poll_interval)
                continue
            status, _stdout, stderr = await self._run_mux(
                self.readiness_probe(), self.config.probe_timeout
            )
            if status == 0:
                return
            if status != 1:
                detail = self._diagnostic(stderr.decode("utf-8", errors="replace"))
                message = "remote Xpra session metadata does not match the owned server"
                if detail:
                    message = f"{message}: {detail}"
                raise SSHError(
                    "xpra_identity_mismatch",
                    message,
                )
            await asyncio.sleep(self.config.poll_interval)
        raise SSHError(
            "xpra_ready_timeout", "remote Xpra did not become ready before the deadline"
        )

    async def _drain_client(
        self,
        stream: asyncio.StreamReader,
        destination: BoundedTail,
    ) -> None:
        log = XpraLogStream(
            self.journal,
            stderr=destination is self.client_stderr,
            pid=self.client.pid if self.client is not None else 0,
        )
        try:
            while chunk := await stream.read(4096):
                destination.append(chunk)
                log.feed(chunk)
        finally:
            log.finish()

    async def _start_client(self) -> None:
        try:
            self.client = await asyncio.create_subprocess_exec(
                *self.attach_argv(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=xpra_environment(self.config.log_level),
            )
        except OSError as error:
            raise SSHError(
                "xpra_start_failed", "cannot attach the local Xpra client"
            ) from error
        assert self.client.stdout is not None and self.client.stderr is not None
        self._client_drains = (
            asyncio.create_task(
                self._drain_client(
                    self.client.stdout,
                    self.client_stdout,
                )
            ),
            asyncio.create_task(
                self._drain_client(
                    self.client.stderr,
                    self.client_stderr,
                )
            ),
        )

    async def _finish_client_drains(self) -> None:
        if not self._client_drains:
            return
        drains, self._client_drains = self._client_drains, ()
        await asyncio.gather(*drains, return_exceptions=True)

    def _diagnostic(self, raw: str) -> str:
        """Bound one diagnostic line and remove owned private local paths."""
        for path in (
            self._wrapper,
            self.master.control_path,
            self.master.runtime_dir,
        ):
            if path is not None:
                raw = raw.replace(str(path), "<private-path>")

        selected: list[str] = []
        for raw_line in raw.splitlines():
            line = ANSI_ESCAPE.sub("", raw_line)
            line = "".join(character for character in line if character.isprintable())
            line = " ".join(line.split())
            lowered = line.lower()
            if line and any(marker in lowered for marker in CLIENT_DIAGNOSTIC_MARKERS):
                selected.append(line)
        diagnostic = " | ".join(selected[-CLIENT_DIAGNOSTIC_LINES:])
        return diagnostic[-CLIENT_DIAGNOSTIC_CHARACTERS:]

    def _client_diagnostic(self) -> str:
        raw = self.client_stderr.text() or self.client_stdout.text()
        return self._diagnostic(raw)

    def _remote_diagnostic(self) -> str:
        remote = self.remote
        if remote is None:
            return ""
        stderr = getattr(remote, "stderr_tail", None)
        stdout = getattr(remote, "stdout_tail", None)
        raw = stderr.text() if stderr is not None else ""
        if not raw and stdout is not None:
            raw = stdout.text()
        return self._diagnostic(raw)

    async def _wait_lifecycle(self) -> int:
        remote = self.persistent or self.remote
        client = self.client
        master_process = self.master.process
        assert remote is not None and client is not None and master_process is not None
        client_wait = asyncio.create_task(client.wait())
        remote_wait = asyncio.create_task(remote.wait())
        master_wait = asyncio.create_task(master_process.wait())
        waits = {client_wait, remote_wait, master_wait}
        try:
            done, _pending = await asyncio.wait(
                waits, return_when=asyncio.FIRST_COMPLETED
            )
            if client_wait in done and client_wait.result() == 0:
                return 0
            if master_wait in done:
                raise SSHError("connection_lost", "SSH master was lost")
            if remote_wait in done:
                status = remote_wait.result()
                if status == 0:
                    return 0
                diagnostic = self._remote_diagnostic()
                message = f"remote Xpra exited with status {status}"
                if diagnostic:
                    message = f"{message}: {diagnostic}"
                raise SSHError(
                    "xpra_server_exited",
                    message,
                )
            status = client_wait.result()
            await self._finish_client_drains()
            diagnostic = self._client_diagnostic()
            message = f"local Xpra client exited with status {status}"
            if diagnostic:
                message = f"{message}: {diagnostic}"
            raise SSHError(
                "xpra_client_failed",
                message,
            )
        finally:
            for task in waits:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def run(self) -> int:
        """Start, attach, wait, and clean every owned process in order."""
        try:
            try:
                self.journal.open()
            except JournalError as error:
                raise SSHError("journal_unavailable", str(error)) from None
            if self.config.log_level in {"debug", "debug-clipboard"}:
                message = "debug logging is enabled on both hosts; system journals may contain clipboard contents and other sensitive data"
                self.journal.emit(PRIORITIES["warning"], message)
            self.journal.emit(PRIORITIES["info"], "checking local Xpra capabilities")
            await self._check_local_capabilities()
            self.journal.emit(PRIORITIES["info"], "opening the owned SSH master")
            await self.master.start()
            self._wrapper = self.master.create_mux_wrapper("xpra-ssh")
            await self._subscribe_logs(self.session_id)
            if self.config.persistent:
                self.persistent = PersistentSession(
                    self._run_mux,
                    self._run_mux_interactive,
                    self.config.poll_interval,
                    log_level=self.config.log_level,
                    journal=self.journal,
                )
                session_id = await self.persistent.identify(self.config.application)
                # Finish the provisional observer before releasing startup logs
                # under the stable, host/account-qualified persistent identity.
                await self._remote_logs[-1].close()
                self._remote_logs.pop()
                self.session_id = session_id
                self.journal.flush(session_id)
                await self._subscribe_logs(session_id)
            await self._check_remote_capabilities()
            if self.persistent is not None:
                await self.persistent.start(self.config.application, self.server_argv())
                self.session_name = self.persistent.record["session_name"]
            else:
                self.remote = OwnedRemoteProcess(
                    self.master,
                    remote_argv(
                        self.server_argv(),
                        self.config.log_level,
                        self.session_id,
                        with_session_bus=True,
                    ),
                    heartbeat_interval=self.config.heartbeat_interval,
                    lease_timeout=self.config.lease_timeout,
                    grace_timeout=self.config.grace_timeout,
                )
                await self.remote.start()
            await self._wait_ready()
            await self._start_client()
            self.journal.emit(PRIORITIES["info"], "Xpra client attached")
            return await self._wait_lifecycle()
        except asyncio.CancelledError:
            self.journal.emit(PRIORITIES["info"], "session cancelled")
            raise
        except SSHError as error:
            self.journal.emit(PRIORITIES["error"], f"{error.code}: {error.message}")
            raise ReportedSSHError(error.code, error.message, error.details) from None
        except Exception as error:  # noqa: BLE001 - public journal diagnostics stay sanitized.
            self.journal.emit(
                PRIORITIES["error"], f"session_failed: {type(error).__name__}"
            )
            raise ReportedSSHError("session_failed", type(error).__name__) from None
        finally:
            await _terminate_local(self.client)
            await self._finish_client_drains()
            if self.remote is not None:
                await self.remote.close()
            for channel in reversed(self._remote_logs):
                await channel.close()
            await self.master.close()
            self.journal.flush()
            self.journal.emit(PRIORITIES["info"], "local session resources closed")
            self.journal.close()
