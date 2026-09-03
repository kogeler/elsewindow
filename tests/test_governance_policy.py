"""Independent dependency, release, and workflow governance contracts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
ACTION = re.compile(
    r"^\s*uses:\s*([^@\s]+)@([0-9a-f]{40})\s+#\s+(v[0-9][^\s]*)$",
    re.MULTILINE,
)
DIRECT_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*\])?==([^\s;]+)$"
)


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _input_requirements(path: Path, *, extends_runtime: bool) -> dict[str, str]:
    direct: dict[str, str] = {}
    includes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = line.strip()
        if not requirement or requirement.startswith("#"):
            continue
        if requirement.startswith("-r "):
            includes.append(requirement.removeprefix("-r "))
            continue
        match = DIRECT_REQUIREMENT.fullmatch(requirement)
        assert match is not None, (path, requirement)
        name = _normalize_package(match.group(1))
        assert name not in direct
        direct[name] = match.group(2)
    assert direct
    assert includes == (["requirements.in"] if extends_runtime else [])
    return direct


def test_governance_files_are_project_local_and_complete() -> None:
    assert {path.name for path in WORKFLOWS.glob("*.yml")} == {
        "ci.yml",
        "dependency-submission.yml",
        "pages.yml",
        "pr-body.yml",
        "release.yml",
    }
    for name in (
        "CI.md",
        "COMPATIBILITY_SECURITY.md",
        "DEPENDENCIES.md",
        "DEVELOPMENT.md",
        "RELEASES.md",
    ):
        assert (ROOT / "doc/maintenance" / name).is_file()
    for name in (
        "dependency_audit.py",
        "dependency_snapshot.py",
        "lock_validation.py",
        "pr_body.py",
        "release_inventory.py",
        "verify_pypi_release.py",
        "version.py",
    ):
        assert (ROOT / ".github/scripts" / name).is_file()


def test_workflow_actions_are_sha_pinned_and_permissions_are_narrow() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        content = path.read_text(encoding="utf-8")
        assert "DOR" + "MANT" not in content
        assert "working-" + "directory:" not in content
        assert "../" not in content
        external = [
            line
            for line in content.splitlines()
            if "uses:" in line and "uses: ./" not in line
        ]
        assert len(ACTION.findall(content)) == len(external), path
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in ci
    assert "run: make live-test" in ci
    assert ci.count("security-events: write") == 1
    assert "repository:" not in ci
    assert "ubuntu-26.04-arm" in ci
    assert 'python-version: "3.13"' not in ci
    assert "${{ matrix.python }}" in ci
    assert "Exact version progression" in ci
    assert "unpublished_base_version" in ci
    assert "published_current_version" in ci
    version_job = ci.split("\n  version:", 1)[1].split("\n  codeql:", 1)[0]
    assert 'elif [[ "$PUBLISHED_CURRENT_VERSION" != "true" ]]' in version_job
    assert "standalone-amd64" not in ci
    assert "standalone-${{ matrix.architecture }}" in ci
    assert "persist-credentials: false" in ci
    for dependency_input in (
        "requirements.in",
        "requirements-quality.in",
        "requirements-test.in",
        "requirements-package.in",
        "requirements-standalone.in",
        "requirements-docs.in",
    ):
        assert dependency_input in ci
    submission = (WORKFLOWS / "dependency-submission.yml").read_text(encoding="utf-8")
    assert submission.count("contents: write") == 1
    assert "pull_request:" not in submission
    pages = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert pages.count("pages: write") == 1
    assert pages.count("id-token: write") == 1
    assert "make docs-audit SYSTEM_PYTHON=python" in pages
    assert "actions/upload-pages-artifact@" in pages
    assert "actions/deploy-pages@" in pages
    assert "if: github.event_name == 'push'" in pages
    assert "requirements.in" in pages
    assert "requirements-docs.in" in pages
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert release.count("contents: write") == 1
    assert release.count("id-token: write") == 1
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "base_ref: ${{ github.event.before }}" in release
    release_ci = release.split("\n  ci:", 1)[1].split("\n  publish-pypi:", 1)[0]
    assert "needs: release-state" in release_ci
    assert "if: needs.release-state.outputs.release_required == 'true'" in release_ci
    assert "release_required: ${{ steps.check.outputs.release_required }}" in release
    assert 'core.setOutput("release_required", String(releaseRequired))' in release
    assert "const releaseCommit = published ? tagCommit : context.sha;" in release
    assert 'read("CHANGELOG.md", releaseCommit)' in release
    assert "paths:" not in release.split("\npermissions:", 1)[0]
    assert "pypa/gh-action-pypi-publish@" in release
    assert "skip-existing" not in release
    assert "password:" not in release
    assert "v${version}" in release
    assert "elsewindow-linux-amd64" in release
    assert "elsewindow-linux-arm64" in release
    assert "SHA256SUMS.txt" in release
    installer = ci.split("\n  installer:", 1)[1].split("\n  live:", 1)[0]
    assert "GITHUB_TOKEN: ${{ github.token }}" in installer


def test_pr_body_metadata_uses_trusted_code_and_bounded_head_data() -> None:
    pr_body = (WORKFLOWS / "pr-body.yml").read_text(encoding="utf-8")
    target_workflows = [
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if "pull_request_target:" in path.read_text(encoding="utf-8")
    ]
    write_grants = sum(
        path.read_text(encoding="utf-8").count("pull-requests: write")
        for path in WORKFLOWS.glob("*.yml")
    )

    assert target_workflows == ["pr-body.yml"]
    assert write_grants == 1
    assert "branches:\n      - main" in pr_body
    assert "paths:\n      - CHANGELOG.md" in pr_body
    assert "opened\n      - reopened\n      - synchronize" in pr_body
    assert "EXPECTED_REPOSITORY: kogeler/elsewindow" in pr_body
    assert "github.rest.repos.getContent" in pr_body
    assert 'path: "CHANGELOG.md"' in pr_body
    assert "ref: headSha" in pr_body
    assert 'file.encoding !== "base64"' in pr_body
    assert "changelog.byteLength > 1_000_000" in pr_body
    assert "python .github/scripts/pr_body.py" in pr_body
    assert "github.rest.pulls.update" in pr_body
    assert "Pull-request body changed; refusing concurrent overwrite" in pr_body
    checkout = pr_body.split("- name: Check out trusted default branch", 1)[1].split(
        "\n      - name:", 1
    )[0]
    assert "persist-credentials: false" in checkout
    assert "ref:" not in checkout


def test_make_exposes_the_complete_governance_surface() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "lock",
        "build",
        "refresh-dependencies",
        "freeze-check",
        "audit",
        "licenses",
        "outdated",
        "dependency-snapshot",
        "docs-build",
        "docs-audit",
        "docs-serve",
        "package",
        "standalone",
        "smoke",
        "reproducibility",
        "checksums",
        "release-notes",
        "compatibility-python",
        "validate-actions",
        "test-network-block",
        "confinement-test",
        "coverage-report",
        "check",
        "ci",
    ):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.MULTILINE), target
    assert makefile.count("--userns=auto:size=2048") == 3
    assert "tools/build_distributions.py" in makefile
    assert "tools/verify_distribution.py" in makefile
    assert "tools/build_standalone.py" in makefile
    assert "tools/verify_standalone.py" in makefile
    assert "--find-links" not in makefile
    assert "--rebuild" in makefile
    assert "--extra=" not in makefile
    for dependency_input in (
        "requirements.in",
        "requirements-quality.in",
        "requirements-test.in",
        "requirements-package.in",
        "requirements-standalone.in",
        "requirements-docs.in",
    ):
        assert dependency_input in makefile
    assert "pip download --quiet --require-hashes" in makefile
    assert "import importlib.metadata, ssh_wrapper" in makefile
    assert "import importlib.metadata, pip, ssh_wrapper" in makefile
    assert "override NORMALIZATION_EPOCH := 315532800" in makefile
    assert "git log" not in makefile
    assert "git ls-files" not in (ROOT / "tools/create_live_payload.py").read_text(
        encoding="utf-8"
    )
    assert "--volume" not in makefile
    assert "podman" + " cp" not in makefile.lower()
    audit = (ROOT / ".github/scripts/dependency_audit.py").read_text(encoding="utf-8")
    for lock in (
        "requirements.txt",
        "requirements-quality.txt",
        "requirements-test.txt",
        "requirements-package.txt",
        "requirements-standalone.txt",
        "requirements-docs.txt",
    ):
        assert lock in audit
    assert '"--disable-pip"' in audit
    assert '"--requirement"' in audit


def test_python_313_and_314_are_both_enforced_by_metadata_and_ci() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    governance = (ROOT / "containers/governance/Containerfile").read_text(
        encoding="utf-8"
    )

    assert project["project"]["requires-python"] == ">=3.13,<3.15"
    assert project["tool"]["mypy"]["python_version"] == "3.13"
    assert workflow.count('python-version: "3.14"') >= 4
    assert '- "3.13"' in workflow
    assert '- "3.14"' in workflow
    assert "CPython ${{ matrix.python }} unit and policy suite" in workflow
    assert "make test SYSTEM_PYTHON=python" in workflow
    assert re.search(
        r"^ci:\s+check freeze-check audit compatibility-python$",
        makefile,
        re.MULTILINE,
    )
    assert "python313" in makefile
    assert governance.count("python:3.13-slim@sha256:") >= 2


def test_current_locks_and_versions_validate_with_stdlib_helpers(
    tmp_path: Path,
) -> None:
    for helper in ("lock_validation.py", "version.py"):
        arguments = [sys.executable, str(ROOT / ".github/scripts" / helper)]
        if helper == "version.py":
            arguments.append("check")
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    output = tmp_path / "snapshot.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / ".github/scripts/dependency_snapshot.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert set(json.loads(output.read_text(encoding="utf-8"))["manifests"]) == {
        "requirements.txt",
        "requirements-docs.txt",
        "requirements-package.txt",
        "requirements-quality.txt",
        "requirements-standalone.txt",
        "requirements-test.txt",
    }


def test_dependabot_groups_ecosystems_and_keeps_runtime_manual() -> None:
    dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")

    assert dependabot.count("package-ecosystem:") == 2
    assert dependabot.count("open-pull-requests-limit: 1") == 2
    assert dependabot.count('          - "*"') == 2
    assert 'dependency-name: "ssh-wrapper"' in dependabot
    assert "exclude-paths:\n      - pyproject.toml" in dependabot
    assert "python-dependencies:" in dependabot
    assert "github-actions:" in dependabot

    governance = (ROOT / "containers/governance/Containerfile").read_text(
        encoding="utf-8"
    )
    assert governance.count("pip==26.1.1") == 2
    assert governance.count("pip-tools==7.5.3") == 2


def test_audit_starts_with_no_reviewed_exceptions() -> None:
    exceptions = json.loads(
        (ROOT / ".github/dependency-audit-exceptions.json").read_text(encoding="utf-8")
    )
    assert exceptions == {"exceptions": []}


def test_dependency_audiences_have_exact_direct_owners() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dynamic"] == ["version", "dependencies"]
    assert "dependencies" not in project["project"]
    assert "optional-dependencies" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["dependencies"] == {
        "file": ["requirements.in"]
    }
    expected_inputs = {
        "requirements.in",
        "requirements-quality.in",
        "requirements-test.in",
        "requirements-package.in",
        "requirements-standalone.in",
        "requirements-docs.in",
    }
    assert {path.name for path in ROOT.glob("requirements*.in")} == expected_inputs

    runtime = _input_requirements(ROOT / "requirements.in", extends_runtime=False)
    assert runtime == {"ssh-wrapper": "0.1.0"}
    expected = {
        "quality": {"bandit", "mypy", "pip-audit", "pip-licenses", "ruff"},
        "test": {"pytest", "pytest-asyncio", "pytest-cov", "pytest-xdist"},
        "package": {"build", "setuptools", "wheel"},
        "standalone": {"pyinstaller"},
        "docs": {"mkdocs-material"},
    }
    audiences = {
        audience: _input_requirements(
            ROOT / f"requirements-{audience}.in", extends_runtime=True
        )
        for audience in expected
    }
    flattened = [name for requirements in audiences.values() for name in requirements]
    assert len(flattened) == len(set(flattened))
    for audience, requirements in audiences.items():
        assert set(requirements) == expected[audience]

    for input_name in expected_inputs:
        lock_name = input_name.removesuffix(".in") + ".txt"
        header = "\n".join(
            (ROOT / lock_name).read_text(encoding="utf-8").splitlines()[:8]
        )
        assert input_name in header
        assert "pyproject.toml" not in header


def test_distribution_contract_includes_runtime_yaml_and_console_entry() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {"elsewindow": "elsewindow.cli:main"}
    assert project["tool"]["setuptools"]["package-data"] == {
        "elsewindow": ["live-cli.yml", "profiles.yml", "py.typed"]
    }
    verifier = (ROOT / "tools/verify_distribution.py").read_text(encoding="utf-8")
    smoke = (ROOT / "tools/smoke_distribution.py").read_text(encoding="utf-8")
    for required in ("live-cli.yml", "profiles.yml", "py.typed", "RECORD"):
        assert required in verifier
    for required in ("--no-index", "--help", "--diagnose", "--strict"):
        assert required in smoke


def test_parallel_coverage_state_is_confined_to_artifacts() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["coverage"]["run"]["data_file"] == (".artifacts/.coverage")


def test_release_builds_once_and_routes_artifacts_to_exact_destinations() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert ci.count("make package reproducibility smoke-wheel smoke-sdist") == 1
    assert ci.count("make standalone smoke-standalone") == 1
    assert ci.count("actions/upload-artifact@") == 2
    assert release.count("actions/download-artifact@") == 4
    assert "make package" not in release
    assert "make standalone" not in release
    pypi_section = release.split("publish-pypi:", 1)[1].split("publish-github:", 1)[0]
    assert "standalone-" not in pypi_section
    assert "packages-dir: dist" in pypi_section
