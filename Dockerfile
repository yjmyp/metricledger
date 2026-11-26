FROM node:24-bookworm-slim AS web-build

WORKDIR /app
COPY web/package.json web/package-lock.json ./web/
RUN npm --prefix web ci
COPY web ./web
RUN npm --prefix web run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    METRICLEDGER_DB=/app/data/metricledger.duckdb \
    METRICLEDGER_LEDGER=/app/data/audit.sqlite3 \
    METRICLEDGER_EVAL_PATH=/app/eval/results.json \
    METRICLEDGER_STATIC_DIR=/app/web/dist \
    METRICLEDGER_HOST=0.0.0.0 \
    METRICLEDGER_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY eval ./eval
COPY docs ./docs
COPY --from=web-build /app/web/dist ./web/dist

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 metricledger \
    && mkdir -p /app/data \
    && chown -R metricledger:metricledger /app

USER metricledger
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["python", "-m", "scripts.run_api"]
