# Build from the repository root:
#   docker build -t mcp-hangar .

# Stage 1: Build the Python wheel
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS py-builder
WORKDIR /app
RUN pip install --no-cache-dir hatch
COPY pyproject.toml README.md ./
COPY src/mcp_hangar ./src/mcp_hangar
RUN hatch build

# Stage 2: Final runtime image
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5
WORKDIR /app
RUN useradd --create-home --shell /bin/bash hangar
RUN mkdir -p /app/data && chown hangar:hangar /app/data
COPY --from=py-builder /app/dist/*.whl /tmp/
# Upgrade the bundled build tooling first: the base image ships older pip /
# setuptools / wheel that trip image scanners (e.g. wheel PYSEC / setuptools-vendored
# jaraco.context path-traversal). They are build-time only, not used by the app.
# The `[kubernetes]` extra is in the image on purpose: this image is what runs
# in a cluster, where the kubernetes discovery source is the one that matters.
# Its client is an extra rather than a base dependency so a laptop install stays
# small, which would leave the in-cluster deployment unable to construct the
# source at all. `docker` is already a base dependency.
#
# `[postgres]` is here for the same reason and was missing. This image is also
# what runs with more than one replica, and more than one replica requires a
# storage backend they can share -- which is PostgreSQL, whose adapters import
# psycopg2. Without the extra the driver is simply absent from the image, so
# `persistence.backend: postgresql` fails at startup on the one artefact where
# it is the recommended configuration. Found by deploying it (#790, phase 4.4).
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # [redis]: the truncation continuation cache on N>1 replicas needs a shared
    # store, and the image is the HA artefact -- same class as [postgres] (#1008).
    pip install --no-cache-dir "$(ls /tmp/*.whl)[kubernetes,postgres,redis]" opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp fpdf2 websockets && \
    rm /tmp/*.whl

USER hangar
EXPOSE 8080
ENTRYPOINT ["mcp-hangar"]
CMD ["serve", "--http", "--host", "0.0.0.0", "--port", "8080"]
