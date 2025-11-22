#!/usr/bin/env python3
"""
Userbot + Control Bot for Auto React + Radio link feature
This version safely handles missing BOT_TOKEN by disabling the control-bot
and continuing to run the userbot. Set BOT_TOKEN in your environment to
enable the control bot.
"""
import os
import asyncio
import logging
import random
from dotenv import load_dotenv
load_dotenv()  # ← meka thama .env file eka load karanne
from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.errors import FloodWait, ReactionInvalid, MessageNotModified, PeerIdInvalid
from pymongo import MongoClient
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration from environment (NO FALLBACKS - force env setup) ---
API_ID = int(os.environ["API_ID"])  # Required - no default
API_HASH = os.environ["API_HASH"]   # Required - no default
SESSION_STRING = os.environ.get("SESSION_STRING")  # Optional - if missing, will create new session
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Optional - if empty, control bot disabled
MONGO_URI = os.environ["MONGO_URI"]  # Required - no default
OWNER_ID_ENV = os.environ.get("OWNER_ID")

if not API_ID or not API_HASH or not MONGO_URI:
    logger.critical("API_ID, API_HASH, and MONGO_URI must be set in environment")
    raise SystemExit(1)

# --- Initialize user client (v2.2.13 string session support) ---
user_app = Client(
    name="userbot",                    # Name for the session (if creating file fallback)
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,     # Use string if provided (in-memory)
    in_memory=True,                    # Always in-memory for portability
)

# compatibility alias for any legacy @app usage
app = user_app

# Initialize control bot only if BOT_TOKEN provided
if BOT_TOKEN:
    bot_app = Client(
        "control_bot",
        bot_token=BOT_TOKEN,
        in_memory=True,
    )
else:
    bot_app = None
    logger.warning("BOT_TOKEN not set — control bot is disabled. Set BOT_TOKEN env to enable it.")

# --- MongoDB setup ---
mongo = MongoClient(MONGO_URI)
db = mongo.get_database("userbot_db")
settings_coll = db.get_collection("react_settings")

# --- Emojis used for reactions ---
VALID_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌭", "💯", "🤣", "⚡", "🍌",
    "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴"
]

# In-memory cache for quick access (keeps in sync with DB on update)
react_cache = {}  # key: (owner_id, chat_id) -> bool
radio_cache = {}  # key: (owner_id, chat_id) -> url

# Owner id (discovered at runtime)
OWNER_ID: Optional[int] = int(OWNER_ID_ENV) if OWNER_ID_ENV else None

# Utility DB functions (unchanged)
def _key(owner_id: int, chat_id: int) -> dict:
    return {"owner_id": owner_id, "chat_id": chat_id}

def get_react_setting(owner_id: int, chat_id: int) -> bool:
    k = _key(owner_id, chat_id)
    doc = settings_coll.find_one(k, {"react": 1})
    if doc and "react" in doc:
        return bool(doc["react"])
    # default ON
    return True

def set_react_setting(owner_id: int, chat_id: int, enabled: bool) -> None:
    k = _key(owner_id, chat_id)
    settings_coll.update_one(k, {"$set": {"react": bool(enabled)}}, upsert=True)
    react_cache[(owner_id, chat_id)] = bool(enabled)

def get_radio(owner_id: int, chat_id: int) -> Optional[str]:
    k = _key(owner_id, chat_id)
    doc = settings_coll.find_one(k, {"radio_url": 1})
    if doc:
        return doc.get("radio_url")
    return None

def set_radio(owner_id: int, chat_id: int, url: Optional[str]) -> None:
    k = _key(owner_id, chat_id)
    if url:
        settings_coll.update_one(k, {"$set": {"radio_url": url}}, upsert=True)
        radio_cache[(owner_id, chat_id)] = url
    else:
        settings_coll.update_one(k, {"$unset": {"radio_url": ""}})
        radio_cache.pop((owner_id, chat_id), None)

# Helper to load caches on startup
def load_caches_for_owner(owner_id: int):
    for doc in settings_coll.find({"owner_id": owner_id}):
        key = (doc["owner_id"], doc["chat_id"])
        react_cache[key] = bool(doc.get("react", True))
        if "radio_url" in doc:
            radio_cache[key] = doc["radio_url"]

