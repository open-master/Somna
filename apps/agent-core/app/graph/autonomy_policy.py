"""effective_autonomy + delivery / reflect knobs (risk caps autonomy)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Autonomy = Literal["low", "medium", "high"]


def effective_autonomy_level(task_frame: dict[str, Any] | None) -> Autonomy:
    """Declared autonomy capped by risk: high risk → at most medium."""
    f = task_frame if isinstance(task_frame, dict) else {}
    raw = str(f.get("autonomy_level") or "medium").strip().lower()
    if raw not in ("low", "medium", "high"):
        raw = "medium"
    risk = str(f.get("risk_level") or "low").strip().lower()
    if risk not in ("low", "medium", "high"):
        risk = "low"
    if risk == "high" and raw == "high":
        return "medium"
    return raw  # type: ignore[return-value]


@dataclass(frozen=True)
class DeliveryValidationPolicy:
    """Native: max rounds of model-stop-without-delivery before error; SDK: delivery retry cap."""

    max_native_stop_without_delivery: int
    max_sdk_delivery_rounds: int


def delivery_validation_policy(autonomy: Autonomy) -> DeliveryValidationPolicy:
    """Table-driven: low = more chances to fix delivery; high = slightly fewer SDK friction rounds."""
    if autonomy == "low":
        return DeliveryValidationPolicy(max_native_stop_without_delivery=3, max_sdk_delivery_rounds=12)
    if autonomy == "high":
        return DeliveryValidationPolicy(max_native_stop_without_delivery=2, max_sdk_delivery_rounds=8)
    return DeliveryValidationPolicy(max_native_stop_without_delivery=2, max_sdk_delivery_rounds=10)
