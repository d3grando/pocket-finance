#!/usr/bin/env bash
# создать .env из .env.example (если его нет) и заполнить пустые секреты:
# SECRET_KEY, BOT_SERVICE_TOKEN, POSTGRES_PASSWORD. Уже заданные значения не меняются.
#   scripts/gen-secrets.sh [путь-к-env]   (по умолчанию .env в корне проекта — его читает compose;
#                                          относительный путь — от текущего каталога)
source "$(dirname "$0")/lib.sh"
if [ $# -gt 0 ]; then env_file="$(user_path "$1")"; else env_file="$PF_ROOT/.env"; fi

if [ ! -f "$env_file" ]; then
  cp "$PF_ROOT/.env.example" "$env_file"
  say "создан $env_file из .env.example"
fi
chmod 600 "$env_file"

rand() {  # rand <length> — [A-Za-z0-9], URL-safe
  local out=""
  while [ "${#out}" -lt "$1" ]; do
    out+="$(head -c 512 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')"
  done
  printf '%s' "${out:0:$1}"
}

fill() {  # fill VAR length
  local var="$1" len="$2" val
  if grep -qE "^${var}=.+" "$env_file"; then
    say "$var уже задан — не трогаю"
    return
  fi
  val="$(rand "$len")"
  if grep -qE "^${var}=" "$env_file"; then
    sed -i "s|^${var}=.*$|${var}=${val}|" "$env_file"
  else
    printf '%s=%s\n' "$var" "$val" >>"$env_file"
  fi
  say "$var сгенерирован"
}

fill SECRET_KEY 64
fill BOT_SERVICE_TOKEN 48
fill POSTGRES_PASSWORD 32
missing=""
for k in PUBLIC_URL AI_BASE_URL AI_API_KEY AI_MODEL TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_IDS TELEGRAM_BOT_USERNAME; do
  v="$(sed -n "s/^${k}=//p" "$env_file" | tail -n 1 | tr -d "\"'")"
  [ -n "$v" ] || missing="$missing $k"
done
if [ -n "$missing" ]; then
  say "готово ($env_file). Ещё не заполнены:$missing"
  say "(без AI — AI_ENABLED=false; без бота — оставьте TELEGRAM_* пустыми; см. docs/INSTALL.md)"
else
  say "готово ($env_file). Все обязательные переменные заполнены."
fi
