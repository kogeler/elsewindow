# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""A private foreground notification bus inside the existing session owner."""

from __future__ import annotations

import os
import selectors
import subprocess
import tempfile

DBUS_DAEMON = "/usr/bin/dbus-daemon"
START_TIMEOUT = 5.0


class SessionBusError(RuntimeError):
    """The remote private session bus could not be started."""


class OwnedSessionBus:
    """Keep the daemon in the owned process group/cgroup; never adopt a user bus."""

    def __init__(self) -> None:
        self.directory: tempfile.TemporaryDirectory[str] | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, environment: dict[str, str]) -> dict[str, str]:
        if self.directory is not None:
            raise SessionBusError("the private session bus was already started")
        self.directory = tempfile.TemporaryDirectory(prefix="elsewindow-bus-")
        address = f"unix:path={self.directory.name}/bus"
        selected = {
            key: value
            for key, value in environment.items()
            if not key.startswith("DBUS_")
        }
        try:
            self.process = subprocess.Popen(
                [
                    DBUS_DAEMON,
                    "--session",
                    "--nofork",
                    "--nopidfile",
                    "--print-address=1",
                    f"--address={address}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=selected,
            )
            assert self.process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(START_TIMEOUT):
                    raise SessionBusError(
                        "the private session bus did not become ready"
                    )
                published = os.read(self.process.stdout.fileno(), 512)
            self.process.stdout.close()
            if (
                self.process.poll() is not None
                or not published.endswith(b"\n")
                or published.partition(b",guid=")[0].rstrip(b"\n") != address.encode()
            ):
                raise SessionBusError(
                    "the private session bus returned an invalid address"
                )
            selected["DBUS_SESSION_BUS_ADDRESS"] = address
            selected["DBUS_SESSION_BUS_PID"] = str(self.process.pid)
            return selected
        except (OSError, SessionBusError) as error:
            self.close()
            raise SessionBusError(
                "cannot start the private notification bus; install dbus-daemon on the remote host"
            ) from error

    def close(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
