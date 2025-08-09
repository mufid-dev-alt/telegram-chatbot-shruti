#!/usr/bin/env python3
"""
Temporary script to help identify user IDs from Telegram group.
Run this script and send /whoami in your group to get user information.
"""

import os
import json
from dotenv import load_dotenv
from telegram import Bot

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    print("❌ TELEGRAM_TOKEN not found in environment variables")
    exit(1)

async def get_bot_info():
    """Get bot information"""
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        bot_info = await bot.get_me()
        print(f"🤖 Bot: @{bot_info.username} (ID: {bot_info.id})")
        print(f"📝 Bot Name: {bot_info.first_name}")
        print("\n✅ Bot is working! Now:")
        print("1. Add this bot to your group")
        print("2. Send /whoami in the group")
        print("3. The bot will show you your user ID and username")
        print("4. Update the users.json file with the correct user IDs")
        print("\n💡 You can also mention the bot with @YourBotUsername to test responses")
        
    except Exception as e:
        print(f"❌ Error getting bot info: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(get_bot_info())
