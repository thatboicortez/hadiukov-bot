import uuid
import asyncio
from datetime import datetime, date
from urllib.parse import urlencode, quote_plus
from typing import Optional, Tuple

import aiohttp
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

from config import BOT_TOKEN, TALLY_FORM_URL, NOTION_TOKEN, NOTION_DATABASE_ID

# =========================
# CONFIG / CONSTANTS
# =========================

ADMIN_USERNAME = "@name"  # поменяешь потом

YOUTUBE_URL = "https://youtube.com/@hadiukov?si=vy9gXXiLKeDYIfR_"
INSTAGRAM_URL = "https://www.instagram.com/hadiukov?igsh=MTdtZmp4MmtxdzF2dw=="
TELEGRAM_URL = "https://t.me/hadiukov"

RESOURCES_IMAGE_PATH = "pictures/resources.png"
PRODUCTS_IMAGE_PATH = "pictures/products.png"
PAYMENT_IMAGE_PATH = "pictures/payment.png"
SUBSCRIPTION_IMAGE_PATH = "pictures/subscription.png"

USDT_TRC20_ADDRESS = "TAzH2VDmTZnmAjgwDUUVDDFGntpWk7a5kQ"

COMMUNITY_USDT_1M = 50
COMMUNITY_USDT_3M = 120
COMMUNITY_UAH_1M = 2200
COMMUNITY_UAH_3M = 5200

MENTORING_USDT = 3000
MENTORING_UAH = 130000

PERIOD_TEXT = {"1m": "1 month", "3m": "3 months"}
PERIOD_MONTHS = {"1m": 1, "3m": 3}

# Notion constants
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# =========================
# BOT INIT
# =========================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# =========================
# HELPERS
# =========================

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")

def build_tally_url(params: dict) -> str:
    params = dict(params)
    params["_tail"] = "1"
    query = urlencode(params, quote_via=quote_plus)
    return f"{TALLY_FORM_URL}?{query}"

async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except Exception:
        await message.answer(caption or " ", reply_markup=reply_markup)

def tally_confirm_kb(tally_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])

def cabinet_refresh_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="cabinet:refresh")]
    ])

def _format_ddmmyyyy(yyyy_mm_dd: str) -> str:
    try:
        dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return yyyy_mm_dd or "Не указано"

def _is_active(expires_at: str) -> bool:
    try:
        dt = datetime.strptime(expires_at, "%Y-%m-%d").date()
        return dt >= date.today()
    except Exception:
        return False

# =========================
# NOTION (READ ONLY)
# =========================

async def notion_query_latest_approved_by_tg_id(tg_id: str) -> Optional[dict]:
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    payload = {
        "filter": {
            "and": [
                {"property": "tg_id", "rich_text": {"equals": str(tg_id)}},
                {"property": "status", "status": {"equals": "approved"}},
            ]
        },
        "sorts": [
            {"property": "created_at", "direction": "descending"},
        ],
        "page_size": 1,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Notion query error {resp.status}: {data}")
            results = data.get("results", [])
            return results[0] if results else None

def _notion_get_plain_text(prop: dict) -> str:
    if not prop:
        return ""
    if prop.get("type") == "title":
        arr = prop.get("title", [])
        return "".join([x.get("plain_text", "") for x in arr]).strip()
    if prop.get("type") == "rich_text":
        arr = prop.get("rich_text", [])
        return "".join([x.get("plain_text", "") for x in arr]).strip()
    if prop.get("type") == "email":
        return (prop.get("email") or "").strip()
    if prop.get("type") == "status":
        st = prop.get("status") or {}
        return (st.get("name") or "").strip()
    return ""

def _extract_user_profile_from_page(page: dict) -> Tuple[str, str, str]:
    props = (page or {}).get("properties", {}) or {}
    discord = _notion_get_plain_text(props.get("discord", {}))
    email = _notion_get_plain_text(props.get("email", {}))
    expires_at = _notion_get_plain_text(props.get("expires_at", {}))
    return discord, email, expires_at

async def build_cabinet_text(user_id: int) -> str:
    page = await notion_query_latest_approved_by_tg_id(str(user_id))
    if not page:
        return (
            "👤 <b>Личный кабинет</b>\n\n"
            "Discord: <b>Не указан</b>\n"
            "Email: <b>Не указан</b>\n\n"
            "Hadiukov Community — <b>нет активной подписки</b>\n"
        )

    discord, email, expires_at = _extract_user_profile_from_page(page)
    discord_out = discord if discord else "Не указан"
    email_out = email if email else "Не указан"

    if expires_at and _is_active(expires_at):
        sub_line = f"Hadiukov Community — до <b>{_format_ddmmyyyy(expires_at)}</b>"
    else:
        sub_line = "Hadiukov Community — <b>нет активной подписки</b>"

    return (
        "👤 <b>Личный кабинет</b>\n\n"
        f"Discord: <b>{discord_out}</b>\n"
        f"Email: <b>{email_out}</b>\n\n"
        f"{sub_line}\n"
    )

# =========================
# PAYMENT FLOW (Tally URL)
# =========================

async def send_payment_flow_final(
    message: Message,
    *,
    user_id: int,
    user_username: Optional[str],
    product: str,
    pay_method: str,
    currency: str,
    amount: int,
    period_key: str = "",
    period_text: str = "",
    expires_at: str = "",
):
    order_id = str(uuid.uuid4())
    tg_username = (user_username or "").lstrip("@")

    params = {
        "order_id": order_id,
        "tg_id": str(user_id),
        "tg_username": tg_username,
        "product": product,
        "period": period_text,
        "period_key": period_key,
        "pay_method": pay_method,
        "expires_at": expires_at,
        "amount_usdt": str(amount) if currency == "USDT" else "",
        "amount_uah": str(amount) if currency == "UAH" else "",
    }

    tally_url = build_tally_url(params)
    kb = tally_confirm_kb(tally_url)

    if currency == "USDT":
        await message.answer(f"Для оплаты Вам необходимо перевести {amount} USDT:")
        await message.answer(
            f"<code>{USDT_TRC20_ADDRESS}</code> (USDT. Сеть TRC20)",
            reply_markup=kb,
        )
    else:
        await message.answer(f"Для оплаты Вам необходимо перевести {amount} грн на указанные реквизиты:")
        await message.answer("Скоро добавим карту.", reply_markup=kb)

# =========================
# KEYBOARDS
# =========================

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ℹ️ Информация"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="📦 Мои продукты"), KeyboardButton(text="🌐 Мои ресурсы")],
            [KeyboardButton(text="👤 Личный кабинет")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def resources_back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="В главное меню")]],
        resize_keyboard=True,
        is_persistent=True,
    )

