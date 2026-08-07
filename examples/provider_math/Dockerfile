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

# The comment below used to say "defaults to streamable-http on :8080" and the
# image did not: `server.py` reads MCP_TRANSPORT and falls back to **stdio**, so
# the container started, spoke stdio to nobody and exited 0 with port 8080
# unbound. Cookbook 01 and 04 both `docker run -p 8080:8080` this image and then
# point a gateway at it, so the very first recipe in the book had nothing to
# talk to. Found by auditing the documentation against 2.5.0-rc.3.
#
# Set here rather than changing the default in `server.py`: stdio is the right
# default for a module launched as a subprocess, which is the other way this
# example is used. An image that publishes a port should serve on it.
ENV MCP_TRANSPORT=streamable-http

CMD ["python", "server.py"]
