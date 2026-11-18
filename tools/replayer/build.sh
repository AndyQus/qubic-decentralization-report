#!/bin/sh
# Builds the BPP-9000 replayer from qubic/core's own reference scorer.
#
# The core's headers are fetched at a pinned tag, not vendored. Its platform layer
# targets UEFI/MSVC, so four headers are copied with their includes pointed at
# platform_shim.h; nothing else in them changes. Proven bit-exact on 2026-11-18:
# test/data/gt_ant_production.csv chain 11 depth 0 -> 5966/0, and a live epoch-240
# solution (tick 91,912,670) -> 4332, the error the node logged. Linked statically,
# so the binary runs on the python:slim runtime image whatever its glibc.
#
# Usage: sh build.sh [core-tag]   (needs git and clang++ >= 18)
set -eu
TAG="${1:-v1.313.0}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
git clone -q --depth 1 --branch "$TAG" https://github.com/qubic/core "$WORK/core"
C="$WORK/core"
{ echo "#pragma once"; grep -E "^static constexpr .* BPP9000_[A-Z_]+ = " "$C/src/public_settings.h" | grep -v HASH; } > "$WORK/bpp9000_settings_shim.h"
sed -e 's|#include <lib/platform_common/qintrin.h>|#include "platform_shim.h"|' \
    -e 's|#include "platform/memory.h"||' -e 's|#include "platform/assert.h"||' \
    "$C/src/kangaroo_twelve.h" > "$WORK/kangaroo_twelve.h"
cp "$C/src/mining/rating.h" "$C/test/score_common_reference.h" "$WORK/"
sed -e 's|#include "../src/mining/score_common.h"|#include "platform_shim.h"|' \
    -e 's|#include "../src/mining/rating.h"|#include "rating.h"|' \
    -e 's|#include "../src/mining/task_file.h"||' \
    -e 's|std::vector<unsigned char> poolVec;|PoolView poolVec;|' \
    "$C/test/score_bpp9000_reference.h" > "$WORK/score_bpp9000_reference.h"
cp "$HERE/replay_bpp9000.cpp" "$HERE/platform_shim.h" "$WORK/"
# Two builds: AVX2 (~15 s per walk) and plain x86-64 (~22 s) for hosts without
# AVX2, where the first would die on an illegal instruction. qdr/colony.py picks
# by /proc/cpuinfo. Both reproduce the reference values above.
"${CXX:-clang++-18}" -std=c++20 -O3 -mavx -mavx2 -static -I"$WORK" "$WORK/replay_bpp9000.cpp" -o "${OUT:-$HERE/replay}"
"${CXX:-clang++-18}" -std=c++20 -O3 -march=x86-64 -static -I"$WORK" "$WORK/replay_bpp9000.cpp" -o "${OUT:-$HERE/replay}-portable"
rm -rf "$WORK"
echo "built ${OUT:-$HERE/replay} from qubic/core $TAG"
