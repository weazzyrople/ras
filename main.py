import subprocess
import sys

# ========== АВТОУСТАНОВКА БИБЛИОТЕК ==========
def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

try:
    import aiogram
except ImportError:
    print("Устанавливаю aiogram..."); install("aiogram")

try:
    import aiosqlite
except ImportError:
    print("Устанавливаю aiosqlite..."); install("aiosqlite")

try:
    import telethon
except ImportError:
    print("Устанавливаю telethon..."); install("telethon")

try:
    import dotenv
except ImportError:
    print("Устанавливаю python-dotenv..."); install("python-dotenv")

print("✅ Все библиотеки установлены!")
# =============================================

import asyncio
import logging
import os
from datetime import datetime, timedelta

import sys
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon import TelegramClient, errors
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from dotenv import load_dotenv

# ========== ЗАГРУЗКА .env ==========
load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID'))
_second = os.getenv('SECOND_ADMIN_ID', '')
SECOND_ADMIN_ID = int(_second) if _second.strip() else None
SESSION_STRING = os.getenv('SESSION_STRING', '')
# ====================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = 'autopost.db'

# ----- База данных -----
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                interval_minutes INTEGER DEFAULT 60,
                added_date TEXT,
                banned INTEGER DEFAULT 0
            )
        ''')
        cursor = await db.execute("PRAGMA table_info(chats)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if 'interval_minutes' not in column_names:
            await db.execute("ALTER TABLE chats ADD COLUMN interval_minutes INTEGER DEFAULT 60")
        if 'banned' not in column_names:
            await db.execute("ALTER TABLE chats ADD COLUMN banned INTEGER DEFAULT 0")

        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message TEXT,
                date TEXT
            )
        ''')
        await db.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                         ('message_text', 'Привет! Это автосообщение.'))
        await db.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                         ('posting_active', '0'))
        await db.commit()

async def get_chats(include_banned=False):
    async with aiosqlite.connect(DB_PATH) as db:
        if include_banned:
            cursor = await db.execute('SELECT chat_id, title, interval_minutes FROM chats')
        else:
            cursor = await db.execute('SELECT chat_id, title, interval_minutes FROM chats WHERE banned = 0')
        return await cursor.fetchall()

async def add_chat(chat_id, title, interval_minutes):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute('''
                INSERT INTO chats (chat_id, title, interval_minutes, added_date, banned)
                VALUES (?, ?, ?, ?, 0)
            ''', (chat_id, title, interval_minutes, datetime.now().isoformat()))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def remove_chat(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM chats WHERE chat_id = ?', (chat_id,))
        await db.commit()

async def set_chat_interval(chat_id, minutes):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE chats SET interval_minutes = ? WHERE chat_id = ?', (minutes, chat_id))
        await db.commit()

async def mark_chat_banned(chat_id, banned=True):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE chats SET banned = ? WHERE chat_id = ?', (1 if banned else 0, chat_id))
        await db.commit()

async def get_chat_info(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT title, interval_minutes, banned FROM chats WHERE chat_id = ?', (chat_id,))
        return await cursor.fetchone()

async def get_setting(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        await db.commit()

async def save_post(chat_id, message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO posts (chat_id, message, date) VALUES (?, ?, ?)',
                         (chat_id, message, datetime.now().isoformat()))
        await db.commit()

# ----- Клиенты -----
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

if SESSION_STRING:
    user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    user_client = TelegramClient('user_session', API_ID, API_HASH)

def is_owner(user_id):
    if user_id == OWNER_ID:
        return True
    if SECOND_ADMIN_ID and user_id == SECOND_ADMIN_ID:
        return True
    return False

# ----- Клавиатуры -----
def main_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить чат", callback_data="add_chat")
    b.button(text="📋 Список рассылки", callback_data="list_chats")
    b.button(text="📋 Мои чаты", callback_data="my_chats")
    b.button(text="✏️ Текст сообщения", callback_data="set_text")
    b.button(text="▶️ Запустить", callback_data="start_posting")
    b.button(text="⏸️ Остановить", callback_data="stop_posting")
    b.button(text="📊 Статус", callback_data="status")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()

def back_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="back_to_main")
    return b.as_markup()

# ----- Состояния FSM -----
class AddChat(StatesGroup):
    waiting_for_link = State()
    waiting_for_interval = State()

class SetText(StatesGroup):
    waiting = State()

class SetInterval(StatesGroup):
    waiting_for_minutes = State()

# ----- Показ списка групп -----
async def show_my_chats(callback: types.CallbackQuery, page: int):
    try:
        dialogs = await user_client.get_dialogs()
        groups = [d for d in dialogs if d.is_group]

        if not groups:
            await callback.message.edit_text("📭 Вы не участвуете ни в одной группе.", reply_markup=back_keyboard())
            return

        per_page = 10
        total_pages = (len(groups) + per_page - 1) // per_page
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = start + per_page
        page_groups = groups[start:end]

        builder = InlineKeyboardBuilder()
        for dialog in page_groups:
            title = dialog.name or "Без названия"
            chat_id = dialog.id
            if str(chat_id).startswith('-100'):
                clean_id = int(str(chat_id)[4:])
            else:
                clean_id = abs(chat_id)
            builder.button(text=title, callback_data=f"add_from_list_{clean_id}_{page}")

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️ Предыдущая", callback_data=f"my_chats_page_{page-1}"))
        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton(text="Следующая ▶️", callback_data=f"my_chats_page_{page+1}"))
        if nav_buttons:
            builder.row(*nav_buttons)

        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)

        text = f"📋 **Ваши группы (страница {page+1} из {total_pages})**\nНажмите на группу, чтобы добавить в рассылку (интервал 60 мин):"
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Ошибка в show_my_chats")
        await callback.message.edit_text(f"❌ Ошибка: {e}", reply_markup=back_keyboard())

