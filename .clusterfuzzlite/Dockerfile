# ClusterFuzzLite build image (#1104).
#
# `base-builder-python` is the image OSS-Fuzz uses for Python targets: it
# carries `compile_python_fuzzer`, which wraps each harness with PyInstaller so
# libFuzzer can execute it as a standalone binary. Building here rather than
# installing atheris on a runner is the whole reason CFL can fuzz a diff.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:6b2b3a7e4a2da50de47f94925cffbe34747e1aae4f33d7f07ba6c681dd648b23

COPY . $SRC/mcp-hangar
WORKDIR $SRC/mcp-hangar
COPY .clusterfuzzlite/build.sh $SRC/build.sh
