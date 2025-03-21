#!/bin/bash

set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd $WORK_DIR

./bin/stop-server.sh --remove-volumes --no-backup &&
    ./bin/start-server.sh --rebuild-containers --no-detach
