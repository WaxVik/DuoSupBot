import asyncio
import logging
import re
import secrets
import string
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
        CREATE TABLE IF NOT EXISTS moderators (user_id BIGINT PRIMARY KEY, username TEXT, level INT DEFAULT 1, role TEXT);
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

async def is_creator(user_id):
    return user_id == CREATOR_ID

def get_role_name(level):
    roles = {
        1: "Младший модератор",
        2: "Модератор",
        3: "Старший модератор",
        4: "Администратор",
        5: "Главный администратор"
    }
    return roles.get(level, f"Уровень {level}")

async def check_permission(user_id, min_level):
    # Для команд модерации: проверяем, что уровень пользователя >= min_level
    if await is_creator(user_id):
        return True
    level = await get_moderator_level(user_id)
    return level >= min_level

# ========== НОВАЯ ЛОГИКА ПРОВЕРКИ ПРАВ НА ВЫДАЧУ ВАРНА ==========
async def can_punish_warn(moderator_id, target_id):
    """
    Проверяет, может ли модератор выдать варн цели.
    Возвращает (True, None) или (False, сообщение_об_ошибке).
    """
    # Создатель может всё
    if await is_creator(moderator_id):
        return True, None

    mod_level = await get_moderator_level(moderator_id)
    target_level = await get_moderator_level(target_id)

    # Модератор должен иметь уровень >= 1 (т.е. быть модератором)
    if mod_level < 1:
        return False, "⛔ Вы не являетесь модератором."

    # Если цель - создатель, то никто кроме создателя не может выдать варн
    if await is_creator(target_id):
        return False, "❌ Нельзя выдать варн создателю."

    # Если цель - обычный участник (уровень 0), то любой модератор может выдать варн
    if target_level == 0:
        return True, None

    # Если цель - модератор (уровень > 0), то выдающий должен иметь уровень строго выше
    if mod_level > target_level:
        return True, None
    else:
        return False, f"❌ Нельзя выдать варн модератору с уровнем {target_level} (ваш уровень {mod_level} должен быть выше)."

async def set_admin_rights(chat_id, user_id, is_admin=True, custom_title=None):
    try:
        if is_admin:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id,
                can_delete_messages=True, can_restrict_members=True,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False, can_post_stories=False,
                can_edit_stories=False, can_delete_stories=False,
                custom_title=custom_title
            )
        else:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id,
                can_delete_messages=False, can_restrict_members=False,
                can_invite_users=False, can_change_info=False,
                can_pin_messages=False, can_promote_members=False,
                can_manage_topics=False, can_manage_video_chats=False,
                can_manage_chat=False, can_post_stories=False,
                can_edit_stories=False, can_delete_stories=False,
                custom_title=""
            )
        return True
    except Exception as e:
        logging.error(f"Ошибка прав: {e}")
        return False

async def update_admin_list():
    rows = await db.fetch("SELECT user_id, username, level, role FROM moderators ORDER BY level DESC")
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

@dp.message(Command("upmod"))
async def upmod(msg: Message):
    if not await is_creator(msg.from_user.id):
        await msg.answer("⛔ Только создатель может повышать.")
        return
    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Используйте как ответ на сообщение или укажите @username.")
        return
    uid, uname, _ = target
    if uid == CREATOR_ID:
        await msg.answer("❌ Нельзя изменить статус создателя.")
        return
    row = await db.fetchrow("SELECT level FROM moderators WHERE user_id=$1", uid)
    if row:
        lvl = row[0]
        if lvl >= 5:
            await msg.answer("❌ Уже максимальный уровень (Главный администратор).")
            return
        new_lvl = lvl + 1
        await db.execute("UPDATE moderators SET level=$1, role=$2 WHERE user_id=$3", new_lvl, get_role_name(new_lvl), uid)
    else:
        new_lvl = 1
        await db.execute("INSERT INTO moderators (user_id, username, level, role) VALUES ($1, $2, $3, $4)", uid, uname, new_lvl, get_role_name(new_lvl))
    role = get_role_name(new_lvl)
    hublox = await get_config("hublox_id")
    hubsup = await get_config("hubsup_id")
    if hublox:
        await set_admin_rights(int(hublox), uid, True, role)
    if hubsup:
        await set_admin_rights(int(hubsup), uid, True, role)
    await msg.answer(f"✅ @{uname} повышен до уровня {new_lvl} ({role}) и получил права администратора с тегом «{role}».")
    await update_admin_list()

