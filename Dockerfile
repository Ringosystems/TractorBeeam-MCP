# TractorBeeam365 MCP — container image for Veeam Backup for Microsoft 365
# (independent; not affiliated with Veeam Software).
# Build:  docker build -t tractorbeeam365-mcp .
# No secrets are baked in; VB365_*/GRAPH_*/TB_* are supplied at runtime.
# Ships READ-ONLY by default; set TB_ENABLE_ACTIONS=true + TB_ALLOW_* to enable
# the gated action/restore tools.
#
# Defaults to the stdio transport so MCP clients can run it directly
# (`docker run -i ...`), which is how the MCP Registry OCI package is consumed.
# For the persistent HTTP service, set MCP_TRANSPORT=streamable-http (docker-compose
# does this) plus MCP_AUTH_TOKEN.
FROM python:3.12-slim

# Ownership marker required by the MCP Registry to verify this image belongs to
# the published server (must equal the server.json "name").
LABEL io.modelcontextprotocol.server.name="io.github.Ringosystems/tractorbeeam-mcp"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
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
