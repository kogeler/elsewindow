# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Optional desktop failure must not change application argv or lifetime."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from elsewindow import desktop
from elsewindow.session_bus import OwnedSessionBus


def test_service_worker_pool_is_bounded_without_limiting_the_application() -> None:
    original = os.sched_getaffinity(0)
    result = subprocess.run(
        [
            desktop.SYSTEM_PYTHON,
            "-I",
            "-c",
            desktop.SERVICE_LOADER,
            desktop.SYSTEM_PYTHON,
            "-I",
            "-c",
            "import json,os;print(json.dumps(sorted(os.sched_getaffinity(0))))",
        ],
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout) == sorted(original)[: desktop.SERVICE_CPUS]
    assert os.sched_getaffinity(0) == original


@pytest.mark.parametrize("notifications", (False, True))
def test_portal_configuration_selects_only_owned_optional_interfaces(
    monkeypatch: pytest.MonkeyPatch,
    notifications: bool,
) -> None:
    token = object()
    monkeypatch.setattr(
        desktop,
        "gio",
        lambda: (
            SimpleNamespace(DBusCallFlags=SimpleNamespace(NO_AUTO_START=token)),
            SimpleNamespace(
                Variant=lambda *_args: None,
                VariantType=SimpleNamespace(new=lambda _kind: None),
            ),
        ),
    )
    calls = []
    bus = SimpleNamespace(
        call_sync=lambda *args: calls.append(args), close_sync=lambda _arg: None
    )
    monkeypatch.setattr(desktop, "connection", lambda _address: bus)
    monkeypatch.setattr(desktop, "has_owner", lambda *_args: False)
    monkeypatch.setattr(desktop, "executable", lambda name: "/usr/libexec/" + name)
    monkeypatch.setenv("ELSEWINDOW_SESSION_BUS", "unix:path=/owned-fixture/bus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/owned-fixture/bus")
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/unowned-fixture/bus")
    monkeypatch.setenv("XDG_DESKTOP_PORTAL_DIR", "/unowned-fixture/backends")
    services = desktop.DesktopServices()
    environments = []
    monkeypatch.setattr(
        services, "_start", lambda _program, _name, env: environments.append(env.copy())
    )
    try:
        services.start(notifications)
        assert len(environments) == 3 and services.directory is not None
        root = Path(services.directory.name)
        config = (root / "config/xdg-desktop-portal/portals.conf").read_text()
        assert "default=none" in config and "FileChooser=gtk" in config
        assert ("Notification=gtk" in config) is notifications
        for env in environments:
            assert env["DBUS_SYSTEM_BUS_ADDRESS"] == "unix:path=/dev/null"
            assert (
                env["GSETTINGS_BACKEND"] == "memory" and env["GIO_USE_VFS"] == "local"
            )
            assert "XDG_DESKTOP_PORTAL_DIR" not in env
        assert Path(environments[0]["XDG_DATA_HOME"]).parent == root
        assert len(calls) == 1 + notifications
        assert all(call[-3] is token for call in calls)
    finally:
        services.close()
    assert not root.exists()


def test_owned_application_starts_once_even_with_concurrent_duplicate_commands(
    tmp_path: Path,
) -> None:
    bus = OwnedSessionBus()
    environment = bus.start(dict(os.environ))
    application = (
        "/usr/bin/python3",
        "-c",
        "import pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text('started');time.sleep(1)",
        str(tmp_path / "started"),
    )
    command = desktop.application_argv(application)
    first = subprocess.Popen(
        command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "started").read_text() == "started"
        assert first.poll() is None
        second = subprocess.run(
            command, env=environment, capture_output=True, timeout=5, check=False
        )
        assert second.returncode == 0 and second.stderr == b""
        assert first.poll() is None
        assert first.wait(timeout=5) == 0
        (tmp_path / "started").unlink()
        assert (
            subprocess.run(
                command, env=environment, capture_output=True, timeout=5, check=False
            ).returncode
            == 0
        )
        assert not (tmp_path / "started").exists()
    finally:
        if first.poll() is None:
            first.terminate()
            first.wait(timeout=5)
        bus.close()


