# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""The lightweight agent-notification probe and its local desktop checks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from elsewindow.cli import build_parser
from elsewindow.config import SUPPORTED_ENCODING_PROFILES, SUPPORTED_NETWORK_PROFILES
from tests.live_support.agent_notification import (
    LocalWindow,
    parse_active_window,
    parse_shell_fields,
    parse_window_tree,
)
from tests.live_support.agent_probe import NOTIFICATION_SIZE, notification_windows
from tests.live_support.journal import LIVE_LOG_LEVELS
from tests.live_support.xpra_target import (
    AGENT_CASE,
    AGENT_MARKER,
    AGENT_PROBE,
    FIXTURE_SOURCES,
    OWNED_TITLE,
)
from tests.live_xpra_e2e import AGENT_NOTIFY_MODE, AGENT_OPERATOR_OPTIONS

LIVE_SUPPORT = Path(__file__).with_name("live_support")


def test_probe_opens_one_window_per_output_only_when_zed_would() -> None:
    assert NOTIFICATION_SIZE == (450, 72)
    assert notification_windows("all_screens", False, 0, 2) == 2
    # The agent status is visible while the main window holds keyboard focus.
    assert notification_windows("all_screens", True, 0, 1) == 0
    # An open notification suppresses another one.
    assert notification_windows("all_screens", False, 1, 1) == 0
    # GPUI's Wayland client has no primary display, so Zed's default is silent.
    assert notification_windows("primary_screen", False, 0, 1) == 0
    assert notification_windows("never", False, 0, 1) == 0
    with pytest.raises(ValueError, match="unsupported notification mode"):
        notification_windows("system", False, 0, 1)


def test_operator_command_is_parsed_by_the_production_parser() -> None:
    arguments = build_parser().parse_args(
        [
            "--ssh-alias",
            "target",
            *AGENT_OPERATOR_OPTIONS,
            "--",
            AGENT_PROBE,
            AGENT_MARKER,
            OWNED_TITLE,
            AGENT_NOTIFY_MODE,
        ]
    )
    assert arguments.encoding_profile == "h264" in SUPPORTED_ENCODING_PROFILES
    assert arguments.network_profile == "gigabit_lan" in SUPPORTED_NETWORK_PROFILES
    assert arguments.persistent is True
    assert arguments.env is None
    assert arguments.application[-4:] == [
        AGENT_PROBE,
        AGENT_MARKER,
        OWNED_TITLE,
        AGENT_NOTIFY_MODE,
    ]
    # A home-relative path, like `.local/zed.app/libexec/zed-editor`.
    assert not AGENT_PROBE.startswith("/") and "/" in AGENT_PROBE
    assert LIVE_LOG_LEVELS[AGENT_CASE] == build_parser().get_default("log_level")


def test_every_remote_fixture_is_installed_with_its_shared_helpers() -> None:
    helpers = ast.parse((LIVE_SUPPORT / "remote_fixture.py").read_text())
    defined = {node.name for node in helpers.body if isinstance(node, ast.FunctionDef)}
    assert set(FIXTURE_SOURCES) == {"app", "agent-probe", "remote_fixture.py"}
    for installed, source in FIXTURE_SOURCES.items():
        text = (LIVE_SUPPORT / source).read_text()
        if installed != "remote_fixture.py":
            assert text.startswith("#!/usr/bin/python3\n")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "remote_fixture":
                assert {alias.name for alias in node.names} <= defined


def test_window_tree_reports_absolute_geometry_of_named_and_unnamed_windows() -> None:
    output = """
xwininfo: Window id: 0x3ec (the root window) (has no name)

  Root window id: 0x3ec (the root window) (has no name)
  Parent window id: 0x0 (none)
     3 children:
     0x600011 "Elsewindow Live Test": ("xpra" "Xpra")  560x360+10+20  +10+20
        0x600012 (has no name): ()  450x72+0+0  +830+-4
     0xa00003 (has no name): ()  450x72+830+4  +830+4
"""
    assert parse_window_tree(output) == (
        LocalWindow(0x600011, "Elsewindow Live Test", 560, 360, 10, 20),
        LocalWindow(0x600012, None, 450, 72, 830, -4),
        LocalWindow(0xA00003, None, 450, 72, 830, 4),
    )


def test_mouse_location_fields_are_parsed_from_shell_output() -> None:
    fields = parse_shell_fields(b"X=10\nY=20\nSCREEN=0\nWINDOW=6291474\n")
    assert fields["WINDOW"] == "6291474"


def test_active_window_is_read_without_requiring_one() -> None:
    assert (
        parse_active_window(b"_NET_ACTIVE_WINDOW(WINDOW): window id # 0x600011\n")
        == 0x600011
    )
    assert parse_active_window(b"_NET_ACTIVE_WINDOW:  not found.\n") == 0
