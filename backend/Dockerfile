FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG TZ=Europe/Paris
ARG PG_VERSION=16
ARG PGDATA=/var/lib/postgresql/${PG_VERSION}/main

# Pré-requis
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg2 \
    lsb-release \
    python3 python3-pip python3-dev subversion \
    gosu \
    redis-server \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Installation PostgreSQL 16 + l'extension pgvector
RUN sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
    && cat /etc/apt/sources.list.d/pgdg.list \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && apt-get update \
    && apt-get install -y postgresql-${PG_VERSION} postgresql-client-${PG_VERSION} libpq-dev postgresql-${PG_VERSION}-pgvector \
    && rm -rf /var/lib/apt/lists/* \
    && echo "shared_preload_libraries = 'vector'" >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf

# Installation de TimescaleDB 2
RUN sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list" \
    && wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | apt-key add - \
    && apt-get update \
    && apt-get install -y timescaledb-2-postgresql-${PG_VERSION} timescaledb-toolkit-postgresql-${PG_VERSION} \
    && rm -rf /var/lib/apt/lists/* \
    && echo "shared_preload_libraries = 'timescaledb'" >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf

# Configuration Redis
RUN sed -i 's/protected-mode yes/protected-mode no/g' /etc/redis/redis.conf && \
    sed -i 's/# maxmemory <bytes>/maxmemory 1024mb/g' /etc/redis/redis.conf && \
    sed -i 's/# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/g' /etc/redis/redis.conf && \
#    sed -i 's/bind 127.0.0.1 -::1/bind 0.0.0.0/g' /etc/redis/redis.conf && \
    sed -i 's/daemonize yes/daemonize no/g' /etc/redis/redis.conf

# Configuration de supervisord pour gérer tous les processus
RUN mkdir -p /var/log/supervisor
COPY ./supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Installation du backend
COPY ./src/ /backend
COPY ./requirements.txt /backend
RUN pip3 install -r /backend/requirements.txt --break-system-packages \
    && mkdir -p /backend/assets \
    && chown -R postgres:postgres /backend \
    && rm -rf /root/.cache

# Création du script de démarrage
RUN wget -O /usr/bin/dumb-init https://github.com/Yelp/dumb-init/releases/download/v1.2.5/dumb-init_1.2.5_x86_64 \
    && chmod +x /usr/bin/dumb-init \
    && echo "#!/bin/bash" > /start.sh \
    && echo "mkdir -p ${PGDATA}" >> /start.sh \
    && echo "chmod 700 ${PGDATA}" >> /start.sh \
    && echo "chown -R postgres ${PGDATA}" >> /start.sh \
    && echo "if [ ! -s ${PGDATA}/PG_VERSION ]; then" >> /start.sh \
    && echo "  echo 'Initialisation de la base de données'" >> /start.sh \
    && echo "  gosu postgres /usr/lib/postgresql/${PG_VERSION}/bin/initdb -D /var/lib/postgresql/${PG_VERSION}/main" >> /start.sh \
    && echo "fi" >> /start.sh \
    && echo "" >> /start.sh \
    && echo "sed -i \"s/#listen_addresses = 'localhost'/listen_addresses = '*'/\" /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
    && echo "sed -i \"s/#idle_in_transaction_session_timeout = 0/idle_in_transaction_session_timeout = 3600000/\" /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
#    && echo "echo 'log_min_messages = error' >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
#    && echo "echo 'log_min_error_statement = error' >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
#    && echo "echo 'log_statement = none' >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
#    && echo "echo 'log_filename = \'/tmp/pgsql.log\'' >> /etc/postgresql/${PG_VERSION}/main/postgresql.conf" >> /start.sh \
    && echo "echo 'host all all ::1/128 md5' >> /etc/postgresql/${PG_VERSION}/main/pg_hba.conf" >> /start.sh \
    && echo "echo 'host all all 127.0.0.1/32 md5' >> /etc/postgresql/${PG_VERSION}/main/pg_hba.conf" >> /start.sh \
#    && echo "echo 'host all all 10.0.0.0/8 md5' >> /etc/postgresql/${PG_VERSION}/main/pg_hba.conf" >> /start.sh \
    && echo "echo 'host all all 192.168.20.0/24 md5' >> /etc/postgresql/${PG_VERSION}/main/pg_hba.conf" >> /start.sh \
    && echo "" >> /start.sh \
    && echo "exec /usr/bin/supervisord" >> /start.sh \
    && echo "" >> /start.sh \
    && chmod +x /start.sh

HEALTHCHECK --interval=60s --timeout=20s --start-period=60s --retries=3 \
    CMD pg_isready -U postgres && curl -f http://localhost:5020/health || exit 1
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/start.sh"]
