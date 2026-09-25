# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Explicit preparation and read-only validation of the local Xpra Python env."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .machine import MachineIdentityError, environment_key

LOCK_PATH = Path(__file__).with_name("requirements-xpra.txt")
BUILD_LOCK_PATH = Path(__file__).with_name("requirements-xpra-build.txt")
SYSTEM_PYTHON = Path("/usr/bin/python3")
DIRECTORY_VARIABLE = "ELSEWINDOW_XPRA_VENV"
STATE_NAME = ".elsewindow-xpra-runtime.json"
VALIDATION_TIMEOUT = 180
SETUP_HINT = (
    "prepare with 'make runtime-venv' from the checkout or 'elsewindow --prepare-xpra'"
)
PROBE = r"""
import base64
import csv
import hashlib
import importlib
import importlib.metadata as metadata
import io
import json
import pathlib
import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from functools import partial

def verify_file(distribution, row):
    relative, recorded_hash, _size = row
    if not recorded_hash:
        assert relative.endswith(("/RECORD", ".pyc")), "unhashed package file"
        return
    path = pathlib.Path(distribution.locate_file(relative))
    assert path.resolve().is_relative_to(prefix), "package file escapes venv"
    mode, separator, expected_hash = recorded_hash.partition("=")
    assert mode == "sha256" and separator, "unsupported installed file hash"
    digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest())
    assert digest.rstrip(b"=").decode() == expected_hash, "installed file changed"

identity = {
    "executable": str(pathlib.Path(sys._base_executable).resolve()),
    "version": sys.version,
    "abi": sysconfig.get_config_var("SOABI"),
}
if len(sys.argv) > 1:
    expected = json.loads(sys.argv[1])
    prefix = pathlib.Path(sys.prefix).resolve()
    local = pathlib.Path(sysconfig.get_path("purelib"))
    installed = {
        d.metadata["Name"].lower().replace("_", "-"): d
        for d in metadata.distributions(path=[str(local)])
    }
    assert set(installed) == set(expected), "unexpected local package inventory"
    # Bound simultaneous reads while overlapping shared-filesystem latency.
    with ThreadPoolExecutor(max_workers=4) as workers:
        for name, version in expected.items():
            distribution = installed[name]
            assert distribution.version == version, "installed version differs from lock"
            # metadata.files stats every entry and silently filters out missing files.
            record = distribution.read_text("RECORD")
            assert record, "installed package has no file inventory"
            for _ in workers.map(
                partial(verify_file, distribution), csv.reader(io.StringIO(record))
            ):
                pass
    for name in ("OpenGL", "OpenGL_accelerate", "OpenGL_accelerate.formathandler"):
        module = importlib.import_module(name)
        assert pathlib.Path(module.__file__).resolve().is_relative_to(prefix), "foreign module"
    from OpenGL import GL
print(json.dumps(identity, sort_keys=True))
"""


class XpraRuntimeError(RuntimeError):
    """The isolated Xpra environment is absent, stale, or cannot be prepared."""


class XpraRuntimeTimeout(XpraRuntimeError):
    """A preparation or validation deadline expired without proving stale inputs."""


def system_environment() -> dict[str, str]:
    """Do not leak Python or PyInstaller's private libraries into system tools."""
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"):
        environment.pop(name, None)
    # Retain only pip transport/artifact selection, never inherited install
    # destinations, extra requirements, or backend settings. Hashes still bind
    # every artifact, including one obtained from an operator's private mirror.
    pip_inputs = {
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "PIP_NO_BINARY",
        "PIP_CERT",
        "PIP_CLIENT_CERT",
        "PIP_PROXY",
        "PIP_TIMEOUT",
        "PIP_RETRIES",
    }
    for name in tuple(environment):
        if name.startswith("PIP_") and name not in pip_inputs:
            environment.pop(name)
    environment["PIP_CONFIG_FILE"] = os.devnull
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", "")
        if original:
            environment["LD_LIBRARY_PATH"] = original
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    return environment


def runtime_directory() -> Path:
    """Use an explicit location or a machine-scoped persistent XDG directory."""
    configured = os.environ.get(DIRECTORY_VARIABLE)
    if configured is not None:
        path = Path(configured)
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", ""))
        if not data.is_absolute():
            data = Path.home() / ".local/share"
        try:
            key = environment_key()
        except MachineIdentityError as error:
            raise XpraRuntimeError(str(error)) from error
        path = data / "elsewindow" / key / "xpra-venv"
    return _dedicated_directory(path)


def _dedicated_directory(path: Path) -> Path:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path in (Path("/"), Path.home(), Path.cwd())
    ):
        raise XpraRuntimeError(
            "the Xpra environment location must be a dedicated absolute directory"
        )
    return path


