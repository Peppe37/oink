# Oink: one image, the web app built in and served by the API.
# The web app is the same for every architecture: build it once, natively.
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/Peppe37/oink" \
      org.opencontainers.image.description="Self-hosted personal finance" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
    OINK_DATA_DIR=/data OINK_STATIC_DIR=/app/static PORT=8080 OINK_VERSION=${VERSION}
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./static
RUN useradd --system --uid 10001 oink && mkdir -p /data && chown oink /data
USER oink
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
CMD ["/app/.venv/bin/oink"]
