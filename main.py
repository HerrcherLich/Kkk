import json
import os
import logging
from collections import deque
from datetime import datetime
from typing import Dict, Set, Deque, Any, Optional
import asyncio
import time
from threading import RLock
import signal
import sys
import telegram
print("PTB VERSION:", telegram.__version__)
import os

os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("ALL_PROXY", None)
os.environ["NO_PROXY"] = "*"

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telegram.error import RetryAfter, TimedOut, NetworkError

# ================= ЛОГИ =================
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8636522321:AAG_cgeOHZinV7vLDYGaSNiTHJe2AuXFnEA"
MAIN_ADMIN_ID = 7907064235
SUPPORT_LINK = "https://t.me/Alexanderw1rq"
DATA_FILE = "bot_data.json"
DEFAULT_HOLD_TIME = 20
SAVE_INTERVAL = 300
CLEANUP_INTERVAL = 3600
REQUEST_TIMEOUT = 120
REQUEST_COOLDOWN = 3

# ================= БЛОКИРОВКА =================
data_lock = RLock()

# ================= ДАННЫЕ =================
admins: Set[int] = {MAIN_ADMIN_ID}
banned_users: Set[int] = set()
numbers_queue: Deque[dict] = deque()
number_providers: Dict[str, int] = {}
number_tariffs: Dict[str, str] = {}
active_numbers: Dict[str, dict] = {}
number_status: Dict[str, dict] = {}
active_numbers_set: Set[str] = set()

waiting_for_message: Dict[int, str] = {}
waiting_for_photo: Dict[int, str] = {}
waiting_for_admin_action: Dict[int, dict] = {}
waiting_for_number: Dict[int, str] = {}
waiting_for_broadcast: Set[int] = set()
waiting_for_hold_time: Dict[int, Any] = {}
waiting_for_additional_time: Dict[int, dict] = {}
waiting_for_welcome_text: Set[int] = set()
waiting_for_tariff_edit: Dict[int, dict] = {}

hold_time_settings: Dict[int, int] = {}
numbers_hold_info: Dict[str, dict] = {}
username_cache: Dict[int, str] = {}
all_users: Set[int] = set()
last_request_time: Dict[int, float] = {}
last_save_time = time.time()

topic_settings: Dict[int, Dict[int, Dict[str, bool]]] = {}
allowed_groups: Dict[int, Dict[str, Any]] = {}

DEFAULT_WELCOME_TEXT = (
    "🌟 Добро пожаловать, {user_name}! 🌟\n\n"
    "Как пользоваться ботом:\n"
    "• Нажми кнопку 📱 Сдать номер или используй команду /submit\n"
    "• Выбери тариф из предложенных\n"
    "• После этого отправь номер телефона\n"
    "• Номер встанет в очередь\n"
    "• Ты будешь получать уведомления о статусе своего номера\n"
    "• В любой момент можешь посмотреть свою очередь через /queue\n"
    "• Посмотреть свои номера можно через /numbers\n\n"
    "Доступные тарифы:\n"
    "📱 КЗ ВЦ ФБХ — 3$/10 минут\n"
    "💎 КЗ ВЦ БХ — 8$/30 минут\n\n"
    "Доступные команды:\n"
    "/start - 🏠 Главное меню\n"
    "/submit - 📱 Сдать номер\n"
    "/queue - 📋 Моя очередь\n"
    "/numbers - 📊 Мои номера\n"
    "/support - 🛠 Тех поддержка"
)

welcome_text = DEFAULT_WELCOME_TEXT

BANNED_MESSAGE = (
    "⛔ ВЫ ЗАБЛОКИРОВАНЫ ⛔\n\n"
    "К сожалению, вам заблокирован доступ к этому боту.\n"
    "Если вы считаете, что это ошибка, свяжитесь с администратором."
)

tariff_stats: Dict[str, Dict[str, Any]] = {
    "kz_wc_fbx": {
        "id": "kz_wc_fbx", "name": "КЗ ВЦ ФБХ", "price": "3", "currency": "$",
        "duration": "10", "duration_unit": "минут", "description": "Базовый тариф",
        "count": 0, "emoji": "📱", "active": True
    },
    "kz_wc_bh": {
        "id": "kz_wc_bh", "name": "КЗ ВЦ БХ", "price": "8", "currency": "$",
        "duration": "30", "duration_unit": "минут", "description": "Премиум тариф",
        "count": 0, "emoji": "💎", "active": True
    }
}

# ================= СИГНАЛЫ =================
def signal_handler(sig, frame):
    print("\n👋 Останавливаем бота...")
    safe_save_data(force=True)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ================= JSON DATETIME =================
def json_default(obj):
    if isinstance(obj, datetime):
        return {"__datetime__": obj.isoformat()}
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

def restore_datetimes(obj):
    if isinstance(obj, dict):
        if "__datetime__" in obj:
            return datetime.fromisoformat(obj["__datetime__"])
        return {k: restore_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore_datetimes(x) for x in obj]
    return obj

# ================= СОХРАНЕНИЕ =================
def safe_save_data(force: bool = False):
    global last_save_time
    current_time = time.time()

    if not force and current_time - last_save_time < SAVE_INTERVAL:
        return

    with data_lock:
        try:
            data = {
                "admins": list(admins),
                "banned_users": list(banned_users),
                "numbers_queue": list(numbers_queue),
                "number_providers": number_providers,
                "number_tariffs": number_tariffs,
                "tariff_stats": tariff_stats,
                "username_cache": username_cache,
                "all_users": list(all_users),
                "hold_time_settings": hold_time_settings,
                "welcome_text": welcome_text,
                "numbers_hold_info": numbers_hold_info,
                "active_numbers": active_numbers,
                "number_status": number_status,
                "topic_settings": {
                    str(chat_id): {str(topic_id): info for topic_id, info in topics.items()}
                    for chat_id, topics in topic_settings.items()
                },
                "allowed_groups": {
                    str(chat_id): info for chat_id, info in allowed_groups.items()
                },
            }

            temp_file = DATA_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=json_default)

            os.replace(temp_file, DATA_FILE)
            last_save_time = current_time
            logger.info("💾 Данные сохранены")

        except Exception as e:
            logger.error(f"❌ Ошибка сохранения данных: {e}", exc_info=True)

def load_data():
    global admins, banned_users, all_users, welcome_text
    global number_providers, number_tariffs, username_cache, hold_time_settings
    global numbers_hold_info, number_status

    if not os.path.exists(DATA_FILE):
        logger.info("📁 Файл данных не найден, создаем новый")
        return

    try:
        with data_lock:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = restore_datetimes(json.load(f))

            admins.clear()
            admins.update(set(data.get("admins", [MAIN_ADMIN_ID])))
            admins.add(MAIN_ADMIN_ID)

            banned_users.clear()
            banned_users.update(set(data.get("banned_users", [])))

            all_users.clear()
            all_users.update(set(data.get("all_users", [])))

            welcome_text = data.get("welcome_text", DEFAULT_WELCOME_TEXT)

            numbers_queue.clear()
            for item in data.get("numbers_queue", []):
                if isinstance(item, dict) and "number" in item and "tariff" in item:
                    numbers_queue.append(item)

            number_providers.clear()
            number_providers.update(data.get("number_providers", {}))

            number_tariffs.clear()
            number_tariffs.update(data.get("number_tariffs", {}))

            username_cache.clear()
            username_cache.update({int(k) if str(k).isdigit() else k: v for k, v in data.get("username_cache", {}).items()})

            hold_time_settings.clear()
            hold_time_settings.update({int(k): v for k, v in data.get("hold_time_settings", {}).items()})

            saved_tariffs = data.get("tariff_stats", {})
            for tariff_id, tariff_data in saved_tariffs.items():
                if tariff_id in tariff_stats:
                    tariff_stats[tariff_id].update(tariff_data)

            numbers_hold_info.clear()
            numbers_hold_info.update(data.get("numbers_hold_info", {}))

            active_numbers.clear()
            active_numbers_set.clear()
            for num, info in data.get("active_numbers", {}).items():
                active_numbers[num] = info
                active_numbers_set.add(num)

            number_status.clear()
            number_status.update(data.get("number_status", {}))

            topic_settings.clear()
            for chat_id_str, topics_data in data.get("topic_settings", {}).items():
                topic_settings[int(chat_id_str)] = {
                    int(topic_id): info for topic_id, info in topics_data.items()
                }

            allowed_groups.clear()
            for chat_id_str, info in data.get("allowed_groups", {}).items():
                allowed_groups[int(chat_id_str)] = info

            logger.info("📂 Данные загружены")
            logger.info(f"👑 Главный админ: {MAIN_ADMIN_ID}")
            logger.info(f"📁 Файл данных: {DATA_FILE}")

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки данных: {e}", exc_info=True)

# ================= УТИЛЫ =================
def format_number(number: str) -> str:
    clean = "".join(filter(str.isdigit, str(number)))
    if len(clean) == 11:
        return f"+{clean[0]} ({clean[1:4]}) {clean[4:7]}-{clean[7:9]}-{clean[9:11]}"
    if len(clean) == 10:
        return f"({clean[0:3]}) {clean[3:6]}-{clean[6:8]}-{clean[8:10]}"
    return str(number)

def get_tariff_display(tariff_id: str) -> str:
    t = tariff_stats.get(tariff_id)
    if not t:
        return "❌ Неизвестный тариф"
    return f"{t['emoji']} {t['name']} — {t['price']}{t['currency']}/{t['duration']} {t['duration_unit']}"

def get_tariff_short_display(tariff_id: str) -> str:
    t = tariff_stats.get(tariff_id)
    if not t:
        return "❌ Неизвестный тариф"
    return f"{t['emoji']} {t['name']}"

def tariff_exists(tariff_id: str) -> bool:
    return tariff_id in tariff_stats

def format_welcome(text: str, user_name: str) -> str:
    try:
        return text.format(user_name=user_name)
    except Exception:
        return text.replace("{user_name}", user_name)

