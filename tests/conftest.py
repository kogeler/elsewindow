# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Keep unit-test logging on a private real Unix socket, never the host journal."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from elsewindow import journal


@pytest.fixture(autouse=True)
def private_journal(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[bytes]]:
    path = str(tmp_path_factory.mktemp("journal") / "journal-native.sock")
    records: list[bytes] = []
    stopping = threading.Event()
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.bind(path)
        channel.settimeout(0.05)

        def receive() -> None:
            while not stopping.is_set():
                try:
                    records.append(channel.recv(65536))
                except TimeoutError:
                    continue

        worker = threading.Thread(target=receive)
        worker.start()
        monkeypatch.setattr(journal, "JOURNAL_SOCKET", path)
        try:
            yield records
        finally:
            stopping.set()
            worker.join()
