# ClusterFuzzLite build image (#1104).
#
# `base-builder-python` is the image OSS-Fuzz uses for Python targets: it
# carries `compile_python_fuzzer`, which wraps each harness with PyInstaller so
# libFuzzer can execute it as a standalone binary. Building here rather than
# installing atheris on a runner is the whole reason CFL can fuzz a diff.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:6a90ec89a18436e057a06f54266c3f120eb87d96c095b0c67a2b15cab33ec1f4

COPY . $SRC/mcp-hangar
WORKDIR $SRC/mcp-hangar
COPY .clusterfuzzlite/build.sh $SRC/build.sh