def _requirements(path: Path | None = None) -> tuple[bytes, dict[str, str]]:
    lock = (LOCK_PATH if path is None else path).read_bytes()
    if len(lock) > 128 * 1024 or b"autogenerated by pip-compile" not in lock:
        raise XpraRuntimeError("the bundled Xpra dependency lock is invalid")
    pins: dict[str, str] = {}
    current = ""
    hashes: set[str] = set()
    for line in lock.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            digest = re.fullmatch(r"--hash=sha256:([0-9a-f]{64})(?: \\)?", line.strip())
            if not current or digest is None or digest[1] in hashes:
                raise XpraRuntimeError("the bundled Xpra lock has an invalid hash")
            hashes.add(digest[1])
            continue
        pin = re.fullmatch(r"([a-z0-9-]+)==([^\s;\\]+) \\", line)
        if pin is None or pin[1] in pins or (current and not hashes):
            raise XpraRuntimeError("the bundled Xpra lock has an invalid requirement")
        current = pin[1]
        pins[current] = pin[2]
        hashes = set()
    if not hashes:
        raise XpraRuntimeError("the bundled Xpra lock has no package hashes")
    if path is None and (
        set(pins) != {"pyopengl", "pyopengl-accelerate"} or len(set(pins.values())) != 1
    ):
        raise XpraRuntimeError(
            "the bundled Xpra lock must contain a matched PyOpenGL pair"
        )
    if path is not None and set(pins) != {"cython", "numpy", "setuptools"}:
        raise XpraRuntimeError("the bundled Xpra build lock has an invalid inventory")
    return lock, pins


def _run(arguments: list[str], *, purpose: str, timeout: float = 30) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            env=system_environment(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise XpraRuntimeTimeout(
            f"timed out after {timeout:g}s while trying to {purpose}; retry the command"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise XpraRuntimeError(f"cannot {purpose}") from error
    if completed.returncode:
        raise XpraRuntimeError(f"cannot {purpose} (exit status {completed.returncode})")
    return completed.stdout


def _identity(python: Path, pins: dict[str, str] | None = None) -> dict[str, Any]:
    arguments = [str(python), "-I", "-B", "-c", PROBE]
    if pins is not None:
        arguments.append(json.dumps(pins))
    try:
        result = json.loads(
            _run(
                arguments,
                purpose="validate the Xpra Python environment",
                timeout=VALIDATION_TIMEOUT if pins is not None else 30,
            )
        )
    except (ValueError, UnicodeError) as error:
        raise XpraRuntimeError("the Xpra Python identity is invalid") from error
    if not isinstance(result, dict) or set(result) != {"executable", "version", "abi"}:
        raise XpraRuntimeError("the Xpra Python identity is invalid")
    return result


def _pip(python: Path, arguments: list[str], *, purpose: str) -> None:
    _run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "--disable-pip-version-check",
            "--no-cache-dir",
            *arguments,
        ],
        purpose=purpose,
        timeout=600,
    )


def _bootstrap(stage: Path, build_lock: bytes) -> Path:
    """Create a private interpreter with only hash-locked temporary build tools."""
    _run(
        [str(SYSTEM_PYTHON), "-I", "-m", "venv", "--system-site-packages", str(stage)],
        purpose="create a system-Python venv; ensure python3-venv is installed",
        timeout=120,
    )
    staged_lock = stage / BUILD_LOCK_PATH.name
    staged_lock.write_bytes(build_lock)
    python = stage / "bin/python"
    _pip(
        python,
        [
            "install",
            "--quiet",
            "--require-hashes",
            "--only-binary=:all:",
            "--ignore-installed",
            "--no-deps",
            "--requirement",
            str(staged_lock),
        ],
        purpose="install the locked temporary Xpra build tools; check PyPI access",
    )
    return python


def _system_identity(xpra: Path) -> dict[str, Any]:
    with xpra.open("rb") as stream:
        shebang = stream.readline(256).decode("ascii").strip()
    if not shebang.startswith("#!") or shebang[2:].split() != [str(SYSTEM_PYTHON)]:
        raise XpraRuntimeError(
            "local Xpra must be the system-Python distribution entry point"
        )
    return _identity(SYSTEM_PYTHON)


def _launcher(directory: Path, xpra: Path) -> bytes:
    command = shlex.join((str(directory / "bin/python"), "-I", str(xpra)))
    return f'#!/bin/sh\nexec {command} "$@"\n'.encode()


