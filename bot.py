import uuid
from datetime import datetime
from urllib.parse import urlencode, quote_plus

from dateutil.relativedelta import relativedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
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

# ----------------- constants (resources) -----------------

YOUTUBE_URL = "https://youtube.com/@hadiukov?si=vy9gXXiLKeDYIfR_"
INSTAGRAM_URL = "https://www.instagram.com/hadiukov?igsh=MTdtZmp4MmtxdzF2dw=="
TELEGRAM_URL = "https://t.me/hadiukov"

RESOURCES_IMAGE_PATH = "pictures/resources.png"

# ----------------- bot init -----------------

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ---------- helpers ----------

def amount_from_key(key: str) -> int:
    return int(PRICES[key])

def period_from_key(key: str) -> str:
    return PERIOD_TEXT[key]

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")

def build_tally_url(params: dict) -> str:
    params = dict(params)
    params["_tail"] = "1"  # чтобы #tgWebAppData не прилипал к expires_at
    query = urlencode(params, quote_via=quote_plus)
    return f"{TALLY_FORM_URL}?{query}"

# ---------- keyboards ----------

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="ℹ️ Информация"),
                KeyboardButton(text="❓ Помощь"),
            ],
            [
                KeyboardButton(text="📦 Мои продукты"),
                KeyboardButton(text="🌐 Мои ресурсы"),
            ],
            [
                KeyboardButton(text="👤 Личный кабинет"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        selective=False,
    )

def back_to_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="В главное меню")]],
        resize_keyboard=True,
        is_persistent=True,
        selective=False,
    )

def periods_kb() -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Подтвердить оплату", web_app=WebAppInfo(url=tally_url))]
    ])

def resources_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="YouTube", url=YOUTUBE_URL)],
        [
            InlineKeyboardButton(text="INST: hadiukov", url=INSTAGRAM_URL),
            InlineKeyboardButton(text="TG: hadiukov", url=TELEGRAM_URL),
        ],
    ])

# ---------- UI helpers ----------

WELCOME_TEXT = (
    "Вас приветствует Sever by Hadiukov!\n\n"
    "Сейчас вы находитесь в официальном боте проекта.\n"
    "Здесь вы можете оформить или продлить подписку и отправить подтверждение оплаты.\n\n"
    "Выберите нужный раздел в меню снизу 👇\n"
    "Если возникнут вопросы — напишите администратору @name."
)

async def show_main_menu(message: Message, text: str = WELCOME_TEXT):
    await message.answer(text, reply_markup=main_menu_kb())

async def show_products(message: Message):
    await message.answer(
        f"📦 {PRODUCT_NAME}\n\nВыбери период подписки:",
        reply_markup=periods_kb(),
    )

# ---------- handlers ----------

@dp.message(CommandStart())
async def start(message: Message):
    await show_main_menu(message)

@dp.message(Command("menu"))
async def menu(message: Message):
    await show_main_menu(message, "Главное меню 👇")

# Нажатия на нижние кнопки (ReplyKeyboard) приходят как обычный текст

@dp.message(F.text == "📦 Мои продукты")
async def products_from_menu(message: Message):
    await show_products(message)

@dp.message(F.text == "ℹ️ Информация")
async def info_from_menu(message: Message):
    await message.answer("ℹ️ Раздел «Информация» пока в разработке.")

@dp.message(F.text == "❓ Помощь")
async def help_from_menu(message: Message):
    await message.answer("❓ Раздел «Помощь» пока в разработке.")

@dp.message(F.text == "👤 Личный кабинет")
async def cabinet_from_menu(message: Message):
    await message.answer("👤 Раздел «Личный кабинет» пока в разработке.")

@dp.message(F.text == "🌐 Мои ресурсы")
async def resources_from_menu(message: Message):
    # 1) Картинка + подпись + inline кнопки
    photo = FSInputFile(RESOURCES_IMAGE_PATH)
    await message.answer_photo(
        photo=photo,
        caption="Подписывайтесь ⬇️⬇️⬇️",
        reply_markup=resources_links_kb(),
    )

    # 2) Снизу оставляем только одну кнопку
    await message.answer(
        "Чтобы вернуться, нажмите «В главное меню».",
        reply_markup=back_to_menu_kb(),
    )

@dp.message(F.text == "В главное меню")
async def back_to_main_menu(message: Message):
    await message.answer("Главное меню", reply_markup=main_menu_kb())

# --- дальше твоя существующая логика inline-кнопок для продукта ---

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
    await cb.message.edit_text(
        f"📦 {PRODUCT_NAME}\n\nВыбери период подписки:",
        reply_markup=periods_kb(),
    )
    await cb.answer()

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
        "period": period_text,
        "period_key": period_key,
        "pay_method": pay_method,
        "amount_usdt": str(amount),
        "expires_at": expires_at,
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