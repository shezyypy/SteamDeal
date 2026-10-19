-- СПРАВОЧНАЯ копия схемы — соответствует app/db/models.py и миграциям
-- app/db/migrations/versions/0001_initial.py + 0002_chats_premium_wishlist.py.
--
-- В проекте это создаётся командой `alembic upgrade head`, а не этим файлом.
-- Используй его только чтобы посмотреть/понять структуру, либо в крайнем случае — для
-- ручного бутстрапа среды без Python (тогда сразу после выполнения нужно проставить
-- Alembic'у, что база уже на актуальной версии: `alembic stamp head`).

CREATE TABLE users (
    tg_id       BIGINT PRIMARY KEY,
    username    VARCHAR(64),
    steam_id64  VARCHAR(32),                              -- для повторного /import_wishlist без ссылки
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chats (
    chat_id        BIGINT PRIMARY KEY,                    -- для личных чатов совпадает с users.tg_id
    chat_type      VARCHAR(20) NOT NULL,                  -- private / group / supergroup / channel
    title          VARCHAR(256),
    premium_until  TIMESTAMPTZ,                           -- NULL или дата в прошлом = free
    price_region   VARCHAR(8) NOT NULL DEFAULT 'ru',       -- cc-код Steam, смена — Premium-фича
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tracked_games (
    id                  SERIAL PRIMARY KEY,
    chat_id             BIGINT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    appid               INTEGER NOT NULL,
    name                VARCHAR(256) NOT NULL,
    added_by_tg_id      BIGINT,                            -- снэпшот для "добавил(а) @username" в группах
    added_by_username   VARCHAR(64),
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chat_game UNIQUE (chat_id, appid)
);
CREATE INDEX ix_tracked_games_appid ON tracked_games (appid);

CREATE TABLE sale_snapshots (
    id                     SERIAL PRIMARY KEY,
    appid                  INTEGER NOT NULL,
    name                   VARCHAR(256) NOT NULL,
    price_original_cents   INTEGER NOT NULL,
    price_final_cents      INTEGER NOT NULL,
    discount_percent       INTEGER NOT NULL,
    currency               VARCHAR(8) NOT NULL DEFAULT 'RUB',
    fetched_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_sale_snapshots_appid ON sale_snapshots (appid);

CREATE TABLE notification_log (
    id                 SERIAL PRIMARY KEY,
    chat_id            BIGINT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    appid              INTEGER NOT NULL,
    discount_percent   INTEGER NOT NULL,
    sent_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_notification UNIQUE (chat_id, appid, discount_percent)
);
CREATE INDEX ix_notification_log_appid ON notification_log (appid);
