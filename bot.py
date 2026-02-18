import os
import json
import logging
import asyncio
import aiohttp
from datetime import date, datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"
DATA_FILE = "data.json"

WAITING_BALANCE = "waiting_balance"
WAITING_DATE = "waiting_date"
WAITING_EXPENSE = "waiting_expense"
WAITING_REMINDER = "waiting_reminder"
IDLE = "idle"


# ── Хранилище ──────────────────────────────────────────────
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

def get_all_users():
    return load_data()


# ── Расчёт ─────────────────────────────────────────────────
def calc_daily(balance, end_date_str):
    end = datetime.strptime(end_date_str, "%d.%m.%Y").date()
    today = date.today()
    days = (end - today).days + 1
    if days <= 0:
        return 0, 0
    return round(balance / days, 2), days

def today_str():
    return date.today().strftime("%d.%m.%Y")

def spent_today(user):
    expenses = user.get("expenses", [])
    today = today_str()
    return sum(e["amount"] for e in expenses if e["date"] == today)

def spent_week(user):
    expenses = user.get("expenses", [])
    week_ago = (date.today() - timedelta(days=6)).strftime("%d.%m.%Y")
    # простое сравнение строк не работает для дат — конвертируем
    total = 0
    for e in expenses:
        try:
            edate = datetime.strptime(e["date"], "%d.%m.%Y").date()
            if edate >= date.today() - timedelta(days=6):
                total += e["amount"]
        except Exception:
            pass
    return total


# ── Telegram API ───────────────────────────────────────────
async def tg(session, method, **kwargs):
    async with session.post(f"{API}/{method}", json=kwargs) as r:
        return await r.json()

