#!/usr/bin/env bash
# восстановление базы из бэкапа (docs/BACKUP.md).
#   scripts/restore.sh [--yes] latest
#   scripts/restore.sh [--yes] daily/pocket_2026-10-27_033000.dump   (путь внутри BACKUP_DIR)
#   scripts/restore.sh [--yes] /path/to/file.dump                    (любой файл на хосте;
#                                        относительный путь — от текущего каталога)
#   scripts/restore.sh --check latest    проверочное восстановление в отдельную БД <db>_verify
#                                        (живая база не трогается, потом БД удаляется)
# Живое восстановление: останавливает api/worker/bot, делает страховочный дамп текущей базы
# (pre-restore/), пересоздаёт БД, восстанавливает, запускает сервисы (api применит миграции).
source "$(dirname "$0")/lib.sh"
need_docker

yes=0; check=0; src=""
for a in "$@"; do
  case "$a" in
    -y | --yes) yes=1 ;;
    --check) check=1 ;;
    -h | --help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) die "неизвестный флаг: $a" ;;
    *) [ -z "$src" ] || die "укажите один файл"; src="$a" ;;
  esac
done
[ -n "$src" ] || { sed -n '2,11p' "$0"; exit 2; }

is_running db || die "база (сервис db) не запущена: docker compose up -d db"
if ! is_running backup; then
  say "запускаю сервис backup"
  dc up -d backup >/dev/null
fi

# A file from the host (absolute, or relative to the directory the script was started from)
# → copy it into the container (backups/import/). Otherwise the name is a path inside
# BACKUP_DIR (daily/…, weekly/…, latest).
if [ "$src" != "latest" ] && [ -f "$(user_path "$src")" ]; then
  src="$(user_path "$src")"
  name="$(basename "$src")"
  dc exec -T backup mkdir -p /backups/import
  dc cp "$src" "backup:/backups/import/$name"
  src="import/$name"
  say "файл скопирован в BACKUP_DIR/import/$name"
fi

if [ "$check" -eq 1 ]; then
  db="$(dc exec -T backup sh -c 'printf %s "$PGDATABASE"')"
  verify="${db}_verify"
  say "проверочное восстановление $src → $verify"
  dc exec -T backup pf-restore --yes --no-safety-backup --target-db "$verify" "$src"
  dc exec -T backup psql -d postgres -qAtX -c "DROP DATABASE IF EXISTS \"$verify\" WITH (FORCE)"
  say "бэкап читается и восстанавливается — OK"
  exit 0
fi

if [ "$yes" -ne 1 ]; then
  warn "Восстановление ЗАМЕНИТ все данные в базе. Текущая база будет сохранена в pre-restore/."
  read -r -p "Введите «да», чтобы продолжить: " ans
  [ "$ans" = "да" ] || [ "$ans" = "yes" ] || die "отменено"
fi

running=()
for s in api worker bot; do is_running "$s" && running+=("$s"); done
if [ "${#running[@]}" -gt 0 ]; then
  say "останавливаю: ${running[*]}"
  dc stop "${running[@]}" >/dev/null
fi

rc=0
dc exec -T backup pf-restore --yes "$src" || rc=$?
if [ "$rc" -ne 0 ]; then
  # Do not start the API on a half-restored/empty database (it would create an empty schema).
  die "восстановление не удалось (код $rc); сервисы ${running[*]:-} оставлены остановленными.
  Повторите с другим файлом или верните прежнюю базу: scripts/restore.sh --yes pre-restore/<файл>"
fi

start=("${running[@]}")
[ "${#start[@]}" -gt 0 ] || start=(api)
say "запускаю: ${start[*]}"
dc up -d "${start[@]}" >/dev/null
wait_healthy api 240
say "восстановление завершено, API healthy"
