from __future__ import annotations

from pathlib import Path

import pytest

from tools import install_xpra_release as installer
from tools import prepare_xpra_images as images
from tools import xpra_test_release as pin


def test_committed_pin_is_one_valid_release_name() -> None:
    value = pin.pinned_release_name()

    assert value == installer.release_selector(value)


def test_pin_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "XPRA_TEST_RELEASE"
    path.write_text("# comment\n\n  7.0-r1-1  \n# trailing\n", encoding="utf-8")

    assert pin.pinned_release_name(path) == "7.0-r1-1"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("# only a comment\n", "exactly one"),
        ("7.0-r1-1\n8.0-r2-1\n", "exactly one"),
        ("../x\n", "release selector"),
    ],
)
def test_pin_fails_closed(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "XPRA_TEST_RELEASE"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(installer.ReleaseError, match=message):
        pin.pinned_release_name(path)


def test_missing_pin_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(installer.ReleaseError, match="cannot read"):
        pin.pinned_release_name(tmp_path / "XPRA_TEST_RELEASE")


def test_image_preparation_resolves_the_pinned_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []

    class Api:
        def __init__(self, token: str | None = None) -> None:
            self.token = token

        def releases(self) -> tuple[object, ...]:
            return ({"id": 7},)

    class Release:
        release_id = 7

    def resolve(api: object, *, release_name: str | None = None) -> Release:
        calls.append(release_name)
        return Release()

    monkeypatch.setattr(installer, "GitHubApi", Api)
    monkeypatch.setattr(installer, "resolve_latest_release", resolve)

    _api, release, raw = images.resolve_release("7.0-r1-1")

    assert calls == ["7.0-r1-1"]
    assert release.release_id == 7
    assert raw == {"id": 7}
