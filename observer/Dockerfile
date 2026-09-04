FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY autonomous_kernel ./autonomous_kernel
COPY experiments ./experiments
COPY config ./config
COPY state ./state

RUN python -m pip install --upgrade pip && \
    python -m pip install .

RUN mkdir -p /app/runtime /app/artifacts /app/evidence

ENTRYPOINT ["python", "-m", "experiments.market_observer", "--mode", "daemon", "--root", "/app"]
