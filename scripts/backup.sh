#!/usr/bin/env bash
# бэкап базы вручную и обслуживание бэкапов (docs/BACKUP.md).
#   scripts/backup.sh            сделать бэкап сейчас (= run)
#   scripts/backup.sh list       список дампов
#   scripts/backup.sh status     последний успех/ошибка, расписание
#   scripts/backup.sh rotate     только ротация 7д/4н/6м
#   scripts/backup.sh remote     только отправка на удалённый хост (BACKUP_REMOTE)
source "$(dirname "$0")/lib.sh"
need_docker
cmd="${1:-run}"
case "$cmd" in
  run | list | status | rotate | remote | health) ;;
  -h | --help) sed -n '2,7p' "$0"; exit 0 ;;
  *) die "неизвестная команда: $cmd (run|list|status|rotate|remote|health)" ;;
esac
is_running db || die "база (сервис db) не запущена: docker compose up -d db"
in_backup pf-backup "$@"
