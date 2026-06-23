# TractorBeeam365 MCP — persistent HTTP MCP service for Veeam Backup for
# Microsoft 365 (independent; not affiliated with Veeam Software).
# Build:  docker build -t tractorbeeam365-mcp .
# No secrets are baked in; VB365_*/GRAPH_*/TB_* are supplied at runtime.
# Ships READ-ONLY by default; set TB_ENABLE_ACTIONS=true + TB_ALLOW_* to enable
# the gated action/restore tools.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    VB365_PORT=4443 \
    VB365_API_VERSION=v8 \
    VB365_VERIFY_SSL=false \
    VB365_TIMEOUT=30

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY tractorbeeam365/ ./tractorbeeam365/

# Audit log + any generated files (PST exports, reports) land here; mount a
# volume to persist them when the operator tier is enabled.
RUN mkdir -p /app/audit /app/downloads

# Run as an unprivileged user.
RUN useradd -r -u 10001 mcp && chown -R mcp /app
USER mcp

EXPOSE 8000

# Liveness: the MCP port is accepting connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.getenv('MCP_PORT','8000'))), 3).close()" || exit 1

CMD ["python", "server.py"]
