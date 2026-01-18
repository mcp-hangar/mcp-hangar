# packages/core/Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir hatch

COPY pyproject.toml README.md ./
COPY mcp_hangar ./mcp_hangar

RUN hatch build

FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --shell /bin/bash hangar

COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER hangar

EXPOSE 8080

ENTRYPOINT ["mcp-hangar"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
