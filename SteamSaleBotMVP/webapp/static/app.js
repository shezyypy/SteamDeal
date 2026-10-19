/* ============================================================
   SteamSale Mini App — клиент (vanilla JS, без сборки и CDN)
   Воспроизводит функции Telegram-бота в интерфейсе мини-аппки.
   ============================================================ */
"use strict";

/* ---------- окружение ---------- */
let tg = null;
try { tg = window.Telegram?.WebApp; } catch (e) { tg = null; }
if (tg && tg.ready) tg.ready();
if (tg && tg.setHeaderColor) { try { tg.setHeaderColor("#070b12"); tg.setBackgroundColor("#070b12"); } catch (e) {} }

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const state = { tab: "games", session: null, games: [], deals: null, alerts: [], unread: 0,
  dealScope: "mine", marketPending: null, feedRendered: 0, chartSeq: 0, deleting: new Set(), busyRefresh: false,
  wallet: null,
  // рынок: обзор (игра, последний ответ), живой тренд алертов (снимок цен, таймер)
  marketOvGame: "cs2", marketOv: null, marketOvLoading: false,
  marketSnap: null, marketLiveTimer: null, marketLiveOn: false, marketFired: [],
  // рынок: цель алерта (по цене / в % от текущей), текущая цена найденного предмета
  marketGoalMode: "price", marketCur: null,
  marketOvTimer: null, marketOvPauseUntil: 0,
  // пуши: seen-ключи событий (не дублируем), первый поллинг = базовая линия,
  // marketHitAt — когда алерт уже «отпушен» при снятии, чтобы лента не повторила
  pushSeen: new Set(), pushEver: false, marketHitAt: {} };

/* ---------- helpers ---------- */
async function api(path, opts, timeoutMs) {
  const ms = timeoutMs || 12000;
  let ctrl = null, to = null;
  if (typeof AbortController !== "undefined") { ctrl = new AbortController(); to = setTimeout(() => ctrl.abort(), ms); }
  let resp;
  try {
    resp = await fetch(path, Object.assign({}, opts || {}, ctrl ? { signal: ctrl.signal } : {}));
  } catch (e) {
    const err = new Error("Нет соединения с сервером");
    err.network = true;
    throw err;
  } finally {
    if (to) clearTimeout(to);
  }
  let data = null;
  try { data = await resp.json(); } catch (e) {}
  if (!resp.ok) {
    const err = new Error((data && data.message) || ("HTTP " + resp.status));
    err.data = data;
    throw err;
  }
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function curSym(cur) {
  const up = String(cur || "").toUpperCase();
  const map = { RUB: "₽", RUR: "₽", USD: "$", EUR: "€", RU: "₽", US: "$", DE: "€" };
  return map[up] || cur || "";
}
function fmtMoney(cents, cur) {
  if (cents == null) return "—";
  const s = curSym(cur);
  const v = cents / 100;
  if (s === "₽") return Math.round(v).toLocaleString("ru-RU") + " ₽";
  if (s === "$") return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (s === "€") return v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " " + (cur || "");
}
function moneyHtml(cents, cur, isFree, cls) {
  if (isFree) return `<span class="price-now free ${cls || ""}">Бесплатно</span>`;
  if (cents == null) return `<span class="price-now na ${cls || ""}">нет цены</span>`;
  return `<span class="price-now ${cls || ""}">${esc(fmtMoney(cents, cur))}</span>`;
}
function timeAgo(ts) {
  const d = Date.now() - new Date(ts).getTime();
  const m = Math.floor(d / 60000);
  if (m < 1) return "только что";
  if (m < 60) return m + " мин назад";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " ч назад";
  const days = Math.floor(h / 24);
  return days + " дн назад";
}
function dayLabel(ts) {
  const d = new Date(ts); const now = new Date();
  const s = (x) => x.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  if (d.toDateString() === now.toDateString()) return "Сегодня";
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "Вчера";
  return s(d);
}
function untilLabel(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

/* ---------- toast ---------- */
let toastTimer = null;
function toast(msg, isErr, ms) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  clearTimeout(t._hide);
  toastTimer = setTimeout(() => { t.classList.add("hide"); t._hide = setTimeout(() => t.classList.add("hidden"), 220); }, ms || 3400);
}
function errToast(e) { toast((e && e.message) || "Что-то пошло не так", true); }

/* ============================================================
   🔔 ПУШ-УВЕДОМЛЕНИЯ
   Слои «заметности» при срабатывании алерта / новой скидке:
   1) пуш-баннер сверху (в стиле системных уведомлений, всегда виден);
   2) системный Notification браузера, если пользователь разрешил.
   Источник — события ленты (их шлёт воркер при срабатывании), поэтому
   лишних запросов к Steam не делаем: /api/events и так поллится каждые 20 с.
   ============================================================ */
const PUSH_KINDS = new Set(["market", "sale", "low", "target"]);
const PUSH_ICON = { market: "📈", sale: "🔥", low: "🏆", target: "🎯" };
const PUSH_KEY = "steamsale_push";

function pushEnabled() { try { return localStorage.getItem(PUSH_KEY) !== "0"; } catch (e) { return true; } }
function setPushEnabled(v) { try { localStorage.setItem(PUSH_KEY, v ? "1" : "0"); } catch (e) {} }

function evPushKey(e) {
  return e.kind + "|" + e.ts + "|" + e.title + "|" + String(e.body || "").slice(0, 60);
}
function absorbEvents(d) {
  (d && d.events || []).forEach((e) => state.pushSeen.add(evPushKey(e)));
}

/* системный Notification браузера (нужен HTTPS + разрешение пользователя) */
function sysNotify(title, body, tag) {
  try {
    if (!("Notification" in window)) return false;
    if (Notification.permission !== "granted") return false;
    if (!pushEnabled()) return false;
    const n = new Notification(String(title).slice(0, 140), { body: String(body || "").slice(0, 240), tag: tag || undefined });
    n.onclick = function () { try { window.focus(); } catch (e) {} try { this.close(); } catch (e) {} };
    return true;
  } catch (e) { return false; }
}

/* пуш-баннер сверху; клик ведёт в ленту/рынок, ✕ закрывает, сам исчезает через 9 с */
function pushTop(o) {
  if (!pushEnabled()) return;
  const layer = document.getElementById("pushLayer");
  if (!layer) return;
  while (layer.children.length >= 3) layer.removeChild(layer.firstChild);
  const card = document.createElement("div");
  card.className = "push-card";
  card.innerHTML = `
    <div class="push-ico">${o.ico || "🔔"}</div>
    <div class="push-body">
      <div class="push-app"><span class="pulse-dot"></span>SteamSale</div>
      <div class="push-title">${esc(String(o.title || "").slice(0, 90))}</div>
      ${o.text ? `<div class="push-text">${esc(String(o.text).slice(0, 220))}</div>` : ""}
    </div>
    <span class="push-time">только что</span>
    <button class="push-x" type="button" aria-label="Скрыть">✕</button>`;
  card.addEventListener("click", (ev) => {
    if (ev.target.closest(".push-x")) return;
    hidePush(card);
    if (o.tab) switchTab(o.tab);
    if (o.onopen) o.onopen();
  });
  card.querySelector(".push-x").addEventListener("click", () => hidePush(card));
  layer.appendChild(card);
  setTimeout(() => hidePush(card), 9000);
  return card;
}
function hidePush(card) {
  if (card._gone) return;
  card._gone = true;
  card.classList.add("bye");
  setTimeout(() => { if (card.parentNode) card.parentNode.removeChild(card); }, 240);
}

/* новый пуш из события ленты (геймы/скидки/цели/market) */
function handleEventPush(e) {
  if (!PUSH_KINDS.has(e.kind)) return;
  const marketName = e.kind === "market" && e.title
    ? String(e.title).replace(/^📈\s*Market:\s*/i, "").replace(/^[«»" ]+|["»« ]+$/g, "").trim() : "";
  // если это «тот же» сработавший алерт, который мы уже отпуш-баннерили при снятии — не дублируем
  if (marketName && Date.now() - (state.marketHitAt[marketName] || 0) < 200000) return;
  const title = String(e.title || "SteamSale").replace(/^[^\w\s«»"']{1,6}\s*/, ""); // без эмодзи-префикса
  const tag = marketName ? "m:" + marketName : evPushKey(e);
  pushTop({ ico: PUSH_ICON[e.kind] || "🔔", title, text: e.body || "", tag,
    tab: e.kind === "market" ? "market" : "feed" });
  sysNotify(e.title || "SteamSale", e.body || "", tag);
  if (marketName) state.marketHitAt[marketName] = Date.now();
}

/* «сработал market-алерт» прямо с вкладки Рынок (снятие в живом тике) */
function notifyMarketFired(name, body) {
  if (!pushEnabled()) return;
  state.marketHitAt[name] = Date.now();
  pushTop({ ico: "📈", title: name, text: body || "Цена пересекла цель — алерт снят.", tab: "market" });
  sysNotify("🎯 Алерт сработал: " + name, body || "Цена пересекла цель. Алерт снят — событие в ленте.", "m:" + name);
}

/* статус системных уведомлений: 'on' | 'ask' | 'off' | 'none' | 'denied' */
function pushSysState() {
  if (!("Notification" in window)) return "none";
  if (Notification.permission === "granted") return "on";
  if (Notification.permission === "denied") return "denied";
  return "ask";
}
async function requestPushPermission() {
  try {
    if (!("Notification" in window)) { toast("Этот браузер не умеет системные уведомления", true); return; }
    const p = await Notification.requestPermission();
    if (p === "granted") { toast("🔔 Системные уведомления включены"); }
    else if (p === "denied") { toast("Системные уведомления запрещены — баннеры внутри приложения продолжат работать"); }
  } catch (e) { /* вне защищённого контекста */ }
  refreshPushUI();
}
function refreshPushUI() {
  const row = document.getElementById("pushRow");
  if (!row) return;
  const t = document.getElementById("pushToggle");
  const btn = document.getElementById("pushPermBtn");
  const hint = document.getElementById("pushHint");
  if (t) t.checked = pushEnabled();
  const st = pushSysState();
  const stateLabel = st === "on" ? "системные вкл" : st === "denied" ? "системные запрещены в браузере" : st === "none" ? "только баннер внутри" : "системные ждут разрешения";
  if (hint) hint.textContent = "баннер сверху при срабатывании алерта/скидки · " + stateLabel;
  if (btn) {
    const need = st === "ask" || st === "none";
    btn.classList.toggle("hidden", !(need && pushEnabled()));
    btn.textContent = st === "none"
      ? "🔔 Системные уведомления не поддержаны этим браузером"
      : "🔔 Разрешить системные уведомления";
    btn.disabled = st === "none";
  }
}

/* ---------- тема (тёмная/светлая) ---------- */
const THEME_KEY = "steamsale_theme";
function currentTheme() { try { return localStorage.getItem(THEME_KEY) || "dark"; } catch (e) { return "dark"; } }
function applyTheme(t) {
  const theme = t === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
  const chip = document.getElementById("themeChip");
  if (chip) chip.textContent = theme === "light" ? "☀️ Светлая" : "🌙 Тёмная";
  const seg = document.getElementById("themeSeg");
  if (seg) seg.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.themeVal === theme));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#eef2f7" : "#070b12");
}

/* ---------- экран «нет соединения» ---------- */
function showNetOverlay() {
  const box = document.getElementById("netOverlay");
  if (box) box.classList.remove("hidden");
}
function hideNetOverlay() {
  const box = document.getElementById("netOverlay");
  if (box) box.classList.add("hidden");
}

/* ---------- картинки с фолбэком ---------- */
window.__imgBroken = function (img, emoji) {
  const ph = document.createElement("span");
  ph.className = img.dataset.ph || "gc-ph";
  ph.textContent = emoji || "🎮";
  if (img.parentElement) img.parentElement.replaceChild(ph, img);
};
function artTag(url, phCls, emoji) {
  const cls = phCls || "gc-ph";
  if (!url) return `<span class="${cls}">${emoji || "🎮"}</span>`;
  return `<img loading="lazy" class="${cls}" alt="" src="${esc(url)}" data-ph="${cls}" onerror="__imgBroken(this, '${emoji || "🎮"}')">`;
}

/* ============================================================
   💼 Кошелёк вишлиста (живой итог по ценам Steam)
   ============================================================ */
function moneyNum(cents, region) {
  /* число денег БЕЗ символа валюты (для больших анимируемых значений):
     рубли — целые с разделителем, $/€ — с двумя знаками после запятой. */
  const v = (cents || 0) / 100;
  const s = curSym(region);
  if (s === "₽") return Math.round(v).toLocaleString("ru-RU");
  if (s === "$") return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (s === "€") return v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return Math.round(v).toLocaleString("ru-RU");
}
function countUp(el, to) {
  const cur = parseInt((el.dataset.v || "0"), 10) || 0;
  if (cur === to) { el.textContent = to.toLocaleString("ru-RU"); return; }
  const t0 = performance.now(), dur = 900;
  const tick = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = Math.round(cur + (to - cur) * e).toLocaleString("ru-RU");
    if (k < 1) requestAnimationFrame(tick);
  };
  el.dataset.v = to;
  requestAnimationFrame(tick);
}
function countMoney(el, fromCents, toCents, region) {
  /* плавный «добор» денег с правильным форматированием каждый кадр */
  const cur = parseInt((el.dataset.v || String(fromCents || 0)), 10) || 0;
  const f0 = Number.isFinite(cur) ? cur : 0;
  const f1 = toCents || 0;
  if (f0 === f1) { el.textContent = moneyNum(f1, region); return; }
  const t0 = performance.now(), dur = 900;
  const tick = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = moneyNum(f0 + (f1 - f0) * e, region);
    if (k < 1) requestAnimationFrame(tick);
  };
  el.dataset.v = f1;
  requestAnimationFrame(tick);
}

function walletHeroHtml(w, prev) {
  const sym = curSym(w.region);
  const fmt = (c) => fmtMoney(c, w.region);   // с символом и пробелами
  const num = (c) => moneyNum(c, w.region);   // без символа — он подставляется рядом
  const onSale = (w.items || []).filter((i) => i.on_sale);
  const hero = document.createElement("div");
  hero.className = "wcard anim-in";
  if (!w.priced_count) {
    hero.innerHTML = `
      <div class="wcard-top"><div class="wcard-title">💼 Кошелёк вишлиста</div></div>
      <div class="wcard-empty muted small">Добавьте игры — и здесь соберётся живой итог: во что обойдётся купить весь список прямо сейчас и сколько вы сэкономите на текущих скидках Steam.</div>`;
    return hero;
  }
  // стек-бар: каждый сегмент = игра, ширина по доле в полной цене
  let bar = "", sum = 0;
  const items = (w.items || []).slice();
  items.forEach((i) => { sum += i.full_cents || 1; });
  items.forEach((i) => {
    const pct = Math.max(1.2, ((i.full_cents || 1) / sum) * 100);
    const sale = i.on_sale;
    bar += `<span class="wb-seg ${sale ? "sale" : ""}" style="width:${pct.toFixed(2)}%"
      title="${esc(i.name)}${sale ? " −" + i.discount_percent + "%" : " · полная цена"}"></span>`;
  });
  const hot = onSale.slice(0, 4).map((i) => `${esc(i.name)} <b>−${i.discount_percent}%</b>`).join(" · ");
  const rows = onSale.length
    ? `<div class="wcard-rows">${onSale.map((i, idx) => `
        <button class="wb-row anim-in" type="button" data-appid="${i.appid}" style="animation-delay:${Math.min(idx,6)*50}ms">
          <span class="wb-row-art">${artTag(i.header_image, "wb-row-ph", "🎮")}</span>
          <span class="wb-row-name">${esc(i.name)}</span>
          <span class="wb-row-price"><span class="strike">${fmt(i.full_cents)}</span>
          <span class="price-now">${i.today_cents === 0 ? "Бесплатно" : fmt(i.today_cents)}</span></span>
          <span class="badge-sale">−${i.discount_percent}%</span>
        </button>`).join("")}
        ${w.items.length - onSale.length > 0 ? `<div class="wb-rest muted small">+${w.items.length - onSale.length} по полной цене (ждут скидку)</div>` : ""}
      </div>`
    : `<div class="wb-rest muted small">Сейчас в списке всё по полной цене. Скидки зажгут этот кошелёк сами 🔥 — или загляните на вкладку «Сделки».</div>`;
  const prevFull = prev ? prev.full_cents : 0;
  const prevToday = prev ? prev.today_cents : 0;
  const prevSave = prev ? prev.savings_cents : 0;
  const age = w.age_sec != null ? w.age_sec : 0;
  hero.innerHTML = `
    <div class="wcard-top">
      <div>
        <div class="wcard-title">💼 Кошелёк вишлиста</div>
        <div class="wcard-live"><span class="pulse-dot ${age > 300 ? "slow" : ""}"></span>цены Steam обновлены ${age <= 60 ? "только что" : age <= 300 ? "недавно" : "ждут проверки"} · авто-опрос раз в ~2,5 мин</div>
      </div>
      <div class="wcard-topacts">
        <button class="wcard-share" type="button" title="Поделиться карточкой экономии">📤</button>
        <button class="wcard-fold" type="button" title="развернуть">⌄</button>
      </div>
    </div>
    <div class="wcard-body">
      <div class="wcard-full muted">если брать всё по полной цене: <s class="wcard-full-num" id="walletFull">${num(prevFull)}</s> ${sym}</div>
      <div class="wcard-big"><span class="wcard-big-num" id="walletToday" data-v="${prevToday}">${num(prevToday)}</span><span class="wcard-big-sym"> ${sym}</span></div>
      <div class="wcard-sub">купить весь список прямо сейчас</div>
      <div class="wcard-save ${w.savings_cents > 0 ? "" : "none"}">
        ${w.savings_cents > 0
          ? `экономия <b class="wcard-save-num" id="walletSave" data-v="${prevSave}">${num(prevSave)}</b> ${sym} <span class="wcard-pct">−${w.pct}%</span>`
          : `<span class="muted small">скидок пока нет — но мы следим каждые пару минут</span>`}
      </div>
      ${w.savings_cents > 0 ? `<div class="wb-bar">${bar}</div><div class="wb-hot small">${hot}</div>` : ""}
      ${rows}
    </div>`;
  return hero;
}

async function loadWallet() {
  try {
    const d = await api("/api/wallet");
    const prev = state.wallet;
    state.wallet = d;
    renderWalletHero(prev);
  } catch (e) { /* не критично: кошелёк дополнителен */ }
}
function renderWalletHero(prev) {
  const box = document.getElementById("walletHero");
  if (!box || !state.wallet) { if (box) box.classList.add("hidden"); return; }
  const w = state.wallet;
  const sig = (x) => x && JSON.stringify({
    full: x.full_cents, today: x.today_cents, save: x.savings_cents,
    items: (x.items || []).map((i) => [i.appid, i.on_sale, i.discount_percent]),
  });
  if (prev && sig(prev) === sig(w)) return;   // цифры не менялись — не перерисовываем
  const node = walletHeroHtml(w, prev);
  box.innerHTML = "";
  box.appendChild(node);
  box.classList.remove("hidden");
  // анимация счётчиков (в рублях/валюте — не в копейках)
  const p0 = prev || {};
  requestAnimationFrame(() => {
    const t = document.getElementById("walletToday");
    const s = document.getElementById("walletSave");
    const f = document.getElementById("walletFull");
    if (t) countMoney(t, p0.today_cents || 0, w.today_cents, w.region);
    if (s) countMoney(s, p0.savings_cents || 0, w.savings_cents, w.region);
    if (f) countMoney(f, p0.full_cents || 0, w.full_cents, w.region);
  });
  // разбор по играм кликабелен (открывает карточку)
  node.querySelectorAll(".wb-row").forEach((b) => {
    b.addEventListener("click", () => openGameSheet(parseInt(b.dataset.appid, 10)));
  });
  // складной режим
  const fold = node.querySelector(".wcard-fold");
  const body = node.querySelector(".wcard-body");
  if (fold && body) {
    if (localStorage.getItem("steamsale_wallet_folded") === "1") {
      body.classList.add("folded");
      fold.classList.add("rot");
    }
    fold.addEventListener("click", () => {
      body.classList.toggle("folded");
      fold.classList.toggle("rot");
      try { localStorage.setItem("steamsale_wallet_folded", body.classList.contains("folded") ? "1" : "0"); } catch (e) {}
    });
  }
  // share-карточка «моя экономия»: PNG -> Telegram
  const ws = node.querySelector(".wcard-share");
  if (ws) ws.addEventListener("click", () => {
    try {
      haptic("impact", [10]);
      const w2 = state.wallet;
      const { lines } = walletShareLines(w2);
      const cap = "💼 Мой кошелёк SteamSale: " +
        lines.map((l) => l.l + " " + l.r).join(" · ") +
        " — слежу за ценами Steam каждые пару минут. " + "t.me/SteamSaleBot";
      shareFlow(shareCardWallet(w2), cap);
    } catch (e) { errToast(e); }
  });
}

/* ---- мини-спарклайн цены предмета (для карточек алертов) ---- */
function alertSpark(pts, cur) {
  if (!pts || pts.length < 2) return "";
  const W = 110, H = 30, P = 3;
  const vals = pts.map((p) => p.f);
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if (lo === hi) { lo = lo * 0.97; hi = hi * 1.03; }
  const range = hi - lo || 1;
  const t0 = new Date(pts[0].t).getTime(), t1 = new Date(pts[pts.length - 1].t).getTime();
  const span = (t1 - t0) || 1;
  const X = (p) => P + ((new Date(p.t).getTime() - t0) / span) * (W - P * 2);
  const Y = (f) => H - P - ((f - lo) / range) * (H - P * 2);
  const uid = "sp" + (++state.chartSeq);
  let line = "";
  pts.forEach((p, i) => { line += (i ? " L" : "M") + X(p).toFixed(1) + " " + Y(p.f).toFixed(1); });
  const last = pts[pts.length - 1], first = pts[0];
  const down = last.f < first.f;
  const color = down ? "#39d98a" : (last.f > first.f ? "#ffb454" : "#8b5cf6");
  const area = line + " L" + (W - P) + " " + (H - P) + " L" + P + " " + (H - P) + " Z";
  const step = pts.length > 1 ? (new Date(pts[1].t) - new Date(pts[0].t)) : 0;
  const spanDays = span / 86400000;
  const hint = spanDays >= 1 ? spanDays.toFixed(0) + " дн" : Math.max(1, Math.round(span / 3600000)) + " ч";
  return `<svg class="al-spark-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">
    <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${color}" stop-opacity=".20"/><stop offset="100%" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#${uid})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
    <circle class="sp-halo" cx="${X(last).toFixed(1)}" cy="${Y(last.f).toFixed(1)}" r="5.5" fill="${color}"/>
    <circle class="sp-last" cx="${X(last).toFixed(1)}" cy="${Y(last.f).toFixed(1)}" r="2.6" fill="${color}" stroke="#0b1320" stroke-width="1"/>
    <title>${hint} · ${esc(fmtMoney(last.f, last.c || cur))}</title>
  </svg>`;
}

