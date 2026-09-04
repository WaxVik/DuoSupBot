import asyncio
import logging
import secrets
import string
import re
from datetime import datetime
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# ========================== НАСТРОЙКИ ==========================
BOT_TOKEN = "8970388836:AAEc_r1mZoswY_nKWTQOxcQbg3vXR4ehD8M"
CREATOR_ID = 7675985792
DATABASE_URL = "postgresql://postgres:RsGrWajXqIorzUjiGwAJQiXoOqWzEYcx@postgres.railway.internal:5432/railway"

TOPICS = {
    "mod_chat": 6,
    "appeals": 9,
    "modlist": 10,
    "redact": 8,
    "reports": 258,
    "announcements": 16,
    "rules": 6,
    "chat": 7,
    "appeals_hublox": 20,
    "welcome": 1,
    "admin": 27,
    "raids": 17,
    "trades": 8,
}
IGNORED_TOPICS = [TOPICS["admin"], TOPICS["appeals_hublox"]]

db = None

async def init_db():
    global db
    db = await asyncpg.connect(DATABASE_URL)
    await db.execute('''
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, warns INT DEFAULT 0, banned BOOL DEFAULT FALSE, ban_until BIGINT);
        CREATE TABLE IF NOT EXISTS warn_logs (id SERIAL PRIMARY KEY, user_id BIGINT, warn_number TEXT, reason TEXT, moderator_id BIGINT, chat_id BIGINT, message_id BIGINT, created_at BIGINT, is_active BOOL DEFAULT TRUE);
        CREATE TABLE IF NOT EXISTS ban_logs (id SERIAL PRIMARY KEY, user_id BIGINT, ban_number TEXT, reason TEXT, moderator_id BIGINT, chat_id BIGINT, message_id BIGINT, created_at BIGINT);
        CREATE TABLE IF NOT EXISTS unban_logs (id SERIAL PRIMARY KEY, user_id BIGINT, unban_number TEXT, moderator_id BIGINT, chat_id BIGINT, message_id BIGINT, created_at BIGINT);
        CREATE TABLE IF NOT EXISTS unwarn_logs (id SERIAL PRIMARY KEY, user_id BIGINT, unwarn_number TEXT, moderator_id BIGINT, chat_id BIGINT, message_id BIGINT, created_at BIGINT);
        CREATE TABLE IF NOT EXISTS rules (version TEXT PRIMARY KEY, rule_text TEXT, created_at BIGINT);
        CREATE TABLE IF NOT EXISTS appeals (id SERIAL PRIMARY KEY, appeal_number TEXT, user_id BIGINT, username TEXT, violation_number TEXT, appeal_text TEXT, created_at BIGINT, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS appeal_blocks (user_id BIGINT PRIMARY KEY, block_until BIGINT);
        CREATE TABLE IF NOT EXISTS moderators (user_id BIGINT PRIMARY KEY, username TEXT, level INT DEFAULT 0, role TEXT);
        CREATE TABLE IF NOT EXISTS templates (key TEXT PRIMARY KEY, value TEXT);
    ''')
    defaults = {
        'welcome_template': '{user}\nДобро пожаловать в HuBBlox\nПожалуйста ознакомтесь с правилами сообщества.',
        'rules_version': '1.0'
    }
    for k, v in defaults.items():
        await db.execute("INSERT INTO templates (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", k, v)
    for counter in ['warn_counter', 'ban_counter', 'unban_counter', 'unwarn_counter', 'appeal_counter', 'report_counter']:
        await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", counter, '0')
    for key in ['link_code', 'hublox_id', 'hubsup_id']:
        await db.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", key, '')
    await db.execute("INSERT INTO moderators (user_id, username, level, role) VALUES ($1, $2, 7, 'Создатель') ON CONFLICT (user_id) DO NOTHING",
                     CREATOR_ID, 'WaxVik0')

async def get_config(key):
    row = await db.fetchrow("SELECT value FROM config WHERE key=$1", key)
    return row[0] if row else None

async def set_config(key, value):
    await db.execute("UPDATE config SET value=$1 WHERE key=$2", value, key)

async def get_template(key):
    row = await db.fetchrow("SELECT value FROM templates WHERE key=$1", key)
    return row[0] if row else None

