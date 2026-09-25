# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Public CLI tests for the Xpra application runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from elsewindow import cli
from elsewindow.config import DEFAULT_NETWORK_PROFILE, SUPPORTED_NETWORK_PROFILES
from tools.runtime_dependency import runtime_version


def _path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("elsewindow.config.prepared_launcher", lambda path: path)
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
    assert "--env NAME[=VALUE]" in output
    assert "--log-level" in output
    assert "debug-clipboard" in output
    assert "--diagnose" in output
    assert DEFAULT_NETWORK_PROFILE in output
    assert "application argv after --" in output


@pytest.mark.parametrize("option", ("--prepare-xpra", "--diagnose"))
def test_setup_rejects_application_environment(option: str) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([option, "--env", "LANG=C"])
    assert raised.value.code == 2


@pytest.mark.parametrize("error_type", (cli.JournalError, OSError))
def test_optional_diagnostics_warn_without_failing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[Exception],
) -> None:
    import subprocess

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "prepared_launcher", lambda path: path)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, b'{"notifications": false, "render": false}', b""
        ),
    )

    def unavailable(_self) -> None:
        raise error_type("missing")

    monkeypatch.setattr(cli.Journal, "open", unavailable)
    assert cli.main(["--diagnose"]) == 0
    output = capsys.readouterr()
    assert "optional-local-notifications: unavailable" in output.out
    assert "python3-dbus" in output.err and "dunst" in output.err
    assert "journald is unavailable" in output.err
    assert "optional-remote-features: checked after SSH" in output.out


def test_diagnose_reports_versions_resources_and_missing_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert cli.main(["--diagnose"]) == 1

    captured = capsys.readouterr()
    assert f"elsewindow: {cli.__version__}" in captured.out
    assert f"ssh-wrapper: {runtime_version()}" in captured.out.splitlines()
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
        ("--log-level", "unreviewed"),
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
    output = capsys.readouterr().err
    prefix, message = output.split("] ", 1)
    assert prefix.startswith("elsewindow-local: [session=")
    assert len(prefix.rsplit("=", 1)[1]) == 32
    assert message == "connection_lost: SSH master was lost\n"
