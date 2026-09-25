# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Optional desktop services using distribution tools on the owned session bus.

The source is also an isolated system-Python helper. Its bootstrap imports only
the standard library; GTK/GIO and D-Bus never enter the Elsewindow environment.
"""

from __future__ import annotations

import base64
import fcntl
import importlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zlib
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

FEATURE_PREFIX = "ELSEWINDOW_FEATURE|"
SYSTEM_PYTHON = "/usr/bin/python3"
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
NOTIFICATIONS_NAME = "org.freedesktop.Notifications"
PORTAL_PACKAGES = "xdg-desktop-portal xdg-desktop-portal-gtk python3-gi"
NOTIFICATION_PACKAGES = "dbus-daemon python3-dbus python3-gi"
SERVICE_TIMEOUT = 5.0
SERVICE_CPUS = 2
MAX_APPLICATION_ENVIRONMENT_VARIABLES = 128
MAX_APPLICATION_ENVIRONMENT_BYTES = 8 * 1024
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SESSION_ENVIRONMENT = frozenset({"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"})
SERVICE_LOADER = (
    "import os,sys;"
    f"os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:{SERVICE_CPUS}]);"
    "os.execv(sys.argv[1],sys.argv[1:])"
)
HELPER_LOADER = (
    "import base64,sys,zlib;"
    "__source__=zlib.decompress(base64.b64decode(sys.argv[1])).decode();"
    "exec(compile(__source__,'<elsewindow-desktop>','exec'));"
)
APPLICATION_LOADER = HELPER_LOADER + "raise SystemExit(application_main(sys.argv[2:]))"
PROBE_LOADER = HELPER_LOADER + "print(json.dumps(prerequisites(sys.argv[2])))"


def source_payload() -> str:
    source = globals().get("__source__")
    if source is None:
        source = files("elsewindow").joinpath("desktop.py").read_text()
    return base64.b64encode(zlib.compress(source.encode())).decode()


def application_argv(
    application: tuple[str, ...],
    notifications: bool = True,
    *,
    application_environment: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    payload = {
        "argv": application,
        "environment": validate_application_environment(dict(application_environment)),
    }
    return (
        SYSTEM_PYTHON,
        "-I",
        "-c",
        APPLICATION_LOADER,
        source_payload(),
        base64.b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode(),
        str(int(notifications)),
    )


def validate_application_environment(value: Any) -> dict[str, str]:
    """Validate explicit application overrides on both sides of the SSH channel."""
    if (
        not isinstance(value, dict)
        or len(value) > MAX_APPLICATION_ENVIRONMENT_VARIABLES
    ):
        raise ValueError(
            "application environment is limited to "
            f"{MAX_APPLICATION_ENVIRONMENT_VARIABLES} variables"
        )
    size = 0
    for name, content in value.items():
        if (
            not isinstance(name, str)
            or ENVIRONMENT_NAME.fullmatch(name) is None
            or not isinstance(content, str)
            or "\0" in content
        ):
            raise ValueError(
                "application environment requires valid names and NUL-free values"
            )
        if name in SESSION_ENVIRONMENT or name.startswith(("ELSEWINDOW_", "DBUS_")):
            raise ValueError(f"{name} is reserved for the owned remote session")
        size += len(name.encode()) + len(content.encode()) + 2
    if size > MAX_APPLICATION_ENVIRONMENT_BYTES:
        raise ValueError(
            "application environment is limited to "
            f"{MAX_APPLICATION_ENVIRONMENT_BYTES} UTF-8 bytes"
        )
    return dict(sorted(value.items()))


def probe_argv(side: str) -> tuple[str, ...]:
    if side not in {"client", "server"}:
        raise ValueError("invalid prerequisite side")
    return SYSTEM_PYTHON, "-I", "-c", PROBE_LOADER, source_payload(), side


def probe_result(status: int, output: bytes) -> dict[str, bool]:
    value = json.loads(output) if status == 0 and len(output) <= 4096 else None
    if (
        not isinstance(value, dict)
        or set(value) != {"notifications", "render"}
        or any(type(item) is not bool for item in value.values())
    ):
        raise ValueError("invalid optional capability reply")
    return {name: bool(item) for name, item in value.items()}


def executable(name: str) -> str | None:
    for directory in (
        "/usr/libexec",
        "/usr/lib/xdg-desktop-portal",
        "/usr/lib/xdg-desktop-portal-gtk",
    ):
        path = Path(directory) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def gio() -> tuple[Any, Any]:
    bindings = importlib.import_module("gi")
    bindings.require_version("Gio", "2.0")
    return importlib.import_module("gi.repository.Gio"), importlib.import_module(
        "gi.repository.GLib"
    )


def connection(address: str) -> Any:
    Gio, _GLib = gio()
    if not address:
        raise ValueError("no supplied session bus")
    return Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )


def has_owner(bus: Any, name: str, *, activatable: bool = False) -> bool:
    Gio, GLib = gio()
    owned = bool(
        bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            1000,
            None,
        ).unpack()[0]
    )
    if owned or not activatable:
        return owned
    names = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "ListActivatableNames",
        None,
        GLib.VariantType.new("(as)"),
        Gio.DBusCallFlags.NO_AUTO_START,
        1000,
        None,
    ).unpack()[0]
    return name in names


def prerequisites(side: str) -> dict[str, bool]:
    """Check distribution bindings and public D-Bus services, without activation."""
    notification = False
    try:
        importlib.import_module("dbus")
        gio()
        notification = True
    except Exception:  # noqa: BLE001 - optional distribution bindings may be broken.
        notification = False
    if side == "server":
        notification = notification and os.access("/usr/bin/dbus-daemon", os.X_OK)
    elif side == "client":
        bus = None
        try:
            address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
            if not address:
                runtime = os.environ.get("XDG_RUNTIME_DIR", "")
                path = Path(runtime) / "bus"
                if runtime and path.is_socket() and path.stat().st_uid == os.getuid():
                    address = f"unix:path={path}"
            bus = connection(address)
            notification = notification and has_owner(
                bus, NOTIFICATIONS_NAME, activatable=True
            )
        except Exception:  # noqa: BLE001 - optional service failures are inert capability data.
            notification = False
        finally:
            if bus is not None:
                with suppress(Exception):
                    bus.close_sync(None)
    else:
        raise ValueError("invalid prerequisite side")
    try:
        render = any(
            path.is_char_device() and os.access(path, os.R_OK | os.W_OK)
            for path in Path("/dev/dri").glob("renderD*")
        )
    except OSError:
        render = False
    return {"notifications": notification, "render": render}


def notice(feature: str, reason: str, packages: str = PORTAL_PACKAGES) -> None:
    message = (
        f"Remote {feature} disabled for this session: {reason}. "
        f"On Debian/Ubuntu, install on the remote host: {packages}. "
        "The application will continue without this feature."
    )
    try:
        print(FEATURE_PREFIX + message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


class DesktopServices:
    """Own foreground portal processes; never contact another session's bus."""

    def __init__(self) -> None:
        self.directory: tempfile.TemporaryDirectory[str] | None = None
        self.processes: list[subprocess.Popen[bytes]] = []
        self.bus: Any = None

    def _start(
        self, executable_path: str, name: str, environment: dict[str, str]
    ) -> None:
        process = subprocess.Popen(
            [SYSTEM_PYTHON, "-I", "-c", SERVICE_LOADER, executable_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        self.processes.append(process)
        deadline = time.monotonic() + SERVICE_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("a portal service exited during startup")
            if has_owner(self.bus, name):
                return
            time.sleep(0.05)
        raise RuntimeError("a portal service did not become ready")

    def start(self, notifications: bool) -> None:
        programs = {
            name: executable(name)
            for name in (
                "xdg-permission-store",
                "xdg-desktop-portal-gtk",
                "xdg-desktop-portal",
            )
        }
        if not all(programs.values()):
            notice(
                "portal file dialogs and portal notifications",
                "the desktop portal packages are missing",
            )
            return
        try:
            environment = os.environ.copy()
            # Only the owner publishes this marker; an inherited desktop bus is
            # never a fallback, even when optional startup fails.
            address = environment.get("ELSEWINDOW_SESSION_BUS", "")
            if not address or address != environment.get("DBUS_SESSION_BUS_ADDRESS"):
                raise RuntimeError("the private session bus is unavailable")
            self.bus = connection(address)
            if has_owner(self.bus, PORTAL_NAME):
                raise RuntimeError("the private portal name is already owned")
            self.directory = tempfile.TemporaryDirectory(prefix="elsewindow-desktop-")
            root = Path(self.directory.name)
            config = root / "config" / "xdg-desktop-portal"
            config.mkdir(parents=True, mode=0o700)
            selected = "[preferred]\ndefault=none\norg.freedesktop.impl.portal.FileChooser=gtk\n"
            if notifications:
                selected += "org.freedesktop.impl.portal.Notification=gtk\n"
            (config / "portals.conf").write_text(selected, encoding="utf-8")
            environment.update(
                XDG_CURRENT_DESKTOP="Elsewindow",
                XDG_CONFIG_HOME=str(config.parent),
                XDG_CONFIG_DIRS=str(config.parent),
                XDG_DESKTOP_PORTAL_CONFIG_DIR=str(config),
                GIO_USE_VFS="local",
                GSETTINGS_BACKEND="memory",
                DBUS_SYSTEM_BUS_ADDRESS="unix:path=/dev/null",
                RAYON_NUM_THREADS=str(SERVICE_CPUS),
                LP_NUM_THREADS=str(SERVICE_CPUS),
                GTK_USE_PORTAL="0",
                NO_AT_BRIDGE="1",
            )
            environment.pop("XDG_DESKTOP_PORTAL_DIR", None)
            # The permission store is transient, with no shared host permission DB.
            permission_environment = environment | {"XDG_DATA_HOME": str(root / "data")}
            self._start(
                str(programs["xdg-permission-store"]),
                "org.freedesktop.impl.portal.PermissionStore",
                permission_environment,
            )
            self._start(
                str(programs["xdg-desktop-portal-gtk"]),
                "org.freedesktop.impl.portal.desktop.gtk",
                environment,
            )
            self._start(str(programs["xdg-desktop-portal"]), PORTAL_NAME, environment)
            Gio, GLib = gio()
            for interface in (
                "FileChooser",
                *(("Notification",) if notifications else ()),
            ):
                self.bus.call_sync(
                    PORTAL_NAME,
                    PORTAL_PATH,
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant(
                        "(ss)", (f"org.freedesktop.portal.{interface}", "version")
                    ),
                    GLib.VariantType.new("(v)"),
                    Gio.DBusCallFlags.NO_AUTO_START,
                    1000,
                    None,
                )
        except Exception:  # noqa: BLE001 - optional services cannot own application lifetime.
            self.close()
            notice(
                "portal file dialogs and portal notifications",
                "the private portal could not start; check its packages and the remote display",
            )

    def check(self) -> None:
        if self.processes and any(
            process.poll() is not None for process in self.processes
        ):
            self.close()
            notice(
                "portal file dialogs and portal notifications",
                "a private portal service exited",
            )

    def close(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.processes.clear()
        if self.bus is not None:
            with suppress(Exception):
                self.bus.close_sync(None)
            self.bus = None
        if self.directory is not None:
            with suppress(OSError):
                self.directory.cleanup()
            self.directory = None


def application_lock() -> tuple[int | None, bool]:
    """One launch per owned scope, including concurrent or late duplicates."""
    value = os.environ.get("ELSEWINDOW_APPLICATION_LOCK")
    if value is None:
        return None, True
    path = Path(value)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError("unsafe application scope")
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("unsafe application lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None, False
        if os.read(descriptor, 1):
            os.close(descriptor)
            return None, False
        os.write(descriptor, b"1")
        return descriptor, True
    except BaseException:
        os.close(descriptor)
        raise


def application_main(arguments: list[str]) -> int:
    """Start desktop helpers after Xpra supplies the display; preserve app argv."""
    payload = json.loads(base64.b64decode(arguments[0], validate=True))
    if not isinstance(payload, dict) or set(payload) != {"argv", "environment"}:
        raise ValueError("invalid application payload")
    application = payload["argv"]
    overrides = validate_application_environment(payload["environment"])
    if (
        not isinstance(application, list)
        or not application
        or any(not isinstance(arg, str) for arg in application)
    ):
        raise ValueError("invalid application argv")
    descriptor, launch = application_lock()
    if not launch:
        return 0
    services = DesktopServices()
    process: subprocess.Popen[bytes] | None = None
    stopping: float | None = None

    def stop(_number: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = stopping or time.monotonic()
        if process is not None and process.poll() is None:
            process.terminate()

    for selected in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(selected, stop)
    try:
        services.start(arguments[1] == "1")
        if stopping is not None:
            return 143
        process = subprocess.Popen(application, env=os.environ | overrides)
        while process.poll() is None:
            services.check()
            if stopping is not None and time.monotonic() - stopping > 5:
                process.kill()
            time.sleep(0.05)
        return (
            process.returncode if process.returncode >= 0 else 128 - process.returncode
        )
    finally:
        services.close()
        if descriptor is not None:
            os.close(descriptor)
