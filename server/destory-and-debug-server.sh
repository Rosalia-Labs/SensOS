#!/bin/bash

set -e

./stop-server.sh --remove-volumes --no-save-database &&
    ./start-server.sh --rebuild-containers --no-detach
