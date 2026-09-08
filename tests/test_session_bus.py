# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Real private notification buses stay inside the existing process owner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from elsewindow import session_bus


def test_private_buses_are_distinct_foreground_owned_and_selectively_reaped() -> None:
    first = session_bus.OwnedSessionBus()
    second = session_bus.OwnedSessionBus()
    inherited = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/unowned/bus",
        "DBUS_SYSTEM_BUS_ADDRESS": "unowned",
        "LANG": "C.UTF-8",
    }
    try:
        environments = [bus.start(inherited) for bus in (first, second)]
        assert (
            environments[0]["DBUS_SESSION_BUS_ADDRESS"]
            != environments[1]["DBUS_SESSION_BUS_ADDRESS"]
        )
        for bus, environment in zip((first, second), environments, strict=True):
            assert bus.process is not None and bus.directory is not None
            assert os.getpgid(bus.process.pid) == os.getpgrp()
            directory = Path(bus.directory.name)
            assert directory.stat().st_mode & 0o777 == 0o700
            assert (directory / "bus").is_socket()
            assert environment["DBUS_SESSION_BUS_PID"] == str(bus.process.pid)
            assert environment["LANG"] == inherited["LANG"]
            assert "DBUS_SYSTEM_BUS_ADDRESS" not in environment
        process = first.process
        assert first.directory is not None
        directory = Path(first.directory.name)
        first.close()
        assert process is not None and process.poll() is not None
        assert not directory.exists()
        assert second.process is not None and second.process.poll() is None
        assert inherited["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/unowned/bus"
    finally:
        first.close()
        second.close()


def test_bus_cannot_be_started_twice() -> None:
    bus = session_bus.OwnedSessionBus()
    try:
        bus.start({})
        with pytest.raises(session_bus.SessionBusError, match="already started"):
            bus.start({})
    finally:
        bus.close()


@pytest.mark.parametrize("behavior", ("missing", "exit", "invalid", "timeout"))
def test_failed_bus_start_reaps_only_its_process_and_private_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    executable = tmp_path / "daemon"
    if behavior != "missing":
        body = {
            "exit": "exit 1",
            "invalid": "printf 'wrong-address\\n'",
            "timeout": "exec sleep 30",
        }[behavior]
        executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setattr(session_bus, "DBUS_DAEMON", str(executable))
    monkeypatch.setattr(session_bus, "START_TIMEOUT", 0.05)
    bus = session_bus.OwnedSessionBus()
    with pytest.raises(
        session_bus.SessionBusError, match="cannot start the private notification bus"
    ):
        bus.start({})
    assert bus.directory is None and bus.process is None
    bus.close()
