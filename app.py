#!/usr/bin/env python3
"""
Userbot + Control Bot for Auto React + Radio link feature

Features:
- Auto-react to incoming messages in private, groups and channels (skips edited & replies)
- Per-chat ON/OFF stored in MongoDB (per-user account)
- Uses a Telegram Bot (BOT_TOKEN) to handle inline-button interactions via deep links
  (buttons link to the bot: https://t.me/<BOT_USERNAME>?start=...)
  This allows safe toggling through the bot UI while storing settings in MongoDB.
- Commands use '!' prefix (also supports '/')
  - !react  -> posts inline buttons that open the bot to toggle ON/OFF for that chat (only owner can toggle)
  - !setradio <url> -> store a radio URL for that chat (only owner)
  - !radio -> show current radio URL with a Play button
  - !help -> show help text
- Only the user account (owner) is allowed to change settings (the bot will refuse others)
- Uses pymongo to persist settings across restarts

Requirements:
- Python 3.8+
- pyrogram
- pymongo

Environment variables:
- API_ID, API_HASH, SESSION_STRING (for user account client)
- BOT_TOKEN (for the control bot)
- MONGO_URI (mongodb connection string)
- OWNER_ID (optional; if not provided, owner's id is discovered from the user session on start)

Notes:
- The control bot must be reachable via https://t.me/<BOT_USERNAME>.
  The owner and/or the bot should be added to groups where you expect to toggle settings
  if you want the bot to post confirmations in the group. Otherwise, confirmations are sent to owner's private chat.
- Radio "play" is implemented as providing a link with a "Play" button (opens the URL in the client).
  Implementing live streaming / voice chat joining requires additional libraries (e.g., pytgcalls) and
  is out of scope of this script.
"""

import os
import asyncio
import logging
import random
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

# --- Configuration from environment ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", None)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
# Optional: supply OWNER_ID if you want to avoid discovering owner from session
OWNER_ID_ENV = os.environ.get("OWNER_ID")

if not API_ID or not API_HASH or not SESSION_STRING:
    logger.critical("API_ID, API_HASH and SESSION_STRING must be set in environment")
    raise SystemExit(1)

if not BOT_TOKEN:
    logger.critical("BOT_TOKEN must be set in environment")
    raise SystemExit(1)

