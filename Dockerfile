FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY relay ./relay
RUN mkdir -p /data && useradd --uid 10001 relay && chown -R relay:relay /data /app
USER relay
ENV RELAY_DB=/data/relay.db
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "relay.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
