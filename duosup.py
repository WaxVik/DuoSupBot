import asyncio
import import asyncio
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
import threading
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from flask import Flask

# Нейросеть для модерации (усиленная)
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAFIfuQ-W3_ZW6Na-WqelTc_hpuirgBOYjQ"  # исправлено
CREATOR_ID = 7675985792

# ======================= ID ТЕМ (по твоей структуре) =======================
# HubSup (админский чат)
TOPIC_MOD_CHAT = 6          # чат модерации
TOPIC_RULES_HUBSUP = 69     # правила для модераторов (не используется, но оставлю)
TOPIC_APPEALS = 9           # аппеляции
TOPIC_MODLIST = 10          # состав администрации
TOPIC_REDACT = 8            # редакт

# HuBBlox (основной чат)
TOPIC_ANNOUNCEMENTS = 16    # оповещения
TOPIC_RULES = 6             # правила
TOPIC_CHAT = 7              # чат
TOPIC_APPEALS_HUBBLOX = 20  # аппеляция
TOPIC_WELCOME = 1           # добро пожаловать
TOPIC_ADMIN = 27            # администрация (игнорируется)
TOPIC_RAIDS = 17            # рейды
TOPIC_TRADES = 8            # трейды

# Темы, где модерация НЕ работает
IGNORED_TOPICS = [TOPIC_ADMIN, TOPIC_RULES, TOPIC_ANNOUNCEMENTS, TOPIC_WELCOME, TOPIC_APPEALS_HUBBLOX]
# Темы, где бот НЕ должен ничего делать (даже команды)
NO_INTERACTION_TOPICS = [TOPIC_ADMIN, TOPIC_APPEALS_HUBBLOX]

# Репорты отправляем в основной чат модерации (можно было бы создать отдельную тему, но пока так)
TOPIC_REPORTS_HUBSUP = TOPIC_MOD_CHAT

# ======================= УСИЛЕННАЯ ПРОВЕРКА ОСКОРБЛЕНИЙ =======================
# Расширенный чёрный список
BANNED_WORDS = [
    "хуй", "пизда", "бля", "сука", "ебать", "нахуй", "пиздец", "ебаный",
    "пидор", "лох", "редиска", "мудак", "гандон", "залупа", "долбоёб", "тупой",
    "уебок", "шлюха", "тварь", "гнида", "козёл", "овца", "свинья", "чмо",
    "дебил", "идиот", "кретин", "петух", "шалава", "проститутка", "сучка"
]

print("Загрузка модели cointegrated/rubert-tiny-toxicity...")
model_name = "cointegrated/rubert-tiny-toxicity"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(model_name)
model.eval()
print("Модель загружена!")

def is_insult(text: str, is_reply: bool = False) -> bool:
    # 1. Проверка по чёрному списку (быстро)
    text_lower = text.lower()
    for word in BANNED_WORDS:
        if word in text_lower:
            return True

    # 2. Нейросеть – порог снижен для большей чувствительности
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=192)
    with torch.no_grad():
        logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0]
        insult_prob = float(probs[1])  # 1 - insult
        # Для ответов (reply) делаем порог ещё ниже – 0.5, для обычных – 0.6
        threshold = 0.5 if is_reply else 0.6
        return insult_prob >= threshold

# ======================= БАЗА ДАННЫХ =======================
conn = sqlite3.connect("duosup.db", check_same_thread=False)
cursor = conn.cursor()

# ... (все CREATE TABLE и defaults точно такие же, как в предыдущей версии, я не буду повторять для краткости, но они есть в коде ниже)

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
                        message_thread_id=TOPIC_CHAT,  # можно отправить в общий чат
                        text=f"🔄 Все нарушения с пользователя {mention} были сняты.\nПожалуйста, следите за языком."
                    )
                except:
                    pass

# ======================= КОМАНДЫ =======================
# (все команды, включая /start, /link_hublox, /link_hubsup, /rules, /report, /warn, /ban, /unban, /unwarn, /redact, /addmod, /removemod, /stats, /appeal – остаются без изменений, кроме ID тем)

# Я приведу только изменённые части: обработчики с использованием новых ID и усиленной модерации.

