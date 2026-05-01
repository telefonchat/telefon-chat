#!/usr/bin/env python3
"""
AI Guide Russia — Bot-blogger for AI events across Russia.

Workflow:
1. Fetches events from multiple sources (seed, scraping, web search)
2. Posts new events immediately upon discovery
3. Sends reminders T-30 days, T-7 days before each event
4. Weekly digest: events in next 7 / 14 / 30 days

Powered by Telefon.Chat Bot API
"""

import asyncio
import json
import logging
import os
import random
import re
import time as time_module
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN = "1777623970767604:xm-A-72XxypV1eJggVlFqSBNI_bJInyK"
API_BASE = "https://api.telefon.chat/api/bot"
WORKING_DIR = "/opt/bots/ai_guide"

MSK_OFFSET = timedelta(hours=3)
WORK_START_HOUR = 8
WORK_END_HOUR = 24
CHECK_INTERVAL = 90  # minutes

POSTS_PER_CYCLE = 3
SECONDS_BETWEEN_POSTS = 10  # delay to avoid rate limits

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("ai_guide")

# ─── Date helpers ─────────────────────────────────────────────────────────────

RUS_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
ENG_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def parse_date_russian(text: str) -> Optional[date]:
    """Parse Russian date strings like '25-26 июня 2026' or 'Июль 2026'."""
    if not text:
        return None
    t = text.strip().lower()

    # Skip past events
    if "прошл" in t:
        return None

    # Pattern: "25-26 июня 2026" or "25 июня 2026"
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

    # Pattern: "Июль 2026" (month only)
    m = re.search(r'([а-яё]+)\s+(\d{4})', t)
    if m:
        month_name = m.group(1)
        year = int(m.group(2))
        for rus_name, month_num in RUS_MONTHS.items():
            if rus_name in month_name:
                return date(year, month_num, 1)

    # Pattern: "2026" only
    m = re.search(r'(\d{4})', t)
    if m:
        return date(int(m.group(1)), 6, 1)  # Approximate to mid-year

    return None

def days_until(event_date: date) -> Optional[int]:
    """Calculate days from today (MSK) until event_date."""
    if not event_date:
        return None
    today = (datetime.now(timezone.utc) + MSK_OFFSET).date()
    delta = (event_date - today).days
    return delta if delta >= 0 else None

# ─── Events DB ────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(WORKING_DIR, "events_db.json")

class EventsDB:
    """Persistent event storage with reminder tracking."""

    def __init__(self):
        self.events: list[dict] = []  # List of event dicts with extra tracking fields
        self._load()

    def _load(self):
        if os.path.exists(DB_PATH):
            try:
                with open(DB_PATH) as f:
                    self.events = json.load(f)
                    logger.info(f"Loaded {len(self.events)} events from DB")
            except:
                self.events = []
        else:
            self.events = []

    def _save(self):
        os.makedirs(WORKING_DIR, exist_ok=True)
        with open(DB_PATH, "w") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    def exists(self, url: str) -> bool:
        return any(e.get("url") == url for e in self.events)

    def add(self, event: dict):
        """Add event with tracking fields."""
        if self.exists(event.get("url", "")):
            return
        entry = {
            "title": event.get("title", "AI-мероприятие"),
            "url": event.get("url", ""),
            "date_str": event.get("date", ""),
            "city": event.get("city", "Россия"),
            "source": event.get("source", ""),
            "event_type": event.get("event_type", "Мероприятие"),
            "description": event.get("description", ""),
            "price": event.get("price", ""),
            # Parsed date (ISO format or null)
            "start_date": None,
            # Reminder tracking
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "reminder_30_sent": False,
            "reminder_7_sent": False,
            # Weekly digest tracking
            "included_in_digest": False,
        }
        # Try to parse date
        parsed = parse_date_russian(event.get("date", ""))
        if parsed:
            entry["start_date"] = parsed.isoformat()

        self.events.append(entry)
        self._save()
        logger.info(f"Added event to DB: {entry['title'][:50]}")

    def get_pending_30day(self) -> list[dict]:
        """Events that need T-30 reminder."""
        today = (datetime.now(timezone.utc) + MSK_OFFSET).date()
        results = []
        for e in self.events:
            if e.get("reminder_30_sent"):
                continue
            sd = e.get("start_date")
            if not sd:
                continue
            try:
                event_date = date.fromisoformat(sd)
                delta = (event_date - today).days
                if 25 <= delta <= 35:  # ~30 days window
                    results.append(e)
            except:
                pass
        return results

    def get_pending_7day(self) -> list[dict]:
        """Events that need T-7 reminder."""
        today = (datetime.now(timezone.utc) + MSK_OFFSET).date()
        results = []
        for e in self.events:
            if e.get("reminder_7_sent"):
                continue
            sd = e.get("start_date")
            if not sd:
                continue
            try:
                event_date = date.fromisoformat(sd)
                delta = (event_date - today).days
                if 5 <= delta <= 9:  # ~7 days window
                    results.append(e)
            except:
                pass
        return results

    def get_upcoming(self, days_ahead: int) -> list[dict]:
        """Events happening within `days_ahead` days."""
        today = (datetime.now(timezone.utc) + MSK_OFFSET).date()
        limit = today + timedelta(days=days_ahead)
        results = []
        for e in self.events:
            sd = e.get("start_date")
            if not sd:
                continue
            try:
                event_date = date.fromisoformat(sd)
                if today <= event_date <= limit:
                    results.append(e)
            except:
                pass
        # Sort by closest first
        results.sort(key=lambda x: x.get("start_date", ""))
        return results

    def get_unposted(self) -> list[dict]:
        """Events that were added to DB but never posted to chat."""
        return [e for e in self.events if not e.get("posted_to_chat", False)]

    def mark_posted_to_chat(self, url: str):
        for e in self.events:
            if e.get("url") == url:
                e["posted_to_chat"] = True
                self._save()
                return

    def mark_reminder_sent(self, url: str, reminder_type: str):
        for e in self.events:
            if e.get("url") == url:
                if reminder_type == "30":
                    e["reminder_30_sent"] = True
                elif reminder_type == "7":
                    e["reminder_7_sent"] = True
                self._save()
                return

