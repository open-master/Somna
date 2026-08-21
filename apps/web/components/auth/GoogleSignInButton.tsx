"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { googleAuthRequest } from "@/lib/api/auth";
import { finishLogin } from "@/lib/auth/finish-login";

let gsiLoad: Promise<void> | null = null;

function loadGsiScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!gsiLoad) {
    gsiLoad = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("failed to load Google Sign-In"));
      document.body.appendChild(s);
    });
  }
  return gsiLoad;
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (cfg: {
            client_id: string;
            callback: (resp: { credential?: string }) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            opts: { theme?: string; size?: string; width?: number; text?: string; locale?: string },
          ) => void;
        };
      };
    };
  }
}

export function GoogleSignInButton({
  nextPath,
  afterLogin,
}: {
  nextPath: string;
  afterLogin: string | null;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim();
    const el = hostRef.current;
    if (!clientId || !el) return;

    let cancelled = false;

    void (async () => {
      try {
        await loadGsiScript();
        if (cancelled || !el.isConnected) return;
        const g = window.google;
        if (!g?.accounts?.id) return;

        async function onCred(credential: string) {
          await googleAuthRequest(credential);
          await finishLogin(router, nextPath, afterLogin);
        }

        g.accounts.id.initialize({
          client_id: clientId,
          callback: (resp) => {
            if (resp.credential) void onCred(resp.credential);
          },
        });
        g.accounts.id.renderButton(el, {
          theme: "outline",
          size: "large",
          width: 320,
          text: "continue_with",
          locale: "zh_CN",
        });
      } catch {
        /* 未配置或网络失败时静默 */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nextPath, afterLogin, router]);

  if (!process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim()) {
    return (
      <p className="text-center text-xs text-muted-foreground">未配置 Google 客户端 ID（NEXT_PUBLIC_GOOGLE_CLIENT_ID）</p>
    );
  }

  return <div ref={hostRef} className="flex w-full min-h-[40px] justify-center" />;
}
