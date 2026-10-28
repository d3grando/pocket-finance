# Бэкапы и восстановление

## Как устроено

- Сервис `backup` (образ на базе `postgres:16-alpine`) по расписанию `BACKUP_CRON`
  (по умолчанию **ежедневно в 03:30**, пояс `BACKUP_TZ=Asia/Ho_Chi_Minh`) делает
  `pg_dump` в формате custom (`-Fc`, сжатие) и **проверяет архив полным чтением**
  (`pg_restore` в `/dev/null`) — битый дамп не сохраняется.
- Дампы лежат на хосте в `BACKUP_DIR` (права 600):

  ```
  BACKUP_DIR/
    daily/    pocket_2026-10-27_033000.dump   последние 7 дней (все дампы этих дней)
    weekly/   pocket_2026-W44.dump            первый дамп недели (ISO), последние 4 недели
    monthly/  pocket_2026-10.dump             первый дамп месяца, последние 6 месяцев
    pre-restore/                              страховочные дампы перед восстановлением (5 шт.)
    .state/                                   время последнего успеха/ошибки, known_hosts
  ```

  Недельные и месячные копии — жёсткие ссылки на дневной дамп (место не удваивается).
  Глубина ротации: `BACKUP_KEEP_DAILY=7`, `BACKUP_KEEP_WEEKLY=4`, `BACKUP_KEEP_MONTHLY=6`.
- Если задан `BACKUP_REMOTE`, после каждого бэкапа каталог зеркалируется на удалённый хост
  (rsync по SSH, с учётом ротации) или новый дамп копируется по scp.
- Контейнер `backup` становится **unhealthy**, если последний успешный бэкап старше
  `BACKUP_MAX_AGE_HOURS` (30 ч) или последняя попытка закончилась ошибкой — любой: база
  недоступна, идёт другой бэкап/восстановление, `pg_dump`, проверка архива, отправка на
  удалённый хост. Это видно в `docker compose ps`, причина — в `scripts/backup.sh status`.
  Следующий успешный бэкап снимает отметку.
- Дополнительно имеет смысл снимать снапшоты всей машины/тома средствами хостинга.

## Команды

```bash
scripts/backup.sh            # бэкап прямо сейчас (+ ротация + отправка на удалённый хост)
scripts/backup.sh list       # все дампы с размерами
scripts/backup.sh status     # последний успех/ошибка, расписание, число дампов
scripts/backup.sh remote     # только отправить на удалённый хост
docker compose logs backup   # журнал ночных запусков
```

## Восстановление

```bash
scripts/restore.sh latest                                  # самый свежий дневной дамп
scripts/restore.sh daily/pocket_2026-10-27_033000.dump     # конкретный дамп из BACKUP_DIR
scripts/restore.sh monthly/pocket_2026-09.dump
scripts/restore.sh /home/user/pocket_2026-10-01.dump       # любой файл с хоста (скопируется в import/)
scripts/restore.sh ./pocket_2026-10-01.dump                # относительный путь — от текущего каталога
scripts/restore.sh --yes latest                            # без вопроса (для скриптов)
```

Что происходит:

1. Архив проверяется целиком — битый файл отклоняется **до** того, как база тронута.
2. Останавливаются `api`, `worker`, `bot` (веб отвечает «Сервер временно недоступен»).
3. Текущая база сохраняется в `pre-restore/` (страховка: можно вернуть как было).
4. База пересоздаётся (`DROP DATABASE … WITH (FORCE)`, UTF8) и восстанавливается одной
   транзакцией (`pg_restore --single-transaction --exit-on-error`).
5. Сервисы запускаются обратно; `api` применяет миграции, если дамп от более старой версии.
   Скрипт ждёт, пока `api` станет healthy.

Если восстановление не удалось, сервисы остаются остановленными, а прежняя база лежит в
`pre-restore/`: `scripts/restore.sh --yes pre-restore/<файл>`.

### Проверить бэкап, не трогая живую базу

```bash
scripts/restore.sh --check latest
```

Восстанавливает дамп в отдельную базу `<POSTGRES_DB>_verify`, проверяет и удаляет её.
Полезно раз в месяц или после переноса бэкапов.

### Перенос на другой сервер

1. На новом сервере — установка по `docs/INSTALL.md` (тот же `.env`, особенно `SECRET_KEY`:
   с другим ключом все входы и токены устройств станут недействительными).
