from __future__ import annotations

from app.graph.autonomy_policy import (
    delivery_validation_policy,
    effective_autonomy_level,
)


def test_effective_autonomy_passthrough_low_and_medium():
    assert effective_autonomy_level({"autonomy_level": "low", "risk_level": "high"}) == "low"
    assert effective_autonomy_level({"autonomy_level": "medium", "risk_level": "high"}) == "medium"


def test_effective_autonomy_caps_high_when_risk_high():
    assert effective_autonomy_level({"autonomy_level": "high", "risk_level": "high"}) == "medium"
    assert effective_autonomy_level({"autonomy_level": "high", "risk_level": "low"}) == "high"


def test_effective_autonomy_defaults():
    assert effective_autonomy_level({}) == "medium"
    assert effective_autonomy_level(None) == "medium"


def test_delivery_validation_policy_table():
    low = delivery_validation_policy("low")
    assert low.max_native_stop_without_delivery == 3
    assert low.max_sdk_delivery_rounds == 12
    med = delivery_validation_policy("medium")
    assert med.max_native_stop_without_delivery == 2
    assert med.max_sdk_delivery_rounds == 10
    high = delivery_validation_policy("high")
    assert high.max_native_stop_without_delivery == 2
    assert high.max_sdk_delivery_rounds == 8
