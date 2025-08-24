#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../../sensos/stage-base/00-sensos/files/lib"

docker run --rm \
  -v "$LIB_DIR:/sensos/lib:ro" \
  debian:bookworm-slim bash -c '
    set -euo pipefail

    apt-get update -qq
    apt-get install -y --no-install-recommends bash >/dev/null

    source /sensos/lib/parse-switches.sh

    register_option --mode mode "Processing mode" "default"
    register_option --count count "Number of items" "10"
    register_option --flag1 flag1 "A boolean flag" "false"
    register_option --flag2 flag2 "A boolean flag" "false"
    register_option --flag3 flag3 "A boolean flag" "true"

    parse_switches test --mode fast --count 42 --flag1 --flag2=true --flag3=false

    echo "Parsed options:"
    echo "  mode  = $mode"
    echo "  count = $count"
    echo "  flag1  = $flag1"
    echo "  flag2  = $flag2"
    echo "  flag3  = $flag3"
  '
