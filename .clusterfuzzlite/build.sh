#!/bin/bash -eu
# Build the fuzz targets for ClusterFuzzLite (#1104).
#
# The harnesses import `invariants` from `fuzz/`, so that directory has to be
# importable inside the built binary -- PyInstaller follows the import, but
# only if it resolves at build time, which is what PYTHONPATH is for here.

pip3 install --no-cache-dir .

for harness in "$SRC"/mcp-hangar/fuzz/fuzz_*.py; do
  PYTHONPATH="$SRC/mcp-hangar/fuzz" compile_python_fuzzer "$harness"
done

# Seed the parse target. The other two consume raw FuzzedDataProvider bytes, so
# a hand-written seed for them would be an opaque blob -- libFuzzer builds and
# keeps their corpus itself. See fuzz/README.md.
if [ -d "$SRC/mcp-hangar/fuzz/corpus/parse" ]; then
  zip -j "$OUT/fuzz_policy_parse_seed_corpus.zip" "$SRC"/mcp-hangar/fuzz/corpus/parse/*
fi
