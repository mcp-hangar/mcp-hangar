# Build from the repository root:
#   docker build -t mcp-hangar .
#
# Stage 1: Build the React/Vite UI
FROM node:20-slim AS ui-builder
WORKDIR /ui
COPY packages/ui/package.json packages/ui/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY packages/ui ./
RUN npm run build

# Stage 2: Build the Python wheel
FROM python:3.11-slim AS py-builder
WORKDIR /app
RUN pip install --no-cache-dir hatch
COPY pyproject.toml README.md ./
COPY src/mcp_hangar ./src/mcp_hangar
RUN hatch build

# Stage 3: Final runtime image
FROM python:3.11-slim
WORKDIR /app
RUN useradd --create-home --shell /bin/bash hangar
COPY --from=py-builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY --from=ui-builder /ui/dist /app/ui/dist
ENV MCP_UI_DIST=/app/ui/dist
USER hangar
EXPOSE 8080
ENTRYPOINT ["mcp-hangar"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
