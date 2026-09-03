import os
import asyncio
import logging
import secrets
import string
from datetime import datetime, timedelta
import re
import asyncpg

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAH0cFseraGhVRMRb1WB0_gh-PzbjjVhYJA"
CREATOR_ID = 7675985792
DATABASE_URL = os.environ.get("DATABASE_URL")  # Railway подставит

# ======================= ID ТЕМ =======================
TOPIC_MOD_CHAT = 6
TOPIC_APPEALS = 9
TOPIC_MODLIST = 10
TOPIC_REDACT = 8
TOPIC_REPORTS = 258

TOPIC_ANNOUNCEMENTS = 16
TOPIC_RULES = 6
TOPIC_CHAT = 7
TOPIC_APPEALS_HUBBLOX = 20
TOPIC_WELCOME = 1
TOPIC_ADMIN = 27
TOPIC_RAIDS = 17
TOPIC_TRADES = 8

IGNORED_TOPICS = [TOPIC_ADMIN, TOPIC_APPEALS_HUBBLOX]

# ======================= ПОДКЛЮЧЕНИЕ К БАЗЕ =======================
db = None

async def init_db():
    global db
    db = await asyncpg.connect(DATABASE_URL)
    # Создаём таблицы, если их нет
    await db.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            warns INTEGER DEFAULT 0,
            banned BOOLEAN DEFAULT FALSE,
            ban_until BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS warn_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            warn_number TEXT,
            reason TEXT,
            moderator_id BIGINT,
            chat_id BIGINT,
            message_id BIGINT,
            created_at BIGINT,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS ban_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            ban_number TEXT,
            reason TEXT,
            moderator_id BIGINT,
            chat_id BIGINT,
            message_id BIGINT,
            created_at BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS unban_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            unban_number TEXT,
            moderator_id BIGINT,
            chat_id BIGINT,
            message_id BIGINT,
            created_at BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS unwarn_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            unwarn_number TEXT,
            moderator_id BIGINT,
            chat_id BIGINT,
            message_id BIGINT,
            created_at BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS rules (
            version TEXT PRIMARY KEY,
            rule_text TEXT,
            created_at BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS appeals (
            id SERIAL PRIMARY KEY,
            appeal_number TEXT,
            user_id BIGINT,
            username TEXT,
            violation_number TEXT,
            appeal_text TEXT,
            created_at BIGINT,
            status TEXT DEFAULT 'pending'
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS appeal_blocks (
            user_id BIGINT PRIMARY KEY,
            block_until BIGINT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS moderators (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            level INTEGER DEFAULT 1,
            role TEXT
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Инициализация шаблонов
    await db.execute("INSERT INTO templates (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
                     "welcome_template", "{user}\nДобро пожаловать в HuBBlox\nПожалуйста ознакомтесь с правилами сообщества.")
    await db.execute("INSERT INTO templates (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
                     "rules_version", "1.0")
    for counter in ['warn_counter', 'ban_counter', 'unban_counter', 'unwarn_counter', 'appeal_counter', 'report_counter']:
        await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", counter, "0")
    await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", "link_code", "")
    await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", "hublox_id", "")
    await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", "hubsup_id", "")

# ======================= ФУНКЦИИ РАБОТЫ С БД =======================
async def get_config(key):
    row = await db.fetchrow("SELECT value FROM config WHERE key=$1", key)
    return row[0] if row else None

async def set_config(key, value):
    await db.execute("UPDATE config SET value=$1 WHERE key=$2", value, key)

async def get_template(key):
    row = await db.fetchrow("SELECT value FROM templates WHERE key=$1", key)
    return row[0] if row else None

async def set_template(key, value):
    await db.execute("UPDATE templates SET value=$1 WHERE key=$2", value, key)

async def get_next_number(counter_name):
    row = await db.fetchrow("SELECT value FROM config WHERE key=$1", counter_name)
    if row:
        current = int(row[0])
        new = current + 1
        await db.execute("UPDATE config SET value=$1 WHERE key=$2", str(new), counter_name)
    else:
        new = 1
        await db.execute("INSERT INTO config (key, value) VALUES ($1, $2)", counter_name, str(new))
    return new

def format_number(num):
    return f"#-{(num // 1000):03d}.{(num % 1000):03d}"

async def get_user_warns(user_id):
    row = await db.fetchrow("SELECT warns FROM users WHERE user_id=$1", user_id)
    return row[0] if row else 0

async def add_warn(user_id, reason, moderator_id, chat_id, message_id=None):
    current = await get_user_warns(user_id)
    warn_id = await get_next_number('warn_counter')
    warn_number = format_number(warn_id)
    if current == 0:
        await db.execute("INSERT INTO users (user_id, warns) VALUES ($1, 1)", user_id)
    else:
        await db.execute("UPDATE users SET warns = warns + 1 WHERE user_id=$1", user_id)
    new_warns = current + 1
    await db.execute(
        "INSERT INTO warn_logs (user_id, warn_number, reason, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        user_id, warn_number, reason, moderator_id, chat_id, message_id, int(datetime.now().timestamp())
    )
    return new_warns, warn_number

async def remove_all_warns(user_id):
    await db.execute("UPDATE users SET warns=0 WHERE user_id=$1", user_id)
    await db.execute("UPDATE warn_logs SET is_active=FALSE WHERE user_id=$1 AND is_active=TRUE", user_id)

async def is_banned(user_id):
    row = await db.fetchrow("SELECT banned, ban_until FROM users WHERE user_id=$1", user_id)
    if not row:
        return False
    banned, until = row
    if banned and until is None:
        return True
    if banned and until and datetime.now().timestamp() > until:
        await db.execute("UPDATE users SET banned=FALSE, ban_until=NULL WHERE user_id=$1", user_id)
        return False
    return bool(banned)

async def get_moderator_level(user_id):
    row = await db.fetchrow("SELECT level FROM moderators WHERE user_id=$1", user_id)
    return row[0] if row else 0

async def is_creator(user_id):
    return user_id == CREATOR_ID

async def check_permission(message, min_level):
    if await is_creator(message.from_user.id):
        return True
    level = await get_moderator_level(message.from_user.id)
    return level >= min_level

async def get_target_user_from_message(message):
    """Асинхронно определяет цель команды (reply или @username)."""
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        return user.id, user.username, message.reply_to_message.message_id
    text = message.text
    match = re.search(r'@(\w+)', text)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, username, None
        except:
            return None
    return None

async def get_current_rules():
    row = await db.fetchrow("SELECT version, rule_text FROM rules ORDER BY created_at DESC LIMIT 1")
    return row[0], row[1] if row else None

async def update_rules(version, text):
    await db.execute("INSERT INTO rules (version, rule_text, created_at) VALUES ($1, $2, $3)", version, text, int(datetime.now().timestamp()))

async def get_next_appeal_number():
    num = await get_next_number('appeal_counter')
    return format_number(num)

async def get_next_unban_number():
    num = await get_next_number('unban_counter')
    return format_number(num)

async def get_next_unwarn_number():
    num = await get_next_number('unwarn_counter')
    return format_number(num)

async def get_next_ban_number():
    num = await get_next_number('ban_counter')
    return format_number(num)

async def get_next_report_number():
    num = await get_next_number('report_counter')
    return format_number(num)

def get_role_name(level):
    roles = {
        1: "Младший модератор",
        2: "Модератор",
        3: "Младший администратор",
        4: "Администратор",
        5: "Главный администратор"
    }
    return roles.get(level, f"Уровень {level}")

async def set_admin_rights(chat_id, user_id, is_admin=True, custom_title=None):
    """Назначает или снимает права администратора и устанавливает тег."""
    try:
        if is_admin:
            await bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                can_delete_messages=True,
                can_restrict_members=True,
                can_invite_users=False,
                can_change_info=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_topics=False,
                can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                custom_title=custom_title  # тег/звание
            )
        else:
            # Снимаем все права и сбрасываем звание
            await bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                can_delete_messages=False,
                can_restrict_members=False,
                can_invite_users=False,
                can_change_info=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_topics=False,
                can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                custom_title=""  # пустая строка сбрасывает звание
            )
        return True
    except Exception as e:
        logging.error(f"Ошибка при изменении прав: {e}")
        return False

async def update_admin_list():
    rows = await db.fetch("SELECT user_id, username, level, role FROM moderators ORDER BY level DESC")
    if not rows:
        text = "👥 Список администраторов пуст."
    else:
        lines = []
        for row in rows:
            user_id, username, level, role = row
            mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
            role_text = role if role else get_role_name(level)
            lines.append(f"{mention} — {role_text}")
        lines.append(f"@{bot.username} — Создатель (владелец)")
        text = "👥 **Состав администрации:**\n" + "\n".join(lines)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        old_msg_id = await get_config("adminlist_msg_hubsup")
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
        await set_config("adminlist_msg_hubsup", str(sent.message_id))

    hublox_id = await get_config("hublox_id")
    if hublox_id:
        old_msg_id = await get_config("adminlist_msg_hublox")
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
        await set_config("adminlist_msg_hublox", str(sent.message_id))

# ======================= КЛАССЫ FSM =======================
class AppealStates(StatesGroup):
    waiting_for_appeal = State()

class RedactRuleStates(StatesGroup):
    waiting_for_rule = State()

# ======================= БОТ =======================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ======================= КОМАНДЫ =======================

@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "👋 **Duosup Bot**\n\n"
        "Я модератор для HuBBlox.\n"
        "Для связи чатов используйте:\n"
        "• В HuBBlox: /link_hublox\n"
        "• В администрации: /link_hubsup <код>"
    )

def generate_link_code():
    alphabet = string.ascii_lowercase + string.digits
    parts = [''.join(secrets.choice(alphabet) for _ in range(5)) for _ in range(5)]
    return '-'.join(parts)

@dp.message(Command("link_hublox"))
async def link_hublox(message: Message):
    if await get_config("hublox_id") and await get_config("hubsup_id"):
        await message.answer("⚠️ Чаты уже связаны. Команда игнорируется.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Только в группе!")
        return
    code = generate_link_code()
    await set_config("link_code", code)
    await set_config("hublox_id", str(message.chat.id))
    await message.answer(
        f"🔗 **Код:**\n`{code}`\n\n"
        "В административном чате выполните:\n"
        f"/link_hubsup {code}"
    )

@dp.message(Command("link_hubsup"))
async def link_hubsup(message: Message):
    if await get_config("hublox_id") and await get_config("hubsup_id"):
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
    saved_code = await get_config("link_code")
    if not saved_code:
        await message.answer("⚠️ Сначала выполните /link_hublox в HuBBlox!")
        return
    if code != saved_code:
        await message.answer("❌ Неверный код!")
        return
    await set_config("hubsup_id", str(message.chat.id))
    await message.answer("✅ Административный чат связан с HuBBlox!")
    hublox_id = await get_config("hublox_id")
    if hublox_id:
        await bot.send_message(
            chat_id=int(hublox_id),
            text="🔗 **Административный чат связан!**\nБот работает в обоих чатах."
        )
    await update_admin_list()

@dp.message(Command("redactrule"))
async def redact_rule(message: Message, state: FSMContext):
    if not await is_creator(message.from_user.id):
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
    current_version = await get_template('rules_version')
    major, minor = map(int, current_version.split('.'))
    minor += 1
    new_version = f"{major}.{minor}"
    await set_template('rules_version', new_version)
    await update_rules(new_version, new_text)

    hublox_id = await get_config("hublox_id")
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

# --- upmod / downmod ---
@dp.message(Command("upmod"))
async def upmod_cmd(message: Message):
    if not await is_creator(message.from_user.id):
        await message.answer("⛔ Только создатель может повышать.")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, _ = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя изменить статус создателя.")
        return
    row = await db.fetchrow("SELECT level FROM moderators WHERE user_id=$1", user_id)
    if row:
        current_level = row[0]
        if current_level >= 5:
            await message.answer("❌ Пользователь уже на максимальном уровне (Главный администратор).")
            return
        new_level = current_level + 1
        await db.execute("UPDATE moderators SET level=$1, role=$2 WHERE user_id=$3", new_level, get_role_name(new_level), user_id)
    else:
        new_level = 1
        await db.execute("INSERT INTO moderators (user_id, username, level, role) VALUES ($1, $2, $3, $4)",
                         user_id, username, new_level, get_role_name(new_level))
    # Назначаем права и тег в обоих чатах
    role_title = get_role_name(new_level)
    hublox_id = await get_config("hublox_id")
    hubsup_id = await get_config("hubsup_id")
    if hublox_id:
        await set_admin_rights(int(hublox_id), user_id, is_admin=True, custom_title=role_title)
    if hubsup_id:
        await set_admin_rights(int(hubsup_id), user_id, is_admin=True, custom_title=role_title)
    await message.answer(f"✅ @{username} повышен до уровня {new_level} ({role_title}) и получил права администратора с тегом «{role_title}».")
    await update_admin_list()

@dp.message(Command("downmod"))
async def downmod_cmd(message: Message):
    if not await is_creator(message.from_user.id):
        await message.answer("⛔ Только создатель может понижать.")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    user_id, username, _ = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя изменить статус создателя.")
        return
    row = await db.fetchrow("SELECT level FROM moderators WHERE user_id=$1", user_id)
    if not row:
        await message.answer("⚠️ Пользователь не является модератором.")
        return
    current_level = row[0]
    if current_level == 0:
        await message.answer("⚠️ Пользователь уже имеет уровень 0 (участник).")
        return
    new_level = current_level - 1
    if new_level == 0:
        await db.execute("DELETE FROM moderators WHERE user_id=$1", user_id)
        # Снимаем права и сбрасываем тег в обоих чатах
        hublox_id = await get_config("hublox_id")
        hubsup_id = await get_config("hubsup_id")
        if hublox_id:
            await set_admin_rights(int(hublox_id), user_id, is_admin=False)
        if hubsup_id:
            await set_admin_rights(int(hubsup_id), user_id, is_admin=False)
        await message.answer(f"✅ @{username} понижен до уровня 0 (участник) и удалён из списка модераторов, права администратора и тег сняты.")
    else:
        await db.execute("UPDATE moderators SET level=$1, role=$2 WHERE user_id=$3", new_level, get_role_name(new_level), user_id)
        # Обновляем тег на новый уровень
        role_title = get_role_name(new_level)
        hublox_id = await get_config("hublox_id")
        hubsup_id = await get_config("hubsup_id")
        if hublox_id:
            await set_admin_rights(int(hublox_id), user_id, is_admin=True, custom_title=role_title)
        if hubsup_id:
            await set_admin_rights(int(hubsup_id), user_id, is_admin=True, custom_title=role_title)
        await message.answer(f"✅ @{username} понижен до уровня {new_level} ({role_title}), тег обновлён.")
    await update_admin_list()

# --- /warn ---
@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    if not await check_permission(message, 2):
        await message.answer("⛔ Недостаточно прав (требуется уровень 2+).")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
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
    if await is_banned(user_id):
        await message.answer("⚠️ Пользователь уже забанен.")
        return

    warn_count, warn_number = await add_warn(user_id, reason, message.from_user.id, message.chat.id, msg_id)
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"

    levels = [
        ("предупреждение", 1),
        ("мут на 5 минут", 2),
        ("мут на 24 часа", 3),
        ("бан", 4)
    ]
    level_lines = []
    for label, level in levels:
        if level == warn_count:
            level_lines.append(f" • {level}/4 - {label} {chr(9888)}")
        else:
            level_lines.append(f" • {level}/4 - {label}")
    levels_text = "\n".join(level_lines)

    chat_msg = (
        f"{user_mention} получает варн ({warn_count}/4) ⚠️\n"
        f"Причина: «{reason}»\n"
        f"— · — · — · — · — · — · —\n"
        f"{levels_text}\n"
        f"— · — · — · — · — · — · —\n"
        f"ID варна: {warn_number}\n"
        f"— · — · — · — · — · — · —"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]
    ])
    if msg_id:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        )
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**ВЫДАН ВАРН** ⚠️\n"
            f"Причина: {reason}\n"
            f"ID варна: {warn_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Предупреждений: {warn_count}/4\n"
            f"Кем выдан: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

    if warn_count >= 4:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", user_id)

