# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Check the agent-notification probe on a local window-managed desktop."""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from elsewindow.session import XpraSession

from .agent_probe import NOTIFICATION_SIZE
from .gui import application_state

LOCAL_TITLE = "Elsewindow Local Application"
# The operator desktop this scenario mirrors uses xfwm4 with its defaults.
WINDOW_MANAGER = ("xfwm4", "--compositor=off", "--sm-client-disable")
LOCAL_APPLICATION = (
    "xmessage",
    "-title",
    LOCAL_TITLE,
    "-geometry",
    "640x360+320+180",
    "A local application keeps keyboard focus.",
)
WAIT_TIMEOUT = 15.0
DIAGNOSTIC_FIELDS = (
    "main_focused",
    "main_active",
    "outputs",
    "notification",
    "windows",
    "shown",
    "dismissed",
)
TREE_LINE = re.compile(
    r"^\s*(0x[0-9a-f]+) (?:\"(?P<name>.*)\"|\(has no name\)):.*?"
    r"\s(?P<width>\d+)x(?P<height>\d+)[+-]-?\d+[+-]-?\d+"
    r"\s+\+(?P<x>-?\d+)\+(?P<y>-?\d+)$"
)


@dataclass(frozen=True, slots=True)
class LocalWindow:
    identifier: int
    name: str | None
    width: int
    height: int
    x: int
    y: int


def parse_window_tree(output: str) -> tuple[LocalWindow, ...]:
    """Parse `xwininfo -root -tree` absolute geometry without Xpra internals."""
    windows = []
    for line in output.splitlines():
        match = TREE_LINE.match(line)
        if match is None:
            continue
        windows.append(
            LocalWindow(
                int(match.group(1), 16),
                match.group("name"),
                int(match.group("width")),
                int(match.group("height")),
                int(match.group("x")),
                int(match.group("y")),
            )
        )
    return tuple(windows)


def parse_active_window(output: bytes) -> int:
    """Parse `xprop -root _NET_ACTIVE_WINDOW`; absent or none is zero."""
    match = re.search(rb"window id # (0x[0-9a-f]+)", output)
    return int(match.group(1), 16) if match else 0


def parse_shell_fields(output: bytes) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in output.decode("ascii", errors="replace").splitlines()
        if "=" in line
    )


class LocalDesktop:
    """Run a real window manager and one unrelated local application.

    The desktop outlives individual sessions, like an operator's desktop across
    persistent reconnects; `session` selects the current local command runner.
    """

    def __init__(self, session: XpraSession) -> None:
        self.session = session
        self.processes: list[asyncio.subprocess.Process] = []
        self.local_window = ""

    async def command(self, *argv: str) -> bytes:
        status, output, stderr = await self.session._run_local(list(argv), 5)
        if status:
            detail = stderr.decode("utf-8", errors="replace").strip()[-512:]
            raise RuntimeError(
                f"the public desktop probe failed ({status}): "
                f"{shlex.join(argv)[:256]}: {detail}"
            )
        return output

    async def spawn(self, *argv: str) -> None:
        self.processes.append(
            await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )

    async def start(self) -> None:
        try:
            await self.spawn(*WINDOW_MANAGER)

            async def managed() -> bool:
                status, output, _stderr = await self.session._run_local(
                    ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], 5
                )
                return status == 0 and b"window id #" in output

            await wait_for("the local window manager", managed, self.processes)
            await self.spawn(*LOCAL_APPLICATION)

            async def local_application() -> bool:
                status, output, _stderr = await self.session._run_local(
                    [
                        "xdotool",
                        "search",
                        "--onlyvisible",
                        "--name",
                        f"^{LOCAL_TITLE}$",
                    ],
                    5,
                )
                if status or len(output.split()) != 1:
                    return False
                self.local_window = output.strip().decode()
                return True

            await wait_for(
                "the local application window", local_application, self.processes
            )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for process in reversed(self.processes):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        self.processes.clear()

    async def windows(self) -> tuple[LocalWindow, ...]:
        return parse_window_tree(
            (await self.command("xwininfo", "-root", "-tree")).decode(
                "utf-8", errors="replace"
            )
        )

    async def viewable(self, window: LocalWindow) -> bool:
        status, output, _stderr = await self.session._run_local(
            ["xwininfo", "-id", hex(window.identifier)], 5
        )
        return status == 0 and b"Map State: IsViewable" in output

    async def topmost_at(self, x: int, y: int) -> int:
        """Return the client window under a pointer placed at one root point."""
        fields: dict[str, str] = {}

        async def placed() -> bool:
            await self.command("xdotool", "mousemove", str(x), str(y))
            fields.update(
                parse_shell_fields(
                    await self.command("xdotool", "getmouselocation", "--shell")
                )
            )
            return (fields.get("X"), fields.get("Y")) == (str(x), str(y))

        await wait_for(f"the local pointer at {x},{y}", placed, self.processes)
        return int(fields.get("WINDOW", "0"))

    async def active(self) -> int:
        """Read EWMH focus without failing when no window is active."""
        return parse_active_window(
            await self.command("xprop", "-root", "_NET_ACTIVE_WINDOW")
        )

    async def activate(self, window: str) -> None:
        await self.command("xdotool", "windowactivate", "--sync", window)