# If bot_app exists, register its handlers inside a function (so decorators run only when bot_app exists)
def register_control_bot_handlers(bot_client: Client):
    @bot_client.on_message(filters.command("start") & filters.private)
    async def bot_start(client: Client, message: Message):
        """
        Handles deep-link start payloads like:
          react_on_<owner_id>_<chat_id>
          react_off_<owner_id>_<chat_id>
        The bot will check that the user pressing the button is the owner (owner_id).
        """
        if len(message.command) < 2:
            await message.reply_text(
                "Hello! This bot helps control the Userbot's auto-react feature.\n"
                "You can only toggle settings for the owner account that created the buttons."
            )
            return
        payload = message.command[1]  # everything after /start
        try:
            if payload.startswith("react_on_") or payload.startswith("react_off_"):
                parts = payload.split("_", 2)
                tail = parts[2]
                owner_str, chat_str = tail.split("_", 1)
                owner = int(owner_str)
                chat_id = int(chat_str)
                requester = message.from_user.id
                if requester != owner:
                    await message.reply_text(
                        "Sorry, these buttons are linked to another user's account. "
                        "Only the account owner can change this setting."
                    )
                    return
                enabled = payload.startswith("react_on_")
                set_react_setting(owner, chat_id, enabled)
                text = "🟢 Auto React ENABLED!" if enabled else "🔴 Auto React DISABLED!"
                posted = False
                try:
                    await bot_client.send_message(chat_id, f"{text}\n(Changed by @{message.from_user.username or message.from_user.first_name})")
                    posted = True
                except Exception:
                    posted = False
                if not posted:
                    await message.reply_text(f"{text}\n(Chat notification failed — bot may not be in the chat).")
                else:
                    await message.reply_text(f"{text}\nNotified the chat successfully.")
            elif payload.startswith("setradio_"):
                _, tail = payload.split("_", 1)
                owner_str, chat_str = tail.split("_", 1)
                owner = int(owner_str)
                chat_id = int(chat_str)
                requester = message.from_user.id
                if requester != owner:
                    await message.reply_text("This radio button is for another user.")
                    return
                await message.reply_text(
                    "Send me the radio stream URL (http/https). I'll save it for that chat.\n"
                    "Send 'cancel' to abort."
                )
                try:
                    resp = await bot_client.listen(
                        message.chat.id,
                        filters=filters.user(message.from_user.id) & filters.text,
                        timeout=120,
                    )
                except asyncio.TimeoutError:
                    await message.reply_text("Timed out waiting for URL. Please try again.")
                    return
                if not resp or not resp.text:
                    await message.reply_text("No URL received. Cancelled.")
                    return
                text = resp.text.strip()
                if text.lower() == "cancel":
                    await message.reply_text("Cancelled.")
                    return
                if not (text.startswith("http://") or text.startswith("https://")):
                    await message.reply_text("That doesn't look like a valid URL. Cancelled.")
                    return
                set_radio(owner, chat_id, text)
                await message.reply_text("Saved radio URL. Use the Userbot command !radio in the chat to show/play it.")
                try:
                    await bot_client.send_message(chat_id, f"🔊 Radio set by @{message.from_user.username or message.from_user.first_name}.\nUse the play button or the userbot command to show it.")
                except Exception:
                    pass
            else:
                await message.reply_text(
                    "Unknown start parameter. This bot handles toggling the userbot's features when you click buttons.\n"
                    "Use the buttons sent by your userbot in the group to control settings."
                )
        except Exception as e:
            logger.exception("Error handling /start payload")
            await message.reply_text(f"Error processing request: {e}")

    @bot_client.on_message(filters.command("help") & filters.private)
    async def bot_help(client: Client, message: Message):
        await message.reply_text(
            "Control Bot Help\n\n"
            "- This bot receives button clicks created by your Userbot.\n"
            "- Only the owner (the userbot account that created the buttons) can toggle settings.\n"
            "- It can also accept setting a radio URL when invoked from a deep link /start parameter.\n\n"
            "Typical flow:\n"
            "1. In a group, use the userbot command !react\n"
            "2. Click the ON/OFF button which opens this bot and toggles the setting for that chat."
        )

# Register control bot handlers if we have a bot client
if bot_app:
    register_control_bot_handlers(bot_app)

