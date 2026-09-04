# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Public CLI tests for the Xpra application runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from elsewindow import cli
from elsewindow.config import DEFAULT_NETWORK_PROFILE, SUPPORTED_NETWORK_PROFILES


def _path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ssh", "false", "python3", "xpra"):
        executable = tmp_path / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


def test_help_is_english_and_documents_both_authority_forms(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    output = capsys.readouterr().out
    assert raised.value.code == 0
    assert "--ssh-alias" in output
    assert "--host" in output
    assert "--backend" not in output
    assert "--encoding-profile" in output
    assert "--network-profile" in output
    assert "--clipboard {off,to-server,both}" in output
    assert "--diagnose" in output
    assert DEFAULT_NETWORK_PROFILE in output
    assert "application argv after --" in output


def test_diagnose_reports_versions_resources_and_missing_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert cli.main(["--diagnose"]) == 1

    captured = capsys.readouterr()
    assert f"elsewindow: {cli.__version__}" in captured.out
    assert "ssh-wrapper: 0.1.0" in captured.out
    assert "live-cli.yml: sha256:" in captured.out
    assert "profiles.yml: sha256:" in captured.out
    assert captured.err.splitlines() == [
        "elsewindow: missing_dependency: required command not found on PATH: ssh",
        "elsewindow: missing_dependency: required command not found on PATH: false",
        "elsewindow: missing_dependency: required command not found on PATH: xpra",
    ]


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("--encoding-profile", "auto"),
        ("--network-profile", "unreviewed"),
        ("--clipboard", "unreviewed"),
    ),
)
def test_unreviewed_policy_is_rejected_before_session_start(
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
) -> None:
    called = False

    async def unexpected(_config: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "_run_with_signals", unexpected)

    with pytest.raises(SystemExit) as raised:
        cli.main(
            [
                "--ssh-alias",
                "workstation",
                option,
                value,
                "--",
                "xterm",
            ]
        )

    assert raised.value.code == 2
    assert called is False


def test_main_returns_session_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path(tmp_path, monkeypatch)

    selected: list[object] = []
    network_profile = next(
        name for name in SUPPORTED_NETWORK_PROFILES if name != DEFAULT_NETWORK_PROFILE
    )

    async def successful(config: object) -> int:
        selected.append(config)
        return 0

    monkeypatch.setattr(cli, "_run_with_signals", successful)

    assert (
        cli.main(
            [
                "--ssh-alias",
                "workstation",
                "--network-profile",
                network_profile,
                "--",
                "xterm",
            ]
        )
        == 0
    )
    assert selected[0].network_profile == network_profile  # type: ignore[attr-defined]


def test_main_sanitizes_expected_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _path(tmp_path, monkeypatch)

    async def failed(_config: object) -> int:
        from ssh_wrapper.errors import SSHError

        raise SSHError("connection_lost", "SSH master was lost")

    monkeypatch.setattr(cli, "_run_with_signals", failed)

    assert cli.main(["--ssh-alias", "workstation", "--", "xterm"]) == 1
    assert capsys.readouterr().err == (
        "elsewindow: connection_lost: SSH master was lost\n"
    )
