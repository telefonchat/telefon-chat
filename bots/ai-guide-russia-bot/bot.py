#!/usr/bin/env python3
"""
AI Guide Russia — Bot for posting AI events in Russia to Telefon.Chat

Fetches AI events from multiple sources, formats them as posts,
and sends them to the bot's channel/chat.
"""

import os
import json
import logging
import asyncio
import random
from datetime import datetime, time, timedelta
from typing import Optional

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ─── Configuration ────────────────────────────────────────────────────────────

BOT_TOKEN = "1777623970767604:xm-A-72XxypV1eJggVlFqSBNI_bJInyK"
API_BASE = "https://api.telefon.chat/api/bot"

# Time settings (MSK = UTC+3)
WORK_START = time(8, 0)   # 08:00 MSK
WORK_END = time(0, 0)     # 00:00 MSK (next day)
TIMEZONE = "Europe/Moscow"
POST_INTERVAL_MINUTES = 60  # Check for new events every hour

# Channel/Chat to post to (None = post in bot's own chat)
CHAT_ID = None  # Will use bot's private chat

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ai_guide_russia")

# ─── Event Sources ────────────────────────────────────────────────────────────

class EventSource:
    """Base class for event source scrapers."""
    
    name = ""
    
    async def fetch(self) -> list[dict]:
        """Fetch events from this source. Returns list of event dicts."""
        raise NotImplementedError

class TimePadSource(EventSource):
    """Fetch AI events from TimePad."""
    
    name = "TimePad"
    
    async def fetch(self) -> list[dict]:
        events = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # TimePad API: search for AI-related events
                url = "https://api.timepad.ru/v1/events"
                params = {
                    "limit": 20,
                    "skip": 0,
                    "fields": "name,description,starts_at,location,url,tags",
                    "cities": "Москва,Санкт-Петербург,Казань,Новосибирск,Екатеринбург,Нижний Новгород",
                    "keywords": "искусственный интеллект,AI,нейросети,machine learning,deep learning,artificial intelligence,chatgpt,llm,нейронка",
                    "sort": "starts_at",
                    "starts_at_min": datetime.now().isoformat(),
                }
                resp = await client.get(url, params=params)
                data = resp.json()
                
                for item in data.get("values", []):
                    events.append({
                        "title": item.get("name", ""),
                        "description": item.get("description_short", item.get("description", "")),
                        "date": item.get("starts_at", ""),
                        "city": item.get("location", {}).get("city", "Онлайн"),
                        "url": item.get("url", ""),
                        "source": "TimePad",
                        "tags": item.get("tags", []),
                    })
        except Exception as e:
            logger.error(f"TimePad fetch error: {e}")
        
        return events

class HabrEventsSource(EventSource):
    """Fetch AI events from Habr Career."""
    
    name = "Habr"
    
    async def fetch(self) -> list[dict]:
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                # Habr Career events page for AI hub
                url = "https://career.habr.com/events"
                params = {
                    "q": "искусственный интеллект",
                    "type": "all",
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                resp = await client.get(url, params=params, headers=headers)
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "lxml")
                
                # Parse event cards
                cards = soup.select(".event-card") or soup.select(".vacancy-card")
                for card in cards[:10]:
                    title_el = card.select_one(".title") or card.select_one("a")
                    date_el = card.select_one(".date") or card.select_one("time")
                    loc_el = card.select_one(".location") or card.select_one(".meta")
                    
                    title = title_el.get_text(strip=True) if title_el else ""
                    date = date_el.get("datetime", "") if date_el else ""
                    location = loc_el.get_text(strip=True) if loc_el else "Онлайн"
                    link = title_el.get("href", "") if title_el else ""
                    
                    if title and "AI" or title and any(kw in title.lower() for kw in ["нейросет", "искусствен", "machine", "deep", "llm", "чатгпт"]):
                        events.append({
                            "title": title,
                            "description": "",
                            "date": date,
                            "city": location,
                            "url": f"https://career.habr.com{link}" if link and link.startswith("/") else link,
                            "source": "Habr Career",
                            "tags": [],
                        })
        except Exception as e:
            logger.error(f"Habr fetch error: {e}")
        
        return events

