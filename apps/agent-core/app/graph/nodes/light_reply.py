"""轻量回复：澄清追问或直接回答（不调用工具、不经 Planner）。"""

from __future__ import annotations

import base64
from typing import Any

from langchain_core.messages import AIMessage
from somna_events import MessageDeltaEvent, SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.state import SessionState
from app.graph.user_turn import last_human_turn_text, prior_conversation_text
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.services.billing import emit_model_usage

log = get_logger(__name__)

_MAX_DIRECT_ANSWER_IMAGES = 8


async def _build_direct_answer_user_payload(
    state: SessionState,
    text_block: str,
) -> str | list[dict[str, Any]]:
    """轻量直接回答：附带 PDF 文本与图片（OpenAI 多模态），与 ingest 写入沙箱的附件列表对齐。"""
    from app.services.attachments import extract_pdf_text, fetch_object_bytes

    attachments = list(state.get("attachments") or [])
    if not attachments:
        return text_block

    pdf_sections: list[str] = []
    image_items: list[tuple[dict[str, Any], bytes]] = []

    for att in attachments:
        key = att.get("s3_key")
        if not isinstance(key, str):
            continue
        mime = str(att.get("mime") or "").lower()
        fname = str(att.get("filename") or "file")

        if mime.startswith("image/"):
            data = await fetch_object_bytes(key)
            if data and len(image_items) < _MAX_DIRECT_ANSWER_IMAGES:
                image_items.append((att, data))
            continue

        is_pdf = mime == "application/pdf" or fname.lower().endswith(".pdf")
        if is_pdf:
            data = await fetch_object_bytes(key)
            if data:
                txt = extract_pdf_text(data)
                if txt.strip():
                    pdf_sections.append(f"### {fname}\n{txt}")

    body = text_block
    if pdf_sections:
        body = text_block + "\n\n### 附件文本（PDF 提取）\n\n" + "\n\n".join(pdf_sections)

    if not image_items:
        return body

    out: list[dict[str, Any]] = [{"type": "text", "text": body}]
    for att, raw in image_items:
        m = str(att.get("mime") or "image/png").split(";")[0].strip()
        if not m.startswith("image/"):
            m = "image/png"
        b64 = base64.standard_b64encode(raw).decode("ascii")
        out.append({"type": "image_url", "image_url": {"url": f"data:{m};base64,{b64}"}})
    return out


async def clarify_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    frame = state.get("task_frame") or {}
    qs = frame.get("clarification_questions") or []
    if isinstance(qs, str):
        qs = [qs]

    lines = ["在开始执行前，需要先确认以下内容：", ""]
    for i, q in enumerate(qs, start=1):
        prompt = (
            str(q.get("prompt") or "").strip()
            if isinstance(q, dict)
            else str(q).strip()
        )
        if prompt:
            lines.append(f"{i}. {prompt}")
    text = "\n".join(lines).strip() or "请补充更多任务细节后再继续。"

    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.waiting_user,
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
    turn_text = last_human_turn_text(state)
    frame = state.get("task_frame") or {}

    parts: list[str] = []
    cm = (state.get("compact_memory") or "").strip()
    if cm:
        parts.append(f"### 会话摘要（供参考）\n{cm}\n")
    prior_context = prior_conversation_text(state)
    if prior_context:
        parts.append(f"### 最近对话（用于理解指代和追问）\n{prior_context}\n")
    parts.append("### 任务定调（供你对齐语气与深度，勿照抄给用户）\n")
    parts.append(f"task_mode={frame.get('task_mode')} effort={frame.get('effort_level')} deliverable={frame.get('deliverable_type')}")
    sc = frame.get("success_criteria") or []
    if isinstance(sc, list) and sc:
        parts.append("success_criteria: " + "；".join(str(x) for x in sc[:5]))
    parts.append("\n### 用户问题与材料\n" + turn_text)

    user_block = "\n".join(parts)
    user_payload = await _build_direct_answer_user_payload(state, user_block)
    is_multimodal = isinstance(user_payload, list)

    system = (
        "你是 Somna。当前为「直接回答」模式：用户请求适合用简短对话完成，无需启动沙盒工具或多步任务编排。\n"
        "若用户提供了图片，请直接基于图片内容作答；若提供了 PDF 提取文本，请基于该文本作答。\n"
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

    att_n = len(list(state.get("attachments") or []))
    log.info(
        "graph.direct_answer",
        session_id=str(session_id),
        multimodal=is_multimodal,
        attachments=att_n,
    )

    client = get_async_openai()
    text_buf = ""
    prompt_tokens = completion_tokens = 0
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_payload},
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
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    if state.get("user_id") and (prompt_tokens or completion_tokens):
        await emit_model_usage(
            session_id=session_id,
            run_id=run_id,
            usage_key=f"{run_id}:direct_answer",
            phase="direct_answer",
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

    text = text_buf.strip() or "（未能生成回答，请重试或改用完整任务描述。）"
    new_msgs = list(state.get("messages") or [])
    new_msgs.append(AIMessage(content=text))
    log.info("graph.direct_answer.done", session_id=str(session_id), chars=len(text))
    return {"messages": new_msgs, "assistant_text": text, "finished": True}