async def wait_for(
    description: str,
    predicate: Callable[[], Awaitable[bool]],
    processes: list[asyncio.subprocess.Process] | None = None,
    diagnostics: Callable[[], Awaitable[str]] | None = None,
) -> None:
    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if any(process.returncode is not None for process in processes or ()):
            raise RuntimeError(f"a local desktop process exited before {description}")
        if await predicate():
            return
        await asyncio.sleep(0.1)
    detail = f"; {await diagnostics()}" if diagnostics is not None else ""
    raise RuntimeError(f"timed out waiting for {description}{detail}")


async def verify_agent_notification(
    session: XpraSession, desktop: LocalDesktop, marker: str, main_title: str
) -> dict[str, Any]:
    """Finish one agent turn while a local application is active."""
    main: list[str] = []

    async def main_visible() -> bool:
        # A newly started window manager remaps existing windows.
        status, output, _stderr = await session._run_local(
            ["xdotool", "search", "--onlyvisible", "--name", f"^{main_title}$"], 5
        )
        main[:] = output.decode().split() if status == 0 else []
        return len(main) == 1

    await wait_for("one visible local main window", main_visible, desktop.processes)
    main_window = main[0]
    before = await application_state(session, marker)

    async def probe_diagnostics() -> str:
        state = await application_state(session, marker)
        probe = {key: state.get(key) for key in DIAGNOSTIC_FIELDS}
        tree = [window for window in await desktop.windows() if window.name][:16]
        return f"probe={probe}, named local windows={tree}"[:4096]

    async def main_focus(expected: bool) -> bool:
        return (await application_state(session, marker))["main_focused"] is expected

    await desktop.activate(main_window)
    await wait_for(
        "remote keyboard focus on the main window",
        lambda: main_focus(True),
        desktop.processes,
        probe_diagnostics,
    )
    # The operator works in another local application while the agent runs.
    await desktop.activate(desktop.local_window)
    await wait_for(
        "remote keyboard focus loss after local activation",
        lambda: main_focus(False),
        desktop.processes,
        probe_diagnostics,
    )
    known = {window.identifier for window in await desktop.windows()}
    status, _stdout, _stderr = await session._run_mux(
        shlex.join(("touch", marker + ".turn"))
    )
    if status:
        raise RuntimeError("cannot finish the probe's agent turn")

    async def shown() -> bool:
        state = await application_state(session, marker)
        return (
            state["turns"] > before["turns"]
            and state["notification"] == "shown"
            and len(state["windows"]) == state["outputs"] > 0
            and all(
                window["mapped"]
                and (window["width"], window["height"]) == NOTIFICATION_SIZE
                for window in state["windows"]
            )
        )

    await wait_for(
        "the remote agent notification window",
        shown,
        desktop.processes,
        probe_diagnostics,
    )
    state = await application_state(session, marker)
    located: list[LocalWindow] = []

    async def presented() -> bool:
        located.clear()
        for window in await desktop.windows():
            if (
                window.identifier not in known
                and (window.width, window.height) == NOTIFICATION_SIZE
                and await desktop.viewable(window)
            ):
                located.append(window)
        return len(located) >= state["outputs"]

    await wait_for(
        "the forwarded notification window on the local desktop",
        presented,
        desktop.processes,
        probe_diagnostics,
    )
    width, height = (
        int(value)
        for value in (await desktop.command("xdotool", "getdisplaygeometry")).split()
    )
    visible = []
    for window in located:
        inside = (
            window.x >= 0
            and window.y >= 0
            and window.x + window.width <= width
            and window.y + window.height <= height
        )
        center = (window.x + window.width // 2, window.y + window.height // 2)
        top = await desktop.topmost_at(*center)
        visible.append(
            {
                "geometry": [window.x, window.y, window.width, window.height],
                "on_screen": inside,
                "on_top": top in {candidate.identifier for candidate in located},
            }
        )
    focus = await desktop.active()
    if not all(item["on_screen"] and item["on_top"] for item in visible):
        raise RuntimeError(
            "the agent notification is not visible above the active local "
            f"application: {visible}; active window={focus:#x}"
        )
    # Returning to the conversation dismisses every notification window.
    await desktop.activate(main_window)

    async def dismissed() -> bool:
        current = await application_state(session, marker)
        if current["windows"] or current["dismissed"] <= before["dismissed"]:
            return False
        return not any([await desktop.viewable(window) for window in located])

    await wait_for(
        "the agent notification dismissal",
        dismissed,
        desktop.processes,
        probe_diagnostics,
    )
    return {"windows": visible, "stole_focus": focus != int(desktop.local_window)}
