# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Keep the single CLI reference complete and bound to the real parser."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from elsewindow.cli import build_parser

REFERENCE = Path(__file__).resolve().parents[1] / "doc/cli.md"


def test_cli_reference_has_every_option_and_its_current_default() -> None:
    documentation = REFERENCE.read_text(encoding="utf-8")
    rows = re.findall(
        r"^\| (`-[^|]+) \| ([^|]+) \| ([^|]+) \|$",
        documentation,
        flags=re.MULTILINE,
    )
    actions = [action for action in build_parser()._actions if action.option_strings]
    assert len(rows) == len(actions)
    documented = {
        tuple(re.findall(r"`([^`]+)`", names)): (default.strip(), description.strip())
        for names, default, description in rows
    }
    assert set(documented) == {tuple(action.option_strings) for action in actions}
    for action in actions:
        default, description = documented[tuple(action.option_strings)]
        value = action.default
        if value is None or value == argparse.SUPPRESS:
            expected = "—"
        elif value is False:
            expected = "disabled"
        elif isinstance(value, float):
            expected = f"`{value:g}` s"
        else:
            expected = f"`{value}`"
        assert default == expected, action.dest
        assert description, action.dest


@pytest.mark.parametrize(
    "action",
    [action for action in build_parser()._actions if action.choices is not None],
    ids=lambda action: action.dest,
)
def test_cli_reference_has_exactly_the_supported_choices(
    action: argparse.Action,
) -> None:
    documentation = REFERENCE.read_text(encoding="utf-8")
    heading = action.dest.replace("_", " ").title()
    section = re.search(
        rf"^### {re.escape(heading)}\n(.*?)(?=^##|\Z)",
        documentation,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert section is not None, action.dest
    choices = re.findall(r"^\| `([^`]+)`", section.group(1), flags=re.MULTILINE)
    assert choices == list(action.choices), action.dest
