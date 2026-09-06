# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Cache verified Xpra setup artifacts for the actual system interpreter."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from elsewindow import xpra_runtime as runtime


def download(destination: Path) -> None:
    """Include a source archive so offline smoke exercises native compilation."""
    destination.mkdir(parents=True, exist_ok=True)
    lock, _pins = runtime._requirements()
    build_lock, _builder = runtime._requirements(runtime.BUILD_LOCK_PATH)
    with tempfile.TemporaryDirectory(prefix="elsewindow-xpra-download-") as temporary:
        stage = Path(temporary)
        python = runtime._bootstrap(stage, build_lock)
        staged_lock = stage / runtime.LOCK_PATH.name
        staged_lock.write_bytes(lock)
        for requirement, policy in (
            (stage / runtime.BUILD_LOCK_PATH.name, ["--only-binary=:all:"]),
            (staged_lock, ["--only-binary=pyopengl"]),
            (
                staged_lock,
                ["--only-binary=pyopengl", "--no-binary=pyopengl-accelerate"],
            ),
        ):
            runtime._pip(
                python,
                [
                    "download",
                    "--quiet",
                    "--require-hashes",
                    "--no-deps",
                    "--no-build-isolation",
                    *policy,
                    "--dest",
                    str(destination),
                    "--requirement",
                    str(requirement),
                ],
                purpose="download the hash-locked Xpra setup artifacts",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        download(parser.parse_args().destination.resolve())
    except (OSError, ValueError, runtime.XpraRuntimeError) as error:
        print(f"Xpra dependency download: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