def test_missing_portal_packages_preserve_real_application_argv_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = zlib.decompress(base64.b64decode(desktop.source_payload()))
    source += b"\nexecutable = lambda _name: None\n"
    monkeypatch.setattr(
        desktop,
        "source_payload",
        lambda: base64.b64encode(zlib.compress(source)).decode(),
    )
    arguments = ("with spaces", "", "literal;$HOME", "line\nvalue")
    child = (
        "/usr/bin/python3",
        "-I",
        "-c",
        "import json,sys;print(json.dumps(sys.argv[1:]));sys.exit(23)",
        *arguments,
    )
    result = subprocess.run(
        desktop.application_argv(child),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 23
    assert json.loads(result.stdout) == list(arguments)
    assert result.stderr.startswith(desktop.FEATURE_PREFIX)
    assert desktop.PORTAL_PACKAGES in result.stderr
    assert "disabled for this session" in result.stderr


def test_environment_overrides_reach_only_the_application_without_shell_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_MODE", "inherited")
    source = zlib.decompress(base64.b64decode(desktop.source_payload()))
    source += (
        b"\ndef start_fixture(self, notifications):\n"
        b"    assert os.environ['APP_MODE'] == 'inherited'\n"
        b"DesktopServices.start = start_fixture\n"
    )
    monkeypatch.setattr(
        desktop,
        "source_payload",
        lambda: base64.b64encode(zlib.compress(source)).decode(),
    )
    overrides = {
        "APP_MODE": "remote override",
        "EMPTY": "",
        "LITERAL": "literal;$HOME $(touch ignored); 'quoted'\nnext=part\\end \u2603",
        "PYTHONPATH": "application-only imports",
    }
    child = (
        desktop.SYSTEM_PYTHON,
        "-I",
        "-c",
        "import json,os,sys;print(json.dumps({k:os.environ[k] for k in sys.argv[1:]}))",
        *overrides,
    )
    result = subprocess.run(
        desktop.application_argv(
            child, application_environment=tuple(overrides.items())
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == overrides
    assert os.environ["APP_MODE"] == "inherited"
    assert not (tmp_path / "ignored").exists()


@pytest.mark.parametrize(
    "value",
    (
        [],
        {"": "value"},
        {"A=B": "value"},
        {"1NAME": "value"},
        {"NAME": "bad\0value"},
        {"NAME": 1},
        {"DISPLAY": ":99"},
        {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/unowned"},
        {"ELSEWINDOW_APPLICATION_LOCK": "/unowned"},
        {"NAME": "x" * desktop.MAX_APPLICATION_ENVIRONMENT_BYTES},
        {f"V{i}": "" for i in range(desktop.MAX_APPLICATION_ENVIRONMENT_VARIABLES + 1)},
    ),
)
def test_invalid_application_environment_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        desktop.validate_application_environment(value)


def test_portal_never_adopts_an_inherited_desktop_bus(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(desktop, "executable", lambda _name: "/bin/false")
    monkeypatch.delenv("ELSEWINDOW_SESSION_BUS", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/unowned/desktop")
    monkeypatch.setattr(
        desktop,
        "connection",
        lambda _address: pytest.fail("must not contact the inherited bus"),
    )
    services = desktop.DesktopServices()
    services.start(True)
    assert not services.processes and services.directory is None
    assert "private portal could not start" in capsys.readouterr().err


def test_optional_notification_probe_is_inert_when_bindings_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> None:
        raise ImportError("missing distribution binding")

    monkeypatch.setattr(desktop.importlib, "import_module", missing)
    assert desktop.prerequisites("server")["notifications"] is False
    assert desktop.prerequisites("client")["notifications"] is False
    with pytest.raises(ValueError):
        desktop.prerequisites("unowned")
    with pytest.raises(ValueError):
        desktop.probe_argv("unowned")


def test_failed_service_reaps_only_recorded_processes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    owned = subprocess.Popen(["/bin/sleep", "30"])
    unrelated = subprocess.Popen(["/bin/sleep", "30"])
    ended = subprocess.Popen(["/bin/true"])
    ended.wait()
    services = desktop.DesktopServices()
    services.processes = [owned, ended]
    try:
        services.check()
        assert owned.poll() is not None
        assert unrelated.poll() is None
        assert not services.processes
        assert "service exited" in capsys.readouterr().err
    finally:
        services.close()
        unrelated.terminate()
        unrelated.wait()


def test_start_timeout_closes_a_real_foreground_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    helper = tmp_path / "service"
    helper.write_text("#!/bin/sh\nexec sleep 30\n", encoding="ascii")
    helper.chmod(0o700)
    monkeypatch.setattr(desktop, "has_owner", lambda _bus, _name: False)
    monkeypatch.setattr(desktop, "SERVICE_TIMEOUT", 0.05)
    services = desktop.DesktopServices()
    try:
        with pytest.raises(RuntimeError, match="did not become ready"):
            services._start(str(helper), "org.elsewindow.Fixture", dict(os.environ))
        process = services.processes[0]
        assert os.getpgid(process.pid) == os.getpgrp()
        services.close()
        assert process.poll() is not None
    finally:
        services.close()