# --- Userbot handlers (unchanged from your original) ---
async def ensure_owner_id():
    global OWNER_ID
    if OWNER_ID is None:
        me = await user_app.get_me()
        OWNER_ID = me.id
    load_caches_for_owner(OWNER_ID)
    logger.info(f"Owner user id: {OWNER_ID}")

@user_app.on_message(filters.command("help", prefixes=["!", "/"]) & filters.me)
async def user_help(client: Client, message: Message):
    bot_username = None
    if bot_app:
        try:
            bot_user = await bot_app.get_me()
            bot_username = bot_user.username
        except Exception:
            bot_username = None
    if not bot_username:
        bot_username = "<control bot not configured>"
    await message.reply_text(
        "Userbot Help (commands start with ! or /)\n\n"
        "!react - Post bot-control buttons in the chat (use to toggle Auto React for that chat)\n"
        "!setradio <url> - Save a radio stream URL for this chat (owner only)\n"
        "!radio - Show the saved radio link with a Play button\n"
        "!help - Show this message\n\n"
        f"Buttons will open @{bot_username} to confirm actions (if configured).\n"
        "Only the owner account can change settings — others cannot toggle your userbot."
    )

@user_app.on_message(filters.command("react", prefixes=["!", "/"]) & (filters.group | filters.channel) & filters.me)
async def user_post_react_buttons(client: Client, message: Message):
    await ensure_owner_id()
    if not bot_app:
        # bot control not configured
        await message.reply_text(
            "Auto React Controller\n\n"
            "Control bot is not configured on this instance (BOT_TOKEN missing).\n"
            "Set BOT_TOKEN in your environment to enable inline ON/OFF buttons."
        )
        return
    bot_user = await bot_app.get_me()
    bot_username = bot_user.username or bot_user.first_name
    chat_id = message.chat.id
    owner_id = OWNER_ID
    # payloads embedded in the start param
    on_payload = f"react_on_{owner_id}_{chat_id}"
    off_payload = f"react_off_{owner_id}_{chat_id}"
    setradio_payload = f"setradio_{owner_id}_{chat_id}"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 ON", url=f"https://t.me/{bot_username}?start={on_payload}"),
                InlineKeyboardButton("🔴 OFF", url=f"https://t.me/{bot_username}?start={off_payload}"),
            ],
            [
                InlineKeyboardButton("🔊 Set Radio", url=f"https://t.me/{bot_username}?start={setradio_payload}"),
                InlineKeyboardButton("🎧 Show Radio", callback_data=f"show_radio_{owner_id}_{chat_id}"),
            ],
            [InlineKeyboardButton("Close", callback_data=f"close_{owner_id}_{chat_id}")],
        ]
    )
    current = get_react_setting(owner_id, chat_id)
    await message.reply_text(
        f"Auto React Controller\n\nChat: `{message.chat.title or message.chat.id}`\nStatus: `{'ON' if current else 'OFF'}`\n\n"
        "Click ON/OFF to open the control bot and confirm the change.",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

@user_app.on_callback_query(filters.regex(r"^show_radio_\d+_"))
async def user_handle_show_radio(client: Client, cb: CallbackQuery):
    await ensure_owner_id()
    data = cb.data  # like "show_radio_<owner>_<chat>"
    try:
        _, owner_str, chat_str = data.split("_", 2)
        owner = int(owner_str)
        chat_id = int(chat_str)
    except Exception:
        await cb.answer("Invalid payload", show_alert=True)
        return
    requester = cb.from_user.id
    if requester != owner:
        await cb.answer("This button is for the userbot owner only.", show_alert=True)
        return
    url = get_radio(owner, chat_id)
    if not url:
        await cb.answer("No radio URL saved for this chat.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Play", url=url)]])
    try:
        await cb.message.reply_text(f"Radio for this chat:\n{url}", reply_markup=keyboard)
        await cb.answer()
    except Exception:
        await cb.answer("Failed to show radio.", show_alert=True)

@user_app.on_callback_query(filters.regex(r"^close_\d+_"))
async def user_handle_close(client: Client, cb: CallbackQuery):
    await ensure_owner_id()
    data = cb.data
    try:
        _, owner_str, chat_str = data.split("_", 2)
        owner = int(owner_str)
    except Exception:
        await cb.answer("Invalid payload", show_alert=True)
        return
    if cb.from_user.id != owner:
        await cb.answer("You are not allowed to close this.", show_alert=True)
        return
    try:
        await cb.message.delete()
        await cb.answer()
    except Exception:
        await cb.answer("Could not delete message.", show_alert=True)