def check_spam(user_id: int) -> bool:
    now = time.time()
    last_time = last_request_time.get(user_id, 0)
    if now - last_time < REQUEST_COOLDOWN:
        return True
    last_request_time[user_id] = now
    return False

def add_number_to_queue(number: str, tariff: str, user_id: int) -> bool:
    with data_lock:
        if number in active_numbers_set or number in number_providers:
            return False
        for item in numbers_queue:
            if item["number"] == number:
                return False
        numbers_queue.append({"number": number, "tariff": tariff})
        number_providers[number] = user_id
        number_tariffs[number] = tariff
        if tariff in tariff_stats:
            tariff_stats[tariff]["count"] += 1
        return True

def get_number_from_queue() -> Optional[dict]:
    with data_lock:
        if not numbers_queue:
            return None

        queue_item = numbers_queue.popleft()
        num = queue_item["number"]
        tariff = queue_item["tariff"]
        provider = number_providers.get(num)

        if provider is None:
            logger.error(f"❌ Потерян владелец номера {num}")
            return None

        number_providers.pop(num, None)
        active_numbers[num] = {
            "owner_id": provider,
            "issued_time": datetime.now(),
            "tariff": tariff
        }
        active_numbers_set.add(num)

        return {"number": num, "tariff": tariff, "provider": provider}

# ================= ПРОВЕРКИ =================
async def check_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False

    user_id = update.effective_user.id
    if user_id in admins:
        return False

    with data_lock:
        if user_id in banned_users:
            try:
                if update.message:
                    await update.message.reply_text(BANNED_MESSAGE)
                elif update.callback_query:
                    await update.callback_query.answer("⛔ Вы заблокированы!", show_alert=True)
            except Exception:
                pass
            return True
    return False

async def check_banned_callback(query) -> bool:
    if not query or not query.from_user:
        return False

    user_id = query.from_user.id
    if user_id in admins:
        return False

    with data_lock:
        if user_id in banned_users:
            try:
                await query.answer("⛔ Вы заблокированы в этом боте!", show_alert=True)
            except Exception:
                pass
            return True
    return False

async def check_allowed_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_chat:
        return False

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else None

    if user_id and user_id in admins:
        return True

    with data_lock:
        if chat_id in allowed_groups:
            return True

    logger.info(f"🚫 Запрос из неразрешенной группы {chat_id}")
    return False

# ================= ПОЛУЧЕНИЕ ИНФО =================
async def get_user_info(bot, user_id: int) -> dict:
    global all_users
    with data_lock:
        if user_id not in banned_users and user_id not in admins:
            all_users.add(user_id)

    try:
        chat = await bot.get_chat(user_id)
        user_info = {
            "exists": True,
            "username": f"@{chat.username}" if getattr(chat, "username", None) else None,
            "first_name": getattr(chat, "first_name", None),
            "last_name": getattr(chat, "last_name", None),
        }

        with data_lock:
            if getattr(chat, "username", None):
                username_cache[user_id] = f"@{chat.username}"
            elif getattr(chat, "first_name", None):
                name = chat.first_name
                if getattr(chat, "last_name", None):
                    name += f" {chat.last_name}"
                username_cache[user_id] = name

        safe_save_data()
        return user_info

    except Exception as e:
        logger.error(f"Ошибка получения информации о пользователе {user_id}: {e}")
        with data_lock:
            cached_name = username_cache.get(user_id, "Не найден")
        return {"exists": False, "username": cached_name, "first_name": None, "last_name": None}