# ─── Event Sources ────────────────────────────────────────────────────────────

SEED_EVENTS = [
    {
        "title": "Conversations 2026 — Конференция по генеративному AI",
        "description": "2 дня, 4 трека, 70+ спикеров. Бизнес и AI-трансформация, стартапы, GenAI в продуктах, технологии, ИИ-агенты.",
        "date": "25-26 июня 2026",
        "city": "Санкт-Петербург",
        "url": "https://conversations-ai.com/",
        "source": "HighTime Media",
        "event_type": "Конференция",
        "price": "от 29 900 ₽"
    },
    {
        "title": "Tech Week 2026 — Технологическая конференция",
        "description": "ИИ, разработка, ритейл, e-commerce, цифровизация, логистика. Выставка ведущих компаний.",
        "date": "17-18 июня 2026",
        "city": "Москва",
        "url": "https://techweek.moscow/",
        "source": "HighTime Media",
        "event_type": "Конференция",
        "price": "от 20 000 ₽"
    },
    {
        "title": "PyCon Russia 2026 — Крупнейшая Python-конференция",
        "description": "700+ участников, 35+ докладов. Data Science, ML/AI, backend, инфраструктура для Python- и ML-инженеров.",
        "date": "Июль 2026",
        "city": "Москва",
        "url": "https://www.pycon.ru/",
        "source": "PyCon Russia",
        "event_type": "Конференция"
    },
    {
        "title": "Летняя Conversations 2026",
        "description": "2 дня, 4 трека, 40+ докладов, дискуссии, нетворкинг. Крупнейшее событие по GenAI в России.",
        "date": "25-26 июня 2026",
        "city": "Санкт-Петербург",
        "url": "https://all-events.ru/events/letnyaya-conversations-2026/",
        "source": "All-Events",
        "event_type": "Конференция"
    },
    {
        "title": "AI Future Forum",
        "description": "Форум по будущему искусственного интеллекта. Ведущие эксперты и практики AI-трансформации.",
        "date": "14-15 апреля 2026",
        "city": "Москва",
        "url": "https://hightime.media/it-conferences/ai/",
        "source": "HighTime Media",
        "event_type": "Конференция"
    },
    {
        "title": "IT Purple Conf",
        "description": "Конференция по AI и IT-технологиям. Практические кейсы и технологии.",
        "date": "18 апреля 2026",
        "city": "Москва",
        "url": "https://hightime.media/it-conferences/ai/",
        "source": "HighTime Media",
        "event_type": "Конференция"
    },
    {
        "title": "STORMCONF26 — BPM и AI в бизнесе",
        "description": "BPM и AI вместе в бизнесе. Кейсы крупных компаний, управленческие метрики, автоматизация.",
        "date": "10 апреля 2026",
        "city": "Москва",
        "url": "https://hightime.media/it-conferences/ai/",
        "source": "HighTime Media",
        "event_type": "Конференция"
    },
    {
        "title": "Neo4j Nodes — Engineering Better Intelligence",
        "description": "Графовые БД и AI. Машинное обучение, анализ данных, интеллектуальные системы.",
        "date": "2026",
        "city": "Онлайн",
        "url": "https://neo4j.com/nodes/",
        "source": "10Times",
        "event_type": "Конференция"
    },
    {
        "title": "Machine Learning Developers Summit",
        "description": "ML-разработка, MLOps, продакшн ML-систем, инфраструктура для моделей.",
        "date": "2026",
        "city": "Онлайн",
        "url": "https://www.mlds.com/",
        "source": "10Times",
        "event_type": "Саммит"
    },
]

