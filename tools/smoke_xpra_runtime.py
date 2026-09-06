# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Shared installed-wheel and frozen-command Xpra environment acceptance."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def smoke_xpra_runtime(
    command: list[str], *, root: Path, wheels: Path, environment: dict[str, str]
) -> None:
    """Use real PyPI wheels and a source build without network or a checkout."""
    binaries = root / "xpra-fixture"
    binaries.mkdir()
    xpra = binaries / "xpra"
    xpra.write_text(
        "#! /usr/bin/python3\n"
        "import json, sys, OpenGL, OpenGL_accelerate\n"
        "import importlib.util\n"
        "if importlib.util.find_spec('numpy') is not None:\n"
        "    import OpenGL_accelerate.numpy_formathandler\n"
        "print(json.dumps({'args': sys.argv[1:], 'prefix': sys.prefix, "
        "'pyopengl': OpenGL.__version__, 'accelerate': OpenGL_accelerate.__version__}))\n",
        encoding="utf-8",
    )
    xpra.chmod(0o755)
    directory = root / "prepared Xpra"
    selected = environment.copy()
    selected.update(
        PATH=str(binaries) + os.pathsep + os.environ.get("PATH", ""),
        ELSEWINDOW_XPRA_VENV=str(directory),
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(wheels),
    )

    def run(
        arguments: list[str], expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            arguments,
            cwd=root,
            env=selected,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"Xpra setup smoke returned {result.returncode}, expected {expected}: {result.stderr.strip()}"
            )
        return result

    absent = run([*command, "--diagnose"], 1)
    if "xpra_environment_unprepared" not in absent.stderr or directory.exists():
        raise RuntimeError(
            "diagnosis did not reject the missing environment without creating it"
        )
    run([*command, "--prepare-xpra"])
    verified = run([*command, "--diagnose"])
    if "xpra-environment: verified" not in verified.stdout:
        raise RuntimeError(
            "installed artifact did not verify its prepared Xpra environment"
        )
    observation = json.loads(
        run([str(directory / "bin/xpra"), "check-argv", "with spaces", ""]).stdout
    )
    if (
        observation["args"] != ["check-argv", "with spaces", ""]
        or observation["prefix"] != str(directory)
        or observation["pyopengl"] != observation["accelerate"]
    ):
        raise RuntimeError("prepared launcher used the wrong Python, modules, or argv")
    source = next(directory.glob("lib/python*/site-packages/OpenGL/__init__.py"))
    source.write_bytes(source.read_bytes() + b"\n# changed installed bytes\n")
    stale = run([*command, "--diagnose"], 1)
    if "xpra_environment_unprepared" not in stale.stderr:
        raise RuntimeError("installed artifact trusted a stale Xpra environment")
    selected["PIP_NO_BINARY"] = "pyopengl-accelerate"
    run([*command, "--prepare-xpra"])
    run([*command, "--diagnose"])
    run([str(directory / "bin/xpra"), "check-source-build"])
