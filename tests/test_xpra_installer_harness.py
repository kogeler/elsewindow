from __future__ import annotations

import pytest

from tests import xpra_installer_acceptance as acceptance
from tools import install_xpra_release as installer


def test_acceptance_uses_only_the_latest_release_without_history_input() -> None:
    source = (acceptance.ROOT / "tests/xpra_installer_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert acceptance.BUILD_INPUTS == (
        acceptance.CONTAINERFILE,
        acceptance.INSTALLER,
    )
    assert source.count("resolve_latest_release(api)") == 1
    for forbidden in ("older", "predecessor", "verify_release_is_current"):
        assert forbidden not in source.casefold()


def test_pre_purge_inventory_accepts_native_dpkg_spellings() -> None:
    inventory = [
        f"{package}:amd64" if index % 2 else package
        for index, package in enumerate(installer.REQUIRED_XPRA_PACKAGES)
    ]

    acceptance.require_pre_purge(inventory, "fixture")


@pytest.mark.parametrize(
    "inventory",
    (
        [*installer.REQUIRED_XPRA_PACKAGES, "xpra-extra"],
        [*installer.REQUIRED_XPRA_PACKAGES[:-1]],
        [
            *installer.REQUIRED_XPRA_PACKAGES[:-1],
            f"{installer.REQUIRED_XPRA_PACKAGES[-1]}:arm64",
        ],
        [
            *installer.REQUIRED_XPRA_PACKAGES[:-1],
            installer.REQUIRED_XPRA_PACKAGES[0],
        ],
    ),
)
def test_pre_purge_inventory_rejects_non_exact_set(inventory: list[str]) -> None:
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.require_pre_purge(inventory, "fixture")
