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

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import aiohttp

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

MENTORING_USDT = 3000
MENTORING_UAH = 130000

PERIOD_TEXT = {"1m": "1 month", "3m": "3 months"}
PERIOD_MONTHS = {"1m": 1, "3m": 3}

NOTION_VERSION = "2022-06-28"


# =========================
# BOT INIT
# =========================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())


# =========================
# FSM (Личный кабинет: правки)
# =========================

class CabinetEdit(StatesGroup):
    waiting_discord = State()
    waiting_email = State()


# =========================
# HELPERS (общие)
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


# =========================
# NOTION HELPERS
# =========================

def notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def _rt_get(props: dict, name: str) -> str:
    p = props.get(name, {})
    if p.get("type") == "rich_text":
        arr = p.get("rich_text", [])
        if not arr:
            return ""
        return "".join([x.get("plain_text", "") for x in arr]).strip()
    return ""

def _email_get(props: dict, name: str) -> str:
    p = props.get(name, {})
    if p.get("type") == "email":
        return (p.get("email") or "").strip()
    return ""

def _date_get(props: dict, name: str) -> str:
    p = props.get(name, {})
    if p.get("type") == "date" and p.get("date") and p["date"].get("start"):
        return p["date"]["start"]
    return ""

def _fmt_ddmmyyyy(iso_date: str) -> str:
    # iso_date может быть "2026-01-18" или "2026-01-18T..."
    if not iso_date:
        return ""
    d = iso_date.split("T")[0]
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return d

async def notion_query_by_tg_id(tg_id: int) -> list[dict]:
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        return []

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {"property": "tg_id", "number": {"equals": int(tg_id)}},
        "sorts": [{"property": "created_at", "direction": "descending"}],
        "page_size": 10,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=notion_headers(), json=payload) as resp:
            if resp.status >= 300:
                text = await resp.text()
                raise RuntimeError(f"Notion query error {resp.status}: {text}")
            data = await resp.json()
            return data.get("results", [])

async def notion_create_user_row(tg_id: int, tg_username: str) -> str:
    """
    Создаёт строку, если у юзера ещё нет записи.
    Возвращает page_id.
    """
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        return ""

    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": str(tg_id)}}]},
            "tg_id": {"number": int(tg_id)},
            "tg_username": {"rich_text": [{"text": {"content": tg_username or ""}}]},
            "discord": {"rich_text": [{"text": {"content": ""}}]},
            "email": {"email": ""},
        }
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=notion_headers(), json=payload) as resp:
            if resp.status >= 300:
                text = await resp.text()
                raise RuntimeError(f"Notion create error {resp.status}: {text}")
            data = await resp.json()
            return data.get("id", "")

async def notion_update_page(page_id: str, *, discord: str | None = None, email: str | None = None) -> None:
    if not NOTION_TOKEN or not page_id:
        return

    url = f"https://api.notion.com/v1/pages/{page_id}"
    props: dict = {}

    if discord is not None:
        props["discord"] = {"rich_text": [{"text": {"content": discord}}]}
    if email is not None:
        props["email"] = {"email": email}

    payload = {"properties": props}

    async with aiohttp.ClientSession() as session:
        async with session.patch(url, headers=notion_headers(), json=payload) as resp:
            if resp.status >= 300:
                text = await resp.text()
                raise RuntimeError(f"Notion update error {resp.status}: {text}")

async def notion_get_or_create_latest(tg_id: int, tg_username: str) -> tuple[str, dict]:
    """
    Возвращает (page_id, properties) самой свежей записи.
    Если записей нет — создаёт.
    """
    rows = await notion_query_by_tg_id(tg_id)
    if rows:
        page = rows[0]
        return page.get("id", ""), page.get("properties", {})
    page_id = await notion_create_user_row(tg_id, tg_username)
    # после создания вернём пустые props (или можно перечитать, но не обязательно)
    return page_id, {
        "tg_username": {"type": "rich_text", "rich_text": [{"plain_text": tg_username}]},
        "discord": {"type": "rich_text", "rich_text": []},
        "email": {"type": "email", "email": ""},
        "expires_at": {"type": "date", "date": None},
    }


# =========================
# PAYMENT FINAL (ВАЖНО: user_id/user_username отдельно!)
# =========================

async def send_payment_flow_final(
    chat_message: Message,
    *,
    user_id: int,
    user_username: str,
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
        "tg_id": str(user_id),
        "tg_username": user_username or "",
        "product": product,
        "period": period_text,
        "period_key": period_key,
        "pay_method": pay_method,
        "currency": currency,
        "amount": str(amount),
        "expires_at": expires_at,
    }

    # поля для совместимости с твоими скрытыми полями в Tally
    if currency == "USDT":
        params["amount_usdt"] = str(amount)
    else:
        params["amount_uah"] = str(amount)

    tally_url = build_tally_url(params)
    kb = tally_confirm_kb(tally_url)

    if currency == "USDT":
        await chat_message.answer(f"Для оплаты Вам необходимо перевести {amount} USDT:")
        await chat_message.answer(
            f"<code>{USDT_TRC20_ADDRESS}</code> (USDT. Сеть TRC20)",
            reply_markup=kb,
        )
    else:
        await chat_message.answer(f"Для оплаты Вам необходимо перевести {amount} грн на указанные реквизиты:")
        await chat_message.answer("Скоро добавим карту.", reply_markup=kb)


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

