"""轻量回复：澄清追问或直接回答（不调用工具、不经 Planner）。"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from somna_events import MessageDeltaEvent, SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.state import SessionState
from app.llm.client import get_async_openai
from app.logging_setup import get_logger

log = get_logger(__name__)


async def clarify_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    frame = state.get("task_frame") or {}
    qs = frame.get("clarification_questions") or []
    if isinstance(qs, str):
        qs = [qs]

    lines = ["在开始执行前，需要先确认以下内容：", ""]
    for i, q in enumerate(qs, start=1):
        if str(q).strip():
            lines.append(f"{i}. {q}")
    text = "\n".join(lines).strip() or "请补充更多任务细节后再继续。"

    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message="需要您补充信息",
        )
    )
    await emit(MessageDeltaEvent(session_id=session_id, run_id=run_id, text=text))
    new_msgs = list(state.get("messages") or [])
    new_msgs.append(AIMessage(content=text))
    log.info("graph.clarify", session_id=str(session_id), n_questions=len(qs))
    return {"messages": new_msgs, "assistant_text": text, "finished": True}


async def direct_answer_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    settings = get_settings()
    model = (state.get("executor_model") or settings.agent_default_executor).strip()
    user_message = state.get("user_message") or ""
    frame = state.get("task_frame") or {}

    parts: list[str] = []
    cm = (state.get("compact_memory") or "").strip()
    if cm:
        parts.append(f"### 会话摘要（供参考）\n{cm}\n")
    parts.append("### 任务定调（供你对齐语气与深度，勿照抄给用户）\n")
    parts.append(f"task_mode={frame.get('task_mode')} effort={frame.get('effort_level')} deliverable={frame.get('deliverable_type')}")
    sc = frame.get("success_criteria") or []
    if isinstance(sc, list) and sc:
        parts.append("success_criteria: " + "；".join(str(x) for x in sc[:5]))
    parts.append("\n### 用户问题\n" + user_message)

    user_block = "\n".join(parts)

    system = (
        "你是 Somna。当前为「直接回答」模式：用户请求适合用简短对话完成，无需启动沙盒工具或多步任务编排。\n"
        "要求：简洁、准确、分点有条理；不确定处明确说明；不要编造未经验证的事实；"
        "不要承诺本会话内会去执行需要浏览器/搜索/写文件的操作（若需要，应建议用户改用完整任务模式）。\n"
        "语言与用户一致，默认中文。"
    )

    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message="直接回答（轻量模式）",
        )
    )

    client = get_async_openai()
    text_buf = ""
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_block},
        ],
        temperature=0.2,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                text_buf += delta.content
                await emit(
                    MessageDeltaEvent(session_id=session_id, run_id=run_id, text=delta.content)
                )

    text = text_buf.strip() or "（未能生成回答，请重试或改用完整任务描述。）"
    new_msgs = list(state.get("messages") or [])
    new_msgs.append(AIMessage(content=text))
    log.info("graph.direct_answer", session_id=str(session_id), chars=len(text))
    return {"messages": new_msgs, "assistant_text": text, "finished": True}
