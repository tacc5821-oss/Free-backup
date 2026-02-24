import os
import json
import asyncio
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, Dict, List

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputFile, ContentType
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

COOLDOWN = 90  # seconds
BATCH_SIZE = 30  # တစ်ပြိုင်နက် 30 ယောက်ပဲလုပ်
AUTO_DELETE_OPTIONS = [5, 10, 30]  # seconds

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ---------------- BATCH QUEUE SYSTEM ----------------
ACTIVE_USERS = 0
WAITING_QUEUE = asyncio.Queue()
BATCH_LOCK = asyncio.Lock()
USER_PROCESSING_TIME = {}  # user_id -> timestamp

# ---------------- MOVIES DICTIONARY CACHE ----------------
MOVIES_DICT = {}

# ---------------- JSON STORAGE ----------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

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

# ---------------- START WELCOME PHOTO ROTATION ----------------
def get_start_welcome():
    data = load_json("start_welcome")
    if not data:
        # Default welcome
        return [{
            "text": "👋 **Welcome to Movie Bot!**\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။",
            "photo_id": None,
            "caption": ""
        }]
    return data

def get_next_welcome_photo():
    """Welcome Photo ကို Rotation နဲ့ပြမယ် - start တစ်ခါနှိပ်တိုင်း ပုံပြောင်းပြမယ်"""
    data = get_start_welcome()
    if not data:
        return None

    current = get_setting("welcome_photo_index")
    if current is None:
        current = 0
    else:
        try:
            current = int(current)
        except:
            current = 0

    # နောက်တစ်ပုံကို ရွေ့မယ်
    next_idx = (current + 1) % len(data)
    set_setting("welcome_photo_index", next_idx)

    # လက်ရှိပုံကိုပြမယ် (ပြီးမှ နောက်တစ်ပုံကိုရွှေ့)
    return data[current % len(data)]

def add_start_welcome(text=None, photo_id=None, caption=None):
    """Welcome Photo အသစ်ထည့်မယ် (ပုံအများကြီးထည့်လို့ရ)"""
    data = get_start_welcome()
    data.append({
        "text": text or "👋 **Welcome to Movie Bot!**",
        "photo_id": photo_id,
        "caption": caption or ""
    })
    save_json("start_welcome", data)

def delete_start_welcome(index):
    """Welcome Photo တစ်ခုဖျက်မယ်"""
    data = get_start_welcome()
    if 0 <= index < len(data):
        data.pop(index)
        save_json("start_welcome", data)
        return True
    return False

def get_start_welcome_count():
    """Welcome Photo အရေအတွက်ပြန်"""
    return len(get_start_welcome())

# ---------------- START BUTTONS CONFIG ----------------
def get_start_buttons():
    return load_json("start_buttons")

def add_start_button(name, link, row=0):
    data = get_start_buttons()
    # row number ကို 2 column အတွက် အလိုအလျောက်သတ်မှတ်
    if row == 0:
        # နောက်ဆုံး row ကိုရှာ
        if data:
            max_row = max(b.get("row", 0) for b in data)
            # တစ်row ကို 2 button စီ
            buttons_in_row = sum(1 for b in data if b.get("row") == max_row)
            if buttons_in_row >= 2:
                row = max_row + 1
            else:
                row = max_row
        else:
            row = 0

    data.append({
        "id": len(data) + 1,
        "name": name,
        "link": link,
        "row": row
    })
    save_json("start_buttons", data)

def update_start_button(btn_id, name=None, link=None, row=None):
    data = get_start_buttons()
    for b in data:
        if b["id"] == int(btn_id):
            if name:
                b["name"] = name
            if link:
                b["link"] = link
            if row is not None:
                b["row"] = row
            break
    save_json("start_buttons", data)

def delete_start_button(btn_id):
    data = get_start_buttons()
    new = [b for b in data if b["id"] != int(btn_id)]
    save_json("start_buttons", new)

def get_start_buttons_by_row():
    """Row အလိုက် buttons ကိုပြန် (2 columns)"""
    buttons = get_start_buttons()
    rows = {}
    for btn in buttons:
        row = btn.get("row", 0)
        if row not in rows:
            rows[row] = []
        rows[row].append(btn)
    return rows

# ---------------- MOVIES ----------------
def get_movies():
    return load_json("movies")

def load_movies_cache():
    """Bot စကတည်း movies အားလုံးကို dictionary ဆောက်မယ်"""
    global MOVIES_DICT
    movies = get_movies()
    MOVIES_DICT = {}
    for m in movies:
        if m.get("movie_code"):
            MOVIES_DICT[m["movie_code"].upper()] = m
    print(f"✅ Loaded {len(MOVIES_DICT)} movies to cache")

def reload_movies_cache():
    """Movie ထည့်/ဖျက်ပြီးတိုင်း cache ပြန်တင်"""
    load_movies_cache()

def find_movie_by_code(code: str) -> Optional[dict]:
    """O(1) search - dictionary ကနေချက်ချင်းထုတ်"""
    return MOVIES_DICT.get(code.upper())

