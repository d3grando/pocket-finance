#!/usr/bin/env bash
# обновление до свежего кода и откат (docs/UPDATE.md).
#   scripts/update.sh [--no-pull] [--no-backup] [--ref <ветка|тег|коммит>] [--restore <дамп>]
# Шаги: бэкап базы (пока ничего не тронуто) → git: ветка по умолчанию (или --ref) + pull →
# PF_VERSION в .env → сборка образов → [--restore: остановить api/worker/bot, вернуть базу из
# дампа] → up -d (api применяет миграции) → ждём health → удаляем образы старше прошлой версии.
# Сбой до запуска контейнеров (бэкап, git, сборка) — код и PF_VERSION возвращаются как были.
#   --ref X          перейти на тег/ветку/коммит (откат). Без --ref — всегда ветка по умолчанию
#                    (main), даже если рабочая копия осталась на коммите после прошлого --ref.
#   --restore DUMP   откат на версию со старой схемой БД: после сборки вернуть базу из дампа
#                    в BACKUP_DIR (daily/pocket_….dump — бэкап, сделанный перед обновлением);
#                    текущая база сохраняется в pre-restore/.
source "$(dirname "$0")/lib.sh"
need_docker

pull=1; backup=1; ref=""; restore=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull) pull=0 ;;
    --no-backup) backup=0 ;;
    --ref) shift; ref="${1:?--ref требует значение}" ;;
    --restore) shift; restore="${1:?--restore требует файл дампа (daily/…)}" ;;
    -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "неизвестный аргумент: $1" ;;
  esac
  shift
done
[ -f .env ] || [ -n "${PF_COMPOSE_OPTS:-}" ] || die ".env не найден — сначала установка (docs/INSTALL.md)"
[ "$pull" -eq 1 ] || [ -z "$ref" ] || die "--ref и --no-pull вместе не имеют смысла"

uses_env_file() { [ -f .env ] && [ -z "${PF_COMPOSE_OPTS:-}" ]; }

# ---------- state before the update (for the automatic revert) ----------
prev_head="$(git rev-parse HEAD)"
prev_branch="$(git symbolic-ref -q --short HEAD || true)"
prev_version="${PF_VERSION:-}"; prev_had_line=0
if uses_env_file && grep -q '^PF_VERSION=' .env; then
  prev_had_line=1
  prev_version="$(sed -n 's/^PF_VERSION=//p' .env | tail -n 1 | tr -d "\"'")"
fi

set_env_version() {  # set_env_version <value>  (only for the real .env)
  uses_env_file || return 0
  if grep -q '^PF_VERSION=' .env; then sed -i "s|^PF_VERSION=.*|PF_VERSION=$1|" .env
  else printf 'PF_VERSION=%s\n' "$1" >>.env; fi
}

revert_on_fail=0; started=0
on_exit() {
  local rc=$?
  [ "$rc" -ne 0 ] || return 0
  if [ "$started" -eq 1 ]; then
    warn "новая версия не поднялась. Откат на прежний код: scripts/update.sh --ref $(git rev-parse --short "$prev_head")${pre_dump:+ (если api пишет «база данных уже обновлена» — добавьте --restore $pre_dump)}; подробно — docs/UPDATE.md «Откат»"
    return 0
  fi
  [ "$revert_on_fail" -eq 1 ] || return 0
  warn "обновление не удалось — возвращаю код на ${prev_branch:-$prev_head} ($(git rev-parse --short "$prev_head")) и PF_VERSION=${prev_version:-<нет>}; работающие контейнеры не тронуты"
  if [ -n "$prev_branch" ]; then
    git checkout --quiet "$prev_branch" && git reset --quiet --keep "$prev_head"
  else
    git checkout --quiet "$prev_head"
  fi || warn "не удалось вернуть рабочую копию: git checkout $prev_head"
  if uses_env_file; then
    if [ "$prev_had_line" -eq 1 ]; then set_env_version "$prev_version"
    else sed -i '/^PF_VERSION=/d' .env; fi
  fi
}
trap on_exit EXIT

