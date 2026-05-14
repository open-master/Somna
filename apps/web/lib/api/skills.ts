import { authHeaders } from "@/lib/auth/cookie";

const SKILLS_BASE =
  typeof window !== "undefined"
    ? "/api/v1/skills"
    : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/skills`;

async function readErr(res: Response, fb: string): Promise<string> {
  try {
    const j = (await res.json()) as { detail?: unknown };
    if (typeof j.detail === "string") return j.detail;
  } catch {
    /* ignore */
  }
  return fb;
}

export interface SkillRow {
  id: string;
  owner_user_id?: string | null;
  owner_email?: string | null;
  name: string;
  title?: string | null;
  description: string;
  visibility: "private" | "shared" | "official";
  source: string;
  status: "draft" | "active" | "archived";
  version: number;
  installed?: boolean;
  enabled?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SkillDetail extends SkillRow {
  skill_md: string;
  files: Record<string, string>;
}

export async function listSkills(scope: "my" | "market"): Promise<SkillRow[]> {
  const res = await fetch(`${SKILLS_BASE}?scope=${scope}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await readErr(res, `listSkills: ${res.status}`));
  return res.json();
}

export async function createSkill(skill_md: string, visibility: "private" | "shared" = "private"): Promise<SkillRow> {
  const res = await fetch(SKILLS_BASE, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ skill_md, visibility }),
  });
  if (!res.ok) throw new Error(await readErr(res, `createSkill: ${res.status}`));
  return res.json();
}

export async function uploadSkill(file: File, visibility: "private" | "shared" = "private"): Promise<SkillRow> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("visibility", visibility);
  const res = await fetch(`${SKILLS_BASE}/upload`, {
    method: "POST",
    headers: { ...authHeaders() },
    body: fd,
  });
  if (!res.ok) throw new Error(await readErr(res, `uploadSkill: ${res.status}`));
  return res.json();
}

export async function createSkillFromSession(
  sessionId: string,
  visibility: "private" | "shared" = "private",
): Promise<SkillRow> {
  const res = await fetch(`${SKILLS_BASE}/from-session`, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ session_id: sessionId, visibility }),
  });
  if (!res.ok) throw new Error(await readErr(res, `createSkillFromSession: ${res.status}`));
  return res.json();
}

export async function getSkill(id: string): Promise<SkillDetail> {
  const res = await fetch(`${SKILLS_BASE}/${encodeURIComponent(id)}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await readErr(res, `getSkill: ${res.status}`));
  return res.json();
}

export async function setSkillEnabled(id: string, enabled: boolean): Promise<SkillRow> {
  const res = await fetch(`${SKILLS_BASE}/${encodeURIComponent(id)}/enabled`, {
    method: "PATCH",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(await readErr(res, `setSkillEnabled: ${res.status}`));
  return res.json();
}

export async function setSkillVisibility(
  id: string,
  visibility: "private" | "shared" | "official",
): Promise<SkillRow> {
  const res = await fetch(`${SKILLS_BASE}/${encodeURIComponent(id)}/visibility`, {
    method: "PATCH",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ visibility }),
  });
  if (!res.ok) throw new Error(await readErr(res, `setSkillVisibility: ${res.status}`));
  return res.json();
}

export async function deleteSkill(id: string): Promise<void> {
  const res = await fetch(`${SKILLS_BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await readErr(res, `deleteSkill: ${res.status}`));
}
