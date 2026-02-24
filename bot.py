import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from collections import Counter
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
from motor.motor_asyncio import AsyncIOMotorClient

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

COOLDOWN = 90
BATCH_SIZE = 30
AUTO_DELETE_OPTIONS = [5, 10, 30]

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.movie_bot

ACTIVE_USERS = 0
WAITING_QUEUE = asyncio.Queue()
BATCH_LOCK = asyncio.Lock()
USER_PROCESSING_TIME = {}
MOVIES_DICT = {}

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

async def get_movies():
    movies = await db.movies.find().to_list(None)
    return movies

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
    await db.movies.insert_one({
        "movie_name": name,
        "movie_code": code.upper(),
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    await reload_movies_cache()

async def delete_movie(code):
    await db.movies.delete_many({"movie_code": code.upper()})
    await reload_movies_cache()

async def get_ads():
    ads = await db.ads.find().to_list(None)
    return ads

async def add_ad(msgid, chatid):
    count = await db.ads.count_documents({})
    await db.ads.insert_one({
        "id": count + 1,
        "message_id": msgid,
        "storage_chat_id": chatid
    })

async def delete_ad(aid):
    await db.ads.delete_one({"id": int(aid)})

async def get_users():
    users = await db.users.find().to_list(None)
    return users

async def add_new_user(uid, name, mention):
    existing = await db.users.find_one({"user_id": uid})
    if existing:
        return False

    await db.users.insert_one({
        "user_id": uid,
        "last_search": None,
        "join_date": datetime.now().isoformat(),
        "name": name,
        "mention": mention,
        "search_count": 0
    })
    return True

async def get_user_count():
    return await db.users.count_documents({})

async def update_user_search(uid):
    existing = await db.users.find_one({"user_id": uid})
    if existing:
        await db.users.update_one(
            {"user_id": uid},
            {
                "$set": {"last_search": datetime.now().isoformat()},
                "$inc": {"search_count": 1}
            }
        )
    else:
        await db.users.insert_one({
            "user_id": uid,
            "last_search": datetime.now().isoformat(),
            "join_date": datetime.now().isoformat(),
            "search_count": 1
        })

async def get_user_last(uid):
    user = await db.users.find_one({"user_id": uid})
    if user:
        return user.get("last_search")
    return None

async def get_top_searches(limit=5):
    pipeline = [
        {"$match": {"search_count": {"$gt": 0}}},
        {"$sort": {"search_count": -1}},
        {"$limit": limit}
    ]
    return await db.users.aggregate(pipeline).to_list(None)

async def get_daily_active_users():
    yesterday = datetime.now() - timedelta(days=1)
    count = await db.users.count_documents({
        "last_search": {"$gte": yesterday.isoformat()}
    })
    return count

async def get_setting(key):
    setting = await db.settings.find_one({"key": key})
    if setting:
        return setting.get("value")
    return None

async def set_setting(key, value):
    await db.settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value}},
        upsert=True
    )

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

async def get_auto_delete_config():
    configs = await db.auto_delete.find().to_list(None)
    if not configs:
        configs = [
            {"type": "group", "seconds": 0},
            {"type": "dm", "seconds": 0}
        ]
        await db.auto_delete.insert_many(configs)
    return configs

async def set_auto_delete_config(config_type, value):
    await db.auto_delete.update_one(
        {"type": config_type},
        {"$set": {"type": config_type, "seconds": value}},
        upsert=True
    )

async def get_force_channels():
    channels = await db.force_channels.find().to_list(None)
    return channels

async def add_force_channel(chat_id, title, invite):
    count = await db.force_channels.count_documents({})
    await db.force_channels.insert_one({
        "id": count + 1,
        "chat_id": chat_id,
        "title": title,
        "invite": invite
    })

async def delete_force_channel(cid):
    await db.force_channels.delete_one({"id": int(cid)})

async def get_custom_text(key):
    text_doc = await db.custom_texts.find_one({"key": key})
    if text_doc:
        return {
            "text": text_doc.get("text", ""),
            "photo_id": text_doc.get("photo_id"),
            "sticker_id": text_doc.get("sticker_id"),
            "animation_id": text_doc.get("animation_id")
        }
    return {"text": "", "photo_id": None, "sticker_id": None, "animation_id": None}

