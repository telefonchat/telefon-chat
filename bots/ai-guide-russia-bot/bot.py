#!/usr/bin/env python3
"""
AI Guide Russia — Bot-blogger for AI events across Russia.

Finds, formats and posts AI event announcements to Telefon.Chat.
Combines web scraping, web search, and curated seed data.
"""

import asyncio
import json
import logging
import random
import re
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ─── Configuration ────────────────────────────────────────────────────────────

BOT_TOKEN = "1777623970767604:xm-A-72XxypV1eJggVlFqSBNI_bJInyK"
API_BASE = "https://api.telefon.chat/api/bot"
WORKING_DIR = "/opt/bots/ai_guide"
SEED_FILE = os.path.join(WORKING_DIR, "events_seed.json")
POSTED_FILE = os.path.join(WORKING_DIR, "events_posted.json")

# Moscow time (UTC+3)
MSK_OFFSET = timedelta(hours=3)
WORK_START_HOUR = 8   # 08:00 MSK
WORK_END_HOUR = 24    # 00:00 MSK
CHECK_INTERVAL_MINUTES = 90  # Check every 1.5 hours

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("ai_guide")

# ─── Event Data ───────────────────────────────────────────────────────────────

SEED_EVENTS = [
    {
        "title": "Conversations 2026 — Конференция по генеративному AI",
        "description": "2 дня, 4 трека, 70+ спикеров. Бизнес и AI-трансформация, стартапы, GenAI в продуктах, технологии, ИИ-агенты, инфраструктура и безопасность.",
        "date": "25-26 июня 2026",
        "city": "Санкт-Петербург",
        "url": "https://conversations-ai.com/",
        "source": "HighTime Media",
        "event_type": "Конференция",
        "price": "от 29 900 ₽"
    },
    {
        "title": "Tech Week 2026 — Крупнейшая технологическая конференция",
        "description": "ИИ и технологии для бизнеса, разработка, ритейл, e-commerce, цифровизация, логистика, маркетинг. Выставка-экспозиция ведущих компаний.",
        "date": "17-18 июня 2026",
        "city": "Москва",
        "url": "https://techweek.moscow/",
        "source": "HighTime Media",
        "event_type": "Конференция",
        "price": "от 20 000 ₽"
    },
    {
        "title": "PyCon Russia 2026 — Крупнейшая Python-конференция России",
        "description": "700+ участников, 35+ докладов, мастер-классы. Data Science, ML/AI, backend, инфраструктура. Для Python-разработчиков и ML-инженеров.",
        "date": "Июль 2026",
        "city": "Москва",
        "url": "https://www.pycon.ru/",
        "source": "PyCon Russia",
        "event_type": "Конференция"
    },
    {
        "title": "Летняя Conversations 2026",
        "description": "2 дня, 4 трека, 40+ докладов, дискуссии, нетворкинг и вечеринка. Крупнейшее событие по генеративному ИИ в России.",
        "date": "25-26 июня 2026",
        "city": "Санкт-Петербург",
        "url": "https://all-events.ru/events/letnyaya-conversations-2026/",
        "source": "All-Events",
        "event_type": "Конференция"
    },
    {
        "title": "STORMCONF26 — BPM и AI в бизнес-процессах",
        "description": "Конференция по процессному подходу: BPM и AI вместе в бизнесе. Где процессы теряют деньги, какие инструменты работают, кейсы внедрения.",
        "date": "10 апреля 2026 (прошла)",
        "city": "Москва",
        "url": "https://hightime.media/it-conferences/ai/",
        "source": "HighTime Media",
        "event_type": "Конференция"
    },
    {
        "title": "AiConf 2026 — Прикладные аспекты ML и AI",
        "description": "Ежегодная российская конференция. Управление R&D, инновации, эффективность НИОКР. Новый формат: экспертные зоны, мастер-классы, case-clinic.",
        "date": "20 апреля 2026 (прошла)",
        "city": "Москва",
        "url": "https://hightime.media/it-conferences/ai/",
        "source": "HighTime Media",
        "event_type": "Конференция"
    },
    {
        "title": "Neo4j Nodes — Engineering Better Intelligence",
        "description": "Конференция по графовым базам данных и AI. Охватывает машинное обучение, анализ данных и современные подходы к интеллектуальным системам.",
        "date": "2026",
        "city": "Онлайн",
        "url": "https://10times.com/russia/artificial-intelligence",
        "source": "10Times",
        "event_type": "Конференция"
    },
    {
        "title": "AI in Finance Summit",
        "description": "Саммит по искусственному интеллекту в финансах от RE-WORK LTD. Кейсы применения AI в финансовом секторе, алгоритмическая торговля, риск-менеджмент.",
        "date": "2026",
        "city": "Лондон / Онлайн",
        "url": "https://www.re-work.co/events",
        "source": "RE-WORK",
        "event_type": "Саммит"
    },
    {
        "title": "Machine Learning Developers Summit",
        "description": "Конференция для ML-разработчиков. Свежие подходы к разработке моделей, MLOps, инфраструктура для ML, продакшн ML-систем.",
        "date": "2026",
        "city": "Бангалор / Онлайн",
        "url": "https://www.mlds.com/",
        "source": "10Times",
        "event_type": "Саммит"
    },
]

