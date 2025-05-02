FROM debian:bookworm-slim

# Install common tools (optional, useful for debugging)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tar bash sudo && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy and extract the client tarball
ADD client.tar.gz /sensos_source

WORKDIR /sensos_source/00-sensos

# ✅ How to install from a package list file (line-separated list)
RUN xargs -a 00-packages-nr apt-get install -y --no-install-recommends

# ✅ Run your scripts in a container-safe way
RUN ROOTFS_DIR=/ bash 00-run.sh

RUN on_chroot() { "$@"; }; \
    bash 01-run.sh; \
    bash 02-run.sh; \
    bash 03-run.sh; \
    bash 05-run.sh; \
    bash 06-run.sh

# ❌ `apt-get remove` won't work with a filename — you need to `xargs` it too
RUN xargs -a 99-packages-remove apt-get remove -y

# ✅ Optional cleanup
RUN apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*

# 🔧 These should come *after* the system is set up
WORKDIR /home/sensos-admin
USER sensos-admin

CMD ["/bin/bash"]