def products_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Hadiukov Community")],
            [KeyboardButton(text="Hadiukov Mentoring")],
            [KeyboardButton(text="В главное меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def resources_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="YouTube", url=YOUTUBE_URL)],
        [
            InlineKeyboardButton(text="INST: hadiukov", url=INSTAGRAM_URL),
            InlineKeyboardButton(text="TG: hadiukov", url=TELEGRAM_URL),
        ],
    ])

def kb_community_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить подписку", callback_data="buy:community")]
    ])

def kb_mentoring_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Приобрести", callback_data="buy:mentoring")]
    ])

def kb_payment_methods(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Crypto (USDT)", callback_data=f"pm:{product_key}:crypto"),
            InlineKeyboardButton(text="Fiat (UAH)", callback_data=f"pm:{product_key}:fiat"),
        ]
    ])

def kb_community_crypto_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц – 50 USDT", callback_data="sub:community:crypto:1m")],
        [InlineKeyboardButton(text="3 месяца – 120 USDT", callback_data="sub:community:crypto:3m")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def kb_community_fiat_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц – 2200 UAH", callback_data="sub:community:fiat:1m")],
        [InlineKeyboardButton(text="3 месяца – 5200 UAH", callback_data="sub:community:fiat:3m")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def kb_mentoring_crypto() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3000 USDT", callback_data="sub:mentoring:crypto:once")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

def kb_mentoring_fiat() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="130000 UAH", callback_data="sub:mentoring:fiat:once")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close")],
    ])

# =========================
# TEXTS
# =========================

WELCOME_TEXT = (
    "Вас приветствует Sever by Hadiukov!\n\n"
    "Сейчас вы находитесь в официальном боте проекта.\n"
    "Здесь вы можете оформить или продлить подписку и отправить подтверждение оплаты.\n\n"
    "Выберите нужный раздел в меню снизу 👇\n"
    f"Если возникнут вопросы — напишите администратору {ADMIN_USERNAME}."
)

# =========================
# HANDLERS
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("menu"))
async def menu(message: Message):
    await message.answer("Главное меню 👇", reply_markup=main_menu_kb())

@dp.message(lambda m: (m.text or "") == "В главное меню")
async def back_to_main_menu(message: Message):
    await message.answer("Главное меню", reply_markup=main_menu_kb())

@dp.message(lambda m: "Информация" in (m.text or ""))
async def info_from_menu(message: Message):
    await message.answer("ℹ️ Раздел «Информация» пока в разработке.", reply_markup=main_menu_kb())

@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    await message.answer("❓ Раздел «Помощь» пока в разработке.", reply_markup=main_menu_kb())