# ---------- 1. backup first: nothing has been changed yet ----------
pre_dump=""
if [ "$backup" -eq 1 ] && [ -z "$restore" ]; then
  if is_running db; then
    say "бэкап перед обновлением"
    in_backup pf-backup run || die "бэкап не удался — обновление остановлено, ничего не изменено (повторите или --no-backup)"
    pre_dump="$(in_backup sh -c 'cat "$BACKUP_DIR/.state/last_ok"' 2>/dev/null | tr -d '\r' || true)"
  else
    warn "база не запущена — бэкап перед обновлением пропущен"
  fi
fi

# ---------- 2. code ----------
revert_on_fail=1
if [ "$pull" -eq 1 ]; then
  [ -z "$(git status --porcelain --untracked-files=no)" ] || die "в рабочей копии есть локальные правки (git status) — разберитесь перед обновлением"
  say "git fetch"
  git fetch --tags --prune
  if [ -n "$ref" ]; then
    git checkout --quiet "$ref"
  elif ! git symbolic-ref -q HEAD >/dev/null; then
    # Detached HEAD after an earlier `--ref` (rollback): a plain update means "latest main".
    branch="$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)"
    branch="${branch#origin/}"; branch="${branch:-main}"
    say "рабочая копия на коммите $(git rev-parse --short HEAD) (после --ref) — переключаюсь на ветку $branch"
    git checkout --quiet "$branch"
  fi
  if git symbolic-ref -q HEAD >/dev/null; then git pull --ff-only; fi
fi

version="$(git describe --tags --always --dirty 2>/dev/null || echo 1.0.0)"
set_env_version "$version"
export PF_VERSION="$version"
say "версия: $version (была ${prev_version:-<нет>})"

# ---------- 3. images ----------
say "сборка образов"
dc build --pull

# ---------- 4. optional database rollback, then start ----------
# From here on the running stack changes: no automatic code revert any more.
revert_on_fail=0
if [ -n "$restore" ]; then
  say "останавливаю api, worker, bot и возвращаю базу из $restore (текущая база → pre-restore/)"
  dc stop api worker bot >/dev/null 2>&1 || true
  in_backup pf-restore --yes "$restore" || die "восстановление базы из $restore не удалось; api/worker/bot остановлены.
  Прежняя база лежит в pre-restore/: scripts/restore.sh --yes pre-restore/<файл>, затем scripts/update.sh --no-pull --no-backup"
fi
say "запуск"
started=1
dc up -d --remove-orphans
wait_healthy api 300
wait_healthy web 60
say "health: $(dc exec -T api pf-entrypoint healthcheck | tr -d '\r')"
started=0

# ---------- 5. disk: keep images of this and the previous version only (for a rollback) ----------
cleanup_images() {  # cleanup_images <tag-to-keep>...
  local img repo ref tag k keep
  for img in $(dc config --images api bot web backup 2>/dev/null | sort -u); do
    repo="${img%:*}"
    while read -r ref; do
      [ -n "$ref" ] || continue
      tag="${ref##*:}"
      keep=0
      [ "$tag" = "<none>" ] && keep=1
      for k in "$@"; do [ -n "$k" ] && [ "$tag" = "$k" ] && keep=1; done
      if [ "$keep" -eq 0 ] && docker rmi "$ref" >/dev/null 2>&1; then
        say "удалён образ старой версии: $ref"
      fi
    done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' "$repo" 2>/dev/null)
  done
  # dangling layers of this project (labelled in infra/docker/*.Dockerfile)
  docker image prune -f --filter "label=org.opencontainers.image.source=https://github.com/d3grando/pocket-finance" >/dev/null 2>&1 || true
}
cleanup_images "$version" "$prev_version"

[ -z "$pre_dump" ] || say "бэкап перед обновлением: $pre_dump (для отката: docs/UPDATE.md)"
say "обновлено до $version"
