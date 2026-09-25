# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Real Unix datagram coverage for the shared local and remote journal adapter."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import os
import shlex
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from elsewindow import _persistent_agent as agent
from elsewindow import journal
from elsewindow import log_transport as transport
from tests.live_support import journal as live_journal
from tests.live_support.journal import select_records
from tests.live_support.process import LiveFailure


@pytest.fixture
def receiver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[socket.socket]:
    path = str(tmp_path / "journal.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.bind(path)
        channel.settimeout(0.1)
        monkeypatch.setattr(journal, "JOURNAL_SOCKET", path)
        yield channel


def message(receiver: socket.socket) -> tuple[bytes, bytes]:
    fields, data = receiver.recv(65536).split(b"MESSAGE\n", 1)
    length = struct.unpack("<Q", data[:8])[0]
    assert len(data) == length + 9
    session = dict(line.split(b"=", 1) for line in fields.splitlines())[
        b"ELSEWINDOW_SESSION"
    ]
    assert transport.SESSION.fullmatch(session.decode()) is not None
    marker = b"[session=" + session + b"] "
    assert data[8:-1].startswith(marker)
    return fields, data[8 + len(marker) : -1]


class RelayMaster:
    """Exercise the same isolated remote payload without an SSH test topology."""

    def __init__(self, root: Path) -> None:
        self.ready_checks = 0
        self.channels = 0
        main = (
            "from pathlib import Path\nfrom elsewindow import log_transport as t\n"
            f"t.ROOT=Path({str(root)!r})\n"
            "raise SystemExit(t.relay_main(sys.argv[2]))"
        )
        self.source = journal.remote_source(main)

    async def ensure_ready(self) -> None:
        self.ready_checks += 1

    def command_argv(self, program: str) -> list[str]:
        self.channels += 1
        arguments = shlex.split(program)
        assert arguments[:2] == ["python3", "-c"]
        return [
            sys.executable,
            "-I",
            "-c",
            journal.REMOTE_LOADER,
            self.source,
            arguments[-1],
        ]


@pytest.mark.parametrize("negotiated", (None, "a" * 64))
def test_startup_records_share_one_visible_identity_even_on_early_failure(
    receiver: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    negotiated: str | None,
) -> None:
    monkeypatch.setattr(journal, "MAX_QUEUE", 600)
    sink = journal.Journal("info", "client", deferred=True)
    original = sink.session
    sink.emit(7, "filtered")
    sink.emit(6, "early Xpra probe", xpra=True, pid=123)
    sink.emit(4, "first\nsecond\x1b")
    sink.emit(4, "excess")
    assert sink.pending_bytes <= journal.MAX_QUEUE and sink.dropped == 1
    assert capsys.readouterr().out == ""
    with pytest.raises(TimeoutError):
        receiver.recv(1)
    sink.flush(negotiated)
    expected = negotiated or original
    for body in (
        b"early Xpra probe",
        b"first\nsecond\x1b",
        b"startup log buffer dropped 1 records",
    ):
        fields, data = message(receiver)
        assert f"ELSEWINDOW_SESSION={expected}\n".encode() in fields
        assert data == body
    output = capsys.readouterr()
    for line in (output.out + output.err).splitlines():
        assert f": [session={expected}] " in line
        assert "\x1b" not in line
    assert not sink.pending
    sink.close()


def test_warning_default_filters_levels_and_preserves_tracebacks(
    receiver: socket.socket,
) -> None:
    sink = journal.Journal(journal.DEFAULT_LOG_LEVEL, "client", "a" * 32)
    stream = journal.XpraLogStream(sink, stderr=True)
    stream.feed(b"ELSEWINDOW_LOG|INFO|xpra.client|hidden\n")
    stream.feed(
        b"ELSEWINDOW_LOG|ERROR|xpra.opengl|failure\nTraceback:\nValueError: test"
    )
    stream.finish()
    for expected in (b"failure", b"Traceback:", b"ValueError: test"):
        fields, text = message(receiver)
        assert b"PRIORITY=3\n" in fields
        assert b"SYSLOG_IDENTIFIER=elsewindow-xpra-local\n" in fields
        assert text == expected
    with pytest.raises(TimeoutError):
        receiver.recv(1)
    sink.close()


def test_clipboard_debug_is_targeted_and_own_messages_share_threshold(
    receiver: socket.socket,
) -> None:
    sink = journal.Journal("debug-clipboard", "server")
    stream = journal.XpraLogStream(sink, stderr=True)
    stream.feed(b"ELSEWINDOW_LOG|DEBUG|xpra.encoding|hidden\n")
    stream.feed(b"ELSEWINDOW_LOG|DEBUG|xpra.clipboard|selected\n")
    sink.emit(journal.PRIORITIES["info"], "hidden lifecycle")
    sink.emit(journal.PRIORITIES["warning"], "own warning")
    assert message(receiver)[1] == b"selected"
    fields, text = message(receiver)
    assert b"SYSLOG_IDENTIFIER=elsewindow-remote\n" in fields
    assert text == b"own warning"
    with pytest.raises(TimeoutError):
        receiver.recv(1)
    sink.close()


def test_framing_bounds_and_missing_journal(
    receiver: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = journal.Journal("debug", "client")
    sink.emit(3, "test\nPRIORITY=0\n")
    fields, text = message(receiver)
    assert fields.count(b"PRIORITY=") == 1
    assert text == b"test\nPRIORITY=0\n"
    stream = journal.XpraLogStream(sink, stderr=True)
    stream.feed(b"x" * (journal.MAX_LINE + 10))
    assert len(message(receiver)[1]) == journal.MAX_LINE
    assert len(stream.pending) == 10
    sink.close()
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", "/missing/journal")
    with pytest.raises(
        journal.JournalError, match="client system journal is unavailable"
    ):
        sink.open()


@pytest.mark.parametrize("level", journal.LOG_LEVELS)
def test_public_environment_keeps_level_selection_out_of_xpra_internals(
    level: str,
) -> None:
    environment = journal.xpra_environment(level)
    assert environment["XPRA_LOG_FORMAT"] == journal.LOG_FORMAT
    assert environment["XPRA_ALL_DEBUG"] == str(int(level == "debug"))
    assert environment["XPRA_CLIPBOARD_DEBUG"] == str(int(level == "debug-clipboard"))
    assert environment["XPRA_LOG_TO_FILE"] == "0"


def test_client_terminal_uses_the_identical_filter_and_severity_stream(
    receiver: socket.socket,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink = journal.Journal("info", "client")
    sink.emit(journal.PRIORITIES["debug"], "hidden")
    sink.emit(journal.PRIORITIES["info"], "lifecycle")
    sink.emit(journal.PRIORITIES["warning"], "warning", xpra=True)
    captured = capsys.readouterr()
    assert captured.out == f"elsewindow-local: [session={sink.session}] lifecycle\n"
    assert captured.err == f"elsewindow-xpra-local: [session={sink.session}] warning\n"
    assert message(receiver)[1] == b"lifecycle"
    assert message(receiver)[1] == b"warning"
    sink.close()


@pytest.mark.parametrize("with_bus", (False, True))
def test_remote_payload_preserves_display_exit_and_separate_journal_records(
    receiver: socket.socket, tmp_path: Path, with_bus: bool
) -> None:
    bus_identity = tmp_path / "bus-pid"
    program = (
        "import logging,os,sys;from pathlib import Path;"
        f"bus=int(os.environ.get('DBUS_SESSION_BUS_PID','0'));assert bool(bus)=={with_bus!r};"
        f"Path({str(bus_identity)!r}).write_text(str(bus));"
        "logging.basicConfig(format=os.environ['XPRA_LOG_FORMAT'],level=logging.DEBUG);"
        "logging.info('filtered');logging.error('remote marker');"
        "print('wayland-8',flush=True);sys.exit(23)"
    )
    main = (
        "from elsewindow import journal as j\n"
        f"j.JOURNAL_SOCKET={receiver.getsockname()!r}\n"
        f"raise SystemExit(j.remote_main(sys.argv[2:], with_session_bus={with_bus!r}))"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            journal.REMOTE_LOADER,
            journal.remote_source(main),
            "warning",
            "a" * 32,
            base64.b64encode(
                json.dumps([sys.executable, "-c", program]).encode()
            ).decode(),
        ],
        capture_output=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("DBUS_")
        },
        timeout=10,
        check=False,
    )
    assert result.returncode == 23
    assert result.stdout == b"wayland-8\n"
    if with_bus:
        assert not Path(f"/proc/{bus_identity.read_text()}").exists()
    fields, text = message(receiver)
    assert b"ELSEWINDOW_SIDE=server\n" in fields
    assert b"SYSLOG_IDENTIFIER=elsewindow-xpra-remote\n" in fields
    assert text == b"remote marker"
    fields, text = message(receiver)
    assert b"SYSLOG_IDENTIFIER=elsewindow-remote\n" in fields
    assert b"status 23" in text


@pytest.mark.parametrize("level", journal.LOG_LEVELS)
@pytest.mark.parametrize("side", ("client", "server"))
def test_every_level_has_one_filter_for_both_destinations(
    receiver: socket.socket,
    capsys: pytest.CaptureFixture[str],
    level: str,
    side: str,
) -> None:
    sink = journal.Journal(level, side)
    expected = {
        name: priority
        for name, priority in journal.PRIORITIES.items()
        if priority <= journal.PRIORITIES.get(level, journal.PRIORITIES["warning"])
    }
    for name, priority in journal.PRIORITIES.items():
        sink.emit(priority, name)
    for name in expected:
        assert message(receiver)[1] == name.encode()
    captured = capsys.readouterr()
    assert captured.out == (
        ""
        if side == "server"
        else "".join(
            f"elsewindow-local: [session={sink.session}] {name}\n"
            for name, priority in expected.items()
            if priority > journal.PRIORITIES["warning"]
        )
    )
    assert captured.err == (
        ""
        if side == "server"
        else "".join(
            f"elsewindow-local: [session={sink.session}] {name}\n"
            for name, priority in expected.items()
            if priority <= journal.PRIORITIES["warning"]
        )
    )
    with pytest.raises(TimeoutError):
        receiver.recv(1)
    sink.close()


def test_journal_loss_does_not_raise_or_repeat_delivery_notice(
    receiver: socket.socket,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = journal.Journal("warning", "client")
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", "/missing/journal")
    sink.emit(journal.PRIORITIES["warning"], "first")
    sink.emit(journal.PRIORITIES["warning"], "second")
    assert capsys.readouterr().err.count("journal delivery failed") == 1
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", receiver.getsockname())
    sink.emit(journal.PRIORITIES["warning"], "recovered")
    assert message(receiver)[1] == b"recovered"
    sink.close()


def test_optional_journal_start_keeps_terminal_and_remote_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", str(tmp_path / "missing"))
    local = journal.Journal("warning", "client")
    local.open_optional()
    local.emit(4, "application can start")
    output = capsys.readouterr().err
    assert output.count("journal delivery failed") == 1
    assert "systemd" in output and "application can start" in output
    remote = journal.Journal("warning", "server")
    records: list[bytes] = []
    assert remote.publisher is not None
    monkeypatch.setattr(
        remote.publisher,
        "emit",
        lambda _session, _priority, data, **_kw: records.append(data),
    )
    remote.open_optional()
    remote.emit(4, "remote application can start")
    assert b"systemd" in records[0]
    assert records[-1] == b"remote application can start"
    assert capsys.readouterr().err == ""
    local.close()
    remote.close()


def test_four_origins_are_distinct_in_terminal_and_native_journal(
    receiver: socket.socket,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink = journal.Journal("info", "client")
    for side in ("client", "server"):
        for xpra in (False, True):
            sink.emit(6, "record", origin=side, xpra=xpra, pid=123)
            fields, text = message(receiver)
            label = journal.source_label(side, xpra)
            assert f"SYSLOG_IDENTIFIER={label}\n".encode() in fields
            assert f"ELSEWINDOW_FORWARDED={int(side == 'server')}\n".encode() in fields
            assert b"ELSEWINDOW_SOURCE_PID=123\n" in fields
            assert text == b"record"
    assert capsys.readouterr().out.splitlines() == [
        f"elsewindow-local: [session={sink.session}] record",
        f"elsewindow-xpra-local: [session={sink.session}] record",
        f"elsewindow-remote: [session={sink.session}] record",
        f"elsewindow-xpra-remote: [session={sink.session}] record",
    ]
    sink.close()
    remote = journal.Journal("info", "server")
    with pytest.raises(ValueError, match="origin"):
        remote.emit(6, "not a server record", origin="client")


@pytest.mark.asyncio
async def test_real_remote_log_relay_resubscribes_without_history_or_new_authentication(
    receiver: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = "a" * 64
    receiver.settimeout(5)
    with tempfile.TemporaryDirectory(prefix="ew-log-test-") as temporary:
        root = Path(temporary)
        monkeypatch.setattr(transport, "ROOT", root)

        master = RelayMaster(root)
        sink = journal.Journal("warning", "client", "b" * 32)
        publisher = transport.Publisher()
        for iteration in range(2):
            channel = transport.RemoteLogChannel(master, sink, key)  # type: ignore[arg-type]
            try:
                await channel.start()
                for priority, data in (
                    (6, b"filtered"),
                    (4, f"connected-{iteration}".encode()),
                ):
                    publisher.emit(
                        key,
                        priority,
                        data,
                        xpra=True,
                        category="xpra.clipboard",
                        pid=123,
                    )
                fields, data = await asyncio.to_thread(message, receiver)
                assert data == f"connected-{iteration}".encode()
                assert b"SYSLOG_IDENTIFIER=elsewindow-xpra-remote\n" in fields
                assert b"ELSEWINDOW_FORWARDED=1\n" in fields
                assert f"ELSEWINDOW_SESSION={key}\n".encode() in fields
                assert b"ELSEWINDOW_SOURCE_PID=123\n" in fields
            finally:
                await channel.close()
            assert not list(root.rglob("*.sock"))
            publisher.emit(
                key, 4, b"disconnected-history", xpra=True, category="", pid=123
            )
        assert master.ready_checks == master.channels == 2
        assert capsys.readouterr().err.splitlines() == [
            f"elsewindow-xpra-remote: [session={key}] connected-0",
            f"elsewindow-xpra-remote: [session={key}] connected-1",
        ]
        publisher.close()
        sink.close()


@pytest.mark.asyncio
async def test_concurrent_log_subscribers_share_only_their_session(
    receiver: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    receiver.settimeout(5)
    with tempfile.TemporaryDirectory(prefix="ew-log-test-") as temporary:
        root = Path(temporary)
        monkeypatch.setattr(transport, "ROOT", root)
        master = RelayMaster(root)
        keys = ["a" * 64, "a" * 64, "b" * 64]
        sinks = [journal.Journal("warning", "client", key) for key in keys]
        channels = [
            transport.RemoteLogChannel(master, sink, key)  # type: ignore[arg-type]
            for sink, key in zip(sinks, keys, strict=True)
        ]
        publisher = transport.Publisher()
        try:
            await asyncio.gather(*(channel.start() for channel in channels))
            for iteration in range(2):
                if iteration:
                    await channels[0].close()
                for key in set(keys):
                    publisher.emit(
                        key, 4, key.encode(), xpra=True, category="", pid=123
                    )
                expected = sorted(keys[iteration:])
                received = []
                for _index in expected:
                    fields, data = await asyncio.to_thread(message, receiver)
                    assert f"ELSEWINDOW_SESSION={data.decode()}\n".encode() in fields
                    received.append(data.decode())
                assert sorted(received) == expected
                with pytest.raises(TimeoutError):
                    await asyncio.to_thread(message, receiver)
            assert master.channels == master.ready_checks == len(keys)
        finally:
            await asyncio.gather(*(channel.close() for channel in channels))
            publisher.close()
            for sink in sinks:
                sink.close()
        assert not list(root.rglob("*.sock"))


def test_remote_publication_is_nonblocking_bounded_and_reports_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "a" * 32
    with tempfile.TemporaryDirectory(prefix="ew-log-test-") as temporary:
        monkeypatch.setattr(transport, "ROOT", Path(temporary))
        directory = transport._directory(key, create=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(directory / ("b" * 16 + ".sock")))
            receiver.setblocking(False)
            publisher = transport.Publisher()
            publisher.emit(key, 4, b"warm-up", xpra=False, category="", pid=123)
            assert publisher.socket is not None
            publisher.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
            started = time.monotonic()
            for _index in range(100):
                publisher.emit(key, 4, b"bounded", xpra=False, category="", pid=123)
            assert time.monotonic() - started < 2
            assert publisher.dropped
            with pytest.raises(BlockingIOError):
                while True:
                    receiver.recv(transport.MAX_FRAME)
            publisher.emit(key, 4, b"recovered", xpra=False, category="", pid=123)
            assert transport.decode(receiver.recv(transport.MAX_FRAME), key)["gap"] > 0
            assert (
                transport.decode(receiver.recv(transport.MAX_FRAME), key)["message"]
                == b"recovered"
            )
            publisher.close()


@pytest.mark.parametrize(
    "change",
    (
        {"session": "b" * 32},
        {"priority": True},
        {"pid": -1},
        {"origin": "client"},
        {"message": "invalid base64!"},
        {"category": "injected\nFIELD=1"},
    ),
)
def test_remote_frames_cannot_cross_session_or_metadata_boundaries(
    change: dict[str, object],
) -> None:
    record = {
        "session": "a" * 32,
        "priority": 4,
        "xpra": True,
        "category": "",
        "pid": 123,
        "message": base64.b64encode(b"safe").decode(),
    }
    with pytest.raises(transport.LogTransportError):
        transport.decode(transport.encode(record | change), "a" * 32)
    with pytest.raises(transport.LogTransportError, match="limit"):
        transport.decode(b"x" * (transport.MAX_FRAME + 1), "a" * 32)


def test_remote_journal_never_uses_a_terminal_even_when_delivery_fails(
    receiver: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink = journal.Journal("debug", "server")
    sink.emit(3, "native only")
    assert message(receiver)[1] == b"native only"
    sink.close()
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", "/missing/journal")
    sink.emit(3, "failed delivery")
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_persistent_service_errors_keep_the_shared_id_without_raw_console_output(
    receiver: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = "a" * 64

    def failed(_request: object) -> None:
        raise agent.AgentError("persistent_unsafe_state", "unsafe service state")

    monkeypatch.setattr(agent, "serve", failed)
    assert agent.main(["source", "serve", agent.encode({"log_session": key})]) == 1
    fields, data = message(receiver)
    assert f"ELSEWINDOW_SESSION={key}\n".encode() in fields
    assert b"SYSLOG_IDENTIFIER=elsewindow-remote\n" in fields
    assert data == b"persistent_unsafe_state: unsafe service state"
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.asyncio
async def test_invalid_remote_greeting_with_full_output_pipe_has_bounded_cleanup() -> (
    None
):
    class Master:
        calls = 0

        async def ensure_ready(self) -> None:
            pass

        def command_argv(self, _program: str) -> list[str]:
            self.calls += 1
            return [
                sys.executable,
                "-u",
                "-c",
                (
                    "import os\nprint('invalid-greeting',flush=True)\n"
                    "while True: os.write(1,b'x'*65536)\n"
                ),
            ]

    master = Master()
    sink = journal.Journal("warning", "client")
    channel = transport.RemoteLogChannel(master, sink, "a" * 32)  # type: ignore[arg-type]
    try:
        with pytest.raises(transport.LogTransportError, match="subscribe"):
            await asyncio.wait_for(channel.start(), 8)
        assert master.calls == 1
        assert channel.process is not None and channel.process.returncode is not None
    finally:
        await channel.close()
        sink.close()


def test_journal_and_stderr_loss_together_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedOutput:
        def write(self, _text: str) -> None:
            raise BrokenPipeError

    monkeypatch.setattr(journal, "JOURNAL_SOCKET", "/missing/journal")
    monkeypatch.setattr(sys, "stderr", ClosedOutput())
    sink = journal.Journal("warning", "server")
    sink.emit(journal.PRIORITIES["warning"], "unavailable destinations")
    assert sink.failed


def test_live_journal_verifier_rejects_wrong_side_and_unfiltered_records() -> None:
    record = {
        "ELSEWINDOW_SESSION": "a" * 32,
        "MESSAGE": f"[session={'a' * 32}] record",
        "ELSEWINDOW_LOG_LEVEL": "warning",
        "ELSEWINDOW_SIDE": "client",
        "ELSEWINDOW_COMPONENT": "elsewindow",
        "ELSEWINDOW_FORWARDED": "0",
        "SYSLOG_IDENTIFIER": "elsewindow-local",
        "PRIORITY": str(journal.PRIORITIES["warning"]),
    }
    evidence = {"sessions": [record["ELSEWINDOW_SESSION"]], "log_level": "warning"}
    assert select_records([record], "client", evidence) == [record]
    with pytest.raises(LiveFailure, match="provisional ID"):
        select_records(
            [record],
            "client",
            evidence | {"provisional_sessions": [record["ELSEWINDOW_SESSION"]]},
        )
    with pytest.raises(LiveFailure, match="shared session identity"):
        select_records([record | {"MESSAGE": "unlabeled"}], "client", evidence)
    with pytest.raises(LiveFailure, match="side or severity"):
        select_records([record], "server", evidence)
    record["PRIORITY"] = str(journal.PRIORITIES["debug"])
    with pytest.raises(LiveFailure, match="side or severity"):
        select_records([record], "client", evidence)


def test_live_journal_read_rejects_a_truncated_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_journal, "MAX_JOURNAL_RECORDS", 2)
    monkeypatch.setattr(
        live_journal,
        "checked",
        lambda command, _purpose: subprocess.CompletedProcess(command, 0, "{}\n{}\n"),
    )
    with pytest.raises(LiveFailure, match="record limit reached"):
        live_journal.journal_records(
            SimpleNamespace(podman="podman"),
            "fixture",  # type: ignore[arg-type]
            {"sessions": ["a" * 32], "log_level": "info"},
        )


@pytest.mark.parametrize("close_output", (False, True))
def test_remote_group_stop_is_not_duplicated_and_broken_output_keeps_draining(
    receiver: socket.socket,
    close_output: bool,
) -> None:
    program = """
import logging, os, signal, time
logging.basicConfig(format=os.environ['XPRA_LOG_FORMAT'], level=logging.INFO)
stopped = None
def stop(*unused):
    global stopped
    if stopped is not None:
        raise SystemExit(42)
    stopped = time.monotonic()
signal.signal(signal.SIGTERM, stop)
print('ready', flush=True)
while stopped is None or time.monotonic() - stopped < 0.2:
    time.sleep(0.02)
    print('output', flush=True)
logging.warning('graceful cleanup completed')
"""
    main = (
        "from elsewindow import journal as j\n"
        f"j.JOURNAL_SOCKET={receiver.getsockname()!r}\n"
        "raise SystemExit(j.remote_main(sys.argv[2:]))"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            journal.REMOTE_LOADER,
            journal.remote_source(main),
            "warning",
            "a" * 32,
            base64.b64encode(
                json.dumps([sys.executable, "-c", program]).encode()
            ).decode(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == b"ready\n"
        if close_output:
            child.stdout.close()
            time.sleep(0.1)
            assert child.poll() is None
        os.killpg(child.pid, signal.SIGTERM)
        assert child.wait(timeout=5) == 0
        assert message(receiver)[1] == b"graceful cleanup completed"
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()


def test_remote_group_stop_drains_when_the_ssh_output_pipe_is_full(
    receiver: socket.socket, tmp_path: Path
) -> None:
    """A connected but unread SSH pipe must not hold the lifetime owner alive."""
    ready = tmp_path / "ready"
    cleaned = tmp_path / "cleaned"
    program = r"""
import logging, os, signal, sys
from pathlib import Path
logging.basicConfig(format=os.environ['XPRA_LOG_FORMAT'], level=logging.INFO)
def stop(*unused):
    os.write(1, b'cleanup output\n' * 512)
    Path(sys.argv[2]).touch()
    logging.warning('graceful cleanup completed under backpressure')
    os._exit(0)
signal.signal(signal.SIGTERM, stop)
Path(sys.argv[1]).touch()
while True:
    os.write(1, b'output\n' * 512)
"""
    main = (
        "from elsewindow import journal as j\n"
        f"j.JOURNAL_SOCKET={receiver.getsockname()!r}\n"
        "raise SystemExit(j.remote_main(sys.argv[2:]))"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            journal.REMOTE_LOADER,
            journal.remote_source(main),
            "warning",
            "a" * 32,
            base64.b64encode(
                json.dumps(
                    [sys.executable, "-c", program, str(ready), str(cleaned)]
                ).encode()
            ).decode(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert child.stdout is not None
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        # Leave both consumer ends open, unlike the separate broken-pipe test.
        capacity = fcntl.fcntl(child.stdout, fcntl.F_GETPIPE_SZ)
        atomic_write = os.fpathconf(child.stdout.fileno(), "PC_PIPE_BUF")

        def pipe_bytes() -> int:
            return struct.unpack(
                "I", fcntl.ioctl(child.stdout, termios.FIONREAD, struct.pack("I", 0))
            )[0]

        # A pipe can reject another atomic write while a partial page is free.
        # Requiring its nominal byte capacity would itself introduce a race.
        while pipe_bytes() <= capacity - atomic_write and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pipe_bytes() > capacity - atomic_write
        os.killpg(child.pid, signal.SIGTERM)
        assert child.wait(timeout=3) == 0
        assert cleaned.exists()
        records = [message(receiver)[1]]
        while records[-1] != b"graceful cleanup completed under backpressure":
            records.append(message(receiver)[1])
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        child.stdout.close()
        assert child.stderr is not None
        child.stderr.close()


def test_forwarded_output_is_bounded_preserves_order_and_restores_flags(
    receiver: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    flags = {1: True, 2: False}
    monkeypatch.setattr(journal.os, "get_blocking", flags.__getitem__)
    monkeypatch.setattr(journal.os, "set_blocking", flags.__setitem__)
    monkeypatch.setattr(journal, "MAX_QUEUE", 32)
    blocked = True
    delivered = {1: bytearray(), 2: bytearray()}

    def write(destination: int, data: bytes) -> int:
        if blocked:
            raise BlockingIOError
        delivered[destination].extend(data[:5])
        return min(5, len(data))

    monkeypatch.setattr(journal.os, "write", write)
    sink = journal.Journal("warning", "server")
    output = journal.ForwardedOutput(sink)
    assert flags == {1: False, 2: False}
    output.feed(1, b"0123456789" * 10)
    output.feed(2, b"stderr")
    assert len(output.pending[1]) == journal.MAX_QUEUE
    assert output.dropped == 68
    blocked = False
    while any(output.pending.values()):
        output.flush()
    output.close()
    assert flags == {1: True, 2: False}
    assert delivered == {1: (b"0123456789" * 10)[:32], 2: b"stderr"}
    assert b"68 bytes" in message(receiver)[1]
    sink.close()


def test_remote_short_command_finishes_forwarding_to_a_delayed_reader(
    receiver: socket.socket, tmp_path: Path
) -> None:
    ready = tmp_path / "ready"
    program = (
        "import os,sys\nfrom pathlib import Path\n"
        f"os.write(1, b'x' * {journal.MAX_QUEUE})\nPath(sys.argv[1]).touch()\n"
    )
    main = (
        "from elsewindow import journal as j\n"
        f"j.JOURNAL_SOCKET={receiver.getsockname()!r}\n"
        "raise SystemExit(j.remote_main(sys.argv[2:]))"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            journal.REMOTE_LOADER,
            journal.remote_source(main),
            "warning",
            "a" * 32,
            base64.b64encode(
                json.dumps([sys.executable, "-c", program, str(ready)]).encode()
            ).decode(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        time.sleep(0.1)
        stdout, stderr = child.communicate(timeout=3)
        assert child.returncode == 0
        assert stdout == b"x" * journal.MAX_QUEUE
        assert not stderr
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        assert child.stdout is not None and child.stderr is not None
        child.stdout.close()
        child.stderr.close()


def test_forwarded_output_disables_a_failed_destination_without_stopping_the_other(
    receiver: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal.os, "get_blocking", lambda _fd: True)
    monkeypatch.setattr(journal.os, "set_blocking", lambda _fd, _flag: None)
    delivered = bytearray()

    def write(destination: int, data: bytes) -> int:
        if destination == 1:
            raise BrokenPipeError
        delivered.extend(data)
        return len(data)

    monkeypatch.setattr(journal.os, "write", write)
    sink = journal.Journal("warning", "server")
    output = journal.ForwardedOutput(sink)
    output.feed(1, b"lost")
    output.feed(1, b"more lost")
    output.feed(2, b"available")
    output.close()
    assert delivered == b"available"
    assert b"13 bytes" in message(receiver)[1]
    sink.close()
