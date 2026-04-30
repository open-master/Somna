# syntax=docker/dockerfile:1.7
# ============================================================
#  Somna AI — Web (Next.js 14)
# ============================================================
FROM node:20-slim AS base
ENV NODE_ENV=production
WORKDIR /app

# ---------- deps ----------
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
COPY apps/web /app
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
