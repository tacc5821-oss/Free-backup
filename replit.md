# Telegram Movie Bot

## Overview
A Telegram bot built with aiogram that allows users to search for movies. Features include:
- Movie search with cooldown (90 seconds)
- **Ads Rotation System**: Shows a rotating ad for 10 seconds before displaying search results. Ads automatically delete after 10 seconds.
- Admin panel for managing movies, ads, broadcasting, and maintenance mode
- Force join channels requirement
- JSON file-based storage for movies, ads, users, and settings
- Auto-delete messages feature
- Custom text/media for welcome, force join, and searching overlays
- Backup and restore functionality

## Architecture
- **Language**: Python 3.11
- **Framework**: aiogram 2.25.1 (Telegram Bot API)
- **Storage**: JSON files in `data/` directory

## Project Structure
```
bot.py           - Main bot application with all handlers
data/            - JSON data files (movies, ads, users, settings, etc.)
requirements.txt - Python dependencies
```

## Required Environment Variables
- `BOT_TOKEN` - Telegram Bot API token (from @BotFather)
- `OWNER_ID` - Telegram user ID of the bot owner (numeric)

## Running the Bot
The bot runs as a console application using long polling:
```
python bot.py
```

## Deployment
Deploy as a VM-type deployment since the bot needs to run continuously for Telegram polling.