def _owned(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise XpraRuntimeError("the Xpra environment is not an owned directory")
    state = directory / STATE_NAME
    if state.is_symlink() or state.stat().st_size > 16 * 1024:
        raise XpraRuntimeError("the Xpra environment ownership record is invalid")
    record = json.loads(state.read_bytes())
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise XpraRuntimeError("the Xpra environment ownership record is invalid")
    return record


def prepared_launcher(xpra: Path, directory: Path | None = None) -> Path:
    """Revalidate current lock, interpreter, installed bytes and exact launcher."""
    try:
        xpra = xpra.resolve(strict=True)
        directory = (
            runtime_directory()
            if directory is None
            else _dedicated_directory(directory)
        )
        record = _owned(directory)
        lock, pins = _requirements()
        build_lock, _build_pins = _requirements(BUILD_LOCK_PATH)
        identity = _system_identity(xpra)
        expected = {
            "schema": 1,
            "lock": hashlib.sha256(lock).hexdigest(),
            "build_lock": hashlib.sha256(build_lock).hexdigest(),
            "python": identity,
            "xpra": str(xpra),
        }
        if (
            record != expected
            or (directory / LOCK_PATH.name).read_bytes() != lock
            or (directory / BUILD_LOCK_PATH.name).read_bytes() != build_lock
        ):
            raise XpraRuntimeError("the Xpra environment no longer matches its inputs")
        launcher = directory / "bin/xpra"
        if (
            launcher.is_symlink()
            or launcher.read_bytes() != _launcher(directory, xpra)
            or not os.access(launcher, os.X_OK)
        ):
            raise XpraRuntimeError("the Xpra environment launcher is invalid")
        if _identity(directory / "bin/python", pins) != identity:
            raise XpraRuntimeError("the Xpra environment Python is incompatible")
        return launcher
    except XpraRuntimeTimeout:
        raise
    except (OSError, ValueError, XpraRuntimeError) as error:
        raise XpraRuntimeError(
            f"local Xpra environment is missing or stale; {SETUP_HINT}"
        ) from error


def prepare(xpra: Path, directory: Path | None = None) -> Path:
    """Install only into a recorded project venv; never alter system packages."""
    xpra = xpra.resolve(strict=True)
    directory = (
        runtime_directory() if directory is None else _dedicated_directory(directory)
    )
    lock, pins = _requirements()
    build_lock, build_pins = _requirements(BUILD_LOCK_PATH)
    identity = _system_identity(xpra)
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        directory.parent / f".{directory.name}.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w"):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise XpraRuntimeError(
                "another Xpra environment preparation is running"
            ) from error
        if directory.exists() or directory.is_symlink():
            try:
                _owned(directory)
            except (OSError, ValueError) as error:
                raise XpraRuntimeError(
                    "refusing to replace an unowned Xpra environment directory"
                ) from error
            try:
                return prepared_launcher(xpra, directory)
            except XpraRuntimeTimeout:
                raise
            except XpraRuntimeError:
                pass
        print("Preparing the isolated local Xpra Python environment...", flush=True)
        stage = Path(
            tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent)
        )
        backup: Path | None = None
        try:
            python = _bootstrap(stage, build_lock)
            staged_lock = stage / LOCK_PATH.name
            staged_lock.write_bytes(lock)
            _pip(
                python,
                [
                    "install",
                    "--quiet",
                    "--require-hashes",
                    "--only-binary=pyopengl",
                    "--no-build-isolation",
                    "--ignore-installed",
                    "--no-deps",
                    "--requirement",
                    str(staged_lock),
                ],
                purpose=(
                    "install the locked Xpra additions; check PyPI access; "
                    "source builds require build-essential and python3-dev"
                ),
            )
            _pip(
                python,
                [
                    "uninstall",
                    "--quiet",
                    "--yes",
                    "pip",
                    *sorted(build_pins),
                ],
                purpose="remove the temporary Xpra installer and build tools",
            )
            if _identity(python, pins) != identity:
                raise XpraRuntimeError("the prepared Xpra interpreter identity changed")
            (stage / "bin/xpra").write_bytes(_launcher(directory, xpra))
            (stage / "bin/xpra").chmod(0o700)
            (stage / STATE_NAME).write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "lock": hashlib.sha256(lock).hexdigest(),
                        "build_lock": hashlib.sha256(build_lock).hexdigest(),
                        "python": identity,
                        "xpra": str(xpra),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if directory.exists():
                backup = stage.with_name(stage.name + "-previous")
                directory.rename(backup)
            try:
                stage.rename(directory)
                launcher = prepared_launcher(xpra, directory)
            except BaseException:
                if directory.exists() and not stage.exists():
                    directory.rename(stage)
                if backup is not None:
                    backup.rename(directory)
                    backup = None
                raise
            return launcher
        finally:
            # Only the unique staging and recorded backup paths are removable.
            if stage.exists():
                shutil.rmtree(stage)
            if backup is not None and backup.exists():
                shutil.rmtree(backup)
