# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Machine-scoped environment selection without prepared Python dependencies."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from elsewindow import machine


def test_environment_namespace_is_stable_private_and_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "machine-id"
    monkeypatch.setattr(machine, "MACHINE_ID_FILE", identity)
    monkeypatch.setattr(machine.os, "getuid", lambda: 1000)
    raw = "0123456789abcdef" * 2
    identity.write_text(raw + "\n", encoding="ascii")
    first = machine.environment_key()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert raw not in first
    assert machine.repository_venv_root() == Path(".venvs") / first
    identity.write_text(raw, encoding="ascii")
    assert machine.environment_key() == first
    identity.write_text("fedcba9876543210" * 2, encoding="ascii")
    assert machine.environment_key() != first
    identity.write_text(raw, encoding="ascii")
    monkeypatch.setattr(machine.os, "getuid", lambda: 1001)
    assert machine.environment_key() != first


@pytest.mark.parametrize(
    "value",
    (
        b"",
        b"uninitialized\n",
        b"0" * 32,
        b"0" * 32 + b"\n",
        b"A" * 32,
        b"g" * 32,
        b"1" * 31,
        b"1" * 33,
        b"1" * 32 + b"\n\n",
        b"1" * 32 + b"\x00",
        b"1" * 1000,
    ),
)
def test_invalid_identity_has_no_common_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: bytes
) -> None:
    identity = tmp_path / "machine-id"
    identity.write_bytes(value)
    monkeypatch.setattr(machine, "MACHINE_ID_FILE", identity)
    with pytest.raises(machine.MachineIdentityError, match="invalid or uninitialized"):
        machine.repository_venv_root()


def test_machine_selector_reports_failure_without_identity_or_path_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = tmp_path / "missing"
    monkeypatch.setattr(machine, "MACHINE_ID_FILE", identity)
    assert machine.main() == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "cannot read /etc/machine-id" in captured.err
    assert str(tmp_path) not in captured.err
    identity.write_text("0123456789abcdef" * 2, encoding="ascii")
    assert machine.main() == 0
    captured = capsys.readouterr()
    assert captured.out == f"{machine.repository_venv_root()}\n"
    assert not captured.err
