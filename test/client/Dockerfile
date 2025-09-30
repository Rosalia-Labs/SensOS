FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    APT_LISTCHANGES_FRONTEND=none \
    ROOTFS_DIR=/

# Base tools + hygiene (note: xargs comes from findutils; no separate pkg)
RUN set -eux; \
  apt-get update; \
  apt-get install -y --no-install-recommends \
    ca-certificates apt-utils curl gnupg \
    xz-utils bzip2 \
    bash sudo coreutils grep sed findutils procps iproute2 \
    tar git; \
  rm -rf /var/lib/apt/lists/*

# Prevent service auto-starts during apt (pi-gen friendliness) + on_chroot shim
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
RUN printf 'on_chroot() { "$@"; }\n' > /usr/local/bin/on_chroot && chmod +x /usr/local/bin/on_chroot

# Bring in SensOS client stage files from repo root context
WORKDIR /sensos_source
COPY client/sensos/stage-base/00-sensos/ ./00-sensos/
RUN chmod +x /sensos_source/00-sensos/*.sh || true

# Install packages from list (comments/blank-safe)
RUN set -eux; \
  apt-get update; \
  sed -E 's/#.*$//;/^\s*$/d' 00-sensos/00-packages-nr > /tmp/pkg.install || true; \
  if [ -s /tmp/pkg.install ]; then \
    xargs -a /tmp/pkg.install apt-get install -y --no-install-recommends; \
  fi; \
  rm -rf /var/lib/apt/lists/*

# 1) Proper on_chroot shim: runs stdin (heredoc) or passes through args
RUN printf '%s\n' \
  '#!/bin/sh' \
  'set -e' \
  'if [ "$#" -gt 0 ]; then' \
  '  exec "$@"' \
  'else' \
  '  # read heredoc from stdin and execute with bash -eu' \
  '  exec bash -eu -s' \
  'fi' > /usr/local/bin/on_chroot && chmod +x /usr/local/bin/on_chroot

# 2) Stub systemctl so enable/disable doesn’t fail in a container
RUN printf '%s\n' \
  '#!/bin/sh' \
  '[ "$1" = "--version" ] && { echo "systemd (shim)"; exit 0; }' \
  'echo "[shim] systemctl $*"' \
  'exit 0' > /usr/bin/systemctl && chmod +x /usr/bin/systemctl

# 3) Ensure the firmware path exists for 05-run.sh appends
RUN mkdir -p /boot/firmware && : > /boot/firmware/config.txt

# Run all NN-run.sh in order
RUN set -eux; cd 00-sensos; \
  for f in $(ls -1 [0-9][0-9]-run.sh 2>/dev/null | sort); do \
    echo ">>> running $f"; bash "$f"; \
  done

# Optional removals
RUN set -eux; \
  apt-get update; \
  sed -E 's/#.*$//;/^\s*$/d' 00-sensos/99-packages-remove > /tmp/pkg.remove || true; \
  if [ -s /tmp/pkg.remove ]; then \
    xargs -a /tmp/pkg.remove apt-get purge -y || xargs -a /tmp/pkg.remove apt-get remove -y || true; \
    apt-get -y autoremove; \
  fi; \
  apt-get clean; rm -rf /var/lib/apt/lists/*

# Install Docker CLI (daemonless) to talk to inner DinD
RUN set -eux; \
  install -d -m 0755 /etc/apt/keyrings; \
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
  chmod a+r /etc/apt/keyrings/docker.gpg; \
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list; \
  apt-get update; \
  apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin; \
  rm -rf /var/lib/apt/lists/*

RUN set -eux; echo sensos > /sensos/keys/api_password

RUN set -eux; chown -R sensos-admin:sensos-data /sensos

RUN set -eux; \
    install -d -m 0755 /etc/sudoers.d; \
    printf 'sensos-admin ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/sensos-admin; \
    chown root:root /etc/sudoers.d/sensos-admin; \
    chmod 0440 /etc/sudoers.d/sensos-admin; \
    visudo -cf /etc/sudoers >/dev/null

# Stay root for tests (no user creation)
WORKDIR /root
CMD ["/bin/bash"]
