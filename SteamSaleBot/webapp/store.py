"""Демо-хранилище мини-аппки: состояние, логика уведомлений «как в воркере бота»,
история цен и статистика. В продакшене это заменяется на repository.py (Postgres) —
все публичные методы спроектированы один-в-один под будущие SQL-запросы бота.

Состояние сохраняется в webapp/data/demo_state.json (чистое демо: в реальном боте
данные живут в Postgres и в памяти перезапусков не боятся).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import math
import os
import random
import time
import uuid

logger = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "demo_state.json")

FREE_GAME_LIMIT = 15
TRIAL_DAYS = 3
REFERRAL_REWARD_DAYS = 7
PREMIUM_DAYS = 30

# Порог «похоже на сезонную распродажу» (как SEASONAL_ALERT_THRESHOLD в боте)
SEASONAL_THRESHOLD = 0.3


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _ago_iso(**kw) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).isoformat(timespec="seconds")


class DemoStore:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        # цены/названия игр (кэш опроса): appid -> info
        self.prices: dict[int, dict] = {}
        # демо-история цен для графиков
        self.history: dict[int, list[dict]] = {}
        # события «уведомлений», как их слал бы воркер бота
        self.events: list[dict] = []
        self._seen_event_ids: set[str] = set()

    # ------------------------------------------------------------------ загрузка

    @staticmethod
    def _clean_low_spam(events: list[dict]) -> list[dict]:
        """Из серии одинаковых «исторических минимумов» оставляет самый первый
        (раньше баг слал один и тот же low каждый цикл — чистим разово при загрузке)."""
        keep: dict[tuple, tuple[float, str]] = {}  # (kind,title,body) -> (ts, id) самого раннего
        for e in events:
            if e.get("kind") != "low":
                continue
            key = (e.get("kind"), e.get("title"), e.get("body"))
            ts = _parse_ts(e.get("ts"))
            prev = keep.get(key)
            if prev is None or ts < prev[0]:
                keep[key] = (ts, e["id"])
        low_ids = {v[1] for v in keep.values()}
        return [e for e in events if e.get("kind") != "low" or e["id"] in low_ids]

    def _default_state(self) -> dict:
        return {
            "version": 2,
            "user": {
                "name": "Демо-пользователь",
                "premium_until": _ago_iso(days=-365),  # премиум активен (демо)
                "trial_used_at": None,
            },
            "settings": {"region": "ru", "threshold": 0, "digest_enabled": True},
            # кэш последних успешных цен предметов Market: hash_name -> {..price, ts}
            # (при 429 от Steam Market показываем последнюю известную цену)
            "market_prices_cache": {},
            # история цен предметов Market: hash_name -> [{"t","f","demo"?}] (для мини-графика)
            "market_history": {},
            "games": [
                {"appid": 367520, "added_at": _ago_iso(days=62)},
                {"appid": 620, "added_at": _ago_iso(days=48)},
                {"appid": 413150, "added_at": _ago_iso(days=30)},
                {"appid": 646570, "added_at": _ago_iso(days=12)},
                {"appid": 1245620, "added_at": _ago_iso(days=9)},
                {"appid": 504230, "added_at": _ago_iso(days=4)},
                {"appid": 105600, "added_at": _ago_iso(days=1)},
            ],
            "market_alerts": [],
        }

    async def load(self) -> None:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("version") == 2:
                    self.state = data
                    raw_events = data.get("events", [])
                    cleaned = self._clean_low_spam(raw_events)
                    self.events = cleaned
                    self.history = {int(k): v for k, v in data.get("history", {}).items()}
                    self._seen_event_ids = {e["id"] for e in self.events}
                    if len(cleaned) != len(raw_events):
                        # разово чистим накопившийся спам «исторических минимумов» из прошлой версии
                        await self._save()
                    return
            except Exception:
                logger.exception("Не удалось прочитать demo_state.json")
        self.state = self._default_state()
        self.events = []
        await self._save()

    async def _save(self) -> None:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        self.state["events"] = self.events[-400:]  # храним последние 400 событий
        self.state["history"] = {str(k): v for k, v in self.history.items()}
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)

    async def _mutate(self, fn):
        """Выполняет мутацию под lock'ом с сохранением. fn может быть синхронной
        или корутиной (внутренние методы store пишут и так и так — страхуемся)."""
        async with self.lock:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            await self._save()
            return result

    # ------------------------------------------------------------ пользователь

    @property
    def user(self) -> dict:
        return self.state["user"]

    @property
    def settings(self) -> dict:
        return self.state["settings"]

    def is_premium(self) -> bool:
        until = self.user.get("premium_until")
        if not until:
            return False
        return _parse_dt(until) > dt.datetime.now(dt.timezone.utc)

    def premium_days_left(self) -> int:
        until = _parse_dt(self.user.get("premium_until"))
        if not until:
            return 0
        delta = until - dt.datetime.now(dt.timezone.utc)
        return max(0, int(delta.total_seconds() // 86400))

    def session_view(self) -> dict:
        return {
            "user": {
                "name": self.user.get("name", "Пользователь"),
                "is_demo": True,
                "premium": self.is_premium(),
                "premium_until": self.user.get("premium_until"),
                "premium_days_left": self.premium_days_left(),
                "trial_available": not self.user.get("trial_used_at") and not self.is_premium(),
            },
            "settings": dict(self.settings),
            "limits": {"free_game_limit": FREE_GAME_LIMIT},
            "region_label": self.region_label(),
        }

    def region_label(self) -> str:
        labels = {"ru": "Россия · ₽", "us": "США · $", "de": "Европа · €"}
        return labels.get(self.settings["region"], self.settings["region"].upper())

    def games_count(self) -> int:
        return len(self.state["games"])

    # ------------------------------------------------------------ настройки

    async def set_region(self, region: str) -> None:
        async with self.lock:
            self.settings["region"] = region
            self.history.clear()  # история цен привязана к валюте региона
            self.state.setdefault("market_prices_cache", {}).clear()
            self.state.setdefault("market_history", {}).clear()
            await self._save()

    async def set_threshold(self, percent: int) -> None:
        async with self.lock:
            self.settings["threshold"] = max(0, min(95, int(percent)))
            await self._save()

    async def set_digest(self, enabled: bool) -> None:
        async with self.lock:
            self.settings["digest_enabled"] = bool(enabled)
            await self._save()

    async def buy_premium(self) -> None:
        async with self.lock:
            self.user["premium_until"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=PREMIUM_DAYS)).isoformat()
            self._push_event_locked("premium", "💎 Premium активирован", f"Оплата Stars (демо) · {PREMIUM_DAYS} дн.")
            await self._save()

    async def grant_premium_days(self, days: int) -> None:
        """Админ-выдача: продлевает Premium с сегодня (как /admin_premium в боте)."""
        async with self.lock:
            days = max(1, min(3650, int(days)))
            base = max(dt.datetime.now(dt.timezone.utc),
                       _parse_dt(self.user.get("premium_until")) or dt.datetime.now(dt.timezone.utc))
            self.user["premium_until"] = (base + dt.timedelta(days=days)).isoformat()
            self._push_event_locked("premium", "💎 Premium выдан админом", f"{days} дн. (админ-панель)")
            await self._save()

    async def use_trial(self) -> bool:
        async with self.lock:
            if self.user.get("trial_used_at") or self.is_premium():
                return False
            self.user["trial_used_at"] = _now()
            self.user["premium_until"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=TRIAL_DAYS)).isoformat()
            self._push_event_locked("premium", "🎁 Пробный Premium", f"Бесплатно на {TRIAL_DAYS} дн. — один раз на чат")
            await self._save()
            return True

    async def revoke_premium(self) -> None:
        async with self.lock:
            self.user["premium_until"] = _now()
            await self._save()

    async def reset_demo(self) -> None:
        """Админ: полный сброс демо-состояния к чистому (игры/алерты/события)."""
        async with self.lock:
            self.state = self._default_state()
            self.events = []
            self.history = {}
            self._seen_event_ids.clear()
            self.prices.clear()
            await self._save()

    # ------------------------------------------------------------ игры (трекинг)

    def tracked(self) -> list[dict]:
        return list(self.state["games"])

    def tracked_appids(self) -> list[int]:
        return [g["appid"] for g in self.state["games"]]

    def is_tracked(self, appid: int) -> bool:
        return any(g["appid"] == appid for g in self.state["games"])

    def get_tracked(self, appid: int) -> dict | None:
        return next((g for g in self.state["games"] if g["appid"] == appid), None)

    async def add_game(self, appid: int, name: str, image: str | None) -> dict:
        """Добавляет игру. Возвращает {"ok": True, "game": {...}} либо {"ok": False, "error": ...}."""
        async with self.lock:
            if self.is_tracked(appid):
                return {"ok": False, "error": "already_tracked"}
            if not self.is_premium() and len(self.state["games"]) >= FREE_GAME_LIMIT:
                return {"ok": False, "error": "limit", "message": "Лимит бесплатного тарифа исчерпан"}
            game = {"appid": appid, "added_at": _now(), "target_cents": None}
            self.state["games"].append(game)
            self._push_event_locked("add", "✅ Игра добавлена", name)
            await self._save()
            return {"ok": True, "game": game}

    async def remove_game(self, appid: int) -> bool:
        async with self.lock:
            before = len(self.state["games"])
            self.state["games"] = [g for g in self.state["games"] if g["appid"] != appid]
            if len(self.state["games"]) != before:
                self._push_event_locked("remove", "🗑 Игра удалена", f"appid {appid}")
                await self._save()
                return True
            return False

    async def set_target(self, appid: int, cents: int | None) -> None:
        async with self.lock:
            game = self.get_tracked(appid)
            if game:
                game["target_cents"] = cents
                await self._save()

    async def set_game_field(self, appid: int, field: str, value) -> None:
        """Произвольное поле на записи игры (напр. last_discount) с сохранением."""
        async with self.lock:
            game = self.get_tracked(appid)
            if game:
                game[field] = value
                await self._save()

    # --------------------------------------------- кэш цен предметов Market

    def market_cached_price(self, market_hash_name: str) -> dict | None:
        return self.state.get("market_prices_cache", {}).get(market_hash_name)

    async def set_market_cached_price(self, market_hash_name: str, price: dict) -> None:
        """Сохраняет последнюю успешно полученную цену предмета (переживает 429 и рестарты)."""
        async with self.lock:
            self.state.setdefault("market_prices_cache", {})[market_hash_name] = {
                "lowest_cents": price.get("lowest_cents"),
                "median_cents": price.get("median_cents"),
                "volume": price.get("volume"),
                "ts": _now(),
            }
            await self._save()

    # --------------------------------------------- история цен предметов Market

    def market_history_points(self, market_hash_name: str) -> list[dict]:
        """Точки истории предмета: [{"t": iso, "f": cents, "demo"?: true}]."""
        return list(self.state.get("market_history", {}).get(market_hash_name, []))

    def market_history_has_demo(self, market_hash_name: str) -> bool:
        return any(p.get("demo") for p in self.market_history_points(market_hash_name))

    async def note_market_price(self, market_hash_name: str, cents: int, ts: str | None = None,
                                appid: int | None = None) -> None:
        """Записывает реальное наблюдение цены; если истории ещё нет — сеет короткую
        демо-реконструкцию около текущей цены (чтобы график сразу был наглядным),
        как и для игр: первые точки помечены demo, дальше копятся реальные."""
        ts = ts or _now()
        now_ts = _parse_dt(ts) or dt.datetime.now(dt.timezone.utc)

        def _build() -> None:
            hist = self.state.setdefault("market_history", {})
            pts = hist.setdefault(market_hash_name, [])
            if not pts:
                seeded = _seed_market_series(market_hash_name, int(cents), now_ts)
                pts.extend(seeded)
            last = pts[-1] if pts else None
            last_ts = _parse_dt(last.get("t")) if last else None
            # не плодим дубли в быстрой серии запросов
            if last_ts and (now_ts - last_ts).total_seconds() < 1800:
                if last.get("f") == int(cents):
                    return
            pts.append({"t": ts, "f": int(cents)})
            # лимит точек на предмет
            if len(pts) > 250:
                del pts[: len(pts) - 250]
            # лимит предметов в словаре (эвакуация самых старых)
            if len(hist) > 140:
                oldest_key = min(hist, key=lambda k: _parse_dt(hist[k][0]["t"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
                del hist[oldest_key]

        await self._mutate(_build)

    # ------------------------------------------------------------ события

    def _push_event_locked(self, kind: str, title: str, body: str = "", price: dict | None = None) -> None:
        eid = uuid.uuid4().hex[:12]
        if eid in self._seen_event_ids:
            return
        self._seen_event_ids.add(eid)
        self.events.append(
            {"id": eid, "kind": kind, "title": title, "body": body, "price": price,
             "ts": _now(), "read": False}
        )

    def push_event(self, kind: str, title: str, body: str = "", price: dict | None = None) -> None:
        self._push_event_locked(kind, title, body, price)

    def unread_events(self) -> int:
        return sum(1 for e in self.events if not e.get("read"))

    async def mark_all_read(self) -> None:
        async with self.lock:
            for e in self.events:
                e["read"] = True
            await self._save()

    # ------------------------------------------------------------ статистика

    def stats(self) -> dict:
        now_ts = time.time()
        sale_events = [e for e in self.events if e.get("kind") in ("sale", "target")]
        total_savings_cents = 0
        for e in sale_events:
            p = e.get("price") or {}
            orig = p.get("price_original_cents")
            final = p.get("price_final_cents")
            if orig and final and orig > final:
                total_savings_cents += orig - final

        week_ago = now_ts - 7 * 86400
        week_events = [e for e in self.events if _parse_ts(e["ts"]) >= week_ago]

        market_hit = sum(1 for e in self.events if e.get("kind") == "market")
        lows = sum(1 for e in self.events if e.get("kind") == "low")
        return {
            "tracked_games": len(self.state["games"]),
            "market_alerts": len(self.state["market_alerts"]),
            "events_total": len(self.events),
            "events_week": len(week_events),
            "savings_cents": total_savings_cents,
            "best_discount": max((int((e.get("price") or {}).get("discount_percent") or 0)
                                  for e in self.events if e.get("price")), default=0),
            "market_triggered": market_hit,
            "all_time_lows": lows,
        }

    # ------------------------------------------------------------ история цен

    def history_points(self, appid: int) -> list[dict]:
        pts = self.history.get(appid, [])
        return sorted(pts, key=lambda p: p["t"])

    async def add_history_point(self, appid: int, final_cents: int, discount: int, currency: str, ts: str,
                                original_cents: int | None = None) -> None:
        async with self.lock:
            pts = self.history.setdefault(appid, [])
            # пишем точку только если цена/скидка изменились с прошлой записи
            if pts and pts[-1]["f"] == final_cents and pts[-1]["d"] == discount:
                return
            pts.append({"t": ts, "f": final_cents, "d": discount, "c": currency})
            pts[:] = pts[-200:]
            await self._save()

    async def seed_demo_history(self, appid: int, original_cents: int, final_cents: int, currency: str) -> None:
        """Для демо подкладываем правдоподобную историю на основе типовых сезонных распродаж
        Steam, чтобы графики выглядели «по-настоящему» сразу. Метка-дисклеймер в UI.
        В реальном боте история берётся из sale_snapshots.
        Фиксируется один раз на игру; текущее наблюдение добавляется отдельно
        (add_history_point), чтобы не было двух одинаковых точек «сейчас»."""
        if len(self.history.get(appid, [])) >= 3:
            return
        async with self.lock:
            if len(self.history.get(appid, [])) >= 3:
                return
            base = original_cents or final_cents
            now = dt.datetime.now(dt.timezone.utc)
            pts = []
            # ~14 недель типовой динамики: полная цена + сезонные просадки
            curve = [
                (-98, 0.90), (-84, 0.75), (-70, 0.35), (-60, 0.9), (-49, 0.5),
                (-42, 0.25), (-30, 0.9), (-21, 0.5), (-14, 0.35), (-7, 0.9),
                (-5, 0.6), (-3, 0.5),
            ]
            for days_ago, level in curve:
                price = int(base * level)
                disc = int(round((1 - level) * 100))
                pts.append({"t": (now - dt.timedelta(days=-days_ago)).isoformat(timespec="seconds"),
                            "f": price, "d": max(0, disc), "c": currency})
            pts.sort(key=lambda p: p["t"])
            self.history[appid] = pts
            await self._save()

    # ------------------------------------------------------------ market-алерты

    def market_alerts(self) -> list[dict]:
        return list(self.state["market_alerts"])

    async def add_market_alert(self, appid: int, market_hash_name: str, direction: str,
                               threshold_cents: int, item_icon: str | None = None,
                               item_type: str | None = None,
                               mode: str = "price", pct: float | None = None,
                               base_cents: int | None = None) -> dict:
        """mode="price": цель — фиксированная цена threshold_cents.
        mode="percent": трейдерская цель «ещё на X% от base_cents» — порог считается
        на каждый чек динамически (ниже: base·(1−pct/100), выше: base·(1+pct/100)),
        а threshold_cents хранит текущий эквивалент для показа в UI."""
        dup_key = (market_hash_name, direction, mode, None if pct is None else round(float(pct), 3))
        async with self.lock:
            for a in self.state["market_alerts"]:
                if (a["market_hash_name"], a["direction"], a.get("mode", "price"),
                        (None if a.get("pct") is None else round(float(a["pct"]), 3))) == dup_key:
                    return {"ok": False, "error": "exists", "message": "Такой алерт уже есть"}
            base = int(base_cents) if base_cents else int(threshold_cents)
            alert = {
                "id": uuid.uuid4().hex[:8],
                "appid": appid,
                "market_hash_name": market_hash_name,
                "direction": direction,
                "mode": mode,
                "threshold_cents": int(threshold_cents),
                "base_cents": base,
                "pct": round(float(pct), 3) if pct is not None else None,
                "added_at": _now(),
                "item_icon": item_icon,
                "item_type": item_type,
            }
            self.state["market_alerts"].append(alert)
            label = self._alert_goal_locked(alert)
            self._push_event_locked(
                "market_add", "📌 Алерт создан",
                f"{market_hash_name} · сработает, когда станет {label}",
                None,
            )
            await self._save()
            return {"ok": True, "alert": alert}

    @staticmethod
    def _alert_goal_locked(alert: dict) -> str:
        """Человекочитаемая цель алерта: «ниже 300 ₽» или «ниже на 10% (≈ 300 ₽)»."""
        direction = alert["direction"]
        word = "выше" if direction == "above" else "ниже"
        cents = int(alert.get("threshold_cents") or 0) / 100
        if alert.get("mode") == "percent" and alert.get("pct") is not None:
            base = int(alert.get("base_cents") or 0) / 100
            pct = float(alert["pct"])
            if base:
                return f"{word} на {pct:g}% от {base:g} (≈ {cents:g})"
            return f"{word} на {pct:g}%"
        return f"{word} {cents:g}"

    async def set_market_alert_meta(self, alert_id: str, icon: str | None, item_type: str | None) -> None:
        """Лениво заполняет иконку/тип у старых алертов (созданных до появления витрины)."""
        async with self.lock:
            for a in self.state["market_alerts"]:
                if a["id"] == alert_id:
                    if icon:
                        a["item_icon"] = icon
                    if item_type:
                        a["item_type"] = item_type
                    await self._save()
                    return

    async def remove_market_alert(self, alert_id: str) -> bool:
        async with self.lock:
            before = len(self.state["market_alerts"])
            self.state["market_alerts"] = [a for a in self.state["market_alerts"] if a["id"] != alert_id]
            if len(self.state["market_alerts"]) != before:
                await self._save()
                return True
            return False

    # ------------------------------------------------------------ воркер-проверки

    async def apply_market_prices(self, prices: dict[str, dict]) -> list[str]:
        """prices: hash_name -> {"lowest_cents", ...}. Сработавшие алерты удаляются (как в боте —
        алерт одноразовый, «лимитная заявка»), событие уходит в ленту. Возвращает ids сработавших."""
        triggered: list[tuple[dict, int, int]] = []

        async def _apply() -> list[str]:
            remaining = []
            for alert in self.state["market_alerts"]:
                p = prices.get(alert["market_hash_name"])
                if not p or p.get("lowest_cents") is None:
                    remaining.append(alert)
                    continue
                lowest = p["lowest_cents"]
                # трейдерский %-алерт: порог живой, считается от base_cents на каждый чек
                thr = int(alert.get("threshold_cents") or 0)
                if alert.get("mode") == "percent" and alert.get("pct") is not None:
                    base = int(alert.get("base_cents") or 0)
                    pct = float(alert["pct"])
                    if alert["direction"] == "below":
                        thr = int(base * (1 - pct / 100))
                    else:
                        thr = int(base * (1 + pct / 100))
                hit = lowest <= thr if alert["direction"] == "below" else lowest >= thr
                if hit:
                    triggered.append((alert, lowest, thr))
                else:
                    remaining.append(alert)
            self.state["market_alerts"] = remaining
            return [a["id"] for a, _, _ in triggered]

        ids = await self._mutate(_apply)
        for alert, lowest, thr in triggered:
            if alert.get("mode") == "percent" and alert.get("pct") is not None:
                pct = float(alert["pct"])
                arrow = f"↘ на {pct:g}% ниже" if alert["direction"] == "below" else f"↗ на {pct:g}% выше"
                text = (f"Цена {alert['direction'] == 'below' and 'упала' or 'выросла'} до {lowest / 100:g} "
                        f"— цель {arrow} от {int(alert.get('base_cents') or 0) / 100:g} достигнута. Алерт снят.")
            else:
                arrow = "↘ ниже" if alert["direction"] == "below" else "↗ выше"
                text = f"Цена {arrow} {lowest / 100:g} — цель {thr / 100:g} достигнута. Алерт снят."
            self.push_event(
                "market",
                f"📈 Market: {alert['market_hash_name']}",
                text,
                {"price_final_cents": lowest, "price_original_cents": thr,
                 "currency": "local", "discount_percent": 0},
            )
        return ids

    async def digest_event_if_needed(self, sales: list[dict], region: str) -> bool:
        """Эмуляция дайджеста/сезонного баннера: событие-сводка не чаще раза в 6 часов."""
        last_digest = max((e["ts"] for e in self.events if e.get("kind") == "digest"), default=None)
        if last_digest and (time.time() - _parse_ts(last_digest)) < 6 * 3600:
            return False

        top = sorted(sales, key=lambda s: s["discount_percent"], reverse=True)[:3]
        if not top:
            return False

        parts = []
        total = 0
        for s in top:
            total += (s["price_original_cents"] - s["price_final_cents"]) if s["price_original_cents"] > s["price_final_cents"] else 0
            parts.append(f"{s['name']} −{s['discount_percent']}%")
        self.push_event(
            "digest",
            "📰 Дайджест скидок",
            "Сейчас самое горячее: " + ", ".join(parts) + f". Потенциальная экономия {total / 100:g}.",
            None,
        )
        return True

    async def seasonal_banner_if_due(self, tracked_sale_count: int, tracked_total: int) -> bool:
        """Как в боте: если скидка одновременно у доли отслеживаемых игр >= SEASONAL_THRESHOLD —
        «похоже на сезонную распродажу», но не чаще раза в неделю."""
        if tracked_total == 0 or tracked_sale_count / tracked_total < SEASONAL_THRESHOLD:
            return False
        last = max((e["ts"] for e in self.events if e.get("kind") == "season"), default=None)
        if last and (time.time() - _parse_ts(last)) < 7 * 86400:
            return False
        self.push_event("season", "🔥 Похоже, началась крупная распродажа",
                        "Скидка одновременно у заметной доли вашего списка — Steam намекает на сезон. Проверьте /deals!", None)
        return True


# ------------------------------------------------------------------ helpers

def _seed_market_series(market_hash_name: str, cents: int, now: dt.datetime) -> list[dict]:
    """Детерминированная «реконструкция» цены предмета для демо-графика:
    ~5 недель с шагом ~23 ч и якорем ~24 ч назад (для «тренда за день»),
    приходит к текущей цене. Точки помечены demo — UI честно пишет,
    что это реконструкция по типовой динамике, а не реальная история Steam
    (реальную history Steam отдаёт только под логином)."""
    if cents <= 0:
        cents = 100
    digest = hashlib.md5(market_hash_name.encode("utf-8")).digest()
    rnd = random.Random(digest[0] | (digest[1] << 8))
    phase = rnd.uniform(0, 6.28)
    n = 36
    pts: list[dict] = []
    for i in range(n - 1, -1, -1):
        if i == 0:
            t = now - dt.timedelta(hours=1, minutes=rnd.randint(30, 90))
        else:
            # шаг ~23 ч + лёгкий джиттер: точка «сутки назад» почти всегда есть
            t = now - dt.timedelta(hours=i * 23 + 1, minutes=rnd.randint(30, 130))
        # сезонная волна + шум, сужающийся к «сейчас»
        k = (n - 1 - i) / (n - 1)          # 0..1 (ближе к концу = к текущей цене)
        wobble = 0.085 * math.sin(i / 5.0 + phase) + 0.045 * math.sin(i / 2.1 + phase * 1.7)
        noise = (rnd.uniform(-1, 1) * 0.02) * (1.15 - k)
        f = int(round(cents * (1 + wobble + noise)))
        pts.append({"t": t.isoformat(timespec="seconds"), "f": max(1, f), "demo": True})
    return pts


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_ts(value: str) -> float:
    try:
        return dt.datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0.0
