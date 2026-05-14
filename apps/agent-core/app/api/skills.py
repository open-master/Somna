"""Skill management API for Claude-compatible Skill packages."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_current_user
from app.services.skills import (
    create_skill,
    delete_skill,
    generate_skill_from_session,
    get_skill_detail,
    list_market_skills,
    list_my_skills,
    package_from_markdown,
    package_from_zip,
    set_skill_enabled,
    update_skill_visibility,
)

router = APIRouter(prefix="/v1/skills", tags=["skills"])


class SkillCreateReq(BaseModel):
    skill_md: str = Field(min_length=1)
    visibility: str | None = Field(default=None, pattern="^(private|shared|official)$")


class SkillVisibilityReq(BaseModel):
    visibility: str = Field(pattern="^(private|shared|official)$")


class SkillEnabledReq(BaseModel):
    enabled: bool = True


class SkillFromSessionReq(BaseModel):
    session_id: uuid.UUID
    visibility: str | None = Field(default=None, pattern="^(private|shared|official)$")


@router.get("")
async def list_skills(
    scope: str = "my",
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    if scope == "my":
        return await list_my_skills(user)
    if scope == "market":
        return await list_market_skills(user)
    raise HTTPException(status_code=400, detail="scope must be my or market")


@router.post("", status_code=201)
async def create_skill_from_markdown(
    req: SkillCreateReq,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    package = package_from_markdown(req.skill_md)
    return await create_skill(user=user, package=package, visibility=req.visibility, source="manual")


@router.post("/upload", status_code=201)
async def upload_skill_package(
    file: UploadFile = File(...),
    visibility: str | None = Form(default=None),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    name = file.filename or "skill"
    body = await file.read()
    lowered = name.lower()
    if lowered.endswith((".zip", ".skill")):
        package = package_from_zip(body)
    elif name == "SKILL.md" or lowered.endswith(".md"):
        try:
            skill_md = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="SKILL.md 必须是 UTF-8 文本") from exc
        package = package_from_markdown(skill_md)
    else:
        raise HTTPException(status_code=400, detail="仅支持 .skill、.zip 或 SKILL.md")
    return await create_skill(user=user, package=package, visibility=visibility, source="upload")


@router.post("/from-session", status_code=201)
async def create_skill_from_session(
    req: SkillFromSessionReq,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    package = await generate_skill_from_session(req.session_id, user)
    return await create_skill(
        user=user,
        package=package,
        visibility=req.visibility,
        source="generated",
        status="active",
    )


@router.get("/{skill_id}")
async def get_skill(skill_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)) -> dict:
    return await get_skill_detail(skill_id, user)


@router.patch("/{skill_id}/enabled")
async def patch_skill_enabled(
    skill_id: uuid.UUID,
    req: SkillEnabledReq,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return await set_skill_enabled(skill_id, user, req.enabled)


@router.patch("/{skill_id}/visibility")
async def patch_skill_visibility(
    skill_id: uuid.UUID,
    req: SkillVisibilityReq,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    return await update_skill_visibility(skill_id, user, req.visibility)


@router.delete("/{skill_id}", status_code=204)
async def remove_skill(skill_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)) -> None:
    await delete_skill(skill_id, user)
