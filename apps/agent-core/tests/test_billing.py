"""Pure billing policy tests; database reservation integration is covered by service SQL tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import billing

TEST_CONFIG = {
    "version": 1,
    "task_base": {
        "chat": 1,
        "research": 2,
        "content_build": 4,
        "code_build": 5,
        "operate": 3,
        "media": 1,
    },
    "effort_multiplier": {"low": 1, "medium": 2, "high": 4},
    "model_meter": {
        "reserve_points": 2,
        "input_tokens_per_point": 8000,
        "output_tokens_per_point": 2000,
    },
    "tool_costs": {
        "web_search_batch": 1,
        "visual_critique": 2,
        "image_per_output": 8,
        "tts_per_1000_chars": 2,
        "video_default_per_output": 40,
        "external_side_effect": 1,
    },
    "reservation_ttl_seconds": 7200,
}


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ({"task_mode": "direct_answer", "deliverable_type": "chat_answer"}, "chat"),
        ({"task_mode": "research", "deliverable_type": "chat_answer"}, "research"),
        ({"task_mode": "research_and_report", "deliverable_type": "markdown_report"}, "content_build"),
        ({"task_mode": "build", "deliverable_type": "spreadsheet"}, "content_build"),
        ({"task_mode": "build", "deliverable_type": "code"}, "code_build"),
        ({"task_mode": "operate", "deliverable_type": "browser_action"}, "operate"),
        ({"task_mode": "full_pipeline", "deliverable_type": "video"}, "media"),
    ],
)
def test_classify_task(frame: dict[str, str], expected: str) -> None:
    assert billing.classify_task(frame) == expected


def test_task_enum_normalization_is_strict() -> None:
    assert billing.normalize_task_mode("not-a-mode") == "full_pipeline"
    assert billing.normalize_effort_level("unlimited") == "medium"
    assert billing.normalize_deliverable_type("unknown-binary") == "unspecified"


def test_reservation_quote_uses_base_effort_and_model_reserve() -> None:
    with patch.object(billing, "default_billing_config", return_value=TEST_CONFIG):
        assert billing.reservation_quote(TEST_CONFIG, "chat", "low") == (1, 3)
        assert billing.reservation_quote(TEST_CONFIG, "code_build", "high") == (5, 22)


def test_model_usage_rounds_once_for_the_run() -> None:
    with patch.object(billing, "default_billing_config", return_value=TEST_CONFIG):
        assert billing.model_usage_points(TEST_CONFIG, 0, 0) == 0
        assert billing.model_usage_points(TEST_CONFIG, 8000, 2000) == 2
        assert billing.model_usage_points(TEST_CONFIG, 1, 0) == 1


def test_tool_quotes_use_successful_output_and_ignore_mock_search() -> None:
    with patch.object(billing, "default_billing_config", return_value=TEST_CONFIG):
        assert billing.tool_point_quote(TEST_CONFIG, "wan_text2image", {"n": 3}) == 24
        assert billing.tool_point_quote(TEST_CONFIG, "minimax_tts", {"text": "x" * 1001}) == 4
        assert billing.tool_point_quote(TEST_CONFIG, "wan_t2v", {}) == 40

        image = SimpleNamespace(ok=True, output={"paths": ["a.png", "b.png"]})
        assert billing.tool_usage_points(TEST_CONFIG, "wan_text2image", {"n": 4}, image) == 16
        mock_search = SimpleNamespace(ok=True, output={"provider": "mock", "results": []})
        assert billing.tool_usage_points(TEST_CONFIG, "search", {}, mock_search) == 0
        failed = SimpleNamespace(ok=False, output=None)
        assert billing.tool_usage_points(TEST_CONFIG, "wan_t2v", {}, failed) == 0


def test_only_irreversible_media_is_billable_when_later_task_fails() -> None:
    assert billing.tool_bills_on_failure("wan_text2image") is True
    assert billing.tool_bills_on_failure("wan_t2v") is True
    assert billing.tool_bills_on_failure("visual_critique") is False
    assert billing.tool_bills_on_failure("search") is False
