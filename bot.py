import uuid
import asyncio
from datetime import datetime, date
from urllib.parse import urlencode, quote_plus

import httpx
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

from config import BOT_TOKEN, TALLY_FORM_URL, NOTION_TOKEN, NOTION_DATABASE_ID, ADMIN_USERNAME

# =========================
# CONFIG / CONSTANTS
# =========================

# Resources links
YOUTUBE_URL = "https://youtube.com/@hadiukov?si=vy9gXXiLKeDYIfR_"
INSTAGRAM_URL = "https://www.instagram.com/hadiukov?igsh=MTdtZmp4MmtxdzF2dw=="
TELEGRAM_URL = "https://t.me/hadiukov"

# Images (пути в репо)
RESOURCES_IMAGE_PATH = "pictures/resources.png"
PRODUCTS_IMAGE_PATH = "pictures/products.png"
PAYMENT_IMAGE_PATH = "pictures/payment.png"
SUBSCRIPTION_IMAGE_PATH = "pictures/subscription.png"

# Wallet
USDT_TRC20_ADDRESS = "TAzH2VDmTZnmAjgwDUUVDDFGntpWk7a5kQ"

# Prices
COMMUNITY_USDT_1M = 50
COMMUNITY_USDT_3M = 120
COMMUNITY_UAH_1M = 2200
COMMUNITY_UAH_3M = 5200

MENTORING_USDT = 3000
MENTORING_UAH = 130000

PERIOD_TEXT = {"1m": "1 month", "3m": "3 months"}
PERIOD_MONTHS = {"1m": 1, "3m": 3}

# =========================
# BOT INIT
# =========================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# =========================
# HELPERS (general)
# =========================

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")

def build_tally_url(params: dict) -> str:
    params = dict(params)
    params["_tail"] = "1"  # чтобы tgWebAppData не прилипал к последнему параметру
    query = urlencode(params, quote_via=quote_plus)
    return f"{TALLY_FORM_URL}?{query}"

async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
    """
    Пытаемся отправить локальную картинку.
    Если файла нет/ошибка — отправим просто текст, чтобы бот не падал.
    """
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except Exception:
        await message.answer(caption or " ", reply_markup=reply_markup)

def tally_confirm_kb(tally_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])

def safe_get_rich_text(props: dict, key: str) -> str:
    """
    Поддержка Text properties в Notion: { "rich_text": [...] }
    """
    try:
        p = props.get(key, {})
        rt = p.get("rich_text", [])
        if not rt:
            return ""
        return "".join([x.get("plain_text", "") for x in rt]).strip()
    except Exception:
        return ""

def safe_get_status(props: dict, key: str) -> str:
    """
    Notion Status property: { "status": { "name": "pending" } }
    """
    try:
        p = props.get(key, {})
        st = p.get("status")
        if not st:
            return ""
        return (st.get("name") or "").strip()
    except Exception:
        return ""

def format_date_ddmmyyyy(iso_yyyy_mm_dd: str) -> str:
    """
    '2026-01-21' -> '21.01.2026'
    """
    try:
        d = datetime.strptime(iso_yyyy_mm_dd.strip(), "%Y-%m-%d").date()
        return d.strftime("%d.%m.%Y")
    except Exception:
        return iso_yyyy_mm_dd.strip()

def is_future_or_today(iso_yyyy_mm_dd: str) -> bool:
    try:
        d = datetime.strptime(iso_yyyy_mm_dd.strip(), "%Y-%m-%d").date()
        return d >= date.today()
    except Exception:
        return False

# =========================
# NOTION API
# =========================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

