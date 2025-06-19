FROM postgres:17-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg2 && \
    wget --quiet -O /usr/share/keyrings/postgresql.gpg https://www.postgresql.org/media/keys/ACCC4CF8.asc

RUN echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list

ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

COPY postgres.conf /etc/postgresql/postgresql.conf

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
