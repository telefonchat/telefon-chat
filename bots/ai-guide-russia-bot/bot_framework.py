#!/usr/bin/env python3
"""
Bot Framework — Universal event posting bot for Telefon.Chat.

Features:
- Posts events from seed data + web scrapers
- T-30 / T-7 day reminders
- Weekly digest (7/14/30 days)
- Inline keyboard for links
- Persistent event database
- Config-driven: no hardcoded topics

Usage:
    from bot_framework import run_bot
    run_bot("path/to/config.json")
"""

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta, date, time as dt_time
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logger = logging.getLogger("bot_framework")


# ═══════════════════════════════════════════════════════════════════════════════
# Date helpers
# ═══════════════════════════════════════════════════════════════════════════════

RUS_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

def parse_date_russian(text: str) -> Optional[date]:
    if not text:
        return None
    t = text.strip().lower()
    if "прошл" in t:
        return None

    m = re.search(r'(\d{1,2})\s*[–\-—]?\s*\d{1,2}?\s*([а-яё]+)\s*(\d{4})', t)
    if not m:
        m = re.search(r'(\d{1,2})\s+([а-яё]+)\s+(\d{4})', t)
    if m:
        day = int(m.group(1))
        month_name = m.group(2)
        year = int(m.group(3))
        for rus_name, month_num in RUS_MONTHS.items():
            if rus_name in month_name:
                try:
                    return date(year, month_num, day)
                except ValueError:
                    return date(year, month_num, 1)
        return None

    m = re.search(r'([а-яё]+)\s+(\d{4})', t)
    if m:
        month_name = m.group(1)
        year = int(m.group(2))
        for rus_name, month_num in RUS_MONTHS.items():
            if rus_name in month_name:
                return date(year, month_num, 1)

    m = re.search(r'(\d{4})', t)
    if m:
        return date(int(m.group(1)), 6, 1)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Events database
# ═══════════════════════════════════════════════════════════════════════════════

