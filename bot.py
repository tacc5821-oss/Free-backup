import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes,
    ConversationHandler
)

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

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

# ==================== Conversation States ====================
(ADD_MOVIE_NAME, ADD_MOVIE_CODE, ADD_MOVIE_MSGID, ADD_MOVIE_CHATID,
 DEL_MOVIE_CODE,
 BROADCAST_CONTENT, BROADCAST_BUTTONS, BROADCAST_CONFIRM,
 ADD_AD_MSGID, ADD_AD_CHATID,
 EDIT_TEXT_WAITING,
 START_BUTTON_NAME, START_BUTTON_TYPE, START_BUTTON_LINK, START_BUTTON_POPUP,
 WELCOME_PHOTO, WELCOME_TEXT) = range(17)

# ==================== COLOR BUTTON FUNCTION (2026.2 SUPPORT) ====================
def color_button(text: str, 
                 callback_data: str = None, 
                 url: str = None,
                 color: str = "secondary"):
    """
    Telegram 2026.2 Background Color Button
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
    
    # Telegram 2026.2 Color Support
    if color == "primary":
        kwargs["color"] = "primary"
    elif color == "success":
        kwargs["color"] = "success"
    elif color == "danger":
        kwargs["color"] = "danger"
    
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

async def schedule_auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int):
    if seconds <= 0:
        return
    await asyncio.sleep(seconds)
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception as e:
        print(f"Failed to delete message: {e}")

async def is_maintenance():
    return await get_setting("maint") == "on"

async def check_force_join(user_id, context: ContextTypes.DEFAULT_TYPE):
    channels = await get_force_channels()
    if not channels:
        return True

    for ch in channels:
        try:
            m = await context.bot.get_chat_member(ch["chat_id"], user_id)
            if m.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

async def send_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await get_force_channels()
    if not channels:
        return True

    keyboard = []
    for ch in channels:
        keyboard.append([color_button(text=ch["title"], url=ch["invite"], color="primary")])
    keyboard.append([color_button(text="✅ Done ✅", callback_data="force_done", color="success")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    force_text = await get_custom_text("forcemsg")
    formatted_text = parse_telegram_format(
        force_text.get("text") or "⚠️ **BOTအသုံးပြုခွင့် ကန့်သတ်ထားပါသည်။**\n\nBOT ကိုအသုံးပြု နိုင်ရန်အတွက်အောက်ပါ Channel များကို အရင် Join ပေးထားရပါမည်။",
        update.effective_user.full_name,
        update.effective_user.mention_html()
    )

    await update.message.reply_text(
        formatted_text,
        reply_markup=reply_markup
    )
    return False

async def send_searching_overlay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    overlay = await get_custom_text("searching")

    try:
        if overlay.get("sticker_id"):
            msg = await context.bot.send_sticker(update.effective_chat.id, overlay["sticker_id"])
        elif overlay.get("animation_id"):
            msg = await context.bot.send_animation(update.effective_chat.id, overlay["animation_id"],
                                                 caption=overlay.get("text", ""))
        elif overlay.get("photo_id"):
            msg = await context.bot.send_photo(update.effective_chat.id, overlay["photo_id"],
                                             caption=overlay.get("text", ""))
        else:
            text = overlay.get("text", "🔍 ရှာဖွေနေပါသည်...")
            msg = await context.bot.send_message(update.effective_chat.id, text)
        return msg.message_id
    except Exception as e:
        print(f"Error sending overlay: {e}")
        try:
            msg = await context.bot.send_message(update.effective_chat.id, "🔍 ရှာဖွေနေပါသည်...")
            return msg.message_id
        except:
            return None

async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id, message_id)
    except:
        pass

def main_menu(is_owner=False):
    keyboard = [
        [KeyboardButton("🔍 Search Movie")],
        [KeyboardButton("📋 Movie List")]
    ]
    if is_owner:
        keyboard.append([KeyboardButton("🛠 Admin Panel")])
        keyboard.append([KeyboardButton("📊 Statistics")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== START COMMAND ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_owner = user.id == OWNER_ID
    
    is_new = await add_new_user(user.id, user.full_name, user.mention_html())

    if is_new:
        total_users = await get_user_count()
        notification_text = (
            f"👤 <b>New User Notification</b>\n\n"
            f"<b>Total Users:</b> {total_users}\n"
            f"<b>ID:</b> <code>{user.id}</code>\n"
            f"<b>Name:</b> {user.full_name}\n"
            f"<b>Mention:</b> {user.mention_html()}"
        )
        try:
            await context.bot.send_message(OWNER_ID, notification_text)
        except Exception as e:
            print(f"Failed to notify owner: {e}")

    if not await check_force_join(user.id, context):
        await send_force_join(update, context)
        return

    await send_start_welcome(update, context, is_owner)

    await update.message.reply_text(
        "📌 **Main Menu**\n\nအောက်ပါခလုတ်များကိုသုံးပါ:",
        reply_markup=main_menu(is_owner)
    )

async def send_start_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, is_owner: bool):
    welcome_data = await get_next_welcome_photo()
    user = update.effective_user

    keyboard = []
    rows = await get_start_buttons_by_row()

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
            await update.message.reply_photo(
                photo=welcome_data["photo_id"],
                caption=welcome_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Error sending welcome photo: {e}")
            await update.message.reply_text(
                welcome_text,
                reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup
        )

# ==================== FORCE DONE ====================
async def force_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    ok = await check_force_join(query.from_user.id, context)

    if not ok:
        await query.answer(
            "❌ Channel အားလုံးကို Join မလုပ်ရသေးပါ။\n"
            "ကျေးဇူးပြု၍ သတ်မှတ်ထားသော Channel များအားလုံးကို အရင် Join လုပ်ပါ။\n"
            "ပြီးရင် 'Done' ကို နှိပ်ပါ။",
            show_alert=True
        )
        return

    await query.answer("joinပေးတဲ့အတွက်ကျေးဇူးတင်ပါတယ်!", show_alert=True)
    await query.message.delete()
    
    # Create new update for welcome message
    new_update = Update(update.update_id, message=query.message)
    new_update.effective_user = query.from_user
    new_update.effective_chat = query.message.chat
    await send_start_welcome(new_update, context, query.from_user.id == OWNER_ID)

# ==================== POPUP HANDLER ====================
async def handle_popup_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    buttons = await get_start_buttons()
    for btn in buttons:
        if btn.get("callback_data") == query.data:
            await query.answer(btn.get("link", ""), show_alert=True)
            return
    await query.answer("Popup text not found", show_alert=True)

# ==================== SEARCH COMMAND ====================
async def search_movie_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        color_button(
            text="🎬 Movie + Code ကြည့်ရန်",
            url="https://t.me/seatvmmmovielist",
            color="success"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔍 <b>ဇာတ်ကား Code ပို့ပေးပါ</b>",
        reply_markup=reply_markup
    )

# ==================== MOVIE LIST ====================
async def movie_list_redirect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        color_button(
            text="🎬 Movie + Code ကြည့်ရန်",
            url="https://t.me/seatvmmmovielist",
            color="primary"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📌 ရှိတဲ့ Code များကြည့်ရန် အောက်ပါ Button ကိုနှိပ်ပါ",
        reply_markup=reply_markup
    )

# ==================== STATISTICS ====================
async def statistics_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
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

    await update.message.reply_text(text)

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
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
    
    await update.message.reply_text(
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram 2026.2 Color Buttons**\n"
        "• 🟢 အစိမ်း - Success\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🔴 အနီ - Danger",
        reply_markup=reply_markup
    )

# ==================== BACK HANDLER ====================
async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await query.message.reply_text(
        "Menu:",
        reply_markup=main_menu(query.from_user.id == OWNER_ID)
    )

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    new_update = Update(update.update_id, message=query.message)
    new_update.effective_user = query.from_user
    new_update.effective_chat = query.message.chat
    await send_start_welcome(new_update, context, query.from_user.id == OWNER_ID)

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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
    
    await query.message.edit_text(
        "🛠 **Admin Panel**\n\n"
        "🎨 **Telegram 2026.2 Color Buttons**\n"
        "• 🟢 အစိမ်း - Success\n"
        "• 🔵 အပြာ - Primary\n"
        "• 🔴 အနီ - Danger",
        reply_markup=reply_markup
    )

# ==================== AUTO DELETE ====================
async def auto_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    config = await get_auto_delete_config()
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

    await query.message.edit_text(text, reply_markup=reply_markup)

async def set_auto_delete_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    delete_type = "group" if "group" in query.data else "dm"

    keyboard = []
    row = []
    for sec in AUTO_DELETE_OPTIONS:
        row.append(InlineKeyboardButton(text=f"{sec}s", callback_data=f"set_time_{delete_type}_{sec}"))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="❌ Disable", callback_data=f"set_time_{delete_type}_0")])
    keyboard.append([InlineKeyboardButton(text="⬅ Back", callback_data="auto_delete")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(f"Select auto-delete time for {delete_type.upper()}:", reply_markup=reply_markup)

async def confirm_auto_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    delete_type = parts[2]
    seconds = int(parts[3])

    await set_auto_delete_config(delete_type, seconds)

    if seconds > 0:
        await query.answer(f"{delete_type.upper()} auto-delete set to {seconds} seconds!", show_alert=True)
    else:
        await query.answer(f"{delete_type.upper()} auto-delete disabled!", show_alert=True)

    await auto_delete_menu(update, context)

async def disable_all_auto_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await set_auto_delete_config("group", 0)
    await set_auto_delete_config("dm", 0)
    await query.answer("All auto-delete disabled!", show_alert=True)
    await auto_delete_menu(update, context)

# ==================== CLEAR ALL DATA ====================
async def clear_all_data_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton(text="✅ Confirm Clear All", callback_data="confirm_clear_all")],
        [InlineKeyboardButton(text="⬅ Back", callback_data="back_admin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text("⚠️ <b>Are you sure you want to delete ALL data?</b>\nThis includes movies, users, ads, and settings.", reply_markup=reply_markup)

async def process_clear_all_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
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
    
    await query.message.edit_text("✅ All data has been cleared!\n\n🛠 **Admin Panel**", reply_markup=reply_markup)

# ==================== FORCE CHANNELS ====================
async def force_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    channels = await get_force_channels()
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

    await query.message.edit_text(text, reply_markup=reply_markup)

async def add_force_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    await query.message.reply_text(
        "📌 Channel link ပေးပါ (public/private OK)\n\n"
        "Example:\nhttps://t.me/yourchannel\nhttps://t.me/+AbCdEfGhIjKlMn=="
    )

async def catch_force_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    link = update.message.text.strip()
    chat_id = None
    chat = None

    if "+" not in link:
        username = link.split("t.me/")[1].replace("@", "").strip("/")
        try:
            chat = await context.bot.get_chat(f"@{username}")
            chat_id = chat.id
        except:
            await update.message.reply_text("❌ Public channel not found")
            return
    else:
        try:
            chat = await context.bot.get_chat(link)
            chat_id = chat.id
        except:
            await update.message.reply_text("❌ Private channel invalid")
            return

    try:
        bot_member = await context.bot.get_chat_member(chat_id, (await context.bot.get_me()).id)
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text("❌ Bot must be admin in channel")
            return
    except:
        await update.message.reply_text("❌ Cannot check admin status")
        return

    try:
        invite = await context.bot.export_chat_invite_link(chat_id)
    except:
        if chat.username:
            invite = f"https://t.me/{chat.username}"
        else:
            await update.message.reply_text("❌ Cannot create invite link")
            return

    await add_force_channel(chat_id, chat.title, invite)

    await update.message.reply_text(f"✅ Added: {chat.title}")

async def delete_force_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    cid = query.data.split("_")[1]
    await delete_force_channel(cid)
    await query.answer("✅ Deleted", show_alert=True)
    await force_menu(update, context)

# ==================== EDIT TEXT ====================
async def edit_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    key = query.data.replace("edit_", "")
    context.user_data['edit_key'] = key

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
        await query.message.reply_text(
            "🔍 Searching overlay အတွက် content ပို့ပေးပါ:\n\n"
            "• Text message ပို့ရင် - စာသားအဖြစ်သိမ်းမယ်\n"
            "• Photo ပို့ရင် - Photo နဲ့ caption သိမ်းမယ်\n"
            "• Sticker ပို့ရင် - Sticker အဖြစ်သိမ်းမယ်\n"
            "• GIF/Animation ပို့ရင် - GIF အဖြစ်သိမ်းမယ်\n" +
            formatting_guide +
            "\nမပို့ချင်ရင် /cancel ရိုက်ပါ။"
        )
    else:
        await query.message.reply_text(
            f"'{key}' အတွက် စာအသစ်ပို့ပေးပါ (Photo ပါရင် Photo နဲ့အတူ Caption ထည့်ပေးပါ)" +
            formatting_guide
        )
    
    return EDIT_TEXT_WAITING

async def edit_text_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get('edit_key')
    
    if update.message.text == '/cancel':
        await update.message.reply_text("❌ Cancelled")
        context.user_data.clear()
        return ConversationHandler.END

    if update.message.text:
        await set_custom_text(key, text=update.message.text)
        await update.message.reply_text(f"✅ {key} text updated successfully")

    elif update.message.photo:
        photo_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        await set_custom_text(key, text=caption, photo_id=photo_id)
        await update.message.reply_text(f"✅ {key} photo updated successfully")

    elif update.message.sticker:
        sticker_id = update.message.sticker.file_id
        await set_custom_text(key, sticker_id=sticker_id)
        await update.message.reply_text(f"✅ {key} sticker updated successfully")

    elif update.message.animation:
        animation_id = update.message.animation.file_id
        caption = update.message.caption or ""
        await set_custom_text(key, text=caption, animation_id=animation_id)
        await update.message.reply_text(f"✅ {key} GIF updated successfully")

    else:
        await update.message.reply_text("❌ Unsupported content type")

    context.user_data.clear()
    return ConversationHandler.END

# ==================== ADD MOVIE ====================
async def add_movie_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text("🎬 ဇာတ်ကားနာမည်?")
    return ADD_MOVIE_NAME

async def add_movie_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['movie_name'] = update.message.text
    await update.message.reply_text("🔢 ဇာတ်ကား Code (ဥပမာ: 101010, MM101, etc):")
    return ADD_MOVIE_CODE

async def add_movie_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    if not code:
        await update.message.reply_text("❌ Code ထည့်ပါ။")
        return ADD_MOVIE_CODE
    context.user_data['movie_code'] = code
    await update.message.reply_text("📨 Message ID?")
    return ADD_MOVIE_MSGID

async def add_movie_msgid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ ဂဏန်းပဲထည့်ပါ။")
        return ADD_MOVIE_MSGID
    context.user_data['msgid'] = int(update.message.text)
    await update.message.reply_text("💬 Storage Group Chat ID?")
    return ADD_MOVIE_CHATID

async def add_movie_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chatid = int(update.message.text)
    except:
        await update.message.reply_text("❌ Chat ID မမှန်ပါ။")
        return ADD_MOVIE_CHATID

    await add_movie_record(
        context.user_data['movie_name'],
        context.user_data['movie_code'],
        context.user_data['msgid'],
        chatid
    )

    await update.message.reply_text(
        f"✅ ဇာတ်ကားထည့်ပြီးပါပြီ!\n\n"
        f"နာမည်: {context.user_data['movie_name']}\n"
        f"Code: {context.user_data['movie_code']}"
    )
    context.user_data.clear()
    return ConversationHandler.END

# ==================== DELETE MOVIE ====================
async def del_movie_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text("🗑 ဖျက်မည့် ဇာတ်ကား Code ကိုထည့်ပါ:")
    return DEL_MOVIE_CODE

async def del_movie_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    await delete_movie(code)
    await update.message.reply_text(f"✅ Code `{code}` ဖျက်ပြီးပါပြီ။")
    return ConversationHandler.END

# ==================== BROADCAST ====================
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text(
        "📢 Broadcast စာသား/ပုံ ပို့ပါ။\n\n"
        "📝 Formatting supported:\n"
        "• **bold**, *italic*, __underline__\n"
        "• {mention}, {name} - placeholders\n\n"
        "Photo/Video/GIF ပါ ပို့လို့ရပါတယ်။"
    )
    return BROADCAST_CONTENT

async def broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['content_type'] = 'text'
        context.user_data['text'] = update.message.text
    elif update.message.photo:
        context.user_data['content_type'] = 'photo'
        context.user_data['photo_id'] = update.message.photo[-1].file_id
        context.user_data['caption'] = update.message.caption or ""
    elif update.message.video:
        context.user_data['content_type'] = 'video'
        context.user_data['video_id'] = update.message.video.file_id
        context.user_data['caption'] = update.message.caption or ""
    elif update.message.animation:
        context.user_data['content_type'] = 'animation'
        context.user_data['animation_id'] = update.message.animation.file_id
        context.user_data['caption'] = update.message.caption or ""
    else:
        await update.message.reply_text("❌ Unsupported content type")
        return BROADCAST_CONTENT

    keyboard = [
        [
            InlineKeyboardButton("✅ ပြန်ဖြစ်ရင်ပဲပို့မယ်", callback_data="bc_no_buttons"),
            InlineKeyboardButton("➕ Buttons ထည့်မယ်", callback_data="bc_add_buttons"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Buttons ထည့်မလား?", reply_markup=reply_markup)
    return BROADCAST_BUTTONS

async def broadcast_no_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['buttons'] = []
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=reply_markup)
    return BROADCAST_CONFIRM

async def broadcast_add_buttons_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.message.edit_text(
        "📝 Buttons ထည့်ရန်:\n\n"
        "Format: Button Name | URL\n"
        "Example:\n"
        "Channel | https://t.me/yourchannel\n"
        "Group | https://t.me/yourgroup\n\n"
        "တစ်ကြောင်းကို button တစ်ခု၊ ပြီးရင် ပို့ပါ။\n"
        "ပြီးသွားရင် /done ရိုက်ပါ။"
    )
    return BROADCAST_BUTTONS

async def broadcast_buttons_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "/done":
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("📢 Broadcast ပို့မှာသေချာပြီလား?", reply_markup=reply_markup)
        return BROADCAST_CONFIRM

    if "|" not in update.message.text:
        await update.message.reply_text("❌ Format မမှန်ပါ။ Button Name | URL အဖြစ်ထည့်ပါ။")
        return BROADCAST_BUTTONS

    parts = update.message.text.split("|")
    if len(parts) != 2:
        await update.message.reply_text("❌ Format မမှန်ပါ။")
        return BROADCAST_BUTTONS

    name = parts[0].strip()
    url = parts[1].strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ URL မမှန်ပါ။")
        return BROADCAST_BUTTONS

    buttons = context.user_data.get('buttons', [])
    buttons.append({"name": name, "url": url})
    context.user_data['buttons'] = buttons

    await update.message.reply_text(
        f"✅ Button '{name}' ထည့်ပြီး။\n"
        f"ထပ်ထည့်မယ်ဆိုရင် ဆက်ပို့ပါ။\n"
        f"ပြီးရင် /done ရိုက်ပါ။"
    )
    return BROADCAST_BUTTONS

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = context.user_data
    users = await get_users()

    buttons = data.get('buttons', [])
    reply_markup = None
    if buttons:
        keyboard = []
        for btn in buttons:
            keyboard.append([InlineKeyboardButton(text=btn["name"], url=btn["url"])])
        reply_markup = InlineKeyboardMarkup(keyboard)

    sent = 0
    failed = 0

    status_msg = await query.message.edit_text(f"📢 Broadcasting... 0/{len(users)}")

    for i, u in enumerate(users):
        try:
            if data['content_type'] == 'text':
                await context.bot.send_message(u["user_id"], data['text'], reply_markup=reply_markup)
            elif data['content_type'] == 'photo':
                await context.bot.send_photo(u["user_id"], data['photo_id'], caption=data.get('caption'), reply_markup=reply_markup)
            elif data['content_type'] == 'video':
                await context.bot.send_video(u["user_id"], data['video_id'], caption=data.get('caption'), reply_markup=reply_markup)
            elif data['content_type'] == 'animation':
                await context.bot.send_animation(u["user_id"], data['animation_id'], caption=data.get('caption'), reply_markup=reply_markup)
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
    context.user_data.clear()
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.message.edit_text("❌ Broadcast cancelled")
    context.user_data.clear()
    return ConversationHandler.END

# ==================== ADS MANAGER ====================
async def ads_manager_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    ads = await get_ads()
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

    await query.message.edit_text(text, reply_markup=reply_markup)

async def add_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text("Enter Ad Message ID:")
    return ADD_AD_MSGID

async def add_ad_msgid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("Please enter a numeric Message ID.")
        return ADD_AD_MSGID
    context.user_data['msgid'] = int(update.message.text)
    await update.message.reply_text("Enter Storage Group Chat ID for this Ad:")
    return ADD_AD_CHATID

async def add_ad_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chatid = int(update.message.text)
    except:
        await update.message.reply_text("Invalid Chat ID.")
        return ADD_AD_CHATID

    await add_ad(context.user_data['msgid'], chatid)
    await update.message.reply_text("✅ Ad added successfully!")
    context.user_data.clear()
    return ConversationHandler.END

async def delete_ad_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return
        
    aid = query.data.split("_")[1]
    await delete_ad(aid)
    await query.answer("✅ Ad deleted", show_alert=True)
    await ads_manager_menu(update, context)

# ==================== BACKUP ====================
async def backup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
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

    with open("backup.json", "rb") as f:
        await context.bot.send_document(
            OWNER_ID,
            f,
            caption="📥 JSON Backup File"
        )

    await query.answer("Backup sent!", show_alert=True)

async def restore_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return
        
    await query.message.reply_text("📤 Upload backup.json file")

async def restore_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    try:
        file = await update.message.document.get_file()
        data = json.loads(await file.download_as_bytearray())

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
        await update.message.reply_text("✅ Restore Completed from JSON backup!")
    except Exception as e:
        await update.message.reply_text(f"❌ Restore Failed: {str(e)}")

# ==================== MAINTENANCE ====================
async def maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return
        
    current = await is_maintenance()
    new = "off" if current else "on"
    await set_setting("maint", new)
    await query.answer(f"Maintenance: {new.upper()}", show_alert=True)

# ==================== START BUTTON MANAGEMENT ====================
async def manage_start_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
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

    await query.message.edit_text(text, reply_markup=reply_markup)

async def add_start_button_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text("🔹 Button နာမည်ထည့်ပါ:")
    return START_BUTTON_NAME

async def add_start_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['btn_name'] = update.message.text
    
    keyboard = [
        [
            InlineKeyboardButton(text="🔗 URL Button", callback_data="btn_type_url"),
            InlineKeyboardButton(text="📢 Popup Button", callback_data="btn_type_popup"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Button အမျိုးအစားရွေးပါ:", reply_markup=reply_markup)
    return START_BUTTON_TYPE

async def add_start_button_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    btn_type = query.data.split("_")[2]
    context.user_data['button_type'] = btn_type

    if btn_type == "url":
        await query.message.edit_text("🔗 Button Link ထည့်ပါ (https://t.me/... or https://...):")
        return START_BUTTON_LINK
    else:
        await query.message.edit_text("📝 Popup စာသားထည့်ပါ:")
        return START_BUTTON_POPUP

async def add_start_button_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Link မမှန်ပါ။ http:// သို့မဟုတ် https:// နဲ့စပါ။")
        return START_BUTTON_LINK

    await add_start_button(
        context.user_data['btn_name'], 
        update.message.text, 
        button_type="url"
    )
    await update.message.reply_text(f"✅ Button '{context.user_data['btn_name']}' ထည့်ပြီးပါပြီ။")
    context.user_data.clear()
    return ConversationHandler.END

async def add_start_button_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback_data = f"popup_{update.message.text[:20]}"
    await add_start_button(
        context.user_data['btn_name'], 
        update.message.text, 
        button_type="popup", 
        callback_data=callback_data
    )
    await update.message.reply_text(f"✅ Popup Button '{context.user_data['btn_name']}' ထည့်ပြီးပါပြီ။")
    context.user_data.clear()
    return ConversationHandler.END

async def delete_start_button_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    buttons = await get_start_buttons()
    if not buttons:
        await query.answer("❌ Button မရှိပါ။", show_alert=True)
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

    await query.message.edit_text("ဖျက်မည့် Button ကိုရွေးပါ:", reply_markup=reply_markup)

async def delete_start_button_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    btn_id = query.data.split("_")[1]
    await delete_start_button(btn_id)
    await query.answer("✅ Button ဖျက်ပြီးပါပြီ။", show_alert=True)
    await manage_start_buttons(update, context)

# ==================== WELCOME MANAGEMENT ====================
async def manage_start_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
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

    await query.message.edit_text(text, reply_markup=reply_markup)

async def add_welcome_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text(
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
    return WELCOME_PHOTO

async def add_welcome_photo_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        await add_start_welcome(photo_id=photo_id, caption=caption, text=caption)
        count = await get_start_welcome_count()
        await update.message.reply_text(f"✅ Welcome Photo ထည့်ပြီးပါပြီ။\n📸 စုစုပေါင်းပုံ: {count} ပုံ")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Please send a photo.")
        return WELCOME_PHOTO

async def add_welcome_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return ConversationHandler.END
        
    await query.message.reply_text(
        "📝 Welcome Text ထည့်ရန် စာသားပို့ပါ။\n\n"
        "📝 Formatting:\n"
        "• **bold text** - စာလုံးမဲအတွက်\n"
        "• *italic text* - စာလုံးစောင်းအတွက်\n"
        "• __underline__ - မျဉ်းသားအတွက်\n"
        "• {mention} - User mention အတွက်\n"
        "• {name} - User name အတွက်\n\n"
        "မထည့်ချင်ရင် /cancel ရိုက်ပါ။"
    )
    return WELCOME_TEXT

async def add_welcome_text_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == '/cancel':
        await update.message.reply_text("❌ Cancelled")
        return ConversationHandler.END

    if update.message.text:
        await add_start_welcome(text=update.message.text)
        count = await get_start_welcome_count()
        await update.message.reply_text(f"✅ Welcome Text ထည့်ပြီးပါပြီ။\n📝 စုစုပေါင်း: {count} ခု")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Please send text.")
        return WELCOME_TEXT

async def delete_welcome_item_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    welcome_list = await get_start_welcome()
    if not welcome_list:
        await query.answer("❌ ဖျက်စရာမရှိပါ။", show_alert=True)
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

    await query.message.edit_text("ဖျက်မည့် Welcome Item ကိုရွေးပါ:", reply_markup=reply_markup)

async def delete_welcome_item_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != OWNER_ID:
        return

    index = int(query.data.split("_")[1])
    if await delete_start_welcome(index):
        await query.answer("✅ ဖျက်ပြီးပါပြီ။", show_alert=True)
    else:
        await query.answer("❌ ဖျက်လို့မရပါ။", show_alert=True)

    await manage_start_welcome(update, context)

# ==================== MAIN SEARCH FUNCTION ====================
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith('/'):
        return

    if await is_maintenance() and update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🛠 Bot ပြုပြင်နေပါသဖြင့် ခေတ္တပိတ်ထားပါသည်။")
        return

    if not await check_force_join(update.effective_user.id, context):
        await send_force_join(update, context)
        return

    if update.effective_user.id != OWNER_ID:
        last = await get_user_last(update.effective_user.id)
        if last:
            diff = datetime.now() - datetime.fromisoformat(last)
            if diff.total_seconds() < COOLDOWN:
                remain = int(COOLDOWN - diff.total_seconds())
                await update.message.reply_text(f"⏳ ခေတ္တစောင့်ပေးပါ {remain} စက္ကန့်")
                return

    code = update.message.text.strip().upper()
    movie = find_movie_by_code(code)

    if not movie:
        await update.message.reply_text(f"❌ Code `{code}` မရှိပါ။\n\n🔍 Search Movie နှိပ်ပြီး Code စစ်ပါ။")
        return

    global ACTIVE_USERS

    async with BATCH_LOCK:
        if ACTIVE_USERS >= BATCH_SIZE:
            await WAITING_QUEUE.put(update.effective_user.id)
            position = WAITING_QUEUE.qsize()

            queue_msg = await update.message.reply_text(
                f"⏳ **စောင့်ဆိုင်းနေဆဲအသုံးပြုသူများ**\n\n"
                f"• သင့်နေရာ: **{position}**\n"
                f"• လက်ရှိအသုံးပြုနေသူ: **{ACTIVE_USERS}/{BATCH_SIZE}**\n\n"
                f"ကျေးဇူးပြု၍ စောင့်ဆိုင်းပေးပါ။"
            )

            await asyncio.sleep(5)
            await safe_delete_message(context, update.effective_chat.id, queue_msg.message_id)
            return

        ACTIVE_USERS += 1

    try:
        await update_user_search(update.effective_user.id)
        USER_PROCESSING_TIME[update.effective_user.id] = datetime.now()

        ads = await get_ads()
        if ads:
            idx = await get_next_ad_index()
            if idx is not None and idx < len(ads):
                ad = ads[idx]
                try:
                    ad_sent = await context.bot.copy_message(
                        chat_id=update.effective_user.id,
                        from_chat_id=ad["storage_chat_id"],
                        message_id=ad["message_id"]
                    )
                    asyncio.create_task(schedule_auto_delete(context, update.effective_user.id, ad_sent.message_id, 10))
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"Error sending ad: {e}")

        searching_msg_id = await send_searching_overlay(update, context)

        owner_button = color_button(
            text="⚜️Owner⚜️",
            url="https://t.me/osamu1123",
            color="primary"
        )
        
        sent = await context.bot.copy_message(
            chat_id=update.effective_user.id,
            from_chat_id=movie["storage_chat_id"],
            message_id=movie["message_id"],
            reply_markup=InlineKeyboardMarkup([[owner_button]])
        )

        if searching_msg_id:
            await safe_delete_message(context, update.effective_user.id, searching_msg_id)

        config = await get_auto_delete_config()
        dm_sec = next((c["seconds"] for c in config if c["type"] == "dm"), 0)
        if dm_sec > 0:
            asyncio.create_task(schedule_auto_delete(context, update.effective_user.id, sent.message_id, dm_sec))

    except Exception as e:
        print(f"Error sending movie: {e}")
        await update.message.reply_text("❌ Error sending movie. Please try again.")
    finally:
        async with BATCH_LOCK:
            ACTIVE_USERS -= 1

# ==================== TEST COLOR BUTTONS ====================
async def test_color_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test Telegram 2026.2 Color Buttons (Fully Supported)"""
    
    keyboard = [
        [color_button("🔵 အပြာရောင် Button (Primary)", callback_data="test_blue", color="primary")],
        [color_button("🟢 အစိမ်းရောင် Button (Success)", callback_data="test_green", color="success")],
        [color_button("🔴 အနီရောင် Button (Danger)", callback_data="test_red", color="danger")],
        [color_button("⚪ မီးခိုးရောင် Button (Secondary)", callback_data="test_gray", color="secondary")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎨 **Telegram 2026.2 Color Button Test**\n\n"
        "**✅ python-telegram-bot က Color Buttons ကို ထောက်ပံ့ပါတယ်**\n\n"
        "အောက်က Button တွေမှာ အရောင်တွေပြရင် ✅ အလုပ်လုပ်တယ်\n\n"
        "🔵 Primary - အပြာ\n"
        "🟢 Success - အစိမ်း\n"
        "🔴 Danger - အနီ\n"
        "⚪ Secondary - မီးခိုး (Default)",
        reply_markup=reply_markup
    )

async def handle_test_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    color_names = {
        "test_blue": "အပြာ (Primary)",
        "test_green": "အစိမ်း (Success)",
        "test_red": "အနီ (Danger)",
        "test_gray": "မီးခိုး (Secondary)"
    }
    
    color_name = color_names.get(query.data, "Unknown")
    await query.answer(f"✅ {color_name} Button ကိုနှိပ်လိုက်ပါတယ်", show_alert=True)

# ==================== OS COMMAND ====================
async def os_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("This command can only be used in groups!")
        return

    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    response = await update.message.reply_text(
        "**owner-@osamu1123**\n\n"
        "• Bot Status: ✅ Online\n"
        "• Queue System: 🟢 Active (Batch: 30)\n"
        "• Auto-Delete: " + ("✅ " + str(group_sec) + "s" if group_sec > 0 else "❌ Disabled") + "\n"
        "• Version: 4.0 (JSON Storage)\n\n"
        "Use /os name command."
    )

    if group_sec > 0:
        asyncio.create_task(schedule_auto_delete(context, update.effective_chat.id, response.message_id, group_sec))
        asyncio.create_task(schedule_auto_delete(context, update.effective_chat.id, update.message.message_id, group_sec))

# ==================== GROUP MESSAGE HANDLER ====================
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = await get_auto_delete_config()
    group_sec = next((c["seconds"] for c in config if c["type"] == "group"), 0)

    if group_sec > 0 and not update.message.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete(context, update.effective_chat.id, update.message.message_id, group_sec))

