#!/usr/bin/python3
# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Small public GTK application for the existing SSH-owned live topology."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from pathlib import Path


def main() -> int:
    # Bound native graphics worker pools on high-core-count CI machines.
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    os.environ["GDK_BACKEND"] = "wayland"
    os.umask(0o077)
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk

    marker = Path(sys.argv[1])
    # Exercise a normal single-instance GUI on the private session bus. Separate
    # fixture invocations still have separate IDs, even on the same bus.
    application = Gtk.Application(
        application_id="org.elsewindow.Live.a"
        + hashlib.sha256(str(marker).encode()).hexdigest()
    )
    application.register(None)
    if application.get_is_remote():
        return application.run(["elsewindow-live-app"])
    observation = Path(str(marker) + ".gui")
    exit_request = Path(str(marker) + ".exit")
    title = sys.argv[2]
    status = 0
    window = Gtk.ApplicationWindow(application=application, title=title)
    # Keep widget coordinates equal to surface coordinates; client-side frame
    # decorations are rendering behavior owned by the maintained fork's tests.
    window.set_decorated(False)
    window.set_default_size(560, 360)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_border_width(20)
    window.add(box)
    entry = Gtk.Entry()
    scroll = Gtk.EventBox()
    scroll.add(Gtk.Label(label="Scroll vertically and horizontally here"))
    scroll.set_size_request(440, 100)
    scroll.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
    dialog_button = Gtk.Button(label="Open modal dialog")
    notify_button = Gtk.Button(label="Send notification")
    widgets = {
        "entry": entry,
        "scroll": scroll,
        "dialog": dialog_button,
        "notify": notify_button,
    }
    for widget in widgets.values():
        box.pack_start(widget, False, False, 0)
    bus_pid = int(os.environ["DBUS_SESSION_BUS_PID"])
    bus_stat = Path(f"/proc/{bus_pid}/stat").read_text()
    state = {
        "text": "",
        "scroll_x": 0.0,
        "scroll_y": 0.0,
        "bus": {
            "pid": bus_pid,
            "start": bus_stat[bus_stat.rfind(")") + 2 :].split()[19],
        },
    }

    def publish() -> None:
        state["positions"] = {
            name: list(
                widget.translate_coordinates(
                    window,
                    widget.get_allocated_width() // 2,
                    widget.get_allocated_height() // 2,
                )
            )
            for name, widget in widgets.items()
        }
        staged = observation.with_suffix(".pending")
        staged.write_text(json.dumps(state), encoding="utf-8")
        staged.replace(observation)

    def changed(_entry: Gtk.Entry) -> None:
        state["text"] = entry.get_text()
        publish()

    def scrolled(_widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        directions = {
            Gdk.ScrollDirection.UP: (0, -1),
            Gdk.ScrollDirection.DOWN: (0, 1),
            Gdk.ScrollDirection.LEFT: (-1, 0),
            Gdk.ScrollDirection.RIGHT: (1, 0),
        }
        dx, dy = directions.get(event.direction, event.get_scroll_deltas()[1:])
        state["scroll_x"] += dx
        state["scroll_y"] += dy
        publish()
        return True

    def dialog(_button: Gtk.Button) -> None:
        popup = Gtk.Dialog(title=title + " Dialog", transient_for=window, modal=True)
        popup.add_button("Close", Gtk.ResponseType.CLOSE)
        popup.get_content_area().add(Gtk.Label(label="Modal child window"))

        def closed(child: Gtk.Dialog, _response: int) -> None:
            state["dialog"] = False
            child.destroy()
            publish()

        popup.connect("response", closed)
        popup.show_all()
        state["dialog"] = True
        publish()

    def notify(_button: Gtk.Button) -> None:
        state["notification"] = False
        state.pop("notification_error", None)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Elsewindow fixture",
                        0,
                        "",
                        title + " Notification",
                        "Remote notification reached the local desktop",
                        [],
                        {},
                        5000,
                    ),
                ),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            state["notification"] = True
        except GLib.Error as error:
            state["notification_error"] = error.message
        publish()

    def poll() -> bool:
        nonlocal status
        if exit_request.exists():
            status = int(exit_request.read_text(encoding="ascii").strip())
            application.quit()
            return False
        publish()
        return True

    entry.connect("changed", changed)
    scroll.connect("scroll-event", scrolled)
    scroll.connect(
        "realize",
        lambda widget: widget.get_window().set_cursor(
            Gdk.Cursor.new_from_name(widget.get_display(), "crosshair")
        ),
    )
    dialog_button.connect("clicked", dialog)
    notify_button.connect("clicked", notify)
    window.connect("destroy", lambda _window: application.quit())
    application.connect("activate", lambda _application: window.present())
    for selected in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLibUnix.signal_add(
            GLib.PRIORITY_DEFAULT, selected, lambda: (application.quit(), False)[1]
        )
    window.show_all()
    process_stat = Path(f"/proc/{os.getpid()}/stat").read_text()
    start = process_stat[process_stat.rfind(")") + 2 :].split()[19]
    marker.write_text(
        f"{os.getpid()} {start} {os.environ['WAYLAND_DISPLAY']}\n", encoding="ascii"
    )
    GLib.timeout_add(100, poll)
    try:
        application.run(["elsewindow-live-app"])
    finally:
        for path in (marker, observation, exit_request):
            path.unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
