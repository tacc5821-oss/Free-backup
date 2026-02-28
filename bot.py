import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, ContentType, ButtonColor
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

COOLDOWN = 90
BATCH_SIZE = 30
AUTO_DELETE_OPTIONS = [5, 10, 30]

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

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

async def get_movies():
    return load_json("movies")

async def load_movies_cache():
    global MOVIES_DICT
    movies = await get_movies()
    MOVIES_DICT = {}
    for m in movies:
        if m.get("movie_code"):
            MOVIES_DICT[m["movie_code"].upper()] = m
    print(f"Loaded {len(MOVIES_DICT)} movies to cache")

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

async def get_start_welcome():
    welcome = load_json("start_welcome")
    if not welcome:
        return [{
            "text": "Welcome to Movie Bot!\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။",
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
        "text": text or "Welcome to Movie Bot!",
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

async def get_start_buttons():
    return load_json("start_buttons")

async def add_start_button(name, link, row=0, button_type="url", callback_data=None, color=None):
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
        "callback_data": callback_data,
        "color": color
    })
    save_json("start_buttons", buttons)

async def update_start_button(btn_id, name=None, link=None, row=None, button_type=None, callback_data=None, color=None):
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
            if color:
                b["color"] = color
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

async def send_force_join(msg: Message):
    channels = await get_force_channels()
    if not channels:
        return True

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for ch in channels:
        kb.inline_keyboard.append([InlineKeyboardButton(text=ch["title"], url=ch["invite"])])
    kb.inline_keyboard.append([InlineKeyboardButton(
        text="Done",
        callback_data="force_done",
        color=ButtonColor.SUCCESS
    )])

    force_text = await get_custom_text("forcemsg")
    formatted_text = parse_telegram_format(
        force_text.get("text") or "BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။\n\nBOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။",
        msg.from_user.full_name,
        f'<a href="tg://user?id={msg.from_user.id}">{msg.from_user.full_name}</a>'
    )

    await msg.answer(
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
            text = overlay.get("text", "ရှာဖွေနေပါသည်...")
            msg = await bot.send_message(chat_id, text, protect_content=True)
        return msg.message_id
    except Exception as e:
        print(f"Error sending overlay: {e}")
        try:
            msg = await bot.send_message(chat_id, "ရှာဖွေနေပါသည်...", protect_content=True)
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
            [KeyboardButton(text="Search Movie")],
            [KeyboardButton(text="Movie List")]
        ],
        resize_keyboard=True
    )
    if is_owner:
        kb.keyboard.append([KeyboardButton(text="Admin Panel")])
        kb.keyboard.append([KeyboardButton(text="Statistics")])
    return kb

@router.message(Command("start"))
async def start(msg: Message):
    is_owner = msg.from_user.id == OWNER_ID
    user_id = msg.from_user.id
    display_name = msg.from_user.full_name
    user_mention = f'<a href="tg://user?id={user_id}">{display_name}</a>'

    is_new = await add_new_user(user_id, display_name, user_mention)

    if is_new:
        total_users = await get_user_count()

        notification_text = (
            f"<b>New User Notification</b>\n\n"
            f"<b>Total Users:</b> {total_users}\n"
            f"<b>ID:</b> <code>{user_id}</code>\n"
            f"<b>Name:</b> {display_name}\n"
            f"<b>Mention:</b> {user_mention}"
        )
        try:
            await bot.send_message(OWNER_ID, notification_text, protect_content=True, parse_mode="HTML")
        except Exception as e:
            print(f"Failed to notify owner: {e}")

    if not await check_force_join(msg.from_user.id):
        await send_force_join(msg)
        return

    await send_start_welcome(msg, is_owner)

    await msg.answer(
        "Main Menu",
        reply_markup=main_menu(is_owner),
        protect_content=True
    )

