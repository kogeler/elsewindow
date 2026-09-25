# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Target-side helpers shared by the live GUI fixtures.

The harness installs this module beside each fixture on the disposable target.
It imports no GTK binding; callers pass their loaded Gio and GLib modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PORTAL_SERVICES = (
    "org.freedesktop.impl.portal.PermissionStore",
    "org.freedesktop.impl.portal.desktop.gtk",
    "org.freedesktop.portal.Desktop",
)


def limit_cpus() -> None:
    """Bound native graphics worker pools on high-core-count CI machines."""
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])


def process_start(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rfind(")") + 2 :].split()[19]


def application_environment() -> dict[str, str]:
    return {
        name: value for name, value in os.environ.items() if name.startswith("EW_LIVE_")
    }


def bus_identity(bus: Any, gio: Any, glib: Any) -> tuple[dict[str, Any], str | None]:
    """Record the owned private bus and its portal helpers without activation."""
    bus_pid = int(os.environ["DBUS_SESSION_BUS_PID"])
    services = []
    error = None
    for name in PORTAL_SERVICES:
        try:
            pid = bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetConnectionUnixProcessID",
                glib.Variant("(s)", (name,)),
                glib.VariantType.new("(u)"),
                gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            ).unpack()[0]
            services.append({"pid": pid, "start": process_start(pid)})
        except glib.Error as failure:
            error = failure.message
    return {
        "pid": bus_pid,
        "start": process_start(bus_pid),
        "services": services,
    }, error


def publish(observation: Path, state: dict[str, Any]) -> None:
    staged = observation.with_suffix(".pending")
    staged.write_text(json.dumps(state), encoding="utf-8")
    staged.replace(observation)


def write_marker(marker: Path) -> None:
    pid = os.getpid()
    marker.write_text(
        f"{pid} {process_start(pid)} {os.environ['WAYLAND_DISPLAY']}\n",
        encoding="ascii",
    )
