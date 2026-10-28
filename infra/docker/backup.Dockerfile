# syntax=docker/dockerfile:1.7
# Pocket Finance backup image: pg_dump 16 + rotation 7d/4w/6m + optional
# rsync/scp to a remote host, scheduled by busybox crond (BACKUP_CRON, BACKUP_TZ).
# Build context = repository root:
#   docker build -f infra/docker/backup.Dockerfile -t pocket-backup .
ARG POSTGRES_IMAGE=postgres:16-alpine
FROM ${POSTGRES_IMAGE}
RUN apk add --no-cache rsync openssh-client tzdata
COPY --chmod=0755 infra/backup/lib.sh /usr/local/lib/pf-backup/lib.sh
COPY --chmod=0755 infra/backup/pf-backup.sh /usr/local/bin/pf-backup
COPY --chmod=0755 infra/backup/pf-restore.sh /usr/local/bin/pf-restore
COPY --chmod=0755 infra/backup/entrypoint.sh /usr/local/bin/pf-backup-entrypoint
COPY --chmod=0755 infra/tests/test_rotation.sh /usr/local/lib/pf-backup/test_rotation.sh
ENV BACKUP_DIR=/backups \
    BACKUP_CRON="30 3 * * *" \
    BACKUP_TZ=Asia/Ho_Chi_Minh
VOLUME ["/backups"]
HEALTHCHECK --interval=5m --timeout=20s --start-period=30s --retries=1 \
  CMD ["pf-backup", "health"]
ENTRYPOINT ["pf-backup-entrypoint"]
CMD ["cron"]
ARG PF_VERSION=dev
LABEL org.opencontainers.image.title="pocket-backup" \
      org.opencontainers.image.source="https://github.com/d3grando/pocket-finance" \
      org.opencontainers.image.version="${PF_VERSION}"
