"""SteamSale Mini App — API-сервер + фоновый воркер (демо-режим).

Полный набор функций бота, пересобранный в интерфейс мини-аппки:
  • отслеживание игр (добавление поиском, удаление, целевая цена, история цен);
  • «Сделки»: живые скидки (ваши + глобальный фид Steam), сезонный баннер, дайджест;
  • Market-алерты по предметам (реальные цены Steam Community Market, одноразовые);
  • Premium/триал/порог скидки/регион, лента уведомлений, статистика экономии,
    импорт вишлиста (демо-набор или реальный публичный steamid64).

Данные живут в DemoStore (JSON-файл). Реальная интеграция с Postgres и Telegram —
по карте в webapp/README.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from steamapi import SteamClient, REGION_LABELS
from store import DemoStore, FREE_GAME_LIMIT, TRIAL_DAYS, REFERRAL_REWARD_DAYS, SEASONAL_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

HOST = os.environ.get("WEBAPP_HOST", os.environ.get("HOST", "127.0.0.1"))
PORT = int(os.environ.get("WEBAPP_PORT", os.environ.get("PORT", "8787")))

# Демо-админка живёт на /admin и открывается по коду. Код не имеет публичного
# значения по умолчанию: задайте ADMIN_CODE и ADMIN_SECRET через окружение.
ADMIN_CODE = os.environ.get("ADMIN_CODE", "")
_ADMIN_COOKIE = "ss_admin"
_ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

# Демо-набор «чужой вишлист» для кнопки /import (реальные appid популярных игр).
DEMO_WISHLIST = [730, 570, 271590, 377160, 2369390, 1172470, 1222670, 291550, 289070, 427520]

# Пара "глобальные сделки": кэш + TTL
_DEALS_CACHE: dict = {"ts": 0, "items": [], "total": None, "total_all": None, "hidden": None}
_DEALS_TTL = 180

# Минимальный зазор между живыми запросами страниц каталога к Steam (анти-буст).
_CATALOG_GAP = float(os.environ.get("CATALOG_GAP", "1.2"))

# Обзор рынка: наборы «игр» (appid -> метка в UI). render отдаёт цены только в USD
# центах, поэтому строки пересчитываются в валюту региона по курсу Steam (см. fx).
_OVERVIEW_GAMES = {
    "cs2":   {"appid": 730,    "title": "CS2"},
    "dota2": {"appid": 570,    "title": "Dota 2"},
    "tf2":   {"appid": 440,    "title": "TF2"},
    "rust":  {"appid": 252490, "title": "Rust"},
}
# валюта отображения цен Market по региону (для обзора)
_CC_CUR = {"ru": "RUB", "us": "USD", "de": "EUR", "ua": "RUB"}
_OVERVIEW_TTL = 75.0          # сколько живёт кэш обзора каждой игры
_MARKET_FX_TTL = 600.0        # курс «руб/цент-USD по ценам Steam» — на 10 минут
_OVERVIEW_COOLDOWN_BASE = 60.0   # после неудачи Steam: пауза перед следующей попыткой
_OVERVIEW_COOLDOWN_MAX = 300.0   # ... с экспоненциальным ростом до 5 минут
# файл-снимок обзора: когда Steam временно 429, отдаём последний удачный топ,
# а не пустой экран (снимок обновляется при каждом успешном ответе Steam)
_OVERVIEW_SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "market_overview_snapshot.json")
# «резервная витрина»: когда Steam лимитирует render-эндпоинт «топ по лотам»
# (обычная пауза), показываем популярные предметы с живыми ценами priceoverview.
# Список намеренно короткий и редкий, чтобы не продлевать 429-блокировку.
_OVERVIEW_FALLBACK = {
    "cs2": [(730, "Glove Case"), (730, "Fracture Case"), (730, "Kilowatt Case"),
            (730, "AK-47 | Redline (Field-Tested)")],
    "tf2": [(440, "Mann Co. Supply Crate Key"), (440, "Tour of Duty Ticket")],
}
_OVERVIEW_FALLBACK_TTL = 240.0   # кэш резерва: не чаще чем раз в 4 минуты трогаем Steam
_OVERVIEW_FALLBACK_KEEP = 600.0  # даже «устаревший» резерв показываем ещё 10 минут, если Steam молчит


def _market_hash_from_url(text: str) -> tuple[int, str] | None:
    """Парсит https://steamcommunity.com/market/listings/<appid>/<hash> -> (appid, hash)."""
    m = re.search(r"/market/listings/(\d+)/([^?/]+)", text.strip())
    if not m:
        return None
    try:
        appid = int(m.group(1))
    except ValueError:
        return None
    return appid, m.group(2)