class AIEventsHub(EventSource):
    """Aggregated fetch from multiple sources via 10times.com."""
    
    name = "10Times"
    
    async def fetch(self) -> list[dict]:
        events = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                url = "https://10times.com/russia/artificial-intelligence"
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = await client.get(url, headers=headers)
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "lxml")
                
                cards = soup.select(".event-card") or soup.select("[class*=event]") or []
                for card in cards[:10]:
                    title_el = card.select_one("h3") or card.select_one("a")
                    date_el = card.select_one(".date") or card.select_one("time")
                    loc_el = card.select_one(".location") or card.select_one("[class*=city]")
                    
                    events.append({
                        "title": title_el.get_text(strip=True) if title_el else "",
                        "description": "",
                        "date": date_el.get_text(strip=True) if date_el else "",
                        "city": loc_el.get_text(strip=True) if loc_el else "Россия",
                        "url": title_el.get("href", "") if title_el else "",
                        "source": "10Times",
                        "tags": [],
                    })
        except Exception as e:
            logger.error(f"10Times fetch error: {e}")
        
        return events

# ─── Event Scraping Engine ────────────────────────────────────────────────────

class EventScraper:
    """Manages multiple event sources and fetches events."""
    
    def __init__(self):
        self.sources: list[EventSource] = [
            TimePadSource(),
            HabrEventsSource(),
            AIEventsHub(),
        ]
        self.seen_urls = set()
    
    async def fetch_all(self) -> list[dict]:
        """Fetch events from all sources."""
        all_events = []
        for source in self.sources:
            try:
                events = await source.fetch()
                logger.info(f"Fetched {len(events)} events from {source.name}")
                all_events.extend(events)
            except Exception as e:
                logger.error(f"Error fetching from {source.name}: {e}")
        
        # Deduplicate by URL
        unique = []
        for event in all_events:
            url = event.get("url", "")
            if url and url not in self.seen_urls:
                self.seen_urls.add(url)
                unique.append(event)
        
        # Filter by AI relevance
        ai_keywords = [
            "искусствен", "нейросет", "machine learning", "deep learning",
            "artificial intelligence", "chatgpt", "gpt", "llm", "нейронка",
            "ai", "data science", "генератив", "анализ данных", "big data",
            "компьютерное зрение", "nlp", "обработка", "дипфейк",
            "робот", "автоматизац",
        ]
        
        ai_events = []
        for event in unique:
            title = (event.get("title", "") or "").lower()
            desc = (event.get("description", "") or "").lower()
            tags = [t.lower() if isinstance(t, str) else str(t).lower() for t in (event.get("tags", []) or [])]
            
            if any(kw in title for kw in ai_keywords):
                ai_events.append(event)
            elif any(kw in desc for kw in ai_keywords):
                ai_events.append(event)
            elif any(any(kw in t for kw in ai_keywords) for t in tags):
                ai_events.append(event)
        
        logger.info(f"Total: {len(all_events)}, Unique: {len(unique)}, AI-relevant: {len(ai_events)}")
        return ai_events

# ─── Post Formatting ──────────────────────────────────────────────────────────