# --- Initialize clients ---
user_app = Client(
    "userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

bot_app = Client(
    "control_bot",
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# --- MongoDB setup ---
mongo = MongoClient(MONGO_URI)
db = mongo.get_database("userbot_db")
settings_coll = db.get_collection("react_settings")
# Document schema (example):
# {
#   "owner_id": 123456789,
#   "chat_id": -100111222333,
#   "react": true,
#   "radio_url": "https://example.com/stream"
# }

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

# Utility DB functions


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


# --- Bot handlers (control bot) ---
@bot_app.on_message(filters.command("start") & filters.private)
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
    # Expected formats:
    # react_on_<owner_id>_<chat_id>
    # react_off_<owner_id>_<chat_id>
    try:
        if payload.startswith("react_on_") or payload.startswith("react_off_"):
            parts = payload.split("_", 2)
            # parts[0] = react, parts[1] = on/off, parts[2] = <owner_id>_<chat_id>
            # But because we used split with max 2, parts[2] will be "<ownerId>_<chatId>" or include extra underscores.
            # We'll re-split parts[2] from the right to find chat_id.
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

            # Try to notify the group/channel if the bot is present there:
            posted = False
            try:
                await bot_app.send_message(chat_id, f"{text}\n(Changed by @{message.from_user.username or message.from_user.first_name})")
                posted = True
            except Exception:
                # Not able to post in the chat (bot not member / no permission)
                posted = False

            if not posted:
                await message.reply_text(f"{text}\n(Chat notification failed — bot may not be in the chat).")
            else:
                await message.reply_text(f"{text}\nNotified the chat successfully.")

        elif payload.startswith("setradio_"):
            # format: setradio_<owner>_<chat>
            _, tail = payload.split("_", 1)
            owner_str, chat_str = tail.split("_", 1)
            owner = int(owner_str)
            chat_id = int(chat_str)
            requester = message.from_user.id

            if requester != owner:
                await message.reply_text("This radio button is for another user.")
                return

            # Ask user to send the radio URL as next message:
            await message.reply_text(
                "Send me the radio stream URL (http/https). I'll save it for that chat.\n"
                "Send 'cancel' to abort."
            )

            # Wait for a single reply from the same user within 2 minutes
            try:
                resp = await bot_app.listen(
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

            # basic validation
            if not (text.startswith("http://") or text.startswith("https://")):
                await message.reply_text("That doesn't look like a valid URL. Cancelled.")
                return

            set_radio(owner, chat_id, text)
            await message.reply_text("Saved radio URL. Use the Userbot command !radio in the chat to show/play it.")
            # optional: post to chat
            try:
                await bot_app.send_message(chat_id, f"🔊 Radio set by @{message.from_user.username or message.from_user.first_name}.\nUse the play button or the userbot command to show it.")
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


@bot_app.on_message(filters.command("help") & filters.private)
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


# --- Userbot handlers ---
async def ensure_owner_id():
    global OWNER_ID
    if OWNER_ID is None:
        me = await user_app.get_me()
        OWNER_ID = me.id
    # load cached settings for owner
    load_caches_for_owner(OWNER_ID)
    logger.info(f"Owner user id: {OWNER_ID}")


@user_app.on_message(filters.command("help", prefixes=["!", "/"]) & filters.me)
async def user_help(client: Client, message: Message):
    bot_user = await bot_app.get_me()
    bot_username = bot_user.username
    await message.reply_text(
        "Userbot Help (commands start with ! or /)\n\n"
        "!react  - Post bot-control buttons in the chat (use to toggle Auto React for that chat)\n"
        "!setradio <url> - Save a radio stream URL for this chat (owner only)\n"
        "!radio - Show the saved radio link with a Play button\n"
        "!help - Show this message\n\n"
        f"Buttons will open @{bot_username} to confirm actions.\n"
        "Only the owner account can change settings — others cannot toggle your userbot."
    )


@user_app.on_message(filters.command("react", prefixes=["!", "/"]) & (filters.group | filters.channel) & filters.me)
async def user_post_react_buttons(client: Client, message: Message):
    """
    Post inline buttons that open the control bot via /start deep-link to toggle.
    Buttons are URLs so clicking them opens the bot (private chat) and the bot performs the toggle.
    """
    await ensure_owner_id()
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
    """
    When the inline 'Show Radio' button (created by userbot) is clicked,
    this callback is received by the userbot (because userbot sent the message).
    Only allow the owner to use it (cb.from_user.id should be owner).
    """
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
    # Allow only owner to close the message
    await ensure_owner_id()
    data = cb.data  # close_<owner>_<chat>
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
    & ~filters.edited
)
async def auto_react(client: Client, message: Message):
    """
    React automatically using a random valid emoji, if enabled for this owner/chat.
    """
    # Skip messages without id
    if not getattr(message, "id", None):
        return

    # Ensure owner known
    await ensure_owner_id()
    chat_id = message.chat.id
    owner = OWNER_ID

    # If setting cached, use cache; otherwise default True
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
        # emoji not supported
        pass
    except FloodWait as e:
        logger.warning(f"FloodWait: sleeping {e.value}s")
        await asyncio.sleep(e.value)
    except PeerIdInvalid:
        logger.warning(f"PeerIdInvalid skipped: {chat_id}")
        # Try to auto-resolve by fetching chat
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
    """
    Save radio URL for this chat. Usage:
      !setradio https://example.com/stream
    """
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
    # Start both clients
    await user_app.start()
    await bot_app.start()

    # discover owner id and load caches
    await ensure_owner_id()

    # show started
    me = await user_app.get_me()
    try:
        logger.info(f"Userbot started as @{me.username or me.first_name} ({me.id})")
    except Exception:
        logger.info("Userbot started")

    bot_me = await bot_app.get_me()
    logger.info(f"Control bot started as @{bot_me.username} ({bot_me.id})")

    # Send alive message to saved "me" chat for owner once
    try:
        await user_app.send_message("me", "🚀 Auto React Userbot is ONLINE.")
    except Exception:
        pass


async def stop_all():
    try:
        await user_app.stop()
    except Exception:
        pass
    try:
        await bot_app.stop()
    except Exception:
        pass


def run():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_all())
        # keep running forever
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
