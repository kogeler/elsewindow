# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Strictly load the Xpra live configuration mirrored from the maintained fork."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
NETWORK_PROFILES_PATH = PACKAGE_ROOT / "profiles.yml"
LIVE_CLI_PATH = PACKAGE_ROOT / "live-cli.yml"
FORK_NETWORK_PROFILES_URL = (
    "https://github.com/kogeler/xpra/blob/develop/fork-maintenance/profiles.yml"
)
FORK_LIVE_CLI_URL = (
    "https://github.com/kogeler/xpra/blob/develop/fork-maintenance/live-cli.yml"
)
CONFIG_BYTES_LIMIT = 128 * 1024
KEY_RE = re.compile(r"[a-z][a-z0-9_-]*")
PROFILE_RE = re.compile(r"[a-z][a-z0-9_]*")
PROFILE_FIELDS = frozenset(
    {
        "auto_refresh_delay_seconds",
        "bandwidth_limit",
        "min_quality",
        "min_speed",
        "refresh_rate_hz",
    }
)
ROLE_FIELDS = {
    "server": frozenset({"base", "commands", "diagnostics", "lifecycle", "transports"}),
    "client": frozenset({"base", "commands", "diagnostics", "transports"}),
}
ROLE_COMMANDS = {
    "server": frozenset({"info", "version"}),
    "client": frozenset({"detach", "version"}),
}
PROFILE_OPTION_PREFIXES = (
    "--auto-refresh-delay=",
    "--bandwidth-limit=",
    "--min-quality=",
    "--min-speed=",
    "--quality=",
    "--refresh-rate=",
    "--speed=",
)
ENCODING_PROFILE_POLICIES = {
    "rgb": ("rgb", "strict"),
    "h264": ("h264", "adaptive-alpha"),
}
DEFAULT_ENCODING_PROFILE = next(iter(ENCODING_PROFILE_POLICIES))


