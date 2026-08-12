"use client";

import { Check, Copy, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  adminAdjustPoints,
  adminCreateInviteCodes,
  adminGetBillingCatalog,
  adminListInviteCodes,
  adminListPointAccounts,
  adminResetBillingCatalog,
  adminUpdateBillingCatalog,
  type AdminPointAccount,
  type BillingCatalog,
  type BillingConfig,
  type InviteCode,
} from "@/lib/api/points";
import { cn } from "@/lib/utils/cn";

const INVITE_OPTIONS = [
  { value: "sub_basic", label: "订阅基础版" },
  { value: "sub_pro", label: "订阅专业版" },
  { value: "topup_1000", label: "永久积分充值 1,000" },
  { value: "topup_2000", label: "永久积分充值 2,000" },
  { value: "topup_3000", label: "永久积分充值 3,000" },
  { value: "topup_4000", label: "永久积分充值 4,000" },
  { value: "topup_5000", label: "永久积分充值 5,000" },
] as const;

const TASK_PRICE_FIELDS: { key: keyof BillingConfig["task_base"]; label: string }[] = [
  { key: "chat", label: "对话与理解" },
  { key: "research", label: "搜索与分析" },
  { key: "content_build", label: "内容与办公交付" },
  { key: "code_build", label: "代码与工程构建" },
  { key: "operate", label: "操作与自动化" },
  { key: "media", label: "生成式媒体基础" },
];

const TOOL_PRICE_FIELDS: { key: keyof BillingConfig["tool_costs"]; label: string }[] = [
  { key: "web_search_batch", label: "联网搜索/次" },
  { key: "visual_critique", label: "视觉评审/次" },
  { key: "image_per_output", label: "图片/张" },
  { key: "tts_per_1000_chars", label: "语音/千字" },
  { key: "video_default_per_output", label: "视频/条" },
  { key: "external_side_effect", label: "外部操作/次" },
];

