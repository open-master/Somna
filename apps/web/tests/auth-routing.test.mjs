import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

async function importTypeScriptModule(path) {
  const source = await readFile(path, "utf8");
  return importTypeScriptSource(source);
}

async function importTypeScriptSource(source) {
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
}

async function importAuthRoute() {
  const cookieSource = await readFile(new URL("../lib/auth/cookie.ts", import.meta.url), "utf8");
  const cookieOutput = ts.transpileModule(cookieSource, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const cookieUrl = `data:text/javascript;base64,${Buffer.from(cookieOutput).toString("base64")}`;

  const routePath = new URL("../app/api/v1/auth/[...path]/route.ts", import.meta.url);
  const routeSource = (await readFile(routePath, "utf8"))
    .replace('from "next/server"', `from ${JSON.stringify(import.meta.resolve("next/server.js"))}`)
    .replace('from "@/lib/auth/cookie"', `from ${JSON.stringify(cookieUrl)}`);
  return importTypeScriptSource(routeSource);
}

test("generic API proxy is a fallback so explicit auth routes win", async () => {
  const config = (await import(new URL("../next.config.mjs", import.meta.url).href)).default;
  const rewrites = await config.rewrites();

  assert.ok(!Array.isArray(rewrites));
  assert.deepEqual(rewrites.beforeFiles ?? [], []);
  assert.deepEqual(rewrites.afterFiles ?? [], []);
  assert.equal(rewrites.fallback.length, 1);
  assert.equal(rewrites.fallback[0].source, "/api/v1/:path*");
});

test("auth gate allows public and authenticated requests", async () => {
  const policyPath = new URL("../lib/auth/middleware-policy.ts", import.meta.url);
  const { authGateDecision } = await importTypeScriptModule(policyPath);

  assert.equal(authGateDecision("/api/v1/auth/login", false), "allow");
  assert.equal(authGateDecision("/api/health", false), "allow");
  assert.equal(authGateDecision("/chat/session-id", true), "allow");
  assert.equal(authGateDecision("/api/v1/sessions", true), "allow");
});

test("auth gate returns 401 for APIs and redirects pages when unauthenticated", async () => {
  const policyPath = new URL("../lib/auth/middleware-policy.ts", import.meta.url);
  const { authGateDecision } = await importTypeScriptModule(policyPath);

  assert.equal(authGateDecision("/api/v1/sessions", false), "api-unauthorized");
  assert.equal(authGateDecision("/api/v1/auth/me", false), "allow");
  assert.equal(authGateDecision("/chat/session-id", false), "redirect-login");
  assert.equal(authGateDecision("/settings", false), "redirect-login");
});

test("auth route clears the old cookie and a later login sets a new HttpOnly cookie", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const { NextRequest } = await import("next/server.js");
  const route = await importAuthRoute();

  const logoutReq = new NextRequest("https://somna-ai.com/api/v1/auth/logout", { method: "POST" });
  const logoutRes = await route.POST(logoutReq, { params: { path: ["logout"] } });
  const clearedCookie = logoutRes.headers.get("set-cookie") ?? "";
  assert.match(clearedCookie, /somna_access_token=/);
  assert.match(clearedCookie, /Max-Age=0/i);

  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        access_token: "new.jwt.token",
        user: { id: "1", email: "user@example.com", role: "user" },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );

  const loginReq = new NextRequest("https://somna-ai.com/api/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-proto": "https" },
    body: JSON.stringify({ email: "user@example.com", password: "secret" }),
  });
  const loginRes = await route.POST(loginReq, { params: { path: ["login"] } });
  const loginCookie = loginRes.headers.get("set-cookie") ?? "";
  assert.match(loginCookie, /somna_access_token=new\.jwt\.token/);
  assert.match(loginCookie, /HttpOnly/i);
  assert.match(loginCookie, /Secure/i);
  assert.match(loginCookie, /SameSite=lax/i);

  const responseBody = await loginRes.json();
  assert.equal(responseBody.access_token, undefined);
  assert.equal(responseBody.user.email, "user@example.com");
});
