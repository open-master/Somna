from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionWorkflowInput:
    session_id: str
    run_id: str
    text: str
    planner_model: str | None
    executor_model: str | None
    executor_engine: str = "native"
    task_frame_model: str | None = None
    compact_model: str | None = None
    coder_model: str | None = None
    reasoner_model: str | None = None
    longctx_model: str | None = None
    mcp_tool_models: dict[str, str] | None = None
