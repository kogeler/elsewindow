"""Regression checks for the extractable project and container boundary."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from tests.live_support.process import (
    LIVE_CLIENT_USER_NAMESPACE,
    LIVE_TARGET_USER_NAMESPACE,
    LiveFailure,
    parse_policy,
)
from tools.project_tree import ProjectTreeError, project_files

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def test_project_has_one_identity_and_no_foreign_application_markers() -> None:
    forbidden = (
        "remote_" + "ssh_" + "mcp",
        "remote-" + "ssh-" + "mcp",
        "remote_" + "ssh_" + "core",
        "remote_" + "xpra",
        "remote-" + "xpra-" + "run",
        "kogeler/" + "remote-" + "xpra",
        "Remote " + "Xpra Run",
    )
    for path in project_files(ROOT):
        assert path.is_file() and not path.is_symlink(), path
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not any(marker.casefold() in text.casefold() for marker in forbidden), (
            path
        )
        assert "/home/" + "agents/" not in text, path


def test_python_has_no_foreign_project_import_or_path_escape() -> None:
    forbidden_modules = {
        "remote_" + "ssh_" + "mcp",
        "remote_" + "ssh_" + "core",
        "joplin_" + "md_" + "sync",
    }
    for path in project_files(ROOT):
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.partition(".")[0] not in forbidden_modules
                    for alias in node.names
                ), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.partition(".")[0] not in forbidden_modules, path


def test_top_level_inventory_is_exact_and_contains_no_generated_state() -> None:
    expected = {
        ".containerignore",
        ".github",
        ".gitignore",
        ".version",
        "AGENTS.md",
        "CHANGELOG.md",
        "LICENSE",
        "MANIFEST.in",
        "Makefile",
        "README.md",
        "bin",
        "containers",
        "doc",
        "elsewindow",
        "mkdocs.yml",
        "pyproject.toml",
        "pytest.ini",
        "requirements-docs.txt",
        "requirements-package.txt",
        "requirements-quality.txt",
        "requirements-standalone.txt",
        "requirements-test.txt",
        "requirements.txt",
        "ruff.toml",
        "tests",
        "tools",
    }
    actual = {path.relative_to(ROOT).parts[0] for path in project_files(ROOT)}
    assert actual == expected


def test_live_harness_has_one_automatic_route() -> None:
    live = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "tests/live_support").glob("*.py"))
    ).lower()
    forbidden = (
        "--" + "scenario",
        "--" + "public-key",
        "--" + "identity-file",
        "strip-" + "session-environment",
        "fi" + "do",
    )
    assert not any(value in live for value in forbidden)


def test_runtime_does_not_reproduce_fork_implementation_tests() -> None:
    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "elsewindow").glob("*.py"))
    )
    assert "import xpra" not in runtime
    assert "from xpra" not in runtime


def test_user_docs_explain_why_the_maintained_xpra_fork_is_required() -> None:
    guide = " ".join(
        (ROOT / "doc/xpra.md").read_text(encoding="utf-8").casefold().split()
    )
    for required in (
        "dozens of xpra defects",
        "contributed directly and accepted upstream",
        "after reports were filed as issues",
        "were not accepted",
        "technical disagreements",
        "additional patches",
        "generic upstream build",
    ):
        assert required in guide


def test_container_transport_has_no_host_filesystem_channel() -> None:
    sources = [ROOT / "Makefile", *sorted((ROOT / "containers").rglob("*"))]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sources
        if path.is_file() and not path.is_symlink()
    ).lower()
    assert "podman" + " cp" not in text
    assert "--" + "volume" not in text
    assert "type=" + "bind" not in text


def test_every_live_runtime_uses_the_bounded_user_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert LIVE_CLIENT_USER_NAMESPACE == LIVE_TARGET_USER_NAMESPACE
    assert makefile.count(LIVE_CLIENT_USER_NAMESPACE) == 3
    policy = f"--pull=never {LIVE_CLIENT_USER_NAMESPACE} --cap-drop=ALL"
    monkeypatch.setenv("ELSEWINDOW_TEST_POLICY", policy)
    assert parse_policy("ELSEWINDOW_TEST_POLICY") == policy.split()


@pytest.mark.parametrize(
    "user_namespace",
    (
        "",
        "--userns=auto",
        "--userns=keep-id:size=2048",
        "--userns=nomap:size=2048",
        "--userns=host",
        "--userns=auto:size=65536",
    ),
)
def test_live_runtime_rejects_missing_or_unbounded_user_namespace(
    monkeypatch: pytest.MonkeyPatch, user_namespace: str
) -> None:
    monkeypatch.setenv(
        "ELSEWINDOW_TEST_POLICY",
        f"--pull=never {user_namespace} --cap-drop=ALL",
    )
    with pytest.raises(LiveFailure, match="must contain exactly"):
        parse_policy("ELSEWINDOW_TEST_POLICY")


def test_markdown_links_are_relative_and_resolve() -> None:
    for path in project_files(ROOT):
        if path.suffix.lower() != ".md":
            continue
        for raw_target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme in {"http", "https", "mailto"}:
                continue
            assert not parsed.scheme and not target.startswith("/"), (path, target)
            relative = unquote(parsed.path)
            if not relative:
                continue
            resolved = (path.parent / relative).resolve()
            assert resolved.is_relative_to(ROOT.resolve()), (path, target)
            assert resolved.exists(), (path, target)


def test_project_enumeration_uses_current_files_without_git_state(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source/changed.py").write_text("first\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("included\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/index").write_bytes(b"irrelevant")
    (tmp_path / "site").mkdir()
    (tmp_path / "site/index.html").write_text("generated\n", encoding="utf-8")
    (tmp_path / "doc/site").mkdir(parents=True)
    (tmp_path / "doc/site/hooks.py").write_text("maintained\n", encoding="utf-8")

    expected = {"doc/site/hooks.py", "source/changed.py", "untracked.txt"}
    assert {
        path.relative_to(tmp_path).as_posix() for path in project_files(tmp_path)
    } == expected

    (tmp_path / "source/changed.py").write_text("second\n", encoding="utf-8")
    changed = next(
        path for path in project_files(tmp_path) if path.name == "changed.py"
    )
    assert changed.read_text(encoding="utf-8") == "second\n"


def test_project_enumeration_rejects_maintained_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(target)

    with pytest.raises(ProjectTreeError, match="symlink"):
        project_files(tmp_path)