# ─── Sources Manager ──────────────────────────────────────────────────────────

class SourceManager:
    """Manages multiple event sources: scraping + seed data."""

    def __init__(self):
        self.seen_urls = set()
        self._load_posted()

    def _load_posted(self):
        self.posted_ids = set()
        if os.path.exists(POSTED_FILE):
            try:
                with open(POSTED_FILE) as f:
                    self.posted_ids = set(json.load(f))
            except:
                pass

    def _save_posted(self):
        os.makedirs(os.path.dirname(POSTED_FILE) or ".", exist_ok=True)
        with open(POSTED_FILE, "w") as f:
            json.dump(list(self.posted_ids), f)

    def mark_posted(self, event_id: str):
        self.posted_ids.add(event_id)
        self._save_posted()

    def is_posted(self, event_id: str) -> bool:
        return event_id in self.posted_ids

    def event_id(self, event: dict) -> str:
        return event.get("url", "") or str(hash(event.get("title", "")))

    async def fetch_seed(self) -> list[dict]:
        """Return seed events that haven't been posted yet."""
        events = []
        for ev in SEED_EVENTS:
            eid = self.event_id(ev)
            if eid not in self.posted_ids:
                events.append(ev)
        random.shuffle(events)
        return events

    async def fetch_hightime(self) -> list[dict]:
        """Scrape hightime.media for upcoming AI events."""
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                resp = await client.get("https://hightime.media/it-conferences/ai/", headers=headers)
                if resp.status_code != 200:
                    return events

                soup = BeautifulSoup(resp.text, "lxml")

                # Extract event sections - they have conference names in h2/h3
                sections = soup.find_all(["h2", "h3", "h4"])
                current_ev = {}

                for tag in sections:
                    text = tag.get_text(strip=True)
                    lower = text.lower()

                    # Skip non-event sections
                    if any(s in lower for s in ["топ", "прошедшие", "ближайшие", "подробнее", "перейти"]):
                        if current_ev and current_ev.get("title"):
                            events.append(current_ev)
                            current_ev = {}
                        continue

                    # Try to extract event from section
                    parent = tag.find_parent(["section", "div", "article"])
                    if parent and text and len(text) > 5:
                        links = parent.find_all("a", href=True)
                        url = ""
                        for link in links:
                            href = link.get("href", "")
                            if href.startswith("http") and "hightime" not in href:
                                url = href
                                break

                        # Get description
                        desc_div = parent.find("div", class_=lambda c: c and "desc" in c.lower()) if parent else None
                        desc = desc_div.get_text(strip=True)[:300] if desc_div else ""

                        # Try to get date and city
                        date_text = ""
                        city_text = ""
                        parent_text = parent.get_text() if parent else ""
                        # Find date patterns
                        date_match = re.search(r'\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\s+\d{4}', parent_text)
                        if date_match:
                            date_text = date_match.group(0)
                        # Find city
                        for city in ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург", "Онлайн"]:
                            if city in parent_text:
                                city_text = city
                                break

                        if text and url:
                            eid = self.event_id({"url": url})
                            if eid not in self.posted_ids:
                                events.append({
                                    "title": text,
                                    "description": desc[:200] if desc else "",
                                    "date": date_text,
                                    "city": city_text or "Россия",
                                    "url": url,
                                    "source": "HighTime Media",
                                    "event_type": "Конференция",
                                })
        except Exception as e:
            logger.error(f"HighTime scrape error: {e}")

        return events

    async def fetch_gomeetup(self) -> list[dict]:
        """Scrape gomeetup.ru for AI events."""
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = await client.get("https://gomeetup.ru/ai", headers=headers)
                if resp.status_code != 200:
                    return events

                soup = BeautifulSoup(resp.text, "lxml")
                cards = soup.select("[class*=event], [class*=card], [class*=item]")

                for card in cards[:15]:
                    title_el = card.select_one("h3, h4, .title, a[href*='event']")
                    date_el = card.select_one("[class*=date], time")
                    link_el = card.select_one("a[href]")

                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    url = link_el.get("href", "") if link_el else ""
                    if url and not url.startswith("http"):
                        url = "https://gomeetup.ru" + url

                    eid = self.event_id({"url": url or title})
                    if eid not in self.posted_ids and title and len(title) > 5:
                        events.append({
                            "title": title,
                            "description": "",
                            "date": date_el.get_text(strip=True) if date_el else "",
                            "city": "",
                            "url": url,
                            "source": "GoMeetup",
                            "event_type": "Мероприятие",
                        })
        except Exception as e:
            logger.error(f"GoMeetup scrape error: {e}")

        return events

    async def fetch_web_search(self) -> list[dict]:
        """Use DuckDuckGo search to find AI events."""
        events = []
        queries = [
            "site:timepad.ru искусственный интеллект конференция 2026",
            "конференция AI Россия 2026 анонс",
            "нейросети митап Москва 2026",
        ]
        try:
            import urllib.parse
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0"}
                for query in queries:
                    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                    resp = await client.get(url, headers=headers)
                    soup = BeautifulSoup(resp.text, "lxml")
                    results = soup.select(".result, .web-result, [class*=result]")

                    for r in results[:5]:
                        link = r.select_one("a[href]")
                        title_el = r.select_one("h2, h3, .result__title")
                        if not link or not title_el:
                            continue

                        href = link.get("href", "")
                        # DuckDuckGo wraps URLs in redirect links
                        if "uddg=" in href:
                            from urllib.parse import parse_qs, urlparse
                            parsed = urlparse(href)
                            qs = parse_qs(parsed.query)
                            href = qs.get("uddg", [""])[0]

                        title = title_el.get_text(strip=True)
                        eid = self.event_id({"url": href or title})
                        if eid not in self.posted_ids and title and len(title) > 10:
                            events.append({
                                "title": title,
                                "description": "",
                                "date": "",
                                "city": "",
                                "url": href,
                                "source": "Web Search",
                                "event_type": "Мероприятие",
                            })
        except Exception as e:
            logger.error(f"Web search error: {e}")

        return events

    async def fetch_all(self) -> list[dict]:
        """Fetch from all sources, return unique events."""
        all_events = []
        sources = [
            ("Seed", self.fetch_seed()),
            ("HighTime", self.fetch_hightime()),
            ("GoMeetup", self.fetch_gomeetup()),
            ("WebSearch", self.fetch_web_search()),
        ]

        for name, coro in sources:
            try:
                events = await coro
                logger.info(f"{name}: {len(events)} events")
                all_events.extend(events)
            except Exception as e:
                logger.error(f"{name} failed: {e}")

        # Deduplicate by URL
        seen = set()
        unique = []
        for ev in all_events:
            key = self.event_id(ev)
            if key and key not in seen:
                seen.add(key)
                unique.append(ev)

        logger.info(f"Total unique new events: {len(unique)}")
        return unique


