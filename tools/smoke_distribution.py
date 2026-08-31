# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Clean-install, exercise, and strictly type-check a wheel or sdist."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SmokeError(RuntimeError):
    """A distribution failed clean installed-package smoke."""


def _executable(path: Path, *, label: str) -> Path:
    candidate: Path
    if not path.is_absolute() and len(path.parts) == 1:
        located = shutil.which(str(path))
        if located is None:
            raise SmokeError(f"{label} is not available: {path}")
        candidate = Path(located)
    else:
        candidate = path.absolute()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise SmokeError(f"{label} is not executable: {candidate}")
    return candidate


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != expected:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise SmokeError(
            f"command returned {completed.returncode}, expected {expected}: {detail}"
        )
    return completed


def _extract_sdist(path: Path, destination: Path) -> Path:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        roots = {
            Path(member.name).parts[0] for member in members if Path(member.name).parts
        }
        if len(roots) != 1 or any(
            member.issym() or member.islnk() or member.isdev() for member in members
        ):
            raise SmokeError("sdist is not one safe regular source tree")
        archive.extractall(destination, filter="data")
    root = destination / roots.pop()
    if not root.is_dir():
        raise SmokeError("sdist root directory is missing")
    return root


def _dependency_wheel(directory: Path) -> Path:
    matches = tuple(directory.glob("ssh_wrapper-0.1.0-*.whl"))
    if len(matches) != 1 or not matches[0].is_file():
        raise SmokeError("expected one ssh-wrapper 0.1.0 dependency wheel")
    return matches[0].resolve()


