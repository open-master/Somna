"""Pydantic models for the Agent → UI event protocol.

All events produced by Agent Core MUST conform to the models here.
They are:
  1. Persisted into Postgres `events` table (for replay)
  2. Published to NATS subject  `session.{session_id}`  (for live streaming)
  3. Forwarded by BFF as SSE / WebSocket messages to the browser

See also: docs/adr/0005-event-schema.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AGENT_EVENT_SCHEMA_VERSION = "1"


# =====================================================================
# Enums
# =====================================================================
class SessionPhase(str, Enum):
    idle = "idle"
    planning = "planning"
    executing = "executing"
    compacting = "compacting"
    waiting_user = "waiting_user"
    interrupted = "interrupted"
    stopped = "stopped"
    done = "done"
    partial = "partial"
    error = "error"


class TodoStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class ScreenshotSource(str, Enum):
    browser = "browser"
    desktop = "desktop"
    custom = "custom"


# =====================================================================
# Nested value objects
# =====================================================================
class TodoItem(BaseModel):
    id: str
    text: str
    status: TodoStatus
    parent_id: Optional[str] = None


class SkillDebugItem(BaseModel):
    id: str
    name: str
    reason: str = ""
    load_files: List[str] = Field(default_factory=list)
    forced: bool = Field(default=False, description="True when Task Frame explicitly named this enabled Skill")


# =====================================================================
# Base event
# =====================================================================
class BaseEvent(BaseModel):
    """Fields shared by every event."""

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    type: str
    v: str = Field(default=AGENT_EVENT_SCHEMA_VERSION, description="schema version")
    session_id: UUID
    run_id: Optional[str] = Field(default=None, description="Agent run identifier")
    seq: Optional[int] = Field(default=None, description="monotonic seq per session")
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =====================================================================
# Concrete events
# =====================================================================
class MessageDeltaEvent(BaseEvent):
    type: Literal["message.delta"] = "message.delta"
    role: Literal["assistant"] = "assistant"
    text: str


class ThinkingDeltaEvent(BaseEvent):
    type: Literal["thinking.delta"] = "thinking.delta"
    text: str


class ToolCallEvent(BaseEvent):
    type: Literal["tool.call"] = "tool.call"
    id: str
    name: str
    args: Any


class ToolResultEvent(BaseEvent):
    type: Literal["tool.result"] = "tool.result"
    id: str
    ok: bool
    preview: str = Field(default="", description="short human-readable preview")
    full_ref: Optional[str] = Field(default=None, description="S3 key or URL for full content")
    duration_ms: Optional[int] = None


class ScreenshotEvent(BaseEvent):
    type: Literal["screenshot"] = "screenshot"
    url: str
    source: ScreenshotSource = ScreenshotSource.browser
    width: Optional[int] = None
    height: Optional[int] = None


class ArtifactEvent(BaseEvent):
    type: Literal["artifact"] = "artifact"
    name: str
    mime: str
    url: str
    size_bytes: Optional[int] = None
    description: Optional[str] = None


class PlanUpdateEvent(BaseEvent):
    type: Literal["plan.update"] = "plan.update"
    todos: List[TodoItem]


class ClarificationQuestion(BaseModel):
    id: str
    prompt: str
    options: List[str] = Field(default_factory=list)
    allow_custom: bool = True


class TaskFrameEvent(BaseEvent):
    """阶段 A 任务定调结果，供前端在「任务计划」上方展示。"""

    type: Literal["task.frame"] = "task.frame"
    summary: str = Field(..., description="一行中文结论")
    detail: str = Field(default="", description="定调要点（多行），可折叠展示")
    questions: List[ClarificationQuestion] = Field(default_factory=list)


class SkillDebugEvent(BaseEvent):
    """Skill 路由调试信息，帮助确认候选、选中、强制注入与加载文件。"""

    type: Literal["skill.debug"] = "skill.debug"
    candidate_count: int = 0
    selected_skills: List[SkillDebugItem] = Field(default_factory=list)


class StatusEvent(BaseEvent):
    type: Literal["status"] = "status"
    phase: SessionPhase
    message: Optional[str] = None


class TokenUsageEvent(BaseEvent):
    type: Literal["token.usage"] = "token.usage"
    model: str
    input: int
    output: int
    cost_usd: float = 0.0


class InterruptAckEvent(BaseEvent):
    type: Literal["interrupt.ack"] = "interrupt.ack"
    reason: str


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


# =====================================================================
# Discriminated union
# =====================================================================
AgentEvent = Annotated[
    Union[
        MessageDeltaEvent,
        ThinkingDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        ScreenshotEvent,
        ArtifactEvent,
        PlanUpdateEvent,
        TaskFrameEvent,
        SkillDebugEvent,
        StatusEvent,
        TokenUsageEvent,
        InterruptAckEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]


class EventEnvelope(BaseModel):
    """Wraps a single AgentEvent for transport over NATS / SSE / WS.

    NATS message body is the JSON of this envelope.
    """

    model_config = ConfigDict(use_enum_values=True)

    subject: str = Field(description="NATS subject, e.g. 'session.abc123'")
    event: AgentEvent


# =====================================================================
# Helpers
# =====================================================================
def dump_json_schema(indent: int = 2) -> str:
    """Return the full JSON schema for AgentEvent.

    Used by codegen to produce TypeScript types for the frontend.
    Run via: `python -m somna_events.export > schema/event.schema.json`
    """
    from pydantic import TypeAdapter

    adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
    return json.dumps(adapter.json_schema(by_alias=True), indent=indent, ensure_ascii=False)
