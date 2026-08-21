"""Somna AI — Agent → UI event protocol.

Canonical (source of truth) definition is in this package.
TypeScript side is generated from the JSON schema exported here.
"""

from .events import (
    AGENT_EVENT_SCHEMA_VERSION,
    AgentEvent,
    ArtifactEvent,
    BaseEvent,
    ClarificationQuestion,
    ErrorEvent,
    EventEnvelope,
    InterruptAckEvent,
    MessageDeltaEvent,
    PlanUpdateEvent,
    ScreenshotEvent,
    ScreenshotSource,
    SessionPhase,
    SkillDebugEvent,
    SkillDebugItem,
    StatusEvent,
    TaskFrameEvent,
    ThinkingDeltaEvent,
    TodoItem,
    TodoStatus,
    TokenUsageEvent,
    ToolCallEvent,
    ToolResultEvent,
    dump_json_schema,
)

__all__ = [
    "AGENT_EVENT_SCHEMA_VERSION",
    "AgentEvent",
    "ArtifactEvent",
    "BaseEvent",
    "ClarificationQuestion",
    "ErrorEvent",
    "EventEnvelope",
    "InterruptAckEvent",
    "MessageDeltaEvent",
    "PlanUpdateEvent",
    "ScreenshotEvent",
    "ScreenshotSource",
    "SessionPhase",
    "SkillDebugEvent",
    "SkillDebugItem",
    "StatusEvent",
    "TaskFrameEvent",
    "ThinkingDeltaEvent",
    "TodoItem",
    "TodoStatus",
    "TokenUsageEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "dump_json_schema",
]