/* ============================================================
   График цены (SVG, без библиотек)
   ============================================================ */
function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
  } catch (e) { return iso || ""; }
}

function priceChart(points, opts) {
  const o = opts || {};
  if (!points || points.length < 2) return null;
  const W = 560, H = 170, PAD = 12;
  const vals = points.map((p) => p.f);
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if (lo === hi) { lo = lo * 0.9; hi = hi * 1.05; }
  const range = hi - lo || 1;
  lo -= range * 0.08; hi += range * 0.10;
  const t0 = new Date(points[0].t).getTime(), t1 = new Date(points[points.length - 1].t).getTime();
  const span = (t1 - t0) || 1;
  const X = (p) => PAD + ((new Date(p.t).getTime() - t0) / span) * (W - PAD * 2);
  const Y = (f) => H - PAD - ((f - lo) / (hi - lo)) * (H - PAD * 2);
  const uid = "g" + (++state.chartSeq);
  let line = "";
  points.forEach((p, i) => { line += (i ? " L" : "M") + X(p).toFixed(1) + " " + Y(p.f).toFixed(1); });
  const area = line + " L" + (W - PAD) + " " + (H - PAD) + " L" + PAD + " " + (H - PAD) + " Z";
  // распродажные точки: маленькая видимая + большая невидимая зона для клика/тапа
  let dots = "";
  points.forEach((p, i) => {
    if (!p.d || p.d <= 0) return;
    const x = X(p).toFixed(1), y = Y(p.f).toFixed(1);
    dots += `<g class="pt" data-i="${i}" tabindex="0" role="button" aria-label="Точка распродажи">
      <circle class="pt-hit" cx="${x}" cy="${y}" r="12" fill="transparent"/>
      <circle class="pt-dot" cx="${x}" cy="${y}" r="3.8" fill="#0b1320" stroke="#a1cd44" stroke-width="2.1"/>
      <title>${fmtDate(p.t)} — ${esc(fmtMoney(p.f, p.c))}${p.d ? " (−" + p.d + "%)" : ""}</title>
    </g>`;
  });
  const last = points[points.length - 1];
  const cur = `<circle cx="${X(last).toFixed(1)}" cy="${Y(last.f).toFixed(1)}" r="4.6" fill="#4ea1ff" stroke="#eaf2ff" stroke-width="1.5"/>`;
  const curX = X(last); let anchor = "start";
  if (curX > W - 130) anchor = "end";
  const curLabel = `<text x="${curX + (anchor === "end" ? -8 : 8)}" y="${(Y(last.f) - 8).toFixed(1)}" font-size="11.5" font-weight="700" fill="var(--text)" text-anchor="${anchor}">${esc(fmtMoney(last.f, last.c))}</text>`;
  return `
  <svg id="priceChart" viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
    <defs>
      <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#4ea1ff" stop-opacity=".30"/>
        <stop offset="100%" stop-color="#4ea1ff" stop-opacity=".02"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#${uid})"/>
    <path d="${line}" fill="none" stroke="#5aa7f5" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}${cur}${curLabel}
  </svg>`;
}

