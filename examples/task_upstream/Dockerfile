FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5

LABEL org.opencontainers.image.title="MCP task-emitting upstream"
LABEL org.opencontainers.image.description="v2-native Tasks-extension MCP server, for testing mcp-hangar's governed task relay (ADR-014)"

WORKDIR /app

# SDK first for layer caching. mcp==2.0.0b2 is a pre-release, so --pre is
# required -- without it pip resolves the stable 1.x line, which has no Tasks
# extension and this server will not import.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --pre -r /app/requirements.txt

COPY server.py /app/server.py

# Streamable HTTP on 0.0.0.0:8080; Hangar connects to http://<host>:8080/mcp.
EXPOSE 8080

RUN useradd -m -u 1000 mcp
USER mcp

CMD ["python", "/app/server.py"]
