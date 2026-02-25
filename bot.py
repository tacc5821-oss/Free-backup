import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import logging

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, ContentType, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

COOLDOWN = 90
BATCH_SIZE = 30
AUTO_DELETE_OPTIONS = [5, 10, 30]

# ==================== BOT INIT ====================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== GLOBAL VARIABLES ====================
ACTIVE_USERS = 0
WAITING_QUEUE = asyncio.Queue()
BATCH_LOCK = asyncio.Lock()
USER_PROCESSING_TIME = {}
MOVIES_DICT = {}

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ==================== JSON Functions ====================
def load_json(name):
    path = f"{DATA_DIR}/{name}.json"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(name, data):
    path = f"{DATA_DIR}/{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ==================== MOVIES ====================
async def get_movies():
    return load_json("movies")

async def load_movies_cache():
    global MOVIES_DICT
    movies = await get_movies()
    MOVIES_DICT = {}
    for m in movies:
        if m.get("movie_code"):
            MOVIES_DICT[m["movie_code"].upper()] = m
    print(f"✅ Loaded {len(MOVIES_DICT)} movies to cache")

async def reload_movies_cache():
    await load_movies_cache()

def find_movie_by_code(code: str) -> Optional[dict]:
    return MOVIES_DICT.get(code.upper())

async def add_movie_record(name, code, msgid, chatid):
    movies = load_json("movies")
    movies.append({
        "movie_name": name,
        "movie_code": code.upper(),
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("movies", movies)
    await reload_movies_cache()

async def delete_movie(code):
    movies = load_json("movies")
    movies = [m for m in movies if m.get("movie_code", "").upper() != code.upper()]
    save_json("movies", movies)
    await reload_movies_cache()

# ==================== ADS ====================
async def get_ads():
    return load_json("ads")

async def add_ad(msgid, chatid):
    ads = load_json("ads")
    ads.append({
        "id": len(ads) + 1,
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("ads", ads)

async def delete_ad(aid):
    ads = load_json("ads")
    ads = [a for a in ads if a["id"] != int(aid)]
    save_json("ads", ads)

# ==================== USERS ====================
async def get_users():
    return load_json("users")

async def add_new_user(uid, name, mention):
    users = load_json("users")
    for u in users:
        if u["user_id"] == uid:
            return False
    
    users.append({
        "user_id": uid,
        "last_search": None,
        "join_date": datetime.now().isoformat(),
        "name": name,
        "mention": mention,
        "search_count": 0
    })
    save_json("users", users)
    return True

async def get_user_count():
    return len(load_json("users"))

async def update_user_search(uid):
    users = load_json("users")
    found = False
    for u in users:
        if u["user_id"] == uid:
            u["last_search"] = datetime.now().isoformat()
            u["search_count"] = u.get("search_count", 0) + 1
            found = True
            break
    if not found:
        users.append({
            "user_id": uid,
            "last_search": datetime.now().isoformat(),
            "join_date": datetime.now().isoformat(),
            "name": "Unknown",
            "mention": "",
            "search_count": 1
        })
    save_json("users", users)

async def get_user_last(uid):
    users = load_json("users")
    for u in users:
        if u["user_id"] == uid:
            return u.get("last_search")
    return None

async def get_top_searches(limit=5):
    users = load_json("users")
    filtered = [u for u in users if u.get("search_count", 0) > 0]
    sorted_users = sorted(filtered, key=lambda x: x.get("search_count", 0), reverse=True)
    return sorted_users[:limit]

async def get_daily_active_users():
    users = load_json("users")
    yesterday = datetime.now() - timedelta(days=1)
    count = 0
    for u in users:
        last = u.get("last_search")
        if last and datetime.fromisoformat(last) >= yesterday:
            count += 1
    return count

# ==================== SETTINGS ====================
async def get_setting(key):
    settings = load_json("settings")
    for s in settings:
        if s["key"] == key:
            return s.get("value")
    return None

async def set_setting(key, value):
    settings = load_json("settings")
    found = False
    for s in settings:
        if s["key"] == key:
            s["value"] = value
            found = True
            break
    if not found:
        settings.append({"key": key, "value": value})
    save_json("settings", settings)

async def get_next_ad_index():
    current = await get_setting("last_ad_index")
    if current is None:
        current = 0
    else:
        try:
            current = int(current)
        except:
            current = 0

    ads = await get_ads()
    if not ads:
        return None

    next_idx = (current + 1) % len(ads)
    await set_setting("last_ad_index", next_idx)
    return current % len(ads)

# ==================== AUTO DELETE ====================
async def get_auto_delete_config():
    configs = load_json("auto_delete")
    if not configs:
        configs = [
            {"type": "group", "seconds": 0},
            {"type": "dm", "seconds": 0}
        ]
        save_json("auto_delete", configs)
    return configs

async def set_auto_delete_config(config_type, value):
    configs = load_json("auto_delete")
    found = False
    for c in configs:
        if c["type"] == config_type:
            c["seconds"] = value
            found = True
            break
    if not found:
        configs.append({"type": config_type, "seconds": value})
    save_json("auto_delete", configs)

# ==================== FORCE CHANNELS ====================
async def get_force_channels():
    return load_json("force_channels")

async def add_force_channel(chat_id, title, invite):
    channels = load_json("force_channels")
    channels.append({
        "id": len(channels) + 1,
        "chat_id": chat_id,
        "title": title,
        "invite": invite
    })
    save_json("force_channels", channels)

async def delete_force_channel(cid):
    channels = load_json("force_channels")
    channels = [c for c in channels if c["id"] != int(cid)]
    save_json("force_channels", channels)

# ==================== CUSTOM TEXTS ====================
async def get_custom_text(key):
    texts = load_json("custom_texts")
    for t in texts:
        if t["key"] == key:
            return {
                "text": t.get("text", ""),
                "photo_id": t.get("photo_id"),
                "sticker_id": t.get("sticker_id"),
                "animation_id": t.get("animation_id")
            }
    return {"text": "", "photo_id": None, "sticker_id": None, "animation_id": None}

async def set_custom_text(key, text=None, photo_id=None, sticker_id=None, animation_id=None):
    texts = load_json("custom_texts")
    found = False
    for t in texts:
        if t["key"] == key:
            if text is not None:
                t["text"] = text
            if photo_id:
                t["photo_id"] = photo_id
            if sticker_id:
                t["sticker_id"] = sticker_id
            if animation_id:
                t["animation_id"] = animation_id
            found = True
            break
    if not found:
        texts.append({
            "key": key,
            "text": text or "",
            "photo_id": photo_id,
            "sticker_id": sticker_id,
            "animation_id": animation_id
        })
    save_json("custom_texts", texts)

# ==================== START WELCOME ====================
async def get_start_welcome():
    welcome = load_json("start_welcome")
    if not welcome:
        return [{
            "text": "👋 **Welcome to Movie Bot!**\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။",
            "photo_id": None,
            "caption": ""
        }]
    return welcome

async def get_next_welcome_photo():
    data = await get_start_welcome()
    if not data:
        return None

    current = await get_setting("welcome_photo_index")
    if current is None:
        current = 0
    else:
        try:
            current = int(current)
        except:
            current = 0

    next_idx = (current + 1) % len(data)
    await set_setting("welcome_photo_index", next_idx)

    return data[current % len(data)]

async def add_start_welcome(text=None, photo_id=None, caption=None):
    welcome = load_json("start_welcome")
    welcome.append({
        "id": len(welcome) + 1,
        "text": text or "👋 **Welcome to Movie Bot!**",
        "photo_id": photo_id,
        "caption": caption or ""
    })
    save_json("start_welcome", welcome)

async def delete_start_welcome(index):
    welcome = load_json("start_welcome")
    if 0 <= index < len(welcome):
        welcome.pop(index)
        save_json("start_welcome", welcome)
        return True
    return False

async def get_start_welcome_count():
    return len(load_json("start_welcome"))

# ==================== START BUTTONS ====================
async def get_start_buttons():
    return load_json("start_buttons")

async def add_start_button(name, link, row=0, button_type="url", callback_data=None):
    buttons = load_json("start_buttons")
    if row == 0:
        if buttons:
            max_row = max(b.get("row", 0) for b in buttons)
            buttons_in_row = sum(1 for b in buttons if b.get("row") == max_row)
            if buttons_in_row >= 2:
                row = max_row + 1
            else:
                row = max_row
        else:
            row = 0

    buttons.append({
        "id": len(buttons) + 1,
        "name": name,
        "link": link,
        "row": row,
        "type": button_type,
        "callback_data": callback_data
    })
    save_json("start_buttons", buttons)

async def update_start_button(btn_id, name=None, link=None, row=None, button_type=None, callback_data=None):
    buttons = load_json("start_buttons")
    for b in buttons:
        if b["id"] == int(btn_id):
            if name:
                b["name"] = name
            if link:
                b["link"] = link
            if row is not None:
                b["row"] = row
            if button_type:
                b["type"] = button_type
            if callback_data:
                b["callback_data"] = callback_data
            break
    save_json("start_buttons", buttons)

async def delete_start_button(btn_id):
    buttons = load_json("start_buttons")
    buttons = [b for b in buttons if b["id"] != int(btn_id)]
    save_json("start_buttons", buttons)

async def get_start_buttons_by_row():
    buttons = await get_start_buttons()
    rows = {}
    for btn in buttons:
        row = btn.get("row", 0)
        if row not in rows:
            rows[row] = []
        rows[row].append(btn)
    return rows

# ==================== HELPER FUNCTIONS ====================
def parse_telegram_format(text, user_name="", user_mention=""):
    if not text:
        return text

    text = text.replace("{mention}", user_mention)
    text = text.replace("{name}", user_name)

    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)

    return text

# ==================== COLOR BUTTON HELPER (Telegram 2026.2) ====================
def color_button(text: str, 
                 callback_data: str = None, 
                 url: str = None,
                 color: str = "secondary"):
    """
    Telegram 2026.2 Background Color Button
    အရောင်များ:
        - "primary"   -> အပြာ
        - "positive"  -> အစိမ်း  
        - "danger"    -> အနီ
        - "secondary" -> မီးခိုး (Default)
    """
    data = {"text": text}
    
    if url:
        data["url"] = url
    if callback_data:
        data["callback_data"] = callback_data
    if color != "secondary":
        data["color"] = color
        
    return InlineKeyboardButton(**data)

# ==================== BATCH WORKER ====================
async def batch_worker():
    global ACTIVE_USERS

    while True:
        async with BATCH_LOCK:
            if ACTIVE_USERS >= BATCH_SIZE:
                await asyncio.sleep(0.5)
                continue

            slots = BATCH_SIZE - ACTIVE_USERS
            users_to_process = []

            for _ in range(slots):
                try:
                    user_id = WAITING_QUEUE.get_nowait()
                    users_to_process.append(user_id)
                    ACTIVE_USERS += 1
                except asyncio.QueueEmpty:
                    break

            for user_id in users_to_process:
                asyncio.create_task(process_user_request(user_id))

        await asyncio.sleep(0.1)

async def process_user_request(user_id: int):
    global ACTIVE_USERS

    try:
        await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Error processing user {user_id}: {e}")
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

async def schedule_auto_delete(chat_type: str, chat_id: int, message_id: int, seconds: int):
    if seconds <= 0:
        return
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        print(f"Failed to delete message: {e}")

async def is_maintenance():
    return await get_setting("maint") == "on"

async def check_force_join(user_id):
    channels = await get_force_channels()
    if not channels:
        return True

    for ch in channels:
        try:
            m = await bot.get_chat_member(ch["chat_id"], user_id)
            if m.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

async def send_force_join(msg: types.Message):
    channels = await get_force_channels()
    if not channels:
        return True

    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(text=ch["title"], url=ch["invite"])
    builder.button(text="✅ Done ✅", callback_data="force_done")
    builder.adjust(1)

    force_text = await get_custom_text("forcemsg")
    formatted_text = parse_telegram_format(
        force_text.get("text") or "⚠️ **BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။**\n\nBOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။",
        msg.from_user.full_name,
        msg.from_user.mention_html()
    )

    force_msg = await msg.answer(
        formatted_text,
        reply_markup=builder.as_markup(),
        protect_content=True
    )
    return False

async def send_searching_overlay(chat_id: int) -> Optional[int]:
    overlay = await get_custom_text("searching")

    try:
        if overlay.get("sticker_id"):
            msg = await bot.send_sticker(chat_id, overlay["sticker_id"], protect_content=True)
        elif overlay.get("animation_id"):
            msg = await bot.send_animation(chat_id, overlay["animation_id"],
                                         caption=overlay.get("text", ""), protect_content=True)
        elif overlay.get("photo_id"):
            msg = await bot.send_photo(chat_id, overlay["photo_id"],
                                     caption=overlay.get("text", ""), protect_content=True)
        else:
            text = overlay.get("text", "🔍 ရှာဖွေနေပါသည်...")
            msg = await bot.send_message(chat_id, text, protect_content=True)
        return msg.message_id
    except Exception as e:
        print(f"Error sending overlay: {e}")
        try:
            msg = await bot.send_message(chat_id, "🔍 ရှာဖွေနေပါသည်...", protect_content=True)
            return msg.message_id
        except:
            return None

async def safe_delete_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

def main_menu(is_owner=False):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Search Movie")],
            [KeyboardButton(text="📋 Movie List")]
        ],
        resize_keyboard=True
    )
    if is_owner:
        kb.keyboard.append([KeyboardButton(text="🛠 Admin Panel")])
        kb.keyboard.append([KeyboardButton(text="📊 Statistics")])
    return kb

# ==================== START COMMAND ====================
@dp.message(Command("start"))
async def start(message: types.Message):
    is_owner = message.from_user.id == OWNER_ID
    user_id = message.from_user.id
    display_name = message.from_user.full_name
    user_mention = message.from_user.mention_html()

    is_new = await add_new_user(user_id, display_name, user_mention)

    if is_new:
        total_users = await get_user_count()

        notification_text = (
            f"👤 <b>New User Notification</b>\n\n"
            f"<b>Total Users:</b> {total_users}\n"
            f"<b>ID:</b> <code>{user_id}</code>\n"
            f"<b>Name:</b> {display_name}\n"
            f"<b>Mention:</b> {user_mention}"
        )
        try:
            await bot.send_message(OWNER_ID, notification_text, protect_content=True)
        except Exception as e:
            print(f"Failed to notify owner: {e}")

    if not await check_force_join(message.from_user.id):
        await send_force_join(message)
        return

    await send_start_welcome(message, is_owner)

    await message.answer(
        "📌 **Main Menu**\n\nအောက်ပါခလုတ်များကိုသုံးပါ:",
        reply_markup=main_menu(is_owner),
        protect_content=True
    )

async def send_start_welcome(message: types.Message, is_owner: bool):
    welcome_data = await get_next_welcome_photo()

    builder = InlineKeyboardBuilder()
    rows = await get_start_buttons_by_row()

    for row_num in sorted(rows.keys()):
        row_buttons = rows[row_num]
        for btn in row_buttons[:2]:
            if btn.get("type") == "popup":
                builder.button(
                    text=btn["name"], 
                    callback_data=btn.get("callback_data", f"popup_{btn['id']}")
                )
            else:
                builder.button(
                    text=btn["name"], 
                    url=btn["link"]
                )
        builder.adjust(2)

    if is_owner:
        builder.button(text="⚙️ Manage Start Buttons", callback_data="manage_start_buttons")
        builder.adjust(1)

    welcome_text = parse_telegram_format(
        welcome_data.get("caption") or welcome_data.get("text", "👋 Welcome!"),
        message.from_user.full_name,
        message.from_user.mention_html()
    )

    if welcome_data and welcome_data.get("photo_id"):
        try:
            await message.answer_photo(
                photo=welcome_data["photo_id"],
                caption=welcome_text,
                reply_markup=builder.as_markup(),
                protect_content=True
            )
        except Exception as e:
            print(f"Error sending welcome photo: {e}")
            await message.answer(
                welcome_text,
                reply_markup=builder.as_markup(),
                protect_content=True
            )
    else:
        await message.answer(
            welcome_text,
            reply_markup=builder.as_markup(),
            protect_content=True
        )

# ==================== FORCE DONE ====================
@dp.callback_query(F.data == "force_done")
async def force_done(callback: CallbackQuery):
    ok = await check_force_join(callback.from_user.id)

    if not ok:
        await callback.answer(
            "❌ Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
            "ကျေးဇူးပြု၍ သတ်မှတ်ထားသော Channel များအားလုံးကို အရင် Join လုပ်ပါ။\n"
            "ပြီးရင် 'Done' ကို နှိပ်ပါ။",
            show_alert=True
        )
        return

    await callback.answer("joinပေးတဲ့အတွက်ကျေးဇူးတင်ပါတယ်!", show_alert=True)
    await callback.message.delete()
    await send_start_welcome(callback.message, callback.from_user.id == OWNER_ID)

# ==================== POPUP HANDLER ====================
@dp.callback_query(F.data.startswith("popup_"))
async def handle_popup_button(callback: CallbackQuery):
    buttons = await get_start_buttons()
    for btn in buttons:
        if btn.get("callback_data") == callback.data:
            await callback.answer(btn.get("link", ""), show_alert=True)
            return
    await callback.answer("Popup text not found", show_alert=True)

# ==================== SEARCH COMMAND ====================
@dp.message(F.text == "🔍 Search Movie")
async def search_movie_prompt(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎬 Movie + Code ကြည့်ရန်", 
        url="https://t.me/seatvmmmovielist"
    )
    await message.answer(
        "🔍 <b>ဇာတ်ကား Code ပို့ပေးပါ</b>",
        reply_markup=builder.as_markup(),
        protect_content=True
    )

# ==================== MOVIE LIST ====================
@dp.message(F.text == "📋 Movie List")
async def movie_list_redirect(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎬 Movie + Code ကြည့်ရန်", 
        url="https://t.me/seatvmmmovielist"
    )
    await message.answer(
        "📌 ရှိတဲ့ Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ",
        reply_markup=builder.as_markup(),
        protect_content=True
    )

# ==================== ADMIN PANEL ====================
@dp.message(F.text == "🛠 Admin Panel")
async def admin_panel(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    
    builder = InlineKeyboardBuilder()
    
    builder.row(
        color_button("➕ Add Movie", callback_data="add_movie", color="positive"),
        color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        width=2
    )
    
    builder.row(
        color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
        color_button("📡 Force Channels", callback_data="force", color="primary"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📥 Backup", callback_data="backup"),
        InlineKeyboardButton(text="📤 Restore", callback_data="restore"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🛑 Maintenance", callback_data="maint"),
        InlineKeyboardButton(text="📺 Ads Manager", callback_data="ads_manager"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⏰ Auto Delete", callback_data="auto_delete"),
        color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📝 Welcome Set", callback_data="edit_welcome"),
        InlineKeyboardButton(text="📢 Force Msg Set", callback_data="edit_forcemsg"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🔍 Searching Set", callback_data="edit_searching"),
        InlineKeyboardButton(text="⚙️ Start Buttons", callback_data="manage_start_buttons"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back"),
        width=1
    )
    
    await message.answer(
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram 2026.2 Color Buttons**\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🟢 အစိမ်း - Positive\n"
        "• 🔴 အနီ - Danger",
        reply_markup=builder.as_markup(),
        protect_content=True
    )

# ==================== STATISTICS ====================
@dp.message(F.text == "📊 Statistics")
async def statistics_panel(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    total_users = await get_user_count()
    daily_active = await get_daily_active_users()
    top_users = await get_top_searches(5)
    total_movies = len(MOVIES_DICT)

    text = "📊 **Bot Statistics**\n\n"
    text += f"👥 Total Users: **{total_users}**\n"
    text += f"🟢 Daily Active: **{daily_active}**\n"
    text += f"🎬 Total Movies: **{total_movies}**\n\n"

    text += "🔝 **Top 5 Searchers:**\n"
    for i, user in enumerate(top_users, 1):
        name = user.get("name", "Unknown")
        count = user.get("search_count", 0)
        text += f"{i}. {name} - {count} searches\n"

    await message.answer(text, protect_content=True)

# ==================== MAIN SEARCH FUNCTION ====================
@dp.message()
async def search(message: types.Message):
    if message.text.startswith("/"):
        return

    if await is_maintenance() and message.from_user.id != OWNER_ID:
        await message.answer("🛠 Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။", protect_content=True)
        return

    if not await check_force_join(message.from_user.id):
        await send_force_join(message)
        return

    if message.from_user.id != OWNER_ID:
        last = await get_user_last(message.from_user.id)
        if last:
            diff = datetime.now() - datetime.fromisoformat(last)
            if diff.total_seconds() < COOLDOWN:
                remain = int(COOLDOWN - diff.total_seconds())
                await message.answer(f"⏳ ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်", protect_content=True)
                return

    code = message.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        await message.answer(f"❌ Code `{code}` မရှိပါ။\n\n🔍 Search Movie နှိပ်ပြီး Code စစ်ပါ။", protect_content=True)
        return

    global ACTIVE_USERS

    async with BATCH_LOCK:
        if ACTIVE_USERS >= BATCH_SIZE:
            await WAITING_QUEUE.put(message.from_user.id)
            position = WAITING_QUEUE.qsize()

            queue_msg = await message.answer(
                f"⏳ **စောင့်ဆိုင်းနေဆဲအသုံးပြုသူများ**\n\n"
                f"• သင့်နေရာ: **{position}**\n"
                f"• လက်ရှိအသုံးပြုနေသူ: **{ACTIVE_USERS}/{BATCH_SIZE}**\n\n"
                f"ကျေးဇူးပြု၍ စောင့်ဆိုင်းပေးပါ။",
                protect_content=True
            )

            await asyncio.sleep(5)
            await safe_delete_message(message.chat.id, queue_msg.message_id)
            return

        ACTIVE_USERS += 1

    try:
        await update_user_search(message.from_user.id)
        USER_PROCESSING_TIME[message.from_user.id] = datetime.now()

        ads = await get_ads()
        if ads:
            idx = await get_next_ad_index()
            if idx is not None and idx < len(ads):
                ad = ads[idx]
                try:
                    ad_sent = await bot.copy_message(
                        chat_id=message.from_user.id,
                        from_chat_id=ad["storage_chat_id"],
                        message_id=ad["message_id"],
                        protect_content=True
                    )
                    asyncio.create_task(schedule_auto_delete("dm", message.from_user.id, ad_sent.message_id, 10))
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"Error sending ad: {e}")

        searching_msg_id = await send_searching_overlay(message.from_user.id)

        owner_button = color_button(
            text="⚜️Owner⚜️",
            url="https://t.me/osamu1123",
            color="primary"
        )
        
        sent = await bot.copy_message(
            chat_id=message.from_user.id,
            from_chat_id=movie["storage_chat_id"],
            message_id=movie["message_id"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[owner_button]]),
            protect_content=True
        )

        if searching_msg_id:
            await safe_delete_message(message.from_user.id, searching_msg_id)

        config = await get_auto_delete_config()
        dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)
        if dm_sec > 0:
            asyncio.create_task(schedule_auto_delete("dm", message.from_user.id, sent.message_id, dm_sec))

    except Exception as e:
        print(f"Error sending movie: {e}")
        await message.answer("❌ Error sending movie. Please try again.", protect_content=True)
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

# ==================== BACK ====================
@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "Menu:",
        reply_markup=main_menu(callback.from_user.id == OWNER_ID),
        protect_content=True
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery):
    await callback.message.delete()
    await send_start_welcome(callback.message, callback.from_user.id == OWNER_ID)

@dp.callback_query(F.data == "back_admin")
async def back_admin(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    
    builder.row(
        color_button("➕ Add Movie", callback_data="add_movie", color="positive"),
        color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        width=2
    )
    
    builder.row(
        color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
        color_button("📡 Force Channels", callback_data="force", color="primary"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📥 Backup", callback_data="backup"),
        InlineKeyboardButton(text="📤 Restore", callback_data="restore"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🛑 Maintenance", callback_data="maint"),
        InlineKeyboardButton(text="📺 Ads Manager", callback_data="ads_manager"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⏰ Auto Delete", callback_data="auto_delete"),
        color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📝 Welcome Set", callback_data="edit_welcome"),
        InlineKeyboardButton(text="📢 Force Msg Set", callback_data="edit_forcemsg"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🔍 Searching Set", callback_data="edit_searching"),
        InlineKeyboardButton(text="⚙️ Start Buttons", callback_data="manage_start_buttons"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back"),
        width=1
    )
    
    await callback.message.edit_text(
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram 2026.2 Color Buttons**\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🟢 အစိမ်း - Positive\n"
        "• 🔴 အနီ - Danger",
        reply_markup=builder.as_markup()
    )

# ==================== AUTO DELETE ====================
@dp.callback_query(F.data == "auto_delete")
async def auto_delete_menu(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)
    dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)

    text = f"🕒 Auto Delete Settings:\n\n"
    text += f"Group Messages: {group_sec} seconds\n"
    text += f"DM Messages: {dm_sec} seconds\n\n"
    text += "Select option to change:"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Group", callback_data="set_group_delete"),
        InlineKeyboardButton(text="💬 DM", callback_data="set_dm_delete"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="❌ Disable All", callback_data="disable_auto_delete"),
        width=1
    )
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back_admin"),
        width=1
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("set_") & F.data.contains("delete"))
async def set_auto_delete_type(callback: CallbackQuery):
    delete_type = "group" if "group" in callback.data else "dm"

    builder = InlineKeyboardBuilder()
    for sec in AUTO_DELETE_OPTIONS:
        builder.button(text=f"{sec}s", callback_data=f"set_time_{delete_type}_{sec}")
    builder.button(text="❌ Disable", callback_data=f"set_time_{delete_type}_0")
    builder.button(text="⬅ Back", callback_data="auto_delete")
    builder.adjust(3)

    await callback.message.edit_text(f"Select auto-delete time for {delete_type.upper()}:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("set_time_"))
async def confirm_auto_delete(callback: CallbackQuery):
    parts = callback.data.split("_")
    delete_type = parts[2]
    seconds = int(parts[3])

    await set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        await callback.answer(f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        await callback.answer(f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    await auto_delete_menu(callback)

@dp.callback_query(F.data == "disable_auto_delete")
async def disable_all_auto_delete(callback: CallbackQuery):
    await set_auto_delete_config("group", 0)
    await set_auto_delete_config("dm", 0)
    await callback.answer("All auto-delete disabled!", show_alert=True)
    await auto_delete_menu(callback)

# ==================== CLEAR ALL DATA ====================
@dp.callback_query(F.data == "clear_all_data")
async def clear_all_data_confirm(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Confirm Clear All", callback_data="confirm_clear_all"),
        width=1
    )
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back_admin"),
        width=1
    )
    await callback.message.edit_text("⚠️ <b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings.", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "confirm_clear_all")
async def process_clear_all_data(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    save_json("movies", [])
    save_json("users", [])
    save_json("ads", [])
    save_json("settings", [])
    save_json("force_channels", [])
    save_json("custom_texts", [])
    save_json("auto_delete", [])
    save_json("start_buttons", [])
    save_json("start_welcome", [])

    await reload_movies_cache()

    builder = InlineKeyboardBuilder()
    builder.row(
        color_button("➕ Add Movie", callback_data="add_movie", color="positive"),
        color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        width=2
    )
    
    builder.row(
        color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
        color_button("📡 Force Channels", callback_data="force", color="primary"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📥 Backup", callback_data="backup"),
        InlineKeyboardButton(text="📤 Restore", callback_data="restore"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🛑 Maintenance", callback_data="maint"),
        InlineKeyboardButton(text="📺 Ads Manager", callback_data="ads_manager"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⏰ Auto Delete", callback_data="auto_delete"),
        color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="📝 Welcome Set", callback_data="edit_welcome"),
        InlineKeyboardButton(text="📢 Force Msg Set", callback_data="edit_forcemsg"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="🔍 Searching Set", callback_data="edit_searching"),
        InlineKeyboardButton(text="⚙️ Start Buttons", callback_data="manage_start_buttons"),
        width=2
    )
    
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back"),
        width=1
    )
    
    await callback.message.edit_text("✅ All data has been cleared!\n\n🛠 **Admin Panel**", reply_markup=builder.as_markup())
    await callback.answer("Data cleared", show_alert=True)

# ==================== FORCE CHANNELS ====================
@dp.callback_query(F.data == "force")
async def force_menu(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    channels = await get_force_channels()
    text = "📡 Force Channels:\n\n"

    if not channels:
        text += "No force channels added yet."
    else:
        for ch in channels:
            text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    builder = InlineKeyboardBuilder()

    for ch in channels:
        builder.row(
            InlineKeyboardButton(text=f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}"),
            width=1
        )

    builder.row(
        InlineKeyboardButton(text="➕ Add Channel", callback_data="add_force"),
        width=1
    )
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back_admin"),
        width=1
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_force")
async def add_force_start(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    await callback.message.answer(
        "📌 Channel link ပေးပါ (public/private OK)\n\n"
        "Example:\nhttps://t.me/yourchannel\nhttps://t.me/+AbCdEfGhIjKlMn==",
        protect_content=True
    )
    await callback.answer()

@dp.message(lambda m: m.text and m.text.startswith("https://t.me/"))
async def catch_force_link(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    link = message.text.strip()
    chat_id = None
    chat = None

    if "+" not in link:
        username = link.split("t.me/")[1].replace("@", "").strip("/")
        try:
            chat = await bot.get_chat(f"@{username}")
            chat_id = chat.id
        except:
            await message.answer("❌ Public channel not found", protect_content=True)
            return
    else:
        try:
            chat = await bot.get_chat(link)
            chat_id = chat.id
        except:
            await message.answer("❌ Private channel invalid", protect_content=True)
            return

    try:
        bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer("❌ Bot must be admin in channel", protect_content=True)
            return
    except:
        await message.answer("❌ Cannot check admin status", protect_content=True)
        return

    try:
        invite = await bot.export_chat_invite_link(chat_id)
    except:
        if chat.username:
            invite = f"https://t.me/{chat.username}"
        else:
            await message.answer("❌ Cannot create invite link", protect_content=True)
            return

    await add_force_channel(chat_id, chat.title, invite)

    await message.answer(f"✅ Added: {chat.title}", protect_content=True)

@dp.callback_query(F.data.startswith("delch_"))
async def delete_force_channel_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    cid = callback.data.split("_")[1]
    await delete_force_channel(cid)
    await callback.answer("✅ Deleted", show_alert=True)
    await force_menu(callback)

# ==================== EDIT TEXT ====================
class EditText(StatesGroup):
    waiting = State()

@dp.callback_query(F.data.startswith("edit_"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return

    key = callback.data.replace("edit_", "")
    await state.set_state(EditText.waiting)
    await state.update_data(key=key)

    formatting_guide = (
        "\n\n📝 Formatting Guide:\n"
        "• **bold text** - စာလုံးမဲ\n"
        "• *italic text* - စာလုံးစောင်း\n"
        "• __underline__ - မျဉ်းသား\n"
        "• ~~strikethrough~~ - ကြားမျဉ်း\n"
        "• `code` - Code\n"
        "• {mention} - User mention\n"
        "• {name} - User name\n"
    )

    if key == "searching":
        await callback.message.answer(
            "🔍 Searching overlay အတွက် content ပို့ပေးပါ:\n\n"
            "• Text message ပို့ရင် - စာသားအဖြစ်သိမ်းမယ်\n"
            "• Photo ပို့ရင် - Photo နဲ့ caption သိမ်းမယ်\n"
            "• Sticker ပို့ရင် - Sticker အဖြစ်သိမ်းမယ်\n"
            "• GIF/Animation ပို့ရင် - GIF အဖြစ်သိမ်းမယ်\n" +
            formatting_guide +
            "\nမပို့ချင်ရင် /cancel ရိုက်ပါ။",
            protect_content=True
        )
    else:
        await callback.message.answer(
            f"'{key}' အတွက် စာအသစ်ပို့ပေးပါ (Photo ပါရင် Photo နဲ့အတူ Caption ထည့်ပေးပါ)" +
            formatting_guide,
            protect_content=True
        )

    await callback.answer()

@dp.message(EditText.waiting)
async def edit_text_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data['key']

    if message.content_type == 'text' and message.text == '/cancel':
        await message.answer("❌ Cancelled", protect_content=True)
        await state.clear()
        return

    if message.content_type == 'text':
        await set_custom_text(key, text=message.text)
        await message.answer(f"✅ {key} text updated successfully", protect_content=True)

    elif message.content_type == 'photo':
        photo_id = message.photo[-1].file_id
        caption = message.caption or ""
        await set_custom_text(key, text=caption, photo_id=photo_id)
        await message.answer(f"✅ {key} photo updated successfully", protect_content=True)

    elif message.content_type == 'sticker':
        sticker_id = message.sticker.file_id
        await set_custom_text(key, sticker_id=sticker_id)
        await message.answer(f"✅ {key} sticker updated successfully", protect_content=True)

    elif message.content_type == 'animation':
        animation_id = message.animation.file_id
        caption = message.caption or ""
        await set_custom_text(key, text=caption, animation_id=animation_id)
        await message.answer(f"✅ {key} GIF updated successfully", protect_content=True)

    else:
        await message.answer("❌ Unsupported content type", protect_content=True)

    await state.clear()

# ==================== ADD MOVIE ====================
class AddMovie(StatesGroup):
    name = State()
    code = State()
    msgid = State()
    chatid = State()

@dp.callback_query(F.data == "add_movie")
async def add_movie_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(AddMovie.name)
    await callback.message.answer("🎬 ဇာတ်ကားနာမည်?", protect_content=True)
    await callback.answer()

@dp.message(AddMovie.name)
async def add_movie_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddMovie.code)
    await message.answer("🔢 ဇာတ်ကား Code (ဥပမာ: 101010, MM101, etc):", protect_content=True)

@dp.message(AddMovie.code)
async def add_movie_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    if not code:
        return await message.answer("❌ Code ထည့်ပါ။", protect_content=True)
    await state.update_data(code=code)
    await state.set_state(AddMovie.msgid)
    await message.answer("📨 Message ID?", protect_content=True)

@dp.message(AddMovie.msgid)
async def add_movie_msgid(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ ဂဏန်းပဲထည့်ပါ။", protect_content=True)
    await state.update_data(msgid=int(message.text))
    await state.set_state(AddMovie.chatid)
    await message.answer("💬 Storage Group Chat ID?", protect_content=True)

@dp.message(AddMovie.chatid)
async def add_movie_chatid(message: types.Message, state: FSMContext):
    try:
        chatid = int(message.text)
    except:
        return await message.answer("❌ Chat ID မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    await add_movie_record(data["name"], data["code"], data["msgid"], chatid)

    await message.answer(f"✅ ဇာတ်ကားထည့်ပြီးပါပြီ!\n\nနာမည်: {data['name']}\nCode: {data['code']}", protect_content=True)
    await state.clear()

# ==================== DELETE MOVIE ====================
class DelMovie(StatesGroup):
    code = State()

@dp.callback_query(F.data == "del_movie")
async def del_movie_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(DelMovie.code)
    await callback.message.answer("🗑 ဖျက်မည့် ဇာတ်ကား Code ကိုထည့်ပါ:", protect_content=True)
    await callback.answer()

@dp.message(DelMovie.code)
async def del_movie_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    await delete_movie(code)
    await message.answer(f"✅ Code `{code}` ဖျက်ပြီးပါပြီ။", protect_content=True)
    await state.clear()

# ==================== BROADCAST ====================
class Broadcast(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()
    confirm = State()

@dp.callback_query(F.data == "broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(Broadcast.waiting_content)
    await callback.message.answer(
        "📢 Broadcast စာသား/ပုံ ပို့ပါ။\n\n"
        "📝 Formatting supported:\n"
        "• **bold**, *italic*, __underline__\n"
        "• {mention}, {name} - placeholders\n\n"
        "Photo/Video/GIF ပါ ပို့လို့ရပါတယ်။",
        protect_content=True
    )
    await callback.answer()

@dp.message(Broadcast.waiting_content)
async def broadcast_content(message: types.Message, state: FSMContext):
    content_type = message.content_type

    if content_type == 'text':
        await state.update_data(text=message.text, content_type="text")
    elif content_type == 'photo':
        photo_id = message.photo[-1].file_id
        caption = message.caption or ""
        await state.update_data(photo_id=photo_id, caption=caption, content_type="photo")
    elif content_type == 'video':
        video_id = message.video.file_id
        caption = message.caption or ""
        await state.update_data(video_id=video_id, caption=caption, content_type="video")
    elif content_type == 'animation':
        animation_id = message.animation.file_id
        caption = message.caption or ""
        await state.update_data(animation_id=animation_id, caption=caption, content_type="animation")
    else:
        return await message.answer("❌ Unsupported content type", protect_content=True)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ ပြန်ဖြစ်ရင်ပဲပို့မယ်", callback_data="bc_no_buttons"),
        InlineKeyboardButton(text="➕ Buttons ထည့်မယ်", callback_data="bc_add_buttons"),
        width=2
    )

    await message.answer("Buttons ထည့်မလား?", reply_markup=builder.as_markup(), protect_content=True)

@dp.callback_query(Broadcast.waiting_content, F.data == "bc_no_buttons")
async def broadcast_no_buttons(callback: CallbackQuery, state: FSMContext):
    await state.update_data(buttons=[])
    await state.set_state(Broadcast.confirm)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Confirm & Send", callback_data="bc_confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="bc_cancel"),
        width=2
    )
    
    await callback.message.answer("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=builder.as_markup(), protect_content=True)
    await callback.answer()

@dp.callback_query(Broadcast.waiting_content, F.data == "bc_add_buttons")
async def broadcast_add_buttons_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.waiting_buttons)
    await callback.message.answer(
        "📝 Buttons ထည့်ရန်:\n\n"
        "Format: Button Name | URL\n"
        "Example:\n"
        "Channel | https://t.me/yourchannel\n"
        "Group | https://t.me/yourgroup\n\n"
        "တစ်ကြောင်းကို button တစ်ခု၊ ပြီးရင် ပို့ပါ။\n"
        "ပြီးသွားရင် /done ရိုက်ပါ။",
        protect_content=True
    )
    await callback.answer()

@dp.message(Broadcast.waiting_buttons)
async def broadcast_buttons_collect(message: types.Message, state: FSMContext):
    if message.text == "/done":
        data = await state.get_data()
        if not data.get("buttons"):
            await state.update_data(buttons=[])
        await state.set_state(Broadcast.confirm)
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Confirm & Send", callback_data="bc_confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bc_cancel"),
            width=2
        )
        
        await message.answer("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=builder.as_markup(), protect_content=True)
        return

    if "|" not in message.text:
        return await message.answer("❌ Format မမှန်ပါ။ Button Name | URL အဖြစ်ထည့်ပါ။", protect_content=True)

    parts = message.text.split("|")
    if len(parts) != 2:
        return await message.answer("❌ Format မမှန်ပါ။", protect_content=True)

    name = parts[0].strip()
    url = parts[1].strip()

    if not url.startswith(("http://", "https://")):
        return await message.answer("❌ URL မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    buttons = data.get("buttons", [])
    buttons.append({"name": name, "url": url})
    await state.update_data(buttons=buttons)

    await message.answer(f"✅ Button '{name}' ထည့်ပြီး။\nထပ်ထည့်မယ်ဆိုရင် ဆက်ပို့ပါ။\nပြီးရင် /done ရိုက်ပါ။", protect_content=True)

@dp.callback_query(Broadcast.confirm, F.data == "bc_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    users = await get_users()

    buttons = data.get("buttons", [])
    kb = None
    if buttons:
        builder = InlineKeyboardBuilder()
        for btn in buttons:
            builder.button(text=btn["name"], url=btn["url"])
        builder.adjust(1)
        kb = builder.as_markup()

    sent = 0
    failed = 0

    status_msg = await callback.message.answer(f"📢 Broadcasting... 0/{len(users)}", protect_content=True)

    for i, u in enumerate(users):
        try:
            if data["content_type"] == "text":
                await bot.send_message(u["user_id"], data["text"], reply_markup=kb, protect_content=True)
            elif data["content_type"] == "photo":
                await bot.send_photo(u["user_id"], data["photo_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True)
            elif data["content_type"] == "video":
                await bot.send_video(u["user_id"], data["video_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True)
            elif data["content_type"] == "animation":
                await bot.send_animation(u["user_id"], data["animation_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True)
            sent += 1
        except Exception as e:
            print(f"Failed to send to {u['user_id']}: {e}")
            failed += 1

        if (i + 1) % 10 == 0:
            try:
                await status_msg.edit_text(f"📢 Broadcasting... {i+1}/{len(users)}")
            except:
                pass

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n✅ Sent: {sent}\n❌ Failed: {failed}")
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "bc_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Broadcast cancelled", protect_content=True)
    await callback.answer()

# ==================== ADS MANAGER ====================
class AddAd(StatesGroup):
    msgid = State()
    chatid = State()

@dp.callback_query(F.data == "ads_manager")
async def ads_manager_menu(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    ads = await get_ads()
    text = "📺 Ads Manager:\n\n"
    if not ads:
        text += "No ads added yet."
    else:
        for a in ads:
            text += f"ID: {a['id']} | MsgID: {a['message_id']} | ChatID: {a['storage_chat_id']}\n"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Add Ad", callback_data="add_ad_start"),
        width=1
    )
    for a in ads:
        builder.row(
            InlineKeyboardButton(text=f"🗑 Delete Ad {a['id']}", callback_data=f"delad_{a['id']}"),
            width=1
        )
    builder.row(
        InlineKeyboardButton(text="⬅ Back", callback_data="back_admin"),
        width=1
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_ad_start")
async def add_ad_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(AddAd.msgid)
    await callback.message.answer("Enter Ad Message ID:", protect_content=True)
    await callback.answer()

@dp.message(AddAd.msgid)
async def add_ad_msgid(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Please enter a numeric Message ID.", protect_content=True)
    await state.update_data(msgid=int(message.text))
    await state.set_state(AddAd.chatid)
    await message.answer("Enter Storage Group Chat ID for this Ad:", protect_content=True)

@dp.message(AddAd.chatid)
async def add_ad_chatid(message: types.Message, state: FSMContext):
    try:
        chatid = int(message.text)
    except:
        return await message.answer("Invalid Chat ID.", protect_content=True)

    data = await state.get_data()
    await add_ad(data["msgid"], chatid)
    await message.answer("✅ Ad added successfully!", protect_content=True)
    await state.clear()

@dp.callback_query(F.data.startswith("delad_"))
async def delete_ad_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    aid = callback.data.split("_")[1]
    await delete_ad(aid)
    await callback.answer("✅ Ad deleted", show_alert=True)
    await ads_manager_menu(callback)

# ==================== BACKUP ====================
@dp.callback_query(F.data == "backup")
async def backup_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    data = {
        "movies": await get_movies(),
        "users": await get_users(),
        "settings": load_json("settings"),
        "force_channels": await get_force_channels(),
        "auto_delete": await get_auto_delete_config(),
        "custom_texts": load_json("custom_texts"),
        "start_buttons": await get_start_buttons(),
        "start_welcome": await get_start_welcome(),
        "ads": await get_ads()
    }

    with open("backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    await bot.send_document(
        OWNER_ID,
        FSInputFile("backup.json"),
        caption="📥 JSON Backup File",
        protect_content=True
    )

    await callback.answer("Backup sent!", show_alert=True)

# ==================== RESTORE ====================
@dp.callback_query(F.data == "restore")
async def restore_request(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    await callback.message.answer("📤 Upload backup.json file", protect_content=True)
    await callback.answer()

@dp.message(F.document)
async def restore_process(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    try:
        file = await bot.download_file(message.document.file_id)
        data = json.loads(file.read())

        if data.get("movies"):
            save_json("movies", data["movies"])
        if data.get("users"):
            save_json("users", data["users"])
        if data.get("settings"):
            save_json("settings", data["settings"])
        if data.get("force_channels"):
            save_json("force_channels", data["force_channels"])
        if data.get("auto_delete"):
            save_json("auto_delete", data["auto_delete"])
        if data.get("custom_texts"):
            save_json("custom_texts", data["custom_texts"])
        if data.get("start_buttons"):
            save_json("start_buttons", data["start_buttons"])
        if data.get("start_welcome"):
            save_json("start_welcome", data["start_welcome"])
        if data.get("ads"):
            save_json("ads", data["ads"])

        await reload_movies_cache()
        await message.answer("✅ Restore Completed from JSON backup!", protect_content=True)
    except Exception as e:
        await message.answer(f"❌ Restore Failed: {str(e)}", protect_content=True)

# ==================== MAINTENANCE ====================
@dp.callback_query(F.data == "maint")
async def maintenance_toggle(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    current = await is_maintenance()
    new = "off" if current else "on"
    await set_setting("maint", new)
    await callback.answer(f"Maintenance: {new.upper()}", show_alert=True)

# ==================== START BUTTON MANAGEMENT (Continued) ====================
class StartButtonManagement(StatesGroup):
    waiting_for_name = State()
    waiting_for_link = State()
    waiting_for_type = State()
    waiting_for_popup_text = State()

@dp.callback_query(F.data == "manage_start_buttons")
async def manage_start_buttons(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
    text = "⚙️ **Start Buttons Management**\n\n"

    if not buttons:
        text += "Buttons မရှိသေးပါ။\n"
    else:
        rows = await get_start_buttons_by_row()
        for row_num in sorted(rows.keys()):
            text += f"\n🔹 Row {row_num + 1}:\n"
            for btn in rows[row_num]:
                btn_type = btn.get("type", "url")
                text += f"   • ID: {btn['id']} | {btn['name']} ({btn_type})\n"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Add Button", callback_data="add_start_button"),
        InlineKeyboardButton(text="🗑 Delete Button", callback_data="delete_start_button"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🖼 Manage Welcome", callback_data="manage_start_welcome"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_start"),
        width=2
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_start_button")
async def add_start_button_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(StartButtonManagement.waiting_for_name)
    await callback.message.answer("🔹 Button နာမည်ထည့်ပါ:", protect_content=True)
    await callback.answer()

@dp.message(StartButtonManagement.waiting_for_name)
async def add_start_button_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(StartButtonManagement.waiting_for_type)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔗 URL Button", callback_data="btn_type_url"),
        InlineKeyboardButton(text="📢 Popup Button", callback_data="btn_type_popup"),
        width=2
    )
    await message.answer("Button အမျိုးအစားရွေးပါ:", reply_markup=builder.as_markup(), protect_content=True)

@dp.callback_query(StartButtonManagement.waiting_for_type, F.data.startswith("btn_type_"))
async def add_start_button_type(callback: CallbackQuery, state: FSMContext):
    btn_type = callback.data.split("_")[2]
    await state.update_data(button_type=btn_type)

    if btn_type == "url":
        await state.set_state(StartButtonManagement.waiting_for_link)
        await callback.message.answer("🔗 Button Link ထည့်ပါ (https://t.me/... or https://...):", protect_content=True)
    else:
        await state.set_state(StartButtonManagement.waiting_for_popup_text)
        await callback.message.answer("📝 Popup စာသားထည့်ပါ:", protect_content=True)
    await callback.answer()

@dp.message(StartButtonManagement.waiting_for_link)
async def add_start_button_link(message: types.Message, state: FSMContext):
    if not message.text.startswith(('http://', 'https://')):
        return await message.answer("❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။", protect_content=True)

    data = await state.get_data()
    await add_start_button(data['name'], message.text, button_type="url")
    await message.answer(f"✅ Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    await state.clear()

@dp.message(StartButtonManagement.waiting_for_popup_text)
async def add_start_button_popup(message: types.Message, state: FSMContext):
    data = await state.get_data()
    callback_data = f"popup_{message.text[:20]}"
    await add_start_button(data['name'], message.text, button_type="popup", callback_data=callback_data)
    await message.answer(f"✅ Popup Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    await state.clear()

@dp.callback_query(F.data == "delete_start_button")
async def delete_start_button_list(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
    if not buttons:
        await callback.answer("❌ Button မရှိပါ။", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for btn in buttons:
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 {btn['name']} (Row {btn.get('row', 0)+1})",
                callback_data=f"delstartbtn_{btn['id']}"
            ),
            width=1
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_buttons"),
        width=1
    )

    await callback.message.edit_text("ဖျက်မည့် Button ကိုရွေးပါ:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delstartbtn_"))
async def delete_start_button_confirm(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    btn_id = callback.data.split("_")[1]
    await delete_start_button(btn_id)
    await callback.answer("✅ Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    await manage_start_buttons(callback)

# ==================== WELCOME MANAGEMENT ====================
class StartWelcomeManagement(StatesGroup):
    waiting_for_photo = State()

@dp.callback_query(F.data == "manage_start_welcome")
async def manage_start_welcome(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
    text = f"🖼 **Start Welcome Management**\n\n"
    text += f"📸 စုစုပေါင်းပုံ: {len(welcome_list)} ပုံ\n\n"

    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            text += f"{i+1}. 🖼 Photo - {w.get('caption', 'No caption')[:30]}\n"
        else:
            text += f"{i+1}. 📝 Text - {w.get('text', '')[:30]}\n"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Add Photo", callback_data="add_welcome_photo"),
        InlineKeyboardButton(text="➕ Add Text", callback_data="add_welcome_text"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Delete", callback_data="delete_welcome_item"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_buttons"),
        width=2
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_welcome_photo")
async def add_welcome_photo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(StartWelcomeManagement.waiting_for_photo)
    await callback.message.answer(
        "🖼 Welcome Photo ထည့်ရန် Photo ပို့ပါ။\n"
        "Caption ပါထည့်ချင်ရင် Photo နဲ့အတူ Caption ရေးပို့ပါ။\n\n"
        "📝 Formatting:\n"
        "• **bold text** - စာလုံးမဲအတွက်\n"
        "• *italic text* - စာလုံးစောင်းအတွက်\n"
        "• __underline__ - မျဉ်းသားအတွက်\n"
        "• ~~strikethrough~~ - ကြားမျဉ်းအတွက်\n"
        "• `code` - Code အတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await callback.answer()

@dp.message(StartWelcomeManagement.waiting_for_photo, F.photo)
async def add_welcome_photo_done(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    caption = message.caption or ""
    await add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
    count = await get_start_welcome_count()
    await message.answer(f"✅ Welcome Photo ထည့်ပြီးပါပြီ။\n📸 စုစုပေါင်းပုံ: {count} ပုံ", protect_content=True)
    await state.clear()

@dp.callback_query(F.data == "add_welcome_text")
async def add_welcome_text_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(StartWelcomeManagement.waiting_for_photo)
    await callback.message.answer(
        "📝 Welcome Text ထည့်ရန် စာသားပို့ပါ။\n\n"
        "📝 Formatting:\n"
        "• **bold text** - စာလုံးမဲအတွက်\n"
        "• *italic text* - စာလုံးစောင်းအတွက်\n"
        "• __underline__ - မျဉ်းသားအတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await callback.answer()

@dp.message(StartWelcomeManagement.waiting_for_photo, F.text)
async def add_welcome_text_done(message: types.Message, state: FSMContext):
    if message.text == '/cancel':
        await message.answer("❌ Cancelled", protect_content=True)
        await state.clear()
        return

    await add_start_welcome(text=message.text)
    count = await get_start_welcome_count()
    await message.answer(f"✅ Welcome Text ထည့်ပြီးပါပြီ။\n📝 စုစုပေါင်း: {count} ခု", protect_content=True)
    await state.clear()

@dp.callback_query(F.data == "delete_welcome_item")
async def delete_welcome_item_list(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
    if not welcome_list:
        await callback.answer("❌ ဖျက်စရာမရှိပါ။", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            builder.row(
                InlineKeyboardButton(
                    text=f"🗑 {i+1}. 🖼 Photo - {w.get('caption', 'No caption')[:20]}",
                    callback_data=f"delwelcome_{i}"
                ),
                width=1
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"🗑 {i+1}. 📝 Text - {w.get('text', '')[:20]}",
                    callback_data=f"delwelcome_{i}"
                ),
                width=1
            )
    builder.row(
        InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_welcome"),
        width=1
    )

    await callback.message.edit_text("ဖျက်မည့် Welcome Item ကိုရွေးပါ:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delwelcome_"))
async def delete_welcome_item_confirm(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    index = int(callback.data.split("_")[1])
    if await delete_start_welcome(index):
        await callback.answer("✅ ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        await callback.answer("❌ ဖျက်လို့မရပါ။", show_alert=True)

    await manage_start_welcome(callback)

# ==================== OS COMMAND ====================
@dp.message(Command("os"))
async def os_command(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("This command can only be used in groups!", protect_content=True)
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = await message.reply(
        "**owner-@osamu1123**\n\n"
        "• Bot Status: ✅ Online\n"
        "• Queue System: 🟢 Active (Batch: 30)\n"
        "• Auto-Delete: " + ("✅ " + str(group_sec) + "s" if group_sec > 0 else "❌ Disabled") + "\n"
        "• Version: 4.0 (JSON Storage)\n\n"
        "Use /os name command.",
        protect_content=True
    )

    if group_sec > 0:
        asyncio.create_task(schedule_auto_delete("group", message.chat.id, response.message_id, group_sec))
        asyncio.create_task(schedule_auto_delete("group", message.chat.id, message.message_id, group_sec))

# ==================== TEST COLOR BUTTONS ====================
@dp.message(Command("testcolor"))
async def test_color_buttons(message: types.Message):
    """Test Telegram 2026.2 Color Buttons"""
    
    builder = InlineKeyboardBuilder()
    
    builder.button(
        text="🔵 အပြာရောင် Button",
        callback_data="test_blue",
        color="primary"
    )
    
    builder.button(
        text="🟢 အစိမ်းရောင် Button",
        callback_data="test_green",
        color="positive"
    )
    
    builder.button(
        text="🔴 အနီရောင် Button",
        callback_data="test_red",
        color="danger"
    )
    
    builder.button(
        text="⚪ မီးခိုးရောင် Button",
        callback_data="test_gray"
    )
    
    builder.adjust(1)
    
    await message.answer(
        "🎨 **Telegram 2026.2 Color Button Test**\n\n"
        "အောက်က Button တွေမှာ အရောင်တွေပြရင် ✅ အလုပ်လုပ်တယ်\n"
        "အားလုံး မီးခိုးရောင်ပဲပြရင် ❌ အလုပ်မလုပ်ဘူး\n\n"
        "**သင့် Telegram Version စစ်ရန်**\n"
        "Settings → About → Version မှာကြည့်ပါ",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("test_"))
async def handle_test_buttons(callback: CallbackQuery):
    color_names = {
        "test_blue": "အပြာ (Primary)",
        "test_green": "အစိမ်း (Positive)",
        "test_red": "အနီ (Danger)",
        "test_gray": "မီးခိုး (Secondary)"
    }
    
    color_name = color_names.get(callback.data, "Unknown")
    await callback.answer(f"✅ {color_name} Button ကိုနှိပ်လိုက်ပါတယ်", show_alert=True)

# ==================== GROUP MESSAGE HANDLER ====================
@dp.message(F.chat.type.in_(["group", "supergroup"]))
async def group_message_handler(message: types.Message):
    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not message.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete("group", message.chat.id, message.message_id, group_sec))

# ==================== ON STARTUP ====================
async def on_startup():
    for file in ["movies", "users", "ads", "settings", "force_channels", 
                 "custom_texts", "auto_delete", "start_buttons", "start_welcome"]:
        if not os.path.exists(f"{DATA_DIR}/{file}.json"):
            save_json(file, [])
    
    await load_movies_cache()
    asyncio.create_task(batch_worker())
    print("✅ Bot started with JSON Storage")
    print(f"✅ Movies in cache: {len(MOVIES_DICT)}")
    print(f"✅ Batch size: {BATCH_SIZE}")
    print("✅ Telegram 2026.2 Color Buttons Enabled (Primary, Positive, Danger)")

    welcome_count = await get_start_welcome_count()
    print(f"✅ Welcome photos: {welcome_count}")

# ==================== MAIN ====================
async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
