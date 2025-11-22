#!/usr/bin/env python3
"""
Combined Userbot + Control Bot + Radio assistant (single entry app.py)

- Userbot runs as a user session (supports session_string).
- Optional control-bot (BOT_TOKEN) handles deep-links from buttons posted by the userbot.
- Optional assistant account (ASSISTANT_SESSION) + PyTgCalls manages voice chat playback.
- MongoDB stores per-chat settings (auto-react and radio_url).
- Reactive auto-react feature (random emoji) is run by the userbot account.
- Userbot posts inline keyboards that open the control-bot for confirmation (ON/OFF/Set Radio).
- The control-bot receives the /start payloads and updates DB (only the owner can change).
- The DLK radio bot (bot account) provides richer radio menus & voice playback via the assistant.

Notes:
- Designed for Pyrogram v2.2.13 and PyTgCalls usage.
- yt-dlp is optional; if missing, /play (youtube) won't work.
- Cookies for yt-dlp optional (YT_DLP_COOKIES env) — omitted handling will still work for many streams.
"""
import os
import sys
import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
load_dotenv()

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.errors import FloodWait, ReactionInvalid, PeerIdInvalid

# optional components
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
except Exception:
    PyTgCalls = None
    MediaStream = None

# optional yt_dlp
try:
    import yt_dlp as youtube_dl
except Exception:
    youtube_dl = None

# optional pymongo
try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dlk_app")

# ===================== ENV / CONFIG =====================
API_ID = int(os.environ.get("API_ID", "") or 0)
API_HASH = os.environ.get("API_HASH", "") or ""
SESSION_STRING = os.environ.get("SESSION_STRING")  # user session string (recommended)
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # control bot token (optional)
DLK_BOT_TOKEN = os.environ.get("DLK_BOT_TOKEN")  # actual DLK radio bot token (optional)
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")  # assistant account session string (optional)
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
OWNER_ID_ENV = os.environ.get("OWNER_ID")  # optional pre-set owner id
OWNER_ID: Optional[int] = int(OWNER_ID_ENV) if OWNER_ID_ENV else None

if not API_ID or not API_HASH or not MONGO_URI:
    logger.critical("API_ID, API_HASH and MONGO_URI must be set in environment")
    # We exit because DB is required for settings persistence in this combined app.
    raise SystemExit(1)

# ===================== MONGO SETUP =====================
mongo_client = MongoClient(MONGO_URI) if MongoClient else None
if mongo_client is None:
    logger.critical("pymongo not installed. Install pymongo to use DB-backed settings.")
    raise SystemExit(1)
db = mongo_client.get_database(MONGO_DBNAME)
settings_coll = db.get_collection("react_settings")
# optional playing state collection used by radio assistant
playing_coll = db.get_collection("playing")

# ===================== IN-MEMORY CACHES & CONSTANTS =====================
VALID_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌭", "💯", "🤣", "⚡", "🍌",
    "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴"
]

react_cache: Dict[tuple, bool] = {}
radio_cache: Dict[tuple, str] = {}

# Example radio stations (you can extend)
RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "HelaNadaFM": "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    "RedFM": "https://shaincast.caster.fm:47830/listen.mp3",
    "HiruFM": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden?1707015384",
}