/* Мини-график цены предмета Market (для витрины) */
function marketMiniChart(pts) {
  if (!pts || pts.length < 2) return "";
  const W = 560, H = 92, P = 8;
  const vals = pts.map((p) => p.f);
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if (lo === hi) { lo = lo * 0.9; hi = hi * 1.05; }
  const t0 = new Date(pts[0].t).getTime(), t1 = new Date(pts[pts.length - 1].t).getTime();
  const span = (t1 - t0) || 1;
  const X = (p) => P + ((new Date(p.t).getTime() - t0) / span) * (W - P * 2);
  const Y = (f) => H - P - ((f - lo) / (hi - lo)) * (H - P * 2);
  const uid = "m" + (++state.chartSeq);
  let line = "";
  pts.forEach((p, i) => { line += (i ? " L" : "M") + X(p).toFixed(1) + " " + Y(p.f).toFixed(1); });
  const area = line + " L" + (W - P) + " " + (H - P) + " L" + P + " " + (H - P) + " Z";
  const last = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
    <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#8b5cf6" stop-opacity=".28"/>
      <stop offset="100%" stop-color="#8b5cf6" stop-opacity=".02"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#${uid})"/>
    <path d="${line}" fill="none" stroke="#a06bff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(last).toFixed(1)}" cy="${Y(last.f).toFixed(1)}" r="3.6" fill="#8b5cf6" stroke="#fff" stroke-width="1.4"/>
  </svg>`;
}

/* ============================================================
   Навигация / topbar
   ============================================================ */
function switchTab(name) {
  const prev = state.tab;
  state.tab = name;
  if (prev && prev !== name) haptic("impact", [8]);   // микро-полировка: лёгкий отклик
  $$(".view").forEach((v) => v.classList.add("hidden"));
  const view = $("#view-" + name);
  if (view) view.classList.remove("hidden");
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "games") loadGames();
  else if (name === "deals") loadDeals();
  else if (name === "market") {
    loadMarket();
    marketOvAutoStart();
    marketLiveStart();
  }
  else if (name === "feed") { loadFeed(); markRead(); }
  else if (name === "profile") renderProfile();
  if (name !== "market") { marketLiveStop(); marketOvAutoStop(); }
  window.scrollTo({ top: 0 });
}

async function loadSession() {
  state.session = await api("/api/session");
  renderTop();
}
function renderTop() {
  if (!state.session) return;
  const s = state.session;
  const user = s.user;
  $("#premiumPill").classList.toggle("hidden", !user.premium);
  const regionEmoji = { ru: "🇷🇺", us: "🇺🇸", de: "🇪🇺" }[s.settings.region] || "";
  $("#regionPill").textContent = regionEmoji + " " + s.settings.region.toUpperCase();
  $("#regionChip").textContent = regionEmoji + " " + s.settings.region.toUpperCase();
  const parts = [];
  if (user.premium) parts.push("Premium");
  parts.push(regionEmoji + " " + s.region_label.replace(" ·", ""));
  const count = state.games ? state.games.length : 0;
  if (count) parts.push(count + " игр");
  $("#topInfo").textContent = parts.join(" · ");
}

async function pollUnread() {
  try {
    const d = await api("/api/events");
    state.unread = d.unread;
    renderBadge();
    const evs = d.events || [];
    if (!state.pushEver) {
      // первый заход = базовая линия: старую историю не «пушим» (иначе спам на открытии)
      state.pushEver = true;
      absorbEvents(d);
      if (state.tab === "feed" && d.unread > 0) loadFeed();
      return;
    }
    const fresh = evs.filter((e) => !state.pushSeen.has(evPushKey(e)) && PUSH_KINDS.has(e.kind));
    absorbEvents(d);
    const onFeed = state.tab === "feed";
    if (onFeed && d.unread > 0) loadFeed();
    // на вкладке Ленты пользователь и так видит новые события — баннер не нужен
    if (!onFeed) fresh.forEach(handleEventPush);
  } catch (e) {}
}
function renderBadge() {
  const b = $("#feedBadge");
  b.classList.toggle("hidden", !state.unread);
  b.textContent = state.unread > 9 ? "9+" : state.unread;
}

/* ============================================================
   ГЛАВНЫЙ РЕФРЕШ (кнопка ⟳) — как полный цикл воркера
   ============================================================ */
async function refreshAll() {
  if (state.busyRefresh) return;
  state.busyRefresh = true;
  const btn = $("#refreshTop");
  btn.disabled = true; btn.classList.add("spinning");
  try {
    const r = await post("/api/games/refresh", {});
    if (!r.ok) { toast(r.message || "Сейчас идёт другое обновление", true); return; }
    const s = r.summary || {};
    if (s.locked) { toast("Обновление уже идёт…"); return; }
    let msg = "Цены обновлены";
    if (s.sales_found > 0) msg += ` · скидок: ${s.sales_found}`;
    if (s.market_triggered > 0) msg += ` · market: ${s.market_triggered}`;
    if (s.new_events > 0) msg += ` · новые события: ${s.new_events}`;
    toast(msg);
    await loadSession();
    if (state.tab === "games") await loadGames();
    else if (state.tab === "deals") await loadDeals();
    else if (state.tab === "market") await loadMarket();
  } catch (e) { errToast(e); }
  finally {
    state.busyRefresh = false;
    btn.disabled = false; btn.classList.remove("spinning");
  }
}

/* ============================================================
   🎮 ИГРЫ
   ============================================================ */
async function loadGames() {
  try {
    const d = await api("/api/games");
    state.games = d.games;
  } catch (e) { errToast(e); state.games = []; }
  renderTop();
  renderGames();
  loadWallet();
}

/* ============================================================
   🔥 Автокарусель «Сейчас горит» — 5 секунд на слайд
   (ваши игры со скидкой + топ глобальных сделок Steam)
   ============================================================ */
let hotTimer = null, hotIdx = 0, hotN = 0;
function stopHot() { if (hotTimer) { clearInterval(hotTimer); hotTimer = null; } }
function goHot(step) { if (!hotN) return; hotIdx = (hotIdx + step + hotN) % hotN; paintHot(); }
function paintHot() {
  const wrap = document.getElementById("hotWrap");
  if (!wrap) return;
  const track = wrap.querySelector(".hot-track");
  if (track) track.style.transform = "translateX(-" + (hotIdx * 100) + "%)";
  wrap.querySelectorAll(".hot-dot").forEach((d, i) => d.classList.toggle("on", i === hotIdx));
  const cnt = wrap.querySelector(".hot-count");
  if (cnt) cnt.textContent = (hotIdx + 1) + " / " + (hotN || 1);
}
function startHot() {
  stopHot();
  if (hotN < 2) return;
  hotTimer = setInterval(() => {
    if (document.hidden) return;
    if (state.tab !== "games") return;
    if (state.hotPaused) return;
    goHot(1);
  }, 5000);
}
function renderHotCarousel(games) {
  const strip = $("#featuredStrip");
  if (!strip) return;
  stopHot();
  const tracked = (games || []).filter((g) => g.on_sale)
    .sort((a, b) => ((b.price && b.price.discount_percent) || 0) - ((a.price && a.price.discount_percent) || 0));
  const slides = tracked.slice(0, 4).map((g) => ({
    appid: g.appid, name: g.name, img: g.header_image,
    disc: (g.price && g.price.discount_percent) || 0,
    orig: (g.price && g.price.price_original_cents) || 0,
    fin: (g.price && g.price.price_final_cents) || 0,
    cur: (g.price && g.price.currency) || "RUB",
    tracked: true, isFree: !!(g.price && g.price.is_free),
  }));
  const fill = 6 - slides.length;
  const haveGlob = !!(state.deals && state.deals.global && state.deals.global.length);
  if (fill > 0 && !haveGlob && !state.hotLoading) {
    state.hotLoading = true;
    api("/api/deals").then((d) => {
      state.deals = d;
      state.hotLoading = false;
      if (state.tab === "games") renderHotCarousel(state.games || games);
    }).catch(() => { state.hotLoading = false; });
  }
  if (fill > 0 && haveGlob) {
    const mine = new Set((games || []).map((g) => g.appid));
    state.deals.global
      .filter((i) => !mine.has(i.appid) && !slides.some((s) => s.appid === i.appid))
      .sort((a, b) => (b.discount_percent || 0) - (a.discount_percent || 0))
      .slice(0, fill)
      .forEach((i) => slides.push({
        appid: i.appid, name: i.name, img: i.header_image, disc: i.discount_percent,
        orig: i.price_original_cents, fin: i.price_final_cents,
        cur: i.currency || state.deals.region, tracked: false, isFree: !!i.is_free,
      }));
  }
  if (!slides.length) { strip.classList.add("hidden"); strip.innerHTML = ""; return; }

  strip.classList.remove("hidden");
  strip.classList.add("hotcar");
  hotN = slides.length;
  if (hotIdx >= hotN) hotIdx = 0;
  const slideHtml = (s) => {
    const tag = s.tracked ? "ваш список 🔥" : "сейчас в Steam";
    const price = s.isFree
      ? '<span class="price-now free">Бесплатно</span>'
      : `<span class="strike">${esc(fmtMoney(s.orig, s.cur))}</span><span class="price-now">${esc(fmtMoney(s.fin, s.cur))}</span>`;
    return `
    <div class="hc-slide" data-appid="${s.appid}" data-tracked="${s.tracked ? 1 : 0}">
      ${artTag(s.img, "hc-bg", "🎮")}
      <div class="hc-shade"></div>
      <span class="hc-tag ${s.tracked ? "own" : ""}">${tag}</span>
      <div class="hc-content">
        <div class="hc-name">${esc(s.name)}</div>
        <div class="hc-row">
          <span class="badge-sale">−${s.disc}%</span>
          <span class="hc-prices">${price}</span>
        </div>
      </div>
      ${s.tracked ? "" : '<button class="hc-cta" type="button">＋ Следить</button>'}
    </div>`;
  };
  strip.innerHTML = `
    <div class="hot-wrap" id="hotWrap">
      <div class="hot-track">${slides.map(slideHtml).join("")}</div>
      <button class="hot-nav hp" type="button" aria-label="Назад">‹</button>
      <button class="hot-nav hn" type="button" aria-label="Вперёд">›</button>
      <div class="hot-foot">
        <div class="hot-dots">${slides.map((s, i) => `<span class="hot-dot${i === hotIdx ? " on" : ""}" data-i="${i}"></span>`).join("")}</div>
        <span class="hot-count">${(hotIdx + 1)} / ${hotN}</span>
      </div>
    </div>`;
  paintHot();
  startHot();

  const wrap = strip.querySelector(".hot-wrap");
  if (!wrap) return;
  wrap.querySelector(".hp").addEventListener("click", () => goHot(-1));
  wrap.querySelector(".hn").addEventListener("click", () => goHot(1));
  wrap.querySelectorAll(".hot-dot").forEach((d) => {
    d.addEventListener("click", () => { hotIdx = parseInt(d.dataset.i, 10); paintHot(); });
  });
  wrap.querySelectorAll(".hc-slide").forEach((sl) => {
    const appid = parseInt(sl.dataset.appid, 10);
    const trackedFlag = sl.dataset.tracked === "1";
    const cta = sl.querySelector(".hc-cta");
    if (trackedFlag) {
      sl.addEventListener("click", () => openGameSheet(appid));
    } else if (cta) {
      cta.addEventListener("click", async (e) => {
        e.stopPropagation();
        cta.disabled = true; cta.textContent = "…";
        try {
          const r = await post("/api/games/add", { appid });
          if (r.ok) { toast("Добавлено в список"); await loadGames(); }
          else toast(r.message || "Не добавить", true);
        } catch (err) { errToast(err); }
        if (document.body.contains(cta)) { cta.disabled = false; cta.textContent = "＋ Следить"; }
      });
    }
  });
  wrap.addEventListener("pointerenter", () => { state.hotPaused = true; });
  wrap.addEventListener("pointerleave", () => { state.hotPaused = false; });
  let tx = 0;
  wrap.addEventListener("touchstart", (e) => { tx = e.touches[0].clientX; state.hotPaused = true; }, { passive: true });
  wrap.addEventListener("touchend", (e) => {
    const dx = e.changedTouches[0].clientX - tx;
    state.hotPaused = false;
    if (Math.abs(dx) > 45) goHot(dx < 0 ? 1 : -1);
  }, { passive: true });
}

function renderGames() {
  const list = $("#gameList");
  list.innerHTML = "";
  const games = state.games || [];
  $("#gamesEmpty").classList.toggle("hidden", games.length > 0);
  $("#gamesSub").textContent = games.length + " в списке · "
    + (state.session?.user?.premium ? "безлимит" : "лимит " + state.session?.limits?.free_game_limit);

  // автокарусель «Сейчас горит»: ваши скидки + топ сделок Steam (5 с на слайд)
  renderHotCarousel(games);

  games.forEach((g, i) => {
    const li = gameCard(g);
    li.classList.add("anim-in");
    li.style.animationDelay = Math.min(i, 14) * 35 + "ms";
    list.appendChild(li);
  });
  if (!games.length) return;
}

function gameCard(g) {
  const li = document.createElement("li");
  li.className = "game-card" + (g.on_sale ? " sale" : "");
  const p = g.price;
  let priceHtml;
  if (p && p.discount_percent > 0) {
    priceHtml = `<span class="badge-sale">−${p.discount_percent}%</span>` +
      `<span class="strike">${esc(fmtMoney(p.price_original_cents, p.currency))}</span>` + moneyHtml(p.price_final_cents, p.currency, false);
  } else {
    priceHtml = moneyHtml(p && p.price_final_cents, p && p.currency, p && p.is_free);
  }
  const target = g.target_cents ? `<span class="gc-target">🎯 до ${esc(fmtMoney(g.target_cents, g.price ? g.price.currency : "RUB"))}</span>` : "";
  const meta = g.on_sale ? '<span style="color:var(--sale);font-weight:700">скидка прямо сейчас</span>'
    : (g.fetched_at ? "в списке" : "ждём данные…");
  li.innerHTML = `
    <div class="gc-art">${artTag(g.header_image, "gc-ph")}</div>
    <div class="gc-body">
      <div class="gc-name">${esc(g.name)}</div>
      <div class="gc-meta">${meta}${target}</div>
      <div class="gc-price">${priceHtml}</div>
    </div>
    <div class="gc-chevs">
      <button class="gc-open" type="button" title="Подробнее">⋯</button>
      <button class="gc-open del" type="button" title="Удалить" style="color:var(--faint)">✕</button>
    </div>`;
  li.querySelector(".gc-open:not(.del)").addEventListener("click", (e) => { e.stopPropagation(); openGameSheet(g.appid); });
  const delBtn = li.querySelector(".del");
  delBtn.addEventListener("click", (e) => { e.stopPropagation(); twoTapDelete(delBtn, g); });
  li.addEventListener("click", () => openGameSheet(g.appid));
  return li;
}

function twoTapDelete(btn, g) {
  if (btn.dataset.armed === "1") {
    removeGame(g.appid);
    return;
  }
  btn.dataset.armed = "1"; btn.style.color = "var(--danger)"; btn.textContent = "?";
  setTimeout(() => { btn.dataset.armed = "0"; btn.style.color = ""; btn.textContent = "✕"; }, 2500);
}
async function removeGame(appid) {
  try { await post("/api/games/remove", { appid }); toast("Игра удалена из списка"); await loadGames(); }
  catch (e) { errToast(e); }
}

/* --- поиск и добавление --- */
function openSearch() {
  const panel = $("#searchPanel");
  const open = !panel.classList.contains("hidden");
  if (open) { panel.classList.add("hidden"); $("#addGameOpen").textContent = "＋ Добавить"; return; }
  panel.classList.remove("hidden");
  $("#addGameOpen").textContent = "Закрыть";
  setTimeout(() => $("#searchInput").focus(), 60);
}

let searchTimer = null, searchSeq = 0;
async function runSearch(q) {
  const seq = ++searchSeq;
  const box = $("#searchResults");
  if (q.length < 2) { box.innerHTML = ""; return; }
  box.innerHTML = '<li class="search-status">Ищу в Steam…</li>';
  try {
    const d = await api("/api/search?q=" + encodeURIComponent(q));
    if (seq !== searchSeq) return;
    box.innerHTML = "";
    if (!d.items.length) { box.innerHTML = '<li class="search-status">Ничего не нашлось. Попробуйте иначе или вставьте ссылку Steam.</li>'; return; }
    d.items.forEach((it) => {
      const li = document.createElement("li");
      const name = document.createElement("span"); name.className = "sname"; name.textContent = it.name; name.title = it.name;
      li.appendChild(name);
      if (it.tracked) {
        const badge = document.createElement("span"); badge.className = "sbadge"; badge.textContent = "в списке ✓"; li.appendChild(badge);
      } else {
        const btn = document.createElement("button"); btn.className = "btn btn-primary btn-sm"; btn.textContent = "＋";
        btn.addEventListener("click", () => addFromSearch(it.appid, btn));
        li.appendChild(btn);
      }
      box.appendChild(li);
    });
  } catch (e) {
    if (seq === searchSeq) box.innerHTML = '<li class="search-status">Ошибка поиска</li>';
  }
}
async function addFromSearch(appid, btn) {
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await post("/api/games/add", { appid });
    if (!r.ok) {
      if (r.error === "limit") { toast("Лимит бесплатного тарифа — включите Premium", true); openProfileAndUpsell(); }
      else toast(r.message || "Не удалось добавить", r.error !== "already_tracked");
      btn.disabled = false; btn.textContent = "＋"; return;
    }
    $("#searchInput").value = ""; $("#searchResults").innerHTML = "";
    $("#searchPanel").classList.add("hidden"); $("#addGameOpen").textContent = "＋ Добавить";
    toast("Добавлено: " + r.game.name);
    await loadGames();
    openGameSheet(appid);
  } catch (e) { errToast(e); btn.disabled = false; btn.textContent = "＋"; }
}

/* --- детали игры (шторка) --- */
async function openGameSheet(appid) {
  const backdrop = $("#sheetBackdrop"), sheet = $("#sheet");
  const show = () => { backdrop.classList.remove("hidden"); sheet.classList.remove("hidden"); document.body.style.overflow = "hidden"; };
  backdrop.classList.remove("hidden"); sheet.classList.remove("hidden");
  $("#sheetBody").innerHTML = '<div class="sh-body" style="padding-top:26px"><div class="sk sk-line"></div><div class="sk sk-line"></div></div>';
  show();
  try {
    const d = await api("/api/games/" + appid);
    renderSheet(d.game, d.history);
  } catch (e) {
    $("#sheetBody").innerHTML = '<div class="sh-body"><p class="search-status">Не удалось загрузить: ' + esc(e.message) + "</p></div>";
  }
}
function closeSheet() { $("#sheetBackdrop").classList.add("hidden"); $("#sheet").classList.add("hidden"); document.body.style.overflow = ""; }

/* ============================================================
   🧭 Аналитика цены игры (Premium): вердикт + зона покупки
   ============================================================ */
function gameAnalysis(hist, cur) {
  if (!hist || hist.length < 2 || cur == null) return null;
  let min = Infinity, max = -Infinity, sum = 0, saleCount = 0, saleMin = null, salePt = null;
  for (const p of hist) {
    sum += p.f;
    if (p.f < min) min = p.f;
    if (p.f > max) max = p.f;
    if ((p.d || 0) > 0) { saleCount++; if (saleMin == null || p.f < saleMin) { saleMin = p.f; salePt = p; } }
  }
  const n = hist.length;
  const range = max - min || 1;
  return { n, min, max, avg: sum / n, saleCount, saleMin, salePt, cur, pos: (cur - min) / range };
}
function gameSharesBelow(hist, t) {
  let k = 0;
  for (const p of hist) if (p.f <= t) k++;
  return hist.length ? k / hist.length : 0;
}

function gameVerdict(st) {
  const cheaperAvg = st.cur < st.avg * 0.98;
  if (st.pos <= 0.25) return { c: "v-cheap", t: "Дёшево · цена у нижней границы своей истории. Сейчас — исторически выгодная зона для покупки." };
  if (cheaperAvg && st.pos <= 0.5) return { c: "v-ok", t: "Нормально · цена ниже своей средней по истории — уже неплохой момент." };
  if (st.pos >= 0.78) return { c: "v-pricey", t: "Дорого · цена у верхней границы истории. Обычно выгоднее дождаться распродажи." };
  return { c: "v-ok", t: "Норма · цена в среднем диапазоне своей истории — нейтрально." };
}

function gameAnHtml(hist, g, p) {
  const premium = premiumNow();
  const sym = curSym(p && p.currency ? p.currency : (state.session?.settings?.region || "RUB"));
  if (!premium) {
    return `<div class="sn sn-locked anim-in" style="margin-top:0">
      <div class="sn-head"><b>🧭 Когда покупать?</b><span class="sn-pill prem">Premium</span></div>
      <p class="sn-txt">Минимум/средняя по истории, вердикт «дёшево-дорого» и зона покупки со шансом захода — аналитика цены для тех, кто не хочет брать дорого.</p>
    </div>`;
  }
  const st = gameAnalysis(hist, p && p.price_final_cents);
  if (!st) return "";
  const fmt = (c) => esc(fmtMoney(Math.round(c), sym));
  const v = gameVerdict(st);
  const demoNote = hist.length < 8
    ? "<span class=\"sn-demo\">история — демо-кривая сезонных распродаж + реальные наблюдения (уточняется)</span>"
    : `<span class="sn-demo">по ${st.n} наблюдениям истории цен</span>`;
  const bestSale = st.salePt
    ? `лучшая распродажа в истории: ${fmt(st.saleMin)} · ${fmtDate(st.salePt.t)}${st.salePt.d ? " · −" + st.salePt.d + "%" : ""}`
    : `распродаж в истории не отмечено · минимум ${fmt(st.min)}`;
  const nowTxt = p && p.discount_percent > 0
    ? `${fmt(p.price_final_cents)} сейчас (со скидкой −${p.discount_percent}%)`
    : `${fmt(st.cur)} сейчас (без скидки)`;
  return `
  <div class="sn anim-in" style="margin-top:0">
    <div class="sn-head">
      <b>🧭 Когда покупать?</b>
      <span class="sn-pill on">Premium</span>
    </div>
    <div class="sn-chance-top" style="margin-top:8px">
      <span style="font-size:12.5px;color:var(--text);font-weight:800">${esc(nowTxt)}</span>
      <span class="mk-cmp-pill ${v.c}">${v.t.split(" ")[0].replace(/[· ]/g, "")}</span>
    </div>
    <div class="mk-cmp-stats" style="grid-template-columns:repeat(3,auto);margin-top:8px">
      <span><i>минимум</i> <b>${fmt(st.min)}</b></span>
      <span><i>средняя</i> <b>${fmt(st.avg)}</b></span>
      <span><i>максимум</i> <b>${fmt(st.max)}</b></span>
    </div>
    <p class="sn-verdict" style="margin-top:6px">${v.t} ${esc(bestSale)}.</p>
    <div class="sn-row">
      <input type="range" id="gaRange" min="5" max="60" step="5" value="15" aria-label="зона покупки, %">
      <div class="sn-labels"><span id="gaLabel">купить, если упадёт на 15%</span><span class="sn-target" id="gaTarget"></span></div>
    </div>
    <div class="sn-chance">
      <div class="sn-chance-top"><span>шанс: история доходила до этой цены</span><b id="gaPct">—</b></div>
      <div class="sn-bar"><div class="sn-bar-fill mid" id="gaFill" style="width:0%"></div></div>
      <div class="sn-scale"><span>редко</span><span>часто</span></div>
      <p class="sn-verdict" id="gaVerd" style="margin-top:8px"></p>
    </div>
    <div class="sn-actions">
      <button class="btn btn-primary btn-sm" id="gaWatch" type="button">🎯 Поставить цель на эту цену</button>
      ${demoNote}
    </div>
  </div>`;
}

/* привязка аналитики игры: ползунок + кнопка цели */
function bindGameAn(hist, g, p) {
  const range = $("#gaRange");
  if (!range || !hist || !p || !premiumNow()) return;
  const cur = p.price_final_cents;
  const sym = curSym(p.currency);
  const fmt = (c) => fmtMoney(c, sym);
  const update = () => {
    const pct = parseInt(range.value, 10) || 15;
    const t = Math.round(cur * (1 - pct / 100));
    const sh = gameSharesBelow(hist, t);
    const lbl = $("#gaLabel"); if (lbl) lbl.textContent = "купить, если упадёт на " + pct + "%";
    const tgt = $("#gaTarget"); if (tgt) tgt.textContent = "≈ " + fmt(t);
    const pctEl = $("#gaPct"); if (pctEl) pctEl.textContent = Math.round(sh * 100) + "%";
    const fill = $("#gaFill");
    if (fill) {
      fill.style.width = (sh * 100) + "%";
      fill.classList.remove("hi", "mid", "lo");
      fill.classList.add(sh >= 0.5 ? "hi" : sh >= 0.15 ? "mid" : "lo");
    }
    const vd = $("#gaVerd");
    if (vd) {
      const k = Math.round(sh * hist.length);
      vd.textContent = sh >= 0.5
        ? "Частая зона: в " + k + " из " + hist.length + " наблюдений цена опускалась до неё — реалистичная цель."
        : sh >= 0.15
          ? "Умеренная зона: " + k + " раз из " + hist.length + " — бывает на распродажах, стоит подождать."
          : "Редкая зона: цена доходила лишь " + k + " раз из " + hist.length + " — глубокий вход, возможно, упустите.";
    }
    const w = $("#gaWatch"); if (w) w.dataset.ga = String(Math.round(t));
  };
  range.addEventListener("input", update);
  update();
  const w = $("#gaWatch");
  if (w) w.addEventListener("click", () => {
    const ti = $("#targetInput");
    if (!ti) { toast("Сначала задайте целевую цену в блоке выше", true); return; }
    ti.value = (parseInt(w.dataset.ga, 10) || 0) / 100;
    const save = $("#targetSave");
    if (save) save.click();
    else toast("Целевая цена обновлена", false);
  });
}

function renderSheet(g, history) {
  const p = g.price;
  const premium = state.session?.user?.premium;
  const body = $("#sheetBody");
  const isFree = p && p.is_free;
  let priceBlock;
  if (p && p.discount_percent > 0) {
    priceBlock = `<span class="sh-disc">−${p.discount_percent}%</span>
      <span class="sh-price-old">${esc(fmtMoney(p.price_original_cents, p.currency))}</span>
      <span class="sh-price-big">${esc(fmtMoney(p.price_final_cents, p.currency))}</span>`;
  } else {
    priceBlock = moneyHtml(p && p.price_final_cents, p && p.currency, isFree, "sh-price-big");
  }

  // исторический минимум среди известных точек
  const minPt = history && history.length ? history.reduce((a, b) => (b.f < a.f ? b : a)) : null;
  const isLow = p && minPt && p.price_final_cents <= minPt.f;

  let targetRow = "";
  if (premium) {
    const cur = g.target_cents || (p ? p.price_final_cents : null);
    targetRow = `
      <div class="sh-section">
        <h3>🎯 Целевая цена</h3>
        <div class="sh-target">
          <input type="number" id="targetInput" placeholder="до N ${curSym(p ? p.currency : "RUB")}" value="${cur != null ? Math.round(cur / 100) : ""}" inputmode="decimal">
          <button class="btn btn-primary btn-sm" id="targetSave">Сохранить</button>
          <button class="btn btn-ghost btn-sm" id="targetClear">Убрать</button>
        </div>
        <div class="sh-note">Когда цена упадёт до цели — событие появится в ленте (и ушло бы пушем в Telegram).</div>
      </div>`;
  } else {
    targetRow = `<div class="sh-section"><h3>🎯 Целевая цена</h3>
      <p class="sh-note">Фича Premium — следите, когда игра упадёт до нужной цены.</p></div>`;
  }

  const chartWrap = history && history.length > 1
    ? `<div class="sh-section">
        <h3>📉 История цены</h3>
        <div class="chart-wrap">${priceChart(history, {})}
          <div class="chart-readout" id="chartReadout">👆 Нажмите на зелёную точку — покажу дату и цену распродажи</div>
        </div>
        <div class="chart-legend"><span><i class="dot" style="background:#a1cd44"></i>распродажи (нажмите на точку)</span><span><i class="dot" style="background:#5aa7f5"></i>цена</span></div>
        <div class="sh-note">${history.length < 8 ? "Сгенерирована по типовым сезонным распродажам Steam (демо) + реальные наблюдения" : "Точки записываются при каждом изменении цены (аналог sale_snapshots)"}</div>
      </div>`
    : `<div class="sh-section"><h3>📉 История цены</h3><p class="sh-note">Наберётся точек после нескольких проверок — как sale_snapshots в боте.</p></div>`;

  // 🧭 аналитика «когда покупать» (Premium) — блок под графиком
  const anRow = p && history && history.length > 1
    ? `<div class="sh-section" style="padding-top:2px;padding-bottom:2px">${gameAnHtml(history, g, p)}</div>`
    : "";

  body.innerHTML = `
    <div class="sh-hero">
      ${g.header_image
        ? `<img class="sh-hero-img" src="${esc(g.header_image)}" alt="" data-ph="sh-hero-ph" onerror="__imgBroken(this,'🎮')">`
        : '<div class="sh-hero-ph">🎮</div>'}
    </div>
    <div class="sh-body">
      <div class="sh-title">${esc(g.name)}</div>
      <div class="sh-tags">
        <span class="pill ${g.on_sale ? "pill-accent" : ""}">${g.on_sale ? "🔥 в продаже" : "отслеживается"}</span>
        ${isLow ? '<span class="pill pill-premium">🏆 исторический минимум</span>' : ""}
        ${g.target_cents ? `<span class="pill pill-accent">🎯 цель ${esc(fmtMoney(g.target_cents, p ? p.currency : "RUB"))}</span>` : ""}
      </div>
      <div class="sh-price-row">${priceBlock}</div>
      <div class="sh-actions">
        <a class="btn btn-primary" target="_blank" rel="noopener" href="https://store.steampowered.com/app/${g.appid}/">🛒 Открыть в Steam</a>
        <button class="btn btn-danger btn-sm" id="sheetDelete">Удалить из списка</button>
      </div>
      ${targetRow}
      ${chartWrap}
      ${anRow}
    </div>`;

  $("#sheetDelete").addEventListener("click", async () => {
    await post("/api/games/remove", { appid: g.appid });
    closeSheet(); toast("Игра удалена"); await loadGames();
  });
  const cur = state.session && curSym(p ? p.currency : "RUB");
  const save = $("#targetSave");
  if (save) save.addEventListener("click", async () => {
    const v = parseFloat($("#targetInput").value);
    if (!v || v <= 0) { toast("Введите сумму цели", true); return; }
    await post(`/api/games/${g.appid}/target`, { cents: Math.round(v * 100) });
    toast("Целевая цена сохранена: до " + v + " " + cur);
    await loadGames(); renderSheet(await (await api("/api/games/" + g.appid)).game, await (await api("/api/games/" + g.appid)).history);
  });
  const clr = $("#targetClear");
  if (clr) clr.addEventListener("click", async () => {
    await post(`/api/games/${g.appid}/target`, { cents: null });
    toast("Цель убрана"); closeSheet();
  });
  try { bindGameAn(history, g, p); } catch (e) { console.error("bindGameAn:", e); }
  bindChart(history);
}

/* Клики/тапы по точкам распродаж на графике */
function bindChart(points) {
  const wrap = document.getElementById("chartReadout");
  const groups = Array.from(document.querySelectorAll("#priceChart g.pt"));
  if (!wrap || !groups.length) { if (wrap) wrap.classList.add("hidden"); return; }
  const readout = wrap;
  let active = null;
  const paint = (g) => {
    groups.forEach((gg) => gg.classList.remove("sel"));
    g.classList.add("sel");
    active = g;
    const p = points[+g.dataset.i];
    if (!p) return;
    readout.innerHTML = `<b>${fmtDate(p.t)}</b> — ${esc(fmtMoney(p.f, p.c))}` +
      (p.d ? ` <span style="color:var(--sale);font-weight:800">−${p.d}%</span>` : "") +
      '<span style="float:right;opacity:.6">распродажа</span>';
  };
  groups.forEach((g) => {
    g.addEventListener("click", (e) => { e.stopPropagation(); paint(g); });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); paint(g); }
    });
  });
  // по умолчанию подсветить самую свежую распродажную точку
  const saleIdx = points.map((p, i) => (p.d > 0 ? i : -1)).filter((i) => i >= 0).pop();
  const target = saleIdx != null ? groups.find((g) => +g.dataset.i === saleIdx) : null;
  if (target) paint(target); else if (readout) readout.classList.add("hidden");
}

/* ============================================================
   🔥 СДЕЛКИ
   ============================================================ */
/* Каталог распродаж «Глобально»: вместо 50 «непонятно выбранных» — весь листаемый
   список игр региона со скидкой (страницы по 100 через /api/deals/global?start=N). */
const catState = {
  items: [],        // уже загруженные строки каталога (без повторов по appid)
  ids: new Set(),   // appid загруженных
  next: 0,          // следующий offset (окна по 100)
  busy: false,
  done: false,
  err: null,
  emptyN: 0,        // подряд пустых страниц (лимит Steam)
  retryAt: 0,       // время следующей попытки после кулдауна
  retryT: null,
  info: null,       // {total, hidden, effective} — сводка первой страницы
  filters: { preset: "", q: "", sort: "" },  // preset: ""|max300|max600|max1200|week|topsell
  pendingApply: false,
};
let catObserver = null;

function catEff() {
  const info = catState.info || {};
  return info.effective || 0;
}

/* Фильтры каталога: только те, что реально поддерживает выдача Steam
   (проверено: term, maxprice «лесенкой», filter=weeklongdeals|globaltopsellers,
   sort_by=Price_ASC|Price_DESC). Клиент сам их не «рисует» — Steam их применяет. */
function catFilterParams() {
  const f = catState.filters || {};
  const p = {};
  if (f.preset === "week") p.filter = "weeklongdeals";
  else if (f.preset === "topsell") p.filter = "globaltopsellers";
  else if (f.preset === "max300") p.maxprice = 300;
  else if (f.preset === "max600") p.maxprice = 600;
  else if (f.preset === "max1200") p.maxprice = 1200;
  if (f.sort === "asc") p.sort_by = "Price_ASC";
  else if (f.sort === "desc") p.sort_by = "Price_DESC";
  const q = (f.q || "").trim();
  if (q) p.q = q;
  return p;
}
function catQueryString() {
  const p = catFilterParams();
  const parts = Object.keys(p).map((k) => k + "=" + encodeURIComponent(p[k]));
  return parts.length ? "&" + parts.join("&") : "";
}
function catFilterActiveText() {
  const p = catFilterParams();
  const bits = [];
  if (p.filter === "weeklongdeals") bits.push("недельные скидки");
  else if (p.filter === "globaltopsellers") bits.push("топ-продажи");
  if (p.maxprice) bits.push("до " + p.maxprice.toLocaleString("ru-RU") + " ₽");
  if (p.q) bits.push("«" + p.q + "»");
  if (p.sort_by === "Price_ASC") bits.push("дешевле");
  else if (p.sort_by === "Price_DESC") bits.push("дороже");
  return bits;
}
function catTotalMarkup() {
  const eff = catEff();
  const act = catFilterActiveText();
  const extra = act.length ? `<i>фильтр: ${esc(act.join(" · "))}</i>` : "";
  return eff
    ? `<b>${eff.toLocaleString("ru-RU")}</b> игр со скидкой по данным Steam — долистать можно все${extra}`
    : `<span class="muted">считаем число игр со скидкой…</span>${extra}`;
}

function catFootMsg(html, isErr) {
  const f = document.getElementById("catFoot");
  if (!f) return;
  f.innerHTML = html;
  f.className = "cat-foot" + (isErr ? " err" : "");
}

function catTrackedNow() {
  return new Set((state.games || []).map((g) => g.appid));
}

function catRow(it, tracked) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "cat-row" + (tracked ? " tracked" : "");
  const price = it.price_final_cents === 0
    ? `<span class="price-now free">Бесплатно</span>`
    : `<span class="strike">${esc(fmtMoney(it.price_original_cents, it.currency))}</span>` +
      `<span class="price-now">${esc(fmtMoney(it.price_final_cents, it.currency))}</span>`;
  b.innerHTML = `
    <span class="cat-art">${artTag(it.header_image, "cat-ph", "🎮")}</span>
    <span class="cat-mid">
      <span class="cat-name">${esc(it.name)}</span>
      <span class="cat-price"><span class="badge-sale">−${it.discount_percent}%</span>${price}</span>
    </span>
    <span class="cat-cta">${tracked ? "В списке ✓" : "＋ Следить"}</span>`;
  b.addEventListener("click", async () => {
    if (tracked) { openGameSheet(it.appid); return; }
    b.disabled = true;
    const cta = b.querySelector(".cat-cta");
    if (cta) cta.textContent = "…";
    try {
      const r = await post("/api/games/add", { appid: it.appid });
      if (r && r.ok) {
        toast("Добавлено в список");
        await loadGames();           // обновит «ваши скидки»/кошелёк/карусель
        b.classList.add("tracked");
        if (cta) cta.textContent = "В списке ✓";
      } else toast((r && r.message) || "Не добавить", true);
    } catch (e) { errToast(e); }
    b.disabled = false;
  });
  return b;
}

async function catLoadPage() {
  if (catState.busy || catState.done || catState.err) return;
  const list = document.getElementById("catList");
  if (!list) return;
  if (catState.retryAt && Date.now() < catState.retryAt) {
    // Steam притормозил: ждём до следующей попытки, не дёргая сервер
    clearTimeout(catState.retryT);
    catState.retryT = setTimeout(() => { if (!catState.done && !catState.err) catLoadPage(); }, catState.retryAt - Date.now() + 200);
    return;
  }
  catState.busy = true;
  const loaded = catState.items.length;
  catFootMsg(`<span class="cat-spin"></span> подгружаем следующую сотню… показано ${loaded.toLocaleString("ru-RU")}${catEff() ? " из " + catEff().toLocaleString("ru-RU") : ""}`);
  try {
    const r = await api("/api/deals/global?start=" + catState.next + catQueryString(), {}, 30000);
    catState.info = { total: r.total, hidden: r.hidden, effective: r.effective, region: r.region };
    const nl = document.getElementById("catNumline");
    if (nl) nl.innerHTML = catTotalMarkup();
    const tracked = catTrackedNow();
    let got = 0;
    (r.items || []).forEach((it) => {
      if (catState.ids.has(it.appid)) return;
      catState.ids.add(it.appid);
      catState.items.push(it);
      list.appendChild(catRow(it, tracked.has(it.appid)));
      got++;
    });
    if (r.more === false) {
      catState.done = true;
      const uniq = catState.items.length;
      if (!uniq) {
        catFootMsg("по этому фильтру Steam ничего не нашёл — поменяйте запрос или сбросьте фильтр", true);
      } else {
        catFootMsg(`✓ долистали до конца списка Steam — показано <b>${uniq.toLocaleString("ru-RU")}</b> уникальных игр` +
          (catEff() && catEff() !== uniq ? ` (Steam числит ${catEff().toLocaleString("ru-RU")}, часть дублируется при пересортировке)` : ""));
      }
    } else if (got === 0) {
      // пустая страница при more=true — временный лимит Steam, не конец списка
      const backoff = Math.min(30000, (catState.emptyN || 0) * 9000 + 6000);
      catState.emptyN = (catState.emptyN || 0) + 1;
      catState.retryAt = Date.now() + backoff;
      catFootMsg(`Steam притормозил листание (сетевой лимит) — сам продолжу через ${Math.round(backoff / 1000)} с…`);
      clearTimeout(catState.retryT);
      catState.retryT = setTimeout(() => {
        if (!catState.done && !catState.err && state.dealScope === "global") {
          catState.retryAt = 0;
          catLoadPage();
        }
      }, backoff + 300);
    } else {
      catState.emptyN = 0;
      catState.retryAt = 0;
      clearTimeout(catState.retryT);
      catState.next += 100;   // окна Steam ровно по 100 позиций
      catFootMsg(`показано <b>${catState.items.length.toLocaleString("ru-RU")}</b>` +
        `${catEff() ? " из " + catEff().toLocaleString("ru-RU") : ""} игр — листайте вниз, догружу ещё`);
    }
  } catch (e) {
    catState.err = e;
    catFootMsg(`не загрузилось: ${esc((e && e.message) || "сеть")} · ` +
      `<button class="btn btn-ghost btn-sm" id="catRetry" type="button">повторить</button>`, true);
    const rb = document.getElementById("catRetry");
    if (rb) rb.addEventListener("click", () => { catState.err = null; catLoadPage(); });
  } finally {
    catState.busy = false;
  }
  if (catState.pendingApply && !catState.done && !catState.err) {
    catState.pendingApply = false;
    catApplyFilters();
  }
}

function catResetObserver() {
  if (catObserver) { try { catObserver.disconnect(); } catch (e) {} catObserver = null; }
  const sent = document.getElementById("catSent");
  if (!sent || typeof IntersectionObserver === "undefined") return;
  catObserver = new IntersectionObserver((entries) => {
    if (catState.done || catState.busy || catState.err) return;
    if (entries.some((en) => en.isIntersecting)) catLoadPage();
  }, { rootMargin: "0px 0px 420px 0px" });
  catObserver.observe(sent);
}

function buildCatalogBlock() {
  const region = String((state.deals && state.deals.region) || "ru").toLowerCase();
  const priceChips = region === "ru"
    ? `<button class="fchip" type="button" data-preset="max300">до 300 ₽</button>
       <button class="fchip" type="button" data-preset="max600">до 600 ₽</button>
       <button class="fchip" type="button" data-preset="max1200">до 1 200 ₽</button>`
    : "";
  const sec = document.createElement("section");
  sec.className = "cat-sec anim-in";
  sec.innerHTML = `
    <div class="cat-head">
      <div>
        <div class="cat-title">🗂 Весь каталог распродаж Steam</div>
        <div class="cat-sub">только игры — без DLC и софта · листаем по 100 до конца · фильтры применяет сам Steam</div>
      </div>
    </div>
    <div class="cat-numline" id="catNumline">${catTotalMarkup()}</div>
    <div class="cat-tools">
      <input id="catQ" class="cat-input" type="search" placeholder="найти игру на распродаже…" autocomplete="off" autocapitalize="off" spellcheck="false">
      <div class="cat-chips" id="catChips">
        <button class="fchip on" type="button" data-preset="">Всё</button>
        ${priceChips}
        <button class="fchip" type="button" data-preset="week">недельные скидки</button>
        <button class="fchip" type="button" data-preset="topsell">топ-продажи</button>
      </div>
      <div class="cat-sortrow">
        <span class="cat-sortlab">сортировка</span>
        <button class="fchip on" type="button" data-sort="">как Steam</button>
        <button class="fchip" type="button" data-sort="asc">дешевле</button>
        <button class="fchip" type="button" data-sort="desc">дороже</button>
      </div>
    </div>
    <div id="catList"></div>
    <div class="cat-foot" id="catFoot"></div>
    <div id="catSent"></div>`;
  bindCatalogTools(sec);
  return sec;
}

function catSyncFilterUI() {
  const f = catState.filters || {};
  const q = document.getElementById("catQ");
  if (q) q.value = f.q || "";
  document.querySelectorAll("#catChips .fchip[data-preset]").forEach((b) => {
    b.classList.toggle("on", b.dataset.preset === (f.preset || ""));
  });
  document.querySelectorAll(".cat-sortrow .fchip[data-sort]").forEach((b) => {
    b.classList.toggle("on", b.dataset.sort === (f.sort || ""));
  });
}

function bindCatalogTools(sec) {
  const q = sec.querySelector("#catQ");
  const chips = Array.from(sec.querySelectorAll("#catChips .fchip"));
  const sortBtns = Array.from(sec.querySelectorAll(".cat-sortrow .fchip"));
  let deb = null;
  const fire = () => catApplyFilters();
  chips.forEach((b) => b.addEventListener("click", () => {
    catState.filters.preset = b.dataset.preset || "";
    catSyncFilterUI();
    fire();
  }));
  sortBtns.forEach((b) => b.addEventListener("click", () => {
    catState.filters.sort = b.dataset.sort || "";
    catSyncFilterUI();
    fire();
  }));
  if (q) {
    q.addEventListener("input", () => {
      clearTimeout(deb);
      const v = q.value;
      deb = setTimeout(() => {
        const nv = (catState.filters.q || "").trim();
        if (v.trim() !== nv) { catState.filters.q = v.trim(); fire(); }
      }, 450);
    });
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        clearTimeout(deb);
        catState.filters.q = q.value.trim();
        fire();
        q.blur();
      } else if (e.key === "Escape") {
        q.value = "";
        clearTimeout(deb);
        if (catState.filters.q) { catState.filters.q = ""; fire(); }
      }
    });
  }
}

function catApplyFilters() {
  if (catState.busy) { catState.pendingApply = true; return; }
  catState.items = [];
  catState.ids = new Set();
  catState.next = 0;
  catState.done = false;
  catState.err = null;
  catState.emptyN = 0;
  catState.retryAt = 0;
  catState.info = null;
  if (catState.retryT) { clearTimeout(catState.retryT); catState.retryT = null; }
  const list = document.getElementById("catList");
  if (list) list.innerHTML = "";
  const nl = document.getElementById("catNumline");
  if (nl) nl.innerHTML = catTotalMarkup();
  catFootMsg(`<span class="cat-spin"></span> применяю фильтр…`);
  const box = document.getElementById("dealsGlobal");
  if (!box || box.classList.contains("hidden") || state.dealScope !== "global") return;
  catResetObserver();
  catLoadPage();
}

function renderCatalogLoaded() {
  const list = document.getElementById("catList");
  if (!list) return;
  const tracked = catTrackedNow();
  catState.items.forEach((it) => list.appendChild(catRow(it, tracked.has(it.appid))));
  const nl = document.getElementById("catNumline");
  if (nl) nl.innerHTML = catTotalMarkup();
  if (catState.done) {
    const uniq = catState.items.length;
    catFootMsg(`✓ долистали до конца списка Steam — показано <b>${uniq.toLocaleString("ru-RU")}</b> уникальных игр`);
  } else if (!catState.items.length) {
    catFootMsg("каталог ещё не грузился");
  } else {
    catFootMsg(`показано <b>${catState.items.length.toLocaleString("ru-RU")}</b>` +
      `${catEff() ? " из " + catEff().toLocaleString("ru-RU") : ""} игр — листайте вниз, догружу ещё`);
  }
}

function buildCuratedBlock(d) {
  const items = (d && d.global) || [];
  if (!items.length) return null;
  const sec = document.createElement("div");
  const eff = (d.pulse && d.pulse.total) || 0;
  const tag = String((d && d.region) || "").toUpperCase();
  sec.className = "cur-sec anim-in";
  sec.innerHTML = `
    <div class="cur-head">
      <div>
        <div class="cur-title">🏆 Топ продаж Steam со скидкой</div>
        <div class="cur-sub">живая подборка Steam «глобальные топ-продажи»${eff ? ` — лишь верхушка каталога из ${eff.toLocaleString("ru-RU")} игр` : ""}${tag ? ` · регион ${tag}` : ""}</div>
      </div>
    </div>
    <div class="disc-grid"></div>`;
  const grid = sec.querySelector(".disc-grid");
  if (typeof state.globalShow !== "number") state.globalShow = 12;
  const renderTo = (n) => {
    n = Math.min(items.length, Math.max(12, n));
    const from = grid.children.length;
    for (let i = from; i < n; i++) grid.appendChild(dealCardNode(items[i], Math.min(i, 8) * 45));
    state.globalShow = n;
  };
  renderTo(state.globalShow);
  const row = document.createElement("div");
  row.className = "deal-more-row";
  const more = document.createElement("button");
  more.type = "button";
  more.className = "btn btn-ghost btn-sm";
  row.appendChild(more);
  const refresh = () => {
    const rem = items.length - grid.children.length;
    if (rem > 0) { more.textContent = "Показать ещё " + rem + (rem === 1 ? " игру" : " игр"); more.disabled = false; }
    else { more.textContent = "это всё из топ-подборки Steam ✓"; more.disabled = true; }
  };
  more.addEventListener("click", () => {
    renderTo(grid.children.length + 10);
    refresh();
    more.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  sec.appendChild(row);
  refresh();
  return sec;
}

function dealsGlobalCaption(d) {
  const p = (d && d.pulse) || {};
  const eff = p.total || 0;
  const n = ((d && d.global) || []).length;
  const tag = String((d && d.region) || "").toUpperCase();
  if (eff) return `каталог Steam ${tag}: ${eff.toLocaleString("ru-RU")} игр со скидкой · живой фид — топ-${n}`;
  return n ? `в Steam сейчас ${n} сделок со скидкой` : "живые скидки Steam сейчас";
}

async function pulseRefresh() {
  try {
    const d = await api("/api/deals");
    if (!state.deals) { state.deals = d; return; }
    const a = state.deals.pulse || {}, b = d.pulse || {};
    const sigA = [a.count, a.total, a.max && a.max.name, a.cheapest && a.cheapest.name].join("|");
    const sigB = [b.count, b.total, b.max && b.max.name, b.cheapest && b.cheapest.name].join("|");
    state.deals = d;
    if (sigA === sigB) return;   // пульс не изменился — список не трогаем
    const box = document.getElementById("dealsGlobal");
    const old = box && box.querySelector(".pulse-card");
    const node = steamPulseNode(d);
    if (box) { if (old) old.remove(); if (node) box.insertBefore(node, box.firstChild); }
    if (node) {
      const psNum = node.querySelector(".ps-num");
      const target = (b.total || b.count) || 0;
      if (psNum) requestAnimationFrame(() => countUp(psNum, target));
    }
    if (state.dealScope === "global") {
      $("#dealsSub").textContent = dealsGlobalCaption(d);
    }
  } catch (e) { /* тихий фоновый пульс */ }
}

async function loadDeals() {
  try { state.deals = await api("/api/deals"); } catch (e) { errToast(e); state.deals = { tracked_sales: [], global: [], seasonal: false, threshold: 0 }; }
  const d = state.deals;
  const seg = state.dealScope;
  $$("#dealsSeg .seg").forEach((b) => b.classList.toggle("active", b.dataset.seg === seg));
  $("#dealsMine").classList.toggle("hidden", seg !== "mine");
  $("#dealsGlobal").classList.toggle("hidden", seg !== "global");
  $("#seasonalBanner").classList.toggle("hidden", !d.seasonal);
  // сезонный оверлей на весь интерфейс, когда Steam реально ушёл в большую распродажу
  document.body.classList.toggle("seasonal", !!d.seasonal);

  const th = d.threshold || 0;
  if (seg === "global") {
    $("#dealsSub").textContent = dealsGlobalCaption(d);
  } else {
    $("#dealsSub").textContent = th > 0 ? "скидки от " + th + "% и выше · сейчас" : "живые скидки · сейчас";
  }

  const mine = $("#dealsMine");
  mine.innerHTML = "";
  if (seg === "mine") {
    if (!d.tracked_sales.length) {
      mine.innerHTML = `<div class="deal-empty">Сейчас в вашем списке ничего не горит.<br>
        Скидки появятся здесь и в <b>Ленте</b>, как только начнутся распродажи.
        Гляньте вкладку «Глобально» 👉</div>`;
    } else {
      d.tracked_sales.forEach((s) => mine.appendChild(discRow(s)));
    }
  }
  renderGlobal(seg === "global");
}

function discRow(s) {
  const li = document.createElement("div");
  li.className = "card";
  li.style.cssText = "display:flex;gap:14px;align-items:center;margin:10px 0";
  li.innerHTML = `
    <div class="gc-art" style="width:110px;height:52px">${artTag(s.header_image, "gc-ph")}</div>
    <div style="flex:1;min-width:0">
      <div class="game-name" style="font-weight:800;font-size:14.5px">${esc(s.name)}</div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
        <span class="badge-sale">−${s.discount_percent}%</span>
        <span class="strike">${esc(fmtMoney(s.price_original_cents, s.currency))}</span>
        <span class="price-now">${esc(fmtMoney(s.price_final_cents, s.currency))}</span>
      </div>
    </div>
    <button class="btn btn-ghost btn-sm" data-act="open">⋯</button>`;
  li.querySelector("button").addEventListener("click", () => {
    if (state.games && state.games.find((g) => g.appid === s.appid)) openGameSheet(s.appid);
    else addFromSearch(s.appid, li.querySelector("button"));
  });
  return li;
}

function dealCardNode(it, staggerMs) {
  const card = document.createElement("div");
  card.className = "disc-card anim-in";
  if (staggerMs) card.style.animationDelay = staggerMs + "ms";
  const tracked = it.tracked;
  const timeLeft = it.discount_expiration
    ? "до " + new Date(it.discount_expiration * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })
    : "";
  card.innerHTML = `
    <div class="dc-imgwrap">
      ${artTag(it.header_image, "dc-ph")}
      <span class="dc-off">−${it.discount_percent}%</span>
    </div>
    <div class="dc-body">
      <div class="dc-name">${esc(it.name)}</div>
      <div class="dc-price">
        <span class="strike">${esc(fmtMoney(it.price_original_cents, it.currency))}</span>
        <span style="margin-left:6px" class="price-now ${it.is_free ? "free" : ""}">${it.is_free ? "Бесплатно" : esc(fmtMoney(it.price_final_cents, it.currency))}</span>
      </div>
      <div class="dc-time">${timeLeft}</div>
    </div>`;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn dc-add btn-sm " + (tracked ? "btn-ghost" : "btn-primary");
  btn.textContent = tracked ? "В списке ✓" : "＋ Следить";
  const tryAddAndOpen = async () => {
    if (tracked) { openGameSheet(it.appid); return; }
    btn.disabled = true;
    try {
      const r = await post("/api/games/add", { appid: it.appid });
      if (r.ok) {
        it.tracked = true;
        btn.textContent = "В списке ✓";
        btn.classList.remove("btn-primary"); btn.classList.add("btn-ghost");
        toast("Добавлено в список");
        await loadGames();
        openGameSheet(it.appid);
      } else toast(r.message || "Не добавить", true);
    } catch (e) { errToast(e); }
    btn.disabled = false;
  };
  if (!tracked) btn.addEventListener("click", tryAddAndOpen);
  card.querySelector(".dc-imgwrap").addEventListener("click", tryAddAndOpen);
  card.appendChild(btn);
  return card;
}

function steamPulseNode(d) {
  const pulse = d && d.pulse;
  if (!pulse || !pulse.count) return null;
  const node = document.createElement("div");
  node.className = "pulse-card anim-in";
  const fmtAge = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    return s < 60 ? "только что" : Math.round(s / 60) + " мин назад";
  };
  const max = pulse.max;
  const cheap = pulse.cheapest;
  const regionTag = String(d.region || "").toUpperCase();
  const total = pulse.total || 0;            // игр со скидкой (только игры), все листаются
  const mkMini = (item, label) => {
    if (!item) return "";
    const free = item.price_final_cents === 0;
    return `
    <div class="pulse-mini">
      <div class="pulse-mini-art">${artTag(item.header_image, "pulse-ph", "🎮")}</div>
      <div class="pulse-mini-body">
        <div class="pulse-mini-lab">${label}</div>
        <div class="pulse-mini-name">${esc(item.name)}</div>
        <div class="pulse-mini-price">
          <span class="badge-sale">−${item.discount_percent}%</span>
          ${free ? '<span class="price-now free">Бесплатно</span>' : `<span class="price-now">${esc(fmtMoney(item.price_final_cents, item.currency || d.region))}</span>`}
        </div>
      </div>
    </div>`;
  };
  node.innerHTML = `
    <div class="pulse-head">
      <span class="pulse-title">⚡ Прямо сейчас в Steam</span>
      <span class="pulse-live"><span class="pulse-dot"></span>обновлено ${fmtAge(pulse.ts)}</span>
    </div>
    <div class="pulse-top">
      <div class="pulse-count">
        <span class="ps-num" data-v="0">0</span>
        <span class="ps-lab">${total
          ? `игр со скидкой в Steam ${regionTag} — все листаются ниже`
          : `сделок со скидкой прямо сейчас`}</span>
      </div>
      <div class="pulse-side">
        ${total ? `<span class="pulse-chip">топ-${pulse.count} в фиде</span>` : ""}
        ${max ? `<span class="pulse-chip hot">до −${max.discount_percent}%</span>` : ""}
      </div>
    </div>
    ${total ? `<div class="pulse-note">Только игры — без DLC и софта. Сразу ниже — топ-продажи из живого фида, ещё ниже — весь каталог из ${total.toLocaleString("ru-RU")}: листается до конца.</div>` : ""}
    ${pulse.stale ? `<div class="pulse-stale">⚠ Steam сейчас не отвечает — показан последний удачный снимок (${fmtAge(pulse.ts)}). Данные обновятся сами.</div>` : ""}
    <div class="pulse-minis">
      ${max ? mkMini(max, "Максимальная скидка в фиде") : ""}
      ${cheap ? mkMini(cheap, "Самая дешёвая сейчас") : ""}
    </div>`;
  return node;
}

function renderGlobal(visible) {
  const box = $("#dealsGlobal");
  box.innerHTML = "";
  const d = state.deals || { global: [], pulse: null, region: "ru", tracked_sales: [], seasonal: false, threshold: 0 };
  // «пульс» сверху — масштаб каталога и пара хитов фида
  const pulse = steamPulseNode(d);
  if (pulse) box.appendChild(pulse);
  if (pulse) {
    const psNum = pulse.querySelector(".ps-num");
    const target = (d.pulse && (d.pulse.total || d.pulse.count)) || 0;
    if (psNum) requestAnimationFrame(() => countUp(psNum, target));
  }
  // сразу после пульса — курируемая витрина «топ-продажи со скидкой»
  const cur = buildCuratedBlock(d);
  if (cur) box.appendChild(cur);
  // внизу — главный блок: весь листаемый каталог распродаж Steam
  box.appendChild(buildCatalogBlock());
  renderCatalogLoaded();
  catSyncFilterUI();
  catResetObserver();
  // первая сотня каталога подгружается сразу, дальше — по мере прокрутки (sentinel)
  if (visible && !catState.items.length && !catState.done && !catState.err) catLoadPage();
}
/* ============================================================
   📈 РЫНОК
   ============================================================ */
function renderMarketGate() {
  const gate = $("#marketGate");
  const premium = state.session?.user?.premium;
  if (premium) { gate.classList.add("hidden"); gate.innerHTML = ""; return; }
  gate.classList.remove("hidden");
  gate.innerHTML = `
    <div class="pg-emoji">📈</div>
    <div class="pg-title">Market-алерты — Premium</div>
    <div class="pg-text">Следите за скинами, кейсами и карточками Steam Market. Цена пересекла цель — пришлём уведомление и снимем алерт. На бесплатном тарифе эта функция закрыта (как в боте).</div>
    <button class="btn btn-primary" id="gateTrial">🎁 Попробовать бесплатно (триал)</button>
    <button class="btn btn-ghost" id="gateBuy" style="margin-left:8px">💎 Оформить Premium</button>`;
  gate.querySelector("#gateTrial").addEventListener("click", () => activateTrial());
  gate.querySelector("#gateBuy").addEventListener("click", () => buyPremium());
}

/* ============================================================
   📈 РЫНОК — три блока: обзор / ценовой компас / живые алерты
   ============================================================ */
const MARKET_OV_GAMES = [["cs2", "CS2"], ["dota2", "Dota 2"], ["tf2", "TF2"], ["rust", "Rust"]];

function fmtPct(v) { return (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }

/* ---- ценовой компас: диапазон истории и вердикт ---- */
function marketCompass(pts, cur) {
  if (!pts || pts.length < 2 || cur == null) return null;
  let min = Infinity, max = -Infinity, sum = 0;
  for (const p of pts) { const f = p.f; if (f < min) min = f; if (f > max) max = f; sum += f; }
  if (!isFinite(min) || min <= 0) return null;
  const avg = sum / pts.length;
  const range = max - min || 1;
  const pos = Math.min(1, Math.max(0, (cur - min) / range));
  let verdict, cls;
  if (pos <= 0.25) { verdict = "дёшево"; cls = "v-cheap"; }
  else if (pos >= 0.75) { verdict = "дорого"; cls = "v-pricey"; }
  else { verdict = "норма"; cls = "v-ok"; }
  return { min, max, avg, n: pts.length, pos, verdict, cls, cur };
}

/* ---- «тренд за день»: текущая цена против точки ~сутки назад ---- */
function marketDayDelta(pts, cur) {
  if (!pts || pts.length < 2 || cur == null) return null;
  const nowMs = Date.now(), day = 24 * 3600e3;
  let best = null, bestDiff = Infinity;
  for (const p of pts) {
    const t = new Date(p.t).getTime();
    if (!(t > 0)) continue;
    const age = nowMs - t;
    if (age < 10 * 3600e3 || age > 100 * 3600e3) continue;
    const diff = Math.abs(age - day);
    if (diff < bestDiff) { bestDiff = diff; best = { age, p }; }
  }
  if (!best || !best.p.f) return null;
  return { pct: (cur - best.p.f) / best.p.f * 100, ageH: Math.round(best.age / 3600e3), demo: !!best.p.demo };
}

function mkDeltaPill(delta, ttl) {
  if (!delta) return `<span class="ov-dl t-na">тренд —</span>`;
  const up = delta.pct >= 0;
  const cls = delta.pct > 0.05 ? "t-up" : (delta.pct < -0.05 ? "t-down" : "t-ok");
  const age = ttl || (delta.ageH > 40 ? ("~" + delta.ageH + " ч") : "сутки");
  return `<span class="ov-dl ${cls}">${up ? "▲" : "▼"} ${fmtPct(Math.abs(delta.pct))} / ${age}${delta.demo ? "*" : ""}</span>`;
}

/* ---- HTML ценового компаса (вставляется в результат поиска) ---- */
function mkCompassHtml(pts, cur, sym, histDemo) {
  const c = marketCompass(pts, cur);
  if (!c) return "";
  const pctPos = Math.round(c.pos * 100);
  const minL = esc(fmtMoney(Math.round(c.min), sym));
  const avgL = esc(fmtMoney(Math.round(c.avg), sym));
  const maxL = esc(fmtMoney(Math.round(c.max), sym));
  const curL = esc(fmtMoney(c.cur, sym));
  const tip = c.verdict === "дёшево"
    ? "Цена у нижней границы своей истории — исторически выгодная точка для покупки."
    : c.verdict === "дорого"
      ? "Цена у верхней границы — обычно выгоднее подождать отката к средней."
      : "Цена в среднем диапазоне своей истории — нейтрально.";
  const demoNote = histDemo
    ? `<div class="m-hist-note">* диапазон построен по демо-реконструкции: реальные точки копятся при каждой проверке цены.</div>`
    : "";
  return `
  <div class="mk-cmp anim-in">
    <div class="mk-cmp-top">
      <b>Ценовой компас</b>
      <span class="mk-cmp-pill ${c.cls}">${c.verdict}</span>
    </div>
    <div class="mk-cmp-stats">
      <span><i>сейчас</i> <b>${curL}</b></span>
      <span><i>минимум</i> <b>${minL}</b></span>
      <span><i>средняя</i> <b>${avgL}</b></span>
      <span><i>максимум</i> <b>${maxL}</b></span>
    </div>
    <div class="mk-cmp-bar">
      <div class="mk-cmp-track">
        <div class="mk-cmp-marker" style="left:${Math.max(2, Math.min(98, pctPos))}%"></div>
      </div>
      <div class="mk-cmp-scale"><span>${minL}</span><span class="mk-cmp-cur">${curL} · сейчас</span><span>${maxL}</span></div>
    </div>
    <div class="mk-cmp-tip">${tip} Наблюдений в истории: ${c.n}.</div>
    ${demoNote}
  </div>`;
}

/* ---- 🎯 Sniper-анализ (Premium): шанс захода в зону + мгновенный алерт ---- */
function premiumNow() { return !!(state.session && state.session.user && state.session.user.premium); }

function sniperChance(hist, targetCents) {
  if (!hist || hist.length < 2 || targetCents == null) return null;
  let below = 0;
  for (const p of hist) { if (p.f <= targetCents) below++; }
  return Math.round(below / hist.length * 100);
}

function mkSniperHtml(hist, cur, sym, histDemo) {
  if (cur == null || !hist || hist.length < 2) return "";
  if (!premiumNow()) {
    return `<div class="sn sn-locked">
      <div class="sn-head"><b>🎯 Sniper-анализ</b><span class="sn-pill prem">Premium</span></div>
      <p class="sn-txt">Шанс «цена дойдёт до нужной зоны» по истории, %-алерты и зона входа — это для трейдеров, за которыми идёт Premium.</p>
    </div>`;
  }
  const pct0 = 10;
  const target = Math.round(cur * (1 - pct0 / 100));
  const chance = sniperChance(hist, target);
  return `
  <div class="sn anim-in">
    <div class="sn-head">
      <b>🎯 Sniper-анализ · зона входа</b>
      <span class="sn-pill on">Premium</span>
    </div>
    <div class="sn-row">
      <input type="range" id="snRange" min="2" max="40" step="1" value="${pct0}" aria-label="глубина зоны, %">
      <div class="sn-labels">
        <span id="snLabel">−${pct0}% от текущей</span>
        <span id="snTarget" class="sn-target">цель ≈ ${esc(fmtMoney(target, sym))}</span>
      </div>
    </div>
    <div class="sn-chance">
      <div class="sn-chance-top"><span>шанс встретить цену ≤ цели</span><b id="snPct">${chance != null ? chance + "%" : "—"}</b></div>
      <div class="sn-bar"><div class="sn-bar-fill ${chance == null ? "na" : chance >= 60 ? "hi" : chance >= 30 ? "mid" : "lo"}" id="snBarFill" style="width:${chance != null ? chance : 0}%"></div></div>
      <div class="sn-scale"><span>0%</span><span>50%</span><span>100%</span></div>
      <p class="sn-verdict" id="snVerdict">${esc(sniperVerdict(chance))}</p>
    </div>
    <div class="sn-actions">
      <button class="btn btn-primary btn-sm" id="snWatch" type="button">＋ Следить ниже этой цены</button>
      <span class="sn-demo">оценка по ${hist.length} наблюдениям${histDemo ? " (часть — демо-реконструкция)" : " реальной истории"}</span>
    </div>
  </div>`;
}

function sniperVerdict(chance) {
  if (chance == null) return "Истории пока мало — включите наблюдение за предметом, и оценка появится.";
  if (chance >= 60) return "Частая зона: цена регулярно сюда опускается — реалистичная цель для заявки.";
  if (chance >= 30) return "Умеренная зона: бывает не каждый день — разумно подождать и не гнаться.";
  return "Редкая зона: цена почти не доходила — либо глубокий вход, либо «грааль». Риск выше, цель можно ставить аккуратнее.";
}

function bindSniper(hist, cur, sym, hash, appid) {
  if (!premiumNow()) return;
  const range = $("#snRange");
  if (!range || !hist || cur == null) return;
  const update = () => {
    const pct = parseInt(range.value, 10) || 10;
    const target = Math.round(cur * (1 - pct / 100));
    const chance = sniperChance(hist, target);
    const lbl = $("#snLabel"); if (lbl) lbl.textContent = "−" + pct + "% от текущей";
    const tgt = $("#snTarget"); if (tgt) tgt.textContent = "цель ≈ " + fmtMoney(target, sym);
    const pctEl = $("#snPct"); if (pctEl) pctEl.textContent = chance != null ? chance + "%" : "—";
    const fill = $("#snBarFill");
    if (fill) {
      fill.style.width = (chance != null ? chance : 0) + "%";
      fill.classList.remove("na", "hi", "mid", "lo");
      fill.classList.add(chance == null ? "na" : (chance >= 60 ? "hi" : chance >= 30 ? "mid" : "lo"));
    }
    const v = $("#snVerdict"); if (v) v.textContent = sniperVerdict(chance);
    const w = $("#snWatch");
    if (w) { w.dataset.price = String(Math.round(target)); }
  };
  range.addEventListener("input", update);
  update();
  const watch = $("#snWatch");
  if (watch) {
    watch.addEventListener("click", () => {
      if (!state.marketPending) { toast("Сначала найдите предмет", true); return; }
      // режим «по цене», направление вниз, сумма = зона входа — и сразу ставим
      state.marketGoalMode = "price";
      $$("#goalSeg .seg").forEach((x) => x.classList.toggle("active", x.dataset.mode === "price"));
      $$("#dirSeg .seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === "below"));
      const amt = $("#alertAmount");
      amt.value = (parseInt(watch.dataset.price, 10) || cur) / 100;
      marketGoalHint();
      createMarketAlert();
    });
  }
}

/* ============================================================
   🗺 Обзор рынка Steam
   ============================================================ */
async function marketOvLoad(game, force) {
  const g = game || state.marketOvGame || "cs2";
  state.marketOvGame = g;
  if (state.marketOvLoading) return;
  state.marketOvLoading = true;
  marketOvTabsSync(g);
  const box = $("#marketOvRows");
  const refreshBtn = $("#marketOvRefresh");
  if (refreshBtn) refreshBtn.disabled = true;
  const hadRows = !!(box.querySelector(".ov-row"));
  const setMsg = (html, cls) => {
    box.querySelectorAll(".ov-pause").forEach((n) => n.remove());
    if (hadRows) box.insertAdjacentHTML("afterbegin", `<div class="pulse-stale ov-pause">${html}</div>`);
    else box.innerHTML = `<p class="search-status ${cls || ""}">${html}</p>`;
  };
  if (!hadRows) box.innerHTML = `<p class="search-status">Смотрю топ предметов в Steam Market…</p>`;
  else box.classList.add("ov-refreshing");
  let d = null;
  try {
    d = await api("/api/market/overview?game=" + encodeURIComponent(g) + (force ? "&force=1" : ""));
  } catch (e) {
    setMsg(esc(e.message || "Обзор не загрузился"), "no-ico");
  }
  if (refreshBtn) refreshBtn.disabled = false;
  state.marketOvLoading = false;
  box.classList.remove("ov-refreshing");
  if (!d || !d.ok) {
    state.marketOvPauseUntil = d && d.retry_in ? Date.now() + d.retry_in * 1000 : Date.now() + 30000;
    const ri = d && d.retry_in ? "~" + Math.ceil(d.retry_in / 60) + " мин" : "через минуту";
    setMsg(`⚠ ${esc((d && d.message) || "Steam Market не отвечает")} · обновлю сам ${ri}.`, "no-ico");
    return;
  }
  state.marketOvPauseUntil = 0;
  state.marketOv = d;
  const ovNote = document.querySelector("#marketOvBlock .ov-note");
  if (ovNote) ovNote.textContent = d.fallback
    ? "Steam временно не отдаёт «топ по лотам» — показаны популярные предметы (резервная витрина). Цены и объём живые из Steam."
    : "Топ-10 по числу активных лотов на продаже. Цена «купить сейчас», пересчёт в валюту региона по курсу Steam. Клик по предмету — ценовой компас и алерт.";
  try {
    marketOvRender();
  } catch (e) {
    console.error("Обзор не отрисовался:", e);
    setMsg(esc(e.message || "Не удалось отрисовать обзор"), "no-ico");
  }
}

function marketOvTabsSync(active) {
  const wrap = $("#marketOvTabs");
  if (!wrap) return;
  if (!wrap.children.length) {
    MARKET_OV_GAMES.forEach(([key, label]) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ov-tab" + (key === active ? " active" : "");
      b.dataset.ov = key; b.textContent = label;
      b.addEventListener("click", () => { state.marketOvGame = key; marketOvLoad(key, false); });
      wrap.appendChild(b);
    });
  } else {
    $$("#marketOvTabs .ov-tab").forEach((b) => b.classList.toggle("active", b.dataset.ov === active));
  }
}

function marketOvRender() {
  const d = state.marketOv;
  const box = $("#marketOvRows");
  if (!d || !d.rows) return;
  const sym = d.cur || (d.fx_rate != null ? "RUB" : "USD");
  const rows = d.rows.map((r) => marketOvRowHtml(r, sym)).join("");
  const stale = d.stale && !d.fallback
    ? `<div class="pulse-stale">⚠ Steam Market сейчас не отвечает — показан последний снимок обзора.</div>` : "";
  const fx = d.fx_stale
    ? `<div class="search-meta" style="padding-top:6px">Курс пересчёта — из последнего известного (Steam не ответил сейчас).</div>` : "";
  const ts = d.ts ? " · " + fmtDate(d.ts) : "";
  const cap = d.fallback
    ? `🔌 Резервная витрина: Steam ограничил «топ по лотам» — ниже популярные предметы с живыми ценами Steam (объём за 24 ч). Вернётся к полному топу автоматически.`
    : `Топ-10 по активным лотам${ts} · цены «купить сейчас» в ${sym}${d.stale ? "" : " · * тренд по демо-истории"}.`;
  box.innerHTML = rows + stale + fx +
    `<div class="search-meta ov-meta">${cap}</div>`;
  $$("#marketOvRows .ov-row").forEach((el) => el.addEventListener("click", () => marketPickRow(el.dataset)));
  const wave = $("#marketOvWave");
  if (wave) {
    if (d.fallback) { wave.classList.add("hidden"); wave.innerHTML = ""; }
    else { wave.innerHTML = marketWaveHtml(d.rows); wave.classList.remove("hidden"); }
  }
}

/* «Живая волна» обзора: сколько позиций дорожает/дешевеет и топ движений */
function marketWaveHtml(rows) {
  let up = 0, down = 0;
  const movers = [];
  rows.forEach((r) => {
    const cur = r.price_cents != null ? r.price_cents : (r.usd_cents != null ? r.usd_cents : null);
    if (cur == null) return;
    const dd = marketDayDelta(r.hist, cur);
    if (!dd) return;
    if (dd.pct > 0.05) up++;
    else if (dd.pct < -0.05) down++;
    movers.push({ name: r.name, pct: dd.pct });
  });
  if (!movers.length) {
    return `<div class="ow-txt">тренд появится, когда накопятся наблюдения по ценам</div>`;
  }
  movers.sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct));
  const chips = movers.slice(0, 3).map((m) => {
    const cUp = m.pct > 0;
    const name = m.name.length > 24 ? m.name.slice(0, 24) + "…" : m.name;
    return `<span class="ow-chip ${cUp ? "c-up" : "c-down"}">${cUp ? "▲" : "▼"} ${esc(name)} · ${fmtPct(Math.abs(m.pct))}</span>`;
  }).join("");
  const flat = movers.length - up - down;
  return `<div class="ow-counts">
      <span class="ow-count up">▲ <b>${up}</b> дорожает</span>
      <span class="ow-count down">▼ <b>${down}</b> дешевеет</span>
      <span class="ow-count flat"><b>${flat}</b> ровно</span>
    </div>
    <div class="ow-chips">${chips}</div>
    <div class="ow-foot">движения за сутки по истории цен · автообновление ~45 с</div>`;
}

/* автопилот обзора: пока открыта вкладка «Рынок», периодически обновляем топ */
function marketOvAutoStop() {
  if (state.marketOvTimer) { clearInterval(state.marketOvTimer); state.marketOvTimer = null; }
}
function marketOvAutoStart() {
  marketOvAutoStop();
  if (!state.marketOv || state.marketOvLoading) marketOvLoad(state.marketOvGame || "cs2", false);
  state.marketOvTimer = setInterval(marketOvAutoTick, 45000);
}
function marketOvAutoTick() {
  if (state.tab !== "market" || document.visibilityState === "hidden") { marketOvAutoStop(); return; }
  if (state.marketOvLoading) return;
  const now = Date.now();
  if (state.marketOvPauseUntil) {
    if (now < state.marketOvPauseUntil) return;          // ждём, пока Steam отпустит
    state.marketOvPauseUntil = 0;
    marketOvLoad(state.marketOvGame || "cs2", false);    // пауза кончилась — пробуем сами
    return;
  }
  if (!state.marketOv) { marketOvLoad(state.marketOvGame || "cs2", false); return; }
  const age = now - new Date(state.marketOv.ts || now).getTime();
  if (age > 30000) marketOvLoad(state.marketOvGame || "cs2", false);
}

function marketOvRowHtml(r, sym) {
  const cur = r.price_cents != null ? r.price_cents : (r.usd_cents != null ? r.usd_cents : null);
  const delta = marketDayDelta(r.hist, cur);
  const icon = r.icon
    ? `<div class="ov-thumb"><img class="ov-img" src="${esc(r.icon)}" alt="" data-ph="ov-img" onerror="__imgBroken(this,'🎒')"></div>`
    : `<div class="ov-thumb"><span class="ov-ph">🎒</span></div>`;
  return `<div class="ov-row anim-in" role="button" tabindex="0" data-appid="${r.appid}" data-hash="${esc(r.hash)}" data-name="${esc(r.name)}" data-icon="${r.icon ? esc(r.icon) : ""}">
      ${icon}
      <div class="ov-main">
        <div class="ov-name">${esc(r.name)}</div>
        <div class="ov-meta">${r.fallback
          ? (r.volume != null ? `${Number(r.volume).toLocaleString("ru-RU")} продаж за 24 ч` : "резервная строка · Steam молчит")
          : `${Number(r.listings || 0).toLocaleString("ru-RU")} активных лотов`}</div>
      </div>
      <div class="ov-right">
        <div class="ov-price">${cur != null ? esc(fmtMoney(cur, sym)) : "—"}</div>
        ${mkDeltaPill(delta)}
      </div>
    </div>`;
}

/* клик по строке обзора: компас в поиске + форма алерта */
function marketPickRow(ds) {
  const panel = $("#marketAddPanel");
  if (panel.classList.contains("hidden")) marketAddToggle();
  const input = $("#marketInput");
  input.value = ds.name || ds.hash;
  hideSuggest();
  lookupMarket(parseInt(ds.appid || "730", 10), ds.hash, true);
}

/* ============================================================
   📡 Активные алерты: отрисовка + живой тренд каждые ~25 с
   ============================================================ */
async function loadMarket() {
  try {
    const d = await api("/api/market/alerts");
    state.alerts = d.alerts || [];
  } catch (e) { state.alerts = []; }
  renderMarketGate();
  marketSyncDom(state.alerts, "full");
}

function marketLiveStop() {
  state.marketLiveOn = false;
  if (state.marketLiveTimer) { clearInterval(state.marketLiveTimer); state.marketLiveTimer = null; }
  const dot = $("#marketLiveDot");
  if (dot) dot.classList.remove("on");
}

function marketLiveStart() {
  if (state.marketLiveOn) return;
  state.marketLiveOn = true;
  const dot = $("#marketLiveDot");
  if (dot) dot.classList.add("on");
  state.marketLiveTimer = setInterval(() => marketLiveTick(), 25000);
}

async function marketLiveTick() {
  if (state.tab !== "market") { marketLiveStop(); return; }
  if (document.visibilityState === "hidden") return;
  let d = null;
  try { d = await api("/api/market/alerts", null, 9000); }
  catch (e) { return; }   // тихий пропуск: следующий тик подхватит
  state.alerts = d.alerts || [];
  marketSyncDom(state.alerts, "tick");
  const dot = $("#marketLiveDot");
  if (dot) dot.title = "цены обновляются каждые ~25 с · " + new Date().toLocaleTimeString("ru-RU");
}

/* отрисовка/обновление списка: полная (mode=full) или точечная (mode=tick) */
function marketSyncDom(alerts, mode) {
  const list = $("#marketAlerts");
  if (!list) return;
  const prevMap = state.marketSnap || new Map();
  const newMap = new Map(alerts.map((a) => [a.market_hash_name, a]));
  if (mode === "full") {
    list.innerHTML = "";
    state.marketFired = [];
  }
  // снятые (сработавшие или удалённые) алерты + текущие — одним проходом
  const removed = [];
  const existing = new Map();
  $$("#marketAlerts .alert-card").forEach((li) => {
    if (!newMap.has(li.dataset.hash)) {
      removed.push(prevMap.get(li.dataset.hash) || { market_hash_name: li.dataset.hash });
      li.remove();
    } else {
      existing.set(li.dataset.hash, li);
    }
  });
  alerts.forEach((a) => {
    const li = existing.get(a.market_hash_name);
    if (!li) {
      const card = alertCard(a);
      list.appendChild(card);
    } else {
      syncAlertNode(li, a, prevMap.get(a.market_hash_name));
    }
  });
  state.marketSnap = newMap;
  const has = alerts.length > 0;
  const empty = $("#marketEmpty");
  if (empty) empty.classList.toggle("hidden", has);
  const label = $("#marketListLabel");
  if (label) label.classList.toggle("hidden", !has);
  if (mode === "tick" && removed.length) marketFlashFired(removed);
}

/* заметный момент срабатывания: баннер + «открытие кейса» с конфетти + хаптика */
function marketFlashFired(removed) {
  if (removed && removed.length) {
    const first = removed[0];
    if (first && first.market_hash_name) {
      // системный «пуш сверху» (ОС) — сразу, без ожидания события ленты;
      // внутри приложения внимание забирает оверлей «открытие кейса» ниже
      try {
        const sym = curSym(first.currency);
        sysNotify("🎯 Алерт сработал: " + first.market_hash_name,
          "Цель " + alertGoalMeta(first, sym) + ". Алерт снят — событие в ленте.",
          "m:" + first.market_hash_name);
      } catch (e) {}
      state.marketHitAt[first.market_hash_name] = Date.now();
      try { celebrationHit(first); }
      catch (e) { console.error("celebration:", e); }
    }
  }
  removed.forEach((a) => {
    const name = a.market_hash_name || "предмет";
    const flash = document.createElement("div");
    flash.className = "hit-banner";
    flash.innerHTML = `<span class="hit-banner-x">🎯</span>${esc(name)} — цена пересекла цель. Алерт снят, событие в ленте.`;
    const list = $("#marketAlerts");
    const holder = $("#marketListLabel");
    if (holder && list) { holder.parentNode.insertBefore(flash, holder.nextSibling); }
    setTimeout(() => { flash.classList.add("out"); setTimeout(() => flash.remove(), 500); }, 9000);
    toast("🎯 Алерт сработал: " + name);
  });
}

/* ============ haptic / вибрации (Telegram WebApp + fallback) ============ */
function haptic(kind, pattern) {
  try {
    const tg = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback;
    if (tg) {
      if (kind === "success") tg.notificationOccurred("success");
      else if (kind === "error") tg.notificationOccurred("error");
      else tg.impactOccurred("light");
      return;
    }
  } catch (e) { /* вне Telegram — ниже обычная вибрация */ }
  try { if (navigator.vibrate) navigator.vibrate(pattern || [8]); } catch (e) {}
}

/* ============ конфетти (DOM-частицы, без библиотек) ============ */
function confettiBurst(host, n) {
  const colors = ["#4ea1ff", "#8b5cf6", "#39d98a", "#ffb454", "#ff5c7a", "#a1cd44", "#ffd86b"];
  const total = n || 70;
  for (let i = 0; i < total; i++) {
    const s = document.createElement("i");
    s.className = "cf";
    const w = 5 + Math.random() * 6;
    const hgt = w * (0.5 + Math.random());
    s.style.cssText =
      "left:" + (Math.random() * 100).toFixed(2) + "%;" +
      "width:" + w.toFixed(1) + "px;height:" + hgt.toFixed(1) + "px;" +
      "background:" + colors[(Math.random() * colors.length) | 0] + ";" +
      "--cfx:" + ((Math.random() * 240 - 120) | 0) + "px;" +
      "animation-delay:" + (Math.random() * 0.8).toFixed(2) + "s;" +
      "animation-duration:" + (2.4 + Math.random() * 2.2).toFixed(2) + "s;";
    host.appendChild(s);
  }
  setTimeout(() => { while (host.firstChild) host.removeChild(host.firstChild); }, 5000);
}

/* ============ оверлей: «открытие кейса» при срабатывании алерта ============ */
function overlayCount() { return document.querySelectorAll(".ovl").length; }
function lockBody() { document.body.style.overflow = "hidden"; }
function unlockBody() { if (!overlayCount()) document.body.style.overflow = ""; }
function closeOvl(ovl) {
  ovl.classList.remove("on");
  setTimeout(() => { ovl.remove(); unlockBody(); }, 280);
}

function celebrationHit(a) {
  haptic("success", [14, 50, 14, 50, 90]);
  lockBody();
  const sym = curSym(a.currency);
  const name = a.market_hash_name || "предмет";
  const goalShort = thrTxt(a, sym);
  const goalLong = alertGoalMeta(a, sym);
  const nowCents = a.price && a.price.lowest_cents != null ? a.price.lowest_cents : null;
  const nowTxt = nowCents != null ? fmtMoney(nowCents, sym) : "—";
  const ovl = document.createElement("div");
  ovl.className = "ovl";
  ovl.innerHTML = `
    <div class="ovl-back" data-close></div>
    <div class="oc-card">
      <button class="oc-x" type="button" data-close>✕</button>
      <div class="oc-conf"></div>
      <div class="oc-emoji">🎯</div>
      <div class="oc-kicker">Sniper · алерт сработал</div>
      <div class="oc-title">Цель достигнута!</div>
      <div class="oc-name">${esc(name)}</div>
      <div class="oc-goal">${esc(goalShort)} · ${esc(goalLong)}</div>
      <div class="oc-price">цена сейчас <b>${esc(nowTxt)}</b></div>
      <div class="oc-note muted small">Алерт снят — событие уже в ленте.</div>
      <div class="oc-btns">
        <button class="btn btn-primary" id="ocShare" type="button">📤 Поделиться</button>
        <button class="btn btn-ghost" type="button" data-close>Закрыть</button>
      </div>
    </div>`;
  document.body.appendChild(ovl);
  requestAnimationFrame(() => ovl.classList.add("on"));
  confettiBurst(ovl.querySelector(".oc-conf"), 90);
  ovl.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => closeOvl(ovl)));
  const sh = ovl.querySelector("#ocShare");
  if (sh) sh.addEventListener("click", () => {
    try {
      const url = shareCardHit({ name, nowCents, sym, goalShort, goalLong });
      const cap = "🎯 Алерт SteamSale сработал: " + name + " — " + goalLong + " (сейчас " + nowTxt + ")";
      closeOvl(ovl);
      shareFlow(url, cap);
    } catch (e) { errToast(e); }
  });
}

/* короткая подпись цели алерта (в деньгах) */
function thrTxt(a, sym) {
  return fmtMoney(alertGoalCents(a), sym);
}

/* ============ Share-карточка: canvas -> PNG -> Telegram ============ */
function mkCardCtx() {
  const c = document.createElement("canvas");
  c.width = 1080; c.height = 720;
  return { cv: c, x: c.getContext("2d") };
}
function rr(x, px, py, w, h, r) {
  x.beginPath();
  x.moveTo(px + r, py);
  x.arcTo(px + w, py, px + w, py + h, r);
  x.arcTo(px + w, py + h, px, py + h, r);
  x.arcTo(px, py + h, px, py, r);
  x.arcTo(px, py, px + w, py, r);
  x.closePath();
}
function fitText(x, text, maxW, font) {
  x.font = font;
  if (x.measureText(text).width <= maxW) return [text];
  const words = String(text).split(" "); const out = []; let cur = "";
  for (const w of words) {
    const t = cur ? cur + " " + w : w;
    if (x.measureText(t).width > maxW && cur) { out.push(cur); cur = w; }
    else cur = t;
  }
  if (cur) out.push(cur);
  return out.slice(0, 2).map((s) => (s.length > 2 && out.length > 2 ? s + "…" : s));
}

function buildShareCard(meta) {
  const { cv, x } = mkCardCtx();
  const W = 1080, H = 720;
  // фон
  const g = x.createLinearGradient(0, 0, W, H);
  g.addColorStop(0, "#0a1120"); g.addColorStop(0.55, "#0e1c33"); g.addColorStop(1, "#151a2f");
  x.fillStyle = g; x.fillRect(0, 0, W, H);
  // цветные свечения
  let r = x.createRadialGradient(W * 0.88, H * 0.12, 0, W * 0.88, H * 0.12, 460);
  r.addColorStop(0, "rgba(139,92,246,.5)"); r.addColorStop(1, "rgba(139,92,246,0)");
  x.fillStyle = r; x.fillRect(0, 0, W, H);
  r = x.createRadialGradient(W * 0.08, H * 0.95, 0, W * 0.08, H * 0.95, 380);
  r.addColorStop(0, "rgba(78,161,255,.35)"); r.addColorStop(1, "rgba(78,161,255,0)");
  x.fillStyle = r; x.fillRect(0, 0, W, H);
  r = x.createRadialGradient(W * 0.5, H * 0.5, 0, W * 0.5, H * 0.5, 620);
  r.addColorStop(0, "rgba(57,217,138,.06)"); r.addColorStop(1, "rgba(57,217,138,0)");
  x.fillStyle = r; x.fillRect(0, 0, W, H);
  // водяной знак
  x.globalAlpha = 0.07;
  x.font = "600 300px sans-serif"; x.textAlign = "center";
  x.fillText("🔥", W - 130, H - 40);
  x.globalAlpha = 1;
  // шапка: логотип + имя
  x.textAlign = "left";
  x.font = "64px sans-serif"; x.fillText("🔥", 84, 118);
  const g2 = x.createLinearGradient(180, 0, 560, 0);
  g2.addColorStop(0, "#ffffff"); g2.addColorStop(1, "#9fd0ff");
  x.fillStyle = g2;
  x.font = "900 58px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
  x.fillText("SteamSale", 180, 122);
  x.font = "600 24px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
  x.fillStyle = "rgba(216,228,248,.72)";
  x.textAlign = "right";
  x.fillText("мини-апп · живые цены Steam", W - 84, 104);
  // разделитель
  x.strokeStyle = "rgba(126,165,255,.25)"; x.lineWidth = 2;
  x.beginPath(); x.moveTo(84, 168); x.lineTo(W - 84, 168); x.stroke();
  x.textAlign = "left";

  // кикер
  x.fillStyle = meta.kickerColor || "#ffd86b";
  x.font = "800 26px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
  x.fillText(String(meta.kicker || "").toUpperCase(), 84, 250);
  // заголовок (перенос на 2 строки максимум)
  let ty = 340;
  const tLines = fitText(x, meta.title, W - 170, "900 66px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif");
  x.font = "900 66px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
  tLines.forEach((ln, i) => { x.fillStyle = "#f2f7ff"; x.fillText(ln, 84, ty + i * 78); });
  ty += (tLines.length - 1) * 78 + 26;
  if (meta.sub) {
    x.font = "600 30px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    x.fillStyle = "rgba(190,205,232,.85)";
    const sl = fitText(x, meta.sub, W - 170, "600 30px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif");
    x.font = "600 30px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    sl.forEach((ln, i) => x.fillText(ln, 84, ty + 40 + i * 42));
    ty += 40 + (sl.length - 1) * 42 + 46;
  } else { ty += 16; }

  // строки значений
  const rows = meta.lines || [];
  const rh = 76, gap = 14;
  rows.forEach((row, i) => {
    const y0 = ty + i * (rh + gap);
    rr(x, 84, y0, W - 168, rh, 20);
    x.fillStyle = "rgba(255,255,255,.055)"; x.fill();
    x.strokeStyle = "rgba(126,165,255,.18)"; x.lineWidth = 1.5; x.stroke();
    x.font = "700 32px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    x.fillStyle = "rgba(200,213,238,.72)";
    x.fillText(String(row.l), 116, y0 + 49);
    x.textAlign = "right";
    x.font = "900 40px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    x.fillStyle = row.color || "#ffffff";
    const vv = fitText(x, String(row.r), W - 480, "900 40px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif");
    x.font = "900 40px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    x.fillText(vv[0], W - 116, y0 + 49);
    x.textAlign = "left";
  });
  ty += rows.length * (rh + gap) + 26;
  // футер
  x.strokeStyle = "rgba(126,165,255,.2)"; x.lineWidth = 2;
  x.beginPath(); x.moveTo(84, H - 96); x.lineTo(W - 84, H - 96); x.stroke();
  x.textAlign = "center";
  x.font = "800 25px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
  x.fillStyle = "rgba(216,228,248,.65)";
  x.fillText(meta.footer || "SteamSale · t.me/SteamSaleBot · мини-апп Steam", W / 2, H - 46);
  x.textAlign = "left";
  return cv.toDataURL("image/png");
}

function shareCardHit(o) {
  return buildShareCard({
    kicker: "Sniper · цель достигнута",
    title: o.name,
    sub: o.goalShort + " · " + o.goalLong,
    lines: [
      { l: "Цена сейчас", r: o.nowCents != null ? fmtMoney(o.nowCents, o.sym) : "—", color: "#39d98a" },
    ],
    footer: "SteamSale · t.me/SteamSaleBot · мини-апп Steam",
  });
}

function walletShareLines(w) {
  const sym = curSym(w.region);
  const fmt = (c) => fmtMoney(c, w.region);
  const lines = [];
  if (w.full_cents) lines.push({ l: "Полная цена списка", r: fmt(w.full_cents) });
  if (w.today_cents != null) lines.push({ l: "Купить прямо сейчас", r: fmt(w.today_cents), color: "#4ea1ff" });
  if (w.savings_cents > 0) lines.push({ l: "Экономия", r: "−" + fmt(w.savings_cents), color: "#a1cd44" });
  if (w.pct) lines.push({ l: "Общая скидка", r: "−" + w.pct + "%", color: "#39d98a" });
  return { lines, sym, fmt };
}
function shareCardWallet(w) {
  const { lines } = walletShareLines(w);
  return buildShareCard({
    kicker: "Мой кошелёк вишлиста",
    title: (w.items || []).length + " игр в списке",
    sub: "Цены Steam сейчас — столько нужно, чтобы купить весь список.",
    lines,
    footer: "SteamSale · t.me/SteamSaleBot · слежу за ценами каждые пару минут",
  });
}

/* data-URL (PNG) → Blob: надёжнее для download и для системного share с файлом */
function shareBlobFromDataURL(dataURL) {
  const m = /^data:([^;,]+);base64,(.*)$/.exec(dataURL || "");
  if (!m) return Promise.reject(new Error("Формат картинки не распознан"));
  try {
    const bin = atob(m[2]);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return Promise.resolve(new Blob([arr], { type: m[1] || "image/png" }));
  } catch (e) { return Promise.reject(e); }
}
function shareFilesCapable() {
  try { return !!navigator.share && typeof navigator.canShare === "function"; } catch (e) { return false; }
}
function isEmbedded() {
  try { return window.self !== window.top; } catch (e) { return true; }
}
function legacyCopy(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(ta);
    const sel = document.getSelection();
    const prev = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove();
    if (prev && sel) { sel.removeAllRanges(); sel.addRange(prev); }
    return ok;
  } catch (e) { return false; }
}
async function copyTextSmart(text) {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {}
  return legacyCopy(text);
}

/* показать готовую картинку: системный «Отправить» / скачать PNG / Telegram / копия.
   Скачивание идёт через objectURL (Blob), а не огромный data-URL — так надёжнее
   на мобильных; системный share умеет прикрепить картинку прямо в чат Telegram. */
function shareFlow(dataURL, caption) {
  lockBody();
  const ovl = document.createElement("div");
  ovl.className = "ovl";
  const tUrl = encodeURIComponent("https://t.me/SteamSaleBot");
  const tText = encodeURIComponent(caption);
  const tgHref = "https://t.me/share/url?url=" + tUrl + "&text=" + tText;
  const hasShare = shareFilesCapable();
  const embedded = isEmbedded();
  const q = (id) => ovl.querySelector(id);
  const imgOk = "📋 Копировать картинку";
  ovl.innerHTML = `
    <div class="ovl-back" data-close></div>
    <div class="oc-card oc-share-card">
      <button class="oc-x" type="button" data-close>✕</button>
      <div class="oc-kicker">📤 Готовая карточка · PNG</div>
      <img class="oc-img" src="${dataURL}" alt="Share-карточка SteamSale">
      <div class="oc-btns">
        ${hasShare ? '<button class="btn btn-primary" id="ocNative" type="button">📤 Отправить…</button>' : ""}
        <a class="btn btn-primary" id="ocDl" download="steamsale-card.png" href="${dataURL}" style="pointer-events:none;opacity:.75">⬇ Скачать PNG</a>
        <a class="btn btn-ghost" id="ocTg" target="_blank" rel="noopener noreferrer" href="${tgHref}">✈️ Telegram</a>
        <button class="btn btn-ghost hidden" id="ocCopy" type="button">${imgOk}</button>
        <button class="btn btn-ghost" id="ocCopyTxt" type="button">📋 Копировать текст</button>
      </div>
      <p class="search-meta" id="ocNote" style="padding-top:8px">${hasShare
        ? "«Отправить» откроет системное меню — там Telegram прикрепит картинку сразу. «Скачать PNG» сохранит файл в «Загрузки»."
        : "Скачайте PNG (папка «Загрузки») и прикрепите его в Telegram как фото."}${embedded
          ? " 💡 Это окно предпросмотра (песочница): здесь браузер часто блокирует скачивание и буфер обмена — тогда откройте приложение отдельной вкладкой."
          : ""}</p>
    </div>`;
  document.body.appendChild(ovl);
  requestAnimationFrame(() => ovl.classList.add("on"));

  let objUrl = null;
  const closeOv = () => {
    const keep = objUrl;
    objUrl = null;
    setTimeout(() => { if (keep) { try { URL.revokeObjectURL(keep); } catch (e) {} } }, 4000);
    closeOvl(ovl);
  };
  ovl.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeOv));

  /* общий обработчик копирования: картинка -> текст (фолбэк) -> execCommand */
  const copyImageHandler = async (blob) => {
    try {
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard && typeof navigator.clipboard.write === "function") {
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
        toast("Картинка скопирована — вставьте в Telegram (Ctrl/Cmd+V)");
        return true;
      }
    } catch (e) { /* ниже — текст-фолбэк */ }
    const ok = await copyTextSmart(caption);
    toast(ok
      ? "Копирование картинки тут заблокировано — скопировал текст: вставьте в Telegram и приложите PNG (кнопка «Скачать PNG») рядом"
      : "Копирование в этом окне не работает — нажмите «Скачать PNG» или откройте приложение отдельной вкладкой", !ok);
    return ok;
  };
  const copyTxtHandler = async () => {
    const ok = await copyTextSmart(caption);
    toast(ok ? "Текст скопирован — вставьте в Telegram" : "Буфер обмена в этом окне недоступен", !ok);
  };

  shareBlobFromDataURL(dataURL).then((blob) => {
    objUrl = URL.createObjectURL(blob);
    const dl = q("#ocDl");
    if (dl) { dl.href = objUrl; dl.style.pointerEvents = ""; dl.style.opacity = ""; }
    const cp = q("#ocCopy");
    const hasClip = typeof ClipboardItem !== "undefined" && navigator.clipboard && typeof navigator.clipboard.write === "function";
    if (cp) {
      cp.classList.toggle("hidden", !hasClip);
      cp.addEventListener("click", () => copyImageHandler(blob).catch(() => toast("Копирование не вышло", true)));
    }
    const ct = q("#ocCopyTxt");
    if (ct) ct.addEventListener("click", () => copyTxtHandler().catch(() => toast("Копирование не вышло", true)));
    const nat = q("#ocNative");
    if (nat) nat.addEventListener("click", async () => {
      try {
        const f = new File([blob], "steamsale-card.png", { type: blob.type });
        const data = { files: [f], text: caption };
        if (navigator.canShare && !navigator.canShare(data)) {
          delete data.files;
          if (navigator.canShare && !navigator.canShare(data)) { toast("Системный шеринг сейчас недоступен", true); return; }
        }
        await navigator.share(data);
      } catch (err) {
        if (err && err.name === "AbortError") return;   // пользователь сам закрыл шторку
        toast("Не получилось отправить: " + ((err && err.message) || "система не дала"), true);
      }
    });
  }).catch(() => {
    // Blob не собрался — последний шанс через data-URL в href (как раньше)
    const dl = q("#ocDl");
    if (dl) { dl.href = dataURL; dl.style.pointerEvents = ""; dl.style.opacity = ""; }
  });
  const dl2 = q("#ocDl");
  if (dl2) dl2.addEventListener("click", () => {
    setTimeout(() => {
      toast(embedded
        ? "В песочнице загрузка часто блокируется. Если файла нет в «Загрузках» — откройте приложение отдельной вкладкой или зажмите картинку и «Сохранить изображение»"
        : "Скачиваю steamsale-card.png — файл в папке «Загрузки»");
    }, 250);
  });
  const tg = q("#ocTg");
  if (tg) tg.addEventListener("click", () => {
    // подсказка на случай, если браузер заблокировал новую вкладку
    setTimeout(() => {
      const note = q("#ocNote");
      if (note && !note.dataset.touched) {
        note.dataset.touched = "1";
        note.textContent = "Не открылось? Разрешите всплывающие окна для этого сайта — или нажмите «📋 Копировать текст» / «Скачать PNG» и пришлите сами. Ссылка: t.me/SteamSaleBot";
      }
    }, 1200);
  });
}



/* share-карточка из события ленты (скидка / цель / минимум / market) */
function shareFeedFlow(e) {
  const K = { sale: { e: "🔥", t: "живая распродажа" }, low: { e: "🏆", t: "исторический минимум" },
    target: { e: "🎯", t: "цель достигнута" }, market: { e: "📈", t: "market-алерт" } };
  const k = K[e.kind] || K.sale;
  const price = e.price || {};
  let cur = price.currency;
  if (!cur || cur === "local") cur = state.session?.settings?.region || "RUB";
  const sym = curSym(cur);
  const lines = [];
  const fmt = (c) => fmtMoney(c, cur);
  if (price.price_original_cents != null && (price.price_final_cents == null || price.price_original_cents > price.price_final_cents)) {
    lines.push({ l: "Было", r: fmt(price.price_original_cents) });
  }
  if (price.price_final_cents != null) lines.push({ l: "Цена", r: fmt(price.price_final_cents), color: "#4ea1ff" });
  if (price.discount_percent) lines.push({ l: "Скидка", r: "−" + price.discount_percent + "%", color: "#a1cd44" });
  if (!lines.length && e.body) lines.push({ l: "Событие", r: e.body.length > 40 ? e.body.slice(0, 38) + "…" : e.body });
  const title = String(e.title || "").replace(/^[\u{1F300}-\u{1FAFF}\u{FE0F}\u{200D}]+\s*/u, "").replace(/[«»"]/g, "");
  const url = buildShareCard({
    kicker: k.t,
    title: title || "Событие SteamSale",
    sub: e.body ? e.body.replace(/[«»"]/g, "") : "",
    lines,
    footer: "SteamSale · t.me/SteamSaleBot · мини-апп Steam",
  });
  const cap = "SteamSale: " + e.title + (e.body ? " — " + e.body : "") + " (t.me/SteamSaleBot)";
  shareFlow(url, cap);
}

function alertGoalCents(a) {
  // %-алерт считает порог от base_cents на каждый чек — так же, как сервер
  if (a.mode === "percent" && a.pct != null && a.base_cents) {
    const pct = parseFloat(a.pct) || 0;
    return a.direction === "below"
      ? Math.round(a.base_cents * (1 - pct / 100))
      : Math.round(a.base_cents * (1 + pct / 100));
  }
  return a.threshold_cents;
}

function alertGoalMeta(a, sym) {
  const word = a.direction === "below" ? "ниже" : "выше";
  if (a.mode === "percent" && a.pct != null) {
    const pct = String(a.pct).replace(/\.?0+$/, "");
    let s = "сработает, когда станет " + word + " на " + pct + "%";
    if (a.base_cents) s += " от " + fmtMoney(a.base_cents, sym);
    if (a.threshold_cents && a.threshold_cents > 0) s += " (≈ " + fmtMoney(a.threshold_cents, sym) + ")";
    return s;
  }
  return "сработает, когда станет " + word + " " + fmtMoney(a.threshold_cents, sym);
}

function alertCard(a) {
  const li = document.createElement("li");
  li.className = "alert-card anim-in";
  li.dataset.hash = a.market_hash_name;
  li.dataset.id = a.id;
  const sym = curSym(a.currency);
  const current = a.price && a.price.lowest_cents != null ? a.price.lowest_cents : null;
  const goal = alertGoalCents(a);
  const hit = current != null && (a.direction === "below" ? current <= goal : current >= goal);
  const arrow = a.direction === "below" ? "ниже" : "выше";
  const dirEmoji = a.direction === "below" ? "📉" : "📈";
  const spark = alertSpark(a.history, sym);
  const dayDelta = marketDayDelta(a.history, current);
  const dayTtl = dayDelta && dayDelta.ageH > 40 ? "~" + dayDelta.ageH + " ч" : "сутки";
  const firstTick = dayDelta && Math.abs(dayDelta.pct) > 0.05
    ? `<span class="${dayDelta.pct > 0 ? "t-up" : "t-down"}">${dayDelta.pct > 0 ? "▲" : "▼"} ${fmtPct(Math.abs(dayDelta.pct))} / ${dayTtl}</span>`
    : `<span class="t-ok">изменений в истории нет</span>`;
  const thumb = a.item_icon
    ? `<div class="al-thumb"><img class="al-thumb-img" src="${esc(a.item_icon)}" alt="" data-ph="al-thumb-img" onerror="__imgBroken(this,'${dirEmoji}')"><span class="al-dir">${dirEmoji}</span></div>`
    : `<div class="al-thumb"><span class="al-thumb-ph">${dirEmoji}</span></div>`;
  li.innerHTML = `
    ${thumb}
    <div class="al-body">
      <div class="al-name">${esc(a.market_hash_name)}</div>
      ${a.item_type ? `<div class="al-type">${esc(a.item_type)}</div>` : ""}
      <div class="al-meta">${esc(alertGoalMeta(a, sym))}</div>
      ${spark ? `<div class="al-spark">${spark}</div>` : ""}
      ${spark && a.history_demo ? `<div class="al-demo">ранние точки — демо-реконструкция</div>` : ""}
    </div>
    <div class="al-price">
      <div class="ap ${hit ? "ap-hit" : ""}">${current != null ? esc(fmtMoney(current, sym)) : "…"}</div>
      <div class="ap-tick" data-tick="1">${firstTick}</div>
      <div class="ap-now">сейчас${a.price && a.price.volume ? " · объём " + a.price.volume : ""}</div>
    </div>
    <button class="al-del" type="button">✕</button>`;
  li.querySelector(".al-del").addEventListener("click", async () => {
    await del("/api/market/alerts/" + a.id);
    toast("Алерт удалён");
    await loadMarket();
  });
  return li;
}

/* точечное обновление карточки алерта при «живом» тике */
function syncAlertNode(li, a, prevA) {
  const sym = curSym(a.currency);
  const current = a.price && a.price.lowest_cents != null ? a.price.lowest_cents : null;
  const oldCur = prevA && prevA.price && prevA.price.lowest_cents != null ? prevA.price.lowest_cents : null;
  const hit = current != null && (a.direction === "below" ? current <= alertGoalCents(a) : current >= alertGoalCents(a));
  const ap = li.querySelector(".ap");
  if (ap) { ap.textContent = current != null ? fmtMoney(current, sym) : "…"; ap.classList.toggle("ap-hit", hit); }
  const tickEl = li.querySelector(".ap-tick");
  if (tickEl && current != null && oldCur != null && current !== oldCur) {
    const pct = (current - oldCur) / oldCur * 100;
    const up = current > oldCur;
    tickEl.innerHTML = `<span class="${up ? "t-up" : "t-down"}">${up ? "▲" : "▼"} ${fmtPct(Math.abs(pct))} / 25 с</span>`;
    li.classList.remove("tick-flash"); void li.offsetWidth; li.classList.add("tick-flash");
  }
  const nowEl = li.querySelector(".ap-now");
  if (nowEl) nowEl.textContent = "сейчас" + (a.price && a.price.volume ? " · объём " + a.price.volume : "");
  // если история выросла — обновляем спарклайн
  const sparkEl = li.querySelector(".al-spark");
  const histLen = (a.history || []).length;
  if (sparkEl && li.dataset.histLen !== String(histLen)) {
    li.dataset.histLen = String(histLen);
    sparkEl.innerHTML = alertSpark(a.history, sym);
  }
  li.dataset.histLen = String(histLen);
}

/* ---- быстрый выбор из кураторского набора ---- */
async function loadCurated() {
  try {
    const d = await api("/api/market/curated");
    const box = $("#marketChips");
    box.innerHTML = "";
    d.items.forEach((it) => {
      const c = document.createElement("button");
      c.type = "button"; c.className = "chip";
      c.innerHTML = `${esc(it.name)} <span class="chip-game">(${it.appid})</span>`;
      c.addEventListener("click", () => pickMarketItem(it));
      box.appendChild(c);
    });
  } catch (e) {}
}

function pickMarketItem(it) {
  state.marketPending = { appid: it.appid, hash: it.hash };
  $("#marketInput").value = it.hash;
  lookupMarket(it.appid, it.hash);
}

/* ---- автокомплит предметов Market ---- */
let suggestTimer = null, suggestSeq = 0, suggestItems = [], suggestActive = -1;
let marketAppid = 730;  // меняется, если вставлена ссылка на другой appid

function marketAppidFrom(value) {
  const m = String(value || "").match(/market\/listings\/(\d+)\//);
  return m ? parseInt(m[1], 10) : 730;
}

function renderSuggest() {
  const box = $("#marketSuggest");
  box.innerHTML = "";
  if (!suggestItems.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  suggestItems.forEach((it, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "m-sg" + (i === suggestActive ? " active" : "");
    const img = it.icon
      ? `<img class="m-sg-ph" src="${esc(it.icon)}" alt="" data-ph="m-sg-ph" onerror="__imgBroken(this,'🎒')">`
      : `<span class="m-sg-ph">🎒</span>`;
    b.innerHTML = `${img}<span class="m-sg-body">
        <span class="m-sg-name">${esc(it.name)}</span>
        ${it.type ? `<span class="m-sg-type">${esc(it.type)}</span>` : ""}
      </span><span class="m-sg-link">↗</span>`;
    b.addEventListener("mousedown", (e) => { e.preventDefault(); chooseSuggest(i); });
    b.addEventListener("click", () => chooseSuggest(i));
    box.appendChild(b);
  });
}

async function runSuggest(v) {
  const seq = ++suggestSeq;
  const box = $("#marketSuggest");
  if (v.trim().length < 2) {
    box.classList.add("hidden"); box.innerHTML = ""; suggestItems = []; suggestActive = -1;
    return;
  }
  try {
    const d = await api("/api/market/suggest?q=" + encodeURIComponent(v.trim()) + "&appid=" + marketAppidFrom(v));
    if (seq !== suggestSeq) return;
    suggestItems = d.items || [];
    suggestActive = -1;
    renderSuggest();
  } catch (e) { /* сеть/лимит — просто не показываем подсказки */ }
}

function chooseSuggest(i) {
  const it = suggestItems[i];
  if (!it) return;
  hideSuggest();
  const appid = marketAppidFrom($("#marketInput").value) || it.appid || 730;
  $("#marketInput").value = it.name;
  lookupMarket(appid, it.name);
}

function hideSuggest() {
  const box = $("#marketSuggest");
  if (box) { box.classList.add("hidden"); box.innerHTML = ""; }
  suggestItems = []; suggestActive = -1;
}

function bindMarketInput() {
  const input = $("#marketInput");
  input.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const v = input.value.trim();
    state.marketPending = null;
    if (v.length < 2) { hideSuggest(); return; }
    suggestTimer = setTimeout(() => runSuggest(v), 220);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!suggestItems.length) return;
      e.preventDefault();
      suggestActive = e.key === "ArrowDown"
        ? Math.min(suggestItems.length - 1, suggestActive + 1)
        : Math.max(0, suggestActive - 1);
      renderSuggest();
      const el = document.querySelector(".m-sg.active");
      if (el) el.scrollIntoView({ block: "nearest" });
      return;
    }
    if (e.key === "Escape") { hideSuggest(); return; }
    if (e.key === "Enter") {
      e.preventDefault();
      if (suggestActive >= 0 && suggestItems[suggestActive]) { chooseSuggest(suggestActive); return; }
      hideSuggest();
      const v = input.value.trim();
      if (v.length < 3) return;
      lookupMarket(marketAppidFrom(v), v);
      $("#marketLookupResult").scrollIntoView({ behavior: "smooth" });
    }
  });
  input.addEventListener("blur", () => setTimeout(hideSuggest, 150));
}

function mkChartHtml(d) {
  const hist = (d && d.history) || [];
  if (!hist || hist.length < 2) return "";
  const from = fmtDate(hist[0].t), to = fmtDate(hist[hist.length - 1].t);
  const note = d.history_demo
    ? "Первые точки — реконструкция по типовой динамике (демо). Дальше копятся реальные цены при каждой проверке."
    : "Реальные наблюдения при каждой проверке цены.";
  return `<div class="m-hist anim-in">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
      <b style="font-size:12px">Цена за период</b>
      <span style="font-size:11px;color:var(--faint);white-space:nowrap">${esc(from)} — ${esc(to)}</span>
    </div>
    ${marketMiniChart(hist)}
    <div class="m-hist-note">${note}</div>
  </div>`;
}

async function lookupMarket(appid, raw, scrollTo) {
  const box = $("#marketLookupResult");
  const form = $("#alertForm");
  box.innerHTML = '<p class="search-status">Смотрю цену в Steam Market…</p>';
  form.classList.add("hidden");
  try {
    const d = await api(`/api/market/lookup?q=${encodeURIComponent(raw)}&appid=${appid}`);
    const preview = d.preview;
    if (!d.ok) {
      if (d.error === "market_limited") {
        // Steam Market троттлит — но предмет чаще всего находим: ставим алерт по каноничному
        // названию с превью, цену подтянем, как только API ответит.
        state.marketPending = {
          appid: d.appid || appid, hash: d.canonical || raw,
          icon: preview ? preview.image : null, type: preview ? preview.type : null,
        };
        $("#alertAmount").value = "";
        box.innerHTML = `
          <div class="lookup-card mk-card anim-in">
            ${preview && preview.image
              ? `<div class="mk-thumb"><img class="mk-img" src="${esc(preview.image)}" alt="" data-ph="mk-img" onerror="__imgBroken(this,'🎒')"></div>`
              : `<div class="mk-thumb"><span class="mk-img">🎒</span></div>`}
            <div class="mk-main">
              <div class="lk-name">${esc(state.marketPending.hash)}</div>
              ${preview && preview.type ? `<div class="mk-type">${esc(preview.type)}</div>` : ""}
              <div class="search-meta" style="padding:2px 0 0;color:var(--warn)">Steam Market сейчас отвечает 429 (слишком частые запросы).
              Алерт создадим по названию, а цену покажем, как только API ответит.</div>
            </div>
            ${preview && preview.market_url ? `<a class="mk-open" href="${esc(preview.market_url)}" target="_blank" rel="noopener">↗</a>` : ""}
          </div>`;
        form.classList.remove("hidden");
        return;
      }
      // not_found и прочие: если поиск всё же подобрал похожий предмет — покажем его превью
      const extra = (d.error === "not_found" && preview)
        ? `<div class="lookup-card mk-card anim-in" style="margin-top:8px">
             ${preview.image ? `<div class="mk-thumb"><img class="mk-img" src="${esc(preview.image)}" alt="" data-ph="mk-img" onerror="__imgBroken(this,'🎒')"></div>` : ""}
             <div class="mk-main">
               <div class="lk-name">${esc(preview.name || "")}</div>
               ${preview.type ? `<div class="mk-type">${esc(preview.type)}</div>` : ""}
               <div class="search-meta">похоже, вы это имели в виду — попробуйте точное название</div>
             </div>
           </div>`
        : "";
      box.innerHTML = `<p class="search-status" style="color:var(--danger)">${esc(d.message || "Не найден")}</p>${extra}`;
      return;
    }
    state.marketPending = {
      appid: d.appid, hash: d.market_hash_name,
      icon: preview ? preview.image : null, type: preview ? preview.type : null,
    };
    const sym = curSym(state.session?.settings?.region);
    const hasMed = d.price.median_cents && d.price.median_cents !== d.price.lowest_cents;
    box.innerHTML = `
      <div class="lookup-card mk-card anim-in">
        ${preview && preview.image
          ? `<div class="mk-thumb"><img class="mk-img" src="${esc(preview.image)}" alt="" data-ph="mk-img" onerror="__imgBroken(this,'🎒')"></div>`
          : `<div class="mk-thumb"><span class="mk-img">🎒</span></div>`}
        <div class="mk-main">
          <div class="lk-name">${esc(d.market_hash_name)}</div>
          ${preview && preview.type ? `<div class="mk-type">${esc(preview.type)}</div>` : ""}
          <div class="mk-meta">объём за 24ч: <b>${d.price.volume || "—"}</b></div>
          <div class="mk-prices">
            <span class="mk-chip">сейчас&nbsp;<b>${esc(fmtMoney(d.price.lowest_cents, sym))}</b></span>
            ${hasMed ? `<span class="mk-chip dim">медиана ${esc(fmtMoney(d.price.median_cents, sym))}</span>` : ""}
          </div>
        </div>
        <div class="mk-side">
          <div class="lk-price">${esc(fmtMoney(d.price.lowest_cents, sym))}</div>
          ${preview && preview.market_url ? `<a class="mk-open" href="${esc(preview.market_url)}" target="_blank" rel="noopener">Steam Market ↗</a>` : ""}
        </div>
      </div>
      ${mkCompassHtml(d.history, d.price.lowest_cents, sym, d.history_demo)}
      ${mkSniperHtml(d.history, d.price.lowest_cents, sym, d.history_demo)}
      ${mkChartHtml(d)}`;
    state.marketCur = d.price.lowest_cents;
    const amount = $("#alertAmount");
    if (state.marketGoalMode === "percent") {
      amount.value = "";
    } else {
      amount.value = d.direction === "above"
        ? Math.ceil((d.price.lowest_cents / 100) * 1.15)
        : Math.floor((d.price.lowest_cents / 100) * 0.9);
    }
    marketGoalHint();
    bindSniper(d.history, d.price.lowest_cents, sym, d.market_hash_name, d.appid);
    form.classList.remove("hidden");
    if (scrollTo) box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    box.innerHTML = `<p class="search-status" style="color:var(--danger)">Ошибка: ${esc(e.message)}</p>`;
  }
}

function marketAddToggle() {
  const panel = $("#marketAddPanel");
  const open = !panel.classList.contains("hidden");
  if (open) { panel.classList.add("hidden"); $("#marketAddOpen").textContent = "＋ Алерт / компас"; return; }
  panel.classList.remove("hidden");
  $("#marketAddOpen").textContent = "Закрыть";
  if (!$("#marketChips").children.length) loadCurated();
  setTimeout(() => $("#marketInput").focus(), 60);
}

/* ============================================================
   🔔 ЛЕНТА СОБЫТИЙ
   ============================================================ */
const KIND_META = {
  sale: ["🔥", "fi-k-sale"], low: ["🏆", "fi-k-low"], target: ["🎯", "fi-k-target"],
  market: ["📈", "fi-k-market"], market_add: ["📌", "fi-k-market_add"], digest: ["📰", "fi-k-digest"],
  season: ["🎉", "fi-k-season"], premium: ["💎", "fi-k-premium"], add: ["➕", "fi-k-premium"],
  remove: ["🗑", "fi-k-premium"],
};
async function loadFeed() {
  try {
    const d = await api("/api/events");
    state.unread = d.unread;
    renderBadge();
    absorbEvents(d);   // чтобы поллер не «перепушил» только что отрисованное
    const list = $("#feedList");
    list.innerHTML = "";
    $("#feedEmpty").classList.toggle("hidden", d.events.length > 0);
    let curDay = "";
    let seq = 0;
    d.events.forEach((e) => {
      const day = dayLabel(e.ts);
      if (day !== curDay) {
        curDay = day;
        const dl = document.createElement("div"); dl.className = "feed-day"; dl.textContent = day;
        list.appendChild(dl);
      }
      const [emoji, cls] = KIND_META[e.kind] || ["•", "fi-k-premium"];
      const item = document.createElement("div");
      item.className = "feed-item" + (e.read ? "" : " unread") + " anim-in";
      item.style.animationDelay = Math.min(seq, 10) * 55 + "ms";
      seq += 1;
      const bodyEsc = esc(e.body);
      const shareable = e.kind === "sale" || e.kind === "low" || e.kind === "target" || e.kind === "market";
      item.innerHTML = `
        <div class="fi-icon ${cls}">${emoji}</div>
        <div class="fi-body">
          <div class="fi-title">${esc(e.title)}</div>
          ${bodyEsc ? `<div class="fi-body-text">${bodyEsc}</div>` : ""}
          <div class="fi-meta">
            <span class="fi-time">${timeAgo(e.ts)}</span>
            ${shareable ? `<button class="fi-share" type="button">📤 Поделиться</button>` : ""}
          </div>
        </div>`;
      const shareBtn = item.querySelector(".fi-share");
      if (shareBtn) shareBtn.addEventListener("click", () => { try { shareFeedFlow(e); } catch (err) { errToast(err); } });
      list.appendChild(item);
    });
    state.feedRendered = d.events.length;
  } catch (e) { errToast(e); }
}
async function markRead() {
  try { await post("/api/events/read", {}); state.unread = 0; renderBadge(); }
  catch (e) {}
}

/* ============================================================
   👤 ПРОФИЛЬ
   ============================================================ */
async function renderProfile() {
  await loadSession();
  const s = state.session;
  const u = s.user;
  const card = $("#premiumCard");
  const badge = $("#pcBadge"), big = $("#pcBig"), note = $("#pcTrialInfo"), actions = $("#pcActions");
  badge.className = "pc-badge" + (u.premium ? "" : " free");
  actions.innerHTML = "";
  if (u.premium) {
    badge.textContent = "💎 PREMIUM";
    const left = u.premium_days_left;
    big.textContent = left > 30 ? "Premium активен" : `${left} ${plural(left, "день", "дня", "дней")} Premium`;
    note.textContent = "до " + untilLabel(u.premium_until);
    const b1 = document.createElement("button"); b1.className = "btn btn-primary btn-sm";
    b1.textContent = "📦 Продлить (+30 дн.)";
    b1.addEventListener("click", () => buyPremium());
    const b2 = document.createElement("button"); b2.className = "btn btn-ghost btn-sm";
    b2.textContent = "🧪 Посмотреть free-режим";
    b2.addEventListener("click", async () => {
      await post("/api/session/set", { action: "revoke" });
      toast("Теперь free — проверьте лимиты и гейты");
      await loadSession(); await loadGames(); renderProfile();
    });
    actions.append(b1, b2);
  } else {
    badge.textContent = "FREE";
    big.textContent = "Premium не активен";
    note.textContent = u.trial_available ? "Доступен бесплатный триал" : "Триал уже был использован";
    if (u.trial_available) {
      const b1 = document.createElement("button"); b1.className = "btn btn-primary btn-sm";
      b1.textContent = "🎁 Триал на 3 дня";
      b1.addEventListener("click", () => activateTrial());
      actions.appendChild(b1);
    }
    const b2 = document.createElement("button"); b2.className = "btn btn-ghost btn-sm";
    b2.textContent = "💎 Оформить Premium";
    b2.addEventListener("click", () => buyPremium());
    actions.appendChild(b2);
  }

  // регион
  const reg = s.settings.region;
  $$("#regionSeg .seg").forEach((b) => b.classList.toggle("active", b.dataset.region === reg));
  const th = s.settings.threshold;
  $("#thresholdRange").value = th;
  $("#thresholdPill").textContent = th + "%";
  $("#digestToggle").checked = s.settings.digest_enabled;

  // статистика
  const stats = await api("/api/stats");
  const sym = curSym(reg);
  const items = [
    ["🎮", stats.tracked_games, "игр в списке"],
    ["📈", stats.market_alerts, "market-алертов"],
    ["🔔", stats.events_total, "уведомлений"],
    ["🕐", stats.events_week, "за неделю"],
  ];
  $("#statGrid").innerHTML = "";
  items.forEach(([e, n, l], i) => {
    const st = document.createElement("div"); st.className = "stat anim-in";
    st.style.animationDelay = Math.min(i, 6) * 60 + "ms";
    st.innerHTML = `<div class="st-num">${e} ${n}</div><div class="st-lab">${l}</div>`;
    $("#statGrid").appendChild(st);
  });
  const extra = document.createElement("div"); extra.className = "stat anim-in";
  extra.style.animationDelay = "300ms";
  extra.innerHTML = `<div class="st-num">💸 ${fmtMoney(stats.savings_cents, sym)}</div><div class="st-lab">потенциальная экономия</div>`;
  const extra2 = document.createElement("div"); extra2.className = "stat anim-in";
  extra2.style.animationDelay = "360ms";
  extra2.innerHTML = `<div class="st-num">−${stats.best_discount}%</div><div class="st-lab">лучшая скидка</div>`;
  $("#statGrid").append(extra, extra2);

  applyTheme(currentTheme()); // синхронизировать активную кнопку темы при каждом рендере профиля
  refreshPushUI();            // статус пуш-уведомлений (вкл/выкл + разрешение ОС)
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if ([2, 3, 4].includes(m10) && ![12, 13, 14].includes(m100)) return few;
  return many;
}

async function activateTrial() {
  try {
    const r = await post("/api/session/set", { action: "trial" });
    if (!r.ok) { toast("Триал уже был использован", true); }
    else { toast("🎁 Premium активирован на 3 дня!"); await loadSession(); renderProfile(); renderMarketGate(); }
  } catch (e) { errToast(e); }
}
async function buyPremium() {
  try {
    await post("/api/session/set", { action: "premium_buy" });
    toast("💎 Premium оформлен (демо Stars, +30 дней)");
    await loadSession(); renderProfile(); renderMarketGate();
  } catch (e) { errToast(e); }
}
function openProfileAndUpsell() { switchTab("profile"); }

/* --- импорт вишлиста --- */
let wishOpen = false;
function toggleWish() {
  wishOpen = !wishOpen;
  const r = $("#wishResult");
  if (!wishOpen) { r.innerHTML = ""; return; }
  r.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;padding-top:6px">
      <button class="btn btn-primary btn-sm" id="wishDemo">⭐ Демо-вишлист (мгновенно)</button>
      <button class="btn btn-ghost btn-sm" id="wishReal">🌐 Мой steamid64</button>
    </div>
    <div id="wishRealBox" class="hidden" style="display:flex;gap:8px;margin-top:8px">
      <input type="text" id="wishSteamId" placeholder="76561198… или /profiles/…" style="flex:1;padding:10px;background:var(--bg1);border:1px solid var(--line);border-radius:10px;color:var(--text)">
      <button class="btn btn-primary btn-sm" id="wishGo">Импорт</button>
    </div>
    <p class="search-meta">В боте импорт идёт из вашего реального публичного вишлиста Steam. Здесь — демо-набор популярных игр или реальный запрос по числовому id (без ключа API).</p>`;
  r.querySelector("#wishDemo").addEventListener("click", () => runWishImport({ demo: true }));
  r.querySelector("#wishReal").addEventListener("click", () => $("#wishRealBox").classList.toggle("hidden"));
  r.querySelector("#wishGo").addEventListener("click", () => {
    const v = $("#wishSteamId").value.trim();
    if (v) runWishImport({ profile: v });
  });
}
async function runWishImport(body) {
  const out = $("#wishResult");
  out.innerHTML = '<p class="search-status">Импортирую, это займёт немного времени…</p>';
  try {
    const r = await post("/api/wishlist/import", body);
    if (!r.ok) {
      out.innerHTML = `<p class="search-status" style="color:var(--warn)">${esc(r.message || "Не получилось")}</p>`;
      return;
    }
    out.innerHTML = `<p class="search-status" style="color:var(--good)">✅ Импортировано из ${esc(r.source)}: +${r.added}, уже было/пропущено: ${r.skipped}.</p>`;
    await loadGames();
  } catch (e) {
    out.innerHTML = `<p class="search-status" style="color:var(--danger)">Ошибка: ${esc(e.message)}</p>`;
  }
}

