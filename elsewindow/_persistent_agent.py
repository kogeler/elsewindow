# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Standard-library remote entry point, sent as inert source over the owned mux."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from elsewindow.journal import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    PRIORITIES,
    REMOTE_LOADER,
    Journal,
    JournalError,
    XpraLogStream,
    source_label,
    xpra_environment,
)
from elsewindow.log_transport import LOG_PROTOCOL

SCHEMA = 1
MAX_BYTES = 128 * 1024
COMMAND_TIMEOUT = 15
LOADER = REMOTE_LOADER
APP_LOADER = "import base64,json,os,sys;a=json.loads(base64.b64decode(sys.argv[1]));os.execv(a[0],a)"
IDENTITY = re.compile(r"[a-f0-9]{64}")
TOKEN = re.compile(r"[a-f0-9]{32}")
DISPLAY = re.compile(r"wayland-[0-9]+")


class AgentError(Exception):
    """Only these fixed diagnostics may cross the SSH boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def encode(value: object) -> str:
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def log_session(key: str) -> str:
    """Namespace application identity by the remote machine and account."""
    machine = Path("/etc/machine-id").read_text().strip()
    if TOKEN.fullmatch(machine) is None:
        raise AgentError(
            "persistent_systemd_unavailable", "remote machine identity is unavailable"
        )
    return digest(["elsewindow-log", machine, os.getuid(), key])


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )


def secure_directory(path: Path, *, create: bool = False) -> Path:
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise AgentError(
            "persistent_unsafe_state",
            "persistent runtime ownership or permissions are unsafe",
        )
    return path


def runtime_directory() -> Path:
    runtime = secure_directory(Path("/run/user") / str(os.getuid()))
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    return runtime


def linger_enabled() -> bool:
    result = run(
        "/usr/bin/loginctl",
        "show-user",
        str(os.getuid()),
        "--property=Linger",
        "--value",
    )
    if result.returncode or result.stdout.strip() not in {"yes", "no"}:
        raise AgentError(
            "persistent_systemd_unavailable",
            "cannot query the remote account through systemd-logind",
        )
    return result.stdout.strip() == "yes"


def prerequisites() -> dict[str, Any]:
    for name in ("loginctl", "systemctl", "systemd-run"):
        if not os.access(f"/usr/bin/{name}", os.X_OK):
            raise AgentError(
                "persistent_systemd_unavailable",
                "persistent sessions require systemd, loginctl and systemd-run on the remote host",
            )
    linger = linger_enabled()
    manager = False
    if linger:
        runtime_directory()
        manager = (
            run("/usr/bin/systemctl", "--user", "show-environment").returncode == 0
        )
        if not manager:
            raise AgentError(
                "persistent_systemd_unavailable",
                "the remote systemd user manager is not available",
            )
    return {"linger": linger, "manager": manager}


def enable_linger() -> dict[str, Any]:
    """Called only after the local terminal has supplied affirmative consent."""
    if not linger_enabled():
        command = ("/usr/bin/loginctl", "enable-linger", str(os.getuid()))
        direct = run(command[0], "--no-ask-password", *command[1:])
        if direct.returncode:
            if not os.access("/usr/bin/sudo", os.X_OK):
                raise AgentError(
                    "persistent_linger_failed",
                    "enabling remote linger requires permission and /usr/bin/sudo",
                )
            # stdin is the operator's remote PTY; passwords never enter Python.
            result = subprocess.run(("/usr/bin/sudo", *command), check=False)
            if result.returncode:
                raise AgentError(
                    "persistent_linger_failed",
                    "permission to enable remote linger was not granted",
                )
    if not linger_enabled():
        raise AgentError("persistent_linger_failed", "remote linger is still disabled")
    return {"linger": True}


def canonical_application(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 256
        or any(not isinstance(arg, str) or "\0" in arg for arg in value)
        or sum(len(arg.encode()) for arg in value) > 16 * 1024
    ):
        raise AgentError(
            "invalid_application", "persistent application arguments are invalid"
        )
    executable = value[0]
    if "/" not in executable:
        executable = shutil.which(executable) or ""
    if not executable:
        raise AgentError(
            "invalid_application", "the remote application executable was not found"
        )
    # Preserve invocation semantics: resolving a venv interpreter symlink would
    # launch the system interpreter, and resolving a versioned app link would
    # change the session key after an update. Keep '..' for kernel resolution too.
    path = Path(executable).absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AgentError(
            "invalid_application", "the remote application is not an executable file"
        )
    return [str(path), *value[1:]]


def state_root() -> Path:
    return secure_directory(runtime_directory() / "elsewindow-persistent", create=True)


@contextmanager
def locked(root: Path, key: str) -> Iterator[None]:
    if IDENTITY.fullmatch(key) is None:
        raise AgentError(
            "persistent_unsafe_state", "invalid persistent session identity"
        )
    descriptor = os.open(
        root / f"{key}.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise AgentError(
                "persistent_unsafe_state", "unsafe persistent session lock"
            )
        deadline = time.monotonic() + COMMAND_TIMEOUT
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AgentError(
                        "persistent_busy",
                        "persistent session creation is still in progress",
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def read_record(root: Path, key: str) -> dict[str, Any] | None:
    try:
        fd = os.open(root / f"{key}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise AgentError(
                "persistent_unsafe_state", "unsafe persistent session record"
            )
        data = stream.read(MAX_BYTES + 1)
    value = json.loads(data) if len(data) <= MAX_BYTES else None
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("key") != key
        or not isinstance(value.get("token"), str)
        or TOKEN.fullmatch(value["token"]) is None
    ):
        raise AgentError("persistent_unsafe_state", "invalid persistent session record")
    return value


def write_record(root: Path, record: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        dir=root, prefix=".record-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(record, ensure_ascii=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, root / f"{record['key']}.json")
        finally:
            temporary.unlink(missing_ok=True)


def unit_name(key: str) -> str:
    return f"elsewindow-{key}.service"


def description(record: dict[str, Any]) -> str:
    return f"Elsewindow persistent {record['key']} {record['token']}"


def unit_state(key: str) -> dict[str, str]:
    result = run(
        "/usr/bin/systemctl",
        "--user",
        "--no-pager",
        "show",
        unit_name(key),
        "--property=LoadState,ActiveState,MainPID,Description,InvocationID",
    )
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    if fields.get("LoadState") == "not-found":
        return {}
    if result.returncode or fields.get("LoadState") != "loaded":
        raise AgentError(
            "persistent_systemd_unavailable",
            "cannot inspect the persistent systemd service",
        )
    return fields


def process_start(pid: int) -> str:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return ""
    return text[text.rfind(")") + 2 :].split()[19]


def verify_unit(record: dict[str, Any] | None, state: dict[str, str]) -> None:
    if record is None or state.get("Description") != description(record):
        raise AgentError(
            "persistent_identity_mismatch",
            "an existing systemd service does not match the owned session",
        )
    if record.get("worker_pid") and (
        state.get("MainPID") != str(record["worker_pid"])
        or state.get("InvocationID") != record.get("invocation")
        or process_start(record["worker_pid"]) != record.get("worker_start")
    ):
        raise AgentError(
            "persistent_identity_mismatch",
            "the persistent service process identity changed",
        )


def public_record(record: dict[str, Any], *, created: bool = False) -> dict[str, Any]:
    return {
        name: record.get(name)
        for name in (
            "key",
            "log_session",
            "token",
            "display",
            "argv_sha256",
            "xpra_pid",
            "xpra_start",
            "session_name",
        )
    } | {"created": created}


def ensure(request: dict[str, Any], source: str) -> dict[str, Any]:
    """Serialize lookup and creation; never stop or replace an existing service."""
    # Complete an accepted launch even if the SSH command loses its terminal.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    application = canonical_application(request["application"])
    key = digest(application)
    session = log_session(key)
    if request.get("log_session", session) != session:
        raise AgentError(
            "persistent_identity_mismatch", "persistent log session changed"
        )
    level = request.get("log_level", DEFAULT_LOG_LEVEL)
    if level not in LOG_LEVELS:
        raise AgentError("invalid_configuration", "invalid logging policy")
    root = state_root()
    server = request["server"]
    if not isinstance(server, list) or not all(isinstance(arg, str) for arg in server):
        raise AgentError("invalid_configuration", "invalid persistent server command")
    server = [arg.replace("$XDG_RUNTIME_DIR", str(root.parent)) for arg in server]
    child = shlex.join((sys.executable, "-c", APP_LOADER, encode(application)))
    indexes = [i for i, arg in enumerate(server) if arg.startswith("--start-child=")]
    if len(indexes) != 1:
        raise AgentError(
            "invalid_configuration", "persistent server has no unique child command"
        )
    server[indexes[0]] = f"--start-child={child}"
    session_name = f"elsewindow-{key[:16]}"
    server = [
        f"--session-name={session_name}" if arg.startswith("--session-name=") else arg
        for arg in server
    ]
    argv_sha256 = digest(["xpra", *server[3:]])
    with locked(root, key):
        record = read_record(root, key)
        state = unit_state(key)
        if state:
            verify_unit(record, state)
            if state.get("ActiveState") in {"active", "activating", "reloading"}:
                assert record is not None
                if (
                    record.get("application") != application
                    or record.get("argv_sha256") != argv_sha256
                    or record.get("log_level") != level
                    or record.get("journal_protocol") != LOG_PROTOCOL
                    or record.get("log_session") != session
                ):
                    raise AgentError(
                        "persistent_configuration_mismatch",
                        "the existing session uses different server, clipboard or logging settings; reconnect with its original settings or exit the application first",
                    )
                return public_record(record)
            raise AgentError(
                "persistent_busy",
                "the previous persistent service is still stopping; retry shortly",
            )
        record = {
            "schema": SCHEMA,
            "key": key,
            "token": secrets.token_hex(16),
            "application": application,
            "server": server,
            "argv_sha256": argv_sha256,
            "cwd": str(Path.cwd()),
            "environment": {
                key: os.environ[key]
                for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
                if key in os.environ
            },
            "display": None,
            "worker_pid": 0,
            "session_name": session_name,
            "log_level": level,
            "journal_protocol": LOG_PROTOCOL,
            "log_session": session,
        }
        write_record(root, record)
        command = (
            "/usr/bin/systemd-run",
            "--user",
            "--quiet",
            "--collect",
            f"--unit={unit_name(key)}",
            f"--description={description(record)}",
            "--service-type=exec",
            "--expand-environment=no",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=10s",
            "--property=UMask=0077",
            "--property=StandardOutput=journal",
            "--property=StandardError=journal",
            f"--property=SyslogIdentifier={source_label('server')}",
            "--property=SyslogLevel=err",
            f"--property=LogLevelMax={PRIORITIES['debug' if level == 'debug-clipboard' else level]}",
            "--",
            sys.executable,
            "-c",
            LOADER,
            source,
            "serve",
            encode(
                {
                    "key": key,
                    "token": record["token"],
                    "log_session": session,
                    "log_level": level,
                }
            ),
        )
        result = run(*command)
        if result.returncode:
            # The service may have started despite loss of the command reply.
            # Preserve its ownership record for the next invocation.
            raise AgentError(
                "persistent_start_failed",
                "could not start the persistent user service; retry to inspect its state",
            )
        return public_record(record, created=True)


def status(request: dict[str, Any]) -> dict[str, Any]:
    root = state_root()
    key = request["key"]
    with locked(root, key):
        record = read_record(root, key)
        if record is None or record["token"] != request["token"]:
            return {"ended": True}
        state = unit_state(key)
        if not state or state.get("ActiveState") not in {
            "active",
            "activating",
            "reloading",
        }:
            return {"ended": True}
        verify_unit(record, state)
        return public_record(record) | {"ended": False}


def serve(request: dict[str, Any]) -> None:
    """The user manager owns this process and its entire application cgroup."""
    root = state_root()
    key, token = request["key"], request["token"]
    process: subprocess.Popen[bytes] | None = None
    stopping = False
    journal: Journal | None = None
    streams: list[XpraLogStream] = []

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    for selected in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(selected, stop)
    try:
        with locked(root, key):
            record = read_record(root, key)
            if record is None or record["token"] != token:
                return
            invocation = os.environ.get("INVOCATION_ID", "")
            if TOKEN.fullmatch(invocation) is None:
                raise AgentError(
                    "persistent_identity_mismatch",
                    "the persistent supervisor is not running in a systemd invocation",
                )
            os.chdir(record["cwd"])
            os.environ.update(record["environment"])
            journal = Journal(
                record.get("log_level", DEFAULT_LOG_LEVEL),
                "server",
                record["log_session"],
            )
            journal.open()
            record.update(
                worker_pid=os.getpid(),
                worker_start=process_start(os.getpid()),
                invocation=invocation,
            )
            process = subprocess.Popen(
                record["server"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=xpra_environment(journal.level),
            )
            record.update(xpra_pid=process.pid, xpra_start=process_start(process.pid))
            write_record(root, record)
        journal.emit(PRIORITIES["info"], "persistent Xpra process started")
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            for stream, is_error in ((process.stdout, False), (process.stderr, True)):
                log = XpraLogStream(journal, stderr=is_error, pid=process.pid)
                streams.append(log)
                selector.register(stream, selectors.EVENT_READ, log)
            pending = b""
            exited_at = None
            while selector.get_map() and not stopping:
                for event, _mask in selector.select(timeout=0.2):
                    chunk = os.read(event.fd, 4096)
                    if not chunk:
                        event.data.finish()
                        selector.unregister(event.fileobj)
                        continue
                    event.data.feed(chunk)
                    if event.fileobj is not process.stdout:
                        continue
                    pending += chunk
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        display = line.decode("ascii", errors="replace").strip()
                        if DISPLAY.fullmatch(display) and not record.get("display"):
                            with locked(root, key):
                                record["display"] = display
                                write_record(root, record)
                    pending = pending[-1024:]
                if process.poll() is not None:
                    exited_at = exited_at or time.monotonic()
                    if time.monotonic() - exited_at > 1:
                        break
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            if journal is not None:
                for log_stream in streams:
                    log_stream.finish()
                journal.emit(
                    PRIORITIES["error" if process.returncode else "info"],
                    f"persistent Xpra process exited with status {process.returncode}",
                )
                journal.close()
        with locked(root, key):
            latest = read_record(root, key)
            if latest is not None and latest["token"] == token:
                (root / f"{key}.json").unlink()
        # systemd KillMode=control-group reaps any remaining descendants.


def main(arguments: list[str]) -> int:
    journal: Journal | None = None
    serving = False
    try:
        source, action, encoded = arguments
        serving = action == "serve"
        if len(encoded) > MAX_BYTES * 2:
            raise ValueError("payload exceeds limit")
        request = json.loads(base64.b64decode(encoded, validate=True))
        if not isinstance(request, dict):
            raise TypeError("payload must be an object")
        if action in {
            "identify",
            "probe",
            "enable-linger",
            "ensure",
            "status",
            "serve",
        }:
            journal = Journal(
                request.get("log_level", DEFAULT_LOG_LEVEL),
                "server",
                request.get("log_session", ""),
            )
            journal.open()
        if action == "identify":
            key = digest(canonical_application(request["application"]))
            # No successful record precedes identity negotiation. Errors still
            # use the shared invocation ID supplied by the client.
            result = {"key": key, "log_session": log_session(key)}
        elif action == "probe":
            result = prerequisites()
            if journal is not None:
                journal.emit(PRIORITIES["info"], "persistent prerequisites checked")
        elif action == "enable-linger":
            result = enable_linger()
        elif action == "ensure":
            result = ensure(request, source)
            if journal is not None:
                journal.emit(PRIORITIES["info"], "persistent session selected")
        elif action == "status":
            result = status(request)
        elif action == "serve":
            serve(request)
            return 0
        else:
            raise ValueError("invalid operation")
        print(json.dumps(result), flush=True)
        return 0
    except AgentError as error:
        if journal is not None:
            journal.emit(PRIORITIES["error"], f"{error.code}: {error}")
        if not serving:
            print(json.dumps({"error": error.code, "message": str(error)}), flush=True)
    except JournalError:
        if not serving:
            print(json.dumps({"error": "journal_unavailable"}), flush=True)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        if not serving:
            print(
                json.dumps(
                    {
                        "error": "persistent_operation_failed",
                        "message": "the remote persistent operation failed",
                    }
                ),
                flush=True,
            )
        if journal is not None:
            journal.emit(PRIORITIES["error"], "persistent operation failed")
    finally:
        if journal is not None:
            journal.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