@user_app.on_message(
    (filters.private | filters.group | filters.channel)
    & filters.incoming
    & ~filters.reply
)
async def auto_react(client: Client, message: Message):
    # Skip edited messages inside the handler
    if getattr(message, "edit_date", None):
        return
    await ensure_owner_id()
    chat_id = message.chat.id
    owner = OWNER_ID
    enabled = react_cache.get((owner, chat_id))
    if enabled is None:
        enabled = get_react_setting(owner, chat_id)
        react_cache[(owner, chat_id)] = enabled
    if not enabled:
        return
    emoji = random.choice(VALID_EMOJIS)
    try:
        await message.react(emoji=emoji)
        logger.info(f"Reacted {emoji} in chat {chat_id} (msg {message.id})")
    except ReactionInvalid:
        pass
    except FloodWait as e:
        logger.warning(f"FloodWait: sleeping {e.value}s")
        await asyncio.sleep(e.value)
    except PeerIdInvalid:
        logger.warning(f"PeerIdInvalid skipped: {chat_id}")
        try:
            await user_app.get_chat(chat_id)
        except Exception:
            pass
    except Exception as e:
        err = str(e)
        if "MESSAGE_ID_INVALID" in err or "REACTION_INVALID" in err:
            pass
        else:
            logger.exception("React failed")

@user_app.on_message(filters.command("setradio", prefixes=["!", "/"]) & filters.me)
async def user_set_radio(client: Client, message: Message):
    await ensure_owner_id()
    chat_id = message.chat.id
    owner = OWNER_ID
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: !setradio <url> or send !setradio none to unset")
        return
    url = parts[1].strip()
    if url.lower() in ("none", "off", "unset"):
        set_radio(owner, chat_id, None)
        await message.reply_text("Radio URL removed for this chat.")
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.reply_text("Please provide a valid http/https URL.")
        return
    set_radio(owner, chat_id, url)
    await message.reply_text("Saved radio URL. Use !radio to show it.")

@user_app.on_message(filters.command("radio", prefixes=["!", "/"]) & filters.me)
async def user_show_radio(client: Client, message: Message):
    await ensure_owner_id()
    chat_id = message.chat.id
    owner = OWNER_ID
    url = get_radio(owner, chat_id)
    if not url:
        await message.reply_text("No radio URL saved for this chat. Use !setradio <url> to set one.")
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Play", url=url)]])
    await message.reply_text(f"Radio for this chat:\n{url}", reply_markup=keyboard)

# --- Startup / Main ---
async def start_all():
    await user_app.start()
    # If no SESSION_STRING provided, this will prompt for phone code/2FA and create a new session
    # Export the new string and save to env for future runs
    if not SESSION_STRING:
        logger.info("No session string provided - authorizing new session...")
        # After start(), export and log/print the string
        session_str = await user_app.export_session_string()
        logger.info(f"New session string generated: {session_str}")
        # You can send it to yourself: await user_app.send_message("me", f"New Session: `{session_str}`")
    
    # start bot_app only if configured
    if bot_app:
        await bot_app.start()
    await ensure_owner_id()
    me = await user_app.get_me()
    try:
        logger.info(f"Userbot started as @{me.username or me.first_name} ({me.id})")
    except Exception:
        logger.info("Userbot started")
    if bot_app:
        bot_me = await bot_app.get_me()
        logger.info(f"Control bot started as @{bot_me.username} ({bot_me.id})")
    else:
        logger.info("Control bot not started (BOT_TOKEN missing)")
    try:
        await user_app.send_message("me", "🚀 Auto React Userbot is ONLINE.")
    except Exception:
        pass

async def stop_all():
    try:
        await user_app.stop()
    except Exception:
        pass
    if bot_app:
        try:
            await bot_app.stop()
        except Exception:
            pass

def run():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_all())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception:
        logger.exception("Fatal error")
    finally:
        loop.run_until_complete(stop_all())
        try:
            loop.close()
        except Exception:
            pass

if __name__ == "__main__":
    run()