# ================= КЛАВЫ =================
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📱 Сдать номер", callback_data="give_number")],
        [InlineKeyboardButton("📋 Моя очередь", callback_data="show_my_queue")],
        [InlineKeyboardButton("📊 Мои номера", callback_data="show_my_numbers")],
        [InlineKeyboardButton("🛠 Тех поддержка", url=SUPPORT_LINK)]
    ]
    if user_id in admins:
        keyboard.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_tariff_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for tariff_id, tariff_data in tariff_stats.items():
        if tariff_data.get("active", True):
            button_text = f"{tariff_data['emoji']} {tariff_data['name']} — {tariff_data['price']}{tariff_data['currency']}/{tariff_data['duration']} {tariff_data['duration_unit']} 💰"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"tariff_{tariff_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("👥 Список админов", callback_data="admin_list"), InlineKeyboardButton("📋 Общая очередь", callback_data="show_queue_admin")],
        [InlineKeyboardButton("📊 Полная статистика", callback_data="full_stats"), InlineKeyboardButton("📊 Статистика тарифов", callback_data="tariff_stats")],
        [InlineKeyboardButton("⏱ Управление отстоем", callback_data="hold_management"), InlineKeyboardButton("📣 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton("📝 Редактировать приветствие", callback_data="edit_welcome"), InlineKeyboardButton("💰 Управление тарифами", callback_data="manage_tariffs")],
        [InlineKeyboardButton("🏢 Офиса", callback_data="offices_list"), InlineKeyboardButton("📁 Экспорт данных", callback_data="export_data")],
        [InlineKeyboardButton("🔄 Очистить очередь", callback_data="clear_queue")],
        [InlineKeyboardButton("📊 Автоотчет", callback_data="auto_report")],
        [InlineKeyboardButton("➕ Добавить админа", callback_data="admin_add"), InlineKeyboardButton("➖ Удалить админа", callback_data="admin_remove")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_tariff_management_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for tariff_id, tariff_data in tariff_stats.items():
        status = "✅" if tariff_data.get("active", True) else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {tariff_data['emoji']} {tariff_data['name']}", callback_data=f"edit_tariff_{tariff_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_tariff_edit_keyboard(tariff_id: str) -> InlineKeyboardMarkup:
    tariff = tariff_stats.get(tariff_id)
    if not tariff:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="manage_tariffs")]])
    status = "Деактивировать" if tariff.get("active", True) else "Активировать"
    keyboard = [
        [InlineKeyboardButton("✏️ Изменить название", callback_data=f"edit_name_{tariff_id}")],
        [InlineKeyboardButton("💰 Изменить цену", callback_data=f"edit_price_{tariff_id}")],
        [InlineKeyboardButton("⏱ Изменить время", callback_data=f"edit_duration_{tariff_id}")],
        [InlineKeyboardButton("💱 Изменить валюту", callback_data=f"edit_currency_{tariff_id}")],
        [InlineKeyboardButton("📝 Изменить описание", callback_data=f"edit_desc_{tariff_id}")],
        [InlineKeyboardButton(f"🔄 {status}", callback_data=f"toggle_tariff_{tariff_id}")],
        [InlineKeyboardButton("🔙 Назад к тарифам", callback_data="manage_tariffs")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_hold_management_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current_time = hold_time_settings.get(user_id, DEFAULT_HOLD_TIME)
    buttons = [
        [InlineKeyboardButton(f"⏱ Текущее: {current_time} мин", callback_data="show_current_hold_time"),
         InlineKeyboardButton("✏️ Установить время", callback_data="set_hold_time")],
        [InlineKeyboardButton("📊 Отчет по отстою", callback_data="hold_report"),
         InlineKeyboardButton("➕ Добавить время", callback_data="add_hold_time")],
        [InlineKeyboardButton("❌ Отчет по ошибкам", callback_data="error_report"),
         InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_group_keyboard(number: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✅ Встал", callback_data=f"g_{number}"), InlineKeyboardButton("❌ Слетел", callback_data=f"f_{number}")],
        [InlineKeyboardButton("📝 Сообщение", callback_data=f"m_{number}"), InlineKeyboardButton("📸 Фото", callback_data=f"p_{number}")],
        [InlineKeyboardButton("⚠️ Ошибка", callback_data=f"e_{number}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ================= ТЕКСТЫ =================
async def get_user_numbers_text(user_id: int) -> str:
    with data_lock:
        numbers = []

        for item in numbers_queue:
            if number_providers.get(item["number"]) == user_id:
                numbers.append((item["number"], item["tariff"], "⏳ В очереди", None))

        for num, info in active_numbers.items():
            if info["owner_id"] == user_id:
                issued = info.get("issued_time")
                if isinstance(issued, datetime):
                    time_diff = int((datetime.now() - issued).total_seconds() / 60)
                else:
                    time_diff = 0
                numbers.append((num, info["tariff"], f"🔄 В работе ({time_diff} мин)", issued if isinstance(issued, datetime) else None))

        for num, info in number_status.items():
            if info.get("owner_id") == user_id:
                status = info.get("status")
                tm = info.get("time")
                if status == "встал":
                    hold_time = info.get("hold_time")
                    status_text = f"✅ Встал {hold_time.strftime('%H:%M')}" if isinstance(hold_time, datetime) else "✅ Встал"
                elif status == "слет":
                    end_time = info.get("end_time")
                    status_text = f"❌ Слетел {end_time.strftime('%H:%M')}" if isinstance(end_time, datetime) else "❌ Слетел"
                else:
                    status_text = "⚠️ Ошибка"
                numbers.append((num, info.get("tariff"), status_text, tm if isinstance(tm, datetime) else None))

        if not numbers:
            return "📊 МОИ НОМЕРА\n\nУ вас пока нет сданных номеров.\n\nЧтобы сдать номер, используйте команду /submit"

        numbers.sort(key=lambda x: x[3] or datetime.min, reverse=True)
        text = "📊 МОИ НОМЕРА\n\n"
        for num, tariff, status, tm in numbers[:20]:
            text += f"📱 {format_number(num)}\n💰 {get_tariff_display(tariff)}\n📌 {status}"
            if tm:
                text += f" 🕐 {tm.strftime('%H:%M')}"
            text += "\n\n"
        if len(numbers) > 20:
            text += f"\n🔒 Показаны последние 20 номеров из {len(numbers)}"
        return text

async def get_queue_text_for_user(user_id: int) -> str:
    with data_lock:
        user_numbers = []
        position = 1
        for item in numbers_queue:
            if number_providers.get(item["number"]) == user_id:
                user_numbers.append((item["number"], position, item["tariff"]))
            position += 1

        if user_numbers:
            text = f"📋 ВАША ОЧЕРЕДЬ\n\n📊 Всего в очереди: {len(numbers_queue)}\n👤 Ваших номеров: {len(user_numbers)}\n\n📱 Ваши номера:\n"
            for num, pos, tariff in user_numbers:
                text += f"• {format_number(num)}\n  {get_tariff_display(tariff)}\n  📍 Позиция #{pos}\n\n"
            return text
        return "📋 У вас нет номеров в очереди" if numbers_queue else "📋 Очередь номеров пуста"

async def get_queue_text_for_admin() -> str:
    with data_lock:
        if not numbers_queue:
            return "📋 Очередь номеров пуста"

        text = "📋 ОБЩАЯ ОЧЕРЕДЬ (Админ-панель):\n\n"
        for i, item in enumerate(numbers_queue, 1):
            formatted = format_number(item["number"])
            provider_id = number_providers.get(item["number"])
            tariff_display = get_tariff_short_display(item["tariff"])
            provider_info = ""
            if provider_id:
                cached_name = username_cache.get(provider_id, f"ID: {provider_id}")
                provider_info = f"👤 {cached_name} (ID: {provider_id})"
            text += f"{i}. {tariff_display} {formatted}\n   {provider_info}\n\n"
        text += f"\n📊 Всего в очереди: {len(numbers_queue)}"
        return text

async def get_tariff_stats_text() -> str:
    with data_lock:
        text = "📊 СТАТИСТИКА ПО ТАРИФАМ\n\n"
        total = sum(t["count"] for t in tariff_stats.values())
        text += f"📈 Всего использовано номеров: {total}\n\n"
        for _, tariff_data in tariff_stats.items():
            status = "✅ Активен" if tariff_data.get("active", True) else "❌ Неактивен"
            text += (
                f"{tariff_data['emoji']} {tariff_data['name']}\n"
                f"💰 {tariff_data['price']}{tariff_data['currency']}/{tariff_data['duration']} {tariff_data['duration_unit']}\n"
                f"📝 {tariff_data['description']}\n"
                f"📊 Использовано: {tariff_data['count']} номеров\n"
                f"📌 Статус: {status}\n\n"
            )
        return text

async def get_full_stats_text() -> str:
    with data_lock:
        text = "📊 ПОЛНАЯ СТАТИСТИКА БОТА\n\n"
        text += f"📱 Номеров в очереди: {len(numbers_queue)}\n"
        text += f"✅ Активных номеров: {len(active_numbers)}\n"
        text += f"👥 Всего админов: {len(admins)}\n"
        text += f"👥 Всего пользователей: {len(all_users)}\n"
        text += f"⛔ Забанено пользователей: {len(banned_users)}\n"
        text += f"🏢 Офисов: {len(allowed_groups)}\n\n"

        total_numbers = sum(t["count"] for t in tariff_stats.values())
        text += "💰 Статистика по тарифам:\n"
        for _, tariff_data in tariff_stats.items():
            text += f"{tariff_data['emoji']} {tariff_data['name']}: {tariff_data['count']} номеров\n"
        text += f"📊 Всего использовано: {total_numbers} номеров\n\n"

        text += "📈 ДЕТАЛЬНАЯ СТАТИСТИКА ИСПОЛЬЗОВАНИЯ:\n\n"

        all_records = []

        for num, info in active_numbers.items():
            owner_id = info["owner_id"]
            owner_name = username_cache.get(owner_id, f"ID: {owner_id}")
            all_records.append({
                "number": num,
                "tariff": info.get("tariff"),
                "status": "🟡 Активен",
                "time": info.get("issued_time") if isinstance(info.get("issued_time"), datetime) else datetime.min,
                "owner": owner_name,
                "owner_id": owner_id
            })

        for num, info in number_status.items():
            owner_id = info.get("owner_id")
            owner_name = username_cache.get(owner_id, f"ID: {owner_id}") if owner_id else "Неизвестно"
            hold_time = info.get("hold_time")
            end_time = info.get("end_time")

            if info.get("status") == "встал" and isinstance(hold_time, datetime):
                status_text = f"✅ Встал {hold_time.strftime('%H:%M')}"
            elif info.get("status") == "слет" and isinstance(end_time, datetime):
                status_text = f"❌ Слетел {end_time.strftime('%H:%M')}"
            elif info.get("status") == "слет":
                status_text = "❌ Слетел"
            elif info.get("status") == "ошибка":
                status_text = "⚠️ Ошибка"
            else:
                status_text = f"🟡 {info.get('status', 'неизвестно')}"

            all_records.append({
                "number": num,
                "tariff": info.get("tariff"),
                "status": status_text,
                "time": info.get("time") if isinstance(info.get("time"), datetime) else datetime.min,
                "owner": owner_name,
                "owner_id": owner_id
            })

        all_records.sort(key=lambda x: x["time"], reverse=True)
        text += f"📋 ПОСЛЕДНИЕ НОМЕРА (всего: {len(all_records)}):\n\n"

        for record in all_records[:30]:
            formatted = format_number(record["number"])
            tariff_emoji = tariff_stats.get(record["tariff"], {}).get("emoji", "📱")
            text += f"{tariff_emoji} {formatted}\n"
            text += f"{record['status']}\n"
            if record["owner_id"]:
                owner_display = record["owner"]
                if str(owner_display).startswith("ID:"):
                    owner_display = f"ID: {record['owner_id']}"
                text += f"👤 {owner_display}\n\n"
            else:
                text += "👤 Неизвестно\n\n"

        if len(all_records) > 30:
            text += f"\n🔒 Показаны последние 30 номеров из {len(all_records)}"

        return text

async def get_hold_report(admin_id: int = None) -> str:
    with data_lock:
        text = "⏱ ОТЧЕТ ПО ОТСТОЮ\n\n"
        hold_numbers, not_hold_numbers, error_numbers = [], [], []
        hold_time = hold_time_settings.get(admin_id, DEFAULT_HOLD_TIME) if admin_id else DEFAULT_HOLD_TIME

        text += f"⏱ Время отстоя: {hold_time} минут\n\n"
        tariff_emojis = {"kz_wc_fbx": "📱", "kz_wc_bh": "💎"}

        for number, info in active_numbers.items():
            issued = info.get("issued_time")
            minutes = int((datetime.now() - issued).total_seconds() / 60) if isinstance(issued, datetime) else 0
            tariff_emoji = tariff_emojis.get(info.get("tariff", "unknown"), "📱")
            status = number_status.get(number, {}).get("status", "active")
            owner = username_cache.get(info["owner_id"], f"ID: {info['owner_id']}")
            if status == "ошибка":
                error_numbers.append((number, minutes, tariff_emoji, owner))
            elif minutes >= hold_time:
                hold_numbers.append((number, minutes, tariff_emoji, owner))
            else:
                not_hold_numbers.append((number, minutes, tariff_emoji, owner))

        text += f"✅ Отстояли ({len(hold_numbers)}):\n" + ("\n".join([f"• {emoji} {format_number(num)} - {mins} мин (👤 {owner})" for num, mins, emoji, owner in hold_numbers]) or "• Нет номеров") + "\n"
        text += f"\n⏳ В процессе ({len(not_hold_numbers)}):\n" + ("\n".join([f"• {emoji} {format_number(num)} - {mins} мин (👤 {owner})" for num, mins, emoji, owner in not_hold_numbers]) or "• Нет номеров") + "\n"
        text += f"\n⚠️ Ошибки ({len(error_numbers)}):\n" + ("\n".join([f"• {emoji} {format_number(num)} - {mins} мин (👤 {owner})" for num, mins, emoji, owner in error_numbers]) or "• Нет ошибок") + "\n"
        return text

async def get_error_report() -> str:
    with data_lock:
        text = "❌ ОТЧЕТ ПО ОШИБКАМ\n\n"
        errors = []
        for num, info in number_status.items():
            if info.get("status") == "ошибка":
                owner_id = info.get("owner_id")
                owner = username_cache.get(owner_id, f"ID: {owner_id}") if owner_id else "Неизвестно"
                tariff_emoji = tariff_stats.get(info.get("tariff"), {}).get("emoji", "📱")
                tm = info.get("time")
                errors.append((num, tm if isinstance(tm, datetime) else None, tariff_emoji, owner, owner_id))

        if errors:
            for num, tm, emoji, owner, owner_id in errors:
                text += f"• {emoji} {format_number(num)}\n"
                text += f"  🕐 {tm.strftime('%d.%m %H:%M') if tm else '-'}\n"
                text += f"  👤 {owner}"
                if owner_id:
                    text += f" (ID: {owner_id})"
                text += "\n\n"
            text += f"\n📊 Всего ошибок: {len(errors)}"
        else:
            text += "Ошибок не зафиксировано"

        return text

async def get_admin_list_text(bot) -> str:
    text = "👑 СПИСОК АДМИНИСТРАТОРОВ\n\n"
    for i, admin_id in enumerate(sorted(admins), 1):
        user_info = await get_user_info(bot, admin_id)
        crown = "👑 " if admin_id == MAIN_ADMIN_ID else ""
        if user_info["exists"]:
            name = " ".join(filter(None, [user_info.get("first_name"), user_info.get("last_name")])) or "Без имени"
            username = user_info.get("username", "")
            if username:
                text += f"{i}. {crown}{username}\n   📍 ID: {admin_id}\n   👤 {name}\n\n"
            else:
                text += f"{i}. {crown}ID: {admin_id}\n   👤 {name}\n\n"
        else:
            cached = username_cache.get(admin_id, "Недоступен")
            text += f"{i}. {crown}⚠️ {cached}\n   📍 ID: {admin_id}\n\n"

    text += f"\n📊 Всего админов: {len(admins)}\n\n👑 Главный администратор может добавлять и удалять админов."
    return text

# ================= SAFE SEND =================
async def safe_send_message(bot, chat_id: int, text: str, parse_mode=None, reply_markup=None, max_retries=5, message_thread_id=None):
    for attempt in range(max_retries):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                message_thread_id=message_thread_id,
                read_timeout=REQUEST_TIMEOUT,
                write_timeout=REQUEST_TIMEOUT,
                connect_timeout=REQUEST_TIMEOUT
            )
        except (RetryAfter, TimedOut, NetworkError) as e:
            wait_time = getattr(e, "retry_after", 2 ** attempt)
            logger.warning(f"⚠️ Ошибка отправки (попытка {attempt + 1}/{max_retries}): {e}")
            await asyncio.sleep(min(wait_time, 10))
        except Exception as e:
            if "blocked" in str(e).lower():
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            raise
    return None

# ================= КОМАНДЫ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_banned(update, context):
        return
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Пользователь"
    await get_user_info(context.bot, user_id)
    logger.info(f"👋 Пользователь {user_name} ({user_id}) запустил бота")
    await update.message.reply_text(format_welcome(welcome_text, user_name), reply_markup=get_main_keyboard(user_id))

async def submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_banned(update, context):
        return
    user_id = update.effective_user.id
    await get_user_info(context.bot, user_id)
    text = "💰 Выберите тариф для вашего номера:\n\n"
    for tariff_id, tariff_data in tariff_stats.items():
        if tariff_data.get("active", True):
            text += f"{get_tariff_display(tariff_id)}\n"
            if tariff_data["description"]:
                text += f"📝 {tariff_data['description']}\n\n"
    await update.message.reply_text(text, reply_markup=get_tariff_keyboard())

async def queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_banned(update, context):
        return
    user_id = update.effective_user.id
    await update.message.reply_text(
        await get_queue_text_for_user(user_id),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="refresh_my_queue")]])
    )

async def numbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_banned(update, context):
        return
    user_id = update.effective_user.id
    await get_user_info(context.bot, user_id)
    await update.message.reply_text(await get_user_numbers_text(user_id))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_banned(update, context):
        return
    await update.message.reply_text(
        "🛠 Техническая поддержка\n\nЕсли у вас возникли вопросы или проблемы:\n• Нажмите кнопку ниже\n• Опишите вашу проблему\n• Мы ответим в ближайшее время",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👨‍💻 Написать в техподдержку", url=SUPPORT_LINK)]])
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admins and await check_banned(update, context):
        return

    if user_id in waiting_for_number:
        del waiting_for_number[user_id]
        await update.message.reply_text("❌ Отправка номера отменена", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_broadcast:
        waiting_for_broadcast.remove(user_id)
        await update.message.reply_text("❌ Рассылка отменена", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_admin_action:
        del waiting_for_admin_action[user_id]
        await update.message.reply_text("❌ Действие отменено", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_hold_time:
        del waiting_for_hold_time[user_id]
        await update.message.reply_text("❌ Установка времени отменена", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_additional_time:
        del waiting_for_additional_time[user_id]
        await update.message.reply_text("❌ Добавление времени отменено", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_welcome_text:
        waiting_for_welcome_text.remove(user_id)
        await update.message.reply_text("❌ Редактирование приветствия отменено", reply_markup=get_main_keyboard(user_id))
    elif user_id in waiting_for_tariff_edit:
        del waiting_for_tariff_edit[user_id]
        await update.message.reply_text("❌ Редактирование тарифа отменено", reply_markup=get_main_keyboard(user_id))
    else:
        await update.message.reply_text("❌ Нет активного действия")

# ================= ОФИСЫ =================
async def addoffice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Без названия"

    if user_id not in admins:
        await update.message.reply_text("⛔ Только администраторы могут добавлять офисы!")
        return

    with data_lock:
        if chat_id in allowed_groups:
            await update.message.reply_text(f"🏢 Офис «{chat_title}» уже есть в белом списке!", message_thread_id=update.message.message_thread_id)
            return
        allowed_groups[chat_id] = {"title": chat_title, "added_by": user_id, "added_at": datetime.now()}

    safe_save_data(force=True)
    logger.info(f"✅ Офис {chat_title} ({chat_id}) добавлен админом {user_id}")

    await update.message.reply_text(
        f"✅ Офис успешно добавлен!\n\n🏢 Название: {chat_title}\n🆔 ID: {chat_id}\n👤 Добавил: {update.effective_user.first_name}\n\n📌 Теперь в этой группе можно включать топики через /set и получать номера.",
        message_thread_id=update.message.message_thread_id
    )

async def removeoffice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Без названия"

    if user_id not in admins:
        await update.message.reply_text("⛔ Только администраторы могут удалять офисы!")
        return

    with data_lock:
        if chat_id not in allowed_groups:
            await update.message.reply_text(f"❌ Офис «{chat_title}» не найден в белом списке!", message_thread_id=update.message.message_thread_id)
            return
        del allowed_groups[chat_id]

    safe_save_data(force=True)
    logger.info(f"✅ Офис {chat_title} ({chat_id}) удален админом {user_id}")

    await update.message.reply_text(
        f"✅ Офис успешно удален!\n\n🏢 Название: {chat_title}\n🆔 ID: {chat_id}\n👤 Удалил: {update.effective_user.first_name}\n\n📌 Теперь бот не будет работать в этой группе.",
        message_thread_id=update.message.message_thread_id
    )

async def offices_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in admins:
        await query.answer("⛔ Доступ запрещен!", show_alert=True)
        return

    await query.answer()

    if not allowed_groups:
        await query.message.edit_text(
            "🏢 СПИСОК ОФИСОВ\n\n📭 Нет добавленных офисов.\n\nЧтобы добавить офис:\n1. Зайдите в нужную группу\n2. Напишите команду /addoffice",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
        )
        return

    text = "🏢 СПИСОК ОФИСОВ\n\n"
    keyboard = []

    for chat_id, info in allowed_groups.items():
        added_at = info.get("added_at")
        added_time = added_at.strftime("%d.%m.%Y %H:%M") if isinstance(added_at, datetime) else "-"
        text += f"• {info['title']}\n  🆔 {chat_id}\n  👤 Добавил: ID {info['added_by']}\n  📅 {added_time}\n\n"
        keyboard.append([InlineKeyboardButton(f"❌ Удалить {info['title']}", callback_data=f"deloffice_{chat_id}")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_office_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in admins:
        await query.answer("⛔ Доступ запрещен!", show_alert=True)
        return

    chat_id = int(query.data.replace("deloffice_", ""))

    with data_lock:
        if chat_id in allowed_groups:
            chat_title = allowed_groups[chat_id]["title"]
            del allowed_groups[chat_id]
        else:
            await query.answer("❌ Офис не найден!", show_alert=True)
            return

    safe_save_data(force=True)
    await query.answer(f"✅ Офис «{chat_title}» удален!")
    logger.info(f"✅ Офис {chat_title} ({chat_id}) удален админом {user_id} через панель")
    await offices_list(update, context)

# ================= БАН =================
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admins:
        await update.message.reply_text("⛔ У вас нет прав на эту команду!")
        return

    if not context.args:
        await update.message.reply_text("❌ Использование: /ban <id>\n\nПример: /ban 123456789")
        return

    try:
        target_id = int(context.args[0])
        if target_id in admins or target_id == MAIN_ADMIN_ID:
            await update.message.reply_text("❌ Нельзя заблокировать администратора!")
            return

        with data_lock:
            if target_id in banned_users:
                await update.message.reply_text(f"⚠️ Пользователь {target_id} уже заблокирован!")
                return
            banned_users.add(target_id)

        safe_save_data(force=True)
        user_info = await get_user_info(context.bot, target_id)
        name = "Неизвестно"
        if user_info["exists"]:
            name = " ".join(filter(None, [user_info.get("first_name"), user_info.get("last_name")])) or "Без имени"

        await update.message.reply_text(f"✅ Пользователь заблокирован!\n\n👤 ID: {target_id}\n👤 Имя: {name}\n👥 Всего забанено: {len(banned_users)}")
        logger.info(f"🔨 Пользователь {target_id} ({name}) забанен админом {user_id}")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом!")
    except Exception as e:
        logger.error(f"Ошибка при бане: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка!")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admins:
        await update.message.reply_text("⛔ У вас нет прав на эту команду!")
        return

    if not context.args:
        await update.message.reply_text("❌ Использование: /unban <id>\n\nПример: /unban 123456789")
        return

    try:
        target_id = int(context.args[0])

        with data_lock:
            if target_id not in banned_users:
                await update.message.reply_text(f"⚠️ Пользователь {target_id} не в бане!")
                return
            banned_users.discard(target_id)

        safe_save_data(force=True)
        user_info = await get_user_info(context.bot, target_id)
        name = "Неизвестно"
        if user_info["exists"]:
            name = " ".join(filter(None, [user_info.get("first_name"), user_info.get("last_name")])) or "Без имени"

        await update.message.reply_text(f"✅ Пользователь разблокирован!\n\n👤 ID: {target_id}\n👤 Имя: {name}\n👥 Осталось забанено: {len(banned_users)}")
        logger.info(f"🔓 Пользователь {target_id} ({name}) разбанен админом {user_id}")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом!")
    except Exception as e:
        logger.error(f"Ошибка при разбане: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка!")

async def banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admins:
        await update.message.reply_text("⛔ У вас нет прав на эту команду!")
        return

    with data_lock:
        if not banned_users:
            await update.message.reply_text("📋 Список забаненных пуст")
            return

        text = "📋 СПИСОК ЗАБАНЕННЫХ ПОЛЬЗОВАТЕЛЕЙ\n\n"
        for i, banned_id in enumerate(sorted(banned_users), 1):
            cached_name = username_cache.get(banned_id, "Неизвестно")
            text += f"{i}. ID: {banned_id}\n   👤 {cached_name}\n\n"

        text += f"\n📊 Всего забанено: {len(banned_users)}"
        await update.message.reply_text(text)

# ================= /SET =================
async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ Эта команда работает только в группах!")
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    message_thread_id = update.message.message_thread_id

    if message_thread_id is None:
        await update.message.reply_text("❌ Эта команда работает только в топиках!\nСоздайте топик и напишите /set в нём.")
        return

    if user_id not in admins:
        await update.message.reply_text("⛔ Только администратор может использовать эту команду!")
        return

    logger.info(f"🔧 Команда /set в чате {chat_id}, топик {message_thread_id} от пользователя {user_id}")

    if chat_id not in topic_settings:
        topic_settings[chat_id] = {}

    current_state = topic_settings[chat_id].get(message_thread_id, {}).get("enabled", False)
    new_state = not current_state
    topic_settings[chat_id][message_thread_id] = {"enabled": new_state}
    safe_save_data(force=True)

    if new_state:
        await update.message.reply_text(
            "✅ Режим выдачи номеров ВКЛЮЧЕН для этого топика!\n\nТеперь при запросе «номер» в ЭТОМ топике будут выдаваться номера из очереди.\nЧтобы выключить, снова напишите /set",
            message_thread_id=message_thread_id
        )
    else:
        await update.message.reply_text(
            "❌ Режим выдачи номеров ВЫКЛЮЧЕН для этого топика.\nНомера больше не будут выдаваться сюда.",
            message_thread_id=message_thread_id
        )

# ================= РАССЫЛКА =================
async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in admins:
        await query.answer("⛔ Доступ запрещен!", show_alert=True)
        return

    waiting_for_broadcast.add(user_id)
    await query.answer()
    await query.message.edit_text(
        f"📣 РАССЫЛКА СООБЩЕНИЯ\n\nОтправьте текст для рассылки.\n\n👥 Всего пользователей: {len(all_users)}\n\n❌ Отправьте /cancel для отмены",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]])
    )

async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if user_id not in waiting_for_broadcast:
        return

    waiting_for_broadcast.remove(user_id)
    status_msg = await update.message.reply_text("📣 Начинаю рассылку...")

    successful, failed, blocked = 0, 0, 0
    users_list = list(all_users)
    total = len(users_list)

    for i, target_id in enumerate(users_list):
        try:
            await safe_send_message(context.bot, target_id, f"📣 РАССЫЛКА ОТ АДМИНА:\n\n{text}")
            successful += 1
            if (i + 1) % 10 == 0:
                await status_msg.edit_text(f"📣 Рассылка: {successful}/{total}")
            await asyncio.sleep(0.1)
        except Exception as e:
            if "blocked" in str(e).lower():
                blocked += 1
            else:
                failed += 1

    await status_msg.edit_text(
        f"📣 РАССЫЛКА ЗАВЕРШЕНА!\n\n✅ Успешно: {successful}\n❌ Ошибок: {failed}\n🚫 Заблокировали: {blocked}"
    )

# ================= PRIVATE =================
async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global welcome_text
    try:
        if not update.message or not update.message.text:
            return

        user_id = update.message.from_user.id
        text = update.message.text.strip()

        if user_id not in admins:
            in_waiting = (
                user_id in waiting_for_number
                or user_id in waiting_for_broadcast
                or user_id in waiting_for_admin_action
                or user_id in waiting_for_hold_time
                or user_id in waiting_for_additional_time
                or user_id in waiting_for_welcome_text
                or user_id in waiting_for_tariff_edit
            )
            if not in_waiting and await check_banned(update, context):
                return

        logger.info(f"💬 Личное сообщение от {user_id}: {text[:50]}")
        await get_user_info(context.bot, user_id)

        if user_id in waiting_for_broadcast:
            await execute_broadcast(update, context)
            return

        if user_id in waiting_for_number:
            clean = "".join(filter(str.isdigit, text))
            if clean.isdigit() and len(clean) >= 10:
                tariff = waiting_for_number.pop(user_id)

                if clean in active_numbers_set or clean in number_providers:
                    await update.message.reply_text("❌ Этот номер уже находится в очереди или в работе!")
                    return

                if add_number_to_queue(clean, tariff, user_id):
                    safe_save_data(force=True)
                    await update.message.reply_text(
                        f"✅ Номер успешно добавлен в очередь!\n\n"
                        f"📱 Номер: {format_number(clean)}\n"
                        f"💰 Тариф: {get_tariff_display(tariff)}\n"
                        f"📍 Позиция в очереди: {len(numbers_queue)}"
                    )
                    logger.info(f"✅ Номер {clean} добавлен пользователем {user_id} с тарифом {tariff}")
                else:
                    await update.message.reply_text("❌ Этот номер уже есть в очереди или в работе.")
                return
            else:
                await update.message.reply_text(
                    "❌ Это не похоже на номер телефона\n\n"
                    "Пожалуйста, отправьте 10 или более цифр\n"
                    "Например: 89991234567 или +79991234567\n\n"
                    "❌ Отправьте /cancel для отмены"
                )
                return

        if user_id in waiting_for_welcome_text:
            waiting_for_welcome_text.remove(user_id)
            welcome_text = text
            safe_save_data(force=True)
            await update.message.reply_text("✅ Текст приветствия успешно обновлен!", reply_markup=get_main_keyboard(user_id))
            return

        if user_id in waiting_for_tariff_edit:
            edit_data = waiting_for_tariff_edit[user_id]
            tariff_id = edit_data["tariff_id"]
            field = edit_data["field"]

            if tariff_id in tariff_stats:
                if field == "name":
                    tariff_stats[tariff_id]["name"] = text
                elif field == "price":
                    try:
                        float(text)
                        tariff_stats[tariff_id]["price"] = text
                    except ValueError:
                        await update.message.reply_text("❌ Цена должна быть числом!", reply_markup=get_main_keyboard(user_id))
                        return
                elif field == "duration":
                    try:
                        int(text)
                        tariff_stats[tariff_id]["duration"] = text
                    except ValueError:
                        await update.message.reply_text("❌ Время должно быть числом!", reply_markup=get_main_keyboard(user_id))
                        return
                elif field == "currency":
                    tariff_stats[tariff_id]["currency"] = text
                elif field == "description":
                    tariff_stats[tariff_id]["description"] = text

                safe_save_data(force=True)
                await update.message.reply_text(
                    f"✅ Тариф успешно обновлен!\n\n{get_tariff_display(tariff_id)}",
                    reply_markup=get_main_keyboard(user_id)
                )

            del waiting_for_tariff_edit[user_id]
            return

        if user_id in waiting_for_hold_time:
            try:
                minutes = int(text)
                if minutes <= 0:
                    await update.message.reply_text("❌ Введите положительное число!", reply_markup=get_main_keyboard(user_id))
                    return
                hold_time_settings[user_id] = minutes
                safe_save_data(force=True)
                del waiting_for_hold_time[user_id]
                await update.message.reply_text(f"✅ Время отстоя установлено: {minutes} минут", reply_markup=get_main_keyboard(user_id))
            except ValueError:
                await update.message.reply_text("❌ Введите число (минуты)!", reply_markup=get_main_keyboard(user_id))
            return

        if user_id in waiting_for_additional_time:
            try:
                minutes = int(text)
                if minutes <= 0:
                    await update.message.reply_text("❌ Введите положительное число!", reply_markup=get_main_keyboard(user_id))
                    return

                number_info = waiting_for_additional_time[user_id]
                number = number_info["number"]

                if number in active_numbers:
                    numbers_hold_info[number] = {
                        "additional_time": minutes,
                        "added_by": user_id,
                        "added_at": datetime.now()
                    }
                    safe_save_data(force=True)
                    await update.message.reply_text(
                        f"✅ К номеру {format_number(number)} добавлено {minutes} минут",
                        reply_markup=get_main_keyboard(user_id)
                    )
                else:
                    await update.message.reply_text("❌ Номер не найден в активных!", reply_markup=get_main_keyboard(user_id))

                del waiting_for_additional_time[user_id]
            except ValueError:
                await update.message.reply_text("❌ Введите число (минуты)!", reply_markup=get_main_keyboard(user_id))
            return

        if user_id in waiting_for_admin_action:
            action_data = waiting_for_admin_action[user_id]
            action = action_data["action"]

            if action == "confirm_add" and text.lower() in ["да", "yes", "д", "+", "ok", "lf"]:
                target_id = action_data["target_id"]
                admins.add(target_id)
                safe_save_data(force=True)
                await update.message.reply_text(f"✅ Администратор добавлен принудительно!\n\n⚠️ ID: {target_id}", reply_markup=get_main_keyboard(user_id))
                del waiting_for_admin_action[user_id]
                return
            elif action == "confirm_add":
                await update.message.reply_text("❌ Добавление отменено", reply_markup=get_main_keyboard(user_id))
                del waiting_for_admin_action[user_id]
                return

            try:
                if text.startswith("@"):
                    await update.message.reply_text(
                        "❌ Пожалуйста, отправьте числовой ID пользователя, а не username!\n\n"
                        "🔍 Как узнать ID:\n1️⃣ Попросите пользователя написать боту\n2️⃣ Или используйте @getmyid_bot",
                        reply_markup=get_main_keyboard(user_id)
                    )
                    return

                target_id = int(text)

                if action == "add":
                    if target_id in admins:
                        await update.message.reply_text("❌ Этот пользователь уже является администратором!", reply_markup=get_main_keyboard(user_id))
                        return

                    user_info = await get_user_info(context.bot, target_id)
                    if user_info["exists"]:
                        admins.add(target_id)
                        name = " ".join(filter(None, [user_info.get("first_name"), user_info.get("last_name")])) or "Без имени"
                        safe_save_data(force=True)
                        await update.message.reply_text(
                            f"✅ Администратор успешно добавлен!\n\n👤 ID: {target_id}\n👤 Имя: {name}\n👥 Всего админов: {len(admins)}",
                            reply_markup=get_main_keyboard(user_id)
                        )
                        try:
                            await safe_send_message(context.bot, target_id, "🎉 Поздравляем! Вас назначили администратором бота!")
                        except Exception:
                            pass
                    else:
                        waiting_for_admin_action[user_id] = {"action": "confirm_add", "target_id": target_id}
                        await update.message.reply_text(
                            f"⚠️ Пользователь с ID {target_id} не найден!\n\n❓ Всё равно добавить этого пользователя в админы?\n(Отправьте да или нет)",
                            reply_markup=get_main_keyboard(user_id)
                        )
                    return

                elif action == "remove":
                    if target_id == MAIN_ADMIN_ID:
                        await update.message.reply_text("❌ Нельзя удалить главного администратора!", reply_markup=get_main_keyboard(user_id))
                        return

                    if target_id not in admins:
                        await update.message.reply_text("❌ Этот пользователь не является администратором!", reply_markup=get_main_keyboard(user_id))
                        return

                    admins.remove(target_id)
                    safe_save_data(force=True)
                    await update.message.reply_text(
                        f"✅ Администратор успешно удален!\n\n👤 ID: {target_id}\n👥 Осталось админов: {len(admins)}",
                        reply_markup=get_main_keyboard(user_id)
                    )
                    return

            except ValueError:
                await update.message.reply_text(
                    "❌ Пожалуйста, отправьте число (ID пользователя)\n\nПример: 123456789",
                    reply_markup=get_main_keyboard(user_id)
                )
                return

        await update.message.reply_text(
            "❌ Сначала нажмите кнопку «📱 Сдать номер»\n\nили используйте команду /submit",
            reply_markup=get_main_keyboard(user_id)
        )

    except Exception as e:
        logger.error(f"💥 Критическая ошибка в handle_private: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ Произошла ошибка при обработке сообщения. Попробуйте еще раз.")
        except Exception:
            pass

# ================= ФОТО =================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        if user_id not in admins and user_id not in waiting_for_photo and await check_banned(update, context):
            return

        if user_id in waiting_for_photo:
            number = waiting_for_photo.pop(user_id)
            with data_lock:
                owner = active_numbers.get(number, {}).get("owner_id")

            if owner and update.message.photo:
                try:
                    photo = update.message.photo[-1]
                    await context.bot.send_photo(owner, photo.file_id, caption=f"📸 Фото по номеру {format_number(number)}")
                    await update.message.reply_text("✅ Фото отправлено!")
                except Exception as e:
                    logger.error(f"Ошибка отправки фото: {e}", exc_info=True)
                    await update.message.reply_text("❌ Не удалось отправить фото")
            return

        await update.message.reply_text("❓ Неизвестная команда. Используйте /start", reply_markup=get_main_keyboard(user_id))
    except Exception as e:
        logger.error(f"Ошибка в handle_photo: {e}", exc_info=True)

# ================= ГРУППА =================
async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.callback_query or not update.message or update.effective_chat.type not in ["group", "supergroup"]:
            return

        if not await check_allowed_group(update, context):
            return

        user_id = update.message.from_user.id
        user_name = update.message.from_user.first_name or "Пользователь"
        chat_id = update.effective_chat.id
        message_thread_id = update.message.message_thread_id

        if check_spam(user_id):
            logger.info(f"🚫 Спам от {user_name} ({user_id}) - игнорируем")
            return

        with data_lock:
            if user_id not in admins and user_id in banned_users:
                logger.info(f"🚫 Забаненный пользователь {user_name} ({user_id}) попытался написать в группе")
                return

        await get_user_info(context.bot, user_id)

        if user_id in waiting_for_message and update.message.text:
            number = waiting_for_message.pop(user_id)
            with data_lock:
                owner = active_numbers.get(number, {}).get("owner_id")

            if owner:
                try:
                    await safe_send_message(context.bot, owner, f"📝 Сообщение по номеру {format_number(number)}:\n\n{update.message.text}")
                    await update.message.reply_text("✅ Сообщение отправлено!")
                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения: {e}", exc_info=True)
                    await update.message.reply_text("❌ Не удалось отправить сообщение")
            else:
                await update.message.reply_text("❌ Не найден владелец номера")
            return

        if user_id in waiting_for_photo and update.message.photo:
            number = waiting_for_photo.pop(user_id)
            with data_lock:
                owner = active_numbers.get(number, {}).get("owner_id")

            if owner:
                try:
                    photo = update.message.photo[-1]
                    await context.bot.send_photo(owner, photo.file_id, caption=f"📸 Фото по номеру {format_number(number)}")
                    await update.message.reply_text("✅ Фото отправлено!")
                except Exception as e:
                    logger.error(f"Ошибка отправки фото: {e}", exc_info=True)
                    await update.message.reply_text("❌ Не удалось отправить фото")
            else:
                await update.message.reply_text("❌ Не найден владелец номера")
            return

        if update.message.text and "номер" in update.message.text.lower():
            if message_thread_id is None:
                logger.info("🚫 Запрос 'номер' в обычном чате - игнорируем")
                return

            topic_enabled = topic_settings.get(chat_id, {}).get(message_thread_id, {}).get("enabled", False)
            if not topic_enabled:
                logger.info(f"🚫 Запрос 'номер' в выключенном топике {message_thread_id} - игнорируем")
                return

            if not numbers_queue:
                await safe_send_message(context.bot, chat_id, "❌ Очередь номеров пуста!", message_thread_id=message_thread_id)
                return

            number_data = get_number_from_queue()
            if not number_data:
                await safe_send_message(context.bot, chat_id, "❌ Ошибка при получении номера!", message_thread_id=message_thread_id)
                return

            num = number_data["number"]
            tariff = number_data["tariff"]
            provider = number_data["provider"]

            safe_save_data(force=True)
            logger.info(f"📞 Номер {num} (тариф {tariff}) выдан пользователем {user_name} ({user_id}) в топике {message_thread_id}")

            tariff_display = get_tariff_display(tariff)
            tariff_emoji = tariff_stats.get(tariff, {}).get("emoji", "📱")

            try:
                await safe_send_message(
                    context.bot,
                    provider,
                    f"📞 Ваш номер {format_number(num)} взят в работу!\n\n{tariff_emoji} Тариф: {tariff_display}\n⏰ Время: {datetime.now().strftime('%H:%M:%S')}\n\nОжидайте фото от оператора."
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления владельца: {e}", exc_info=True)

            await safe_send_message(
                context.bot,
                chat_id,
                f"📞 Номер выдан!\n\n"
                f"📱 <code>+{num}</code>\n"
                f"💰 {tariff_display}\n\n"
                f"⏰ Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"Используйте кнопки ниже:",
                reply_markup=get_group_keyboard(num),
                message_thread_id=message_thread_id,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Ошибка в handle_group: {e}", exc_info=True)

# ================= CALLBACKS =================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global welcome_text
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    try:
        if await check_banned_callback(query):
            return

        logger.info(f"🔘 Callback от {user_id}: {data}")

        if data == "offices_list":
            await offices_list(update, context)
            return

        if data.startswith("deloffice_"):
            await delete_office_callback(update, context)
            return

        if data == "show_my_numbers":
            await query.answer()
            await query.message.edit_text(
                await get_user_numbers_text(user_id),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]])
            )
            return

        if data == "auto_report":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                await get_auto_report_text(),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
            )
            return

        if data.startswith("tariff_") and not any(data.startswith(p) for p in ["tariff_name_", "tariff_price_", "tariff_duration_", "tariff_currency_", "tariff_desc_", "tariff_toggle_"]):
            tariff = data.replace("tariff_", "")
            if not tariff_exists(tariff):
                await query.answer("❌ Тариф не найден!", show_alert=True)
                return
            if not tariff_stats[tariff].get("active", True):
                await query.answer("❌ Этот тариф временно недоступен!", show_alert=True)
                return
            waiting_for_number[user_id] = tariff
            await query.answer(f"✅ Выбран тариф: {get_tariff_display(tariff)}")
            await query.edit_message_text(
                f"{tariff_stats[tariff]['emoji']} ВЫБРАН ТАРИФ: {get_tariff_display(tariff)}\n\n📱 Теперь отправьте номер телефона в этот чат.\n\n✅ Форматы:\n• 89991234567\n• +79991234567\n• 9991234567\n\n❌ Отправьте /cancel для отмены"
            )
            return

        if data == "manage_tariffs":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            text = "💰 УПРАВЛЕНИЕ ТАРИФАМИ\n\nВыберите тариф для редактирования:\n\n"
            for tariff_id, tariff_data in tariff_stats.items():
                status = "✅" if tariff_data.get("active", True) else "❌"
                text += f"{status} {get_tariff_display(tariff_id)}\n"
            await query.message.edit_text(text, reply_markup=get_tariff_management_keyboard())
            return

        if data.startswith("edit_tariff_"):
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            tariff_id = data.replace("edit_tariff_", "")
            if not tariff_exists(tariff_id):
                await query.answer("❌ Тариф не найден!", show_alert=True)
                return
            tariff = tariff_stats[tariff_id]
            status = "Активен" if tariff.get("active", True) else "Неактивен"
            text = f"📝 РЕДАКТИРОВАНИЕ ТАРИФА\n\n{tariff['emoji']} {tariff['name']}\n💰 Цена: {tariff['price']}{tariff['currency']}\n⏱ Время: {tariff['duration']} {tariff['duration_unit']}\n📝 Описание: {tariff['description']}\n📌 Статус: {status}\n\nВыберите что хотите изменить:"
            await query.message.edit_text(text, reply_markup=get_tariff_edit_keyboard(tariff_id))
            return

        edit_fields = {
            "edit_name_": "name",
            "edit_price_": "price",
            "edit_duration_": "duration",
            "edit_currency_": "currency",
            "edit_desc_": "description"
        }

        for prefix, field in edit_fields.items():
            if data.startswith(prefix):
                if user_id not in admins:
                    await query.answer("⛔ Доступ запрещен!", show_alert=True)
                    return
                tariff_id = data.replace(prefix, "")
                if not tariff_exists(tariff_id):
                    await query.answer("❌ Тариф не найден!", show_alert=True)
                    return
                waiting_for_tariff_edit[user_id] = {"tariff_id": tariff_id, "field": field}
                await query.answer()
                current_value = tariff_stats[tariff_id].get(field, "")
                await query.message.edit_text(f"✏️ Введите новое значение для {field}\n\nТекущее: {current_value}\n\n❌ Отправьте /cancel для отмены")
                return

        if data.startswith("toggle_tariff_"):
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            tariff_id = data.replace("toggle_tariff_", "")
            if not tariff_exists(tariff_id):
                await query.answer("❌ Тариф не найден!", show_alert=True)
                return
            current = tariff_stats[tariff_id].get("active", True)
            tariff_stats[tariff_id]["active"] = not current
            safe_save_data(force=True)
            await query.answer(f"✅ Тариф {'активирован' if not current else 'деактивирован'}!")
            tariff = tariff_stats[tariff_id]
            new_status = "Активен" if tariff.get("active", True) else "Неактивен"
            text = f"📝 РЕДАКТИРОВАНИЕ ТАРИФА\n\n{tariff['emoji']} {tariff['name']}\n💰 Цена: {tariff['price']}{tariff['currency']}\n⏱ Время: {tariff['duration']} {tariff['duration_unit']}\n📝 Описание: {tariff['description']}\n📌 Статус: {new_status}\n\nВыберите что хотите изменить:"
            await query.edit_message_text(text, reply_markup=get_tariff_edit_keyboard(tariff_id))
            return

        if data == "edit_welcome":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            waiting_for_welcome_text.add(user_id)
            await query.answer()
            await query.message.edit_text(
                f"📝 РЕДАКТИРОВАНИЕ ТЕКСТА ПРИВЕТСТВИЯ\n\nОтправьте новый текст приветствия.\n\nВАЖНО:\n• Используйте {{user_name}} для вставки имени пользователя\n\n❌ Отправьте /cancel для отмены\n\nТекущий текст:\n{welcome_text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel"), InlineKeyboardButton("🔄 Сбросить", callback_data="reset_welcome")]
                ])
            )
            return

        if data == "reset_welcome":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            welcome_text = DEFAULT_WELCOME_TEXT
            safe_save_data(force=True)
            await query.answer("✅ Приветствие сброшено на стандартное!")
            await query.message.edit_text(
                f"👑 АДМИНИСТРАТИВНАЯ ПАНЕЛЬ\n\n👥 Всего пользователей: {len(all_users)}\n📊 Номеров в очереди: {len(numbers_queue)}\n✅ Активных номеров: {len(active_numbers)}\n⛔ Забанено: {len(banned_users)}\n🏢 Офисов: {len(allowed_groups)}\n\nВыберите действие:",
                reply_markup=get_admin_panel_keyboard(user_id)
            )
            return

        if data == "back_to_main":
            await query.answer()
            await query.message.edit_text("🌟 Главное меню\n\nВыберите действие:", reply_markup=get_main_keyboard(user_id))
            return

        if data == "give_number":
            await query.answer()
            active_tariffs = [t for t in tariff_stats.values() if t.get("active", True)]
            if not active_tariffs:
                await query.message.edit_text(
                    "❌ В данный момент нет доступных тарифов",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]])
                )
                return
            text = "💰 Выберите тариф для вашего номера:\n\n"
            for tariff_id, tariff_data in tariff_stats.items():
                if tariff_data.get("active", True):
                    text += f"{get_tariff_display(tariff_id)}\n"
                    if tariff_data["description"]:
                        text += f"📝 {tariff_data['description']}\n\n"
            await query.message.edit_text(text, reply_markup=get_tariff_keyboard())
            return

        if data == "show_my_queue":
            await query.answer()
            await query.message.edit_text(
                await get_queue_text_for_user(user_id),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_my_queue"), InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return

        if data == "refresh_my_queue":
            await query.answer()
            await query.message.edit_text(
                await get_queue_text_for_user(user_id),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_my_queue"), InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return

        if data == "admin_panel":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            admin_text = f"👑 АДМИНИСТРАТИВНАЯ ПАНЕЛЬ\n\n👥 Всего пользователей: {len(all_users)}\n📊 Номеров в очереди: {len(numbers_queue)}\n✅ Активных номеров: {len(active_numbers)}\n⛔ Забанено: {len(banned_users)}\n🏢 Офисов: {len(allowed_groups)}\n\nВыберите действие:"
            await query.message.edit_text(admin_text, reply_markup=get_admin_panel_keyboard(user_id))
            return

        if data == "broadcast":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await broadcast_message(update, context)
            return

        if data == "tariff_stats":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                await get_tariff_stats_text(),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="tariff_stats"), InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
            )
            return

        if data == "full_stats":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                await get_full_stats_text(),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="full_stats"), InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
            )
            return

        if data == "hold_management":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                "⏱ УПРАВЛЕНИЕ ОТСТОЕМ\n\nЗдесь вы можете:\n• Установить время отстоя\n• Посмотреть отчет по отстою\n• Добавить время к номеру\n• Посмотреть ошибки",
                reply_markup=get_hold_management_keyboard(user_id)
            )
            return

        if data == "set_hold_time":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            waiting_for_hold_time[user_id] = True
            current_time = hold_time_settings.get(user_id, DEFAULT_HOLD_TIME)
            await query.answer()
            await query.message.edit_text(
                f"⏱ УСТАНОВКА ВРЕМЕНИ ОТСТОЯ\n\nТекущее время: {current_time} минут\n\nВведите новое время в минутах\n\n❌ Отправьте /cancel для отмены",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="hold_management")]])
            )
            return

        if data == "hold_report":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                await get_hold_report(user_id),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="hold_report"), InlineKeyboardButton("🔙 Назад", callback_data="hold_management")]])
            )
            return

        if data == "error_report":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            await query.message.edit_text(
                await get_error_report(),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="error_report"), InlineKeyboardButton("🔙 Назад", callback_data="hold_management")]])
            )
            return

        if data == "add_hold_time":
            if user_id not in admins:
                await query.answer("⛔ Доступ запрещен!", show_alert=True)
                return
            await query.answer()
            if active_numbers:
                text = "➕ ДОБАВЛЕНИЕ ВРЕМЕНИ К НОМЕРУ\n\nАктивные номера:\n"
                keyboard = []
                for num in list(active_numbers.keys())[:10]:
                    formatted = format_number(num)
                    tariff = active_numbers[num].get("tariff", "unknown")
                    tariff_emoji = tariff_stats.get(tariff, {}).get("emoji", "📱")
                    owner = username_cache.get(active_numbers[num]["owner_id"], f"ID: {active_numbers[num]['owner_id']}")
                    text += f"• {tariff_emoji} {formatted} (👤 {owner})\n"
                    keyboard.append([InlineKeyboardButton(f"⏱ {tariff_emoji} {formatted}", callback_data=f"select_number_{num}")])
                text += "\nВыберите номер из списка:"
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="hold_management")])
                await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.message.edit_text("❌ Нет активных номеров", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="hold_management")]]))
            return

        if data.startswith("select_number_"):
            number = data.replace("select_number_", "")
            waiting_for_additional_time[user_id] = {"number": number}
            await query.answer()
            await query.message.edit_text(
                f"➕ ДОБАВЛЕНИЕ ВРЕМЕНИ\n\nНомер: {format_number(number)}\n\nВведите сколько минут добавить:\n\n❌ Отправьте /cancel для отмены",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="add_hold_time")]])
            )
            return

        if data == "show_current_hold_time":
            current_time = hold_time_settings.get(user_id, DEFAULT_HOLD_TIME)
            await query.answer(f"⏱ Текущее время отстоя: {current_time} минут", show_alert=True)
            return

        if user_id not in admins:
            await query.answer("⛔ Доступ запрещен!", show_alert=True)
            return

        if data == "admin_list":
            await query.answer()
            await query.message.edit_text(await get_admin_list_text(context.bot), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
            return

        if data == "admin_add":
            waiting_for_admin_action[user_id] = {"action": "add"}
            await query.answer()
            await query.message.edit_text(
                "➕ Добавление администратора\n\nОтправьте числовой ID пользователя:\n\n🔍 Как узнать ID:\n1️⃣ Попросите пользователя написать боту\n2️⃣ Или используйте @getmyid_bot\n\n❌ Отправьте /cancel для отмены",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]])
            )
            return

        if data == "admin_remove":
            waiting_for_admin_action[user_id] = {"action": "remove"}
            await query.answer()
            await query.message.edit_text(
                "➖ Удаление администратора\n\nОтправьте числовой ID пользователя:\n\n⚠️ Нельзя удалить главного админа\n\n❌ Отправьте /cancel для отмены",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]])
            )
            return

        if data == "show_queue_admin":
            await query.answer()
            await query.message.edit_text(await get_queue_text_for_admin(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
            return

        if data == "clear_queue":
            with data_lock:
                queue_size = len(numbers_queue)
                numbers_queue.clear()
                number_providers.clear()
                number_tariffs.clear()
            safe_save_data(force=True)
            await query.answer()
            await query.message.edit_text(f"✅ Очередь очищена!\n\nУдалено номеров: {queue_size}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
            return

        if data == "export_data":
            await query.answer()
            export_text = (
                f"📁 ЭКСПОРТ ДАННЫХ\n\n"
                f"📊 Общая статистика:\n"
                f"• Всего номеров: {len(number_status) + len(active_numbers)}\n"
                f"• В очереди: {len(numbers_queue)}\n"
                f"• Активных: {len(active_numbers)}\n"
                f"• Админов: {len(admins)}\n"
                f"• Пользователей: {len(all_users)}\n"
                f"• Забанено: {len(banned_users)}\n"
                f"• Офисов: {len(allowed_groups)}\n\n"
                f"💰 Статистика по тарифам:\n"
                + "\n".join([f"• {t['emoji']} {t['name']}: {t['count']}" for t in tariff_stats.values()])
                + "\n\n👑 Администраторы:\n"
                + "\n".join([f"{'👑 ' if aid == MAIN_ADMIN_ID else ''}ID: {aid} - {username_cache.get(aid, 'Неизвестно')}" for aid in sorted(admins)[:10]])
            )
            await query.message.edit_text(export_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
            return

        if data.startswith("g_"):
            num = data.replace("g_", "")
            hold_time = datetime.now()
            with data_lock:
                number_status[num] = {
                    "status": "встал",
                    "time": hold_time,
                    "tariff": active_numbers.get(num, {}).get("tariff"),
                    "owner_id": active_numbers.get(num, {}).get("owner_id"),
                    "hold_time": hold_time
                }
                if num in active_numbers:
                    numbers_hold_info[num] = {
                        "start_time": active_numbers[num]["issued_time"],
                        "hold_time": hold_time,
                        "status": "встал",
                        "tariff": active_numbers[num].get("tariff"),
                        "owner_id": active_numbers[num]["owner_id"]
                    }
                owner_id = active_numbers.get(num, {}).get("owner_id")
            safe_save_data(force=True)
            if owner_id:
                try:
                    await safe_send_message(context.bot, owner_id, f"✅ Ваш номер {format_number(num)} успешно встал!\n\n⏰ Время: {hold_time.strftime('%H:%M:%S')}")
                except Exception as e:
                    logger.error(f"Ошибка уведомления владельца: {e}", exc_info=True)
            await query.answer()
            await query.edit_message_text(text=query.message.text + f"\n✅ Встал ({hold_time.strftime('%H:%M:%S')})", reply_markup=query.message.reply_markup)
            return

        if data.startswith("f_"):
            num = data.replace("f_", "")
            end_time = datetime.now()
            with data_lock:
                hold_time = number_status.get(num, {}).get("hold_time")
                number_status[num] = {
                    "status": "слет",
                    "time": end_time,
                    "tariff": active_numbers.get(num, {}).get("tariff"),
                    "owner_id": active_numbers.get(num, {}).get("owner_id"),
                    "hold_time": hold_time,
                    "end_time": end_time
                }
                if num in active_numbers:
                    issued_time = active_numbers[num]["issued_time"]
                    minutes = (end_time - issued_time).total_seconds() / 60 if isinstance(issued_time, datetime) else 0
                    numbers_hold_info[num] = {
                        "start_time": issued_time,
                        "hold_time": hold_time,
                        "end_time": end_time,
                        "hold_duration": minutes,
                        "status": "слет",
                        "tariff": active_numbers[num].get("tariff"),
                        "owner_id": active_numbers[num]["owner_id"]
                    }
                owner_id = active_numbers.get(num, {}).get("owner_id")
                active_numbers.pop(num, None)
                active_numbers_set.discard(num)
            safe_save_data(force=True)
            if owner_id:
                try:
                    await safe_send_message(context.bot, owner_id, f"❌ Ваш номер {format_number(num)} слетел!\n\n⏰ Время: {end_time.strftime('%H:%M:%S')}")
                except Exception as e:
                    logger.error(f"Ошибка уведомления владельца: {e}", exc_info=True)
            await query.answer()
            await query.edit_message_text(text=query.message.text + f"\n❌ Слетел ({end_time.strftime('%H:%M:%S')})", reply_markup=query.message.reply_markup)
            return

        if data.startswith("e_"):
            num = data.replace("e_", "")
            end_time = datetime.now()
            with data_lock:
                number_status[num] = {
                    "status": "ошибка",
                    "time": end_time,
                    "tariff": active_numbers.get(num, {}).get("tariff"),
                    "owner_id": active_numbers.get(num, {}).get("owner_id")
                }
                if num in active_numbers:
                    numbers_hold_info[num] = {
                        "start_time": active_numbers[num]["issued_time"],
                        "end_time": end_time,
                        "status": "ошибка",
                        "tariff": active_numbers[num].get("tariff"),
                        "owner_id": active_numbers[num]["owner_id"]
                    }
                owner_id = active_numbers.get(num, {}).get("owner_id")
                active_numbers.pop(num, None)
                active_numbers_set.discard(num)
            safe_save_data(force=True)
            if owner_id:
                try:
                    await safe_send_message(context.bot, owner_id, f"⚠️ По номеру {format_number(num)} произошла ошибка!\n\n⏰ Время: {end_time.strftime('%H:%M:%S')}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}", exc_info=True)
            await query.answer()
            await query.edit_message_text(text=query.message.text + f"\n⚠️ Ошибка ({end_time.strftime('%H:%M:%S')})", reply_markup=query.message.reply_markup)
            return

        if data.startswith("m_"):
            num = data.replace("m_", "")
            waiting_for_message[user_id] = num
            await query.answer()
            await query.message.reply_text(f"📝 Напишите сообщение для владельца номера {format_number(num)}")
            return

        if data.startswith("p_"):
            num = data.replace("p_", "")
            waiting_for_photo[user_id] = num
            await query.answer()
            await query.message.reply_text(f"📸 Отправьте фото для владельца номера {format_number(num)}")
            return

    except Exception as e:
        logger.error(f"💥 Ошибка в callbacks: {e}", exc_info=True)
        try:
            await query.answer("❌ Произошла ошибка", show_alert=True)
        except Exception:
            pass

# ================= ПЕРИОДИКА =================
async def cleanup_numbers(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    to_delete = []

    with data_lock:
        for num, info in active_numbers.items():
            issued = info.get("issued_time")
            if isinstance(issued, datetime) and (now - issued).total_seconds() > 3600:
                to_delete.append(num)

        for num in to_delete:
            active_numbers.pop(num, None)
            active_numbers_set.discard(num)
            logger.info(f"🧹 Очищен старый номер {num}")

    if to_delete:
        safe_save_data(force=True)

async def periodic_save(context: ContextTypes.DEFAULT_TYPE):
    safe_save_data()

# ================= ERROR HANDLER =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"❌ Ошибка: {context.error}", exc_info=True)

# ================= POST INIT =================
async def post_init(app: Application):
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("submit", "📱 Сдать номер"),
        BotCommand("queue", "📋 Моя очередь"),
        BotCommand("numbers", "📊 Мои номера"),
        BotCommand("support", "🛠 Тех поддержка"),
        BotCommand("addoffice", "🏢 Добавить офис"),
        BotCommand("removeoffice", "🏢 Удалить офис"),
        BotCommand("ban", "🔨 Забанить"),
        BotCommand("unban", "🔓 Разбанить"),
        BotCommand("banlist", "📋 Список банов"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("✅ Команды установлены")
    except Exception as e:
        logger.error(f"❌ Ошибка установки команд: {e}")

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(periodic_save, interval=SAVE_INTERVAL, first=10)
        job_queue.run_repeating(cleanup_numbers, interval=CLEANUP_INTERVAL, first=60)
        logger.info("✅ Периодические задачи запущены")

# ================= AUTO REPORT =================
async def get_auto_report_text() -> str:
    with data_lock:
        report_lines = []
        user_totals = {}
        tariff_prices = {"kz_wc_fbx": 3, "kz_wc_bh": 8}
        all_records = []

        for num, info in active_numbers.items():
            issued = info.get("issued_time")
            tariff = info.get("tariff")
            owner = info.get("owner_id")
            tariff_name = "бх" if tariff == "kz_wc_bh" else "фбх"
            all_records.append({
                "number": num, "tariff": tariff_name, "status": "+",
                "start": issued, "end": None, "owner": owner,
                "price": tariff_prices.get(tariff, 0)
            })

        for num, info in number_status.items():
            start = info.get("hold_time")
            end = info.get("end_time") or info.get("time")
            tariff = info.get("tariff")
            owner = info.get("owner_id")
            tariff_name = "бх" if tariff == "kz_wc_bh" else "фбх"
            status = info.get("status")
            status_symbol = "+" if status == "встал" else "-"
            all_records.append({
                "number": num, "tariff": tariff_name, "status": status_symbol,
                "start": start, "end": end, "owner": owner,
                "price": tariff_prices.get(tariff, 0), "status_text": status
            })

        for record in all_records:
            owner = record["owner"]
            if owner not in user_totals:
                user_totals[owner] = {"total": 0, "count": 0, "numbers": []}

            duration = ""
            if record["start"] and isinstance(record["start"], datetime):
                start_time = record["start"].strftime("%H:%M")
                if record["end"] and isinstance(record["end"], datetime):
                    minutes = int((record["end"] - record["start"]).total_seconds() / 60)
                    duration = f", отстоял {minutes} мин"
                    end_time = record["end"].strftime("%H:%M")
                else:
                    end_time = "сейчас"
                    minutes = int((datetime.now() - record["start"]).total_seconds() / 60)
                    duration = f", отстоял {minutes} мин"

                line = f"{record['number']} {record['status']}{record['tariff']} (встал {start_time}, слетел {end_time}{duration})"
            else:
                line = f"{record['number']} {record['status']}{record['tariff']}"

            report_lines.append(line)

            if record["status"] == "+" and record["start"] and not record.get("status_text") == "слет":
                user_totals[owner]["count"] += 1
                user_totals[owner]["numbers"].append(record["number"])

        for owner, data in user_totals.items():
            total_payout = data["count"] * 3
            owner_name = username_cache.get(owner, f"ID:{owner}")
            report_lines.append(f"\n@{owner_name} общая выплата ${total_payout}")

        return "\n".join(report_lines) if report_lines else "Нет данных для отчета"

# ================= MAIN =================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_NEW_TOKEN_HERE":
        raise ValueError("Вставь новый BOT_TOKEN")

    print("🚀 Запуск бота...")
    load_data()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("submit", submit))
    app.add_handler(CommandHandler("queue", queue))
    app.add_handler(CommandHandler("numbers", numbers_command))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("set", set_command))
    app.add_handler(CommandHandler("addoffice", addoffice))
    app.add_handler(CommandHandler("removeoffice", removeoffice))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("banlist", banlist))

    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.PHOTO), handle_group))

    app.add_error_handler(error_handler)

    print("""
╔═══════════════════════════════════════════════╗
║           🤖 БОТ ЗАПУЩЕН!                     ║
║                                               ║
║  ✅ Автосохранение каждые 5 минут             ║
║  ✅ Очистка старых номеров каждый час         ║
║  ✅ Анти-спам 3 секунды                       ║
║  ✅ Таймауты увеличены до 120 секунд          ║
╚═══════════════════════════════════════════════╝""")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
        safe_save_data(force=True)
    except Exception as e:
        print(f"💥 Ошибка: {e}")
        import traceback
        traceback.print_exc()
