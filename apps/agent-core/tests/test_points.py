"""Point plan and invite-code helper tests."""

from __future__ import annotations

import re

import pytest

from app.services.points import (
    generate_invite_code,
    invite_code_type_for_plan,
    invite_code_type_for_topup,
    invite_payload,
    normalize_invite_code,
    normalize_plan,
    plan_daily,
    plan_monthly,
)


@pytest.mark.parametrize("amount", [1000, 2000, 3000, 4000, 5000])
def test_topup_invite_code_type(amount: int) -> None:
    assert invite_code_type_for_topup(amount) == f"topup_{amount}"
    assert invite_payload(f"topup_{amount}") == (None, amount)


@pytest.mark.parametrize("plan", ["basic", "pro"])
def test_plan_invite_code_type(plan: str) -> None:
    assert invite_code_type_for_plan(plan) == f"sub_{plan}"
    assert invite_payload(f"sub_{plan}") == (plan, None)


def test_invite_helpers_reject_unsupported_values() -> None:
    with pytest.raises(ValueError):
        invite_code_type_for_topup(999)
    with pytest.raises(ValueError):
        invite_code_type_for_plan("free")
    with pytest.raises(ValueError):
        invite_payload("unknown")


def test_plan_catalog_matches_quizgalaxy() -> None:
    assert (plan_daily("free"), plan_monthly("free")) == (10, 50)
    assert (plan_daily("basic"), plan_monthly("basic")) == (20, 200)
    assert (plan_daily("pro"), plan_monthly("pro")) == (50, 500)
    assert normalize_plan("invalid") == "free"


def test_generate_invite_code_uses_somna_format() -> None:
    values = {generate_invite_code() for _ in range(20)}
    assert len(values) == 20
    assert all(re.fullmatch(r"SM-[A-HJ-NP-Z2-9]{5}-[A-HJ-NP-Z2-9]{5}", value) for value in values)


def test_normalize_invite_code() -> None:
    assert normalize_invite_code("  sm-abc23-def45  ") == "SM-ABC23-DEF45"
