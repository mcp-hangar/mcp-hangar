# Build from the repository root:
#   docker build -t mcp-hangar .

# Stage 1: Build the Python wheel
FROM python:3.11-slim AS py-builder
WORKDIR /app
RUN pip install --no-cache-dir hatch
COPY pyproject.toml README.md ./
COPY src/mcp_hangar ./src/mcp_hangar
RUN hatch build

# Stage 2: Final runtime image
FROM python:3.11-slim
WORKDIR /app
RUN useradd --create-home --shell /bin/bash hangar
RUN mkdir -p /app/data && chown hangar:hangar /app/data
COPY --from=py-builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp fpdf2 websockets && rm /tmp/*.whl

# Copy enterprise modules (BSL 1.1 licensed) into the runtime image.
# The bootstrap code imports from 'enterprise.*' so the package must be
# on PYTHONPATH.  /app is already WORKDIR, so setting PYTHONPATH=/app
# makes 'import enterprise' resolve to /app/enterprise/.
COPY enterprise/ ./enterprise/
ENV PYTHONPATH=/app

USER hangar
EXPOSE 8080
ENTRYPOINT ["mcp-hangar"]
CMD ["serve", "--http", "--host", "0.0.0.0", "--port", "8080"]