class SourceManager:
    """Multi-source event fetcher."""

    def __init__(self, db: EventsDB):
        self.db = db

    async def fetch_seed(self) -> list[dict]:
        """Return seed events not yet in DB."""
        return [ev for ev in SEED_EVENTS if not self.db.exists(ev.get("url", ""))]

    async def fetch_hightime(self) -> list[dict]:
        """Scrape hightime.media for upcoming AI events."""
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = await client.get("https://hightime.media/it-conferences/ai/", headers=headers)
                if resp.status_code != 200:
                    return events

                soup = BeautifulSoup(resp.text, "lxml")
                # Look for conference name patterns
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
                    if url and not self.db.exists(url):
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
            logger.error(f"HighTime error: {e}")
        return events

    async def fetch_all(self) -> list[dict]:
        """Fetch from all sources, return new unique events."""
        results = []
        for name, coro in [
            ("Seed", self.fetch_seed()),
            ("HighTime", self.fetch_hightime()),
        ]:
            try:
                evs = await coro
                logger.info(f"{name}: {len(evs)} new events")
                results.extend(evs)
            except Exception as e:
                logger.error(f"{name} failed: {e}")
        return results

# ─── Post formatting ──────────────────────────────────────────────────────────

def detect_type(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ["конференци", "conference", "summit"]): return "Конференция"
    if any(w in t for w in ["митап", "meetup", "встреч"]): return "Митап"
    if any(w in t for w in ["хакатон", "hackathon"]): return "Хакатон"
    if any(w in t for w in ["воркшоп", "workshop", "мастер"]): return "Воркшоп"
    if any(w in t for w in ["выставк", "expo"]): return "Выставка"
    if any(w in t for w in ["вебинар", "webinar"]): return "Вебинар"
    return "Мероприятие"

def time_left_text(d: int) -> str:
    if d <= 0: return "🏁 Уже сегодня!" if d == 0 else "🏁 Уже прошло"
    if d == 1: return "⏰ Завтра!"
    if d <= 7: return f"⏰ Через {d} дн."
    if d <= 14: return f"⏰ Через {d} дн."
    if d <= 30: return f"⏰ Через {d} дн."
    return f"📅 Через {d} дн."

def format_post(event: dict, style: str = "new", inline_keyboard: bool = False) -> tuple:
    """Format event post. style: new | reminder_7 | reminder_30 | digest."""
    title = event.get("title", "AI-мероприятие")
    date_str = event.get("date_str", event.get("date", "Дата уточняется"))
    city = event.get("city", "Россия")
    url = event.get("url", "")
    source = event.get("source", "")
    etype = event.get("event_type") or detect_type(title)
    price = event.get("price", "")
    desc = event.get("description", "")
    event_id = str(abs(hash(url))) if url else str(random.randint(100000, 999999))

    # Calculate days until
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

    headers = {
        "new": "🤖 <b>AI Guide Russia</b> — Новый анонс",
        "reminder_30": "🔔 <b>AI Guide Russia</b> — Месяц до мероприятия!",
        "reminder_7": "🔔 <b>AI Guide Russia</b> — Неделя до мероприятия!",
        "digest": "📋 <b>AI Guide Russia</b> — Еженедельный дайджест",
    }
    header = headers.get(style, headers["new"])

    text = f"{header}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📌 <b>{title}</b>\n"
    text += f"📅 {date_str}\n"
    if day_left:
        text += f"{day_left}\n"
    text += f"📍 {city}\n"
    text += f"🏷 {etype}"
    if price:
        text += f"\n💰 {price}"
    if desc:
        d = re.sub(r"<[^>]+>", "", desc)[:250]
        text += f"\n\n{d}"
    if source:
        text += f"\n\n<i>Источник: {source}</i>"
    text += "\n━━━━━━━━━━━━━━━━━━━━"

    if inline_keyboard:
        text += "\n\n📎 Нажми кнопку ниже, чтобы получить ссылку"
    elif url:
        text += f"\n\n🔗 <b>Ссылка:</b> {url}"

    return text, event_id, url