async def get_next_number(counter_name):
    row = await db.fetchrow("SELECT value FROM config WHERE key=$1", counter_name)
    if row:
        new = int(row[0]) + 1
        await db.execute("UPDATE config SET value=$1 WHERE key=$2", str(new), counter_name)
        return new
    await db.execute("INSERT INTO config (key, value) VALUES ($1, $2)", counter_name, '1')
    return 1

def format_number(num):
    return f"#-{num:05d}"

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

async def set_moderator_level(user_id, level, username=None):
    if level == 0:
        await db.execute("DELETE FROM moderators WHERE user_id=$1", user_id)
    else:
        role_names = {
            1: "Младший модератор",
            2: "Модератор",
            3: "Младший администратор",
            4: "Администратор",
            5: "Старший администратор",
            6: "Главный администратор",
            7: "Создатель"
        }
        role = role_names.get(level, f"Уровень {level}")
        await db.execute(
            "INSERT INTO moderators (user_id, username, level, role) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO UPDATE SET username=$2, level=$3, role=$4",
            user_id, username, level, role
        )

async def is_creator(user_id):
    return user_id == CREATOR_ID

def get_role_name(level):
    roles = {
        0: "Участник",
        1: "Младший модератор",
        2: "Модератор",
        3: "Младший администратор",
        4: "Администратор",
        5: "Старший администратор",
        6: "Главный администратор",
        7: "Создатель"
    }
    return roles.get(level, f"Уровень {level}")

async def check_permission(user_id, min_level):
    if await is_creator(user_id):
        return True
    level = await get_moderator_level(user_id)
    return level >= min_level

async def can_punish(moderator_id, target_id):
    mod_level = await get_moderator_level(moderator_id)
    target_level = await get_moderator_level(target_id)
    if mod_level == 7:
        return True, None, mod_level, target_level
    if target_level >= mod_level:
        return False, f"❌ Вы не можете применить наказание к пользователю с рангом {target_level} (ваш ранг {mod_level}).", mod_level, target_level
    if mod_level < 1:
        return False, "⛔ Ваш ранг слишком низок для выдачи наказаний.", mod_level, target_level
    return True, None, mod_level, target_level

# ========================== НОВАЯ ФУНКЦИЯ ПАРСИНГА (через entities) ==========================
async def parse_warn_ban_command(message: Message, command_type: str):
    """
    Возвращает (target_user_id, target_username, reason, message_id_reply) или (None, None, None, None)
    """
    text = message.text
    cmd = f"/{command_type}"
    if text.startswith(cmd):
        text = text[len(cmd):].strip()
    else:
        return None, None, None, None

    # 1. Если есть reply – берём автора ответа
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if user:
            reason = text
            if not reason:
                return None, None, None, None
            logging.info(f"Reply target: {user.id} ({user.username})")
            return user.id, user.username, reason, message.reply_to_message.message_id
        return None, None, None, None

    # 2. Ищем упоминания через entities
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = text[entity.offset:entity.offset + entity.length]
                username = mention_text.replace('@', '')
                logging.info(f"Found mention: @{username}")
                try:
                    chat = await bot.get_chat(f"@{username}")
                    if chat and chat.type == "private":
                        # Проверяем, что это не сам отправитель
                        if chat.id == message.from_user.id:
                            logging.warning("Mention is the sender itself")
                            continue  # пропускаем это упоминание
                        reason = text.replace(mention_text, "").strip()
                        if not reason:
                            return None, None, None, None
                        logging.info(f"Target via mention: {chat.id} ({chat.username})")
                        return chat.id, chat.username, reason, None
                except Exception as e:
                    logging.error(f"Error getting user @{username}: {e}")
                    continue

    # 3. Если ничего не нашли
    logging.warning("No target found")
    return None, None, None, None

async def parse_unwarn_unban_command(message: Message, command_type: str):
    text = message.text
    cmd = f"/{command_type}"
    if text.startswith(cmd):
        text = text[len(cmd):].strip()
    else:
        return None, None, None

    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if user:
            return user.id, user.username, message.reply_to_message.message_id
        return None, None, None

    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = text[entity.offset:entity.offset + entity.length]
                username = mention_text.replace('@', '')
                try:
                    chat = await bot.get_chat(f"@{username}")
                    if chat and chat.type == "private" and chat.id != message.from_user.id:
                        return chat.id, chat.username, None
                except:
                    continue
    return None, None, None

