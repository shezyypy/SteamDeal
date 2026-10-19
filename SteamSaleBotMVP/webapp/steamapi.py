"""Лёгкий клиент публичного Steam API (без STEAM_API_KEY).

Используется демо-мини-аппкой. В продакшене эти же данные бот берёт через
app/services/* — см. webapp/README.md. Методы максимум по 1 запросу, а не пачкой,
как и в боте (appdetails официально «один appid за запрос»).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import urllib.parse

import aiohttp

logger = logging.getLogger(__name__)

_APPDETAILS = "https://store.steampowered.com/api/appdetails"
_STORESEARCH = "https://store.steampowered.com/api/storesearch/"
_FEATURED = "https://store.steampowered.com/api/featuredcategories"
_MARKET = "https://steamcommunity.com/market/priceoverview/"
_WISHLIST = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# market currency -> (country cc)
MARKET_CURRENCY = {"ru": 5, "us": 1, "de": 3}
REGION_LABELS = {"ru": "Россия · ₽", "us": "США · $", "de": "Европа · €"}

# Допустимые регионы (cc для store API + валюта Market)
REGIONS = {"ru": "ru", "us": "us", "de": "de"}

# Задержка между последовательными запросами к store API (демо-щажение; в боте — RateLimiter).
REQUEST_PAUSE = 0.5


def _int_from_text(value: str | None) -> int | None:
    """'15,543' / '9 519' / '1 297' -> int (выкидываем все разделители)."""
    if not value:
        return None
    try:
        return int(re.sub(r"\D", "", value))
    except ValueError:
        return None


def _price_text_to_cents(value: str | None) -> int | None:
    """'1200 руб' / '4,99 €' / '$9.99' -> центы."""
    if not value:
        return None
    m = re.search(r"\d[\d.,]*", value)
    if not m:
        return None
    s = m.group(0)
    try:
        if "," in s:
            if s.rfind(",") < s.rfind("."):
                return int(round(float(s.replace(",", "")) * 100))
            whole, _, frac = s.partition(",")
            return int(round(float(f"{whole.replace('.', '') or '0'}.{frac}") * 100))
        if s.count(".") > 1:
            return int(round(float(s.replace(".", "")) * 100))
        return int(round(float(s) * 100))
    except ValueError:
        return None


def _parse_results_info(html: str) -> dict:
    """Числа со страницы поиска Steam: сколько нашлось и сколько скрыто настройками."""
    info: dict = {"total": None, "hidden": None}
    m = re.search(r"Результатов по вашему запросу:\s*([\d.,\s\u00a0]+)", html)
    if m:
        info["total"] = _int_from_text(m.group(1))
    m = re.search(r"Некоторые продукты \((\d[\d.,\u00a0]*)\) скрыты", html)
    if m:
        info["hidden"] = _int_from_text(m.group(1))
    return info


def _parse_deal_rows(html: str) -> list[dict]:
    """Распродажи из search_result_row: appid, название, скидка, цены, картинка."""
    items: list[dict] = []
    seen: set[int] = set()
    for m in re.finditer(
        r'<a[^>]*data-ds-appid="(\d+)"[^>]*class="[^"]*search_result_row[^>]*>(.*?)</a>',
        html, re.S,
    ):
        try:
            appid = int(m.group(1))
        except ValueError:
            continue
        row = m.group(2)
        if appid in seen:
            continue
        name_m = re.search(r'class="title">([^<]{1,120})<', row)
        name = (name_m.group(1).strip() if name_m else f"Игра {appid}")[:256]
        disc_m = re.search(r'data-discount="(\d+)"', row)
        if not disc_m:
            continue
        discount = int(disc_m.group(1))
        if discount <= 0:
            continue
        final_m = re.search(r'data-price-final="(\d+)"', row)
        final_cents = int(final_m.group(1)) if final_m else 0
        orig_m = re.search(r'discount_original_price[^>]*>([^<]+)<', row)
        orig_cents = None
        if orig_m:
            orig_cents = _price_text_to_cents(orig_m.group(1))
        if orig_cents is None:
            orig_cents = final_cents
        img_m = re.search(r'<img src="(https://[^"]+)"', row)
        image = img_m.group(1) if img_m else None
        items.append({
            "appid": appid, "name": name, "discount_percent": discount,
            "price_final_cents": final_cents, "price_original_cents": orig_cents,
            "header_image": image,
        })
        seen.add(appid)
    return items


class SteamClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=20),
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get_json(self, url: str, params: dict, pause: float = REQUEST_PAUSE) -> dict | None:
        """GET с общей паузой между запросами + один ретрай при 429 (Steam Market умеет
        жёстко лимитировать по IP — как и предупреждает README бота)."""
        assert self._session is not None
        async with self._lock:
            for attempt in range(2):
                try:
                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 429:
                            logger.warning("Steam %s -> 429 (attempt %d), пауза…", url, attempt + 1)
                            await asyncio.sleep(2.5 + attempt * 2.5)
                            continue
                        if resp.status != 200:
                            logger.warning("Steam %s -> HTTP %s (%s)", url, resp.status, params)
                            return None
                        return await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.warning("Steam запрос не удался %s: %s", url, exc)
                    return None
                finally:
                    await asyncio.sleep(pause)
            logger.warning("Steam %s: отдал 429 дважды, пропускаю", url)
            return None

    # ---- игры / цены ----------------------------------------------------------

    async def appdetails(self, appid: int, cc: str) -> dict | None:
        """Имя + обложка + цена. Возвращает None, если игра недоступна в регионе/сетевая ошибка.
        Цена отсутствует (None) для бесплатных игр и игр без price_overview."""
        payload = await self._get_json(
            _APPDETAILS,
            {"appids": str(appid), "cc": cc, "l": "russian", "filters": "basic,price_overview"},
        )
        if not payload:
            return None
        entry = payload.get(str(appid)) or {}
        if not entry.get("success"):
            # success:false — например, издатель ушёл из региона (Cyberpunk в RU).
            return None
        data = entry.get("data")
        if not isinstance(data, dict):
            return None

        info: dict = {
            "appid": appid,
            "name": (data.get("name") or f"Игра {appid}")[:256],
            "header_image": data.get("header_image"),
        }
        overview = data.get("price_overview")
        if isinstance(overview, dict) and isinstance(overview.get("final"), int):
            final = int(overview["final"])
            info["price"] = {
                "price_final_cents": final,
                "price_original_cents": int(overview.get("initial") or final),
                "discount_percent": int(overview.get("discount_percent") or 0),
                "currency": overview.get("currency", cc.upper()),
                "is_free": bool(overview.get("is_free")),
            }
        elif data.get("is_free"):
            info["price"] = {"price_final_cents": 0, "price_original_cents": 0,
                             "discount_percent": 0, "currency": cc.upper(), "is_free": True}
        else:
            info["price"] = None
        return info

    async def name_of(self, appid: int, cc: str) -> str | None:
        info = await self.appdetails(appid, cc)
        return info["name"] if info else None

    async def search(self, term: str, cc: str) -> list[dict]:
        payload = await self._get_json(_STORESEARCH, {"term": term, "cc": cc, "l": "russian"})
        items = []
        for it in (payload or {}).get("items") or []:
            try:
                items.append({"appid": int(it["id"]), "name": it.get("name") or ""})
            except (KeyError, ValueError, TypeError):
                continue
        return items[:8]

    _SEARCH_HTML = "https://store.steampowered.com/search/results/"

    async def _get_text(self, url: str, params: dict) -> str | None:
        assert self._session is not None
        async with self._lock:
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("Steam text %s -> HTTP %s", url, resp.status)
                        return None
                    return await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("Steam text запрос не удался %s: %s", url, exc)
                return None
            finally:
                await asyncio.sleep(REQUEST_PAUSE)

    async def deal_search(self, cc: str, count: int = 50) -> list[dict]:
        """Курируемая выборка распродаж (top sellers) — для «витрины»."""
        html = await self._get_text(
            self._SEARCH_HTML,
            {"specials": "1", "cc": cc, "l": "russian",
             "filter": "globaltopsellers", "count": str(count)},
        )
        if not html:
            return []
        items = _parse_deal_rows(html)
        for it in items:
            it["header_image"] = self._bump_image(it["header_image"])
            it["currency"] = cc.upper()
        return items

    async def deal_page(self, cc: str, start: int = 0, count: int = 100,
                        games_only: bool = True, extra: dict | None = None) -> dict:
        """Страница полного каталога распродаж Steam (offset = start, до 100 за раз).

        Возвращает {"items": [...], "total": int|None, "hidden": int|None}, где total —
        число, которое Steam сам пишет на странице, hidden — сколько скрыто настройками
        региона. games_only=True сужает до игр (без DLC/софта) через category1=998.
        extra — проверенные фильтры Steam: term, maxprice (только «лесенка» 150..1800),
        filter (weeklongdeals/globaltopsellers), sort_by (Price_ASC/Price_DESC).
        """
        params = {"specials": "1", "cc": cc, "l": "russian",
                  "count": str(max(1, min(int(count), 100))),
                  "start": str(max(0, int(start)))}
        if games_only:
            params["category1"] = "998"
        for key, value in (extra or {}).items():
            if value not in (None, ""):
                params[key] = str(value)
        html = await self._get_text(self._SEARCH_HTML, params)
        if not html:
            return {"items": [], "total": None, "hidden": None}
        items = _parse_deal_rows(html)
        for it in items:
            # картинки каталога не «прокачиваем» до 616px: их тысячи, хватит миниатюр 184px
            it["currency"] = cc.upper()
        info = _parse_results_info(html)
        return {"items": items, "total": info["total"], "hidden": info["hidden"]}

    @staticmethod
    def _bump_image(url: str | None) -> str | None:
        """Из миниатюры капсулы в поиске делаем крупную (616x353) — чтобы карточки сделок
        не выглядели размытыми. У Steam те же файлы лежат под разными именами размеров."""
        if not url:
            return url
        return (url.replace("capsule_184x69.jpg", "capsule_616x353.jpg")
                   .replace("capsule_231x87.jpg", "capsule_616x353.jpg")
                   .replace("capsule_sm_120.jpg", "capsule_616x353.jpg")
                   .replace("capsule_sm_120.jpg?", "capsule_616x353.jpg?"))

    @staticmethod
    def _price_text_to_cents(value: str) -> int | None:
        """'1200 руб' / '4,99 €' / '$9.99' -> центы."""
        if not value:
            return None
        m = re.search(r"\d[\d.,]*", value)
        if not m:
            return None
        s = m.group(0)
        try:
            if "," in s:
                if s.rfind(",") < s.rfind("."):
                    return int(round(float(s.replace(",", "")) * 100))
                whole, _, frac = s.partition(",")
                return int(round(float(f"{whole.replace('.', '') or '0'}.{frac}") * 100))
            if s.count(".") > 1:
                return int(round(float(s.replace(".", "")) * 100))
            return int(round(float(s) * 100))
        except ValueError:
            return None

    async def specials(self, cc: str) -> list[dict]:
        """Живой фид «сейчас в продаже» из главного магазина Steam (10-30 игр)."""
        payload = await self._get_json(_FEATURED, {"cc": cc, "l": "russian"})
        if not payload:
            return []
        items = []
        seen = set()
        for section_key in ("specials", "top_sellers", "coming_soon"):
            section = payload.get(section_key)
            for it in (section or {}).get("items") or []:
                try:
                    appid = int(it["id"])
                except (KeyError, ValueError, TypeError):
                    continue
                if appid in seen:
                    continue
                seen.add(appid)
                items.append(
                    {
                        "appid": appid,
                        "name": (it.get("name") or "")[:256],
                        "discount_percent": int(it.get("discount_percent") or 0),
                        "price_original_cents": int(it.get("original_price") or 0),
                        "price_final_cents": int(it.get("final_price") or 0),
                        "currency": it.get("currency") or cc.upper(),
                        "header_image": it.get("header_image") or it.get("large_capsule_image"),
                        "discount_expiration": it.get("discount_expiration"),
                    }
                )
        return items

    # ---- Steam Market ---------------------------------------------------------

    _MARKET_SEARCH = "https://steamcommunity.com/market/search/render/"

    async def market_item_meta(self, appid: int, query: str) -> dict | None:
        """Метаданные предмета для «витрины»: иконка (economy CDN), тип/редкость,
        каноническое название. Через поисковый JSON-эндпоинт Market (curl, см. _market_curl).
        Возвращает None, если предмет не найден или Steam отдал ошибку."""
        q = urllib.parse.quote(query or "")
        if not q:
            return None
        url = f"{self._MARKET_SEARCH}?query={q}&appid={int(appid or 730)}&norender=1&count=3"

        async with self._lock:
            for attempt in range(2):
                code, payload = await self._market_curl(url)
                if code == 429 or payload is None:
                    await asyncio.sleep(0.8)
                    continue
                break
            else:
                logger.warning("Market meta %s: дважды 429/пусто", query)
                return None

        results = (payload or {}).get("results") or []
        if not results:
            return None
        ql = (query or "").lower()
        chosen = None
        for it in results:
            name = (((it.get("asset_description") or {}).get("name")) or "").strip()
            if name.lower() == ql:
                chosen = it
                break
        if chosen is None:
            chosen = results[0]
        return self._market_item_from_result(chosen, appid, query)

    async def market_search(self, appid: int, query: str, limit: int = 7) -> list[dict]:
        """Автокомплит: результаты поиска Market с иконками (для выпадающего списка)."""
        q = urllib.parse.quote(query or "")
        if not q:
            return []
        url = f"{self._MARKET_SEARCH}?query={q}&appid={int(appid or 730)}&norender=1&count={int(limit) + 3}"
        async with self._lock:
            for attempt in range(2):
                code, payload = await self._market_curl(url)
                if code == 429 or payload is None:
                    await asyncio.sleep(0.7)
                    continue
                break
            else:
                logger.warning("Market search %s: дважды 429/пусто", query)
                return []
        results = (payload or {}).get("results") or []
        out: list[dict] = []
        seen: set[str] = set()
        for it in results:
            item = self._market_item_from_result(it, appid, query)
            if item and item["name"] not in seen:
                out.append(item)
                seen.add(item["name"])
            if len(out) >= limit:
                break
        return out

    async def market_top(self, appid: int, count: int = 10, cc: str = "ru") -> list[dict]:
        """«Обзор рынка»: топ предметов игры по числу активных лотов на продаже
        (sort_column=quantity). Возвращает живой список с ценой «купить сейчас»
        (sell_price, в центах USD — Steam отдаёт render только в USD независимо
        от currency), числом лотов и иконкой. Названия локализуются под регион,
        hash_name остаётся каноническим английским."""
        lng = {"ru": "russian", "de": "german", "us": "english", "ua": "russian"}.get(cc, "english")
        query = urllib.parse.urlencode({
            "query": "", "appid": str(int(appid or 0)), "norender": "1",
            "count": str(int(count)), "sort_column": "quantity", "sort_dir": "desc",
            "l": lng, "cc": cc,
        })
        url = f"{self._MARKET_SEARCH}?{query}"
        async with self._lock:
            for attempt in range(2):
                code, payload = await self._market_curl(url)
                if code == 429 or payload is None:
                    await asyncio.sleep(0.8)
                    continue
                break
            else:
                logger.warning("market_top(appid=%s): дважды 429/пусто", appid)
                return []
        results = (payload or {}).get("results") or []
        out: list[dict] = []
        for it in results:
            try:
                ad = it.get("asset_description") or {}
                hash_name = (it.get("hash_name") or (ad.get("market_hash_name") or "")).strip()
                name = (ad.get("name") or hash_name or "").strip()
                icon_url = ad.get("icon_url") or ad.get("icon_url_large")
                sell_price = it.get("sell_price")
                listings = it.get("sell_listings")
                if not hash_name or sell_price is None:
                    continue
                out.append({
                    "hash": hash_name[:256],
                    "name": name[:256],
                    "icon": (f"https://community.akamai.steamstatic.com/economy/image/{icon_url}"
                             if icon_url else None),
                    "usd_cents": int(sell_price),
                    "listings": int(listings or 0),
                })
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= int(count):
                break
        return out

    @staticmethod
    def _market_item_from_result(result: dict, appid: int, fallback_name: str) -> dict | None:
        try:
            ad = result.get("asset_description") or {}
            name = (ad.get("name") or "").strip()
            if not name:
                return None
            icon_url = ad.get("icon_url") or ad.get("icon_url_large")
            icon = f"https://community.akamai.steamstatic.com/economy/image/{icon_url}" if icon_url else None
            return {
                "name": name[:256],
                "type": (ad.get("type") or "").strip()[:80] or None,
                "icon": icon,
                "market_url": f"https://steamcommunity.com/market/listings/{int(appid or 730)}/" + urllib.parse.quote(name),
            }
        except Exception:  # noqa: BLE001
            return None

    async def _market_curl(self, url: str) -> tuple[int, dict | None]:
        """GET через curl-подпроцесс. Steam Community отвечает 429 на Python-TLS-отпечаток
        (проверено: urllib/aiohttp из этой среды всегда 429, curl — всегда 200).
        Это демо-сервер; в проде смотри app/services/steam_market.py + RateLimiter.

        Без --compressed: Steam стабильно отдаёт 429 на запросы с этим флагом
        (Accept-Encoding c gzip/br/zstd), хотя без него тот же URL отвечает 200."""

        def _run():
            try:
                proc = subprocess.run(
                    ["curl", "-s", "-L", "-m", "20", "-A", _UA, "-w", "\n%{http_code}", url],
                    capture_output=True, text=True, timeout=30,
                )
                parts = proc.stdout.rsplit("\n", 1)
                code = int(parts[-1].strip()) if parts and parts[-1].strip().isdigit() else 0
                try:
                    return code, json.loads(parts[0]) if code in (200, 429) else None
                except json.JSONDecodeError:
                    return code, None
            except Exception:
                return 0, None

        return await asyncio.get_running_loop().run_in_executor(None, _run)

    async def market_price(self, appid: int, market_hash_name: str, cc: str) -> dict | None:
        """Реальная цена предмета Steam Community Market (см. комментарий про curl)."""
        currency = MARKET_CURRENCY.get(cc, 5)
        query = urllib.parse.urlencode(
            {"appid": str(appid), "currency": str(currency), "market_hash_name": market_hash_name}
        )
        url = f"{_MARKET}?{query}"
        async with self._lock:
            for _ in range(2):
                code, payload = await self._market_curl(url)
                if code == 429 or payload is None:
                    await asyncio.sleep(1.5)  # Market лимитирует жёстче, чем store API
                    continue
                if not payload.get("success"):
                    return None
                break
            else:
                logger.warning("Market %s: 429/пусто дважды, пропускаю", market_hash_name)
                return None

        def _parse_cents(value: str | None) -> int | None:
            if not value:
                return None
            # Steam локализует цену: "3386,95 руб." / "1.234,56 €" / "$1,234.56".
            # Берём первое число с разделителями — и не цепляем точку из "руб."
            m = re.search(r"\d[\d.,]*", value)
            if not m:
                return None
            s = m.group(0)
            try:
                if "," in s:
                    if s.rfind(",") < s.rfind("."):
                        # US-формат: "1,234.56" — запятые-тысячи, точка-десятичная
                        f = float(s.replace(",", ""))
                    else:
                        # EU/руб.: "1.234,56" / "60,62" — запятая-десятичная
                        whole, _, frac = s.partition(",")
                        whole = (whole.replace(".", "") or "0")
                        f = float(f"{whole}.{frac}")
                elif s.count(".") > 1:      # "1.234" — точки-разделители тысяч
                    f = float(s.replace(".", ""))
                else:
                    f = float(s)
                return int(round(f * 100))
            except ValueError:
                return None

        def _parse_volume(value) -> int:
            digits = re.sub(r"\D", "", str(value or ""))
            return int(digits) if digits else 0

        return {
            "lowest_cents": _parse_cents(payload.get("lowest_price")),
            "median_cents": _parse_cents(payload.get("median_price")),
            "volume": _parse_volume(payload.get("volume")),
        }

    # ---- Вишлист Steam --------------------------------------------------------

    async def wishlist(self, steamid64: str) -> list[int] | None:
        """Публичный вишлист по числовому steamid64 (без ключа). Возвращает None при ошибке,
        [] — если пуст/приватный (Steam не различает, см. README бота)."""
        payload = await self._get_json(_WISHLIST, {"steamid": steamid64})
        if payload is None:
            return None
        # при ошибке API отдаёт {"response":{}} — трактуем как пусто
        response = payload.get("response") or {}
        items = response.get("items") or []
        appids = []
        for it in items:
            try:
                appids.append(int(it["appid"]))
            except (KeyError, ValueError, TypeError):
                continue
        return appids