class LiveConfigError(ValueError):
    """Raised when mirrored live configuration is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class NetworkProfile:
    """One reviewed set of client-side quality and network controls."""

    name: str
    min_quality: int
    min_speed: int
    auto_refresh_delay_seconds: Decimal
    refresh_rate_hz: int
    bandwidth_limit: str

    def client_options(self) -> tuple[str, ...]:
        return (
            f"--min-quality={self.min_quality}",
            f"--min-speed={self.min_speed}",
            f"--auto-refresh-delay={self.auto_refresh_delay_seconds:.2f}",
            f"--refresh-rate={self.refresh_rate_hz}",
            f"--bandwidth-limit={self.bandwidth_limit}",
        )


def _yaml_scalar(value: str, path: Path, line_number: int) -> object:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise LiveConfigError(
            f"{path}:{line_number}: values must use JSON-compatible YAML scalars"
        ) from error
    if isinstance(parsed, (dict, list)):
        raise LiveConfigError(f"{path}:{line_number}: flow collections are forbidden")
    return parsed


def _yaml_tokens(path: Path) -> list[tuple[int, str, int]]:
    if path.is_symlink() or not path.is_file():
        raise LiveConfigError(f"live configuration is not a regular file: {path}")
    try:
        source_bytes = path.read_bytes()
        source = source_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LiveConfigError(
            f"cannot read live configuration {path}: {error}"
        ) from error
    if len(source_bytes) > CONFIG_BYTES_LIMIT:
        raise LiveConfigError(
            f"live configuration exceeds {CONFIG_BYTES_LIMIT} bytes: {path}"
        )
    tokens: list[tuple[int, str, int]] = []
    for line_number, raw in enumerate(source.splitlines(), 1):
        if "\t" in raw or raw.rstrip() != raw:
            raise LiveConfigError(f"{path}:{line_number}: unsafe whitespace")
        if not raw or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise LiveConfigError(
                f"{path}:{line_number}: indentation must use two spaces"
            )
        tokens.append((indent, raw[indent:], line_number))
    if not tokens:
        raise LiveConfigError(f"live configuration is empty: {path}")
    return tokens


def _parse_yaml_block(
    tokens: list[tuple[int, str, int]],
    index: int,
    indent: int,
    path: Path,
) -> tuple[object, int]:
    if index >= len(tokens) or tokens[index][0] != indent:
        raise LiveConfigError(f"{path}: invalid YAML block indentation")
    list_block = tokens[index][1].startswith("- ")
    value: list[object] | dict[str, object] = [] if list_block else {}
    while index < len(tokens):
        current_indent, content, line_number = tokens[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise LiveConfigError(f"{path}:{line_number}: unexpected indentation")
        if list_block:
            if not content.startswith("- ") or not content[2:]:
                raise LiveConfigError(
                    f"{path}:{line_number}: invalid scalar list entry"
                )
            assert isinstance(value, list)
            value.append(_yaml_scalar(content[2:], path, line_number))
            index += 1
            continue
        if content.startswith("- "):
            raise LiveConfigError(f"{path}:{line_number}: mixed YAML collection types")
        key, separator, payload = content.partition(":")
        if not separator or not KEY_RE.fullmatch(key):
            raise LiveConfigError(f"{path}:{line_number}: invalid mapping key")
        assert isinstance(value, dict)
        if key in value:
            raise LiveConfigError(f"{path}:{line_number}: duplicate mapping key: {key}")
        index += 1
        payload = payload.lstrip(" ")
        if payload:
            value[key] = _yaml_scalar(payload, path, line_number)
            continue
        if index >= len(tokens) or tokens[index][0] != indent + 2:
            raise LiveConfigError(f"{path}:{line_number}: mapping value is missing")
        value[key], index = _parse_yaml_block(tokens, index, indent + 2, path)
    return value, index


def load_strict_yaml(path: Path) -> dict[str, object]:
    """Parse the small deterministic YAML subset used by the fork."""
    tokens = _yaml_tokens(path)
    if tokens[0][0] != 0:
        raise LiveConfigError(f"{path}: top-level YAML must start at column zero")
    payload, index = _parse_yaml_block(tokens, 0, 0, path)
    if index != len(tokens) or not isinstance(payload, dict):
        raise LiveConfigError(f"{path}: top-level YAML must be one mapping")
    return payload


def _integer(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiveConfigError(f"network profile {name} must be an integer")
    if not minimum <= value <= maximum:
        raise LiveConfigError(
            f"network profile {name} must be between {minimum} and {maximum}"
        )
    return value


def _network_profile(name: str, payload: object) -> NetworkProfile:
    if not PROFILE_RE.fullmatch(name) or not isinstance(payload, dict):
        raise LiveConfigError(f"invalid network profile: {name!r}")
    if set(payload) != PROFILE_FIELDS:
        raise LiveConfigError(f"network profile {name} fields are inconsistent")
    delay_value = payload["auto_refresh_delay_seconds"]
    if isinstance(delay_value, bool) or not isinstance(delay_value, (int, float)):
        raise LiveConfigError(f"network profile {name} delay must be numeric")
    try:
        delay = Decimal(str(delay_value))
    except InvalidOperation as error:
        raise LiveConfigError(f"network profile {name} delay is invalid") from error
    if not math.isfinite(float(delay)) or not Decimal("0.01") <= delay <= Decimal(60):
        raise LiveConfigError(f"network profile {name} delay is out of range")
    bandwidth = payload["bandwidth_limit"]
    if not isinstance(bandwidth, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*(?:K|M|G)bps)", bandwidth
    ):
        raise LiveConfigError(f"network profile {name} bandwidth limit is invalid")
    return NetworkProfile(
        name=name,
        min_quality=_integer(
            payload["min_quality"], name=f"{name}.min_quality", minimum=0, maximum=100
        ),
        min_speed=_integer(
            payload["min_speed"], name=f"{name}.min_speed", minimum=0, maximum=100
        ),
        auto_refresh_delay_seconds=delay,
        refresh_rate_hz=_integer(
            payload["refresh_rate_hz"],
            name=f"{name}.refresh_rate_hz",
            minimum=1,
            maximum=240,
        ),
        bandwidth_limit=bandwidth,
    )


@cache
def load_network_profiles(
    path: Path = NETWORK_PROFILES_PATH,
) -> tuple[str, dict[str, NetworkProfile]]:
    payload = load_strict_yaml(path)
    if (
        set(payload) != {"schema", "default_profile", "profiles"}
        or payload.get("schema") != 1
    ):
        raise LiveConfigError("network profile configuration schema is inconsistent")
    profiles_payload = payload.get("profiles")
    if not isinstance(profiles_payload, dict) or not profiles_payload:
        raise LiveConfigError("network profile configuration has no profiles")
    profiles = {
        name: _network_profile(name, value) for name, value in profiles_payload.items()
    }
    default = payload.get("default_profile")
    if not isinstance(default, str) or default not in profiles:
        raise LiveConfigError("default network profile is unavailable")
    return default, profiles


def network_profile_names(path: Path = NETWORK_PROFILES_PATH) -> tuple[str, ...]:
    return tuple(load_network_profiles(path)[1])


def network_profile(name: str, path: Path = NETWORK_PROFILES_PATH) -> NetworkProfile:
    profiles = load_network_profiles(path)[1]
    try:
        return profiles[name]
    except KeyError as error:
        raise LiveConfigError(f"unsupported live network profile: {name}") from error


def _option_list(value: object, *, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(option, str)
            or not option
            or option.strip() != option
            or "\x00" in option
            for option in value
        )
    ):
        raise LiveConfigError(f"{label} must be a non-empty list of exact arguments")
    return tuple(value)


@cache
def load_live_cli(path: Path = LIVE_CLI_PATH) -> dict[str, dict[str, Any]]:
    payload = load_strict_yaml(path)
    if set(payload) != {"schema", "server", "client"} or payload.get("schema") != 1:
        raise LiveConfigError("live CLI configuration schema is inconsistent")
    result: dict[str, dict[str, Any]] = {}
    all_options: list[str] = []
    for role, expected_fields in ROLE_FIELDS.items():
        role_payload = payload.get(role)
        if not isinstance(role_payload, dict) or set(role_payload) != expected_fields:
            raise LiveConfigError(f"live CLI {role} fields are inconsistent")
        role_result: dict[str, Any] = {}
        for block in expected_fields - {"commands", "transports"}:
            options = _option_list(role_payload[block], label=f"{role}.{block}")
            role_result[block] = options
            all_options.extend(options)
        commands = role_payload.get("commands")
        if not isinstance(commands, dict) or set(commands) != ROLE_COMMANDS[role]:
            raise LiveConfigError(f"live CLI {role} commands are inconsistent")
        command_result = {
            command: _option_list(options, label=f"{role}.commands.{command}")
            for command, options in commands.items()
        }
        role_result["commands"] = command_result
        for options in command_result.values():
            all_options.extend(options)
        transports = role_payload.get("transports")
        if not isinstance(transports, dict) or not transports:
            raise LiveConfigError(f"live CLI {role} transports are inconsistent")
        transport_result: dict[str, dict[str, Any]] = {}
        for encoding, transport in transports.items():
            if not KEY_RE.fullmatch(encoding):
                raise LiveConfigError(f"live CLI {role} transport name is invalid")
            if not isinstance(transport, dict) or set(transport) != {
                "common",
                "policies",
            }:
                raise LiveConfigError(f"live CLI {role}.{encoding} is inconsistent")
            common = _option_list(
                transport["common"], label=f"{role}.{encoding}.common"
            )
            policies = transport.get("policies")
            if (
                not isinstance(policies, dict)
                or not policies
                or any(not KEY_RE.fullmatch(policy) for policy in policies)
            ):
                raise LiveConfigError(
                    f"live CLI {role}.{encoding} policies are inconsistent"
                )
            policy_result = {
                policy: _option_list(options, label=f"{role}.{encoding}.{policy}")
                for policy, options in policies.items()
            }
            transport_result[encoding] = {
                "common": common,
                "policies": policy_result,
            }
            all_options.extend(common)
            for options in policy_result.values():
                all_options.extend(options)
        role_result["transports"] = transport_result
        result[role] = role_result
    server_transports = result["server"]["transports"]
    client_transports = result["client"]["transports"]
    if set(server_transports) != set(client_transports) or any(
        set(server_transports[encoding]["policies"])
        != set(client_transports[encoding]["policies"])
        for encoding in server_transports
    ):
        raise LiveConfigError("server and client transport policies differ")
    if any(option.startswith(PROFILE_OPTION_PREFIXES) for option in all_options):
        raise LiveConfigError(
            "profile-managed client arguments are forbidden in live CLI blocks"
        )
    return result


def static_cli_options(
    role: str, block: str, path: Path = LIVE_CLI_PATH
) -> tuple[str, ...]:
    try:
        value = load_live_cli(path)[role][block]
    except KeyError as error:
        raise LiveConfigError(
            f"unsupported static live CLI block: {role}.{block}"
        ) from error
    if not isinstance(value, tuple):
        raise LiveConfigError(f"live CLI block is not static: {role}.{block}")
    return value


def command_cli_options(
    role: str, command: str, path: Path = LIVE_CLI_PATH
) -> tuple[str, ...]:
    try:
        value = load_live_cli(path)[role]["commands"][command]
    except KeyError as error:
        raise LiveConfigError(
            f"unsupported live CLI command block: {role}.{command}"
        ) from error
    if not isinstance(value, tuple) or any(
        not isinstance(option, str) for option in value
    ):
        raise LiveConfigError(f"live CLI command is invalid: {role}.{command}")
    return value


def transport_options(
    role: str,
    encoding: str,
    policy: str,
    path: Path = LIVE_CLI_PATH,
) -> tuple[str, ...]:
    try:
        transport = load_live_cli(path)[role]["transports"][encoding]
        return (*transport["common"], *transport["policies"][policy])
    except KeyError as error:
        raise LiveConfigError(
            f"unsupported live CLI transport: {role}.{encoding}.{policy}"
        ) from error


def encoding_profile_names() -> tuple[str, ...]:
    """Return public production profile names in stable order."""
    return tuple(ENCODING_PROFILE_POLICIES)


def production_encoding(profile: str) -> str:
    """Return the fork transport selected by one public production profile."""
    try:
        return ENCODING_PROFILE_POLICIES[profile][0]
    except KeyError as error:
        raise LiveConfigError(
            f"unsupported production encoding profile: {profile}"
        ) from error


def production_transport_options(role: str, profile: str) -> tuple[str, ...]:
    """Render the reviewed strict RGB or universal adaptive-alpha H.264 profile."""
    try:
        encoding, policy = ENCODING_PROFILE_POLICIES[profile]
    except KeyError as error:
        raise LiveConfigError(
            f"unsupported production encoding profile: {profile}"
        ) from error
    return transport_options(role, encoding, policy)