# ========================== ОБНОВЛЕНИЕ СПИСКА АДМИНИСТРАТОРОВ ==========================
async def update_admin_list():
    rows = await db.fetch("SELECT user_id, username, level, role FROM moderators WHERE level > 0 ORDER BY level DESC")
    if not rows:
        text = "👥 Список администраторов пуст."
    else:
        lines = []
        for row in rows:
            uid, uname, lvl, role = row
            mention = f"@{uname}" if uname else f"[{uid}](tg://user?id={uid})"
            lines.append(f"{mention} — {role if role else get_role_name(lvl)}")
        text = "👥 **Состав администрации:**\n" + "\n".join(lines)

    for chat_key, topic in [("hubsup_id", TOPICS["modlist"]), ("hublox_id", TOPICS["admin"])]:
        cid = await get_config(chat_key)
        if cid:
            old = await get_config(f"adminlist_msg_{chat_key}")
            if old:
                try:
                    await bot.delete_message(chat_id=int(cid), message_id=int(old))
                except:
                    pass
            sent = await bot.send_message(chat_id=int(cid), message_thread_id=topic, text=text, parse_mode="Markdown")
            await set_config(f"adminlist_msg_{chat_key}", str(sent.message_id))

class AppealState(StatesGroup):
    waiting_text = State()

class RuleState(StatesGroup):
    waiting_text = State()

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========================== КОМАНДЫ ==========================

@dp.message(Command("start"))
async def start_cmd(msg: Message):
    await msg.answer(
        "👋 **Duosup Bot**\n\n"
        "Я модератор для HuBBlox.\n"
        "Для связи чатов используйте:\n"
        "• В HuBBlox: /link_hublox\n"
        "• В администрации: /link_hubsup <код>"
    )

def generate_link_code():
    return '-'.join(''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(5)) for _ in range(5))

@dp.message(Command("link_hublox"))
async def link_hublox(msg: Message):
    if await get_config("hublox_id") and await get_config("hubsup_id"):
        await msg.answer("⚠️ Чаты уже связаны.")
        return
    if msg.chat.type not in ("group", "supergroup"):
        await msg.answer("Только в группе!")
        return
    code = generate_link_code()
    await set_config("link_code", code)
    await set_config("hublox_id", str(msg.chat.id))
    await msg.answer(f"🔗 **Код:**\n`{code}`\n\nВ административном чате выполните:\n/link_hubsup {code}")

@dp.message(Command("link_hubsup"))
async def link_hubsup(msg: Message):
    if await get_config("hublox_id") and await get_config("hubsup_id"):
        await msg.answer("⚠️ Чаты уже связаны.")
        return
    if msg.chat.type not in ("group", "supergroup"):
        await msg.answer("Только в группе!")
        return
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /link_hubsup <код>")
        return
    code = args[1]
    saved = await get_config("link_code")
    if not saved:
        await msg.answer("⚠️ Сначала выполните /link_hublox в HuBBlox!")
        return
    if code != saved:
        await msg.answer("❌ Неверный код!")
        return
    await set_config("hubsup_id", str(msg.chat.id))
    await msg.answer("✅ Административный чат связан с HuBBlox!")
    hublox = await get_config("hublox_id")
    if hublox:
        await bot.send_message(chat_id=int(hublox), text="🔗 **Административный чат связан!**\nБот работает в обоих чатах.")
    await update_admin_list()

