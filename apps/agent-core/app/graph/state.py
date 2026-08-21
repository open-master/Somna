"""LangGraph session state."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.graph import add_messages


class SessionState(TypedDict, total=False):
    """Session graph state.

    Compact summaries live on `compact_memory`; compaction runs inside execute,
    not as a separate graph node.
    """

    session_id: UUID
    run_id: str
    user_id: str | None

    # Input for this run
    user_message: str
    attachments: list[dict[str, Any]]

    # Models
    planner_model: str
    task_frame_model: str
    executor_model: str
    compact_model: str
    coder_model: str
    reasoner_model: str
    longctx_model: str
    skill_model: str
    skill_mode: str
    mcp_tool_models: dict[str, str]
    selected_skills: list[dict[str, Any]]
    skill_prompt_block: str | None
    skill_candidate_count: int
    skill_route_resolved: bool
    skip_planner: bool
    # executor loop: native = OpenAI /v1; anthropic = LiteLLM /anthropic/v1 (same aliases)
    executor_engine: str

    # Sandbox (one per session for M2)
    sandbox_id: str

    # Task framing (phase A) — intent / mode before planner
    task_frame: dict[str, Any] | None
    # Phase B: sandbox-relative paths to materialized JSON (under .somna/runs/<run_id>/)
    task_frame_path: str | None
    plan_path: str | None

    # Accumulated chat history (LangChain messages)
    messages: Annotated[list, add_messages]

    # Planner output (dict: {id, reasoning, todos: [...], estimated_steps})
    plan: dict[str, Any] | None
    # Latest compacted summary (markdown from maybe_compact inside execute)
    compact_memory: str | None
    # Execution summary used by reflect / replan
    execution_summary: dict[str, Any] | None
    reflection: dict[str, Any] | None
    reflection_count: int
    next_node: str | None

    # Execution result
    assistant_text: str
    # Run-global budgets. These remain monotonic across reflect → execute passes.
    tool_turns: int
    total_agent_turns: int
    total_execution_tokens: int
    finished: bool
    error: str | None

    # Mid-execute HITL: next user turn should skip re-plan and continue native execute.
    resume_execute: bool
    resume_goal: str | None
