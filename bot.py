import os
import json
import logging
import asyncio
import aiohttp
from datetime import date, datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"
DATA_FILE = "data.json"

WAITING_BALANCE = "waiting_balance"
WAITING_DATE = "waiting_date"
IDLE = "idle"


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(uid):
    return load_data().get(str(uid), {})

def set_user(uid, info):
    data = load_data()
    data[str(uid)] = info
    save_data(data)


def calc_daily(balance, end_date_str):
    end = datetime.strptime(end_date_str, "%d.%m.%Y").date()
    today = date.today()
    days = (end - today).days + 1
    if days <= 0:
        return 0, 0
    return round(balance / days, 2), days


async def tg(session, method, **kwargs):
    async with session.post(f"{API}/{method}", json=kwargs) as r:
        return await r.json()

async def send(session, chat_id, text, keyboard=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        params["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    await tg(session, "sendMessage", **params)

def main_kb():
    return [
        [{"text": "📊 Мой бюджет"}],
        [{"text": "✏️ Обновить баланс"}, {"text": "📅 Изменить дату"}]
    ]


async def handle_message(session, message):
    chat_id = message["chat"]["id"]
    uid = str(chat_id)
    text = message.get("text", "").strip()

    user = get_user(uid)
    state = user.get("state", IDLE)

    if text == "/start":
        set_user(uid, {"state": WAITING_BALANCE})
        await send(session, chat_id,
            "👋 Привет! Я помогу следить за бюджетом.\n\nВведи текущий баланс (число):")
        return

    if text == "✏️ Обновить баланс":
        user["state"] = WAITING_BALANCE
        set_user(uid, user)
        await send(session, chat_id, "Введи новый баланс:")
        return

    if text == "📅 Изменить дату":
        if "balance" not in user:
            await send(session, chat_id, "Сначала введи баланс через /start")
            return
        user["state"] = WAITING_DATE
        set_user(uid, user)
        await send(session, chat_id, "Введи новую дату (ДД.ММ.ГГГГ):")
        return

    if text == "📊 Мой бюджет":
        if "balance" not in user or "end_date" not in user:
            await send(session, chat_id, "У тебя нет данных. Напиши /start чтобы начать.", keyboard=main_kb())
            return
        daily, days = calc_daily(user["balance"], user["end_date"])
        if days <= 0:
            await send(session, chat_id, "⏰ Период закончился! Обнови баланс и дату.", keyboard=main_kb())
        else:
            await send(session, chat_id,
                f"📊 *Твой бюджет*\n\n"
                f"💰 Остаток: {user['balance']:,.2f} ₽\n"
                f"📅 До: {user['end_date']} ({days} дн.)\n"
                f"📆 В день: *{daily:,.2f} ₽*",
                keyboard=main_kb())
        return

    if state == WAITING_BALANCE:
        try:
            balance = float(text.replace(",", ".").replace(" ", ""))
            if balance < 0:
                raise ValueError
        except ValueError:
            await send(session, chat_id, "❌ Введи корректное число, например: 15000")
            return
        user["balance"] = balance
        user["state"] = WAITING_DATE
        set_user(uid, user)
        await send(session, chat_id,
            f"✅ Баланс: {balance:,.2f} ₽\n\nТеперь введи дату до которой нужно дожить.\nФормат: ДД.ММ.ГГГГ, например: 31.03.2025")
        return

    if state == WAITING_DATE:
        try:
            end = datetime.strptime(text, "%d.%m.%Y").date()
            if end < date.today():
                await send(session, chat_id, "❌ Дата уже прошла. Введи будущую дату:")
                return
        except ValueError:
            await send(session, chat_id, "❌ Неверный формат. Введи дату в виде ДД.ММ.ГГГГ:")
            return
        user["end_date"] = text
        user["state"] = IDLE
        set_user(uid, user)
        daily, days = calc_daily(user["balance"], text)
        await send(session, chat_id,
            f"🎉 Всё готово!\n\n"
            f"💰 Баланс: {user['balance']:,.2f} ₽\n"
            f"📅 До: {text} ({days} дн.)\n"
            f"📆 Можно тратить в день: *{daily:,.2f} ₽*",
            keyboard=main_kb())
        return

    await send(session, chat_id, "Используй кнопки ниже 👇", keyboard=main_kb())


async def polling():
    offset = 0
    async with aiohttp.ClientSession() as session:
        logger.info("Бот запущен!")
        while True:
            try:
                result = await tg(session, "getUpdates", offset=offset, timeout=30)
                updates = result.get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    if "message" in upd:
                        await handle_message(session, upd["message"])
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(polling())
