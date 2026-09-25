#!/usr/bin/python3
# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Lightweight GTK Wayland probe of Zed's agent-notification window flow.

The probe reproduces the public Wayland behavior of Zed 1.20.2 without
shipping Zed itself:

- ``ConversationView::show_notification`` notifies only while the agent status
  is not visible, which requires the main window to hold keyboard focus;
  GPUI's Wayland backend derives that state from ``wl_keyboard`` enter/leave.
- An open notification suppresses another one.
- ``primary_screen`` (the Zed default) needs ``cx.primary_display()``, which
  GPUI's Wayland client always reports as absent, so it opens no window there;
  ``request_attention`` is empty on Wayland as well.
- ``all_screens`` opens one ``AgentNotification`` per Wayland output: a
  borderless, transparent 450x72 ``xdg_toplevel`` without parent or title that
  shares the application's app ID and requests no activation. Wayland ignores
  its requested top-right position.
- Regaining main-window focus, or activating a notification, dismisses them.
"""

from __future__ import annotations

import hashlib
import os
import signal
import sys
from pathlib import Path

NOTIFICATION_SIZE = (450, 72)
NOTIFY_MODES = ("primary_screen", "all_screens", "never")
CARD_STYLE = b"""
window.elsewindow-agent-notification { background-color: transparent; }
.elsewindow-agent-card {
    background-color: #1f2329;
    border-radius: 8px;
    color: #f5f5f5;
    padding: 12px;
}
"""


def notification_windows(
    mode: str, main_focused: bool, open_windows: int, outputs: int
) -> int:
    """Return how many Wayland notification windows Zed would open."""
    if mode not in NOTIFY_MODES:
        raise ValueError(f"unsupported notification mode: {mode}")
    if open_windows or main_focused or mode != "all_screens":
        return 0
    return outputs


def main() -> int:
    from remote_fixture import (
        application_environment,
        bus_identity,
        limit_cpus,
        publish,
        write_marker,
    )

    limit_cpus()
    os.environ["GDK_BACKEND"] = "wayland"
    os.umask(0o077)
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk

    marker = Path(sys.argv[1])
    title = sys.argv[2]
    mode = sys.argv[3]
    if mode not in NOTIFY_MODES:
        raise SystemExit(f"unsupported notification mode: {mode}")
    application = Gtk.Application(
        application_id="org.elsewindow.AgentProbe.a"
        + hashlib.sha256(str(marker).encode()).hexdigest()
    )
    application.register(None)
    if application.get_is_remote():
        return application.run(["elsewindow-agent-probe"])
    observation = Path(str(marker) + ".gui")
    turn_request = Path(str(marker) + ".turn")
    exit_request = Path(str(marker) + ".exit")
    status = 0
    style = Gtk.CssProvider()
    style.load_from_data(CARD_STYLE)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window = Gtk.ApplicationWindow(application=application, title=title)
    window.set_decorated(False)
    window.set_default_size(560, 360)
    window.add(Gtk.Label(label="Agent conversation"))
    display = Gdk.Display.get_default()
    state = {
        "application_environment": application_environment(),
        "mode": mode,
        "main_focused": False,
        "main_active": False,
        "outputs": display.get_n_monitors(),
        "turns": 0,
        "notification": "none",
        "windows": [],
        "shown": 0,
        "dismissed": 0,
        "accepted": 0,
    }
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state["bus"], portal_error = bus_identity(bus, Gio, GLib)
    if portal_error is not None:
        state["portal_error"] = portal_error
    notifications: list[Gtk.Window] = []

    def update() -> None:
        state["main_active"] = window.is_active()
        state["windows"] = [
            {
                "width": popup.get_allocated_width(),
                "height": popup.get_allocated_height(),
                "mapped": popup.get_mapped(),
            }
            for popup in notifications
        ]
        publish(observation, state)

    def dismiss() -> None:
        if not notifications:
            return
        for popup in notifications:
            popup.destroy()
        notifications.clear()
        state["dismissed"] += 1
        update()

    def accepted(_widget: Gtk.Widget, _event: Gdk.EventButton) -> bool:
        state["accepted"] += 1
        window.present()
        dismiss()
        return True

    def pop_up() -> Gtk.Window:
        popup = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        # Share the application's app ID like GPUI; set no title or parent.
        application.add_window(popup)
        popup.get_style_context().add_class("elsewindow-agent-notification")
        visual = popup.get_screen().get_rgba_visual()
        if visual is not None:
            popup.set_visual(visual)
        popup.set_app_paintable(True)
        popup.set_decorated(False)
        popup.set_resizable(False)
        popup.set_focus_on_map(False)
        popup.set_default_size(*NOTIFICATION_SIZE)
        card = Gtk.EventBox()
        card.get_style_context().add_class("elsewindow-agent-card")
        card.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        card.connect("button-press-event", accepted)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
        content.pack_start(Gtk.Label(label="New message", xalign=0), False, False, 0)
        card.add(content)
        popup.add(card)
        popup.connect("size-allocate", lambda *_arguments: update())
        popup.connect("map-event", lambda *_arguments: update())
        # Show without presenting: GPUI requests no activation for this window.
        popup.show_all()
        return popup

    def finish_turn() -> None:
        state["turns"] += 1
        state["outputs"] = display.get_n_monitors()
        count = notification_windows(
            mode, state["main_focused"], len(notifications), state["outputs"]
        )
        if count:
            notifications.extend(pop_up() for _index in range(count))
            state["notification"] = "shown"
            state["shown"] += 1
        elif notifications:
            state["notification"] = "already-open"
        elif state["main_focused"]:
            state["notification"] = "agent-visible"
        else:
            state["notification"] = "no-window"
        update()

    def focused(_window: Gtk.Window, _event: Gdk.EventFocus) -> bool:
        state["main_focused"] = True
        update()
        # Zed observes main-window activation and dismisses once visible.
        dismiss()
        return False

    def unfocused(_window: Gtk.Window, _event: Gdk.EventFocus) -> bool:
        state["main_focused"] = False
        update()
        return False

    def poll() -> bool:
        nonlocal status
        if exit_request.exists():
            status = int(exit_request.read_text(encoding="ascii").strip())
            application.quit()
            return False
        if turn_request.exists():
            turn_request.unlink()
            finish_turn()
        else:
            update()
        return True

    window.connect("focus-in-event", focused)
    window.connect("focus-out-event", unfocused)
    window.connect("destroy", lambda _window: application.quit())
    application.connect("activate", lambda _application: window.present())
    for selected in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLibUnix.signal_add(
            GLib.PRIORITY_DEFAULT, selected, lambda: (application.quit(), False)[1]
        )
    window.show_all()
    write_marker(marker)
    GLib.timeout_add(100, poll)
    try:
        application.run(["elsewindow-agent-probe"])
    finally:
        for path in (marker, observation, turn_request, exit_request):
            path.unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