/* --- рефералка --- */
async function copyReferral() {
  try {
    const d = await api("/api/referral");
    const share = `https://t.me/share/url?url=${encodeURIComponent(d.link)}&text=${encodeURIComponent("Присоединяйся — вместе следим за скидками в Steam 🔥 SteamSale")}`;
    try { await navigator.clipboard.writeText(d.link); toast("Ссылка скопирована: " + d.link); }
    catch (e) { window.open(share, "_blank"); }
  } catch (e) { errToast(e); }
}

/* ============================================================
   СВЯЗКА СОБЫТИЙ
   ============================================================ */
function bindEvents() {
  $$(".tab-btn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#refreshTop").addEventListener("click", refreshAll);
  $("#regionPill").addEventListener("click", () => switchTab("profile"));

  // игры
  $("#addGameOpen").addEventListener("click", openSearch);
  $("#emptyAddBtn").addEventListener("click", openSearch);
  $("#emptyWishBtn").addEventListener("click", () => { switchTab("profile"); setTimeout(toggleWish, 50); });
  const si = $("#searchInput");
  si.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => runSearch(si.value.trim()), 320); });

  // сделки
  $$("#dealsSeg .seg").forEach((b) => b.addEventListener("click", () => {
    state.dealScope = b.dataset.seg;
    loadDeals();
  }));

  // 🎭 «какая игра — твоя»
  const funOpen = document.getElementById("funOpen");
  if (funOpen) funOpen.addEventListener("click", () => { try { faceOpenFlow(); } catch (e) { errToast(e); } });

  // рынок
  $("#marketAddOpen").addEventListener("click", marketAddToggle);
  bindMarketInput();
  $$("#dirSeg .seg").forEach((b) => b.addEventListener("click", () => {
    $$("#dirSeg .seg").forEach((x) => x.classList.toggle("active", x === b));
    marketGoalHint();
  }));
  $$("#goalSeg .seg").forEach((b) => b.addEventListener("click", () => {
    $$("#goalSeg .seg").forEach((x) => x.classList.toggle("active", x === b));
    state.marketGoalMode = b.dataset.mode || "price";
    const amount = $("#alertAmount");
    if (state.marketGoalMode === "percent") {
      amount.placeholder = "Падение / рост, %";
      amount.value = "";
      amount.min = "1";
    } else {
      amount.placeholder = "Цена-цель, ₽";
      amount.min = "1";
    }
    marketGoalHint();
  }));
  $("#marketCreateBtn").addEventListener("click", createMarketAlert);
  $("#marketOvRefresh").addEventListener("click", () => marketOvLoad(state.marketOvGame || "cs2", true));

  // лента
  // профиль
  $$("#regionSeg .seg").forEach((b) => b.addEventListener("click", async () => {
    if (!state.session?.user?.premium) { toast("Смена региона — Premium-функция (как /region в боте)", true); return; }
    const r = b.dataset.region;
    const btn = b;
    btn.disabled = true;
    try {
      await post("/api/session/set", { action: "region", region: r });
      toast("Регион: " + r.toUpperCase() + ". Цены пересчитаны");
      catState.filters = { preset: "", q: "", sort: "" };
      state.marketOv = null;
      await loadSession(); await loadGames(); await loadDeals();
      renderProfile();
    } catch (e) { errToast(e); }
    btn.disabled = false;
  }));
  $("#thresholdRange").addEventListener("input", (e) => $("#thresholdPill").textContent = e.target.value + "%");
  $("#thresholdRange").addEventListener("change", async (e) => {
    if (!state.session?.user?.premium) { toast("Порог скидки — Premium-функция (как /threshold в боте)", true); return; }
    try { await post("/api/session/set", { action: "threshold", percent: +e.target.value }); toast("Порог: от " + e.target.value + "%"); }
    catch (ex) { errToast(ex); }
  });
  $("#digestToggle").addEventListener("change", async (e) => {
    try { await post("/api/session/set", { action: "digest", enabled: e.target.checked }); toast(e.target.checked ? "Дайджест включён" : "Дайджест выключен"); }
    catch (ex) { errToast(ex); }
  });

  // тема
  const themeSeg = document.getElementById("themeSeg");
  if (themeSeg) themeSeg.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => {
    applyTheme(b.dataset.themeVal);
    toast(b.dataset.themeVal === "light" ? "☀️ Светлая тема" : "🌙 Тёмная тема");
  }));

  // пуши
  const pushT = document.getElementById("pushToggle");
  if (pushT) pushT.addEventListener("change", async (e) => {
    setPushEnabled(e.target.checked);
    refreshPushUI();
    if (e.target.checked) {
      const st = pushSysState();
      if (st === "ask") await requestPushPermission();
      else if (st === "on") toast("🔔 Пуш-уведомления включены");
      else toast(st === "none"
        ? "Баннер сверху работает (системные этот браузер не поддержал)"
        : "Баннер сверху работает · системные запрещены в браузере");
    } else toast("Пуш-уведомления выключены");
  });
  const permBtn = document.getElementById("pushPermBtn");
  if (permBtn) permBtn.addEventListener("click", () => requestPushPermission());

  // импорт вишлиста и рефералка
  $("#wishImportBtn").addEventListener("click", toggleWish);
  $("#referralBtn").addEventListener("click", copyReferral);

  // шторка
  $("#sheetClose").addEventListener("click", closeSheet);
  $("#sheetBackdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

  // повтор подключения
  const netRetry = document.getElementById("netRetry");
  if (netRetry) netRetry.addEventListener("click", () => location.reload());
}

