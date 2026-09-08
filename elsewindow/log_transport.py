# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Bounded live log fan-out and one no-reconnect SSH subscriber per session key."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import selectors
import shlex
import signal
import socket
import stat
import time
from contextlib import suppress
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ssh_wrapper.connection import OpenSSHMaster

    from .journal import Journal

MAX_MESSAGE = 16 * 1024
MAX_FRAME = 32 * 1024
MAX_QUEUE = 8 * MAX_FRAME
MAX_SUBSCRIBERS = 16
LOG_PROTOCOL = 2
SESSION = re.compile(r"[a-f0-9]{16,64}")
SOCKET_NAME = re.compile(r"[a-f0-9]{16}\.sock")
ROOT = Path("/tmp") / f"elsewindow-log-{os.getuid()}"


class LogTransportError(RuntimeError):
    """Log transport failed without granting any new connection authority."""


def _directory(session: str, *, create: bool = False) -> Path:
    if SESSION.fullmatch(session) is None:
        raise LogTransportError("invalid remote log session")
    directory = ROOT / hashlib.sha256(session.encode()).hexdigest()[:32]
    for path in (ROOT, directory):
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        details = path.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_mode & 0o077
        ):
            raise LogTransportError("remote log directory is not private to its owner")
    return directory


def encode(record: dict[str, Any]) -> bytes:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def decode(data: bytes, session: str) -> dict[str, Any]:
    """Accept only a bounded record for the subscribed session, never executable data."""
    if len(data) > MAX_FRAME:
        raise LogTransportError("remote log frame exceeds its limit")
    try:
        record = json.loads(data)
        if not isinstance(record, dict):
            raise TypeError
        if set(record) == {"gap"}:
            if type(record["gap"]) is not int or not 0 < record["gap"] <= 2**32:
                raise ValueError
            return record
        if set(record) != {"session", "priority", "xpra", "category", "pid", "message"}:
            raise ValueError
        if (
            record["session"] != session
            or type(record["priority"]) is not int
            or not 0 <= record["priority"] <= 7
            or type(record["xpra"]) is not bool
            or type(record["pid"]) is not int
            or not 0 < record["pid"] < 2**32
            or not isinstance(record["category"], str)
            or re.fullmatch(r"[a-zA-Z0-9_.-]{0,128}", record["category"]) is None
            or not isinstance(record["message"], str)
        ):
            raise ValueError
        message = base64.b64decode(record["message"], validate=True)
        if len(message) > MAX_MESSAGE:
            raise ValueError
        return record | {"message": message}
    except (ValueError, TypeError, UnicodeError) as error:
        raise LogTransportError("invalid remote log frame") from error


class Publisher:
    """Nonblocking IPC only; a missing or slow SSH observer cannot block Xpra."""

    def __init__(self) -> None:
        self.socket: socket.socket | None = None
        self.dropped: dict[str, int] = {}

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def emit(
        self,
        session: str,
        priority: int,
        message: bytes,
        *,
        xpra: bool,
        category: str,
        pid: int,
    ) -> None:
        if not session:
            return
        try:
            directory = _directory(session)
            with os.scandir(directory) as entries:
                paths = [entry for entry in islice(entries, MAX_SUBSCRIBERS)]
            names = {entry.name for entry in paths}
            self.dropped = {
                name: count for name, count in self.dropped.items() if name in names
            }
            if not paths:
                return
            if self.socket is None:
                self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self.socket.setblocking(False)
            frame = encode(
                {
                    "session": session,
                    "priority": priority,
                    "xpra": xpra,
                    "category": category,
                    "pid": pid,
                    "message": base64.b64encode(message).decode(),
                }
            )
            for entry in paths:
                if SOCKET_NAME.fullmatch(entry.name) is None:
                    continue
                try:
                    details = entry.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISSOCK(details.st_mode)
                        or details.st_uid != os.getuid()
                    ):
                        continue
                    if missing := self.dropped.get(entry.name, 0):
                        self.socket.sendto(encode({"gap": missing}), entry.path)
                        self.dropped.pop(entry.name, None)
                    self.socket.sendto(frame, entry.path)
                except BlockingIOError:
                    self.dropped[entry.name] = min(
                        self.dropped.get(entry.name, 0) + 1, 2**32
                    )
                except OSError:
                    self.dropped.pop(entry.name, None)
        except (OSError, LogTransportError):
            # The native journal is the durable destination; this observer is
            # optional once a session is running, and never owns its lifetime.
            pass


