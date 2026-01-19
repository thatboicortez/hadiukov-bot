import uuid
import asyncio
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

from config import (
    BOT_TOKEN,
    TALLY_FORM_URL,
)

# ----------------- settings / constants -----------------

# Админ (позже заменишь @name в тексте приветствия)
ADMIN_USERNAME = "@name"

# Links (ресурсы)
YOUTUBE_URL = "https://youtube.com/@hadiukov?si=vy9gXXiLKeDYIfR_"
INSTAGRAM_URL = "https://www.instagram.com/hadiukov?igsh=MTdtZmp4MmtxdzF2dw=="
TELEGRAM_URL = "https://t.me/hadiukov"

# Images (пути в репо)
RESOURCES_IMAGE_PATH = "pictures/resources.png"
PRODUCTS_IMAGE_PATH = "pictures/products.png"       # если нет — код не упадёт, просто без фото
PAYMENT_IMAGE_PATH = "pictures/payment.png"         # нужно добавить файл
SUBSCRIPTION_IMAGE_PATH = "pictures/subscription.png"  # нужно добавить файл

# Wallet (Crypto)
USDT_TRC20_ADDRESS = "TAzH2VDmTZnmAjgwDUUVDDFGntpWk7a5kQ"

# Prices
COMMUNITY_USDT = {
    "1m": 50,
    "3m": 120,
}
COMMUNITY_UAH = {
    "1m": 2200,
    "3m": 5200,
}
MENTORING_USDT = 3000
MENTORING_UAH = 130000

# Period text
PERIOD_TEXT = {
    "1m": "1 month",
    "3m": "3 months",
}
PERIOD_MONTHS = {
    "1m": 1,
    "3m": 3,
}

# ----------------- bot init -----------------

# HTML нужен, чтобы делать monospace через <code>...</code>
bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# ----------------- helpers -----------------

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")

def build_tally_url(params: dict) -> str:
    params = dict(params)
    params["_tail"] = "1"  # чтобы #tgWebAppData не прилипал к последнему параметру
    query = urlencode(params, quote_via=quote_plus)
    return f"{TALLY_FORM_URL}?{query}"

async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
    """
    Пытаемся отправить фото из репо. Если файла нет — просто отправляем текст, чтобы бот не падал.
    """
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except Exception:
        # fallback
        text = caption if caption else ""
        await message.answer(text or " ", reply_markup=reply_markup)

async def send_payment_instructions(
    message: Message,
    *,
    product: str,
    pay_method: str,
    currency: str,
    amount: int,
    period_key: str | None,
    period_text: str | None,
    expires_at: str | None,
):
    """
    Финальные сообщения:
    - 1) "Для оплаты ... перевести N ..."
    - 2) адрес/карта + inline кнопка на Tally mini app
    """
    order_id = str(uuid.uuid4())
    tg_id = str(message.from_user.id)
    tg_username = message.from_user.username or ""

    # общие поля (не мешают даже если в форме некоторых нет — просто игнорируются)
    params = {
        "order_id": order_id,
        "tg_id": tg_id,
        "tg_username": tg_username,
        "product": product,
        "pay_method": pay_method,
        "currency": currency,
        "amount": str(amount),
        "period_key": period_key or "",
        "period": period_text or "",
        "expires_at": expires_at or "",
    }

    # для совместимости с твоими hidden fields
    if currency == "USDT":
        params["amount_usdt"] = str(amount)
    else:
        params["amount_uah"] = str(amount)

    tally_url = build_tally_url(params)
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])

    if currency == "USDT":
        await message.answer(f"Для оплаты Вам необходимо перевести {amount} USDT:")
        # monospace ТОЛЬКО адрес, остальное обычным
        await message.answer(
            f"<code>{USDT_TRC20_ADDRESS}</code> (USDT. Сеть TRC20)",
            reply_markup=confirm_kb,
        )
    else:
        await message.answer(f"Для оплаты Вам необходимо перевести {amount} грн на указанные реквизиты:")
        await message.answer("Скоро добавим карту.", reply_markup=confirm_kb)

# ----------------- keyboards -----------------

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

def resources_back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="В главное меню")]],
        resize_keyboard=True,
        is_persistent=True,
        selective=False,
    )

def products_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Hadiukov Mentoring")],
            [KeyboardButton(text="Hadiukov Community")],
            [KeyboardButton(text="В главное меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        selective=False,
    )

def resources_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="YouTube", url=YOUTUBE_URL)],
        [
            InlineKeyboardButton(text="INST: hadiukov", url=INSTAGRAM_URL),
            InlineKeyboardButton(text="TG: hadiukov", url=TELEGRAM_URL),
        ],
    ])

def community_buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить подписку", callback_data="buy:community")]
    ])

def mentoring_buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Приобрести", callback_data="buy:mentoring")]
    ])

def payment_methods_kb(product_key: str) -> InlineKeyboardMarkup:
    # product_key: community | mentoring
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Crypto (USDT)", callback_data=f"pm:{product_key}:crypto"),
            InlineKeyboardButton(text="Fiat (UAH)", callback_data=f"pm:{product_key}:fiat"),
        ]
    ])

def close_kb(cbdata: str = "close") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть", callback_data=cbdata)]
    ])