async def send_start_welcome(msg: Message, is_owner: bool):
    welcome_data = await get_next_welcome_photo()

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    rows = await get_start_buttons_by_row()

    for row_num in sorted(rows.keys()):
        row_buttons = rows[row_num]
        button_row = []
        for btn in row_buttons[:2]:
            color_map = {
                "danger": ButtonColor.DANGER,
                "success": ButtonColor.SUCCESS,
                "primary": ButtonColor.PRIMARY
            }
            btn_color = color_map.get(btn.get("color"))

            if btn.get("type") == "popup":
                button_row.append(InlineKeyboardButton(
                    text=btn["name"],
                    callback_data=btn.get("callback_data", f"popup_{btn['id']}"),
                    color=btn_color
                ))
            else:
                button_row.append(InlineKeyboardButton(
                    text=btn["name"],
                    url=btn["link"],
                    color=btn_color
                ))
        if button_row:
            kb.inline_keyboard.append(button_row)

    if is_owner:
        kb.inline_keyboard.append([InlineKeyboardButton(
            text="Manage Start Buttons",
            callback_data="manage_start_buttons",
            color=ButtonColor.PRIMARY
        )])

    welcome_text = parse_telegram_format(
        welcome_data.get("caption") or welcome_data.get("text", "Welcome!"),
        msg.from_user.full_name,
        f'<a href="tg://user?id={msg.from_user.id}">{msg.from_user.full_name}</a>'
    )

    if welcome_data and welcome_data.get("photo_id"):
        try:
            await msg.answer_photo(
                photo=welcome_data["photo_id"],
                caption=welcome_text,
                reply_markup=kb,
                protect_content=True,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Error sending welcome photo: {e}")
            await msg.answer(
                welcome_text,
                reply_markup=kb,
                protect_content=True,
                parse_mode="HTML"
            )
    else:
        await msg.answer(
            welcome_text,
            reply_markup=kb,
            protect_content=True,
            parse_mode="HTML"
        )

class StartButtonManagement(StatesGroup):
    waiting_for_name = State()
    waiting_for_link = State()
    waiting_for_type = State()
    waiting_for_popup_text = State()
    waiting_for_color = State()
    waiting_for_edit_id = State()
    waiting_for_edit_name = State()
    waiting_for_edit_link = State()
    waiting_for_edit_row = State()

@router.callback_query(F.data == "manage_start_buttons")
async def manage_start_buttons(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
    text = "Start Buttons Management\n\n"

    if not buttons:
        text += "Buttons မရှိသေးပါ။\n"
    else:
        rows = await get_start_buttons_by_row()
        for row_num in sorted(rows.keys()):
            text += f"\nRow {row_num + 1}:\n"
            for btn in rows[row_num]:
                btn_type = btn.get("type", "url")
                btn_color = btn.get("color", "default")
                text += f"   • ID: {btn['id']} | {btn['name']} ({btn_type}) - Color: {btn_color}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Add Button", callback_data="add_start_button", color=ButtonColor.SUCCESS),
            InlineKeyboardButton(text="Edit Button", callback_data="edit_start_button")
        ],
        [
            InlineKeyboardButton(text="Delete Button", callback_data="delete_start_button", color=ButtonColor.DANGER),
            InlineKeyboardButton(text="Manage Welcome", callback_data="manage_start_welcome")
        ],
        [InlineKeyboardButton(text="Back", callback_data="back_to_start")]
    ])

    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "add_start_button")
async def add_start_button_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(StartButtonManagement.waiting_for_name)
    await call.message.answer("Button နာမည်ထည့်ပါ:", protect_content=True)
    await call.answer()

@router.message(StartButtonManagement.waiting_for_name)
async def add_start_button_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await state.set_state(StartButtonManagement.waiting_for_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="URL Button", callback_data="btn_type_url"),
            InlineKeyboardButton(text="Popup Button", callback_data="btn_type_popup")
        ]
    ])
    await msg.answer("Button အမျိုးအစားရွေးပါ:", reply_markup=kb, protect_content=True)

@router.callback_query(F.data.startswith("btn_type_"), StartButtonManagement.waiting_for_type)
async def add_start_button_type(call: CallbackQuery, state: FSMContext):
    btn_type = call.data.split("_")[2]
    await state.update_data(button_type=btn_type)

    if btn_type == "url":
        await state.set_state(StartButtonManagement.waiting_for_link)
        await call.message.answer("Button Link ထည့်ပါ (https://t.me/... or https://...):", protect_content=True)
    else:
        await state.set_state(StartButtonManagement.waiting_for_popup_text)
        await call.message.answer("Popup စာသားထည့်ပါ:", protect_content=True)
    await call.answer()

@router.message(StartButtonManagement.waiting_for_link)
async def add_start_button_link(msg: Message, state: FSMContext):
    if not msg.text.startswith(('http://', 'https://')):
        return await msg.answer("Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။", protect_content=True)

    await state.update_data(link=msg.text)
    await state.set_state(StartButtonManagement.waiting_for_color)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Red", callback_data="btn_color_danger", color=ButtonColor.DANGER),
            InlineKeyboardButton(text="Green", callback_data="btn_color_success", color=ButtonColor.SUCCESS),
            InlineKeyboardButton(text="Blue", callback_data="btn_color_primary", color=ButtonColor.PRIMARY)
        ],
        [InlineKeyboardButton(text="No Color", callback_data="btn_color_none")]
    ])
    await msg.answer("Button အရောင်ရွေးပါ:", reply_markup=kb, protect_content=True)

@router.message(StartButtonManagement.waiting_for_popup_text)
async def add_start_button_popup(msg: Message, state: FSMContext):
    await state.update_data(link=msg.text)
    await state.set_state(StartButtonManagement.waiting_for_color)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Red", callback_data="btn_color_danger", color=ButtonColor.DANGER),
            InlineKeyboardButton(text="Green", callback_data="btn_color_success", color=ButtonColor.SUCCESS),
            InlineKeyboardButton(text="Blue", callback_data="btn_color_primary", color=ButtonColor.PRIMARY)
        ],
        [InlineKeyboardButton(text="No Color", callback_data="btn_color_none")]
    ])
    await msg.answer("Button အရောင်ရွေးပါ:", reply_markup=kb, protect_content=True)

