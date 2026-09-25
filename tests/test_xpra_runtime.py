# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Isolated Xpra environment preparation, integrity and interpreter boundaries."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from elsewindow import cli, machine
from elsewindow import xpra_runtime as runtime
from elsewindow.config import XpraConfig


def _packages(prefix: Path) -> None:
    """A tiny installed-file fixture; real PyPI wheels are covered by smoke/live."""
    site = next((prefix / "lib").glob("python*/site-packages"))
    _lock, pins = runtime._requirements()
    for name, version in pins.items():
        package = "OpenGL" if name == "pyopengl" else "OpenGL_accelerate"
        metadata = f"{name.replace('-', '_')}-{version}.dist-info"
        files = {
            f"{package}/__init__.py": f"__version__ = {version!r}\n",
            f"{package}/data, with spaces.txt": "fixture data\n",
            f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        }
        files[f"{package}/{'GL' if name == 'pyopengl' else 'formathandler'}.py"] = (
            "# fixture\n"
        )
        records = []
        for relative, content in files.items():
            path = site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode()
            path.write_bytes(data)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            records.append((relative, f"sha256={digest}", str(len(data))))
        records.append((f"{metadata}/RECORD", "", ""))
        inventory = io.StringIO(newline="")
        csv.writer(inventory).writerows(records)
        (site / metadata / "RECORD").write_text(inventory.getvalue(), encoding="utf-8")


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[list[str]]]:
    system = Path(sys._base_executable)
    monkeypatch.setattr(runtime, "SYSTEM_PYTHON", system)
    xpra = tmp_path / "system xpra"
    xpra.write_text(
        f"#! {system}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    xpra.chmod(0o755)
    directory = tmp_path / "private venv"
    monkeypatch.setenv(runtime.DIRECTORY_VARIABLE, str(directory))
    calls: list[list[str]] = []
    original = runtime._run

    def run(arguments: list[str], *, purpose: str, timeout: float = 30) -> bytes:
        calls.append(arguments)
        if "pip" in arguments:
            if "install" in arguments and "--no-build-isolation" in arguments:
                _packages(Path(arguments[0]).parent.parent)
            return b""
        if "venv" in arguments:
            arguments = [*arguments[:-1], "--without-pip", arguments[-1]]
        return original(arguments, purpose=purpose, timeout=timeout)

    monkeypatch.setattr(runtime, "_run", run)
    return xpra, directory, calls


def test_prepare_isolated_env_preserves_argv_and_revalidates_without_installs(
    setup: tuple[Path, Path, list[list[str]]],
) -> None:
    xpra, directory, calls = setup
    launcher = runtime.prepare(xpra)
    assert launcher == directory / "bin/xpra"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert "--system-site-packages" in next(call for call in calls if "venv" in call)
    install = next(call for call in calls if "install" in call)
    assert {
        "--require-hashes",
        "--only-binary=:all:",
        "--ignore-installed",
        "--no-deps",
    }.issubset(install)
    accelerator = next(call for call in calls if "--no-build-isolation" in call)
    assert {"--require-hashes", "--only-binary=pyopengl", "--no-cache-dir"}.issubset(
        accelerator
    )
    removal = next(call for call in calls if "uninstall" in call)
    assert {"pip", *runtime._requirements(runtime.BUILD_LOCK_PATH)[1]}.issubset(removal)
    completed = subprocess.run(
        [str(launcher), "a b", "", "semi;colon", "$(false)"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == ["a b", "", "semi;colon", "$(false)"]
    calls.clear()
    assert runtime.prepared_launcher(xpra) == launcher
    assert runtime.prepare(xpra) == launcher
    assert all("pip" not in call and "venv" not in call for call in calls)


@pytest.mark.parametrize("mode", (0o700, 0o750, 0o755, 0o770, 0o777))
def test_prepare_and_startup_accept_shared_permissions_and_mapped_owner(
    setup: tuple[Path, Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    xpra, directory, calls = setup
    launcher = runtime.prepare(xpra)
    directory.chmod(mode)
    mapped_uid = directory.stat().st_uid + 1
    monkeypatch.setattr(runtime.os, "getuid", lambda: mapped_uid)
    calls.clear()
    assert runtime.prepared_launcher(xpra) == launcher
    assert runtime.prepare(xpra) == launcher
    assert all("pip" not in call and "venv" not in call for call in calls)


@pytest.mark.parametrize("alias", ("file", "directory"))
def test_setup_diagnosis_and_session_share_symlinked_xpra(
    setup: tuple[Path, Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    alias: str,
) -> None:
    xpra, directory, calls = setup
    commands = directory.parent / "commands"
    commands.mkdir()
    if alias == "file":
        (commands / "xpra").symlink_to(xpra)
    else:
        (commands / "xpra").write_bytes(xpra.read_bytes())
        (commands / "xpra").chmod(0o755)
        linked = commands.with_name("linked commands")
        linked.symlink_to(commands, target_is_directory=True)
        commands = linked
    monkeypatch.setenv("PATH", str(commands), prepend=os.pathsep)
    monkeypatch.setattr(cli, "_diagnose_optional", lambda: None)
    sessions: list[XpraConfig] = []

    async def started(config: XpraConfig) -> int:
        sessions.append(config)
        return 0

    monkeypatch.setattr(cli, "_run_with_signals", started)
    assert cli.main(["--prepare-xpra"]) == 0
    assert "environment is ready" in capsys.readouterr().out
    calls.clear()
    assert cli.main(["--diagnose"]) == 0
    assert "xpra-environment: verified" in capsys.readouterr().out
    assert cli.main(["--ssh-alias", "workstation", "--persistent", "--", "xterm"]) == 0
    assert len(sessions) == 1
    assert sessions[0].xpra_path == directory / "bin/xpra"
    assert all("pip" not in call and "venv" not in call for call in calls)


@pytest.mark.parametrize("operation", ("startup", "setup"))
def test_validation_timeout_preserves_environment_without_reinstall(
    setup: tuple[Path, Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    xpra, directory, calls = setup
    runtime.prepare(xpra)
    state = (directory / runtime.STATE_NAME).read_bytes()
    original = runtime.subprocess.run

    def slow(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if arguments[-2] == runtime.PROBE:
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
        return original(arguments, **kwargs)

    monkeypatch.setattr(runtime.subprocess, "run", slow)
    calls.clear()
    validate = runtime.prepared_launcher if operation == "startup" else runtime.prepare
    with pytest.raises(runtime.XpraRuntimeError, match="timed out") as raised:
        validate(xpra)
    assert "missing or stale" not in str(raised.value)
    assert runtime.SETUP_HINT not in str(raised.value)
    assert (directory / runtime.STATE_NAME).read_bytes() == state
    assert all("pip" not in call and "venv" not in call for call in calls)
    assert not list(directory.parent.glob(f".{directory.name}-*"))


@pytest.mark.parametrize(
    "changed",
    (
        "lock",
        "build-lock",
        "module",
        "missing-file",
        "launcher",
        "python",
        "record",
        "extra-package",
    ),
)
def test_startup_rejects_current_filesystem_changes_without_repair(
    setup: tuple[Path, Path, list[list[str]]], changed: str
) -> None:
    xpra, directory, calls = setup
    runtime.prepare(xpra)
    if changed == "lock":
        (directory / runtime.LOCK_PATH.name).write_text("changed\n", encoding="utf-8")
    elif changed == "build-lock":
        (directory / runtime.BUILD_LOCK_PATH.name).write_text(
            "changed\n", encoding="utf-8"
        )
    elif changed == "module":
        module = next(directory.glob("lib/python*/site-packages/OpenGL/__init__.py"))
        module.write_text(
            "raise RuntimeError('must not import a modified module')\n",
            encoding="utf-8",
        )
    elif changed == "missing-file":
        next(
            directory.glob("lib/python*/site-packages/OpenGL/data, with spaces.txt")
        ).unlink()
    elif changed == "launcher":
        (directory / "bin/xpra").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    elif changed == "record":
        (directory / runtime.STATE_NAME).write_text("[]", encoding="utf-8")
    elif changed == "python":
        record = json.loads((directory / runtime.STATE_NAME).read_bytes())
        record["python"]["abi"] = "other-abi"
        (directory / runtime.STATE_NAME).write_text(
            json.dumps(record), encoding="utf-8"
        )
    else:
        site = next(directory.glob("lib/python*/site-packages"))
        extra = site / "unreviewed-1.dist-info"
        extra.mkdir()
        (extra / "METADATA").write_text(
            "Name: unreviewed\nVersion: 1\n", encoding="utf-8"
        )
    calls.clear()
    with pytest.raises(runtime.XpraRuntimeError, match="missing or stale"):
        runtime.prepared_launcher(xpra)
    assert all("pip" not in call and "venv" not in call for call in calls)


def test_failed_repair_keeps_previous_owned_environment(
    setup: tuple[Path, Path, list[list[str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    xpra, directory, _calls = setup
    runtime.prepare(xpra)
    (directory / runtime.LOCK_PATH.name).write_text("stale\n", encoding="utf-8")
    original = runtime._run

    def fail(arguments: list[str], **kwargs: object) -> bytes:
        if "pip" in arguments:
            raise runtime.XpraRuntimeError("wheel download failed")
        return original(arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime, "_run", fail)
    with pytest.raises(runtime.XpraRuntimeError, match="wheel download failed"):
        runtime.prepare(xpra)
    assert (directory / runtime.LOCK_PATH.name).read_text(encoding="utf-8") == "stale\n"
    assert not list(directory.parent.glob(f".{directory.name}-*"))


@pytest.mark.parametrize("mode", (0o700, 0o777))
def test_successful_repair_replaces_only_owned_environment(
    setup: tuple[Path, Path, list[list[str]]], mode: int
) -> None:
    xpra, directory, _calls = setup
    runtime.prepare(xpra)
    directory.chmod(mode)
    unrelated = directory.parent / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    (directory / runtime.LOCK_PATH.name).write_text("stale\n", encoding="utf-8")
    assert runtime.prepare(xpra) == directory / "bin/xpra"
    assert runtime.prepared_launcher(xpra).is_file()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not list(directory.parent.glob(f".{directory.name}-*"))


@pytest.mark.parametrize("kind", ("foreign", "symlink", "unrecorded-shared"))
def test_prepare_never_replaces_unowned_directories(
    setup: tuple[Path, Path, list[list[str]]], kind: str
) -> None:
    xpra, directory, calls = setup
    foreign = directory.parent / "foreign"
    foreign.mkdir(mode=0o700)
    marker = foreign / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    if kind == "symlink":
        directory.symlink_to(foreign)
    else:
        directory.mkdir(mode=0o700 if kind == "foreign" else 0o755)
    with pytest.raises(runtime.XpraRuntimeError):
        runtime.prepare(xpra)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert all("pip" not in call and "venv" not in call for call in calls)


def test_xdg_defaults_and_unsafe_explicit_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(runtime.DIRECTORY_VARIABLE, raising=False)
    monkeypatch.setattr(runtime, "environment_key", lambda: "machine-user-key")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert (
        runtime.runtime_directory()
        == tmp_path / "elsewindow/machine-user-key/xpra-venv"
    )
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert (
        runtime.runtime_directory()
        == Path.home() / ".local/share/elsewindow/machine-user-key/xpra-venv"
    )
    for value in ("", "relative", "/", str(Path.home()), str(Path.cwd())):
        monkeypatch.setenv(runtime.DIRECTORY_VARIABLE, value)
        with pytest.raises(runtime.XpraRuntimeError, match="dedicated absolute"):
            runtime.runtime_directory()


def test_xdg_default_requires_machine_identity_but_explicit_path_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> str:
        raise runtime.MachineIdentityError("machine identity unavailable")

    monkeypatch.setattr(runtime, "environment_key", unavailable)
    monkeypatch.delenv(runtime.DIRECTORY_VARIABLE, raising=False)
    with pytest.raises(runtime.XpraRuntimeError, match="machine identity unavailable"):
        runtime.runtime_directory()
    monkeypatch.setenv(runtime.DIRECTORY_VARIABLE, str(tmp_path / "explicit"))
    assert runtime.runtime_directory() == tmp_path / "explicit"


def test_shared_xdg_setup_never_reuses_or_repairs_another_machine_environment(
    setup: tuple[Path, Path, list[list[str]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xpra, _directory, calls = setup
    monkeypatch.delenv(runtime.DIRECTORY_VARIABLE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "shared-data"))
    identity = tmp_path / "machine-id"
    monkeypatch.setattr(machine, "MACHINE_ID_FILE", identity)
    identity.write_text("0123456789abcdef" * 2, encoding="ascii")
    first = runtime.prepare(xpra)
    first_state = first.parent.parent / runtime.STATE_NAME
    first_bytes = first_state.read_bytes()
    identity.write_text("fedcba9876543210" * 2, encoding="ascii")
    calls.clear()
    with pytest.raises(runtime.XpraRuntimeError, match="missing or stale"):
        runtime.prepared_launcher(xpra)
    assert not calls
    second = runtime.prepare(xpra)
    assert first != second
    assert runtime.prepared_launcher(xpra) == second
    identity.write_text("0123456789abcdef" * 2, encoding="ascii")
    calls.clear()
    assert runtime.prepare(xpra) == first
    assert all("pip" not in call and "venv" not in call for call in calls)
    assert first_state.read_bytes() == first_bytes
    assert second.is_file()


def test_frozen_subprocess_environment_restores_host_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from elsewindow.journal import xpra_environment

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "foreign")
    monkeypatch.setenv("LD_LIBRARY_PATH", "frozen-private")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "host-libraries")
    environment = runtime.system_environment()
    assert environment["LD_LIBRARY_PATH"] == "host-libraries"
    assert xpra_environment("warning")["LD_LIBRARY_PATH"] == "host-libraries"
    assert (
        not {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"}
        & environment.keys()
    )
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in runtime.system_environment()
    assert "LD_LIBRARY_PATH" not in xpra_environment("warning")


def test_setup_ignores_inherited_pip_destinations_and_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "PIP_TARGET",
        "PIP_PREFIX",
        "PIP_USER",
        "PIP_REQUIREMENT",
        "PIP_CONFIG_FILE",
    ):
        monkeypatch.setenv(name, "unowned")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", "verified-artifacts")
    environment = runtime.system_environment()
    assert (
        not {"PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_REQUIREMENT"}
        & environment.keys()
    )
    assert environment["PIP_CONFIG_FILE"] == "/dev/null"
    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["PIP_FIND_LINKS"] == "verified-artifacts"


def test_setup_is_explicit_and_cannot_start_a_session(
    setup: tuple[Path, Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xpra, _directory, _calls = setup
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: str(xpra) if name == "xpra" else None
    )
    assert cli.main(["--prepare-xpra"]) == 0
    assert "environment is ready" in capsys.readouterr().out
    for arguments in (
        ["--prepare-xpra", "--diagnose"],
        ["--prepare-xpra", "--ssh-alias", "workstation"],
        ["--prepare-xpra", "--", "xterm"],
    ):
        with pytest.raises(SystemExit):
            cli.main(arguments)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["--prepare-xpra"]) == 1
    assert "install the supported system Xpra" in capsys.readouterr().err


def test_system_entry_and_locked_pair_are_required(
    setup: tuple[Path, Path, list[list[str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    xpra, directory, calls = setup
    xpra.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    with pytest.raises(runtime.XpraRuntimeError, match="distribution entry point"):
        runtime.prepare(xpra)
    assert not calls
    invalid = directory.parent / "invalid-lock.txt"
    invalid.write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(runtime, "LOCK_PATH", invalid)
    with pytest.raises(runtime.XpraRuntimeError, match="lock is invalid"):
        runtime._requirements()


@pytest.mark.parametrize(
    "suffix",
    (
        "--extra-index-url https://example.invalid\n",
        "unexpected==1 \\\n",
        "    --hash=sha256:invalid\n",
    ),
)
def test_bundled_lock_rejects_unrecognized_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    path = tmp_path / "requirements-xpra.txt"
    path.write_bytes(runtime.LOCK_PATH.read_bytes() + suffix.encode())
    monkeypatch.setattr(runtime, "LOCK_PATH", path)
    with pytest.raises(runtime.XpraRuntimeError, match="invalid|matched|hashes"):
        runtime._requirements()


def test_activation_failure_restores_previous_owned_environment(
    setup: tuple[Path, Path, list[list[str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    xpra, directory, _calls = setup
    runtime.prepare(xpra)
    (directory / runtime.LOCK_PATH.name).write_text("stale\n", encoding="utf-8")

    def fail(*args: object) -> Path:
        raise runtime.XpraRuntimeError("activation failed")

    monkeypatch.setattr(runtime, "prepared_launcher", fail)
    with pytest.raises(runtime.XpraRuntimeError, match="activation failed"):
        runtime.prepare(xpra)
    assert (directory / runtime.LOCK_PATH.name).read_text(encoding="utf-8") == "stale\n"
    assert not list(directory.parent.glob(f".{directory.name}-*"))