# --- /ban ---
@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not await check_permission(message, 3):
        await message.answer("⛔ Недостаточно прав (требуется уровень 3+).")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
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
    if await is_banned(user_id):
        await message.answer("⚠️ Пользователь уже забанен.")
        return

    await bot.restrict_chat_member(
        chat_id=message.chat.id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=False)
    )
    await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", user_id)
    ban_number = await get_next_ban_number()
    await db.execute(
        "INSERT INTO ban_logs (user_id, ban_number, reason, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        user_id, ban_number, reason, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp())
    )
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"

    chat_msg = (
        f"{user_mention} получает бан ⚠️\n"
        f"Причина: «{reason}»\n"
        f"— · — · — · — · — · — · —\n"
        f"ID бана: {ban_number}\n"
        f"— · — · — · — · — · — · —"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]
    ])
    if msg_id:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        )
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**ВЫДАН БАН** ⚠️\n"
            f"Причина: {reason}\n"
            f"ID бана: {ban_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Кем выдан: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

# --- /unwarn ---
@dp.message(Command("unwarn"))
async def unwarn_cmd(message: Message):
    if not await check_permission(message, 4):
        await message.answer("⛔ Недостаточно прав (требуется уровень 4+).")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя снять варн с создателя.")
        return
    warns = await get_user_warns(user_id)
    if warns == 0:
        await message.answer("⚠️ У пользователя нет активных варнов.")
        return
    await remove_all_warns(user_id)
    unwarn_number = await get_next_unwarn_number()
    await db.execute(
        "INSERT INTO unwarn_logs (user_id, unwarn_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
        user_id, unwarn_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp())
    )
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = (
        f"С пользователя {user_mention} сняты ограничения (0/4)\n"
        f"— · — · — · — · — · — · —\n"
        f"Пожалуйста прочитайте правила сообщества по лучше.\n"
        f"— · — · — · — · — · — · —\n"
        f"Номер снятия: {unwarn_number}"
    )
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**СНЯТ ВАРН** ⚠️\n"
            f"Причина: (снятие варнов)\n"
            f"Номер снятия: {unwarn_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Кем снят: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\n"
            f"Пожалуйста ознакомитесь с правилами сообщества по лучше."
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

