# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Contracts for the Xpra CLI and network configuration mirrored from the fork."""

from __future__ import annotations

from pathlib import Path

import pytest

from elsewindow import live_config

NETWORK_TABLE_START = "<!-- BEGIN GENERATED XPRA NETWORK PROFILES -->"
NETWORK_TABLE_END = "<!-- END GENERATED XPRA NETWORK PROFILES -->"
ENCODING_TABLE_START = "<!-- BEGIN GENERATED XPRA ENCODING PROFILES -->"
ENCODING_TABLE_END = "<!-- END GENERATED XPRA ENCODING PROFILES -->"


def _network_table() -> str:
    default, profiles = live_config.load_network_profiles()
    lines = [
        NETWORK_TABLE_START,
        "| Profile | Minimum quality | Minimum speed | Auto refresh | Refresh rate | Bandwidth limit |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, profile in profiles.items():
        label = f"`{name}`" + (" (default)" if name == default else "")
        bandwidth = profile.bandwidth_limit
        if bandwidth == "0":
            bandwidth_label = "unlimited (`0`)"
        else:
            bandwidth_label = (
                bandwidth.replace("Mbps", " Mbps")
                .replace("Gbps", " Gbps")
                .replace("Kbps", " Kbps")
            )
        lines.append(
            f"| {label} | {profile.min_quality} | {profile.min_speed} | "
            f"{profile.auto_refresh_delay_seconds:.2f} s | "
            f"{profile.refresh_rate_hz} Hz | {bandwidth_label} |"
        )
    lines.append(NETWORK_TABLE_END)
    return "\n".join(lines)


def _encoding_table() -> str:
    lines = [
        ENCODING_TABLE_START,
        "| CLI value | Canonical transport and policy |",
        "|---|---|",
    ]
    lines.extend(
        f"| `{public}` | `{transport}.{policy}` |"
        for public, (transport, policy) in live_config.ENCODING_PROFILE_POLICIES.items()
    )
    lines.append(ENCODING_TABLE_END)
    return "\n".join(lines)


def test_mirrors_drive_every_production_block_without_option_copies() -> None:
    default, profiles = live_config.load_network_profiles()
    assert default in profiles
    assert tuple(profiles) == live_config.network_profile_names()
    assert all(profile.client_options() for profile in profiles.values())

    configured = live_config.load_live_cli()
    for role, blocks in configured.items():
        for block, options in blocks.items():
            if block in {"commands", "transports", "clipboard"}:
                continue
            assert live_config.static_cli_options(role, block) == options
        for command, options in blocks["commands"].items():
            assert live_config.command_cli_options(role, command) == options
        for encoding, transport in blocks["transports"].items():
            for policy, policy_options in transport["policies"].items():
                assert live_config.transport_options(role, encoding, policy) == (
                    *transport["common"],
                    *policy_options,
                )

    assert live_config.encoding_profile_names() == tuple(
        live_config.ENCODING_PROFILE_POLICIES
    )
    for public_name, (
        encoding,
        policy,
    ) in live_config.ENCODING_PROFILE_POLICIES.items():
        for role in ("server", "client"):
            transport = configured[role]["transports"][encoding]
            assert live_config.production_transport_options(role, public_name) == (
                *transport["common"],
                *transport["policies"][policy],
            )


def test_mirrored_configuration_parser_fails_closed_generically(tmp_path: Path) -> None:
    for source in (live_config.NETWORK_PROFILES_PATH, live_config.LIVE_CLI_PATH):
        candidate = tmp_path / source.name
        lines = source.read_text(encoding="utf-8").splitlines()
        first_content = next(
            index
            for index, line in enumerate(lines)
            if line and not line.startswith("#")
        )
        lines[first_content] += " "
        candidate.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(live_config.LiveConfigError, match="unsafe whitespace"):
            live_config.load_strict_yaml(candidate)


def test_documented_network_table_is_rendered_from_the_mirror() -> None:
    documentation = (Path(__file__).resolve().parents[1] / "doc/cli.md").read_text(
        encoding="utf-8"
    )
    start = documentation.index(NETWORK_TABLE_START)
    end = documentation.index(NETWORK_TABLE_END, start) + len(NETWORK_TABLE_END)

    assert documentation[start:end] == _network_table()

    encoding_start = documentation.index(ENCODING_TABLE_START)
    encoding_end = documentation.index(ENCODING_TABLE_END, encoding_start) + len(
        ENCODING_TABLE_END
    )
    assert documentation[encoding_start:encoding_end] == _encoding_table()


def test_unknown_profile_and_policy_are_rejected() -> None:
    configured = live_config.load_live_cli()
    transport = next(iter(configured["client"]["transports"]))
    with pytest.raises(live_config.LiveConfigError, match="network profile"):
        live_config.network_profile("unreviewed")
    with pytest.raises(live_config.LiveConfigError, match="encoding profile"):
        live_config.production_transport_options("client", "unreviewed")
    with pytest.raises(live_config.LiveConfigError, match="transport"):
        live_config.transport_options("client", transport, "unreviewed")