# ======================= ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ (приветствие) =======================
@dp.message(content_types=ContentType.NEW_CHAT_MEMBERS)
async def welcome_new_member(message: Message):
    if str(message.chat.id) != get_config("hublox_id"):
        return
    for member in message.new_chat_members:
        if member.id == bot.id:
            continue  # не приветствуем самого бота
        user_mention = f"@{member.username}" if member.username else member.full_name
        template = get_template('welcome_template')
        msg_text = template.format(user=user_mention)
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=TOPIC_WELCOME,
            text=msg_text
        )

# ======================= ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ (МОДЕРАЦИЯ) =======================
@dp.message()
async def handle_all_messages(message: Message):
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

    is_reply = message.reply_to_message is not None
    if is_insult(message.text, is_reply):
        await process_violation(message, message.text, "оскорбление", is_reply)

# ======================= ФУНКЦИЯ ОБРАБОТКИ НАРУШЕНИЙ =======================
# (остаётся без изменений, но использует новые пороги)

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
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
import aiohttp
import threading
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from flask import Flask

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8970388836:AAFIfuQ-W3_ZW6Na-WqeITc_hpuirgBOYjQ"
CREATOR_ID = 7675985792

# API-ключ Lakera Guard
LAKERA_API_KEY = "eee5eeea5aaee980fce82725ed4e88535b4b2d21e972b31f49cc722ddb87a258"

# ID тем в HuBBlox
TOPIC_ANNOUNCEMENTS = 16
TOPIC_RULES = 6
TOPIC_ADMIN = 27
TOPIC_REPORTS = 20
TOPIC_RAIDS = 17
TOPIC_WELCOME = 1
TOPIC_CHAT = 7
TOPIC_TRADES = 8

# ID тем в HubSup
TOPIC_MOD_CHAT = 6
TOPIC_REDACT = 8
TOPIC_APPEALS = 9
TOPIC_MODLIST = 10

# Темы, где модерация НЕ работает
IGNORED_TOPICS = [TOPIC_ADMIN, TOPIC_RULES]

