# T14 — контейнер, CI и процедура выпуска

PR #26 T13 проверен и слит. Реальные VPS, домен и бюджет не заданы; платные ресурсы не создавались.

## Состав

Docker собирает клиент Node.js 24 и устанавливает Python 3.13.16 по uv.lock без dev-зависимостей/editable. Процесс работает с UID/GID 10001. Docker-контекст закрыт по умолчанию и содержит только разрешённые исходники. Wheel включает 18 обезличенных карточек. Миграции лежат в `/app`, путь задан `RATATOUILLE_MIGRATIONS_ROOT`. SQLite хранится в отдельном именованном томе `ratatouille_data`.

Compose запускает web, отдельный polling/worker бота и Caddy для HTTPS. Миграция — явная команда профиля admin. `/api/health` проверяет процесс; `/api/ready` — схему `0004`, quick_check и сборку, без личных данных. Бот не наследует HTTP healthcheck. Готовность web не подтверждает доступность Telegram.

Токен передаётся файлом Compose secret через BOT_TOKEN_FILE. Обычный запуск может использовать BOT_TOKEN; два варианта одновременно запрещены. Токен не входит в image/Git/workflow. На VPS файл `deploy/.secrets/bot-token` должен принадлежать UID 10001 и иметь режим 0400; каталог — 0700 под контролем администратора. APP_URL/APP_HOST/OWNER_ID находятся в исключённом `deploy/.env`. Пример содержит только заглушки.

## Первый запуск на Linux VPS

1. Выбрать сервер/бюджет, установить Docker Engine и Compose plugin по официальной инструкции дистрибутива. Подготовить `/opt/ratatouille`, доступ оператора и DNS hostname к VPS. Открыть 80/443; web привязан к loopback хоста. Не публиковать SQLite или Docker socket.
2. Скопировать `deploy/config.env.example` в `deploy/.env`, задать HTTPS URL в корне сайта, hostname и Telegram ID. Создать закрытый файл токена без ввода в историю команд. Владелец должен сначала написать боту `/start`.
3. Из корня проекта выполнить `docker compose --env-file deploy/.env build`, затем `docker compose --env-file deploy/.env run --rm migrate`. Запустить `docker compose --env-file deploy/.env up -d web bot caddy`. Caddy получает TLS после корректного DNS.
4. Проверить `curl --fail http://127.0.0.1:8000/api/ready`, HTTPS `/api/ready`, открытие через Telegram и реальную доставку. Без TLS/Telegram выпуск не принят.

## BotFather

Создать бота через `/newbot`, хранить токен только локально/на VPS. Настроить Mini App через Bot Settings → Configure Mini App (или `/newapp`) с HTTPS URL. Настроить Menu Button тем же URL; launcher устанавливает кнопку владельцу. Проверить `/start` в личном чате и открытие на Desktop/Android/iOS. Основание — [Telegram Mini Apps](https://core.telegram.org/bots/webapps).

## CI и выпуск

CI на pull_request/main проверяет приложение, публикационный gate, Docker build, миграцию, readiness, перезапуск с сохранёнными целями и резервную копию. Actions закреплены SHA официальных репозиториев; права — contents:read.

Release запускается только вручную workflow_dispatch из main, повторяет проверки, передаёт image по SSH и вызывает `tools/deploy/release.sh`. Environment `production` требует настройки required reviewer владельца и секретов DEPLOY_HOST/USER/KEY/KNOWN_HOSTS. Ключ хоста проверяется строго; env/token на VPS не перезаписываются. До настройки environment/сервера workflow не запускать. Push/PR не вызывают деплой.

После остановки web/bot каждая попытка делает уникальную копию через SQLite backup API с проверкой целостности, затем миграцию и запуск image. При отказе backup прежние контейнеры запускаются без миграции. Ошибка миграции/readiness сохраняет копию и контейнеры для диагностики; автоматического downgrade нет. Успех записывает SHA в `deploy/.release/current`. Повтор SHA не конфликтует с копией.

## Резервирование и откат

Для резервной копии экспортировать RATATOUILLE_VERSION с текущим SHA и выполнить `docker compose --env-file deploy/.env run --rm migrate python -m ratatouille.backup /data/backups/NEW-NAME.sqlite3`. Старые копии не перезаписываются. Вынести копии с VPS в закрытое хранилище и проверить восстановление на отдельном томе; срок хранения зависит от выбранного бюджета.

Откат image допустим при совместимой схеме: остановить web/bot, сохранить текущую БД, экспортировать прежний проверенный RATATOUILLE_VERSION и выполнить `up -d --no-build`. При несовместимой схеме на остановленных сервисах восстановить предмиграционную копию в отдельный новый том; изменения после копии будут потеряны. Downgrade и удаление рабочего тома без отдельного решения запрещены. Проверить readiness, цели, планы, отметки, расписание и настоящий бот.

Пример переключения на новый том (подставить прежний SHA, выбранную копию и уникальное имя):

```bash
export RATATOUILLE_VERSION=PREVIOUS_VERIFIED_SHA
export RATATOUILLE_DATA_VOLUME=ratatouille_restore_YYYYMMDD
docker compose --env-file deploy/.env stop web bot
docker volume create "$RATATOUILLE_DATA_VOLUME"
docker run --rm --user 0:0 --entrypoint python \
  -v ratatouille_data:/old:ro -v "$RATATOUILLE_DATA_VOLUME:/data" \
  -e BACKUP_NAME=CHOSEN_BACKUP.sqlite3 "ratatouille:$RATATOUILLE_VERSION" \
  -c "import os; from pathlib import Path; from ratatouille.backup import backup; backup(Path('/old/backups')/os.environ['BACKUP_NAME'],Path('/data/ratatouille.sqlite3')); os.chown('/data',10001,10001); os.chown('/data/ratatouille.sqlite3',10001,10001)"
docker compose --env-file deploy/.env up -d --no-build --force-recreate web bot caddy
curl --fail http://127.0.0.1:8000/api/ready
```

Исходный том в примере монтируется только для чтения и сохраняется. Если текущий том имеет другое имя, заменить `ratatouille_data` в команде. После успешной проверки записать выбранные RATATOUILLE_VERSION/DATA_VOLUME в `deploy/.env`, чтобы следующий запуск сохранил переключение. Новый том должен быть пустым; команда не перезаписывает существующую БД.

## Проверка

221 тест и проверки проекта, YAML/bаsh-синтаксис прошли. Проверены копирование/восстановление, запрет перезаписи, файловый секрет, readiness, повторный выпуск и восстановление сервисов при отказе копии. Установленный wheel читает 18 карточек и мигрирует из заданного корня. Локального Docker daemon нет. [CI PR #27](https://github.com/Sphag/ratatouille/actions/runs/37964404312) прошёл: реальные Docker build, миграция, readiness, сохранение целей после перезапуска и backup.

VPS/домен/production-секреты не заданы. Live HTTPS, рестарт/обновление/откат на VPS и BotFather не проверены. Issue #15 остаётся открытой.
