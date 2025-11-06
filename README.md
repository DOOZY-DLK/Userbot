import os
import random
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# Userbot session
app = Client(
    "my_userbot",
    api_id=int(os.environ.get("API_ID", "11447635")),
    api_hash=os.environ.get("API_HASH", "fd48e41738daae23b21d25610448da3c"),
    session_string=os.environ.get("SESSION_STRING", "BQCurVMAGg7Siis7_zpZiQMzsrXLJ-Ll6N8CkvsIXJ1rVVxg91H95eqngKuH4_RQGNiqgawRjf603d_Nxgg8FgNBOfFMpFr51-L2jznZshsFG7suKi94idHR5K6-WV7xPtMbBevfVjW_P-wvwOzW1VbJ4YF2Cjan9bHL7FpfXSWecg8Bwl6zH041DNy5VAYQkE-LubCCLwZ9Gqm49yQHmgKMeaoRjM59siN08NrMbwPwP2DpeOZsAxCKuFysC-0f-emOHGWsl3IJKR0UI44x1tz6wphKJASlR0FoorFh7KYyNTswCEP9uOUBJHXiHz-CswVD46H7gM5y79sbXE-iS_w1U7ZjhAAAAAF86bnEAA")
)

# Emojis list
emojis = [
    "🥰", "❤️", "💋", "👍", "👏", "😂", "😊", "🎉", "🤔", "😎",
    "🙏", "💯", "🔥", "😍", "🤣", "🙂", "🤗", "🥳", "😇", "💖"
]

react_status = {}

@app.on_message(filters.command("react") & filters.group)
async def toggle_react(client, message: Message):
    chat_id = message.chat.id
    current = react_status.get(chat_id, True)
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ON", callback_data=f"react_on_{chat_id}"),
            InlineKeyboardButton("❌ OFF", callback_data=f"react_off_{chat_id}")
        ],
        [InlineKeyboardButton("🗑️ Close", callback_data=f"react_close_{message.id}")]
    ])
    
    await message.reply(
        f"**🤖 Auto React (Userbot Mode)**\n\nStatus: `{'ON' if current else 'OFF'}`",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("^react_"))
async def callback_handler(client, cb: CallbackQuery):
    data = cb.data
    chat_id = cb.message.chat.id
    
    if data.startswith("react_on_"):
        react_status[chat_id] = True
        text = "✅ **Auto reactions ON!** (Userbot)"
    elif data.startswith("react_off_"):
        react_status[chat_id] = False
        text = "❌ **Auto reactions OFF!**"
    elif data.startswith("react_close_"):
        await cb.message.delete()
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Close", callback_data=f"react_close_{cb.message.id}")]
    ])
    
    await cb.edit_message_text(text, reply_markup=keyboard)
    await cb.answer("✅ Updated!")

@app.on_message(filters.group & ~filters.command(["react"]))
async def auto_react(client, message: Message):
    chat_id = message.chat.id
    
    # Check if auto-react is ON for this chat
    if not react_status.get(chat_id, True):
        return
    
    # Random emoji
    emoji = random.choice(emojis)
    
    try:
        await message.react(emoji=emoji)
    except Exception as e:
        print(f"React error: {e}")

# Start the bot
app.run()