def format_event_post(event: dict, include_link_in_text: bool = False) -> tuple[str, str]:
    """
    Format an event into a post text and extract callback data.
    Returns (html_text, event_id, url).
    
    If include_link_in_text is True, the URL will be embedded in the text
    (fallback when inline buttons aren't supported by the backend yet).
    """
    title = event.get("title", "Мероприятие по AI")
    date = event.get("date", "Дата уточняется")
    city = event.get("city", "Россия")
    source = event.get("source", "")
    url = event.get("url", "")
    
    # Generate event ID from URL
    event_id = str(abs(hash(url))) if url else str(random.randint(100000, 999999))
    
    # Determine event type
    title_lower = title.lower()
    if any(w in title_lower for w in ["конференци", "conference", "summit"]):
        etype = "Конференция"
    elif any(w in title_lower for w in ["митап", "meetup", "встреч"]):
        etype = "Митап"
    elif any(w in title_lower for w in ["хакатон", "hackathon"]):
        etype = "Хакатон"
    elif any(w in title_lower for w in ["воркшоп", "workshop", "мастер"]):
        etype = "Воркшоп"
    elif any(w in title_lower for w in ["выставк", "expo", "exhibition"]):
        etype = "Выставка"
    elif any(w in title_lower for w in ["вебинар", "webinar"]):
        etype = "Вебинар"
    elif any(w in title_lower for w in ["соревнован"]):
        etype = "Соревнование"
    else:
        etype = "Мероприятие"
    
    # Clean date
    clean_date = date
    if date:
        # Try to parse ISO date
        try:
            dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
            clean_date = dt.strftime("%d %B %Y, %H:%M")
        except:
            pass
    
    # Short description
    description = event.get("description", "") or ""
    if description:
        # Clean HTML tags and truncate
        import re
        description = re.sub(r"<[^>]+>", "", description)
        if len(description) > 200:
            description = description[:197] + "..."
    
    text = (
        f"🤖 <b>AI Guide Russia</b> — анонс мероприятия\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{title}</b>\n"
        f"📅 {clean_date}\n"
        f"📍 {city}\n"
        f"🏷 {etype}"
    )
    
    if description:
        text += f"\n\n{description}"
    
    if source:
        text += f"\n\n<i>Источник: {source}</i>"
    
    text += "\n━━━━━━━━━━━━━━━━━━━━"
    
    if include_link_in_text and url:
        text += f"\n\n🔗 <b>Регистрация:</b> {url}"
    else:
        text += "\n\n📎 Нажми кнопку ниже, чтобы получить ссылку на регистрацию"
    
    return text, event_id, url

# ─── Statistics ───────────────────────────────────────────────────────────────

class BotStats:
    def __init__(self):
        self.posts_sent = 0
        self.links_sent = 0
        self.events_total = 0
    
    def to_text(self) -> str:
        return (
            f"📊 <b>Статистика AI Guide Russia</b>\n"
            f"\n"
            f"📝 Постов отправлено: {self.posts_sent}\n"
            f"🔗 Ссылок отправлено: {self.links_sent}\n"
            f"🗂 Всего найдено событий: {self.events_total}"
        )

# ─── Bot Commands ────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 <b>AI Guide Russia</b>\n\n"
        "Я собираю и публикую анонсы AI-мероприятий по всей России.\n\n"
        "Подпишись на канал @ai_guide_russia, чтобы получать свежие анонсы!\n\n"
        "Доступные команды:\n"
        "/stats — статистика бота\n"
        "/help — помощь",
        parse_mode="HTML",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(
        "🤖 <b>AI Guide Russia</b> — бот-блогер AI-мероприятий\n\n"
        "🔍 <b>Что делает:</b>\n"
        "Находит и публикует анонсы AI-конференций, митапов,\n"
        "хакатонов, воркшопов и выставок по всей России.\n\n"
        "⏰ <b>Когда работает:</b>\n"
        "С 08:00 до 24:00 по московскому времени.\n\n"
        "📎 <b>Как получить ссылку:</b>\n"
        "Нажми «Подробнее о мероприятии» под постом —\n"
        "и бот пришлёт ссылку на регистрацию в личные сообщения.\n\n"
        "🌐 <b>Источники:</b>\n"
        "TimePad, Habr Career, 10Times и другие.\n\n"
        "📊 <b>Команды:</b>\n"
        "/stats — статистика\n"
        "/help — эта справка",
        parse_mode="HTML",
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    stats = context.bot_data.get("stats", BotStats())
    await update.message.reply_text(stats.to_text(), parse_mode="HTML")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("event_"):
        event_id = data.replace("event_", "")
        
        # Look up the event URL
        event_urls = context.bot_data.get("event_urls", {})
        url = event_urls.get(event_id, "")
        
        if url:
            await query.edit_message_reply_markup(reply_markup=None)
            
            # Send registration link in private message
            user_id = query.from_user.id
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔗 <b>Ссылка на регистрацию:</b>\n\n{url}",
                parse_mode="HTML",
            )
            
            # Update stats
            stats = context.bot_data.get("stats", BotStats())
            stats.links_sent += 1
            context.bot_data["stats"] = stats
        else:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="😕 Извините, ссылка на это мероприятие временно недоступна.",
            )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")