function marketGoalHint() {
  const hint = $("#alertGoalHint");
  if (!hint) return;
  const sym = curSym(state.session?.settings?.region);
  const curTxt = state.marketCur != null ? fmtMoney(state.marketCur, sym) : "цена ещё не получена";
  const dir = ($("#dirSeg .seg.active")?.dataset.dir) || "below";
  const dWord = dir === "below" ? "упадёт" : "вырастет";
  hint.textContent = state.marketGoalMode === "percent"
    ? `Сработает один раз, когда цена ${dWord} на указанный % от текущей (≈ ${curTxt}).`
    : "Сработает один раз — как «лимитная заявка»: пересечёт цель и снимется.";
}

async function createMarketAlert() {
  if (!state.marketPending) { toast("Сначала найдите предмет", true); return; }
  const dir = ($("#dirSeg .seg.active")?.dataset.dir) || "below";
  const mode = state.marketGoalMode || "price";
  const amount = parseFloat($("#alertAmount").value);
  const sym = curSym(state.session?.settings?.region);
  const btn = $("#marketCreateBtn");
  btn.disabled = true; btn.textContent = "Ставлю…";
  const base = { appid: state.marketPending.appid, market_hash_name: state.marketPending.hash,
    direction: dir, icon: state.marketPending.icon || null, type: state.marketPending.type || null };
  let doneMsg;
  if (mode === "percent") {
    if (!amount || amount <= 0 || amount > 95) {
      toast("Процент от 1 до 95", true); btn.disabled = false; btn.textContent = "Поставить алерт"; return;
    }
    Object.assign(base, { mode: "percent", pct: amount, threshold_cents: 0,
      base_cents: state.marketCur != null ? state.marketCur : undefined });
    doneMsg = "Алерт " + (dir === "below" ? "на −" : "на +") + amount + "% от текущей цены";
  } else {
    if (!amount || amount <= 0) { toast("Введите цену-цель", true); btn.disabled = false; btn.textContent = "Поставить алерт"; return; }
    Object.assign(base, { mode: "price", threshold_cents: Math.round(amount * 100) });
    doneMsg = "Алерт: " + (dir === "below" ? "ниже" : "выше") + " " + amount + " " + sym;
  }
  try {
    const r = await post("/api/market/alerts", base);
    if (!r.ok) { toast(r.message || "Не удалось", true); btn.disabled = false; btn.textContent = "Поставить алерт"; return; }
    toast(doneMsg);
    $("#marketInput").value = ""; $("#marketLookupResult").innerHTML = ""; $("#alertForm").classList.add("hidden");
    $("#marketAddPanel").classList.add("hidden"); $("#marketAddOpen").textContent = "＋ Алерт / компас";
    state.marketPending = null; state.marketCur = null;
    await loadMarket();
  } catch (e) { errToast(e); }
  btn.disabled = false; btn.textContent = "Поставить алерт";
}

