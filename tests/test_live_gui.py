# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Live GUI synchronization uses the application's observed remote focus."""

from __future__ import annotations

import json

import pytest

from tests.live_support.gui import focus_dialog


@pytest.mark.asyncio
async def test_local_dialog_mapping_is_not_remote_keyboard_focus() -> None:
    class Session:
        active = False

        async def _run_local(self, argv: list[str], _timeout: int):
            assert "key" not in argv
            return 0, b"123\n" if "search" in argv else b"", b""

        async def _run_mux(self, _command: str):
            return (
                0,
                json.dumps({"dialog": True, "dialog_active": self.active}).encode(),
                b"",
            )

    session = Session()
    assert not await focus_dialog(session, "/owned-fixture")
    session.active = True
    assert await focus_dialog(session, "/owned-fixture")
