# syntax=docker/dockerfile:1
# Local container packaging for the reviewer workbench, the synthetic demo and the
# repository checks. It never authorizes real case documents; see deploy/README.md.

# The locked frontend needs Node 22.13 or newer and excludes Node 23.
FROM node:22-bookworm-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# No VITE_API_BASE_URL: the app calls same-origin /v1/, which the proxy forwards.
RUN npm run build

# The integrated local runbook specifies Python 3.12.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYMUPDF_SUGGEST_LAYOUT_ANALYZER=0 \
    PYMUPDF_MESSAGE=fd:2
# The local approval store binds receipts to a real OS account, so the image needs a
# passwd entry for the unprivileged UID the cloud_tests image already uses.
RUN useradd --system --uid 10001 --create-home --home-dir /home/reviewer --shell /usr/sbin/nologin reviewer
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir '.[aws,pdf,documents,privacy]'

# The reviewer workbench: the synthetic launcher on container loopback, with the
# built frontend served in front of it by a same-origin proxy.
FROM base AS workbench
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/ ./scripts/
COPY deploy/probe.py ./deploy/probe.py
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/workbench-entrypoint.sh /usr/local/bin/workbench-entrypoint
COPY --from=webbuild /web/dist/ /usr/share/nginx/html/
RUN chmod 0755 /usr/local/bin/workbench-entrypoint \
    && mkdir -p /app/artifacts \
    && chown -R reviewer:reviewer /app/artifacts /var/lib/nginx /var/log/nginx
ENV APPRAISAL_WORKBENCH_DIRECTORY=/app/artifacts/workbench \
    APPRAISAL_WORKBENCH_PORT=8765
USER reviewer
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=10 \
    CMD python /app/deploy/probe.py 127.0.0.1:8080
ENTRYPOINT ["workbench-entrypoint"]
CMD ["serve"]

# The single-shot review service: the existing local HTTP factory, unchanged.
FROM base AS reviewer
COPY scripts/ ./scripts/
COPY deploy/reviewer-entrypoint.sh /usr/local/bin/reviewer-entrypoint
RUN chmod 0755 /usr/local/bin/reviewer-entrypoint \
    && mkdir -p /app/artifacts/local-demo \
    && chown -R reviewer:reviewer /app/artifacts
ENV APPRAISAL_DEMO_DIRECTORY=/app/artifacts/local-demo \
    APPRAISAL_DEMO_CONFIG=config.json \
    PORT=8000
USER reviewer
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=10 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=2)"
ENTRYPOINT ["reviewer-entrypoint"]
CMD ["serve"]

# The demo console: a separate visual surface over the reviewer's public HTTP API.
FROM base AS console
RUN pip install --no-cache-dir 'httpx>=0.28,<1'
COPY deploy/console/ ./console/
RUN mkdir -p /app/artifacts && chown -R reviewer:reviewer /app/artifacts
ENV APPRAISAL_DEMO_DIRECTORY=/app/artifacts/local-demo \
    PORT=8080
USER reviewer
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/console/health' % os.environ.get('PORT','8080'), timeout=2)"
CMD ["sh", "-c", "exec python -m uvicorn console.app:app --host 0.0.0.0 --port ${PORT}"]

# The Python repository checks, run against this same interpreter and dependency set.
FROM base AS verify
# The publication-gate tests drive real Git repositories in a temporary directory.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -e '.[dev]' -r cloud_tests/requirements-dev.txt \
    && chmod 0755 deploy/verify.sh \
    && chown -R reviewer:reviewer /app
ENV APPRAISAL_DEMO_DIRECTORY=/app/artifacts/local-demo \
    HOME=/home/reviewer
USER reviewer
CMD ["./deploy/verify.sh"]

# The frontend checks: typecheck, lint, format, tests, build and artifact checks.
FROM node:22-bookworm-slim AS web-verify
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
CMD ["npm", "run", "verify"]
