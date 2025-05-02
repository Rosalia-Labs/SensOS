FROM debian:bookworm-slim

# Install minimal tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    tar bash sudo && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy and extract the server tarball
ADD server.tar.gz /sensos

WORKDIR /sensos

CMD ["/bin/bash"]
