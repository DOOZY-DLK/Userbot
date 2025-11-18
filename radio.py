#DLK-DEVELOPER
import logging
import random
import string
import time
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
import asyncio

from YOUR import anon, app, config, db, lang, queue, tg, yt
from YOUR import buttons, utils

# Set up logging
LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Dictionary of radio stations with their stream URLs
RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "HelaNadaFM": "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    "Radio Plus Hitz": "https://altair.streamerr.co/stream/8054",
    "English": "https://hls-01-regions.emgsound.ru/11_msk/playlist.m3u8",
    "HiruFM": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden?1707015384",
    "RedFM": "https://shaincast.caster.fm:47830/listen.mp3",
    "RanFM": "https://207.148.74.192:7874/ran.mp3",
    "YFM": "http://live.trusl.com:1180/;",
    "+212": "http://stream.radio.co/sf55ced545/listen",
    "Deep House Music": "http://live.dancemusic.ro:7000/",
    "Radio Italia best music": "https://energyitalia.radioca.st",
    "The Best Music": "http://s1.slotex.pl:7040/",
    "HITZ FM": "https://stream-173.zeno.fm/uyx7eqengijtv",
    "Prime Radio HD": "https://stream-153.zeno.fm/oksfm5djcfxvv",
    "1Mix Radio - Trance": "https://fr3.1mix.co.uk:8000/128",
    "Mangled Music Radio": "http://hearme.fm:9500/autodj?8194",
    "ShreeFM": "https://207.148.74.192:7874/stream2.mp3",
    "ShaaFM": "https://radio.lotustechnologieslk.net:2020/stream/shaafmgarden",
    "SithaFM": "https://stream.streamgenial.stream/cdzzrkrv0p8uv",
    "Joint Radio Beat": "https://jointil.com/stream-beat",
    "eFM": "https://207.148.74.192:7874/stream",
    "RFI Tiếng Việt": "https://rfivietnamien96k.ice.infomaniak.ch/rfivietnamien-96k.mp3",
    "Phat": "https://phat.stream.laut.fm/phat",
    "Dai Phat Thanh Viet Nam": "http://c13.radioboss.fm:8127/stream",
    "Pulse EDM Dance Music Radio": "https://naxos.cdnstream.com/1373_128",
    "Base Music": "https://base-music.stream.laut.fm/base-music",
    "Ultra Music Festival": "http://prem4.di.fm/umfradio_hi?20a1d1bf879e76&_ic2=1733161375677",
    "Na Dahasa FM": "https://stream-155.zeno.fm/z7q96fbw7rquv",
    "Parani Gee Radio": "http://cast2.citrus3.com:8288/;",
    "SunFM": "https://radio.lotustechnologieslk.net:2020/stream/sunfmgarden",
    "The EDM MEGASHUFFLE": "https://maggie.torontocast.com:9030/stream",
}

def radio_buttons(page=0, per_page=5):
    stations = sorted(RADIO_STATION.keys())
    total_pages = (len(stations) - 1) // per_page + 1
    start = page * per_page
    end = start + per_page
    current_stations = stations[start:end]

    buttons_list = [
        [InlineKeyboardButton(name, callback_data=f"station_{name}") for name in current_stations[i:i+2]]
        for i in range(0, len(current_stations), 2)
    ]

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("Back", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next", callback_data=f"page_{page+1}"))

    if nav_buttons:
        buttons_list.append(nav_buttons)

    buttons_list.append([InlineKeyboardButton("Help", callback_data=f"radio_help_{page}")])

    return InlineKeyboardMarkup(buttons_list)

async def is_admin_or_anonymous(chat_id, user_id):
    if user_id == 1087968824:  # Anonymous admin ID
        return True
    member = await app.get_chat_member(chat_id, user_id)
    return member.status in ["administrator", "creator"]

async def update_timer(chat_id, message_id, station_name, start_time):
    while True:
        try:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            timer = f"{mins:02d}:{secs:02d}"
            await app.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=f"📻 Now playing: **{station_name}**\n⏳ Time: {timer}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"Station: {station_name}", callback_data="noop")],
                    [
                        InlineKeyboardButton("ʙᴏᴏꜱᴛᴇʀ", url="https://t.me/DLKGBOT"),
                        InlineKeyboardButton("ꜱᴛᴀᴛɪᴏɴꜱ", callback_data="skip_radio"),
                        InlineKeyboardButton("ᴄʟᴏꜱᴇ", callback_data="close_message")
                    ],
                    [InlineKeyboardButton("𓆩⌗DEVELOPER⌗𓆪", url="https://t.me/DLKDEVELOPERS")]
                ])
            )
        except Exception as e:
            LOGGER.error(f"Failed to update timer: {e}")
            break
        await asyncio.sleep(5)

