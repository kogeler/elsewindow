from __future__ import annotations

from pathlib import Path

import pytest

from tools import install_xpra_release as installer
from tools import prepare_xpra_images as images


def release() -> installer.ForkRelease:
    return installer.ForkRelease(
        7,
        "release-tag",
        "7.0-r1",
        "a" * 40,
        "2026-08-29T00:00:00Z",
        9,
        1,
        (
            installer.ReleaseAsset(
                10,
                installer.ASSET_NAMES[0],
                100,
                "b" * 64,
                "https://github.com/kogeler/xpra/releases/download/tag/debian",
            ),
            installer.ReleaseAsset(
                11,
                installer.ASSET_NAMES[1],
                200,
                "c" * 64,
                "https://github.com/kogeler/xpra/releases/download/tag/ubuntu",
            ),
        ),
    )


def test_image_input_binds_role_files_release_and_asset() -> None:
    selected = release()
    spec = images.SPECS[0]
    asset = selected.asset(installer.ASSET_FOR_DISTRO[spec.distro])

    first = images.image_input_digest(spec, b"release-one", asset)
    repeated = images.image_input_digest(spec, b"release-one", asset)
    changed = images.image_input_digest(spec, b"release-two", asset)

    assert first == repeated
    assert first != changed
    assert len(first) == 64


def test_image_labels_bind_release_input_and_role() -> None:
    selected = release()
    spec = images.SPECS[1]
    asset = selected.asset(installer.ASSET_FOR_DISTRO[spec.distro])

    labels = images.expected_labels(spec, selected, asset, "d" * 64)

    installer.verify_image_release_labels(labels, selected, asset, spec.distro)
    assert labels[images.IMAGE_LABEL_INPUT] == "d" * 64
    assert labels[images.IMAGE_LABEL_ROLE] == spec.role


@pytest.mark.parametrize("value", ("", "a" * 63, "A" * 64, "z" * 64))
def test_image_id_must_be_exact_lowercase_hex(value: str) -> None:
    with pytest.raises(images.ImageError, match="invalid image ID"):
        images.require_image_id(value)


def test_private_descriptor_is_atomic_and_role_addressable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = tmp_path / ".artifacts/xpra-images/current.json"
    monkeypatch.setattr(images, "DESCRIPTOR", descriptor)
    value = {
        "images": {
            "target": {"image_id": "a" * 64},
            "client": {"image_id": "b" * 64},
        },
        "release": release().descriptor(),
        "schema": 1,
    }

    images.publish(value)

    assert descriptor.stat().st_mode & 0o777 == 0o600
    assert images.read_image("target") == "a" * 64
    assert images.read_image("client") == "b" * 64
    assert not (descriptor.parent / ".current.json.new").exists()