class ApiServer:
    def __init__(self) -> None:
        self.store = DemoStore()
        self.steam = SteamClient()
        self.worker_lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None
        self._market_price_cache: dict = {"ts": 0.0, "prices": {}}
        # кэш метаданных предметов (иконка/тип) — не долбить Market при частых look-up'ах
        self._market_meta_cache: dict = {}
        # кэш автокомплита: (appid, query) -> (ts, items)
        self._suggest_cache: dict = {}
        # листаемый каталог распродаж: (region, start) -> (ts, ответ deal_page)
        self._deal_page_lock = asyncio.Lock()
        self._deal_pages: dict[tuple, tuple[float, dict]] = {}
        # анти-буст Steam: не чаще одного живого запроса страницы каталога за _CATALOG_GAP
        self._catalog_last_fetch = 0.0
        # кулдаун после 429/пустых ответов Steam (глобально, чтобы не долбить)
        self._catalog_cooldown_until = 0.0
        # сводка каталога региона (из первой страницы): total/hidden/effective
        self._catalog_info: dict[str, dict] = {}
        # обзор рынка: (game, cc) -> {"ts": float, "data": dict}; курс руб/USD-cents
        self._overview_cache: dict[tuple, dict] = {}
        self._market_fx: dict = {"rate": None, "rate_ts": 0.0}
        # последний живой запрос цены по hash_name (анти-троттлинг priceoverview)
        self._market_last_live: dict[str, float] = {}
        # single-flight: check+запрос цены атомарны, иначе 2 параллельных тика
        # оба видят «ещё не запрашивали» и летят в Steam одним залпом -> 429
        self._market_price_flight = asyncio.Lock()
        # --- демо-админка: heartbeat воркера, кольцевой буфер логов, статусы Steam ---
        self._started_at = time.time()
        self._last_cycle: dict | None = None
        self._log_lines: list[str] = []
        self._steam_status: dict = {"market": {"ok": 0, "empty": 0, "at": None},
                                    "deals": {"ok": 0, "empty": 0, "at": None}}
        self._attach_log_ring()
        # обзор рынка: кулдаун после неудач Steam + кэш «резервной витрины» (per game)
        self._overview_fail_until = 0.0
        self._overview_fail_count = 0
        self._overview_fallback_cache: dict[str, tuple[float, dict]] = {}

    def _attach_log_ring(self) -> None:
        """Кольцевой буфер логов для админки (последние ~600 строк)."""

        class _Ring(logging.Handler):
            def __init__(self, buf: list[str]):
                super().__init__()
                self._buf = buf
                self.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"))

            def emit(self, record: logging.LogRecord) -> None:
                try:
                    line = self.format(record)
                    self._buf.append(line)
                    if len(self._buf) > 600:
                        del self._buf[:len(self._buf) - 600]
                except Exception:  # noqa: BLE001
                    pass

        logging.getLogger().addHandler(_Ring(self._log_lines))

    # ------------------------------------------------------------ setup

    def _admin_token(self) -> str:
        return hashlib.sha256((_ADMIN_SECRET + ":" + ADMIN_CODE).encode()).hexdigest()

    def _admin_ok(self, request: web.Request) -> bool:
        """Код из cookie-токена либо X-Admin-Code / ?code= (удобно для curl)."""
        tok = request.cookies.get(_ADMIN_COOKIE, "")
        if tok and tok == self._admin_token():
            return True
        head = request.headers.get("X-Admin-Code", "")
        query = request.query.get("code", "")
        given = head or query
        return bool(given) and given == ADMIN_CODE

    async def startup(self) -> None:
        await self.store.load()
        await self.steam.start()
        # первичный опрос демо-списка в фоне + кэш глобальных сделок
        self._worker_task = asyncio.get_running_loop().create_task(self._background_loop())

    async def cleanup(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        await self.steam.close()

    async def _background_loop(self) -> None:
        """Фоновый воркер: раз в 150 с тихо проверяет цены игр и market-алерты."""
        await asyncio.sleep(2)          # даём серверу подняться
        try:
            while True:
                try:
                    await self.full_cycle()
                except Exception:
                    logger.exception("Фоновый цикл упал")
                await asyncio.sleep(150)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------ циклы проверок

    async def full_cycle(self) -> dict:
        """Один полный проход воркера (как worker.py в боте): цены игр + market + сделки.
        Одновременно может идти только один цикл (защита от ручного + фонового)."""
        if self.worker_lock.locked():
            return {"locked": True}
        async with self.worker_lock:
            summary: dict = {"games_checked": 0, "sales_found": 0, "new_events": 0,
                             "market_checked": 0, "market_triggered": 0}
            before = self.store.unread_events()

            summary["games_checked"], sales = await self._games_cycle()
            summary["sales_found"] = len(sales)

            await self._refresh_deals_cache()
            summary["market_checked"], summary["market_triggered"] = await self._market_cycle()

            # дайджест и сезонный баннер (только если включён /digest)
            tracked_sale_count = sum(1 for s in sales if s["discount_percent"] >= self.store.settings["threshold"])
            if self.store.settings["digest_enabled"]:
                await self.store.digest_event_if_needed(sales, self.store.settings["region"])
                await self.store.seasonal_banner_if_due(tracked_sale_count, len(self.store.tracked()))
            summary["new_events"] = self.store.unread_events() - before
            summary["ts"] = time.time()
            self._last_cycle = dict(summary)
            return summary

    async def _games_cycle(self) -> tuple[int, list[dict]]:
        """Проверяет цены отслеживаемых игр, пишет историю и шлёт «уведомления» (события).
        Возвращает (сколько игр проверено, список найденных распродаж)."""
        appids = self.store.tracked_appids()
        if not appids:
            return 0, []
        cc = self.store.settings["region"]
        threshold = self.store.settings["threshold"]
        premium = self.store.is_premium()
        sales: list[dict] = []

        for appid in appids:
            info = await self.steam.appdetails(appid, cc)
            if not info:
                continue
            price = info.get("price")
            game = self.store.get_tracked(appid)
            if game is None:
                continue
            if price is None:
                self.store.prices[appid] = {**info, "fetched_at": time.time()}
                continue

            # -- история цен: сначала демо-кривая (только пока точек мало), потом реальная точка
            await self.store.seed_demo_history(
                appid, price["price_original_cents"], price["price_final_cents"], price["currency"],
            )
            await self.store.add_history_point(
                appid, price["price_final_cents"], price["discount_percent"],
                price["currency"], _now_iso(),
            )

            self.store.prices[appid] = {
                **info,
                "fetched_at": time.time(),
                "last_notified_discount": game.get("last_discount", 0),
            }

            # -- целевая цена (Premium-фича в плане фич, здесь показываем всегда)
            target = game.get("target_cents")
            if target is not None and price["price_final_cents"] <= target:
                await self.store.set_target(appid, None)
                self.store.push_event(
                    "target", "🎯 Цель достигнута!",
                    f"{info['name']} теперь {price['price_final_cents'] / 100:g} "
                    f"{price['currency']} — ниже вашей цели {target / 100:g}",
                    price,
                )

            # -- уведомление о скидке (дедуп по уровню скидки, как в боте)
            disc = price["discount_percent"]
            last_disc = game.get("last_discount") or 0
            if disc > 0 and disc >= threshold:
                # исторический минимум (только для Premium — как в боте)
                # «якорь» low_notified_cents не даёт спамить: событие — только когда
                # цена упала ниже уже объявленного минимума (seed-точки в prev_min
                # иначе «подпирали» бы порог и событие слалось бы каждый цикл).
                points = self.store.history_points(appid)
                if premium and len(points) > 1:
                    prev_min = min(p["f"] for p in points[:-1])
                    low_mark = game.get("low_notified_cents")
                    if (price["price_final_cents"] < prev_min
                            and price["price_final_cents"] < price["price_original_cents"]
                            and (low_mark is None or price["price_final_cents"] < low_mark)):
                        await self.store.set_game_field(appid, "low_notified_cents", price["price_final_cents"])
                        self.store.push_event(
                            "low", "🏆 Исторический минимум!",
                            f"{info['name']} — {price['price_final_cents'] / 100:g} {price['currency']}."
                            f" Самая низкая цена за всё время наблюдений.",
                            price,
                        )
                if disc != last_disc:
                    await self.store.set_game_field(appid, "last_discount", disc)
                    orig = price["price_original_cents"] / 100
                    final = price["price_final_cents"] / 100
                    self.store.push_event(
                        "sale", f"🔥 −{disc}% на «{info['name']}»",
                        f"{orig:g} → {final:g} {price['currency']}",
                        price,
                    )
                    sales.append({**price, "name": info["name"]})
            elif disc == 0:
                if last_disc:
                    await self.store.set_game_field(appid, "last_discount", 0)

        return len(appids), sales

    async def _market_cycle(self) -> tuple[int, int]:
        """Проверка market-алертов: один запрос на каждый уникальный предмет.
        Успешные цены кладём в кэш (переживает 429)."""
        alerts = self.store.market_alerts()
        if not alerts:
            return 0, 0
        prices: dict[str, dict] = {}
        for alert in alerts:
            if alert["market_hash_name"] not in prices:
                p = await self.steam.market_price(alert["appid"], alert["market_hash_name"],
                                                  self.store.settings["region"])
                if p and p.get("lowest_cents") is not None:
                    await self.store.set_market_cached_price(alert["market_hash_name"], p)
                    try:  # реальное наблюдение -> история для мини-графика
                        await self.store.note_market_price(alert["market_hash_name"], p["lowest_cents"])
                    except Exception:  # noqa: BLE001
                        logger.warning("note_market_price (worker) не удалось", exc_info=True)
                prices[alert["market_hash_name"]] = p or {}
        triggered = await self.store.apply_market_prices(prices)
        # обновляем кэш цен предметов (для отображения в списке). Пустые ответы
        # (429) не затирают последнюю удачную цену — иначе живой тик алертов
        # начинал бы долбить Steam каждые 25 с заново.
        good = {h: p for h, p in prices.items() if p and p.get("lowest_cents") is not None}
        self._market_price_cache = {"ts": time.time(), "prices": {**self._market_price_cache["prices"], **good}}
        st = self._steam_status["market"]
        st["at"] = time.time()
        st["ok"] += len(good)
        st["empty"] += max(0, len(prices) - len(good))
        return len(alerts), len(triggered)

    async def _market_price_smart(self, appid: int, market_hash_name: str) -> dict:
        """Живая цена Market, при недоступности (429) — последняя известная из кэша.
        Возвращает {"price": {...}|None, "stale": bool, "limited": bool}.

        Single-flight через лок: живые тики клиента могут приходить одновременно
        (несколько вкладок/запросов), и без атомарной проверки каждый из них считал
        бы «ещё не запрашивали» и дублировал запрос к Steam одним залпом -> 429.

        Не чаще ~1 живого запроса на предмет в 75 с: «живой» тик алертов ходит
        каждые 25 с, и без троттлинга priceoverview ловил бы 429 на каждом тике."""
        async with self._market_price_flight:
            return await self._market_price_smart_core(appid, market_hash_name)

    async def _market_price_smart_core(self, appid: int, market_hash_name: str) -> dict:
        now = time.time()
        last = self._market_last_live.get(market_hash_name)
        cached = self.store.market_cached_price(market_hash_name)
        if last and now - last < 75:
            if cached and cached.get("lowest_cents") is not None:
                return {"price": cached, "stale": True, "limited": False}
            return {"price": None, "stale": False, "limited": True}
        self._market_last_live[market_hash_name] = now
        p = await self.steam.market_price(appid, market_hash_name, self.store.settings["region"])
        if p and p.get("lowest_cents") is not None:
            await self.store.set_market_cached_price(market_hash_name, p)
            return {"price": p, "stale": False, "limited": False}
        if cached and cached.get("lowest_cents") is not None:
            return {"price": cached, "stale": True, "limited": False}
        return {"price": None, "stale": False, "limited": True}

    async def _refresh_deals_cache(self) -> None:
        if time.time() - _DEALS_CACHE["ts"] < _DEALS_TTL:
            return
        cc = self.store.settings["region"]
        # два источника: «витринные» спецпредложения (10) + большой список распродаж поиска (50)
        featured = await self.steam.specials(cc)
        search_many = await self.steam.deal_search(cc, count=50)
        # число распродаж по каталогу игр региона — из первой страницы листаемого каталога
        # (total минус скрытые настройками = столько реально можно долистать)
        await self._deal_page_cached(cc, 0)
        ci = self._catalog_info.get(cc, {})
        _DEALS_CACHE["total"] = ci.get("effective")
        _DEALS_CACHE["total_all"] = ci.get("total")
        _DEALS_CACHE["hidden"] = ci.get("hidden")

        merged: list[dict] = []
        seen: set[int] = set()
        for it in featured:
            if it["discount_percent"] > 0 and it["appid"] not in seen:
                merged.append(it)
                seen.add(it["appid"])
        for it in search_many:
            if it["appid"] not in seen:
                merged.append(it)
                seen.add(it["appid"])
        # Steam может лечь/лимитировать. Тогда фид держим на последнем удачном снимке
        # (помечаем stale и не сбрасываем ts, чтобы скоро повторить), а не пустотой.
        had_old = bool(_DEALS_CACHE.get("items"))
        ci_ok = ci.get("effective") is not None
        dst = self._steam_status["deals"]
        dst["at"] = time.time()
        if merged or not had_old:
            _DEALS_CACHE["items"] = merged
            _DEALS_CACHE["ts"] = time.time()
            _DEALS_CACHE["stale"] = False
            dst["ok"] += 1
        else:
            _DEALS_CACHE["stale"] = True
            dst["empty"] += 1
            logger.warning("Steam не отдал фид сделок (%d featured, %d search) — держим "
                           "последний снимок %d, повтор через %ds",
                           len(featured), len(search_many), len(_DEALS_CACHE["items"]), _DEALS_TTL)
        if ci_ok:
            _DEALS_CACHE["total"] = ci.get("effective")
            _DEALS_CACHE["total_all"] = ci.get("total")
            _DEALS_CACHE["hidden"] = ci.get("hidden")
        logger.info("Глобальные сделки: %d в фиде (featured %d + search), всего в каталоге: %s",
                    len(_DEALS_CACHE["items"]), len(featured), _DEALS_CACHE.get("total"))

    # ------------------------------------------------------------ помощь в JSON

    def _game_card(self, appid: int) -> dict:
        cached = self.store.prices.get(appid, {})
        game = self.store.get_tracked(appid) or {}
        return {
            "appid": appid,
            "name": cached.get("name") or f"Игра {appid}",
            "header_image": cached.get("header_image"),
            "price": cached.get("price"),
            "fetched_at": cached.get("fetched_at"),
            "added_at": game.get("added_at"),
            "target_cents": game.get("target_cents"),
            "on_sale": bool(cached.get("price") and cached["price"]["discount_percent"] > 0),
        }

    # ------------------------------------------------------------ routes

    async def api_session(self, request: web.Request) -> web.Response:
        return web.json_response(self.store.session_view())

    async def api_session_set(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = body.get("action")
        if action == "region" and body.get("region") in REGION_LABELS:
            await self.store.set_region(body["region"])
            await self.full_cycle()  # пересчитываем всё под новую валюту
            return web.json_response({"ok": True})
        if action == "threshold":
            await self.store.set_threshold(int(body.get("percent") or 0))
            return web.json_response({"ok": True})
        if action == "digest":
            await self.store.set_digest(bool(body.get("enabled")))
            return web.json_response({"ok": True})
        if action == "premium_buy":
            await self.store.buy_premium()
            return web.json_response({"ok": True})
        if action == "trial":
            ok = await self.store.use_trial()
            return web.json_response({"ok": ok})
        if action == "revoke":
            await self.store.revoke_premium()
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "unknown"}, status=400)

    async def api_games(self, request: web.Request) -> web.Response:
        cards = [self._game_card(g["appid"]) for g in self.store.tracked()]
        cards.sort(key=lambda c: c["added_at"] or "", reverse=True)
        return web.json_response({"games": cards})

    async def api_game_add(self, request: web.Request) -> web.Response:
        body = await request.json()
        appid = int(body.get("appid", 0) or 0)
        if not appid:
            return web.json_response({"ok": False, "error": "bad_request"})
        if self.store.is_tracked(appid):
            return web.json_response({"ok": False, "error": "already_tracked", "message": "Эта игра уже в списке."})
        cc = self.store.settings["region"]
        info = await self.steam.appdetails(appid, cc)
        if not info:
            return web.json_response({"ok": False, "error": "not_found",
                                      "message": "Steam не отдал данные по этой игре (возможно, недоступна в вашем регионе)."})
        res = await self.store.add_game(appid, info["name"], info.get("header_image"))
        if not res["ok"]:
            return web.json_response(res)
        # кэшируем и подкладываем демо-историю
        price = info.get("price")
        if price:
            await self.store.seed_demo_history(appid, price["price_original_cents"],
                                               price["price_final_cents"], price["currency"])
            await self.store.add_history_point(appid, price["price_final_cents"],
                                               price["discount_percent"], price["currency"], _now_iso())
        self.store.prices[appid] = {**info, "fetched_at": time.time()}
        return web.json_response({"ok": True, "game": self._game_card(appid)})

    async def api_game_remove(self, request: web.Request) -> web.Response:
        body = await request.json()
        appid = int(body.get("appid", 0) or 0)
        ok = await self.store.remove_game(appid)
        self.store.prices.pop(appid, None)
        return web.json_response({"ok": ok})

    async def api_game_detail(self, request: web.Request) -> web.Response:
        appid = int(request.match_info["appid"])
        if not self.store.is_tracked(appid):
            return web.json_response({"error": "not_tracked"}, status=404)
        history = self.store.history_points(appid)
        return web.json_response({"game": self._game_card(appid), "history": history})

    async def api_game_target(self, request: web.Request) -> web.Response:
        appid = int(request.match_info["appid"])
        body = await request.json()
        cents = body.get("cents")
        if not self.store.is_tracked(appid):
            return web.json_response({"ok": False})
        await self.store.set_target(appid, int(cents) if cents else None)
        return web.json_response({"ok": True})

    async def api_games_refresh(self, request: web.Request) -> web.Response:
        summary = await self.full_cycle()
        if summary.get("locked"):
            return web.json_response({"ok": False, "error": "busy", "message": "Обновление уже идёт…"})
        return web.json_response({"ok": True, "summary": summary})

    async def api_search(self, request: web.Request) -> web.Response:
        q = (request.query.get("q") or "").strip()
        if len(q) < 2:
            return web.json_response({"items": []})
        items = await self.steam.search(q, self.store.settings["region"])
        for it in items:
            it["tracked"] = self.store.is_tracked(it["appid"])
        return web.json_response({"items": items})

    async def api_deals(self, request: web.Request) -> web.Response:
        await self._refresh_deals_cache()
        cc = self.store.settings["region"]
        threshold = self.store.settings["threshold"]
        # «ваши скидки» — по кэшированным ценам отслеживаемых
        tracked_sales = []
        for g in self.store.tracked():
            p = (self.store.prices.get(g["appid"]) or {}).get("price")
            if p and p["discount_percent"] >= threshold and p["discount_percent"] > 0:
                tracked_sales.append({**p, "appid": g["appid"],
                                      "name": (self.store.prices[g["appid"]].get("name") or f"Игра {g['appid']}"),
                                      "header_image": self.store.prices[g["appid"]].get("header_image")})
        tracked_sales.sort(key=lambda s: s["discount_percent"], reverse=True)

        global_items = []
        for it in _DEALS_CACHE["items"]:
            if it["discount_percent"] > 0:
                it = dict(it)
                it["tracked"] = self.store.is_tracked(it["appid"])
                it["is_free"] = it["price_final_cents"] == 0
                global_items.append(it)

        # «пульс Steam»: сколько игр со скидкой в листаемом каталоге региона, топ в фиде,
        # самая большая скидка. total = листаемые игры (эффектив), total_all — включая
        # DLC/издания, hidden — скрытые настройками региона.
        pulse = {"count": len(global_items), "total": _DEALS_CACHE.get("total"),
                 "total_all": _DEALS_CACHE.get("total_all"),
                 "hidden": _DEALS_CACHE.get("hidden"),
                 "stale": bool(_DEALS_CACHE.get("stale")),
                 "max": None, "cheapest": None, "ts": _DEALS_CACHE["ts"]}
        if global_items:
            pulse["max"] = max(global_items, key=lambda i: i["discount_percent"])
            non_free = [i for i in global_items if i["price_final_cents"] > 0]
            if non_free:
                pulse["cheapest"] = min(non_free, key=lambda i: i["price_final_cents"])

        seasonal = False
        tracked = self.store.tracked()
        if tracked:
            frac = len(tracked_sales) / len(tracked)
            seasonal = frac >= SEASONAL_THRESHOLD
        return web.json_response({
            "region": cc,
            "tracked_sales": tracked_sales,
            "global": global_items,
            "pulse": pulse,
            "seasonal": seasonal,
            "threshold": threshold,
        })

    @staticmethod
    def _parse_catalog_filters(query) -> tuple:
        """Достаём из query только те фильтры Steam, что мы проверили и умеем слать.
        Возвращает (extra_dict, key_tuple); пустые значения отбрасываются."""
        extra: dict = {}
        q = (query.get("q") or query.get("term") or "").strip()
        if q:
            # термин поиска Steam: обрезаем и убираем управляющие символы
            extra["term"] = re.sub(r"[\x00-\x1f]", "", q)[:80]
        raw_price = (query.get("maxprice") or "").strip()
        if raw_price:
            try:
                price = int(raw_price)
            except ValueError:
                price = 0
            if price in (150, 300, 450, 600, 750, 900, 1050, 1200, 1350, 1500, 1650, 1800):
                extra["maxprice"] = price
        fval = (query.get("filter") or "").strip()
        if fval in ("weeklongdeals", "globaltopsellers"):
            extra["filter"] = fval
        sval = (query.get("sort_by") or "").strip()
        if sval in ("Price_ASC", "Price_DESC"):
            extra["sort_by"] = sval
        key = tuple(sorted(extra.items()))
        return extra, key

    async def _deal_page_cached(self, cc: str, start: int, extra: tuple = ()) -> dict:
        """Страница каталога распродаж Steam: кэш 5 мин + зазор между живыми запросами.

        extra — кортеж проверенных фильтров (ключ кэша). start=0 без фильтров
        дополнительно обновляет базовую сводку каталога (для пульса). Пустые ответы
        НЕ кэшируем — обычно это временный лимит Steam.
        """
        key = (cc, start, extra)
        now = time.time()
        hit = self._deal_pages.get(key)
        if hit and (now - hit[0]) < 300:
            return hit[1]
        async with self._deal_page_lock:
            now = time.time()
            hit = self._deal_pages.get(key)
            if hit and (now - hit[0]) < 300:
                return hit[1]
            wait = max(_CATALOG_GAP - (now - self._catalog_last_fetch),
                      self._catalog_cooldown_until - now)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                res = await self.steam.deal_page(cc, start, count=100, games_only=True,
                                                 extra=dict(extra) or None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("deal_page(%s, %d, %s) упал: %s", cc, start, extra, exc)
                res = {"items": [], "total": None, "hidden": None}
            self._catalog_last_fetch = time.time()
            if not res.get("items"):
                # Steam лимитирует серию страниц (429): не долбим, держим общий кулдаун
                self._catalog_cooldown_until = time.time() + 25
            else:
                self._deal_pages[key] = (time.time(), res)
                if len(self._deal_pages) > 160:  # лёгкая LRU-эвакуация
                    oldest = min(self._deal_pages, key=lambda k: self._deal_pages[k][0])
                    del self._deal_pages[oldest]
            if start == 0 and not extra and res.get("items"):
                # Базовая сводка (без фильтров): «скрыто настройками» Steam всё равно
                # отдаёт при глубоком листании, поэтому листаемый объём = total.
                eff = int(res["total"]) if res.get("total") else None
                self._catalog_info[cc] = {"total": res.get("total"), "hidden": res.get("hidden"),
                                          "effective": eff}
            return res

    async def api_deals_global(self, request: web.Request) -> web.Response:
        """Следующая порция каталога распродаж Steam (offset `start`, до 100 за раз).
        Поддерживает проверенные фильтры Steam: q/term, maxprice, filter, sort_by.
        Сводку (total/hidden) Steam пишет на каждой странице — берём из ответа,
        базовую сводку без фильтров — как запасную."""
        cc = self.store.settings["region"]
        try:
            start = max(0, int(request.query.get("start", "0") or 0))
        except (TypeError, ValueError):
            start = 0
        extra, key = self._parse_catalog_filters(request.query)
        res = await self._deal_page_cached(cc, start, key)
        base = self._catalog_info.get(cc, {})
        items = res.get("items", [])
        total = res.get("total") if res.get("total") is not None else base.get("total")
        # Steam «скрыто настройками» всё равно отдаёт при глубоком листании: объём = total
        effective = int(total) if total else base.get("effective")
        if effective is not None:
            more = (start + len(items)) < effective
        else:
            # сводки ещё нет (первая страница не отдалась) — пустую страницу концом
            # не считаем, иначе клиент остановится на нуле
            more = True if not items else len(items) >= 100
        return web.json_response({
            "region": cc,
            "start": start,
            "items": items,
            "more": more,
            "count": len(items),
            "total": total,
            "hidden": res.get("hidden") if res.get("hidden") is not None else base.get("hidden"),
            "effective": effective,
            "filters": dict(key),   # что реально применилось (для UI-подписи)
        })

    async def api_wallet(self, request: web.Request) -> web.Response:
        """«Кошелёк вишлиста»: во что обойдётся покупка всего списка прямо сейчас
        vs полная цена по прайсу Steam. Считается из уже закэшированных живых цен —
        новых запросов к Steam не делает."""
        cc = self.store.settings["region"]
        items: list[dict] = []
        full_cents = today_cents = 0
        priced = free = on_sale = 0
        best_disc = 0
        updated_ts: float | None = None
        for g in self.store.tracked():
            cached = self.store.prices.get(g["appid"])
            if not cached:
                continue
            p = cached.get("price")
            if not p:
                continue
            priced += 1
            fa = cached.get("fetched_at") or 0
            updated_ts = max(updated_ts or 0, fa)
            o = int(p.get("price_original_cents") or 0)
            f = int(p.get("price_final_cents") or 0)
            disc = int(p.get("discount_percent") or 0)
            if o == 0 and f == 0:
                free += 1
            full_cents += o if o > 0 else f
            today_cents += f
            if disc > 0:
                on_sale += 1
                best_disc = max(best_disc, disc)
            items.append({
                "appid": g["appid"],
                "name": cached.get("name") or f"Игра {g['appid']}",
                "header_image": cached.get("header_image"),
                "full_cents": o if o > 0 else f,
                "today_cents": f,
                "discount_percent": disc,
                "on_sale": disc > 0,
            })
        items.sort(key=lambda i: (i["full_cents"] - i["today_cents"]), reverse=True)
        savings = full_cents - today_cents
        pct = round(savings * 100 / full_cents) if full_cents > 0 else 0
        return web.json_response({
            "region": cc,
            "priced_count": priced, "free_count": free, "on_sale_count": on_sale,
            "full_cents": full_cents, "today_cents": today_cents,
            "savings_cents": max(0, savings), "pct": max(0, pct),
            "best_discount": best_disc,
            "items": items,
            "updated_at": updated_ts,
            "age_sec": round(time.time() - updated_ts) if updated_ts else None,
        })

    async def api_wishlist_import(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("demo"):
            appids = DEMO_WISHLIST
            source = "демо-вишлиста"
        else:
            profile = (body.get("profile") or "").strip()
            # принимаем число или ссылку /profiles/<id64>
            m = re.search(r"(profiles/)?(\d{17})", profile)
            if not m or len(m.group(2)) != 17:
                return web.json_response({"ok": False, "error": "bad_profile",
                                          "message": "Для демо нужен числовой steamid64 или ссылка /profiles/<id64>. "
                                                     "Привязка по нику (vanity) требует STEAM_API_KEY — в боте она работает."})
            appids = await self.steam.wishlist(m.group(2))
            source = "вишлиста Steam"
            if appids is None:
                return web.json_response({"ok": False, "error": "network"})
            if not appids:
                return web.json_response({"ok": False, "error": "empty",
                                          "message": "Вишлист пуст или приватный — Steam не различает эти случаи. "
                                                     "Сделайте список желаемого публичным и попробуйте ещё раз."})

        added, skipped, total = await self._bulk_add(appids)
        return web.json_response({"ok": True, "source": source,
                                  "added": added, "skipped": skipped, "total": total})

    async def _bulk_add(self, appids: list[int]) -> tuple[int, int, int]:
        cc = self.store.settings["region"]
        added = skipped = 0
        for appid in appids:
            if self.store.is_tracked(appid):
                skipped += 1
                continue
            if not self.store.is_premium() and self.store.games_count() >= FREE_GAME_LIMIT:
                skipped += 1
                continue
            info = await self.steam.appdetails(appid, cc)
            if not info:
                skipped += 1
                continue
            res = await self.store.add_game(appid, info["name"], info.get("header_image"))
            if not res["ok"]:
                skipped += 1
                continue
            price = info.get("price")
            if price:
                await self.store.seed_demo_history(appid, price["price_original_cents"],
                                                   price["price_final_cents"], price["currency"])
                await self.store.add_history_point(appid, price["price_final_cents"],
                                                   price["discount_percent"], price["currency"], _now_iso())
            self.store.prices[appid] = {**info, "fetched_at": time.time()}
            added += 1
        return added, skipped, len(appids)

    # ---- market ---------------------------------------------------------

    async def api_market_alerts(self, request: web.Request) -> web.Response:
        alerts = self.store.market_alerts()
        cache = self._market_price_cache
        fresh = bool(cache and (time.time() - cache.get("ts", 0)) < 180)
        # старые алерты без иконки — лениво добиваем (не больше 8 за открытие)
        legacy = [a for a in alerts if not a.get("item_icon")][:8]
        result = []
        for a in alerts:
            price = stale = None
            if fresh:
                price = cache.get("prices", {}).get(a["market_hash_name"]) or None
            if not price or not price.get("lowest_cents"):
                smart = await self._market_price_smart(a["appid"], a["market_hash_name"])
                price, stale = smart["price"], smart["stale"]
            hist = self.store.market_history_points(a["market_hash_name"])[-40:]
            result.append({**a, "price": price, "stale": stale if stale is not None else not fresh,
                           "history": hist,
                           "history_demo": any(p.get("demo") for p in hist),
                           "currency": self.store.settings["region"].upper()})
        for a in legacy:  # иконки для алертов, созданных до появления витрины
            meta = await self._market_meta_cached(a["appid"], a["market_hash_name"])
            if meta and meta.get("icon"):
                await self.store.set_market_alert_meta(a["id"], meta.get("icon"), meta.get("type"))
                for r in result:
                    if r["id"] == a["id"]:
                        r["item_icon"], r["item_type"] = meta.get("icon"), meta.get("type")
        return web.json_response({"alerts": result})

    async def api_market_add(self, request: web.Request) -> web.Response:
        body = await request.json()
        hash_name = (body.get("market_hash_name") or "").strip()
        direction = body.get("direction")
        threshold_cents = int(body.get("threshold_cents") or 0)
        mode = body.get("mode") or "price"
        pct = body.get("pct")
        if pct is not None:
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                pct = None
        appid = int(body.get("appid") or 730)
        if not hash_name or direction not in ("below", "above") or mode not in ("price", "percent"):
            return web.json_response({"ok": False, "error": "bad_request",
                                      "message": "Нужны предмет, направление и тип цели."})
        if mode == "price" and threshold_cents <= 0:
            return web.json_response({"ok": False, "error": "bad_request",
                                      "message": "Укажите цену-цель."})
        if mode == "percent" and not (pct and 0 < pct <= 95):
            return web.json_response({"ok": False, "error": "bad_request",
                                      "message": "Процент должен быть от 0 до 95."})
        if not self.store.is_premium():
            return web.json_response({"ok": False, "error": "premium",
                                      "message": "Market-алерты — Premium-функция (как в боте)."})
        icon = (body.get("icon") or "").strip() or None
        item_type = (body.get("type") or "").strip() or None

        base_cents = None
        current = None
        if mode == "percent":
            # для %-алерта нужна базовая цена «от чего считаем»; тянем сейчас или из кэша
            smart = await self._market_price_smart(appid, hash_name)
            if smart["price"] and smart["price"].get("lowest_cents") is not None:
                current = smart["price"]["lowest_cents"]
                base_cents = current
            base_cents = int(body.get("base_cents") or 0) or base_cents
            if not base_cents:
                return web.json_response({"ok": False, "error": "no_price",
                                          "message": "Steam Market сейчас не отвечает — не от чего считать проценты. "
                                                     "Попробуйте ещё раз через минуту."})
            if direction == "below":
                threshold_cents = int(base_cents * (1 - pct / 100))
            else:
                threshold_cents = int(base_cents * (1 + pct / 100))
            res = await self.store.add_market_alert(appid, hash_name, direction, threshold_cents,
                                                    item_icon=icon, item_type=item_type,
                                                    mode="percent", pct=pct, base_cents=base_cents)
        else:
            # создаём алерт, не дожидаясь ответа Market (он может быть временно 429-троттлен)
            res = await self.store.add_market_alert(appid, hash_name, direction, threshold_cents,
                                                    item_icon=icon, item_type=item_type)
        if not res["ok"]:
            return web.json_response(res)
        # запоминаем цену наблюдением (для мини-графика)
        if current is None:
            smart = await self._market_price_smart(appid, hash_name)  # цена подтянется при возможности
            if smart["price"] and smart["price"].get("lowest_cents") is not None:
                current = smart["price"]["lowest_cents"]
        if current is not None:
            try:
                await self.store.note_market_price(hash_name, current)
            except Exception:  # noqa: BLE001
                pass
        return web.json_response({"ok": True, "alert": res["alert"],
                                  "current_price": {"lowest_cents": current} if current else {},
                                  "stale": bool(current is None), "premium": True})

    async def api_market_delete(self, request: web.Request) -> web.Response:
        alert_id = request.match_info["alert_id"]
        ok = await self.store.remove_market_alert(alert_id)
        return web.json_response({"ok": ok})

    async def _market_meta_cached(self, appid: int, query: str) -> dict | None:
        key = (int(appid or 0), (query or "").strip().lower())
        if not key[0] or not key[1]:
            return None
        cached = self._market_meta_cache.get(key)
        if cached is not None:
            # негативный кэш: Steam был недоступен — не долбим его каждый живой тик
            if isinstance(cached, dict) and cached.get("fail_ts"):
                if time.time() - cached["fail_ts"] < 300:
                    return None
                self._market_meta_cache.pop(key, None)
            else:
                return cached.get("meta") if isinstance(cached, dict) else cached
        try:
            meta = await self.steam.market_item_meta(key[0], query)
        except Exception as exc:  # noqa: BLE001 — превью не должно ронять lookup
            logger.warning("market_item_meta(%s) упал: %s", query, exc)
            meta = None
        if meta:
            if len(self._market_meta_cache) > 300:
                self._market_meta_cache.clear()
            self._market_meta_cache[key] = {"meta": meta, "fail_ts": None}
        else:
            # Steam вернул пусто (429/не найдено): повторим не раньше чем через 5 минут
            if len(self._market_meta_cache) > 300:
                self._market_meta_cache.clear()
            self._market_meta_cache[key] = {"meta": None, "fail_ts": time.time()}
        return meta

    def _preview_of(self, meta: dict | None) -> dict | None:
        if not meta:
            return None
        out = {"name": meta.get("name")}
        if meta.get("type"):
            out["type"] = meta["type"]
        if meta.get("icon"):
            out["image"] = meta["icon"]
        if meta.get("market_url"):
            out["market_url"] = meta["market_url"]
        return out

    async def api_market_lookup(self, request: web.Request) -> web.Response:
        q = request.query.get("q") or ""
        parsed = _market_hash_from_url(q)
        if parsed:
            appid, hash_name = parsed
        else:
            hash_name = q.strip()
            appid = int(request.query.get("appid") or 730)
        if not hash_name:
            return web.json_response({"ok": False, "error": "empty"})
        try:
            import urllib.parse
            hash_name = urllib.parse.unquote(hash_name)
        except Exception:
            pass

        # цена и метаданные (превью) — параллельно, чтобы lookup отвечал быстрее
        smart, meta = await asyncio.gather(
            self._market_price_smart(appid, hash_name),
            self._market_meta_cached(appid, hash_name),
        )
        limited = smart["limited"]
        price_ok = bool(smart["price"] and smart["price"].get("lowest_cents") is not None)

        canonical = hash_name
        if limited or not price_ok:
            # цена не получена — поиск всё равно мог найти предмет: показываем превью
            # и пробуем точное каноничное название (полезно, когда ввели неполное имя)
            if meta and meta["name"].lower() != hash_name.lower():
                canonical = meta["name"]
                if not limited:
                    smart = await self._market_price_smart(appid, canonical)
                    limited = smart["limited"]
                    price_ok = bool(smart["price"] and smart["price"].get("lowest_cents") is not None)
            if limited:
                hist = self.store.market_history_points(canonical)
                return web.json_response({
                    "ok": False, "error": "market_limited",
                    "message": "Steam Market сейчас ограничивает запросы (429). "
                               "Алерт можно поставить по названию — цену подтянем, как только API ответит.",
                    "canonical": canonical, "preview": self._preview_of(meta),
                    "history": hist[-60:], "history_demo": self.store.market_history_has_demo(canonical),
                })
            if not price_ok:
                return web.json_response({
                    "ok": False, "error": "not_found",
                    "message": "Предмет не найден (проверьте название или ссылку).",
                    "canonical": canonical, "preview": self._preview_of(meta),
                })

        # цена есть: если поиск для превью вернул другой предмет — уточним иконку по точному имени
        if meta and meta.get("name") and meta["name"].lower() != canonical.lower():
            meta2 = await self._market_meta_cached(appid, canonical)
            if meta2:
                meta = meta2

        # реальное наблюдение цены -> копим историю для мини-графика
        if smart["price"] and smart["price"].get("lowest_cents") is not None:
            try:
                await self.store.note_market_price(canonical, smart["price"]["lowest_cents"])
            except Exception as exc:  # noqa: BLE001 — история не должна ронять lookup
                logger.warning("note_market_price(%s) упал: %s", canonical, exc)
        hist = self.store.market_history_points(canonical)
        return web.json_response({"ok": True, "appid": appid, "market_hash_name": canonical,
                                  "price": smart["price"], "stale": smart["stale"],
                                  "premium": self.store.is_premium(),
                                  "preview": self._preview_of(meta),
                                  "history": hist[-60:], "history_demo": self.store.market_history_has_demo(canonical)})

    async def _market_fx_rate(self) -> tuple[float | None, bool]:
        """Приблизительный курс «цены Steam в центах региона / USD-центе»: render
        отдаёт только USD, а нам нужны рубли. Берём первый предмет топа CS2 из
        кэша обзора (или одним render-запросом) и снимаем его цену priceoverview
        в валюте региона. Не чаще раза в _MARKET_FX_TTL; при 429 — старый курс."""
        fx = self._market_fx
        if fx["rate"] is not None and time.time() - fx["rate_ts"] < _MARKET_FX_TTL:
            return fx["rate"], False
        cc = self.store.settings["region"]
        row = None
        cached = self._overview_cache.get(("cs2", cc))
        if cached and cached["data"].get("rows"):
            row = cached["data"]["rows"][0]
        if not row:
            try:
                top = await self.steam.market_top(730, count=1, cc=cc)
                if top:
                    row = top[0]
            except Exception as exc:  # noqa: BLE001
                logger.warning("fx: market_top(730) упал: %s", exc)
        if row and row.get("usd_cents"):
            try:
                p = await self.steam.market_price(730, row["hash"], cc)
            except Exception as exc:  # noqa: BLE001
                p = None
                logger.warning("fx: market_price(%s) упал: %s", row["hash"], exc)
            if p and p.get("lowest_cents"):
                rate = p["lowest_cents"] / row["usd_cents"]
                fx.update({"rate": rate, "rate_ts": time.time()})
                return rate, False
        # Steam не ответил — держим последний известный курс (может быть None)
        return fx["rate"], fx["rate"] is not None

    def _overview_snapshot_save(self, game: str, data: dict) -> None:
        """Снимок успешного топа игры — на случай 429-окон у Steam."""
        try:
            old: dict = {}
            if os.path.exists(_OVERVIEW_SNAPSHOT):
                with open(_OVERVIEW_SNAPSHOT, encoding="utf-8") as fh:
                    old = json.load(fh)
            old[game] = data
            with open(_OVERVIEW_SNAPSHOT, "w", encoding="utf-8") as fh:
                json.dump(old, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001 — снимок не критичен
            logger.warning("не удалось сохранить снимок обзора %s", game, exc_info=True)

    def _overview_snapshot_get(self, game: str) -> dict | None:
        try:
            if os.path.exists(_OVERVIEW_SNAPSHOT):
                with open(_OVERVIEW_SNAPSHOT, encoding="utf-8") as fh:
                    return json.load(fh).get(game)
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _overview_paused(self, game: str, cached: dict | None) -> web.Response:
        """Steam молчит: не долбим его, а отдаём что есть. Неудача — только когда
        реально пробовали (cached снимок приходит без роста счётчика)."""
        now = time.time()
        if cached is None:
            self._overview_fail_count += 1
        cooldown = min(_OVERVIEW_COOLDOWN_MAX,
                       _OVERVIEW_COOLDOWN_BASE * (2 ** min(3, self._overview_fail_count - 1)))
        self._overview_fail_until = now + cooldown
        retry_in = int(cooldown)
        if cached:
            return web.json_response({**cached["data"], "stale": True, "retry_in": retry_in})
        snap = self._overview_snapshot_get(game)
        if snap:
            return web.json_response({**snap, "stale": True, "retry_in": retry_in})
        fb = await self._overview_fallback(game)
        if fb:
            return web.json_response(fb)
        return web.json_response({
            "ok": False, "error": "overview_paused", "game": game,
            "message": f"Steam Market сейчас ограничивает запросы (обычная пауза). "
                       f"Попробую снова автоматически через ~{retry_in} с.",
            "retry_in": retry_in,
        })

    async def _overview_fallback(self, game: str) -> dict | None:
        """Резервная витрина: популярные предметы с живыми ценами Steam
        (priceoverview работает, даже когда render-«топ по лотам» в 429-паузе).
        Редкая и короткая: при 2 неудачах подряд прекращаем трогать Steam и
        показываем прошлый резерв (до _OVERVIEW_FALLBACK_KEEP) — не кормим 429."""
        now = time.time()
        hit = self._overview_fallback_cache.get(game)
        cc = self.store.settings["region"]
        if hit:
            if now - hit[0] < _OVERVIEW_FALLBACK_TTL:
                return hit[1]
            stale_age = now - hit[0]
        else:
            stale_age = None
        pairs = _OVERVIEW_FALLBACK.get(game)
        if not pairs:
            return None
        rows: list[dict] = []
        fails = 0
        for appid, h in pairs:
            try:
                pr = await self.steam.market_price(appid, h, cc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fallback price %s: %s", h, exc)
                pr = None
            if not pr or pr.get("lowest_cents") is None:
                fails += 1
                if fails >= 2:  # Steam блокирует и цены — хватит на сегодня
                    break
                continue
            rows.append({
                "appid": appid, "hash": h, "name": h,
                "icon": None, "price_cents": pr["lowest_cents"],
                "listings": None, "volume": pr.get("volume"),
                "hist": [], "hist_demo": False,
            })
        if not rows:
            # оставляем последний удачный резерв, даже если он старше TTL
            if hit and stale_age is not None and stale_age < _OVERVIEW_FALLBACK_KEEP:
                return hit[1]
            return None
        rows.sort(key=lambda r: -(r.get("volume") or 0))
        data = {"ok": True, "game": game, "appid": _OVERVIEW_GAMES[game]["appid"],
                "title": _OVERVIEW_GAMES[game]["title"], "cc": cc,
                "cur": _CC_CUR.get(cc, "USD"), "fx_rate": None, "fx_stale": False,
                "ts": _now_iso(), "rows": rows, "stale": True, "fallback": True}
        self._overview_fallback_cache[game] = (now, data)
        return data

    async def api_market_overview(self, request: web.Request) -> web.Response:
        """🗺️ Обзор рынка: топ-10 предметов игры по активным лотам, живые цены
        (пересчитаны в валюту региона), число лотов и история для «тренда за день».
        При падении Steam — последний удачный снимок/резервная витрина с пометкой
        stale и плановым авто-повтором (без долбёжки Steam и «вечного спиннера»)."""
        game = (request.query.get("game") or "cs2").lower()
        cfg = _OVERVIEW_GAMES.get(game)
        if not cfg:
            return web.json_response({"ok": False, "error": "bad_game",
                                      "message": "Неизвестная игра. Доступно: " + ", ".join(_OVERVIEW_GAMES)})
        cc = self.store.settings["region"]
        key = (game, cc)
        now = time.time()
        cached = self._overview_cache.get(key)
        force = request.query.get("force") == "1"
        if cached and not force and now - cached["ts"] < _OVERVIEW_TTL:
            return web.json_response(cached["data"])
        if now < self._overview_fail_until and not force:
            return await self._overview_paused(game, cached)
        try:
            top = await self.steam.market_top(cfg["appid"], count=10, cc=cc)
            if not top:
                raise RuntimeError("Steam вернул пустой список предметов")
            rate, fx_stale = await self._market_fx_rate()
            rows: list[dict] = []
            seen: set[str] = set()
            for it in top:
                h = it["hash"]
                if h in seen:
                    continue
                seen.add(h)
                rub = round(it["usd_cents"] * rate) if (rate and it.get("usd_cents") is not None) else None
                # если истории ещё нет — сеем демо-кривую и первое наблюдение,
                # чтобы «тренд за день» и компас были сразу наглядными
                pts = self.store.market_history_points(h)
                if not pts and rub is not None:
                    try:
                        await self.store.note_market_price(h, rub)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("overview seed %s не удалось: %s", h, exc)
                    pts = self.store.market_history_points(h)
                rows.append({
                    "appid": cfg["appid"], "hash": h, "name": it["name"],
                    "icon": it["icon"], "usd_cents": it["usd_cents"], "price_cents": rub,
                    "listings": it["listings"],
                    "hist": pts[-60:], "hist_demo": any(p.get("demo") for p in pts),
                })
            data = {"ok": True, "game": game, "appid": cfg["appid"], "title": cfg["title"],
                    "cc": cc, "cur": _CC_CUR.get(cc, "USD"),
                    "fx_rate": rate, "fx_stale": bool(fx_stale),
                    "ts": _now_iso(), "rows": rows, "stale": False}
            self._overview_snapshot_save(game, data)
            self._overview_fail_count = 0
            self._overview_fail_until = 0.0
            self._overview_cache[key] = {"ts": now, "data": data}
            return web.json_response(data)
        except Exception as exc:  # noqa: BLE001 — при отказе Steam показываем снимок
            logger.warning("overview(%s) упал: %s", game, exc)
            return await self._overview_paused(game, cached)

    async def api_market_curated(self, request: web.Request) -> web.Response:
        return web.json_response({"items": CURATED_ITEMS})

    async def api_market_suggest(self, request: web.Request) -> web.Response:
        q = (request.query.get("q") or "").strip()
        appid = int(request.query.get("appid") or 730)
        if len(q) < 2:
            return web.json_response({"items": []})
        key = (appid, q.lower())
        hit = self._suggest_cache.get(key)
        if hit and (time.time() - hit[0]) < 60:
            return web.json_response({"items": hit[1]})
        try:
            items = await self.steam.market_search(appid, q, limit=7)
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_search упал: %s", exc)
            items = []
        self._suggest_cache[key] = (time.time(), items)
        if len(self._suggest_cache) > 120:
            self._suggest_cache.clear()
        return web.json_response({"items": items})

    # ---- лента событий / статистика ------------------------------------

    async def api_events(self, request: web.Request) -> web.Response:
        events = sorted(self.store.events, key=lambda e: e["ts"], reverse=True)
        return web.json_response({"events": events, "unread": self.store.unread_events()})

    async def api_events_read(self, request: web.Request) -> web.Response:
        await self.store.mark_all_read()
        return web.json_response({"ok": True})

    async def api_stats(self, request: web.Request) -> web.Response:
        return web.json_response(self.store.stats())

    async def api_referral(self, request: web.Request) -> web.Response:
        link = "https://t.me/SteamSaleBot?start=refDEMO123"  # заменится при деплое
        return web.json_response({
            "link": link,
            "reward_days": REFERRAL_REWARD_DAYS,
            "note": "В демо ссылка не активирует награду; в боте — только за первую добавленную игру.",
        })

    # ------------------------------------------------------------ демо-админка /admin

    def _admin_response(self, status: int = 403, message: str = "Нет доступа") -> web.Response:
        return web.json_response({"ok": False, "message": message}, status=status)

    async def api_admin_login(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._admin_response(400, "Нужен JSON с полем code")
        given = (body.get("code") or "").strip()
        if not given or given != ADMIN_CODE:
            return self._admin_response(403, "Неверный код доступа")
        resp = web.json_response({"ok": True})
        resp.set_cookie(_ADMIN_COOKIE, self._admin_token(),
                        max_age=60 * 60 * 12, httponly=True, samesite="lax", path="/")
        return resp

    async def api_admin_logout(self, request: web.Request) -> web.Response:
        resp = web.json_response({"ok": True})
        resp.del_cookie(_ADMIN_COOKIE, path="/")
        return resp

    def _cache_report(self) -> dict:
        now = time.time()
        mp = self._market_price_cache
        deals = _DEALS_CACHE
        return {
            "market_price": {"age_s": round(max(0, now - mp.get("ts", 0)), 1),
                             "items": len(mp.get("prices", {}))},
            "market_meta": len(self._market_meta_cache),
            "suggest": sum(len(v[1]) for v in self._suggest_cache.values()),
            "deals": {"age_s": round(max(0, now - deals.get("ts", 0)), 1),
                      "items": len(deals.get("items", [])), "stale": deals.get("stale", False),
                      "total": deals.get("total"), "total_all": deals.get("total_all"),
                      "hidden": deals.get("hidden")},
            "overview": {
                g: {"age_s": round(max(0, now - v["ts"]), 1), "rows": len(v["data"].get("rows", []))}
                for (g, _cc), v in self._overview_cache.items()
            },
            "fx": {"rate": self._market_fx.get("rate"),
                   "age_s": round(max(0, now - self._market_fx.get("rate_ts", 0)), 1)},
            "catalog": {
                cc: {"total": ci.get("total"), "hidden": ci.get("hidden"), "effective": ci.get("effective")}
                for cc, ci in self._catalog_info.items()
            },
            "catalog_cooldown_until_s": round(max(0, self._catalog_cooldown_until - now), 1),
        }

    async def api_admin_state(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        now = time.time()
        worker_alive = bool(self._worker_task and not self._worker_task.done())
        last = self._last_cycle
        return web.json_response({
            "ok": True,
            "code": ADMIN_CODE,
            "server": {"uptime_s": round(now - self._started_at, 1), "port": PORT,
                       "started_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self._started_at))},
            "worker": {"alive": worker_alive, "locked": self.worker_lock.locked(),
                       "cycle_age_s": round(now - last["ts"], 1) if last else None,
                       "last_cycle": last},
            "steam": self._steam_status,
            "store": {
                "premium": self.store.is_premium(),
                "premium_days_left": self.store.premium_days_left(),
                "trial_available": not self.store.user.get("trial_used_at") and not self.store.is_premium(),
                "region": self.store.settings.get("region"),
                "user_name": self.store.user.get("name"),
                "games": len(self.store.tracked()),
                "alerts": len(self.store.market_alerts()),
                "events_total": len(self.store.events),
                "events_unread": self.store.unread_events(),
            },
            "caches": self._cache_report(),
            "logs_tail": self._log_lines[-80:],
        })

    async def api_admin_premium(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        try:
            body = await request.json()
            days = int(body.get("days") or 0)
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "message": "Нужно число дней"}, status=400)
        if days <= 0:
            return web.json_response({"ok": False, "message": "Дней должно быть > 0"}, status=400)
        await self.store.grant_premium_days(days)
        return web.json_response({"ok": True,
                                  "premium_days_left": self.store.premium_days_left(),
                                  "premium_until": self.store.user.get("premium_until")})

    async def api_admin_revoke(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        await self.store.revoke_premium()
        return web.json_response({"ok": True, "premium": self.store.is_premium()})

    async def api_admin_announce(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "message": "Нужен JSON"}, status=400)
        text = (body.get("text") or "").strip()
        if not text:
            return web.json_response({"ok": False, "message": "Пустое сообщение"}, status=400)
        self.store.push_event("admin", "📢 Объявление от админа", text[:600], None)
        return web.json_response({"ok": True, "text": text[:600]})

    async def api_admin_reset(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        await self.store.reset_demo()
        # чистим и память сервера, чтобы «сброс состояния» был честным
        self._market_price_cache = {"ts": 0.0, "prices": {}}
        self._market_meta_cache.clear()
        self._suggest_cache.clear()
        self._overview_cache.clear()
        self._market_fx = {"rate": None, "rate_ts": 0.0}
        self._overview_fail_until = 0.0
        self._overview_fail_count = 0
        self._overview_fallback_cache.clear()
        _DEALS_CACHE.update({"ts": 0, "items": [], "total": None, "total_all": None, "hidden": None, "stale": False})
        self._catalog_info.clear()
        self._deal_pages.clear()
        self._catalog_cooldown_until = 0.0
        self._catalog_last_fetch = 0.0
        self._market_last_live.clear()
        self._last_cycle = None
        return web.json_response({"ok": True, "message": "Состояние демо сброшено"})

    async def api_admin_cycle(self, request: web.Request) -> web.Response:
        if not self._admin_ok(request):
            return self._admin_response()
        summary = await self.full_cycle()
        return web.json_response({"ok": True, "cycle": summary, "locked": summary.get("locked")})

    # ------------------------------------------------------------ app

    def build_app(self) -> web.Application:
        app = web.Application()

        @web.middleware
        async def no_cache(request: web.Request, handler) -> web.StreamResponse:
            resp = await handler(request)
            if request.path.startswith("/api/"):
                resp.headers.setdefault("Cache-Control", "no-store")
            else:
                # статика меняется во время разработки — всегда свежие файлы
                resp.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
            return resp

        app.middlewares.append(no_cache)
        app.on_startup.append(lambda a: self.startup())
        app.on_cleanup.append(lambda a: self.cleanup())

        app.router.add_get("/api/session", self.api_session)
        app.router.add_post("/api/session/set", self.api_session_set)

        app.router.add_get("/api/games", self.api_games)
        app.router.add_post("/api/games/add", self.api_game_add)
        app.router.add_post("/api/games/remove", self.api_game_remove)
        app.router.add_post("/api/games/refresh", self.api_games_refresh)
        app.router.add_get("/api/games/{appid:\\d+}", self.api_game_detail)
        app.router.add_post("/api/games/{appid:\\d+}/target", self.api_game_target)

        app.router.add_get("/api/search", self.api_search)
        app.router.add_get("/api/deals", self.api_deals)
        app.router.add_get("/api/deals/global", self.api_deals_global)
        app.router.add_get("/api/wallet", self.api_wallet)
        app.router.add_post("/api/wishlist/import", self.api_wishlist_import)

        app.router.add_get("/api/market/alerts", self.api_market_alerts)
        app.router.add_post("/api/market/alerts", self.api_market_add)
        app.router.add_delete("/api/market/alerts/{alert_id}", self.api_market_delete)
        app.router.add_get("/api/market/lookup", self.api_market_lookup)
        app.router.add_get("/api/market/suggest", self.api_market_suggest)
        app.router.add_get("/api/market/curated", self.api_market_curated)
        app.router.add_get("/api/market/overview", self.api_market_overview)

        app.router.add_get("/api/events", self.api_events)
        app.router.add_post("/api/events/read", self.api_events_read)
        app.router.add_get("/api/stats", self.api_stats)
        app.router.add_get("/api/referral", self.api_referral)

        app.router.add_post("/api/admin/login", self.api_admin_login)
        app.router.add_post("/api/admin/logout", self.api_admin_logout)
        app.router.add_get("/api/admin/state", self.api_admin_state)
        app.router.add_post("/api/admin/premium", self.api_admin_premium)
        app.router.add_post("/api/admin/revoke", self.api_admin_revoke)
        app.router.add_post("/api/admin/announce", self.api_admin_announce)
        app.router.add_post("/api/admin/reset", self.api_admin_reset)
        app.router.add_post("/api/admin/cycle", self.api_admin_cycle)

        # статика мини-аппки
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        index_path = os.path.join(static_dir, "index.html")

        async def index(request: web.Request) -> web.FileResponse:
            return web.FileResponse(index_path)

        async def admin_page(request: web.Request) -> web.FileResponse:
            return web.FileResponse(os.path.join(static_dir, "admin.html"))

        app.router.add_get("/", index)
        app.router.add_get("/admin", admin_page)
        app.router.add_static("/", static_dir, show_index=False)
        return app


# Курируемый список предметов Market для быстрого добавления (реальные hash_name).
CURATED_ITEMS = [
    {"appid": 730, "name": "AK-47 | Redline (FT)", "hash": "AK-47 | Redline (Field-Tested)"},
    {"appid": 730, "name": "AWP | Asiimov (FT)", "hash": "AWP | Asiimov (Field-Tested)"},
    {"appid": 730, "name": "M4A1-S | Hyper Beast (MW)", "hash": "M4A1-S | Hyper Beast (Minimal Wear)"},
    {"appid": 730, "name": "Desert Eagle | Blaze (FN)", "hash": "Desert Eagle | Blaze (Factory New)"},
    {"appid": 730, "name": "Butterfly Knife | Doppler (FN)", "hash": "Butterfly Knife | Doppler (Factory New)"},
    {"appid": 730, "name": "Glove Case", "hash": "Glove Case"},
    {"appid": 730, "name": "Fracture Case", "hash": "Fracture Case"},
    {"appid": 730, "name": "CS:GO Weapon Case", "hash": "CS:GO Weapon Case"},
    {"appid": 730, "name": "Prisma 2 Case", "hash": "Prisma 2 Case"},
    {"appid": 252490, "name": "Rust: Forest Raider", "hash": "Forest Raider"},
    {"appid": 252490, "name": "Rust: Desert Eagle", "hash": "Desert Eagle"},
    {"appid": 753, "name": "Steam Summer Sale Card", "hash": "Steam Summer Sale 2024 Card"},
]


def _now_iso() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    api = ApiServer()
    logger.info("Mini App: http://%s:%d (демо, все данные Steam живые)", HOST, PORT)
    web.run_app(api.build_app(), host=HOST, port=PORT, print=None)