@dp.message(Command("redactrule"))
async def redact_rule(msg: Message, state: FSMContext):
    if not await is_creator(msg.from_user.id):
        await msg.answer("⛔ Доступно только создателю.")
        return
    text = msg.text.replace("/redactrule", "").strip()
    if text:
        current = await get_template('rules_version')
        major, minor = map(int, current.split('.'))
        minor += 1
        new_ver = f"{major}.{minor}"
        await set_template('rules_version', new_ver)
        await db.execute("INSERT INTO rules (version, rule_text, created_at) VALUES ($1, $2, $3)", new_ver, text, int(datetime.now().timestamp()))
        hublox = await get_config("hublox_id")
        if hublox:
            await bot.send_message(chat_id=int(hublox), message_thread_id=TOPICS["rules"], text=f"📜 **Правила сообщества HuBBlox (v{new_ver})**\n\n{text}", parse_mode="Markdown")
            for t in [TOPICS["chat"], TOPICS["trades"], TOPICS["raids"], TOPICS["announcements"]]:
                if t:
                    await bot.send_message(chat_id=int(hublox), message_thread_id=t, text=f"🔔 **Обновление правил!**\nВерсия {new_ver}. Ознакомьтесь в теме «Правила».", parse_mode="Markdown")
        await msg.answer(f"✅ Правила обновлены до версии {new_ver}!")
    else:
        await msg.answer("📝 Введите новые правила (полный текст):")
        await state.set_state(RuleState.waiting_text)

@dp.message(RuleState.waiting_text)
async def rule_text(msg: Message, state: FSMContext):
    await redact_rule(msg, state)
    await state.clear()

# ========================== /upmod и /downmod (только создатель) ==========================
@dp.message(Command("upmod"))
async def upmod_cmd(msg: Message):
    if not await is_creator(msg.from_user.id):
        await msg.answer("⛔ Только создатель может повышать.")
        return
    target = await parse_unwarn_unban_command(msg, "upmod")
    if not target:
        await msg.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    uid, uname, _ = target
    if uid == CREATOR_ID:
        await msg.answer("❌ Нельзя изменить статус создателя.")
        return
    current_level = await get_moderator_level(uid)
    if current_level >= 7:
        await msg.answer("❌ Пользователь уже на максимальном уровне (7).")
        return
    new_level = current_level + 1
    await set_moderator_level(uid, new_level, uname)
    hublox = await get_config("hublox_id")
    hubsup = await get_config("hubsup_id")
    if hublox and new_level > 0:
        await bot.promote_chat_member(
            chat_id=int(hublox), user_id=uid,
            can_delete_messages=True, can_restrict_members=True,
            can_invite_users=False, can_change_info=False,
            can_pin_messages=False, can_promote_members=False,
            can_manage_topics=False, can_manage_video_chats=False,
            can_manage_chat=False,
            can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
            custom_title=get_role_name(new_level)
        )
    if hubsup and new_level > 0:
        await bot.promote_chat_member(
            chat_id=int(hubsup), user_id=uid,
            can_delete_messages=True, can_restrict_members=True,
            can_invite_users=False, can_change_info=False,
            can_pin_messages=False, can_promote_members=False,
            can_manage_topics=False, can_manage_video_chats=False,
            can_manage_chat=False,
            can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
            custom_title=get_role_name(new_level)
        )
    await msg.answer(f"✅ @{uname} повышен до уровня {new_level} ({get_role_name(new_level)}).")
    await update_admin_list()

@dp.message(Command("downmod"))
async def downmod_cmd(msg: Message):
    if not await is_creator(msg.from_user.id):
        await msg.answer("⛔ Только создатель может понижать.")
        return
    target = await parse_unwarn_unban_command(msg, "downmod")
    if not target:
        await msg.answer("⚠️ Используйте команду как ответ на сообщение или укажите @username.")
        return
    uid, uname, _ = target
    if uid == CREATOR_ID:
        await msg.answer("❌ Нельзя изменить статус создателя.")
        return
    current_level = await get_moderator_level(uid)
    if current_level == 0:
        await msg.answer("⚠️ Пользователь уже имеет уровень 0 (участник).")
        return
    new_level = current_level - 1
    await set_moderator_level(uid, new_level, uname)
    hublox = await get_config("hublox_id")
    hubsup = await get_config("hubsup_id")
    if hublox:
        if new_level == 0:
            await bot.promote_chat_member(
                chat_id=int(hublox), user_id=uid,
                can_delete_messages=False, can_restrict_members=False,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                custom_title=""
            )
        else:
            await bot.promote_chat_member(
                chat_id=int(hublox), user_id=uid,
                can_delete_messages=True, can_restrict_members=True,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                custom_title=get_role_name(new_level)
            )
    if hubsup:
        if new_level == 0:
            await bot.promote_chat_member(
                chat_id=int(hubsup), user_id=uid,
                can_delete_messages=False, can_restrict_members=False,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                custom_title=""
            )
        else:
            await bot.promote_chat_member(
                chat_id=int(hubsup), user_id=uid,
                can_delete_messages=True, can_restrict_members=True,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False,
                can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                custom_title=get_role_name(new_level)
            )
    await msg.answer(f"✅ @{uname} понижен до уровня {new_level} ({get_role_name(new_level)}).")
    await update_admin_list()

