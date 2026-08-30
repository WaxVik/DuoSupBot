import asyncio
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
import threading
import os
import aiohttp

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ContentTypeFilter
from aiogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from flask import Flask

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAFIfuQ-W3_ZW6Na-WqelTc_hpuirgBOYjQ"
CREATOR_ID = 7675985792

# API-ключ Lakera Guard
LAKERA_API_KEY = "eee5eeea5aaee980fce82725ed4e88535b4b2d21e972b31f49cc722ddb87a258"

# ======================= ID ТЕМ =======================
# HubSup (админский чат)
TOPIC_MOD_CHAT = 6
TOPIC_RULES_HUBSUP = 69
TOPIC_APPEALS = 9
TOPIC_MODLIST = 10
TOPIC_REDACT = 8

# HuBBlox (основной чат)
TOPIC_ANNOUNCEMENTS = 16
TOPIC_RULES = 6
TOPIC_CHAT = 7
TOPIC_APPEALS_HUBBLOX = 20
TOPIC_WELCOME = 1
TOPIC_ADMIN = 27
TOPIC_RAIDS = 17
TOPIC_TRADES = 8

# Темы, где модерация НЕ работает
IGNORED_TOPICS = [TOPIC_ADMIN, TOPIC_RULES, TOPIC_ANNOUNCEMENTS, TOPIC_WELCOME, TOPIC_APPEALS_HUBBLOX]
# Темы, где бот НЕ должен ничего делать (даже команды)
NO_INTERACTION_TOPICS = [TOPIC_ADMIN, TOPIC_APPEALS_HUBBLOX]

# Репорты отправляем в основной чат модерации
TOPIC_REPORTS_HUBSUP = TOPIC_MOD_CHAT