@app.on_message(
    filters.command(["radio", "cradio", "radioplayforce"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@utils.checkUB  # Assuming checkUB is similar to your checkUB
async def radio_hndlr(_, m: Message, cplay: bool = False) -> None:
    chat_id = m.chat.id
    if cplay:
        channel_id = await db.get_cmode(m.chat.id)
        if channel_id is None:
            return await m.reply_text(
                "❌ **Channel play is not enabled.**\n\n"
                "**To enable for linked channel:**\n"
                "`/channelplay linked`\n\n"
                "**To enable for any channel:**\n"
                "`/channelplay [channel_id]`"
            )
        try:
            chat = await app.get_chat(channel_id)
            chat_id = channel_id
        except:
            await db.set_cmode(m.chat.id, None)
            return await m.reply_text(
                "❌ **Failed to get channel.**\n\n"
                "Make sure I'm admin in the channel and channel play is set correctly."
            )

    await m.reply_text(
        "Please select a radio station to play:",
        reply_markup=radio_buttons(page=0),
        reply_to_message_id=m.id
    )

@app.on_callback_query(filters.regex(r"^page_"))
async def on_page_change(_, callback_query):
    page = int(callback_query.data.split("_")[1])
    await callback_query.message.edit_reply_markup(radio_buttons(page=page))

@app.on_callback_query(filters.regex(r"^station_"))
async def on_station_select(_, callback_query):
    station_name = callback_query.data.split("station_")[1]
    RADIO_URL = RADIO_STATION.get(station_name)

    if RADIO_URL:
        mention = callback_query.from_user.mention if callback_query.from_user.id != 1087968824 else "Anonymous Admin"
        user_id = callback_query.from_user.id if callback_query.from_user.id != 1087968824 else 0

        mystic = await callback_query.message.reply_photo(
            photo="https://files.catbox.moe/3o9qj5.jpg",
            caption=f"📻 Now playing: **{station_name}**\n⏳ Time: 00:00",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Station: {station_name}", callback_data="noop")],
                [
                    InlineKeyboardButton("ʙᴏᴏꜱᴛᴇʀ", url="https://t.me/DLKGBOT"),
                    InlineKeyboardButton("ꜱᴛᴀᴛɪᴏɴꜱ", callback_data="skip_radio"),
                    InlineKeyboardButton("ᴄʟᴏꜱᴇ", callback_data="close_message")
                ],
                [InlineKeyboardButton("𓆩⌗DEVELOPER⌗𓆪", url="https://t.me/DLKDEVELOPERS")]
            ])
        )

        start_time = time.time()
        asyncio.create_task(update_timer(callback_query.message.chat.id, mystic.id, station_name, start_time))

        # Create a dummy file object for radio stream
        class RadioFile:
            def __init__(self, url, title, is_live=True):
                self.url = url
                self.title = title
                self.is_live = is_live
                self.duration = "Live Stream"
                self.duration_sec = 0  # No duration for live
                self.file_path = None  # Stream directly
                self.id = url  # Use URL as ID
                self.message_id = mystic.id

        file = RadioFile(RADIO_URL, station_name)

        if await db.is_logger():
            await utils.play_log(callback_query.message, file.title, file.duration)

        file.user = mention
        position = queue.add(callback_query.message.chat.id, file)

        if await db.get_call(callback_query.message.chat.id):
            await mystic.edit_text(
                callback_query.message.lang["play_queued"].format(
                    position,
                    file.url,
                    file.title,
                    file.duration,
                    mention,
                ),
                reply_markup=buttons.play_queued(
                    callback_query.message.chat.id, file.id, callback_query.message.lang["play_now"]
                ),
            )
            return

        # Play the stream
        await anon.play_media(chat_id=callback_query.message.chat.id, message=mystic, media=file)
    else:
        await callback_query.message.reply("Invalid station name.")

@app.on_callback_query(filters.regex(r"^skip_radio"))
async def skip_radio_callback(_, callback_query):
    if await is_admin_or_anonymous(callback_query.message.chat.id, callback_query.from_user.id):
        await callback_query.message.reply_text(
            "Please select another radio station to play:",
            reply_markup=radio_buttons(page=0)
        )
    else:
        await callback_query.answer("Only group admins can skip stations.", show_alert=True)

@app.on_callback_query(filters.regex(r"^close_message"))
async def close_message_callback(_, callback_query):
    if await is_admin_or_anonymous(callback_query.message.chat.id, callback_query.from_user.id):
        try:
            await callback_query.message.delete()
        except Exception as e:
            await callback_query.message.reply(f"Error deleting message: {str(e)}")
    else:
        await callback_query.answer("Only group admins can close this message.", show_alert=True)

@app.on_callback_query(filters.regex(r"^radio_help_"))
async def on_radio_help(_, callback_query):
    page = int(callback_query.data.split("_")[2])
    help_text = (
        "**English:**\n"
        "1. Type /radio to see the station list.\n"
        "2. Select a station using the buttons.\n"
        "3. Use '/end' to stop, '/skip' to change stations, or 'Close' to remove the message.\n\n"
        "**සිංහල:**\n"
        "1. /radio ලැයිස්තුව බලන්න.\n"
        "2. බටන් භාවිතයෙන් ස්ටේෂන් එකක් තෝරන්න.\n"
        "3. '/end' නවත්වන්න, '/skip' වලින් වෙනත් ස්ටේෂන් එකකට යන්න, හෝ 'Close' වලින් පණිවිඩය ඉවත් කරන්න.\n"
    )
    await callback_query.message.edit_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Stations", callback_data=f"back_to_stations_{page}")]
        ])
    )