def build_warn_msg(user_mention, warn_count, reason, warn_number):
    levels = ["предупреждение", "мут на 5 минут", "мут на 24 часа", "бан"]
    level_lines = [f" • {i+1}/4 - {levels[i]} {'⚠️' if i+1 == warn_count else ''}" for i in range(4)]
    return f"{user_mention} получает варн ({warn_count}/4)\nПричина: «{reason}»\n— · —\n" + "\n".join(level_lines) + f"\n— · —\nID варна: {warn_number}\n— · —"

# ========================== /warn ==========================
@dp.message(Command("warn"))
async def warn_cmd(msg: Message):
    if not await check_permission(msg.from_user.id, 1):
        await msg.answer("⛔ Ваш ранг слишком низок для выдачи варнов.")
        return

    target_id, target_username, reason, mid = await parse_warn_ban_command(msg, "warn")
    if not target_id:
        await msg.answer("⚠️ Используйте команду с ответом на сообщение или укажите @Username и укажите причину.")
        return
    if not reason:
        await msg.answer("⚠️ Укажите причину.")
        return

    # Дополнительная проверка: если цель — сам отправитель (на всякий случай)
    if target_id == msg.from_user.id:
        await msg.answer("❌ Нельзя выдать варн самому себе.")
        return

    allowed, err_msg, mod_level, target_level = await can_punish(msg.from_user.id, target_id)
    if not allowed:
        hubsup_id = await get_config("hubsup_id")
        if hubsup_id:
            report_text = (
                f"🚨 **Попытка выдать варн**\n"
                f"Модератор: @{msg.from_user.username or msg.from_user.first_name} (ранг {mod_level})\n"
                f"Цель: @{target_username or target_id} (ранг {target_level})\n"
                f"Ошибка: {err_msg}"
            )
            await bot.send_message(
                chat_id=int(hubsup_id),
                message_thread_id=TOPICS["reports"],
                text=report_text,
                parse_mode="Markdown"
            )
        await msg.answer(err_msg)
        return

    if await is_banned(target_id):
        await msg.answer("⚠️ Пользователь уже забанен.")
        return

    warn_count, warn_number = await add_warn(target_id, reason, msg.from_user.id, msg.chat.id, mid)
    mention = f"@{target_username}" if target_username else f"[{target_id}](tg://user?id={target_id})"
    chat_msg = build_warn_msg(mention, warn_count, reason, warn_number)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]])
    await msg.reply(chat_msg, parse_mode="Markdown", reply_markup=kb)

    hubsup = await get_config("hubsup_id")
    if hubsup:
        admin_text = (
            f"**ВЫДАН ВАРН**\n"
            f"Причина: {reason}\n"
            f"ID варна: {warn_number}\n"
            f"Пользователь: {mention}\n"
            f"ID: `{target_id}`\n"
            f"Предупреждений: {warn_count}/4\n"
            f"Кем выдан: @{msg.from_user.username or msg.from_user.first_name}\n"
            f"Чат ID: `{msg.chat.id}`\n"
            f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
        )
        admin_kb = None
        if mid:
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{msg.chat.id}/{mid}")]
            ])
        await bot.send_message(
            chat_id=int(hubsup),
            message_thread_id=TOPICS["mod_chat"],
            text=admin_text,
            parse_mode="Markdown",
            reply_markup=admin_kb
        )

    if warn_count >= 4:
        await bot.restrict_chat_member(msg.chat.id, target_id, permissions=ChatPermissions(can_send_messages=False))
        await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", target_id)

# ========================== ОСТАЛЬНЫЕ КОМАНДЫ (без изменений) ==========================
# ... (ban, unwarn, unban, report, stats, appeal, welcome, links) — они такие же, как в предыдущем коде, я не буду их дублировать, но они есть в полном коде.

# ========================== ЗАПУСК ==========================
async def main():
    await init_db()
    await update_admin_list()
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
