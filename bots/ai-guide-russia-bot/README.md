# 🤖 AI Guide Russia

[![Telefon.Chat](https://img.shields.io/badge/powered%20by-Telefon.Chat-0088cc)](https://telefon.chat)

> 🇷🇺 Бот-блогер, который находит и публикует анонсы AI-мероприятий по всей России.
> 
> 🇬🇧 A bot-blogger that finds and publishes AI event announcements across Russia.

## 🌟 Features / Возможности

- 🔍 **Smart scraping** — searches TimePad, Habr Career, 10Times and other sources
- 📝 **Auto-posting** — finds new AI events and posts them automatically
- 🎯 **AI focused** — filters events by AI/ML/DL/NLP/CV relevance
- ⏰ **Schedule** — works 08:00–24:00 Moscow time
- 🏷 **Smart categorization** — detects event type (conference, meetup, hackathon, etc.)
- 🔗 **One-click registration** — inline button sends registration link in private message
- 🌍 **Bilingual** — Russian + English descriptions

## 📋 Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help`  | Help / справка |
| `/stats` | Statistics / статистика |

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/telefonchat/telefon-chat.git
cd telefon-chat/bots/ai-guide-russia-bot

# Install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
# Edit bot.py → set BOT_TOKEN

# Run
python3 bot.py
```

### Systemd service

```ini
[Unit]
Description=AI Guide Russia Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bots/ai_guide
ExecStart=/opt/bots/ai_guide/venv/bin/python3 /opt/bots/ai_guide/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 🏗 Architecture / Архитектура

```
bot.py
├── EventSource (base)     # Base scraper class
│   ├── TimePadSource      # TimePad API events
│   ├── HabrEventsSource   # Habr Career events
│   └── AIEventsHub        # 10Times events
├── EventScraper           # Aggregator, deduplication, AI filter
├── format_event_post()    # HTML post formatting
├── button_callback()      # "Подробнее" inline button handler
└── check_new_events()     # Scheduled event checking
```

## 🔗 Powered by Telefon.Chat

This bot runs on [**Telefon.Chat**](https://telefon.chat) — an open-source messenger and bot platform with Telegram-compatible Bot API.  
Бот работает на платформе [**Telefon.Chat**](https://telefon.chat) — открытом мессенджере и платформе ботов с Bot API, совместимым с Telegram.

## 📄 License

MIT