async def notion_query_by_tg_id(tg_id: str, limit: int = 20) -> list[dict]:
    """
    Берём записи пользователя по tg_id, сортируем по created_time (самое новое сверху).
    """
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    payload = {
        "filter": {
            "property": "tg_id",
            "rich_text": {"equals": tg_id},
        },
        "sorts": [
            {"timestamp": "created_time", "direction": "descending"}
        ],
        "page_size": limit,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Notion query error {r.status_code}: {r.text}")
        data = r.json()
        return data.get("results", [])

def pick_latest_records(records: list[dict]) -> dict:
    """
    Из всех записей пользователя берём:
    - latest_approved_community: последняя approved для community (period_key 1m/3m)
    - has_pending_any: есть ли pending (любая)
    """
    latest_approved_community = None
    has_pending_any = False

    for page in records:
        props = page.get("properties", {})
        status = safe_get_status(props, "status").lower()
        period_key = safe_get_rich_text(props, "period_key").lower()

        if status == "pending":
            has_pending_any = True

        # mentoring не показываем
        if period_key == "mentoring":
            continue

        # community — это 1m/3m
        if status == "approved" and period_key in ("1m", "3m"):
            latest_approved_community = page
            break  # уже отсортировано по created_time desc

    return {
        "latest_approved_community": latest_approved_community,
        "has_pending_any": has_pending_any,
    }

async def render_cabinet_text(tg_id: str) -> str:
    records = await notion_query_by_tg_id(tg_id=tg_id, limit=30)
    picked = pick_latest_records(records)

    approved = picked["latest_approved_community"]
    has_pending = picked["has_pending_any"]

    if approved:
        props = approved.get("properties", {})
        discord = safe_get_rich_text(props, "discord") or "Не указан"
        email = safe_get_rich_text(props, "email") or "Не указан"
        expires_at = safe_get_rich_text(props, "expires_at").strip()

        if expires_at and is_future_or_today(expires_at):
            plan_line = f"Hadiukov Community — {format_date_ddmmyyyy(expires_at)}"
        elif expires_at:
            plan_line = f"Hadiukov Community — истекла {format_date_ddmmyyyy(expires_at)}"
        else:
            plan_line = "Hadiukov Community — активная дата не указана"

        return (
            "👤 <b>Личный кабинет</b>\n\n"
            f"Discord: <b>{discord}</b>\n"
            f"Email: <b>{email}</b>\n\n"
            f"{plan_line}"
        )

    # нет approved
    base = (
        "👤 <b>Личный кабинет</b>\n\n"
        "Discord: <b>Не указан</b>\n"
        "Email: <b>Не указан</b>\n\n"
    )
    if has_pending:
        return base + "Статус: <b>На проверке админом</b>"
    return base + "Статус: <b>Нет активной подписки</b>"

# =========================
# PAYMENT FLOW (final messages)
# =========================

async def send_payment_flow_final(
    message: Message,
    *,
    tg_id: int,
    tg_username: str | None,
    product: str,
    pay_method: str,
    currency: str,
    amount: int,
    period_key: str = "",
    period_text: str = "",
    expires_at: str = "",
):
    """
    Финальные сообщения после выбора суммы:
    - Crypto: "Для оплаты ... N USDT" + "адрес (в monospace только адрес) + кнопка tally"
    - Fiat: "Для оплаты ... X грн" + "Скоро добавим карту." + кнопка tally
    """
    order_id = str(uuid.uuid4())

    params = {
        "order_id": order_id,
        "tg_id": str(tg_id),
        "tg_username": tg_username or "",
        "product": product,
        "period": period_text,
        "period_key": period_key,
        "pay_method": pay_method,
        "expires_at": expires_at,
    }

    # поля для совместимости с твоими hidden-полями в Tally
    if currency == "USDT":
        params["amount_usdt"] = str(amount)
        params["amount_uah"] = ""
    else:
        params["amount_uah"] = str(amount)
        params["amount_usdt"] = ""

    tally_url = build_tally_url(params)
    kb = tally_confirm_kb(tally_url)

    if currency == "USDT":
        await message.answer(f"Для оплаты Вам необходимо перевести {amount} USDT:")
        # monospace только адрес, остальное обычным
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

def kb_close() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть", callback_data="close")]
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
    await message.answer("ℹ️ Раздел «Информация» пока в разработке.")

@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    await message.answer("❓ Раздел «Помощь» пока в разработке.")

@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    # Важно: НЕ меняем reply_markup, чтобы нижние плитки не исчезали
    try:
        text = await render_cabinet_text(str(message.from_user.id))
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Ошибка кабинета: {e}")

@dp.message(lambda m: "Мои ресурсы" in (m.text or ""))
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
    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=kb_payment_methods("community"),
    )
    await cb.answer()

@dp.callback_query(F.data == "buy:mentoring")
async def buy_mentoring(cb: CallbackQuery):
    await cb.message.delete()
    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=kb_payment_methods("mentoring"),
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("pm:"))
async def payment_method_choice(cb: CallbackQuery):
    _, product_key, method = cb.data.split(":")

    if product_key == "community" and method == "crypto":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=kb_community_crypto_periods(),
        )
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=kb_community_fiat_periods(),
        )
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=kb_mentoring_crypto(),
        )
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(
            cb.message,
            SUBSCRIPTION_IMAGE_PATH,
            caption="Выберите срок подписки",
            reply_markup=kb_mentoring_fiat(),
        )

    await cb.answer()

@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()

@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    # ✅ ВАЖНО: пользователь = cb.from_user (НЕ cb.message.from_user)
    tg_id = cb.from_user.id
    tg_username = cb.from_user.username

    if product_key == "community":
        product_name = "Hadiukov Community"

        if choice in ("1m", "3m"):
            period_key = choice
            period_text = PERIOD_TEXT[period_key]
            expires_at = expires_from_key(period_key)
        else:
            period_key = ""
            period_text = ""
            expires_at = ""

        if method == "crypto":
            amount = COMMUNITY_USDT_1M if choice == "1m" else COMMUNITY_USDT_3M
            await send_payment_flow_final(
                cb.message,
                tg_id=tg_id,
                tg_username=tg_username,
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
                tg_id=tg_id,
                tg_username=tg_username,
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

        # mentoring в кабинет не выводим, но оплату оставить можно
        if method == "crypto":
            await send_payment_flow_final(
                cb.message,
                tg_id=tg_id,
                tg_username=tg_username,
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
                tg_id=tg_id,
                tg_username=tg_username,
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
# RUN
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