@dp.message(Command("downmod"))
async def downmod(msg: Message):
    if not await is_creator(msg.from_user.id):
        await msg.answer("⛔ Только создатель может понижать.")
        return
    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Используйте как ответ на сообщение или укажите @username.")
        return
    uid, uname, _ = target
    if uid == CREATOR_ID:
        await msg.answer("❌ Нельзя изменить статус создателя.")
        return
    row = await db.fetchrow("SELECT level FROM moderators WHERE user_id=$1", uid)
    if not row:
        await msg.answer("⚠️ Пользователь не является модератором.")
        return
    lvl = row[0]
    if lvl == 0:
        await msg.answer("⚠️ Уже участник (уровень 0).")
        return
    new_lvl = lvl - 1
    if new_lvl == 0:
        await db.execute("DELETE FROM moderators WHERE user_id=$1", uid)
        hublox = await get_config("hublox_id")
        hubsup = await get_config("hubsup_id")
        if hublox:
            await set_admin_rights(int(hublox), uid, False)
        if hubsup:
            await set_admin_rights(int(hubsup), uid, False)
        await msg.answer(f"✅ @{uname} понижен до уровня 0 (участник) и удалён из списка модераторов, права и тег сняты.")
    else:
        await db.execute("UPDATE moderators SET level=$1, role=$2 WHERE user_id=$3", new_lvl, get_role_name(new_lvl), uid)
        role = get_role_name(new_lvl)
        hublox = await get_config("hublox_id")
        hubsup = await get_config("hubsup_id")
        if hublox:
            await set_admin_rights(int(hublox), uid, True, role)
        if hubsup:
            await set_admin_rights(int(hubsup), uid, True, role)
        await msg.answer(f"✅ @{uname} понижен до уровня {new_lvl} ({role}), тег обновлён.")
    await update_admin_list()

def build_warn_msg(user_mention, warn_count, reason, warn_number):
    levels = ["предупреждение", "мут на 5 минут", "мут на 24 часа", "бан"]
    level_lines = [f" • {i+1}/4 - {levels[i]} {'⚠️' if i+1 == warn_count else ''}" for i in range(4)]
    return f"{user_mention} получает варн ({warn_count}/4)\nПричина: «{reason}»\n— · —\n" + "\n".join(level_lines) + f"\n— · —\nID варна: {warn_number}\n— · —"

# ========== ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕЛИ (работает и через reply, и через @username) ==========
async def get_target_user(message: Message):
    # 1. Если есть ответ на сообщение – берём автора
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if user:
            return user.id, user.username, message.reply_to_message.message_id
        return None

    # 2. Если нет ответа, ищем упоминания через entities (исключаем самого отправителя)
    if message.entities:
        sender_id = message.from_user.id
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = message.text[entity.offset:entity.offset + entity.length]
                username = mention_text.replace('@', '')
                try:
                    chat = await bot.get_chat(f"@{username}")
                    if chat and chat.id != sender_id:
                        return chat.id, chat.username, None
                except Exception:
                    continue
    return None

# ========================== КОМАНДА /warn (исправлена) ==========================
@dp.message(Command("warn"))
async def warn_cmd(msg: Message):
    # Проверяем, что пользователь является модератором (уровень >= 1) или создателем
    if not await check_permission(msg.from_user.id, 1):
        await msg.answer("⛔ Вы не являетесь модератором.")
        return

    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return

    uid, uname, mid = target

    # Нельзя выдать варн самому себе
    if uid == msg.from_user.id:
        await msg.answer("❌ Нельзя выдать варн самому себе.")
        return

    # Проверка прав на выдачу варна (новая логика)
    allowed, err = await can_punish_warn(msg.from_user.id, uid)
    if not allowed:
        await msg.answer(err)
        return

    reason = msg.text.replace("/warn", "").strip()
    if not reason:
        await msg.answer("⚠️ Укажите причину: /warn причина")
        return

    if await is_banned(uid):
        await msg.answer("⚠️ Пользователь уже забанен.")
        return

    warn_count, warn_number = await add_warn(uid, reason, msg.from_user.id, msg.chat.id, mid)
    mention = f"@{uname}" if uname else f"[{uid}](tg://user?id={uid})"
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
            f"ID: `{uid}`\n"
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
        await bot.restrict_chat_member(msg.chat.id, uid, permissions=ChatPermissions(can_send_messages=False))
        await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", uid)

