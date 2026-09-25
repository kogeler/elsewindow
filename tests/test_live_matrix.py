# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""The ordered live matrix can resume at a fixed failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.live_support.application import parse_arguments
from tests.live_support.process import LiveFailure
from tests.live_support.xpra import LIVE_CASES, resumed_cases
from tests.live_support.xpra_target import AGENT_CASE

ROOT = Path(__file__).resolve().parents[1]


def test_matrix_order_and_resumed_tails() -> None:
    assert LIVE_CASES == (
        "linger-declined",
        "linger",
        "detach",
        "abrupt",
        "persistent",
        AGENT_CASE,
    )
    assert resumed_cases(None) == LIVE_CASES
    assert resumed_cases("persistent") == ("persistent", AGENT_CASE)
    assert resumed_cases(AGENT_CASE) == (AGENT_CASE,)
    with pytest.raises(LiveFailure, match="unknown live case"):
        resumed_cases("scenario")


def test_harness_and_make_expose_one_resume_point() -> None:
    arguments = ("--target-image", "target", "--client-image", "client")
    assert parse_arguments(list(arguments)).from_case is None
    resumed = parse_arguments([*arguments, "--from-case", "abrupt"])
    assert resumed.from_case == "abrupt"
    with pytest.raises(SystemExit):
        parse_arguments([*arguments, "--from-case", "unknown"])
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "$(if $(strip $(LIVE_FROM)),--from-case '$(strip $(LIVE_FROM))')" in (
        makefile
    )
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "resume at the failed case with `make live-test LIVE_FROM=<case>`" in agents
    assert "only that complete run validates the change" in agents
