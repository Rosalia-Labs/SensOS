#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd $WORK_DIR

./bin/stop-server.sh --remove-volumes --no-backup &&
    ./bin/start-server.sh --rebuild-containers --no-detach