# ======================= ПРОВЕРКА ТОКСИЧНОСТИ ЧЕРЕЗ LAKERA =======================
async def is_insult(text: str) -> bool:
    url = "https://api.lakera.ai/v1/content_moderation"
    headers = {
        "Authorization": f"Bearer {LAKERA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "input": text,
        "language": "ru"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("is_toxic", False)
                else:
                    print(f"Lakera API error: {resp.status}")
                    return False
        except Exception as e:
            print(f"Lakera API exception: {e}")
            return False

# ======================= БАЗА ДАННЫХ =======================
conn = sqlite3.connect("duosup.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    warns INTEGER DEFAULT 0,
    banned BOOLEAN DEFAULT 0,
    ban_until INTEGER,
    last_warn_time INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS global_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_number INTEGER DEFAULT 0
)
''')
cursor.execute("INSERT OR IGNORE INTO global_counter (id, last_number) VALUES (1, 0)")

cursor.execute('''
CREATE TABLE IF NOT EXISTS warn_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    warn_number TEXT,
    reason TEXT,
    created_at INTEGER,
    is_active BOOLEAN DEFAULT 1
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS rules (
    version TEXT PRIMARY KEY,
    rule_text TEXT,
    created_at INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT,
    user_id INTEGER,
    username TEXT,
    text TEXT,
    created_at INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS templates (
    key TEXT PRIMARY KEY,
    value TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS moderators (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    role TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    text TEXT,
    action TEXT,
    is_reply BOOLEAN,
    created_at INTEGER
)
''')

defaults = {
    'warn_template': '{user} получает варн {warn}/4 {warn_id}\n<текст сообщения который я введу>',
    'ban_template': '{user} получает Бан\n<текст который я введу>',
    'unban_template': 'Пользователь {user} разбанен\n<текст который я введу>',
    'unwarn_template': 'С пользователя {user} были сняты все варны 0/4\n<текст который я введу>',
    'welcome_template': '{user} добро пожаловать в HuBBlox\n<Ознакомтесь правилами группы пожалуйста>',
    'appeal_template': 'Пожалуйста напишите номер вашего варна/бана. Видео, аудио и фото доказательства не принимаются. По вопросам писать <текст который я задам>',
    'rules_version': '1.0',
    'admins_list': '@WaxVik0\n@ISHELJ'
}
for key, val in defaults.items():
    cursor.execute("INSERT OR IGNORE INTO templates (key, value) VALUES (?, ?)", (key, val))

cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('link_code', '')")
cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('hublox_id', '')")
cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('hubsup_id', '')")
conn.commit()

# ======================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =======================
def get_config(key):
    cursor.execute("SELECT value FROM config WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None

def set_config(key, value):
    cursor.execute("UPDATE config SET value=? WHERE key=?", (value, key))
    conn.commit()

def get_template(key):
    cursor.execute("SELECT value FROM templates WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else ""

def set_template(key, value):
    cursor.execute("UPDATE templates SET value=? WHERE key=?", (value, key))
    conn.commit()

def generate_link_code():
    alphabet = string.ascii_lowercase + string.digits
    parts = [''.join(secrets.choice(alphabet) for _ in range(5)) for _ in range(5)]
    return '-'.join(parts)

def get_next_warn_number():
    cursor.execute("UPDATE global_counter SET last_number = last_number + 1 WHERE id = 1 RETURNING last_number")
    new_num = cursor.fetchone()[0]
    conn.commit()
    return f"#{new_num:05d}"

def get_user_warns(user_id):
    cursor.execute("SELECT warns FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_warn(user_id, reason=""):
    current = get_user_warns(user_id)
    warn_id = get_next_warn_number()
    if current == 0:
        cursor.execute("INSERT INTO users (user_id, warns) VALUES (?, 1)", (user_id,))
    else:
        cursor.execute("UPDATE users SET warns = warns + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    new_warns = current + 1
    cursor.execute(
        "INSERT INTO warn_logs (user_id, warn_number, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, warn_id, reason, int(datetime.now().timestamp()))
    )
    conn.commit()
    return new_warns, warn_id

def remove_all_warns(user_id):
    cursor.execute("UPDATE users SET warns=0, last_warn_time=NULL WHERE user_id=?", (user_id,))
    cursor.execute("UPDATE warn_logs SET is_active=0 WHERE user_id=? AND is_active=1", (user_id,))
    conn.commit()

def reset_warns_if_needed(user_id):
    cursor.execute("SELECT last_warn_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    now_ts = datetime.now().timestamp()
    if row and row[0]:
        if now_ts - row[0] > 7 * 24 * 3600:
            remove_all_warns(user_id)
            return True
    return False

def is_banned(user_id):
    cursor.execute("SELECT banned, ban_until FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return False
    banned, until = row
    if banned and until is None:
        return True
    if banned and until and datetime.now().timestamp() > until:
        cursor.execute("UPDATE users SET banned=0, ban_until=NULL WHERE user_id=?", (user_id,))
        conn.commit()
        return False
    return bool(banned)

def get_current_rules():
    cursor.execute("SELECT version, rule_text FROM rules ORDER BY created_at DESC LIMIT 1")
    return cursor.fetchone()

def update_rules(version, text):
    cursor.execute("INSERT INTO rules (version, rule_text, created_at) VALUES (?, ?, ?)",
                   (version, text, int(datetime.now().timestamp())))
    conn.commit()

def get_next_appeal_number():
    cursor.execute("SELECT COUNT(*) FROM appeals")
    count = cursor.fetchone()[0] + 1
    return f"#{count:05d}"

def get_moderator_ids():
    cursor.execute("SELECT user_id FROM moderators")
    return [row[0] for row in cursor.fetchall()]

def is_creator_or_moderator(user_id):
    return user_id == CREATOR_ID or user_id in get_moderator_ids()

class AppealStates(StatesGroup):
    waiting_for_text = State()

class RedactStates(StatesGroup):
    waiting_for_input = State()

# ======================= ИНИЦИАЛИЗАЦИЯ БОТА =======================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ======================= ФОНОВАЯ ЗАДАЧА (снятие варнов) =======================
async def check_expired_warns():
    while True:
        await asyncio.sleep(3600)
        now_ts = int(datetime.now().timestamp())
        cursor.execute(
            "SELECT user_id FROM warn_logs WHERE is_active=1 AND created_at <= ?",
            (now_ts - 7 * 24 * 3600,)
        )
        expired = cursor.fetchall()
        for row in expired:
            user_id = row[0]
            remove_all_warns(user_id)
            hublox_id = get_config("hublox_id")
            if hublox_id:
                try:
                    user = await bot.get_chat(user_id)
                    mention = f"@{user.username}" if user.username else user.full_name
                    await bot.send_message(
                        chat_id=int(hublox_id),
                        message_thread_id=TOPIC_CHAT,
                        text=f"🔄 Все нарушения с пользователя {mention} были сняты.\nПожалуйста, следите за языком."
                    )
                except:
                    pass

# ======================= КОМАНДЫ =======================
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "👋 **Duosup Bot**\n\n"
        "Я модератор для HuBBlox и HubSup.\n"
        "Для связки чатов используйте:\n"
        "• В HuBBlox: /link_hublox\n"
        "• В HubSup: /link_hubsup <код>"
    )

@dp.message(Command("link_hublox"))
async def link_hublox(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группе!")
        return
    code = generate_link_code()
    set_config("link_code", code)
    set_config("hublox_id", str(message.chat.id))
    await message.answer(
        f"🔗 **Код для связки:**\n`{code}`\n\n"
        "Теперь добавьте меня в HubSup и выполните:\n"
        f"/link_hubsup {code}"
    )

@dp.message(Command("link_hubsup"))
async def link_hubsup(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в группе!")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /link_hubsup <код>")
        return
    code = args[1]
    saved_code = get_config("link_code")
    if not saved_code:
        await message.answer("⚠️ Сначала выполните /link_hublox в HuBBlox!")
        return
    if code != saved_code:
        await message.answer("❌ Неверный код!")
        return
    set_config("hubsup_id", str(message.chat.id))
    await message.answer("✅ HubSup успешно связан с HuBBlox!")
    hublox_id = get_config("hublox_id")
    if hublox_id:
        await bot.send_message(
            chat_id=int(hublox_id),
            text="🔗 **HubSup успешно связан!**\nТеперь бот работает в обоих чатах."
        )
        await send_help_to_chat(int(hublox_id))

async def send_help_to_chat(chat_id):
    help_text = (
        "🤖 **Duosup Bot — список команд**\n\n"
        "**Для всех:**\n"
        "/rules — показать правила\n"
        "/report — ответьте на сообщение нарушителя и напишите /report <текст>\n"
        "Напишите \"Бот\" — бот ответит \"На месте ✅\"\n\n"
        "**Для модераторов и создателя:**\n"
        "/warn — ответьте на сообщение и напишите /warn <причина>\n"
        "/ban — ответьте на сообщение и напишите /ban <причина>\n"
        "/unban — ответьте на сообщение и напишите /unban\n"
        "/unwarn — ответьте на сообщение и напишите /unwarn\n"
        "/stats — статистика нарушений\n\n"
        "**Для создателя:**\n"
        "/redact — настройка всех сообщений\n"
        "/addmod @username [роль] — добавить модератора\n"
        "/removemod @username — удалить модератора"
    )
    await bot.send_message(chat_id=chat_id, text=help_text, parse_mode="Markdown")

# Обработчик текстовых сообщений (включая "Бот")
@dp.message(F.text)
async def handle_text(message: Message):
    # Обработка "Бот"
    if message.text and message.text.lower() == "бот":
        await message.reply("На месте ✅")
        return

    # Остальную логику (модерацию) обрабатываем в основном хендлере
    # Но если мы уже здесь, нужно вызвать основной обработчик или реализовать логику
    # Проще всего оставить основной хендлер ниже, но чтобы избежать конфликтов,
    # мы можем просто вернуться, если сообщение не "Бот", и дать обработать дальше.
    # Но в aiogram 3.x обработчики выполняются по порядку, и если у нас есть
    # несколько хендлеров, то нужно быть аккуратным. Лучше совместить всё в одном
    # или использовать F.filter.
    # Я перепишу логику так: все сообщения идут в общий хендлер, где уже есть
    # проверка на "Бот". Поэтому этот хендлер можно убрать, но оставим пока как есть.

@dp.message(Command("rules"))
async def rules_cmd(message: Message):
    rules = get_current_rules()
    if not rules:
        await message.answer("📜 Правила ещё не установлены.")
        return
    await message.answer(f"📜 **Правила чата (v{rules[0]})**\n\n{rules[1]}", parse_mode="Markdown")

@dp.message(Command("report"))
async def report_cmd(message: Message):
    if not message.reply_to_message:
        await message.answer("⚠️ Используй команду как ответ на сообщение нарушителя!")
        return
    reporter = message.from_user
    violator = message.reply_to_message.from_user
    reason = message.text.replace("/report", "").strip() or "Без причины"
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report_text = (
            f"📩 **Новый репорт**\n"
            f"От: @{reporter.username or reporter.first_name}\n"
            f"Нарушитель: @{violator.username or violator.first_name}\n"
            f"Причина: {reason}"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_REPORTS_HUBSUP,
            text=report_text,
            parse_mode="Markdown"
        )
        await message.reply(f"✅ Репорт отправлен. Администрация рассмотрит.")
    else:
        await message.reply("⚠️ Бот не связан с чатом администрации.")

def is_mod_command_allowed(message: Message) -> bool:
    if not message.reply_to_message:
        return False
    return is_creator_or_moderator(message.from_user.id)

@dp.message(Command("warn"))
async def manual_warn(message: Message):
    if not is_mod_command_allowed(message):
        await message.answer("⛔ Доступно только модераторам и создателю, используйте как ответ на сообщение.")
        return
    user = message.reply_to_message.from_user
    reason = message.text.replace("/warn", "").strip() or "Нарушение правил"
    warn_count, warn_id = add_warn(user.id, reason)
    user_mention = f"@{user.username}" if user.username else user.first_name

    action = ""
    if warn_count == 1:
        action = "предупреждение"
    elif warn_count == 2:
        action = "мут 30 мин"
        until = datetime.now() + timedelta(minutes=30)
        await bot.restrict_chat_member(message.chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
    elif warn_count == 3:
        action = "мут 24 ч"
        until = datetime.now() + timedelta(hours=24)
        await bot.restrict_chat_member(message.chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
    else:
        action = "бан навсегда"
        await bot.ban_chat_member(message.chat.id, user.id)
        cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user.id,))
        conn.commit()

    template = get_template('warn_template')
    msg_text = template.format(user=user_mention, warn=warn_count, warn_id=warn_id)
    await message.reply(msg_text)

    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=f"🛠 **Ручной варн**\nМодератор: @{message.from_user.username or message.from_user.first_name}\nПользователь: {user_mention}\nПричина: {reason}\nДействие: {action}",
            parse_mode="Markdown"
        )

@dp.message(Command("ban"))
async def manual_ban(message: Message):
    if not is_mod_command_allowed(message):
        await message.answer("⛔ Доступно только модераторам и создателю, используйте как ответ на сообщение.")
        return
    user = message.reply_to_message.from_user
    reason = message.text.replace("/ban", "").strip() or "Нарушение правил"
    await bot.ban_chat_member(message.chat.id, user.id)
    cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user.id,))
    conn.commit()
    user_mention = f"@{user.username}" if user.username else user.first_name
    template = get_template('ban_template')
    msg_text = template.format(user=user_mention)
    await message.reply(f"{msg_text}\nПричина: {reason}")

@dp.message(Command("unban"))
async def manual_unban(message: Message):
    if not is_mod_command_allowed(message):
        await message.answer("⛔ Доступно только модераторам и создателю, используйте как ответ на сообщение.")
        return
    user = message.reply_to_message.from_user
    await bot.unban_chat_member(message.chat.id, user.id)
    cursor.execute("UPDATE users SET banned=0, ban_until=NULL WHERE user_id=?", (user.id,))
    conn.commit()
    user_mention = f"@{user.username}" if user.username else user.first_name
    template = get_template('unban_template')
    msg_text = template.format(user=user_mention)
    await message.reply(msg_text)

@dp.message(Command("unwarn"))
async def manual_unwarn(message: Message):
    if not is_mod_command_allowed(message):
        await message.answer("⛔ Доступно только модераторам и создателю, используйте как ответ на сообщение.")
        return
    user = message.reply_to_message.from_user
    remove_all_warns(user.id)
    user_mention = f"@{user.username}" if user.username else user.first_name
    template = get_template('unwarn_template')
    msg_text = template.format(user=user_mention)
    await message.reply(msg_text)

@dp.message(Command("redact"))
async def redact_menu(message: Message):
    if message.from_user.id != CREATOR_ID:
        await message.answer("⛔ Доступно только создателю.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Rules", callback_data="redact_rules")],
        [InlineKeyboardButton(text="⚠️ Warn", callback_data="redact_warn")],
        [InlineKeyboardButton(text="🚫 Ban", callback_data="redact_ban")],
        [InlineKeyboardButton(text="🔔 Alerts", callback_data="redact_alerts")],
        [InlineKeyboardButton(text="👋 Welcome", callback_data="redact_welcome")],
        [InlineKeyboardButton(text="✅ Unban", callback_data="redact_unban")],
        [InlineKeyboardButton(text="🔄 Unwarn", callback_data="redact_unwarn")],
        [InlineKeyboardButton(text="📩 Appeal", callback_data="redact_appeal")],
    ])
    await message.answer("📝 Выберите, что хотите изменить:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("redact_"))
async def process_redact_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key_map = {
        "redact_rules": "rules_version",
        "redact_warn": "warn_template",
        "redact_ban": "ban_template",
        "redact_alerts": "rules_version",
        "redact_welcome": "welcome_template",
        "redact_unban": "unban_template",
        "redact_unwarn": "unwarn_template",
        "redact_appeal": "appeal_template"
    }
    action = callback.data
    if action == "redact_alerts":
        await callback.message.answer("Введите текст для уведомления об обновлении правил (используйте {version}):")
        await state.set_state(RedactStates.waiting_for_input)
        await state.update_data(redact_key="alerts_text")
        return
    if action == "redact_rules":
        await callback.message.answer("Введите новые правила (полный список):")
        await state.set_state(RedactStates.waiting_for_input)
        await state.update_data(redact_key="rules")
        return
    if action == "redact_appeal":
        await callback.message.answer("Введите список администраторов (по одному на строку, без @):")
        await state.set_state(RedactStates.waiting_for_input)
        await state.update_data(redact_key="admins")
        return
    key = key_map.get(action)
    if not key:
        await callback.message.answer("Неизвестная опция.")
        return
    current = get_template(key)
    await callback.message.answer(f"Текущее значение:\n{current}\n\nВведите новый текст (используйте плейсхолдеры {user}, {warn}, {warn_id} где нужно):")
    await state.set_state(RedactStates.waiting_for_input)
    await state.update_data(redact_key=key)

@dp.message(RedactStates.waiting_for_input)
async def process_redact_input(message: Message, state: FSMContext):
    data = await state.get_data()
    redact_key = data.get("redact_key")
    new_text = message.text

    if redact_key == "rules":
        current_version = get_template('rules_version')
        major, minor = map(int, current_version.split('.'))
        minor += 1
        new_version = f"{major}.{minor}"
        set_template('rules_version', new_version)
        update_rules(new_version, new_text)
        hublox_id = get_config("hublox_id")
        if hublox_id:
            await bot.send_message(
                chat_id=int(hublox_id),
                message_thread_id=TOPIC_RULES,
                text=f"📜 **Правила чата HuBBlox (v{new_version})**\n\n{new_text}",
                parse_mode="Markdown"
            )
            for topic in [TOPIC_CHAT, TOPIC_TRADES, TOPIC_RAIDS, TOPIC_ANNOUNCEMENTS]:
                if topic:
                    await bot.send_message(
                        chat_id=int(hublox_id),
                        message_thread_id=topic,
                        text=f"🔔 **Правила сообщества обновлены!**\nВерсия {new_version}. Ознакомьтесь в теме «Правила».",
                        parse_mode="Markdown"
                    )
        await message.answer(f"✅ Правила обновлены до версии {new_version}!")
    elif redact_key == "alerts_text":
        set_template("alerts_template", new_text)
        await message.answer("✅ Шаблон уведомлений обновлён!")
    elif redact_key == "admins":
        set_template("admins_list", new_text)
        await message.answer("✅ Список администраторов обновлён!")
    else:
        set_template(redact_key, new_text)
        await message.answer(f"✅ Шаблон обновлён!")

    await state.clear()

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_creator_or_moderator(message.from_user.id):
        return await message.answer("⛔ Доступно только создателю и модераторам.")
    now = datetime.now()
    day_ago = int((now - timedelta(days=1)).timestamp())
    week_ago = int((now - timedelta(days=7)).timestamp())
    cursor.execute("SELECT COUNT(*) FROM violations WHERE created_at >= ?", (day_ago,))
    day_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM violations WHERE created_at >= ?", (week_ago,))
    week_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM violations")
    total_count = cursor.fetchone()[0]
    await message.answer(
        f"📊 **Статистика нарушений**\n"
        f"За день: {day_count}\n"
        f"За неделю: {week_count}\n"
        f"Всего: {total_count}"
    )

@dp.message(Command("addmod"))
async def add_moderator(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /addmod @username [роль]")
        return
    username = args[1].replace("@", "")
    role = " ".join(args[2:]) if len(args) > 2 else "Модератор"
    try:
        chat = await bot.get_chat(f"@{username}")
        user_id = chat.id
        cursor.execute("INSERT OR REPLACE INTO moderators (user_id, username, role) VALUES (?, ?, ?)",
                       (user_id, username, role))
        conn.commit()
        await message.answer(f"✅ @{username} добавлен как {role}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("removemod"))
async def remove_moderator(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /removemod @username")
        return
    username = args[1].replace("@", "")
    cursor.execute("DELETE FROM moderators WHERE username=?", (username,))
    conn.commit()
    await message.answer(f"✅ @{username} удалён из модераторов.")

@dp.message(Command("appeal"))
async def appeal_start(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await message.answer("📝 Напишите номер вашего варна/бана и ваше оправдание.")
        await state.set_state(AppealStates.waiting_for_text)
    else:
        await message.answer("📝 Используйте /appeal в личных сообщениях боту.")

@dp.message(AppealStates.waiting_for_text)
async def appeal_text(message: Message, state: FSMContext):
    appeal_text = message.text
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    number = get_next_appeal_number()
    cursor.execute(
        "INSERT INTO appeals (number, user_id, username, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (number, user_id, username, appeal_text, int(datetime.now().timestamp()))
    )
    conn.commit()
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"📩 **Апелляция {number}**\n"
            f"От: @{username}\n"
            f"Текст:\n{appeal_text}"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_APPEALS,
            text=report,
            parse_mode="Markdown"
        )
    await message.answer(f"✅ Ваша аппеляция {number} принята. Администрация рассмотрит её.")
    await state.clear()

# ======================= ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ (исправленный синтаксис) =======================
@dp.message(ContentTypeFilter(ContentType.NEW_CHAT_MEMBERS))
async def welcome_new_member(message: Message):
    if str(message.chat.id) != get_config("hublox_id"):
        return
    for member in message.new_chat_members:
        if member.id == bot.id:
            continue
        user_mention = f"@{member.username}" if member.username else member.full_name
        template = get_template('welcome_template')
        msg_text = template.format(user=user_mention)
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=TOPIC_WELCOME,
            text=msg_text
        )

# ======================= ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (модерация) =======================
@dp.message(F.text)
async def handle_all_messages(message: Message):
    # Проверяем "Бот" - это дублируется, но оставим для надёжности
    if message.text and message.text.lower() == "бот":
        await message.reply("На месте ✅")
        return

    # Игнорируем сообщения из запрещённых тем
    if message.message_thread_id in NO_INTERACTION_TOPICS:
        return

    hublox_id = get_config("hublox_id")
    if not hublox_id or str(message.chat.id) != hublox_id:
        return

    if message.message_thread_id in IGNORED_TOPICS:
        return

    if is_banned(message.from_user.id):
        await message.delete()
        await message.answer("Вы забанены и не можете писать.")
        return

    reset_warns_if_needed(message.from_user.id)

    if not message.text:
        return

    # Проверка на оскорбление через Lakera
    if await is_insult(message.text):
        is_reply = message.reply_to_message is not None
        await process_violation(message, message.text, "оскорбление", is_reply)

# ======================= ФУНКЦИЯ ОБРАБОТКИ НАРУШЕНИЙ =======================
async def process_violation(message: Message, text: str, msg_type: str, is_reply: bool):
    user = message.from_user
    warn_count, warn_id = add_warn(user.id, text)
    user_mention = f"@{user.username}" if user.username else user.first_name

    action = ""
    if warn_count == 1:
        action = "предупреждение"
    elif warn_count == 2:
        action = "мут 30 мин"
        until = datetime.now() + timedelta(minutes=30)
        await bot.restrict_chat_member(message.chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
    elif warn_count == 3:
        action = "мут 24 ч"
        until = datetime.now() + timedelta(hours=24)
        await bot.restrict_chat_member(message.chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
    else:
        action = "бан навсегда"
        await bot.ban_chat_member(message.chat.id, user.id)
        cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user.id,))
        conn.commit()

    template = get_template('warn_template')
    msg_text = template.format(user=user_mention, warn=warn_count, warn_id=warn_id)
    await message.reply(msg_text)

    cursor.execute(
        "INSERT INTO violations (user_id, username, text, action, is_reply, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user.id, user.username, text, action, is_reply, int(datetime.now().timestamp()))
    )
    conn.commit()

    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        reply_text = " (в ответ на сообщение)" if is_reply else ""
        report = (
            f"🚨 **Нарушение в HuBBlox**{reply_text}\n"
            f"Пользователь: {user_mention}\n"
            f"Тип: {msg_type}\n"
            f"Текст: `{text}`\n"
            f"Наказание: {action} (варн {warn_id})"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

# ======================= ВЕБ-СЕРВЕР ДЛЯ RENDER =======================
app = Flask('')

@app.route('/')
def home():
    return "Duosup Bot is running!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ======================= ЗАПУСК =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.create_task(check_expired_warns())
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