def add_movie_record(name, code, msgid, chatid):
    """Movie ထည့်တဲ့အခါ code ပါထည့်"""
    data = get_movies()
    data.append({
        "movie_name": name,
        "movie_code": code.upper(),
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("movies", data)
    reload_movies_cache()  # cache ပြန်တင်

def delete_movie(code):
    """Code နဲ့ဖျက်"""
    data = get_movies()
    new = [m for m in data if m.get("movie_code", "").upper() != code.upper()]
    save_json("movies", new)
    reload_movies_cache()  # cache ပြန်တင်

# ---------------- ADS ----------------
def get_ads():
    return load_json("ads")

def add_ad(msgid, chatid):
    data = get_ads()
    data.append({
        "id": len(data) + 1,
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("ads", data)

def delete_ad(aid):
    data = get_ads()
    new = [a for a in data if a["id"] != int(aid)]
    save_json("ads", new)

# ---------------- USERS ----------------
def get_users():
    return load_json("users")

def add_new_user(uid, name, mention):
    """User အသစ်ထည့်ပြီး Owner ကို Notify ပို့မယ်"""
    users = get_users()

    # User ရှိပြီးသားလားစစ်
    for u in users:
        if u["user_id"] == uid:
            return False

    # User အသစ်ထည့်
    users.append({
        "user_id": uid, 
        "last_search": None,
        "join_date": datetime.now().isoformat(),
        "name": name,
        "mention": mention
    })
    save_json("users", users)
    return True

def get_user_count():
    """Total User Count ပြန်"""
    return len(get_users())

def update_user_search(uid):
    users = get_users()
    found = False
    for u in users:
        if u["user_id"] == uid:
            u["last_search"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        users.append({
            "user_id": uid, 
            "last_search": datetime.now().isoformat(),
            "join_date": datetime.now().isoformat()
        })
    save_json("users", users)

def get_user_last(uid):
    users = get_users()
    for u in users:
        if u["user_id"] == uid:
            return u["last_search"]
    return None

# ---------------- SETTINGS ----------------
def get_setting(key):
    data = load_json("settings")
    for s in data:
        if s["key"] == key:
            return s["value"]
    return None

def set_setting(key, value):
    data = load_json("settings")
    found = False
    for s in data:
        if s["key"] == key:
            s["value"] = value
            found = True
            break
    if not found:
        data.append({"key": key, "value": value})
    save_json("settings", data)

# ---------------- ADS ROTATION ----------------
def get_next_ad_index():
    current = get_setting("last_ad_index")
    if current is None:
        current = 0
    else:
        try:
            current = int(current)
        except:
            current = 0

    ads = get_ads()
    if not ads:
        return None

    next_idx = (current + 1) % len(ads)
    set_setting("last_ad_index", next_idx)
    return current % len(ads)

# ---------------- AUTO DELETE CONFIG ----------------
def get_auto_delete_config():
    data = load_json("auto_delete")
    if not data:
        data = [
            {"type": "group", "seconds": 0},
            {"type": "dm", "seconds": 0}
        ]
        save_json("auto_delete", data)
    return data

def set_auto_delete_config(config_type, value):
    data = get_auto_delete_config()
    found = False
    for c in data:
        if c["type"] == config_type:
            c["seconds"] = value
            found = True
            break
    if not found:
        data.append({"type": config_type, "seconds": value})
    save_json("auto_delete", data)

# ---------------- FORCE CHANNELS ----------------
def get_force_channels():
    return load_json("force_channels")

def add_force_channel(chat_id, title, invite):
    data = get_force_channels()
    data.append({
        "id": len(data) + 1,
        "chat_id": chat_id,
        "title": title,
        "invite": invite
    })
    save_json("force_channels", data)

def delete_force_channel(cid):
    data = get_force_channels()
    new = [c for c in data if c["id"] != int(cid)]
    save_json("force_channels", new)

# ---------------- CUSTOM TEXTS ----------------
def get_custom_text(key):
    data = load_json("custom_texts")
    for t in data:
        if t["key"] == key:
            return {
                "text": t.get("text", ""),
                "photo_id": t.get("photo_id"),
                "sticker_id": t.get("sticker_id"),
                "animation_id": t.get("animation_id")
            }
    return {"text": "", "photo_id": None, "sticker_id": None, "animation_id": None}

def set_custom_text(key, text=None, photo_id=None, sticker_id=None, animation_id=None):
    data = load_json("custom_texts")
    found = False
    for t in data:
        if t["key"] == key:
            if text is not None:
                t["text"] = text
            if photo_id is not None:
                t["photo_id"] = photo_id
            if sticker_id is not None:
                t["sticker_id"] = sticker_id
            if animation_id is not None:
                t["animation_id"] = animation_id
            found = True
            break
    if not found:
        data.append({
            "key": key, 
            "text": text or "",
            "photo_id": photo_id,
            "sticker_id": sticker_id,
            "animation_id": animation_id
        })
    save_json("custom_texts", data)

# ---------------- AUTO DELETE TASKS ----------------
auto_delete_tasks: Dict[str, asyncio.Task] = {}

async def schedule_auto_delete(chat_type: str, chat_id: int, message_id: int, seconds: int):
    """Schedule message auto-deletion"""
    if seconds <= 0:
        return
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        print(f"Failed to delete message: {e}")

# ---------------- BATCH WORKER ----------------
async def batch_worker():
    """30 စီအလုပ်ခွဲလုပ်မယ် - Queue ထဲကနေဆွဲထုတ်"""
    global ACTIVE_USERS

    while True:
        async with BATCH_LOCK:
            # Active User 30 ပြည့်နေရင် စောင့်
            if ACTIVE_USERS >= BATCH_SIZE:
                await asyncio.sleep(0.5)
                continue

            # Queue ထဲက User တွေကို 30 ထိထုတ်
            slots = BATCH_SIZE - ACTIVE_USERS
            users_to_process = []

            for _ in range(slots):
                try:
                    user_id = WAITING_QUEUE.get_nowait()
                    users_to_process.append(user_id)
                    ACTIVE_USERS += 1
                except asyncio.QueueEmpty:
                    break

            # User တွေကို Process လုပ်
            for user_id in users_to_process:
                asyncio.create_task(process_user_request(user_id))

        await asyncio.sleep(0.1)

async def process_user_request(user_id: int):
    """User တစ်ယောက်ချင်းစီအတွက် Request ကို Process လုပ်"""
    global ACTIVE_USERS

    try:
        # ဒီနေရာမှာ မူရင်း Search Logic ကိုခေါ်မယ်
        await asyncio.sleep(0.1)  # Placeholder
    except Exception as e:
        print(f"Error processing user {user_id}: {e}")
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

# ---------------- UTILS ----------------
async def is_maintenance():
    return get_setting("maint") == "on"

async def check_force_join(user_id):
    channels = get_force_channels()
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

async def send_force_join(msg):
    channels = get_force_channels()
    if not channels:
        return True

    kb = InlineKeyboardMarkup()
    for ch in channels:
        kb.add(InlineKeyboardButton(ch["title"], url=ch["invite"]))
    kb.add(InlineKeyboardButton("✅ Done ✅", callback_data="force_done"))

    force_msg = await msg.answer(
    "⚠️ **BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။**\n\n"
    "• BOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။\n\n"
    "• Join ပြီးပါက \"✅Done\" ကိုနှိပ်ပါ။\n\n"
    "• လုပ်ဆောင်ချက်အောင်မြင်ပါက\n"
    "• ဇာတ်ကားနာမည်ပို့၍ရှာလို့ရပါပြီ။",
    reply_markup=kb,
    protect_content=True  # Save/Record/Download/Copy Link ပိတ်မယ်
)
    return False

async def send_searching_overlay(chat_id: int) -> Optional[int]:
    """Send searching overlay and return message_id"""
    overlay = get_custom_text("searching")

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

# ---------------- MAIN MENU (REPLY KEYBOARD) ----------------
def main_menu(is_owner=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Search Movie"))
    kb.add(KeyboardButton("📋 Movie List"))
    if is_owner:
        kb.add(KeyboardButton("🛠 Admin Panel"))
    return kb

# ---------------- START COMMAND ----------------
@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    is_owner = msg.from_user.id == OWNER_ID
    user_id = msg.from_user.id
    display_name = msg.from_user.full_name
    user_mention = msg.from_user.get_mention(as_html=True)

    # User အသစ်စစ်ပြီး Notification ပို့
    is_new = add_new_user(user_id, display_name, user_mention)

    if is_new:
        # New User Notification ပို့
        total_users = get_user_count()

        notification_text = (
            f"👤 <b>New User Notification</b>\n\n"
            f"<b>User:</b> {total_users}\n"
            f"<b>ID:</b> <code>{user_id}</code>\n"
            f"<b>User display name:</b> {display_name}\n"
            f"<b>Mention:</b> {user_mention}"
        )
        try:
            await bot.send_message(OWNER_ID, notification_text, protect_content=True)
        except Exception as e:
            print(f"Failed to notify owner: {e}")

    # Force Join စစ်
    if not await check_force_join(msg.from_user.id):
        await send_force_join(msg)
        return

    # Force Join ပြီးမှသာ Start Buttons ပြမယ် (Photo Rotation နဲ့)
    await send_start_welcome(msg, is_owner)
    
    # Main Menu ကို Reply Keyboard နဲ့ပို့မယ် (Owner ဆိုရင် Admin Panel ပါမယ်)
    await msg.answer(
        "📌 **Main Menu**\n\nအောက်ပါခလုတ်များကိုသုံးပါ:",
        reply_markup=main_menu(is_owner),
        protect_content=True
    )

async def send_start_welcome(msg: types.Message, is_owner: bool):
    """Start Welcome ပုံနဲ့ Buttons ပို့မယ် - Photo Rotation နဲ့"""

    # Photo Rotation အတိုင်းပုံရွေး
    welcome_data = get_next_welcome_photo()

    # Inline Keyboard - 2 Columns အတိုင်း
    kb = InlineKeyboardMarkup(row_width=2)
    rows = get_start_buttons_by_row()

    # Row အလိုက် buttons ထည့်
    for row_num in sorted(rows.keys()):
        row_buttons = rows[row_num]
        buttons = []
        for btn in row_buttons[:2]:  # တစ်တန်းကို 2 ခုစီ
            buttons.append(InlineKeyboardButton(btn["name"], url=btn["link"]))
        if buttons:
            kb.row(*buttons)

    # Owner အတွက် Manage Buttons ခလုတ်
    if is_owner:
        kb.add(InlineKeyboardButton("⚙️ Manage Start Buttons", callback_data="manage_start_buttons"))

    # Welcome Message ပို့ (Photo ရှိရင် Photo နဲ့၊ မရှိရင် Text) - protect_content=True
    if welcome_data and welcome_data.get("photo_id"):
        await msg.answer_photo(
            photo=welcome_data["photo_id"],
            caption=welcome_data.get("caption") or welcome_data.get("text", "👋 Welcome!"),
            reply_markup=kb,
            protect_content=True
        )
    else:
        await msg.answer(
            welcome_data.get("text", "👋 **Welcome to Movie Bot!**\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။"),
            reply_markup=kb,
            protect_content=True
        )

# ---------------- MANAGE START BUTTONS ----------------
class StartButtonManagement(StatesGroup):
    waiting_for_name = State()
    waiting_for_link = State()
    waiting_for_edit_id = State()
    waiting_for_edit_name = State()
    waiting_for_edit_link = State()
    waiting_for_edit_row = State()

@dp.callback_query_handler(lambda c: c.data == "manage_start_buttons")
async def manage_start_buttons(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = get_start_buttons()
    text = "⚙️ **Start Buttons Management**\n\n"

    if not buttons:
        text += "Buttons မရှိသေးပါ။\n"
    else:
        rows = get_start_buttons_by_row()
        for row_num in sorted(rows.keys()):
            text += f"\n🔹 Row {row_num + 1}:\n"
            for btn in rows[row_num]:
                text += f"   • ID: {btn['id']} | {btn['name']} - {btn['link'][:30]}...\n"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Button", callback_data="add_start_button"),
        InlineKeyboardButton("✏️ Edit Button", callback_data="edit_start_button")
    )
    kb.add(
        InlineKeyboardButton("🗑 Delete Button", callback_data="delete_start_button"),
        InlineKeyboardButton("🖼 Manage Welcome", callback_data="manage_start_welcome")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_start"))

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "add_start_button")
async def add_start_button_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await StartButtonManagement.waiting_for_name.set()
    await call.message.answer("🔹 Button နာမည်ထည့်ပါ:", protect_content=True)
    await call.answer()

@dp.message_handler(state=StartButtonManagement.waiting_for_name)
async def add_start_button_name(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await StartButtonManagement.waiting_for_link.set()
    await msg.answer("🔗 Button Link ထည့်ပါ (https://t.me/... or https://...):", protect_content=True)

@dp.message_handler(state=StartButtonManagement.waiting_for_link)
async def add_start_button_link(msg: types.Message, state: FSMContext):
    if not msg.text.startswith(('http://', 'https://')):
        return await msg.answer("❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။", protect_content=True)

    data = await state.get_data()
    add_start_button(data['name'], msg.text)
    await msg.answer(f"✅ Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    await state.finish()

    # Admin Panel ကိုပြန်ခေါ်
    await manage_start_buttons(msg)

@dp.callback_query_handler(lambda c: c.data == "delete_start_button")
async def delete_start_button_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = get_start_buttons()
    if not buttons:
        await call.answer("❌ Button မရှိပါ။", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for btn in buttons:
        kb.add(InlineKeyboardButton(
            f"🗑 {btn['name']} (Row {btn.get('row', 0)+1})", 
            callback_data=f"delstartbtn_{btn['id']}"
        ))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="manage_start_buttons"))

    await call.message.edit_text("ဖျက်မည့် Button ကိုရွေးပါ:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("delstartbtn_"))
async def delete_start_button_confirm(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    btn_id = call.data.split("_")[1]
    delete_start_button(btn_id)
    await call.answer("✅ Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    await manage_start_buttons(call)

@dp.callback_query_handler(lambda c: c.data == "edit_start_button")
async def edit_start_button_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = get_start_buttons()
    if not buttons:
        await call.answer("❌ Button မရှိပါ။", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for btn in buttons:
        kb.add(InlineKeyboardButton(
            f"✏️ {btn['name']} (Row {btn.get('row', 0)+1})", 
            callback_data=f"editstartbtn_{btn['id']}"
        ))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="manage_start_buttons"))

    await call.message.edit_text("ပြင်မည့် Button ကိုရွေးပါ:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("editstartbtn_"))
async def edit_start_button_choice(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return

    btn_id = call.data.split("_")[1]
    await state.update_data(edit_id=btn_id)

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Name ပြင်မည်", callback_data=f"editname_{btn_id}"),
        InlineKeyboardButton("🔗 Link ပြင်မည်", callback_data=f"editlink_{btn_id}"),
        InlineKeyboardButton("📊 Row ပြင်မည်", callback_data=f"editrow_{btn_id}")
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="manage_start_buttons"))

    await call.message.edit_text("ဘာကိုပြင်မှာလဲ?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("editname_"))
async def edit_start_button_name(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    btn_id = call.data.split("_")[1]
    await state.update_data(edit_id=btn_id)
    await StartButtonManagement.waiting_for_edit_name.set()
    await call.message.answer("Button နာမည်အသစ်ထည့်ပါ:", protect_content=True)
    await call.answer()

@dp.message_handler(state=StartButtonManagement.waiting_for_edit_name)
async def edit_start_button_name_done(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    update_start_button(data['edit_id'], name=msg.text)
    await msg.answer(f"✅ Button name ပြင်ပြီးပါပြီ။", protect_content=True)
    await state.finish()
    await manage_start_buttons(msg)

@dp.callback_query_handler(lambda c: c.data.startswith("editlink_"))
async def edit_start_button_link(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    btn_id = call.data.split("_")[1]
    await state.update_data(edit_id=btn_id)
    await StartButtonManagement.waiting_for_edit_link.set()
    await call.message.answer("Button Link အသစ်ထည့်ပါ:", protect_content=True)
    await call.answer()

@dp.message_handler(state=StartButtonManagement.waiting_for_edit_link)
async def edit_start_button_link_done(msg: types.Message, state: FSMContext):
    if not msg.text.startswith(('http://', 'https://')):
        return await msg.answer("❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။", protect_content=True)

    data = await state.get_data()
    update_start_button(data['edit_id'], link=msg.text)
    await msg.answer(f"✅ Button link ပြင်ပြီးပါပြီ။", protect_content=True)
    await state.finish()
    await manage_start_buttons(msg)

@dp.callback_query_handler(lambda c: c.data.startswith("editrow_"))
async def edit_start_button_row(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    btn_id = call.data.split("_")[1]
    await state.update_data(edit_id=btn_id)
    await StartButtonManagement.waiting_for_edit_row.set()
    await call.message.answer("Row နံပါတ်အသစ်ထည့်ပါ (0 = ပထမတန်း):", protect_content=True)
    await call.answer()

@dp.message_handler(state=StartButtonManagement.waiting_for_edit_row)
async def edit_start_button_row_done(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("❌ ဂဏန်းပဲထည့်ပါ။", protect_content=True)

    data = await state.get_data()
    update_start_button(data['edit_id'], row=int(msg.text))
    await msg.answer(f"✅ Button row ပြင်ပြီးပါပြီ။", protect_content=True)
    await state.finish()
    await manage_start_buttons(msg)

# ---------------- MANAGE START WELCOME ----------------
class StartWelcomeManagement(StatesGroup):
    waiting_for_photo = State()
    waiting_for_delete_index = State()

@dp.callback_query_handler(lambda c: c.data == "manage_start_welcome")
async def manage_start_welcome(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = get_start_welcome()
    text = f"🖼 **Start Welcome Management**\n\n"
    text += f"📸 စုစုပေါင်းပုံ: {len(welcome_list)} ပုံ\n\n"

    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            text += f"{i+1}. 🖼 Photo - {w.get('caption', 'No caption')[:30]}\n"
        else:
            text += f"{i+1}. 📝 Text - {w.get('text', '')[:30]}\n"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Photo", callback_data="add_welcome_photo"),
        InlineKeyboardButton("➕ Add Text", callback_data="add_welcome_text")
    )
    kb.add(
        InlineKeyboardButton("🗑 Delete", callback_data="delete_welcome_item"),
        InlineKeyboardButton("⬅️ Back", callback_data="manage_start_buttons")
    )

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "add_welcome_photo")
async def add_welcome_photo_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await StartWelcomeManagement.waiting_for_photo.set()
    await call.message.answer(
        "🖼 Welcome Photo ထည့်ရန် Photo ပို့ပါ။\n"
        "Caption ပါထည့်ချင်ရင် Photo နဲ့အတူ Caption ရေးပို့ပါ။\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@dp.message_handler(state=StartWelcomeManagement.waiting_for_photo, content_types=['photo'])
async def add_welcome_photo_done(msg: types.Message, state: FSMContext):
    photo_id = msg.photo[-1].file_id
    caption = msg.caption or ""
    add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
    await msg.answer(f"✅ Welcome Photo ထည့်ပြီးပါပြီ။\n📸 စုစုပေါင်းပုံ: {get_start_welcome_count()} ပုံ", protect_content=True)
    await state.finish()
    await manage_start_welcome(msg)

@dp.callback_query_handler(lambda c: c.data == "add_welcome_text")
async def add_welcome_text_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await StartWelcomeManagement.waiting_for_photo.set()
    await call.message.answer(
        "📝 Welcome Text ထည့်ရန် စာသားပို့ပါ။\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@dp.message_handler(state=StartWelcomeManagement.waiting_for_photo, content_types=['text'])
async def add_welcome_text_done(msg: types.Message, state: FSMContext):
    if msg.text == '/cancel':
        await msg.answer("❌ Cancelled", protect_content=True)
        await state.finish()
        return

    add_start_welcome(text=msg.text)
    await msg.answer(f"✅ Welcome Text ထည့်ပြီးပါပြီ။\n📝 စုစုပေါင်း: {get_start_welcome_count()} ခု", protect_content=True)
    await state.finish()
    await manage_start_welcome(msg)

@dp.callback_query_handler(lambda c: c.data == "delete_welcome_item")
async def delete_welcome_item_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = get_start_welcome()
    if not welcome_list:
        await call.answer("❌ ဖျက်စရာမရှိပါ။", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            kb.add(InlineKeyboardButton(
                f"🗑 {i+1}. 🖼 Photo - {w.get('caption', 'No caption')[:20]}", 
                callback_data=f"delwelcome_{i}"
            ))
        else:
            kb.add(InlineKeyboardButton(
                f"🗑 {i+1}. 📝 Text - {w.get('text', '')[:20]}", 
                callback_data=f"delwelcome_{i}"
            ))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="manage_start_welcome"))

    await call.message.edit_text("ဖျက်မည့် Welcome Item ကိုရွေးပါ:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("delwelcome_"))
async def delete_welcome_item_confirm(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    index = int(call.data.split("_")[1])
    if delete_start_welcome(index):
        await call.answer("✅ ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        await call.answer("❌ ဖျက်လို့မရပါ။", show_alert=True)

    await manage_start_welcome(call)

# ---------------- ADMIN PANEL ----------------
def admin_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("➕ Add Movie", callback_data="add_movie"),
           InlineKeyboardButton("🗑 Delete Movie", callback_data="del_movie"))
    kb.add(InlineKeyboardButton("📢 Broadcast", callback_data="broadcast"),
           InlineKeyboardButton("📡 Force Channels", callback_data="force"))
    kb.add(InlineKeyboardButton("📥 Backup", callback_data="backup"),
           InlineKeyboardButton("📤 Restore", callback_data="restore"))
    kb.add(InlineKeyboardButton("🛑 Maintenance", callback_data="maint"),
           InlineKeyboardButton("📺 Ads Manager", callback_data="ads_manager"))
    kb.add(InlineKeyboardButton("⏰ Auto Delete", callback_data="auto_delete"),
           InlineKeyboardButton("🗑 Clear All Data", callback_data="clear_all_data"))
    kb.add(InlineKeyboardButton("📝 Welcome Set", callback_data="edit_welcome"))
    kb.add(InlineKeyboardButton("📢 Force Msg Set", callback_data="edit_forcemsg"))
    kb.add(InlineKeyboardButton("🔍 Searching Set", callback_data="edit_searching"))
    kb.add(InlineKeyboardButton("⚙️ Start Buttons", callback_data="manage_start_buttons"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back"))
    return kb

# ---------------- ADS MANAGER ----------------
class AddAd(StatesGroup):
    msgid = State()
    chatid = State()

@dp.callback_query_handler(lambda c: c.data == "ads_manager")
async def ads_manager(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    ads = get_ads()
    text = "📺 Ads Manager:\n\n"
    if not ads:
        text += "No ads added yet."
    else:
        for a in ads:
            text += f"ID: {a['id']} | MsgID: {a['message_id']} | ChatID: {a['storage_chat_id']}\n"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("➕ Add Ad", callback_data="add_ad"))
    for a in ads:
        kb.add(InlineKeyboardButton(f"🗑 Delete Ad {a['id']}", callback_data=f"delad_{a['id']}"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "add_ad")
async def add_ad_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await AddAd.msgid.set()
    await call.message.answer("Enter Ad Message ID:", protect_content=True)
    await call.answer()

@dp.message_handler(state=AddAd.msgid)
async def add_ad_msgid(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("Please enter a numeric Message ID.", protect_content=True)
    await state.update_data(msgid=int(msg.text))
    await AddAd.chatid.set()
    await msg.answer("Enter Storage Group Chat ID for this Ad:", protect_content=True)

@dp.message_handler(state=AddAd.chatid)
async def add_ad_chatid(msg: types.Message, state: FSMContext):
    try:
        chatid = int(msg.text)
    except:
        return await msg.answer("Invalid Chat ID.", protect_content=True)

    data = await state.get_data()
    add_ad(data["msgid"], chatid)
    await msg.answer("✅ Ad added successfully!", protect_content=True)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("delad_"))
async def del_ad_process(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    aid = call.data.split("_")[1]
    delete_ad(aid)
    await call.answer("✅ Ad deleted", show_alert=True)
    await ads_manager(call)

@dp.message_handler(lambda m: m.text == "🛠 Admin Panel")
async def admin_panel(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer("Admin Panel", reply_markup=admin_menu(), protect_content=True)

@dp.callback_query_handler(lambda c: c.data == "back")
async def back(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer("Menu:", reply_markup=main_menu(call.from_user.id == OWNER_ID), protect_content=True)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_start")
async def back_to_start(call: types.CallbackQuery):
    await call.message.delete()
    await send_start_welcome(call.message, call.from_user.id == OWNER_ID)

# ---------------- AUTO DELETE SETTINGS ----------------
@dp.callback_query_handler(lambda c: c.data == "auto_delete")
async def auto_delete_menu(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)
    dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)

    text = f"🕒 Auto Delete Settings:\n\n"
    text += f"Group Messages: {group_sec} seconds\n"
    text += f"DM Messages: {dm_sec} seconds\n\n"
    text += "Select option to change:"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("👥 Group", callback_data="set_group_delete"),
           InlineKeyboardButton("💬 DM", callback_data="set_dm_delete"))
    kb.add(InlineKeyboardButton("❌ Disable All", callback_data="disable_auto_delete"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("set_") and "delete" in c.data)
async def set_auto_delete_type(call: types.CallbackQuery):
    delete_type = "group" if "group" in c.data else "dm"

    kb = InlineKeyboardMarkup(row_width=3)
    for sec in AUTO_DELETE_OPTIONS:
        kb.insert(InlineKeyboardButton(f"{sec}s", callback_data=f"set_time_{delete_type}_{sec}"))
    kb.add(InlineKeyboardButton("❌ Disable", callback_data=f"set_time_{delete_type}_0"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="auto_delete"))

    await call.message.edit_text(f"Select auto-delete time for {delete_type.upper()}:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("set_time_"))
async def confirm_auto_delete(call: types.CallbackQuery):
    parts = call.data.split("_")
    delete_type = parts[2]
    seconds = int(parts[3])

    set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        await call.answer(f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        await call.answer(f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    await auto_delete_menu(call)

@dp.callback_query_handler(lambda c: c.data == "disable_auto_delete")
async def disable_all_auto_delete(call: types.CallbackQuery):
    set_auto_delete_config("group", 0)
    set_auto_delete_config("dm", 0)
    await call.answer("All auto-delete disabled!", show_alert=True)
    await auto_delete_menu(call)

@dp.callback_query_handler(lambda c: c.data == "back_admin")
async def back_admin(call: types.CallbackQuery):
    await call.message.edit_text("Admin Panel", reply_markup=admin_menu())

@dp.callback_query_handler(lambda c: c.data == "clear_all_data")
async def clear_all_data_confirm(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm Clear All", callback_data="confirm_clear_all"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))
    await call.message.edit_text("⚠️ <b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings.", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "confirm_clear_all")
async def process_clear_all_data(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    files_to_clear = ["movies", "users", "ads", "settings", "force_channels", "custom_texts", "auto_delete", "start_buttons", "start_welcome"]
    for f in files_to_clear:
        save_json(f, [])

    await call.message.edit_text("✅ All data has been cleared successfully!", reply_markup=admin_menu())
    await call.answer("Data cleared", show_alert=True)

# ---------------- FORCE CHANNELS ----------------
@dp.callback_query_handler(lambda c: c.data == "force")
async def force(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    channels = get_force_channels()
    text = "📡 Force Channels:\n\n"

    if not channels:
        text += "No force channels added yet."
    else:
        for ch in channels:
            text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    kb = InlineKeyboardMarkup(row_width=1)

    for ch in channels:
        kb.add(InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}"))

    kb.add(InlineKeyboardButton("➕ Add Channel", callback_data="add_force"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "add_force")
async def add_force(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    await call.message.answer(
        "📌 Channel link ပေးပါ (public/private OK)\n\n"
        "Example:\nhttps://t.me/yourchannel\nhttps://t.me/+AbCdEfGhIjKlMn==",
        protect_content=True
    )

@dp.message_handler(lambda m: m.text and m.text.startswith("https://t.me/"))
async def catch_force_link(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        return

    link = msg.text.strip()
    chat_id = None
    chat = None

    if "+" not in link:
        username = link.split("t.me/")[1].replace("@", "").strip("/")
        try:
            chat = await bot.get_chat(f"@{username}")
            chat_id = chat.id
        except:
            return await msg.answer("❌ Public channel not found", protect_content=True)
    else:
        try:
            chat = await bot.get_chat(link)
            chat_id = chat.id
        except:
            return await msg.answer("❌ Private channel invalid", protect_content=True)

    try:
        bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
        if bot_member.status not in ("administrator", "creator"):
            return await msg.answer("❌ Bot must be admin in channel", protect_content=True)
    except:
        return await msg.answer("❌ Cannot check admin status", protect_content=True)

    try:
        invite = await bot.export_chat_invite_link(chat_id)
    except:
        if chat.username:
            invite = f"https://t.me/{chat.username}"
        else:
            return await msg.answer("❌ Cannot create invite link", protect_content=True)

    add_force_channel(chat_id, chat.title, invite)

    await msg.answer(f"✅ Added: {chat.title}", protect_content=True)

    channels = get_force_channels()
    text = "📡 Force Channels:\n\n"
    for ch in channels:
        text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    kb = InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        kb.add(InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}"))
    kb.add(InlineKeyboardButton("➕ Add Channel", callback_data="add_force"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))

    await msg.answer(text, reply_markup=kb, protect_content=True)

@dp.callback_query_handler(lambda c: c.data.startswith("delch_"))
async def delch(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    cid = call.data.split("_")[1]
    delete_force_channel(cid)
    await call.answer("✅ Deleted", show_alert=True)

    channels = get_force_channels()
    text = "📡 Force Channels:\n\n"

    if not channels:
        text += "No force channels added yet."
    else:
        for ch in channels:
            text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    kb = InlineKeyboardMarkup(row_width=1)

    for ch in channels:
        kb.add(InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}"))

    kb.add(InlineKeyboardButton("➕ Add Channel", callback_data="add_force"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))

    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "force_done")
async def force_done(call: types.CallbackQuery):
    ok = await check_force_join(call.from_user.id)

    if not ok:
        await call.answer(
            "❌ Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
            "ကျေးဇူးပြု၍ သတ်မှတ်ထားသော Channel များအားလုံးကို အရင် Join လုပ်ပါ။\n"
            "ပြီးရင် “Done” ကို နှိပ်ပါ။",
            show_alert=True
        )
        return

    await call.answer("joinပေးတဲ့အတွက်ကျေးဇူးတင်ပါတယ်!", show_alert=True)
    await call.message.delete()
    await send_start_welcome(call.message, call.from_user.id == OWNER_ID)

# ---------------- TEXT SETTINGS ----------------
class EditText(StatesGroup):
    waiting = State()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_text_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    key = call.data.replace("edit_", "")
    await EditText.waiting.set()
    state = dp.current_state(user=call.from_user.id)
    await state.update_data(key=key)

    if key == "searching":
        await call.message.answer(
            "🔍 Searching overlay အတွက် content ပို့ပေးပါ:\n\n"
            "• Text message ပို့ရင် - စာသားအဖြစ်သိမ်းမယ်\n"
            "• Photo ပို့ရင် - Photo နဲ့ caption သိမ်းမယ်\n"
            "• Sticker ပို့ရင် - Sticker အဖြစ်သိမ်းမယ်\n"
            "• GIF/Animation ပို့ရင် - GIF အဖြစ်သိမ်းမယ်\n\n"
            "မပို့ချင်ရင် /cancel ရိုက်ပါ။",
            protect_content=True
        )
    else:
        await call.message.answer(f"'{key}' အတွက် စာအသစ်ပို့ပေးပါ (Photo ပါရင် Photo နဲ့အတူ Caption ထည့်ပေးပါ)", protect_content=True)

    await call.answer()

@dp.message_handler(state=EditText.waiting, content_types=types.ContentTypes.ANY)
async def edit_text_done(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data['key']

    if msg.content_type == 'text' and msg.text == '/cancel':
        await msg.answer("❌ Cancelled", protect_content=True)
        await state.finish()
        return

    if msg.content_type == 'text':
        set_custom_text(key, text=msg.text)
        await msg.answer(f"✅ {key} text updated successfully", protect_content=True)

    elif msg.content_type == 'photo':
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        set_custom_text(key, text=caption, photo_id=photo_id)
        await msg.answer(f"✅ {key} photo updated successfully", protect_content=True)

    elif msg.content_type == 'sticker':
        sticker_id = msg.sticker.file_id
        set_custom_text(key, sticker_id=sticker_id)
        await msg.answer(f"✅ {key} sticker updated successfully", protect_content=True)

    elif msg.content_type == 'animation':
        animation_id = msg.animation.file_id
        caption = msg.caption or ""
        set_custom_text(key, text=caption, animation_id=animation_id)
        await msg.answer(f"✅ {key} GIF updated successfully", protect_content=True)

    else:
        await msg.answer("❌ Unsupported content type", protect_content=True)

    await state.finish()

@dp.message_handler(lambda m: m.text == "📋 Movie List")
async def movie_list_redirect(msg: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎬 Movie + Code ကြည့်ရန်", url="https://t.me/seatvmmmovielist"))
    await msg.answer("📌 ရှိတဲ့ Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ", reply_markup=kb, protect_content=True)

# ---------------- MAINTENANCE ----------------
@dp.callback_query_handler(lambda c: c.data == "maint")
async def maint(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    current = await is_maintenance()
    new = "off" if current else "on"
    set_setting("maint", new)
    await call.answer(f"Maintenance: {new.upper()}", show_alert=True)

# ---------------- ADD MOVIE ----------------
class AddMovie(StatesGroup):
    name = State()
    code = State()
    msgid = State()
    chatid = State()

@dp.callback_query_handler(lambda c: c.data == "add_movie")
async def add_movie(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await AddMovie.name.set()
    await call.message.answer("🎬 ဇာတ်ကားနာမည်?", protect_content=True)
    await call.answer()

@dp.message_handler(state=AddMovie.name)
async def add_movie_name(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await AddMovie.code.set()
    await msg.answer("🔢 ဇာတ်ကား Code (ဥပမာ: 101010, MM101, etc):", protect_content=True)

@dp.message_handler(state=AddMovie.code)
async def add_movie_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    if not code:
        return await msg.answer("❌ Code ထည့်ပါ။", protect_content=True)
    await state.update_data(code=code)
    await AddMovie.msgid.set()
    await msg.answer("📨 Message ID?", protect_content=True)

@dp.message_handler(state=AddMovie.msgid)
async def add_movie_msgid(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("❌ ဂဏန်းပဲထည့်ပါ။", protect_content=True)
    await state.update_data(msgid=int(msg.text))
    await AddMovie.chatid.set()
    await msg.answer("💬 Storage Group Chat ID?", protect_content=True)

@dp.message_handler(state=AddMovie.chatid)
async def add_movie_chatid(msg: types.Message, state: FSMContext):
    try:
        chatid = int(msg.text)
    except:
        return await msg.answer("❌ Chat ID မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    add_movie_record(data["name"], data["code"], data["msgid"], chatid)

    await msg.answer(f"✅ ဇာတ်ကားထည့်ပြီးပါပြီ!\n\nနာမည်: {data['name']}\nCode: {data['code']}", protect_content=True)
    await state.finish()

# ---------------- DELETE MOVIE ----------------
class DelMovie(StatesGroup):
    code = State()

@dp.callback_query_handler(lambda c: c.data == "del_movie")
async def del_movie(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await DelMovie.code.set()
    await call.message.answer("🗑 ဖျက်မည့် ဇာတ်ကား Code ကိုထည့်ပါ:", protect_content=True)
    await call.answer()

@dp.message_handler(state=DelMovie.code)
async def del_movie_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    delete_movie(code)
    await msg.answer(f"✅ Code `{code}` ဖျက်ပြီးပါပြီ။", protect_content=True)
    await state.finish()

# ---------------- BROADCAST ----------------
class Broadcast(StatesGroup):
    text = State()

@dp.callback_query_handler(lambda c: c.data == "broadcast")
async def bc(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await Broadcast.text.set()
    await call.message.answer("Broadcast text?", protect_content=True)
    await call.answer()

@dp.message_handler(state=Broadcast.text)
async def bc_text(msg: types.Message, state: FSMContext):
    text = msg.text
    await state.finish()

    users = get_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], text, protect_content=True)
            sent += 1
        except:
            pass

    await msg.answer(f"📢 Broadcast sent to {sent} users.", protect_content=True)

# ---------------- /os COMMAND ----------------
@dp.message_handler(commands=["os"])
async def os_command(msg: types.Message):
    if msg.chat.type not in ["group", "supergroup"]:
        await msg.answer("This command can only be used in groups!", protect_content=True)
        return

    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = await msg.reply(
        " **owner-@osamu1123**\n\n"
        "• Bot Status: ✅ Online\n"
        "• Queue System: 🟢 Active (Batch: 30)\n"
        "• Auto-Delete: " + ("✅ " + str(group_sec) + "s" if group_sec > 0 else "❌ Disabled") + "\n"
        "• Version: 3.2\n\n"
        "Use /os name command.",
        protect_content=True
    )

    if group_sec > 0:
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, response.message_id, group_sec))
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

# ---------------- SEARCH MOVIE ----------------
@dp.message_handler()
async def search(msg: types.Message):
    # Search Movie Button
    if msg.text == "🔍 Search Movie":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎬 Movie + Code ကြည့်ရန်", url="https://t.me/seatvmmmovielist"))
        return await msg.answer("🔍 <b>ဇာတ်ကား Code ပို့ပေးပါ</b>", reply_markup=kb, protect_content=True)

    # Command ဆိုရင် skip
    if msg.text.startswith("/"):
        return

    # Maintenance check
    if await is_maintenance() and msg.from_user.id != OWNER_ID:
        return await msg.answer("🛠 Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။", protect_content=True)

    # Force Join check
    if not await check_force_join(msg.from_user.id):
        sent = await send_force_join(msg)
        if sent is False:
            return

    # Cooldown check
    if msg.from_user.id != OWNER_ID:
        last = get_user_last(msg.from_user.id)
        if last:
            diff = datetime.now() - datetime.fromisoformat(last)
            if diff.total_seconds() < COOLDOWN:
                remain = int(COOLDOWN - diff.total_seconds())
                return await msg.answer(f"⏳ ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်", protect_content=True)

    # Code နဲ့ရှာ (O(1))
    code = msg.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        return await msg.answer(f"❌ Code `{code}` မရှိပါ။\n\n🔍 Search Movie နှိပ်ပြီး Code စစ်ပါ။", protect_content=True)

    # ---------- BATCH QUEUE SYSTEM ----------
    global ACTIVE_USERS

    async with BATCH_LOCK:
        # Active User 30 ပြည့်နေရင် Queue ထဲထည့်
        if ACTIVE_USERS >= BATCH_SIZE:
            await WAITING_QUEUE.put(msg.from_user.id)
            position = WAITING_QUEUE.qsize()

            queue_msg = await msg.answer(
                f"⏳ **စောင့်ဆိုင်းနေဆဲအသုံးပြုသူများ**\n\n"
                f"• သင့်နေရာ: **{position}**\n"
                f"• လက်ရှိအသုံးပြုနေသူ: **{ACTIVE_USERS}/{BATCH_SIZE}**\n\n"
                f"ကျေးဇူးပြု၍ စောင့်ဆိုင်းပေးပါ။",
                protect_content=True
            )

            # 5 စက္ကန့်အကြာမှာ Queue မက်ဆေ့ဖျက်
            await asyncio.sleep(5)
            await safe_delete_message(msg.chat.id, queue_msg.message_id)
            return

        # Active User 30 အောက်ဆိုရင် ချက်ချင်းလုပ်
        ACTIVE_USERS += 1

    try:
        # Cooldown update
        update_user_search(msg.from_user.id)
        USER_PROCESSING_TIME[msg.from_user.id] = datetime.now()

        # --- ADS LOGIC ---
        ads = get_ads()
        if ads:
            idx = get_next_ad_index()
            if idx is not None and idx < len(ads):
                ad = ads[idx]
                try:
                    ad_sent = await bot.copy_message(
                        chat_id=msg.from_user.id,
                        from_chat_id=ad["storage_chat_id"],
                        message_id=ad["message_id"],
                        protect_content=True  # Save/Record/Download/Copy Link ပိတ်မယ်
                    )
                    asyncio.create_task(schedule_auto_delete("dm", msg.from_user.id, ad_sent.message_id, 10))
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"Error sending ad: {e}")

        # Searching overlay
        searching_msg_id = await send_searching_overlay(msg.from_user.id)

        # Send movie - ဖျက်မည် Button ဖြုတ်ပြီး Owner Button တစ်ခုတည်းထားမယ်
        sent = await bot.copy_message(
            chat_id=msg.from_user.id,
            from_chat_id=movie["storage_chat_id"],
            message_id=movie["message_id"],
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⚜️Owner⚜️", url="https://t.me/osamu1123")
            ),
            protect_content=True  # Save/Record/Download/Copy Link အကုန်ပိတ်မယ်
        )

        # Delete searching overlay
        if searching_msg_id:
            await safe_delete_message(msg.from_user.id, searching_msg_id)

        # Auto-delete
        config = get_auto_delete_config()
        dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)
        if dm_sec > 0:
            asyncio.create_task(schedule_auto_delete("dm", msg.from_user.id, sent.message_id, dm_sec))

    except Exception as e:
        print(f"Error sending movie: {e}")
        await msg.answer("❌ Error sending movie. Please try again.", protect_content=True)
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

# ---------------- DELETE CALLBACK (ဖြုတ်လိုက်ပြီ) ----------------
# ဖျက်မည် Button မရှိတော့ဘူး

# ---------------- BACKUP ----------------
@dp.callback_query_handler(lambda c: c.data == "backup")
async def backup_db(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    data = {
        "movies": get_movies(),
        "users": get_users(),
        "settings": load_json("settings"),
        "force_channels": get_force_channels(),
        "auto_delete": get_auto_delete_config(),
        "custom_texts": load_json("custom_texts"),
        "start_buttons": get_start_buttons(),
        "start_welcome": get_start_welcome()
    }

    with open("backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    await bot.send_document(
        OWNER_ID,
        InputFile("backup.json"),
        caption="📥 Backup File",
        protect_content=True
    )

    await call.answer("Backup sent!", show_alert=True)

# ---------------- RESTORE ----------------
@dp.callback_query_handler(lambda c: c.data == "restore")
async def restore_request(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await call.message.answer("📤 Upload backup.json file", protect_content=True)
    await call.answer()

@dp.message_handler(content_types=types.ContentTypes.DOCUMENT)
async def restore_process(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        return

    try:
        file = await msg.document.download(destination_file="restore.json")

        with open("restore.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        save_json("movies", data.get("movies", []))
        save_json("users", data.get("users", []))
        save_json("settings", data.get("settings", []))
        save_json("force_channels", data.get("force_channels", []))
        save_json("auto_delete", data.get("auto_delete", []))
        save_json("custom_texts", data.get("custom_texts", []))
        save_json("start_buttons", data.get("start_buttons", []))
        save_json("start_welcome", data.get("start_welcome", []))

        reload_movies_cache()  # Cache ပြန်တင်
        await msg.answer("✅ Restore Completed!", protect_content=True)
    except Exception as e:
        await msg.answer(f"❌ Restore Failed: {str(e)}", protect_content=True)

# ---------------- GROUP MESSAGE AUTO-DELETE ----------------
@dp.message_handler(content_types=ContentType.ANY, chat_type=["group", "supergroup"])
async def group_message_handler(msg: types.Message):
    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not msg.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

# ---------------- ON STARTUP ----------------
async def on_startup(dp):
    # Load movies cache
    load_movies_cache()
    # Start batch worker
    asyncio.create_task(batch_worker())
    print("✅ Bot started with Code-only search + Batch 30 + Queue system")
    print(f"✅ Movies in cache: {len(MOVIES_DICT)}")
    print(f"✅ Batch size: {BATCH_SIZE}")

    # Welcome Photo count
    welcome_count = get_start_welcome_count()
    print(f"✅ Welcome photos: {welcome_count}")

# ---------------- RUN ----------------
if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