async def set_custom_text(key, text=None, photo_id=None, sticker_id=None, animation_id=None):
    await db.custom_texts.update_one(
        {"key": key},
        {
            "$set": {
                "key": key,
                "text": text or "",
                "photo_id": photo_id,
                "sticker_id": sticker_id,
                "animation_id": animation_id
            }
        },
        upsert=True
    )

async def get_start_welcome():
    welcome_list = await db.start_welcome.find().to_list(None)
    if not welcome_list:
        return [{
            "text": "👋 **Welcome to Movie Bot!**\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။",
            "photo_id": None,
            "caption": ""
        }]
    return welcome_list

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
    count = await db.start_welcome.count_documents({})
    await db.start_welcome.insert_one({
        "id": count + 1,
        "text": text or "👋 **Welcome to Movie Bot!**",
        "photo_id": photo_id,
        "caption": caption or ""
    })

async def delete_start_welcome(index):
    welcome_list = await get_start_welcome()
    if 0 <= index < len(welcome_list):
        item = welcome_list[index]
        if "_id" in item:
            await db.start_welcome.delete_one({"_id": item["_id"]})
            return True
    return False

async def get_start_welcome_count():
    return await db.start_welcome.count_documents({})

async def get_start_buttons():
    buttons = await db.start_buttons.find().to_list(None)
    return buttons

async def add_start_button(name, link, row=0, button_type="url", callback_data=None):
    count = await db.start_buttons.count_documents({})
    if row == 0:
        if count > 0:
            max_button = await db.start_buttons.find_one(sort=[("row", -1)])
            max_row = max_button.get("row", 0) if max_button else 0
            buttons_in_row = await db.start_buttons.count_documents({"row": max_row})
            if buttons_in_row >= 2:
                row = max_row + 1
            else:
                row = max_row
        else:
            row = 0

    await db.start_buttons.insert_one({
        "id": count + 1,
        "name": name,
        "link": link,
        "row": row,
        "type": button_type,
        "callback_data": callback_data
    })

async def update_start_button(btn_id, name=None, link=None, row=None, button_type=None, callback_data=None):
    update_dict = {}
    if name:
        update_dict["name"] = name
    if link:
        update_dict["link"] = link
    if row is not None:
        update_dict["row"] = row
    if button_type:
        update_dict["type"] = button_type
    if callback_data:
        update_dict["callback_data"] = callback_data

    if update_dict:
        await db.start_buttons.update_one(
            {"id": int(btn_id)},
            {"$set": update_dict}
        )

async def delete_start_button(btn_id):
    await db.start_buttons.delete_one({"id": int(btn_id)})

async def get_start_buttons_by_row():
    buttons = await get_start_buttons()
    rows = {}
    for btn in buttons:
        row = btn.get("row", 0)
        if row not in rows:
            rows[row] = []
        rows[row].append(btn)
    return rows

auto_delete_tasks: Dict[str, asyncio.Task] = {}

async def schedule_auto_delete(chat_type: str, chat_id: int, message_id: int, seconds: int):
    if seconds <= 0:
        return
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        print(f"Failed to delete message: {e}")

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

