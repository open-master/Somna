const AUTH_BASE =
  typeof window !== "undefined"
    ? "/api/v1/auth"
    : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/auth`;

const CREDENTIALS: RequestCredentials = "same-origin";

async function readApiError(res: Response, fallback: string): Promise<string> {
  try {
    const j: unknown = await res.json();
    if (j && typeof j === "object" && "detail" in j) {
      const d = (j as { detail: unknown }).detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) {
        const parts = d
          .map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : ""))
          .filter(Boolean);
        if (parts.length) return parts.join(", ");
      }
    }
  } catch {
    /* ignore */
  }
  try {
    const t = await res.text();
    if (t) return t;
  } catch {
    /* ignore */
  }
  return fallback;
}

export interface AuthUser {
  id: string;
  email: string;
  role: string;
  account_status?: string;
  username?: string | null;
  has_password?: boolean;
}

export interface TokenResponse {
  access_token?: string;
  user: AuthUser;
}

export async function sendRegistrationCode(email: string): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/send-registration-code`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: email.trim() }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `send-registration-code: ${res.status}`));
}

export async function sendLoginCode(email: string): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/send-login-code`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: email.trim() }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `send-login-code: ${res.status}`));
}

export async function registerRequest(email: string, password: string, code: string): Promise<TokenResponse> {
  const res = await fetch(`${AUTH_BASE}/register`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: email.trim(), password, code: code.trim() }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `register: ${res.status}`));
  return res.json();
}

export async function loginRequest(email: string, password: string): Promise<TokenResponse> {
  const res = await fetch(`${AUTH_BASE}/login`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: email.trim(), password }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `login: ${res.status}`));
  return res.json();
}

export async function loginCodeRequest(email: string, code: string): Promise<TokenResponse> {
  const res = await fetch(`${AUTH_BASE}/login-code`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: email.trim(), code: code.trim() }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `login-code: ${res.status}`));
  return res.json();
}

export async function googleAuthRequest(credential: string): Promise<TokenResponse> {
  const res = await fetch(`${AUTH_BASE}/google`, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ credential }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `google: ${res.status}`));
  return res.json();
}

export async function meRequest(): Promise<AuthUser | null> {
  const res = await fetch(`${AUTH_BASE}/me`, {
    credentials: CREDENTIALS,
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json();
}

export async function updateMyProfile(username: string): Promise<AuthUser> {
  const res = await fetch(`${AUTH_BASE}/me`, {
    method: "PATCH",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) throw new Error(await readApiError(res, `update profile: ${res.status}`));
  return res.json();
}

export async function changeMyPassword(body: {
  current_password?: string;
  new_password: string;
}): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/me/password`, {
    method: "PUT",
    credentials: CREDENTIALS,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readApiError(res, `change password: ${res.status}`));
}