def format_digest(events_7: list, events_14: list, events_30: list) -> str:
    """Format weekly digest."""
    text = "📋 <b>AI Guide Russia — Дайджест</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n"

    if events_7:
        text += "\n📅 <b>На этой неделе (7 дней):</b>\n"
        for e in events_7:
            d = e.get("date_str", e.get("date", ""))
            text += f"• <b>{e['title'][:60]}</b> — {d}\n"

    if events_14:
        text += "\n📅 <b>В ближайшие 2 недели:</b>\n"
        for e in events_14:
            d = e.get("date_str", e.get("date", ""))
            text += f"• {e['title'][:60]} — {d}\n"

    if events_30:
        text += "\n📅 <b>В ближайший месяц:</b>\n"
        for e in events_30:
            d = e.get("date_str", e.get("date", ""))
            text += f"• {e['title'][:60]} — {d}\n"

    if not (events_7 or events_14 or events_30):
        text += "\nПока нет запланированных мероприятий с точными датами. Скоро появятся!"

    text += "\n━━━━━━━━━━━━━━━━━━━━"
    text += "\n\n🤖 Подпишись: @ai_guide_russia"
    return text

# ─── Bot Handlers ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>AI Guide Russia</b>\n\nСобираю и публикую анонсы AI-мероприятий по всей России.\n\n"
        "🔍 Поиск по источникам\n"
        "🔔 Напоминания за месяц и неделю\n"
        "📋 Еженедельный дайджест\n\n"
        "/help — справка\n/stats — статистика",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>AI Guide Russia</b>\n\n"
        "⏰ Работаю 08:00–24:00 МСК\n"
        "🔄 Проверка источников каждые 90 мин\n"
        "🔔 Напоминаю за 30 и 7 дней до события\n"
        "📋 Дайджест каждую неделю\n\n"
        "Команды:\n/stats — статистика\n/help — справка",
        parse_mode="HTML"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data.get("db", EventsDB())
    s = context.bot_data.get("stats", {"posts": 0, "links": 0, "events_found": 0})
    total = len(db.events)
    with_dates = sum(1 for e in db.events if e.get("start_date"))
    await update.message.reply_text(
        f"📊 <b>AI Guide Russia</b>\n\n"
        f"📝 Постов отправлено: {s['posts']}\n"
        f"🔗 Ссылок отправлено: {s['links']}\n"
        f"🗂 Всего событий в БД: {total}\n"
        f"📅 С датами: {with_dates}",
        parse_mode="HTML"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("event_"):
        event_id = data.replace("event_", "")
        urls = context.bot_data.get("event_urls", {})
        url = urls.get(event_id, "")
        if url:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except:
                pass
            try:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=f"🔗 <b>Ссылка на регистрацию:</b>\n\n{url}",
                    parse_mode="HTML"
                )
            except:
                await query.message.reply_text(f"🔗 {url}")
            s = context.bot_data["stats"]
            s["links"] += 1

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

# ─── Posting ──────────────────────────────────────────────────────────────────

async def send_post(context: ContextTypes.DEFAULT_TYPE, text: str,
                    event_id: str = "", url: str = "",
                    style: str = "new") -> bool:
    """Send a post with optional inline keyboard."""

    chat_id = context.bot_data.get("chat_id", "VaraxinDenis")
    keyboard = [[
        InlineKeyboardButton("📎 Подробнее о мероприятии", callback_data=f"event_{event_id}")
    ]] if event_id else None
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
        if url and event_id:
            urls = context.bot_data.get("event_urls", {})
            urls[event_id] = url
            context.bot_data["event_urls"] = urls
        return True
    except Exception as e:
        logger.warning(f"Post with keyboard failed ({e}), trying without")
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML"
            )
            return True
        except Exception as e2:
            logger.error(f"Text-only also failed: {e2}")
            return False

