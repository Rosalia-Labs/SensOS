FROM postgres:17-bookworm

WORKDIR /app

# Add the PostgreSQL APT repo signing key (modern way)
RUN apt-get update && apt-get install -y wget gnupg2 && \
    wget --quiet -O /usr/share/keyrings/postgresql.gpg https://www.postgresql.org/media/keys/ACCC4CF8.asc

# Optional: Ensure the repo is listed with the signed-by option (sometimes not needed if the base image already has the repo)
# RUN echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list

# Set required DEBs
ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

# Install packages (now repo key is present)
RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

COPY postgres.conf /etc/postgresql/postgresql.conf

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
