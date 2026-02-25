import os
import json
import re
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dotenv import load_dotenv

import telebot
from telebot.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
    Message, CallbackQuery
)
from telebot import apihelper

# ==================== LOAD ENV ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# ==================== CONFIG ====================
COOLDOWN = 90
BATCH_SIZE = 30
AUTO_DELETE_OPTIONS = [5, 10, 30]

# ==================== GLOBAL VARIABLES ====================
ACTIVE_USERS = 0
WAITING_QUEUE = asyncio.Queue()
BATCH_LOCK = asyncio.Lock()
USER_PROCESSING_TIME = {}
MOVIES_DICT = {}

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ==================== INIT BOT ====================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==================== COLOR BUTTON FUNCTION ====================
def color_button(text: str, 
                 callback_data: str = None, 
                 url: str = None,
                 color: str = "secondary"):
    """
    Telegram Color Button
    အရောင်များ:
        - "primary"   -> အပြာ
        - "success"   -> အစိမ်း
        - "danger"    -> အနီ
        - "secondary" -> မီးခိုး (Default)
    """
    
    kwargs = {"text": text}
    
    if url:
        kwargs["url"] = url
    if callback_data:
        kwargs["callback_data"] = callback_data
    
    # Telegram Color Support (pyTelegramBotAPI မှာ color parameter ပါတယ်)
    if color in ["primary", "success", "danger"]:
        kwargs["color"] = color
    
    return InlineKeyboardButton(**kwargs)

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
def get_movies():
    return load_json("movies")

def load_movies_cache():
    global MOVIES_DICT
    movies = get_movies()
    MOVIES_DICT = {}
    for m in movies:
        if m.get("movie_code"):
            MOVIES_DICT[m["movie_code"].upper()] = m
    print(f"✅ Loaded {len(MOVIES_DICT)} movies to cache")

def reload_movies_cache():
    load_movies_cache()

def find_movie_by_code(code: str) -> Optional[dict]:
    return MOVIES_DICT.get(code.upper())

