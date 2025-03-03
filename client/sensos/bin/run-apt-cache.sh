#!/bin/bash

docker run -d --name apt-cacher-ng \
    -p 3142:3142 \
    --restart unless-stopped \
    sameersbn/apt-cacher-ng

echo "Set APT_PROXY=http://localhost:3142"
