import asyncio
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAH0cFseraGhVRMRb1WB0_gh-PzbjjVhYJA"
CREATOR_ID = 7675985792

# ======================= ID ТЕМ =======================
# HubSup (Администрация)
TOPIC_MOD_CHAT = 6          # чат модерации (отчёты)
TOPIC_APPEALS = 9           # аппеляции
TOPIC_MODLIST = 10          # состав администрации
TOPIC_REDACT = 8            # редакт (для /redactrule)
TOPIC_REPORTS = 258         # репорты

# HuBBlox (основной чат)
TOPIC_ANNOUNCEMENTS = 16    # оповещения
TOPIC_RULES = 6             # правила
TOPIC_CHAT = 7              # чат
TOPIC_APPEALS_HUBBLOX = 20  # аппеляция (не используется)
TOPIC_WELCOME = 1           # добро пожаловать
TOPIC_ADMIN = 27            # администрация
TOPIC_RAIDS = 17            # рейды
TOPIC_TRADES = 8            # трейды

# Темы, где бот НЕ должен отвечать на команды
IGNORED_TOPICS = [TOPIC_ADMIN, TOPIC_APPEALS_HUBBLOX]

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
    ban_until INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS warn_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    warn_number TEXT,
    reason TEXT,
    moderator_id INTEGER,
    chat_id INTEGER,
    message_id INTEGER,
    created_at INTEGER,
    is_active BOOLEAN DEFAULT 1
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS ban_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    ban_number TEXT,
    reason TEXT,
    moderator_id INTEGER,
    chat_id INTEGER,
    message_id INTEGER,
    created_at INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS unban_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    unban_number TEXT,
    moderator_id INTEGER,
    chat_id INTEGER,
    message_id INTEGER,
    created_at INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS unwarn_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    unwarn_number TEXT,
    moderator_id INTEGER,
    chat_id INTEGER,
    message_id INTEGER,
    created_at INTEGER
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
    appeal_number TEXT,
    user_id INTEGER,
    username TEXT,
    violation_number TEXT,
    appeal_text TEXT,
    created_at INTEGER,
    status TEXT DEFAULT 'pending'
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS appeal_blocks (
    user_id INTEGER PRIMARY KEY,
    block_until INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS moderators (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    level INTEGER DEFAULT 1,
    role TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS templates (
    key TEXT PRIMARY KEY,
    value TEXT
)
''')

# Шаблоны
defaults = {
    'welcome_template': '{user}\nДобро пожаловать в HuBBlox\nПожалуйста ознакомтесь с правилами сообщества.',
    'rules_version': '1.0'
}
for key, val in defaults.items():
    cursor.execute("INSERT OR IGNORE INTO templates (key, value) VALUES (?, ?)", (key, val))

cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('link_code', '')")
cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('hublox_id', '')")
cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('hubsup_id', '')")
conn.commit()

# ======================= ГЛОБАЛЬНЫЕ СЧЁТЧИКИ =======================
def get_next_number(counter_name):
    cursor.execute("SELECT value FROM config WHERE key=?", (counter_name,))
    row = cursor.fetchone()
    if row:
        current = int(row[0])
        new = current + 1
        cursor.execute("UPDATE config SET value=? WHERE key=?", (str(new), counter_name))
    else:
        new = 1
        cursor.execute("INSERT INTO config (key, value) VALUES (?, ?)", (counter_name, "1"))
    conn.commit()
    return new

# ======================= ФУНКЦИИ =======================
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

def get_user_warns(user_id):
    cursor.execute("SELECT warns FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_warn(user_id, reason, moderator_id, chat_id, message_id=None):
    current = get_user_warns(user_id)
    warn_id = get_next_number('warn_counter')
    warn_number = f"#-{warn_id:05d}"
    if current == 0:
        cursor.execute("INSERT INTO users (user_id, warns) VALUES (?, 1)", (user_id,))
    else:
        cursor.execute("UPDATE users SET warns = warns + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    new_warns = current + 1
    cursor.execute(
        "INSERT INTO warn_logs (user_id, warn_number, reason, moderator_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, warn_number, reason, moderator_id, chat_id, message_id, int(datetime.now().timestamp()))
    )
    conn.commit()
    return new_warns, warn_number

def remove_all_warns(user_id):
    cursor.execute("UPDATE users SET warns=0 WHERE user_id=?", (user_id,))
    cursor.execute("UPDATE warn_logs SET is_active=0 WHERE user_id=? AND is_active=1", (user_id,))
    conn.commit()

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

def get_moderator_level(user_id):
    cursor.execute("SELECT level FROM moderators WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def get_moderator_role(user_id):
    cursor.execute("SELECT role FROM moderators WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def is_creator(user_id):
    return user_id == CREATOR_ID

def is_creator_or_moderator(user_id):
    if is_creator(user_id):
        return True
    level = get_moderator_level(user_id)
    return level >= 1

def has_permission(user_id, min_level):
    if is_creator(user_id):
        return True
    level = get_moderator_level(user_id)
    return level >= min_level

def get_current_rules():
    cursor.execute("SELECT version, rule_text FROM rules ORDER BY created_at DESC LIMIT 1")
    return cursor.fetchone()

def update_rules(version, text):
    cursor.execute("INSERT INTO rules (version, rule_text, created_at) VALUES (?, ?, ?)",
                   (version, text, int(datetime.now().timestamp())))
    conn.commit()

def get_next_appeal_number():
    return f"#-{get_next_number('appeal_counter'):05d}"

def get_next_unban_number():
    return f"#-{get_next_number('unban_counter'):05d}"

def get_next_unwarn_number():
    return f"#-{get_next_number('unwarn_counter'):05d}"

def get_next_ban_number():
    return f"#-{get_next_number('ban_counter'):05d}"

def get_next_report_number():
    return f"#-{get_next_number('report_counter'):05d}"

def get_user_info(user_id):
    try:
        # можно получить через bot.get_chat, но в синхронном контексте неудобно
        # будем использовать сохранённый username из таблицы moderators или просто вернуть ID
        return f"[{user_id}](tg://user?id={user_id})"
    except:
        return str(user_id)

def format_time(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')

# ======================= КЛАССЫ СОСТОЯНИЙ FSM =======================
class AppealStates(StatesGroup):
    waiting_for_appeal = State()

class RedactRuleStates(StatesGroup):
    waiting_for_rule = State()

# ======================= ИНИЦИАЛИЗАЦИЯ БОТА =======================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ======================= ОБНОВЛЕНИЕ СОСТАВА АДМИНИСТРАЦИИ =======================
async def update_admin_list():
    """Обновляет список администраторов в темах «Состав администрации» (HubSup) и «Администрация» (HuBBlox)."""
    cursor.execute("SELECT user_id, username, level, role FROM moderators ORDER BY level DESC")
    mods = cursor.fetchall()
    if not mods:
        text = "👥 Список администраторов пуст."
    else:
        level_names = {
            1: "Младший модератор",
            2: "Модератор",
            3: "Младший администратор",
            4: "Администратор",
            5: "Главный администратор"
        }
        lines = []
        for user_id, username, level, role in mods:
            mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
            role_text = role if role else level_names.get(level, f"Уровень {level}")
            lines.append(f"{mention} — {role_text}")
        # Добавляем создателя
        lines.append(f"@{bot.username} — Создатель (владелец)")
        text = "👥 **Состав администрации:**\n" + "\n".join(lines)
    
    # Отправляем в HubSup (тема состав администрации)
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        # Удаляем предыдущее сообщение, если оно было (сохраняем message_id в config)
        old_msg_id = get_config("adminlist_msg_hubsup")
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=int(hubsup_id), message_id=int(old_msg_id))
            except:
                pass
        sent = await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MODLIST,
            text=text,
            parse_mode="Markdown"
        )
        set_config("adminlist_msg_hubsup", str(sent.message_id))
    
    # Отправляем в HuBBlox (тема администрация)
    hublox_id = get_config("hublox_id")
    if hublox_id:
        old_msg_id = get_config("adminlist_msg_hublox")
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=int(hublox_id), message_id=int(old_msg_id))
            except:
                pass
        sent = await bot.send_message(
            chat_id=int(hublox_id),
            message_thread_id=TOPIC_ADMIN,
            text=text,
            parse_mode="Markdown"
        )
        set_config("adminlist_msg_hublox", str(sent.message_id))

# ======================= ОБРАБОТЧИК КОМАНД =======================

# --- /start ---
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "👋 **Duosup Bot**\n\n"
        "Я модератор для HuBBlox.\n"
        "Для связи чатов используйте:\n"
        "• В HuBBlox: /link_hublox\n"
        "• В администрации: /link_hubsup <код>"
    )

# --- /link_hublox ---
@dp.message(Command("link_hublox"))
async def link_hublox(message: Message):
    if get_config("hublox_id") and get_config("hubsup_id"):
        await message.answer("⚠️ Чаты уже связаны. Команда игнорируется.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Только в группе!")
        return
    code = generate_link_code()
    set_config("link_code", code)
    set_config("hublox_id", str(message.chat.id))
    await message.answer(
        f"🔗 **Код:**\n`{code}`\n\n"
        "В административном чате выполните:\n"
        f"/link_hubsup {code}"
    )

# --- /link_hubsup ---
@dp.message(Command("link_hubsup"))
async def link_hubsup(message: Message):
    if get_config("hublox_id") and get_config("hubsup_id"):
        await message.answer("⚠️ Чаты уже связаны. Команда игнорируется.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Только в группе!")
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
    await message.answer("✅ Административный чат связан с HuBBlox!")
    hublox_id = get_config("hublox_id")
    if hublox_id:
        await bot.send_message(
            chat_id=int(hublox_id),
            text="🔗 **Административный чат связан!**\nБот работает в обоих чатах."
        )
    # Обновляем список администрации в обоих чатах
    await update_admin_list()

# --- /redactrule ---
@dp.message(Command("redactrule"))
async def redact_rule(message: Message, state: FSMContext):
    if not is_creator(message.from_user.id):
        await message.answer("⛔ Доступно только создателю.")
        return
    text = message.text.replace("/redactrule", "").strip()
    if text:
        await process_rule_update(message, text)
    else:
        await message.answer("📝 Введите новые правила (полный текст):")
        await state.set_state(RedactRuleStates.waiting_for_rule)

@dp.message(RedactRuleStates.waiting_for_rule)
async def get_rule_text(message: Message, state: FSMContext):
    await process_rule_update(message, message.text)
    await state.clear()

async def process_rule_update(message: Message, new_text):
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
            text=f"📜 **Правила сообщества HuBBlox (v{new_version})**\n\n{new_text}",
            parse_mode="Markdown"
        )
        for topic in [TOPIC_CHAT, TOPIC_TRADES, TOPIC_RAIDS, TOPIC_ANNOUNCEMENTS]:
            if topic:
                await bot.send_message(
                    chat_id=int(hublox_id),
                    message_thread_id=topic,
                    text=f"🔔 **Обновление правил!**\nНовая версия {new_version}. Ознакомьтесь в теме «Правила».",
                    parse_mode="Markdown"
                )
    await message.answer(f"✅ Правила обновлены до версии {new_version}!")

# --- /upmod и /downmod ---
@dp.message(Command("upmod"))
async def upmod_cmd(message: Message):
    if not is_creator(message.from_user.id):
        await message.answer("⛔ Только создатель может повышать.")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, _ = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя изменить статус создателя.")
        return
    # Проверяем, есть ли уже в модераторах
    cursor.execute("SELECT level FROM moderators WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row:
        current_level = row[0]
        if current_level >= 5:
            await message.answer("❌ Пользователь уже на максимальном уровне (Главный администратор).")
            return
        new_level = current_level + 1
        cursor.execute("UPDATE moderators SET level=?, role=? WHERE user_id=?", (new_level, get_role_name(new_level), user_id))
    else:
        new_level = 1  # начинаем с младшего модератора
        cursor.execute("INSERT INTO moderators (user_id, username, level, role) VALUES (?, ?, ?, ?)",
                       (user_id, username, new_level, get_role_name(new_level)))
    conn.commit()
    await message.answer(f"✅ @{username} повышен до уровня {new_level} ({get_role_name(new_level)}).")
    await update_admin_list()

@dp.message(Command("downmod"))
async def downmod_cmd(message: Message):
    if not is_creator(message.from_user.id):
        await message.answer("⛔ Только создатель может понижать.")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, _ = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя изменить статус создателя.")
        return
    cursor.execute("SELECT level FROM moderators WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        await message.answer("⚠️ Пользователь не является модератором.")
        return
    current_level = row[0]
    if current_level <= 1:
        await message.answer("❌ Нельзя понизить ниже уровня 1.")
        return
    new_level = current_level - 1
    cursor.execute("UPDATE moderators SET level=?, role=? WHERE user_id=?", (new_level, get_role_name(new_level), user_id))
    conn.commit()
    await message.answer(f"✅ @{username} понижен до уровня {new_level} ({get_role_name(new_level)}).")
    await update_admin_list()

def get_role_name(level):
    roles = {
        1: "Младший модератор",
        2: "Модератор",
        3: "Младший администратор",
        4: "Администратор",
        5: "Главный администратор"
    }
    return roles.get(level, f"Уровень {level}")

def parse_target_user(message: Message):
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        return user.id, user.username, message.reply_to_message.message_id
    text = message.text
    match = re.search(r'@(\w+)', text)
    if match:
        username = match.group(1)
        try:
            # ищем в модераторах
            cursor.execute("SELECT user_id FROM moderators WHERE username=?", (username,))
            row = cursor.fetchone()
            if row:
                return row[0], username, None
            # если не нашли, может быть обычный пользователь, тогда пытаемся через API
            # но для простоты вернём None
            return None
        except:
            return None
    return None

# --- Остальные команды с проверкой прав ---

def check_permission(message: Message, min_level):
    if is_creator(message.from_user.id):
        return True
    level = get_moderator_level(message.from_user.id)
    return level >= min_level

# --- /warn (минимальный уровень 2) ---
@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    if not check_permission(message, 2):
        await message.answer("⛔ Недостаточно прав (требуется уровень 2+).")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя выдать варн создателю.")
        return
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя выдать варн самому себе.")
        return
    reason = message.text.replace("/warn", "").strip()
    if not reason:
        await message.answer("⚠️ Укажите причину: /warn причина")
        return
    if is_banned(user_id):
        await message.answer("⚠️ Пользователь уже забанен.")
        return
    warn_count, warn_number = add_warn(user_id, reason, message.from_user.id, message.chat.id, msg_id)
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    
    # Сообщение в чат (HuBBlox)
    chat_msg = f"{user_mention} получает варн ({warn_count}/4) ⚠️\nПричина: {reason}\n— · — · — · — · — · — · — · — · —\n"
    if warn_count == 1:
        chat_msg += "  • 1/4 - предупреждение\n"
    elif warn_count == 2:
        chat_msg += "  • 2/4 - мут на 5 минут\n"
    elif warn_count == 3:
        chat_msg += "  • 3/4 - мут на 24 часа\n"
    elif warn_count >= 4:
        chat_msg += "  • 4/4 - бан\n"
    chat_msg += f"— · — · — · — · — · — · — · — · —\nНомер нарушения: {warn_number}\n— · — · — · — · — · — · — · — · —\nПодать аппеляцию можно:\nContact us — связаться с нами"
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    # Отчёт в админ-чат
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**ВЫДАН ВАРН** ⚠️\n"
            f"Цитирование: {reason}\n"
            f"Номер нарушения: {warn_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Причина: {reason}\n"
            f"Предупреждений: {warn_count}/4\n"
            f"Кем выдан: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        kb = None
        if msg_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
            ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown",
            reply_markup=kb
        )

    if warn_count >= 4:
        await bot.ban_chat_member(message.chat.id, user_id)
        cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user_id,))
        conn.commit()

# --- /ban (уровень 3+) ---
@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not check_permission(message, 3):
        await message.answer("⛔ Недостаточно прав (требуется уровень 3+).")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя забанить создателя.")
        return
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя забанить самого себя.")
        return
    reason = message.text.replace("/ban", "").strip()
    if not reason:
        await message.answer("⚠️ Укажите причину: /ban причина")
        return
    if is_banned(user_id):
        await message.answer("⚠️ Пользователь уже забанен.")
        return
    await bot.ban_chat_member(message.chat.id, user_id)
    cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user_id,))
    conn.commit()
    ban_number = get_next_ban_number()
    cursor.execute(
        "INSERT INTO ban_logs (user_id, ban_number, reason, moderator_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, ban_number, reason, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    )
    conn.commit()
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"{user_mention} получает бан ⚠️\nПричина: {reason}\n— · — · — · — · — · — · — · — · —\nНомер бана: {ban_number}\n— · — · — · — · — · — · — · — · —\nПодать аппеляцию можно:\nContact us — связаться с нами"
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    # Отчёт в админ-чат
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**ВЫДАН БАН** ⚠️\n"
            f"Цитирование: {reason}\n"
            f"Номер бана: {ban_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Причина: {reason}\n"
            f"Кем выдан: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        kb = None
        if msg_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
            ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown",
            reply_markup=kb
        )

# --- /unban (уровень 5+) ---
@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if not check_permission(message, 5):
        await message.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя разбанить создателя.")
        return
    if not is_banned(user_id):
        await message.answer("⚠️ Пользователь не забанен.")
        return
    await bot.unban_chat_member(message.chat.id, user_id)
    cursor.execute("UPDATE users SET banned=0, ban_until=NULL WHERE user_id=?", (user_id,))
    conn.commit()
    unban_number = get_next_unban_number()
    cursor.execute(
        "INSERT INTO unban_logs (user_id, unban_number, moderator_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, unban_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    )
    conn.commit()
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"С пользователя {user_mention} сняты ограничения (0/4)\n— · — · — · — · — · — · — · — · —\nПожалуйста прочитайте правила сообщества по лучше.\n— · — · — · — · — · — · — · — · —\nНомер снятия: {unban_number}"
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**СНЯТ БАН** ⚠️\n"
            f"Цитирование: (разбан)\n"
            f"Номер снятия: {unban_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Кем снят: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\n"
            f"Пожалуйста ознакомитесь с правилами сообщества по лучше."
        )
        kb = None
        if msg_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
            ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown",
            reply_markup=kb
        )

# --- /unwarn (уровень 4+) ---
@dp.message(Command("unwarn"))
async def unwarn_cmd(message: Message):
    if not check_permission(message, 4):
        await message.answer("⛔ Недостаточно прав (требуется уровень 4+).")
        return
    target = parse_target_user(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя снять варн с создателя.")
        return
    warns = get_user_warns(user_id)
    if warns == 0:
        await message.answer("⚠️ У пользователя нет активных варнов.")
        return
    remove_all_warns(user_id)
    unwarn_number = get_next_unwarn_number()
    cursor.execute(
        "INSERT INTO unwarn_logs (user_id, unwarn_number, moderator_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, unwarn_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    )
    conn.commit()
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"С пользователя {user_mention} сняты ограничения (0/4)\n— · — · — · — · — · — · — · — · —\nПожалуйста прочитайте правила сообщества по лучше.\n— · — · — · — · — · — · — · — · —\nНомер снятия: {unwarn_number}"
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**СНЯТ ВАРН** ⚠️\n"
            f"Цитирование: (снятие варнов)\n"
            f"Номер снятия: {unwarn_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Кем снят: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\n"
            f"Пожалуйста ознакомитесь с правилами сообщества по лучше."
        )
        kb = None
        if msg_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
            ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown",
            reply_markup=kb
        )

# --- /report (доступно всем, но обрабатывается модераторами) ---
@dp.message(Command("report"))
async def report_cmd(message: Message):
    if not message.reply_to_message:
        await message.answer("⚠️ Используйте команду как ответ на сообщение нарушителя.")
        return
    reporter = message.from_user
    violator = message.reply_to_message.from_user
    reason = message.text.replace("/report", "").strip()
    if not reason:
        await message.answer("⚠️ Укажите причину репорта: /report причина")
        return
    report_number = get_next_report_number()
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report_text = (
            f"**Получен Репорт {report_number}**\n"
            f"Отправил: @{reporter.username or reporter.first_name}\n"
            f"На кого: @{violator.username or violator.first_name}\n"
            f"ID чата: `{message.chat.id}`\n"
            f"Причина: {reason}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Рассмотреть", callback_data=f"report_{report_number}_{violator.id}_{reporter.id}")]
        ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_REPORTS,
            text=report_text,
            parse_mode="Markdown",
            reply_markup=kb
        )
        await message.reply("✅ Репорт отправлен администрации.")
    else:
        await message.reply("⚠️ Бот не связан с административным чатом.")

# --- /stats (уровень 5+) ---
@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not check_permission(message, 5):
        await message.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    cursor.execute("SELECT COUNT(*) FROM warn_logs WHERE is_active=1")
    warns = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ban_logs")
    bans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM unban_logs")
    unbans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM unwarn_logs")
    unwarns = cursor.fetchone()[0]
    await message.answer(
        f"📊 **Статистика**\n"
        f"Активных варнов: {warns}\n"
        f"Всего банов: {bans}\n"
        f"Всего разбанов: {unbans}\n"
        f"Всего снятий варнов: {unwarns}"
    )

# --- Обработчик "Бот" ---
@dp.message(F.text)
async def bot_mention(message: Message):
    if message.text and message.text.lower() == "бот":
        await message.reply("На месте ✅")

# ======================= АППЕЛЯЦИИ =======================
@dp.message(Command("appeal"))
async def appeal_start(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("📝 Используйте /appeal в личных сообщениях бота.")
        return
    cursor.execute("SELECT block_until FROM appeal_blocks WHERE user_id=?", (message.from_user.id,))
    row = cursor.fetchone()
    now = int(datetime.now().timestamp())
    if row and row[0] > now:
        await message.answer(f"⏳ Вы отправили слишком много заявок. Подождите до {datetime.fromtimestamp(row[0]).strftime('%H:%M:%S')}.")
        return
    cursor.execute("SELECT COUNT(*) FROM appeals WHERE user_id=? AND status='pending'", (message.from_user.id,))
    pending = cursor.fetchone()[0]
    if pending > 0:
        await message.answer("⚠️ У вас уже есть ожидающая рассмотрения заявка.")
        return
    await message.answer(
        "📝 **Подача аппеляции**\n"
        "Каждая строчка с новой строки пишите правильно, при повторной отправке ваша аппеляция будет удалена!\n"
        "Повторная аппеляция будет доступна через 1 час.\n"
        "— · — · — · — · — · — · — · — · —\n"
        "Заявка: что в [] не нужно писать в аппеляцию.\n"
        "[Номер нарушения/бана не путайте!] #-xxx.xxx варн/бан\n"
        "[Ваш] @username\n"
        "[Обжалование:]"
    )
    await state.set_state(AppealStates.waiting_for_appeal)

@dp.message(AppealStates.waiting_for_appeal)
async def appeal_text(message: Message, state: FSMContext):
    text = message.text
    lines = text.split('\n')
    violation_number = None
    username = None
    appeal_text = []
    for line in lines:
        if line.startswith('#-'):
            violation_number = line.strip()
        elif line.startswith('@'):
            username = line.strip()
        else:
            if line.strip():
                appeal_text.append(line.strip())
    if not violation_number or not username:
        await message.answer("❌ Неверный формат. Пожалуйста, следуйте шаблону.")
        return
    one_hour_ago = int(datetime.now().timestamp()) - 3600
    cursor.execute("SELECT COUNT(*) FROM appeals WHERE user_id=? AND created_at > ?", (message.from_user.id, one_hour_ago))
    count = cursor.fetchone()[0]
    if count >= 2:
        block_until = int(datetime.now().timestamp()) + 3600
        cursor.execute("INSERT OR REPLACE INTO appeal_blocks (user_id, block_until) VALUES (?, ?)",
                       (message.from_user.id, block_until))
        conn.commit()
        await message.answer("⛔ Вы отправили 2 заявки за час. Доступ к аппеляциям заблокирован на 1 час.")
        await state.clear()
        return
    appeal_number = get_next_appeal_number()
    cursor.execute(
        "INSERT INTO appeals (appeal_number, user_id, username, violation_number, appeal_text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (appeal_number, message.from_user.id, username, violation_number, "\n".join(appeal_text), int(datetime.now().timestamp()))
    )
    conn.commit()
    await message.answer(f"✅ Ваша аппеляция {appeal_number} принята. Ожидайте решения.")
    await state.clear()
    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**Аппеляция {appeal_number}**\n"
            f"{violation_number} варн / бан\n"
            f"{username}\n"
            f"<Цитированный текст обжалования>\n"
            f"{' '.join(appeal_text)}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Принять", callback_data=f"appeal_approve_{appeal_number}_{message.from_user.id}"),
             InlineKeyboardButton(text="Отказать", callback_data=f"appeal_reject_{appeal_number}_{message.from_user.id}")]
        ])
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_APPEALS,
            text=report,
            parse_mode="Markdown",
            reply_markup=kb
        )

# ======================= ОБРАБОТЧИКИ КНОПОК =======================
@dp.callback_query(lambda c: c.data.startswith("appeal_"))
async def appeal_callback(callback: CallbackQuery):
    data = callback.data.split('_')
    action = data[1]  # approve или reject
    appeal_number = data[2]
    user_id = int(data[3])
    # проверяем права: только модераторы с уровнем 1 и выше могут обрабатывать аппеляции
    if not check_permission(callback.message, 1):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    if action == "approve":
        cursor.execute("UPDATE appeals SET status='approved' WHERE appeal_number=?", (appeal_number,))
        conn.commit()
        await bot.send_message(chat_id=user_id, text="✅ Ваша заявка была одобрена.")
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ Одобрено модератором.")
    else:
        cursor.execute("UPDATE appeals SET status='rejected' WHERE appeal_number=?", (appeal_number,))
        conn.commit()
        await bot.send_message(chat_id=user_id, text="❌ Ваша заявка была отклонена.")
        await callback.message.edit_text(f"{callback.message.text}\n\n❌ Отказано модератором.")
    await callback.answer("Готово.")

@dp.callback_query(lambda c: c.data.startswith("report_"))
async def report_callback(callback: CallbackQuery):
    # Только модераторы с уровнем 1+ могут рассмотреть репорт
    if not check_permission(callback.message, 1):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    await callback.answer("Репорт отправлен на рассмотрение.", show_alert=True)

# ======================= ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ =======================
@dp.message(F.content_type.in_({ContentType.NEW_CHAT_MEMBERS}))
async def welcome_new_member(message: Message):
    hublox_id = get_config("hublox_id")
    if not hublox_id or str(message.chat.id) != hublox_id:
        return
    for member in message.new_chat_members:
        if member.id == bot.id:
            continue
        user_mention = f"@{member.username}" if member.username else member.full_name
        template = get_template('welcome_template')
        if '{user}' in template:
            msg = template.format(user=user_mention)
        else:
            msg = f"{user_mention}\n{template}"
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=TOPIC_WELCOME,
            text=msg
        )

# ======================= ЗАПУСК =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    # При запуске обновляем список администрации
    await update_admin_list()
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
