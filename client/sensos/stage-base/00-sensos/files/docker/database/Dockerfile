FROM postgres:17-bookworm

ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"
RUN /lib/install-debs.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