# ==================== CANCEL HANDLER ====================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# ==================== ON STARTUP ====================
async def post_init(application: Application):
    for file in ["movies", "users", "ads", "settings", "force_channels", 
                 "custom_texts", "auto_delete", "start_buttons", "start_welcome"]:
        if not os.path.exists(f"{DATA_DIR}/{file}.json"):
            save_json(file, [])
    
    await load_movies_cache()
    asyncio.create_task(batch_worker())
    print("✅ Bot started with python-telegram-bot")
    print(f"✅ Movies in cache: {len(MOVIES_DICT)}")
    print(f"✅ Batch size: {BATCH_SIZE}")
    print("✅ Telegram 2026.2 Color Buttons: FULLY SUPPORTED")

    welcome_count = await get_start_welcome_count()
    print(f"✅ Welcome photos: {welcome_count}")

# ==================== MAIN ====================
def main():
    # Create application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Basic commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("testcolor", test_color_buttons))
    application.add_handler(CommandHandler("os", os_command))
    application.add_handler(CommandHandler("cancel", cancel))

    # Message handlers
    application.add_handler(MessageHandler(filters.Text("🔍 Search Movie"), search_movie_prompt))
    application.add_handler(MessageHandler(filters.Text("📋 Movie List"), movie_list_redirect))
    application.add_handler(MessageHandler(filters.Text("🛠 Admin Panel"), admin_panel))
    application.add_handler(MessageHandler(filters.Text("📊 Statistics"), statistics_panel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    application.add_handler(MessageHandler(filters.Document.ALL, restore_process))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, group_message_handler))

    # Callback query handlers
    application.add_handler(CallbackQueryHandler(force_done, pattern="^force_done$"))
    application.add_handler(CallbackQueryHandler(handle_popup_button, pattern="^popup_"))
    application.add_handler(CallbackQueryHandler(handle_test_buttons, pattern="^test_"))
    application.add_handler(CallbackQueryHandler(back, pattern="^back$"))
    application.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    application.add_handler(CallbackQueryHandler(back_admin, pattern="^back_admin$"))
    
    # Auto delete callbacks
    application.add_handler(CallbackQueryHandler(auto_delete_menu, pattern="^auto_delete$"))
    application.add_handler(CallbackQueryHandler(set_auto_delete_type, pattern="^set_(group|dm)_delete$"))
    application.add_handler(CallbackQueryHandler(confirm_auto_delete, pattern="^set_time_"))
    application.add_handler(CallbackQueryHandler(disable_all_auto_delete, pattern="^disable_auto_delete$"))
    
    # Clear all data
    application.add_handler(CallbackQueryHandler(clear_all_data_confirm, pattern="^clear_all_data$"))
    application.add_handler(CallbackQueryHandler(process_clear_all_data, pattern="^confirm_clear_all$"))
    
    # Force channels
    application.add_handler(CallbackQueryHandler(force_menu, pattern="^force$"))
    application.add_handler(CallbackQueryHandler(add_force_start, pattern="^add_force$"))
    application.add_handler(CallbackQueryHandler(delete_force_channel_handler, pattern="^delch_"))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^https://t\.me/'), catch_force_link))
    
    # Ads manager
    application.add_handler(CallbackQueryHandler(ads_manager_menu, pattern="^ads_manager$"))
    application.add_handler(CallbackQueryHandler(delete_ad_handler, pattern="^delad_"))
    
    # Backup and restore
    application.add_handler(CallbackQueryHandler(backup_handler, pattern="^backup$"))
    application.add_handler(CallbackQueryHandler(restore_request, pattern="^restore$"))
    
    # Maintenance
    application.add_handler(CallbackQueryHandler(maintenance_toggle, pattern="^maint$"))
    
    # Start buttons management
    application.add_handler(CallbackQueryHandler(manage_start_buttons, pattern="^manage_start_buttons$"))
    application.add_handler(CallbackQueryHandler(delete_start_button_list, pattern="^delete_start_button$"))
    application.add_handler(CallbackQueryHandler(delete_start_button_confirm, pattern="^delstartbtn_"))
    
    # Welcome management
    application.add_handler(CallbackQueryHandler(manage_start_welcome, pattern="^manage_start_welcome$"))
    application.add_handler(CallbackQueryHandler(delete_welcome_item_list, pattern="^delete_welcome_item$"))
    application.add_handler(CallbackQueryHandler(delete_welcome_item_confirm, pattern="^delwelcome_"))

    # Conversation handlers
    # Add Movie
    add_movie_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_movie_start, pattern="^add_movie$")],
        states={
            ADD_MOVIE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_name)],
            ADD_MOVIE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_code)],
            ADD_MOVIE_MSGID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_msgid)],
            ADD_MOVIE_CHATID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_chatid)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_movie_conv)

    # Delete Movie
    del_movie_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(del_movie_start, pattern="^del_movie$")],
        states={
            DEL_MOVIE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, del_movie_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(del_movie_conv)

    # Broadcast
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern="^broadcast$")],
        states={
            BROADCAST_CONTENT: [MessageHandler(filters.ALL, broadcast_content)],
            BROADCAST_BUTTONS: [
                CallbackQueryHandler(broadcast_no_buttons, pattern="^bc_no_buttons$"),
                CallbackQueryHandler(broadcast_add_buttons_start, pattern="^bc_add_buttons$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_buttons_collect),
            ],
            BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm, pattern="^bc_confirm$"),
                CallbackQueryHandler(broadcast_cancel, pattern="^bc_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(broadcast_conv)

    # Add Ad
    add_ad_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_ad_start, pattern="^add_ad_start$")],
        states={
            ADD_AD_MSGID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ad_msgid)],
            ADD_AD_CHATID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ad_chatid)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_ad_conv)

    # Edit Text
    edit_text_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_text_start, pattern="^edit_welcome$"),
            CallbackQueryHandler(edit_text_start, pattern="^edit_forcemsg$"),
            CallbackQueryHandler(edit_text_start, pattern="^edit_searching$"),
        ],
        states={
            EDIT_TEXT_WAITING: [MessageHandler(filters.ALL, edit_text_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(edit_text_conv)

    # Add Start Button
    add_start_button_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start_button_start, pattern="^add_start_button$")],
        states={
            START_BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_start_button_name)],
            START_BUTTON_TYPE: [CallbackQueryHandler(add_start_button_type, pattern="^btn_type_")],
            START_BUTTON_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_start_button_link)],
            START_BUTTON_POPUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_start_button_popup)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_start_button_conv)

    # Add Welcome Photo
    add_welcome_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_welcome_photo_start, pattern="^add_welcome_photo$")],
        states={
            WELCOME_PHOTO: [MessageHandler(filters.PHOTO, add_welcome_photo_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_welcome_photo_conv)

    # Add Welcome Text
    add_welcome_text_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_welcome_text_start, pattern="^add_welcome_text$")],
        states={
            WELCOME_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_welcome_text_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_welcome_text_conv)

    # Start bot
    print("✅ Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