# ======================= ПРОВЕРКА ТОКСИЧНОСТИ ЧЕРЕЗ LAKERA GUARD =======================
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
    created_at INTEGER
)
''')

defaults = {
    'warn_template': '{user} получает варн ({warn}/4)\n<1 варн - предупреждение\n2 варн - мут (30 мин)\n3 варн - мут (24 ч)\n4 варн - бан>',
    'ban_template': '{user} получает бан ({warn}/4)\nПричина: нарушение правил (получено 4 варна)',
    'welcome_template': '{user}, добро пожаловать в HuBBlox!\nПожалуйста, ознакомьтесь с правилами сообщества.',
    'report_template': '📋 Шаблон для аппеляции:\nНапишите текст вашей аппеляции.\nСсылка для связи: @admin',
    'rules_version': '1.0'
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

def get_user_warns(user_id):
    cursor.execute("SELECT warns FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_warn(user_id):
    current = get_user_warns(user_id)
    if current == 0:
        cursor.execute("INSERT INTO users (user_id, warns) VALUES (?, 1)", (user_id,))
    else:
        cursor.execute("UPDATE users SET warns = warns + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    return current + 1

def reset_warns_if_needed(user_id):
    cursor.execute("SELECT last_warn_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    now_ts = datetime.now().timestamp()
    if row and row[0]:
        if now_ts - row[0] > 7 * 24 * 3600:
            cursor.execute("UPDATE users SET warns=0, last_warn_time=? WHERE user_id=?", (now_ts, user_id))
            conn.commit()
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

# ======================= ИНИЦИАЛИЗАЦИЯ БОТА =======================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

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
        await message.answer("Эта команда работает только в группе HuBBlox!")
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
        await message.answer("Эта команда работает только в группе HubSup!")
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
        await message.answer("❌ Неверный код! Проверьте и попробуйте снова.")
        return
    set_config("hubsup_id", str(message.chat.id))
    await message.answer("✅ HubSup успешно связан с HuBBlox!")
    hublox_id = get_config("hublox_id")
    if hublox_id:
        await bot.send_message(
            chat_id=int(hublox_id),
            text="🔗 **HubSup успешно связан!**\nТеперь бот работает в обоих чатах."
        )

@dp.message(Command("redactwarn"))
async def redact_warn(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    new_text = message.text.replace("/redactwarn", "").strip()
    if not new_text:
        await message.answer("Напишите новый шаблон для варнов. Используйте {user}, {warn}")
        return
    set_template('warn_template', new_text)
    await message.answer("✅ Шаблон варнов обновлён!")

@dp.message(Command("redactban"))
async def redact_ban(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    new_text = message.text.replace("/redactban", "").strip()
    if not new_text:
        await message.answer("Напишите новый шаблон для банов. Используйте {user}, {warn}")
        return
    set_template('ban_template', new_text)
    await message.answer("✅ Шаблон банов обновлён!")

@dp.message(Command("redactwelcome"))
async def redact_welcome(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    new_text = message.text.replace("/redactwelcome", "").strip()
    if not new_text:
        await message.answer("Напишите новый текст приветствия. Используйте {user}")
        return
    set_template('welcome_template', new_text)
    await message.answer("✅ Приветствие обновлено!")

@dp.message(Command("redactreport"))
async def redact_report(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    new_text = message.text.replace("/redactreport", "").strip()
    if not new_text:
        await message.answer("Напишите новый текст для репортов.")
        return
    set_template('report_template', new_text)
    await message.answer("✅ Шаблон репортов обновлён!")

@dp.message(Command("redactrule"))
async def redact_rule(message: Message):
    if message.from_user.id != CREATOR_ID:
        return await message.answer("⛔ Доступно только создателю.")
    new_text = message.text.replace("/redactrule", "").strip()
    if not new_text:
        await message.answer("Напишите новые правила.")
        return
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
            message_thread_id=TOPIC_ANNOUNCEMENTS,
            text=f"📢 **Обновление правил v{new_version}**\n\n{new_text}",
            parse_mode="Markdown"
        )
    await message.answer(f"✅ Правила обновлены до версии {new_version}!")

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
    await message.answer("📝 Напишите текст вашей аппеляции. Опишите ситуацию подробно.")
    await state.set_state(AppealStates.waiting_for_text)

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
            f"Текст:\n> {appeal_text}"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_APPEALS,
            text=report,
            parse_mode="Markdown"
        )
    await message.answer(f"✅ Ваша аппеляция {number} принята. Администрация рассмотрит её в ближайшее время.")
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

# ======================= ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ =======================
@dp.message()
async def handle_all_messages(message: Message):
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

    if await is_insult(message.text):
        await process_violation(message, message.text, "оскорбление")

# ======================= ФУНКЦИЯ ОБРАБОТКИ НАРУШЕНИЙ =======================
async def process_violation(message: Message, text: str, msg_type: str):
    user = message.from_user
    warn_count = add_warn(user.id)
    user_mention = f"@{user.username}" if user.username else user.first_name

    action = ""
    if warn_count == 1:
        action = "предупреждение"
    elif warn_count == 2:
        action = "мут 30 мин"
        until = datetime.now() + timedelta(minutes=30)
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
    elif warn_count == 3:
        action = "мут 24 ч"
        until = datetime.now() + timedelta(hours=24)
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
    else:
        action = "бан навсегда"
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user.id)
        cursor.execute("UPDATE users SET banned=1, ban_until=NULL WHERE user_id=?", (user.id,))
        conn.commit()

    if warn_count < 4:
        template = get_template('warn_template')
        msg_text = template.format(user=user_mention, warn=warn_count)
    else:
        template = get_template('ban_template')
        msg_text = template.format(user=user_mention, warn=warn_count)
    await message.reply(msg_text)

    cursor.execute(
        "INSERT INTO violations (user_id, username, text, action, created_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, text, action, int(datetime.now().timestamp()))
    )
    conn.commit()

    hubsup_id = get_config("hubsup_id")
    if hubsup_id:
        report = (
            f"🚨 **Нарушение в HuBBlox**\n"
            f"Пользователь: {user_mention}\n"
            f"Тип: {msg_type}\n"
            f"Текст: `{text}`\n"
            f"Наказание: {action} (варн #{warn_count})"
        )
        await bot.send_message(
            chat_id=int(hubsup_id),
            message_thread_id=TOPIC_MOD_CHAT,
            text=report,
            parse_mode="Markdown"
        )

# ======================= ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы не было "no open ports") =======================
app = Flask('')

@app.route('/')
def home():
    return "Duosup Bot is running!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ======================= ЗАПУСК БОТА И ВЕБ-СЕРВЕРА =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("Duosup запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем веб-сервер в отдельном потоке, чтобы не блокировать бота
    threading.Thread(target=run_web, daemon=True).start()
    # Запускаем бота
    asyncio.run(main())
