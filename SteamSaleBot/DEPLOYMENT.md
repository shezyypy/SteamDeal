# SteamDeal production checklist

Этот файл не содержит токенов, паролей или персональных данных. Все значения из
`.env` задаются только на сервере и не коммитятся в Git.

## 1. Подготовить сервер

Нужны Docker Engine с Compose v2, домен с HTTPS и Telegram-бот. PostgreSQL и
Redis запускаются из `docker-compose.yml`; наружу их порты не публикуются.

## 2. Заполнить окружение

```bash
cp .env.example .env
```

В `.env` обязательно задать реальные значения:

- `BOT_TOKEN` — токен из BotFather;
- `ADMIN_CHAT_ID` — Telegram ID администратора для feedback и админ-команд;
- `DATABASE_URL` — `postgresql+asyncpg://...`;
- `POSTGRES_PASSWORD` — пароль PostgreSQL для Compose;
- `STEAM_API_KEY` — ключ Steam Web API;
- `DATA_ENCRYPTION_KEY` — ключ Fernet для шифрования сохранённого Steam ID64;
- `WEBAPP_MODE=production`;
- `WEBAPP_AUTH_REQUIRED=1`;
- `WEBAPP_URL=https://...`;
- `BOT_USERNAME`, `PRIVACY_URL`, `TERMS_URL`, `SUPPORT_URL` — публичные ссылки.

Для генерации ключа шифрования:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`WEBAPP_URL`, legal-ссылки и support-ссылка должны быть HTTPS URL. Не вставляйте
значения из `.env` в issues, логи или публичные сообщения.

## 3. Проверить конфигурацию до запуска

```bash
python scripts/preflight.py
```

Команда завершится с ошибкой, если production-конфигурация неполная. Она не
печатает значения секретов.

## 4. Запустить общий контур

```bash
docker compose up -d --build
docker compose ps
curl -fsS https://your-domain.example/healthz
```

Сервис `migrate` применяет Alembic-миграции до запуска бота, worker и Mini App.
В production Steam-опрос выполняет только `worker.py`; Mini App не запускает
второй независимый опросщик.

## 5. Настроить Telegram

В BotFather укажите `WEBAPP_URL` как URL Telegram Web App и добавьте кнопку
открытия Mini App в меню бота. После запуска проверить:

1. `/start` и `/app` в боте;
2. загрузку списка игр в Mini App;
3. добавление игры в Mini App и её появление в боте;
4. уведомление worker по общей базе;
5. `/premium` и тестовый Stars-платёж в отдельном тестовом чате;
6. `/delete_data` и удаление данных через Mini App.

## 6. Эксплуатация

- Делайте регулярный backup PostgreSQL через `scripts/backup_db.py`.
- Следите за `/healthz`, логами bot/worker/webapp и heartbeat worker.
- Не публикуйте `.env`, backup-файлы, токены и ключи шифрования.
- Перед каждым изменением запускайте тесты и `scripts/check_secrets.py`.
