import os
import json
import logging
from datetime import date, datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")

# Состояния диалога
WAITING_BALANCE = 1
WAITING_DATE = 2

# Хранилище данных (простой JSON-файл)
DATA_FILE = "data.json"


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_data(user_id: str):
    data = load_data()
    return data.get(user_id)


def set_user_data(user_id: str, user_info: dict):
    data = load_data()
    data[user_id] = user_info
    save_data(data)


def calc_daily_budget(balance: float, end_date: date) -> tuple[float, int]:
    today = date.today()
    days = (end_date - today).days + 1  # включаем сегодня
    if days <= 0:
        return 0, 0
    daily = balance / days
    return round(daily, 2), days


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📊 Мой бюджет")],
         [KeyboardButton("✏️ Обновить баланс"), KeyboardButton("📅 Изменить дату")]],
        resize_keyboard=True
    )


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе следить за бюджетом.\n\n"
        "Ты вводишь сумму и дату, до которой нужно дожить — "
        "я посчитаю, сколько можно тратить каждый день.\n\n"
        "Давай начнём! Введи свой текущий баланс (число):",
        reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True)
    )
    return WAITING_BALANCE


# Получаем баланс
async def get_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(",", ".").replace(" ", "")
    try:
        balance = float(text)
        if balance < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи корректное число, например: 15000 или 15000.50")
        return WAITING_BALANCE

    context.user_data["balance"] = balance
    await update.message.reply_text(
        f"✅ Баланс: {balance:,.2f} ₽\n\n"
        "Теперь введи дату, до которой нужно дожить.\n"
        "Формат: ДД.ММ.ГГГГ, например: 31.03.2025"
    )
    return WAITING_DATE


# Получаем дату
async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        end_date = datetime.strptime(text, "%d.%m.%Y").date()
        if end_date < date.today():
            await update.message.reply_text("❌ Дата уже прошла. Введи будущую дату:")
            return WAITING_DATE
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введи дату в виде ДД.ММ.ГГГГ:")
        return WAITING_DATE

    balance = context.user_data["balance"]
    daily, days = calc_daily_budget(balance, end_date)

    user_id = str(update.effective_user.id)
    set_user_data(user_id, {
        "balance": balance,
        "end_date": text,
        "set_date": date.today().strftime("%d.%m.%Y")
    })

    await update.message.reply_text(
        f"🎉 Всё готово!\n\n"
        f"💰 Баланс: {balance:,.2f} ₽\n"
        f"📅 До: {text} ({days} дн.)\n"
        f"📆 Можно тратить в день: *{daily:,.2f} ₽*",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END


# Показать текущий бюджет
async def show_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    info = get_user_data(user_id)

    if not info:
        await update.message.reply_text(
            "У тебя ещё нет данных. Напиши /start чтобы начать.",
            reply_markup=main_keyboard()
        )
        return

    balance = info["balance"]
    end_date = datetime.strptime(info["end_date"], "%d.%m.%Y").date()
    daily, days = calc_daily_budget(balance, end_date)

    if days <= 0:
        await update.message.reply_text(
            f"⏰ Период закончился! Обнови баланс и дату.",
            reply_markup=main_keyboard()
        )
        return

    await update.message.reply_text(
        f"📊 *Твой бюджет*\n\n"
        f"💰 Остаток: {balance:,.2f} ₽\n"
        f"📅 До: {info['end_date']} ({days} дн.)\n"
        f"📆 В день: *{daily:,.2f} ₽*",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# Обновить баланс — запускаем диалог заново
async def update_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введи новый баланс:",
        reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True)
    )
    return WAITING_BALANCE


# Изменить только дату
async def update_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    info = get_user_data(user_id)
    if not info:
        await update.message.reply_text("Сначала введи баланс через /start")
        return ConversationHandler.END

    context.user_data["balance"] = info["balance"]
    await update.message.reply_text(
        "Введи новую дату (ДД.ММ.ГГГГ):",
        reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True)
    )
    return WAITING_DATE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=main_keyboard())
    return ConversationHandler.END


def main():
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^✏️ Обновить баланс$"), update_balance),
            MessageHandler(filters.Regex("^📅 Изменить дату$"), update_date),
        ],
        states={
            WAITING_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_balance)],
            WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой бюджет$"), show_budget))

    logger.info("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