def community_crypto_periods_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц – 50 USDT", callback_data="sub:community:crypto:1m")],
        [InlineKeyboardButton(text="3 месяца – 120 USDT", callback_data="sub:community:crypto:3m")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def community_fiat_periods_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц – 2200 UAH", callback_data="sub:community:fiat:1m")],
        [InlineKeyboardButton(text="3 месяца – 5200 UAH", callback_data="sub:community:fiat:3m")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def mentoring_crypto_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3000 USDT", callback_data="sub:mentoring:crypto:once")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def mentoring_fiat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="130000 UAH", callback_data="sub:mentoring:fiat:once")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

# ----------------- UI texts -----------------

WELCOME_TEXT = (
    "Вас приветствует Sever by Hadiukov!\n\n"
    "Сейчас вы находитесь в официальном боте проекта.\n"
    "Здесь вы можете оформить или продлить подписку и отправить подтверждение оплаты.\n\n"
    "Выберите нужный раздел в меню снизу 👇\n"
    f"Если возникнут вопросы — напишите администратору {ADMIN_USERNAME}."
)

# ----------------- handlers -----------------

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("menu"))
async def menu(message: Message):
    await message.answer("Главное меню 👇", reply_markup=main_menu_kb())

@dp.message(F.text == "В главное меню")
async def back_to_main_menu(message: Message):
    await message.answer("Главное меню", reply_markup=main_menu_kb())

# ----- Main menu sections -----

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
    await send_photo_safe(
        message,
        RESOURCES_IMAGE_PATH,
        caption="Подписывайтесь ⬇️⬇️⬇️",
        reply_markup=resources_links_kb(),
    )
    await message.answer(
        "Чтобы вернуться, нажмите «В главное меню».",
        reply_markup=resources_back_kb(),
    )

# ----- Products flow -----

@dp.message(F.text == "📦 Мои продукты")
async def products_entry(message: Message):
    # 1) картинка
    await send_photo_safe(message, PRODUCTS_IMAGE_PATH, caption=None)
    # 2) отдельное сообщение + нижние плитки выбора продукта
    await message.answer("Выберите:", reply_markup=products_menu_kb())

@dp.message(F.text == "Hadiukov Community")
async def community_info(message: Message):
    await message.answer(
        "Объяснение внутрянки сервера",
        reply_markup=community_buy_kb(),
    )

@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    await message.answer(
        "Объяснение того что будет на менторке",
        reply_markup=mentoring_buy_kb(),
    )

# ----- Inline: buy/acquire -> payment methods -----

@dp.callback_query(F.data == "buy:community")
async def buy_community(cb: CallbackQuery):
    # удаляем сообщение с объяснением (то, под которым была кнопка)
    await cb.message.delete()

    # показываем payment methods (картинка + текст)
    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=payment_methods_kb("community"),
    )
    await cb.answer()

@dp.callback_query(F.data == "buy:mentoring")
async def buy_mentoring(cb: CallbackQuery):
    await cb.message.delete()

    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=payment_methods_kb("mentoring"),
    )
    await cb.answer()

# ----- Inline: payment method -> subscription choices -----

@dp.callback_query(F.data.startswith("pm:"))
async def payment_method_choice(cb: CallbackQuery):
    # pm:{product}:{crypto|fiat}
    _, product_key, method = cb.data.split(":")

    if product_key == "community" and method == "crypto":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=community_crypto_periods_kb(),
        )
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=community_fiat_periods_kb(),
        )
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=mentoring_crypto_kb(),
        )
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=mentoring_fiat_kb(),
        )

    await cb.answer()

# ----- Inline: close -> delete current message -----

@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()

# ----- Inline: subscription chosen -> send instructions + tally -----

@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    # sub:{product}:{crypto|fiat}:{1m|3m|once}
    _, product_key, method, choice = cb.data.split(":")

    if product_key == "community":
        product_name = "Hadiukov Community"
        if choice in ("1m", "3m"):
            period_key = choice
            period_text = PERIOD_TEXT[period_key]
            expires_at = expires_from_key(period_key)
        else:
            period_key = choice
            period_text = choice
            expires_at = ""

        if method == "crypto":
            amount = COMMUNITY_USDT[choice]
            await send_payment_instructions(
                cb.message,
                product=product_name,
                pay_method="Crypto (USDT)",
                currency="USDT",
                amount=amount,
                period_key=period_key,
                period_text=period_text,
                expires_at=expires_at,
            )
        else:
            amount = COMMUNITY_UAH[choice]
            await send_payment_instructions(
                cb.message,
                product=product_name,
                pay_method="Fiat (UAH)",
                currency="UAH",
                amount=amount,
                period_key=period_key,
                period_text=period_text,
                expires_at=expires_at,
            )

    elif product_key == "mentoring":
        product_name = "Hadiukov Mentoring"
        period_key = "mentoring"
        period_text = "Mentoring"
        expires_at = ""  # не нужен

        if method == "crypto":
            await send_payment_instructions(
                cb.message,
                product=product_name,
                pay_method="Crypto (USDT)",
                currency="USDT",
                amount=MENTORING_USDT,
                period_key=period_key,
                period_text=period_text,
                expires_at=expires_at,
            )
        else:
            await send_payment_instructions(
                cb.message,
                product=product_name,
                pay_method="Fiat (UAH)",
                currency="UAH",
                amount=MENTORING_UAH,
                period_key=period_key,
                period_text=period_text,
                expires_at=expires_at,
            )

    await cb.answer()

# ----------------- run -----------------

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())