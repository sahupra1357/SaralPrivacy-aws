# SaralPrivacy backend — FastAPI on Python 3.12 with Playwright Chromium for PDF rendering.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /bin/uv

# Fonts for Latin + Devanagari (Hindi notices) + emoji; curl for health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates fonts-noto-core fonts-noto-color-emoji fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached layer).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Chromium + its system libraries, shared by every user via PLAYWRIGHT_BROWSERS_PATH.
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY ./scripts /app/scripts
COPY ./pyproject.toml ./uv.lock ./alembic.ini /app/
COPY ./app /app/app

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

RUN groupadd -g 1001 app && useradd -u 1001 -g app -m app \
    && chown -R app:app /app /ms-playwright
USER app

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["bash", "-c", "bash scripts/prestart.sh && fastapi run app/main.py --port 8000"]