# ─── Scheduled Jobs ──────────────────────────────────────────────────────────

async def check_new_events(context: ContextTypes.DEFAULT_TYPE):
    """Fetch new events, add to DB, post them."""
    now_msk = datetime.now(timezone.utc) + MSK_OFFSET
    if now_msk.hour < WORK_START_HOUR or now_msk.hour >= WORK_END_HOUR:
        return

    logger.info("Checking for new events...")
    db: EventsDB = context.bot_data["db"]
    manager = SourceManager(db)

    events = await manager.fetch_all()
    stats = context.bot_data["stats"]
    stats["events_found"] += len(events)

    if not events:
        logger.info("No new events found")
        return

    posted = 0
    for event in events[:POSTS_PER_CYCLE]:
        db.add(event)
        text, eid, url = format_post(event, style="new", inline_keyboard=True)
        ok = await send_post(context, text, eid, url, "new")
        if ok:
            db.mark_posted_to_chat(event.get("url", ""))
            posted += 1
        await asyncio.sleep(SECONDS_BETWEEN_POSTS)

    stats["posts"] += posted
    logger.info(f"Posted {posted} new events")

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Send T-30 and T-7 reminders."""
    now_msk = datetime.now(timezone.utc) + MSK_OFFSET
    if now_msk.hour < WORK_START_HOUR or now_msk.hour >= WORK_END_HOUR:
        return

    db: EventsDB = context.bot_data["db"]

    # T-30 reminders
    for event in db.get_pending_30day():
        text, eid, url = format_post(event, style="reminder_30", inline_keyboard=True)
        ok = await send_post(context, text, eid, url, "reminder_30")
        if ok:
            db.mark_reminder_sent(event.get("url", ""), "30")
        await asyncio.sleep(SECONDS_BETWEEN_POSTS)

    # T-7 reminders
    for event in db.get_pending_7day():
        text, eid, url = format_post(event, style="reminder_7", inline_keyboard=True)
        ok = await send_post(context, text, eid, url, "reminder_7")
        if ok:
            db.mark_reminder_sent(event.get("url", ""), "7")
        await asyncio.sleep(SECONDS_BETWEEN_POSTS)

async def send_digest(context: ContextTypes.DEFAULT_TYPE):
    """Weekly digest: events in next 7, 14, 30 days."""
    now_msk = datetime.now(timezone.utc) + MSK_OFFSET
    if now_msk.hour < WORK_START_HOUR or now_msk.hour >= WORK_END_HOUR:
        return

    db: EventsDB = context.bot_data["db"]

    events_7 = db.get_upcoming(7)
    events_14 = db.get_upcoming(14)
    events_30 = db.get_upcoming(30)

    if not (events_7 or events_14 or events_30):
        logger.info("Nothing for digest")
        return

    text = format_digest(events_7, events_14, events_30)
    ok = await send_post(context, text)
    if ok:
        logger.info("Digest sent")
    stats = context.bot_data["stats"]
    stats["posts"] += 1

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    db = EventsDB()

    app = Application.builder().token(BOT_TOKEN).base_url(API_BASE + "/").build()
    app.bot_data["db"] = db
    app.bot_data["stats"] = {"posts": 0, "links": 0, "events_found": 0}
    app.bot_data["chat_id"] = "VaraxinDenis"

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(button_callback, pattern=r"^event_"))
    app.add_error_handler(error_handler)

    # Job schedule
    jq = app.job_queue
    interval_sec = CHECK_INTERVAL * 60
    jq.run_repeating(check_new_events, interval=interval_sec, first=10)
    jq.run_repeating(check_reminders, interval=interval_sec, first=30)
    # Digest every Saturday at 12:00 MSK (09:00 UTC)
    jq.run_daily(send_digest, time=time_module.struct_time((9, 0, 0)), days_of_week=(5,))  # Saturday

    logger.info(f"Bot started. Check interval: {CHECK_INTERVAL}min. Digest: Saturday 12:00 MSK")

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

def main_sync():
    asyncio.run(main())

if __name__ == "__main__":
    main_sync()
