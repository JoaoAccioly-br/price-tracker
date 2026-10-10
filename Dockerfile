# syntax=docker/dockerfile:1
# =============================================================================
# Imagem do price-tracker
#  - multi-stage: dependências são instaladas num estágio separado e só o
#    virtualenv pronto é copiado -> imagem final menor, sem pip cache/arquivos
#    temporários de build
#  - roda como usuário sem privilégios (uid 10001)
#  - código pertence ao root e é somente leitura para o processo da aplicação
#
# Build com Chromium (para links com use_browser=1):
#   docker build --build-arg INSTALL_BROWSER=true -t price-tracker .
# =============================================================================
ARG PYTHON_VERSION=3.12

# ----------------------------------------------------------------- builder ---
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Só o requirements.txt primeiro: enquanto ele não mudar, esta camada (a mais
# lenta) é reaproveitada do cache mesmo que o código mude.
COPY requirements.txt .
# Cache mount do BuildKit: acelera rebuilds sem deixar o cache dentro da imagem
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ----------------------------------------------------------------- runtime ---
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG INSTALL_BROWSER=false

# PYTHONUNBUFFERED: logs vão direto pro stdout/stderr (docker logs)
# PYTHONDONTWRITEBYTECODE: não grava .pyc (o filesystem é read-only no compose)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TRACKER_DB_PATH=/app/data/tracker.db \
    HOME=/tmp

# Usuário de sistema sem shell e sem home: se a aplicação for comprometida,
# o atacante não tem root dentro do container.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

# Chromium só é instalado quando pedido (+~400 MB na imagem)
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
      playwright install --with-deps chromium \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

# Código e estáticos ficam como root:root (o usuário 'app' só consegue ler)
COPY app/ ./app/
COPY static/ ./static/

# Único diretório gravável: o banco SQLite (montado como volume no compose)
RUN mkdir -p /app/data && chown app:app /app/data
VOLUME ["/app/data"]

USER app

EXPOSE 8000

# Healthcheck com o próprio Python (dispensa instalar curl) num endpoint leve
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# --workers 1: o estado da coleta e o SSE ficam em memória e o SQLite não se
#   beneficia de múltiplos processos escrevendo -> manter 1 worker.
# --no-server-header: não anuncia "uvicorn" nas respostas.
# --timeout-graceful-shutdown: conexões SSE ficam abertas para sempre; sem isso
#   o "docker stop" esperaria o timeout de 10s e mataria o processo com SIGKILL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-server-header", "--timeout-graceful-shutdown", "5"]
