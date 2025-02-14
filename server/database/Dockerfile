FROM postgres:17-bookworm

# Install PostgreSQL and PostGIS
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-postgis \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
