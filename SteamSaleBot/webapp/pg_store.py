"""PostgreSQL-backed Mini App store.

The Telegram bot and the Mini App use the same chat-centric repository.  A
request binds a Telegram user to a private chat (Telegram private chat_id is
the user id), then all state-changing operations are scoped to that chat.
Steam response caches remain process-local and are deliberately not treated as
user data.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import asyncio
import uuid
import time

from app.db import repository
from app.config import settings

FREE_GAME_LIMIT = settings.free_game_limit
TRIAL_DAYS = settings.trial_days


_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("miniapp_user", default=None)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


class PostgresStore:
    """User-scoped store with the same public shape as DemoStore's core API."""

    def __init__(self) -> None:
        self.prices: dict[int, dict] = {}
        self._market_cache: dict[str, dict] = {}
        self._market_history: dict[str, list[dict]] = {}

    async def load(self) -> None:
        return None

    async def bind_user(self, tg_user) -> None:
        chat_id = int(tg_user.id)
        await repository.get_or_create_user(chat_id, tg_user.username)
        await repository.get_or_create_chat(chat_id, "private", tg_user.username or tg_user.first_name)
        chat = await repository.get_chat(chat_id)
        games = await repository.list_tracked_games(chat_id)
        alerts = await repository.list_market_alerts(chat_id)
        events = await repository.list_miniapp_events(chat_id)
        history: dict[int, list[dict]] = {}
        for game in games:
            snapshots = await repository.price_history(game.appid, limit=200)
            history[game.appid] = [
                {"t": _iso(snapshot.fetched_at), "f": snapshot.price_final_cents,
                 "d": snapshot.discount_percent, "c": snapshot.currency}
                for snapshot in reversed(snapshots)
            ]
        for alert in alerts:
            snapshots = await repository.market_price_history(alert.appid, alert.market_hash_name, limit=250)
            self._market_history[alert.market_hash_name] = [
                {"t": _iso(snapshot.observed_at), "f": snapshot.price_cents, "demo": snapshot.is_demo}
                for snapshot in reversed(snapshots)
            ]
        _ctx.set({"chat_id": chat_id, "tg_user": tg_user, "chat": chat, "games": games,
                  "alerts": alerts, "events": [self._event_dict(event) for event in events],
                  "history": history})

    @staticmethod
    def _event_dict(event) -> dict:
        return {
            "id": event.event_key, "kind": event.kind, "title": event.title,
            "body": event.body, "price": event.price, "ts": _iso(event.created_at), "read": event.read,
        }

    def _state(self) -> dict:
        state = _ctx.get()
        if not state:
            raise RuntimeError("Mini App request is not bound to a Telegram user")
        return state

    @property
    def user(self) -> dict:
        chat = self._state()["chat"]
        tg_user = self._state()["tg_user"]
        return {
            "name": tg_user.username or tg_user.first_name or "Пользователь",
            "premium_until": _iso(chat.premium_until),
            "trial_used_at": _iso(chat.trial_used_at),
        }

    @property
    def settings(self) -> dict:
        chat = self._state()["chat"]
        return {
            "region": chat.price_region,
            "threshold": chat.min_discount_percent,
            "digest_enabled": chat.digest_enabled,
        }

    @property
    def events(self) -> list[dict]:
        return self._state()["events"]

    def _chat_id(self) -> int:
        return self._state()["chat_id"]

    async def _refresh(self) -> None:
        state = self._state()
        state["chat"] = await repository.get_chat(state["chat_id"])
        state["games"] = await repository.list_tracked_games(state["chat_id"])
        state["alerts"] = await repository.list_market_alerts(state["chat_id"])

    def is_premium(self) -> bool:
        until = self._state()["chat"].premium_until
        return bool(until and until > dt.datetime.now(dt.timezone.utc))

    def premium_days_left(self) -> int:
        until = self._state()["chat"].premium_until
        if not until:
            return 0
        return max(0, int((until - dt.datetime.now(dt.timezone.utc)).total_seconds() // 86400))

    def session_view(self) -> dict:
        return {
            "user": {
                "name": self.user["name"],
                "is_demo": False,
                "premium": self.is_premium(),
                "premium_until": self.user["premium_until"],
                "premium_days_left": self.premium_days_left(),
                "trial_available": not self.user["trial_used_at"] and not self.is_premium(),
            },
            "settings": dict(self.settings),
            "limits": {"free_game_limit": FREE_GAME_LIMIT},
            "region_label": {"ru": "Россия · ₽", "us": "США · $", "de": "Европа · €"}.get(
                self.settings["region"], self.settings["region"].upper()
            ),
        }

    def games_count(self) -> int:
        return len(self._state()["games"])

    async def set_region(self, region: str) -> None:
        await repository.set_price_region(self._chat_id(), region)
        await self._refresh()

    async def set_threshold(self, percent: int) -> None:
        await repository.set_min_discount(self._chat_id(), max(0, min(95, int(percent))))
        await self._refresh()

    async def set_digest(self, enabled: bool) -> None:
        await repository.set_digest_enabled(self._chat_id(), bool(enabled))
        await self._refresh()

    async def buy_premium(self) -> bool:
        # Premium is granted only by a verified Telegram payment update in the bot.
        return False

    async def grant_premium_days(self, days: int) -> None:
        chat = self._state()["chat"]
        now = dt.datetime.now(dt.timezone.utc)
        base = max(now, chat.premium_until or now)
        await repository.grant_premium(self._chat_id(), base + dt.timedelta(days=max(1, min(3650, int(days)))))
        await self._refresh()

    async def use_trial(self) -> bool:
        result = await repository.grant_trial(self._chat_id(), TRIAL_DAYS)
        if result:
            self.push_event("premium", "🎁 Пробный Premium", f"Бесплатно на {TRIAL_DAYS} дн. — один раз на чат")
        await self._refresh()
        return result

    async def revoke_premium(self) -> None:
        await repository.grant_premium(self._chat_id(), dt.datetime.now(dt.timezone.utc))
        await self._refresh()

    async def delete_data(self) -> bool:
        result = await repository.delete_private_user_data(self._chat_id())
        _ctx.set(None)
        return result

    async def reset_demo(self) -> None:
        raise RuntimeError("reset_demo is unavailable for production data")

    def tracked(self) -> list[dict]:
        return [
            {
                "appid": game.appid,
                "added_at": _iso(game.added_at),
                "target_cents": game.target_cents,
            }
            for game in self._state()["games"]
        ]

    def tracked_appids(self) -> list[int]:
        return [game.appid for game in self._state()["games"]]

    def is_tracked(self, appid: int) -> bool:
        return any(game.appid == appid for game in self._state()["games"])

    def get_tracked(self, appid: int) -> dict | None:
        game = next((game for game in self._state()["games"] if game.appid == appid), None)
        if not game:
            return None
        return {"appid": game.appid, "added_at": _iso(game.added_at), "target_cents": game.target_cents}

    async def add_game(self, appid: int, name: str, image: str | None) -> dict:
        if self.is_tracked(appid):
            return {"ok": False, "error": "already_tracked"}
        if not self.is_premium() and self.games_count() >= FREE_GAME_LIMIT:
            return {"ok": False, "error": "limit", "message": "Лимит бесплатного тарифа исчерпан"}
        tg_user = self._state()["tg_user"]
        ok = await repository.add_tracked_game(
            self._chat_id(), appid, name, added_by_tg_id=tg_user.id, added_by_username=tg_user.username
        )
        if ok:
            self.push_event("add", "✅ Игра добавлена", name)
        await self._refresh()
        return {"ok": ok, "game": self.get_tracked(appid)} if ok else {"ok": False, "error": "already_tracked"}

    async def remove_game(self, appid: int) -> bool:
        result = await repository.delete_tracked_game(self._chat_id(), appid)
        if result:
            self.push_event("remove", "🗑 Игра удалена", f"appid {appid}")
        await self._refresh()
        return result

    async def set_target(self, appid: int, cents: int | None) -> None:
        await repository.set_tracked_game_target(self._chat_id(), appid, cents)
        await self._refresh()

    async def set_game_field(self, appid: int, field: str, value) -> None:
        if field == "target_cents":
            await self.set_target(appid, value)

    def history_points(self, appid: int) -> list[dict]:
        return self._state().setdefault("history", {}).get(appid, [])

    async def add_history_point(self, appid: int, final_cents: int, discount: int, currency: str, ts: str,
                                original_cents: int | None = None) -> None:
        game = self.get_tracked(appid)
        await repository.add_sale_snapshot(
            appid, (game or {}).get("name", f"Игра {appid}"),
            int(original_cents or final_cents), final_cents, discount, currency,
        )
        self._state().setdefault("history", {}).setdefault(appid, []).append(
            {"t": ts, "f": final_cents, "d": discount, "c": currency}
        )

    async def seed_demo_history(self, *args, **kwargs) -> None:
        return None

    def market_cached_price(self, name: str) -> dict | None:
        return self._market_cache.get(name)

    async def set_market_cached_price(self, name: str, price: dict) -> None:
        self._market_cache[name] = {**price, "ts": time.time()}

    def market_history_points(self, name: str) -> list[dict]:
        return self._market_history.get(name, [])

    def market_history_has_demo(self, name: str) -> bool:
        return any(point.get("demo") for point in self.market_history_points(name))

    async def note_market_price(self, name: str, cents: int, ts: str | None = None, appid: int | None = None) -> None:
        stamp = ts or dt.datetime.now(dt.timezone.utc).isoformat()
        if appid is not None:
            await repository.add_market_price_snapshot(
                appid, name, cents, observed_at=dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            )
        self._market_history.setdefault(name, []).append({"t": stamp, "f": cents})

    def market_alerts(self) -> list[dict]:
        return [
            {
                "id": str(alert.id), "appid": alert.appid, "market_hash_name": alert.market_hash_name,
                "direction": alert.direction, "mode": alert.mode, "pct": alert.pct,
                "base_cents": alert.base_cents, "threshold_cents": alert.threshold_cents,
                "added_at": _iso(alert.added_at), "item_icon": alert.item_icon, "item_type": alert.item_type,
            }
            for alert in self._state().get("alerts", [])
        ]

    async def add_market_alert(self, appid: int, market_hash_name: str, direction: str,
                               threshold_cents: int, item_icon: str | None = None,
                               item_type: str | None = None, mode: str = "price",
                               pct: float | None = None, base_cents: int | None = None) -> dict:
        tg_user = self._state()["tg_user"]
        existing = self.market_alerts()
        if any((a["market_hash_name"], a["direction"]) == (market_hash_name, direction) for a in existing):
            return {"ok": False, "error": "exists", "message": "Такой алерт уже есть"}
        ok = await repository.add_market_alert(
            self._chat_id(), appid, market_hash_name, direction, threshold_cents,
            added_by_tg_id=tg_user.id, added_by_username=tg_user.username,
            mode=mode, pct=pct, base_cents=base_cents, item_icon=item_icon, item_type=item_type,
        )
        await self._refresh()
        if not ok:
            return {"ok": False, "error": "exists", "message": "Такой алерт уже есть"}
        created = next(a for a in self.market_alerts() if a["market_hash_name"] == market_hash_name
                       and a["direction"] == direction and a.get("mode", "price") == mode)
        return {"ok": True, "alert": created}

    async def set_market_alert_meta(self, *args, **kwargs) -> None:
        return None

    async def remove_market_alert(self, alert_id: str) -> bool:
        before = {a["id"] for a in self.market_alerts()}
        await repository.delete_market_alert(int(alert_id), self._chat_id())
        await self._refresh()
        return alert_id in before and alert_id not in {a["id"] for a in self.market_alerts()}

    async def apply_market_prices(self, prices: dict[str, dict]) -> list[str]:
        return []

    def unread_events(self) -> int:
        return sum(1 for event in self.events if not event.get("read"))

    async def mark_all_read(self) -> None:
        await repository.mark_miniapp_events_read(self._chat_id())
        for event in self.events:
            event["read"] = True

    def push_event(self, kind: str, title: str, body: str = "", price: dict | None = None) -> None:
        """Keep the immediate API response fast and persist the event asynchronously."""
        state = _ctx.get()
        if not state:
            return
        event = {"id": uuid.uuid4().hex, "kind": kind, "title": title, "body": body,
                 "price": price, "ts": dt.datetime.now(dt.timezone.utc).isoformat(), "read": False}
        state["events"].insert(0, event)
        try:
            asyncio.get_running_loop().create_task(
                repository.create_miniapp_event(self._chat_id(), kind, title, body, price)
            )
        except RuntimeError:
            pass

    def stats(self) -> dict:
        return {"tracked_games": self.games_count(), "market_alerts": len(self.market_alerts()), "events_total": len(self.events),
                "events_week": len(self.events), "savings_cents": 0, "best_discount": 0, "market_triggered": 0, "all_time_lows": 0}

    async def digest_event_if_needed(self, *args, **kwargs) -> None:
        return None

    async def seasonal_banner_if_due(self, *args, **kwargs) -> None:
        return None
