"""Unit tests for session → Skill generation helpers (no LLM)."""

from __future__ import annotations

from app.services.skills import (
    _collect_script_references_from_package_files,
    _merge_message_deltas_for_latest_run,
    _missing_script_paths_in_package,
)


def test_merge_message_deltas_prefers_latest_run_by_event_id() -> None:
    rows = [
        {"id": 1, "payload": {"text": "old-run ", "run_id": "r1"}},
        {"id": 2, "payload": {"text": "aaa", "run_id": "r2"}},
        {"id": 3, "payload": {"text": "bbb", "run_id": "r2"}},
        {"id": 4, "payload": {"text": "z", "run_id": "r1"}},
    ]
    assert _merge_message_deltas_for_latest_run(rows) == "aaabbb"


def test_merge_message_deltas_null_run_id_group() -> None:
    rows = [
        {"id": 10, "payload": {"text": "x", "run_id": None}},
        {"id": 11, "payload": {"text": "y", "run_id": None}},
    ]
    assert _merge_message_deltas_for_latest_run(rows) == "xy"


def test_collect_script_references_and_missing() -> None:
    files = {
        "SKILL.md": "See `scripts/foo.py` for the tool.",
        "references/workflow.md": "Run scripts/bar.py then scripts/missing.py",
        "scripts/foo.py": "print(1)",
    }
    refs = _collect_script_references_from_package_files(files)
    assert refs == {"scripts/foo.py", "scripts/bar.py", "scripts/missing.py"}
    miss = _missing_script_paths_in_package(files)
    assert miss == ["scripts/bar.py", "scripts/missing.py"]