@dp.message(lambda m: "Мои ресурсы" in (m.text or ""))
async def resources_from_menu(message: Message):
    await send_photo_safe(message, RESOURCES_IMAGE_PATH, caption="Подписывайтесь ⬇️⬇️⬇️", reply_markup=resources_links_kb())
    await message.answer("Чтобы вернуться, нажмите «В главное меню».", reply_markup=resources_back_kb())

@dp.message(lambda m: "Мои продукты" in (m.text or ""))
async def products_entry(message: Message):
    await send_photo_safe(message, PRODUCTS_IMAGE_PATH, caption=None)
    await message.answer("Выберите:", reply_markup=products_menu_kb())

@dp.message(F.text == "Hadiukov Community")
async def community_info(message: Message):
    await message.answer("Объяснение внутрянки сервера", reply_markup=kb_community_buy())

@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    await message.answer("Объяснение того что будет на менторке", reply_markup=kb_mentoring_buy())

@dp.callback_query(F.data == "buy:community")
async def buy_community(cb: CallbackQuery):
    await cb.message.delete()
    await send_photo_safe(cb.message, PAYMENT_IMAGE_PATH, caption="Выберите способ оплаты", reply_markup=kb_payment_methods("community"))
    await cb.answer()

@dp.callback_query(F.data == "buy:mentoring")
async def buy_mentoring(cb: CallbackQuery):
    await cb.message.delete()
    await send_photo_safe(cb.message, PAYMENT_IMAGE_PATH, caption="Выберите способ оплаты", reply_markup=kb_payment_methods("mentoring"))
    await cb.answer()

@dp.callback_query(F.data.startswith("pm:"))
async def payment_method_choice(cb: CallbackQuery):
    _, product_key, method = cb.data.split(":")

    if product_key == "community" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, caption="Выберите срок подписки", reply_markup=kb_community_crypto_periods())
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, caption="Выберите срок подписки", reply_markup=kb_community_fiat_periods())
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, caption="Выберите срок подписки", reply_markup=kb_mentoring_crypto())
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, caption="Выберите срок подписки", reply_markup=kb_mentoring_fiat())

    await cb.answer()

@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()

@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    user_id = cb.from_user.id
    user_username = cb.from_user.username  # важно: это username человека, а не бота

    if product_key == "community":
        product_name = "Hadiukov Community"

        period_key = choice if choice in ("1m", "3m") else ""
        period_text = PERIOD_TEXT.get(period_key, "")
        expires_at = expires_from_key(period_key) if period_key else ""

        if method == "crypto":
            amount = COMMUNITY_USDT_1M if choice == "1m" else COMMUNITY_USDT_3M
            await send_payment_flow_final(
                cb.message,
                user_id=user_id,
                user_username=user_username,
                product=product_name,
                pay_method="Crypto (USDT)",
                currency="USDT",
                amount=amount,
                period_key=period_key,
                period_text=period_text,
                expires_at=expires_at,
            )
        else:
            amount = COMMUNITY_UAH_1M if choice == "1m" else COMMUNITY_UAH_3M
            await send_payment_flow_final(
                cb.message,
                user_id=user_id,
                user_username=user_username,
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
        if method == "crypto":
            await send_payment_flow_final(
                cb.message,
                user_id=user_id,
                user_username=user_username,
                product=product_name,
                pay_method="Crypto (USDT)",
                currency="USDT",
                amount=MENTORING_USDT,
                period_key="mentoring",
                period_text="Mentoring",
                expires_at="",
            )
        else:
            await send_payment_flow_final(
                cb.message,
                user_id=user_id,
                user_username=user_username,
                product=product_name,
                pay_method="Fiat (UAH)",
                currency="UAH",
                amount=MENTORING_UAH,
                period_key="mentoring",
                period_text="Mentoring",
                expires_at="",
            )

    await cb.answer()

# =========================
# CABINET
# =========================

@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    try:
        text = await build_cabinet_text(message.from_user.id)
        # 1) сообщение с кабинетом + нижние плитки (ReplyKeyboard)
        await message.answer(text, reply_markup=main_menu_kb())
        # 2) отдельное сообщение с inline-кнопкой "Обновить"
        await message.answer(" ", reply_markup=cabinet_refresh_kb())
    except Exception as e:
        await message.answer(f"Ошибка кабинета: {e}", reply_markup=main_menu_kb())

@dp.callback_query(F.data == "cabinet:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    try:
        text = await build_cabinet_text(cb.from_user.id)
        await cb.message.answer(text, reply_markup=cabinet_refresh_kb())
        await cb.answer("Обновлено")
    except Exception as e:
        await cb.message.answer(f"Ошибка кабинета: {e}")
        await cb.answer()

# =========================
# RUN
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