# ─── Event Posting ────────────────────────────────────────────────────────────

async def post_event(context: ContextTypes.DEFAULT_TYPE, event: dict):
    """Post a single event to the channel/chat."""
    url = event.get("url", "")
    
    # Try posting with inline keyboard first
    text, event_id, _ = format_event_post(event, include_link_in_text=False)
    
    # Build inline keyboard
    keyboard = [[
        InlineKeyboardButton("📎 Подробнее о мероприятии", callback_data=f"event_{event_id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send to chat/channel
    chat_id = context.bot_data.get("chat_id", CHAT_ID)
    if not chat_id:
        chat_id = "VaraxinDenis"  # Default: user's chat for testing
    
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        
        # Store event URL for callback handling
        event_urls = context.bot_data.get("event_urls", {})
        event_urls[event_id] = url
        context.bot_data["event_urls"] = event_urls
        
        # Update stats
        stats = context.bot_data.get("stats", BotStats())
        stats.posts_sent += 1
        context.bot_data["stats"] = stats
        
        logger.info(f"Posted event with keyboard: {event.get('title', '')[:50]}...")
        return True
    except Exception as e:
        logger.warning(f"Inline keyboard failed ({e}), falling back to text-only post")
        
        # Fallback: include link in text (no keyboard)
        text, _, _ = format_event_post(event, include_link_in_text=True)
        try:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
            
            # Update stats
            stats = context.bot_data.get("stats", BotStats())
            stats.posts_sent += 1
            context.bot_data["stats"] = stats
            
            logger.info(f"Posted event (text-only): {event.get('title', '')[:50]}...")
            return True
        except Exception as e2:
            logger.error(f"Text-only post also failed: {e2}")
            return False

async def check_new_events(context: ContextTypes.DEFAULT_TYPE):
    """Check for new events and post them."""
    now = datetime.now()
    now_msk = now + timedelta(hours=3)
    
    # Check if within working hours (08:00-24:00 MSK)
    current_hour = now_msk.hour
    if current_hour < 8:
        logger.info("Outside working hours. Skipping.")
        return
    
    logger.info("Checking for new events...")
    
    scraper = context.bot_data.get("scraper")
    if not scraper:
        scraper = EventScraper()
        context.bot_data["scraper"] = scraper
    
    events = await scraper.fetch_all()
    
    # Update stats
    stats = context.bot_data.get("stats", BotStats())
    stats.events_total += len(events)
    context.bot_data["stats"] = stats
    
    if not events:
        logger.info("No new events found.")
        return
    
    # Post events with delay between them
    for event in events[:5]:  # Max 5 events per check
        success = await post_event(context, event)
        if success:
            await asyncio.sleep(5)  # Delay between posts (5 seconds)
    
    logger.info(f"Posted {min(len(events), 5)} new events.")

# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    """Main entry point."""
    # Initialize stats
    stats = BotStats()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).base_url(API_BASE + "/").build()
    
    # Store shared data
    application.bot_data["stats"] = stats
    application.bot_data["chat_id"] = CHAT_ID
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^event_"))
    
    # Register error handler
    application.add_error_handler(error_handler)
    
    # Schedule event checking (every POST_INTERVAL_MINUTES)
    job_queue = application.job_queue
    job_queue.run_repeating(
        check_new_events,
        interval=POST_INTERVAL_MINUTES * 60,
        first=10,  # First check after 10 seconds
    )
    
    logger.info("Bot started. Checking for events every %d minutes.", POST_INTERVAL_MINUTES)
    
    
    # Initialize and start manually
    await application.initialize()
    await application.start()
    
    updater = application.updater
    await updater.start_polling(allowed_updates=["message", "callback_query"])
    
    # Keep running until interrupted
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await updater.stop()
        await application.stop()
        await application.shutdown()


def main_sync():
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