def add_movie_record(name, code, msgid, chatid):
    movies = load_json("movies")
    movies.append({
        "movie_name": name,
        "movie_code": code.upper(),
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("movies", movies)
    reload_movies_cache()

def delete_movie(code):
    movies = load_json("movies")
    movies = [m for m in movies if m.get("movie_code", "").upper() != code.upper()]
    save_json("movies", movies)
    reload_movies_cache()

# ==================== ADS ====================
def get_ads():
    return load_json("ads")

def add_ad(msgid, chatid):
    ads = load_json("ads")
    ads.append({
        "id": len(ads) + 1,
        "message_id": msgid,
        "storage_chat_id": chatid
    })
    save_json("ads", ads)

def delete_ad(aid):
    ads = load_json("ads")
    ads = [a for a in ads if a["id"] != int(aid)]
    save_json("ads", ads)

# ==================== USERS ====================
def get_users():
    return load_json("users")

def add_new_user(uid, name, mention):
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

def get_user_count():
    return len(load_json("users"))

def update_user_search(uid):
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

def get_user_last(uid):
    users = load_json("users")
    for u in users:
        if u["user_id"] == uid:
            return u.get("last_search")
    return None

def get_top_searches(limit=5):
    users = load_json("users")
    filtered = [u for u in users if u.get("search_count", 0) > 0]
    sorted_users = sorted(filtered, key=lambda x: x.get("search_count", 0), reverse=True)
    return sorted_users[:limit]

def get_daily_active_users():
    users = load_json("users")
    yesterday = datetime.now() - timedelta(days=1)
    count = 0
    for u in users:
        last = u.get("last_search")
        if last and datetime.fromisoformat(last) >= yesterday:
            count += 1
    return count

# ==================== SETTINGS ====================
def get_setting(key):
    settings = load_json("settings")
    for s in settings:
        if s["key"] == key:
            return s.get("value")
    return None

def set_setting(key, value):
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

# ==================== AUTO DELETE ====================
def get_auto_delete_config():
    configs = load_json("auto_delete")
    if not configs:
        configs = [
            {"type": "group", "seconds": 0},
            {"type": "dm", "seconds": 0}
        ]
        save_json("auto_delete", configs)
    return configs

def set_auto_delete_config(config_type, value):
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
def get_force_channels():
    return load_json("force_channels")

def add_force_channel(chat_id, title, invite):
    channels = load_json("force_channels")
    channels.append({
        "id": len(channels) + 1,
        "chat_id": chat_id,
        "title": title,
        "invite": invite
    })
    save_json("force_channels", channels)

def delete_force_channel(cid):
    channels = load_json("force_channels")
    channels = [c for c in channels if c["id"] != int(cid)]
    save_json("force_channels", channels)

# ==================== CUSTOM TEXTS ====================
def get_custom_text(key):
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

def set_custom_text(key, text=None, photo_id=None, sticker_id=None, animation_id=None):
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
def get_start_welcome():
    welcome = load_json("start_welcome")
    if not welcome:
        return [{
            "text": "👋 **Welcome to Movie Bot!**\n\nဇာတ်ကားရှာရန် Code ပို့ပေးပါ။",
            "photo_id": None,
            "caption": ""
        }]
    return welcome

def get_next_welcome_photo():
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

    next_idx = (current + 1) % len(data)
    set_setting("welcome_photo_index", next_idx)

    return data[current % len(data)]

def add_start_welcome(text=None, photo_id=None, caption=None):
    welcome = load_json("start_welcome")
    welcome.append({
        "id": len(welcome) + 1,
        "text": text or "👋 **Welcome to Movie Bot!**",
        "photo_id": photo_id,
        "caption": caption or ""
    })
    save_json("start_welcome", welcome)

def delete_start_welcome(index):
    welcome = load_json("start_welcome")
    if 0 <= index < len(welcome):
        welcome.pop(index)
        save_json("start_welcome", welcome)
        return True
    return False

def get_start_welcome_count():
    return len(load_json("start_welcome"))

# ==================== START BUTTONS ====================
def get_start_buttons():
    return load_json("start_buttons")

def add_start_button(name, link, row=0, button_type="url", callback_data=None):
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

def delete_start_button(btn_id):
    buttons = load_json("start_buttons")
    buttons = [b for b in buttons if b["id"] != int(btn_id)]
    save_json("start_buttons", buttons)

def get_start_buttons_by_row():
    buttons = get_start_buttons()
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

# ==================== MAIN MENU ====================
def main_menu(is_owner=False):
    keyboard = [
        [KeyboardButton("🔍 Search Movie")],
        [KeyboardButton("📋 Movie List")]
    ]
    if is_owner:
        keyboard.append([KeyboardButton("🛠 Admin Panel")])
        keyboard.append([KeyboardButton("📊 Statistics")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== CHECK FORCE JOIN ====================
def check_force_join(user_id):
    channels = get_force_channels()
    if not channels:
        return True

    for ch in channels:
        try:
            m = bot.get_chat_member(ch["chat_id"], user_id)
            if m.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

def send_force_join(message):
    channels = get_force_channels()
    if not channels:
        return True

    keyboard = []
    for ch in channels:
        keyboard.append([color_button(text=ch["title"], url=ch["invite"], color="primary")])
    keyboard.append([color_button(text="✅ Done ✅", callback_data="force_done", color="success")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    force_text = get_custom_text("forcemsg")
    formatted_text = parse_telegram_format(
        force_text.get("text") or "⚠️ **BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။**\n\nBOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။",
        message.from_user.full_name,
        message.from_user.mention_html()
    )

    bot.reply_to(message, formatted_text, reply_markup=reply_markup)
    return False

# ==================== START COMMAND ====================
@bot.message_handler(commands=['start'])
def start(message: Message):
    user = message.from_user
    is_owner = user.id == OWNER_ID
    
    is_new = add_new_user(user.id, user.full_name, user.mention_html())

    if is_new:
        total_users = get_user_count()
        notification_text = (
            f"👤 <b>New User Notification</b>\n\n"
            f"<b>Total Users:</b> {total_users}\n"
            f"<b>ID:</b> <code>{user.id}</code>\n"
            f"<b>Name:</b> {user.full_name}\n"
            f"<b>Mention:</b> {user.mention_html()}"
        )
        try:
            bot.send_message(OWNER_ID, notification_text)
        except Exception as e:
            print(f"Failed to notify owner: {e}")

    if not check_force_join(user.id):
        send_force_join(message)
        return

    send_start_welcome(message, is_owner)

    bot.reply_to(
        message,
        "📌 **Main Menu**\n\nအောက်ပါခလုတ်များကိုသုံးပါ:",
        reply_markup=main_menu(is_owner)
    )

def send_start_welcome(message: Message, is_owner: bool):
    welcome_data = get_next_welcome_photo()
    user = message.from_user

    keyboard = []
    rows = get_start_buttons_by_row()

    for row_num in sorted(rows.keys()):
        row_buttons = []
        for btn in rows[row_num][:2]:
            if btn.get("type") == "popup":
                row_buttons.append(
                    color_button(
                        text=btn["name"],
                        callback_data=btn.get("callback_data", f"popup_{btn['id']}"),
                        color="primary"
                    )
                )
            else:
                row_buttons.append(
                    color_button(
                        text=btn["name"],
                        url=btn["link"],
                        color="success"
                    )
                )
        keyboard.append(row_buttons)

    if is_owner:
        keyboard.append([
            color_button(
                text="⚙️ Manage Start Buttons",
                callback_data="manage_start_buttons",
                color="danger"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    welcome_text = parse_telegram_format(
        welcome_data.get("caption") or welcome_data.get("text", "👋 Welcome!"),
        user.full_name,
        user.mention_html()
    )

    if welcome_data and welcome_data.get("photo_id"):
        try:
            bot.send_photo(
                message.chat.id,
                welcome_data["photo_id"],
                caption=welcome_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending welcome photo: {e}")
            bot.send_message(
                message.chat.id,
                welcome_text,
                reply_markup=reply_markup
            )
    else:
        bot.send_message(
            message.chat.id,
            welcome_text,
            reply_markup=reply_markup
        )

# ==================== FORCE DONE ====================
@bot.callback_query_handler(func=lambda call: call.data == "force_done")
def force_done(call: CallbackQuery):
    ok = check_force_join(call.from_user.id)

    if not ok:
        bot.answer_callback_query(
            call.id,
            "❌ Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
            "ကျေးဇူးပြု၍ သတ်မှတ်ထားသော Channel များအားလုံးကို အရင် Join လုပ်ပါ။\n"
            "ပြီးရင် 'Done' ကို နှိပ်ပါ။",
            show_alert=True
        )
        return

    bot.answer_callback_query(call.id, "joinပေးတဲ့အတွက်ကျေးဇူးတင်ပါတယ်!", show_alert=True)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    
    # Create new message for welcome
    class FakeMessage:
        def __init__(self, user, chat):
            self.from_user = user
            self.chat = chat
    fake_msg = FakeMessage(call.from_user, call.message.chat)
    send_start_welcome(fake_msg, call.from_user.id == OWNER_ID)

# ==================== POPUP HANDLER ====================
@bot.callback_query_handler(func=lambda call: call.data.startswith("popup_"))
def handle_popup_button(call: CallbackQuery):
    buttons = get_start_buttons()
    for btn in buttons:
        if btn.get("callback_data") == call.data:
            bot.answer_callback_query(call.id, btn.get("link", ""), show_alert=True)
            return
    bot.answer_callback_query(call.id, "Popup text not found", show_alert=True)

# ==================== SEARCH COMMAND ====================
@bot.message_handler(func=lambda m: m.text == "🔍 Search Movie")
def search_movie_prompt(message: Message):
    keyboard = [[
        color_button(
            text="🎬 Movie + Code ကြည့်ရန်",
            url="https://t.me/seatvmmmovielist",
            color="success"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.reply_to(
        message,
        "🔍 <b>ဇာတ်ကား Code ပို့ပေးပါ</b>",
        reply_markup=reply_markup
    )

# ==================== MOVIE LIST ====================
@bot.message_handler(func=lambda m: m.text == "📋 Movie List")
def movie_list_redirect(message: Message):
    keyboard = [[
        color_button(
            text="🎬 Movie + Code ကြည့်ရန်",
            url="https://t.me/seatvmmmovielist",
            color="primary"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.reply_to(
        message,
        "📌 ရှိတဲ့ Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ",
        reply_markup=reply_markup
    )

# ==================== STATISTICS ====================
@bot.message_handler(func=lambda m: m.text == "📊 Statistics")
def statistics_panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    total_users = get_user_count()
    daily_active = get_daily_active_users()
    top_users = get_top_searches(5)
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

    bot.reply_to(message, text)

# ==================== ADMIN PANEL ====================
@bot.message_handler(func=lambda m: m.text == "🛠 Admin Panel")
def admin_panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    
    keyboard = [
        [
            color_button("➕ Add Movie", callback_data="add_movie", color="success"),
            color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        ],
        [
            color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
            color_button("📡 Force Channels", callback_data="force", color="primary"),
        ],
        [
            InlineKeyboardButton("📥 Backup", callback_data="backup"),
            InlineKeyboardButton("📤 Restore", callback_data="restore"),
        ],
        [
            InlineKeyboardButton("🛑 Maintenance", callback_data="maint"),
            InlineKeyboardButton("📺 Ads Manager", callback_data="ads_manager"),
        ],
        [
            InlineKeyboardButton("⏰ Auto Delete", callback_data="auto_delete"),
            color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        ],
        [
            InlineKeyboardButton("📝 Welcome Set", callback_data="edit_welcome"),
            InlineKeyboardButton("📢 Force Msg Set", callback_data="edit_forcemsg"),
        ],
        [
            InlineKeyboardButton("🔍 Searching Set", callback_data="edit_searching"),
            InlineKeyboardButton("⚙️ Start Buttons", callback_data="manage_start_buttons"),
        ],
        [
            InlineKeyboardButton("⬅ Back", callback_data="back"),
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.reply_to(
        message,
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram Color Buttons**\n"
        "• 🟢 အစိမ်း - Success\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🔴 အနီ - Danger",
        reply_markup=reply_markup
    )

# ==================== BACK HANDLER ====================
@bot.callback_query_handler(func=lambda call: call.data == "back")
def back(call: CallbackQuery):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.send_message(
        call.message.chat.id,
        "Menu:",
        reply_markup=main_menu(call.from_user.id == OWNER_ID)
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_start")
def back_to_start(call: CallbackQuery):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    
    class FakeMessage:
        def __init__(self, user, chat):
            self.from_user = user
            self.chat = chat
    fake_msg = FakeMessage(call.from_user, call.message.chat)
    send_start_welcome(fake_msg, call.from_user.id == OWNER_ID)

@bot.callback_query_handler(func=lambda call: call.data == "back_admin")
def back_admin(call: CallbackQuery):
    keyboard = [
        [
            color_button("➕ Add Movie", callback_data="add_movie", color="success"),
            color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        ],
        [
            color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
            color_button("📡 Force Channels", callback_data="force", color="primary"),
        ],
        [
            InlineKeyboardButton("📥 Backup", callback_data="backup"),
            InlineKeyboardButton("📤 Restore", callback_data="restore"),
        ],
        [
            InlineKeyboardButton("🛑 Maintenance", callback_data="maint"),
            InlineKeyboardButton("📺 Ads Manager", callback_data="ads_manager"),
        ],
        [
            InlineKeyboardButton("⏰ Auto Delete", callback_data="auto_delete"),
            color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        ],
        [
            InlineKeyboardButton("📝 Welcome Set", callback_data="edit_welcome"),
            InlineKeyboardButton("📢 Force Msg Set", callback_data="edit_forcemsg"),
        ],
        [
            InlineKeyboardButton("🔍 Searching Set", callback_data="edit_searching"),
            InlineKeyboardButton("⚙️ Start Buttons", callback_data="manage_start_buttons"),
        ],
        [
            InlineKeyboardButton("⬅ Back", callback_data="back"),
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.edit_message_text(
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram Color Buttons**\n"
        "• 🟢 အစိမ်း - Success\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🔴 အနီ - Danger",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )

# ==================== AUTO DELETE ====================
@bot.callback_query_handler(func=lambda call: call.data == "auto_delete")
def auto_delete_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)
    dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)

    text = f"🕒 Auto Delete Settings:\n\n"
    text += f"Group Messages: {group_sec} seconds\n"
    text += f"DM Messages: {dm_sec} seconds\n\n"
    text += "Select option to change:"

    keyboard = [
        [
            InlineKeyboardButton("👥 Group", callback_data="set_group_delete"),
            InlineKeyboardButton("💬 DM", callback_data="set_dm_delete"),
        ],
        [
            InlineKeyboardButton("❌ Disable All", callback_data="disable_auto_delete"),
        ],
        [
            InlineKeyboardButton("⬅ Back", callback_data="back_admin"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data in ["set_group_delete", "set_dm_delete"])
def set_auto_delete_type(call: CallbackQuery):
    delete_type = "group" if "group" in call.data else "dm"

    keyboard = []
    row = []
    for sec in AUTO_DELETE_OPTIONS:
        row.append(InlineKeyboardButton(text=f"{sec}s", callback_data=f"set_time_{delete_type}_{sec}"))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="❌ Disable", callback_data=f"set_time_{delete_type}_0")])
    keyboard.append([InlineKeyboardButton(text="⬅ Back", callback_data="auto_delete")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(
        f"Select auto-delete time for {delete_type.upper()}:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_time_"))
def confirm_auto_delete(call: CallbackQuery):
    parts = call.data.split("_")
    delete_type = parts[2]
    seconds = int(parts[3])

    set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        bot.answer_callback_query(call.id, f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        bot.answer_callback_query(call.id, f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    auto_delete_menu(call)

@bot.callback_query_handler(func=lambda call: call.data == "disable_auto_delete")
def disable_all_auto_delete(call: CallbackQuery):
    set_auto_delete_config("group", 0)
    set_auto_delete_config("dm", 0)
    bot.answer_callback_query(call.id, "All auto-delete disabled!", show_alert=True)
    auto_delete_menu(call)

# ==================== CLEAR ALL DATA ====================
@bot.callback_query_handler(func=lambda call: call.data == "clear_all_data")
def clear_all_data_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton(text="✅ Confirm Clear All", callback_data="confirm_clear_all")],
        [InlineKeyboardButton(text="⬅ Back", callback_data="back_admin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.edit_message_text(
        "⚠️ <b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "confirm_clear_all")
def process_clear_all_data(call: CallbackQuery):
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

    reload_movies_cache()

    keyboard = [
        [
            color_button("➕ Add Movie", callback_data="add_movie", color="success"),
            color_button("🗑 Delete Movie", callback_data="del_movie", color="danger"),
        ],
        [
            color_button("📢 Broadcast", callback_data="broadcast", color="primary"),
            color_button("📡 Force Channels", callback_data="force", color="primary"),
        ],
        [
            InlineKeyboardButton("📥 Backup", callback_data="backup"),
            InlineKeyboardButton("📤 Restore", callback_data="restore"),
        ],
        [
            InlineKeyboardButton("🛑 Maintenance", callback_data="maint"),
            InlineKeyboardButton("📺 Ads Manager", callback_data="ads_manager"),
        ],
        [
            InlineKeyboardButton("⏰ Auto Delete", callback_data="auto_delete"),
            color_button("🗑 Clear All Data", callback_data="clear_all_data", color="danger"),
        ],
        [
            InlineKeyboardButton("📝 Welcome Set", callback_data="edit_welcome"),
            InlineKeyboardButton("📢 Force Msg Set", callback_data="edit_forcemsg"),
        ],
        [
            InlineKeyboardButton("🔍 Searching Set", callback_data="edit_searching"),
            InlineKeyboardButton("⚙️ Start Buttons", callback_data="manage_start_buttons"),
        ],
        [
            InlineKeyboardButton("⬅ Back", callback_data="back"),
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.edit_message_text(
        "✅ All data has been cleared!\n\n🛠 **Admin Panel**",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )
    bot.answer_callback_query(call.id, "Data cleared", show_alert=True)

# ==================== FORCE CHANNELS ====================
@bot.callback_query_handler(func=lambda call: call.data == "force")
def force_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    channels = get_force_channels()
    text = "📡 Force Channels:\n\n"

    if not channels:
        text += "No force channels added yet."
    else:
        for ch in channels:
            text += f"{ch['id']}. {ch['title']} ({ch['chat_id']})\n"

    keyboard = []

    for ch in channels:
        keyboard.append([InlineKeyboardButton(text=f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")])

    keyboard.append([InlineKeyboardButton(text="➕ Add Channel", callback_data="add_force")])
    keyboard.append([InlineKeyboardButton(text="⬅ Back", callback_data="back_admin")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_force")
def add_force_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    bot.send_message(
        call.message.chat.id,
        "📌 Channel link ပေးပါ (public/private OK)\n\n"
        "Example:\nhttps://t.me/yourchannel\nhttps://t.me/+AbCdEfGhIjKlMn=="
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith("https://t.me/"))
def catch_force_link(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    link = message.text.strip()
    chat_id = None
    chat = None

    if "+" not in link:
        username = link.split("t.me/")[1].replace("@", "").strip("/")
        try:
            chat = bot.get_chat(f"@{username}")
            chat_id = chat.id
        except:
            bot.reply_to(message, "❌ Public channel not found")
            return
    else:
        try:
            chat = bot.get_chat(link)
            chat_id = chat.id
        except:
            bot.reply_to(message, "❌ Private channel invalid")
            return

    try:
        bot_member = bot.get_chat_member(chat_id, bot.get_me().id)
        if bot_member.status not in ("administrator", "creator"):
            bot.reply_to(message, "❌ Bot must be admin in channel")
            return
    except:
        bot.reply_to(message, "❌ Cannot check admin status")
        return

    try:
        invite = bot.export_chat_invite_link(chat_id)
    except:
        if chat.username:
            invite = f"https://t.me/{chat.username}"
        else:
            bot.reply_to(message, "❌ Cannot create invite link")
            return

    add_force_channel(chat_id, chat.title, invite)

    bot.reply_to(message, f"✅ Added: {chat.title}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("delch_"))
def delete_force_channel_handler(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    cid = call.data.split("_")[1]
    delete_force_channel(cid)
    bot.answer_callback_query(call.id, "✅ Deleted", show_alert=True)
    force_menu(call)

# ==================== EDIT TEXT ====================
# States for conversation
EDITING_TEXT = {}

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_"))
def edit_text_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    key = call.data.replace("edit_", "")
    EDITING_TEXT[call.from_user.id] = key

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
        bot.send_message(
            call.message.chat.id,
            "🔍 Searching overlay အတွက် content ပို့ပေးပါ:\n\n"
            "• Text message ပို့ရင် - စာသားအဖြစ်သိမ်းမယ်\n"
            "• Photo ပို့ရင် - Photo နဲ့ caption သိမ်းမယ်\n"
            "• Sticker ပို့ရင် - Sticker အဖြစ်သိမ်းမယ်\n"
            "• GIF/Animation ပို့ရင် - GIF အဖြစ်သိမ်းမယ်\n" +
            formatting_guide +
            "\nမပို့ချင်ရင် /cancel ရိုက်ပါ။"
        )
    else:
        bot.send_message(
            call.message.chat.id,
            f"'{key}' အတွက် စာအသစ်ပို့ပေးပါ (Photo ပါရင် Photo နဲ့အတူ Caption ထည့်ပေးပါ)" +
            formatting_guide
        )

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in EDITING_TEXT)
def edit_text_done(message: Message):
    user_id = message.from_user.id
    key = EDITING_TEXT[user_id]
    
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Cancelled")
        del EDITING_TEXT[user_id]
        return

    if message.text:
        set_custom_text(key, text=message.text)
        bot.reply_to(message, f"✅ {key} text updated successfully")

    elif message.photo:
        photo_id = message.photo[-1].file_id
        caption = message.caption or ""
        set_custom_text(key, text=caption, photo_id=photo_id)
        bot.reply_to(message, f"✅ {key} photo updated successfully")

    elif message.sticker:
        sticker_id = message.sticker.file_id
        set_custom_text(key, sticker_id=sticker_id)
        bot.reply_to(message, f"✅ {key} sticker updated successfully")

    elif message.animation:
        animation_id = message.animation.file_id
        caption = message.caption or ""
        set_custom_text(key, text=caption, animation_id=animation_id)
        bot.reply_to(message, f"✅ {key} GIF updated successfully")

    else:
        bot.reply_to(message, "❌ Unsupported content type")
        return

    del EDITING_TEXT[user_id]

# ==================== ADD MOVIE ====================
# States
ADDING_MOVIE = {}

@bot.callback_query_handler(func=lambda call: call.data == "add_movie")
def add_movie_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    ADDING_MOVIE[call.from_user.id] = {"step": "name"}
    bot.send_message(call.message.chat.id, "🎬 ဇာတ်ကားနာမည်?")

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in ADDING_MOVIE)
def add_movie_process(message: Message):
    user_id = message.from_user.id
    data = ADDING_MOVIE[user_id]
    step = data.get("step")

    if step == "name":
        data["name"] = message.text
        data["step"] = "code"
        bot.reply_to(message, "🔢 ဇာတ်ကား Code (ဥပမာ: 101010, MM101, etc):")
    
    elif step == "code":
        code = message.text.strip().upper()
        if not code:
            bot.reply_to(message, "❌ Code ထည့်ပါ။")
            return
        data["code"] = code
        data["step"] = "msgid"
        bot.reply_to(message, "📨 Message ID?")
    
    elif step == "msgid":
        if not message.text.isdigit():
            bot.reply_to(message, "❌ ဂဏန်းပဲထည့်ပါ။")
            return
        data["msgid"] = int(message.text)
        data["step"] = "chatid"
        bot.reply_to(message, "💬 Storage Group Chat ID?")
    
    elif step == "chatid":
        try:
            chatid = int(message.text)
        except:
            bot.reply_to(message, "❌ Chat ID မမှန်ပါ။")
            return

        add_movie_record(data["name"], data["code"], data["msgid"], chatid)

        bot.reply_to(
            message,
            f"✅ ဇာတ်ကားထည့်ပြီးပါပြီ!\n\n"
            f"နာမည်: {data['name']}\n"
            f"Code: {data['code']}"
        )
        del ADDING_MOVIE[user_id]

# ==================== DELETE MOVIE ====================
DELETING_MOVIE = {}

@bot.callback_query_handler(func=lambda call: call.data == "del_movie")
def del_movie_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    DELETING_MOVIE[call.from_user.id] = True
    bot.send_message(call.message.chat.id, "🗑 ဖျက်မည့် ဇာတ်ကား Code ကိုထည့်ပါ:")

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in DELETING_MOVIE)
def del_movie_code(message: Message):
    code = message.text.strip().upper()
    delete_movie(code)
    bot.reply_to(message, f"✅ Code `{code}` ဖျက်ပြီးပါပြီ။")
    del DELETING_MOVIE[message.from_user.id]

# ==================== BROADCAST ====================
BROADCAST_DATA = {}

@bot.callback_query_handler(func=lambda call: call.data == "broadcast")
def broadcast_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    BROADCAST_DATA[call.from_user.id] = {"step": "content"}
    bot.send_message(
        call.message.chat.id,
        "📢 Broadcast စာသား/ပုံ ပို့ပါ။\n\n"
        "📝 Formatting supported:\n"
        "• **bold**, *italic*, __underline__\n"
        "• {mention}, {name} - placeholders\n\n"
        "Photo/Video/GIF ပါ ပို့လို့ရပါတယ်။"
    )

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in BROADCAST_DATA)
def broadcast_content(message: Message):
    user_id = message.from_user.id
    data = BROADCAST_DATA[user_id]
    
    if data.get("step") != "content":
        return

    if message.text:
        data['content_type'] = 'text'
        data['text'] = message.text
    elif message.photo:
        data['content_type'] = 'photo'
        data['photo_id'] = message.photo[-1].file_id
        data['caption'] = message.caption or ""
    elif message.video:
        data['content_type'] = 'video'
        data['video_id'] = message.video.file_id
        data['caption'] = message.caption or ""
    elif message.animation:
        data['content_type'] = 'animation'
        data['animation_id'] = message.animation.file_id
        data['caption'] = message.caption or ""
    else:
        bot.reply_to(message, "❌ Unsupported content type")
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ ပြန်ဖြစ်ရင်ပဲပို့မယ်", callback_data="bc_no_buttons"),
            InlineKeyboardButton("➕ Buttons ထည့်မယ်", callback_data="bc_add_buttons"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    data["step"] = "buttons"
    bot.reply_to(message, "Buttons ထည့်မလား?", reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data in ["bc_no_buttons", "bc_add_buttons"])
def broadcast_buttons_choice(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in BROADCAST_DATA:
        return

    data = BROADCAST_DATA[user_id]
    
    if call.data == "bc_no_buttons":
        data['buttons'] = []
        data['step'] = "confirm"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        bot.edit_message_text(
            "📢 Broadcast ပို့မှာသေချာပြီလား?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=reply_markup
        )
    
    else:  # bc_add_buttons
        data['step'] = "adding_buttons"
        data['buttons'] = []
        bot.edit_message_text(
            "📝 Buttons ထည့်ရန်:\n\n"
            "Format: Button Name | URL\n"
            "Example:\n"
            "Channel | https://t.me/yourchannel\n"
            "Group | https://t.me/yourgroup\n\n"
            "တစ်ကြောင်းကို button တစ်ခု၊ ပြီးရင် ပို့ပါ။\n"
            "ပြီးသွားရင် /done ရိုက်ပါ။",
            call.message.chat.id,
            call.message.message_id
        )

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and 
                    m.from_user.id in BROADCAST_DATA and 
                    BROADCAST_DATA[m.from_user.id].get("step") == "adding_buttons")
def broadcast_buttons_collect(message: Message):
    user_id = message.from_user.id
    data = BROADCAST_DATA[user_id]
    
    if message.text == "/done":
        data['step'] = "confirm"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        bot.reply_to(
            message,
            "📢 Broadcast ပို့မှာသေချာပြီလား?",
            reply_markup=reply_markup
        )
        return

    if "|" not in message.text:
        bot.reply_to(message, "❌ Format မမှန်ပါ။ Button Name | URL အဖြစ်ထည့်ပါ။")
        return

    parts = message.text.split("|")
    if len(parts) != 2:
        bot.reply_to(message, "❌ Format မမှန်ပါ။")
        return

    name = parts[0].strip()
    url = parts[1].strip()

    if not url.startswith(("http://", "https://")):
        bot.reply_to(message, "❌ URL မမှန်ပါ။")
        return

    data['buttons'].append({"name": name, "url": url})

    bot.reply_to(
        message,
        f"✅ Button '{name}' ထည့်ပြီး။\n"
        f"ထပ်ထည့်မယ်ဆိုရင် ဆက်ပို့ပါ။\n"
        f"ပြီးရင် /done ရိုက်ပါ။"
    )

@bot.callback_query_handler(func=lambda call: call.data in ["bc_confirm", "bc_cancel"])
def broadcast_confirm(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in BROADCAST_DATA:
        return

    data = BROADCAST_DATA[user_id]
    
    if call.data == "bc_cancel":
        bot.edit_message_text(
            "❌ Broadcast cancelled",
            call.message.chat.id,
            call.message.message_id
        )
        del BROADCAST_DATA[user_id]
        return

    # bc_confirm
    users = get_users()
    buttons = data.get('buttons', [])
    
    reply_markup = None
    if buttons:
        keyboard = []
        for btn in buttons:
            keyboard.append([InlineKeyboardButton(text=btn["name"], url=btn["url"])])
        reply_markup = InlineKeyboardMarkup(keyboard)

    sent = 0
    failed = 0

    status_msg = bot.edit_message_text(
        f"📢 Broadcasting... 0/{len(users)}",
        call.message.chat.id,
        call.message.message_id
    )

    for i, u in enumerate(users):
        try:
            if data['content_type'] == 'text':
                bot.send_message(u["user_id"], data['text'], reply_markup=reply_markup)
            elif data['content_type'] == 'photo':
                bot.send_photo(u["user_id"], data['photo_id'], caption=data.get('caption'), reply_markup=reply_markup)
            elif data['content_type'] == 'video':
                bot.send_video(u["user_id"], data['video_id'], caption=data.get('caption'), reply_markup=reply_markup)
            elif data['content_type'] == 'animation':
                bot.send_animation(u["user_id"], data['animation_id'], caption=data.get('caption'), reply_markup=reply_markup)
            sent += 1
        except Exception as e:
            print(f"Failed to send to {u['user_id']}: {e}")
            failed += 1

        if (i + 1) % 10 == 0:
            try:
                bot.edit_message_text(
                    f"📢 Broadcasting... {i+1}/{len(users)}",
                    call.message.chat.id,
                    call.message.message_id
                )
            except:
                pass

    bot.edit_message_text(
        f"✅ Broadcast complete!\n\n✅ Sent: {sent}\n❌ Failed: {failed}",
        call.message.chat.id,
        call.message.message_id
    )
    del BROADCAST_DATA[user_id]

# ==================== ADS MANAGER ====================
ADDING_AD = {}

@bot.callback_query_handler(func=lambda call: call.data == "ads_manager")
def ads_manager_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    ads = get_ads()
    text = "📺 Ads Manager:\n\n"
    if not ads:
        text += "No ads added yet."
    else:
        for a in ads:
            text += f"ID: {a['id']} | MsgID: {a['message_id']} | ChatID: {a['storage_chat_id']}\n"

    keyboard = [
        [InlineKeyboardButton(text="➕ Add Ad", callback_data="add_ad_start")]
    ]
    for a in ads:
        keyboard.append([InlineKeyboardButton(text=f"🗑 Delete Ad {a['id']}", callback_data=f"delad_{a['id']}")])
    keyboard.append([InlineKeyboardButton(text="⬅ Back", callback_data="back_admin")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_ad_start")
def add_ad_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    ADDING_AD[call.from_user.id] = {"step": "msgid"}
    bot.send_message(call.message.chat.id, "Enter Ad Message ID:")

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in ADDING_AD)
def add_ad_process(message: Message):
    user_id = message.from_user.id
    data = ADDING_AD[user_id]
    step = data.get("step")

    if step == "msgid":
        if not message.text.isdigit():
            bot.reply_to(message, "Please enter a numeric Message ID.")
            return
        data["msgid"] = int(message.text)
        data["step"] = "chatid"
        bot.reply_to(message, "Enter Storage Group Chat ID for this Ad:")
    
    elif step == "chatid":
        try:
            chatid = int(message.text)
        except:
            bot.reply_to(message, "Invalid Chat ID.")
            return

        add_ad(data["msgid"], chatid)
        bot.reply_to(message, "✅ Ad added successfully!")
        del ADDING_AD[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith("delad_"))
def delete_ad_handler(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
        
    aid = call.data.split("_")[1]
    delete_ad(aid)
    bot.answer_callback_query(call.id, "✅ Ad deleted", show_alert=True)
    ads_manager_menu(call)

# ==================== BACKUP ====================
@bot.callback_query_handler(func=lambda call: call.data == "backup")
def backup_handler(call: CallbackQuery):
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
        "start_welcome": get_start_welcome(),
        "ads": get_ads()
    }

    with open("backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    with open("backup.json", "rb") as f:
        bot.send_document(OWNER_ID, f, caption="📥 JSON Backup File")

    bot.answer_callback_query(call.id, "Backup sent!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "restore")
def restore_request(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
        
    bot.send_message(call.message.chat.id, "📤 Upload backup.json file")

@bot.message_handler(content_types=['document'])
def restore_process(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        data = json.loads(downloaded_file)

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

        reload_movies_cache()
        bot.reply_to(message, "✅ Restore Completed from JSON backup!")
    except Exception as e:
        bot.reply_to(message, f"❌ Restore Failed: {str(e)}")

# ==================== MAINTENANCE ====================
@bot.callback_query_handler(func=lambda call: call.data == "maint")
def maintenance_toggle(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
        
    current = get_setting("maint") == "on"
    new = "off" if current else "on"
    set_setting("maint", new)
    bot.answer_callback_query(call.id, f"Maintenance: {new.upper()}", show_alert=True)

# ==================== START BUTTON MANAGEMENT ====================
ADDING_START_BUTTON = {}

@bot.callback_query_handler(func=lambda call: call.data == "manage_start_buttons")
def manage_start_buttons(call: CallbackQuery):
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
                btn_type = btn.get("type", "url")
                text += f"   • ID: {btn['id']} | {btn['name']} ({btn_type})\n"

    keyboard = [
        [
            InlineKeyboardButton(text="➕ Add Button", callback_data="add_start_button"),
            InlineKeyboardButton(text="🗑 Delete Button", callback_data="delete_start_button"),
        ],
        [
            InlineKeyboardButton(text="🖼 Manage Welcome", callback_data="manage_start_welcome"),
            InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_start"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_start_button")
def add_start_button_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    ADDING_START_BUTTON[call.from_user.id] = {"step": "name"}
    bot.send_message(call.message.chat.id, "🔹 Button နာမည်ထည့်ပါ:")

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in ADDING_START_BUTTON)
def add_start_button_process(message: Message):
    user_id = message.from_user.id
    data = ADDING_START_BUTTON[user_id]
    step = data.get("step")

    if step == "name":
        data["name"] = message.text
        data["step"] = "type"
        
        keyboard = [
            [
                InlineKeyboardButton(text="🔗 URL Button", callback_data="btn_type_url"),
                InlineKeyboardButton(text="📢 Popup Button", callback_data="btn_type_popup"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        bot.reply_to(message, "Button အမျိုးအစားရွေးပါ:", reply_markup=reply_markup)
    
    elif step == "link":
        if not message.text.startswith(('http://', 'https://')):
            bot.reply_to(message, "❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။")
            return

        add_start_button(data['name'], message.text, button_type="url")
        bot.reply_to(message, f"✅ Button '{data['name']}' ထည့်ပြီးပါပြီ။")
        del ADDING_START_BUTTON[user_id]
    
    elif step == "popup":
        callback_data = f"popup_{message.text[:20]}"
        add_start_button(data['name'], message.text, button_type="popup", callback_data=callback_data)
        bot.reply_to(message, f"✅ Popup Button '{data['name']}' ထည့်ပြီးပါပြီ။")
        del ADDING_START_BUTTON[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith("btn_type_"))
def add_start_button_type(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in ADDING_START_BUTTON:
        return

    btn_type = call.data.split("_")[2]
    data = ADDING_START_BUTTON[user_id]

    if btn_type == "url":
        data["step"] = "link"
        bot.edit_message_text(
            "🔗 Button Link ထည့်ပါ (https://t.me/... or https://...):",
            call.message.chat.id,
            call.message.message_id
        )
    else:
        data["step"] = "popup"
        bot.edit_message_text(
            "📝 Popup စာသားထည့်ပါ:",
            call.message.chat.id,
            call.message.message_id
        )

@bot.callback_query_handler(func=lambda call: call.data == "delete_start_button")
def delete_start_button_list(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    buttons = get_start_buttons()
    if not buttons:
        bot.answer_callback_query(call.id, "❌ Button မရှိပါ။", show_alert=True)
        return

    keyboard = []
    for btn in buttons:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🗑 {btn['name']} (Row {btn.get('row', 0)+1})",
                callback_data=f"delstartbtn_{btn['id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_buttons")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(
        "ဖျက်မည့် Button ကိုရွေးပါ:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("delstartbtn_"))
def delete_start_button_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    btn_id = call.data.split("_")[1]
    delete_start_button(btn_id)
    bot.answer_callback_query(call.id, "✅ Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    manage_start_buttons(call)

# ==================== WELCOME MANAGEMENT ====================
ADDING_WELCOME = {}

@bot.callback_query_handler(func=lambda call: call.data == "manage_start_welcome")
def manage_start_welcome(call: CallbackQuery):
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

    keyboard = [
        [
            InlineKeyboardButton(text="➕ Add Photo", callback_data="add_welcome_photo"),
            InlineKeyboardButton(text="➕ Add Text", callback_data="add_welcome_text"),
        ],
        [
            InlineKeyboardButton(text="🗑 Delete", callback_data="delete_welcome_item"),
            InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_buttons"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_welcome_photo")
def add_welcome_photo_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    ADDING_WELCOME[call.from_user.id] = {"type": "photo"}
    bot.send_message(
        call.message.chat.id,
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
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။"
    )

@bot.callback_query_handler(func=lambda call: call.data == "add_welcome_text")
def add_welcome_text_start(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    
    ADDING_WELCOME[call.from_user.id] = {"type": "text"}
    bot.send_message(
        call.message.chat.id,
        "📝 Welcome Text ထည့်ရန် စာသားပို့ပါ။\n\n"
        "📝 Formatting:\n"
        "• **bold text** - စာလုံးမဲအတွက်\n"
        "• *italic text* - စာလုံးစောင်းအတွက်\n"
        "• __underline__ - မျဉ်းသားအတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။"
    )

@bot.message_handler(func=lambda m: m.from_user.id == OWNER_ID and m.from_user.id in ADDING_WELCOME)
def add_welcome_process(message: Message):
    user_id = message.from_user.id
    data = ADDING_WELCOME[user_id]
    
    if message.text == '/cancel':
        bot.reply_to(message, "❌ Cancelled")
        del ADDING_WELCOME[user_id]
        return

    if data["type"] == "photo":
        if message.photo:
            photo_id = message.photo[-1].file_id
            caption = message.caption or ""
            add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
            count = get_start_welcome_count()
            bot.reply_to(message, f"✅ Welcome Photo ထည့်ပြီးပါပြီ။\n📸 စုစုပေါင်းပုံ: {count} ပုံ")
            del ADDING_WELCOME[user_id]
        else:
            bot.reply_to(message, "❌ Please send a photo.")
    
    elif data["type"] == "text":
        if message.text:
            add_start_welcome(text=message.text)
            count = get_start_welcome_count()
            bot.reply_to(message, f"✅ Welcome Text ထည့်ပြီးပါပြီ။\n📝 စုစုပေါင်း: {count} ခု")
            del ADDING_WELCOME[user_id]
        else:
            bot.reply_to(message, "❌ Please send text.")

@bot.callback_query_handler(func=lambda call: call.data == "delete_welcome_item")
def delete_welcome_item_list(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    welcome_list = get_start_welcome()
    if not welcome_list:
        bot.answer_callback_query(call.id, "❌ ဖျက်စရာမရှိပါ။", show_alert=True)
        return

    keyboard = []
    for i, w in enumerate(welcome_list):
        if w.get("photo_id"):
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑 {i+1}. 🖼 Photo - {w.get('caption', 'No caption')[:20]}",
                    callback_data=f"delwelcome_{i}"
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑 {i+1}. 📝 Text - {w.get('text', '')[:20]}",
                    callback_data=f"delwelcome_{i}"
                )
            ])
    keyboard.append([InlineKeyboardButton(text="⬅️ Back", callback_data="manage_start_welcome")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.edit_message_text(
        "ဖျက်မည့် Welcome Item ကိုရွေးပါ:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=reply_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("delwelcome_"))
def delete_welcome_item_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    index = int(call.data.split("_")[1])
    if delete_start_welcome(index):
        bot.answer_callback_query(call.id, "✅ ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "❌ ဖျက်လို့မရပါ။", show_alert=True)

    manage_start_welcome(call)

# ==================== MAIN SEARCH FUNCTION ====================
@bot.message_handler(func=lambda m: True)
def search(message: Message):
    if message.text.startswith('/'):
        return

    if get_setting("maint") == "on" and message.from_user.id != OWNER_ID:
        bot.reply_to(message, "🛠 Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။")
        return

    if not check_force_join(message.from_user.id):
        send_force_join(message)
        return

    if message.from_user.id != OWNER_ID:
        last = get_user_last(message.from_user.id)
        if last:
            diff = datetime.now() - datetime.fromisoformat(last)
            if diff.total_seconds() < COOLDOWN:
                remain = int(COOLDOWN - diff.total_seconds())
                bot.reply_to(message, f"⏳ ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်")
                return

    code = message.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        bot.reply_to(message, f"❌ Code `{code}` မရှိပါ။\n\n🔍 Search Movie နှိပ်ပြီး Code စစ်ပါ။")
        return

    global ACTIVE_USERS
    ACTIVE_USERS += 1

    try:
        update_user_search(message.from_user.id)
        USER_PROCESSING_TIME[message.from_user.id] = datetime.now()

        ads = get_ads()
        if ads:
            idx = get_next_ad_index()
            if idx is not None and idx < len(ads):
                ad = ads[idx]
                try:
                    ad_sent = bot.copy_message(
                        message.from_user.id,
                        ad["storage_chat_id"],
                        ad["message_id"]
                    )
                    # Schedule auto delete for ad
                    threading.Timer(10, lambda: delete_message_after_delay(message.from_user.id, ad_sent.message_id)).start()
                except Exception as e:
                    print(f"Error sending ad: {e}")

        # Send searching overlay
        overlay = get_custom_text("searching")
        searching_msg = None
        
        try:
            if overlay.get("sticker_id"):
                searching_msg = bot.send_sticker(message.chat.id, overlay["sticker_id"])
            elif overlay.get("animation_id"):
                searching_msg = bot.send_animation(message.chat.id, overlay["animation_id"],
                                                 caption=overlay.get("text", ""))
            elif overlay.get("photo_id"):
                searching_msg = bot.send_photo(message.chat.id, overlay["photo_id"],
                                             caption=overlay.get("text", ""))
            else:
                text = overlay.get("text", "🔍 ရှာဖွေနေပါသည်...")
                searching_msg = bot.send_message(message.chat.id, text)
        except Exception as e:
            print(f"Error sending overlay: {e}")
            searching_msg = bot.send_message(message.chat.id, "🔍 ရှာဖွေနေပါသည်...")

        owner_button = color_button(
            text="⚜️Owner⚜️",
            url="https://t.me/osamu1123",
            color="primary"
        )
        
        sent = bot.copy_message(
            message.from_user.id,
            movie["storage_chat_id"],
            movie["message_id"],
            reply_markup=InlineKeyboardMarkup([[owner_button]])
        )

        if searching_msg:
            try:
                bot.delete_message(message.chat.id, searching_msg.message_id)
            except:
                pass

        config = get_auto_delete_config()
        dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)
        if dm_sec > 0:
            threading.Timer(dm_sec, lambda: delete_message_after_delay(message.from_user.id, sent.message_id)).start()

    except Exception as e:
        print(f"Error sending movie: {e}")
        bot.reply_to(message, "❌ Error sending movie. Please try again.")
    finally:
        ACTIVE_USERS -= 1

def delete_message_after_delay(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

# ==================== TEST COLOR BUTTONS ====================
@bot.message_handler(commands=['testcolor'])
def test_color_buttons(message: Message):
    """Test Telegram Color Buttons"""
    
    keyboard = [
        [color_button("🔵 အပြာရောင် Button", callback_data="test_blue", color="primary")],
        [color_button("🟢 အစိမ်းရောင် Button", callback_data="test_green", color="success")],
        [color_button("🔴 အနီရောင် Button", callback_data="test_red", color="danger")],
        [color_button("⚪ မီးခိုးရောင် Button", callback_data="test_gray", color="secondary")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot.reply_to(
        message,
        "🎨 **Telegram Color Button Test**\n\n"
        "**✅ pyTelegramBotAPI က Color Buttons ကို ထောက်ပံ့ပါတယ်**\n\n"
        "အောက်က Button တွေမှာ အရောင်တွေပြရင် ✅ အလုပ်လုပ်တယ်\n\n"
        "🔵 Primary - အပြာ\n"
        "🟢 Success - အစိမ်း\n"
        "🔴 Danger - အနီ\n"
        "⚪ Secondary - မီးခိုး (Default)",
        reply_markup=reply_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("test_"))
def handle_test_buttons(call: CallbackQuery):
    color_names = {
        "test_blue": "အပြာ (Primary)",
        "test_green": "အစိမ်း (Success)",
        "test_red": "အနီ (Danger)",
        "test_gray": "မီးခိုး (Secondary)"
    }
    
    color_name = color_names.get(call.data, "Unknown")
    bot.answer_callback_query(call.id, f"✅ {color_name} Button ကိုနှိပ်လိုက်ပါတယ်", show_alert=True)

# ==================== OS COMMAND ====================
@bot.message_handler(commands=['os'])
def os_command(message: Message):
    if message.chat.type not in ["group", "supergroup"]:
        bot.reply_to(message, "This command can only be used in groups!")
        return

    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = bot.reply_to(
        message,
        "**owner-@osamu1123**\n\n"
        "• Bot Status: ✅ Online\n"
        "• Queue System: 🟢 Active (Batch: 30)\n"
        "• Auto-Delete: " + ("✅ " + str(group_sec) + "s" if group_sec > 0 else "❌ Disabled") + "\n"
        "• Version: 4.0 (JSON Storage)\n\n"
        "Use /os name command."
    )

    if group_sec > 0:
        threading.Timer(group_sec, lambda: delete_message_after_delay(message.chat.id, response.message_id)).start()
        threading.Timer(group_sec, lambda: delete_message_after_delay(message.chat.id, message.message_id)).start()

# ==================== GROUP MESSAGE HANDLER ====================
@bot.message_handler(func=lambda m: m.chat.type in ["group", "supergroup"])
def group_message_handler(message: Message):
    config = get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not message.text.startswith('/'):
        threading.Timer(group_sec, lambda: delete_message_after_delay(message.chat.id, message.message_id)).start()

# ==================== ON STARTUP ====================
def on_startup():
    for file in ["movies", "users", "ads", "settings", "force_channels", 
                 "custom_texts", "auto_delete", "start_buttons", "start_welcome"]:
        if not os.path.exists(f"{DATA_DIR}/{file}.json"):
            save_json(file, [])
    
    load_movies_cache()
    
    print("✅ Bot started with pyTelegramBotAPI")
    print(f"✅ Movies in cache: {len(MOVIES_DICT)}")
    print(f"✅ Batch size: {BATCH_SIZE}")
    print("✅ Telegram Color Buttons: SUPPORTED")

    welcome_count = get_start_welcome_count()
    print(f"✅ Welcome photos: {welcome_count}")
    print("✅ Bot is polling...")

# ==================== MAIN ====================
if __name__ == "__main__":
    on_startup()
    
    # Remove webhook if exists
    bot.remove_webhook()
    
    # Start polling
    bot.infinity_polling(skip_pending=True)