/* ============================================================
   СТАРТ
   ============================================================ */
async function init() {
  applyTheme(currentTheme());   // применить тему до отрисовки (без «вспышки»)
  bindEvents();
  try {
    await loadSession();
    hideNetOverlay();
  } catch (e) {
    $("#topInfo").textContent = "не удалось подключиться";
    if (e && e.network) showNetOverlay(); else toast("Ошибка: " + (e && e.message), true);
  }
  try {
    await loadGames();
    hideNetOverlay();
  } catch (e) {
    if (e && e.network) showNetOverlay();
  }
  setInterval(pollUnread, 20000);
  pollUnread();
  // мягкий «живой» рефреш кошелька, пока открыта вкладка Игр
  setInterval(() => { if (state.tab === "games") loadWallet(); }, 60000);
  // живой пульс Steam на вкладке «Сделки»
  setInterval(() => { if (state.tab === "deals" && state.dealScope === "global") pulseRefresh(); }, 45000);
  if (tg && tg.expand) { try { tg.expand(); } catch (e) {} }
  // первый визит: подсказка
  setTimeout(() => toast("Живые данные Steam · проверьте вкладки 🔥 и 📈"), 1200);
}

/* ============================================================
   🎭 «КАКАЯ ИГРА — ТВОЯ?» — по фото, прямо на устройстве
   Никакого «AI по лицу»: считаем настроение кадра (свет, тон, энергия,
   контраст) и подбираем архетип из популярных игр. Фото никуда не уходит.
   ============================================================ */