@router.callback_query(F.data.startswith("btn_color_"), StartButtonManagement.waiting_for_color)
async def add_start_button_color(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[2]
    if color == "none":
        color = None

    data = await state.get_data()

    if data.get("button_type") == "url":
        await add_start_button(data['name'], data['link'], button_type="url", color=color)
        await call.message.answer(f"Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)
    else:
        callback_data = f"popup_{data['name'][:20]}"
        await add_start_button(data['name'], data['link'], button_type="popup", callback_data=callback_data, color=color)
        await call.message.answer(f"Popup Button '{data['name']}' ထည့်ပြီးပါပြီ။", protect_content=True)

    await state.clear()
    await call.answer()

@router.callback_query(F.data.startswith("popup_"))
async def handle_popup_button(call: CallbackQuery):
    buttons = await get_start_buttons()
    for btn in buttons:
        if btn.get("callback_data") == call.data:
            await call.answer(btn.get("link", ""), show_alert=True)
            return
    await call.answer("Popup text not found", show_alert=True)

@router.callback_query(F.data == "delete_start_button")
async def delete_start_button_list(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
    if not buttons:
        await call.answer("Button မရှိပါ။", show_alert=True)
        return

    kb_buttons = []
    for btn in buttons:
        kb_buttons.append([InlineKeyboardButton(
            text=f"{btn['name']} (Row {btn.get('row', 0)+1})",
            callback_data=f"delstartbtn_{btn['id']}",
            color=ButtonColor.DANGER
        )])
    kb_buttons.append([InlineKeyboardButton(text="Back", callback_data="manage_start_buttons")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await call.message.edit_text("ဖျက်မည့် Button ကိုရွေးပါ:", reply_markup=kb)

@router.callback_query(F.data.startswith("delstartbtn_"))
async def delete_start_button_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    btn_id = call.data.split("_")[1]
    await delete_start_button(btn_id)
    await call.answer("Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    await manage_start_buttons(call)

class StartWelcomeManagement(StatesGroup):
    waiting_for_photo = State()
    waiting_for_delete_index = State()

@router.callback_query(F.data == "manage_start_welcome")
async def manage_start_welcome(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
    text = f"Start Welcome Management\n\n"
    text += f"စုစုပေါင်းပုံ: {len(welcome_list)} ပုံ\n\n"

    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            text += f"{i+1}. Photo - {w.get('caption', 'No caption')[:30]}\n"
        else:
            text += f"{i+1}. Text - {w.get('text', '')[:30]}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Add Photo", callback_data="add_welcome_photo", color=ButtonColor.SUCCESS),
            InlineKeyboardButton(text="Add Text", callback_data="add_welcome_text", color=ButtonColor.SUCCESS)
        ],
        [
            InlineKeyboardButton(text="Delete", callback_data="delete_welcome_item", color=ButtonColor.DANGER),
            InlineKeyboardButton(text="Back", callback_data="manage_start_buttons")
        ]
    ])

    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "add_welcome_photo")
async def add_welcome_photo_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(StartWelcomeManagement.waiting_for_photo)
    await call.message.answer(
        "Welcome Photo ထည့်ရန် Photo ပို့ပါ။\n"
        "Caption ပါထည့်ချင်ရင် Photo နဲ့အတူ Caption ရေးပို့ပါ။\n\n"
        "Formatting:\n"
        "• bold text - စာလုံးမဲအတွက်\n"
        "• italic text - စာလုံးစောင်းအတွက်\n"
        "• underline - မျဉ်းသားအတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@router.message(StartWelcomeManagement.waiting_for_photo, F.photo)
async def add_welcome_photo_done(msg: Message, state: FSMContext):
    photo_id = msg.photo[-1].file_id
    caption = msg.caption or ""
    await add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
    count = await get_start_welcome_count()
    await msg.answer(f"Welcome Photo ထည့်ပြီးပါပြီ။\nစုစုပေါင်းပုံ: {count} ပုံ", protect_content=True)
    await state.clear()

@router.callback_query(F.data == "add_welcome_text")
async def add_welcome_text_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(StartWelcomeManagement.waiting_for_photo)
    await call.message.answer(
        "Welcome Text ထည့်ရန် စာသားပို့ပါ။\n\n"
        "Formatting:\n"
        "• bold text - စာလုံးမဲအတွက်\n"
        "• italic text - စာလုံးစောင်းအတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@router.message(StartWelcomeManagement.waiting_for_photo, F.text)
async def add_welcome_text_done(msg: Message, state: FSMContext):
    if msg.text == '/cancel':
        await msg.answer("Cancelled", protect_content=True)
        await state.clear()
        return

    await add_start_welcome(text=msg.text)
    count = await get_start_welcome_count()
    await msg.answer(f"Welcome Text ထည့်ပြီးပါပြီ။\nစုစုပေါင်း: {count} ခု", protect_content=True)
    await state.clear()

@router.callback_query(F.data == "delete_welcome_item")
async def delete_welcome_item_list(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
    if not welcome_list:
        await call.answer("ဖျက်စရာမရှိပါ။", show_alert=True)
        return

    kb_buttons = []
    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            kb_buttons.append([InlineKeyboardButton(
                text=f"{i+1}. Photo - {w.get('caption', 'No caption')[:20]}",
                callback_data=f"delwelcome_{i}",
                color=ButtonColor.DANGER
            )])
        else:
            kb_buttons.append([InlineKeyboardButton(
                text=f"{i+1}. Text - {w.get('text', '')[:20]}",
                callback_data=f"delwelcome_{i}",
                color=ButtonColor.DANGER
            )])
    kb_buttons.append([InlineKeyboardButton(text="Back", callback_data="manage_start_welcome")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await call.message.edit_text("ဖျက်မည့် Welcome Item ကိုရွေးပါ:", reply_markup=kb)

@router.callback_query(F.data.startswith("delwelcome_"))
async def delete_welcome_item_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    index = int(call.data.split("_")[1])
    if await delete_start_welcome(index):
        await call.answer("ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        await call.answer("ဖျက်လို့မရပါ။", show_alert=True)

    await manage_start_welcome(call)

def admin_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Add Movie", callback_data="add_movie", color=ButtonColor.SUCCESS),
            InlineKeyboardButton(text="Delete Movie", callback_data="del_movie", color=ButtonColor.DANGER)
        ],
        [
            InlineKeyboardButton(text="Broadcast", callback_data="broadcast"),
            InlineKeyboardButton(text="Force Channels", callback_data="force")
        ],
        [
            InlineKeyboardButton(text="Backup", callback_data="backup"),
            InlineKeyboardButton(text="Restore", callback_data="restore")
        ],
        [
            InlineKeyboardButton(text="Maintenance", callback_data="maint"),
            InlineKeyboardButton(text="Ads Manager", callback_data="ads_manager")
        ],
        [
            InlineKeyboardButton(text="Auto Delete", callback_data="auto_delete"),
            InlineKeyboardButton(text="Clear All Data", callback_data="clear_all_data", color=ButtonColor.DANGER)
        ],
        [InlineKeyboardButton(text="Welcome Set", callback_data="edit_welcome")],
        [InlineKeyboardButton(text="Force Msg Set", callback_data="edit_forcemsg")],
        [InlineKeyboardButton(text="Searching Set", callback_data="edit_searching")],
        [InlineKeyboardButton(text="Start Buttons", callback_data="manage_start_buttons", color=ButtonColor.PRIMARY)],
        [InlineKeyboardButton(text="Back", callback_data="back")]
    ])
    return kb

class AddAd(StatesGroup):
    msgid = State()
    chatid = State()

@router.callback_query(F.data == "ads_manager")
async def ads_manager(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    ads = await get_ads()
    text = "Ads Manager:\n\n"
    if not ads:
        text += "No ads added yet."
    else:
        for a in ads:
            text += f"ID: {a['id']} | MsgID: {a['message_id']} | ChatID: {a['storage_chat_id']}\n"

    kb_buttons = [[InlineKeyboardButton(text="Add Ad", callback_data="add_ad", color=ButtonColor.SUCCESS)]]
    for a in ads:
        kb_buttons.append([InlineKeyboardButton(
            text=f"Delete Ad {a['id']}",
            callback_data=f"delad_{a['id']}",
            color=ButtonColor.DANGER
        )])
    kb_buttons.append([InlineKeyboardButton(text="Back", callback_data="back_admin")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "add_ad")
async def add_ad_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AddAd.msgid)
    await call.message.answer("Enter Ad Message ID:", protect_content=True)
    await call.answer()

@router.message(AddAd.msgid)
async def add_ad_msgid(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("Please enter a numeric Message ID.", protect_content=True)
    await state.update_data(msgid=int(msg.text))
    await state.set_state(AddAd.chatid)
    await msg.answer("Enter Storage Group Chat ID for this Ad:", protect_content=True)

@router.message(AddAd.chatid)
async def add_ad_chatid(msg: Message, state: FSMContext):
    try:
        chatid = int(msg.text)
    except:
        return await msg.answer("Invalid Chat ID.", protect_content=True)

    data = await state.get_data()
    await add_ad(data["msgid"], chatid)
    await msg.answer("Ad added successfully!", protect_content=True)
    await state.clear()

@router.callback_query(F.data.startswith("delad_"))
async def del_ad_process(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    aid = call.data.split("_")[1]
    await delete_ad(aid)
    await call.answer("Ad deleted", show_alert=True)
    await ads_manager(call)

@router.message(F.text == "Admin Panel")
async def admin_panel(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer("Admin Panel", reply_markup=admin_menu(), protect_content=True)

@router.message(F.text == "Statistics")
async def statistics_panel(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return

    total_users = await get_user_count()
    daily_active = await get_daily_active_users()
    top_users = await get_top_searches(5)
    total_movies = len(MOVIES_DICT)

    text = "Bot Statistics\n\n"
    text += f"Total Users: {total_users}\n"
    text += f"Daily Active: {daily_active}\n"
    text += f"Total Movies: {total_movies}\n\n"

    text += "Top 5 Searchers:\n"
    for i, user in enumerate(top_users, 1):
        name = user.get("name", "Unknown")
        count = user.get("search_count", 0)
        text += f"{i}. {name} - {count} searches\n"

    await msg.answer(text, protect_content=True, parse_mode="HTML")

@router.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await call.message.delete()
    await call.message.answer("Menu:", reply_markup=main_menu(call.from_user.id == OWNER_ID), protect_content=True)
    await call.answer()

@router.callback_query(F.data == "back_to_start")
async def back_to_start(call: CallbackQuery):
    await call.message.delete()
    await send_start_welcome(call.message, call.from_user.id == OWNER_ID)

@router.callback_query(F.data == "back_admin")
async def back_admin(call: CallbackQuery):
    await call.message.edit_text("Admin Panel", reply_markup=admin_menu())

@router.callback_query(F.data == "auto_delete")
async def auto_delete_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)
    dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)

    text = f"Auto Delete Settings:\n\n"
    text += f"Group Messages: {group_sec} seconds\n"
    text += f"DM Messages: {dm_sec} seconds\n\n"
    text += "Select option to change:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Group", callback_data="set_group_delete"),
            InlineKeyboardButton(text="DM", callback_data="set_dm_delete")
        ],
        [InlineKeyboardButton(text="Disable All", callback_data="disable_auto_delete", color=ButtonColor.DANGER)],
        [InlineKeyboardButton(text="Back", callback_data="back_admin")]
    ])

    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data.startswith("set_") & F.data.contains("delete"))
async def set_auto_delete_type(call: CallbackQuery):
    delete_type = "group" if "group" in call.data else "dm"

    kb_buttons = []
    row = []
    for sec in AUTO_DELETE_OPTIONS:
        row.append(InlineKeyboardButton(text=f"{sec}s", callback_data=f"set_time_{delete_type}_{sec}"))
        if len(row) == 3:
            kb_buttons.append(row)
            row = []
    if row:
        kb_buttons.append(row)

    kb_buttons.append([InlineKeyboardButton(text="Disable", callback_data=f"set_time_{delete_type}_0", color=ButtonColor.DANGER)])
    kb_buttons.append([InlineKeyboardButton(text="Back", callback_data="auto_delete")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await call.message.edit_text(f"Select auto-delete time for {delete_type.upper()}:", reply_markup=kb)

@router.callback_query(F.data.startswith("set_time_"))
async def confirm_auto_delete(call: CallbackQuery):
    parts = call.data.split("_")
    delete_type = parts[2]
    seconds = int(parts[3])

    await set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        await call.answer(f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        await call.answer(f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    await auto_delete_menu(call)

@router.callback_query(F.data == "disable_auto_delete")
async def disable_all_auto_delete(call: CallbackQuery):
    await set_auto_delete_config("group", 0)
    await set_auto_delete_config("dm", 0)
    await call.answer("All auto-delete disabled!", show_alert=True)
    await auto_delete_menu(call)

@router.callback_query(F.data == "clear_all_data")
async def clear_all_data_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Confirm Clear All", callback_data="confirm_clear_all", color=ButtonColor.DANGER)],
        [InlineKeyboardButton(text="Back", callback_data="back_admin")]
    ])
    await call.message.edit_text("<b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings.", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "confirm_clear_all")
async def process_clear_all_data(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
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

    await call.message.edit_text("All data has been cleared!", reply_markup=admin_menu())
    await call.answer("Data cleared", show_alert=True)

@router.callback_query(F.data == "force")
async def force(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    channels = await get_force_channels()
    text = "Force Channels:\n\n"

    if not channels:
        text += "No force channels added yet."
    else:
        for ch in channels:
            text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    kb_buttons = []
    for ch in channels:
        kb_buttons.append([InlineKeyboardButton(
            text=f"{ch['title']}",
            callback_data=f"delch_{ch['id']}",
            color=ButtonColor.DANGER
        )])

    kb_buttons.append([InlineKeyboardButton(text="Add Channel", callback_data="add_force", color=ButtonColor.SUCCESS)])
    kb_buttons.append([InlineKeyboardButton(text="Back", callback_data="back_admin")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "add_force")
async def add_force(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    await call.message.answer(
        "Channel link ပေးပါ (public/private OK)\n\n"
        "Example:\nhttps://t.me/yourchannel\nhttps://t.me/+AbCdEfGhIjKlMn==",
        protect_content=True
    )

@router.message(F.text.startswith("https://t.me/"))
async def catch_force_link(msg: Message):
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
            return await msg.answer("Public channel not found", protect_content=True)
    else:
        try:
            chat = await bot.get_chat(link)
            chat_id = chat.id
        except:
            return await msg.answer("Private channel invalid", protect_content=True)

    try:
        bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
        if bot_member.status not in ("administrator", "creator"):
            return await msg.answer("Bot must be admin in channel", protect_content=True)
    except:
        return await msg.answer("Cannot check admin status", protect_content=True)

    try:
        invite = await bot.export_chat_invite_link(chat_id)
    except:
        if chat.username:
            invite = f"https://t.me/{chat.username}"
        else:
            return await msg.answer("Cannot create invite link", protect_content=True)

    await add_force_channel(chat_id, chat.title, invite)

    await msg.answer(f"Added: {chat.title}", protect_content=True)

@router.callback_query(F.data.startswith("delch_"))
async def delch(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    cid = call.data.split("_")[1]
    await delete_force_channel(cid)
    await call.answer("Deleted", show_alert=True)

    await force(call)

@router.callback_query(F.data == "force_done")
async def force_done(call: CallbackQuery):
    ok = await check_force_join(call.from_user.id)

    if not ok:
        await call.answer(
            "Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
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

@router.callback_query(F.data.startswith("edit_"))
async def edit_text_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return

    key = call.data.replace("edit_", "")
    await state.set_state(EditText.waiting)
    await state.update_data(key=key)

    formatting_guide = (
        "\n\nFormatting Guide:\n"
        "• bold text - စာလုံးမဲ\n"
        "• italic text - စာလုံးစောင်း\n"
        "• underline - မျဉ်းသား\n"
        "• {mention} - User mention\n"
        "• {name} - User name\n"
    )

    if key == "searching":
        await call.message.answer(
            "Searching overlay အတွက် content ပို့ပေးပါ:\n\n"
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

@router.message(EditText.waiting)
async def edit_text_done(msg: Message, state: FSMContext):
    data = await state.get_data()
    key = data['key']

    if msg.content_type == ContentType.TEXT and msg.text == '/cancel':
        await msg.answer("Cancelled", protect_content=True)
        await state.clear()
        return

    if msg.content_type == ContentType.TEXT:
        await set_custom_text(key, text=msg.text)
        await msg.answer(f"{key} text updated successfully", protect_content=True)

    elif msg.content_type == ContentType.PHOTO:
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        await set_custom_text(key, text=caption, photo_id=photo_id)
        await msg.answer(f"{key} photo updated successfully", protect_content=True)

    elif msg.content_type == ContentType.STICKER:
        sticker_id = msg.sticker.file_id
        await set_custom_text(key, sticker_id=sticker_id)
        await msg.answer(f"{key} sticker updated successfully", protect_content=True)

    elif msg.content_type == ContentType.ANIMATION:
        animation_id = msg.animation.file_id
        caption = msg.caption or ""
        await set_custom_text(key, text=caption, animation_id=animation_id)
        await msg.answer(f"{key} GIF updated successfully", protect_content=True)

    else:
        await msg.answer("Unsupported content type", protect_content=True)

    await state.clear()

@router.message(F.text == "Movie List")
async def movie_list_redirect(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Movie Code များကြည့်ရန်", url="https://t.me/Movie462")]
    ])
    await msg.answer("Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ", reply_markup=kb, protect_content=True)

@router.callback_query(F.data == "maint")
async def maint(call: CallbackQuery):
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

@router.callback_query(F.data == "add_movie")
async def add_movie(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(AddMovie.name)
    await call.message.answer("ဇာတ်ကားနာမည်?", protect_content=True)
    await call.answer()

@router.message(AddMovie.name)
async def add_movie_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await state.set_state(AddMovie.code)
    await msg.answer("ဇာတ်ကား Code (ဥပမာ: 101010, MM101, etc):", protect_content=True)

@router.message(AddMovie.code)
async def add_movie_code(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    if not code:
        return await msg.answer("Code ထည့်ပါ။", protect_content=True)
    await state.update_data(code=code)
    await state.set_state(AddMovie.msgid)
    await msg.answer("Message ID?", protect_content=True)

@router.message(AddMovie.msgid)
async def add_movie_msgid(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("ဂဏန်းပဲထည့်ပါ။", protect_content=True)
    await state.update_data(msgid=int(msg.text))
    await state.set_state(AddMovie.chatid)
    await msg.answer("Storage Group Chat ID?", protect_content=True)

@router.message(AddMovie.chatid)
async def add_movie_chatid(msg: Message, state: FSMContext):
    try:
        chatid = int(msg.text)
    except:
        return await msg.answer("Chat ID မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    await add_movie_record(data["name"], data["code"], data["msgid"], chatid)

    await msg.answer(f"ဇာတ်ကားထည့်ပြီးပါပြီ!\n\nနာမည်: {data['name']}\nCode: {data['code']}", protect_content=True)
    await state.clear()

class DelMovie(StatesGroup):
    code = State()

@router.callback_query(F.data == "del_movie")
async def del_movie(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(DelMovie.code)
    await call.message.answer("ဖျက်မည့် ဇာတ်ကား Code ကိုထည့်ပါ:", protect_content=True)
    await call.answer()

@router.message(DelMovie.code)
async def del_movie_code(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    await delete_movie(code)
    await msg.answer(f"Code `{code}` ဖျက်ပြီးပါပြီ။", protect_content=True, parse_mode="HTML")
    await state.clear()

class Broadcast(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()
    confirm = State()

@router.callback_query(F.data == "broadcast")
async def bc(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.set_state(Broadcast.waiting_content)
    await call.message.answer(
        "Broadcast စာသား/ပုံ ပို့ပါ။\n\n"
        "Formatting supported:\n"
        "• bold, italic, underline\n"
        "• {mention}, {name} - placeholders\n\n"
        "Photo/Video/GIF ပါ ပို့လို့ရပါတယ်။",
        protect_content=True
    )
    await call.answer()

@router.message(Broadcast.waiting_content)
async def bc_content(msg: Message, state: FSMContext):
    content_type = msg.content_type

    if content_type == ContentType.TEXT:
        await state.update_data(text=msg.text, content_type="text")
    elif content_type == ContentType.PHOTO:
        photo_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        await state.update_data(photo_id=photo_id, caption=caption, content_type="photo")
    elif content_type == ContentType.VIDEO:
        video_id = msg.video.file_id
        caption = msg.caption or ""
        await state.update_data(video_id=video_id, caption=caption, content_type="video")
    elif content_type == ContentType.ANIMATION:
        animation_id = msg.animation.file_id
        caption = msg.caption or ""
        await state.update_data(animation_id=animation_id, caption=caption, content_type="animation")
    else:
        return await msg.answer("Unsupported content type", protect_content=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ပြန်ဖြစ်ရင်ပဲပို့မယ်", callback_data="bc_no_buttons")],
        [InlineKeyboardButton(text="Buttons ထည့်မယ်", callback_data="bc_add_buttons")]
    ])

    await msg.answer("Buttons ထည့်မလား?", reply_markup=kb, protect_content=True)

@router.callback_query(F.data == "bc_no_buttons", Broadcast.waiting_content)
async def bc_no_buttons(call: CallbackQuery, state: FSMContext):
    await state.update_data(buttons=[])
    await confirm_broadcast(call, state)

@router.callback_query(F.data == "bc_add_buttons", Broadcast.waiting_content)
async def bc_add_buttons_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.waiting_buttons)
    await call.message.answer(
        "Buttons ထည့်ရန်:\n\n"
        "Format: Button Name | URL\n"
        "Example:\n"
        "Channel | https://t.me/yourchannel\n"
        "Group | https://t.me/yourgroup\n\n"
        "တစ်ကြောင်းကို button တစ်ခု၊ ပြီးရင် ပို့ပါ။\n"
        "ပြီးသွားရင် /done ရိုက်ပါ။",
        protect_content=True
    )
    await call.answer()

@router.message(Broadcast.waiting_buttons)
async def bc_buttons_collect(msg: Message, state: FSMContext):
    if msg.text == "/done":
        data = await state.get_data()
        if not data.get("buttons"):
            await state.update_data(buttons=[])
        await state.set_state(Broadcast.confirm)
        await confirm_broadcast_message(msg, state)
        return

    if "|" not in msg.text:
        return await msg.answer("Format မမှန်ပါ။ Button Name | URL အဖြစ်ထည့်ပါ။", protect_content=True)

    parts = msg.text.split("|")
    if len(parts) != 2:
        return await msg.answer("Format မမှန်ပါ။", protect_content=True)

    name = parts[0].strip()
    url = parts[1].strip()

    if not url.startswith(("http://", "https://")):
        return await msg.answer("URL မမှန်ပါ။", protect_content=True)

    data = await state.get_data()
    buttons = data.get("buttons", [])
    buttons.append({"name": name, "url": url})
    await state.update_data(buttons=buttons)

    await msg.answer(f"Button '{name}' ထည့်ပြီး။\nထပ်ထည့်မယ်ဆိုရင် ဆက်ပို့ပါ။\nပြီးရင် /done ရိုက်ပါ။", protect_content=True)

async def confirm_broadcast(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.confirm)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Confirm & Send", callback_data="bc_confirm", color=ButtonColor.SUCCESS)],
        [InlineKeyboardButton(text="Cancel", callback_data="bc_cancel", color=ButtonColor.DANGER)]
    ])

    await call.message.answer("Broadcast ပို့မှာသေချာပြီလား?", reply_markup=kb, protect_content=True)

async def confirm_broadcast_message(msg: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Confirm & Send", callback_data="bc_confirm", color=ButtonColor.SUCCESS)],
        [InlineKeyboardButton(text="Cancel", callback_data="bc_cancel", color=ButtonColor.DANGER)]
    ])

    await msg.answer("Broadcast ပို့မှာသေချာပြီလား?", reply_markup=kb, protect_content=True)

@router.callback_query(F.data == "bc_confirm", Broadcast.confirm)
async def bc_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    users = await get_users()

    buttons = data.get("buttons", [])
    kb = None
    if buttons:
        kb_buttons = []
        for btn in buttons:
            kb_buttons.append([InlineKeyboardButton(text=btn["name"], url=btn["url"])])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    sent = 0
    failed = 0

    status_msg = await call.message.answer(f"Broadcasting... 0/{len(users)}", protect_content=True)

    for i, u in enumerate(users):
        try:
            if data["content_type"] == "text":
                await bot.send_message(u["user_id"], data["text"], reply_markup=kb, protect_content=True, parse_mode="HTML")
            elif data["content_type"] == "photo":
                await bot.send_photo(u["user_id"], data["photo_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True, parse_mode="HTML")
            elif data["content_type"] == "video":
                await bot.send_video(u["user_id"], data["video_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True, parse_mode="HTML")
            elif data["content_type"] == "animation":
                await bot.send_animation(u["user_id"], data["animation_id"], caption=data.get("caption"), reply_markup=kb, protect_content=True, parse_mode="HTML")
            sent += 1
        except Exception as e:
            print(f"Failed to send to {u['user_id']}: {e}")
            failed += 1

        if (i + 1) % 10 == 0:
            try:
                await status_msg.edit_text(f"Broadcasting... {i+1}/{len(users)}")
            except:
                pass

    await status_msg.edit_text(f"Broadcast complete!\n\nSent: {sent}\nFailed: {failed}")
    await state.clear()
    await call.answer()

@router.callback_query(F.data == "bc_cancel")
async def bc_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("Broadcast cancelled", protect_content=True)
    await call.answer()

@router.message(Command("os"))
async def os_command(msg: Message):
    if msg.chat.type not in ["group", "supergroup"]:
        await msg.answer("This command can only be used in groups!", protect_content=True)
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = await msg.reply(
        "owner-@osamu1123\n\n"
        "• Bot Status: Online\n"
        "• Queue System: Active (Batch: 30)\n"
        "• Auto-Delete: " + ("" + str(group_sec) + "s" if group_sec > 0 else "Disabled") + "\n"
        "• Version: 4.0 (JSON Storage)\n\n"
        "Use /os name command.",
        protect_content=True,
        parse_mode="HTML"
    )

    if group_sec > 0:
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, response.message_id, group_sec))
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

@router.message()
async def search(msg: Message):
    if msg.text == "Search Movie":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Movie Code များကြည့်ရန်", url="https://t.me/Movie462")]
        ])
        return await msg.answer("<b>ဇာတ်ကား Code ပို့ပေးပါ</b>", reply_markup=kb, protect_content=True, parse_mode="HTML")

    if msg.text.startswith("/"):
        return

    if await is_maintenance() and msg.from_user.id != OWNER_ID:
        return await msg.answer("Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။", protect_content=True)

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
                return await msg.answer(f"ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်", protect_content=True)

    code = msg.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        return await msg.answer(f"Code `{code}` မရှိပါ။\n\nSearch Movie နှိပ်ပြီး Code စစ်ပါ။", protect_content=True, parse_mode="HTML")

    global ACTIVE_USERS

    async with BATCH_LOCK:
        if ACTIVE_USERS >= BATCH_SIZE:
            await WAITING_QUEUE.put(msg.from_user.id)
            position = WAITING_QUEUE.qsize()

            queue_msg = await msg.answer(
                f"စောင့်ဆိုင်းနေဆဲအသုံးပြုသူများ\n\n"
                f"• သင့်နေရာ: {position}\n"
                f"• လက်ရှိအသုံးပြုနေသူ: {ACTIVE_USERS}/{BATCH_SIZE}\n\n"
                f"ကျေးဇူးပြု၍ စောင့်ဆိုင်းပေးပါ။",
                protect_content=True,
                parse_mode="HTML"
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
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Owner", url="https://t.me/osamu1123")]
            ]),
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
        await msg.answer("Error sending movie. Please try again.", protect_content=True)
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

@router.callback_query(F.data == "backup")
async def backup_db(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
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
        caption="JSON Backup File",
        protect_content=False
    )

    await call.answer("Backup sent!", show_alert=True)

@router.callback_query(F.data == "restore")
async def restore_request(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    await call.message.answer("Upload backup.json file", protect_content=True)
    await call.answer()

@router.message(F.document)
async def restore_process(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return

    try:
        file = await bot.download(msg.document, destination="restore.json")

        with open("restore.json", "r", encoding="utf-8") as f:
            data = json.load(f)

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
        await msg.answer("Restore Completed from JSON backup!", protect_content=True)
    except Exception as e:
        await msg.answer(f"Restore Failed: {str(e)}", protect_content=True)

@router.message(F.chat.type.in_(["group", "supergroup"]))
async def group_message_handler(msg: Message):
    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not msg.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete("group", msg.chat.id, msg.message_id, group_sec))

async def on_startup():
    for file in ["movies", "users", "ads", "settings", "force_channels",
                 "custom_texts", "auto_delete", "start_buttons", "start_welcome"]:
        if not os.path.exists(f"{DATA_DIR}/{file}.json"):
            save_json(file, [])

    await load_movies_cache()
    asyncio.create_task(batch_worker())
    print("Bot started with JSON Storage")
    print(f"Movies in cache: {len(MOVIES_DICT)}")
    print(f"Batch size: {BATCH_SIZE}")

    welcome_count = await get_start_welcome_count()
    print(f"Welcome photos: {welcome_count}")

async def main():
    dp.include_router(router)
    await on_startup()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