# --- /unban ---
@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if not await check_permission(message, 5):
        await message.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    target = await get_target_user_from_message(message)
    if not target:
        await message.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return
    user_id, username, msg_id = target
    if user_id == CREATOR_ID:
        await message.answer("❌ Нельзя разбанить создателя.")
        return
    if not await is_banned(user_id):
        await message.answer("⚠️ Пользователь не забанен.")
        return
    await bot.restrict_chat_member(
        chat_id=message.chat.id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=True)
    )
    await db.execute("UPDATE users SET banned=FALSE, ban_until=NULL WHERE user_id=$1", user_id)
    unban_number = await get_next_unban_number()
    await db.execute(
        "INSERT INTO unban_logs (user_id, unban_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
        user_id, unban_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp())
    )
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = (
        f"С пользователя {user_mention} сняты ограничения (0/4)\n"
        f"— · — · — · — · — · — · —\n"
        f"Пожалуйста прочитайте правила сообщества по лучше.\n"
        f"— · — · — · — · — · — · —\n"
        f"Номер снятия: {unban_number}"
    )
    keyboard = None
    if msg_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{msg_id}")]
        ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**СНЯТ БАН** ⚠️\n"
            f"Причина: (разбан)\n"
            f"Номер снятия: {unban_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user_id}`\n"
            f"Кем снят: @{message.from_user.username or message.from_user.first_name}\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\n"
            f"Пожалуйста ознакомитесь с правилами сообщества по лучше."
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