function formatPoints(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function labelOf(codeType: string): string {
  return INVITE_OPTIONS.find((item) => item.value === codeType)?.label ?? codeType;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

function Feedback({ type, children }: { type: "success" | "error"; children: string }) {
  return (
    <p className={cn(
      "rounded-lg px-3 py-2 text-sm",
      type === "success" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive",
    )}>
      {children}
    </p>
  );
}

function CopyButton({ item }: { item: InviteCode }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  async function copy() {
    await navigator.clipboard.writeText(item.code);
    setCopied(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <button type="button" className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={() => void copy()} aria-label={`复制邀请码 ${item.code}`}>
      {copied ? <Check className="size-3.5 text-emerald-600" /> : <Copy className="size-3.5" />}
    </button>
  );
}

export function SettingsInviteManagement({ currentUserId }: { currentUserId: string | null }) {
  const [codeType, setCodeType] = useState("sub_basic");
  const [quantity, setQuantity] = useState(1);
  const [note, setNote] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState<"all" | "unused" | "used">("all");
  const [codes, setCodes] = useState<InviteCode[]>([]);
  const [createdCodes, setCreatedCodes] = useState<InviteCode[]>([]);
  const [accounts, setAccounts] = useState<AdminPointAccount[]>([]);
  const [billing, setBilling] = useState<BillingCatalog | null>(null);
  const [accountQuery, setAccountQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustDescription, setAdjustDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selected = useMemo(() => accounts.find((item) => item.user_id === selectedId), [accounts, selectedId]);

  const loadCodes = useCallback(async () => {
    const result = await adminListInviteCodes({ code_type: filterType || undefined, status: filterStatus });
    setCodes(result.items);
  }, [filterStatus, filterType]);

  const loadAccounts = useCallback(async (query = "") => {
    const result = await adminListPointAccounts(query);
    setAccounts(result.items);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [, , catalog] = await Promise.all([loadCodes(), loadAccounts(), adminGetBillingCatalog()]);
      setBilling(catalog);
    } catch (err) {
      setError(err instanceof Error ? err.message : "管理员数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [loadAccounts, loadCodes]);

  function setTaskPrice(key: keyof BillingConfig["task_base"], value: number) {
    setBilling((current) => current ? {
      ...current,
      config: { ...current.config, task_base: { ...current.config.task_base, [key]: Math.max(0, value || 0) } },
    } : current);
  }

  function setToolPrice(key: keyof BillingConfig["tool_costs"], value: number) {
    setBilling((current) => current ? {
      ...current,
      config: { ...current.config, tool_costs: { ...current.config.tool_costs, [key]: Math.max(0, value || 0) } },
    } : current);
  }

  async function saveBilling() {
    if (!billing) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setBilling(await adminUpdateBillingCatalog(billing.config));
      setMessage("任务计费配置已保存，新任务即时生效");
    } catch (err) {
      setError(err instanceof Error ? err.message : "计费配置保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function resetBilling() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setBilling(await adminResetBillingCatalog());
      setMessage("已恢复 .env 定义的计费默认值");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复默认配置失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function createCodes() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await adminCreateInviteCodes({ code_type: codeType, quantity, note: note.trim() || undefined });
      setCreatedCodes(result);
      setMessage(`已生成 ${result.length} 个邀请码`);
      await loadCodes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "邀请码生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function adjust() {
    const amount = Number(adjustAmount);
    if (!selectedId || !Number.isInteger(amount) || amount === 0 || !adjustDescription.trim()) {
      setError("请选择用户，并填写非 0 整数和调整原因");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await adminAdjustPoints({ user_id: selectedId, amount, description: adjustDescription.trim() });
      setMessage(`${selected?.email ?? "用户"} 的永久积分已调整为 ${formatPoints(result.permanent_points)}`);
      setAdjustAmount("");
      setAdjustDescription("");
      await loadAccounts(accountQuery);
    } catch (err) {
      setError(err instanceof Error ? err.message : "积分调整失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      {message ? <Feedback type="success">{message}</Feedback> : null}
      {error ? <Feedback type="error">{error}</Feedback> : null}

      <section className="rounded-xl border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold">任务计费配置</h3>
            <p className="mt-1 text-xs text-muted-foreground">数据库配置覆盖 .env 默认值；运行中的任务继续使用启动时的价格快照。</p>
          </div>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="secondary" disabled={busy || !billing} onClick={() => void resetBilling()}>恢复默认</Button>
            <Button type="button" size="sm" disabled={busy || !billing} onClick={() => void saveBilling()}>保存单价</Button>
          </div>
        </div>
        {billing ? (
          <div className="mt-4 space-y-4">
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">任务基础分</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {TASK_PRICE_FIELDS.map((field) => (
                  <label key={field.key} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
                    <span>{field.label}</span>
                    <Input className="h-8 w-20 text-right" type="number" min={0} max={10000} value={billing.config.task_base[field.key]} onChange={(event) => setTaskPrice(field.key, Number(event.target.value))} />
                  </label>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">付费能力</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {TOOL_PRICE_FIELDS.map((field) => (
                  <label key={field.key} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
                    <span>{field.label}</span>
                    <Input className="h-8 w-20 text-right" type="number" min={0} max={100000} value={billing.config.tool_costs[field.key]} onChange={(event) => setToolPrice(field.key, Number(event.target.value))} />
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <label className="rounded-lg border px-3 py-2 text-xs">输入 Token/积分<Input className="mt-1 h-8" type="number" min={1} value={billing.config.model_meter.input_tokens_per_point} onChange={(event) => setBilling((current) => current ? { ...current, config: { ...current.config, model_meter: { ...current.config.model_meter, input_tokens_per_point: Math.max(1, Number(event.target.value) || 1) } } } : current)} /></label>
              <label className="rounded-lg border px-3 py-2 text-xs">输出 Token/积分<Input className="mt-1 h-8" type="number" min={1} value={billing.config.model_meter.output_tokens_per_point} onChange={(event) => setBilling((current) => current ? { ...current, config: { ...current.config, model_meter: { ...current.config.model_meter, output_tokens_per_point: Math.max(1, Number(event.target.value) || 1) } } } : current)} /></label>
              <label className="rounded-lg border px-3 py-2 text-xs">模型预留积分<Input className="mt-1 h-8" type="number" min={0} value={billing.config.model_meter.reserve_points} onChange={(event) => setBilling((current) => current ? { ...current, config: { ...current.config, model_meter: { ...current.config.model_meter, reserve_points: Math.max(0, Number(event.target.value) || 0) } } } : current)} /></label>
            </div>
          </div>
        ) : <p className="mt-4 text-sm text-muted-foreground">正在加载计费配置…</p>}
      </section>

      <section className="rounded-xl border bg-card p-4">
        <h3 className="font-semibold">生成邀请码</h3>
        <p className="mt-1 text-xs text-muted-foreground">为套餐升级或永久积分充值生成一次性兑换码。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-[190px_100px_1fr_auto]">
          <select value={codeType} onChange={(event) => setCodeType(event.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
            {INVITE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <Input type="number" min={1} max={200} value={quantity} onChange={(event) => setQuantity(Math.max(1, Math.min(200, Number(event.target.value) || 1)))} aria-label="生成数量" />
          <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="备注（可选）" maxLength={200} />
          <Button type="button" disabled={busy} onClick={() => void createCodes()}>{busy ? "处理中…" : "生成邀请码"}</Button>
        </div>
        {createdCodes.length ? (
          <div className="mt-4 flex max-h-28 flex-wrap gap-2 overflow-y-auto rounded-lg border bg-muted/30 p-3">
            {createdCodes.map((item) => (
              <span key={item.id} className="inline-flex items-center gap-1 rounded border bg-background px-2 py-1 font-mono text-xs">
                {item.code}<CopyButton item={item} />
              </span>
            ))}
          </div>
        ) : null}
      </section>

      <section className="rounded-xl border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
          <h3 className="font-semibold">邀请码记录</h3>
          <div className="flex gap-2">
            <select value={filterType} onChange={(event) => setFilterType(event.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-xs">
              <option value="">全部类型</option>
              {INVITE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select value={filterStatus} onChange={(event) => setFilterStatus(event.target.value as "all" | "unused" | "used")} className="h-8 rounded-md border border-input bg-background px-2 text-xs">
              <option value="all">全部状态</option><option value="unused">未使用</option><option value="used">已使用</option>
            </select>
            <Button type="button" size="sm" variant="secondary" disabled={loading} onClick={() => void loadCodes()}><RefreshCw className="size-3.5" />刷新</Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground"><tr><th className="px-4 py-2.5">邀请码</th><th className="px-4 py-2.5">类型</th><th className="px-4 py-2.5">状态</th><th className="px-4 py-2.5">使用人 / 备注</th><th className="px-4 py-2.5">创建时间</th></tr></thead>
            <tbody>
              {codes.map((item) => (
                <tr key={item.id} className="border-t">
                  <td className="px-4 py-3"><span className="inline-flex items-center gap-1 font-mono text-xs">{item.code}<CopyButton item={item} /></span></td>
                  <td className="px-4 py-3 text-xs">{labelOf(item.code_type)}</td>
                  <td className="px-4 py-3"><span className={cn("rounded-full px-2 py-0.5 text-xs", item.consumed_at ? "bg-amber-500/10 text-amber-700" : "bg-emerald-500/10 text-emerald-700")}>{item.consumed_at ? "已使用" : "未使用"}</span></td>
                  <td className="max-w-[220px] px-4 py-3 text-xs"><p className="truncate">{item.consumed_by_email || "—"}</p>{item.note ? <p className="truncate text-muted-foreground">{item.note}</p> : null}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">{formatDate(item.created_at)}</td>
                </tr>
              ))}
              {!loading && !codes.length ? <tr><td className="px-4 py-10 text-center text-muted-foreground" colSpan={5}>暂无邀请码</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-xl border bg-card p-4">
        <h3 className="font-semibold">永久积分调整</h3>
        <p className="mt-1 text-xs text-muted-foreground">人工调整只影响永久积分，并记录管理员操作流水。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_120px_1fr_auto]">
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm">
            <option value="">选择用户</option>
            {accounts.map((item) => <option key={item.user_id} value={item.user_id}>{item.email}{item.user_id === currentUserId ? "（当前）" : ""} · 永久 {formatPoints(item.permanent_points)}</option>)}
          </select>
          <Input type="number" value={adjustAmount} onChange={(event) => setAdjustAmount(event.target.value)} placeholder="如 500 / -50" aria-label="永久积分调整数量" />
          <Input value={adjustDescription} onChange={(event) => setAdjustDescription(event.target.value)} placeholder="调整原因（必填）" maxLength={200} />
          <Button type="button" disabled={busy} onClick={() => void adjust()}>确认调整</Button>
        </div>
        <div className="mt-4 flex items-center gap-2 border-t pt-4">
          <div className="relative max-w-sm flex-1"><Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="h-8 pl-9 text-xs" value={accountQuery} onChange={(event) => setAccountQuery(event.target.value)} placeholder="搜索用户邮箱" /></div>
          <Button type="button" size="sm" variant="secondary" onClick={() => void loadAccounts(accountQuery)}>搜索</Button>
        </div>
        <div className="mt-3 max-h-48 overflow-y-auto rounded-lg border">
          <table className="w-full min-w-[620px] text-left text-sm"><thead className="sticky top-0 bg-muted text-xs text-muted-foreground"><tr><th className="px-3 py-2">用户</th><th className="px-3 py-2">套餐</th><th className="px-3 py-2 text-right">每日 / 每月 / 永久</th><th className="px-3 py-2 text-right">总积分</th></tr></thead><tbody>
            {accounts.map((item) => <tr key={item.user_id} className="cursor-pointer border-t hover:bg-muted/30" onClick={() => setSelectedId(item.user_id)}><td className="px-3 py-2.5">{item.email}</td><td className="px-3 py-2.5 text-muted-foreground">{item.plan_type}</td><td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{formatPoints(item.daily_points)} / {formatPoints(item.monthly_points)} / {formatPoints(item.permanent_points)}</td><td className="px-3 py-2.5 text-right font-medium tabular-nums">{formatPoints(item.total_points)}</td></tr>)}
          </tbody></table>
        </div>
      </section>
    </div>
  );
}
