# syntax=docker/dockerfile:1.7
# SaralPrivacy frontend — Next.js 16 standalone server. Pages only: PDFs, infographics
# and all data access live in the FastAPI backend, so no Chromium or native renderers here.
#   docker build -t saralprivacy-frontend .

ARG NODE_IMAGE=node:22-bookworm-slim

# ── 1. deps ──────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# ── 2. build ─────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Rewrites in next.config.ts are resolved at build time; the compose service name is
# the default. Override with --build-arg BACKEND_URL=... for other environments.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=${BACKEND_URL}
# Public origin of uploaded files; feeds the CSP img-src and the image allow-list.
ARG PUBLIC_ASSET_BASE_URL=
ENV PUBLIC_ASSET_BASE_URL=${PUBLIC_ASSET_BASE_URL}
# NEXT_PUBLIC_* values are compiled into the browser bundle by `next build`, so they
# must be present here, not only at runtime. "true" shows the header language toggle.
ARG NEXT_PUBLIC_SHOW_HINDI=
ENV NEXT_PUBLIC_SHOW_HINDI=${NEXT_PUBLIC_SHOW_HINDI}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ── 3. runtime ───────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends dumb-init ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs -m nextjs
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/scripts/start-with-warmup.mjs ./scripts/start-with-warmup.mjs
USER nextjs
EXPOSE 3000
ENTRYPOINT ["dumb-init", "--"]
# Starts server.js, then refreshes pages pre-rendered without a backend (see script).
CMD ["node", "scripts/start-with-warmup.mjs"]
