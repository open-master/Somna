import { authHeaders } from "@/lib/auth/cookie";

const POINTS_BASE =
  typeof window !== "undefined"
    ? "/api/v1/points"
    : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/points`;

async function readApiError(res: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) =>
            item && typeof item === "object" && "msg" in item
              ? String((item as { msg: unknown }).msg)
              : "",
          )
          .filter(Boolean);
        if (messages.length) return messages.join("，");
      }
    }
  } catch {
    /* ignore non-JSON errors */
  }
  return fallback;
}

async function pointsFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${POINTS_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) throw new Error(await readApiError(res, `请求失败（${res.status}）`));
  return res.json() as Promise<T>;
}

export interface PlanPointAmounts {
  daily_points: number;
  monthly_points: number;
}

export interface PointAccount {
  user_id: string;
  email: string;
  username: string;
  role: string;
  plan_type: "free" | "basic" | "pro";
  plan_expire_at: string | null;
  daily_points: number;
  monthly_points: number;
  permanent_points: number;
  total_points: number;
  daily_reset_amount: number;
  monthly_points_cap: number;
  last_daily_reset_at: string | null;
  plan_catalog: Record<"free" | "basic" | "pro", PlanPointAmounts>;
}

export interface PointTransaction {
  id: string;
  amount: number;
  total_after: number;
  type: string;
  description: string;
  created_at: string;
}

export interface PointTransactionList {
  items: PointTransaction[];
  total: number;
}

export interface InviteCode {
  id: string;
  code: string;
  code_type: string;
  plan_id: string | null;
  topup_amount: number | null;
  note: string | null;
  consumed_by_user_id: string | null;
  consumed_by_email: string | null;
  consumed_at: string | null;
  created_at: string;
}

export interface InviteCodeList {
  items: InviteCode[];
  total: number;
}

export interface AdminPointAccount {
  user_id: string;
  email: string;
  role: string;
  account_status: string;
  plan_type: string;
  daily_points: number;
  monthly_points: number;
  permanent_points: number;
  total_points: number;
}

export interface AdminPointAccountList {
  items: AdminPointAccount[];
  total: number;
}

export interface BillingConfig {
  version: number;
  task_base: Record<"chat" | "research" | "content_build" | "code_build" | "operate" | "media", number>;
  effort_multiplier: Record<"low" | "medium" | "high", number>;
  model_meter: {
    reserve_points: number;
    input_tokens_per_point: number;
    output_tokens_per_point: number;
  };
  tool_costs: {
    web_search_batch: number;
    visual_critique: number;
    image_per_output: number;
    tts_per_1000_chars: number;
    video_default_per_output: number;
    external_side_effect: number;
  };
  reservation_ttl_seconds: number;
}

export interface BillingCatalog {
  config: BillingConfig;
  defaults: BillingConfig;
}

export function getPointAccount(): Promise<PointAccount> {
  return pointsFetch<PointAccount>("/account");
}

export function getPointTransactions(limit = 50): Promise<PointTransactionList> {
  return pointsFetch<PointTransactionList>(`/transactions?limit=${limit}`);
}

export function getBillingCatalog(): Promise<BillingCatalog> {
  return pointsFetch<BillingCatalog>("/billing-catalog");
}

export function upgradePlan(planId: "basic" | "pro", inviteCode: string): Promise<PointAccount> {
  return pointsFetch<PointAccount>("/upgrade", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId, invite_code: inviteCode }),
  });
}

export function topupPermanentPoints(amount: number, inviteCode: string): Promise<PointAccount> {
  return pointsFetch<PointAccount>("/permanent-topup", {
    method: "POST",
    body: JSON.stringify({ amount, invite_code: inviteCode }),
  });
}

export function adminListPointAccounts(query = ""): Promise<AdminPointAccountList> {
  const params = new URLSearchParams({ limit: "100" });
  if (query.trim()) params.set("query", query.trim());
  return pointsFetch<AdminPointAccountList>(`/admin/accounts?${params.toString()}`);
}

export function adminAdjustPoints(body: {
  user_id: string;
  amount: number;
  description: string;
}): Promise<AdminPointAccount> {
  return pointsFetch<AdminPointAccount>("/admin/adjust", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function adminGetBillingCatalog(): Promise<BillingCatalog> {
  return pointsFetch<BillingCatalog>("/admin/billing-catalog");
}

export function adminUpdateBillingCatalog(config: BillingConfig): Promise<BillingCatalog> {
  return pointsFetch<BillingCatalog>("/admin/billing-catalog", {
    method: "PATCH",
    body: JSON.stringify({ config }),
  });
}

export function adminResetBillingCatalog(): Promise<BillingCatalog> {
  return pointsFetch<BillingCatalog>("/admin/billing-catalog/reset", { method: "POST" });
}

export function adminListInviteCodes(filters: {
  code_type?: string;
  status?: "all" | "unused" | "used";
} = {}): Promise<InviteCodeList> {
  const params = new URLSearchParams({ limit: "200" });
  if (filters.code_type) params.set("code_type", filters.code_type);
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  return pointsFetch<InviteCodeList>(`/admin/invite-codes?${params.toString()}`);
}

export function adminCreateInviteCodes(body: {
  code_type: string;
  quantity: number;
  note?: string;
}): Promise<InviteCode[]> {
  return pointsFetch<InviteCode[]>("/admin/invite-codes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
