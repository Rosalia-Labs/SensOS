#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../stage-base/00-sensos/files/lib"

docker run --rm \
    -v "$LIB_DIR:/sensos/lib:ro" \
    debian:bookworm-slim bash -c '
    set -euo pipefail

    apt-get update -qq
    apt-get install -y --no-install-recommends bash >/dev/null

    source /sensos/lib/parse-switches.sh

    register_option --mode mode "Processing mode" "default"
    register_option --count count "Number of items" "10"
    register_option --flag flag "A boolean flag" "false"

    parse_switches test --mode fast --count 42 --flag

    echo "Parsed options:"
    echo "  mode  = $mode"
    echo "  count = $count"
    echo "  flag  = $flag"
  '