async def send_force_join(msg):
    channels = await get_force_channels()
    if not channels:
        return True

    kb = InlineKeyboardMarkup()
    for ch in channels:
        kb.add(InlineKeyboardButton(ch["title"], url=ch["invite"]))
    kb.add(InlineKeyboardButton("✅ Done ✅", callback_data="force_done"))

    force_text = await get_custom_text("forcemsg")
    formatted_text = parse_telegram_format(
        force_text.get("text") or "⚠️ **BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။**\n\nBOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။",
        msg.from_user.full_name,
        msg.from_user.get_mention(as_html=True)
    )

    force_msg = await msg.answer(
        formatted_text,
        reply_markup=kb,
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
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Search Movie"))
    kb.add(KeyboardButton("📋 Movie List"))
    if is_owner:
        kb.add(KeyboardButton("🛠 Admin Panel"))
        kb.add(KeyboardButton("📊 Statistics"))
    return kb

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    is_owner = msg.from_user.id == OWNER_ID
    user_id = msg.from_user.id
    display_name = msg.from_user.full_name
    user_mention = msg.from_user.get_mention(as_html=True)

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

    if not await check_force_join(msg.from_user.id):
        await send_force_join(msg)
        return

    await send_start_welcome(msg, is_owner)

    await msg.answer(
        "📌 **Main Menu**\n\nအောက်ပါခလုတ်များကိုသုံးပါ:",
        reply_markup=main_menu(is_owner),
        protect_content=True
    )

async def send_start_welcome(msg: types.Message, is_owner: bool):
    welcome_data = await get_next_welcome_photo()

    kb = InlineKeyboardMarkup(row_width=2)
    rows = await get_start_buttons_by_row()

    for row_num in sorted(rows.keys()):
        row_buttons = rows[row_num]
        buttons = []
        for btn in row_buttons[:2]:
            if btn.get("type") == "popup":
                buttons.append(InlineKeyboardButton(btn["name"], callback_data=btn.get("callback_data", f"popup_{btn['id']}")))
            else:
                buttons.append(InlineKeyboardButton(btn["name"], url=btn["link"]))
        if buttons:
            kb.row(*buttons)

    if is_owner:
        kb.add(InlineKeyboardButton("⚙️ Manage Start Buttons", callback_data="manage_start_buttons"))

    welcome_text = parse_telegram_format(
        welcome_data.get("caption") or welcome_data.get("text", "👋 Welcome!"),
        msg.from_user.full_name,
        msg.from_user.get_mention(as_html=True)
    )

    if welcome_data and welcome_data.get("photo_id"):
        await msg.answer_photo(
            photo=welcome_data["photo_id"],
            caption=welcome_text,
            reply_markup=kb,
            protect_content=True
        )
    else:
        await msg.answer(
            welcome_text,
            reply_markup=kb,
            protect_content=True
        )

class StartButtonManagement(StatesGroup):
    waiting_for_name = State()
    waiting_for_link = State()
    waiting_for_type = State()
    waiting_for_popup_text = State()
    waiting_for_edit_id = State()
    waiting_for_edit_name = State()
    waiting_for_edit_link = State()
    waiting_for_edit_row = State()

@dp.callback_query_handler(lambda c: c.data == "manage_start_buttons")
async def manage_start_buttons(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
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
    await StartButtonManagement.waiting_for_type.set()

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔗 URL Button", callback_data="btn_type_url"),
        InlineKeyboardButton("📢 Popup Button", callback_data="btn_type_popup")
    )
    await msg.answer("Button အမျိုးအစားရွေးပါ:", reply_markup=kb, protect_content=True)

@dp.callback_query_handler(lambda c: c.data.startswith("btn_type_"), state=StartButtonManagement.waiting_for_type)
async def add_start_button_type(call: types.CallbackQuery, state: FSMContext):
    btn_type = call.data.split("_")[2]
    await state.update_data(button_type=btn_type)

    if btn_type == "url":
        await StartButtonManagement.waiting_for_link.set()
        await call.message.answer("🔗 Button Link ထည့်ပါ (https://t.me/... or https://...):", protect_content=True)
    else:
        await StartButtonManagement.waiting_for_popup_text.set()
        await call.message.answer("📝 Popup စာသားထည့်ပါ:", protect_content=True)
    await call.answer()

@dp.message_handler(state=StartButtonManagement.waiting_for_link)
async def add_start_button_link(msg: types.Message, state: FSMContext):
    if not msg.text.startswith(('http://', 'https://')):
        return await msg.answer("❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။", protect_content=True)

    data = await state.get_data()
    await add_start_button(data['name'], msg.text, button_type="url")
    await msg.answer(f"✅ Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    await state.finish()

@dp.message_handler(state=StartButtonManagement.waiting_for_popup_text)
async def add_start_button_popup(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    callback_data = f"popup_{msg.text[:20]}"
    await add_start_button(data['name'], msg.text, button_type="popup", callback_data=callback_data)
    await msg.answer(f"✅ Popup Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("popup_"))
async def handle_popup_button(call: types.CallbackQuery):
    buttons = await get_start_buttons()
    for btn in buttons:
        if btn.get("callback_data") == call.data:
            await call.answer(btn.get("link", ""), show_alert=True)
            return
    await call.answer("Popup text not found", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "delete_start_button")
async def delete_start_button_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
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
    await delete_start_button(btn_id)
    await call.answer("✅ Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    await manage_start_buttons(call)

class StartWelcomeManagement(StatesGroup):
    waiting_for_photo = State()
    waiting_for_delete_index = State()

@dp.callback_query_handler(lambda c: c.data == "manage_start_welcome")
async def manage_start_welcome(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
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
    await call.answer()

@dp.message_handler(state=StartWelcomeManagement.waiting_for_photo, content_types=['photo'])
async def add_welcome_photo_done(msg: types.Message, state: FSMContext):
    photo_id = msg.photo[-1].file_id
    caption = msg.caption or ""
    await add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
    count = await get_start_welcome_count()
    await msg.answer(f"✅ Welcome Photo ထည့်ပြီးပါပြီ။\n📸 စုစုပေါင်းပုံ: {count} ပုံ", protect_content=True)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "add_welcome_text")
async def add_welcome_text_start(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await StartWelcomeManagement.waiting_for_photo.set()
    await call.message.answer(
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
    await call.answer()

@dp.message_handler(state=StartWelcomeManagement.waiting_for_photo, content_types=['text'])
async def add_welcome_text_done(msg: types.Message, state: FSMContext):
    if msg.text == '/cancel':
        await msg.answer("❌ Cancelled", protect_content=True)
        await state.finish()
        return

    await add_start_welcome(text=msg.text)
    count = await get_start_welcome_count()
    await msg.answer(f"✅ Welcome Text ထည့်ပြီးပါပြီ။\n📝 စုစုပေါင်း: {count} ခု", protect_content=True)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "delete_welcome_item")
async def delete_welcome_item_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
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
    if await delete_start_welcome(index):
        await call.answer("✅ ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        await call.answer("❌ ဖျက်လို့မရပါ။", show_alert=True)

    await manage_start_welcome(call)

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

class AddAd(StatesGroup):
    msgid = State()
    chatid = State()

@dp.callback_query_handler(lambda c: c.data == "ads_manager")
async def ads_manager(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    ads = await get_ads()
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
    await add_ad(data["msgid"], chatid)
    await msg.answer("✅ Ad added successfully!", protect_content=True)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("delad_"))
async def del_ad_process(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    aid = call.data.split("_")[1]
    await delete_ad(aid)
    await call.answer("✅ Ad deleted", show_alert=True)
    await ads_manager(call)

@dp.message_handler(lambda m: m.text == "🛠 Admin Panel")
async def admin_panel(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer("🛠 Admin Panel", reply_markup=admin_menu(), protect_content=True)

@dp.message_handler(lambda m: m.text == "📊 Statistics")
async def statistics_panel(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
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

    await msg.answer(text, protect_content=True)

@dp.callback_query_handler(lambda c: c.data == "back")
async def back(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer("Menu:", reply_markup=main_menu(call.from_user.id == OWNER_ID), protect_content=True)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_start")
async def back_to_start(call: types.CallbackQuery):
    await call.message.delete()
    await send_start_welcome(call.message, call.from_user.id == OWNER_ID)

@dp.callback_query_handler(lambda c: c.data == "auto_delete")
async def auto_delete_menu(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    config = await get_auto_delete_config()
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
    delete_type = "group" if "group" in call.data else "dm"

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

    await set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        await call.answer(f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        await call.answer(f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    await auto_delete_menu(call)

@dp.callback_query_handler(lambda c: c.data == "disable_auto_delete")
async def disable_all_auto_delete(call: types.CallbackQuery):
    await set_auto_delete_config("group", 0)
    await set_auto_delete_config("dm", 0)
    await call.answer("All auto-delete disabled!", show_alert=True)
    await auto_delete_menu(call)

@dp.callback_query_handler(lambda c: c.data == "back_admin")
async def back_admin(call: types.CallbackQuery):
    await call.message.edit_text("🛠 Admin Panel", reply_markup=admin_menu())

@dp.callback_query_handler(lambda c: c.data == "clear_all_data")
async def clear_all_data_confirm(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm Clear All", callback_data="confirm_clear_all"))
    kb.add(InlineKeyboardButton("⬅ Back", callback_data="back_admin"))
    await call.message.edit_text("⚠️ <b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings from MongoDB.", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "confirm_clear_all")
async def process_clear_all_data(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    await db.movies.delete_many({})
    await db.users.delete_many({})
    await db.ads.delete_many({})
    await db.settings.delete_many({})
    await db.force_channels.delete_many({})
    await db.custom_texts.delete_many({})
    await db.auto_delete.delete_many({})
    await db.start_buttons.delete_many({})
    await db.start_welcome.delete_many({})

    await reload_movies_cache()

    await call.message.edit_text("✅ All data has been cleared from MongoDB!", reply_markup=admin_menu())
    await call.answer("Data cleared", show_alert=True)

@dp.callback_query_handler(lambda c: c.data == "force")
async def force(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    channels = await get_force_channels()
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

    await add_force_channel(chat_id, chat.title, invite)

    await msg.answer(f"✅ Added: {chat.title}", protect_content=True)

@dp.callback_query_handler(lambda c: c.data.startswith("delch_"))
async def delch(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    cid = call.data.split("_")[1]
    await delete_force_channel(cid)
    await call.answer("✅ Deleted", show_alert=True)

    await force(call)

@dp.callback_query_handler(lambda c: c.data == "force_done")
async def force_done(call: types.CallbackQuery):
    ok = await check_force_join(call.from_user.id)

    if not ok:
        await call.answer(
"❌ Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
"ကျေးဇူးပြု၍ သတ်မှတ်ထားသော Channel များအားလုံးကို အရင် Join လုပ်ပါ။\n"
"ပြီးရင် 'Done' ကို နှိပ်ပါ။",
show_alert=True
        )
        return

    await call.answer("joinပေးတဲ့အတွက်ကျေးဇူးတင်ပါတယ်!", show_alert=True)
    await call.message.delete()
    await send_start_welcome(call.message, call.from_user.id == OWNER_ID)

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
        await call.message.answer(
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
        await call.message.answer(
            f"'{key}' အတွက် စာအသစ်ပို့ပေးပါ (Photo ပါရင် Photo နဲ့အတူ Caption ထည့်ပေးပါ)" +
            formatting_guide,
            protect_content=True
        )

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
        await set_custom_text(key, text=msg.text)
        await msg.answer(f"✅ {key} text updated successfully", protect_content=True)

    elif msg.content_type == 'photo':
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        await set_custom_text(key, text=caption, photo_id=photo_id)
        await msg.answer(f"✅ {key} photo updated successfully", protect_content=True)

    elif msg.content_type == 'sticker':
        sticker_id = msg.sticker.file_id
        await set_custom_text(key, sticker_id=sticker_id)
        await msg.answer(f"✅ {key} sticker updated successfully", protect_content=True)

    elif msg.content_type == 'animation':
        animation_id = msg.animation.file_id
        caption = msg.caption or ""
        await set_custom_text(key, text=caption, animation_id=animation_id)
        await msg.answer(f"✅ {key} GIF updated successfully", protect_content=True)

    else:
        await msg.answer("❌ Unsupported content type", protect_content=True)

    await state.finish()

@dp.message_handler(lambda m: m.text == "📋 Movie List")
async def movie_list_redirect(msg: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎬 Movie + Code ကြည့်ရန်", url="https://t.me/seatvmmmovielist"))
    await msg.answer("📌 ရှိတဲ့ Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ", reply_markup=kb, protect_content=True)

@dp.callback_query_handler(lambda c: c.data == "maint")
async def maint(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    current = await is_maintenance()
    new = "off" if current else "on"
    await set_setting("maint", new)
    await call.answer(f"Maintenance: {new.upper()}", show_alert=True)

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
    await add_movie_record(data["name"], data["code"], data["msgid"], chatid)

    await msg.answer(f"✅ ဇာတ်ကားထည့်ပြီးပါပြီ!\n\nနာမည်: {data['name']}\nCode: {data['code']}", protect_content=True)
    await state.finish()

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
    await delete_movie(code)
    await msg.answer(f"✅ Code `{code}` ဖျက်ပြီးပါပြီ။", protect_content=True)
    await state.finish()

class Broadcast(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()
    confirm = State()

@dp.callback_query_handler(lambda c: c.data == "broadcast")
async def bc(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await Broadcast.waiting_content.set()
    await call.message.answer(
        "📢 Broadcast စာသား/ပုံ ပို့ပါ။\n\n"
        "📝 Formatting supported:\n"
        "• **bold**, *italic*, __underline__\n"
        "• {mention}, {name} - placeholders\n\n"
        "Photo/Video/GIF ပါ ပို့လို့ရပါတယ်။",
        protect_content=True
    )
    await call.answer()

@dp.message_handler(state=Broadcast.waiting_content, content_types=types.ContentTypes.ANY)
async def bc_content(msg: types.Message, state: FSMContext):
    content_type = msg.content_type

    if content_type == "text":
        await state.update_data(text=msg.text, content_type="text")
    elif content_type == "photo":
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        await state.update_data(photo_id=photo_id, caption=caption, content_type="photo")
    elif content_type == "video":
        video_id = msg.video.file_id
        caption = msg.caption or ""
        await state.update_data(video_id=video_id, caption=caption, content_type="video")
    elif content_type == "animation":
        animation_id = msg.animation.file_id
        caption = msg.caption or ""
        await state.update_data(animation_id=animation_id, caption=caption, content_type="animation")
    else:
        return await msg.answer("❌ Unsupported content type", protect_content=True)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ ပြန်ဖြစ်ရင်ပဲပို့မယ်", callback_data="bc_no_buttons"))
    kb.add(InlineKeyboardButton("➕ Buttons ထည့်မယ်", callback_data="bc_add_buttons"))

    await msg.answer("Buttons ထည့်မလား?", reply_markup=kb, protect_content=True)

@dp.callback_query_handler(lambda c: c.data == "bc_no_buttons", state=Broadcast.waiting_content)
async def bc_no_buttons(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(buttons=[])
    await confirm_broadcast(call, state)

@dp.callback_query_handler(lambda c: c.data == "bc_add_buttons", state=Broadcast.waiting_content)
async def bc_add_buttons_start(call: types.CallbackQuery, state: FSMContext):
    await Broadcast.waiting_buttons.set()
    await call.message.answer(
        "📝 Buttons ထည့်ရန်:\n\n"
        "Format: Button Name | URL\n"
        "Example:\n"
        "Channel | https://t.me/yourchannel\n"
        "Group | https://t.me/yourgroup\n\n"
        "တစ်ကြောင်းကို button တစ်ခု၊ ပြီးရင် ပို့ပါ။\n"
        "ပြီးသွားရင် /done ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@dp.message_handler(state=Broadcast.waiting_buttons)
async def bc_buttons_collect(msg: types.Message, state: FSMContext):
    if msg.text == "/done":
        data = await state.get_data()
        if not data.get("buttons"):
            await state.update_data(buttons=[])
        await Broadcast.confirm.set()
        await confirm_broadcast_message(msg, state)
        return

    if "|" not in msg.text:
        return await msg.answer("❌ Format မမှန်ပါ။ Button Name | URL အဖြစ်ထည့်ပါ။", protect_content=True)

    parts = msg.text.split("|")
    if len(parts) != 2:
        return await msg.answer("❌ Format မမှန်ပါ။", protect_content=True)

    name = parts[0].strip()
    url = parts[1].strip()

    if not url.startswith(("http://", "https://")):
        return await msg.answer("❌ URL မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    buttons = data.get("buttons", [])
    buttons.append({"name": name, "url": url})
    await state.update_data(buttons=buttons)

    await msg.answer(f"✅ Button '{name}' ထည့်ပြီး။\nထပ်ထည့်မယ်ဆိုရင် ဆက်ပို့ပါ။\nပြီးရင် /done ရိုက်ပါ။", protect_content=True)

async def confirm_broadcast(call: types.CallbackQuery, state: FSMContext):
    await Broadcast.confirm.set()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"))

    await call.message.answer("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=kb, protect_content=True)

async def confirm_broadcast_message(msg: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"))

    await msg.answer("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=kb, protect_content=True)

@dp.callback_query_handler(lambda c: c.data == "bc_confirm", state=Broadcast.confirm)
async def bc_confirm(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    users = await get_users()

    buttons = data.get("buttons", [])
    kb = None
    if buttons:
        kb = InlineKeyboardMarkup(row_width=1)
        for btn in buttons:
            kb.add(InlineKeyboardButton(btn["name"], url=btn["url"]))

    sent = 0
    failed = 0

    status_msg = await call.message.answer(f"📢 Broadcasting... 0/{len(users)}", protect_content=True)

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
    await state.finish()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "bc_cancel", state="*")
async def bc_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await call.message.answer("❌ Broadcast cancelled", protect_content=True)
    await call.answer()

@dp.message_handler(commands=["os"])
async def os_command(msg: types.Message):
    if msg.chat.type not in ["group", "supergroup"]:
        await msg.answer("This command can only be used in groups!", protect_content=True)
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = await msg.reply(
        "**owner-@osamu1123**\n\n"
        "• Bot Status: ✅ Online\n"
        "• Queue System: 🟢 Active (Batch: 30)\n"
        "• Auto-Delete: " + ("✅ " + str(group_sec) + "s" if group_sec > 0 else "❌ Disabled") + "\n"
        "• Version: 4.0 (MongoDB)\n\n"
        "Use /os name command.",
        protect_content=True
    )

    if group_sec > 0:
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, response.message_id, group_sec))
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

@dp.message_handler()
async def search(msg: types.Message):
    if msg.text == "🔍 Search Movie":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎬 Movie + Code ကြည့်ရန်", url="https://t.me/seatvmmmovielist"))
        return await msg.answer("🔍 <b>ဇာတ်ကား Code ပို့ပေးပါ</b>", reply_markup=kb, protect_content=True)

    if msg.text.startswith("/"):
        return

    if await is_maintenance() and msg.from_user.id != OWNER_ID:
        return await msg.answer("🛠 Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။", protect_content=True)

    if not await check_force_join(msg.from_user.id):
        sent = await send_force_join(msg)
        if sent is False:
            return

    if msg.from_user.id != OWNER_ID:
        last = await get_user_last(msg.from_user.id)
        if last:
            diff = datetime.now() - datetime.fromisoformat(last)
            if diff.total_seconds() < COOLDOWN:
                remain = int(COOLDOWN - diff.total_seconds())
                return await msg.answer(f"⏳ ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်", protect_content=True)

    code = msg.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        return await msg.answer(f"❌ Code `{code}` မရှိပါ။\n\n🔍 Search Movie နှိပ်ပြီး Code စစ်ပါ။", protect_content=True)

    global ACTIVE_USERS

    async with BATCH_LOCK:
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

            await asyncio.sleep(5)
            await safe_delete_message(msg.chat.id, queue_msg.message_id)
            return

        ACTIVE_USERS += 1

    try:
        await update_user_search(msg.from_user.id)
        USER_PROCESSING_TIME[msg.from_user.id] = datetime.now()

        ads = await get_ads()
        if ads:
            idx = await get_next_ad_index()
            if idx is not None and idx < len(ads):
                ad = ads[idx]
                try:
                    ad_sent = await bot.copy_message(
                        chat_id=msg.from_user.id,
                        from_chat_id=ad["storage_chat_id"],
                        message_id=ad["message_id"],
                        protect_content=True
                    )
                    asyncio.create_task(schedule_auto_delete("dm", msg.from_user.id, ad_sent.message_id, 10))
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"Error sending ad: {e}")

        searching_msg_id = await send_searching_overlay(msg.from_user.id)

        sent = await bot.copy_message(
            chat_id=msg.from_user.id,
            from_chat_id=movie["storage_chat_id"],
            message_id=movie["message_id"],
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⚜️Owner⚜️", url="https://t.me/osamu1123")
            ),
            protect_content=True
        )

        if searching_msg_id:
            await safe_delete_message(msg.from_user.id, searching_msg_id)

        config = await get_auto_delete_config()
        dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)
        if dm_sec > 0:
            asyncio.create_task(schedule_auto_delete("dm", msg.from_user.id, sent.message_id, dm_sec))

    except Exception as e:
        print(f"Error sending movie: {e}")
        await msg.answer("❌ Error sending movie. Please try again.", protect_content=True)
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

@dp.callback_query_handler(lambda c: c.data == "backup")
async def backup_db(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    data = {
        "movies": await get_movies(),
        "users": await get_users(),
        "settings": await db.settings.find().to_list(None),
        "force_channels": await get_force_channels(),
        "auto_delete": await get_auto_delete_config(),
        "custom_texts": await db.custom_texts.find().to_list(None),
        "start_buttons": await get_start_buttons(),
        "start_welcome": await get_start_welcome(),
        "ads": await get_ads()
    }

    for key in data:
        if data[key]:
            for item in data[key]:
                if "_id" in item:
                    item["_id"] = str(item["_id"])

    with open("backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    await bot.send_document(
        OWNER_ID,
        InputFile("backup.json"),
        caption="📥 MongoDB Backup File",
        protect_content=True
    )

    await call.answer("Backup sent!", show_alert=True)

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

        await db.movies.delete_many({})
        await db.users.delete_many({})
        await db.settings.delete_many({})
        await db.force_channels.delete_many({})
        await db.auto_delete.delete_many({})
        await db.custom_texts.delete_many({})
        await db.start_buttons.delete_many({})
        await db.start_welcome.delete_many({})
        await db.ads.delete_many({})

        if data.get("movies"):
            for item in data["movies"]:
                if "_id" in item:
                    del item["_id"]
            await db.movies.insert_many(data["movies"])

        if data.get("users"):
            for item in data["users"]:
                if "_id" in item:
                    del item["_id"]
            await db.users.insert_many(data["users"])

        if data.get("settings"):
            for item in data["settings"]:
                if "_id" in item:
                    del item["_id"]
            await db.settings.insert_many(data["settings"])

        if data.get("force_channels"):
            for item in data["force_channels"]:
                if "_id" in item:
                    del item["_id"]
            await db.force_channels.insert_many(data["force_channels"])

        if data.get("auto_delete"):
            for item in data["auto_delete"]:
                if "_id" in item:
                    del item["_id"]
            await db.auto_delete.insert_many(data["auto_delete"])

        if data.get("custom_texts"):
            for item in data["custom_texts"]:
                if "_id" in item:
                    del item["_id"]
            await db.custom_texts.insert_many(data["custom_texts"])

        if data.get("start_buttons"):
            for item in data["start_buttons"]:
                if "_id" in item:
                    del item["_id"]
            await db.start_buttons.insert_many(data["start_buttons"])

        if data.get("start_welcome"):
            for item in data["start_welcome"]:
                if "_id" in item:
                    del item["_id"]
            await db.start_welcome.insert_many(data["start_welcome"])

        if data.get("ads"):
            for item in data["ads"]:
                if "_id" in item:
                    del item["_id"]
            await db.ads.insert_many(data["ads"])

        await reload_movies_cache()
        await msg.answer("✅ Restore Completed from MongoDB backup!", protect_content=True)
    except Exception as e:
        await msg.answer(f"❌ Restore Failed: {str(e)}", protect_content=True)

@dp.message_handler(content_types=ContentType.ANY, chat_type=["group", "supergroup"])
async def group_message_handler(msg: types.Message):
    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not msg.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

async def migrate_json_to_mongodb():
    print("🔄 Migrating JSON data to MongoDB...")

    try:
        movies = load_json("movies")
        if movies and await db.movies.count_documents({}) == 0:
            await db.movies.insert_many(movies)
            print(f"✅ Migrated {len(movies)} movies")

        users = load_json("users")
        if users and await db.users.count_documents({}) == 0:
            for user in users:
                if "search_count" not in user:
                    user["search_count"] = 0
            await db.users.insert_many(users)
            print(f"✅ Migrated {len(users)} users")

        settings = load_json("settings")
        if settings and await db.settings.count_documents({}) == 0:
            await db.settings.insert_many(settings)
            print(f"✅ Migrated {len(settings)} settings")

        force_channels = load_json("force_channels")
        if force_channels and await db.force_channels.count_documents({}) == 0:
            await db.force_channels.insert_many(force_channels)
            print(f"✅ Migrated {len(force_channels)} force channels")

        auto_delete = load_json("auto_delete")
        if auto_delete and await db.auto_delete.count_documents({}) == 0:
            await db.auto_delete.insert_many(auto_delete)
            print(f"✅ Migrated {len(auto_delete)} auto delete configs")

        custom_texts = load_json("custom_texts")
        if custom_texts and await db.custom_texts.count_documents({}) == 0:
            await db.custom_texts.insert_many(custom_texts)
            print(f"✅ Migrated {len(custom_texts)} custom texts")

        start_buttons = load_json("start_buttons")
        if start_buttons and await db.start_buttons.count_documents({}) == 0:
            await db.start_buttons.insert_many(start_buttons)
            print(f"✅ Migrated {len(start_buttons)} start buttons")

        start_welcome = load_json("start_welcome")
        if start_welcome and await db.start_welcome.count_documents({}) == 0:
            await db.start_welcome.insert_many(start_welcome)
            print(f"✅ Migrated {len(start_welcome)} welcome messages")

        ads = load_json("ads")
        if ads and await db.ads.count_documents({}) == 0:
            await db.ads.insert_many(ads)
            print(f"✅ Migrated {len(ads)} ads")

        print("✅ Migration completed!")
    except Exception as e:
        print(f"⚠️ Migration error: {e}")

async def on_startup(dp):
    await migrate_json_to_mongodb()
    await load_movies_cache()
    asyncio.create_task(batch_worker())
    print("✅ Bot started with MongoDB + All Features")
    print(f"✅ Movies in cache: {len(MOVIES_DICT)}")
    print(f"✅ Batch size: {BATCH_SIZE}")

    welcome_count = await get_start_welcome_count()
    print(f"✅ Welcome photos: {welcome_count}")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