class EventsDB:
    """Persistent event storage with reminder tracking."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.events: list[dict] = []
        self._load()

    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path) as f:
                    self.events = json.load(f)
            except:
                self.events = []
        else:
            self.events = []

    def _save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    def exists(self, url: str) -> bool:
        return any(e.get("url") == url for e in self.events)

    def add(self, event: dict):
        if self.exists(event.get("url", "")):
            return
        parsed = parse_date_russian(event.get("date", ""))
        entry = {
            "title": event.get("title", ""),
            "url": event.get("url", ""),
            "date_str": event.get("date", ""),
            "city": event.get("city", ""),
            "source": event.get("source", ""),
            "event_type": event.get("event_type", "Мероприятие"),
            "description": event.get("description", ""),
            "price": event.get("price", ""),
            "start_date": parsed.isoformat() if parsed else None,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "posted_to_chat": False,
            "reminder_30_sent": False,
            "reminder_7_sent": False,
        }
        self.events.append(entry)
        self._save()
        logger.info(f"Added: {entry['title'][:50]}")

    def get_pending_30day(self) -> list[dict]:
        today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        results = []
        for e in self.events:
            if e.get("reminder_30_sent") or not e.get("start_date"):
                continue
            try:
                ed = date.fromisoformat(e["start_date"])
                if 25 <= (ed - today).days <= 35:
                    results.append(e)
            except:
                pass
        return results

    def get_pending_7day(self) -> list[dict]:
        today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        results = []
        for e in self.events:
            if e.get("reminder_7_sent") or not e.get("start_date"):
                continue
            try:
                ed = date.fromisoformat(e["start_date"])
                if 5 <= (ed - today).days <= 9:
                    results.append(e)
            except:
                pass
        return results

    def get_upcoming(self, days_ahead: int) -> list[dict]:
        today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        limit = today + timedelta(days=days_ahead)
        results = []
        for e in self.events:
            sd = e.get("start_date")
            if not sd:
                continue
            try:
                ed = date.fromisoformat(sd)
                if today <= ed <= limit:
                    results.append(e)
            except:
                pass
        results.sort(key=lambda x: x.get("start_date", ""))
        return results

    def get_unposted(self) -> list[dict]:
        return [e for e in self.events if not e.get("posted_to_chat")]

    def mark_posted(self, url: str):
        for e in self.events:
            if e.get("url") == url:
                e["posted_to_chat"] = True
                self._save()
                return

    def mark_reminder(self, url: str, rtype: str):
        for e in self.events:
            if e.get("url") == url:
                if rtype == "30": e["reminder_30_sent"] = True
                if rtype == "7": e["reminder_7_sent"] = True
                self._save()
                return

    def stats(self) -> dict:
        total = len(self.events)
        with_dates = sum(1 for e in self.events if e.get("start_date"))
        posted = sum(1 for e in self.events if e.get("posted_to_chat"))
        return {"total": total, "with_dates": with_dates, "posted": posted}


# ═══════════════════════════════════════════════════════════════════════════════
# Source manager
# ═══════════════════════════════════════════════════════════════════════════════

def detect_type(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ["конференци", "conference", "summit"]): return "Конференция"
    if any(w in t for w in ["митап", "meetup", "встреч"]): return "Митап"
    if any(w in t for w in ["хакатон", "hackathon"]): return "Хакатон"
    if any(w in t for w in ["воркшоп", "workshop", "мастер"]): return "Воркшоп"
    if any(w in t for w in ["выставк", "expo"]): return "Выставка"
    if any(w in t for w in ["вебинар", "webinar"]): return "Вебинар"
    return "Мероприятие"


class SeedSource:
    """Provides events from config file seed data."""

    def __init__(self, config: dict):
        self.seed = config.get("seed_events", [])

    async def fetch(self, db: EventsDB) -> list[dict]:
        return [ev for ev in self.seed if not db.exists(ev.get("url", ""))]


class HightimeScraper:
    """Scrapes hightime.media for events matching a topic."""

    def __init__(self, topic_url: str):
        self.topic_url = topic_url

    async def fetch(self, db: EventsDB) -> list[dict]:
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(self.topic_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return events
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup.find_all(["h2", "h3", "h4"]):
                    text = tag.get_text(strip=True)
                    if len(text) < 10 or "подробнее" in text.lower():
                        continue
                    parent = tag.find_parent(["section", "div", "article"])
                    if not parent:
                        continue
                    links = parent.find_all("a", href=True)
                    url = ""
                    for link in links:
                        href = link.get("href", "")
                        if href.startswith("http") and "hightime" not in href:
                            url = href
                            break
                    if not url or db.exists(url):
                        continue
                    parent_text = parent.get_text()
                    date_text = ""
                    city_text = ""
                    dm = re.search(r'\d{1,2}\s*[–\-—]\s*\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\s+\d{4}', parent_text)
                    if dm:
                        date_text = dm.group(0)
                    for city in ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Онлайн"]:
                        if city in parent_text:
                            city_text = city
                            break
                    events.append({
                        "title": text,
                        "description": "",
                        "date": date_text,
                        "city": city_text or "Россия",
                        "url": url,
                        "source": "HighTime Media",
                        "event_type": "Конференция",
                    })
        except Exception as e:
            logger.error(f"Hightime error: {e}")
        return events


class TimePadScraper:
    """Scrapes timepad.ru via free public API. Needs a developer token."""

    CATEGORY_KEYWORDS = {
        "tech": ["конференци", "ИТ и", "форум", "хакатон", "tech", "IT"],
        "education": ["образован", "курс", "мастер-класс", "тренинг", "семинар", "лекци", "вебинар"],
        "business": ["бизнес", "нетворк", "startup", "инвестици", "предпринимател"],
        "gamedev": ["game", "gamedev", "геймдев", "игр"],
        "med": ["медицин", "биотех", "health", "здравоохранен"],
        "build": ["строител", "expo", "выставк", "оборудован", "садовод"],
    }

    def __init__(self, token: str, keywords: list[str] = None, max_events: int = 10):
        """
        Args:
            token: TimePad developer API token
            keywords: list of keywords to filter by (URL-encoded automatically)
            max_events: max events to fetch per cycle
        """
        self.token = token
        self.keywords = keywords or []
        self.max_events = max_events

    async def fetch(self, db: EventsDB) -> list[dict]:
        events = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        six_months = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%d")

        for keyword in self.keywords or [""]:
            try:
                params = {
                    "limit": self.max_events,
                    "fields": "name,description_short,starts_at,location,url,categories",
                    "access_statuses": "public",
                    "starts_at_min": today,
                    "starts_at_max": six_months,
                    "sort": "starts_at",
                }
                if keyword:
                    params["keywords"] = keyword

                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://api.timepad.ru/v1/events",
                        params=params,
                        headers={
                            "Authorization": f"Bearer {self.token}",
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    for item in data.get("values", []):
                        name = item.get("name", "")
                        url = item.get("url", "")
                        if not name or not url or db.exists(url):
                            continue

                        loc = item.get("location", {}) or {}
                        city = loc.get("city", "") if isinstance(loc, dict) else ""
                        if not city:
                            continue

                        starts = item.get("starts_at", "")
                        date_str = ""
                        if starts:
                            try:
                                dt = datetime.fromisoformat(starts.replace("Z", "+00:00"))
                                months = ["января","февраля","марта","апреля","мая","июня",
                                          "июля","августа","сентября","октября","ноября","декабря"]
                                date_str = f"{dt.day} {months[dt.month-1]} {dt.year}"
                            except:
                                date_str = starts[:10]

                        cats = item.get("categories", [])
                        cat_names = [c.get("name", "") for c in cats if isinstance(c, dict)]
                        etype = "Мероприятие"
                        if any("бизнес" in (c or "").lower() for c in cat_names):
                            etype = "Бизнес-событие"
                        elif any("ИТ" in (c or "") for c in cat_names):
                            etype = "Конференция"
                        elif any("образован" in (c or "").lower() for c in cat_names):
                            etype = "Обучение"

                        desc = item.get("description_short", "") or ""
                        desc_clean = re.sub(r"<[^>]+>", "", desc)[:300] if desc else ""

                        events.append({
                            "title": name.strip(),
                            "description": desc_clean,
                            "date": date_str,
                            "city": city,
                            "url": url,
                            "source": "TimePad",
                            "event_type": etype,
                        })

            except Exception as e:
                logger.error(f"TimePad error ({keyword}): {e}")

        return events


class KudaGoScraper:
    """Scrapes kudago.com via free public API. No auth needed."""

    # KudaGo city slugs
    CITIES = {
        "msk": "Москва",
        "spb": "Санкт-Петербург",
        "kazan": "Казань",
        "nsk": "Новосибирск",
        "ekb": "Екатеринбург",
        "nnv": "Нижний Новгород",
        "samara": "Самара",
        "krd": "Краснодар",
        "sochi": "Сочи",
        "ufa": "Уфа",
        "krasnoyarsk": "Красноярск",
        "perm": "Пермь",
        "vlg": "Волгоград",
        "voronezh": "Воронеж",
        "rostov": "Ростов-на-Дону",
        "tula": "Тула",
        "chel": "Челябинск",
    }

    def __init__(self, cities: list[str], categories: list[str], max_per_city: int = 5):
        """
        Args:
            cities: KudaGo city slugs (e.g. ['msk', 'spb'])
            categories: category slugs (e.g. ['business-events', 'education'])
            max_per_city: max events per city per cycle
        """
        self.cities = cities or ["msk"]
        self.categories = categories or []
        self.max_per_city = max_per_city

    async def fetch(self, db: EventsDB) -> list[dict]:
        events = []
        now = int(datetime.now(timezone.utc).timestamp())
        ninety_days_ago = now - 86400 * 90

        for city in self.cities:
            try:
                params = {
                    "location": city,
                    "page_size": self.max_per_city,
                    "fields": "title,description,dates,site_url,categories,location",
                    "actual_since": ninety_days_ago,
                    "order_by": "-publication_date",
                    "ctype": "event",
                }

                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://kudago.com/public-api/v1.4/events/",
                        params=params,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    for result in data.get("results", []):
                        title = result.get("title", "")
                        site_url = result.get("site_url", "")
                        if not title or not site_url or db.exists(site_url):
                            continue

                        # Filter by category if specified
                        cats = result.get("categories", [])
                        if self.categories and not any(c in cats for c in self.categories):
                            continue

                        dates = result.get("dates", [])
                        date_str = ""
                        if dates:
                            start_ts = dates[0].get("start", 0)
                            if start_ts:
                                try:
                                    dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
                                    date_str = dt.strftime("%d %B %Y").replace(" ", " ").replace(
                                        "January","января").replace("February","февраля").replace(
                                        "March","марта").replace("April","апреля").replace(
                                        "May","мая").replace("June","июня").replace("July","июля").replace(
                                        "August","августа").replace("September","сентября").replace(
                                        "October","октября").replace("November","ноября").replace(
                                        "December","декабря")
                                except:
                                    pass

                        description = result.get("description", "")
                        desc_clean = ""
                        if description:
                            desc_clean = re.sub(r"<[^>]+>", "", description)[:300]

                        city_name = self.CITIES.get(city, city.capitalize())
                        raw_cats = result.get("categories", [])
                        etype = "Мероприятие"
                        if "business-events" in raw_cats:
                            etype = "Бизнес-событие"
                        elif "education" in raw_cats:
                            etype = "Обучение"
                        elif "exhibition" in raw_cats:
                            etype = "Выставка"
                        elif "festival" in raw_cats:
                            etype = "Фестиваль"

                        events.append({
                            "title": title.strip(),
                            "description": desc_clean,
                            "date": date_str,
                            "city": city_name,
                            "url": site_url,
                            "source": "KudaGo",
                            "event_type": etype,
                        })

            except Exception as e:
                logger.error(f"KudaGo error for {city}: {e}")

        return events


# ═══════════════════════════════════════════════════════════════════════════════
# Post formatting
# ═══════════════════════════════════════════════════════════════════════════════

def days_until(event_date: date) -> Optional[int]:
    today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
    delta = (event_date - today).days
    return delta if delta >= 0 else None

def time_left_text(d: int) -> str:
    if d <= 0: return "🏁 Уже сегодня!" if d == 0 else "🏁 Прошло"
    if d == 1: return "⏰ Завтра!"
    if d <= 7: return f"⏰ Через {d} дн."
    if d <= 14: return f"⏰ Через {d} дн."
    if d <= 30: return f"⏰ Через {d} дн."
    return f"📅 Через {d} дн."

STYLE_HEADERS = {
    "new": "{emoji} <b>{name}</b> — Новый анонс",
    "reminder_30": "{emoji} <b>{name}</b> — Месяц до мероприятия!",
    "reminder_7": "{emoji} <b>{name}</b> — Неделя до мероприятия!",
    "digest": "{emoji} <b>{name}</b> — Еженедельный дайджест",
}

def format_post(cfg: dict, event: dict, style: str = "new") -> tuple:
    """Format event post. Returns (html_text, event_id, url)."""
    title = event.get("title", cfg.get("bot_name", ""))
    date_str = event.get("date_str", event.get("date", "Дата уточняется"))
    city = event.get("city", "Россия")
    url = event.get("url", "")
    source = event.get("source", "")
    etype = event.get("event_type") or detect_type(title)
    price = event.get("price", "")
    desc = event.get("description", "")
    event_id = str(abs(hash(url))) if url else str(random.randint(100000, 999999))

    day_left = ""
    sd = event.get("start_date")
    if sd:
        try:
            ed = date.fromisoformat(sd) if isinstance(sd, str) else sd
            dl = days_until(ed)
            if dl is not None and dl >= 0:
                day_left = time_left_text(dl)
        except:
            pass

    header = STYLE_HEADERS.get(style, STYLE_HEADERS["new"]).format(
        emoji=cfg.get("emoji", "📌"), name=cfg.get("bot_name", "")
    )

    text = f"{header}\n\n"
    text += f"<b>{title}</b>\n"
    # Compact meta line
    meta = []
    if date_str:
        meta.append(f"📅 {date_str}")
    if city:
        meta.append(f"📍 {city}")
    meta.append(f"🏷 {etype}")
    text += " · ".join(meta)
    if price:
        text += f"\n💰 {price}"
    if day_left:
        text += f"\n{day_left}"
    if desc:
        d = re.sub(r"<[^>]+>", "", desc)[:250]
        text += f"\n\n{d}"
    if source:
        text += f"\n\n<i>Источник: {source}</i>"
    return text, event_id, url


def format_digest(cfg: dict, ev7: list, ev14: list, ev30: list) -> str:
    """Format weekly digest."""
    e = cfg.get("emoji", "📌")
    name = cfg.get("bot_name", "")
    text = f"{e} <b>{name} — Дайджест</b>\n"

    if ev7:
        text += "\n📅 <b>На этой неделе (7 дней):</b>\n"
        for e in ev7:
            d = e.get("date_str", e.get("date", ""))
            text += f"• <b>{e['title'][:60]}</b> — {d}\n"

    if ev14:
        text += "\n📅 <b>В ближайшие 2 недели:</b>\n"
        for e in ev14:
            d = e.get("date_str", e.get("date", ""))
            text += f"• {e['title'][:60]} — {d}\n"

    if ev30:
        text += "\n📅 <b>В ближайший месяц:</b>\n"
        for e in ev30:
            d = e.get("date_str", e.get("date", ""))
            text += f"• {e['title'][:60]} — {d}\n"

    if not (ev7 or ev14 or ev30):
        text += "\nПока нет запланированных мероприятий."

    tag = cfg.get("bot_username", "")
    if tag:
        text += f"\n\n🤖 @{tag}"
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# Bot handlers
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["config"]
    await update.message.reply_text(
        f"👋 {cfg.get('emoji', '')} <b>{cfg['bot_name']}</b>\n\n"
        f"{cfg.get('description', '')}\n\n"
        "/help — справка\n/stats — статистика",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["config"]
    text = f"🤖 {cfg.get('emoji', '')} <b>{cfg['bot_name']}</b>\n\n"
    text += f"{cfg.get('help_text', '')}\n\n"
    text += "Команды:\n/stats — статистика\n/help — справка"
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db: EventsDB = ctx.bot_data["db"]
    s = ctx.bot_data["stats"]
    st = db.stats()
    await update.message.reply_text(
        f"📊 <b>{ctx.bot_data['config']['bot_name']}</b>\n\n"
        f"📝 Постов отправлено: {s['posts']}\n"
        f"🔗 Ссылок отправлено: {s['links']}\n"
        f"🗂 Всего событий в БД: {st['total']}\n"
        f"📅 С датами: {st['with_dates']}\n"
        f"✅ Опубликовано: {st['posted']}",
        parse_mode="HTML"
    )

async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("event_"):
        eid = data.replace("event_", "")
        urls = ctx.bot_data.get("event_urls", {})
        url = urls.get(eid, "")
        if url:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except:
                pass
            try:
                await ctx.bot.send_message(
                    chat_id=query.from_user.id,
                    text=f"🔗 <b>Ссылка на регистрацию:</b>\n\n{url}",
                    parse_mode="HTML"
                )
            except:
                await query.message.reply_text(f"🔗 {url}")
            ctx.bot_data["stats"]["links"] += 1

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}")


# ═══════════════════════════════════════════════════════════════════════════════
# Posting engine
# ═══════════════════════════════════════════════════════════════════════════════

async def send_message(ctx: ContextTypes.DEFAULT_TYPE, text: str,
                         event_id: str = "", url: str = "") -> bool:
    """Send as chat message (sendMessage in Bot API)."""
    cfg = ctx.bot_data["config"]
    chat_id = cfg.get("chat_id", ctx.bot_data.get("chat_id", ""))
    if not chat_id:
        logger.error("No chat_id configured")
        return False

    keyboard = [[
        InlineKeyboardButton(
            cfg.get("button_text", "📎 Подробнее о мероприятии"),
            callback_data=f"event_{event_id}"
        )]
    ] if event_id else None
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    try:
        await ctx.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
        if url and event_id:
            urls = ctx.bot_data.get("event_urls", {})
            urls[event_id] = url
            ctx.bot_data["event_urls"] = urls
        return True
    except Exception as e:
        logger.warning(f"Keyboard failed ({e}), trying text-only")
        try:
            await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            return True
        except Exception as e2:
            logger.error(f"Text-only failed: {e2}")
            return False


async def create_post(ctx: ContextTypes.DEFAULT_TYPE, text: str,
                       event_id: str = "", url: str = "") -> bool:
    """Post to user page (createPost Bot API method).
    Auto-syncs to chat, supports comments and reactions."""
    cfg = ctx.bot_data["config"]
    token = cfg["token"]
    api_base = cfg.get("api_base", "https://api.telefon.chat/api/bot")

    buttons = []
    if url and event_id:
        btn_text = cfg.get("button_text", "📎 Подробнее")
        buttons = [{"text": btn_text, "url": url}]

    payload = {
        "text": text,
        "parse_mode": "HTML",
        "comments_enabled": True,
        "allow_reposts": True,
    }
    if buttons:
        payload["buttons"] = buttons

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{api_base}/{token}/createPost",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            result = resp.json()
            if result.get("ok"):
                stats = ctx.bot_data["stats"]
                stats["posts"] += 1
                post_url = result.get("result", {}).get("url", "")
                if post_url:
                    logger.info(f"Post created: {post_url}")
                return True
            else:
                logger.warning(f"createPost failed: {result.get('description', '')}")
                # Fallback to sendMessage
                return await send_message(ctx, text, event_id, url)
    except Exception as e:
        logger.error(f"createPost error: {e}")
        return await send_message(ctx, text, event_id, url)


async def send_post(ctx: ContextTypes.DEFAULT_TYPE, text: str,
                    event_id: str = "", url: str = "") -> bool:
    """Send post using configured method."""
    cfg = ctx.bot_data["config"]
    method = cfg.get("post_method", "sendMessage")
    if method == "createPost":
        return await create_post(ctx, text, event_id, url)
    return await send_message(ctx, text, event_id, url)


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduled jobs
# ═══════════════════════════════════════════════════════════════════════════════

async def job_new_events(ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["config"]
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    wh = cfg.get("work_hours", {"start": 8, "end": 24})
    if now.hour < wh["start"] or now.hour >= wh["end"]:
        return

    db: EventsDB = ctx.bot_data["db"]
    sources = ctx.bot_data["sources"]

    all_events = []
    for src in sources:
        try:
            evs = await src.fetch(db)
            all_events.extend(evs)
        except Exception as e:
            logger.error(f"Source error: {e}")

    if not all_events:
        return

    limit = cfg.get("posts_per_cycle", 3)
    delay = cfg.get("delay_between_posts", 10)
    posted = 0

    for event in all_events[:limit]:
        db.add(event)
        text, eid, url = format_post(cfg, event, "new")
        ok = await send_post(ctx, text, eid, url)
        if ok:
            db.mark_posted(event.get("url", ""))
            posted += 1
        await asyncio.sleep(delay)

    ctx.bot_data["stats"]["posts"] += posted
    logger.info(f"Posted {posted} new events")


async def job_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["config"]
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    wh = cfg.get("work_hours", {"start": 8, "end": 24})
    if now.hour < wh["start"] or now.hour >= wh["end"]:
        return

    db: EventsDB = ctx.bot_data["db"]
    delay = cfg.get("delay_between_posts", 10)

    for ev in db.get_pending_30day():
        text, eid, url = format_post(cfg, ev, "reminder_30")
        ok = await send_post(ctx, text, eid, url)
        if ok:
            db.mark_reminder(ev.get("url", ""), "30")
        await asyncio.sleep(delay)

    for ev in db.get_pending_7day():
        text, eid, url = format_post(cfg, ev, "reminder_7")
        ok = await send_post(ctx, text, eid, url)
        if ok:
            db.mark_reminder(ev.get("url", ""), "7")
        await asyncio.sleep(delay)


async def job_digest(ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["config"]
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    wh = cfg.get("work_hours", {"start": 8, "end": 24})
    if now.hour < wh["start"] or now.hour >= wh["end"]:
        return

    db: EventsDB = ctx.bot_data["db"]
    ev7 = db.get_upcoming(7)
    ev14 = db.get_upcoming(14)
    ev30 = db.get_upcoming(30)

    if not (ev7 or ev14 or ev30):
        return

    text = format_digest(cfg, ev7, ev14, ev30)
    ok = await send_post(ctx, text)
    if ok:
        ctx.bot_data["stats"]["posts"] += 1
        logger.info("Digest sent")


# ═══════════════════════════════════════════════════════════════════════════════
# Source factory
# ═══════════════════════════════════════════════════════════════════════════════

def build_sources(config: dict) -> list:
    """Create source instances from config."""
    sources = [SeedSource(config)]

    for src_cfg in config.get("sources", []):
        if src_cfg.get("type") == "hightime" and src_cfg.get("url"):
            sources.append(HightimeScraper(src_cfg["url"]))
        elif src_cfg.get("type") == "timepad" and src_cfg.get("token"):
            sources.append(TimePadScraper(
                token=src_cfg["token"],
                keywords=src_cfg.get("keywords", []),
                max_events=src_cfg.get("max_events", 10),
            ))
        elif src_cfg.get("type") == "kudago":
            sources.append(KudaGoScraper(
                cities=src_cfg.get("cities", ["msk"]),
                categories=src_cfg.get("categories", []),
                max_per_city=src_cfg.get("max_per_city", 5),
            ))

    return sources


# ═══════════════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════════════

def run_bot(config_path: str):
    """Load config and start the bot."""

    if not os.path.exists(config_path):
        logger.error(f"Config not found: {config_path}")
        return

    with open(config_path) as f:
        config = json.load(f)

    token = config.get("token")
    api_base = config.get("api_base", "https://api.telefon.chat/api/bot")
    db_path = config.get("db_path", os.path.join(os.path.dirname(config_path), "events_db.json"))
    bot_name = config.get("bot_name", "Bot")
    chat_id = config.get("chat_id", "")
    check_interval = config.get("check_interval_minutes", 90) * 60
    digest_day = config.get("digest_day", 5)       # 0=Mon..6=Sun; default Sat
    digest_time_h = config.get("digest_time_hour", 9)  # UTC

    if not token:
        logger.error("No token in config")
        return

    logger.info(f"Starting bot: {bot_name}")

    db = EventsDB(db_path)
    sources = build_sources(config)

    app = Application.builder().token(token).base_url(f"{api_base}/").build()
    app.bot_data["config"] = config
    app.bot_data["db"] = db
    app.bot_data["sources"] = sources
    app.bot_data["stats"] = {"posts": 0, "links": 0, "events_found": 0}
    app.bot_data["event_urls"] = {}

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(button_callback, pattern=r"^event_"))
    app.add_error_handler(error_handler)

    jq = app.job_queue
    jq.run_repeating(job_new_events, interval=check_interval, first=10)
    jq.run_repeating(job_reminders, interval=check_interval, first=30)
    jq.run_daily(job_digest, time=dt_time(digest_time_h, 0), days=(digest_day,))

    # Run
    async def _run():
        await app.initialize()
        await app.start()
        updater = app.updater
        await updater.start_polling(allowed_updates=["message", "callback_query"])
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await updater.stop()
            await app.stop()
            await app.shutdown()

    asyncio.run(_run())