def relay_main(session: str) -> int:
    """Forward private datagrams over this SSH channel until stdin closes."""
    endpoint: Path | None = None
    directory: Path | None = None
    stopping: float | None = None
    dropped = 0
    pending = bytearray()

    def stop(*_unused: Any) -> None:
        nonlocal stopping
        stopping = stopping or time.monotonic()

    for selected in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(selected, stop)
    try:
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver,
            selectors.DefaultSelector() as selector,
        ):
            for attempt in range(2):
                directory = _directory(session, create=True)
                if (
                    len(tuple(islice(directory.iterdir(), MAX_SUBSCRIBERS)))
                    >= MAX_SUBSCRIBERS
                ):
                    raise LogTransportError("too many remote log subscribers")
                path = directory / f"{secrets.token_hex(8)}.sock"
                try:
                    receiver.bind(str(path))
                    endpoint = path
                    break
                except FileNotFoundError:
                    if attempt:
                        raise
            assert endpoint is not None
            endpoint.chmod(0o600)
            receiver.setblocking(False)
            os.set_blocking(0, False)
            os.set_blocking(1, False)
            selector.register(receiver, selectors.EVENT_READ, "record")
            selector.register(0, selectors.EVENT_READ, "owner")
            pending.extend(encode({"ready": session}))
            while stopping is None or time.monotonic() - stopping < 0.2 or pending:
                if stopping is not None and time.monotonic() - stopping > 2:
                    break
                if pending:
                    with suppress(BlockingIOError):
                        del pending[: os.write(1, pending)]
                if dropped and len(pending) < MAX_QUEUE - MAX_FRAME:
                    pending.extend(encode({"gap": dropped}))
                    dropped = 0
                for event, _mask in selector.select(timeout=0.02):
                    if event.data == "owner":
                        if not os.read(0, 1):
                            selector.unregister(0)
                            stop()
                        else:
                            raise LogTransportError("invalid log channel input")
                    else:
                        frame = receiver.recv(MAX_FRAME + 1)
                        decode(frame, session)
                        if len(pending) + len(frame) <= MAX_QUEUE:
                            pending.extend(frame)
                        else:
                            dropped = min(dropped + 1, 2**32)
        return 0
    except (OSError, ValueError, LogTransportError):
        return 1
    finally:
        if endpoint is not None:
            with suppress(FileNotFoundError):
                endpoint.unlink()
        if directory is not None:
            # Never traverse or remove another observer's socket or directory.
            with suppress(OSError):
                directory.rmdir()


class RemoteLogChannel:
    """The local reader owns this mux channel, not the remote application."""

    def __init__(self, master: OpenSSHMaster, journal: Journal, session: str) -> None:
        self.master, self.journal, self.session = master, journal, session
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.closing = False

    async def start(self) -> None:
        from .journal import REMOTE_LOADER, remote_source

        await self.master.ensure_ready()
        command = shlex.join(
            (
                "python3",
                "-c",
                REMOTE_LOADER,
                remote_source(
                    "from elsewindow.log_transport import relay_main\nraise SystemExit(relay_main(sys.argv[2]))"
                ),
                self.session,
            )
        )
        self.process = await asyncio.create_subprocess_exec(
            *self.master.command_argv(command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=MAX_FRAME,
        )
        assert self.process.stdout is not None
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), 15)
            if json.loads(line) != {"ready": self.session}:
                raise ValueError
        except (TimeoutError, ValueError) as error:
            await self.close()
            raise LogTransportError(
                "cannot subscribe to remote session logs"
            ) from error
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                record = decode(line, self.session)
                if "gap" in record:
                    self.journal.emit(
                        4,
                        f"remote log transport dropped {record['gap']} records; server journal is unchanged",
                    )
                else:
                    self.journal.emit(
                        record["priority"],
                        record["message"],
                        xpra=record["xpra"],
                        category=record["category"],
                        pid=record["pid"],
                        origin="server",
                        session=self.session,
                    )
        except (OSError, ValueError, LogTransportError):
            # Close only this observer and keep draining its bounded pipe. A
            # rejected frame must not leave cleanup waiting on an unread pipe.
            with suppress(ProcessLookupError):
                self.process.terminate()
            await self._discard()
        finally:
            if not self.closing:
                self.journal.emit(
                    4, "remote log stream ended; no reconnection will be attempted"
                )

    async def _discard(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        with suppress(OSError):
            while await self.process.stdout.read(4096):
                pass

    async def close(self) -> None:
        self.closing = True
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()

        async def finish_output() -> None:
            if self.reader is not None:
                await asyncio.gather(self.reader, return_exceptions=True)
            await self._discard()

        drain = asyncio.create_task(finish_output())
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        await drain
