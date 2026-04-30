"""Phase B run artifact path helpers."""

from __future__ import annotations

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
