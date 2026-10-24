# syntax=docker/dockerfile:1
# =============================================================================
# Imagem do price-tracker
#  - multi-stage: dependências instaladas em virtualenv limpo
#  - roda como usuário sem privilégios (uid 10001)
#  - código somente leitura para a aplicação
# =============================================================================
ARG PYTHON_VERSION=3.12

# ----------------------------------------------------------------- builder ---
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ----------------------------------------------------------------- runtime ---
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TRACKER_DB_PATH=/app/data/tracker.db \
    HOME=/tmp

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY app/ ./app/
COPY static/ ./static/
COPY extension/ ./extension/

RUN mkdir -p /app/data && chown app:app /app/data
VOLUME ["/app/data"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-server-header", "--timeout-graceful-shutdown", "5"]