# ===================== CLIENTS =====================
# userbot client (supports session_string)
user_app = Client(
    name="userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

# control bot - optional (handles /start deep links that userbot buttons open)
control_bot = None
if BOT_TOKEN:
    control_bot = Client("control_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
else:
    logger.warning("BOT_TOKEN not set — control bot disabled. Set BOT_TOKEN to enable it.")

# dlk radio bot - optional: provides radio menus & assists assistant join flow (if DLK_BOT_TOKEN provided)
dlk_bot = None
if DLK_BOT_TOKEN:
    dlk_bot = Client("dlk_bot", api_id=API_ID, api_hash=API_HASH, bot_token=DLK_BOT_TOKEN, in_memory=True)
else:
    logger.info("DLK_BOT_TOKEN not set — radio bot UI disabled. You can still use userbot's radio features.")

# assistant account (session string) for voice chat playback (PyTgCalls)
assistant = None
call_py = None
if ASSISTANT_SESSION:
    assistant = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=ASSISTANT_SESSION, in_memory=True)
    if PyTgCalls:
        call_py = PyTgCalls(assistant)
    else:
        logger.warning("pytgcalls not available - voice chat playback disabled.")
else:
    logger.info("ASSISTANT_SESSION not provided — assistant/voice features disabled.")

# compatibility alias for any legacy code that expects `app`
app = user_app

# ===================== DB UTIL FUNCTIONS =====================
def _key(owner_id: int, chat_id: int) -> dict:
    return {"owner_id": owner_id, "chat_id": chat_id}

def get_react_setting(owner_id: int, chat_id: int) -> bool:
    k = _key(owner_id, chat_id)
    doc = settings_coll.find_one(k, {"react": 1})
    if doc and "react" in doc:
        return bool(doc["react"])
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

def load_caches_for_owner(owner_id: int):
    for doc in settings_coll.find({"owner_id": owner_id}):
        key = (doc["owner_id"], doc["chat_id"])
        react_cache[key] = bool(doc.get("react", True))
        if "radio_url" in doc:
            radio_cache[key] = doc["radio_url"]

# ===================== STARTUP helper to set OWNER_ID =====================
async def ensure_owner_id():
    global OWNER_ID
    if OWNER_ID is None:
        me = await user_app.get_me()
        OWNER_ID = me.id
    load_caches_for_owner(OWNER_ID)
    logger.info(f"Owner user id: {OWNER_ID}")

# ===================== CONTROL-BOT HANDLERS (register if exists) =====================
def register_control_handlers(bot_client: Client):
    @bot_client.on_message(filters.command("start") & filters.private)
    async def bot_start(client: Client, message: Message):
        # /start payload handling
        if len(message.command) < 2:
            await message.reply_text(
                "Hello! This control bot toggles settings for the Userbot.\n"
                "You must open it from the button sent by the Userbot."
            )
            return
        payload = message.command[1]
        try:
            # react_on_<owner>_<chat>
            if payload.startswith("react_on_") or payload.startswith("react_off_"):
                # split only on prefix: react_on_ -> tail owner_chat
                tail = payload.split("_", 2)[2]
                # tail is "<owner>_<chat_id_or_peer>"
                owner_str, chat_str = tail.split("_", 1)
                owner = int(owner_str)
                chat_id = int(chat_str)
                requester = message.from_user.id
                if requester != owner:
                    await message.reply_text("You are not the owner for this setting.")
                    return
                enabled = payload.startswith("react_on_")
                set_react_setting(owner, chat_id, enabled)
                text = "🟢 Auto React ENABLED!" if enabled else "🔴 Auto React DISABLED!"
                # try to notify the target chat (bot may not be a member)
                posted = False
                try:
                    await bot_client.send_message(chat_id, f"{text}\n(Changed by @{message.from_user.username or message.from_user.first_name})")
                    posted = True
                except Exception:
                    posted = False
                if posted:
                    await message.reply_text(f"{text}\nNotified the chat successfully.")
                else:
                    await message.reply_text(f"{text}\n(Control bot couldn't post in the chat — maybe it's not a member).")
                return

            if payload.startswith("setradio_"):
                tail = payload.split("_", 1)[1]
                # tail is "<owner>_<chat>"
                parts = tail.split("_", 1)
                if len(parts) != 2:
                    await message.reply_text("Invalid payload.")
                    return
                owner = int(parts[0])
                chat_id = int(parts[1])
                if message.from_user.id != owner:
                    await message.reply_text("This radio button is for another user.")
                    return
                await message.reply_text("Send the radio stream URL (http/https) or send 'cancel'. Waiting 120s.")
                try:
                    resp = await bot_client.listen(message.chat.id, filters=filters.user(message.from_user.id) & filters.text, timeout=120)
                except asyncio.TimeoutError:
                    await message.reply_text("Timed out waiting for URL.")
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
                await message.reply_text("Saved radio URL for this chat.")
                try:
                    await bot_client.send_message(chat_id, f"🔊 Radio set by @{message.from_user.username or message.from_user.first_name}. Use the userbot !radio command to show it.")
                except Exception:
                    pass
                return

            await message.reply_text("Unknown start parameter for control bot.")
        except Exception as e:
            logger.exception("Error handling /start in control bot")
            await message.reply_text(f"Error processing: {e}")

    @bot_client.on_message(filters.command("help") & filters.private)
    async def bot_help(client: Client, message: Message):
        await message.reply_text(
            "Control Bot Help\n"
            "- This bot receives start payloads from the Userbot buttons.\n"
            "- Only the owner can change their userbot settings."
        )

if control_bot:
    register_control_handlers(control_bot)

# ===================== USERBOT HANDLERS =====================
@user_app.on_message(filters.command("help", prefixes=["!", "/"]) & filters.me)
async def user_help(client: Client, message: Message):
    bot_username = None
    if control_bot:
        try:
            bot_user = await control_bot.get_me()
            bot_username = bot_user.username
        except Exception:
            bot_username = None
    if not bot_username:
        bot_username = "<control bot not configured>"
    await message.reply_text(
        "Userbot Help\n\n"
        "!react - Post control buttons to toggle Auto-React for this chat\n"
        "!setradio <url> - Save a radio URL for this chat\n"
        "!radio - Show saved radio link (Play button opens the stream URL)\n"
        "!help - Show this message\n\n"
        f"Control actions open @{bot_username} for confirmation (if configured). Only the owner can change settings."
    )

@user_app.on_message(filters.command("react", prefixes=["!", "/"]) & (filters.group | filters.channel) & filters.me)
async def user_post_react_buttons(client: Client, message: Message):
    await ensure_owner_id()
    if not control_bot:
        await message.reply_text(
            "Auto React Controller\n\nControl bot not configured (BOT_TOKEN missing). Set BOT_TOKEN to enable remote ON/OFF buttons."
        )
        return
    try:
        bot_user = await control_bot.get_me()
        bot_username = bot_user.username or bot_user.first_name
    except Exception:
        bot_username = None
    if not bot_username:
        await message.reply_text("Control bot username not available. Ensure the control bot started correctly.")
        return

    chat_id = message.chat.id
    owner_id = OWNER_ID
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
        f"Auto React Controller\n\nChat: `{message.chat.title or message.chat.id}`\nStatus: `{'ON' if current else 'OFF'}`\n\nClick ON/OFF to open the control bot and confirm the change.",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

@user_app.on_callback_query(filters.regex(r"^show_radio_\d+_"))
async def user_handle_show_radio(client: Client, cb: CallbackQuery):
    await ensure_owner_id()
    data = cb.data
    try:
        _, owner_str, chat_str = data.split("_", 2)
        owner = int(owner_str); chat_id = int(chat_str)
    except Exception:
        await cb.answer("Invalid payload", show_alert=True)
        return
    if cb.from_user.id != owner:
        await cb.answer("This button is for the userbot owner only.", show_alert=True)
        return
    url = get_radio(owner, chat_id)
    if not url:
        await cb.answer("No radio URL saved for this chat.", show_alert=True)
        return
    # Show a Play button that links directly to the stream (opens in external player)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Stream", url=url)]])
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
        _, owner_str, _ = data.split("_", 2)
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

@user_app.on_message((filters.private | filters.group | filters.channel) & filters.incoming & ~filters.reply)
async def auto_react(client: Client, message: Message):
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
    except Exception:
        logger.exception("React failed")

@user_app.on_message(filters.command("setradio", prefixes=["!", "/"]) & filters.me)
async def user_set_radio(client: Client, message: Message):
    await ensure_owner_id()
    chat_id = message.chat.id
    owner = OWNER_ID
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: !setradio <url> or !setradio none to unset")
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
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Stream", url=url)]])
    await message.reply_text(f"Radio for this chat:\n{url}", reply_markup=keyboard)

# ===================== DLK RADIO BOT (optional) - provides menu & playback via assistant =====================
# If dlk_bot and assistant are provided, register DLK radio handlers on dlk_bot
def register_dlk_bot_handlers(bot_client: Client):
    # helper: radio buttons & player controls
    def radio_buttons(page: int = 0, per_page: int = 6):
        stations = sorted(RADIO_STATION.keys())
        total_pages = (len(stations) - 1) // per_page + 1
        start = page * per_page
        end = start + per_page
        current = stations[start:end]
        buttons = []
        for i in range(0, len(current), 2):
            row = [InlineKeyboardButton(current[i], callback_data=f"radio_play_{current[i]}")]
            if i + 1 < len(current):
                row.append(InlineKeyboardButton(current[i+1], callback_data=f"radio_play_{current[i+1]}"))
            buttons.append(row)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◁", callback_data=f"radio_page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▷", callback_data=f"radio_page_{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("❌ Close Menu", callback_data="radio_close")])
        return InlineKeyboardMarkup(buttons)

    def player_controls_markup(chat_id: int):
        # minimal controls - they call back to the bot which will instruct assistant
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("II", callback_data="radio_pause"), InlineKeyboardButton("‣‣I", callback_data="music_skip"), InlineKeyboardButton("▢", callback_data="radio_stop")],
            [InlineKeyboardButton("❌ Close", callback_data="radio_close")]
        ])

    @bot_client.on_message(filters.command("radio") & filters.group)
    async def cmd_radio_menu(_, message: Message):
        kb = radio_buttons(0)
        await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)

    @bot_client.on_callback_query(filters.regex("^radio_play_"))
    async def play_radio_station(_, query: CallbackQuery):
        # Called when a station button is pressed in a group
        station = query.data.replace("radio_play_", "")
        url = RADIO_STATION.get(station)
        chat_id = query.message.chat.id
        user = query.from_user
        if not url:
            await query.answer("Station URL not found", show_alert=True)
            return

        # If assistant not configured -> just send stream URL as fallback
        if assistant is None or call_py is None:
            try:
                await query.message.reply_text(f"▶️ {station}\n{url}")
            except Exception:
                pass
            await query.answer("Assistant not configured — opened link instead.")
            return

        # Ensure assistant is in the group (simplified: try joining via invite link)
        try:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
        except Exception:
            assistant_id = None

        assistant_present = False
        if assistant_id:
            try:
                await assistant.get_chat_member(chat_id, assistant_id)
                assistant_present = True
            except Exception:
                assistant_present = False

        if not assistant_present:
            # create invite link and attempt join
            try:
                invite = await bot_client.create_chat_invite_link(chat_id, member_limit=1, name="dlk_assistant_invite")
                invite_link = invite.invite_link
                try:
                    await assistant.join_chat(invite_link)
                    assistant_present = True
                except Exception:
                    assistant_present = False
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📎 Invite Link", url=invite_link)]])
                    await query.message.reply_text("Assistant is not in the group. Add it using this invite link and then retry.", reply_markup=kb)
                    await query.answer("Assistant missing", show_alert=True)
                    return
            except Exception:
                await query.message.reply_text("Cannot create invite link. Please add the assistant account manually.")
                await query.answer("Invite failed", show_alert=True)
                return

        # play using PyTgCalls
        try:
            # robust call: call_py.play may be sync/async depending on version
            res = call_py.play(chat_id, MediaStream(url))
            if asyncio.iscoroutine(res):
                await res
            # edit original message to show controls (if message is text)
            try:
                await query.message.edit_caption(f"🎧 Connecting to {station}...", reply_markup=player_controls_markup(chat_id))
            except Exception:
                try:
                    await query.message.edit_text(f"🎧 Connecting to {station}...", reply_markup=player_controls_markup(chat_id))
                except Exception:
                    pass
            await query.answer(f"Now playing {station}", show_alert=False)
        except Exception as e:
            logger.exception("Failed to start playback via assistant")
            await query.message.reply_text(f"Failed to start playback: {e}")
            await query.answer("Failed to start", show_alert=True)

    @bot_client.on_callback_query(filters.regex("^radio_page_"))
    async def cb_radio_page(_, query: CallbackQuery):
        try:
            page = int(query.data.split("_")[-1])
            kb = radio_buttons(page)
            try:
                await query.message.edit_text("📻 Radio Stations - choose one:", reply_markup=kb)
            except Exception:
                try:
                    await query.message.edit_reply_markup(reply_markup=kb)
                except Exception:
                    pass
            await query.answer()
        except Exception:
            await query.answer()

    @bot_client.on_callback_query(filters.regex("^radio_close$"))
    async def cb_radio_close(_, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer()

if dlk_bot:
    register_dlk_bot_handlers(dlk_bot)

# ===================== START/STOP & RUN helpers =====================
async def start_all():
    # start user app
    await user_app.start()
    if not SESSION_STRING:
        # export session string for convenience (printed in logs)
        try:
            session_str = await user_app.export_session_string()
            logger.info("New session string generated. Please save it for future runs.")
            logger.info(session_str)
        except Exception:
            pass

    # start control bot
    if control_bot:
        await control_bot.start()

    # start dlk radio bot
    if dlk_bot:
        await dlk_bot.start()

    # start assistant and pytgcalls
    if assistant:
        await assistant.start()
        if call_py:
            call_py.start()

    await ensure_owner_id()
    me = await user_app.get_me()
    logger.info(f"Userbot started as @{me.username or me.first_name} ({me.id})")
    if control_bot:
        try:
            cb = await control_bot.get_me()
            logger.info(f"Control bot started as @{cb.username} ({cb.id})")
        except Exception:
            logger.info("Control bot started (username unknown).")
    if dlk_bot:
        try:
            b = await dlk_bot.get_me()
            logger.info(f"DLK bot started as @{b.username} ({b.id})")
        except Exception:
            logger.info("DLK bot started.")
    if assistant:
        try:
            a = await assistant.get_me()
            logger.info(f"Assistant started as @{a.username} ({a.id})")
        except Exception:
            logger.info("Assistant started.")

async def stop_all():
    try:
        if call_py:
            call_py.stop()
    except Exception:
        pass
    try:
        if assistant:
            await assistant.stop()
    except Exception:
        pass
    try:
        if dlk_bot:
            await dlk_bot.stop()
    except Exception:
        pass
    try:
        if control_bot:
            await control_bot.stop()
    except Exception:
        pass
    try:
        await user_app.stop()
    except Exception:
        pass

def run():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_all())
        # keep running until ctrl+c
        from pyrogram import idle
        idle()
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
    except Exception:
        logger.exception("Fatal error during run")
    finally:
        try:
            loop.run_until_complete(stop_all())
        except Exception:
            pass

if __name__ == "__main__":
    run()
