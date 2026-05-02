import { authHeaders } from "@/lib/auth/cookie";

const ADMIN_USERS_BASE =
  typeof window !== "undefined"
    ? "/api/v1/admin/users"
    : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/admin/users`;

async function readErr(res: Response, fb: string): Promise<string> {
  try {
    const t = await res.text();
    if (t) return t;
  } catch {
    /* ignore */
  }
  return fb;
}

export interface AdminUserRow {
  id: string;
  email: string;
  role: string;
  account_status: string;
  created_at?: string | null;
  has_password: boolean;
  has_google: boolean;
}

export async function adminListUsers(): Promise<AdminUserRow[]> {
  const res = await fetch(ADMIN_USERS_BASE, { cache: "no-store", headers: { ...authHeaders() } });
  if (!res.ok) throw new Error(await readErr(res, `adminListUsers: ${res.status}`));
  return res.json();
}

export async function adminCreateUser(body: {
  email: string;
  password: string;
  role?: string;
  account_status?: string;
}): Promise<AdminUserRow> {
  const res = await fetch(ADMIN_USERS_BASE, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readErr(res, `adminCreateUser: ${res.status}`));
  return res.json();
}

export async function adminPatchUser(
  id: string,
  patch: { role?: string; account_status?: string },
): Promise<AdminUserRow> {
  const res = await fetch(`${ADMIN_USERS_BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await readErr(res, `adminPatchUser: ${res.status}`));
  return res.json();
}

export async function adminDeleteUser(id: string): Promise<void> {
  const res = await fetch(`${ADMIN_USERS_BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await readErr(res, `adminDeleteUser: ${res.status}`));
}