# --- /report ---
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
    report_number = await get_next_report_number()
    hubsup_id = await get_config("hubsup_id")
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

# --- /stats ---
@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not await check_permission(message, 5):
        await message.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    warns = await db.fetchval("SELECT COUNT(*) FROM warn_logs WHERE is_active=TRUE")
    bans = await db.fetchval("SELECT COUNT(*) FROM ban_logs")
    unbans = await db.fetchval("SELECT COUNT(*) FROM unban_logs")
    unwarns = await db.fetchval("SELECT COUNT(*) FROM unwarn_logs")
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

# ======================= АППЕЛЯЦИИ (ЛС) =======================
@dp.message(Command("appeal"))
async def appeal_start(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("📝 Используйте /appeal в личных сообщениях бота.")
        return
    row = await db.fetchrow("SELECT block_until FROM appeal_blocks WHERE user_id=$1", message.from_user.id)
    now = int(datetime.now().timestamp())
    if row and row[0] > now:
        await message.answer(f"⏳ Вы отправили слишком много заявок. Подождите до {datetime.fromtimestamp(row[0]).strftime('%H:%M:%S')}.")
        return
    pending = await db.fetchval("SELECT COUNT(*) FROM appeals WHERE user_id=$1 AND status='pending'", message.from_user.id)
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
    count = await db.fetchval("SELECT COUNT(*) FROM appeals WHERE user_id=$1 AND created_at > $2", message.from_user.id, one_hour_ago)
    if count >= 2:
        block_until = int(datetime.now().timestamp()) + 3600
        await db.execute("INSERT INTO appeal_blocks (user_id, block_until) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET block_until=$2", message.from_user.id, block_until)
        await message.answer("⛔ Вы отправили 2 заявки за час. Доступ к аппеляциям заблокирован на 1 час.")
        await state.clear()
        return
    appeal_number = await get_next_appeal_number()
    await db.execute(
        "INSERT INTO appeals (appeal_number, user_id, username, violation_number, appeal_text, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
        appeal_number, message.from_user.id, username, violation_number, "\n".join(appeal_text), int(datetime.now().timestamp())
    )
    await message.answer(f"✅ Ваша аппеляция {appeal_number} принята. Ожидайте решения.")
    await state.clear()
    hubsup_id = await get_config("hubsup_id")
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
    action = data[1]
    appeal_number = data[2]
    user_id = int(data[3])
    if not await check_permission(callback.message, 1):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    if action == "approve":
        await db.execute("UPDATE appeals SET status='approved' WHERE appeal_number=$1", appeal_number)
        await bot.send_message(chat_id=user_id, text="✅ Ваша заявка была одобрена.")
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ Одобрено модератором.")
    else:
        await db.execute("UPDATE appeals SET status='rejected' WHERE appeal_number=$1", appeal_number)
        await bot.send_message(chat_id=user_id, text="❌ Ваша заявка была отклонена.")
        await callback.message.edit_text(f"{callback.message.text}\n\n❌ Отказано модератором.")
    await callback.answer("Готово.")

@dp.callback_query(lambda c: c.data.startswith("report_"))
async def report_callback(callback: CallbackQuery):
    if not await check_permission(callback.message, 1):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    await callback.answer("Репорт отправлен на рассмотрение.", show_alert=True)

# ======================= ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ =======================
@dp.message(F.content_type.in_({ContentType.NEW_CHAT_MEMBERS}))
async def welcome_new_member(message: Message):
    hublox_id = await get_config("hublox_id")
    if not hublox_id or str(message.chat.id) != hublox_id:
        return
    for member in message.new_chat_members:
        if member.id == bot.id:
            continue
        user_mention = f"@{member.username}" if member.username else member.full_name
        template = await get_template('welcome_template')
        if '{user}' in template:
            msg = template.format(user=user_mention)
        else:
            msg = f"{user_mention}\n{template}"
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=TOPIC_WELCOME,
            text=msg
        )

