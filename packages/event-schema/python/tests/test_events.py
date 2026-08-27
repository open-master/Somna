from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from somna_events import (
    AgentEvent,
    MessageDeltaEvent,
    PlanUpdateEvent,
    SessionPhase,
    StatusEvent,
    TodoItem,
    TodoStatus,
    ToolCallEvent,
)


def _sid():
    return uuid4()


def test_message_delta_roundtrip():
    e = MessageDeltaEvent(session_id=_sid(), text="hi")
    data = e.model_dump(mode="json")
    assert data["type"] == "message.delta"
    assert data["role"] == "assistant"


def test_discriminated_union_parses_correct_subtype():
    adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
    payload = {
        "type": "tool.call",
        "session_id": str(_sid()),
        "id": "call-1",
        "name": "shell",
        "args": {"cmd": "ls"},
    }
    parsed = adapter.validate_python(payload)
    assert isinstance(parsed, ToolCallEvent)


def test_plan_update_with_todos():
    todos = [
        TodoItem(id="1", text="a", status=TodoStatus.done),
        TodoItem(
            id="2",
            text="b",
            status=TodoStatus.in_progress,
            depends_on=["1"],
            acceptance_criteria=["输出文件存在"],
        ),
    ]
    e = PlanUpdateEvent(session_id=_sid(), plan_id="p1", plan_version=2, todos=todos)
    out = e.model_dump(mode="json")
    assert len(out["todos"]) == 2
    assert out["plan_id"] == "p1"
    assert out["plan_version"] == 2
    assert out["todos"][1]["depends_on"] == ["1"]


def test_status_phase_enum():
    e = StatusEvent(session_id=_sid(), phase=SessionPhase.executing)
    assert e.phase == "executing"


def test_task_frame_event_roundtrip():
    from somna_events import TaskFrameEvent

    e = TaskFrameEvent(
        session_id=_sid(),
        run_id="r1",
        summary="定调摘要",
        detail="a\nb",
        questions=[
            {
                "id": "q1",
                "prompt": "交付什么格式？",
                "options": ["Markdown", "PDF"],
            }
        ],
    )
    data = e.model_dump(mode="json")
    assert data["type"] == "task.frame"
    assert data["summary"] == "定调摘要"
    assert data["questions"][0]["options"] == ["Markdown", "PDF"]


def test_unknown_type_rejected():
    adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "not-a-real-event", "session_id": str(_sid())})
