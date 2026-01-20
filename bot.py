import os
import re
import uuid
import asyncio
from datetime import datetime
from urllib.parse import urlencode, quote_plus

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

from config import BOT_TOKEN, TALLY_FORM_URL


# =========================
# ENV (Notion)
# =========================
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()

NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


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

MENTORING_USDT = 3000
MENTORING_UAH = 130000

PERIOD_TEXT = {"1m": "1 month", "3m": "3 months"}
PERIOD_MONTHS = {"1m": 1, "3m": 3}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
        if caption:
            await message.answer(caption, reply_markup=reply_markup)
        else:
            await message.answer(" ", reply_markup=reply_markup)


def tally_confirm_kb(tally_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])


def format_date_ddmmyyyy(date_iso: str | None) -> str | None:
    if not date_iso:
        return None
    try:
        # Notion date start может быть "YYYY-MM-DD" или "YYYY-MM-DDTHH:MM:SS..."
        dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y")
    except Exception:
        # если чисто YYYY-MM-DD
        try:
            dt = datetime.strptime(date_iso[:10], "%Y-%m-%d")
            return dt.strftime("%d.%m.%Y")
        except Exception:
            return None


# =========================
# Notion client
# =========================

async def notion_request(method: str, endpoint: str, json_data: dict | None = None) -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN не задан")
    url = f"{NOTION_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=headers, json=json_data) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"Notion {method} error {resp.status}: {data}")
            return data


def notion_get_rich_text(prop: dict) -> str | None:
    try:
        items = prop.get("rich_text", [])
        if not items:
            return None
        return "".join(x.get("plain_text", "") for x in items).strip() or None
    except Exception:
        return None


def notion_get_email(prop: dict) -> str | None:
    try:
        return prop.get("email", None) or None
    except Exception:
        return None


def notion_get_number(prop: dict) -> int | None:
    try:
        val = prop.get("number", None)
        return int(val) if val is not None else None
    except Exception:
        return None


def notion_get_date(prop: dict) -> str | None:
    try:
        d = prop.get("date")
        if not d:
            return None
        return d.get("start")
    except Exception:
        return None


async def notion_find_by_tg_id(tg_id: int) -> dict | None:
    """
    Ищем страницу по tg_id (Number).
    """
    if not NOTION_DATABASE_ID:
        raise RuntimeError("NOTION_DATABASE_ID не задан")

    payload = {
        "filter": {
            "property": "tg_id",
            "number": {"equals": int(tg_id)},
        }
    }
    data = await notion_request("POST", f"/databases/{NOTION_DATABASE_ID}/query", payload)
    results = data.get("results", [])
    return results[0] if results else None


def notion_props_for_user(tg_id: int, tg_username: str | None, discord: str | None, email: str | None) -> dict:
    """
    ВАЖНО:
    - Name (title) обязателен
    - email (тип Email) принимает только валидный email или None (НЕ "")
    """
    safe_email = email.strip() if (email and email.strip()) else None
    title_text = (tg_username.strip() if tg_username else "") or f"user_{tg_id}"

    props = {
        "Name": {
            "title": [
                {"type": "text", "text": {"content": title_text}}
            ]
        },
        "tg_id": {"number": int(tg_id)},
        "tg_username": {
            "rich_text": (
                [{"type": "text", "text": {"content": tg_username}}]
                if tg_username else []
            )
        },
        "discord": {
            "rich_text": (
                [{"type": "text", "text": {"content": discord}}]
                if discord else []
            )
        },
        "email": {"email": safe_email},
    }
    return props


async def notion_create_user_if_missing(tg_id: int, tg_username: str | None) -> dict:
    """
    Создаём минимальную запись, если её нет.
    Discord/Email пустые (None/[]), чтобы Notion не ругался.
    """
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": notion_props_for_user(tg_id, tg_username, discord=None, email=None),
    }
    return await notion_request("POST", "/pages", payload)


async def notion_update_user(page_id: str, *, tg_username: str | None = None, discord: str | None = None, email: str | None = None) -> dict:
    """
    PATCH /pages/{page_id}
    """
    props: dict = {}

    if tg_username is not None:
        props["tg_username"] = {
            "rich_text": (
                [{"type": "text", "text": {"content": tg_username}}]
                if tg_username else []
            )
        }

    if discord is not None:
        props["discord"] = {
            "rich_text": (
                [{"type": "text", "text": {"content": discord}}]
                if discord else []
            )
        }

    if email is not None:
        safe_email = email.strip() if (email and email.strip()) else None
        props["email"] = {"email": safe_email}

    payload = {"properties": props}
    return await notion_request("PATCH", f"/pages/{page_id}", payload)


def parse_user_from_notion_page(page: dict) -> dict:
    """
    Достаём нужное для кабинета:
    - discord
    - email
    - expires_at
    """
    props = page.get("properties", {})

    discord = notion_get_rich_text(props.get("discord", {}))
    email = notion_get_email(props.get("email", {}))
    expires_at = notion_get_date(props.get("expires_at", {}))  # Date property

    return {
        "discord": discord,
        "email": email,
        "expires_at": expires_at,
    }


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


