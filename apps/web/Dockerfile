# syntax=docker/dockerfile:1.7
# ============================================================
#  Somna AI — Web (Next.js 14)
# ============================================================
FROM node:20-slim AS base
WORKDIR /app

# ---------- deps ----------
# 安装阶段勿设 NODE_ENV=production，否则 npm 会省略 devDependencies；
# next build 需要 tailwindcss / postcss 等（见 package.json devDependencies）。
FROM base AS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy workspace packages first (for file: deps)
COPY packages/event-schema/typescript /packages/event-schema/typescript
# Install the shared package's own deps (zod) so bundler resolution works
RUN --mount=type=cache,target=/root/.npm \
    cd /packages/event-schema/typescript \
    && npm install --no-audit --no-fund --legacy-peer-deps

COPY apps/web/package.json /app/package.json
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund --legacy-peer-deps

# ---------- build ----------
FROM deps AS build
ENV NODE_ENV=production
# Next.js `rewrites()` is evaluated at build time and the destination gets
# baked into `.next/routes-manifest.json`; runtime env vars are ignored there.
# Default to the docker-compose service name so container-to-container works
# out of the box; override with `--build-arg AGENT_CORE_URL=...` for other
# topologies (e.g. host-network dev).
ARG AGENT_CORE_URL=http://agent-core:8000
ENV AGENT_CORE_URL=${AGENT_CORE_URL}
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=${NEXT_PUBLIC_GOOGLE_CLIENT_ID}
COPY apps/web /app
# 构建上下文或检出不完整时，应在本地失败并提示，而不是表现为「@/ 无法解析」
RUN test -f /app/tsconfig.json \
    && test -f /app/components/layout/AppShell.tsx \
    && test -f /app/lib/api/sessions.ts \
    && test -f /app/lib/events/useEventStream.ts
RUN npm run build

# ---------- runtime ----------
FROM node:20-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY --from=build /app/.next          /app/.next
COPY --from=build /app/public         /app/public
COPY --from=build /app/node_modules   /app/node_modules
COPY --from=build /app/package.json   /app/package.json
COPY --from=build /app/next.config.mjs /app/next.config.mjs

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD node -e "fetch('http://localhost:3000/api/health').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["npx", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
