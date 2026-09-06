# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Observe real GUI defaults using public X11 and freedesktop interfaces."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import shlex
from pathlib import Path

from elsewindow.session import XpraSession, build_xpra_command_argv

from .xpra_target import OWNED_TITLE


class CursorImage(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
        ("xhot", ctypes.c_ushort),
        ("yhot", ctypes.c_ushort),
        ("serial", ctypes.c_ulong),
        ("pixels", ctypes.POINTER(ctypes.c_ulong)),
        ("atom", ctypes.c_ulong),
        ("name", ctypes.c_char_p),
    ]


def cursor_pixels() -> tuple[int, int, int, int, str]:
    """Read the actual displayed cursor through XFixes, not Xpra internals."""
    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.argtypes = [ctypes.c_void_p]
    fixes = ctypes.CDLL("libXfixes.so.3")
    fixes.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]
    fixes.XFixesGetCursorImage.restype = ctypes.POINTER(CursorImage)
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("cannot inspect the live client's X11 cursor")
    image = None
    try:
        image = fixes.XFixesGetCursorImage(display)
        if (
            not image
            or not 0 < image.contents.width * image.contents.height <= 1024 * 1024
        ):
            raise RuntimeError("the displayed cursor has invalid dimensions")
        value = image.contents
        pixels = b"".join(
            (value.pixels[index] & 0xFFFFFFFF).to_bytes(4, "little")
            for index in range(value.width * value.height)
        )
        return (
            value.width,
            value.height,
            value.xhot,
            value.yhot,
            hashlib.sha256(pixels).hexdigest(),
        )
    finally:
        if image:
            x11.XFree(image)
        x11.XCloseDisplay(display)


async def application_state(session: XpraSession, marker: str) -> dict:
    status, output, _stderr = await session._run_mux(
        shlex.join(("cat", marker + ".gui"))
    )
    if status or len(output) > 16384:
        raise RuntimeError("the live GUI observation is unavailable")
    value = json.loads(output)
    if not isinstance(value, dict):
        raise TypeError("the live GUI observation is invalid")
    return value


async def verify_gui_defaults(
    session: XpraSession, marker: str, notifications: Path
) -> dict:
    async def command(*argv: str) -> bytes:
        status, output, _stderr = await session._run_local(list(argv), 5)
        if status:
            raise RuntimeError(f"the public GUI probe failed: {argv[0]}")
        return output

    async def wait(description: str, predicate) -> None:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            if await predicate():
                return
            await asyncio.sleep(0.1)
        state = await application_state(session, marker)
        diagnostics = ""
        if "notification" in description:
            argv = build_xpra_command_argv(
                "server", "info", "xpra", display=session._remote_display
            )
            _status, output, _stderr = await session._run_mux(shlex.join(argv))
            selected = [
                line
                for line in output.decode(errors="replace").splitlines()
                if "notification" in line and "dbus-id" not in line
            ]
            diagnostics = f"; public notification properties={selected!r}"
        raise RuntimeError(
            f"timed out verifying {description}; fixture state={json.dumps(state)}"
            + diagnostics[:2048]
        )

    ids = (
        await command(
            "xdotool", "search", "--onlyvisible", "--name", "^" + OWNED_TITLE + "$"
        )
    ).split()
    if len(ids) != 1:
        raise RuntimeError(
            "the GUI probe requires exactly one owned application window"
        )
    window = ids[0].decode()
    state = await application_state(session, marker)

    async def point(name: str, *, click: bool = False) -> None:
        x, y = (await application_state(session, marker))["positions"][name]
        await command("xdotool", "mousemove", "--window", window, str(x), str(y))
        if click:
            await command("xdotool", "click", "1")

    await command("xdotool", "windowfocus", "--sync", window)
    await point("entry", click=True)
    await command("xdotool", "key", "--clearmodifiers", "ctrl+a")
    await command("xdotool", "type", "--clearmodifiers", "Elsewindow GUI")

    async def text_received() -> bool:
        return (await application_state(session, marker))["text"] == "Elsewindow GUI"

    await wait("remote text input and modifier release", text_received)
    # Capture an ordinary widget cursor, then the remote application's custom one.
    await point("notify")
    await asyncio.sleep(0.2)
    ordinary = cursor_pixels()
    await point("scroll")

    async def custom_cursor() -> bool:
        return cursor_pixels() != ordinary

    await wait("remote cursor pixels", custom_cursor)
    before = await application_state(session, marker)
    await command("xdotool", "click", "--repeat", "3", "5")
    await command("xdotool", "click", "--repeat", "3", "7")

    async def scrolled() -> bool:
        current = await application_state(session, marker)
        return (
            current["scroll_y"] > before["scroll_y"]
            and current["scroll_x"] > before["scroll_x"]
        )

    await wait("vertical and horizontal wheel forwarding", scrolled)
    notification_count = len(json.loads(notifications.read_bytes()))
    await point("notify", click=True)

    async def submitted() -> bool:
        current = await application_state(session, marker)
        if error := current.get("notification_error"):
            raise RuntimeError(f"the remote notification request failed: {error}")
        return current.get("notification") is True

    await wait("the application's notification request", submitted)

    async def notified() -> bool:
        records = json.loads(notifications.read_bytes())[notification_count:]
        return any(
            record["summary"] == OWNED_TITLE + " Notification"
            and record["body"] == "Remote notification reached the local desktop"
            for record in records
        )

    await wait("a remote notification on the local desktop bus", notified)
    await point("dialog", click=True)

    async def dialog() -> bool:
        status, output, _stderr = await session._run_local(
            [
                "xdotool",
                "search",
                "--onlyvisible",
                "--name",
                "^" + OWNED_TITLE + " Dialog$",
            ],
            5,
        )
        if status or len(output.split()) != 1:
            return False
        popup = output.strip().decode()
        if not (await application_state(session, marker)).get("dialog"):
            return False
        await command("xdotool", "windowfocus", "--sync", popup, "key", "Escape")
        return True

    # GTK's Wayland modal grab is not an X11 window-manager hint. Check the
    # public dialog interaction, leaving compositor metadata to the fork.
    await wait("the application's modal dialog", dialog)

    async def dialog_closed() -> bool:
        return (await application_state(session, marker)).get("dialog") is False

    await wait("closing the dialog through the forwarded keyboard", dialog_closed)
    return state["bus"]