def kb_cabinet(page_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Изменить Discord", callback_data=f"cab:edit:discord:{page_id}"),
            InlineKeyboardButton(text="Изменить почту", callback_data=f"cab:edit:email:{page_id}"),
        ],
        [InlineKeyboardButton(text="Обновить", callback_data="cab:refresh")],
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

# --- PRODUCTS ENTRY ---
@dp.message(lambda m: "Мои продукты" in (m.text or ""))
async def products_entry(message: Message):
    await send_photo_safe(message, PRODUCTS_IMAGE_PATH, caption=None)
    await message.answer("Выберите:", reply_markup=products_menu_kb())

# --- Products menu choices ---
@dp.message(F.text == "Hadiukov Community")
async def community_info(message: Message):
    await message.answer("Объяснение внутрянки сервера", reply_markup=kb_community_buy())

@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    await message.answer("Объяснение того что будет на менторке", reply_markup=kb_mentoring_buy())

# --- Inline: Buy / Acquire (удаляем сообщение с объяснением) ---
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
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_crypto_periods())
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_fiat_periods())
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_crypto())
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_fiat())

    await cb.answer()

# --- Inline: Close current message ---
@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()

# --- Inline: Subscription selected -> Final instructions + Tally ---
@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    user_id = cb.from_user.id
    user_username = cb.from_user.username or ""  # ✅ ВАЖНО: берём у реального юзера

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
# ЛИЧНЫЙ КАБИНЕТ (Notion)
# =========================

async def build_cabinet_text(tg_id: int, tg_username: str) -> tuple[str, str]:
    """
    Возвращает (text, page_id) для кабинета.
    """
    page_id, props = await notion_get_or_create_latest(tg_id, tg_username)

    discord = _rt_get(props, "discord")
    email = _email_get(props, "email")
    expires_at = _date_get(props, "expires_at")  # ISO
    expires_fmt = _fmt_ddmmyyyy(expires_at)

    # Если нет даты — можно показать что подписки нет
    if expires_fmt:
        community_line = f"Hadiukov Community — {expires_fmt}"
    else:
        community_line = "Hadiukov Community — нет активной подписки"

    text = (
        f"Discord: <b>{discord or '—'}</b>\n"
        f"Email: <b>{email or '—'}</b>\n\n"
        f"{community_line}"
    )
    return text, page_id

@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_entry(message: Message):
    # reply-плитки НЕ трогаем (оставляем main_menu_kb)
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        await message.answer(
            "Личный кабинет пока недоступен (Notion не подключен).",
            reply_markup=main_menu_kb(),
        )
        return

    try:
        text, page_id = await build_cabinet_text(message.from_user.id, message.from_user.username or "")
        await message.answer(text, reply_markup=main_menu_kb())  # плитки остаются
        await message.answer("Ресурсы", reply_markup=kb_cabinet(page_id))
    except Exception as e:
        await message.answer(f"Ошибка кабинета: {e}", reply_markup=main_menu_kb())

@dp.callback_query(F.data == "cab:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    try:
        text, page_id = await build_cabinet_text(cb.from_user.id, cb.from_user.username or "")
        # обновим текущий текст сообщения (где кнопки)
        await cb.message.edit_text("Ресурсы", reply_markup=kb_cabinet(page_id))
        await cb.message.answer(text)
    except Exception as e:
        await cb.message.answer(f"Ошибка обновления: {e}")
    await cb.answer()

@dp.callback_query(F.data.startswith("cab:edit:"))
async def cabinet_edit_start(cb: CallbackQuery, state: FSMContext):
    # cab:edit:discord:{page_id}
    _, _, field, page_id = cb.data.split(":", 3)

    await state.update_data(page_id=page_id)

    if field == "discord":
        await state.set_state(CabinetEdit.waiting_discord)
        await cb.message.answer("Введите новый Discord (ник):")
    elif field == "email":
        await state.set_state(CabinetEdit.waiting_email)
        await cb.message.answer("Введите новую почту (Email):")

    await cb.answer()

@dp.message(CabinetEdit.waiting_discord)
async def cabinet_set_discord(message: Message, state: FSMContext):
    data = await state.get_data()
    page_id = data.get("page_id", "")
    new_val = (message.text or "").strip()

    try:
        await notion_update_page(page_id, discord=new_val)
        await message.answer("✅ Discord обновлён.", reply_markup=main_menu_kb())
    except Exception as e:
        await message.answer(f"Ошибка обновления Discord: {e}", reply_markup=main_menu_kb())

    await state.clear()

@dp.message(CabinetEdit.waiting_email)
async def cabinet_set_email(message: Message, state: FSMContext):
    data = await state.get_data()
    page_id = data.get("page_id", "")
    new_val = (message.text or "").strip()

    try:
        await notion_update_page(page_id, email=new_val)
        await message.answer("✅ Email обновлён.", reply_markup=main_menu_kb())
    except Exception as e:
        await message.answer(f"Ошибка обновления Email: {e}", reply_markup=main_menu_kb())

    await state.clear()


# =========================
# RUN
# =========================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