# ─── Post Formatting ──────────────────────────────────────────────────────────

def detect_event_type(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ["конференци", "conference", "summit"]):
        return "Конференция"
    if any(w in t for w in ["митап", "meetup", "встреч"]):
        return "Митап"
    if any(w in t for w in ["хакатон", "hackathon"]):
        return "Хакатон"
    if any(w in t for w in ["воркшоп", "workshop", "мастер-класс", "мастер"]):
        return "Воркшоп"
    if any(w in t for w in ["выставк", "expo", "exhibition"]):
        return "Выставка"
    if any(w in t for w in ["вебинар", "webinar"]):
        return "Вебинар"
    return "Мероприятие"

def format_post(event: dict, inline_keyboard: bool = False) -> tuple:
    """Format event as HTML post. Returns (html_text, event_id, url)."""
    title = event.get("title", "AI-мероприятие")
    date = event.get("date", "Дата уточняется")
    city = event.get("city", "Россия")
    url = event.get("url", "")
    source = event.get("source", "")
    etype = event.get("event_type") or detect_event_type(title)
    price = event.get("price", "")
    description = event.get("description", "")

    event_id = str(abs(hash(url))) if url else str(random.randint(100000, 999999))

    text = f"🤖 <b>AI Guide Russia</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📌 <b>{title}</b>\n"
    text += f"📅 {date}\n"
    text += f"📍 {city}\n"
    text += f"🏷 {etype}"

    if price:
        text += f"\n💰 {price}"
    if description:
        desc = re.sub(r"<[^>]+>", "", description)[:250]
        text += f"\n\n{desc}"
    if source:
        text += f"\n\n<i>Источник: {source}</i>"

    text += "\n━━━━━━━━━━━━━━━━━━━━"

    if inline_keyboard:
        text += "\n\n📎 Нажми кнопку ниже, чтобы получить ссылку на регистрацию"
    elif url:
        text += f"\n\n🔗 <b>Регистрация:</b> {url}"

    return text, event_id, url