@app.on_callback_query(filters.regex(r"^back_to_stations_"))
async def on_back_to_stations(_, callback_query):
    page = int(callback_query.data.split("_")[-1])
    await callback_query.message.edit_text(
        "Please select a radio station to play:",
        reply_markup=radio_buttons(page=page)
    )

@app.on_message(filters.command("edit") & filters.user(config.OWNER_ID))
async def edit_command(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("/edit <new text>")
        return

    new_text = " ".join(message.command[1:])
    final_text = new_text

    original_msg = await message.reply_text("𓆩⌗DLK DEVELOPER⌗𓆪")
    await asyncio.sleep(2)

    T = 50  # Number of updates over 10 seconds
    interval = 0.2  # 10 seconds / 50 updates = 0.2 seconds per update
    N = len(final_text)
    random_chars = string.ascii_letters + string.digits

    # Precompute when each character should be revealed
    reveal_steps = [random.randint(0, T - 1) for _ in range(N)]

    for k in range(T):
        text = ''.join(
            final_text[i] if k >= reveal_steps[i] else random.choice(random_chars)
            for i in range(N)
        )
        try:
            await original_msg.edit_text(text)
        except Exception as e:
            LOGGER.error(f"Error editing message: {e}")
            break
        await asyncio.sleep(interval)

    await original_msg.edit_text(final_text)

@app.on_callback_query(filters.regex(r"^noop"))
async def on_noop(_, callback_query):
    await callback_query.answer("SEE THE FUTURE THROUGH MY VISION", show_alert=False)