def smoke(
    *,
    kind: str,
    dist_dir: Path,
    dependency_dist: Path,
    python: Path,
    mypy: Path,
    build_python: Path | None,
) -> None:
    """Exercise one artifact kind entirely outside the source tree."""
    version = (ROOT / ".version").read_text(encoding="utf-8").strip()
    python = _executable(python, label="Python interpreter")
    mypy = _executable(mypy, label="mypy")
    wrapper = _dependency_wheel(dependency_dist.resolve())
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    environment.pop("MYPYPATH", None)

    with tempfile.TemporaryDirectory(prefix=f"elsewindow-{kind}-smoke-") as raw:
        root = Path(raw)
        hostile = root / "hostile-pythonpath"
        hostile.mkdir()
        (hostile / "elsewindow.py").write_text(
            "raise RuntimeError('hostile module imported')\n", encoding="utf-8"
        )
        environment["PYTHONPATH"] = str(hostile)
        artifact: Path
        if kind == "wheel":
            artifact = dist_dir.resolve() / f"elsewindow-{version}-py3-none-any.whl"
        elif kind == "sdist":
            if build_python is None:
                raise SmokeError("sdist smoke requires --build-python")
            builder = _executable(build_python, label="build interpreter")
            source = dist_dir.resolve() / f"elsewindow-{version}.tar.gz"
            source_root = _extract_sdist(source, root / "source")
            built = root / "built"
            built.mkdir()
            _run(
                [
                    str(builder),
                    "-m",
                    "build",
                    "--no-isolation",
                    "--wheel",
                    "--outdir",
                    str(built),
                    str(source_root),
                ],
                cwd=root,
                environment=environment,
            )
            wheels = tuple(built.glob("*.whl"))
            if len(wheels) != 1 or wheels[0].name != (
                f"elsewindow-{version}-py3-none-any.whl"
            ):
                raise SmokeError("sdist produced an unexpected wheel inventory")
            artifact = wheels[0]
        else:
            raise SmokeError(f"unsupported distribution kind: {kind}")
        if not artifact.is_file():
            raise SmokeError(f"distribution is missing: {artifact.name}")

        virtualenv = root / "environment"
        _run(
            [str(python), "-m", "venv", str(virtualenv)],
            cwd=root,
            environment=environment,
        )
        installed_python = virtualenv / "bin/python"
        _run(
            [
                str(installed_python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-index",
                "--no-deps",
                str(wrapper),
                str(artifact.resolve()),
            ],
            cwd=root,
            environment=environment,
        )
        _run(
            [str(installed_python), "-m", "pip", "check"],
            cwd=root,
            environment=environment,
        )

        probe = textwrap.dedent(
            f"""
            import importlib.metadata
            import importlib.resources
            from pathlib import Path

            import elsewindow
            import ssh_wrapper
            from elsewindow.cli import build_parser
            from elsewindow.live_config import load_live_cli, load_network_profiles

            assert elsewindow.__version__ == {version!r}
            assert importlib.metadata.version("elsewindow") == {version!r}
            assert importlib.metadata.version("ssh-wrapper") == "0.1.0"
            assert Path(elsewindow.__file__).resolve().is_relative_to(Path(sys.prefix))
            assert Path(ssh_wrapper.__file__).resolve().is_relative_to(Path(sys.prefix))
            package = importlib.resources.files("elsewindow")
            assert package.joinpath("py.typed").is_file()
            assert package.joinpath("live-cli.yml").read_bytes()
            assert package.joinpath("profiles.yml").read_bytes()
            assert set(load_live_cli()) == {{"server", "client"}}
            default, profiles = load_network_profiles()
            assert default in profiles
            parsed = build_parser().parse_args(
                ["--ssh-alias", "example", "--", "xterm"]
            )
            assert parsed.ssh_alias == "example" and parsed.application == ["--", "xterm"]
            """
        ).replace(
            "import importlib.metadata\n", "import importlib.metadata\nimport sys\n"
        )
        _run(
            [str(installed_python), "-I", "-c", probe],
            cwd=root,
            environment=environment,
        )
        module_version = _run(
            [str(installed_python), "-I", "-m", "elsewindow", "--version"],
            cwd=root,
            environment=environment,
        )
        if module_version.stdout.strip() != f"elsewindow {version}":
            raise SmokeError("installed module reports the wrong version")
        command = virtualenv / "bin/elsewindow"
        console_environment = environment.copy()
        console_environment.pop("PYTHONPATH", None)
        version_output = _run(
            [str(command), "--version"], cwd=root, environment=console_environment
        )
        if version_output.stdout.strip() != f"elsewindow {version}":
            raise SmokeError("installed console reports the wrong version")
        help_output = _run(
            [str(command), "--help"], cwd=root, environment=console_environment
        )
        if not all(
            option in help_output.stdout
            for option in ("--encoding-profile", "--network-profile", "--diagnose")
        ):
            raise SmokeError("installed console help is incomplete")
        missing = root / "missing-path"
        missing.mkdir()
        missing_environment = console_environment.copy()
        missing_environment["PATH"] = str(missing)
        diagnostic = _run(
            [str(command), "--ssh-alias", "example", "--", "xterm"],
            cwd=root,
            environment=missing_environment,
            expected=2,
        )
        if (
            "elsewindow: error: required command not found on PATH: ssh"
            not in diagnostic.stderr
        ):
            raise SmokeError("missing OpenSSH diagnostic or error prefix differs")

        consumer = root / "consumer.py"
        consumer.write_text(
            textwrap.dedent(
                """
                from elsewindow import __version__
                from elsewindow.config import XpraConfig
                from elsewindow.live_config import NetworkProfile, network_profile

                version: str = __version__
                profile: NetworkProfile = network_profile("gigabit_lan")
                config_type: type[XpraConfig] = XpraConfig
                """
            ),
            encoding="utf-8",
        )
        _run(
            [
                str(mypy),
                "--strict",
                "--python-executable",
                str(installed_python),
                str(consumer),
            ],
            cwd=root,
            environment=console_environment,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("wheel", "sdist"), required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--dependency-dist", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--mypy", type=Path, required=True)
    parser.add_argument("--build-python", type=Path)
    arguments = parser.parse_args()
    try:
        smoke(
            kind=arguments.kind,
            dist_dir=arguments.dist_dir,
            dependency_dist=arguments.dependency_dist,
            python=arguments.python,
            mypy=arguments.mypy,
            build_python=arguments.build_python,
        )
    except (OSError, SmokeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"{arguments.kind} smoke failed: {error}", file=sys.stderr)
        return 1
    print(f"{arguments.kind} clean-install and strict-type smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