const FACE_ART = (a) => "https://cdn.cloudflare.steamstatic.com/steam/apps/" + a + "/header.jpg";
const FACE_ROSTER = [
  { n: "CS2", a: 730, p: "🎯 Холодный снайпер", v: { e: 82, c: 72, w: 18, d: 52 }, alt: "реакция быстрее, чем вопрос", why: "кадр читается холодно и контрастно — прицел ты наводишь раньше, чем успеваешь подумать." },
  { n: "Dota 2", a: 570, p: "⚔️ Лидер пятёрки", v: { e: 88, c: 36, w: 66, d: 40 }, alt: "тебя слышно даже в замесе", why: "в твоём кадре много энергии и тепла — стиль командного замеса, где ты держишь линию и порядок." },
  { n: "Rust", a: 252490, p: "🏕 Выживальщик с планом", v: { e: 60, c: 46, w: 50, d: 72 }, alt: "у тебя всё под контролем", why: "тёмный насыщенный свет — как рассвет над базой: ты всегда строишь план на три хода вперёд." },
  { n: "Cyberpunk 2077", a: 1091500, p: "🌆 Неоновый скиталец", v: { e: 76, c: 58, w: 56, d: 66 }, alt: "ночной город — твоя сцена", why: "в кадре живут и холодный неон, и тёплые огни — ночная Найт-Сити зовёт тебя." },
  { n: "Elden Ring", a: 1245620, p: "🕯 Путник меж костров", v: { e: 46, c: 42, w: 24, d: 88 }, alt: "спокойствие в темноте", why: "тёмная глубина и минимум шума — ты идешь в неизвестность с факелом и не сдаёшься." },
  { n: "Baldur's Gate 3", a: 1086940, p: "🎲 Дипломат с кубиком", v: { e: 56, c: 30, w: 64, d: 58 }, alt: "убеждаешь даже критический провал", why: "тёплый, живой свет — ты всегда сначала поговоришь, а кубик пусть решает остальное." },
  { n: "GTA V", a: 271590, p: "🕶 Хозяин улиц", v: { e: 78, c: 46, w: 60, d: 60 }, alt: "дело найдётся всегда", why: "энергия и сочный тёплый свет — город у тебя под ногами, осталось выбрать маршрут." },
  { n: "Stardew Valley", a: 413150, p: "🌾 Тёплая душа фермы", v: { e: 40, c: 14, w: 88, d: 16 }, alt: "дом там, где урожай", why: "мягкий тёплый свет — тебе хорошо там, где всё растёт, живёт и поспевает." },
  { n: "Terraria", a: 105600, p: "⛏ Копатель до ядра", v: { e: 72, c: 40, w: 48, d: 34 }, alt: "вечно «ещё один уровень»", why: "ровная живая энергия — ты копаешь глубже, строишь выше и не выходишь из игры «ещё на пять минут»." },
  { n: "Hollow Knight", a: 367520, p: "🐞 Тихий странник", v: { e: 50, c: 62, w: 22, d: 76 }, alt: "слышишь тишину лучше всех", why: "прохладная палитра и глубина — ты замечаешь то, что другие проходят мимо." },
];
function faceNormalize(f) {
  const vals = [f.e, f.c, f.w, f.d];
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const rng = (hi - lo) || 1;
  return { e: (f.e - lo) / rng * 100, c: (f.c - lo) / rng * 100, w: (f.w - lo) / rng * 100, d: (f.d - lo) / rng * 100 };
}
function faceDist(a, b) {
  const dx = a.e - b.e, dy = a.c - b.c, dz = a.w - b.w, dw = a.d - b.d;
  return Math.sqrt(dx * dx + dy * dy + dz * dz + dw * dw);
}
function facePick(features) {
  const f = faceNormalize(features);
  const scored = FACE_ROSTER.map((g) => ({ g, d: faceDist(f, g.v) })).sort((x, y) => x.d - y.d);
  const top = scored[0], second = scored[1] || scored[0];
  const pct = Math.max(56, Math.min(98, Math.round(99 - top.d * 0.72)));
  return { game: top.g, alt: second.g, pct, axes: f };
}
function faceWhyText(game, axes) {
  const order = ["e", "d", "c", "w"].slice().sort((x, y) => axes[y] - axes[x]);
  const labels = { e: "энергичный контраст", d: "глубина и тёмные тона", c: "холодная палитра", w: "тёплый свет" };
  const topTrait = labels[order[0]];
  return { topTrait, text: game.why };
}
function faceTraitPct(axes) {
  const m = { e: ["энергия кадра", "⚡"], d: ["глубина сцены", "🌑"], c: ["холодные тона", "🧊"], w: ["тёплый свет", "🌤"] };
  return ["e", "c", "w", "d"].map((k) => ({ k, lab: m[k][0], ic: m[k][1], v: Math.round(axes[k]) }))
    .sort((a, b) => b.v - a.v);
}
function faceDrawCover(x, src, w, h) {
  const sw = src.naturalWidth || src.width, sh = src.naturalHeight || src.height;
  const k = Math.max(w / sw, h / sh);
  const dw = sw * k, dh = sh * k;
  x.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh);
}
/* вернуть 4 оси из канваса-квадрата */
function faceAxesFromCanvas(cv) {
  const x = cv.getContext("2d");
  const d = x.getImageData(0, 0, cv.width, cv.height).data;
  let n = 0, sl = 0, sl2 = 0, ssat = 0, swarm = 0, scool = 0, sr = 0, sb = 0;
  for (let i = 0; i < d.length; i += 12) {  // каждый 4-й пиксель
    const r = d[i] / 255, g = d[i + 1] / 255, b = d[i + 2] / 255;
    const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    sl += l; sl2 += l * l;
    ssat += (mx - mn) / 255;
    swarm += Math.max(0, r - b);
    scool += Math.max(0, b - r);
    sr += r; sb += b;
    n++;
  }
  const L = sl / n, varL = Math.max(0, sl2 / n - L * L);
  const clamp = (v) => Math.max(0, Math.min(100, v));
  return {
    e: clamp(50 * L + 26 * (ssat / n) * 100 + 18 * Math.min(100, Math.sqrt(varL) * 240)),
    c: clamp((scool / n) * 150 + 8),
    w: clamp((swarm / n) * 150 + 8),
    d: clamp(92 - 55 * L),
  };
}
function faceAxesFromImage(img) {
  const SZ = 96;
  const cv = document.createElement("canvas"); cv.width = SZ; cv.height = SZ;
  faceDrawCover(cv.getContext("2d"), img, SZ, SZ);
  return faceAxesFromCanvas(cv);
}
function faceDemoAxes() {
  // «селфи»-градиент: тёплый закатный свет + холодная тень — честный демо-кадр
  const cv = document.createElement("canvas"); cv.width = 96; cv.height = 96;
  const x = cv.getContext("2d");
  const g = x.createLinearGradient(0, 0, 96, 96);
  g.addColorStop(0, "#ffb454"); g.addColorStop(0.45, "#e07a5f"); g.addColorStop(0.7, "#7f5aa2"); g.addColorStop(1, "#1d2b53");
  x.fillStyle = g; x.fillRect(0, 0, 96, 96);
  x.fillStyle = "rgba(255,255,255,.20)";
  x.beginPath(); x.arc(40, 40, 22, 0, 7); x.fill();
  x.fillStyle = "rgba(10,18,40,.35)";
  x.beginPath(); x.arc(68, 76, 34, 0, 7); x.fill();
  return faceAxesFromCanvas(cv);
}

