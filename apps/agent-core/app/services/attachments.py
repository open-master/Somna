"""Session file uploads: S3 storage, validation, and materialization into MCP sandbox."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.config import Settings, get_settings
from app.logging_setup import get_logger
from app.tools.client import get_client

log = get_logger(__name__)

# 扩展名白名单（与 Content-Type 粗校验）
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".html",
        ".htm",
        ".css",
        ".xml",
        ".yaml",
        ".yml",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
    }
)

_EXT_TO_MIME = {
    ".pdf": ("application/pdf",),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg", "image/jpg"),
    ".jpeg": ("image/jpeg", "image/jpg"),
    ".gif": ("image/gif",),
    ".webp": ("image/webp",),
    ".svg": ("image/svg+xml",),
    ".doc": ("application/msword",),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    ),
    ".xls": ("application/vnd.ms-excel",),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    ),
    ".ppt": ("application/vnd.ms-powerpoint",),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/octet-stream",
    ),
    ".zip": ("application/zip", "application/x-zip-compressed"),
}


def s3_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(
        str(s.s3_endpoint or "").strip()
        and str(s.s3_bucket or "").strip()
        and str(s.s3_access_key or "").strip()
        and str(s.s3_secret_key or "").strip()
    )


def safe_filename(name: str) -> str:
    base = Path(name or "file").name
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._") or "file"
    return safe[:200]


def validate_mime_for_extension(ext: str, content_type: str | None) -> None:
    ext = ext.lower()
    ct = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    allowed = _EXT_TO_MIME.get(ext)
    if not allowed:
        return
    if ct in allowed or ct == "application/octet-stream":
        return
    raise HTTPException(
        status_code=400,
        detail=f"MIME {ct!r} 与扩展名 {ext} 不匹配",
    )


def uploads_key_prefix(session_id: uuid.UUID) -> str:
    return f"sessions/{session_id}/uploads/"


def normalize_attachment_refs(
    session_id: uuid.UUID,
    raw: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """仅接受本会话 uploads 前缀下的对象元数据，防止伪造 key。"""
    s = settings or get_settings()
    prefix = uploads_key_prefix(session_id)
    max_b = s.attachment_max_bytes
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("s3_key")
        if not isinstance(key, str) or not key.startswith(prefix):
            log.warning("attachments.reject_key", session_id=str(session_id), key=key)
            continue
        rest = key[len(prefix) :]
        parts = rest.split("/", 1)
        if len(parts) != 2 or not parts[0] or ".." in parts:
            continue
        uid, fname = parts[0], parts[1]
        if not fname or ".." in Path(fname).parts:
            continue
        try:
            uuid.UUID(uid)
        except ValueError:
            continue
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int):
            try:
                size = int(size)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        if size < 0 or size > max_b:
            continue
        filename = item.get("filename")
        mime = item.get("mime")
        if not isinstance(filename, str) or not filename.strip():
            filename = Path(fname).name
        if not isinstance(mime, str):
            mime = "application/octet-stream"
        out.append(
            {
                "id": uid,
                "filename": filename.strip()[:500],
                "mime": mime[:200],
                "size": size,
                "s3_key": key,
            }
        )
    return out


async def put_upload_object(*, key: str, body: bytes, content_type: str) -> None:
    settings = get_settings()
    if not s3_configured(settings):
        raise HTTPException(status_code=503, detail="对象存储未配置，无法上传附件")

    import aioboto3  # type: ignore[import-untyped]

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    ) as s3c:
        await s3c.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type or "application/octet-stream",
        )


async def fetch_object_bytes(key: str) -> bytes | None:
    if not key or ".." in key:
        return None
    settings = get_settings()
    if not s3_configured(settings):
        return None
    import aioboto3  # type: ignore[import-untyped]

    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        ) as s3c:
            resp = await s3c.get_object(Bucket=settings.s3_bucket, Key=key)
            body = await resp["Body"].read()
            return body
    except Exception as exc:  # noqa: BLE001
        log.warning("attachments.s3_get_failed", key=key, error=str(exc))
        return None


def extract_pdf_text(data: bytes, *, max_pages: int = 80, max_chars: int = 120_000) -> str:
    """从 PDF 字节中提取纯文本（供轻量直接回答等多模态兜底）；失败返回空串。"""
    if not data:
        return ""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        chunks: list[str] = []
        n = min(len(reader.pages), max_pages)
        for i in range(n):
            page = reader.pages[i]
            chunks.append(page.extract_text() or "")
        out = "\n".join(chunks).strip()
        if len(out) > max_chars:
            return out[:max_chars] + "\n…（PDF 文本过长已截断）"
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("attachments.pdf_extract_failed", error=str(exc))
        return ""


async def delete_session_uploads_prefix(session_id: uuid.UUID) -> None:
    """删除本会话下 uploads 前缀的所有对象（硬删会话时调用）。"""
    settings = get_settings()
    if not s3_configured(settings):
        return
    prefix = uploads_key_prefix(session_id)
    import aioboto3  # type: ignore[import-untyped]

    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        ) as s3c:
            token: str | None = None
            while True:
                kwargs: dict[str, Any] = {"Bucket": settings.s3_bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = await s3c.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    k = obj.get("Key")
                    if isinstance(k, str) and k:
                        try:
                            await s3c.delete_object(Bucket=settings.s3_bucket, Key=k)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("attachments.s3_delete_failed", key=k, error=str(exc))
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
                if not token:
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("attachments.prefix_delete_failed", session_id=str(session_id), error=str(exc))


async def materialize_attachments_to_sandbox(
    session_id: uuid.UUID,
    run_id: str | None,
    attachments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """从 S3 拉取附件并写入沙箱 uploads/…；返回成功行与错误文案。"""
    settings = get_settings()
    sandbox_id = str(session_id)
    successes: list[dict[str, Any]] = []
    errors: list[str] = []

    client = get_client()
    for att in attachments:
        key = att.get("s3_key")
        fname = att.get("filename") or "file"
        mime = att.get("mime") or "application/octet-stream"
        if not isinstance(key, str):
            errors.append(f"跳过无效附件: {fname}")
            continue
        data = await fetch_object_bytes(key)
        if data is None:
            errors.append(f"无法读取附件（存储不可用或对象不存在）: {fname}")
            continue
        if len(data) > settings.attachment_max_bytes:
            errors.append(f"附件过大: {fname}")
            continue
        rel = f"uploads/{att.get('id', 'misc')}/{safe_filename(str(fname))}"
        b64 = base64.b64encode(data).decode("ascii")
        tool = await client.invoke(
            "filesystem",
            sandbox_id=sandbox_id,
            session_id=sandbox_id,
            run_id=run_id,
            args={"action": "write", "path": rel, "binary_base64": b64},
        )
        if not tool.ok:
            errors.append(f"写入沙箱失败 {fname}: {tool.error or 'unknown'}")
            continue
        successes.append(
            {
                "filename": fname,
                "mime": mime,
                "sandbox_path": rel,
                "s3_key": key,
                "size": len(data),
            }
        )
    return successes, errors
