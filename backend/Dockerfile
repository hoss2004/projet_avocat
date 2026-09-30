FROM python:3.12-slim AS runtime
ENV PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=8
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --create-home appuser && mkdir -p /data/storage && chown appuser:appuser /data/storage
USER appuser
EXPOSE 8000
FROM runtime AS test
USER root
COPY requirements-test.lock ./
RUN pip install --no-cache-dir -r requirements-test.lock
COPY tests ./tests
USER appuser
FROM runtime AS final
