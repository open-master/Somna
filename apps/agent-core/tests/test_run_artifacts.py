"""Phase B run artifact path helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.graph import run_artifacts as ra


def test_run_state_dir_sanitizes():
    assert ra.run_state_dir("run_xyz") == ".somna/runs/run_xyz"
    assert ".." not in ra.run_state_dir("run../evil")


def test_materialized_paths():
    rid = "run_test123"
    assert ra.task_frame_rel(rid) == ".somna/runs/run_test123/task_frame.json"
    assert ra.plan_rel(rid) == ".somna/runs/run_test123/plan.json"
    assert ra.progress_rel(rid) == ".somna/runs/run_test123/progress.log"
    assert ra.run_index_rel(rid) == ".somna/runs/run_test123/run_index.json"


@pytest.mark.asyncio
async def test_progress_log_overwrites_with_bounded_window():
    ra._progress_buffers.clear()
    writes: list[dict] = []

    async def invoke(*_args, **kwargs):
        writes.append(kwargs["args"])
        return SimpleNamespace(ok=True, error=None)

    sid = uuid4()
    with patch.object(ra, "get_client", return_value=SimpleNamespace(invoke=invoke)):
        for i in range(ra._MAX_PROGRESS_LINES + 6):
            await ra.append_progress_line(
                sandbox_id="sb",
                session_id=sid,
                run_id="run_cap",
                line=f"turn={i}",
            )

    last = writes[-1]
    assert last["action"] == "write"
    assert last["path"] == ".somna/runs/run_cap/progress.log"
    lines = last["content"].strip().split("\n")
    assert len(lines) == ra._MAX_PROGRESS_LINES
    assert "turn=0" not in last["content"]
    assert f"turn={ra._MAX_PROGRESS_LINES + 5}" in last["content"]
