# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Bounded native journald output shared with the standard-library remote agent."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import select
import selectors
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
from importlib.resources import files
from typing import Any

from .desktop import FEATURE_PREFIX
from .log_transport import MAX_MESSAGE, MAX_QUEUE, SESSION, Publisher
from .session_bus import OwnedSessionBus, SessionBusError

DEFAULT_LOG_LEVEL = "warning"
LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "debug-clipboard")
PRIORITIES = {"critical": 2, "error": 3, "warning": 4, "info": 6, "debug": 7}
JOURNAL_SOCKET = "/run/systemd/journal/socket"
MAX_LINE = MAX_MESSAGE
LOG_FORMAT = "ELSEWINDOW_LOG|%(levelname)s|%(name)s|%(message)s"
LOG_HEADER = re.compile(
    rb"^ELSEWINDOW_LOG\|(DEBUG|INFO|WARNING|ERROR|CRITICAL)\|([^|]*)\|(.*)$"
)
REMOTE_LOADER = (
    "import base64,json,sys,types,zlib;"
    "d=json.loads(zlib.decompress(base64.b64decode(sys.argv[1])));"
    "p=types.ModuleType('elsewindow');p.__path__=[];sys.modules[p.__name__]=p;"
    "\nfor n in ('desktop','session_bus','log_transport','journal'):\n"
    " m=types.ModuleType('elsewindow.'+n);m.__source__=d[n];sys.modules[m.__name__]=m;"
    "exec(compile(d[n],'<elsewindow-'+n+'>','exec'),m.__dict__)\n"
    "exec(compile(d['main'],'<elsewindow-agent>','exec'))"
)


class JournalError(RuntimeError):
    """A native journal is unavailable, without exposing private paths."""


def source_label(side: str, xpra: bool = False) -> str:
    return f"elsewindow{'-xpra' if xpra else ''}-{'local' if side == 'client' else 'remote'}"


def terminal_lines(label: str, data: bytes) -> list[str]:
    """Keep a visible source on every line without accepting terminal controls."""
    marker = re.match(rb"\[session=[a-f0-9]{16,64}\] ", data)
    prefix = marker.group().decode() if marker else ""
    if marker:
        data = data[marker.end() :]
    return [
        f"{label}: {prefix}"
        + "".join(character if character.isprintable() else " " for character in line)
        for line in data.decode("utf-8", errors="replace").splitlines()
    ]


def xpra_environment(level: str) -> dict[str, str]:
    """Use the fork's public logging environment, never import Xpra internals."""
    if level not in LOG_LEVELS:
        raise ValueError("invalid log level")
    environment = os.environ.copy()
    # Frozen launchers must not pass their bundled native libraries to Xpra.
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", "")
        if original:
            environment["LD_LIBRARY_PATH"] = original
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    environment.update(
        XPRA_LOG_FORMAT=LOG_FORMAT,
        XPRA_LOG_PREFIX="",
        XPRA_COLOR_LOG="0",
        XPRA_EMOJIS="0",
        XPRA_LOG_TO_FILE="0",
        XPRA_LOG_SYSTEMD_WRAP="0",
        XPRA_DEBUG_DOTFILE="",
        XPRA_ALL_DEBUG="1" if level == "debug" else "0",
        XPRA_CLIPBOARD_DEBUG="1" if level == "debug-clipboard" else "0",
        XPRA_X11_DEBUG_EVENTS="XFSelectionNotify"
        if level in {"debug", "debug-clipboard"}
        else "",
    )
    return environment


