import os
import random
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, ReactionInvalid, MessageNotModified, PeerIdInvalid

# === CONFIG ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client(
    "my_userbot",
    api_id=int(os.environ.get("API_ID", "22971197")),
    api_hash=os.environ.get("API_HASH", "a46aaab0cf1854c634dde4566c827b71"),
    session_string=os.environ.get(
        "SESSION_STRING",
        "BQFegz0Ap3Jxlmhy2KNVtpnSR5W0Ijzn_4R8NezYrR89LEjTemrTc-iSsFQ6iSvD0da-OR5G_Sc-q9x_eWWA5-Qy3DJCOg-UZLLbfpMROp-wfkaSGM5H4p5DGrK-1ZKUko5i6i6tJ7324dAiaIdVT231whhoLZddVhIhsC0l9wAF4c-edsY9lodHTNJ2tLSpjCTLBTfc8opepqErWZumlBmGz_O7xCqpWQrXn2JSQtYwuZGLnh5yFHDLSet2m7j-bEKMPLJWv_6i0EhBHJNuVY8Z0RMrmS_o1pJydcEC31A232Irja3dCeqdbbcppxzZW-qHwIy699GDnWAjYd5jhIxDQreG_wAAAAFgu_1oAA"
    ),
    in_memory=True
)

# Valid Telegram reaction emojis
VALID_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌭", "💯", "🤣", "⚡", "🍌",
    "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴"
]

react_status = {}
alive_sent = False

# === ALIVE MESSAGE ===
async def send_alive():
    global alive_sent
    if alive_sent:
        return
    try:
        await app.send_message(
            "me",
            "**Auto React Userbot FULLY ACTIVE!**\n\n"
            "Reacts in **Private, Groups, Channels**\n"
            "Skips **edited & replied** messages\n"
            "Use `/react` **ON/OFF per chat**\n"
            "**Status: ONLINE & REACTING EVERYWHERE**",
            disable_web_page_preview=True
        )
        alive_sent = True
        logger.info("Alive message sent.")
    except Exception as e:
        logger.error(f"Failed to send alive: {e}")

# === TOGGLE COMMAND ===
@app.on_message(filters.command("react") & (filters.group | filters.channel))
async def toggle_react(client: Client, message: Message):
    chat_id = message.chat.id
    current = react_status.get(chat_id, True)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ON", callback_data=f"react_on_{chat_id}"),
            InlineKeyboardButton("OFF", callback_data=f"react_off_{chat_id}")
        ],
        [InlineKeyboardButton("Close", callback_data="react_close")]
    ])

    await message.reply(
        f"**Auto React Controller**\n\n"
        f"**Chat:** `{message.chat.title or 'Channel'}`\n"
        f"**Status:** `{'ON' if current else 'OFF'}`",
        reply_markup=keyboard
    )

# === CALLBACK HANDLER ===
@app.on_callback_query(filters.regex("^react_(on|off|close)_"))
async def callback_handler(client: Client, cb: CallbackQuery):
    data = cb.data
    chat_id = cb.message.chat.id

    try:
        if data.startswith("react_on_"):
            react_status[chat_id] = True
            text = "**Auto React ENABLED!**"
        elif data.startswith("react_off_"):
            react_status[chat_id] = False
            text = "**Auto React DISABLED!**"
        elif data == "react_close":
            await cb.message.delete()
            await cb.answer()
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Close", callback_data="react_close")]
        ])
        await cb.edit_message_text(text, reply_markup=keyboard)
        await cb.answer("Updated!")

    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await cb.answer("Error!", show_alert=True)

# === MAIN AUTO REACT â NOW WORKS IN PRIVATE TOO ===
@app.on_message(
    (filters.private | filters.group | filters.channel) &
    filters.incoming &
    ~filters.reply &
    ~filters.command("react")
)
async def auto_react(client: Client, message: Message):
    # Skip edited messages
    if message.edit_date:
        return

    chat_id = message.chat.id
    if not react_status.get(chat_id, True):
        return

    if not message.id:
        return

    emoji = random.choice(VALID_EMOJIS)

    try:
        await message.react(emoji=emoji)
        logger.info(f"Reacted {emoji} â {chat_id} | Msg ID: {message.id}")
    except ReactionInvalid:
        pass
    except FloodWait as e:
        logger.warning(f"FloodWait: sleeping {e.value}s")
        await asyncio.sleep(e.value)
    except PeerIdInvalid:
        logger.warning(f"PeerIdInvalid skipped: {chat_id}")
        # Auto-resolve by fetching chat
        try:
            await app.get_chat(chat_id)
        except:
            pass
    except Exception as e:
        error = str(e)
        if "MESSAGE_ID_INVALID" in error or "REACTION_INVALID" in error:
            pass
        else:
            logger.error(f"React failed: {error}")

# === AUTO SEND ALIVE ON FIRST PRIVATE MESSAGE FROM YOU ===
@app.on_message(filters.private & filters.me)
async def auto_start_trigger(client: Client, message: Message):
    await send_alive()

# === MAIN STARTUP ===
async def main():
    try:
        await app.start()
        me = await app.get_me()
        logger.info(f"Userbot started as @{me.username or me.first_name}")

        await send_alive()

        # Keep alive forever
        await asyncio.Event().wait()

    except Exception as e:
        logger.critical(f"Startup failed: {e}")
        await asyncio.sleep(5)
        os._exit(1)

# RUN
if __name__ == "__main__":
    try:
        app.run(main())
    except KeyboardInterrupt:
        logger.info("Userbot stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error: {e}")
        os._exit(1)