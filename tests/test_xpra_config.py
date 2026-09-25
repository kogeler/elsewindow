# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Configuration tests for the Xpra command."""

from __future__ import annotations

from pathlib import Path

import pytest
from ssh_wrapper.errors import SSHError

from elsewindow.cli import build_parser
from elsewindow.config import (
    DEFAULT_CLIPBOARD_POLICY,
    DEFAULT_ENCODING_PROFILE,
    DEFAULT_NETWORK_PROFILE,
    SUPPORTED_ENCODING_PROFILES,
    SUPPORTED_NETWORK_PROFILES,
    XpraConfig,
)


def _executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def executable_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("elsewindow.config.prepared_launcher", lambda path: path)
    for name in ("ssh", "false", "python3", "xpra"):
        _executable(tmp_path / name)
    monkeypatch.setenv("PATH", str(tmp_path))
    return tmp_path


def test_source_defaults_and_application_argv(executable_path: Path) -> None:
    args = build_parser().parse_args(
        ["--ssh-alias", "workstation", "--", "spotify", "value with spaces"]
    )

    config = XpraConfig.from_namespace(args)

    assert config.ready_timeout == 45
    assert config.probe_timeout == 8
    assert config.poll_interval == 1
    assert config.heartbeat_interval == 10
    assert config.lease_timeout == 45
    assert config.encoding_profile == DEFAULT_ENCODING_PROFILE
    assert config.network_profile == DEFAULT_NETWORK_PROFILE
    assert config.clipboard == DEFAULT_CLIPBOARD_POLICY
    assert config.log_level == "warning"
    assert config.persistent is False
    assert config.application_environment == ()
    assert config.application == ("spotify", "value with spaces")
    assert config.authority_uri == "ssh://workstation"


def test_application_environment_supports_explicit_inherited_empty_and_last_values(
    executable_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INHERITED", "local value")
    args = build_parser().parse_args(
        [
            "--ssh-alias",
            "workstation",
            "--env",
            "MODE=first",
            "--env",
            "INHERITED",
            "--env",
            "EMPTY=",
            "--env",
            "MODE=last=value",
            "--",
            "xterm",
            "--env",
            "APP_ARGUMENT=literal",
        ]
    )
    config = XpraConfig.from_namespace(args)
    assert config.application_environment == (
        ("EMPTY", ""),
        ("INHERITED", "local value"),
        ("MODE", "last=value"),
    )
    assert config.application == ("xterm", "--env", "APP_ARGUMENT=literal")


@pytest.mark.parametrize(
    "entry", ("UNSET", "BAD-NAME=private-value", "NAME=bad\0value", "DISPLAY=:99")
)
def test_invalid_environment_fails_before_startup(
    executable_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    monkeypatch.delenv("UNSET", raising=False)
    args = build_parser().parse_args(
        ["--ssh-alias", "workstation", "--env", entry, "--", "xterm"]
    )
    with pytest.raises(SSHError) as raised:
        XpraConfig.from_namespace(args)
    assert raised.value.code == "invalid_application_environment"
    assert "private-value" not in str(raised.value)


def test_direct_ipv6_authority_has_an_unambiguous_uri(executable_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--host",
            "2001:db8::7",
            "--user",
            "deploy",
            "--port",
            "2222",
            "--",
            "xterm",
        ]
    )

    config = XpraConfig.from_namespace(args)

    assert config.authority_uri == "ssh://deploy@[2001:db8::7]:2222"
    assert config.connection.ssh_options == ("-l", "deploy", "-p", "2222")


def test_reviewed_profiles_and_clipboard_policy_are_public_inputs(
    executable_path: Path,
) -> None:
    encoding_profile = next(
        name for name in SUPPORTED_ENCODING_PROFILES if name != DEFAULT_ENCODING_PROFILE
    )
    network_profile = next(
        name for name in SUPPORTED_NETWORK_PROFILES if name != DEFAULT_NETWORK_PROFILE
    )
    args = build_parser().parse_args(
        [
            "--ssh-alias",
            "workstation",
            "--encoding-profile",
            encoding_profile,
            "--network-profile",
            network_profile,
            "--clipboard",
            "to-server",
            "--log-level=debug-clipboard",
            "--persistent",
            "--",
            "xterm",
        ]
    )

    config = XpraConfig.from_namespace(args)

    assert config.encoding_profile == encoding_profile
    assert config.network_profile == network_profile
    assert config.clipboard == "to-server"
    assert config.log_level == "debug-clipboard"
    assert config.persistent is True
    assert config.xpra_path.is_absolute()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--ssh-alias", "workstation"],
        ["--ssh-alias", "workstation", "--user", "deploy", "--", "xterm"],
        ["--host", "host.example", "--", "xterm"],
        [
            "--host",
            "host.example",
            "--user",
            "deploy",
            "--port",
            "70000",
            "--",
            "xterm",
        ],
        [
            "--host",
            "host.example",
            "--user",
            "deploy",
            "--heartbeat-interval",
            "10",
            "--lease-timeout",
            "20",
            "--",
            "xterm",
        ],
    ],
)
def test_invalid_authority_lifecycle_or_application_is_rejected(
    executable_path: Path, arguments: list[str]
) -> None:
    parsed = build_parser().parse_args(arguments)

    with pytest.raises(SSHError):
        XpraConfig.from_namespace(parsed)


@pytest.mark.parametrize(
    "option",
    (
        "--backend",
        "--display",
        "--title",
        "--cursors",
        "--mousewheel",
        "--dpi",
        "--notifications",
    ),
)
def test_display_backend_and_title_are_not_public_inputs(option: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["--ssh-alias", "workstation", option, "value", "--", "xterm"]
        )
