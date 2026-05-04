# ---- Build Stage ----
FROM python:3.13-slim AS builder

WORKDIR /app

# Instala Poetry
RUN pip install --no-cache-dir poetry==2.3.3

# Copia apenas arquivos de dependência primeiro (cache de camadas Docker)
COPY pyproject.toml poetry.lock* ./

# Configura Poetry para não criar venv e instala apenas dependências de produção
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# ---- Runtime Stage ----
FROM python:3.13-slim

WORKDIR /app

# Copia dependências instaladas do builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copia código-fonte
COPY src ./src

# Variáveis de ambiente
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Healthcheck (verifica se o Python está funcional)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "from radar_ev.config import settings; print('OK')" || exit 1

# Labels para metadados
LABEL maintainer="Maicon Douglas"
LABEL description="Radar +EV — Sistema de recomendação pré-jogo"
LABEL version="1.0.0"

# Execução padrão
CMD ["python", "-m", "radar_ev.orchestrator", "--daemon"]