# ─── Bot Handlers ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>AI Guide Russia</b>\n\n"
        "Собираю анонсы AI-мероприятий по всей России.\n\n"
        "/help — справка\n"
        "/stats — статистика",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>AI Guide Russia</b>\n\n"
        "🔍 Нахожу и публикую анонсы AI-конференций,\n"
        "митапов, хакатонов и воркшопов.\n\n"
        "⏰ Работаю с 08:00 до 24:00 МСК\n"
        "🔄 Проверка источников каждые 90 минут\n\n"
        "📊 Команды:\n"
        "/stats — статистика\n"
        "/help — эта справка",
        parse_mode="HTML"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = context.bot_data.get("stats", {
        "posts": 0, "links": 0, "events_found": 0
    })
    await update.message.reply_text(
        f"📊 <b>AI Guide Russia — Статистика</b>\n\n"
        f"📝 Постов отправлено: {stats['posts']}\n"
        f"🔗 Ссылок отправлено: {stats['links']}\n"
        f"🗂 Всего найдено событий: {stats['events_found']}",
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

            s = context.bot_data.get("stats", {"posts": 0, "links": 0, "events_found": 0})
            s["links"] += 1
            context.bot_data["stats"] = s

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")


# ─── Posting Engine ───────────────────────────────────────────────────────────

async def post_event(context: ContextTypes.DEFAULT_TYPE, event: dict) -> bool:
    """Post an event, trying keyboard first, falling back to text-only."""
    text, event_id, url = format_post(event, inline_keyboard=True)

    # Build inline keyboard
    keyboard = [[
        InlineKeyboardButton("📎 Подробнее о мероприятии", callback_data=f"event_{event_id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id = context.bot_data.get("chat_id", "VaraxinDenis")

    # Try with keyboard
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
        )
        logger.info(f"Posted (keyboard): {event.get('title', '')[:50]}")

        urls = context.bot_data.get("event_urls", {})
        urls[event_id] = url
        context.bot_data["event_urls"] = urls
        s = context.bot_data["stats"]
        s["posts"] += 1
        return True

    except Exception as e:
        logger.warning(f"Keyboard failed ({e}), using text-only")
        text2, _, _ = format_post(event, inline_keyboard=False)

        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text2, parse_mode="HTML"
            )
            s = context.bot_data["stats"]
            s["posts"] += 1
            return True
        except Exception as e2:
            logger.error(f"Text-only also failed: {e2}")
            return False


async def check_events(context: ContextTypes.DEFAULT_TYPE):
    """Check for new events and post them."""
    now_msk = datetime.now(timezone.utc) + MSK_OFFSET
    hour = now_msk.hour
    if hour < WORK_START_HOUR or hour >= WORK_END_HOUR:
        logger.info(f"Outside work hours ({hour} MSK), skipping")
        return

    logger.info("Checking for new events...")

    manager = context.bot_data.get("manager")
    if not manager:
        manager = SourceManager()
        context.bot_data["manager"] = manager

    events = await manager.fetch_all()
    s = context.bot_data["stats"]
    s["events_found"] += len(events)

    if not events:
        logger.info("No new events found")
        return

    # Post up to 3 events per check
    for event in events[:3]:
        success = await post_event(context, event)
        if success:
            eid = manager.event_id(event)
            manager.mark_posted(eid)
            await asyncio.sleep(10)  # Delay between posts

    logger.info(f"Posted {min(len(events), 3)} events this cycle")


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    app = Application.builder().token(BOT_TOKEN).base_url(API_BASE + "/").build()

    app.bot_data["stats"] = {"posts": 0, "links": 0, "events_found": 0}
    app.bot_data["chat_id"] = "VaraxinDenis"

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(button_callback, pattern=r"^event_"))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(check_events, interval=CHECK_INTERVAL_MINUTES * 60, first=10)

    logger.info(f"Bot started. Checking events every {CHECK_INTERVAL_MINUTES} min")

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
