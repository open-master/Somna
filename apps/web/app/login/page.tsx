"use client";

import { Activity } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { loginCodeRequest, loginRequest, sendLoginCode } from "@/lib/api/auth";
import { buildAuthHref, finishLogin } from "@/lib/auth/finish-login";
import { cn } from "@/lib/utils/cn";

function LoginForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const nextPath = sp.get("next") && sp.get("next")!.startsWith("/") ? sp.get("next")! : "/";
  const afterLogin = sp.get("afterLogin");

  const registerHref = buildAuthHref("/register", { next: nextPath, afterLogin });

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

  async function onSendLoginCode() {
    setError(null);
    try {
      await sendLoginCode(email.trim());
      startCooldown();
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    }
  }

  async function onPasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await loginRequest(email.trim(), password);
      await finishLogin(router, access_token, nextPath, afterLogin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  async function onCodeSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await loginCodeRequest(email.trim(), code.trim());
      await finishLogin(router, access_token, nextPath, afterLogin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
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
          <h1 className="text-2xl font-semibold tracking-tight">登录</h1>
          <p className="text-sm text-muted-foreground">使用 Google 或邮箱登录 Somna</p>
        </div>

        <div className="space-y-3">
          <GoogleSignInButton nextPath={nextPath} afterLogin={afterLogin} />
        </div>

        <div className="relative py-2">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="bg-card px-2 text-muted-foreground">或者</span>
          </div>
        </div>

        <Tabs defaultValue="code" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="code">验证码登录</TabsTrigger>
            <TabsTrigger value="password">密码登录</TabsTrigger>
          </TabsList>
          <TabsContent value="code" className="mt-4 space-y-4">
            <form className="space-y-4" onSubmit={(e) => void onCodeSubmit(e)}>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="email-code">
                  邮箱
                </label>
                <Input
                  id="email-code"
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
                  <label className="text-sm font-medium" htmlFor="otp">
                    验证码
                  </label>
                  <Input
                    id="otp"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="6 位数字"
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
                    onClick={() => void onSendLoginCode()}
                  >
                    {codeCooldown > 0 ? `${codeCooldown}s` : "获取验证码"}
                  </Button>
                </div>
              </div>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
              <Button type="submit" className="w-full rounded-xl" size="lg" disabled={loading}>
                {loading ? "请稍候…" : "登录"}
              </Button>
            </form>
          </TabsContent>
          <TabsContent value="password" className="mt-4">
            <form className="space-y-4" onSubmit={(e) => void onPasswordSubmit(e)}>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="email-pw">
                  邮箱
                </label>
                <Input
                  id="email-pw"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="rounded-xl"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="password">
                  密码
                </label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={1}
                  className="rounded-xl"
                />
              </div>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
              <Button type="submit" className="w-full rounded-xl" size="lg" disabled={loading}>
                {loading ? "请稍候…" : "登录"}
              </Button>
            </form>
          </TabsContent>
        </Tabs>

        <p className="text-center text-xs text-muted-foreground">
          还没有账号？{" "}
          <Link className="font-medium text-foreground underline underline-offset-4" href={registerHref}>
            注册
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">加载…</div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
