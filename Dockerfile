# syntax=docker/dockerfile:1
# Multi-stage build: the node stage only produces the offline editor bundle;
# the final image contains no node, npm or front-end dependencies.
# bookworm-slim instead of alpine: alpine's npm 10 crashes at the end of npm ci
# ("Exit handler never called" while exiting 0), leaving node_modules incomplete.
FROM node:20-bookworm-slim AS vendor

WORKDIR /build/vendor-build
# Copy only the manifests so this layer is cached; npm ci follows the lockfile.
COPY vendor-build/package.json vendor-build/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --fetch-retries=5 --fetch-timeout=120000 \
    && node -e "require.resolve('esbuild')"
COPY vendor-build/build.mjs ./
RUN node build.mjs && test -s ../static/vendor/editor.bundle.js

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOWNOTES_DATA=/app/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
# Overwrite the repo bundle with the freshly built one so the image always ships it.
COPY --from=vendor /build/static/vendor/editor.bundle.js ./static/vendor/editor.bundle.js

RUN mkdir -p /app/data/notes /app/data/uploads

EXPOSE 8000

# Runs as root by default; docker-compose's user: picks the real identity so new files
# in ./data belong to the host user. PYTHONDONTWRITEBYTECODE keeps /app clean.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