# ========================== ОСТАЛЬНЫЕ КОМАНДЫ (без изменений) ==========================
@dp.message(Command("ban"))
async def ban_cmd(msg: Message):
    if not await check_permission(msg.from_user.id, 3):
        await msg.answer("⛔ Недостаточно прав (требуется уровень 3+).")
        return
    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return
    uid, uname, mid = target
    if uid == msg.from_user.id:
        await msg.answer("❌ Нельзя забанить самого себя.")
        return
    # Для бана используем старую логику can_punish (с min_level=3)
    allowed, err = await can_punish(msg.from_user.id, uid, 3)  # нужно переделать, но пока оставим
    if not allowed:
        await msg.answer(err)
        return
    reason = msg.text.replace("/ban", "").strip()
    if not reason:
        await msg.answer("⚠️ Укажите причину: /ban причина")
        return
    if await is_banned(uid):
        await msg.answer("⚠️ Пользователь уже забанен.")
        return
    await bot.restrict_chat_member(msg.chat.id, uid, permissions=ChatPermissions(can_send_messages=False))
    await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", uid)
    ban_num = format_number(await get_next_number('ban_counter'))
    await db.execute("INSERT INTO ban_logs (user_id, ban_number, reason, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                     uid, ban_num, reason, msg.from_user.id, msg.chat.id, mid, int(datetime.now().timestamp()))
    mention = f"@{uname}" if uname else f"[{uid}](tg://user?id={uid})"
    chat_msg = f"{mention} получает бан\nПричина: «{reason}»\n— · —\nID бана: {ban_num}\n— · —"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]])
    await msg.reply(chat_msg, parse_mode="Markdown", reply_markup=kb)

    hubsup = await get_config("hubsup_id")
    if hubsup:
        admin_text = (
            f"**ВЫДАН БАН**\n"
            f"Причина: {reason}\n"
            f"ID бана: {ban_num}\n"
            f"Пользователь: {mention}\n"
            f"ID: `{uid}`\n"
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

@dp.message(Command("unwarn"))
async def unwarn_cmd(msg: Message):
    if not await check_permission(msg.from_user.id, 4):
        await msg.answer("⛔ Недостаточно прав (требуется уровень 4+).")
        return
    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return
    uid, uname, mid = target
    if uid == msg.from_user.id:
        await msg.answer("❌ Нельзя снять варн с самого себя.")
        return
    allowed, err = await can_punish(msg.from_user.id, uid, 4)  # временно
    if not allowed:
        await msg.answer(err)
        return
    if await get_user_warns(uid) == 0:
        await msg.answer("⚠️ У пользователя нет активных варнов.")
        return
    await remove_all_warns(uid)
    unwarn_num = format_number(await get_next_number('unwarn_counter'))
    await db.execute("INSERT INTO unwarn_logs (user_id, unwarn_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
                     uid, unwarn_num, msg.from_user.id, msg.chat.id, mid, int(datetime.now().timestamp()))
    mention = f"@{uname}" if uname else f"[{uid}](tg://user?id={uid})"
    chat_msg = f"С пользователя {mention} сняты ограничения (0/4)\n— · —\nНомер снятия: {unwarn_num}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]])
    await msg.reply(chat_msg, parse_mode="Markdown", reply_markup=kb)

    hubsup = await get_config("hubsup_id")
    if hubsup:
        admin_text = (
            f"**СНЯТ ВАРН**\n"
            f"Причина: (снятие варнов)\n"
            f"Номер снятия: {unwarn_num}\n"
            f"Пользователь: {mention}\n"
            f"ID: `{uid}`\n"
            f"Кем снят: @{msg.from_user.username or msg.from_user.first_name}\n"
            f"Чат ID: `{msg.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС"
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

@dp.message(Command("unban"))
async def unban_cmd(msg: Message):
    if not await check_permission(msg.from_user.id, 5):
        await msg.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    target = await get_target_user(msg)
    if not target:
        await msg.answer("⚠️ Укажите пользователя: ответьте на сообщение или напишите @username.")
        return
    uid, uname, mid = target
    if uid == msg.from_user.id:
        await msg.answer("❌ Нельзя разбанить самого себя.")
        return
    allowed, err = await can_punish(msg.from_user.id, uid, 5)
    if not allowed:
        await msg.answer(err)
        return
    if not await is_banned(uid):
        await msg.answer("⚠️ Пользователь не забанен.")
        return
    await bot.restrict_chat_member(msg.chat.id, uid, permissions=ChatPermissions(can_send_messages=True))
    await db.execute("UPDATE users SET banned=FALSE, ban_until=NULL WHERE user_id=$1", uid)
    unban_num = format_number(await get_next_number('unban_counter'))
    await db.execute("INSERT INTO unban_logs (user_id, unban_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
                     uid, unban_num, msg.from_user.id, msg.chat.id, mid, int(datetime.now().timestamp()))
    mention = f"@{uname}" if uname else f"[{uid}](tg://user?id={uid})"
    chat_msg = f"С пользователя {mention} сняты ограничения (0/4)\n— · —\nНомер снятия: {unban_num}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]])
    await msg.reply(chat_msg, parse_mode="Markdown", reply_markup=kb)

    hubsup = await get_config("hubsup_id")
    if hubsup:
        admin_text = (
            f"**СНЯТ БАН**\n"
            f"Причина: (разбан)\n"
            f"Номер снятия: {unban_num}\n"
            f"Пользователь: {mention}\n"
            f"ID: `{uid}`\n"
            f"Кем снят: @{msg.from_user.username or msg.from_user.first_name}\n"
            f"Чат ID: `{msg.chat.id}`\n"
            f"Время снятия: {datetime.now().strftime('%H:%M:%S')} по МКС"
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

@dp.message(Command("report"))
async def report_cmd(msg: Message):
    if not msg.reply_to_message:
        await msg.answer("⚠️ Используйте команду как ответ на сообщение нарушителя.")
        return
    reporter = msg.from_user
    violator = msg.reply_to_message.from_user
    reason = msg.text.replace("/report", "").strip()
    if not reason:
        await msg.answer("⚠️ Укажите причину репорта: /report причина")
        return
    report_num = format_number(await get_next_number('report_counter'))
    hubsup = await get_config("hubsup_id")
    if hubsup:
        text = f"**Получен Репорт {report_num}**\nОтправил: @{reporter.username or reporter.first_name}\nНа кого: @{violator.username or violator.first_name}\nID чата: `{msg.chat.id}`\nПричина: {reason}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Рассмотреть", callback_data=f"report_{report_num}_{violator.id}_{reporter.id}")]])
        await bot.send_message(chat_id=int(hubsup), message_thread_id=TOPICS["reports"], text=text, parse_mode="Markdown", reply_markup=kb)
        await msg.reply("✅ Репорт отправлен администрации.")
    else:
        await msg.reply("⚠️ Бот не связан с административным чатом.")

