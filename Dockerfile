FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[monitor]" \
    && useradd --system --create-home --uid 10001 app \
    && mkdir -p /data && chown app:app /data

USER app
CMD ["yandex-mail-monitor"]