async def send(session, chat_id, text, keyboard=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard is not None:
        params["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    await tg(session, "sendMessage", **params)

def main_kb():
    return [
        [{"text": "📊 Мой бюджет"}, {"text": "💸 Трата"}],
        [{"text": "📋 История"}, {"text": "⏰ Напоминание"}],
        [{"text": "✏️ Обновить баланс"}, {"text": "📅 Изменить дату"}],
    ]


# ── Обработка сообщений ────────────────────────────────────
async def handle_message(session, message):
    chat_id = message["chat"]["id"]
    uid = str(chat_id)
    text = message.get("text", "").strip()

    user = get_user(uid)
    state = user.get("state", IDLE)

    # /start
    if text == "/start":
        set_user(uid, {"state": WAITING_BALANCE})
        await send(session, chat_id,
            "👋 Привет! Я помогу следить за бюджетом.\n\nВведи текущий баланс (число):")
        return

    # ── Кнопки меню ────────────────────────────────────────

    # Обработка выбора после поздравления за экономию
    if text == "🎉 Потратить сегодня":
        if "balance" not in user or "end_date" not in user:
            await send(session, chat_id, "Нет данных.", keyboard=main_kb())
            return
        bonus = user.pop("saved_bonus", 0)
        if bonus <= 0:
            await send(session, chat_id, "Бонус уже использован.", keyboard=main_kb())
            return
        user["today_bonus"] = round(user.get("today_bonus", 0) + bonus, 2)
        set_user(uid, user)
        daily, _ = calc_daily(user["balance"], user["end_date"])
        await send(session, chat_id,
            f"🥳 Окей! Сегодня можно потратить: *{daily + user['today_bonus']:,.0f} ₽*\n"
            f"_(базовый лимит {daily:,.0f} ₽ + бонус {bonus:,.0f} ₽)_",
            keyboard=main_kb())
        return

    if text == "📅 Распределить на все дни":
        if "balance" not in user or "end_date" not in user:
            await send(session, chat_id, "Нет данных.", keyboard=main_kb())
            return
        user.pop("saved_bonus", None)
        set_user(uid, user)
        daily, days = calc_daily(user["balance"], user["end_date"])
        await send(session, chat_id,
            f"👍 Сумма распределена на оставшиеся дни.\n"
            f"📆 Новый лимит в день: *{daily:,.2f} ₽*",
            keyboard=main_kb())
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

    if text == "💸 Трата":
        if "balance" not in user:
            await send(session, chat_id, "Сначала настрой бюджет через /start")
            return
        user["state"] = WAITING_EXPENSE
        set_user(uid, user)
        await send(session, chat_id,
            "Введи сумму траты (число):\n"
            "_Можно добавить описание: `500 кофе и обед`_")
        return

    if text == "⏰ Напоминание":
        user["state"] = WAITING_REMINDER
        set_user(uid, user)
        await send(session, chat_id,
            "В какое время присылать ежедневное напоминание?\n"
            "Введи время в формате ЧЧ:ММ, например: `09:00`\n\n"
            "Чтобы отключить напоминание — напиши `отключить`")
        return

    if text == "📋 История":
        if not user.get("expenses"):
            await send(session, chat_id, "Трат пока нет 🤷‍♀️", keyboard=main_kb())
            return

        today_total = spent_today(user)
        week_total = spent_week(user)

        # Последние 10 трат
        last = user["expenses"][-10:][::-1]
        lines = []
        for e in last:
            desc = f" — {e['desc']}" if e.get("desc") else ""
            lines.append(f"`{e['date']}` {e['amount']:,.0f} ₽{desc}")

        daily, _ = calc_daily(user["balance"], user["end_date"]) if "end_date" in user else (0, 0)
        over = today_total - daily if daily > 0 else 0
        over_str = f"\n⚠️ Перерасход сегодня: *{over:,.0f} ₽*" if over > 0 else ""

        msg = (
            f"📋 *История трат*\n\n"
            f"Сегодня: *{today_total:,.0f} ₽*{over_str}\n"
            f"За 7 дней: *{week_total:,.0f} ₽*\n\n"
            f"*Последние траты:*\n" + "\n".join(lines)
        )
        await send(session, chat_id, msg, keyboard=main_kb())
        return

    if text == "📊 Мой бюджет":
        if "balance" not in user or "end_date" not in user:
            await send(session, chat_id,
                "У тебя нет данных. Напиши /start чтобы начать.", keyboard=main_kb())
            return
        daily, days = calc_daily(user["balance"], user["end_date"])
        today_total = spent_today(user)

        if days <= 0:
            await send(session, chat_id,
                "⏰ Период закончился! Обнови баланс и дату.", keyboard=main_kb())
            return

        today_bonus = user.get("today_bonus", 0)
        effective_daily = daily + today_bonus
        remaining_today = effective_daily - today_total
        if remaining_today < 0:
            status = f"⚠️ *Перерасход на {abs(remaining_today):,.0f} ₽*"
        elif remaining_today == 0:
            status = "✅ Лимит исчерпан на сегодня"
        else:
            status = f"✅ Осталось на сегодня: *{remaining_today:,.0f} ₽*"

        msg = (
            f"📊 *Твой бюджет*\n\n"
            f"💰 Баланс: {user['balance']:,.2f} ₽\n"
            f"📅 До: {user['end_date']} ({days} дн.)\n"
            f"📆 Лимит в день: *{effective_daily:,.2f} ₽*" + (f" _(+{today_bonus:,.0f} ₽ бонус)_" if today_bonus else "") + "\n"
            f"💸 Потрачено сегодня: {today_total:,.0f} ₽\n"
            f"{status}"
        )
        await send(session, chat_id, msg, keyboard=main_kb())
        return

    # ── Ввод данных по состояниям ───────────────────────────

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
            f"✅ Баланс: {balance:,.2f} ₽\n\n"
            "Теперь введи дату до которой нужно дожить.\n"
            "Формат: ДД.ММ.ГГГГ, например: 31.03.2025")
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

    if state == WAITING_EXPENSE:
        parts = text.split(None, 1)
        try:
            amount = float(parts[0].replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await send(session, chat_id, "❌ Введи корректную сумму, например: `500` или `500 обед`")
            return

        desc = parts[1] if len(parts) > 1 else ""
        expense = {"date": today_str(), "amount": amount, "desc": desc}

        if "expenses" not in user:
            user["expenses"] = []
        user["expenses"].append(expense)
        user["balance"] = round(user["balance"] - amount, 2)
        user["state"] = IDLE
        set_user(uid, user)

        daily, days = calc_daily(user["balance"], user["end_date"]) if "end_date" in user else (0, 0)
        today_total = spent_today(user)
        remaining = daily - today_total

        if remaining < 0:
            tip = f"⚠️ Перерасход на *{abs(remaining):,.0f} ₽*! Завтра придётся экономить."
        else:
            tip = f"✅ Ещё можно потратить сегодня: *{remaining:,.0f} ₽*"

        desc_str = f" ({desc})" if desc else ""
        await send(session, chat_id,
            f"💸 Записала: *{amount:,.0f} ₽*{desc_str}\n"
            f"💰 Остаток: {user['balance']:,.2f} ₽\n\n{tip}",
            keyboard=main_kb())
        return

    if state == WAITING_REMINDER:
        if text.lower() == "отключить":
            user["reminder"] = None
            user["state"] = IDLE
            set_user(uid, user)
            await send(session, chat_id, "🔕 Напоминание отключено.", keyboard=main_kb())
            return
        try:
            datetime.strptime(text, "%H:%M")
        except ValueError:
            await send(session, chat_id, "❌ Неверный формат. Введи время как `09:00`:")
            return
        user["reminder"] = text
        user["state"] = IDLE
        set_user(uid, user)
        await send(session, chat_id,
            f"⏰ Буду напоминать каждый день в *{text}*!", keyboard=main_kb())
        return

    # Умное распознавание: число = трата, +число = доход
    if state == IDLE and "balance" in user and "end_date" in user:
        parts = text.split(None, 1)
        raw = parts[0].replace(",", ".")
        is_income = raw.startswith("+")
        raw_num = raw.lstrip("+")
        try:
            amount = float(raw_num)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await send(session, chat_id, "Используй кнопки ниже 👇", keyboard=main_kb())
            return

        desc = parts[1] if len(parts) > 1 else ""
        desc_str = f" ({desc})" if desc else ""

        if is_income:
            user["balance"] = round(user["balance"] + amount, 2)
            set_user(uid, user)
            daily, days = calc_daily(user["balance"], user["end_date"])
            await send(session, chat_id,
                f"💚 Доход: *+{amount:,.0f} ₽*{desc_str}\n"
                f"💰 Новый баланс: *{user['balance']:,.2f} ₽*\n"
                f"📆 Новый лимит в день: *{daily:,.2f} ₽*",
                keyboard=main_kb())
        else:
            if "expenses" not in user:
                user["expenses"] = []
            user["expenses"].append({"date": today_str(), "amount": amount, "desc": desc})
            user["balance"] = round(user["balance"] - amount, 2)
            set_user(uid, user)
            daily, days = calc_daily(user["balance"], user["end_date"])
            today_total = spent_today(user)
            remaining = daily - today_total
            if remaining < 0:
                tip = f"⚠️ Перерасход на *{abs(remaining):,.0f} ₽*! Завтра придётся экономить."
            else:
                tip = f"✅ Ещё можно потратить сегодня: *{remaining:,.0f} ₽*"
            await send(session, chat_id,
                f"💸 Трата: *{amount:,.0f} ₽*{desc_str}\n"
                f"💰 Остаток: *{user['balance']:,.2f} ₽*\n\n{tip}",
                keyboard=main_kb())
        return

    await send(session, chat_id, "Используй кнопки ниже 👇", keyboard=main_kb())


# ── Напоминания ────────────────────────────────────────────
async def check_savings(session):
    """Проверяем в начале нового дня — сэкономил ли пользователь вчера"""
    all_users = get_all_users()
    yesterday = (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")
    
    for uid, user in all_users.items():
        if "balance" not in user or "end_date" not in user:
            continue
        if user.get("savings_checked") == yesterday:
            continue  # уже проверяли

        # Считаем сколько потратили вчера
        expenses = user.get("expenses", [])
        spent_yesterday = sum(e["amount"] for e in expenses if e["date"] == yesterday)
        
        # Считаем каким был лимит вчера (упрощённо: текущий баланс + вчерашние траты)
        balance_yesterday = user["balance"] + spent_yesterday
        _, days_left = calc_daily(user["balance"], user["end_date"])
        days_yesterday = days_left + 1
        if days_yesterday <= 0:
            continue
        
        # Лимит на вчера
        try:
            end = datetime.strptime(user["end_date"], "%d.%m.%Y").date()
            days_yesterday_count = (end - date.today()).days + 2
            if days_yesterday_count <= 0:
                continue
            daily_yesterday = round(balance_yesterday / days_yesterday_count, 2)
        except Exception:
            continue

        saved = round(daily_yesterday - spent_yesterday, 2)
        
        # Сбрасываем бонус прошлого дня
        user.pop("today_bonus", None)
        user["savings_checked"] = yesterday
        
        if saved > 0:
            user["saved_bonus"] = saved
            set_user(uid, user)
            savings_kb = [
                [{"text": "🎉 Потратить сегодня"}],
                [{"text": "📅 Распределить на все дни"}]
            ]
            daily_new, days_new = calc_daily(user["balance"], user["end_date"])
            await send(session, int(uid),
                f"🌟 *Отличная работа вчера!*\n\n"
                f"Ты сэкономила *{saved:,.0f} ₽* — это просто супер! 💪\n\n"
                f"Что делаем с этой суммой?\n"
                f"• *Потратить сегодня* — дневной лимит вырастет до *{daily_new + saved:,.0f} ₽*\n"
                f"• *Распределить* — лимит каждого из {days_new} дней станет *{daily_new:,.2f} ₽*",
                keyboard=savings_kb)
        else:
            set_user(uid, user)


async def reminder_loop(session):
    sent_today = set()  # uid -> time чтобы не слать дважды
    last_date = date.today()

    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # Сброс в полночь
        if date.today() != last_date:
            sent_today.clear()
            last_date = date.today()
            await check_savings(session)

        all_users = get_all_users()
        for uid, user in all_users.items():
            reminder = user.get("reminder")
            if not reminder:
                continue
            key = f"{uid}_{current_time}"
            if reminder == current_time and key not in sent_today:
                sent_today.add(key)
                if "balance" in user and "end_date" in user:
                    daily, days = calc_daily(user["balance"], user["end_date"])
                    today_total = spent_today(user)
                    remaining = daily - today_total
                    if days <= 0:
                        msg = "⏰ Период бюджета закончился! Не забудь обновить данные."
                    elif remaining < 0:
                        msg = (f"⏰ *Напоминание*\n\n"
                               f"⚠️ Вчера был перерасход на *{abs(remaining):,.0f} ₽*\n"
                               f"📆 Лимит на сегодня: *{daily:,.0f} ₽*")
                    else:
                        msg = (f"⏰ *Напоминание*\n\n"
                               f"📆 Лимит на сегодня: *{daily:,.0f} ₽*\n"
                               f"💰 Баланс: {user['balance']:,.0f} ₽")
                    try:
                        await send(session, int(uid), msg)
                    except Exception as e:
                        logger.error(f"Ошибка напоминания для {uid}: {e}")

        await asyncio.sleep(30)


# ── Polling ────────────────────────────────────────────────
async def polling():
    offset = 0
    async with aiohttp.ClientSession() as session:
        logger.info("Бот запущен!")
        asyncio.create_task(reminder_loop(session))
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
