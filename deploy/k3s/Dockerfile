FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY titanium ./titanium
COPY tradingagents ./tradingagents
COPY cli ./cli
COPY tools ./tools
COPY deploy ./deploy

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && addgroup --system titanium \
    && adduser --system --ingroup titanium --home /app titanium \
    && mkdir -p /app/results /app/data \
    && chown -R titanium:titanium /app

USER titanium
ENTRYPOINT ["python", "deploy/k3s/entrypoint.py"]

