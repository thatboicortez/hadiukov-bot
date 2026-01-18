import uuid
from datetime import datetime
from urllib.parse import urlencode, quote_plus

from dateutil.relativedelta import relativedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
import asyncio

from config import (
    BOT_TOKEN,
    PRODUCT_NAME,
    PRICES,
    PERIOD_TEXT,
    PERIOD_MONTHS,
    TALLY_FORM_URL,
)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ---------- helpers ----------

def amount_from_key(key: str) -> int:
    return int(PRICES[key])


def period_from_key(key: str) -> str:
    return PERIOD_TEXT[key]


def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    # Можно менять utcnow() на now() если хочешь локальное время
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")


def build_tally_url(params: dict) -> str:
    """
    Важно:
    Telegram WebApp добавляет в URL фрагмент #tgWebAppData=...
    В некоторых сервисах (в т.ч. у тебя в Tally) этот хвост может "прилипать"
    к последнему query-параметру (например expires_at).

    Решение: добавляем последний параметр _tail=1, чтобы #... прилипал к нему,
    а expires_at оставался чистым.
    """
    params = dict(params)
    params["_tail"] = "1"  # <- специально последним

    query = urlencode(params, quote_via=quote_plus)
    return f"{TALLY_FORM_URL}?{query}"


# ---------- keyboards ----------

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Мои продукты", callback_data="products")],
    ])


def periods_kb() -> InlineKeyboardMarkup:
    # делаем кнопки строго из PERIOD_TEXT + PRICES, без хардкода
    rows = []
    for key in ["1m", "3m"]:
        text = f"{PERIOD_TEXT[key]} — {PRICES[key]} USDT"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"period:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pay_kb(period_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Crypto (USDT TRC20)", callback_data=f"pay:crypto:{period_key}")],
        [InlineKeyboardButton(text="🏦 Monobank", callback_data=f"pay:mono:{period_key}")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_periods")],
    ])


def webapp_kb(tally_url: str) -> InlineKeyboardMarkup:
    # ВАЖНО: именно WebAppInfo -> откроется как мини-приложение внутри Telegram
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Подтвердить оплату", web_app=WebAppInfo(url=tally_url))]
    ])


# ---------- handlers ----------

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Выбери раздел 👇",
        reply_markup=main_menu_kb(),
    )


@dp.callback_query(F.data == "products")
async def products(cb: CallbackQuery):
    await cb.message.edit_text(
        f"📦 {PRODUCT_NAME}\n\nВыбери период подписки:",
        reply_markup=periods_kb(),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("period:"))
async def choose_period(cb: CallbackQuery):
    period_key = cb.data.split(":")[1]

    period_text = period_from_key(period_key)
    amount = amount_from_key(period_key)

    await cb.message.edit_text(
        f"Период: {period_text}\n"
        f"Сумма: {amount} USDT\n\n"
        f"Выбери способ оплаты:",
        reply_markup=pay_kb(period_key),
    )
    await cb.answer()


@dp.callback_query(F.data == "back_periods")
async def back(cb: CallbackQuery):
    await products(cb)


@dp.callback_query(F.data.startswith("pay:"))
async def pay(cb: CallbackQuery):
    _, method, period_key = cb.data.split(":")

    amount = amount_from_key(period_key)
    period_text = period_from_key(period_key)
    expires_at = expires_from_key(period_key)

    order_id = str(uuid.uuid4())
    pay_method = "Crypto USDT TRC20" if method == "crypto" else "Monobank"

    params = {
        "order_id": order_id,
        "tg_id": str(cb.from_user.id),
        "tg_username": cb.from_user.username or "",
        "product": PRODUCT_NAME,
        "period": period_text,      # 1 month / 3 months
        "period_key": period_key,   # 1m / 3m
        "pay_method": pay_method,
        "amount_usdt": str(amount), # 50 / 120
        "expires_at": expires_at,   # YYYY-MM-DD (чистый)
    }

    tally_url = build_tally_url(params)

    await cb.message.edit_text(
        "Оплата → подтверждение → доступ выдаётся вручную после проверки.\n\n"
        "Нажми кнопку ниже и заполни форму:",
        reply_markup=webapp_kb(tally_url),
    )
    await cb.answer()


# ---------- run ----------

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())