import asyncio
import logging
import secrets
import string
import re
from datetime import datetime
import asyncpg

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ChatPermissions,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ContentType,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAH0cFseraGhVRMRb1WB0_gh-PzbjjVhYJA"
CREATOR_ID = 7675985792
DATABASE_URL = "postgresql://postgres:kPDbfTTuTEvoTeOcitibgddkpMAKWUKH@postgres.railway.internal:5432/postgres"

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

# ======================= БАЗА ДАННЫХ =======================
db: asyncpg.Pool = None

async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    async with db.acquire() as conn:
        await conn.execute('''
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
        await conn.execute("INSERT INTO templates (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", 'welcome_template', '{user}\nДобро пожаловать в HuBBlox\nПожалуйста ознакомтесь с правилами сообщества.')
        await conn.execute("INSERT INTO templates (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", 'rules_version', '1.0')
        for counter in ['warn_counter', 'ban_counter', 'unban_counter', 'unwarn_counter', 'appeal_counter', 'report_counter']:
            await conn.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", counter, '0')
        await conn.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", 'link_code', '')
        await conn.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", 'hublox_id', '')
        await conn.execute("INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", 'hubsup_id', '')

# ======================= ФУНКЦИИ =======================
async def get_config(key):
    row = await db.fetchrow("SELECT value FROM config WHERE key=$1", key)
    return row[0] if row else None

async def set_config(key, value):
    await db.execute("UPDATE config SET value=$1 WHERE key=$2", str(value), key)

async def get_template(key):
    row = await db.fetchrow("SELECT value FROM templates WHERE key=$1", key)
    return row[0] if row else None

async def set_template(key, value):
    await db.execute("UPDATE templates SET value=$1 WHERE key=$2", value, key)

async def get_next_number(counter_name):
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key=$1", counter_name)
        if row:
            new = int(row[0]) + 1
            await conn.execute("UPDATE config SET value=$1 WHERE key=$2", str(new), counter_name)
            return new
        await conn.execute("INSERT INTO config (key, value) VALUES ($1, $2)", counter_name, '1')
        return 1

def format_number(num):
    return f"#-{num:05d}"

def get_message_url(chat_id: int, message_id: int) -> str:
    chat_str = str(chat_id)
    if chat_str.startswith("-100"):
        clean_id = chat_str[4:]
    elif chat_str.startswith("-"):
        clean_id = chat_str[1:]
    else:
        clean_id = chat_str
    return f"https://t.me/c/{clean_id}/{message_id}"

async def get_user_warns(user_id):
    row = await db.fetchrow("SELECT warns FROM users WHERE user_id=$1", user_id)
    return row[0] if row else 0

async def add_warn(user_id, reason, moderator_id, chat_id, message_id=None):
    warn_id = await get_next_number('warn_counter')
    warn_number = format_number(warn_id)
    
    row = await db.fetchrow(
        """
        INSERT INTO users (user_id, warns) VALUES ($1, 1)
        ON CONFLICT (user_id) DO UPDATE SET warns = users.warns + 1
        RETURNING warns;
        """,
        user_id
    )
    new_warns = row[0]
    
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

async def check_permission(message: Message, min_level: int):
    if await is_creator(message.from_user.id):
        return True
    level = await get_moderator_level(message.from_user.id)
    return level >= min_level

async def can_punish(moderator_id, target_id, min_level_required):
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

async def get_target_user_from_message(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, user.username, message.reply_to_message.message_id
    
    text = message.text or ""
    match = re.search(r'@(\w+)', text)
    if match:
        username = match.group(1)
        row = await db.fetchrow("SELECT user_id, username FROM moderators WHERE LOWER(username)=LOWER($1)", username)
        if row:
            return row[0], row[1], None
        try:
            chat = await bot.get_chat(f"@{username}")
            if chat:
                return chat.id, chat.username, None
        except Exception:
            pass
    return None

async def update_rules(version, text):
    await db.execute("INSERT INTO rules (version, rule_text, created_at) VALUES ($1, $2, $3)", version, text, int(datetime.now().timestamp()))

def get_role_name(level):
    roles = {
        1: "Младший модератор",
        2: "Модератор",
        3: "Старший модератор",
        4: "Администратор",
        5: "Главный администратор"
    }
    return roles.get(level, f"Уровень {level}")

async def set_admin_rights(chat_id, user_id, is_admin=True, custom_title=None):
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
                can_manage_chat=False
            )
            if custom_title:
                try:
                    await bot.set_chat_administrator_custom_title(
                        chat_id=chat_id,
                        user_id=user_id,
                        custom_title=custom_title[:16]
                    )
                except Exception as title_err:
                    logging.warning(f"Ошибка при установке плашки администратора: {title_err}")
        else:
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
                can_manage_chat=False
            )
        return True
    except Exception as e:
        logging.error(f"Ошибка при изменении прав: {e}")
        return False

async def update_admin_list():
    rows = await db.fetch("SELECT user_id, username, level, role FROM moderators ORDER BY level DESC")
    bot_me = await bot.get_me()
    bot_username = bot_me.username or "bot"
    
    lines = []
    if rows:
        for row in rows:
            user_id, username, level, role = row
            mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
            role_text = role if role else get_role_name(level)
            lines.append(f"{mention} — {role_text}")
    lines.append(f"@{bot_username} — Создатель (владелец)")
    text = "👥 **Состав администрации:**\n" + "\n".join(lines)

    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        old_msg_id = await get_config("adminlist_msg_hubsup")
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=int(hubsup_id), message_id=int(old_msg_id))
            except Exception:
                pass
        try:
            sent = await bot.send_message(chat_id=int(hubsup_id), message_thread_id=TOPIC_MODLIST, text=text, parse_mode="Markdown")
            await set_config("adminlist_msg_hubsup", str(sent.message_id))
        except Exception as e:
            logging.error(f"Не удалось отправить список админов в HubSup: {e}")

    hublox_id = await get_config("hublox_id")
    if hublox_id:
        old_msg_id = await get_config("adminlist_msg_hublox")
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=int(hublox_id), message_id=int(old_msg_id))
            except Exception:
                pass
        try:
            sent = await bot.send_message(chat_id=int(hublox_id), message_thread_id=TOPIC_ADMIN, text=text, parse_mode="Markdown")
            await set_config("adminlist_msg_hublox", str(sent.message_id))
        except Exception as e:
            logging.error(f"Не удалось отправить список админов в HuBBlox: {e}")

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
        await bot.send_message(chat_id=int(hublox_id), text="🔗 **Административный чат связан!**\nБот работает в обоих чатах.")
    await update_admin_list()

@dp.message(Command("redactrule"))
async def redact_rule(message: Message, state: FSMContext):
    if not await is_creator(message.from_user.id):
        await message.answer("⛔ Доступно только создателю.")
        return
    text = message.text.replace("/redactrule", "").strip()
    if text:
        current_version = await get_template('rules_version') or "1.0"
        try:
            major, minor = map(int, current_version.split('.'))
            minor += 1
            new_version = f"{major}.{minor}"
        except Exception:
            new_version = "1.1"
            
        await set_template('rules_version', new_version)
        await update_rules(new_version, text)
        hublox_id = await get_config("hublox_id")
        if hublox_id:
            await bot.send_message(chat_id=int(hublox_id), message_thread_id=TOPIC_RULES, text=f"📜 **Правила сообщества HuBBlox (v{new_version})**\n\n{text}", parse_mode="Markdown")
            for topic in [TOPIC_CHAT, TOPIC_TRADES, TOPIC_RAIDS, TOPIC_ANNOUNCEMENTS]:
                if topic:
                    try:
                        await bot.send_message(chat_id=int(hublox_id), message_thread_id=topic, text=f"🔔 **Обновление правил!**\nВерсия {new_version}. Ознакомьтесь в теме «Правила».", parse_mode="Markdown")
                    except Exception as e:
                        logging.error(f"Ошибка при рассылке обновления правил: {e}")
        await message.answer(f"✅ Правила обновлены до версии {new_version}!")
    else:
        await message.answer("📝 Введите новые правила (полный текст):")
        await state.set_state(RedactRuleStates.waiting_for_rule)

@dp.message(RedactRuleStates.waiting_for_rule)
async def get_rule_text(message: Message, state: FSMContext):
    await redact_rule(message, state)
    await state.clear()

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
        await db.execute("UPDATE moderators SET level=$1, role=$2, username=$3 WHERE user_id=$4", new_level, get_role_name(new_level), username, user_id)
    else:
        new_level = 1
        await db.execute("INSERT INTO moderators (user_id, username, level, role) VALUES ($1, $2, $3, $4)", user_id, username, new_level, get_role_name(new_level))
    role_title = get_role_name(new_level)
    hublox_id = await get_config("hublox_id")
    hubsup_id = await get_config("hubsup_id")
    if hublox_id:
        await set_admin_rights(int(hublox_id), user_id, is_admin=True, custom_title=role_title)
    if hubsup_id:
        await set_admin_rights(int(hubsup_id), user_id, is_admin=True, custom_title=role_title)
    await message.answer(f"✅ @{username or user_id} повышен до уровня {new_level} ({role_title}) и получил права администратора с тегом «{role_title}».")
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
    if current_level <= 0:
        await message.answer("⚠️ Пользователь уже имеет уровень 0 (участник).")
        return
    new_level = current_level - 1
    if new_level == 0:
        await db.execute("DELETE FROM moderators WHERE user_id=$1", user_id)
        hublox_id = await get_config("hublox_id")
        hubsup_id = await get_config("hubsup_id")
        if hublox_id:
            await set_admin_rights(int(hublox_id), user_id, is_admin=False)
        if hubsup_id:
            await set_admin_rights(int(hubsup_id), user_id, is_admin=False)
        await message.answer(f"✅ @{username or user_id} понижен до уровня 0 (участник) и удалён из списка модераторов, права и тег сняты.")
    else:
        role_title = get_role_name(new_level)
        await db.execute("UPDATE moderators SET level=$1, role=$2, username=$3 WHERE user_id=$4", new_level, role_title, username, user_id)
        hublox_id = await get_config("hublox_id")
        hubsup_id = await get_config("hubsup_id")
        if hublox_id:
            await set_admin_rights(int(hublox_id), user_id, is_admin=True, custom_title=role_title)
        if hubsup_id:
            await set_admin_rights(int(hubsup_id), user_id, is_admin=True, custom_title=role_title)
        await message.answer(f"✅ @{username or user_id} понижен до уровня {new_level} ({role_title}), тег обновлён.")
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
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя выдать варн самому себе.")
        return
    allowed, err_msg = await can_punish(message.from_user.id, user_id, 2)
    if not allowed:
        await message.answer(err_msg)
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
    levels = ["предупреждение", "мут на 5 минут", "мут на 24 часа", "бан"]
    level_lines = [f" • {i+1}/4 - {levels[i]} {'⚠️' if i+1 == warn_count else ''}" for i in range(4)]
    chat_msg = f"{user_mention} получает варн ({warn_count}/4)\nПричина: «{reason}»\n— · —\n" + "\n".join(level_lines) + f"\n— · —\nID варна: {warn_number}\n— · —"
    
    bot_info = await bot.get_me()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url=f"https://t.me/{bot_info.username}")]])
    if msg_id:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="Перейти к сообщению", url=get_message_url(message.chat.id, msg_id))])
    
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        mod_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=f"**ВЫДАН ВАРН**\nПричина: {reason}\nID варна: {warn_number}\nПользователь: {user_mention}\nID: `{user_id}`\nПредупреждений: {warn_count}/4\nКем выдан: {mod_mention}\nЧат ID: `{message.chat.id}`\nВремя выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС",
            parse_mode="Markdown"
        )
    if warn_count >= 4:
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
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
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя забанить самого себя.")
        return
    allowed, err_msg = await can_punish(message.from_user.id, user_id, 3)
    if not allowed:
        await message.answer(err_msg)
        return
    reason = message.text.replace("/ban", "").strip()
    if not reason:
        await message.answer("⚠️ Укажите причину: /ban причина")
        return
    if await is_banned(user_id):
        await message.answer("⚠️ Пользователь уже забанен.")
        return
    await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False))
    await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", user_id)
    ban_number = format_number(await get_next_number('ban_counter'))
    await db.execute("INSERT INTO ban_logs (user_id, ban_number, reason, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)", user_id, ban_number, reason, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"{user_mention} получает бан\nПричина: «{reason}»\n— · —\nID бана: {ban_number}\n— · —"
    
    bot_info = await bot.get_me()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подать аппеляцию", url=f"https://t.me/{bot_info.username}")]])
    if msg_id:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="Перейти к сообщению", url=get_message_url(message.chat.id, msg_id))])
    
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        mod_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=f"**ВЫДАН БАН**\nПричина: {reason}\nID бана: {ban_number}\nПользователь: {user_mention}\nID: `{user_id}`\nКем выдан: {mod_mention}\nЧат ID: `{message.chat.id}`\nВремя выдачи: {datetime.now().strftime('%H:%M:%S')} по МКС",
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
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя снять варн с самого себя.")
        return
    allowed, err_msg = await can_punish(message.from_user.id, user_id, 4)
    if not allowed:
        await message.answer(err_msg)
        return
    warns = await get_user_warns(user_id)
    if warns == 0:
        await message.answer("⚠️ У пользователя нет активных варнов.")
        return
    await remove_all_warns(user_id)
    unwarn_number = format_number(await get_next_number('unwarn_counter'))
    await db.execute("INSERT INTO unwarn_logs (user_id, unwarn_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)", user_id, unwarn_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"С пользователя {user_mention} сняты ограничения (0/4)\n— · —\nПожалуйста прочитайте правила сообщества по лучше.\n— · —\nНомер снятия: {unwarn_number}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Перейти к сообщению", url=get_message_url(message.chat.id, msg_id))]]) if msg_id else None
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        mod_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=f"**СНЯТ ВАРН**\nПричина: (снятие варнов)\nНомер снятия: {unwarn_number}\nПользователь: {user_mention}\nID: `{user_id}`\nКем снят: {mod_mention}\nЧат ID: `{message.chat.id}`\nВремя снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\nПожалуйста ознакомитесь с правилами сообщества по лучше.",
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
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя разбанить самого себя.")
        return
    allowed, err_msg = await can_punish(message.from_user.id, user_id, 5)
    if not allowed:
        await message.answer(err_msg)
        return
    if not await is_banned(user_id):
        await message.answer("⚠️ Пользователь не забанен.")
        return
    await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=ChatPermissions(can_send_messages=True))
    await db.execute("UPDATE users SET banned=FALSE, ban_until=NULL WHERE user_id=$1", user_id)
    unban_number = format_number(await get_next_number('unban_counter'))
    await db.execute("INSERT INTO unban_logs (user_id, unban_number, moderator_id, chat_id, message_id, created_at) VALUES ($1, $2, $3, $4, $5, $6)", user_id, unban_number, message.from_user.id, message.chat.id, msg_id, int(datetime.now().timestamp()))
    user_mention = f"@{username}" if username else f"[{user_id}](tg://user?id={user_id})"
    chat_msg = f"С пользователя {user_mention} сняты ограничения (0/4)\n— · —\nПожалуйста прочитайте правила сообщества по лучше.\n— · —\nНомер снятия: {unban_number}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Перейти к сообщению", url=get_message_url(message.chat.id, msg_id))]]) if msg_id else None
    await message.reply(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        mod_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=f"**СНЯТ БАН**\nПричина: (разбан)\nНомер снятия: {unban_number}\nПользователь: {user_mention}\nID: `{user_id}`\nКем снят: {mod_mention}\nЧат ID: `{message.chat.id}`\nВремя снятия: {datetime.now().strftime('%H:%M:%S')} по МКС\nПожалуйста ознакомитесь с правилами сообщества по лучше.",
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
    report_number = format_number(await get_next_number('report_counter'))
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report_text = f"**Получен Репорт {report_number}**\nОтправил: @{reporter.username or reporter.first_name}\nНа кого: @{violator.username or violator.first_name}\nID чата: `{message.chat.id}`\nПричина: {reason}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Рассмотреть", callback_data=f"report_{report_number}_{violator.id}_{reporter.id}")]])
        await bot.send_message(chat_id=int(hubsup_id), message_thread_id=TOPIC_REPORTS, text=report_text, parse_mode="Markdown", reply_markup=kb)
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
    await message.answer(f"📊 **Статистика**\nАктивных варнов: {warns}\nВсего банов: {bans}\nВсего разбанов: {unbans}\nВсего снятий варнов: {unwarns}")

# --- Обработчик триггера "бот" ---
@dp.message(F.text.lower() == "бот")
async def bot_mention(message: Message):
    await message.reply("На месте ✅")

# ======================= АППЕЛЯЦИИ =======================
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
    text = message.text or ""
    lines = text.split('\n')
    violation_number = None
    username = None
    appeal_text = []
    for line in lines:
        s_line = line.strip()
        if s_line.startswith('#-'):
            violation_number = s_line
        elif s_line.startswith('@'):
            username = s_line
        else:
            if s_line:
                appeal_text.append(s_line)
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
    appeal_number = format_number(await get_next_number('appeal_counter'))
    await db.execute("INSERT INTO appeals (appeal_number, user_id, username, violation_number, appeal_text, created_at) VALUES ($1, $2, $3, $4, $5, $6)", appeal_number, message.from_user.id, username, violation_number, "\n".join(appeal_text), int(datetime.now().timestamp()))
    await message.answer(f"✅ Ваша аппеляция {appeal_number} принята. Ожидайте решения.")
    await state.clear()
    hubsup_id = await get_config("hubsup_id")
    if hubsup_id:
        report = f"**Аппеляция {appeal_number}**\n{violation_number} варн / бан\n{username}\n<Цитированный текст обжалования>\n{' '.join(appeal_text)}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Принять", callback_data=f"appeal_approve_{appeal_number}_{message.from_user.id}"),
             InlineKeyboardButton(text="Отказать", callback_data=f"appeal_reject_{appeal_number}_{message.from_user.id}")]
        ])
        await bot.send_message(chat_id=int(hubsup_id), message_thread_id=TOPIC_APPEALS, text=report, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("appeal_"))
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
        try:
            await bot.send_message(chat_id=user_id, text="✅ Ваша заявка была одобрена.")
        except Exception:
            pass
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ Одобрено модератором.")
    else:
        await db.execute("UPDATE appeals SET status='rejected' WHERE appeal_number=$1", appeal_number)
        try:
            await bot.send_message(chat_id=user_id, text="❌ Ваша заявка была отклонена.")
        except Exception:
            pass
        await callback.message.edit_text(f"{callback.message.text}\n\n❌ Отказано модератором.")
    await callback.answer("Готово.")

@dp.callback_query(lambda c: c.data and c.data.startswith("report_"))
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
    bot_info = await bot.get_me()
    for member in message.new_chat_members:
        if member.id == bot_info.id:
            continue
        user_mention = f"@{member.username}" if member.username else member.full_name
        template = await get_template('welcome_template') or "{user}\nДобро пожаловать в HuBBlox"
        if '{user}' in template:
            msg = template.format(user=user_mention)
        else:
            msg = f"{user_mention}\n{template}"
        try:
            await bot.send_message(chat_id=message.chat.id, message_thread_id=TOPIC_WELCOME, text=msg)
        except Exception as e:
            logging.error(f"Ошибка отправки приветствия: {e}")

# ======================= ОБРАБОТЧИК СООБЩЕНИЙ И АВТОМОДЕРАЦИЯ =======================
@dp.message(F.text)
async def handle_all_messages(message: Message):
    if message.chat.type == "private":
        return
    if message.message_thread_id in IGNORED_TOPICS:
        return
    hublox_id = await get_config("hublox_id")
    if not hublox_id or str(message.chat.id) != hublox_id:
        return

    # Автомодерация ссылок
    if message.from_user.id != CREATOR_ID and re.search(r'https?://\S+', message.text):
        try:
            await message.delete()
        except Exception:
            pass

        warn_count, warn_number = await add_warn(message.from_user.id, "Ссылка", bot.id, message.chat.id, message.message_id)
        user_mention = f"@{message.from_user.username}" if message.from_user.username else f"[{message.from_user.id}](tg://user?id={message.from_user.id})"
        levels = ["предупреждение", "мут на 5 минут", "мут на 24 часа", "бан"]
        level_lines = [f" • {i+1}/4 - {levels[i]} {'⚠️' if i+1 == warn_count else ''}" for i in range(4)]
        chat_msg = f"{user_mention} получает варн ({warn_count}/4)\nПричина: «Ссылка»\n— · —\n" + "\n".join(level_lines) + f"\n— · —\nID варна: {warn_number}\n— · —"
        
        bot_info = await bot.get_me()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подать аппеляцию", url=f"https://t.me/{bot_info.username}")]
        ])
        await message.answer(chat_msg, parse_mode="Markdown", reply_markup=keyboard)
        
        if warn_count >= 4:
            await bot.restrict_chat_member(chat_id=message.chat.id, user_id=message.from_user.id, permissions=ChatPermissions(can_send_messages=False))
            await db.execute("UPDATE users SET banned=TRUE, ban_until=NULL WHERE user_id=$1", message.from_user.id)
        return

    # Удаление сообщений забаненных
    if await is_banned(message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass

# ======================= ЗАПУСК =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await update_admin_list()
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