def cabinet_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Изменить Discord", callback_data="cab:edit_discord"),
            InlineKeyboardButton(text="Изменить почту", callback_data="cab:edit_email"),
        ],
        [
            InlineKeyboardButton(text="Обновить", callback_data="cab:refresh"),
        ]
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
# Payment final flow (Tally)
# =========================

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
    order_id = str(uuid.uuid4())

    params = {
        "order_id": order_id,
        "tg_id": str(message.from_user.id),
        "tg_username": message.from_user.username or "",
        "product": product,
        "period": period_text,
        "period_key": period_key,
        "pay_method": pay_method,
        "currency": currency,
        "amount": str(amount),
        "expires_at": expires_at,
    }

    # совместимость со скрытыми полями (если используешь)
    if currency == "USDT":
        params["amount_usdt"] = str(amount)
    else:
        params["amount_uah"] = str(amount)

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
# Cabinet state (in-memory)
# =========================
USER_INPUT_STATE: dict[int, str] = {}  # user_id -> "discord" | "email"


async def render_cabinet(message: Message):
    """
    Показываем:
    Discord / Email (или Не указан)
    Hadiukov Community - dd.mm.yyyy (или Не активна)
    """
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        await message.answer(
            "Личный кабинет недоступен: Notion не настроен.\n"
            "Проверь NOTION_TOKEN и NOTION_DATABASE_ID в Koyeb.",
            reply_markup=main_menu_kb(),
        )
        return

    tg_id = message.from_user.id
    page = await notion_find_by_tg_id(tg_id)

    if page:
        data = parse_user_from_notion_page(page)
        discord = data["discord"] or "Не указан"
        email = data["email"] or "Не указан"

        exp_fmt = format_date_ddmmyyyy(data["expires_at"])
        if exp_fmt:
            community_line = f"Hadiukov Community — {exp_fmt}"
        else:
            community_line = "Hadiukov Community — Не активна"
    else:
        discord = "Не указан"
        email = "Не указан"
        community_line = "Hadiukov Community — Не активна"

    text = (
        f"Discord: {discord}\n"
        f"Email: {email}\n\n"
        f"{community_line}"
    )
    await message.answer(text, reply_markup=cabinet_inline_kb())


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


# --- PRODUCTS ENTRY ---
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
# CABINET
# =========================

@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_entry(message: Message):
    try:
        await render_cabinet(message)
    except Exception as e:
        await message.answer(f"Ошибка кабинета: {e}")


@dp.callback_query(F.data == "cab:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    try:
        # просто ещё раз рендерим
        await render_cabinet(cb.message)
    except Exception as e:
        await cb.message.answer(f"Ошибка кабинета: {e}")
    await cb.answer()


@dp.callback_query(F.data == "cab:edit_discord")
async def cabinet_edit_discord(cb: CallbackQuery):
    USER_INPUT_STATE[cb.from_user.id] = "discord"
    await cb.message.answer("Отправь новый Discord (или '-' чтобы очистить).")
    await cb.answer()


@dp.callback_query(F.data == "cab:edit_email")
async def cabinet_edit_email(cb: CallbackQuery):
    USER_INPUT_STATE[cb.from_user.id] = "email"
    await cb.message.answer("Отправь новый Email (или '-' чтобы очистить).")
    await cb.answer()


@dp.message(lambda m: m.from_user and m.from_user.id in USER_INPUT_STATE)
async def cabinet_receive_input(message: Message):
    field = USER_INPUT_STATE.get(message.from_user.id)
    text = (message.text or "").strip()

    # очистка
    if text == "-":
        text = ""

    try:
        if not NOTION_TOKEN or not NOTION_DATABASE_ID:
            await message.answer("Notion не настроен. Проверь NOTION_TOKEN / NOTION_DATABASE_ID.")
            USER_INPUT_STATE.pop(message.from_user.id, None)
            return

        tg_id = message.from_user.id
        tg_username = message.from_user.username or None

        page = await notion_find_by_tg_id(tg_id)
        if not page:
            page = await notion_create_user_if_missing(tg_id, tg_username)

        page_id = page["id"]

        # всегда обновим tg_username (чтобы не было кривых значений)
        await notion_update_user(page_id, tg_username=tg_username)

        if field == "discord":
            new_discord = text.strip() if text.strip() else None
            await notion_update_user(page_id, discord=new_discord)
            await message.answer("✅ Discord обновлён.")
        elif field == "email":
            new_email = text.strip() if text.strip() else None
            if new_email is not None and not EMAIL_RE.match(new_email):
                await message.answer("❌ Это не похоже на email. Отправь корректный email или '-' чтобы очистить.")
                return
            await notion_update_user(page_id, email=new_email)
            await message.answer("✅ Email обновлён.")

        USER_INPUT_STATE.pop(message.from_user.id, None)
        await render_cabinet(message)

    except Exception as e:
        USER_INPUT_STATE.pop(message.from_user.id, None)
        await message.answer(f"Ошибка: {e}")


# =========================
# RUN
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