# ----- Обработчики -----
@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔️ Доступ запрещён.")
        return
    await message.answer("👋 **Авторассылка**\nУправление через кнопки.", reply_markup=main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.edit_text("👋 Главное меню", reply_markup=main_keyboard())

@dp.callback_query(F.data == "add_chat")
async def add_chat_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.edit_text("📎 **Отправьте ссылку, юзернейм или ID чата**")
    await state.set_state(AddChat.waiting_for_link)

@dp.message(AddChat.waiting_for_link)
async def add_chat_link(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    input_text = message.text.strip()
    try:
        entity = await user_client.get_entity(input_text)
        raw_id = entity.id
        if str(raw_id).startswith('-100'):
            chat_id = int(str(raw_id)[4:])
        else:
            chat_id = abs(raw_id)
        title = getattr(entity, 'title', None) or getattr(entity, 'username', str(chat_id))
        try:
            msg = await user_client.send_message(chat_id, ".")
            await msg.delete()
        except errors.UserBannedInChannelError:
            await message.answer(f"❌ Аккаунт забанен в чате {title}.")
            await state.clear()
            return
        except Exception as e:
            logger.warning(f"Не удалось проверить права в {chat_id}: {e}")

        await state.update_data(chat_id=chat_id, title=title)
        await message.answer(f"Чат **{title}** (ID: {chat_id})\nУкажите **интервал в минутах**:")
        await state.set_state(AddChat.waiting_for_interval)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()

@dp.message(AddChat.waiting_for_interval)
async def add_chat_interval(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    try:
        interval = int(message.text.strip())
        if interval < 1:
            raise ValueError
    except:
        await message.answer("❌ Введите целое положительное число (минуты).")
        return
    data = await state.get_data()
    chat_id = data['chat_id']
    title = data['title']
    if await add_chat(chat_id, title, interval):
        await message.answer(f"✅ Чат **{title}** добавлен с интервалом {interval} мин.")
    else:
        await message.answer("❌ Чат уже в списке.")
    await state.clear()
    await message.answer("👋 Возврат в меню", reply_markup=main_keyboard())

@dp.callback_query(F.data == "my_chats")
async def my_chats_callback(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    await show_my_chats(callback, page=0)

@dp.callback_query(F.data.startswith("my_chats_page_"))
async def my_chats_page_callback(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    page = int(callback.data.split("_")[3])
    await show_my_chats(callback, page)

@dp.callback_query(F.data.startswith("add_from_list_"))
async def add_from_list(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    parts = callback.data.split("_")
    chat_id = int(parts[3])
    page = int(parts[4])
    try:
        entity = await user_client.get_entity(chat_id)
        title = getattr(entity, 'title', None) or getattr(entity, 'username', str(chat_id))
        interval = 60
        if await add_chat(chat_id, title, interval):
            await callback.answer(f"✅ Группа добавлена с интервалом {interval} мин.")
        else:
            await callback.answer("❌ Эта группа уже в списке рассылки.", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
    await show_my_chats(callback, page)

@dp.callback_query(F.data == "list_chats")
async def list_chats(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    chats = await get_chats(include_banned=True)
    if not chats:
        await callback.message.edit_text("📭 Список чатов пуст.", reply_markup=back_keyboard())
        return
    builder = InlineKeyboardBuilder()
    for cid, title, interval in chats:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute('SELECT banned FROM chats WHERE chat_id = ?', (cid,))
            banned = (await cur.fetchone())[0]
        status = " [⛔️]" if banned else ""
        builder.button(text=f"{title}{status}", callback_data=f"chat_{cid}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    await callback.message.edit_text("📋 **Список рассылки:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("chat_"))
async def chat_menu(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    chat_id = int(callback.data.split("_")[1])
    info = await get_chat_info(chat_id)
    if not info:
        await callback.answer("Чат не найден", show_alert=True)
        return
    title, interval, banned = info
    status = "⛔️ Забанен" if banned else "✅ Активен"
    text = f"**Чат:** {title}\n**ID:** `{chat_id}`\n**Статус:** {status}\n**Интервал:** {interval} мин."

    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить интервал", callback_data=f"setint_{chat_id}")
    builder.button(text="❌ Удалить чат", callback_data=f"delchat_{chat_id}")
    builder.button(text="⏱️ Установить 3 мин", callback_data=f"set3_{chat_id}")
    builder.button(text="🔙 Назад к списку", callback_data="list_chats")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("set3_"))
async def set_3min(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    chat_id = int(callback.data.split("_")[1])
    await set_chat_interval(chat_id, 3)
    await callback.answer("Интервал установлен 3 минуты")
    await chat_menu(callback)

@dp.callback_query(F.data.startswith("delchat_"))
async def delete_chat(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    chat_id = int(callback.data.split("_")[1])
    await remove_chat(chat_id)
    await callback.answer("Чат удалён")
    await list_chats(callback)

@dp.callback_query(F.data.startswith("setint_"))
async def set_interval_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    chat_id = int(callback.data.split("_")[1])
    await state.update_data(chat_id=chat_id)
    await callback.message.edit_text("Введите новый интервал в минутах:")
    await state.set_state(SetInterval.waiting_for_minutes)

@dp.message(SetInterval.waiting_for_minutes)
async def set_interval_minutes(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    try:
        minutes = int(message.text.strip())
        if minutes < 1:
            raise ValueError
    except:
        await message.answer("❌ Введите положительное число.")
        return
    data = await state.get_data()
    chat_id = data['chat_id']
    await set_chat_interval(chat_id, minutes)
    await message.answer(f"✅ Интервал обновлён на {minutes} мин.")
    await state.clear()
    await message.answer("👋 Возврат в меню", reply_markup=main_keyboard())

@dp.callback_query(F.data == "set_text")
async def set_text_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    current = await get_setting('message_text')
    await callback.message.edit_text(f"✏️ **Текущий текст:**\n{current}\n\nОтправьте новый текст:")
    await state.set_state(SetText.waiting)

@dp.message(SetText.waiting)
async def set_text_input(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    await set_setting('message_text', message.text.strip())
    await message.answer("✅ Текст обновлён.")
    await state.clear()
    await message.answer("👋 Меню", reply_markup=main_keyboard())

@dp.callback_query(F.data == "start_posting")
async def start_posting(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    await set_setting('posting_active', '1')
    await callback.message.edit_text("▶️ Рассылка запущена.", reply_markup=main_keyboard())

@dp.callback_query(F.data == "stop_posting")
async def stop_posting(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    await set_setting('posting_active', '0')
    await callback.message.edit_text("⏸️ Рассылка остановлена.", reply_markup=main_keyboard())

@dp.callback_query(F.data == "status")
async def status(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer()
        return
    active = await get_setting('posting_active')
    text = await get_setting('message_text')
    chats = await get_chats(include_banned=False)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('SELECT COUNT(*) FROM chats WHERE banned = 1')
        banned_cnt = (await cur.fetchone())[0]
    status_str = "🟢 Активна" if active == '1' else "🔴 Остановлена"
    msg = f"**Статус:** {status_str}\n**Активных чатов:** {len(chats)}\n**Забаненных:** {banned_cnt}\n**Текст:** {text}"
    await callback.message.edit_text(msg, reply_markup=back_keyboard(), parse_mode="Markdown")

# ----- Фоновая рассылка -----
async def posting_worker():
    await asyncio.sleep(5)
    last_sent = {}
    while True:
        try:
            active = await get_setting('posting_active')
            if active == '1':
                message_text = await get_setting('message_text')
                chats = await get_chats(include_banned=False)
                now = datetime.now()
                for chat_id, title, interval in chats:
                    last = last_sent.get(chat_id)
                    if last is None or (now - last) > timedelta(minutes=interval):
                        try:
                            entity = await user_client.get_entity(chat_id)
                            await user_client.send_message(entity, message_text)
                            await save_post(chat_id, message_text)
                            last_sent[chat_id] = now
                            logger.info(f"✅ Отправлено в {title} ({chat_id})")
                            await asyncio.sleep(2)
                        except FloodWaitError as e:
                            logger.warning(f"Flood wait {e.seconds}с")
                            await asyncio.sleep(e.seconds)
                        except (errors.UserBannedInChannelError, errors.ChatWriteForbiddenError) as e:
                            logger.error(f"❌ Бан в чате {chat_id}: {e}")
                            await mark_chat_banned(chat_id, True)
                        except ValueError as e:
                            logger.error(f"❌ Не найдена сущность {chat_id}: {e}")
                            await mark_chat_banned(chat_id, True)
                        except Exception as e:
                            logger.error(f"Ошибка отправки в {chat_id}: {e}")
            await asyncio.sleep(30)
        except Exception as e:
            logger.exception("Ошибка в фоновом процессе")
            await asyncio.sleep(60)

# ----- Запуск -----
async def main():
    await init_db()
    logger.info("База данных инициализирована")

    if SESSION_STRING:
        await user_client.start()
    else:
        await user_client.start(phone=PHONE)
        session_str = user_client.session.save()
        logger.info("=" * 50)
        logger.info("СОХРАНИТЕ ЭТУ СТРОКУ И ВСТАВЬТЕ В SESSION_STRING в .env:")
        logger.info(session_str)
        logger.info("=" * 50)

    logger.info("Пользовательский аккаунт подключён")
    me = await user_client.get_me()
    logger.info(f"Аккаунт: {me.first_name} (@{me.username})")

    asyncio.create_task(posting_worker())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