2. `docker compose up -d db backup`
3. `scripts/restore.sh --yes /путь/к/pocket_….dump`
4. `docker compose up -d` (если используется свой HTTPS — `--profile standalone`).

### Вручную, без скриптов

```bash
docker compose exec backup pf-backup run
docker compose stop api worker bot
docker compose exec backup pf-restore --yes daily/pocket_2026-10-27_033000.dump
docker compose up -d
```

## Копия на удалённый хост

1. На удалённом хосте — пользователь только для бэкапов и каталог:
   ```bash
   useradd -m -s /bin/sh pocketbak && install -d -o pocketbak -m 700 /srv/backup/pocket
   apt-get install -y rsync        # rsync нужен на обеих сторонах
   ```
2. На сервере Pocket — ключ без пароля и отпечаток хоста:
   ```bash
   mkdir -p infra/var/ssh && chmod 700 infra/var/ssh
   ssh-keygen -t ed25519 -N '' -C pocket-backup -f infra/var/ssh/id_ed25519
   ssh-keyscan -p 22 backup.example.com > infra/var/ssh/known_hosts
   ```
   Публичный ключ `infra/var/ssh/id_ed25519.pub` → `~pocketbak/.ssh/authorized_keys` на удалённом хосте.
   Рекомендуется ограничить ключ: `restrict,from="<IP сервера Pocket>" ssh-ed25519 …`
   (без проброса портов и терминала, вход только с этого адреса).
3. В `.env`:
   ```ini
   BACKUP_REMOTE=pocketbak@backup.example.com:/srv/backup/pocket
   BACKUP_REMOTE_PORT=22
   BACKUP_REMOTE_METHOD=rsync      # зеркало с ротацией; scp — только новый дамп, без удаления
   BACKUP_REMOTE_DELETE=true
   BACKUP_SSH_DIR=./infra/var/ssh
   ```
4. `docker compose up -d backup && scripts/backup.sh` — проверка: `scripts/backup.sh status`
   без ошибок, на удалённом хосте появились `daily/ weekly/ monthly/`.

Без `known_hosts` отпечаток хоста запоминается при первом подключении (`accept-new`,
хранится в `BACKUP_DIR/.state/known_hosts`); с `known_hosts` проверка строгая.
`pre-restore/` и `.state/` на удалённый хост не отправляются.

## Настройки

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BACKUP_DIR` | `./infra/var/backups` | каталог дампов на хосте (лучше абсолютный путь) |
| `BACKUP_CRON` | `30 3 * * *` | расписание (cron, 5 полей) |
| `BACKUP_TZ` | `Asia/Ho_Chi_Minh` | часовой пояс расписания и имён файлов |
| `BACKUP_ON_START` | `false` | сделать бэкап при старте контейнера |
| `BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY` | `7` / `4` / `6` | глубина ротации |
| `BACKUP_MAX_AGE_HOURS` | `30` | порог unhealthy |
| `BACKUP_REMOTE` | — | `user@host:/path`; пусто — выключено |
| `BACKUP_REMOTE_METHOD` | `rsync` | `rsync` или `scp` |
| `BACKUP_REMOTE_PORT` | `22` | SSH-порт |
| `BACKUP_REMOTE_DELETE` | `true` | rsync: удалять на удалённой стороне то, что удалила ротация |
| `BACKUP_SSH_DIR` | `./infra/var/ssh` | каталог с ключом и `known_hosts` (монтируется только на чтение) |
| `BACKUP_SSH_KEY_NAME` | `id_ed25519` | имя файла ключа в `BACKUP_SSH_DIR` |
| `BACKUP_PREFIX` | `pocket` | префикс имён дампов (`<префикс>_2026-10-27_033000.dump`) |

После изменения: `docker compose up -d backup`.

## Проверено

`scripts/test-stack.sh` на тестовом Docker-хосте: бэкап → изменение данных → `restore.sh --check` (живая база
не тронута) → `restore.sh --yes latest` (данные совпали байт в байт, включая кириллицу и
`NUMERIC(28,8)`) → восстановление файла с хоста → отказ на обрезанном архиве без порчи базы →
rsync и scp на SSH-хост. Ротация (400 дней ночных бэкапов, 7/4/6, жёсткие ссылки, сбои дампа и
удалённой копии, недоступная база, занятая блокировка) — `infra/tests/test_rotation.sh`
(запуск без Docker: `scripts/ci-backup-tests.sh`).
