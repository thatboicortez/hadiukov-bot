import uuid
import asyncio
from datetime import datetime, date
from urllib.parse import urlencode, quote

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

from config import BOT_TOKEN, TALLY_FORM_URL, NOTION_TOKEN, NOTION_DATABASE_ID

# =========================
# CONFIG / CONSTANTS
# =========================

ADMIN_USERNAME = "@name"  # поменяешь потом

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

# Mentoring
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
# NOTION (READ ONLY)
# =========================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


async def notion_query_database(
    filter_obj: dict | None = None,
    page_size: int = 50,
    sorts: list | None = None,
) -> dict:
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    payload: dict = {"page_size": page_size}
    if filter_obj:
        payload["filter"] = filter_obj
    if sorts:
        payload["sorts"] = sorts

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def notion_get_text_prop(page: dict, prop_name: str) -> str:
    """Поддерживает Text (rich_text), Title и Email."""
    props = page.get("properties", {})
    p = props.get(prop_name)
    if not p:
        return ""
    t = p.get("type")
    if t == "rich_text":
        arr = p.get("rich_text", [])
        return arr[0].get("plain_text", "") if arr else ""
    if t == "title":
        arr = p.get("title", [])
        return arr[0].get("plain_text", "") if arr else ""
    if t == "email":
        return p.get("email") or ""
    return ""


def notion_get_status(page: dict, prop_name: str) -> str:
    props = page.get("properties", {})
    p = props.get(prop_name)
    if not p:
        return ""
    if p.get("type") == "status":
        s = p.get("status") or {}
        return s.get("name", "") or ""
    return ""


def parse_expires(expires_at_str: str) -> date | None:
    if not expires_at_str:
        return None
    # ожидаем "YYYY-MM-DD"
    try:
        return datetime.strptime(expires_at_str.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def is_approved(status_name: str) -> bool:
    return (status_name or "").strip().lower() == "approved"


async def get_user_records(tg_id: int) -> list[dict]:
    """Берём все записи пользователя и сортируем по created_time DESC (самые новые сверху)."""
    tg_id_str = str(tg_id)
    filter_obj = {"property": "tg_id", "rich_text": {"equals": tg_id_str}}
    sorts = [{"timestamp": "created_time", "direction": "descending"}]
    data = await notion_query_database(filter_obj, page_size=50, sorts=sorts)
    return data.get("results", []) or []


def pick_active_approved(records: list[dict]) -> dict | None:
    """Первая подходящая (самая свежая) approved-запись, которая ещё не истекла."""
    today = datetime.utcnow().date()
    for page in records:
        status_name = notion_get_status(page, "status")
        if not is_approved(status_name):
            continue

        expires_at = notion_get_text_prop(page, "expires_at")
        exp_date = parse_expires(expires_at)
        if not exp_date:
            continue
        if exp_date < today:
            continue

        return page
    return None


# =========================
# HELPERS
# =========================

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")


def build_tally_url(params: dict) -> str:
    """
    Важно: используем quote (а не quote_plus), чтобы mini app не ловил странные ссылки.
    """
    params = dict(params)
    params["_tail"] = "1"  # чтобы tgWebAppData не прилипал к последнему параметру
    query = urlencode(params, quote_via=quote)
    return f"{TALLY_FORM_URL}?{query}"


async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except Exception:
        await message.answer(caption or " ", reply_markup=reply_markup)


def tally_confirm_kb(tally_url: str) -> InlineKeyboardMarkup:
    # ВАЖНО: именно web_app — это mini app
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])


async def send_payment_flow_final(
    message: Message,
    *,
    product: str,
    pay_method: str,
    currency: str,
    amount: int,
    period_key: str = "",
    period_text: str = "",
    expires_at: str = "",
):
    """
    Hidden-поля в Tally (короткие):
      t  -> tg_id
      u  -> tg_username
      pk -> period_key
      as -> amount_usdt
      au -> amount_uah
      pm -> pay_method
      o  -> order_id
      ex -> expires_at
      product, period (если есть hidden в форме)
    """
    order_id = str(uuid.uuid4())

    params = {
        "t": str(message.from_user.id),
        "u": message.from_user.username or "",
        "product": product,
        "period": period_text,
        "pk": period_key,
        "pm": pay_method,
        "o": order_id,
        "ex": expires_at,
    }

    if currency == "USDT":
        params["as"] = str(amount)
        params["au"] = ""
    else:
        params["as"] = ""
        params["au"] = str(amount)

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


# --- Main menu sections ---

@dp.message(lambda m: "Информация" in (m.text or ""))
async def info_from_menu(message: Message):
    await message.answer("ℹ️ Раздел «Информация» пока в разработке.")


@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    await message.answer("❓ Раздел «Помощь» пока в разработке.")


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


# --- Личный кабинет (READ ONLY из Notion) ---
@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    try:
        records = await get_user_records(message.from_user.id)

        if not records:
            await message.answer(
                "👤 Личный кабинет\n\n"
                "Discord: <b>Не указан</b>\n"
                "Email: <b>Не указан</b>\n\n"
                "Статус: <b>Нет активной подписки</b>"
            )
            return

        active = pick_active_approved(records)

        if active:
            discord = notion_get_text_prop(active, "discord") or "Не указан"
            email = notion_get_text_prop(active, "email") or "Не указан"
            expires_at = notion_get_text_prop(active, "expires_at") or ""

            await message.answer(
                "👤 Личный кабинет\n\n"
                f"Discord: <b>{discord}</b>\n"
                f"Email: <b>{email}</b>\n\n"
                f"Подписка: <b>Hadiukov Community – {expires_at}</b>"
            )
            return

        # Если нет активной approved, но есть запись — значит заявка есть, но на проверке/отклонена/и т.д.
        latest = records[0]
        discord = notion_get_text_prop(latest, "discord") or "Не указан"
        email = notion_get_text_prop(latest, "email") or "Не указан"
        status_name = notion_get_status(latest, "status").strip()

        # Приводим к понятному тексту
        if is_approved(status_name):
            status_text = "Подписка активируется (проверь expires_at)"
        elif status_name:
            status_text = "Заявка на проверке" if status_name.lower() != "rejected" else "Заявка отклонена"
        else:
            status_text = "Заявка на проверке"

        await message.answer(
            "👤 Личный кабинет\n\n"
            f"Discord: <b>{discord}</b>\n"
            f"Email: <b>{email}</b>\n\n"
            f"Статус: <b>{status_text}</b>"
        )

    except Exception as e:
        await message.answer(f"Ошибка кабинета: {e}")


# --- Inline: Buy / Acquire ---
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


# --- Inline: Payment method -> Subscription choices ---
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


# --- Inline: Subscription selected -> Final instructions + Tally ---
@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    if product_key == "community":
        product_name = "Hadiukov Community"

        period_key = choice if choice in ("1m", "3m") else ""
        period_text = PERIOD_TEXT.get(period_key, "")
        expires_at = expires_from_key(period_key) if period_key else ""

        if method == "crypto":
            amount = COMMUNITY_USDT_1M if choice == "1m" else COMMUNITY_USDT_3M
            await send_payment_flow_final(
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
            amount = COMMUNITY_UAH_1M if choice == "1m" else COMMUNITY_UAH_3M
            await send_payment_flow_final(
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
        if method == "crypto":
            await send_payment_flow_final(
                cb.message,
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
