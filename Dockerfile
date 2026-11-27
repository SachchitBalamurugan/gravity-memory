FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 gravity && install -d -o gravity -g gravity -m 0700 /data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY gravity_memory ./gravity_memory
COPY recall_ledger ./recall_ledger
RUN pip install --no-cache-dir ".[connectors]"
USER gravity
ENV GRAVITY_TEAM_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["gravity-memory"]
CMD ["team", "serve", "--host", "0.0.0.0"]
