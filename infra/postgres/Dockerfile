# Custom postgres image: timescale/timescaledb-ha + pgBackRest + cron
# Co-located so archive_command can invoke pgbackrest directly with no SSH/TLS gymnastics.
# Pinned to the same digest the project tested against — bump only after manual verification.
FROM timescale/timescaledb-ha:pg16@sha256:b3d038d0a0757df8a5ec0a94ba68d9ad57b0e16100a024cf4b370c77ad5645f7

USER root

# pgBackRest from upstream PostgreSQL apt repo (PGDG) for current versions
# The base image is Ubuntu-derived; PGDG has Ubuntu packages.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'; \
    apt-get update; \
    apt-get install -y --no-install-recommends pgbackrest cron tini sudo; \
    rm -rf /var/lib/apt/lists/*

# pgBackRest layout — postgres user owns log/spool/repo cache
RUN install -d -o postgres -g postgres -m 0750 \
        /etc/pgbackrest \
        /etc/pgbackrest/conf.d \
        /var/log/pgbackrest \
        /var/lib/pgbackrest \
        /var/spool/pgbackrest

COPY --chown=postgres:postgres --chmod=0640 pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY --chmod=0644 crontab /etc/cron.d/pgbackrest
COPY --chmod=0755 entrypoint.sh /usr/local/bin/parsnip-entrypoint.sh

# Allow the postgres user to start the cron daemon without a password.
# Tightly scoped: only the cron binary, only as root.
RUN echo "postgres ALL=(root) NOPASSWD: /usr/sbin/cron" > /etc/sudoers.d/postgres-cron \
    && chmod 0440 /etc/sudoers.d/postgres-cron

USER postgres
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/parsnip-entrypoint.sh"]
CMD ["postgres"]