class Journal:
    """One host-local sender; journal retention and access remain system policy."""

    def __init__(
        self, level: str, side: str, session: str = "", *, deferred: bool = False
    ) -> None:
        if level not in LOG_LEVELS or side not in {"client", "server"}:
            raise ValueError("invalid journal policy")
        session = session or secrets.token_hex(16)
        if SESSION.fullmatch(session) is None or (deferred and side != "client"):
            raise ValueError("invalid journal session")
        self.level = level
        self.side = side
        self.session = session
        self.socket: socket.socket | None = None
        self.failed = False
        self.terminal_open = side == "client"
        self.publisher = Publisher() if side == "server" else None
        self.deferred = deferred
        self.pending: list[tuple[int, bytes, bool, str, int, str]] = []
        self.pending_bytes = 0
        self.dropped = 0

    def flush(self, session: str | None = None) -> None:
        """Release bounded startup records once the shared identity is known."""
        if session is not None:
            if SESSION.fullmatch(session) is None:
                raise ValueError("invalid journal session")
            self.session = session
        self.deferred = False
        pending, self.pending = self.pending, []
        self.pending_bytes = 0
        for priority, data, xpra, category, pid, origin in pending:
            self.emit(
                priority, data, xpra=xpra, category=category, pid=pid, origin=origin
            )
        dropped, self.dropped = self.dropped, 0
        if dropped:
            self.emit(
                PRIORITIES["warning"], f"startup log buffer dropped {dropped} records"
            )

    def open(self) -> None:
        if self.socket is not None:
            return
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        channel.settimeout(0.1)
        try:
            channel.connect(JOURNAL_SOCKET)
        except OSError:
            channel.close()
            raise JournalError(f"{self.side} system journal is unavailable") from None
        self.socket = channel

    def close(self) -> None:
        if self.publisher is not None:
            self.publisher.close()
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def open_optional(self) -> None:
        """Keep terminal output and the owned relay usable without journald."""
        try:
            self.open()
        except (OSError, JournalError):
            self._delivery_failed()

    def _delivery_failed(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.failed:
            return
        self.failed = True
        side = "local" if self.side == "client" else "remote"
        self.emit(
            PRIORITIES["warning"],
            f"{side} journal delivery failed; native journal recording is currently "
            f"unavailable. On the {side} host, install the systemd package and "
            "enable systemd-journald. Terminal logging and session lifetime are unchanged.",
        )

    def emit(
        self,
        priority: int,
        message: str | bytes,
        *,
        xpra: bool = False,
        category: str = "",
        pid: int = 0,
        origin: str | None = None,
        session: str | None = None,
    ) -> None:
        origin = self.side if origin is None else origin
        if origin not in {"client", "server"} or (
            self.side == "server" and origin != "server"
        ):
            raise ValueError("invalid journal origin")
        session = self.session if session is None else session
        if SESSION.fullmatch(session) is None:
            raise ValueError("invalid journal session")
        limit = PRIORITIES.get(self.level, PRIORITIES["warning"])
        clipboard = self.level == "debug-clipboard" and "clipboard" in category.lower()
        if priority > limit and not clipboard:
            return
        data = (
            message.encode("utf-8", errors="replace")
            if isinstance(message, str)
            else message
        )
        data = data[:MAX_LINE]
        category = re.sub(r"[^a-zA-Z0-9_.-]", "_", category)[:128]
        if self.deferred:
            size = len(data) + len(category) + 256
            if self.pending_bytes + size <= MAX_QUEUE:
                self.pending.append(
                    (priority, data, xpra, category, pid or os.getpid(), origin)
                )
                self.pending_bytes += size
            else:
                self.dropped += 1
            return
        if self.publisher is not None:
            self.publisher.emit(
                session,
                priority,
                data,
                xpra=xpra,
                category=category,
                pid=pid or os.getpid(),
            )
        data = f"[session={session}] ".encode() + data
        label = source_label(origin, xpra)
        if self.terminal_open:
            output = sys.stderr if priority <= PRIORITIES["warning"] else sys.stdout
            try:
                for line in terminal_lines(label, data):
                    print(line, file=output, flush=True)
            except (OSError, ValueError):
                self.terminal_open = False
        fields = (
            f"PRIORITY={priority}\n"
            f"SYSLOG_IDENTIFIER={label}\n"
            f"ELSEWINDOW_SIDE={origin}\n"
            f"ELSEWINDOW_COMPONENT={'xpra' if xpra else 'elsewindow'}\n"
            f"ELSEWINDOW_FORWARDED={int(origin != self.side)}\n"
            f"ELSEWINDOW_SESSION={session}\n"
            f"ELSEWINDOW_LOG_LEVEL={self.level}\n"
            f"ELSEWINDOW_CATEGORY={category}\n"
            f"ELSEWINDOW_SOURCE_PID={pid or os.getpid()}\n"
        ).encode()
        packet = fields + b"MESSAGE\n" + struct.pack("<Q", len(data)) + data + b"\n"
        try:
            self.open()
            assert self.socket is not None
            self.socket.send(packet)
            self.failed = False
        except (OSError, JournalError):
            self._delivery_failed()


class XpraLogStream:
    """Parse declared severity; retain traceback continuation severity and bounds."""

    def __init__(self, journal: Journal, *, stderr: bool, pid: int = 0) -> None:
        self.journal = journal
        self.priority = PRIORITIES["warning" if stderr else "info"]
        self.category = ""
        self.pid = pid
        self.pending = b""

    def feed(self, data: bytes) -> None:
        self.pending += data
        while b"\n" in self.pending or len(self.pending) >= MAX_LINE:
            end = self.pending.find(b"\n", 0, MAX_LINE)
            length = end if end >= 0 else MAX_LINE
            line = self.pending[:length]
            self.pending = self.pending[length + (end >= 0) :]
            self.line(line)

    def line(self, line: bytes) -> None:
        marker = FEATURE_PREFIX.encode()
        if line.startswith(marker):
            self.journal.emit(
                PRIORITIES["warning"],
                line[len(marker) :],
                category="prerequisites",
                pid=self.pid,
            )
            return
        match = LOG_HEADER.match(line)
        if match:
            level, category, line = match.groups()
            self.priority = PRIORITIES[level.decode().lower()]
            self.category = category.decode("utf-8", errors="replace")
        if line:
            category = "clipboard" if b"XFSelectionNotify" in line else self.category
            self.journal.emit(
                self.priority, line, xpra=True, category=category, pid=self.pid
            )

    def finish(self) -> None:
        if self.pending:
            self.line(self.pending)
            self.pending = b""


class ForwardedOutput:
    """Bound SSH output without letting an unread channel block the owner."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.pending: dict[int, bytearray] = {}
        self.blocking: dict[int, bool] = {}
        self.dropped = 0
        # Read both flags before changing either: stdout and stderr can share
        # an open file description. Restore the inherited flags when finished.
        for destination in (1, 2):
            try:
                self.blocking[destination] = os.get_blocking(destination)
            except OSError:
                continue
        for destination in self.blocking:
            try:
                os.set_blocking(destination, False)
            except OSError:
                continue
            self.pending[destination] = bytearray()

    def feed(self, destination: int, data: bytes) -> None:
        self.flush()
        pending = self.pending.get(destination)
        if pending is None:
            self.dropped += len(data)
            return
        available = MAX_QUEUE - len(pending)
        pending.extend(data[:available])
        self.dropped += max(0, len(data) - available)
        self.flush()

    def flush(self) -> None:
        for destination, pending in tuple(self.pending.items()):
            if not pending:
                continue
            try:
                written = os.write(destination, pending)
            except BlockingIOError:
                continue
            except OSError:
                # Output loss is independent of application lifetime. Native
                # journaling and the separate bounded log observers continue.
                self.dropped += len(pending)
                del self.pending[destination]
            else:
                del pending[:written]

    def close(self, *, drain_timeout: float = 0) -> None:
        deadline = time.monotonic() + drain_timeout
        while True:
            self.flush()
            destinations = tuple(
                destination for destination, pending in self.pending.items() if pending
            )
            remaining = deadline - time.monotonic()
            if not destinations or remaining <= 0:
                break
            try:
                select.select((), destinations, (), remaining)
            except OSError:
                break
        self.dropped += sum(len(pending) for pending in self.pending.values())
        self.pending.clear()
        for destination, blocking in self.blocking.items():
            try:
                os.set_blocking(destination, blocking)
            except OSError:
                pass
        if self.dropped:
            self.journal.emit(
                PRIORITIES["warning"],
                f"SSH output unavailable or backpressured; {self.dropped} bytes "
                "of raw output were not forwarded. Native journal processing "
                "and application cleanup continued.",
            )


def remote_source(main: str) -> str:
    """Package owned source as data, with no remote Elsewindow installation."""
    payload = {
        "desktop": files("elsewindow").joinpath("desktop.py").read_text(),
        "session_bus": files("elsewindow").joinpath("session_bus.py").read_text(),
        "journal": files("elsewindow").joinpath("journal.py").read_text(),
        "log_transport": files("elsewindow").joinpath("log_transport.py").read_text(),
        "main": main,
    }
    return base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()


def remote_argv(
    argv: tuple[str, ...], level: str, session: str, *, with_session_bus: bool = False
) -> tuple[str, ...]:
    return (
        "python3",
        "-c",
        REMOTE_LOADER,
        remote_source(
            "from elsewindow.journal import remote_main\n"
            f"raise SystemExit(remote_main(sys.argv[2:], with_session_bus={with_session_bus!r}))"
        ),
        level,
        session,
        base64.b64encode(json.dumps(argv).encode()).decode(),
    )


def remote_main(arguments: list[str], *, with_session_bus: bool = False) -> int:
    """Relay ordinary remote output inside the already owned process group."""
    level, session, encoded = arguments
    journal = Journal(level, "server", session)
    child: subprocess.Popen[bytes] | None = None
    stopping_at: float | None = None
    forwarding = ForwardedOutput(journal)
    streams: list[XpraLogStream] = []
    bus = OwnedSessionBus()

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopping_at
        # The heartbeat owner signals the whole group, including Xpra. Sending
        # another SIGTERM here can interrupt Xpra's graceful socket cleanup.
        stopping_at = stopping_at or time.monotonic()

    for selected in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(selected, stop)
    try:
        journal.open_optional()
        argv = json.loads(base64.b64decode(encoded))
        environment = xpra_environment(level)
        if with_session_bus:
            environment = bus.start_optional(
                environment,
                lambda message: journal.emit(PRIORITIES["warning"], message),
            )
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        journal.emit(PRIORITIES["info"], "remote Xpra process started")
        assert child.stdout is not None and child.stderr is not None
        exited_at = None
        with selectors.DefaultSelector() as selector:
            for stream, is_error in ((child.stdout, False), (child.stderr, True)):
                log = XpraLogStream(journal, stderr=is_error, pid=child.pid)
                streams.append(log)
                selector.register(
                    stream,
                    selectors.EVENT_READ,
                    log,
                )
            while selector.get_map():
                forwarding.flush()
                for event, _mask in selector.select(timeout=0.2):
                    data = os.read(event.fd, 4096)
                    if data:
                        event.data.feed(data)
                        destination = 2 if event.fileobj is child.stderr else 1
                        forwarding.feed(destination, data)
                    else:
                        event.data.finish()
                        selector.unregister(event.fileobj)
                if child.poll() is not None:
                    exited_at = exited_at or time.monotonic()
                    if time.monotonic() - exited_at > 1:
                        break
                if stopping_at is not None and time.monotonic() - stopping_at > 5:
                    break
        status = child.wait(timeout=5)
        journal.emit(
            PRIORITIES["error" if status else "info"],
            f"remote Xpra process exited with status {status}",
        )
        return status if status >= 0 else 128 - status
    except (OSError, JournalError, SessionBusError, subprocess.TimeoutExpired) as error:
        message = (
            str(error)
            if isinstance(error, (JournalError, SessionBusError))
            else "remote Xpra journal relay failed"
        )
        journal.emit(PRIORITIES["error"], message)
        forwarding.feed(2, f"elsewindow: {message}\n".encode())
        return 1
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            for pipe in (child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()
        bus.close()
        for log_stream in streams:
            log_stream.finish()
        # Short command responses must survive a briefly delayed SSH reader.
        # Group termination, unlike an ordinary command exit, never waits for it.
        forwarding.close(drain_timeout=1 if stopping_at is None else 0)
        journal.close()
