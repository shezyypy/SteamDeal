-- Выполнить ОДИН РАЗ от суперпользователя postgres (psql -U postgres -f sql/bootstrap.sql),
-- до запуска бота. Это создаёт саму базу и отдельного пользователя под неё — не стоит
-- держать бота на суперпользователе postgres в проде, даже если для локальной разработки
-- сейчас проще использовать существующего postgres-юзера.

CREATE ROLE steam_bot WITH LOGIN PASSWORD 'change_me';

CREATE DATABASE steam_sale_bot
    OWNER steam_bot
    ENCODING 'UTF8';

GRANT ALL PRIVILEGES ON DATABASE steam_sale_bot TO steam_bot;
