# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""A real freedesktop notification service on the disposable client's bus."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

INTERFACE = "org.freedesktop.Notifications"


class Recorder(dbus.service.Object):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.messages = []
        self.name = dbus.service.BusName(
            INTERFACE, bus=dbus.SessionBus(), do_not_queue=True
        )
        super().__init__(self.name, "/org/freedesktop/Notifications")
        self.path.write_text("[]", encoding="utf-8")

    @dbus.service.method(INTERFACE, in_signature="", out_signature="as")
    def GetCapabilities(self):
        return ["body"]

    @dbus.service.method(INTERFACE, in_signature="", out_signature="ssss")
    def GetServerInformation(self):
        return ("Elsewindow live recorder", "Elsewindow", "1", "1.2")

    @dbus.service.method(INTERFACE, in_signature="susssasa{sv}i", out_signature="u")
    def Notify(self, app, replaces, icon, summary, body, actions, hints, expire):
        self.messages.append({"summary": str(summary), "body": str(body)})
        if len(self.messages) > 100:
            raise RuntimeError("notification fixture exceeded its bound")
        staged = self.path.with_suffix(".pending")
        staged.write_text(json.dumps(self.messages), encoding="utf-8")
        staged.replace(self.path)
        return len(self.messages)

    @dbus.service.method(INTERFACE, in_signature="u", out_signature="")
    def CloseNotification(self, identifier):
        pass


if __name__ == "__main__":
    DBusGMainLoop(set_as_default=True)
    recorder = Recorder(Path(sys.argv[1]))
    print("ready", flush=True)
    GLib.MainLoop().run()