@dp.message(Command("stats"))
async def stats_cmd(msg: Message):
    if not await check_permission(msg.from_user.id, 5):
        await msg.answer("⛔ Недостаточно прав (требуется уровень 5+).")
        return
    warns = await db.fetchval("SELECT COUNT(*) FROM warn_logs WHERE is_active=TRUE")
    bans = await db.fetchval("SELECT COUNT(*) FROM ban_logs")
    unbans = await db.fetchval("SELECT COUNT(*) FROM unban_logs")
    unwarns = await db.fetchval("SELECT COUNT(*) FROM unwarn_logs")
    await msg.answer(f"📊 **Статистика**\nАктивных варнов: {warns}\nВсего банов: {bans}\nВсего разбанов: {unbans}\nВсего снятий варнов: {unwarns}")

@dp.message(F.text)
async def bot_mention(msg: Message):
    if msg.text and msg.text.lower() == "бот":
        await msg.reply("На месте ✅")

@dp.message(Command("appeal"))
async def appeal_start(msg: Message, state: FSMContext):
    if msg.chat.type != "private":
        await msg.answer("📝 Используйте /appeal в личных сообщениях бота.")
        return
    row = await db.fetchrow("SELECT block_until FROM appeal_blocks WHERE user_id=$1", msg.from_user.id)
    now = int(datetime.now().timestamp())
    if row and row[0] > now:
        await msg.answer(f"⏳ Вы отправили слишком много заявок. Подождите до {datetime.fromtimestamp(row[0]).strftime('%H:%M:%S')}.")
        return
    pending = await db.fetchval("SELECT COUNT(*) FROM appeals WHERE user_id=$1 AND status='pending'", msg.from_user.id)
    if pending > 0:
        await msg.answer("⚠️ У вас уже есть ожидающая рассмотрения заявка.")
        return
    await msg.answer(
        "📝 **Подача аппеляции**\n"
        "Каждая строчка с новой строки пишите правильно, при повторной отправке ваша аппеляция будет удалена!\n"
        "Повторная аппеляция будет доступна через 1 час.\n"
        "— · — · — · — · — · — · — · — · —\n"
        "Заявка: что в [] не нужно писать в аппеляцию.\n"
        "[Номер нарушения/бана не путайте!] #-xxx.xxx варн/бан\n"
        "[Ваш] @username\n"
        "[Обжалование:]"
    )
    await state.set_state(AppealState.waiting_text)