# ======================= ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (ссылки) =======================
@dp.message(F.text)
async def handle_all_messages(message: Message):
    # Обработка "Бот"
    if message.text.lower() == "бот":
        await message.reply("На месте ✅")
        return

    if message.message_thread_id in IGNORED_TOPICS:
        return

    hublox_id = await get_config("hublox_id")
    if not hublox_id or str(message.chat.id) != hublox_id:
        return

    # Проверка на ссылки (только не создатель)
    if message.from_user.id != CREATOR_ID:
        if re.search(r'https?://\S+', message.text):
            # Выдаём варн за ссылку
            await process_violation(message, message.text, "ссылка")
            return

    if await is_banned(message.from_user.id):
        await message.delete()
        await message.answer("Вы забанены и не можете писать.")
        return

async def process_violation(message: Message, text: str, msg_type: str):
    user = message.from_user
    if await is_banned(user.id):
        return
    warn_count, warn_number = await add_warn(user.id, f"Нарушение: {msg_type}", bot.id, message.chat.id, message.message_id)
    user_mention = f"@{user.username}" if user.username else f"[{user.id}](tg://user?id={user.id})"

    levels = [
        ("предупреждение", 1),
        ("мут на 5 минут", 2),
        ("мут на 24 часа", 3),
        ("бан", 4)
    ]
    level_lines = []
    for label, level in levels:
        if level == warn_count:
            level_lines.append(f" • {level}/4 - {label} {chr(9888)}")
        else:
            level_lines.append(f" • {level}/4 - {label}")
    levels_text = "\n".join(level_lines)

    chat_msg = (
        f"{user_mention} получает варн ({warn_count}/4) ⚠️\n"
        f"Причина: «{text}»\n"
        f"— · — · — · — · — · — · —\n"
        f"{levels_text}\n"
        f"— · — · — · — · — · — · —\n"
        f"ID варна: {warn_number}\n"
        f"— · — · — · — · — · — · —"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")],
        [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{message.chat.id}/{message.message_id}")]
    ])
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"**ВЫДАН ВАРН** ⚠️\n"
            f"Причина: {text}\n"
            f"ID варна: {warn_number}\n"
            f"Пользователь: {user_mention}\n"
            f"ID: `{user.id}`\n"
            f"Предупреждений: {warn_count}/4\n"
            f"Кем выдан: бот (автоматически)\n"
            f"Чат ID: `{message.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

    if warn_count >= 4:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", user.id)

# ======================= ЗАПУСК =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await update_admin_list()
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
