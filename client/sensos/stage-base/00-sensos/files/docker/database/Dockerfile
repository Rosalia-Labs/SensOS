FROM postgres:17-bookworm

WORKDIR /app

ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

COPY postgres.conf /etc/postgresql/postgresql.conf

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
