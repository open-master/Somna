"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminPatchUser,
  adminResetUserPassword,
  type AdminUserRow,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils/cn";

const STATUS_LABEL: Record<string, string> = { active: "活跃", disabled: "禁用" };
const ROLE_LABEL: Record<string, string> = { user: "用户", admin: "管理员" };

function UserRow({
  row,
  currentUserId,
  onUpdated,
  onDeleted,
  onResetPassword,
}: {
  row: AdminUserRow;
  currentUserId: string | null;
  onUpdated: () => void;
  onDeleted: () => void;
  onResetPassword: (user: AdminUserRow) => void;
}) {
  const [role, setRole] = useState(row.role);
  const [status, setStatus] = useState(row.account_status);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const isSelf = currentUserId === row.id;

  useEffect(() => {
    setRole(row.role);
    setStatus(row.account_status);
  }, [row.role, row.account_status, row.id]);

  async function save() {
    if (role === row.role && status === row.account_status) return;
    setSaving(true);
    try {
      await adminPatchUser(row.id, { role, account_status: status });
      onUpdated();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function del() {
    if (isSelf) {
      window.alert("不能删除当前登录账号");
      return;
    }
    if (!window.confirm(`确定删除用户 ${row.email}？其会话数据将一并删除，不可恢复。`)) return;
    setDeleting(true);
    try {
      await adminDeleteUser(row.id);
      onDeleted();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <tr className="border-b border-border/80 last:border-0">
      <td className="px-2 py-2 align-middle">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{row.email}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{row.id}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {row.has_password ? "密码" : ""}
            {row.has_password && row.has_google ? " · " : ""}
            {row.has_google ? "Google" : ""}
            {!row.has_password && !row.has_google ? "—" : ""}
          </p>
        </div>
      </td>
      <td className="px-2 py-2 align-middle">
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className={cn(
            "h-9 w-full min-w-[100px] rounded-md border border-input bg-background px-2 text-sm",
            "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <option value="user">{ROLE_LABEL.user}</option>
          <option value="admin">{ROLE_LABEL.admin}</option>
        </select>
      </td>
      <td className="px-2 py-2 align-middle">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className={cn(
            "h-9 w-full min-w-[100px] rounded-md border border-input bg-background px-2 text-sm",
            "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <option value="active">{STATUS_LABEL.active}</option>
          <option value="disabled">{STATUS_LABEL.disabled}</option>
        </select>
      </td>
      <td className="hidden px-2 py-2 align-middle text-xs text-muted-foreground sm:table-cell">
        {row.created_at ? new Date(row.created_at).toLocaleString("zh-CN") : "—"}
      </td>
      <td className="px-2 py-2 align-middle">
        <div className="flex flex-wrap gap-1.5">
          <Button type="button" size="sm" variant="secondary" disabled={saving} onClick={() => void save()}>
            {saving ? "保存中…" : "保存"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onResetPassword(row)}
          >
            <KeyRound className="size-3.5" />
            重置密码
          </Button>
          <Button
            type="button"
            size="sm"
            variant="destructive"
            disabled={deleting || isSelf}
            title={isSelf ? "不可删除自己" : undefined}
            onClick={() => void del()}
          >
            {deleting ? "…" : "删除"}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function ResetPasswordDialog({
  user,
  onClose,
  onSuccess,
}: {
  user: AdminUserRow | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setPassword("");
    setConfirmation("");
    setSubmitting(false);
    setError("");
  }, [user]);

  async function submit() {
    if (!user) return;
    if (password.length < 6) {
      setError("新密码至少需要 6 位");
      return;
    }
    if (password !== confirmation) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await adminResetUserPassword(user.id, password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "密码重置失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open={user !== null} onOpenChange={(open) => !open && !submitting && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[61] w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-background p-5 shadow-xl outline-none">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold">重置用户密码</Dialog.Title>
              <Dialog.Description className="mt-1 truncate text-sm text-muted-foreground">
                {user?.email ?? "—"}；保存后旧密码立即失效。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button type="button" size="icon" variant="ghost" className="size-8" disabled={submitting} aria-label="关闭密码重置窗口">
                <X className="size-4" />
              </Button>
            </Dialog.Close>
          </div>

          {error ? <p className="mt-4 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p> : null}

          <div className="mt-4 space-y-3">
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="新密码（至少 6 位）"
              autoComplete="new-password"
              disabled={submitting}
              autoFocus
            />
            <Input
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && void submit()}
              placeholder="再次输入新密码"
              autoComplete="new-password"
              disabled={submitting}
            />
          </div>

          <p className="mt-3 text-xs text-muted-foreground">Google 登录绑定不会受影响；原有已登录会话不会被强制退出。</p>

          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={submitting} onClick={onClose}>取消</Button>
            <Button type="button" disabled={submitting || password.length < 6 || !confirmation} onClick={() => void submit()}>
              {submitting ? "保存中…" : "确认重置"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function SettingsUserManagement({ currentUserId }: { currentUserId: string | null }) {
  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("user");
  const [newStatus, setNewStatus] = useState("active");
  const [creating, setCreating] = useState(false);
  const [passwordUser, setPasswordUser] = useState<AdminUserRow | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await adminListUsers());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    const em = newEmail.trim();
    if (!em || newPassword.length < 6) {
      window.alert("请填写邮箱与至少 6 位密码");
      return;
    }
    setCreating(true);
    try {
      await adminCreateUser({
        email: em,
        password: newPassword,
        role: newRole,
        account_status: newStatus,
      });
      setNewEmail("");
      setNewPassword("");
      setNewRole("user");
      setNewStatus("active");
      await load();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border bg-muted/20 p-4">
        <p className="text-sm font-medium">新建用户</p>
        <p className="mt-1 text-xs text-muted-foreground">管理员代建账号（无需邮箱验证码）；禁用用户不可登录。</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Input placeholder="邮箱" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} className="rounded-lg" />
          <Input
            type="password"
            placeholder="初始密码（≥6 位）"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="rounded-lg"
          />
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            className="h-10 rounded-lg border border-input bg-background px-2 text-sm"
          >
            <option value="user">{ROLE_LABEL.user}</option>
            <option value="admin">{ROLE_LABEL.admin}</option>
          </select>
          <select
            value={newStatus}
            onChange={(e) => setNewStatus(e.target.value)}
            className="h-10 rounded-lg border border-input bg-background px-2 text-sm"
          >
            <option value="active">{STATUS_LABEL.active}</option>
            <option value="disabled">{STATUS_LABEL.disabled}</option>
          </select>
        </div>
        <Button type="button" className="mt-3 rounded-lg" disabled={creating} onClick={() => void create()}>
          {creating ? "创建中…" : "添加用户"}
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {message ? <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{message}</p> : null}
      {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

      {!loading && !error ? (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-2 py-2 font-medium">用户</th>
                <th className="px-2 py-2 font-medium">角色</th>
                <th className="px-2 py-2 font-medium">状态</th>
                <th className="hidden px-2 py-2 font-medium sm:table-cell">创建时间</th>
                <th className="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <UserRow
                  key={u.id}
                  row={u}
                  currentUserId={currentUserId}
                  onUpdated={() => void load()}
                  onDeleted={() => void load()}
                  onResetPassword={setPasswordUser}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <ResetPasswordDialog
        user={passwordUser}
        onClose={() => setPasswordUser(null)}
        onSuccess={() => {
          setMessage(`${passwordUser?.email ?? "用户"} 的密码已重置`);
          setPasswordUser(null);
          void load();
        }}
      />
    </div>
  );
}
