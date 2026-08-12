"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getBillingCatalog,
  getPointAccount,
  getPointTransactions,
  topupPermanentPoints,
  upgradePlan,
  type BillingCatalog,
  type PointAccount,
  type PointTransaction,
} from "@/lib/api/points";
import type { AuthUser } from "@/lib/api/auth";
import { changeMyPassword, updateMyProfile } from "@/lib/api/auth";
import { cn } from "@/lib/utils/cn";

const PLAN_META = {
  free: { zh: "免费版", en: "Free Plan" },
  basic: { zh: "基础版", en: "Basic Plan" },
  pro: { zh: "专业版", en: "Pro Plan" },
} as const;

const PLAN_ORDER = { free: 0, basic: 1, pro: 2 } as const;
const TOPUP_AMOUNTS = [1000, 2000, 3000, 4000, 5000] as const;

function formatPoints(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

function ErrorMessage({ children }: { children: string }) {
  return <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{children}</p>;
}

function UsernameDialog({
  open,
  initialValue,
  onClose,
  onSuccess,
}: {
  open: boolean;
  initialValue: string;
  onClose: () => void;
  onSuccess: (username: string) => void;
}) {
  const [username, setUsername] = useState(initialValue);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setUsername(initialValue);
      setError("");
      setSubmitting(false);
    }
  }, [initialValue, open]);

  async function submit() {
    const value = username.trim();
    if (!value) {
      setError("用户名不能为空");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const profile = await updateMyProfile(value);
      onSuccess(profile.username || value);
    } catch (err) {
      setError(err instanceof Error ? err.message : "用户名保存失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && !submitting && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[61] w-[min(420px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-background p-5 shadow-xl outline-none">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-lg font-semibold">编辑用户名</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">用户名用于账户展示，不会改变登录邮箱。</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button type="button" size="icon" variant="ghost" className="size-8" disabled={submitting} aria-label="关闭用户名编辑窗口"><X className="size-4" /></Button>
            </Dialog.Close>
          </div>
          {error ? <div className="mt-4"><ErrorMessage>{error}</ErrorMessage></div> : null}
          <Input
            className="mt-4"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void submit()}
            maxLength={50}
            autoComplete="nickname"
            disabled={submitting}
            autoFocus
          />
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={submitting} onClick={onClose}>取消</Button>
            <Button type="button" disabled={submitting || !username.trim()} onClick={() => void submit()}>{submitting ? "保存中…" : "保存"}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PasswordDialog({
  open,
  hasPassword,
  onClose,
  onSuccess,
}: {
  open: boolean;
  hasPassword: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setSubmitting(false);
      setError("");
    }
  }, [open]);

  async function submit() {
    if (hasPassword && !currentPassword) {
      setError("请输入当前密码");
      return;
    }
    if (newPassword.length < 6) {
      setError("新密码至少需要 6 位");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await changeMyPassword({
        ...(hasPassword ? { current_password: currentPassword } : {}),
        new_password: newPassword,
      });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "密码修改失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && !submitting && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[61] w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-background p-5 shadow-xl outline-none">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-lg font-semibold">{hasPassword ? "重置密码" : "设置登录密码"}</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                {hasPassword ? "验证当前密码后设置新密码。" : "设置后可使用邮箱和密码登录。"}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button type="button" size="icon" variant="ghost" className="size-8" disabled={submitting} aria-label="关闭密码窗口"><X className="size-4" /></Button>
            </Dialog.Close>
          </div>
          {error ? <div className="mt-4"><ErrorMessage>{error}</ErrorMessage></div> : null}
          <div className="mt-4 space-y-3">
            {hasPassword ? (
              <Input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} placeholder="当前密码" autoComplete="current-password" disabled={submitting} autoFocus />
            ) : null}
            <Input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} placeholder="新密码（至少 6 位）" autoComplete="new-password" disabled={submitting} autoFocus={!hasPassword} />
            <Input type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void submit()} placeholder="再次输入新密码" autoComplete="new-password" disabled={submitting} />
          </div>
          <p className="mt-3 text-xs text-muted-foreground">密码修改后，其他已登录设备不会自动退出。</p>
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={submitting} onClick={onClose}>取消</Button>
            <Button type="button" disabled={submitting || newPassword.length < 6 || !confirmation} onClick={() => void submit()}>{submitting ? "保存中…" : "确认"}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function useAccount() {
  const [account, setAccount] = useState<PointAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setAccount(await getPointAccount());
    } catch (err) {
      setError(err instanceof Error ? err.message : "账户信息加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { account, setAccount, loading, error, load };
}

export function SettingsPointAccount({
  profile,
  onGetPoints,
}: {
  profile: AuthUser | null;
  onGetPoints: () => void;
}) {
  const { account, setAccount, loading, error } = useAccount();
  const [usernameOpen, setUsernameOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [message, setMessage] = useState("");
  const email = account?.email ?? profile?.email ?? "";
  const username = account?.username ?? email.split("@", 1)[0] ?? "用户";
  const initial = (username.charAt(0) || "U").toUpperCase();
  const plan = account?.plan_type ?? "free";

  return (
    <div className="mx-auto w-full max-w-4xl space-y-8 py-1">
      <div className="flex items-center gap-5">
        <div className="flex size-20 shrink-0 items-center justify-center rounded-full bg-primary/10 text-3xl font-semibold text-primary">
          {initial}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-xl font-semibold">{username || "用户"}</p>
            <Button type="button" size="icon" variant="ghost" className="size-8 shrink-0" aria-label="编辑用户名" onClick={() => setUsernameOpen(true)}>
              <Pencil className="size-4" />
            </Button>
          </div>
          <p className="mt-1 truncate text-base text-muted-foreground">{email || "—"}</p>
          {account ? (
            <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => setPasswordOpen(true)}>
              <KeyRound className="size-3.5" />
              {account.has_password ? "重置密码" : "设置登录密码"}
            </Button>
          ) : null}
        </div>
      </div>

      {error ? <ErrorMessage>{error}</ErrorMessage> : null}
      {message ? <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{message}</p> : null}
      {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

      {account ? (
        <section className="rounded-xl border bg-card p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold">
                {PLAN_META[plan].zh} · {PLAN_META[plan].en}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">当前积分与套餐状态</p>
            </div>
            <Button type="button" onClick={onGetPoints}>获得积分</Button>
          </div>
          <dl className="mt-7 space-y-3 text-sm">
            <div className="flex items-center justify-between font-semibold">
              <dt>总积分</dt>
              <dd className="tabular-nums">{formatPoints(account.total_points)}</dd>
            </div>
            <div className="flex items-center justify-between text-muted-foreground">
              <dt>每日积分</dt>
              <dd className="tabular-nums">{formatPoints(account.daily_points)}</dd>
            </div>
            <div className="flex items-center justify-between text-muted-foreground">
              <dt>每月积分</dt>
              <dd className="tabular-nums">
                {formatPoints(account.monthly_points)} / {formatPoints(account.monthly_points_cap)}
              </dd>
            </div>
            <div className="flex items-center justify-between text-muted-foreground">
              <dt>永久积分</dt>
              <dd className="tabular-nums">{formatPoints(account.permanent_points)}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      <UsernameDialog
        open={usernameOpen}
        initialValue={username}
        onClose={() => setUsernameOpen(false)}
        onSuccess={(nextUsername) => {
          setAccount((current) => current ? { ...current, username: nextUsername } : current);
          setUsernameOpen(false);
          setMessage("用户名已更新");
        }}
      />
      <PasswordDialog
        open={passwordOpen}
        hasPassword={account?.has_password ?? false}
        onClose={() => setPasswordOpen(false)}
        onSuccess={() => {
          setAccount((current) => current ? { ...current, has_password: true } : current);
          setPasswordOpen(false);
          setMessage(account?.has_password ? "密码已重置" : "登录密码已设置");
        }}
      />
    </div>
  );
}

export function SettingsPointUsage() {
  const [transactions, setTransactions] = useState<PointTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getPointTransactions(100);
      setTransactions(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "积分流水加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-5xl py-1">
      <div className="mb-5 flex items-end justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold">积分流水</h3>
          <p className="mt-1 text-xs text-muted-foreground">共 {total} 条记录</p>
        </div>
        <Button type="button" size="sm" variant="secondary" disabled={loading} onClick={() => void load()}>
          {loading ? "加载中…" : "刷新"}
        </Button>
      </div>
      {error ? <div className="mb-4"><ErrorMessage>{error}</ErrorMessage></div> : null}
      <div className="overflow-hidden rounded-xl border bg-card">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="px-5 py-4 font-semibold">详情</th>
              <th className="px-5 py-4 font-semibold">日期</th>
              <th className="px-5 py-4 text-right font-semibold">积分变更</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((item) => (
              <tr key={item.id} className="border-t">
                <td className="px-5 py-4">{item.description}</td>
                <td className="px-5 py-4 text-muted-foreground">{formatDate(item.created_at)}</td>
                <td
                  className={cn(
                    "px-5 py-4 text-right font-medium tabular-nums",
                    item.amount > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-foreground",
                  )}
                >
                  {item.amount > 0 ? "+" : ""}{formatPoints(item.amount)}
                </td>
              </tr>
            ))}
            {!loading && transactions.length === 0 ? (
              <tr><td className="px-5 py-12 text-center text-muted-foreground" colSpan={3}>暂无积分流水</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type PendingAction =
  | { type: "upgrade"; plan: "basic" | "pro" }
  | { type: "topup"; amount: number };

function InviteCodeDialog({
  action,
  onClose,
  onSuccess,
}: {
  action: PendingAction | null;
  onClose: () => void;
  onSuccess: (account: PointAccount, message: string) => void;
}) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setCode("");
    setError("");
    setSubmitting(false);
  }, [action]);

  async function confirm() {
    if (!action || !code.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      if (action.type === "upgrade") {
        const account = await upgradePlan(action.plan, code.trim());
        onSuccess(account, `已成功升级到 ${action.plan === "pro" ? "专业版" : "基础版"}`);
      } else {
        const account = await topupPermanentPoints(action.amount, code.trim());
        onSuccess(account, `已成功充值 ${formatPoints(action.amount)} 永久积分`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open={action !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[61] w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-background p-5 shadow-xl outline-none">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-lg font-semibold">请输入邀请码</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                {action?.type === "upgrade"
                  ? `升级${action.plan === "pro" ? "专业版 · Pro Plan" : "基础版 · Basic Plan"}`
                  : `充值 ${formatPoints(action?.amount ?? 0)} 永久积分`}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button type="button" size="icon" variant="ghost" className="size-8" aria-label="关闭邀请码输入框">
                <X className="size-4" />
              </Button>
            </Dialog.Close>
          </div>
          {error ? <div className="mt-4"><ErrorMessage>{error}</ErrorMessage></div> : null}
          <Input
            value={code}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
            onKeyDown={(event) => event.key === "Enter" && void confirm()}
            className="mt-4 font-mono uppercase tracking-wide"
            placeholder="SM-XXXXX-XXXXX"
            disabled={submitting}
            autoFocus
          />
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={submitting} onClick={onClose}>取消</Button>
            <Button type="button" disabled={!code.trim() || submitting} onClick={() => void confirm()}>
              {submitting ? "处理中…" : "确认"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function SettingsPointPlans() {
  const { account, setAccount, loading, error } = useAccount();
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [message, setMessage] = useState("");
  const [billing, setBilling] = useState<BillingCatalog | null>(null);

  useEffect(() => {
    void getBillingCatalog().then(setBilling).catch(() => setBilling(null));
  }, []);

  function planButton(plan: "free" | "basic" | "pro") {
    if (!account) return { label: "加载中…", disabled: true };
    if (plan === account.plan_type) return { label: "当前计划", disabled: true };
    if (plan === "free") return { label: "默认套餐", disabled: true };
    if (PLAN_ORDER[plan] < PLAN_ORDER[account.plan_type]) return { label: "已有更高套餐", disabled: true };
    return { label: "升级", disabled: false };
  }

  return (
    <div className="mx-auto w-full max-w-5xl py-1">
      {error ? <div className="mb-4"><ErrorMessage>{error}</ErrorMessage></div> : null}
      {message ? (
        <p className="mb-4 rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{message}</p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-4">
        {(["free", "basic", "pro"] as const).map((plan) => {
          const state = planButton(plan);
          const amounts = account?.plan_catalog[plan];
          return (
            <section
              key={plan}
              className={cn(
                "flex min-h-[350px] flex-col rounded-xl border bg-card p-5",
                plan === "pro" && "border-primary",
              )}
            >
              <h3 className="text-xl font-semibold">{PLAN_META[plan].zh}</h3>
              <div className="mt-6 space-y-2 text-base">
                <p>{amounts ? formatPoints(amounts.daily_points) : "—"} 每日积分</p>
                <p>{amounts ? formatPoints(amounts.monthly_points) : "—"} 每月积分</p>
              </div>
              <Button
                type="button"
                variant={state.disabled ? "secondary" : "default"}
                disabled={state.disabled || loading}
                className="mt-7 w-full"
                onClick={() => plan !== "free" && setPendingAction({ type: "upgrade", plan })}
              >
                {state.label}
              </Button>
            </section>
          );
        })}

        <section className="flex min-h-[350px] flex-col rounded-xl border border-amber-400/60 bg-amber-500/[0.04] p-5">
          <h3 className="text-xl font-semibold">永久积分</h3>
          <p className="mt-2 text-base leading-6 text-muted-foreground">不会清零，套餐外可持续使用</p>
          <div className="mt-5 grid gap-2">
            {TOPUP_AMOUNTS.map((amount) => (
              <Button
                key={amount}
                type="button"
                variant="outline"
                onClick={() => setPendingAction({ type: "topup", amount })}
              >
                充值 {formatPoints(amount)}
              </Button>
            ))}
          </div>
        </section>
      </div>

      {billing ? (
        <section className="mt-6 rounded-xl border bg-card p-5">
          <h3 className="font-semibold">任务计费说明</h3>
          <p className="mt-1 text-xs text-muted-foreground">实际扣分 = 任务基础分 + 模型资源分 + 成功发生的付费能力；执行前会冻结预算，未使用部分自动退回。</p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {([
              ["对话与内容理解", billing.config.task_base.chat],
              ["搜索与分析", billing.config.task_base.research],
              ["内容与办公交付", billing.config.task_base.content_build],
              ["代码与工程构建", billing.config.task_base.code_build],
              ["操作与自动化", billing.config.task_base.operate],
              ["生成式媒体基础", billing.config.task_base.media],
            ] as const).map(([label, points]) => (
              <div key={label} className="flex justify-between rounded-lg border px-3 py-2 text-sm"><span>{label}</span><span className="font-medium tabular-nums">{points} 分起</span></div>
            ))}
          </div>
          <p className="mt-4 text-xs text-muted-foreground">联网搜索 {billing.config.tool_costs.web_search_batch} 分/次 · 图片 {billing.config.tool_costs.image_per_output} 分/张 · 视觉评审 {billing.config.tool_costs.visual_critique} 分/次 · 语音 {billing.config.tool_costs.tts_per_1000_chars} 分/千字 · 视频 {billing.config.tool_costs.video_default_per_output} 分/条起</p>
        </section>
      ) : null}

      <InviteCodeDialog
        action={pendingAction}
        onClose={() => setPendingAction(null)}
        onSuccess={(next, successMessage) => {
          setAccount(next);
          setMessage(successMessage);
          setPendingAction(null);
        }}
      />
    </div>
  );
}
