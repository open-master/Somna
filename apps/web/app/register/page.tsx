"use client";

import { Activity } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { registerRequest, sendRegistrationCode } from "@/lib/api/auth";
import { buildAuthHref, finishLogin } from "@/lib/auth/finish-login";
import { cn } from "@/lib/utils/cn";

function RegisterForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const nextPath = sp.get("next") && sp.get("next")!.startsWith("/") ? sp.get("next")! : "/";
  const afterLogin = sp.get("afterLogin");
  const loginHref = buildAuthHref("/login", { next: nextPath, afterLogin });

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [codeCooldown, setCodeCooldown] = useState(0);
  const coolTimerRef = useRef<number | null>(null);

  function startCooldown() {
    if (coolTimerRef.current != null) window.clearInterval(coolTimerRef.current);
    setCodeCooldown(60);
    coolTimerRef.current = window.setInterval(() => {
      setCodeCooldown((c) => {
        if (c <= 1) {
          if (coolTimerRef.current != null) window.clearInterval(coolTimerRef.current);
          coolTimerRef.current = null;
          return 0;
        }
        return c - 1;
      });
    }, 1000);
  }

  async function onSendCode() {
    setError(null);
    try {
      await sendRegistrationCode(email.trim());
      startCooldown();
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await registerRequest(email.trim(), password, code.trim());
      await finishLogin(router, nextPath, afterLogin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-screen flex-col items-center justify-center px-4",
        "bg-[radial-gradient(circle_at_1px_1px,hsl(var(--muted-foreground)/0.15)_1px,transparent_0)] [background-size:24px_24px]",
        "bg-muted/30",
      )}
    >
      <div className="w-full max-w-md space-y-6 rounded-2xl border bg-card p-8 shadow-lg">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="grid size-14 place-items-center rounded-2xl border bg-background shadow-sm">
            <Activity className="size-7 text-primary" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">注册</h1>
          <p className="text-sm text-muted-foreground">使用邮箱验证码注册，或通过 Google 直接开始</p>
        </div>

        <div className="space-y-3">
          <GoogleSignInButton nextPath={nextPath} afterLogin={afterLogin} />
        </div>

        <div className="relative py-2">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="bg-card px-2 text-muted-foreground">或者使用邮箱</span>
          </div>
        </div>

        <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="reg-email">
              邮箱
            </label>
            <Input
              id="reg-email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="rounded-xl"
            />
          </div>
          <div className="flex gap-2">
            <div className="min-w-0 flex-1 space-y-2">
              <label className="text-sm font-medium" htmlFor="reg-code">
                验证码
              </label>
              <Input
                id="reg-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="邮件中的验证码"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
                className="rounded-xl"
              />
            </div>
            <div className="flex flex-col justify-end">
              <Button
                type="button"
                variant="outline"
                className="whitespace-nowrap rounded-xl"
                disabled={codeCooldown > 0 || !email.trim()}
                onClick={() => void onSendCode()}
              >
                {codeCooldown > 0 ? `${codeCooldown}s` : "获取验证码"}
              </Button>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="reg-password">
              密码
            </label>
            <Input
              id="reg-password"
              type="password"
              autoComplete="new-password"
              placeholder="至少 6 位"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              className="rounded-xl"
            />
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          <Button type="submit" className="w-full rounded-xl" size="lg" disabled={loading}>
            {loading ? "请稍候…" : "注册"}
          </Button>
        </form>

        <p className="text-center text-xs text-muted-foreground">
          已有账号？{" "}
          <Link className="font-medium text-foreground underline underline-offset-4" href={loginHref}>
            登录
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">加载…</div>
      }
    >
      <RegisterForm />
    </Suspense>
  );
}