@dp.message(AppealState.waiting_text)
async def appeal_text(msg: Message, state: FSMContext):
    lines = msg.text.split('\n')
    violation = None
    username = None
    appeal_text = []
    for line in lines:
        if line.startswith('#-'):
            violation = line.strip()
        elif line.startswith('@'):
            username = line.strip()
        else:
            if line.strip():
                appeal_text.append(line.strip())
    if not violation or not username:
        await msg.answer("❌ Неверный формат. Пожалуйста, следуйте шаблону.")
        return
    one_hour_ago = int(datetime.now().timestamp()) - 3600
    count = await db.fetchval("SELECT COUNT(*) FROM appeals WHERE user_id=$1 AND created_at > $2", msg.from_user.id, one_hour_ago)
    if count >= 2:
        block_until = int(datetime.now().timestamp()) + 3600
        await db.execute("INSERT INTO appeal_blocks (user_id, block_until) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET block_until=$2", msg.from_user.id, block_until)
        await msg.answer("⛔ Вы отправили 2 заявки за час. Доступ к аппеляциям заблокирован на 1 час.")
        await state.clear()
        return
    appeal_num = format_number(await get_next_number('appeal_counter'))
    await db.execute("INSERT INTO appeals (appeal_number, user_id, username, violation_number, appeal_text, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
                     appeal_num, msg.from_user.id, username, violation, "\n".join(appeal_text), int(datetime.now().timestamp()))
    await msg.answer(f"✅ Ваша аппеляция {appeal_num} принята. Ожидайте решения.")
    await state.clear()
    hubsup = await get_config("hubsup_id")
    if hubsup:
        report = f"**Аппеляция {appeal_num}**\n{violation} варн / бан\n{username}\n<Цитированный текст обжалования>\n{' '.join(appeal_text)}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Принять", callback_data=f"appeal_approve_{appeal_num}_{msg.from_user.id}"),
             InlineKeyboardButton(text="Отказать", callback_data=f"appeal_reject_{appeal_num}_{msg.from_user.id}")]
        ])
        await bot.send_message(chat_id=int(hubsup), message_thread_id=TOPICS["appeals"], text=report, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("appeal_"))
async def appeal_cb(cb: CallbackQuery):
    data = cb.data.split('_')
    action = data[1]
    appeal_num = data[2]
    user_id = int(data[3])
    if not await check_permission(cb.from_user.id, 1):
        await cb.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    if action == "approve":
        await db.execute("UPDATE appeals SET status='approved' WHERE appeal_number=$1", appeal_num)
        await bot.send_message(user_id, "✅ Ваша заявка была одобрена.")
        await cb.message.edit_text(f"{cb.message.text}\n\n✅ Одобрено модератором.")
    else:
        await db.execute("UPDATE appeals SET status='rejected' WHERE appeal_number=$1", appeal_num)
        await bot.send_message(user_id, "❌ Ваша заявка была отклонена.")
        await cb.message.edit_text(f"{cb.message.text}\n\n❌ Отказано модератором.")
    await cb.answer("Готово.")