const FACE_SCAN_LINES = [
  "читаю настроение кадра…", "ищу рефлексы и свет…", "сравниваю с 10 популярными…", "определяю твой архетип…",
];

function faceShareCard(face) {
  const g = face.game;
  return buildShareCard({
    kicker: "Какая игра — твоя · по фото",
    title: g.n + " — " + g.p,
    sub: "по настроению твоего фото. Матч " + face.pct + "%",
    lines: [
      { l: "Матч", r: face.pct + "%", color: "#a1cd44" },
      { l: "Архетип", r: g.p },
      { l: "Альтернатива", r: face.alt.n },
    ],
    footer: "SteamSale · t.me/SteamSaleBot · фото не покидает устройство",
  });
}

function faceCloseAll() {
  const root = document.getElementById("faceRoot");
  if (root) root.innerHTML = "";
  if (!document.querySelectorAll(".ovl").length) document.body.style.overflow = "";
}

function faceOpenFlow() {
  haptic("impact", [10]);
  const root = document.getElementById("faceRoot");
  if (!root) return;
  root.innerHTML = "";
  document.body.style.overflow = "hidden";
  const ovl = document.createElement("div");
  ovl.className = "face-ovl";
  ovl.innerHTML = `
    <div class="face-card">
      <div class="fo-kicker">развлечение · без «AI по лицу» · фото не покидает устройство</div>
      <div class="fo-title">🎭 Какая игра — твоя?</div>
      <label class="fo-drop" id="foDrop">
        <span class="big">📸</span>
        <span>Нажми и выбери фото (или перетащи)</span>
        <span class="fo-drop-note">селфи или любая фотка — посмотрим на свет, тон и энергию кадра и подберём игру из популярных</span>
        <input type="file" id="foFile" accept="image/*" hidden>
      </label>
      <div class="fo-btns">
        <button class="btn btn-ghost btn-sm" id="foDemo" type="button">🎲 Без фото — демо-кадр</button>
        <button class="btn btn-ghost btn-sm" id="foClose" type="button">Закрыть</button>
      </div>
      <p class="fo-priv">🔒 Приватно: файл читается только в твоём браузере и сразу забывается — на сервер ничего не уходит.</p>
    </div>`;
  root.appendChild(ovl);
  const close = () => faceCloseAll();
  ovl.querySelector("#foClose").addEventListener("click", close);
  ovl.addEventListener("click", (e) => { if (e.target === ovl) close(); });
  const fileInput = ovl.querySelector("#foFile");
  ovl.querySelector("#foDrop").addEventListener("click", () => fileInput.click());
  ovl.querySelector("#foDrop").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) faceRunFile(ovl, fileInput.files[0]);
  });
  ovl.querySelector("#foDemo").addEventListener("click", () => faceRunAxes(ovl, faceDemoAxes(), null));
  ["dragover", "dragenter"].forEach((ev) => ovl.querySelector("#foDrop").addEventListener(ev, (e) => { e.preventDefault(); e.target.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => ovl.querySelector("#foDrop").addEventListener(ev, (e) => { e.preventDefault(); e.target.classList.remove("drag"); }));
  ovl.querySelector("#foDrop").addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) faceRunFile(ovl, f);
  });
}

function faceRunFile(ovl, file) {
  const rd = new FileReader();
  rd.onload = () => {
    const img = new Image();
    img.onload = () => faceRunAxes(ovl, faceAxesFromImage(img), rd.result);
    img.onerror = () => { errToast("Не удалось прочитать фото — попробуйте другое"); };
    img.src = rd.result;
  };
  rd.onerror = () => errToast("Файл не читается");
  rd.readAsDataURL(file);
}

function faceRunAxes(ovl, axes, photoDataUrl) {
  haptic("impact", [12]);
  ovl.querySelector(".face-card").innerHTML = `
    <div class="fo-kicker">анализ на устройстве</div>
    <div class="fo-title">Сканирую твой кадр…</div>
    <div class="fo-scan">
      <div class="fo-scan-eye">${photoDataUrl ? `<img src="${photoDataUrl}" alt="">` : ""}</div>
      <div class="fo-scan-bar"><div class="fo-scan-fill"></div></div>
      <div class="fo-scan-lines" id="foScanLines"></div>
    </div>`;
  let li = 0;
  const lineEl = ovl.querySelector("#foScanLines");
  const tick = () => {
    if (!lineEl || !ovl.parentNode) return;
    if (li < FACE_SCAN_LINES.length) lineEl.textContent = FACE_SCAN_LINES[li++];
  };
  tick();
  const iv = setInterval(tick, 380);
  setTimeout(() => {
    clearInterval(iv);
    if (!ovl.parentNode) return;
    faceRenderResult(ovl, facePick(axes), photoDataUrl);
  }, 1750);
}

function faceRenderResult(ovl, face, photoDataUrl) {
  haptic("success", [20, 60, 30]);
  const g = face.game;
  const topTraits = faceTraitPct(face.axes);
  const why = faceWhyText(g, face.axes);
  const chips = topTraits.slice(0, 3).map((t) => `<span class="fo-chip">${t.ic} ${t.lab} ${t.v}%</span>`).join("");
  const shareUrl = faceShareCard(face);
  const cap = "🎭 Моя игра по фото — «" + g.n + "» (" + g.p + "), матч " + face.pct + "%. А какая твоя? t.me/SteamSaleBot";
  ovl.querySelector(".face-card").innerHTML = `
    <div class="fo-kicker">по анализу твоего кадра</div>
    <div class="fo-title">Твоя игра</div>
    <div class="fo-art">
      ${artTag(FACE_ART(g.a), "fo-art-ph", "🎮")}
    </div>
    <div class="fo-match"><b>${face.pct}%</b><span>— матч с твоим кадром</span></div>
    <div class="fo-persona">${esc(g.n)} · ${esc(g.p)}</div>
    <div class="fo-why">🗣 <b>Почему так:</b> ${why.topTrait} в твоём фото. ${esc(g.why)}</div>
    <div class="fo-tags">${chips}</div>
    <div class="fo-why" style="background:rgba(161,205,68,.07)">🌱 <b>Альтернатива:</b> ${esc(face.alt.n)} — ${esc(face.alt.p)}${face.alt.a === g.a ? " (почти 50/50!)" : ""}</div>
    <div class="fo-btns fo-share">
      <button class="btn btn-primary" id="foShare" type="button">📤 Поделиться карточкой</button>
      <button class="btn btn-ghost" id="foAgain" type="button">🔄 Ещё фото</button>
      <button class="btn btn-ghost" id="foClose2" type="button">Закрыть</button>
    </div>
    <p class="fo-priv">🃏 Развлечение: подбор по цвету/свету кадра, а не «AI по лицу» · 🔒 фото не покидает устройство</p>`;
  const back = () => faceOpenFlow();
  ovl.querySelector("#foShare").addEventListener("click", () => {
    try { shareFlow(shareUrl, cap); } catch (e) { errToast(e); }
  });
  ovl.querySelector("#foAgain").addEventListener("click", back);
  const c2 = ovl.querySelector("#foClose2");
  c2.addEventListener("click", () => faceCloseAll());
}
init();
