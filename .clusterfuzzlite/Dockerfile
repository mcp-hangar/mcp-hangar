# ClusterFuzzLite build image (#1104).
#
# `base-builder-python` is the image OSS-Fuzz uses for Python targets: it
# carries `compile_python_fuzzer`, which wraps each harness with PyInstaller so
# libFuzzer can execute it as a standalone binary. Building here rather than
# installing atheris on a runner is the whole reason CFL can fuzz a diff.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:b02a06f9d3918428825b1ebfa2c3627b591a5924a83436b8aaec40c85fd15b16

COPY . $SRC/mcp-hangar
WORKDIR $SRC/mcp-hangar
COPY .clusterfuzzlite/build.sh $SRC/build.sh