@dp.callback_query(lambda c: c.data.startswith("report_"))
async def report_cb(cb: CallbackQuery):
    if not await check_permission(cb.from_user.id, 1):
        await cb.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    await cb.answer("Репорт отправлен на рассмотрение.", show_alert=True)

@dp.message(F.content_type.in_({ContentType.NEW_CHAT_MEMBERS}))
async def welcome(msg: Message):
    hublox = await get_config("hublox_id")
    if not hublox or str(msg.chat.id) != hublox:
        return
    for member in msg.new_chat_members:
        if member.id == bot.id:
            continue
        mention = f"@{member.username}" if member.username else member.full_name
        template = await get_template('welcome_template')
        text = template.format(user=mention) if '{user}' in template else f"{mention}\n{template}"
        await bot.send_message(chat_id=msg.chat.id, message_thread_id=TOPICS["welcome"], text=text)

@dp.message(F.text)
async def handle_links(msg: Message):
    if msg.text.lower() == "бот":
        return
    if msg.message_thread_id in IGNORED_TOPICS:
        return
    hublox = await get_config("hublox_id")
    if not hublox or str(msg.chat.id) != hublox:
        return
    if msg.from_user.id == CREATOR_ID:
        return
    if re.search(r'https?://\S+', msg.text):
        warn_count, warn_number = await add_warn(msg.from_user.id, "Ссылка", bot.id, msg.chat.id, msg.message_id)
        mention = f"@{msg.from_user.username}" if msg.from_user.username else f"[{msg.from_user.id}](tg://user?id={msg.from_user.id})"
        chat_msg = build_warn_msg(mention, warn_count, "Ссылка", warn_number)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подать аппеляцию", url="https://t.me/duosup_bot")]
        ])
        await msg.reply(chat_msg, parse_mode="Markdown", reply_markup=kb)

        hubsup = await get_config("hubsup_id")
        if hubsup:
            admin_text = (
                f"**ВЫДАН ВАРН** (автоматически)\n"
                f"Причина: Ссылка\n"
                f"ID варна: {warn_number}\n"
                f"Пользователь: {mention}\n"
                f"ID: `{msg.from_user.id}`\n"
                f"Предупреждений: {warn_count}/4\n"
                f"Кем выдан: бот\n"
                f"Чат ID: `{msg.chat.id}`\n"
                f"Время выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС"
            )
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к сообщению", url=f"https://t.me/c/{msg.chat.id}/{msg.message_id}")]
            ])
            await bot.send_message(
                chat_id=int(hubsup),
                message_thread_id=TOPICS["mod_chat"],
                text=admin_text,
                parse_mode="Markdown",
                reply_markup=admin_kb
            )
        if warn_count >= 4:
            await bot.restrict_chat_member(msg.chat.id, msg.from_user.id, permissions=ChatPermissions(can_send_messages=False))
            await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", msg.from_user.id)
        return
    if await is_banned(msg.from_user.id):
        await msg.delete()
        await msg.answer("Вы забанены и не можете писать.")

# ========================== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ДРУГИХ КОМАНД (оставлена как есть) ==========================
async def can_punish(moderator_id, target_id, min_level_required):
    # Это старая функция для бана и других команд – оставляем без изменений
    if await is_creator(moderator_id):
        return True, None
    mod_level = await get_moderator_level(moderator_id)
    target_level = await get_moderator_level(target_id)
    if mod_level < min_level_required:
        return False, f"⛔ Недостаточно прав (требуется уровень {min_level_required}+)."
    if target_level >= mod_level:
        return False, f"❌ Нельзя применить наказание к пользователю с уровнем {target_level} (выше или равен вашему)."
    if await is_creator(target_id):
        return False, "❌ Нельзя применить наказание к создателю."
    return True, None

# ========================== ЗАПУСК ==========================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await update_admin_list()
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
