# ClusterFuzzLite build image (#1104).
#
# `base-builder-python` is the image OSS-Fuzz uses for Python targets: it
# carries `compile_python_fuzzer`, which wraps each harness with PyInstaller so
# libFuzzer can execute it as a standalone binary. Building here rather than
# installing atheris on a runner is the whole reason CFL can fuzz a diff.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:030add5351585b43fb46a1939aad7c82dc11c7e690eaaaf118e8b71e3b674519

COPY . $SRC/mcp-hangar
WORKDIR $SRC/mcp-hangar
COPY .clusterfuzzlite/build.sh $SRC/build.sh
