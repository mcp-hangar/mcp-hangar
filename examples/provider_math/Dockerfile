FROM python:3.11-slim

WORKDIR /app

# Which MCP SDK to install. Default is the stable 1.x line; server.py supports
# both generations, so the same image can be built against the v2 pre-release to
# get a plain (non-task) modern-era upstream for cross-generation testing:
#
#   docker build --build-arg MCP_SPEC='mcp==2.0.0b2' examples/provider_math
#
# No --pre here on purpose: it would silently pull the v2 beta for the default
# build too. An exact pre-release specifier installs without it (pip honours a
# pre-release that the requirement names explicitly).
ARG MCP_SPEC=mcp
RUN pip install --no-cache-dir "${MCP_SPEC}"

# Copy provider code
COPY server.py /app/server.py
COPY __init__.py /app/__init__.py

EXPOSE 8080

# Run the provider (defaults to streamable-http on :8080)
CMD ["python", "server.py"]
