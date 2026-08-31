"""Shell launcher regression tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FAKE_RUNTIME_PYTHON = r"""#!/usr/bin/env bash
set -euo pipefail
[[ ${PYTHONPATH:-} == ${EXPECTED_PYTHONPATH:?missing expected source path} ]]
if [[ ${1:-} == -c ]]; then
    command=${2:-}
    if [[ $command == 'import elsewindow' ]]; then
        [[ ${FAKE_ELSEWINDOW_INVALID:-0} != 1 ]]
    elif [[ $command == 'import ssh_wrapper' ]]; then
        [[ ${FAKE_WRAPPER_INVALID:-0} != 1 ]]
    else
        [[ ${FAKE_RUNTIME_INVALID:-0} != 1 ]]
    fi
    exit
fi
[[ ${1:-} == -m && ${2:-} == elsewindow ]]
shift 2
printf 'argc=%s\n' "$#"
printf '<%s>\n' "$@"
"""

FORBIDDEN_SYSTEM_PYTHON = r"""#!/usr/bin/env bash
printf '%s\n' invoked >> "${FORBIDDEN_PYTHON_LOG:?missing invocation log}"
exit 99
"""


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def isolated_launcher(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    source = Path(__file__).resolve().parents[1] / "bin/elsewindow"
    repository = tmp_path / "repository"
    fake_bin = tmp_path / "fake-bin"
    repository.mkdir()
    fake_bin.mkdir()
    launcher = repository / "bin/elsewindow"
    launcher.parent.mkdir()
    launcher.write_bytes(source.read_bytes())
    launcher.chmod(source.stat().st_mode & 0o777)
    package = repository / "elsewindow"
    package.mkdir()
    (package / "__main__.py").write_text("# module entry\n", encoding="utf-8")
    (repository / "requirements.txt").write_text("dependency==1\n", encoding="utf-8")
    write_executable(fake_bin / "python3", FORBIDDEN_SYSTEM_PYTHON)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FORBIDDEN_PYTHON_LOG": str(tmp_path / "python.log"),
            "EXPECTED_PYTHONPATH": str(repository),
            "PYTHONPATH": str(tmp_path / "hostile-import-path"),
        }
    )
    return launcher, repository, environment


def install_fake_runtime(repository: Path) -> None:
    runtime = repository / "venv-runtime"
    (runtime / "bin").mkdir(parents=True)
    write_executable(runtime / "bin/python", FAKE_RUNTIME_PYTHON)
    (runtime / ".requirements.txt").write_bytes(
        (repository / "requirements.txt").read_bytes()
    )


def run_launcher(
    launcher: Path,
    cwd: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [launcher, *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_launcher_rejects_missing_entry_point_runtime_and_lock_state(
    tmp_path: Path,
) -> None:
    launcher, repository, environment = isolated_launcher(tmp_path)
    (repository / "elsewindow/__main__.py").unlink()
    missing_entry = run_launcher(launcher, tmp_path, environment)
    assert "Module entry point is missing" in missing_entry.stderr

    (repository / "elsewindow/__main__.py").write_text(
        "# module entry\n", encoding="utf-8"
    )
    missing_runtime = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is not installed" in missing_runtime.stderr
    install_fake_runtime(repository)
    (repository / "venv-runtime/.requirements.txt").unlink()
    missing_state = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is stale" in missing_state.stderr
    assert not (tmp_path / "python.log").exists()


def test_launcher_rejects_stale_runtime(tmp_path: Path) -> None:
    launcher, repository, environment = isolated_launcher(tmp_path)
    install_fake_runtime(repository)
    marker = repository / "venv-runtime/.requirements.txt"
    old_marker = marker.read_bytes()
    (repository / "requirements.txt").write_text("dependency==2\n", encoding="utf-8")
    stale = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is stale" in stale.stderr
    assert marker.read_bytes() == old_marker

    assert marker.is_file()
    assert not (tmp_path / "python.log").exists()


def test_launcher_rejects_each_invalid_owned_package(tmp_path: Path) -> None:
    launcher, repository, environment = isolated_launcher(tmp_path)
    install_fake_runtime(repository)
    for variable, message in (
        ("FAKE_ELSEWINDOW_INVALID", "Elsewindow source is invalid"),
        ("FAKE_WRAPPER_INVALID", "ssh-wrapper package is invalid"),
    ):
        selected = environment.copy()
        selected[variable] = "1"
        invalid = run_launcher(launcher, tmp_path, selected)
        assert invalid.returncode == 1
        assert message in invalid.stderr
    assert not (tmp_path / "python.log").exists()


def test_launcher_validates_owned_packages_and_preserves_argv(tmp_path: Path) -> None:
    launcher, repository, environment = isolated_launcher(tmp_path)
    install_fake_runtime(repository)
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    completed = run_launcher(
        launcher,
        unrelated,
        environment,
        "--option",
        "value with spaces",
        "",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "argc=3\n<--option>\n<value with spaces>\n<>\n"
    assert completed.stderr == ""
    assert not (tmp_path / "python.log").exists()
