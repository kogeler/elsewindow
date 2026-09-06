"""Shell launcher regression tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from elsewindow import machine

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
[[ ${ELSEWINDOW_XPRA_VENV:-} == ${EXPECTED_VENV_ROOT:?missing expected venv root}/venv-xpra ]]
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


@pytest.fixture
def isolated_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, str]]:
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
    identity = tmp_path / "machine-id"
    identity.write_text("0123456789abcdef" * 2 + "\n", encoding="ascii")
    machine_source = Path(machine.__file__).read_text(encoding="utf-8")
    copied_source = machine_source.replace(
        'MACHINE_ID_FILE = Path("/etc/machine-id")',
        f"MACHINE_ID_FILE = Path({str(identity)!r})",
    )
    assert copied_source != machine_source
    (package / "machine.py").write_text(copied_source, encoding="utf-8")
    # A directly executed bootstrap must not import the package or dependencies.
    (package / "__init__.py").write_text(
        "raise AssertionError('package imported before selecting venv')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(machine, "MACHINE_ID_FILE", identity)
    (repository / "requirements.txt").write_text("dependency==1\n", encoding="utf-8")
    write_executable(fake_bin / "python3", FORBIDDEN_SYSTEM_PYTHON)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FORBIDDEN_PYTHON_LOG": str(tmp_path / "python.log"),
            "EXPECTED_PYTHONPATH": str(repository),
            "EXPECTED_VENV_ROOT": str(repository / machine.repository_venv_root()),
            "PYTHONPATH": str(tmp_path / "hostile-import-path"),
        }
    )
    return launcher, repository, environment


def install_fake_runtime(repository: Path) -> None:
    runtime = repository / machine.repository_venv_root() / "venv-runtime"
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
    isolated_launcher: tuple[Path, Path, dict[str, str]],
) -> None:
    launcher, repository, environment = isolated_launcher
    (repository / "elsewindow/__main__.py").unlink()
    missing_entry = run_launcher(launcher, tmp_path, environment)
    assert "Module entry point is missing" in missing_entry.stderr

    (repository / "elsewindow/__main__.py").write_text(
        "# module entry\n", encoding="utf-8"
    )
    missing_runtime = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is not installed" in missing_runtime.stderr
    install_fake_runtime(repository)
    (
        repository / machine.repository_venv_root() / "venv-runtime/.requirements.txt"
    ).unlink()
    missing_state = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is stale" in missing_state.stderr
    assert not (tmp_path / "python.log").exists()


def test_make_clean_preserves_other_machine_and_legacy_environments(
    isolated_launcher: tuple[Path, Path, dict[str, str]],
) -> None:
    _launcher, repository, environment = isolated_launcher
    first = machine.repository_venv_root()
    roles = ("runtime", "xpra", "quality", "test", "package", "standalone", "docs")
    for role in roles:
        directory = repository / first / f"venv-{role}"
        directory.mkdir(parents=True)
        (directory / "dependency.py").write_text("owned\n", encoding="utf-8")
    machine.MACHINE_ID_FILE.write_text("fedcba9876543210" * 2, encoding="ascii")
    second = machine.repository_venv_root()
    for role in roles:
        for root in (repository / second, repository):
            directory = root / f"venv-{role}"
            directory.mkdir(parents=True)
            (directory / "dependency.py").write_text("keep\n", encoding="utf-8")
    machine.MACHINE_ID_FILE.write_text("0123456789abcdef" * 2, encoding="ascii")
    for directory in ("tests", "tools", "doc/site", ".github/scripts"):
        (repository / directory).mkdir(parents=True)
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    completed = subprocess.run(
        ["make", "--no-print-directory", "-f", str(makefile), "clean"],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for role in roles:
        assert not (repository / first / f"venv-{role}").exists()
        for root in (repository / second, repository):
            assert (root / f"venv-{role}/dependency.py").read_text(
                encoding="utf-8"
            ) == "keep\n"


def test_launcher_rejects_stale_runtime(
    tmp_path: Path, isolated_launcher: tuple[Path, Path, dict[str, str]]
) -> None:
    launcher, repository, environment = isolated_launcher
    install_fake_runtime(repository)
    marker = (
        repository / machine.repository_venv_root() / "venv-runtime/.requirements.txt"
    )
    old_marker = marker.read_bytes()
    (repository / "requirements.txt").write_text("dependency==2\n", encoding="utf-8")
    stale = run_launcher(launcher, tmp_path, environment)
    assert "Runtime environment is stale" in stale.stderr
    assert marker.read_bytes() == old_marker

    assert marker.is_file()
    assert not (tmp_path / "python.log").exists()


def test_launcher_rejects_each_invalid_owned_package(
    tmp_path: Path, isolated_launcher: tuple[Path, Path, dict[str, str]]
) -> None:
    launcher, repository, environment = isolated_launcher
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


def test_launcher_validates_owned_packages_and_preserves_argv(
    tmp_path: Path, isolated_launcher: tuple[Path, Path, dict[str, str]]
) -> None:
    launcher, repository, environment = isolated_launcher
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


def test_shared_checkout_selects_only_current_machine_and_keeps_legacy_env(
    tmp_path: Path, isolated_launcher: tuple[Path, Path, dict[str, str]]
) -> None:
    launcher, repository, environment = isolated_launcher
    install_fake_runtime(repository)
    first = machine.repository_venv_root()
    original_id = machine.MACHINE_ID_FILE.read_bytes()
    marker = repository / first / "venv-runtime/.requirements.txt"
    original_lock = marker.read_bytes()
    legacy = repository / "venv-runtime/bin/python"
    legacy.parent.mkdir(parents=True)
    write_executable(legacy, FORBIDDEN_SYSTEM_PYTHON)
    assert run_launcher(launcher, tmp_path, environment).returncode == 0

    machine.MACHINE_ID_FILE.write_text("fedcba9876543210" * 2, encoding="ascii")
    second = machine.repository_venv_root()
    assert first != second
    environment["EXPECTED_VENV_ROOT"] = str(repository / second)
    missing = run_launcher(launcher, tmp_path, environment)
    assert missing.returncode == 1
    assert "Runtime environment is not installed" in missing.stderr
    install_fake_runtime(repository)
    assert run_launcher(launcher, tmp_path, environment).returncode == 0

    machine.MACHINE_ID_FILE.write_bytes(original_id)
    environment["EXPECTED_VENV_ROOT"] = str(repository / first)
    assert run_launcher(launcher, tmp_path, environment).returncode == 0
    assert marker.read_bytes() == original_lock
    assert (repository / second / "venv-runtime/bin/python").is_file()
    assert legacy.read_text(encoding="utf-8") == FORBIDDEN_SYSTEM_PYTHON
    assert not (tmp_path / "python.log").exists()


def test_make_and_launcher_share_all_environment_paths_and_fail_closed(
    tmp_path: Path, isolated_launcher: tuple[Path, Path, dict[str, str]]
) -> None:
    launcher, repository, environment = isolated_launcher
    install_fake_runtime(repository)
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    roles = ("RUNTIME", "XPRA", "QUALITY", "TEST", "PACKAGE", "STANDALONE", "DOCS")
    report = "print-venvs:\n\t@printf '%s\\n' " + " ".join(
        f"'$({role}_VENV)'" for role in roles
    )

    def make(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "--no-print-directory", "-f", str(makefile), *arguments],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    root = machine.repository_venv_root()
    selected = make(f"--eval={report}", "print-venvs")
    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.splitlines() == [
        str(root / f"venv-{role.lower()}") for role in roles
    ]
    prepared = make("-n", "runtime-venv")
    assert prepared.returncode == 0, prepared.stderr
    assert f"{root}/venv-runtime/bin/python" in prepared.stdout
    assert f"{repository}/{root}/venv-xpra" in prepared.stdout
    cleanup = make("-n", "clean")
    assert cleanup.returncode == 0, cleanup.stderr
    assert all(f"'{root}/venv-{role.lower()}'" in cleanup.stdout for role in roles)
    assert "find '.venvs'" not in cleanup.stdout
    assert "'venv-runtime'" not in cleanup.stdout

    machine.MACHINE_ID_FILE.write_text("uninitialized\n", encoding="ascii")
    refused = make(f"--eval={report}", "print-venvs")
    assert refused.returncode != 0
    assert not refused.stdout
    assert "Cannot select machine-specific environments" in refused.stderr
    refused = run_launcher(launcher, tmp_path, environment)
    assert refused.returncode == 1
    assert not refused.stdout
    assert "invalid or uninitialized" in refused.stderr
    assert (repository / root / "venv-runtime/bin/python").is_file()
    assert not (tmp_path / "python.log").exists()
