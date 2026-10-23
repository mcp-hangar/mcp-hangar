# ClusterFuzzLite build image (#1104).
#
# `base-builder-python` is the image OSS-Fuzz uses for Python targets: it
# carries `compile_python_fuzzer`, which wraps each harness with PyInstaller so
# libFuzzer can execute it as a standalone binary. Building here rather than
# installing atheris on a runner is the whole reason CFL can fuzz a diff.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:21f6fab17ace4dc76e1c56de91ee95d3d03da021df94b41056ba036f6c6f55e9

COPY . $SRC/mcp-hangar
WORKDIR $SRC/mcp-hangar
COPY .clusterfuzzlite/build.sh $SRC/build.sh
