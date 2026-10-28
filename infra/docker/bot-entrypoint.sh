#!/bin/sh
# Pocket Finance bot entrypoint: normalize list env vars, then exec the command
# (default: python -m pocket_bot — long polling).
# Without a usable TELEGRAM_BOT_TOKEN the default command does not start: the container logs one
# line and idles (no restart loop, no Telegram calls). Set the token, then `docker compose up -d bot`.
set -eu

log() { printf '{"level":"%s","logger":"pf.bot-entrypoint","msg":"%s"}\n' "$1" "$2"; }

idle() {  # idle <level> <message> — stay up quietly until stopped
  log "$1" "$2"
  trap 'exit 0' TERM INT
  while :; do
    sleep 3600 &
    wait $! || true
  done
}

if [ "$*" = "python -m pocket_bot" ]; then
  token="${TELEGRAM_BOT_TOKEN:-}"
  if [ -z "$token" ]; then
    idle WARNING "TELEGRAM_BOT_TOKEN не задан — бот выключен (контейнер ждёт). Задайте токен в .env и выполните: docker compose up -d bot"
  fi
  # Bot API token format: <numeric bot id>:<35 chars [A-Za-z0-9_-]>.
  if ! printf '%s' "$token" | grep -Eq '^[0-9]{5,20}:[A-Za-z0-9_-]{30,64}$'; then
    idle ERROR "TELEGRAM_BOT_TOKEN имеет неверный формат (ожидается 123456789:AA…, токен от @BotFather) — бот не запущен. Исправьте .env и выполните: docker compose up -d bot"
  fi
fi

eval "$(python /usr/local/lib/pf/pf-env-normalize.py pocket_bot.config:BotSettings)"
exec "$@"
